import copy
from contextlib import closing
from datetime import datetime
import json
import os
import pickle
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_reconciliation_application import window, raw_hex, chart
from tests.test_custom_constraints import stockpile_record, add_stockpile_calendar_rows, calendar_inputs
from GUI.InitialiseGUI import UserInputs
from PyQt5.QtWidgets import QApplication, QMainWindow, QTableWidget
from PyQt5.QtCore import Qt
from classes.AMTFootprintExclusions import normalize_amt_exclusions, excluded_footprints
from classes.DataLoader import DataLoader
from database.SQLiteDatabase import DatabaseManager
from setup.OpeningStockpileInventories import OpeningStockpileInventories


def fixture():
    view = window()
    view.hub_input_choice = "HUB"
    view.mine_input_choice = "CC"
    view.AMT_footprint_exclusions = {}
    view.selected_data_stream = "adjusted_product"
    view.updated_stockpile_data = {"SP1": {"amt": True, "balance": 100, "build": "SP1_001"},
                                   "SP2": {"amt": True, "balance": 200, "build": "SP2_001"},
                                   "INV": {"amt": False, "balance": 300, "build": "INV_001"}}
    view.stockpile_data = copy.deepcopy(view.updated_stockpile_data)
    view.stockpile_data_AMT_column = {"SP1": True, "SP2": True}
    view.AMT_stockpile_data = {name: [{**raw_hex(name), "RAW_WMT": row["balance"], "FOOTPRINT": name}]
                               for name, row in view.updated_stockpile_data.items() if row["amt"]}
    view.hex_sequence_table = [{"footprint": name, "hex": name + "_CHUNK_001", "balance": 100}
                              for name in view.AMT_stockpile_data]
    view.hex_sequence_table_argument = copy.deepcopy(view.hex_sequence_table)
    view.AMT_chunk_settings = {"SP1": {}, "SP2": {}}
    view.total_AMT_stockpile_balances = {"SP1": 100, "SP2": 100}
    view.project_load_restore_in_progress = False
    view.AMT_data_request_signature = ""
    view.AMT_enrichment_signature = ""
    return view


class ExclusionTests(unittest.TestCase):
    def test_exclusion_prunes_all_material_and_preserves_inventory_and_audit(self):
        view = fixture()
        original = copy.deepcopy(view.updated_stockpile_data)
        view.draw_AMT_map = chart()
        view.draw_AMT_map.selected_points = copy.deepcopy(view.hex_sequence_table)
        view.draw_AMT_map.data = pd.DataFrame([{"footprint": "SP1"}, {"footprint": "SP2"}])
        self.assertTrue(view.set_amt_footprint_excluded(" sp1 ", True))
        self.assertEqual(view.updated_stockpile_data, original)
        self.assertEqual(set(view.AMT_stockpile_data), {"SP2"})
        self.assertEqual(set(view.AMT_chunk_settings), {"SP2"})
        self.assertEqual(view.total_AMT_stockpile_balances, {"SP2": 100})
        for rows in (view.hex_sequence_table, view.hex_sequence_table_argument, view.draw_AMT_map.selected_points):
            self.assertEqual([r["footprint"] for r in rows], ["SP2"])
        self.assertEqual(view.draw_AMT_map.data["footprint"].tolist(), ["SP2"])
        audit = view.AMT_footprint_exclusions["SP1"]
        self.assertEqual(audit["outcome"], "excluded")
        self.assertEqual(audit["raw_wmt"], 100)
        self.assertEqual(audit["inventory_wmt"], 100)
        self.assertFalse(audit["eligible_for_processing"])
        self.assertTrue(audit["excluded_at"])

    def test_restoring_does_not_resurrect_cached_rows_or_chunks(self):
        view = fixture()
        view.set_amt_footprint_excluded("SP1", True)
        self.assertTrue(view.set_amt_footprint_excluded("SP1", False))
        self.assertEqual(view.excluded_amt_footprints(), set())
        self.assertIn("SP1", view.selected_AMT_data_source())
        self.assertNotIn("SP1", view.AMT_stockpile_data)
        self.assertEqual([r["footprint"] for r in view.submitted_amt_chunks()], ["SP2"])
        self.assertFalse(view.has_compatible_AMT_data(view.selected_AMT_data_source()))

    def test_only_selected_amt_footprints_can_be_excluded(self):
        view = fixture()
        self.assertFalse(view.set_amt_footprint_excluded("INV", True))
        self.assertFalse(view.set_amt_footprint_excluded("UNKNOWN", True))
        view.set_amt_footprint_excluded("SP1", True)
        first = copy.deepcopy(view.AMT_footprint_exclusions)
        self.assertFalse(view.set_amt_footprint_excluded("SP1", True))
        self.assertEqual(first, view.AMT_footprint_exclusions)

    def test_exclusions_change_requests_and_allow_retained_subset_cache_reuse(self):
        view = fixture()
        before = view.AMT_opening_request_signature(view.selected_AMT_data_source())
        view.set_amt_footprint_excluded("SP1", True)
        after = view.AMT_opening_request_signature(view.updated_stockpile_data)
        self.assertNotEqual(before, after)
        # Passing all selected data cannot sneak the excluded build into a query.
        self.assertNotIn("SP1_001", after)
        request = view.AMT_opening_request_signature(view.selected_AMT_data_source())
        self.assertTrue(view.AMT_cached_snapshot_is_reusable(before, request)[0])

    def test_review_and_database_view_omit_excluded_sources_even_with_stale_chunks(self):
        view = fixture()
        stale = copy.deepcopy(view.hex_sequence_table)
        view.set_amt_footprint_excluded("SP1", True)
        view.hex_sequence_table = stale
        self.assertNotIn("SP1", [r["footprint"] for r in view.submitted_amt_chunks()])
        audits, _overall, _warnings = view.calculate_reconciliation_review()
        self.assertFalse(any("SP1" in r["review_label"] for r in audits))
        rows = view.database_view_stockpile_rows()
        self.assertFalse(any(r.get("parent_stockpile") == "SP1" for r in rows))
        self.assertTrue(any(r.get("source_id") == "INV" for r in rows))

    def test_excluded_hexes_are_not_enriched(self):
        view = fixture()
        original = copy.deepcopy(view.AMT_stockpile_data)
        view.set_amt_footprint_excluded("SP1", True)
        original["SP1"] = [None]  # Would fail if it reached hex processing.
        enriched = view.enrich_AMT_grade_streams({}, original)
        self.assertEqual(set(enriched), {"SP2"})

    def test_all_excluded_requires_conventional_selection(self):
        view = fixture()
        for name in ("SP1", "SP2"):
            view.set_amt_footprint_excluded(name, True)
        view.validate_AMT_participation()
        self.assertEqual(view.selected_amt_footprints(), set())
        del view.updated_stockpile_data["INV"]
        with self.assertRaisesRegex(ValueError, "conventional inventory"):
            view.validate_AMT_participation()
        view.store_AMT_chunk_settings = Mock()
        with patch("GUI.InitialiseGUI.QMessageBox.warning") as warning:
            self.assertFalse(view.store_hex_sequence_table(navigate=False))
        warning.assert_called_once()
        view.store_AMT_chunk_settings.assert_not_called()

    def test_all_excluded_can_submit_conventional_without_a_map(self):
        view = fixture()
        for name in ("SP1", "SP2"):
            view.set_amt_footprint_excluded(name, True)
        view.store_AMT_chunk_settings = lambda: True
        view.apply_cb_split_to_amt_chunks = Mock()
        view.save_active_scenario_state = Mock()
        view.set_page_enabled = Mock()
        view.database_view_tab_index = 1
        self.assertTrue(view.store_hex_sequence_table(navigate=False))
        self.assertEqual(view.hex_sequence_table_argument, [])
        self.assertEqual(set(view.included_stockpile_data()), {"INV"})

    def test_round_trip_and_legacy_state_are_independent(self):
        view = fixture()
        view.set_amt_footprint_excluded("SP1", True)
        state = {"AMT_footprint_exclusions": view.AMT_footprint_exclusions}
        for loaded in (json.loads(json.dumps(state)), pickle.loads(pickle.dumps(state))):
            restored = fixture()
            restored.AMT_footprint_exclusions = normalize_amt_exclusions(loaded["AMT_footprint_exclusions"])
            restored.prune_excluded_AMT_state()
            self.assertEqual(set(restored.AMT_stockpile_data), {"SP2"})
            restored.set_amt_footprint_excluded("SP1", False)
            self.assertEqual(view.excluded_amt_footprints(), {"SP1"})
        self.assertEqual(normalize_amt_exclusions(None), {})
        self.assertEqual(excluded_footprints({}), set())

    def test_solver_loader_omits_stale_excluded_inventory_and_chunks(self):
        data = stockpile_record(amt=True)
        data["KEEP"] = {**copy.deepcopy(data["SP1"]), "amt": False}
        calendar = add_stockpile_calendar_rows(calendar_inputs([]))
        calendar["excluded_amt_footprints"] = ["sp1"]
        loader = DataLoader(data, calendar, pd.DataFrame(), [{"footprint": "SP1", "balance": 1000}])
        stockpiles, _blocks, _equipment, _targets = loader.load_data()
        self.assertEqual([s.name for s in stockpiles], ["KEEP"])
        self.assertEqual(loader.hex_sequence_table, [])
        self.assertEqual(data["SP1"]["balance"], 1000)

    def test_map_cannot_generate_or_restore_excluded_chunks(self):
        draw = chart()
        draw.excluded_footprints = {"SP1"}
        draw.data = pd.DataFrame([{"footprint": "SP1", "balance": 100}])
        draw.selected_points = [{"footprint": "SP1", "balance": 100}]
        self.assertEqual(list(draw.get_unique_footprints()), [])
        self.assertEqual(draw.build_chunks_for_footprint("SP1", None, None, None, None)[0], [])
        self.assertEqual(draw.rebuild_saved_chunk_records(draw.selected_points)[0], [])
        self.assertEqual(draw.return_hex_sequence(), [])

    def test_sqlite_maps_and_report_invalidation_preserve_inputs(self):
        view = fixture()
        stale = copy.deepcopy(view.AMT_stockpile_data)
        view.set_amt_footprint_excluded("SP1", True)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "scenario.db")
            with patch("setup.OpeningStockpileInventories.get_database_path", return_value=path):
                OpeningStockpileInventories().save_AMT_to_database(view.included_AMT_snapshot(stale))
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE optimised_blend_report (source TEXT)")
                db.execute("INSERT INTO optimised_blend_report VALUES ('SP1')")
                db.execute("CREATE TABLE user_report (value TEXT)")
                db.execute("INSERT INTO user_report VALUES ('keep')")
                db.commit()
            DatabaseManager.clear_scheduling_reports(path)
            draw = chart()
            draw.db_path = path
            self.assertEqual(set(draw.fetch_data()["footprint"]), {"SP2"})
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM optimised_blend_report").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM opening_AMT_stockpile_inventories").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT value FROM user_report").fetchone()[0], "keep")


class ExclusionQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self):
        view = fixture()
        QMainWindow.__init__(view)
        headers = view.amt_stockpile_headers()
        view.AMT_stockpile_table = QTableWidget(0, len(headers))
        view.AMT_stockpile_table.setHorizontalHeaderLabels(headers)
        view.opening_stockpile_inventories = SimpleNamespace(
            save_AMT_to_database=Mock(), save_to_database=Mock(),
            clear_AMT_stockpile_database=Mock(), call_opening_AMT_stockpile_inventories=Mock(return_value={}))
        for name in ("start_dash_AMT_map_thread", "ensure_AMT_map_panel", "refresh_AMT_map_data_from_database", "save_active_scenario_state"):
            setattr(view, name, Mock())
        view.refresh_AMT_enrichment_if_needed = Mock()
        view.set_AMT_cache_status = Mock()
        return view

    def test_checkbox_excludes_and_restores_without_fetching_and_stays_visible(self):
        view = self.view()
        view.finish_AMT_stockpile_table(view.selected_AMT_data_source(), view.AMT_stockpile_data, reuse_prepared=True)
        view.AMT_stockpile_table.cellChanged.connect(view.handle_AMT_chunk_cell_change)
        column = view.AMT_column_index("Include footprint")
        with patch.object(DatabaseManager, "clear_scheduling_reports"):
            view.AMT_stockpile_table.item(0, column).setCheckState(Qt.Unchecked)
            self.assertEqual(view.excluded_amt_footprints(), {"SP1"})
            self.assertEqual(view.AMT_stockpile_table.rowCount(), 2)
            self.assertEqual(view.AMT_stockpile_table.item(0, view.AMT_column_index("AMT Total WMT")).text(), "Excluded")
            self.assertNotIn("SP1", view.AMT_chunk_settings)
            view.AMT_stockpile_table.item(0, column).setCheckState(Qt.Checked)
        self.assertEqual(view.excluded_amt_footprints(), set())
        self.assertNotIn("SP1", view.AMT_stockpile_data)
        view.opening_stockpile_inventories.call_opening_AMT_stockpile_inventories.assert_not_called()

    def test_query_only_receives_included_builds_and_freezes_start(self):
        view = self.view()
        view.set_amt_footprint_excluded("SP1", True)
        callbacks = []
        view.run_background_task = lambda *args: callbacks.append(args)
        start = view.start_time_choice
        view.setup_AMT_stockpile_table(force_refresh=True)
        self.assertEqual(view.AMT_stockpile_table.rowCount(), 2)
        self.assertEqual(len(callbacks), 1)
        view.start_time_choice = datetime(2030, 1, 1)
        callbacks[0][1]()
        view.opening_stockpile_inventories.call_opening_AMT_stockpile_inventories.assert_called_once_with(["SP2_001"], start)

    def test_all_excluded_setup_never_queries(self):
        view = self.view()
        for name in ("SP1", "SP2"):
            view.set_amt_footprint_excluded(name, True)
        view.run_background_task = Mock()
        view.setup_AMT_stockpile_table(force_refresh=True)
        view.run_background_task.assert_not_called()
        view.opening_stockpile_inventories.call_opening_AMT_stockpile_inventories.assert_not_called()
        self.assertEqual(view.AMT_stockpile_table.rowCount(), 2)

    def test_stale_fetch_callback_cannot_reintroduce_an_excluded_footprint(self):
        view = self.view()
        source = view.selected_AMT_data_source()
        signature = view.AMT_opening_request_signature(source)
        old = copy.deepcopy(view.AMT_stockpile_data)
        view.set_amt_footprint_excluded("SP1", True)
        view.finish_AMT_stockpile_table = Mock()
        view.finish_AMT_stockpile_table_from_fetch(source, old, signature)
        view.finish_AMT_stockpile_table.assert_not_called()
        self.assertNotIn("SP1", view.AMT_stockpile_data)

    def test_scenario_capture_saves_exclusions_without_sharing_mutable_state(self):
        view = self.view()
        view.set_amt_footprint_excluded("SP1", True)
        view.active_scenario_id = "SCENARIO_A"
        view.capture_stockpile_table_choices = Mock()
        view.capture_active_manual_plan_state = Mock()
        view.capture_calendar_table_inputs = lambda: {}
        view.scenario_database_path = lambda _name: "scenario.db"
        state = view.capture_scenario_state()
        persisted = pickle.loads(pickle.dumps(state))
        self.assertEqual(excluded_footprints(persisted["AMT_footprint_exclusions"]), {"SP1"})
        view.set_amt_footprint_excluded("SP1", False)
        self.assertTrue(state["AMT_footprint_exclusions"]["SP1"]["excluded"])
        other = self.view()
        other.AMT_footprint_exclusions = normalize_amt_exclusions(persisted["AMT_footprint_exclusions"])
        other.prune_excluded_AMT_state()
        self.assertEqual(set(other.selected_AMT_data_source()), {"SP2"})


if __name__ == "__main__":
    unittest.main()
