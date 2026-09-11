from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest

import pandas as pd

from classes.DestinationPlanReport import build_publication, read_publication, write_publication, PUBLICATION_COLUMNS
from classes.PrimaryDestinationAllocator import allocate_final_plan
from database.SQLiteDatabase import DatabaseManager
from tests.test_primary_destination_allocator import context, payload, transactions, START
from tests.test_destination_rules import SOURCE, block, guidance, route


def fixture(capacity=50):
    ctx = context(capacity)
    ctx["destination_rules"] = dict(guidance=guidance((block(), "SP1", 200), (block(material="HG02"), "SP2", 100)),
        areas={"SP1": "CR1", "SP2": "CR1", "NEAR": "CR1"}, haul_routes={k: [route("NEAR", 3, k)] for k in ["SP1", "SP2"]})
    rows = transactions([payload(1, 100), payload(2, 50)])
    rows["source"] = SOURCE
    report = pd.DataFrame([dict(source_type="grade_block", source=SOURCE, source_id="P001", source_actual_tonnes=40,
                                end_datetime=START+timedelta(hours=1))])
    return rows, report, ctx


def publish(rows, report, ctx, **owner):
    owner = {"plan_type": "optimised", "plan_id": "Primary", **owner}
    allocation = allocate_final_plan(rows, report, ctx, crusher_destination="Crushers/CR1", **owner)
    frames = build_publication(rows, report, allocation, ctx, crusher_destination="Crushers/CR1", **owner)
    return frames, allocation


class DestinationPlanReportTests(unittest.TestCase):
    def test_summary_publishes_final_destinations_and_conserves_direct_and_rom_tonnes(self):
        frames, allocation = publish(*fixture())
        summary = frames["material_destination_plan"]
        self.assertEqual(len(summary), 3)
        self.assertEqual(summary["assigned_tonnes"].sum(), 150)
        self.assertEqual(summary["reported_wmt"].sum(), 150)
        self.assertEqual(set(summary["source_tonnes"]), {150})
        self.assertAlmostEqual(summary["assigned_ratio"].sum(), 1)
        self.assertEqual(summary["consumed_capacity_wmt"].sum(), 110)
        self.assertEqual(summary["overrun_wmt"].sum(), 10)
        rom = summary[summary.assigned_destination_type.eq("Stockpile")].set_index("assigned_destination")
        self.assertEqual(rom.loc["SP1", "order_position"], 1)
        self.assertEqual(rom.loc["SP2", "order_position"], 2)
        self.assertEqual(rom.loc["SP1", "starting_capacity_wmt"], 50)
        self.assertEqual(rom.loc["SP1", "capacity_after_wmt"], 0)
        self.assertEqual(rom.loc["SP2", "fallback_1_destination"], "Stockpiles/SP1")
        self.assertEqual(rom.loc["SP2", "fallback_2_destination"], "Stockpiles/NEAR")
        self.assertEqual(json.loads(rom.loc["SP1", "transitions"])[0]["next_destination"], "SP2")
        direct = summary[summary.assigned_destination_type.eq("Crusher")].iloc[0]
        self.assertEqual(direct["assigned_destination"], "Crushers/CR1")
        self.assertTrue(pd.isna(direct["capacity_before_wmt"]))
        self.assertEqual(allocation["run"]["assigned_wmt"], 110)

    def test_interleaved_sources_keep_shared_balance_endpoints_and_own_consumption(self):
        rows = transactions([payload(1, 10), payload(2, 20), payload(3, 10)])
        rows.loc[1, "source"] = block(material="HG02")
        frames, _ = publish(rows, pd.DataFrame(), context(100))
        summary = frames["material_destination_plan"]
        row = summary[summary.payload_count.eq(2)].iloc[0]
        self.assertEqual((row.capacity_before_wmt, row.capacity_after_wmt, row.consumed_capacity_wmt), (100, 60, 20))

    def test_repeated_footprint_build_instances_are_never_collapsed(self):
        rows = transactions([payload(i, 150) for i in (1, 2, 3)])
        frames, _ = publish(rows, pd.DataFrame(), context(100))
        sp1 = frames["material_destination_plan"].query("assigned_destination == 'SP1'")
        self.assertEqual(len(sp1), 2)
        self.assertEqual(set(sp1.build_instance), {1, 2})

    def test_unavailable_and_missing_capacity_evidence_do_not_publish_an_aps_guess_as_assignment(self):
        rows, report, _ = fixture()
        legacy = context(None)
        for entry in legacy["order"]["orders"]:
            entry.pop("inbound_windows")
        for ctx in (None, legacy):
            frames, _ = publish(rows, report, ctx)
            summary = frames["material_destination_plan"]
            self.assertEqual(summary.assigned_tonnes.sum(), 40)
            self.assertEqual(summary.unresolved_wmt.sum(), 110)
            self.assertEqual(set(summary.query("assigned_destination_type == 'Stockpile'").assigned_destination), {""})

    def test_partial_plan_excludes_future_capacity_and_labels_waste(self):
        rows = transactions([payload(1, 20, route_only_waste=True), payload(2, 30), payload(3, 40)])
        report = pd.DataFrame([dict(source_type="stockpile", end_datetime=START+timedelta(minutes=3))])
        frames, _ = publish(rows, report, context(100))
        summary = frames["material_destination_plan"]
        self.assertEqual(summary.reported_wmt.sum(), 90)
        self.assertEqual(summary.assigned_tonnes.sum(), 30)
        self.assertEqual(summary.outside_window_wmt.sum(), 40)
        self.assertEqual(summary.out_of_scope_wmt.sum(), 20)
        self.assertEqual(summary.consumed_capacity_wmt.sum(), 30)

    def test_actual_evidence_is_frozen_including_manual_override_and_cache_provenance(self):
        rows, report, ctx = fixture()
        observed = (START-timedelta(minutes=1)).isoformat()
        ctx["activity"] = dict(status="Cached", fetched_at=observed, request=dict(start=(START-timedelta(hours=12)).isoformat(), end=START.isoformat()),
                               records=[dict(movement_id="ACT1", observed_at=observed, destination="SP2", rom_area="CR1", material_type="HG", source_block="PIT_A|1|100|20|105|HG99", wmt=85)])
        frames, allocation = publish(rows, report, ctx)
        summary = frames["material_destination_plan"]
        self.assertEqual(set(summary.detected_destination), {"SP2"})
        self.assertEqual(set(summary.selection_basis), {"User selected"})
        self.assertEqual(set(summary.activity_wmt), {85})
        self.assertEqual(allocation["run"]["activity_status"], "Cached")
        ctx["activity"]["records"][0]["wmt"] = 999
        self.assertEqual(frames["material_destination_plan_activity"].iloc[0].wmt, 85)

    def test_atomic_publication_plan_ownership_and_saved_result_roundtrip(self):
        frames, allocation = publish(*fixture())
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"plans.db")
            write_publication(frames, path, allocation["run"], allocation)
            previous = read_publication(path)
            broken = deepcopy(frames)
            broken["material_destination_plan"].at[0, "reason"] = object()
            changed = deepcopy(allocation); changed["run"]["assigned_wmt"] = 999
            with self.assertRaises(sqlite3.Error):
                write_publication(broken, path, changed["run"], changed)
            self.assertEqual(read_publication(path), previous)
            for kind, plan_id in (("manual", "Primary"), ("optimised", "Contingency 1")):
                other, run = publish(*fixture(), plan_type=kind, plan_id=plan_id)
                write_publication(other, path, run["run"], run)
            self.assertEqual(len(read_publication(path)["destination_allocation_runs"]), 3)
            DatabaseManager().clear_optimisation_plan_results(path)
            snapshot = read_publication(path)
            for name in PUBLICATION_COLUMNS:
                self.assertTrue(all(r["plan_type"] == "manual" for r in snapshot[name]))
            DatabaseManager.clear_scheduling_reports(path)
            self.assertTrue(all(not r for r in read_publication(path).values()))

    def test_opening_legacy_project_preserves_other_plans_and_never_recalculates_capacity(self):
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"legacy.db")
            with closing(sqlite3.connect(path)) as c:
                pd.DataFrame([dict(plan_type="manual", plan_id="Saved", grade_block=SOURCE, assigned_destination="OLD", assigned_tonnes=123.456789)]).to_sql("material_destination_plan", c, index=False)
            manager = DatabaseManager()
            manager.ensure_material_destination_plan_reports(path)
            saved = read_publication(path)["material_destination_plan"][0]
            self.assertEqual(saved["status"], "Legacy snapshot — recalculate")
            self.assertEqual(saved["assigned_tonnes"], 123.456789)
            rows, report, ctx = fixture()
            manager.write_material_destination_plan_to_database(rows, report, "optimised", database_name=path, destination_reconciliation=ctx)
            self.assertEqual(next(r for r in read_publication(path)["material_destination_plan"] if r["plan_type"] == "manual"), saved)

    def test_no_data_publishes_stable_empty_schemas(self):
        frames, allocation = publish(pd.DataFrame(), pd.DataFrame(), context())
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"empty.db")
            write_publication(frames, path, allocation["run"], allocation)
            with closing(sqlite3.connect(path)) as c:
                for name, columns in PUBLICATION_COLUMNS.items():
                    self.assertEqual({r[1] for r in c.execute(f'PRAGMA table_info("{name}")')}, set(columns))
                    self.assertEqual(c.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
