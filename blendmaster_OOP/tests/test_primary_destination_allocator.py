from copy import deepcopy
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest

import pandas as pd

from classes.DestinationBuildOrder import build_order, digest
from classes.DestinationProgress import lane_key, progress_settings
from classes.MaterialDestinationPlan import MaterialDestinationPlan
from classes.PrimaryDestinationAllocator import AUDIT_COLUMNS, PrimaryDestinationAllocator, allocate_final_plan, write_allocation_audit
from database.SQLiteDatabase import DatabaseManager
from tests.test_destination_activity import START, AREAS
from tests.test_destination_build_order import fixture, movement


def context(capacity=100, *, shared=False):
    frame = fixture()
    if shared:
        frame.loc[2, "Source.FullName"] = movement(material="BA")["Source.FullName"]
        frame.loc[5, "Time.StartTime"] = "2026-09-08 11:00"
        frame.loc[5, "Time.EndTime"] = "2026-09-08 12:00"
    order = build_order(frame, {**AREAS, "SP3": "CR1"} if shared else AREAS)
    first = order["audit"][0]["instance_id"]
    selections = {lane_key("CR1", "HG"): first}
    if shared:
        selections[lane_key("CR1", "BA")] = first
    signature = digest(["scenario-a", "CC", START.isoformat(), order["signature"]])
    settings = progress_settings(dict(context_signature=signature, selected_instances=selections,
                                     remaining_wmt={first: capacity} if capacity is not None else {}))
    return dict(order=order, activity={"records": []}, settings=settings, context_signature=signature,
                scenario_id="scenario-a", site="CC", start=START.isoformat())


def payload(number, tonnes=150, *, material="HG", destination="SP1", direct=0, **changes):
    return dict(payload_id=f"P{number:03}", source=movement(material=material)["Source.FullName"],
                delivered_datetime=START+timedelta(minutes=number), destination=destination,
                payload_wmt=tonnes, direct_tipped_wmt=direct, non_direct_wmt=tonnes-direct, **changes)


def allocator(ctx=None, **owner):
    ctx = ctx or context()
    return PrimaryDestinationAllocator(ctx["order"], ctx["activity"], ctx["settings"], **owner)


def transactions(rows):
    return pd.DataFrame([dict(direct_tip_id=r["payload_id"], source=r["source"], payload=r["payload_wmt"],
                              delivered_datetime=r["delivered_datetime"], destination=r["destination"],
                              planned_destination=r["destination"], route_only_waste=r.get("route_only_waste", False)) for r in rows])


class PrimaryDestinationAllocatorTests(unittest.TestCase):
    def test_whole_payload_crosses_capacity_then_next_payload_advances(self):
        engine = allocator()
        rows = engine.allocate([payload(1), payload(2, 40)])
        self.assertEqual([r["assigned_destination"] for r in rows], ["SP1", "SP2"])
        self.assertEqual([(r["capacity_before_wmt"], r["assigned_wmt"], r["capacity_after_wmt"], r["overrun_wmt"]) for r in rows],
                         [(100, 150, 0, 50), (100, 40, 60, 0)])
        result = engine.result()
        self.assertEqual([r["event"] for r in result["ledger"]], ["Allocate", "Advance", "Allocate"])
        self.assertEqual(result["ledger"][1]["next_destination"], "SP2")
        self.assertEqual(result["run"]["assigned_wmt"], 190)

    def test_zero_skips_but_unset_requires_review(self):
        rows = allocator(context(0)).allocate([payload(1, 30)])
        self.assertEqual(rows[0]["assigned_destination"], "SP2")
        engine = allocator(context(None))
        row = engine.allocate([payload(1)])[0]
        self.assertEqual(row["unresolved_wmt"], 150)
        self.assertIn("not set", row["reason"])
        self.assertEqual(engine.result()["ledger"], [])
        self.assertEqual(engine.result()["run"]["status"], "Needs review")

    def test_shared_materials_consume_one_physical_capacity(self):
        ctx = context(shared=True)
        engine = allocator(ctx)
        rows = engine.allocate([payload(1, 70), payload(2, 50, material="BA"), payload(3, 20), payload(4, 10, material="BA")])
        self.assertEqual([r["assigned_destination"] for r in rows], ["SP1", "SP1", "SP2", "SP3"])
        self.assertEqual(rows[1]["capacity_before_wmt"], 30)
        self.assertEqual(rows[1]["overrun_wmt"], 20)
        first = next(r for r in engine.result()["capacities"] if r["instance_id"] == rows[0]["instance_id"])
        self.assertEqual((first["planned_wmt"], first["starting_wmt"], first["consumed_wmt"]), (200, 100, 120))

    def test_later_capacity_sums_material_rows_and_repeated_builds_are_independent(self):
        ctx = context(shared=True)
        # Begin at zero capacity so both material lanes advance from SP1.
        first = ctx["order"]["audit"][0]["instance_id"]
        ctx["settings"]["remaining_wmt"][first] = 0
        engine = allocator(ctx)
        rows = engine.allocate([payload(1, 120), payload(2, 80), payload(3, 30), payload(4, 10)])
        self.assertEqual([(r["assigned_destination"], r["build_instance"]) for r in rows],
                         [("SP2", 1), ("SP1", 2), ("SP1", 2), ("", None)])
        self.assertEqual(rows[2]["overrun_wmt"], 10)
        self.assertIn("exhausted", rows[3]["reason"])
        self.assertNotEqual(rows[1]["instance_id"], first)
        # With an earlier separate destination, later SP1's HG and BA planned
        # quantities provide one total capacity, not two independent budgets.
        ctx["settings"]["selected_instances"][lane_key("CR1", "HG")] = ctx["order"]["audit"][1]["instance_id"]
        ctx["settings"]["selected_instances"].pop(lane_key("CR1", "BA"))
        ctx["settings"]["remaining_wmt"] = {ctx["order"]["audit"][1]["instance_id"]: 100}
        instance = next(r for r in allocator(ctx).result()["capacities"] if r["instance_id"] == first)
        self.assertEqual(instance["starting_wmt"], 200)

    def test_unconfirmed_off_order_and_unknown_material_never_guess(self):
        ctx = context()
        ctx["settings"]["selected_instances"] = {}
        for activity in ({"records": []}, {"records": [dict(rom_area="CR1", material_type="HG", observed_at=START.isoformat(), destination="SP4", wmt=100)]}):
            ctx["activity"] = activity
            row = allocator(ctx).allocate([payload(1)])[0]
            self.assertEqual(row["assigned_destination"], "")
            self.assertIn("unconfirmed", row["reason"])
        engine = allocator()
        rows = engine.allocate([payload(1, material="LG"), payload(2, destination="UNKNOWN")])
        self.assertTrue(all(r["unresolved_wmt"] == 150 for r in rows))
        self.assertEqual(engine.result()["ledger"], [])

    def test_chronological_batches_retries_and_plan_isolation(self):
        ctx = context()
        original = deepcopy(ctx)
        batch = [payload(3, 20), payload(1, 70), payload(2, 50)]
        one = allocator(ctx)
        one.allocate(batch)
        streamed = allocator(ctx)
        first = streamed.allocate([batch[1]])
        self.assertEqual(streamed.allocate([batch[1]]), first)
        streamed.allocate([batch[2], batch[0]])
        self.assertEqual(streamed.result(), one.result())
        self.assertEqual(ctx, original)
        self.assertEqual([r["capacity_before_wmt"] for r in one.result()["assignments"]], [100, 30, 100])
        alternative = allocator(ctx, plan_id="Contingency 1")
        self.assertEqual(alternative.allocate([payload(1, 10)])[0]["capacity_before_wmt"], 100)

    def test_invalid_batches_and_changed_retries_do_not_mutate_capacity(self):
        engine = allocator()
        engine.allocate([payload(2, 20)])
        before = engine.result()
        invalid = payload(4); invalid["non_direct_wmt"] = -1
        for rows in ([payload(3), invalid], [payload(2, 21)], [payload(1)], [payload(3), payload(3)]):
            with self.assertRaises(ValueError):
                engine.allocate(rows)
            self.assertEqual(engine.result(), before)

    def test_direct_tip_waste_and_invalid_times_do_not_consume_rom_capacity(self):
        invalid = payload(3); invalid["delivered_datetime"] = None
        engine = allocator()
        rows = engine.allocate([payload(1, 100, direct=100), payload(2, route_only_waste=True), invalid])
        self.assertEqual([r["status"] for r in rows], ["Direct tip only", "Outside ROM scope", "Unresolved"])
        self.assertEqual(engine.result()["ledger"], [])
        self.assertEqual(engine.allocate([payload(4, 10)])[0]["capacity_before_wmt"], 100)

    def test_final_report_reconciliation_preserves_payloads_and_fractional_tonnes(self):
        ctx = context(100.123456789)
        inputs = transactions([payload(1, 150), payload(2, 90), payload(3, 100)])
        report = pd.DataFrame([dict(source_type="grade_block", source=inputs.iloc[0]["source"], source_id="P001, P002", source_actual_tonnes=96)])
        detail = MaterialDestinationPlan.build_payload_assignments(inputs, report, "optimised")
        self.assertEqual(set(detail["payload_id"]), {"P001", "P002", "P003"})
        result = allocate_final_plan(inputs, report, ctx)
        self.assertAlmostEqual(result["run"]["direct_tipped_wmt"], 96)
        self.assertAlmostEqual(result["run"]["assigned_wmt"], 244)
        self.assertAlmostEqual(result["run"]["payload_wmt"], 340)
        self.assertAlmostEqual(result["assignments"][0]["capacity_after_wmt"], 10.123456789)
        self.assertAlmostEqual(result["assignments"][1]["overrun_wmt"], 43.876543211)
        self.assertEqual(result["assignments"][2]["assigned_destination"], "SP2")

    def test_partial_plan_only_consumes_the_finalised_awst_window(self):
        inputs = transactions([payload(0, 10), payload(1, 20), payload(2, 30), payload(3, 40)])
        inputs.loc[0, "delivered_datetime"] = START-timedelta(minutes=1)
        report = pd.DataFrame([dict(source_type="stockpile", source_actual_tonnes=100, end_datetime=START+timedelta(minutes=2))])
        result = allocate_final_plan(inputs, report, context())
        self.assertEqual(result["run"]["assigned_wmt"], 20)
        self.assertEqual(result["run"]["outside_window_wmt"], 80)
        self.assertEqual(len(result["ledger"]), 1)
        empty = allocate_final_plan(inputs, report.iloc[:0], context())
        self.assertEqual(empty["run"]["assigned_wmt"], 0)

    def test_crusher_candidate_residual_uses_stockpile_anchor(self):
        inputs = transactions([payload(1, 100)])
        inputs.loc[0, "planned_destination"] = "Crushers/CR1"
        inputs.loc[0, "fallback_destination"] = "Stockpiles/SP1"
        inputs.loc[0, "destination"] = "Stockpiles/SP1"
        row = allocate_final_plan(inputs, pd.DataFrame(), context())["assignments"][0]
        self.assertEqual(row["assigned_destination"], "SP1")

    def test_database_audit_is_replaced_per_plan_and_roundtrips_precision(self):
        inputs = transactions([payload(1, 150.123456789)])
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"plans.db")
            manager = DatabaseManager()
            for kind, name in (("optimised", "Primary"), ("optimised", "Contingency 1"), ("manual", "Primary"), ("optimised", "Primary")):
                manager.write_material_destination_plan_to_database(inputs, pd.DataFrame(), kind, plan_id=name,
                                                                    database_name=path, destination_reconciliation=context())
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute("select count(*) from destination_allocation_runs").fetchone()[0], 3)
                self.assertEqual(c.execute("select count(*) from destination_primary_assignments").fetchone()[0], 3)
                self.assertEqual(c.execute("select count(*) from destination_capacity_ledger").fetchone()[0], 6)
                overrun = c.execute("select overrun_wmt from destination_primary_assignments limit 1").fetchone()[0]
                self.assertAlmostEqual(overrun, 50.123456789)
            unavailable = allocate_final_plan(inputs, pd.DataFrame(), None)
            write_allocation_audit(unavailable, path)
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute("select count(*) from destination_primary_assignments").fetchone()[0], 2)
                self.assertEqual(c.execute("select status from destination_allocation_runs where plan_type='optimised' and plan_id='Primary'").fetchone()[0], "Unavailable")

    def test_stale_context_is_rejected_and_audit_write_is_atomic(self):
        inputs = transactions([payload(1)])
        ctx = context(); ctx["settings"]["context_signature"] = "old"
        with self.assertRaisesRegex(ValueError, "context"):
            allocate_final_plan(inputs, pd.DataFrame(), ctx)
        result = allocate_final_plan(inputs, pd.DataFrame(), context())
        self.assertEqual(json.loads(json.dumps(result)), result)
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"audit.db")
            write_allocation_audit(result, path)
            broken = deepcopy(result)
            broken["assignments"][0]["assigned_wmt"] = 999
            broken["ledger"][0]["event"] = object()
            with self.assertRaises(sqlite3.Error):
                write_allocation_audit(broken, path)
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute("select assigned_wmt from destination_primary_assignments").fetchone()[0], 150)

    def test_new_run_clears_old_contingencies_and_empty_audits_keep_their_schema(self):
        inputs = transactions([payload(1)])
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"audit.db")
            write_allocation_audit(allocate_final_plan(inputs, pd.DataFrame(), None), path)
            with closing(sqlite3.connect(path)) as c:
                for name, columns in AUDIT_COLUMNS.items():
                    self.assertEqual({r[1] for r in c.execute(f'PRAGMA table_info("{name}")')}, set(columns))
            for kind, name in (("optimised", "Primary"), ("optimised", "Contingency 1"), ("manual", "Primary")):
                write_allocation_audit(allocate_final_plan(inputs, pd.DataFrame(), context(), plan_type=kind, plan_id=name), path)
            DatabaseManager().clear_optimisation_plan_results(path)
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute("select plan_type from destination_allocation_runs").fetchall(), [("manual",)])
            DatabaseManager.clear_scheduling_reports(path)
            with closing(sqlite3.connect(path)) as c:
                self.assertEqual(c.execute("select count(*) from destination_allocation_runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
