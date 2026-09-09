"""Task 15: native control interaction, async states and target ownership."""

from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# QtWebEngine must be imported before QApplication, as in the application.
from GUI.InitialiseGUI import UserInputs
from GUI.OPFProductionReport import OPFProductionReport
from PyQt5.QtCore import Qt, QDateTime, QCoreApplication, QEvent
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget
from classes.ProductAssayReport import target_overlays
from classes.ProductTargets import migrate_product_target_state
from setup.ProductAssayHistory import ProductAssayHistory, ProductAssayUnavailable
from tests.test_product_assay_history import START, END, raw


def targets():
    rows = []
    for opf, offset in (("CB_OPF", 0), ("CC_OPF01", .3)):
        for number, hour, last, fe in ((1, 0, 12, 58.2), (2, 12, 24, 59.1)):
            row = dict(opf=opf, brand="SF", byproduct="fines", build_name=f"SF Build {number}",
                       planning_period_start=START + timedelta(hours=hour),
                       planning_period_end=START + timedelta(hours=last))
            for a, target, width in (("fe", fe + offset, .8), ("si", 4.2, .6), ("al", 2.2, .4), ("p", .075, .015), ("mn", .12, .03)):
                row.update({f"target_{a}_lql": target-width, f"target_{a}_target": target, f"target_{a}_hql": target+width})
            rows.append(row)
    return rows


def observations():
    rows = []
    for opf, brand, weight, offset in (("CBOPF", "CBSF", 100, 0), ("CCOPF01", "CCSF", 300, .4)):
        for hour in range(1, 24, 2):
            rows.append(raw(OPF=opf, BRAND=brand, OBSERVED_AT=START + timedelta(hours=hour),
                            SHIFT_DATE=START - timedelta(days=1) if hour < 6 else START,
                            SHIFT="Night" if hour < 6 or hour >= 18 else "Day", DMT=weight,
                            FE=58.0 + .06*hour + offset, SIO2=4.5 - .035*hour,
                            AL2O3=2.3 - .015*hour, P=.065 + .0005*hour, MN=.10 + .001*hour))
    return rows


class DeferredRunner:
    def __init__(self):
        self.jobs = []

    def __call__(self, work, success, failure):
        self.jobs.append((work, success, failure))

    def finish(self, index=0):
        work, success, failure = self.jobs.pop(index)
        try:
            result = work()
        except Exception as exc:
            failure(exc)
        else:
            success(result)


def prepare_fonts(app):
    for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")


def report_context(view, **overrides):
    data = dict(scenario_id="a", scenario_start=END, opfs=["CBOPF", "CCOPF01"],
                active_opf="CBOPF", brands=["SF"], targets=targets())
    view.set_context(**{**data, **overrides})


class TargetOverlayTests(unittest.TestCase):
    def test_dated_builds_clip_to_window_keep_precision_and_change_at_boundary(self):
        rows = targets()
        rows[0]["target_fe_target"] = 58.123456789
        overlays, warnings = target_overlays(rows, ["CB_OPF"], "CBSF", START + timedelta(hours=3), END)
        self.assertEqual(len(overlays), 2)
        self.assertEqual(overlays[0]["start"], (START + timedelta(hours=3)).isoformat())
        self.assertEqual(overlays[0]["values"]["target_fe_target"], 58.123456789)
        self.assertEqual(overlays[0]["end"], overlays[1]["start"])
        self.assertFalse(warnings)

    def test_undated_first_build_is_explicit_reference_and_does_not_infer_hard_bounds(self):
        rows = [dict(opf="CB_OPF", brand="SF", build_name="Current", target_fe_min=57, target_p_target=0),
                dict(opf="CB_OPF", brand="SF", build_name="Next", target_fe_target=60)]
        overlays, warnings = target_overlays(rows, ["CB_OPF"], "SF", START, END)
        self.assertEqual(len(overlays), 1)
        self.assertTrue(overlays[0]["reference_only"])
        self.assertIsNone(overlays[0]["values"]["target_fe_target"])
        self.assertEqual(overlays[0]["values"]["target_p_target"], 0)
        self.assertIn("first listed build", warnings[0])

    def test_cb_lump_and_fines_keep_separate_specifications(self):
        rows = [dict(opf="CB_OPF", brand="SF", byproduct=lane, target_fe_target=grade)
                for lane, grade in (("lump", 61), ("fines", 58))]
        for brand, expected in (("CBFL", 61), ("CBSF", 58), ("SF", 58)):
            with self.subTest(brand=brand):
                overlays, _ = target_overlays(rows, ["CB_OPF"], brand, START, END)
                self.assertEqual(overlays[0]["values"]["target_fe_target"], expected)

    def test_missing_timing_and_future_targets_are_not_applied_retroactively(self):
        row = targets()[0]
        row["planning_period_end"] = None
        overlays, warnings = target_overlays([row], ["CB_OPF"], "SF", START, END)
        self.assertFalse(overlays)
        self.assertIn("incomplete", " ".join(warnings))
        overlays, warnings = target_overlays(targets(), ["CB_OPF"], "SF", START - timedelta(days=3), START)
        self.assertFalse(overlays)
        self.assertIn("no dated", " ".join(warnings))

    def test_duplicate_specs_collapse_and_conflicting_ownership_is_reported(self):
        rows = targets()[:2]
        overlays, _ = target_overlays(rows + deepcopy(rows), ["CB_OPF"], "SF", START, END)
        self.assertEqual(len(overlays), 2)
        conflict = {**rows[0], "target_fe_target": 60}
        overlays, warnings = target_overlays(rows + [conflict], ["CB_OPF"], "SF", START, END)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0]["name"], "SF Build 2")
        self.assertIn("ambiguous build ownership", " ".join(warnings))

    def test_ambiguous_brand_does_not_choose_an_arbitrary_target(self):
        rows = [dict(opf="CC_OPF01", brand=b, target_fe_target=58) for b in ("CBSF", "CCSF")]
        overlays, warnings = target_overlays(rows, ["CC_OPF01"], "SF", START, END)
        self.assertFalse(overlays)
        self.assertIn("several", warnings[0])


class ReportUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        prepare_fonts(cls.app)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = ProductAssayHistory(cache_directory=self.directory.name, clock=lambda: END)
        self.service._query = Mock(return_value=observations())
        self.runner = DeferredRunner()
        self.view = OPFProductionReport(service=self.service, run_async=self.runner)
        self.view.resize(1440, 980)
        report_context(self.view)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.directory.cleanup()

    def load(self, force=False):
        self.view.request_refresh(force=force)
        self.runner.finish()

    def test_default_window_is_scenario_relative_awst_and_three_grains_exist(self):
        self.assertEqual(self.view.start.dateTime().toPyDateTime(), START)
        self.assertEqual(self.view.end.dateTime().toPyDateTime(), END)
        self.assertEqual(self.view.grain.count(), 3)
        self.assertEqual(self.view.grain.currentData(), "shift")
        self.assertFalse(self.view.combined.isEnabled())

    def test_loading_progress_and_refresh_button_return_after_success(self):
        QTest.mouseClick(self.view.refresh, Qt.LeftButton)
        self.assertFalse(self.view.refresh.isEnabled())
        self.assertTrue(self.view.progress.isVisible())
        self.assertIn("Loading", self.view.status.text())
        self.assertFalse(self.view.points)
        self.runner.finish()
        self.assertTrue(self.view.refresh.isEnabled())
        self.assertFalse(self.view.progress.isVisible())
        self.assertIn("Snowflake data loaded", self.view.status.text())
        self.assertIn("Latest production record", self.view.timestamps.text())
        self.assertEqual(len(self.view.figure.axes), 6)
        self.assertEqual(len(self.view.overlays), 2)

    def test_raw_shift_and_daily_are_local_renders_with_correct_dmt_weights(self):
        self.load()
        self.assertEqual(len(self.view.points), 3)
        self.view.grain.setCurrentIndex(self.view.grain.findData("observations"))
        self.assertEqual(len(self.view.points), 12)
        self.view.grain.setCurrentIndex(self.view.grain.findData("day"))
        self.assertEqual(len(self.view.points), 1)
        self.assertAlmostEqual(self.view.points[0]["grades"]["fe"], 58.72)
        self.service._query.assert_called_once()

    def test_multi_opf_includes_per_opf_and_dmt_combined_without_combined_limits(self):
        self.view.opf.setCurrentIndex(self.view.opf.findData("all"))
        self.load()
        self.view.grain.setCurrentIndex(self.view.grain.findData("day"))
        points = {p["opf"]: p for p in self.view.points}
        self.assertEqual(set(points), {"CB_OPF", "CC_OPF01", "Combined"})
        self.assertAlmostEqual(points["Combined"]["grades"]["fe"], 59.02)
        self.assertEqual({o["opf"] for o in self.view.overlays}, {"CB_OPF", "CC_OPF01"})
        self.view.combined.setChecked(False)
        self.assertNotIn("Combined", {p["opf"] for p in self.view.points})

    def test_partial_combined_coverage_is_explicit(self):
        self.service._query.return_value = [r for r in observations() if r["OPF"] == "CBOPF"]
        self.view.opf.setCurrentIndex(self.view.opf.findData("all"))
        self.load()
        self.assertIn("some time buckets lack", self.view.notes.text())

    def test_fresh_cache_and_offline_cache_keep_original_timestamps(self):
        self.load()
        stamps = self.view.timestamps.text()
        self.load()
        self.assertIn("Cached data", self.view.status.text())
        self.service._query.assert_called_once()
        self.service._query.side_effect = RuntimeError("offline")
        self.load(force=True)
        self.assertIn("Snowflake is unavailable — showing cached data", self.view.status.text())
        self.assertEqual(self.view.timestamps.text(), stamps)
        self.assertTrue(self.view.points)

    def test_new_scope_offline_never_shows_previous_opf_data(self):
        self.load()
        self.view.opf.setCurrentIndex(self.view.opf.findData("CC_OPF01"))
        self.assertFalse(self.view.points)
        self.service._query.side_effect = RuntimeError("offline")
        self.load(force=True)
        self.assertIn("no cached data matches", self.view.status.text())
        self.assertFalse(self.view.points)
        self.assertFalse(self.view.timestamps.text())
        self.assertNotIn("Traceback", self.view.status.text())

    def test_successful_no_data_does_not_retain_previous_curve(self):
        self.load()
        self.service._query.return_value = []
        self.load(force=True)
        self.assertFalse(self.view.points)
        self.assertIn("No product records", self.view.status.text())
        self.assertTrue(all(any(t.get_text() == "No assay data" for t in ax.texts) for ax in self.view.figure.axes[:5]))

    def test_records_without_assays_remain_missing_with_visible_warning(self):
        self.service._query.return_value = [raw(FE=None, SIO2=None, AL2O3=None, P=None, MN=None)]
        self.load()
        self.assertIn("No valid assay values", self.view.status.text())
        self.assertTrue(all(v is None for v in self.view.points[0]["grades"].values()))

    def test_response_after_window_change_or_scenario_reset_is_ignored(self):
        self.view.request_refresh()
        self.view.start.setDateTime(QDateTime(START - timedelta(days=1)))
        self.runner.finish()
        self.assertIsNone(self.view.snapshot)
        self.view.request_refresh()
        self.view.reset_context()
        self.runner.finish()
        self.assertIsNone(self.view.snapshot)
        self.assertIsNone(self.view.scenario_start)

    def test_refresh_keeps_old_scope_data_visible_and_loading_state_on_grain_change(self):
        self.load()
        self.view.request_refresh(force=True)
        self.assertTrue(self.view.points)
        self.view.grain.setCurrentIndex(self.view.grain.findData("day"))
        self.assertIn("Loading", self.view.status.text())
        self.runner.finish()

    def test_interval_runs_only_while_visible_and_no_duplicate_request_is_queued(self):
        self.view.interval.setValue(2)
        self.assertTrue(self.view.timer.isActive())
        self.assertEqual(self.view.timer.interval(), 120000)
        self.view._auto_refresh()
        self.view._auto_refresh()
        self.assertEqual(len(self.runner.jobs), 1)
        self.runner.finish()
        self.view.hide()
        self.assertFalse(self.view.timer.isActive())
        self.view.show()
        self.assertTrue(self.view.timer.isActive())
        self.view.interval.setValue(0)
        self.assertFalse(self.view.timer.isActive())

    def test_settings_round_trip_and_scenario_start_change_resets_default_window(self):
        self.view.start.setDateTime(QDateTime(START - timedelta(days=2)))
        self.view.opf.setCurrentIndex(self.view.opf.findData("all"))
        self.view.grain.setCurrentIndex(self.view.grain.findData("day"))
        self.view.interval.setValue(5)
        state = json.loads(json.dumps(self.view.settings()))
        self.view.reset_context()
        report_context(self.view, state=state)
        self.assertEqual(self.view.settings(), state)
        self.view.reset_context()
        report_context(self.view, scenario_start=END + timedelta(days=7), state=state)
        self.assertEqual(self.view.end.dateTime().toPyDateTime(), END + timedelta(days=7))
        self.assertEqual(self.view.start.dateTime().toPyDateTime(), START + timedelta(days=7))

    def test_last_day_button_resets_and_loads_and_invalid_window_does_not_query(self):
        self.view.start.setDateTime(QDateTime(END + timedelta(days=1)))
        self.view.request_refresh()
        self.assertFalse(self.runner.jobs)
        self.assertIn("positive report window", self.view.status.text())
        QTest.mouseClick(self.view.last_day, Qt.LeftButton)
        self.assertEqual(self.view.start.dateTime().toPyDateTime(), START)
        self.runner.finish()

    def test_target_details_show_all_builds_without_rounding_and_expand_on_click(self):
        self.view.opf.setCurrentIndex(self.view.opf.findData("all"))
        self.view.targets[0]["target_fe_target"] = 58.123456789
        self.load()
        QTest.mouseClick(self.view.details_button, Qt.LeftButton)
        self.assertTrue(self.view.target_details.isVisible())
        self.assertIn("58.123456789", self.view.target_details.toPlainText())
        self.assertIn("CC_OPF01 · SF Build 2", self.view.target_details.toPlainText())
        self.assertTrue(self.view._boundaries)

    def test_invalid_or_oversized_result_is_not_mislabelled_as_snowflake_outage(self):
        self.service._query.side_effect = ValueError("More than 200,000 product records match. Choose a shorter window or fewer OPFs.")
        self.load()
        self.assertIn("shorter window", self.view.status.text())
        self.assertNotIn("unavailable", self.view.status.text())


class ReportHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self):
        host = UserInputs.__new__(UserInputs)
        QMainWindow.__init__(host)
        host.workspace_tabs = QTabWidget(host)
        host.setCentralWidget(host.workspace_tabs)
        host.page_locations, host.page_widgets = {}, {}
        host.register_page("dashboard", host.workspace_tabs, QWidget(), "Dashboard")
        host.product_build_tab = QWidget()
        host.register_page("product_targets", host.workspace_tabs, host.product_build_tab, "Product Targets")
        host.register_page("later", host.workspace_tabs, QWidget(), "Later")
        host.background_tasks = []
        host.show_error_popup = Mock()
        host.setup_opf_production_report_tab()
        self.addCleanup(host.deleteLater)
        self.addCleanup(host.hide)
        return host

    def test_tab_is_immediately_after_targets_and_other_page_locations_shift(self):
        host = self.host()
        self.assertEqual(host.workspace_tabs.tabText(2), "OPF Production Report")
        self.assertEqual(host.page_locations["later"][1], 3)
        self.assertEqual(host.opf_production_report_tab_index, "opf_production_report")

    def test_actual_background_worker_returns_on_ui_thread_and_preserves_errors(self):
        host = self.host()
        view = host.opf_production_report
        report_context(view)
        ui_thread = threading.get_ident()
        worker_threads = []
        def fetch(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            raise ProductAssayUnavailable("Snowflake is unavailable and no cached data matches this window and OPF selection.")
        view.service.fetch = fetch
        received_on = []
        original = view.failed
        view.failed = lambda *args: (received_on.append(threading.get_ident()), original(*args))
        view.request_refresh()
        for _ in range(200):
            self.app.processEvents()
            if not host.background_tasks:
                break
            QTest.qWait(5)
        self.assertFalse(host.background_tasks)
        self.assertNotEqual(worker_threads, [ui_thread])
        self.assertEqual(received_on, [ui_thread])
        self.assertIn("no cached data matches", view.status.text())
        host.show_error_popup.assert_not_called()

    def test_host_context_collects_inactive_opfs_and_current_table_edits(self):
        host = self.host()
        host.opf_input_choice = "CBOPF"
        host.start_time_choice = END
        host.active_scenario_id = "a"
        host.product_brand_labels_choice = ["SF"]
        host.product_targets = []
        host.site_scenarios = {"b": {"opf_input_choice": "CCOPF01", "product_targets": targets()[2:]}}
        host.product_build_table = QWidget()
        host.read_product_targets_from_table = Mock(return_value=targets()[:2])
        host.sync_opf_production_report_context()
        self.assertEqual(host.opf_production_report.opfs, ["CB_OPF", "CC_OPF01"])
        self.assertEqual(len(host.opf_production_report.targets), 4)
        host.opf_production_report.interval.setValue(3)
        self.assertEqual(host.product_assay_report_settings["refresh_minutes"], 3)

    def test_scenario_capture_and_project_migration_preserve_independent_report_filters(self):
        host = self.host()
        report_context(host.opf_production_report)
        host.opf_production_report.interval.setValue(7)
        context = SimpleNamespace(
            active_scenario_id="a", product_assay_report_settings=host.product_assay_report_settings,
            capture_stockpile_table_choices=lambda: None, capture_active_manual_plan_state=lambda: None,
            capture_calendar_table_inputs=lambda: {}, scenario_database_path=lambda _id: "a.db",
            capture_table_snapshot=UserInputs.capture_table_snapshot)
        state = UserInputs.capture_scenario_state(context)
        project = migrate_product_target_state({"site_scenarios": {"a": state},
                                               "product_assay_report_settings": state["product_assay_report_settings"]})
        restored = host.normalized_agent_project_state(project)
        self.assertEqual(restored["product_assay_report_settings"]["refresh_minutes"], 7)
        context.product_assay_report_settings["refresh_minutes"] = 9
        self.assertEqual(restored["site_scenarios"]["a"]["product_assay_report_settings"]["refresh_minutes"], 7)


if __name__ == "__main__":
    unittest.main()
