"""Spatial correction for AMT hex overdraw caused by reclaim attribution noise.

The correction is deliberately tonnage-only.  It does not move or recalculate
grades.  Negative hex tonnes are treated as reclaim deficits and transferred to
nearby positive hexes, preferring the inferred direction back into the remaining
stockpile.  A final stockpile-level adjustment reconciles the corrected hexes to
the authoritative inventory balance.
"""

from __future__ import annotations

import json
from copy import deepcopy
from math import atan2, cos, fsum, hypot, isfinite, radians, sin, sqrt
from statistics import median

from classes.PhaseSchemas import (
    amt_footprint_audit, AMT_OUTCOME_RECONCILED, AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW,
    AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY, AMT_OUTCOME_RETAINED_INVENTORY_UNAVAILABLE,
)

AMT_TONNAGE_RECON_VERSION = 3


SPATIAL_METHOD = (
    "directional_nearest_capacity_v3: non-positive raw hex totals are zeroed before inventory allocation; negative hex deficits are allocated to "
    "positive hexes by distance, connected geometry, and inferred reclaim-front "
    "direction; residual inventory deductions are then weighted toward lower "
    "grade-block lineage coverage"
)

INVENTORY_RECON_LINEAGE_BIAS = 4.0


def _number(value, default=None):
    try:
        if value in (None, ""):
            return default
        number = float(value)
        return number if isfinite(number) else default
    except (TypeError, ValueError):
        return default


def raw_amt_hex_total(rows):
    """Q46: signed raw hex tonnes only; missing raw evidence is not final mass."""
    values = [_number(row.get("RAW_WMT", row.get("raw_wmt"))) for row in rows or []]
    return fsum(values) if values and all(v is not None for v in values) else None


def amt_zeroing_outcome(rows):
    rows = rows or []
    raw_total = raw_amt_hex_total(rows)
    inventory = _number(rows[0].get("INVENTORY_BALANCE_WMT", rows[0].get("inventory_balance_wmt"))) if rows else None
    if raw_total is not None and raw_total <= 0:
        return AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW
    if inventory is not None and inventory <= 0:
        return AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY
    return None


def zeroed_amt_footprints(snapshot):
    return {str(name).strip().upper() for name, rows in (snapshot or {}).items() if amt_zeroing_outcome(rows)}


def guard_amt_snapshot(snapshot):
    """Repair zeroed saved snapshots from raw evidence without a warehouse read."""
    from setup.AMTGradeBlockLineage import align_amt_grade_block_lineage
    result = deepcopy(snapshot or {})
    aliases = {"RAW_WMT": "raw_wmt", "FINAL_WMT": "balance",
               "INVENTORY_BALANCE_WMT": "inventory_balance_wmt",
               "UNATTRIBUTED_MOVEMENT_WMT": "unattributed_movement_wmt",
               "GRADE_BLOCK_LINEAGE_JSON": "grade_block_lineage_json",
               "MODELLED_PROPERTIES_JSON": "modelled_properties_json"}
    for footprint, rows in result.items():
        if not amt_zeroing_outcome(rows):
            continue
        for row in rows:
            row.setdefault("FOOTPRINT", footprint)
            for target, alias in aliases.items():
                if target not in row and alias in row:
                    row[target] = row[alias]
        rows = align_amt_grade_block_lineage(reconcile_amt_hex_rows(rows))
        for row in rows:
            for key in ("balance", "final_wmt"):
                if key in row:
                    row[key] = 0.0
        result[footprint] = rows
    return result


def _lineage_coverage(row):
    """Return pre-reconciliation matched lineage coverage as a 0..1 ratio."""
    explicit = _number(row.get("LINEAGE_COVERAGE_PCT"))
    if explicit is not None:
        return min(max(explicit / 100.0, 0.0), 1.0)

    lineage = row.get("GRADE_BLOCK_LINEAGE_JSON")
    if isinstance(lineage, str):
        try:
            lineage = json.loads(lineage)
        except (TypeError, ValueError):
            lineage = []
    if not isinstance(lineage, list):
        lineage = []

    inbound_total = 0.0
    matched_total = 0.0
    for item in lineage:
        if not isinstance(item, dict):
            continue
        inbound_wmt = max(_number(item.get("inbound_wmt"), 0.0), 0.0)
        inbound_total += inbound_wmt
        if str(item.get("match_method") or "").strip().upper() != "UNMATCHED":
            matched_total += inbound_wmt
    if inbound_total <= 1e-9:
        return 0.0
    return min(max(matched_total / inbound_total, 0.0), 1.0)


def _lineage_weighted_inventory_deductions(balances, target_total, coverages):
    """Allocate a stockpile-level reduction without creating negative hexes."""
    balances = [max(_number(value, 0.0), 0.0) for value in balances]
    deduction_required = max(sum(balances) - max(target_total, 0.0), 0.0)
    deductions = [0.0] * len(balances)
    remaining_capacity = list(balances)
    active = {
        index for index, balance in enumerate(remaining_capacity)
        if balance > 1e-9
    }

    # Capped proportional (water-filling) allocation. A 0%-covered hex has
    # five times the initial deduction weight of a fully covered hex, while
    # every positive hex remains eligible if the uncertain tonnes are not
    # sufficient to absorb the authoritative inventory difference.
    while deduction_required > 1e-9 and active:
        weights = {
            index: remaining_capacity[index] * (
                1.0
                + INVENTORY_RECON_LINEAGE_BIAS
                * (1.0 - min(max(coverages[index], 0.0), 1.0))
            )
            for index in active
        }
        total_weight = sum(weights.values())
        if total_weight <= 1e-12:
            break
        proposed = {
            index: deduction_required * weight / total_weight
            for index, weight in weights.items()
        }
        capped = {
            index for index in active
            if proposed[index] >= remaining_capacity[index] - 1e-9
        }
        if not capped:
            for index, amount in proposed.items():
                deductions[index] += amount
                remaining_capacity[index] -= amount
            deduction_required = 0.0
            break
        for index in capped:
            amount = remaining_capacity[index]
            deductions[index] += amount
            deduction_required -= amount
            remaining_capacity[index] = 0.0
            active.remove(index)

    # Numerical remainder only; distribute by remaining mass so the final
    # sum still equals the inventory balance exactly within floating precision.
    if deduction_required > 1e-9 and active:
        remaining_total = sum(remaining_capacity[index] for index in active)
        if remaining_total > 1e-12:
            for index in active:
                amount = min(
                    remaining_capacity[index],
                    deduction_required
                    * remaining_capacity[index] / remaining_total,
                )
                deductions[index] += amount
    return deductions


def _weighted_centroid(points):
    total = sum(weight for _, _, weight in points)
    if total <= 0:
        return None
    return (
        sum(x * weight for x, _, weight in points) / total,
        sum(y * weight for _, y, weight in points) / total,
    )


def _normalise(vector):
    length = hypot(vector[0], vector[1])
    if length <= 1e-12:
        return None
    return vector[0] / length, vector[1] / length


def _coordinates(rows):
    """Return metre-like local coordinates, preferring supplied MGA coordinates."""
    coordinates = {}
    valid_lon_lat = []
    for index, row in enumerate(rows):
        easting = _number(row.get("SOURCEHEXEASTING"))
        northing = _number(row.get("SOURCEHEXNORTHING"))
        if easting is not None and northing is not None:
            coordinates[index] = (easting, northing)
            continue
        longitude = _number(row.get("LONGITUDE"))
        latitude = _number(row.get("LATITUDE"))
        if longitude is not None and latitude is not None:
            valid_lon_lat.append((index, longitude, latitude))

    if valid_lon_lat:
        longitude_origin = median(value[1] for value in valid_lon_lat)
        latitude_origin = median(value[2] for value in valid_lon_lat)
        longitude_scale = 111_320.0 * cos(radians(latitude_origin))
        for index, longitude, latitude in valid_lon_lat:
            if index not in coordinates:
                coordinates[index] = (
                    (longitude - longitude_origin) * longitude_scale,
                    (latitude - latitude_origin) * 110_540.0,
                )
    return coordinates


def _hex_spacing(coordinates):
    points = list(coordinates.values())
    if len(points) < 2:
        return 1.0
    nearest = []
    for index, point in enumerate(points):
        distances = [
            hypot(point[0] - other[0], point[1] - other[1])
            for other_index, other in enumerate(points)
            if other_index != index
        ]
        if distances:
            distance = min(distances)
            if distance > 1e-9:
                nearest.append(distance)
    return median(nearest) if nearest else 1.0


def _components(coordinates, spacing):
    indices = list(coordinates)
    parent = {index: index for index in indices}

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    adjacency_limit = max(spacing * 1.75, 1e-6)
    for position, left in enumerate(indices):
        left_point = coordinates[left]
        for right in indices[position + 1:]:
            right_point = coordinates[right]
            if hypot(
                left_point[0] - right_point[0],
                left_point[1] - right_point[1],
            ) <= adjacency_limit:
                union(left, right)
    return {index: find(index) for index in indices}


def _reclaim_direction(deficits, donors, coordinates):
    """Infer the direction from the negative reclaim front into remaining ore."""
    negative_points = [
        (*coordinates[index], amount)
        for index, amount in deficits.items()
        if index in coordinates
    ]
    positive_points = [
        (*coordinates[index], amount)
        for index, amount in donors.items()
        if index in coordinates
    ]
    negative_centroid = _weighted_centroid(negative_points)
    positive_centroid = _weighted_centroid(positive_points)
    if negative_centroid and positive_centroid:
        toward_material = _normalise((
            positive_centroid[0] - negative_centroid[0],
            positive_centroid[1] - negative_centroid[1],
        ))
    else:
        toward_material = None

    # A long negative band represents one or more reclaimed rows.  Its minor
    # axis is the reclaim progression axis, with the sign chosen toward the
    # centroid of the remaining positive material.
    if negative_centroid and len(negative_points) >= 2:
        total_weight = sum(point[2] for point in negative_points)
        covariance_xx = sum(
            weight * (x - negative_centroid[0]) ** 2
            for x, _, weight in negative_points
        ) / total_weight
        covariance_yy = sum(
            weight * (y - negative_centroid[1]) ** 2
            for _, y, weight in negative_points
        ) / total_weight
        covariance_xy = sum(
            weight * (x - negative_centroid[0]) * (y - negative_centroid[1])
            for x, y, weight in negative_points
        ) / total_weight
        trace = covariance_xx + covariance_yy
        discriminant = sqrt(
            max((covariance_xx - covariance_yy) ** 2 + 4 * covariance_xy ** 2, 0)
        )
        major_value = (trace + discriminant) / 2
        minor_value = max((trace - discriminant) / 2, 0)
        major_angle = 0.5 * atan2(2 * covariance_xy, covariance_xx - covariance_yy)
        row_axis = (cos(major_angle), sin(major_angle))
        progression = (-row_axis[1], row_axis[0])
        anisotropy = major_value / max(minor_value, 1e-9)
        if anisotropy >= 1.35:
            if toward_material and (
                progression[0] * toward_material[0]
                + progression[1] * toward_material[1]
            ) < 0:
                progression = (-progression[0], -progression[1])
            if toward_material and abs(
                progression[0] * toward_material[0]
                + progression[1] * toward_material[1]
            ) < 0.15:
                return toward_material
            return progression
    return toward_material or (1.0, 0.0)


def _allocate_spatial_deficits(rows, raw_balances, coordinates):
    deficits = {
        index: -balance for index, balance in enumerate(raw_balances) if balance < 0
    }
    donors = {
        index: balance for index, balance in enumerate(raw_balances) if balance > 0
    }
    corrected = list(raw_balances)
    donor_used = {index: 0.0 for index in donors}
    deficit_filled = {index: 0.0 for index in deficits}
    directions = {}
    if not deficits or not donors:
        return corrected, donor_used, deficit_filled, deficits, directions, False

    spacing = _hex_spacing(coordinates)
    components = _components(coordinates, spacing)
    global_direction = _reclaim_direction(deficits, donors, coordinates)
    component_directions = {}
    for component in set(components.values()):
        component_deficits = {
            index: amount for index, amount in deficits.items()
            if components.get(index) == component
        }
        component_donors = {
            index: amount for index, amount in donors.items()
            if components.get(index) == component
        }
        if component_deficits and component_donors:
            component_directions[component] = _reclaim_direction(
                component_deficits, component_donors, coordinates
            )

    candidates = []
    geometry_fallback_used = False
    for deficit_index in deficits:
        deficit_point = coordinates.get(deficit_index)
        component = components.get(deficit_index)
        direction = component_directions.get(component, global_direction)
        directions[deficit_index] = direction
        tangent = (-direction[1], direction[0])
        for donor_index in donors:
            donor_point = coordinates.get(donor_index)
            if deficit_point is None or donor_point is None:
                geometry_fallback_used = True
                distance = spacing * 10_000
                cost = distance + abs(donor_index - deficit_index)
            else:
                delta_x = donor_point[0] - deficit_point[0]
                delta_y = donor_point[1] - deficit_point[1]
                distance = hypot(delta_x, delta_y)
                forward = delta_x * direction[0] + delta_y * direction[1]
                lateral = abs(delta_x * tangent[0] + delta_y * tangent[1])
                wrong_side_penalty = 0.0
                if forward < -0.25 * spacing:
                    wrong_side_penalty = 100 * spacing + 10 * abs(forward)
                disconnected_penalty = (
                    1_000 * spacing
                    if component is not None
                    and components.get(donor_index) is not None
                    and components.get(donor_index) != component
                    else 0.0
                )
                cost = (
                    distance
                    + 0.35 * lateral
                    + wrong_side_penalty
                    + disconnected_penalty
                )
            candidates.append((
                cost,
                distance,
                str(rows[deficit_index].get("HEX", deficit_index)),
                str(rows[donor_index].get("HEX", donor_index)),
                deficit_index,
                donor_index,
            ))

    remaining_deficits = dict(deficits)
    remaining_donors = dict(donors)
    for _, _, _, _, deficit_index, donor_index in sorted(candidates):
        required = remaining_deficits.get(deficit_index, 0.0)
        available = remaining_donors.get(donor_index, 0.0)
        if required <= 1e-9 or available <= 1e-9:
            continue
        transfer = min(required, available)
        remaining_deficits[deficit_index] -= transfer
        remaining_donors[donor_index] -= transfer
        deficit_filled[deficit_index] += transfer
        donor_used[donor_index] += transfer

    for index, balance in enumerate(raw_balances):
        if index in deficits:
            corrected[index] = -max(remaining_deficits[index], 0.0)
        elif index in donors:
            corrected[index] = max(remaining_donors[index], 0.0)
        else:
            corrected[index] = balance
    return (
        corrected,
        donor_used,
        deficit_filled,
        remaining_deficits,
        directions,
        geometry_fallback_used,
    )


def reconcile_amt_hex_rows(rows):
    """Return AMT rows with raw, spatially corrected, and final audit tonnes."""
    corrected_rows = [dict(row or {}) for row in rows or []]
    if not corrected_rows:
        return corrected_rows

    raw_balances = [
        _number(row.get("RAW_WMT", row.get("FINAL_WMT")), 0.0)
        for row in corrected_rows
    ]
    # Decide before clipping negatives, adding unattributed movements, or
    # allocating inventory. A donor hex cannot rescue a non-positive sum.
    raw_hex_total = fsum(raw_balances)
    coordinates = _coordinates(corrected_rows)
    (
        spatial_balances,
        donor_used,
        deficit_filled,
        remaining_deficits,
        directions,
        geometry_fallback_used,
    ) = _allocate_spatial_deficits(corrected_rows, raw_balances, coordinates)

    unresolved_total = sum(max(value, 0.0) for value in remaining_deficits.values())
    # No negative balance is passed into chunking.  Any unresolved spatial
    # deficit is retained in the audit and absorbed by the explicit inventory
    # reconciliation below.
    spatial_nonnegative = [max(value, 0.0) for value in spatial_balances]
    spatial_total = sum(spatial_nonnegative)
    inventory_balance = _number(corrected_rows[0].get("INVENTORY_BALANCE_WMT"))
    unattributed_movement = _number(
        corrected_rows[0].get("UNATTRIBUTED_MOVEMENT_WMT"), 0.0
    )
    raw_stockpile_total = raw_hex_total + unattributed_movement
    raw_positive_total = (
        sum(max(value, 0.0) for value in raw_balances)
        + max(unattributed_movement, 0.0)
    )

    final_balances = list(spatial_nonnegative)
    ledger_adjustments = [0.0] * len(corrected_rows)
    inventory_recon_deductions = [0.0] * len(corrected_rows)
    lineage_coverages = [
        _lineage_coverage(row) for row in corrected_rows
    ]
    outcome = AMT_OUTCOME_RECONCILED
    reason = "Spatially corrected raw hex tonnes reconciled to inventory."
    if raw_hex_total <= 0:
        outcome = AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW
        reason = "Sum of raw hex RAW_WMT is at or below zero; all final tonnes are zero and inventory is not allocated."
        final_balances = [0.0] * len(corrected_rows)
        ledger_adjustments = [-value for value in spatial_nonnegative]
    elif inventory_balance is not None:
        if inventory_balance <= 0:
            outcome = AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY
            reason = "Inventory balance is at or below zero; all final footprint tonnes are zero."
        inventory_balance = max(inventory_balance, 0.0)
        if spatial_total > 1e-9:
            if inventory_balance < spatial_total - 1e-9:
                inventory_recon_deductions = (
                    _lineage_weighted_inventory_deductions(
                        spatial_nonnegative,
                        inventory_balance,
                        lineage_coverages,
                    )
                )
                final_balances = [
                    max(spatial - deduction, 0.0)
                    for spatial, deduction in zip(
                        spatial_nonnegative,
                        inventory_recon_deductions,
                    )
                ]
            else:
                scale = inventory_balance / spatial_total
                final_balances = [
                    value * scale for value in spatial_nonnegative
                ]
        elif corrected_rows:
            final_balances = [0.0] * len(corrected_rows)
            final_balances[0] = inventory_balance
        ledger_adjustments = [
            final - spatial
            for final, spatial in zip(final_balances, spatial_nonnegative)
        ]
    else:
        outcome = AMT_OUTCOME_RETAINED_INVENTORY_UNAVAILABLE
        reason = "Inventory balance is unavailable; spatially reconciled AMT tonnes are retained."

    final_total = sum(final_balances)
    status = "OK_SPATIAL_AND_INVENTORY_RECONCILED"
    if outcome in (AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW, AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY):
        status = outcome.upper()
    elif unresolved_total > 0.01:
        status = "WARN_INSUFFICIENT_LOCAL_POSITIVE_TONNES_GLOBAL_FALLBACK_USED"
    elif geometry_fallback_used:
        status = "WARN_MISSING_GEOMETRY_DISTANCE_FALLBACK_USED"
    elif inventory_balance is None:
        status = "WARN_INVENTORY_BALANCE_UNAVAILABLE_SPATIAL_ONLY"

    footprint_audit = amt_footprint_audit(
        corrected_rows[0].get("FOOTPRINT", corrected_rows[0].get("footprint")),
        outcome=outcome, outcome_reason=reason, raw_wmt=raw_hex_total,
        spatially_reconciled_wmt=spatial_total,
        inventory_wmt=_number(corrected_rows[0].get("INVENTORY_BALANCE_WMT")),
        final_wmt=final_total, source_rows=len(corrected_rows),
    )
    for index, row in enumerate(corrected_rows):
        raw_balance = raw_balances[index]
        direction = directions.get(index)
        row.update({
            "RAW_WMT": raw_balance,
            "SPATIALLY_CORRECTED_WMT": spatial_nonnegative[index],
            "SPATIAL_ADJUSTMENT_WMT": spatial_nonnegative[index] - raw_balance,
            "LEDGER_ADJUSTMENT_WMT": ledger_adjustments[index],
            "INVENTORY_RECON_DEDUCTION_WMT": (
                inventory_recon_deductions[index]
            ),
            "INVENTORY_RECON_LINEAGE_COVERAGE_PCT": (
                lineage_coverages[index] * 100.0
            ),
            "FINAL_WMT": final_balances[index],
            "SPATIAL_DEFICIT_WMT": max(-raw_balance, 0.0),
            "SPATIAL_DEFICIT_FILLED_WMT": deficit_filled.get(index, 0.0),
            "SPATIAL_DONOR_WMT": donor_used.get(index, 0.0),
            "SPATIAL_UNRESOLVED_WMT": remaining_deficits.get(index, 0.0),
            "RAW_STOCKPILE_WMT": raw_stockpile_total,
            "RAW_HEX_STOCKPILE_WMT": raw_hex_total,
            "RAW_POSITIVE_STOCKPILE_WMT": raw_positive_total,
            "SPATIALLY_CORRECTED_STOCKPILE_WMT": spatial_total,
            "FINAL_STOCKPILE_WMT": final_total,
            "SPATIAL_RECON_STATUS": status,
            "SPATIAL_RECON_REASON": reason,
            "AMT_FOOTPRINT_AUDIT": deepcopy(footprint_audit),
            "SPATIAL_RECON_METHOD": SPATIAL_METHOD,
            "RECLAIM_DIRECTION_EASTING": direction[0] if direction else None,
            "RECLAIM_DIRECTION_NORTHING": direction[1] if direction else None,
        })
    return corrected_rows
