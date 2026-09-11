import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Import QtWebEngine before QApplication, as in the application.
from GUI.InitialiseGUI import UserInputs
from GUI.DestinationProgressSetup import DestinationProgressSetup
from PyQt5.QtCore import Qt, QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from tests.test_opf_production_report import prepare_fonts, DeferredRunner
from tests.test_destination_build_order import fixture, movement
from tests.test_destination_activity import actual, START, AREAS
from setup.RecentDestinationActivity import RecentDestinationActivity

from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
import pickle
import tempfile
import unittest


class DestinationProgressUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        prepare_fonts(cls.app)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "Mining.csv"
        fixture().to_csv(self.path, index=False)
        self.service = RecentDestinationActivity(cache_directory=Path(self.directory.name) / "cache", clock=lambda: START)
        self.service._query = Mock(return_value=[actual()])
        self.runner = DeferredRunner()
        self.view = DestinationProgressSetup(service=self.service, run_async=self.runner)
        self.context()
        self.view.resize(1450, 850)
        self.view.show()
        self.app.processEvents()

    def context(self, **changes):
        self.view.set_context(**{**dict(scenario_id="a", site="CC", scenario_start=START, path=str(self.path),
                                       inventories={k: {"nearest_crusher": v} for k, v in AREAS.items()}), **changes})

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.directory.cleanup()

    def load(self, force=False):
        self.view.request_refresh(force)
        self.runner.finish()

    def test_loading_fresh_cache_offline_and_evidence_views(self):
        self.view.request_refresh()
        self.assertTrue(self.view.progress.isVisible())
        self.assertFalse(self.view.refresh.isEnabled())
        self.assertIn("Loading", self.view.status.text())
        self.runner.finish()
        self.assertIn("Fresh activity", self.view.status.text())
        self.assertEqual(self.view.table.rowCount(), 2)
        self.assertEqual(self.view.order_table.model().rowCount(), 4)
        self.assertEqual(self.view.audit_table.model().rowCount(), 5)
        self.assertEqual(self.view.activity_table.model().rowCount(), 1)
        self.assertIn("SP2", self.view.details.toPlainText())
        self.load()
        self.assertIn("Cached activity", self.view.status.text())
        self.service._query.side_effect = ConnectionError("offline")
        self.load(True)
        self.assertIn("Offline", self.view.status.text())
        self.assertTrue(self.view.refresh.isEnabled())
        self.assertFalse(self.view.progress.isVisible())

    def test_audit_defaults_to_rom_inbound_with_separate_exceptions_and_reclaim(self):
        frame = fixture()
        frame.loc[len(frame)] = movement(**{"Mining.wetTonnes": -1})
        frame.loc[len(frame)] = movement("CR1", **{"Destination.Type": "Crusher"})
        frame.to_csv(self.path, index=False)
        self.load()
        self.assertEqual(self.view.audit_filter.currentText(), "ROM inbound")
        self.assertEqual(self.view.audit_table.model().rowCount(), 5)
        self.assertEqual(self.view.audit_count.text(), "5 of 8 CSV records")
        for mode, count in (("excluded_inbound", 1), ("reclaim", 1), ("all", 8)):
            self.view.audit_filter.setCurrentIndex(self.view.audit_filter.findData(mode))
            model = self.view.audit_table.model()
            self.assertEqual(model.rowCount(), count)
            if mode == "reclaim":
                self.assertEqual(model.records[0]["destination"], "CR1")
                self.assertEqual(model.records[0]["reclaimed_stockpile"], "SP1")
                self.assertIn(("reclaimed_stockpile", "Reclaimed stockpile"), model.columns)
        self.service._query.assert_called_once()

    def test_activity_window_displays_scenario_relative_awst_dates(self):
        self.assertIn("08 Sep 2026 18:00:00 inclusive", self.view.activity_window.text())
        self.assertIn("09 Sep 2026 06:00:00 exclusive", self.view.activity_window.text())
        self.assertIn("Christmas Creek", self.view.activity_window.text())
        self.view.lookback.setValue(24)
        self.assertIn("08 Sep 2026 06:00:00 inclusive", self.view.activity_window.text())
        self.runner.finish()

    def test_no_data_assumes_first_but_no_cache_outage_stays_unconfirmed(self):
        self.service._query.return_value = []
        self.load()
        self.assertIn("No qualifying", self.view.status.text())
        self.assertTrue(self.view.table.cellWidget(0, 6).isEnabled())
        self.assertEqual(self.view.table.item(0, 2).text(), "Not detected")
        self.assertIn("Assumed first 2WP build", self.view.table.item(0, 7).text())
        self.assertEqual(self.view.rows[0]["current"]["order_position"], 1)
        self.assertEqual(self.view.settings()["selected_instances"], {})
        self.load()
        self.assertIn("Cached activity", self.view.status.text())
        self.assertIn("Assumed first 2WP build", self.view.table.item(0, 7).text())
        self.assertEqual(self.view.order_table.model().rowCount(), 4)
        self.service._query.side_effect = ConnectionError("offline")
        self.view.lookback.setValue(13)
        self.runner.finish()
        self.assertIn("Activity unavailable", self.view.status.text())
        self.assertFalse(self.view.table.cellWidget(0, 6).isEnabled())
        self.assertEqual(self.view.order_table.model().rowCount(), 4)

    def test_ambiguous_instance_review_and_capacity_keyboard_edit(self):
        self.service._query.return_value = [actual("SP1")]
        self.load()
        self.assertIn("Ambiguous", self.view.status.text())
        combo = self.view.table.cellWidget(0, 3)
        self.assertEqual(combo.currentIndex(), 0)
        QTest.keyClick(combo, Qt.Key_End)
        current = self.view.rows[0]["current"]
        self.assertEqual(current["build_instance"], 2)
        self.assertEqual(self.view.rows[0]["selection_basis"], "User selected")
        field = self.view.table.cellWidget(0, 6)
        QTest.keyClicks(field, "123.456789")
        self.assertEqual(self.view.settings()["remaining_wmt"][current["instance_id"]], 123.456789)
        field.setText("-4")
        field.editingFinished.emit()
        self.assertEqual(field.text(), "123.456789")
        self.assertIn("previous value was retained", self.view.validation.text())

    def test_settings_save_load_precision_blank_zero_and_scenario_invalidation(self):
        self.load()
        field = self.view.table.cellWidget(0, 6)
        QTest.keyClicks(field, "123.456789")
        state = pickle.loads(pickle.dumps(self.view.settings()))
        self.view.reset_context()
        self.context(state=state)
        self.load()
        self.assertEqual(self.view.table.cellWidget(0, 6).text(), "123.456789")
        self.assertEqual(self.view.settings(), state)
        field = self.view.table.cellWidget(0, 6)
        QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(field, "0")
        self.assertEqual(list(self.view.settings()["remaining_wmt"].values()), [0.])
        QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(field, Qt.Key_Backspace)
        self.assertEqual(self.view.settings()["remaining_wmt"], {})
        QTest.keyClicks(field, "20")
        state = self.view.settings()
        self.context(scenario_start=START + timedelta(hours=1), state=state)
        self.load()
        self.assertEqual(self.view.settings()["remaining_wmt"], {})
        self.assertIn("re-enter", self.view.validation.text())

    def test_blank_shows_estimate_and_round_trips_without_becoming_override(self):
        from datetime import datetime
        from classes.DestinationProgress import ESTIMATED_CAPACITY_BASIS
        start = datetime(2026, 9, 8, 7, 30)
        self.service._query.return_value = [actual("SP2", when="2026-09-08 07:00")]
        self.context(scenario_start=start)
        self.load()
        field = self.view.table.cellWidget(0, 6)
        self.assertEqual(field.text(), "")
        self.assertEqual(field.placeholderText(), "Estimated: 50.0")
        self.assertIn(ESTIMATED_CAPACITY_BASIS, self.view.details.toPlainText())
        QTest.keyClicks(field, "0")
        self.assertIn("User entered", self.view.details.toPlainText())
        QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(field, Qt.Key_Backspace)
        self.assertIn(ESTIMATED_CAPACITY_BASIS, self.view.details.toPlainText())
        state = pickle.loads(pickle.dumps(self.view.settings()))
        self.assertEqual(state["remaining_wmt"], {})
        self.view.reset_context()
        self.context(scenario_start=start, state=state)
        self.load()
        self.assertEqual(self.view.table.cellWidget(0, 6).placeholderText(), "Estimated: 50.0")
        self.assertEqual(self.view.settings(), state)

    def test_old_async_response_cannot_overwrite_new_scenario(self):
        self.view.request_refresh()
        self.context(scenario_id="b", site="CB")
        self.view.request_refresh()
        self.runner.finish(0)
        self.assertIsNone(self.view.snapshot)
        self.assertTrue(self.view._pending)
        self.runner.finish(0)
        self.assertEqual(self.view.snapshot["activity"]["request"]["site"], "CB")
        self.assertEqual(self.view.snapshot["activity"]["records"], [])

    def test_allocation_context_freezes_edits_and_rejects_stale_inputs(self):
        inputs = dict(scenario_id="a", site="CC", scenario_start=START, path=str(self.path),
                      inventories={k: {"nearest_crusher": v} for k, v in AREAS.items()})
        self.assertIsNone(self.view.allocation_context(**inputs))
        self.load()
        field = self.view.table.cellWidget(0, 6)
        QTest.keyClicks(field, "100")
        frozen = self.view.allocation_context(**inputs)
        self.assertNotIn("audit", frozen["order"])
        self.assertEqual(list(frozen["settings"]["remaining_wmt"].values()), [100.0])
        QTest.keyClicks(field, "0")
        self.assertEqual(list(frozen["settings"]["remaining_wmt"].values()), [100.0])
        self.assertEqual(list(self.view.allocation_context(**inputs)["settings"]["remaining_wmt"].values()), [1000.0])
        for changed in ({"scenario_id": "b"}, {"site": "CB"}, {"scenario_start": START+timedelta(hours=1)},
                        {"inventories": {"SP1": {"nearest_crusher": "OTHER"}}}):
            self.assertIsNone(self.view.allocation_context(**{**inputs, **changed}))
        with self.path.open("a") as stream:
            stream.write("\n")
        self.assertIsNone(self.view.allocation_context(**inputs))

    def test_missing_mapping_and_file_are_visible(self):
        self.context(inventories={})
        self.load()
        self.assertIn("No planned ROM destinations", self.view.status.text())
        self.assertIn("Nearest Crusher", self.view.details.toPlainText())
        self.service._query.assert_not_called()
        self.context(path=str(self.path) + ".missing")
        self.load()
        self.assertIn("Unable to load", self.view.status.text())
        self.assertTrue(self.view.refresh.isEnabled())

    def test_large_audit_remains_complete_and_last_record_is_accessible(self):
        rows = [{"record": i, "reason": "ROM inbound"} for i in range(50000)]
        self.view.fill(self.view.audit_table, rows, [("record", "CSV record"), ("reason", "Reason")])
        model = self.view.audit_table.model()
        self.assertEqual(model.rowCount(), 50000)
        self.assertEqual(model.data(model.index(49999, 0)), "49999")
        self.view.tabs.setCurrentIndex(3)
        self.view.audit_table.scrollToBottom()
        self.app.processEvents()
        self.assertEqual(model.records[-1], rows[-1])

    def test_24hr_only_destination_allowance_save_load_and_stale_file(self):
        from tests.test_destination_supplemental_lanes import schedule_row
        from classes.DestinationSupplementalLanes import SUPPLEMENTAL_ORIGIN
        import pandas as pd
        supplemental = Path(self.directory.name) / "24hr.csv"
        pd.DataFrame([schedule_row()]).to_csv(supplemental, index=False)
        extra = dict(supplemental_path=str(supplemental), selected_agents=["EX1"])
        self.context(**extra)
        self.load()
        index = next(i for i, r in enumerate(self.view.rows) if r["material_type"] == "SG")
        self.view.table.selectRow(index)
        self.assertEqual(self.view.table.item(index, 7).text(), SUPPLEMENTAL_ORIGIN)
        combo = self.view.table.cellWidget(index, 3)
        self.assertEqual(combo.currentText(), "Select destination")
        self.assertFalse(self.view.table.cellWidget(index, 6).isEnabled())
        self.assertEqual(combo.count(), 3)
        combo.setCurrentIndex(1)
        field = self.view.table.cellWidget(index, 6)
        self.assertTrue(field.isEnabled())
        self.assertEqual(field.placeholderText(), "Not set")
        QTest.keyClicks(field, "250.5")
        saved = pickle.loads(pickle.dumps(self.view.settings()))
        self.view.reset_context()
        self.context(state=saved, **extra)
        self.load()
        self.assertIn("SP1", self.view.table.cellWidget(index, 3).currentText())
        self.assertEqual(self.view.table.cellWidget(index, 6).text(), "250.5")
        self.assertEqual(self.view.settings(), saved)
        inputs = dict(scenario_id="a", site="CC", scenario_start=START, path=str(self.path),
                      inventories={k: {"nearest_crusher": v} for k, v in AREAS.items()}, **extra)
        frozen = self.view.allocation_context(**inputs)
        self.assertEqual(len(frozen["order"]["supplemental_lanes"]), 1)
        self.assertEqual(len(frozen["order"]["orders"]), 4)
        self.assertIsNone(self.view.allocation_context(**{**inputs, "selected_agents": ["EX2"]}))
        with supplemental.open("a") as stream:
            stream.write("\n")
        self.assertIsNone(self.view.allocation_context(**inputs))

    def test_24hr_destination_inputs_are_forwarded_from_active_application(self):
        from types import SimpleNamespace
        panel = Mock()
        panel.allocation_context.return_value = None
        owner = SimpleNamespace(destination_progress=panel, expit_mode_choice=2,
                                file_path_24hr_choice="24hr.csv", selected_24hr_expit_agents=["EX1"])
        UserInputs.sync_destination_progress_context(owner)
        self.assertEqual(panel.set_context.call_args.kwargs["supplemental_path"], "24hr.csv")
        UserInputs.destination_allocation_context(owner)
        self.assertEqual(panel.allocation_context.call_args.kwargs["selected_agents"], ["EX1"])
        owner.expit_mode_choice = 1
        UserInputs.sync_destination_progress_context(owner)
        self.assertEqual(panel.set_context.call_args.kwargs["supplemental_path"], "")


if __name__ == "__main__":
    unittest.main()
