"""Real CBC decisions for Soft grades, opening balances and hard compatibility."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from tests import test_decision_levers as fixtures
from tests.test_product_quality_limits import build
from classes.SoftProductGrades import objective_config, analyte_audit, shape_cost
from GUI.SoftGradePreferenceControls import SoftGradePreferenceControls
from PyQt5.QtWidgets import QApplication


class SoftGradeTests(unittest.TestCase):
    event = staticmethod(fixtures.DecisionLeverOptimizerTests.event)
    target = staticmethod(fixtures.DecisionLeverOptimizerTests.target)
    optimize = fixtures.DecisionLeverOptimizerTests.optimize
    selected_sources = staticmethod(fixtures.DecisionLeverOptimizerTests.selected_sources)

    def solve(self, events, row=None, state=None, **config):
        return self.optimize(events, dict(target_product_build=row or build(target_mode="soft", target_fe_target=58),
                             target_product_build_state=state or {}, throughput_incentive_per_tonne=1e6, **config))

    def fe(self, result):
        return next(r for r in result["diagnostics"]["product_quality"] if r["analyte"] == "fe")

    def test_soft_hits_target_with_a_mixture_instead_of_legacy_minimum(self):
        result = self.solve([self.event("LOW", grade_fe=56), self.event("HIGH", grade_fe=60)],
                            build(target_mode="soft", target_fe_target=58, target_fe_min=60))
        self.assertEqual(self.selected_sources(result), {"LOW", "HIGH"})
        self.assertAlmostEqual(self.fe(result)["actual_grade"], 58)
        self.assertAlmostEqual(self.fe(result)["total_penalty"], 0)

    def test_soft_breach_is_allowed_penalised_and_reported_off_limits(self):
        row = build(target_mode="soft", target_fe_target=58, target_fe_lql=57, target_fe_limit_mode="soft")
        result = self.solve([self.event("LOW", grade_fe=56)], row)
        audit = self.fe(result)
        self.assertEqual(self.selected_sources(result), {"LOW"})
        self.assertAlmostEqual(audit["target_penalty"], 40000)
        self.assertAlmostEqual(audit["limit_penalty"], 50000)
        self.assertFalse(audit["within_limits"])
        self.assertTrue(audit["hard_limits_satisfied"])

    def test_hard_lql_is_enforced_even_with_target_penalty_disabled(self):
        row = build(target_mode="soft", target_fe_lql=59, target_fe_target=60)
        result = self.solve([self.event("LOW", grade_fe=58)], row,
                            soft_grade_preferences={"target_weight": 0})
        self.assertEqual(self.selected_sources(result), set())

    def test_cumulative_corrects_opening_build_and_subtracts_opening_penalty(self):
        row = build(target_mode="soft", target_fe_target=58, target_evaluation_basis="cumulative_build", target_fe_limit_mode="soft")
        opening = dict(tonnes=100, grade_fe_metal=5600)
        result = self.solve([self.event("CORRECT", grade_fe=60), self.event("TARGET", grade_fe=58)], row, opening)
        self.assertEqual(self.selected_sources(result), {"CORRECT"})
        self.assertAlmostEqual(self.fe(result)["actual_grade"], 58)
        self.assertAlmostEqual(self.fe(result)["applied_penalty"], -90000)
        row["target_evaluation_basis"] = "steady_state"
        steady = self.solve([self.event("CORRECT", grade_fe=60), self.event("TARGET", grade_fe=58)], row, opening)
        self.assertEqual(self.selected_sources(steady), {"TARGET"})
        self.assertAlmostEqual(self.fe(steady)["opening_penalty"], 0)

    def test_partial_cumulative_hard_limits_do_not_follow_legacy_completion_switches(self):
        row = build(target_tonnes=10000, target_mode="soft", target_fe_target=58, target_fe_lql=57,
                    target_evaluation_basis="cumulative_build")
        # The 56% addition is valid only together with the 60% opening material.
        result = self.solve([self.event("LOW", grade_fe=56)], row, dict(tonnes=100, grade_fe_metal=6000),
                            allow_offspec_steady_states_for_product_build=False,
                            active_product_build_completes_within_horizon=False)
        self.assertEqual(self.selected_sources(result), {"LOW"})
        self.assertAlmostEqual(self.fe(result)["actual_grade"], 58)

    def test_calendar_hard_bounds_remain_independent(self):
        result = self.optimize([self.event("LOW", grade_fe=58)],
                              dict(target_product_build=build(target_mode="soft", target_fe_target=58)), target_fe_min=60)
        self.assertEqual(self.selected_sources(result), set())

    def test_analyte_scale_and_tonnes_change_penalty_as_displayed(self):
        row = build(target_mode="soft", target_fe_lql=None, target_fe_target=58, target_fe_hql=None)
        first = analyte_audit(row, "fe", 5900, 100)
        twice = analyte_audit(row, "fe", 11800, 200)
        self.assertAlmostEqual(twice["total_penalty"], 2 * first["total_penalty"])
        p = build(target_mode="soft", target_p_target=.08)
        self.assertAlmostEqual(analyte_audit(p, "p", 9, 100)["total_penalty"], first["total_penalty"])
        self.assertEqual([shape_cost(d, 1, "piecewise_linear") for d in (0, 1, 2, 3)], [0, 1, 4, 9])

    def test_zero_target_and_one_sided_limits_remain_explicit(self):
        row = build(target_mode="soft", target_fe_target=0, target_fe_lql=None, target_fe_hql=1)
        result = self.solve([self.event("ZERO", grade_fe=0), self.event("HIGH", grade_fe=2)], row)
        self.assertEqual(self.selected_sources(result), {"ZERO"})
        self.assertEqual(self.fe(result)["target"], 0)

    def test_configuration_rejects_invalid_and_future_values(self):
        for value in ({"target_weight": -1}, {"target_weight": float("nan")}, {"limit_multiplier": 1},
                      {"shape": "quadratic"}, {"schema_version": 2}, {"analytes": {"fe": {"scale": 0}}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                objective_config(value)

    def test_penalty_weight_trades_against_existing_source_cost(self):
        row = build(target_mode="soft", target_fe_target=58, target_fe_lql=None, target_fe_hql=None)
        def events():
            return [self.event("CHEAP", grade_fe=56), self.event("TARGET", grade_fe=58, cost=1)]
        without = self.solve(events(), row, soft_grade_preferences={"target_weight": 0})
        with_penalty = self.solve(events(), row, soft_grade_preferences={"target_weight": 1})
        self.assertEqual(self.selected_sources(without), {"CHEAP"})
        self.assertEqual(self.selected_sources(with_penalty), {"TARGET"})

    def test_missing_declared_grade_weight_cannot_create_unpenalised_soft_feed(self):
        event = self.event("NO_DMT", grade_fe=56)
        result = self.solve([event], source_property_weights={"adjusted_product_fe": "missing_dmt"})
        self.assertEqual(self.selected_sources(result), set())

    def test_soft_diagnostic_does_not_claim_legacy_minimum_is_a_constraint(self):
        row = build(target_mode="soft", target_fe_target=58, target_fe_min=99, target_fe_limit_mode="soft")
        result = self.solve([self.event("FE58", grade_fe=58)], row)
        diagnostic = result["diagnostics"]["product_build_targets"][0]
        self.assertEqual(diagnostic["target_mode"], "soft")
        self.assertEqual(diagnostic["grade_targets"]["Fe"]["target_min"], 0)
        self.assertFalse(any("99" in cause for cause in result["diagnostics"]["likely_causes"]))


class SoftGradeControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_configuration_round_trip_and_invalid_edits(self):
        controls = SoftGradePreferenceControls()
        self.addCleanup(controls.deleteLater)
        configured = objective_config({"target_weight": 12.3456789, "shape": "linear", "analytes": {"p": {"scale": .005, "weight": 3}}})
        controls.set_config(configured)
        self.assertEqual(controls.config(), configured)
        controls.analytes["p"]["scale"].setText("0")
        with self.assertRaisesRegex(ValueError, "scale"):
            controls.config()


if __name__ == "__main__":
    unittest.main()
