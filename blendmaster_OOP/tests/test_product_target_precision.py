"""Task 13: 2WP display rounding must never become calculation rounding."""

import copy
from contextlib import closing
import csv
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
import pickle
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from GUI.InitialiseGUI import UserInputs
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLineEdit, QStyleOptionViewItem
from classes.ManualBlendPlanner import ManualBlendPlanner
from classes.ProductBuildProgress import ProductBuildProgress
from classes.ProductTargets import migrate_product_target_state
from classes.SpreadsheetReportExporter import SpreadsheetReportExporter
from database.SQLiteDatabase import DatabaseManager
from setup.PlanningPlanTargets import PlanningPlanTargets
from tests.test_multi_site_planning import FakeInventoryLoader
from tests import test_product_quality_limits as quality_fixtures
from tests import test_decision_levers as decision_fixtures


GRADES = {"fe": 58.123456789, "si": 4.123456789, "al": 2.123456789,
          "p": 0.085123456789, "mn": 0.147123456789}


def imported_targets(vectors=None):
    start = datetime(2026, 7, 14, 6)
    rows = [("2WCB_20260708", "Cloudbreak Central", start + timedelta(hours=12 * i),
             start + timedelta(hours=12 * (i + 1)), "CBSF", 1000 * (i + 1),
             *(Decimal(str(values[a])) if values[a] is not None else None for a in GRADES))
            for i, values in enumerate(vectors or [GRADES])]
    return PlanningPlanTargets(FakeInventoryLoader(rows)).fetch(
        "CB", "OPF01", start, ["SF"], opf="CB OPF")


class ProductTargetPrecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, rows=None):
        view = quality_fixtures.target_window(imported_targets() if rows is None else rows)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.hide)
        return view

    def item(self, view, header, row=0):
        return view.product_build_table.item(row, view.product_build_headers.index(header))

    def displayed(self, view, header, row=0):
        table = view.product_build_table
        option = QStyleOptionViewItem()
        table.itemDelegate().initStyleOption(option, table.indexFromItem(self.item(view, header, row)))
        return option.text

    def editor(self, view, header):
        view.product_build_grade_view.setCurrentText("All grade fields")
        view.resize(1600, 650)
        view.show()
        self.app.processEvents()
        view.product_build_table.editItem(self.item(view, header))
        self.app.processEvents()
        return next(editor for editor in view.product_build_table.findChildren(QLineEdit) if editor.isVisible())

    def assert_grades(self, actual, expected):
        for analyte in GRADES:
            for part in ("min", "max", "target"):
                key = f"target_{analyte}_{part}"
                self.assertEqual(actual[key], expected[key], key)

    def test_query_and_weighted_grouping_reach_table_without_rounding(self):
        imported = imported_targets([GRADES, {a: value + 0.0123456789 for a, value in GRADES.items()}])
        self.assertEqual(imported[0]["planning_grade_targets"], GRADES)
        grouped = PlanningPlanTargets.group_builds_by_brand(imported)
        view = self.window(grouped)
        saved = view.read_product_targets_from_table(False)[0]
        for analyte in GRADES:
            key = f"target_{analyte}_target"
            expected = (1000 * imported[0][key] + 2000 * imported[1][key]) / 3000
            self.assertEqual(saved[key], expected)
            self.assertNotEqual(saved[key], round(expected, 3))
            self.assertEqual(self.displayed(view, f"{analyte.title()} Target"), f"{expected:.3f}")
        self.assert_grades(saved, grouped[0])

    def test_only_2wp_grades_get_three_decimal_presentation(self):
        imported = imported_targets()[0]
        imported.update(target_fe_lql=57.123456789, target_fe_hql=60.123456789)
        manual = quality_fixtures.build(target_fe_min=58.123456789, target_tonnes=1000.9)
        view = self.window([imported, manual])
        for analyte in GRADES:
            for part, suffix in (("min", "Min"), ("max", "Max"), ("target", "Target")):
                header = f"{analyte.title()} {suffix}"
                value = imported[f"target_{analyte}_{part}"]
                self.assertEqual(self.displayed(view, header), f"{value:.3f}")
                self.assertEqual(float(self.item(view, header).text()), value)
        self.assertEqual(self.displayed(view, "Fe Min", 1), "58.12")
        self.assertEqual(self.displayed(view, "Fe Target", 1), "58.123456789")
        self.assertEqual(self.displayed(view, "Target Tonnes", 1), "1,000")
        self.assertEqual(self.displayed(view, "Fe LQL"), "57.123456789")
        self.assertEqual(self.displayed(view, "Fe HQL"), "60.123456789")

    def test_legacy_imported_rows_without_central_target_provenance_keep_precision(self):
        row = imported_targets()[0]
        row.pop("planning_grade_targets")
        view = self.window([row])
        self.assertEqual(self.displayed(view, "P Max"), "0.085")
        self.assert_grades(view.read_product_targets_from_table(False)[0], row)

    def test_opening_and_committing_editor_without_changes_preserves_full_value(self):
        view = self.window()
        for header, value in (("Fe Min", GRADES["fe"]), ("P Max", GRADES["p"]), ("Fe Target", GRADES["fe"])):
            editor = self.editor(view, header)
            self.assertEqual(editor.text(), str(value))
            QTest.keyClick(editor, Qt.Key_Return)
            self.app.processEvents()
            self.assertEqual(self.item(view, header).text(), str(value))
        self.assertTrue(view.store_product_targets(False))
        self.assert_grades(view.product_targets[0], imported_targets()[0])

    def test_typing_the_displayed_value_is_an_intentional_edit_and_escape_cancels(self):
        view = self.window()
        editor = self.editor(view, "P Max")
        editor.selectAll()
        QTest.keyClicks(editor, "0.085")
        QTest.keyClick(editor, Qt.Key_Return)
        self.app.processEvents()
        self.assertEqual(view.read_product_targets_from_table(False)[0]["target_p_max"], 0.085)
        editor = self.editor(view, "Fe Min")
        editor.selectAll()
        QTest.keyClicks(editor, "59")
        QTest.keyClick(editor, Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(view.read_product_targets_from_table(False)[0]["target_fe_min"], GRADES["fe"])

    def test_copy_and_multi_cell_paste_use_full_values_and_update_saved_targets(self):
        rows = imported_targets([GRADES, GRADES])
        view = self.window(rows)
        table = view.product_build_table
        table.setCurrentItem(self.item(view, "P Max"))
        table.copy_selection_to_clipboard()
        self.assertEqual(QApplication.clipboard().text(), str(GRADES["p"]))
        table.setCurrentItem(self.item(view, "P Max", 1))
        table.paste_clipboard_to_selection()
        self.assertEqual(view.read_product_targets_from_table(False)[1]["target_p_max"], GRADES["p"])
        table.setCurrentItem(self.item(view, "Fe Min"))
        QApplication.clipboard().setText("58.222222222\t59.333333333\n58.444444444\t59.555555555")
        table.paste_clipboard_to_selection()
        self.assertTrue(view.store_product_targets(False))
        for row, pair in zip(view.product_targets, [(58.222222222, 59.333333333), (58.444444444, 59.555555555)]):
            self.assertEqual((row["target_fe_min"], row["target_fe_max"]), pair)
        view.populate_product_build_table()
        self.assertEqual(self.displayed(view, "Fe Min", 1), "58.444")
        self.assertEqual(view.read_product_targets_from_table(False)[1]["target_fe_min"], 58.444444444)

    def test_blanks_zero_and_invalid_quality_edits_are_not_hidden_by_display_format(self):
        view = self.window(imported_targets([{**GRADES, "p": 0, "mn": None}]))
        self.assertEqual(self.displayed(view, "P Target"), "0.000")
        self.assertEqual(self.displayed(view, "Mn Target"), "")
        row = view.read_product_targets_from_table(False)[0]
        self.assertEqual(row["target_p_target"], 0)
        self.assertIsNone(row["target_mn_target"])
        self.item(view, "Fe Target").setText("invalid")
        self.assertEqual(self.displayed(view, "Fe Target"), "invalid")
        self.assertFalse(view.store_product_targets(False))

    def test_validation_uses_full_values_even_when_displayed_bounds_are_identical(self):
        view = self.window()
        self.item(view, "P Min").setText("0.0852")
        self.assertEqual(self.displayed(view, "P Min"), self.displayed(view, "P Max"))
        self.assertIsNone(view.read_product_targets_from_table(False))
        self.item(view, "P Min").setText("0")
        self.item(view, "P LQL").setText("0.0851")
        self.assertIsNone(view.read_product_targets_from_table(False))

    def test_row_resizing_deletion_and_repopulation_preserve_survivor_precision(self):
        rows = imported_targets([GRADES, {**GRADES, "p": 0.09587654321}])
        view = self.window(rows)
        view.set_product_build_table_row_count(3)
        view.product_build_table.setCurrentCell(0, 0)
        view.delete_selected_product_build_rows()
        view.populate_product_build_table()
        self.assert_grades(view.read_product_targets_from_table(False)[0], rows[1])
        self.assertEqual(self.displayed(view, "P Max"), "0.096")

    def test_saved_project_scenarios_and_agent_workflow_round_trip_full_values(self):
        view = self.window()
        self.assertTrue(view.store_product_targets(False))
        state = {"product_build_settings": view.product_targets, "calendar_inputs": view.calendar_inputs,
                 "site_scenarios": {"CB OPF": {"product_targets": view.product_targets}}}
        restored = view.normalized_agent_project_state(migrate_product_target_state(pickle.loads(pickle.dumps(state))))
        for rows in (restored["product_targets"], restored["calendar_inputs"]["product_targets"],
                     restored["site_scenarios"]["CB OPF"]["product_targets"]):
            self.assert_grades(rows[0], imported_targets()[0])
        payload = json.loads(json.dumps(view.make_agent_json_safe(restored["product_targets"])))
        self.assertTrue(view.apply_agent_target_value("product_build_settings", payload))
        self.assert_grades(view.product_targets[0], imported_targets()[0])
        view.agent_workflow_payload = {"product_targets": payload}
        view.setup_calendar = Mock()
        view.set_page_enabled = Mock()
        view.show_page = Mock()
        view.calendar_tab_index = "calendar"
        with patch("GUI.InitialiseGUI.QTimer.singleShot"):
            view.agent_workflow_apply_product_targets()
        self.assert_grades(view.calendar_inputs["product_targets"][0], imported_targets()[0])
        self.assertEqual(self.displayed(view, "P Max"), "0.085")

    def test_solver_feasibility_and_diagnostics_use_unrounded_imported_fe(self):
        view = self.window()
        value = quality_fixtures.model(view.read_product_targets_from_table(False))
        value.current_time = datetime(2026, 8, 1)
        value.product_build_runtime_states = [{"tonnes": 0}]
        value.previous_selected_stockpile_source_ids = set()
        value.previous_selected_grade_block_pairs = {}
        value.grade_block_pair_locks = {}
        value.active_product_build_completes_within_horizon = lambda index, lane=None: True
        config = value.solver_config_for_current_step()
        config.update(throughput_incentive_per_tonne=100, enforce_calendar_crusher_grade_targets=False)
        self.assert_grades(config["target_product_build"], imported_targets()[0])
        helper = decision_fixtures.DecisionLeverOptimizerTests()
        event = helper.event("BETWEEN_EXACT_AND_DISPLAY", grade_fe=58.1232)
        result = helper.optimize([event], config)
        self.assertEqual(helper.selected_sources(result), set())
        self.assertEqual(result["diagnostics"]["product_build_targets"][0]["grade_targets"]["Fe"]["target_min"], GRADES["fe"])
        rounded = copy.deepcopy(config)
        rounded["target_product_build"]["target_fe_min"] = round(GRADES["fe"], 3)
        # Both aliases in a real per-step configuration must represent the same row.
        rounded["target_product_builds"]["product"]["target_fe_min"] = round(GRADES["fe"], 3)
        self.assertEqual(helper.selected_sources(helper.optimize([event], rounded)), {"BETWEEN_EXACT_AND_DISPLAY"})

    def test_solver_preserves_small_impurity_bound_beyond_three_decimals(self):
        view = self.window()
        row = quality_fixtures.model(view.read_product_targets_from_table(False)).product_build_settings[0]
        config = {"target_product_build": row, "target_product_build_state": {"tonnes": 0},
                  "throughput_incentive_per_tonne": 100, "enforce_calendar_crusher_grade_targets": False}
        helper = decision_fixtures.DecisionLeverOptimizerTests()
        event = helper.event("P_BELOW_EXACT_LIMIT")
        event._grade_p = 0.0851
        self.assertEqual(helper.selected_sources(helper.optimize([event], config)), {"P_BELOW_EXACT_LIMIT"})
        config["target_product_build"]["target_p_max"] = round(GRADES["p"], 3)
        self.assertEqual(helper.selected_sources(helper.optimize([event], config)), set())

    def test_manual_planner_progress_reports_sqlite_and_csv_keep_full_grade_values(self):
        view = self.window()
        rows = view.read_product_targets_from_table(False)
        planner = ManualBlendPlanner([], [], {}, [], None, {}, rows, 100)
        self.assert_grades(planner.product_build_settings[0], rows[0])
        progress = ProductBuildProgress.annotate(quality_fixtures.source_report(), rows).iloc[0]
        self.assertEqual(progress["product_build_target_p_max"], GRADES["p"])
        self.assertEqual(progress["product_build_target_fe_target"], GRADES["fe"])
        value = quality_fixtures.model(rows)
        value.results = quality_fixtures.source_report()
        value.group_grade_block_rows = lambda frame: frame
        report = value.build_product_build_report()
        self.assert_grades(report.iloc[0], rows[0])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.db")
            with patch("database.SQLiteDatabase.get_database_path", return_value=path):
                DatabaseManager.__new__(DatabaseManager).write_product_build_report_to_database(report)
            with closing(sqlite3.connect(path)) as connection:
                record = connection.execute("SELECT target_fe_min, target_p_max, target_fe_target FROM product_build_report").fetchone()
            self.assertEqual(record, (GRADES["fe"], GRADES["p"], GRADES["fe"]))
            path = SpreadsheetReportExporter.export_csv(os.path.join(directory, "report.csv"), report)
            with open(path, newline="", encoding="utf-8-sig") as stream:
                record = next(csv.DictReader(stream))
            for key in ("target_fe_min", "target_p_max", "target_mn_target"):
                self.assertEqual(float(record[key]), rows[0][key])


if __name__ == "__main__":
    unittest.main()
