"""Reproduce RECONCILIATION_TWO_PITS.md with the application engine, offline.

Run from the project directory: python docs/examples/reconciliation_two_pits.py
No database, network, GUI or application state is read or changed.
"""

from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from classes.PhaseSchemas import reconciliation_sample
from classes.ReconciliationApplication import ReconciliationApplication
from classes.ReconciliationControls import confidence_search_labels, spatial_cell


START = datetime(2026, 8, 11)
HG = "PITA|1|100|10|1|HG01"
BA = "PITB|1|100|10|1|BA01"
OTHER_BA = "PITA|1|100|10|1|BA01"
SOURCE = [{"grade_block_key": HG, "feed_wmt": 500},
          {"grade_block_key": BA, "feed_wmt": 500}]
OFFSETS = {"blend": dict(fe=0, si=-.1, al=-.05, p=.1, mn=-.2),
           "regression": dict(fe=-.1, si=0, al=.05, p=-.1, mn=.1)}
STREAMS = {"modelled_rom": {"SF": dict(fe=50, si=5, al=3, p=.1, mn=.2)},
           "modelled_product": {"SF": dict(fe=60, si=4, al=2, p=.08, mn=.15)}}
GLOBAL = {kind: {a: {"effective": 1.0} for a in offsets}
          for kind, offsets in OFFSETS.items()}


def address(block, changed):
    parts = block.split("|")
    for index, value in changed.items():
        parts[index] = str(value)
    return "|".join(parts)


# Number, day, hour, WMT, Pit A HG %, Pit B BA %, changed address parts, Blend Fe.
# Remaining feed is Pit A BA: it matches neither source component at any level.
SHIFTS = [
    (1, 1, 6, 100, 50, 50, {1: 2}, .90),
    (2, 2, 6, 100, 50, 50, {2: 200}, .95),
    (3, 3, 6, 100, 50, 50, {3: 20}, 1.00),
    (4, 4, 6, 100, 50, 50, {4: 2}, 1.10),
    (5, 4, 12, 300, 100, 0, {}, 1.20),
    (6, 7, 6, 100, 10, 10, {}, 1.15),
    (7, 9, 6, 100, 0, 100, {}, 1.05),
    (8, 10, 6, 50, 30, 30, {4: 2}, 1.10),
]
SHIFT_IDS = {datetime(2026, 8, day, hour).isoformat(): number
             for number, day, hour, *_ in SHIFTS}


def history():
    samples = []
    for number, day, hour, feed, hg, ba, changed, blend_fe in SHIFTS:
        start = datetime(2026, 8, day, hour)
        end = start + timedelta(hours=6 if day == 4 else 12)
        blocks = [{"grade_block_key": block, "feed_wmt": feed * percent / 100}
                  for block, percent in ((address(HG, changed), hg),
                                         (address(BA, changed), ba), (OTHER_BA, 100 - hg - ba))
                  if percent > 0]
        for kind, offsets in OFFSETS.items():
            samples.append(reconciliation_sample(
                "CB OPF", "SF", kind, start, end, feed_wmt=feed,
                contributing_blocks=blocks, factors={a: blend_fe + offset for a, offset in offsets.items()},
                source_rows=1, provenance={"sample_id": f"shift-{number}-{kind}", "lineage_basis": "worked example"}))
    return samples


def run(method, window="calendar_days", minimum=2, cells=(), source=None):
    engine = ReconciliationApplication(
        samples=history(), standard_factors={"SF": GLOBAL}, opf="CB OPF", brands=["SF"],
        scenario_start=START, settings=dict(method=method, window_mode=window, lookback_days=4,
                                          min_production_days=minimum, max_lookback_days=10, cells=cells))
    values, audit = engine.apply(STREAMS, source_id="Stockpile 1", source_kind="inventory",
                                 source_wmt=1000, contributing_blocks=SOURCE if source is None else source)
    result = audit["by_brand"]["SF"]
    result["adjusted_rom_fe"] = values["adjusted_rom"]["SF"]["fe"]
    result["adjusted_product_fe"] = values["adjusted_product"]["SF"]["fe"]
    return result


def compact(result):
    search = result.get("auto_selection", {})
    return dict(
        score=result["confidence_percent"], global_percent=100 * result["global_fraction"],
        lineage_percent=100 * result["lineage_coverage"], manual_percent=100 * result["manual_override_fraction"],
        blend_fe=result["applied_factors"]["blend"]["fe"], regression_fe=result["applied_factors"]["regression"]["fe"],
        adjusted_rom_fe=result["adjusted_rom_fe"], adjusted_product_fe=result["adjusted_product_fe"],
        selected_window=confidence_search_labels(result), history_selection=search.get("history_approach"),
        components=[dict(block=r["grade_block_key"], level=r["resolution_level"], score=r["confidence_percent"],
                         blend_fe=r["blend_factors"]["fe"],
                         shifts=[SHIFT_IDS[p["period_start"]] for p in r["source_history"] if "period_start" in p])
                    for r in result["records"]],
    )


def main():
    results = {window: run("lookback", window) for window in ("calendar_days", "production_days", "latest_campaign")}
    results["spatial"] = run("spatial_compositional")
    results["auto"] = run("auto_max_confidence")
    # Show the existing competing Component-based result in isolation.
    with patch("classes.ReconciliationSharedHistory.SharedHistorySearch.evaluate", return_value=None):
        results["auto_component_based_only"] = run("auto_max_confidence")
    results["auto_minimum_1"] = run("auto_max_confidence", minimum=1)
    results["auto_minimum_3"] = run("auto_max_confidence", minimum=3)
    local = dict(opf="CB OPF", brand="SF", cell=spatial_cell(HG), analyte="fe", blend=1.2)
    results["local_blend"] = run("auto_max_confidence", cells=[local])
    local = dict(opf="CB OPF", brand="SF", cell=spatial_cell(HG), analyte="fe", window=dict(min_production_days=3))
    results["local_minimum_3"] = run("auto_max_confidence", cells=[local])
    results["unknown_20_percent"] = run("auto_max_confidence", source=[{**r, "feed_wmt": 400} for r in SOURCE])
    # Independent arithmetic for the headline results printed in the guide.
    expected = {
        "calendar_days": (185 / 6, 67 / 60, [[6, 8], [6, 7]]),
        "production_days": (38.75, 1.14375, [[5, 6], [6, 7]]),
        "latest_campaign": (70 / 3, 31 / 30, [[], [7, 8]]),
        "spatial": (38.75, 1.14375, [[5, 6], [6, 7]]),
        "auto": (175 / 3, 1.05, [[3, 4], [3, 4]]),
        "auto_component_based_only": (335 / 6, 1.1075, [[3, 4, 5], [4, 7]]),
        "auto_minimum_1": (200 / 3, 1.10, [[4], [4]]),
        "auto_minimum_3": (164 / 3, 1.06, [[3, 4, 8], [3, 4, 8]]),
        "local_blend": (175 / 3, 1.125, [[3, 4], [3, 4]]),
        "unknown_20_percent": (140 / 3, 1.04, [[3, 4], [3, 4], []]),
    }
    for key, (score, factor, selected) in expected.items():
        value = compact(results[key])
        assert math.isclose(value["score"], score, abs_tol=1e-10), key
        assert math.isclose(value["blend_fe"], factor, abs_tol=1e-10), key
        assert [r["shifts"] for r in value["components"]] == selected, key
    assert results["auto"]["auto_selection"]["history_approach"] == "shared_history"
    assert results["auto_minimum_1"]["auto_selection"]["history_approach"] == "component_based"
    assert math.isclose(results["auto"]["adjusted_product_fe"], 57)
    for record in results["auto"]["records"]:
        for kind, offsets in OFFSETS.items():
            for analyte, offset in offsets.items():
                assert math.isclose(record[kind + "_factors"][analyte], 1.05 + offset)
    output = {key: compact(value) for key, value in results.items()}
    output["auto_search"] = results["auto"]["auto_selection"]
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
