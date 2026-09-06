import copy
from contextlib import closing
from datetime import datetime, timedelta
import json
import os
import pickle
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from classes.PhaseSchemas import AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW, AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY
from classes.DataLoader import DataLoader
from classes.AMTChunking import calculate_amt_chunk_plan
from classes.BalanceTracker import BalanceTracker
from classes.EventPoolGenerator import EventPoolGenerator
from setup.AMTSpatialReconciliation import reconcile_amt_hex_rows, guard_amt_snapshot, raw_amt_hex_total, zeroed_amt_footprints
from setup.AMTGradeBlockLineage import compact_amt_stockpile_data
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from tests.test_amt_spatial_reconciliation import row
from tests.test_reconciliation_application import window, raw_hex, chart
from tests.test_custom_constraints import stockpile_record, add_stockpile_calendar_rows, calendar_inputs


def unsafe_snapshot():
    return {"SP1": [{**raw_hex("H1", total=200), "RAW_WMT": -50, "INVENTORY_BALANCE_WMT": 200},
                    {**raw_hex("H2", total=0), "RAW_WMT": 20, "INVENTORY_BALANCE_WMT": 200}]}


class FootprintGuardTests(unittest.TestCase):
    def test_all_non_positive_raw_totals_stay_zero_for_every_inventory_state(self):
        for raw in ((-50, -20), (0, 0), (50, -50), (40, -60)):
            for inventory in (200, 0, -20, None):
                with self.subTest(raw=raw, inventory=inventory):
                    rows = [row(str(i), wmt, i, 0, inventory) for i, wmt in enumerate(raw)]
                    original = copy.deepcopy(rows)
                    result = reconcile_amt_hex_rows(rows)
                    self.assertEqual(rows, original)
                    self.assertEqual([r["FINAL_WMT"] for r in result], [0, 0])
                    audit = result[0]["AMT_FOOTPRINT_AUDIT"]
                    self.assertEqual(audit["outcome"], AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW)
                    self.assertEqual(audit["raw_wmt"], sum(raw))
                    self.assertEqual(audit["inventory_wmt"], inventory)
                    self.assertFalse(audit["eligible_for_processing"])
                    self.assertEqual(result[0]["FINAL_STOCKPILE_WMT"], 0)
                    self.assertEqual(result[0]["SPATIAL_RECON_STATUS"], "ZEROED_NON_POSITIVE_RAW")
                    self.assertEqual(audit, json.loads(json.dumps(audit, allow_nan=False)))

    def test_unattributed_movements_do_not_decide_positivity(self):
        for movement in (-1000, 1000):
            zeroed = reconcile_amt_hex_rows([row("A", 50, 0, 0, 200, movement), row("B", -50, 1, 0, 200, movement)])
            self.assertEqual(sum(r["FINAL_WMT"] for r in zeroed), 0)
            self.assertEqual(zeroed[0]["RAW_STOCKPILE_WMT"], movement)
            self.assertEqual(zeroed[0]["RAW_HEX_STOCKPILE_WMT"], 0)
            positive = reconcile_amt_hex_rows([row("A", 100, 0, 0, 75, movement)])
            self.assertEqual(positive[0]["FINAL_WMT"], 75)
            self.assertEqual(positive[0]["AMT_FOOTPRINT_AUDIT"]["outcome"], "reconciled")

    def test_non_positive_inventory_zeroes_positive_raw_and_preserves_signed_inventory(self):
        for inventory in (0, -100):
            result = reconcile_amt_hex_rows([row("A", 100, 0, 0, inventory)])
            self.assertEqual(result[0]["FINAL_WMT"], 0)
            self.assertEqual(result[0]["AMT_FOOTPRINT_AUDIT"]["outcome"], AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY)
            self.assertEqual(result[0]["AMT_FOOTPRINT_AUDIT"]["inventory_wmt"], inventory)

    def test_missing_inventory_keeps_spatial_mass_despite_negative_unattributed_total(self):
        result = reconcile_amt_hex_rows([row("A", 100, 0, 0, None, -500), row("B", -20, 1, 0, None, -500)])
        self.assertAlmostEqual(sum(r["FINAL_WMT"] for r in result), 80)
        self.assertEqual(result[0]["AMT_FOOTPRINT_AUDIT"]["outcome"], "retained_inventory_unavailable")
        self.assertTrue(result[0]["AMT_FOOTPRINT_AUDIT"]["eligible_for_processing"])

    def test_guard_precedes_inventory_allocation_and_does_not_invent_first_hex_mass(self):
        with patch("setup.AMTSpatialReconciliation._lineage_weighted_inventory_deductions", side_effect=AssertionError("inventory allocation reached")):
            result = reconcile_amt_hex_rows([row("A", -100, 0, 0, 1000000)])
        self.assertEqual(result[0]["FINAL_WMT"], 0)
        self.assertEqual(result[0]["LEDGER_ADJUSTMENT_WMT"], 0)
        self.assertEqual(result[0]["SPATIAL_UNRESOLVED_WMT"], 100)

    def test_snapshot_repair_is_idempotent_and_realigns_remaining_lineage(self):
        old = unsafe_snapshot()
        original = copy.deepcopy(old)
        fixed = guard_amt_snapshot(old)
        self.assertEqual(old, original)
        self.assertEqual(fixed, guard_amt_snapshot(fixed))
        self.assertEqual(fixed, pickle.loads(pickle.dumps(fixed)))
        self.assertEqual(zeroed_amt_footprints(fixed), {"SP1"})
        for record in fixed["SP1"]:
            self.assertEqual(record["FINAL_WMT"], 0)
            self.assertEqual(record["LINEAGE_FINAL_WMT"], 0)
            lineage = json.loads(record["GRADE_BLOCK_LINEAGE_JSON"])
            self.assertTrue(all(item["remaining_wmt"] == 0 for item in lineage))
        self.assertEqual(json.loads(fixed["SP1"][0]["GRADE_BLOCK_LINEAGE_JSON"])[0]["inbound_wmt"], 400)

    def test_sqlite_style_rows_and_unknown_raw_evidence(self):
        fixed = guard_amt_snapshot({"SP1": [{"hex": "H1", "raw_wmt": -1, "balance": 100,
                                             "inventory_balance_wmt": 100, "grade_block_lineage_json": "[]"}]})
        self.assertEqual(fixed["SP1"][0]["balance"], 0)
        self.assertEqual(raw_amt_hex_total(fixed["SP1"]), -1)
        unknown = {"SP1": [{"FINAL_WMT": 100}]}
        self.assertIsNone(raw_amt_hex_total(unknown["SP1"]))
        self.assertEqual(guard_amt_snapshot(unknown), unknown)
        self.assertEqual(zeroed_amt_footprints(unknown), set())


class FootprintBoundaryTests(unittest.TestCase):
    def test_zeroed_hexes_cannot_revive_grade_stream_mass_or_preview_inventory_mass(self):
        view = window()
        view.AMT_stockpile_data = unsafe_snapshot()
        view.updated_stockpile_data = {"SP1": {"amt": True, "balance": 200}}
        view.hex_sequence_table = [{"footprint": "SP1", "sequence": 1, "member_hexes": "H1,H2", "balance": 200}]
        enriched = view.enrich_AMT_grade_streams({}, view.AMT_stockpile_data)
        self.assertTrue(all(r["FINAL_WMT"] == 0 for r in enriched["SP1"]))
        self.assertTrue(all(r["reconciliation"]["source_wmt"] == 0 for r in enriched["SP1"]))
        audits, overall, warnings = view.calculate_reconciliation_review()
        self.assertEqual(overall["source_wmt"], 0)
        self.assertEqual(audits[0]["source_wmt"], 0)
        self.assertIn("zeroed", audits[0]["review_label"])
        self.assertTrue(any("no material" in w for w in warnings))

    def test_old_positive_chunks_removed_from_qt_map_solver_and_database_view(self):
        view = window()
        view.AMT_stockpile_data = unsafe_snapshot()
        view.updated_stockpile_data = {"SP1": {"amt": True, "balance": 200}}
        view.hex_sequence_table = [{"footprint": "SP1", "hex": "SP1_CHUNK_001", "balance": 200}]
        view.hex_sequence_table_argument = copy.deepcopy(view.hex_sequence_table)
        view.draw_AMT_map = SimpleNamespace(selected_points=copy.deepcopy(view.hex_sequence_table))
        self.assertEqual(view.submitted_amt_chunks(), [])
        self.assertEqual(view.database_view_stockpile_rows(), [])
        view.reconcile_saved_AMT_chunk_grade_streams(force=True)
        self.assertEqual(view.hex_sequence_table, [])
        self.assertEqual(view.hex_sequence_table_argument, [])
        self.assertEqual(view.draw_AMT_map.selected_points, [])
        self.assertEqual(view.selected_amt_footprints(), set())

    def test_known_zero_members_are_dropped_when_rebuilding_saved_chunks(self):
        view = chart()
        view.data = pd.DataFrame([{"footprint": "SP1", "hex": "H1", "balance": 0}])
        rows, count = view.rebuild_saved_chunk_records([{"footprint": "SP1", "member_hexes": "H1", "balance": 200}])
        self.assertEqual(rows, [])
        self.assertEqual(count, 1)

    def test_no_new_chunks_are_created_even_with_stale_positive_chunk_settings(self):
        self.assertEqual(calculate_amt_chunk_plan(0)["chunk_count"], 0)
        view = chart()
        view.data = pd.DataFrame([{"footprint": "SP1", "hex": "H1", "balance": 0,
                                  "lat": -22.42, "long": 119.78}])
        view.get_chunk_size = lambda _footprint: 100
        view.geometry_outliers = {}
        chunks, _message = view.build_chunks_for_footprint(
            "SP1", (119.78, -22.42), (119.79, -22.42),
            (119.78, -22.42), (119.78, -22.41),
        )
        self.assertEqual(chunks, [])

    def test_saved_project_restoration_repairs_before_database_write(self):
        view = window()
        view.AMT_stockpile_data = unsafe_snapshot()
        view.AMT_opening_request_signature = lambda _source: "saved"
        view.AMT_data_request_signature = "saved"
        view.hex_sequence_table_argument = [{"footprint": "SP1", "balance": 200}]
        view.opening_stockpile_inventories = SimpleNamespace(save_AMT_to_database=Mock())
        self.assertTrue(view.restore_loaded_AMT_data_to_database({"SP1": {"amt": True}}))
        written = view.opening_stockpile_inventories.save_AMT_to_database.call_args.args[0]
        self.assertEqual([r["RAW_WMT"] for r in written["SP1"]], [-50, 20])
        self.assertTrue(all(r["FINAL_WMT"] == 0 for r in written["SP1"]))
        self.assertEqual(view.hex_sequence_table_argument, [])

    def test_submit_allows_zeroed_footprint_without_requiring_impossible_chunks(self):
        view = window()
        view.AMT_stockpile_data = unsafe_snapshot()
        view.updated_stockpile_data = {"SP1": {"amt": True, "balance": 200}, "INV": {"amt": False, "balance": 100}}
        view.stockpile_data_AMT_column = {"SP1": True}
        stale = [{"footprint": "SP1", "hex": "SP1_CHUNK_001", "balance": 200}]
        view.draw_AMT_map = SimpleNamespace(selected_points=copy.deepcopy(stale), return_hex_sequence=lambda: stale)
        view.store_AMT_chunk_settings = lambda: True
        view.apply_cb_split_to_amt_chunks = Mock()
        view.save_active_scenario_state = Mock()
        view.set_page_enabled = Mock()
        view.database_view_tab_index = 3
        with patch("GUI.InitialiseGUI.QMessageBox.warning") as warning:
            self.assertTrue(view.store_hex_sequence_table(navigate=False))
        warning.assert_not_called()
        self.assertEqual(view.hex_sequence_table_argument, [])
        self.assertEqual(view.updated_stockpile_data["INV"]["balance"], 100)

    def test_solver_loader_cannot_fall_back_to_inventory_without_positive_amt_chunks(self):
        selected = {"SP1": {"amt": True, "balance": 200,
                            "defined_fields": {"modelled_rom_wmt": 200},
                            "source_properties": {"modelled_product_wmt": 150}},
                    "INV": {"amt": False, "balance": 100}}
        original = copy.deepcopy(selected)
        for balances in ([], [0], [-10], [0, 50]):
            with self.subTest(balances=balances):
                chunks = [{"footprint": "SP1", "sequence": i, "balance": value}
                          for i, value in enumerate(balances)]
                loader = DataLoader(selected, {}, pd.DataFrame(), chunks)
                loader.set_first_hex_tonnes_and_grades_to_AMT_stockpile()
                self.assertEqual(loader.stockpile_data["SP1"]["balance"], 50 if 50 in balances else 0)
                if 50 not in balances:
                    self.assertEqual(loader.solver_source_properties(loader.stockpile_data["SP1"]), {})
                self.assertEqual(loader.stockpile_data["INV"]["balance"], 100)
                self.assertEqual(selected, original)

    def test_zeroed_amt_has_no_reclaim_event_for_the_optimizer(self):
        selected = stockpile_record(amt=True)
        original = copy.deepcopy(selected)
        loader = DataLoader(selected, add_stockpile_calendar_rows(calendar_inputs([])), pd.DataFrame(), [])
        stockpiles, blocks, equipment, _targets = loader.load_data()
        tracker = BalanceTracker(stockpiles, blocks, "preplan", [])
        start = datetime(2026, 1, 1)
        events = EventPoolGenerator(stockpiles, blocks, equipment).get_events(
            "preplan", pd.DataFrame(), start, start + timedelta(hours=1), tracker,
        )
        self.assertEqual(events, [])
        self.assertEqual(stockpiles[0].balance, 0)
        self.assertEqual(selected, original)

    def test_raw_signed_ui_total_excludes_unattributed_movement(self):
        view = window()
        view.AMT_stockpile_data = {"SP1": [row("A", 20, 0, 0, 100, 500), row("B", -30, 1, 0, 100, 500)]}
        self.assertEqual(view.AMT_raw_signed_footprint_total("SP1"), -10)

    def test_zeroing_audit_and_raw_tonnes_survive_compaction_sqlite_and_reload(self):
        fixed = compact_amt_stockpile_data(guard_amt_snapshot(unsafe_snapshot()))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "amt.db")
            with patch("setup.OpeningStockpileInventories.get_database_path", return_value=path):
                OpeningStockpileInventories().save_AMT_to_database(fixed)
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute("SELECT raw_wmt, balance, raw_hex_stockpile_wmt, amt_footprint_audit_json, spatial_recon_reason FROM opening_AMT_stockpile_inventories ORDER BY hex").fetchall()
            self.assertEqual([r[0] for r in rows], [-50, 20])
            self.assertTrue(all(r[1] == 0 and r[2] == -30 for r in rows))
            self.assertTrue(all(json.loads(r[3])["outcome"] == AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW for r in rows))
            self.assertTrue(all("not allocated" in r[4] for r in rows))
            draw = chart()
            draw.db_path = path
            draw.data = draw.fetch_data()
            self.assertEqual(draw.data["balance"].sum(), 0)
            self.assertIn("amt_footprint_audit_json", draw.data.columns)


if __name__ == "__main__":
    unittest.main()
