"""Task 12: optional row specifications, end-to-end persistence and hard-bound compatibility."""

import copy
from contextlib import closing
from datetime import datetime
import json
import os
import pickle
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from GUI.InitialiseGUI import UserInputs
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget
from classes.CaseModeller import CaseModeller
from classes.ManualBlendPlanner import ManualBlendPlanner
from classes.ProductBuildProgress import ProductBuildProgress
from classes.ProductQualityLimits import QUALITY_FIELDS, quality_fields, with_quality_configuration
from classes.ProductTargets import migrate_product_target_state
from classes.PhaseSchemas import SCHEMA_ANALYTES
from database.SQLiteDatabase import DatabaseManager
from setup.PlanningPlanTargets import PlanningPlanTargets
from tests.test_multi_site_planning import FakeInventoryLoader
from tests import test_decision_levers as decision_fixtures


def build(**overrides):
    return {
        "build_id": 1, "build_name": "FB Build 1", "brand": "FB", "opf": "OPF1",
        "byproduct": "", "target_tonnes": 1000,
        **{f"target_{a}_{bound}": value for a in SCHEMA_ANALYTES for bound, value in (("min", 0), ("max", 100))},
        "target_fe_lql": 57, "target_fe_target": 58.123456789, "target_fe_hql": 60,
        **overrides,
    }


def model(rows, byproducts=False):
    value = CaseModeller.__new__(CaseModeller)
    value.solver_config = {}
    value.byproducts_enabled = byproducts
    value.product_build_settings = value.normalized_product_build_settings(rows)
    return value


def source_report():
    return pd.DataFrame([{
        "source": "SP1", "source_id": "SP1", "source_type": "Stockpile", "source_actual_tonnes": 100,
        "source_blend_ratio": 1, "crusher_actual_tonnes": 100, "product_build_source_tonnes": 100,
        "steady_state_number": 0, "blend_ID": 1, "blend_option": 1,
        "start_datetime": datetime(2026, 8, 1), "end_datetime": datetime(2026, 8, 1, 1),
        **{f"source_grade_{a}": 56 if a == "fe" else 1 for a in SCHEMA_ANALYTES},
    }])


def target_window(rows=None, byproducts=False):
    view = UserInputs.__new__(UserInputs)
    QMainWindow.__init__(view)
    view.workspace_tabs = QTabWidget(view)
    view.setCentralWidget(view.workspace_tabs)
    view.register_page = lambda name, tabs, widget, title, **kw: tabs.addTab(widget, title)
    view.product_targets = copy.deepcopy([build()] if rows is None else rows)
    view.byproducts_enabled = byproducts
    view.product_brand_labels_choice = ["FB", "SF"]
    view.opf_input_choice = "OPF1"
    view.calendar_inputs = {}
    view.setup_product_targets_tab()
    return view


class QualityDataTests(unittest.TestCase):
    def test_blank_and_absent_values_are_open_but_zero_is_retained(self):
        values = quality_fields({"target_fe_lql": "", "target_p_target": 0, "target_p_hql": "0"})
        self.assertEqual(set(values), set(QUALITY_FIELDS))
        self.assertIsNone(values["target_fe_lql"])
        self.assertIsNone(values["target_fe_target"])
        self.assertEqual(values["target_p_target"], 0)
        self.assertEqual(values["target_p_hql"], 0)
        self.assertIsNone(quality_fields({"target_fe_min": 58})["target_fe_target"])

    def test_nonfinite_nonnumeric_boolean_and_out_of_range_values_are_rejected(self):
        for value in ("bad", "nan", float("nan"), float("inf"), -1, 100.01, True, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite number"):
                quality_fields({"target_fe_target": value})

    def test_partial_limits_are_validated_without_requiring_a_target(self):
        for values in (dict(lql=60, hql=59), dict(lql=59, target=58), dict(target=60, hql=59)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                quality_fields({f"target_fe_{key}": value for key, value in values.items()})
        for values in (dict(lql=58), dict(hql=60), dict(lql=58, target=58, hql=58)):
            result = quality_fields({f"target_fe_{key}": value for key, value in values.items()})
            self.assertEqual(result["target_fe_lql"], values.get("lql"))

    def test_canonical_blank_wins_over_nested_agent_specification(self):
        row = {"grade_targets": {"Fe": {"target": 58, "lql": 57, "hql": 60}}, "target_fe_target": None}
        result = quality_fields(row)
        self.assertIsNone(result["target_fe_target"])
        self.assertEqual(result["target_fe_lql"], 57)

    def test_runtime_schema_is_detached_row_owned_and_reference_only(self):
        row = build(byproduct="fines", planning_grade_targets={"fe": 58.123456789})
        result = with_quality_configuration(row)
        record = result["quality_limits"]
        self.assertEqual((record["schema_version"], record["opf"], record["brand"], record["lane"]), (1, "OPF1", "FB", "fines"))
        self.assertEqual(record["enforcement"], "reference_only")
        self.assertEqual(record["limits"]["fe"]["target_source"], "2wp")
        edited = with_quality_configuration({**row, "target_fe_target": 59})
        self.assertEqual(edited["quality_limits"]["limits"]["fe"]["target_source"], "manual")
        result["planning_grade_targets"]["fe"] = 0
        self.assertEqual(row["planning_grade_targets"]["fe"], 58.123456789)
        self.assertEqual(json.loads(json.dumps(edited)), edited)

    def test_2wp_seeds_target_without_inventing_limits_or_changing_hard_bounds(self):
        rows = [("2WCB_20260708", "Cloudbreak Central", datetime(2026, 7, 14, 6),
                 datetime(2026, 7, 14, 18), "CBSF", 2000, 58.123456789, 6.2, 2.8, 0, None)]
        imported = PlanningPlanTargets(FakeInventoryLoader(rows)).fetch("CB", "OPF01", datetime(2026, 7, 14, 6), ["SF"], opf="CB OPF")
        self.assertEqual(len(imported), 1)
        row = imported[0]
        self.assertEqual(row["target_fe_target"], 58.123456789)
        self.assertEqual(row["target_fe_min"], 58.123456789)
        self.assertEqual(row["target_fe_max"], 100)
        self.assertEqual(row["target_si_max"], 6.2)
        self.assertEqual(row["target_p_target"], 0)
        self.assertIsNone(row["target_mn_target"])
        self.assertTrue(all(row[f"target_{a}_{bound}"] is None for a in SCHEMA_ANALYTES for bound in ("lql", "hql")))

    def test_grouped_targets_are_tonne_weighted_and_limits_stay_specifications(self):
        rows = [build(target_tonnes=1000, target_fe_target=58.1), build(target_tonnes=2000, target_fe_target=58.2)]
        grouped = PlanningPlanTargets.group_builds_by_brand(rows)
        self.assertEqual(len(grouped), 1)
        self.assertAlmostEqual(grouped[0]["target_fe_target"], (1000 * 58.1 + 2000 * 58.2) / 3000)
        self.assertNotEqual(grouped[0]["target_fe_target"], round(grouped[0]["target_fe_target"], 3))
        self.assertEqual((grouped[0]["target_fe_lql"], grouped[0]["target_fe_hql"]), (57, 60))
        rows[1]["target_fe_target"] = None
        self.assertIsNone(PlanningPlanTargets.group_builds_by_brand(rows)[0]["target_fe_target"])

    def test_opf_lane_and_different_manual_limits_prevent_merging_specifications(self):
        for second in (build(opf="OPF2"), build(target_fe_lql=58), build(byproduct="lump")):
            grouped = PlanningPlanTargets.group_builds_by_brand([build(), second])
            self.assertEqual(len(grouped), 2)
        grouped = PlanningPlanTargets.group_builds_by_brand([build(byproduct="lump"), build(byproduct="fines", target_fe_target=59)])
        self.assertEqual({row["byproduct"]: row["target_fe_target"] for row in grouped}, {"lump": 58.123456789, "fines": 59})

    def test_new_and_legacy_project_scenarios_round_trip_without_losing_specs(self):
        raw = {"product_build_settings": [build()], "calendar_inputs": {"product_build_settings": [build()]},
               "site_scenarios": {"OPF2": {"product_targets": [build(opf="OPF2", target_fe_target=59)]}}}
        state = migrate_product_target_state(pickle.loads(pickle.dumps(raw)))
        restored = UserInputs.__new__(UserInputs).normalized_agent_project_state(state)
        self.assertEqual(restored["product_targets"][0]["target_fe_target"], 58.123456789)
        self.assertEqual(restored["calendar_inputs"]["product_targets"][0]["target_fe_lql"], 57)
        self.assertEqual(restored["site_scenarios"]["OPF2"]["product_targets"][0]["target_fe_target"], 59)


class QualityRuntimeTests(unittest.TestCase):
    def test_model_normalization_preserves_legacy_hard_bounds_and_quality_zero(self):
        original = build(target_fe_max=0, target_p_target=0, target_p_hql=0)
        row = model([original]).product_build_settings[0]
        self.assertEqual(row["target_fe_max"], 100)  # Established legacy coercion.
        self.assertEqual(row["target_p_target"], 0)
        self.assertEqual(row["target_p_hql"], 0)
        self.assertEqual(row["target_fe_target"], original["target_fe_target"])
        self.assertEqual(row["quality_limits"]["opf"], "OPF1")
        with self.assertRaises(ValueError):
            model([build(target_fe_target=61)])

    def test_current_solver_config_carries_independent_lane_specifications(self):
        value = model([build(byproduct="lump"), build(byproduct="fines", target_fe_target=59)], True)
        value.current_time = datetime(2026, 8, 1)
        value.product_build_runtime_states = [{"tonnes": 0}, {"tonnes": 0}]
        value.previous_selected_stockpile_source_ids = set()
        value.previous_selected_grade_block_pairs = {}
        value.grade_block_pair_locks = {}
        value.active_product_build_completes_within_horizon = lambda index, lane=None: True
        config = value.solver_config_for_current_step()
        self.assertEqual(config["target_product_builds"]["lump"]["target_fe_target"], 58.123456789)
        self.assertEqual(config["target_product_builds"]["fines"]["target_fe_target"], 59)
        config["target_product_builds"]["fines"]["quality_limits"]["limits"]["fe"]["target"] = 0
        self.assertEqual(value.product_build_settings[1]["quality_limits"]["limits"]["fe"]["target"], 59)

    def test_cbc_still_enforces_min_max_and_does_not_enforce_reference_limits(self):
        helper = decision_fixtures.DecisionLeverOptimizerTests()
        config = {"throughput_incentive_per_tonne": 100, "enforce_calendar_crusher_grade_targets": False,
                  "target_product_build": with_quality_configuration(build(target_fe_lql=98, target_fe_target=99, target_fe_hql=100)),
                  "target_product_build_state": {"tonnes": 0}}
        result = helper.optimize([helper.event("FE58", grade_fe=58)], config)
        self.assertEqual(helper.selected_sources(result), {"FE58"})
        config["target_product_build"]["target_fe_min"] = 60
        result = helper.optimize([helper.event("FE58", grade_fe=58)], config)
        self.assertEqual(helper.selected_sources(result), set())

    def test_progress_reports_specs_separately_from_existing_on_spec_status(self):
        report = ProductBuildProgress.annotate(source_report(), [build()])
        row = report.iloc[0]
        self.assertEqual(row["product_build_target_fe_target"], 58.123456789)
        self.assertEqual(row["product_build_target_fe_lql"], 57)
        self.assertTrue(row["product_build_current_on_spec"])  # Fe 56 meets Min/Max, below reference LQL.
        self.assertEqual(row["product_build_opf"], "OPF1")
        self.assertEqual(ProductBuildProgress.annotate(source_report(), [build()]).to_dict(), report.to_dict())

    def test_product_report_and_sqlite_export_retain_limits_targets_and_nulls(self):
        value = model([build()])
        value.results = source_report()
        value.group_grade_block_rows = lambda frame: frame
        report = value.build_product_build_report()
        self.assertEqual(report.iloc[0]["target_fe_target"], 58.123456789)
        self.assertEqual(report.iloc[0]["opf"], "OPF1")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.db")
            with patch("database.SQLiteDatabase.get_database_path", return_value=path):
                DatabaseManager.__new__(DatabaseManager).write_product_build_report_to_database(report)
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute("SELECT target_fe_lql, target_fe_target, target_fe_hql, target_p_hql FROM product_build_report").fetchone()
            self.assertEqual(row, (57, 58.123456789, 60, None))

    def test_manual_planner_retains_validated_row_configuration(self):
        planner = ManualBlendPlanner([], [], {}, [], None, {}, [build()], 100)
        self.assertEqual(planner.product_build_settings[0]["quality_limits"]["limits"]["fe"]["target"], 58.123456789)
        with self.assertRaises(ValueError):
            ManualBlendPlanner([], [], {}, [], None, {}, [build(target_fe_lql=70)], 100)


class QualityInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, rows=None, byproducts=False):
        value = target_window(rows, byproducts)
        self.addCleanup(value.deleteLater)
        self.addCleanup(value.hide)
        return value

    def edit(self, view, row, column, text):
        view.product_build_table.item(row, view.product_build_headers.index(column)).setText(text)

    def test_grade_view_switches_columns_without_changing_values_or_mode(self):
        view = self.window()
        column = view.product_build_headers.index("Fe Target")
        hard_column = view.product_build_headers.index("Fe Min")
        self.assertTrue(view.product_build_table.isColumnHidden(column))
        view.product_build_grade_view.setCurrentText("LQL / Target / HQL")
        self.assertFalse(view.product_build_table.isColumnHidden(column))
        self.assertTrue(view.product_build_table.isColumnHidden(hard_column))
        view.product_build_grade_view.setCurrentText("All grade fields")
        self.assertFalse(view.product_build_table.isColumnHidden(hard_column))
        self.assertTrue(view.store_product_targets(False))
        self.assertEqual(view.product_targets[0]["target_fe_target"], 58.123456789)
        self.assertEqual(view.product_targets[0]["target_fe_min"], 0)

    def test_edit_and_clear_limits_persist_without_rounding_or_zero_coercion(self):
        view = self.window()
        self.edit(view, 0, "Fe LQL", "")
        self.edit(view, 0, "P Target", "0")
        self.edit(view, 0, "P HQL", "0")
        self.assertTrue(view.store_product_targets(False))
        saved = pickle.loads(pickle.dumps(view.calendar_inputs))
        view.product_targets = saved["product_targets"]
        view.populate_product_build_table()
        row = view.read_product_targets_from_table(False)[0]
        self.assertIsNone(row["target_fe_lql"])
        self.assertEqual(row["target_p_target"], 0)
        self.assertEqual(row["target_p_hql"], 0)
        self.assertEqual(row["target_fe_target"], 58.123456789)

    def test_invalid_specifications_block_submit_and_row_resize_without_losing_state(self):
        view = self.window()
        before = copy.deepcopy(view.product_targets)
        self.edit(view, 0, "Fe Target", "61")
        self.assertFalse(view.store_product_targets(False))
        self.assertEqual(view.product_targets, before)
        with patch("GUI.InitialiseGUI.QMessageBox.warning") as warning:
            view.set_product_build_table_row_count(2)
        self.assertEqual(view.product_build_table.rowCount(), 1)
        self.assertIn("Target must not exceed HQL", warning.call_args.args[-1])

    def test_deleting_row_preserves_survivor_opf_lane_limits_and_planning_provenance(self):
        rows = [build(opf="OPF1", planning_scenario="FIRST", crusher="C1"),
                build(opf="OPF2", planning_scenario="SECOND", crusher="C2", target_fe_target=59)]
        view = self.window(rows)
        view.product_build_table.setCurrentCell(0, 0)
        view.delete_selected_product_build_rows()
        survivor = view.product_targets[0]
        self.assertEqual((survivor["opf"], survivor["crusher"], survivor["planning_scenario"], survivor["target_fe_target"]),
                         ("OPF2", "C2", "SECOND", 59))
        self.assertEqual(survivor["build_id"], 1)

    def test_lump_fines_rows_keep_separate_limits_on_same_brand(self):
        view = self.window([build(byproduct="lump"), build(byproduct="fines", target_fe_target=59)], True)
        self.edit(view, 0, "Fe LQL", "")
        self.assertTrue(view.store_product_targets(False))
        rows = {r["byproduct"]: r for r in view.product_targets}
        self.assertIsNone(rows["lump"]["target_fe_lql"])
        self.assertEqual(rows["fines"]["target_fe_lql"], 57)
        self.assertEqual(rows["fines"]["target_fe_target"], 59)

    def test_agent_alias_and_nested_fields_reach_calendar_with_correct_owner(self):
        view = self.window()
        payload = {"brand": "FB", "opf": "OPF2", "target_tonnes": 1000,
                   "grades": {"fe": {"min": 0, "max": 100, "lql": 57, "target": 58.123456789, "hql": 60}}}
        self.assertTrue(view.apply_agent_target_value("product_build_settings", [payload]))
        row = view.calendar_inputs["product_targets"][0]
        self.assertEqual((row["opf"], row["target_fe_target"]), ("OPF2", 58.123456789))

    def test_invalid_agent_quality_does_not_replace_current_targets_or_continue_workflow(self):
        view = self.window()
        before = copy.deepcopy(view.product_targets)
        self.assertFalse(view.apply_agent_target_value("product_targets", [build(target_fe_hql=50)]))
        self.assertEqual(view.product_targets, before)
        view.agent_workflow_payload = {"product_targets": [build(target_fe_hql=50)]}
        view.stop_agent_workflow_apply = Mock()
        view.setup_calendar = Mock()
        view.agent_workflow_apply_product_targets()
        view.stop_agent_workflow_apply.assert_called_once()
        view.setup_calendar.assert_not_called()
        self.assertEqual(view.product_targets, before)


if __name__ == "__main__":
    unittest.main()
