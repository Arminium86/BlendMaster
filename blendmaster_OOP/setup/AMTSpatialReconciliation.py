"""Spatial correction for AMT hex overdraw caused by reclaim attribution noise.

The correction is deliberately tonnage-only.  It does not move or recalculate
grades.  Negative hex tonnes are treated as reclaim deficits and transferred to
nearby positive hexes, preferring the inferred direction back into the remaining
stockpile.  A final stockpile-level adjustment reconciles the corrected hexes to
the authoritative inventory balance.
"""

from __future__ import annotations

from math import atan2, cos, hypot, radians, sin, sqrt
from statistics import median


SPATIAL_METHOD = (
    "directional_nearest_capacity_v1: negative hex deficits are allocated to "
    "positive hexes by distance, connected geometry, and inferred reclaim-front "
    "direction; the inventory balance is then applied proportionally"
)


def _number(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
    raw_stockpile_total = sum(raw_balances) + unattributed_movement
    raw_positive_total = (
        sum(max(value, 0.0) for value in raw_balances)
        + max(unattributed_movement, 0.0)
    )

    final_balances = list(spatial_nonnegative)
    ledger_adjustments = [0.0] * len(corrected_rows)
    if inventory_balance is not None:
        inventory_balance = max(inventory_balance, 0.0)
        if spatial_total > 1e-9:
            scale = inventory_balance / spatial_total
            final_balances = [value * scale for value in spatial_nonnegative]
        elif corrected_rows:
            final_balances = [0.0] * len(corrected_rows)
            final_balances[0] = inventory_balance
        ledger_adjustments = [
            final - spatial
            for final, spatial in zip(final_balances, spatial_nonnegative)
        ]

    final_total = sum(final_balances)
    status = "OK_SPATIAL_AND_INVENTORY_RECONCILED"
    if unresolved_total > 0.01:
        status = "WARN_INSUFFICIENT_LOCAL_POSITIVE_TONNES_GLOBAL_FALLBACK_USED"
    elif geometry_fallback_used:
        status = "WARN_MISSING_GEOMETRY_DISTANCE_FALLBACK_USED"
    elif inventory_balance is None:
        status = "WARN_INVENTORY_BALANCE_UNAVAILABLE_SPATIAL_ONLY"

    for index, row in enumerate(corrected_rows):
        raw_balance = raw_balances[index]
        direction = directions.get(index)
        row.update({
            "RAW_WMT": raw_balance,
            "SPATIALLY_CORRECTED_WMT": spatial_nonnegative[index],
            "SPATIAL_ADJUSTMENT_WMT": spatial_nonnegative[index] - raw_balance,
            "LEDGER_ADJUSTMENT_WMT": ledger_adjustments[index],
            "FINAL_WMT": final_balances[index],
            "SPATIAL_DEFICIT_WMT": max(-raw_balance, 0.0),
            "SPATIAL_DEFICIT_FILLED_WMT": deficit_filled.get(index, 0.0),
            "SPATIAL_DONOR_WMT": donor_used.get(index, 0.0),
            "SPATIAL_UNRESOLVED_WMT": remaining_deficits.get(index, 0.0),
            "RAW_STOCKPILE_WMT": raw_stockpile_total,
            "RAW_POSITIVE_STOCKPILE_WMT": raw_positive_total,
            "SPATIALLY_CORRECTED_STOCKPILE_WMT": spatial_total,
            "FINAL_STOCKPILE_WMT": final_total,
            "SPATIAL_RECON_STATUS": status,
            "SPATIAL_RECON_METHOD": SPATIAL_METHOD,
            "RECLAIM_DIRECTION_EASTING": direction[0] if direction else None,
            "RECLAIM_DIRECTION_NORTHING": direction[1] if direction else None,
        })
    return corrected_rows
