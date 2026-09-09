"""Target-mode migration, native controls, ownership and execution capability."""

from copy import deepcopy
import json
import os
import pickle
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.test_product_quality_limits import build, model, target_window, UserInputs
from tests.test_opf_production_report import prepare_fonts
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from classes.PhaseSchemas import product_quality_limits
from classes.ProductTargetModes import (
    target_mode_fields, migrate_target_row, require_supported_target_modes,
    TARGET_MODE_FIELDS, PRODUCT_TARGET_SCHEMA_VERSION,
)
from classes.ProductQualityLimits import with_quality_configuration
from classes.ProductTargets import migrate_product_target_state
from classes.ProductBuildProgress import ProductBuildProgress
from classes.CaseModeller import CaseModeller
from classes.ManualBlendPlanner import ManualBlendPlanner
from classes.Optimizer import Optimizer
from setup.PlanningPlanTargets import PlanningPlanTargets
from execute.Run import Run


class TargetModeDataTests(unittest.TestCase):
    def test_legacy_and_reference_rows_default_hard_without_modifying_any_grade(self):
        for original in (build(), with_quality_configuration(build())):
            original = deepcopy(original)
            for key in TARGET_MODE_FIELDS:
                original.pop(key, None)
            before = deepcopy(original)
            current = migrate_target_row(original)
            self.assertEqual(current["product_target_schema_version"], 2)
            self.assertEqual(current["target_mode"], "hard")
            self.assertEqual(current["target_evaluation_basis"], "steady_state")
            self.assertEqual({key: current[key] for key in before}, before)
            self.assertEqual(original, before)
            self.assertEqual(migrate_target_row(current), current)

    def test_explicit_soft_and_analyte_limit_modes_reach_runtime_without_invented_penalties(self):
        source = build(target_mode="soft", target_evaluation_basis="cumulative_build", target_fe_limit_mode="soft")
        row = with_quality_configuration(source)
        record = row["quality_limits"]
        self.assertEqual(record["target_mode"], "soft")
        self.assertEqual(record["evaluation_basis"], "cumulative_build")
        self.assertEqual(record["enforcement"], "soft_target")
        self.assertEqual(record["limits"]["fe"]["limit_mode"], "soft")
        self.assertEqual(record["limits"]["p"]["limit_mode"], "hard")
        self.assertIsNone(record["limits"]["fe"]["target_penalty_weight"])
        self.assertEqual(row["target_fe_min"], source["target_fe_min"])

    def test_flat_modes_override_stale_derived_configuration(self):
        row = with_quality_configuration(build(target_mode="soft", target_fe_limit_mode="soft"))
        row.update(target_mode="hard", target_fe_limit_mode="hard", target_evaluation_basis="cumulative_build")
        result = with_quality_configuration(row)
        self.assertEqual(result["quality_limits"]["target_mode"], "hard")
        self.assertEqual(result["quality_limits"]["limits"]["fe"]["limit_mode"], "hard")
        self.assertEqual(result["quality_limits"]["evaluation_basis"], "cumulative_build")

    def test_nested_schema_modes_are_read_when_flat_fields_are_absent(self):
        row = {"quality_limits": product_quality_limits("CB_OPF", "SF", target_mode="soft",
                evaluation_basis="cumulative_build", limits={"fe": {"limit_mode": "soft"}})}
        modes = target_mode_fields(row)
        self.assertEqual((modes["target_mode"], modes["target_fe_limit_mode"]), ("soft", "soft"))
        self.assertEqual(modes["target_evaluation_basis"], "cumulative_build")

    def test_unknown_modes_and_versions_are_rejected_instead_of_becoming_hard(self):
        changes = [dict(product_target_schema_version=v) for v in (0, 3, True, None, 1.5, "future")]
        changes += [dict(target_mode=v) for v in ("adaptive", "", None, True)]
        changes += [dict(target_p_limit_mode="maybe"), dict(target_evaluation_basis="completed_build"),
                    dict(quality_limits={"schema_version": 999}), dict(quality_limits={"limits": "bad"})]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                migrate_target_row({**build(), **change})

    def test_project_scenario_calendar_migration_retains_independent_soft_intent(self):
        legacy = build()
        soft = build(opf="OPF2", byproduct="fines", target_mode="soft", target_mn_limit_mode="soft")
        state = {"product_build_settings": [legacy], "calendar_inputs": {"product_build_settings": [legacy]},
                 "site_scenarios": {"other": {"product_targets": [soft], "calendar_inputs": {"product_targets": [soft]}}}}
        migrated = migrate_product_target_state(pickle.loads(pickle.dumps(state)))
        restored = json.loads(json.dumps(migrated))
        self.assertEqual(restored["product_targets"][0]["target_mode"], "hard")
        other = restored["site_scenarios"]["other"]
        self.assertEqual(other["product_targets"][0]["target_mode"], "soft")
        self.assertEqual(other["calendar_inputs"]["product_targets"][0]["target_mn_limit_mode"], "soft")
        self.assertEqual(migrate_product_target_state(migrated), migrated)
        self.assertNotIn("target_mode", legacy)

    def test_case_normalization_retains_modes_opf_and_full_precision(self):
        source = build(opf="CC_OPF01", target_mode="soft", target_p_limit_mode="soft", target_evaluation_basis="cumulative_build")
        row = model([source]).product_build_settings[0]
        self.assertEqual(row["target_fe_target"], 58.123456789)
        self.assertEqual(row["opf"], "CC_OPF01")
        self.assertEqual(row["quality_limits"]["target_mode"], "soft")
        self.assertEqual(row["quality_limits"]["limits"]["p"]["limit_mode"], "soft")

    def test_grouping_keeps_different_modes_and_evaluation_settings_separate(self):
        for change in (dict(target_mode="soft"), dict(target_evaluation_basis="cumulative_build"), dict(target_fe_limit_mode="soft")):
            with self.subTest(change=change):
                result = PlanningPlanTargets.group_builds_by_brand([build(), build(**change)])
                self.assertEqual(len(result), 2)
                self.assertEqual(result[1][next(iter(change))], next(iter(change.values())))
                self.assertTrue(all(r["product_target_schema_version"] == PRODUCT_TARGET_SCHEMA_VERSION for r in result))

    def test_compatible_soft_grouping_remains_tonne_weighted_and_does_not_merge_lanes(self):
        rows = [build(target_mode="soft", target_tonnes=100, target_fe_target=58.1),
                build(target_mode="soft", target_tonnes=300, target_fe_target=58.3)]
        result = PlanningPlanTargets.group_builds_by_brand(rows)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["target_fe_target"], 58.25)
        self.assertEqual(result[0]["target_mode"], "soft")
        rows[1]["byproduct"] = "fines"
        self.assertEqual(len(PlanningPlanTargets.group_builds_by_brand(rows)), 2)

    def test_unknown_mode_is_rejected_at_execution_boundaries(self):
        soft = build(target_mode="unknown")
        calls = [lambda: Run.__new__(Run).execute(None, None, None, None, {}, {"product_targets": [soft]}, []),
                 lambda: CaseModeller([], [], [], {}, None, None, 1, [], product_build_settings=[soft]),
                 lambda: Optimizer.run_blending_optimization([], None, None, None, None, None, None, solver_config={"target_product_build": soft}),
                 lambda: Optimizer.run_blending_optimization([], None, None, None, None, None, None, solver_config={"target_product_builds": {"fines": soft}}),
                 lambda: ManualBlendPlanner([], [], {}, [], None, {}, [soft], 100)]
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(ValueError, "Target mode"):
                call()

    def test_soft_is_not_reported_on_spec_using_legacy_bounds(self):
        metals = {a: 5600 if a == "fe" else 100 for a in ("fe", "si", "al", "p", "mn")}
        self.assertFalse(ProductBuildProgress._is_on_spec(100, metals, build(target_mode="soft", target_fe_limit_mode="soft")))
        self.assertTrue(ProductBuildProgress._is_on_spec(100, metals, build()))
        require_supported_target_modes([build()])
        self.assertTrue(ProductBuildProgress._is_on_spec(100, {a: 5800 if a == "fe" else 100 for a in ("fe", "si", "al", "p", "mn")}, build()))


class TargetModeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        prepare_fonts(cls.app)

    def window(self, rows=None, byproducts=False):
        view = target_window(rows, byproducts)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.hide)
        view.resize(1650, 900)
        view.show()
        self.app.processEvents()
        return view

    def mode(self, view, row=0):
        return view.product_build_table.cellWidget(row, view.product_build_headers.index("Target mode"))

    def item(self, view, name, row=0):
        return view.product_build_table.item(row, view.product_build_headers.index(name))

    def test_legacy_opens_hard_and_disables_soft_settings(self):
        view = self.window()
        self.assertEqual(self.mode(view).currentData(), "hard")
        self.assertFalse(view.product_target_mode_controls.evaluation.isEnabled())
        self.assertFalse(view.product_target_mode_notice.isVisible())
        self.assertEqual(view.product_build_grade_view.currentText(), "Min / Max")
        self.assertEqual(view.read_product_targets_from_table(False)[0]["product_target_schema_version"], 2)

    def test_keyboard_soft_selection_exposes_quality_fields_and_pending_notice(self):
        view = self.window()
        QTest.keyClick(self.mode(view), Qt.Key_End)
        self.assertEqual(self.mode(view).currentData(), "soft")
        self.assertTrue(view.product_target_mode_controls.evaluation.isEnabled())
        self.assertTrue(view.product_target_mode_notice.isVisible())
        self.assertIn("Decision Levers", view.product_target_mode_notice.text())
        self.assertEqual(view.product_build_grade_view.currentText(), "LQL / Target / HQL")
        self.assertFalse(view.product_build_table.isColumnHidden(view.product_build_headers.index("Fe Target")))

    def test_switching_modes_preserves_both_grade_sets_limits_and_basis(self):
        view = self.window()
        before = view.read_product_targets_from_table(False)[0]
        self.mode(view).setCurrentIndex(1)
        controls = view.product_target_mode_controls
        controls.evaluation.setCurrentIndex(controls.evaluation.findData("cumulative_build"))
        controls.limits["fe"].setCurrentIndex(1)
        self.mode(view).setCurrentIndex(0)
        self.assertFalse(controls.evaluation.isEnabled())
        hard = view.read_product_targets_from_table(False)[0]
        for key in before:
            if key.startswith("target_") and not key.endswith(("_mode", "_basis")):
                self.assertEqual(hard[key], before[key])
        self.mode(view).setCurrentIndex(1)
        self.assertEqual(controls.evaluation.currentData(), "cumulative_build")
        self.assertEqual(controls.limits["fe"].currentData(), "soft")
        self.assertEqual(controls.limits["mn"].currentData(), "hard")

    def test_settings_are_independent_per_opf_and_lump_fines_row(self):
        view = self.window([build(opf="CB_OPF", byproduct="lump"), build(opf="CC_OPF01", byproduct="fines")], True)
        self.mode(view, 0).setCurrentIndex(1)
        view.product_target_mode_controls.limits["mn"].setCurrentIndex(1)
        view.product_build_table.setCurrentCell(1, 0)
        self.assertFalse(view.product_target_mode_controls.limits["mn"].isEnabled())
        self.assertEqual(view.product_target_mode_controls.limits["mn"].currentData(), "hard")
        rows = view.read_product_targets_from_table(False)
        self.assertEqual([(r["opf"], r["byproduct"], r["target_mode"]) for r in rows], [("CB_OPF", "lump", "soft"), ("CC_OPF01", "fines", "hard")])

    def test_delete_then_edit_mode_and_resize_keeps_survivor_row_ownership(self):
        view = self.window([build(opf="FIRST"), build(opf="SECOND", target_mode="soft")])
        view.product_build_table.setCurrentCell(0, 0)
        view.delete_selected_product_build_rows()
        self.mode(view, 0).setCurrentIndex(0)
        self.assertEqual(view.read_product_targets_from_table(False)[0]["opf"], "SECOND")
        self.assertEqual(view.read_product_targets_from_table(False)[0]["target_mode"], "hard")
        view.set_product_build_table_row_count(2)
        self.assertEqual(self.mode(view, 1).currentData(), "hard")

    def test_saved_soft_rows_repopulate_with_correct_columns_and_full_precision(self):
        row = build(target_mode="soft", target_evaluation_basis="cumulative_build", target_fe_limit_mode="soft",
                    planning_scenario="2WCB", planning_grade_targets={"fe": 58.123456789})
        view = self.window([row])
        self.assertTrue(view.store_product_targets(False))
        view.product_targets = pickle.loads(pickle.dumps(view.calendar_inputs["product_targets"]))
        view.populate_product_build_table()
        self.assertEqual(view.product_build_grade_view.currentText(), "LQL / Target / HQL")
        self.assertEqual(view.product_target_mode_controls.evaluation.currentData(), "cumulative_build")
        self.assertEqual(float(self.item(view, "Fe Target").text()), 58.123456789)
        self.assertEqual(view.product_target_mode_controls.limits["fe"].currentData(), "soft")

    def test_all_grade_fields_remains_available_for_mixed_modes(self):
        view = self.window([build(), build(target_mode="soft")])
        view.product_build_grade_view.setCurrentText("All grade fields")
        view.product_build_table.setCurrentCell(1, 0)
        for name in ("Fe Min", "Fe Target", "Fe HQL"):
            self.assertFalse(view.product_build_table.isColumnHidden(view.product_build_headers.index(name)))

    def test_explicit_grade_view_survives_selecting_a_cell_in_the_same_build(self):
        for mode, grade_view, name in (("hard", "LQL / Target / HQL", "Fe Target"),
                                      ("soft", "Min / Max", "Fe Min")):
            with self.subTest(mode=mode):
                view = self.window([build(target_mode=mode)])
                view.product_build_grade_view.setCurrentText(grade_view)
                column = view.product_build_headers.index(name)
                view.product_build_table.setCurrentCell(0, column)
                self.assertEqual(view.product_build_grade_view.currentText(), grade_view)
                self.assertFalse(view.product_build_table.isColumnHidden(column))

    def test_invalid_quality_order_blocks_save_without_erasing_modes(self):
        view = self.window([build(target_mode="soft")])
        before = deepcopy(view.product_targets)
        self.item(view, "Fe Target").setText("61")
        self.assertFalse(view.store_product_targets(False))
        self.assertEqual(view.product_targets, before)
        self.assertEqual(self.mode(view).currentData(), "soft")

    def test_agent_modes_and_nested_settings_round_trip_and_invalid_modes_are_atomic(self):
        view = self.window()
        nested = with_quality_configuration(build(target_mode="soft", target_p_limit_mode="soft"))
        for key in TARGET_MODE_FIELDS:
            nested.pop(key)
        self.assertTrue(view.apply_agent_target_value("product_build_settings", [nested]))
        self.assertEqual(view.product_targets[0]["target_mode"], "soft")
        self.assertEqual(view.product_targets[0]["target_p_limit_mode"], "soft")
        before = deepcopy(view.product_targets)
        self.assertFalse(view.apply_agent_target_value("product_targets", [build(target_mode="guess")]))
        self.assertEqual(view.product_targets, before)

    def test_agent_workflow_saves_soft_settings_and_proceeds_to_calendar(self):
        view = self.window()
        view.agent_workflow_payload = {"product_targets": [build(target_mode="soft")]}
        view.stop_agent_workflow_apply = Mock()
        view.setup_calendar = Mock(side_effect=RuntimeError("reached calendar"))
        with self.assertRaisesRegex(RuntimeError, "reached calendar"):
            view.agent_workflow_apply_product_targets()
        self.assertEqual(view.calendar_inputs["product_targets"][0]["target_mode"], "soft")
        view.stop_agent_workflow_apply.assert_not_called()
        view.setup_calendar.assert_called_once()
        before = deepcopy(view.product_targets)
        view.agent_workflow_payload = {"product_targets": [build(product_target_schema_version=99)]}
        view.stop_agent_workflow_apply = Mock()
        view.setup_calendar = Mock()
        view.agent_workflow_apply_product_targets()
        view.stop_agent_workflow_apply.assert_called_once()
        view.setup_calendar.assert_not_called()
        self.assertEqual(view.product_targets, before)


if __name__ == "__main__":
    unittest.main()
