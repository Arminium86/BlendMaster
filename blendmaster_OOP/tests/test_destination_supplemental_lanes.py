from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import pandas as pd

from classes.DestinationSupplementalLanes import add_24hr_lanes, SUPPLEMENTAL_ORIGIN, SUPPLEMENTAL_SELECTION, SUPPLEMENTAL_CAPACITY
from classes.DestinationProgress import resolve_progress, lane_key
from classes.PrimaryDestinationAllocator import allocate_final_plan
from tests.test_primary_destination_allocator import context, payload, transactions
from tests.test_destination_build_order import movement
from tests.test_destination_plan_report import publish


def schedule_row(material="SG", destination="SP1", **changes):
    return movement(destination, "2026-09-09 06:00", "2026-09-09 07:00", material,
                    **{"Agent.Name": "EX1", **changes})


def supplemental_context(directory, rows=None):
    ctx = context()
    path = Path(directory) / "24hr.csv"
    pd.DataFrame(rows or [schedule_row()]).to_csv(path, index=False)
    ctx["order"] = add_24hr_lanes(ctx["order"], path, ctx["start"], ["EX1"])
    ctx["activity"] = {"status": "fresh", "records": []}
    return ctx, path


def select_sg(ctx, amount=None, destination="SP1"):
    lane = ctx["order"]["supplemental_lanes"][0]
    entry = next(r for r in lane["candidates"] if r["destination"] == destination)
    ctx["settings"]["selected_instances"][lane_key("CR1", "SG")] = entry["instance_id"]
    if amount is not None:
        ctx["settings"]["remaining_wmt"][entry["instance_id"]] = amount
    return entry


class SupplementalDestinationTests(unittest.TestCase):
    def test_only_missing_active_selected_agent_rom_materials_add_rows(self):
        with TemporaryDirectory() as directory:
            rows = [schedule_row(), schedule_row("HG"), schedule_row("LG", **{"Agent.Name": "EX2"}),
                    schedule_row("SO", **{"Time.StartTime": "2026-09-08 06:00", "Time.EndTime": "2026-09-08 07:00"}),
                    schedule_row("WS"), schedule_row("BA", destination="UNKNOWN")]
            ctx, _ = supplemental_context(directory, rows)
        self.assertEqual(ctx["order"]["orders"], context()["order"]["orders"])
        self.assertEqual(len(ctx["order"]["supplemental_lanes"]), 1)
        lane = ctx["order"]["supplemental_lanes"][0]
        self.assertEqual((lane["rom_area"], lane["material_type"]), ("CR1", "SG"))
        self.assertEqual({r["destination"] for r in lane["candidates"]}, {"SP1", "SP2"})

    def test_no_auto_selection_or_capacity_for_24hr_lane(self):
        with TemporaryDirectory() as directory:
            ctx, _ = supplemental_context(directory)
        row = next(r for r in resolve_progress(ctx["order"], ctx["activity"]) if r["material_type"] == "SG")
        self.assertIsNone(row["current"])
        self.assertEqual(row["selection_basis"], SUPPLEMENTAL_ORIGIN)
        result = allocate_final_plan(transactions([payload(1, material="SG")]), pd.DataFrame(), ctx)
        self.assertIn("select a destination", result["assignments"][0]["reason"])
        select_sg(ctx)
        result = allocate_final_plan(transactions([payload(1, material="SG")]), pd.DataFrame(), ctx)
        self.assertIn("allowance is not set", result["assignments"][0]["reason"])

    def test_allowance_is_explicit_no_automatic_next_or_other_material_capacity(self):
        with TemporaryDirectory() as directory:
            ctx, _ = supplemental_context(directory)
        select_sg(ctx, 50)
        result = allocate_final_plan(transactions([payload(1, 80, material="SG"), payload(2, 20, material="SG"), payload(3, 60)]), pd.DataFrame(), ctx)
        first, second, hg = result["assignments"]
        self.assertEqual((first["assigned_destination"], first["overrun_wmt"]), ("SP1", 30))
        self.assertEqual(first["capacity_basis"], SUPPLEMENTAL_CAPACITY)
        self.assertEqual(first["primary_rule"], SUPPLEMENTAL_SELECTION)
        self.assertIsNone(first["order_position"])
        self.assertIsNone(first["build_instance"])
        self.assertEqual(second["assigned_destination"], "")
        self.assertIn("allowance is exhausted", second["reason"])
        self.assertEqual(hg["capacity_before_wmt"], 100)
        self.assertFalse(any(r["instance_id"].startswith("24hr-") and r["next_destination"] for r in result["ledger"]))

    def test_zero_and_stale_selection_stay_unassigned(self):
        with TemporaryDirectory() as directory:
            ctx, _ = supplemental_context(directory)
        select_sg(ctx, 0)
        row = allocate_final_plan(transactions([payload(1, material="SG")]), pd.DataFrame(), ctx)["assignments"][0]
        self.assertEqual(row["assigned_wmt"], 0)
        self.assertIn("exhausted", row["reason"])
        ctx["settings"]["selected_instances"][lane_key("CR1", "SG")] = "24hr-not-a-current-candidate"
        row = allocate_final_plan(transactions([payload(1, material="SG")]), pd.DataFrame(), ctx)["assignments"][0]
        self.assertIn("select a destination", row["reason"])

    def test_published_origin_and_allowance_are_auditable(self):
        with TemporaryDirectory() as directory:
            ctx, _ = supplemental_context(directory)
        select_sg(ctx, 250)
        original = deepcopy(ctx)
        frames, _ = publish(transactions([payload(1, 214.2, material="SG")]), pd.DataFrame(), ctx, plan_type="manual")
        row = frames["material_destination_plan"].iloc[0]
        self.assertEqual(row.assigned_destination, "SP1")
        self.assertEqual(row.assignment_source, SUPPLEMENTAL_SELECTION)
        self.assertEqual(row.selection_basis, SUPPLEMENTAL_SELECTION)
        self.assertEqual(json.loads(row.destination_rule_trace)["resolution"], "manual_24hr_allowance")
        self.assertAlmostEqual(row.capacity_after_wmt, 35.8)
        self.assertEqual(ctx, original)
