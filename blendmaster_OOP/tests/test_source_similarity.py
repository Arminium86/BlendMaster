"""Optional source-to-product-Target preferences change actual CBC decisions."""

import unittest
from tests import test_soft_product_grades as soft_tests
from tests.test_product_quality_limits import build
from classes.SoftProductGrades import objective_config, source_preference, similarity_coefficients


class SourceSimilarityTests(unittest.TestCase):
    event = staticmethod(soft_tests.SoftGradeTests.event)
    target = staticmethod(soft_tests.SoftGradeTests.target)
    optimize = soft_tests.SoftGradeTests.optimize
    selected_sources = staticmethod(soft_tests.SoftGradeTests.selected_sources)
    solve = soft_tests.SoftGradeTests.solve
    fe = soft_tests.SoftGradeTests.fe

    def choices(self):
        return [self.event("LOW", grade_fe=56), self.event("HIGH", grade_fe=60), self.event("CLOSE", grade_fe=58, cost=1)]

    def test_closeness_dispersion_and_both_prefer_individually_representative_source(self):
        baseline = self.solve(self.choices())
        self.assertEqual(self.selected_sources(baseline), {"LOW", "HIGH"})
        for mode in ("closeness", "dispersion", "both"):
            with self.subTest(mode=mode):
                result = self.solve(self.choices(), soft_grade_preferences={"similarity_mode": mode})
                self.assertEqual(self.selected_sources(result), {"CLOSE"})
                self.assertAlmostEqual(self.fe(result)["source_closeness_score"], 1)
                self.assertAlmostEqual(self.fe(result)["source_dispersion_score"], 0)

    def test_per_analyte_disable_and_zero_weight_restore_the_cheaper_mixture(self):
        for item in ({"similarity_enabled": False}, {"similarity_weight": 0}):
            result = self.solve(self.choices(), soft_grade_preferences={"similarity_mode": "both", "analytes": {"fe": item}})
            self.assertEqual(self.selected_sources(result), {"LOW", "HIGH"})

    def test_amt_and_inventory_are_both_eligible(self):
        events = [self.event("AMT", grade_fe=58, is_amt=True), self.event("INV", grade_fe=58)]
        config = objective_config({"similarity_mode": "both"})
        values = {"product": {a: [58, 58] for a in ("fe", "si", "al", "p", "mn")}}
        weights = {"product": {a: [1, 1] for a in values["product"]}}
        result = similarity_coefficients(events, {"product": build(target_mode="soft", target_fe_target=58)}, values, weights, config)
        self.assertEqual([r["eligible_weight"] for r in result[("product", "fe")]], [1, 1])

    def test_direct_tip_exclusion_is_optional_and_analyte_scales_match(self):
        config = objective_config({"similarity_mode": "both"})
        self.assertEqual(source_preference("fe", 59, 58, 100, True, config)["eligible_weight"], 0)
        config["include_direct_tip"] = True
        fe = source_preference("fe", 59, 58, 100, True, config)
        p = source_preference("p", .09, .08, 100, False, config)
        self.assertAlmostEqual(fe["dispersion_penalty"], p["dispersion_penalty"])
        self.assertEqual(fe["eligible_weight"], 100)

    def test_selected_product_stream_and_active_brand_drive_similarity(self):
        events = [self.event("A", grade_fe=10), self.event("B", grade_fe=58)]
        for event, modelled, adjusted in zip(events, (56, 58), (58, 56)):
            event._grade_streams = {stream: {"FB": {a: fe if a == "fe" else 1 for a in ("fe", "si", "al", "p", "mn")},
                                           "SF": {a: 99 for a in ("fe", "si", "al", "p", "mn")}}
                                   for stream, fe in (("modelled_product", modelled), ("adjusted_product", adjusted))}
        config = dict(soft_grade_preferences={"similarity_mode": "dispersion"}, target_product_brand="FB")
        result = self.solve(events, selected_data_stream="adjusted_product", **config)
        self.assertEqual(self.selected_sources(result), {"A"})
        result = self.solve(events, selected_data_stream="modelled_product", **config)
        self.assertEqual(self.selected_sources(result), {"B"})

    def test_rom_to_product_comparison_is_rejected_when_similarity_enabled(self):
        with self.assertRaisesRegex(ValueError, "product grade stream"):
            self.solve(self.choices(), selected_data_stream="modelled_rom", soft_grade_preferences={"similarity_mode": "both"})

    def test_hard_builds_are_unchanged_by_similarity_preferences(self):
        result = self.solve(self.choices(), build(), soft_grade_preferences={"similarity_mode": "both"})
        self.assertNotIn("CLOSE", self.selected_sources(result))
        self.assertEqual(result["diagnostics"]["applied_source_similarity_penalty"], 0)

    def test_byproduct_similarity_uses_lane_grades_and_declared_weight_not_head_grade(self):
        events = [self.event("HEAD_MATCH", grade_fe=58), self.event("FINES_MATCH", grade_fe=40)]
        grades = ("fe", "si", "al", "p", "mn")
        for event, fines_fe in zip(events, (62, 58)):
            event._source_properties = {"rom_wmt": 1000, "fines_dmt": 600,
                                        **{f"fines_{a}": fines_fe if a == "fe" else 1 for a in grades}}
        result = self.solve(events,
                    byproducts_enabled=True, byproduct_quantity_fields={"fines": "fines_dmt"},
                    byproduct_grade_fields={"fines": {a: f"fines_{a}" for a in grades}},
                    source_property_weights={f"fines_{a}": "fines_dmt" for a in grades},
                    target_product_builds={"fines": build(target_mode="soft", target_fe_target=58)},
                    soft_grade_preferences={"similarity_mode": "both"})
        self.assertEqual(self.selected_sources(result), {"FINES_MATCH"})
        self.assertAlmostEqual(self.fe(result)["grade_weight_tonnes"], 60)
        self.assertAlmostEqual(self.fe(result)["actual_grade"], 58)


if __name__ == "__main__":
    unittest.main()
