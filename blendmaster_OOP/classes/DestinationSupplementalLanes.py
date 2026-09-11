"""Explicit destination allowances for material lanes absent from the 2WP order."""

from copy import deepcopy
import hashlib
import io
from pathlib import Path

import pandas as pd

from classes.DestinationBuildOrder import build_order, digest
from setup.ProductAssayHistory import awst


SUPPLEMENTAL_ORIGIN = "24-hour plan only — no 2WP build order"
SUPPLEMENTAL_SELECTION = "User selected — 24-hour plan only"
SUPPLEMENTAL_CAPACITY = "User entered — 24-hour plan allowance"


def add_24hr_lanes(order, path, scenario_start, selected_agents=None):
    """Extract only valid remaining reserve-to-ROM inputs, respecting agent filters.

    Candidate destinations are a choice set, never a synthetic 2WP sequence.
    The explicit allowance belongs to this area/material/destination, separately
    from any 2WP physical build whose current position is unknown for this lane.
    """
    if not path:
        return order
    content = Path(path).read_bytes()
    columns = {"Source.Type", "Source.FullName", "Destination.Type", "Destination.Name", "Destination.FullName",
               "Time.StartTime", "Time.EndTime", "Mining.wetTonnes", "Agent.Name", "OriginalSource.Name"}
    frame = pd.read_csv(io.BytesIO(content), usecols=lambda name: name in columns)
    agents = sorted(set(selected_agents or []))
    if agents:
        if "Agent.Name" not in frame:
            raise ValueError("24-hour plan is missing Agent.Name for the selected ExPit agents.")
        frame = frame[frame["Agent.Name"].astype(str).str.strip().isin(agents)].reset_index(drop=True)
    schedule = build_order(frame, order["areas"], source_file=Path(path).name)
    existing = {(r["rom_area"], r["material_type"]) for r in order["orders"]}
    sources = {}
    start = awst(scenario_start) if scenario_start is not None else None
    for row in schedule["audit"]:
        lane = (row["rom_area"], row["material_type"])
        if row["outcome"] != "included" or lane in existing or row["material_type"] in {"WS", "WASTE"}:
            continue
        if start is not None and awst(row["end"]) <= start:
            continue
        sources.setdefault(lane, set()).add(row["source"])
    result = deepcopy(order)
    result["supplemental_lanes"] = []
    for (area, material), blocks in sorted(sources.items()):
        candidates = [dict(rom_area=area, material_type=material, destination=destination,
                           instance_id="24hr-" + digest([area, material, destination])[:24],
                           build_instance=None, order_position=i + 1, planned_wmt=0.0,
                           origin=SUPPLEMENTAL_ORIGIN)
                      for i, destination in enumerate(sorted(k for k, v in order["areas"].items() if v == area))]
        result["supplemental_lanes"].append(dict(rom_area=area, material_type=material, candidates=candidates,
                                                  source_blocks=sorted(blocks), origin=SUPPLEMENTAL_ORIGIN))
    result["signature"] = digest([order["signature"], hashlib.sha256(content).hexdigest(), agents, result["supplemental_lanes"]])
    return result
