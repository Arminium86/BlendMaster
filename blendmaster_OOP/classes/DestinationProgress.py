"""Resolve evidence and editable setup state without consuming ROM capacity."""

from collections import defaultdict
from copy import deepcopy

from classes.DestinationBuildOrder import digest
from setup.InventoryBuildLineage import finite_number


def progress_settings(value=None):
    value = value or {}
    if not isinstance(value, dict) or type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise ValueError("Unsupported destination-progress settings.")
    hours = finite_number(value.get("lookback_hours", 12))
    if hours is None or not 0 < hours <= 744:
        raise ValueError("Activity lookback must be greater than 0 and at most 744 hours.")
    remaining, choices = value.get("remaining_wmt", {}), value.get("selected_instances", {})
    if not isinstance(remaining, dict) or not isinstance(choices, dict):
        raise ValueError("Invalid destination-progress settings.")
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
    """Physical destination follows actual recency; dates do not guess build cycles."""
    lanes, movements = defaultdict(list), defaultdict(list)
    for row in order["orders"]:
        lanes[(row["rom_area"], row["material_type"])].append(row)
    for row in activity.get("records", []):
        movements[(row["rom_area"], row["material_type"])].append(row)
    result = []
    for lane, sequence in sorted(lanes.items()):
        evidence = sorted(movements[lane], key=lambda r: (r["observed_at"], r["destination"]), reverse=True)
        warnings = []
        latest = evidence[0]["observed_at"] if evidence else None
        latest_destinations = sorted({r["destination"] for r in evidence if r["observed_at"] == latest})
        active = latest_destinations[0] if len(latest_destinations) == 1 else None
        candidates = [r for r in sequence if r["destination"] in latest_destinations]
        current = candidates[0] if len(candidates) == 1 and active else None
        if len({r["destination"] for r in evidence}) > 1:
            warnings.append("Multiple destinations were active; the latest inbound movement determines the detected destination.")
        if len(latest_destinations) > 1:
            warnings.append("Latest movements have equal timestamps at different destinations. Select the current build instance after review.")
        elif active and not candidates:
            warnings.append("Detected destination is outside the extracted order. Review the 2WP schedule and activity evidence.")
        elif len(candidates) > 1:
            warnings.append("Detected destination has repeated build instances. Actual activity can lead or lag 2WP dates; select the current build instance after review.")
        if not evidence:
            warnings.append("No qualifying inbound activity in this window; the current build instance is unconfirmed.")
        key = lane_key(*lane)
        selected = (selections or {}).get(key)
        manual = next((r for r in sequence if r["instance_id"] == selected), None)
        if manual:
            current = manual
        index = sequence.index(current) if current else None
        result.append(dict(lane_key=key, rom_area=lane[0], material_type=lane[1], sequence=deepcopy(sequence),
                           detected_destination=active, current=deepcopy(current), selection_basis="User selected" if manual else "Latest inbound" if current else "Unconfirmed",
                           previous=deepcopy(sequence[index-1]) if index is not None and index > 0 else None,
                           next=deepcopy(sequence[index+1]) if index is not None and index+1 < len(sequence) else None,
                           latest_inbound=latest, activity_wmt=sum(r["wmt"] for r in evidence), evidence=deepcopy(evidence),
                           warnings=warnings, ambiguous=current is None and bool(evidence)))
    return result
