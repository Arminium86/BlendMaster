import copy
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classes.PhaseSchemas import FACTOR_LEVELS, SCHEMA_ANALYTES, reconciliation_sample
from classes.ReconciliationFactorResolver import (
    ReconciliationFactorResolver, aggregate_source_confidence,
)
from setup.InventoryBuildLineage import canonical_block


GB = "PIT01|1|453|426|456|LG01"
REMOTE = "PIT02|1|453|426|456|HG02"
AS_OF = datetime(2026, 8, 22, 6)


def composition(*items):
    return [{"grade_block_key": key, "feed_wmt": tonnes} for key, tonnes in items]


def standard():
    return {
        "source_brand": "CBSF", "substituted_brand": False,
        "source_brand_by_analyte": {a: {"blend": "CBSF", "regression": "CBSF"} for a in SCHEMA_ANALYTES},
        "lookback_days": {a: {"blend": 7, "regression": 14} for a in SCHEMA_ANALYTES},
        **{kind: {a: {"effective": value, "calculated": value, "row_count": 7} for a in SCHEMA_ANALYTES}
           for kind, value in (("blend", 1.07), ("regression", 0.97))},
    }


def period(start="2026-08-20 06:00", *, block=GB, blocks=None, feed=100, blend=1.1,
           regression=0.9, opf="CB OPF", brand="CBSF", rows=10):
    start = datetime.fromisoformat(start)
    return [reconciliation_sample(
        opf, brand, kind, start, start + timedelta(hours=12), feed_wmt=feed,
        contributing_blocks=composition((block, feed)) if blocks is None else blocks,
        factors={a: value for a in SCHEMA_ANALYTES}, product_dmt=20, source_rows=rows,
        provenance={"sample_id": f"{opf}|{brand}|{start.isoformat()}|{kind}", "lineage_basis": "test"},
    ) for kind, value in (("blend", blend), ("regression", regression))]


def resolver(samples=None, **kwargs):
    args = dict(opf="CB OPF", brand="SF", scenario_start=AS_OF, standard_factors=standard())
    args.update(kwargs)
    return ReconciliationFactorResolver(period() if samples is None else samples, **args)


class SpatialSelectionTests(unittest.TestCase):
    def test_each_confirmed_level_and_global(self):
        cases = [
            (GB, 0), ("PIT01|1|453|426|457|LG02", 1),
            ("PIT01|1|453|999|457|LG02", 2), ("PIT01|1|999|999|457|LG02", 3),
            ("PIT01|2|999|999|457|LG02", 4), ("PIT02|2|999|999|457|LG02", 5),
        ]
        for historical, level in cases:
            with self.subTest(level=level):
                result = resolver(period(block=historical)).resolve(GB)
                self.assertEqual(result["resolution_level"], FACTOR_LEVELS[level])
                self.assertEqual(len(result["provenance"]["attempts"]), level)
                for a in SCHEMA_ANALYTES:
                    self.assertAlmostEqual(result["blend_factors"][a], 1.07 if level == 5 else 1.1)
                    self.assertAlmostEqual(result["regression_factors"][a], 0.97 if level == 5 else 0.9)
                self.assertEqual(bool(result["fallback_reason"]), level > 0)

    def test_material_type_is_required_even_when_all_spatial_tokens_match(self):
        result = resolver(period(block=GB.replace("LG01", "HG01"))).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")
        self.assertEqual(result["blend_factors"]["fe"], 1.07)

    def test_aliases_slices_and_material_number_do_not_create_false_fallbacks(self):
        samples = period(block="Reserves/CB/PIT01/01/0453/426/0456/LG01_123")
        engine = resolver(samples)
        result = engine.resolve("CB_PIT01_01_0453_426_0456_LG02_99")
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
        self.assertEqual(result["matched_spatial_key"], ["PIT01", "1", "453", "426", "456", "LG"])
        self.assertEqual(engine.resolve(GB)["confidence_percent"], 100)

    def test_both_kinds_use_total_period_feed_not_matched_or_product_tonnes(self):
        samples = period(blocks=composition((GB, 1), (REMOTE, 99)))
        samples += period("2026-08-21 06:00", feed=300, blend=1.3, regression=1.1,
                          blocks=composition((GB, 299), (REMOTE, 1)), rows=20)
        samples[2]["product_dmt"] = samples[3]["product_dmt"] = 1
        result = resolver(samples).resolve(GB)
        self.assertAlmostEqual(result["blend_factors"]["fe"], 1.25)
        self.assertAlmostEqual(result["regression_factors"]["fe"], 1.05)
        self.assertEqual(result["source_feed_wmt"], 400)
        self.assertEqual(result["source_rows"], 30)
        self.assertEqual([p["matched_feed_wmt"] for p in result["source_history"]], [1, 299])
        self.assertEqual(len(result["source_history"]), 2)

    def test_multiple_blocks_in_one_cell_count_the_period_once(self):
        result = resolver(period(blocks=composition((GB, 40), (GB.replace("LG01", "LG02"), 60)))).resolve(GB)
        self.assertEqual(result["source_feed_wmt"], 100)
        self.assertEqual(result["source_rows"], 10)
        self.assertEqual(result["source_history"][0]["matched_feed_wmt"], 100)

    def test_one_missing_analyte_forces_both_kinds_to_the_same_broader_level(self):
        samples = period(blend=1.2)
        samples[1]["factors"]["mn"] = None
        samples += period("2026-08-21 06:00", block=GB.replace("|456|", "|457|"), blend=1.4, regression=1.3)
        result = resolver(samples).resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[1])
        self.assertAlmostEqual(result["blend_factors"]["fe"], 1.3)
        self.assertAlmostEqual(result["regression_factors"]["fe"], 1.1)
        self.assertAlmostEqual(result["regression_factors"]["mn"], 1.3)
        self.assertIn("regression.mn", result["fallback_reason"])

    def test_missing_analytes_can_use_different_periods_at_one_level(self):
        samples = period() + period("2026-08-21 06:00", blend=1.5, regression=1.2)
        samples[0]["factors"]["fe"] = None
        samples[3]["factors"]["mn"] = None
        result = resolver(samples).resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
        self.assertEqual(result["blend_factors"]["fe"], 1.5)
        self.assertEqual(result["regression_factors"]["mn"], 0.9)
        detail = result["provenance"]["factor_history"]
        self.assertEqual(detail["blend"]["fe"]["production_days"], 1)
        self.assertEqual(detail["blend"]["si"]["production_days"], 2)
        self.assertNotIn("period_indices", detail["blend"]["fe"])

    def test_two_shifts_on_one_date_do_not_meet_two_production_days(self):
        samples = period() + period("2026-08-20 18:00")
        self.assertEqual(resolver(samples, min_production_days=2).resolve(GB)["resolution_level"], "global")
        samples += period("2026-08-21 06:00")
        self.assertEqual(resolver(samples, min_production_days=2).resolve(GB)["resolution_level"], FACTOR_LEVELS[0])

    def test_bad_factor_values_do_not_support_minimum_days(self):
        for invalid in (None, 0, -1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                samples = period()
                samples[0]["factors"]["p"] = invalid
                self.assertEqual(resolver(samples).resolve(GB)["resolution_level"], "global")

    def test_other_opfs_and_brands_are_never_spatial_evidence(self):
        samples = period(opf="CC OPF01", blend=9) + period(brand="CBFL", blend=8)
        samples += period(blend=1.2)
        result = resolver(samples).resolve(GB)
        self.assertEqual(result["blend_factors"]["fe"], 1.2)
        self.assertEqual(result["source_feed_wmt"], 100)

    def test_ambiguous_brand_alias_falls_back_but_exact_brand_takes_precedence(self):
        samples = period() + period(brand="OTHER_SF", blend=9)
        result = resolver(samples).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")
        self.assertIn("Ambiguous", result["provenance"]["history_warnings"][0])
        samples += period(brand="SF", blend=1.4)
        result = resolver(samples).resolve(GB)
        self.assertEqual(result["blend_factors"]["fe"], 1.4)
        self.assertEqual(result["provenance"]["history_brand"], "SF")


class WindowTests(unittest.TestCase):
    def test_maximum_bound_and_scenario_cutoff_exclude_old_and_incomplete_shifts(self):
        samples = period("2026-08-19 18:00", blend=8) + period(blend=1.2)
        samples += period("2026-08-21 18:00", blend=1.4) + period("2026-08-22 06:00", blend=9)
        result = resolver(samples, max_lookback_days=2).resolve(GB)
        self.assertAlmostEqual(result["blend_factors"]["fe"], 1.3)
        self.assertEqual(result["lookback_start"], "2026-08-20T06:00:00")
        self.assertEqual(result["lookback_end"], "2026-08-22T06:00:00")
        self.assertEqual(result["lookback_days"], 2)

    def test_fixed_calendar_window_falls_back_spatially_without_expanding(self):
        samples = period(blend=9) + period("2026-08-21 06:00", block=GB.replace("|456|", "|457|"), blend=1.3)
        result = resolver(samples, method="lookback", lookback_days=1).resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[1])
        self.assertEqual(result["blend_factors"]["fe"], 1.3)
        result = resolver(samples[:2], method="lookback", lookback_days=1).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")

    def test_calendar_uses_completed_shift_dates_including_prior_night(self):
        samples = period("2026-08-21 18:00", blend=1.2) + period("2026-08-22 06:00", blend=9)
        result = resolver(samples, scenario_start="2026-08-22 18:00", method="lookback", lookback_days=1).resolve(GB)
        self.assertEqual(result["blend_factors"]["fe"], 1.2)
        self.assertEqual(result["lookback_end"], "2026-08-22T06:00:00")

    def test_production_days_span_gaps_but_campaign_stops_at_gap(self):
        samples = period("2026-08-17 06:00", blend=1.8) + period(blend=1.2)
        samples += period("2026-08-21 06:00", blend=1.4)
        prod = resolver(samples, method="lookback", window_mode="production_days", lookback_days=3).resolve(GB)
        campaign = resolver(samples, method="lookback", window_mode="latest_campaign", lookback_days=3).resolve(GB)
        self.assertAlmostEqual(prod["blend_factors"]["fe"], 4.4 / 3)
        self.assertAlmostEqual(campaign["blend_factors"]["fe"], 1.3)
        self.assertEqual(len(campaign["source_history"]), 2)
        # One shift per date still forms a consecutive-date campaign.
        limited = resolver(samples, method="lookback", window_mode="latest_campaign", lookback_days=1).resolve(GB)
        self.assertEqual(limited["blend_factors"]["fe"], 1.4)

    def test_production_date_selection_does_not_expand_to_replace_invalid_factors(self):
        samples = period(blend=1.8) + period("2026-08-21 06:00", blend=None)
        result = resolver(samples, method="lookback", window_mode="production_days", lookback_days=1).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")

    def test_production_windows_still_obey_maximum_calendar_guardrail(self):
        samples = period("2026-08-01 06:00", blend=9) + period()
        result = resolver(samples, max_lookback_days=3, method="lookback",
                          window_mode="production_days", lookback_days=30, min_production_days=2).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")

    def test_timezone_aware_times_are_normalised_to_perth(self):
        samples = period("2026-08-20T22:00:00+00:00")
        result = resolver(samples, scenario_start="2026-08-21T22:00:00+00:00").resolve(GB)
        self.assertEqual(result["lookback_start"], "2026-08-21T06:00:00")
        self.assertEqual(result["provenance"]["scenario_start"], AS_OF.isoformat())


class ConfidenceTests(unittest.TestCase):
    def test_exact_composition_scores_100_at_source_and_component_levels(self):
        blocks = composition((GB, 40), (REMOTE, 60))
        result = resolver(period(blocks=blocks)).resolve_source("stockpile", "inventory", blocks, 100)
        self.assertEqual(result["confidence_percent"], 100)
        self.assertEqual(result["uncertainty_percent"], 0)
        self.assertEqual([r["confidence_percent"] for r in result["records"]], [100, 100])

    def test_composition_ratio_mismatch_changes_confidence_not_factors(self):
        samples = period(blocks=composition((GB, 60), (REMOTE, 40)))
        result = resolver(samples).resolve_source("hex", "amt", composition((GB, 40), (REMOTE, 60)), 100, hex_id="7")
        self.assertAlmostEqual(result["confidence_percent"], 80)
        self.assertEqual(result["hex_id"], "7")
        for record in result["records"]:
            self.assertEqual(record["blend_factors"]["fe"], 1.1)
            self.assertEqual(record["hex_id"], "7")

    def test_similarity_falls_with_spatial_distance(self):
        blocks = [GB, GB.replace("LG01", "LG02"), GB.replace("|456|", "|457|"),
                  "PIT01|1|453|999|457|LG02", "PIT01|1|999|999|457|LG02", "PIT01|2|999|999|457|LG02"]
        for block, overlap in zip(blocks, (6, 5, 4, 3, 2, 1)):
            with self.subTest(block=block):
                result = resolver(period(block=block)).resolve(GB)
                self.assertAlmostEqual(result["confidence_percent"], 100 * overlap / 6)
                self.assertAlmostEqual(result["uncertainty_percent"], 100 - 100 * overlap / 6)

    def test_history_unknown_share_is_not_renormalised(self):
        result = resolver(period(blocks=composition((GB, 50), ("unmapped build", 50)))).resolve(GB)
        self.assertEqual(result["confidence_percent"], 50)
        self.assertEqual(result["source_history"][0]["lineage_coverage"], 0.5)

    def test_unknown_source_share_is_penalised_once_and_retains_global_factors(self):
        result = resolver().resolve_source("stockpile", "inventory", composition((GB, 75), ("unmapped", 25)), 100)
        known, unknown = result["records"]
        self.assertEqual(known["confidence_percent"], 100)
        self.assertEqual(known["lineage_fraction"], 0.75)
        self.assertEqual(unknown["lineage_fraction"], 0.25)
        self.assertEqual(unknown["resolution_level"], "global")
        self.assertEqual(unknown["blend_factors"]["fe"], 1.07)
        self.assertEqual(result["confidence_percent"], 75)
        self.assertEqual(result["lineage_coverage"], 0.75)

    def test_unknown_source_and_unknown_history_reduce_confidence_together(self):
        result = resolver(period(blocks=composition((GB, 50)))).resolve_source(
            "stockpile", "inventory", composition((GB, 75)), 100)
        self.assertEqual(result["confidence_percent"], 37.5)

    def test_all_unknown_source_keeps_one_global_record_and_zero_score(self):
        result = resolver().resolve_source("unknown", "inventory", [], 100)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["lineage_fraction"], 1)
        self.assertEqual(result["records"][0]["resolution_level"], "global")
        self.assertEqual(result["confidence_percent"], 0)

    def test_per_analyte_confidence_uses_its_period_weights_and_overall_uses_minimum(self):
        samples = period() + period("2026-08-21 06:00", feed=300, blocks=composition((GB, 150)))
        samples[0]["factors"]["mn"] = None
        result = resolver(samples).resolve(GB)
        detail = result["provenance"]["factor_history"]
        self.assertEqual(detail["blend"]["fe"]["confidence_percent"], 62.5)
        self.assertEqual(detail["blend"]["mn"]["confidence_percent"], 50)
        self.assertEqual(result["confidence_percent"], 50)

    def test_distinct_components_keep_distinct_factors_and_physical_fractions(self):
        samples = period(blend=1.2) + period("2026-08-21 06:00", block=REMOTE, blend=1.4)
        result = resolver(samples).resolve_source("hex", "amt", composition((GB, 40), (REMOTE, 60)), 100)
        records = {r["grade_block_key"]: r for r in result["records"]}
        self.assertEqual(records[GB]["blend_factors"]["fe"], 1.2)
        self.assertEqual(records[REMOTE]["blend_factors"]["fe"], 1.4)
        self.assertEqual(records[GB]["lineage_fraction"], 0.4)
        self.assertEqual(records[REMOTE]["lineage_fraction"], 0.6)

    def test_wmt_weighted_source_aggregation_keeps_unscored_mass(self):
        result = aggregate_source_confidence([
            {"source_wmt": 100, "confidence_percent": 100},
            {"source_wmt": 300, "confidence_percent": 50},
            {"source_wmt": 100, "confidence_percent": None},
        ])
        self.assertEqual(result, {"source_wmt": 500, "confidence_percent": 50, "uncertainty_percent": 50})
        self.assertIsNone(aggregate_source_confidence([])["confidence_percent"])

    def test_zero_tonne_source_has_no_grade_components_or_confidence(self):
        result = resolver().resolve_source("empty", "inventory", [], 0)
        self.assertEqual(result["records"], [])
        self.assertIsNone(result["confidence_percent"])

    def test_rounding_cannot_produce_confidence_above_100_or_negative_uncertainty(self):
        samples = period(feed=0.01) + period("2026-08-21 06:00", feed=0.02)
        result = resolver(samples).resolve(GB)
        self.assertLessEqual(result["confidence_percent"], 100)
        self.assertGreaterEqual(result["uncertainty_percent"], 0)
        self.assertAlmostEqual(result["confidence_percent"], 100)
        for kind in ("blend", "regression"):
            for field in result["provenance"]["factor_history"][kind].values():
                self.assertLessEqual(field["confidence_percent"], 100)


class ResolverIntegrityTests(unittest.TestCase):
    def test_standard_mode_retains_all_effective_global_values_and_overrides(self):
        factors = standard()
        factors["blend"]["fe"]["effective"] = 1.42
        result = resolver(["unused malformed history"], method="standard", standard_factors=factors).resolve(GB)
        self.assertEqual(result["blend_factors"]["fe"], 1.42)
        self.assertTrue(result["manual_override"])
        self.assertEqual(result["source_history"][0]["standard_record"], factors)
        self.assertEqual(result["resolution_level"], "global")
        self.assertEqual(result["confidence_percent"], 0)

    def test_missing_global_factor_is_not_silently_replaced_with_one(self):
        factors = standard()
        del factors["regression"]["p"]
        with self.assertRaisesRegex(ValueError, "standard regression/p"):
            resolver(standard_factors=factors)
        with self.assertRaisesRegex(ValueError, "standard factor record"):
            resolver(standard_factors=None)

    def test_supplied_global_unit_factor_is_preserved(self):
        factors = standard()
        factors["blend"]["fe"] = 1.0
        self.assertEqual(resolver([], standard_factors=factors).resolve(GB)["blend_factors"]["fe"], 1.0)

    def test_unpaired_or_zero_feed_history_is_excluded_with_a_reason(self):
        for samples in (period()[:1], period(feed=0)):
            result = resolver(samples).resolve(GB)
            self.assertEqual(result["resolution_level"], "global")
            self.assertTrue(result["provenance"]["history_warnings"])

    def test_duplicate_overlapping_or_inconsistent_pairs_are_rejected(self):
        samples = period()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            resolver(samples + samples)
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            resolver(samples + period("2026-08-20 12:00"))
        samples[1]["feed_wmt"] = 101
        with self.assertRaisesRegex(ValueError, "disagree"):
            resolver(samples)
        samples = period()
        samples[1]["contributing_blocks"] = composition((REMOTE, 100))
        with self.assertRaisesRegex(ValueError, "disagree"):
            resolver(samples)

    def test_future_schema_and_bad_periods_are_rejected(self):
        for field, value, message in (("schema_version", 999, "schema version"),
                                      ("period_end", "2026-08-19", "interval"),
                                      ("kind", "unknown", "factor kind"),
                                      ("grain", "unknown", "grain")):
            with self.subTest(field=field):
                samples = period()
                samples[0][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    resolver(samples)

    def test_invalid_configuration_and_source_mass_are_rejected(self):
        for args in ({"min_production_days": 0}, {"max_lookback_days": 1.5},
                     {"lookback_days": True}, {"method": "auto"}, {"window_mode": "auto"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                resolver(**args)
        engine = resolver()
        for blocks, total in ((composition((GB, -1)), 100), (composition((GB, 101)), 100),
                              (composition((GB, 1)), 0), ([], -1)):
            with self.subTest(blocks=blocks, total=total), self.assertRaises(ValueError):
                engine.resolve_source("test", "inventory", blocks, total)
        with self.assertRaises(ValueError):
            engine.resolve(GB, lineage_fraction=1.1)

    def test_tiny_floating_overshoot_does_not_create_excess_fractions(self):
        engine = resolver()
        result = engine.resolve_source("test", "inventory", composition((GB, 100.000000001)), 100)
        self.assertAlmostEqual(sum(r["lineage_fraction"] for r in result["records"]), 1)
        self.assertLessEqual(result["records"][0]["lineage_fraction"], 1)
        self.assertLessEqual(engine.resolve(GB, source_composition=composition((GB, 100.000000001)),
                                            source_wmt=100)["lineage_fraction"], 1)

    def test_results_are_serialisable_deterministic_and_detached_from_inputs_and_cache(self):
        samples = period() + period("2026-08-21 06:00", blend=1.3)
        factors = standard()
        original = copy.deepcopy(samples)
        engine = resolver(samples, standard_factors=factors)
        expected = engine.resolve(GB)
        reversed_result = resolver(list(reversed(samples))).resolve(GB)
        self.assertEqual(expected, reversed_result)
        self.assertEqual(json.loads(json.dumps(expected, allow_nan=False)), expected)
        expected["blend_factors"]["fe"] = 99
        expected["provenance"]["factor_history"]["blend"]["fe"]["feed_wmt"] = -1
        samples[0]["factors"]["fe"] = 98
        factors["blend"]["fe"]["effective"] = 97
        self.assertEqual(engine.resolve(GB), reversed_result)
        self.assertEqual(engine.resolve("unknown")["blend_factors"]["fe"], 1.07)
        self.assertEqual(original[1:], samples[1:])
        engine.resolve(GB.replace("LG01", "LG02"))
        self.assertEqual(len(engine._selection_cache), 1)

    def test_full_source_composition_and_component_api_agree(self):
        blocks = composition((GB, 40), (REMOTE, 60))
        engine = resolver(period(blocks=blocks))
        component = engine.resolve(GB, source_id="hex", source_kind="amt", hex_id="7",
                                   source_composition=blocks, source_wmt=100)
        source = engine.resolve_source("hex", "amt", blocks, 100, hex_id="7")
        self.assertEqual(component, next(r for r in source["records"] if r["grade_block_key"] == canonical_block(GB)))

    def test_period_similarity_is_reused_within_one_source_but_never_between_sources(self):
        from classes.ReconciliationFactorResolver import _similarity
        blocks = composition((GB, 40), (REMOTE, 60))
        engine = resolver(period(blocks=blocks))
        with patch("classes.ReconciliationFactorResolver._similarity", wraps=_similarity) as score:
            exact = engine.resolve_source("first", "amt", blocks, 100)
            different = engine.resolve_source("second", "amt", composition((GB, 60), (REMOTE, 40)), 100)
        self.assertEqual(score.call_count, 2)  # One period per source, not per component.
        self.assertEqual(exact["confidence_percent"], 100)
        self.assertAlmostEqual(different["confidence_percent"], 80)


if __name__ == "__main__":
    unittest.main()
