"""Resolve evidence and editable setup state without consuming ROM capacity."""

from collections import defaultdict
from copy import deepcopy

from classes.DestinationBuildOrder import digest
from setup.InventoryBuildLineage import finite_number
from setup.ProductAssayHistory import awst


ESTIMATED_CAPACITY_BASIS = "Estimated from remaining 2WP deliveries"
ASSUMED_FIRST_BUILD_BASIS = "Assumed first 2WP build — no matching activity"


def remaining_2wp_estimates(order, scenario_start):
    """Sum future material deliveries into each shared physical build balance.

    Intervals crossing scenario start use uniform delivery over their duration.
    Missing interval evidence stays unavailable; a fully elapsed build yields zero.
    """
    if scenario_start is None:
        return {}
    start = awst(scenario_start)
    totals, invalid = defaultdict(float), set()
    for entry in order["orders"]:
        key = entry["instance_id"]
        windows = entry.get("inbound_windows")
        if not windows:
            invalid.add(key)
            continue
        for begin, end, tonnes in windows:
            begin, end = awst(begin), awst(end)
            quantity = finite_number(tonnes)
            if end <= begin or quantity is None or quantity < 0:
                invalid.add(key)
                continue
            fraction = max(0.0, min(1.0, (end - start).total_seconds() / (end - begin).total_seconds()))
            totals[key] += quantity * fraction
    return {key: value for key, value in totals.items() if key not in invalid}


def progress_settings(value=None):
    value = value or {}
    if not isinstance(value, dict) or type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise ValueError("Unsupported destination reconciliation settings.")
    hours = finite_number(value.get("lookback_hours", 12))
    if hours is None or not 0 < hours <= 744:
        raise ValueError("Activity lookback must be greater than 0 and at most 744 hours.")
    remaining, choices = value.get("remaining_wmt", {}), value.get("selected_instances", {})
    if not isinstance(remaining, dict) or not isinstance(choices, dict):
        raise ValueError("Invalid destination reconciliation settings.")
    clean = {}
    for key, raw in remaining.items():
        number = finite_number(raw)
        if number is None or number < 0 or isinstance(raw, bool):
            raise ValueError("Remaining assignable tonnes must be a non-negative ROM WMT value.")
        clean[str(key)] = number
    return dict(schema_version=1, lookback_hours=hours, context_signature=str(value.get("context_signature", "")),
                remaining_wmt=clean, selected_instances={str(k): str(v) for k, v in choices.items()})


def lane_key(area, material):
    return digest([area, material])[:24]


def resolve_progress(order, activity, selections=None):
    """Match actuals to each 2WP lane; an empty successful lookup starts its order."""
    lanes, movements = defaultdict(list), defaultdict(list)
    for row in order["orders"]:
        lanes[(row["rom_area"], row["material_type"])].append(row)
    for row in activity.get("records", []):
        movements[(row["rom_area"], row["material_type"])].append(row)
    result = []
    for lane, sequence in sorted(lanes.items()):
        allowed = {r["destination"] for r in sequence}
        evidence = sorted((r for r in movements[lane] if r["destination"] in allowed),
                          key=lambda r: (r["observed_at"], r["destination"]), reverse=True)
        warnings = []
        excluded = len(movements[lane]) - len(evidence)
        if excluded:
            warnings.append(f"{excluded} actual movements were excluded from detection because their destinations are outside this ROM area/material type's 2WP order. They remain visible in Actual movements.")
        latest = evidence[0]["observed_at"] if evidence else None
        latest_destinations = sorted({r["destination"] for r in evidence if r["observed_at"] == latest})
        active = latest_destinations[0] if len(latest_destinations) == 1 else None
        candidates = [r for r in sequence if r["destination"] in latest_destinations]
        current = candidates[0] if len(candidates) == 1 and active else None
        if len({r["destination"] for r in evidence}) > 1:
            warnings.append("Multiple destinations were active; the latest inbound movement determines the detected destination.")
        if len(latest_destinations) > 1:
            warnings.append("Latest movements have equal timestamps at different destinations. Select the current build instance after review.")
        elif len(candidates) > 1:
            warnings.append("Detected destination has repeated build instances. Actual activity can lead or lag 2WP dates; select the current build instance after review.")
        assumed = not evidence and str(activity.get("status", "")).lower() in {"fresh", "cached"} and not activity.get("error")
        if assumed:
            current = min(sequence, key=lambda r: r["order_position"])
        elif not evidence:
            warnings.append("No matching activity and no successful activity lookup is available; the current build instance is unconfirmed.")
        key = lane_key(*lane)
        selected = (selections or {}).get(key)
        manual = next((r for r in sequence if r["instance_id"] == selected), None)
        if manual:
            current = manual
        elif assumed:
            warnings.append("No matching activity in the lookback window. Starting at the first 2WP build is a planning assumption; it does not prove the stockpile has not been used.")
        index = sequence.index(current) if current else None
        result.append(dict(lane_key=key, rom_area=lane[0], material_type=lane[1], sequence=deepcopy(sequence),
                           detected_destination=active, current=deepcopy(current), selection_basis="User selected" if manual else ASSUMED_FIRST_BUILD_BASIS if assumed else "Latest inbound" if current else "Unconfirmed",
                           previous=deepcopy(sequence[index-1]) if index is not None and index > 0 else None,
                           next=deepcopy(sequence[index+1]) if index is not None and index+1 < len(sequence) else None,
                           latest_inbound=latest, activity_wmt=sum(r["wmt"] for r in evidence), evidence=deepcopy(evidence),
                           warnings=warnings, ambiguous=current is None and bool(evidence)))
    return result
