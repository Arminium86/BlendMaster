"""Shared Auto evidence: real competing histories, independent oracle and UI propagation."""

import copy
from datetime import datetime, timedelta
import json
import math
import os
import random
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from GUI.ReconciliationReview import ReconciliationReview
from PyQt5.QtWidgets import QApplication
from classes.PhaseSchemas import FACTOR_LEVELS, SCHEMA_ANALYTES
from classes.ReconciliationApplication import aggregate_reconciliation
from classes.ReconciliationControls import reconciliation_columns, reconciliation_report_rows, spatial_cell
from tests.test_reconciliation_factor_resolver import GB, REMOTE, AS_OF, composition, period
from tests.test_reconciliation_confidence_search import auto
from tests.test_reconciliation_application import application, apply, window, streams
from tests.test_reconciliation_controls import local


AUTO = "auto_max_confidence"
HG = GB.replace("LG01", "HG25")
BA = GB.replace("LG01", "BA01")
MIXTURE = composition((HG, 50), (BA, 50))


def shared_advantage_history():
    # A large pure-HG shift shares a date with an excellent mixed shift. No
    # calendar window can remove it while retaining the two required dates.
    return (period("2026-08-19 06:00", feed=10000,
                   blocks=composition((HG, 5000), (BA, 5000)), blend=1.1, regression=.9)
            + period("2026-08-19 18:00", feed=100000, block=HG, blend=1.8, regression=1.6)
            + period("2026-08-20 06:00", feed=1,
                     blocks=composition((HG, .2), (BA, .2), (REMOTE, .6)), blend=1.4, regression=1.2))


def resolve(history=None, blocks=None, **kwargs):
    return auto(shared_advantage_history() if history is None else history,
                min_production_days=kwargs.pop("min_production_days", 2), **kwargs).resolve_source(
                    "SP1", "amt", MIXTURE if blocks is None else blocks, 100, hex_id="H1")


def component_only(history=None, blocks=None, **kwargs):
    with patch("classes.ReconciliationSharedHistory.SharedHistorySearch.evaluate", return_value=None):
        return resolve(history, blocks, **kwargs)


def independent_shared_oracle(history, source, minimum, maximum):
    """Enumerate raw shift rows/levels/every integer N; do not use search/resolver helpers."""
    def cell(block, depth):
        parts = block.split("|")
        return block if depth == -1 else tuple(parts[:5 - depth]) + (re.match("[A-Z]+", parts[-1])[0],)

    def bins(blocks, total, depth):
        result = {}
        for row in blocks:
            if row["feed_wmt"] > 0:
                key = cell(row["grade_block_key"], depth)
                result[key] = result.get(key, 0) + row["feed_wmt"] / total
        return result

    known = sum(r["feed_wmt"] for r in source)
    shifts = []
    for left, right in zip(history[::2], history[1::2]):
        start, end = datetime.fromisoformat(left["period_start"]), datetime.fromisoformat(left["period_end"])
        score = 0
        for depth in range(-1, 5):
            a, b = bins(source, known, depth), bins(left["contributing_blocks"], left["feed_wmt"], depth)
            score += sum(min(v, b.get(k, 0)) for k, v in a.items()) * 100 / 6
        complete = all(s["factors"][a] is not None and s["factors"][a] > 0
                       for s in (left, right) for a in SCHEMA_ANALYTES)
        if end <= AS_OF and start >= AS_OF - timedelta(days=maximum):
            shifts.append(dict(start=start, day=start.date(), feed=left["feed_wmt"], score=score,
                               complete=complete, blocks=left["contributing_blocks"]))
    best = None
    midnight = AS_OF.replace(hour=0)
    for family in ("spatial", "calendar", "production", "campaign"):
        for n in range(1, maximum + 1):
            bounded = shifts
            if family == "spatial":
                bounded = [s for s in shifts if s["start"] >= AS_OF - timedelta(days=n)]
            elif family == "calendar":
                bounded = [s for s in shifts if midnight - timedelta(days=n) <= s["start"] < midnight]
            else:
                days = sorted({s["day"] for s in shifts}, reverse=True)
                if family == "campaign":
                    for i in range(1, len(days)):
                        if days[i - 1] - days[i] != timedelta(days=1):
                            days = days[:i]
                            break
                bounded = [s for s in shifts if s["day"] in days[:n]]
            for depth in range(5):
                required = set(bins(source, known, depth))
                eligible = [s for s in bounded if s["complete"] and required <= set(bins(s["blocks"], s["feed"], depth))]
                if len({s["day"] for s in eligible}) < minimum:
                    continue
                if family == "spatial":
                    # Try all score cutoffs, accepting the highest that covers
                    # enough dates. Independent from the production-date sort.
                    for cutoff in sorted({round(s["score"], 10) for s in eligible}, reverse=True):
                        selected = [s for s in eligible if round(s["score"], 10) >= cutoff]
                        if len({s["day"] for s in selected}) >= minimum:
                            break
                else:
                    selected = eligible
                score = sum(s["feed"] * s["score"] for s in selected) / sum(s["feed"] for s in selected) * known / 100
                best = score if best is None else max(best, score)
    return best


class SharedHistoryTests(unittest.TestCase):
    def test_common_set_beats_component_history_and_supplies_all_ten_factors(self):
        result, old = resolve(), component_only()
        self.assertEqual(result["auto_selection"]["history_approach"], "shared_history")
        expected_score = (10000 * 100 + 40) / 10001
        self.assertAlmostEqual(result["confidence_percent"], expected_score)
        self.assertGreater(result["confidence_percent"], old["confidence_percent"] + 20)
        expected_dates = ["2026-08-19T06:00:00", "2026-08-20T06:00:00"]
        for record in result["records"]:
            self.assertEqual([s["period_start"] for s in record["source_history"]], expected_dates)
            self.assertEqual(record["resolution_level"], FACTOR_LEVELS[0])
            for kind, first, second in (("blend", 1.1, 1.4), ("regression", .9, 1.2)):
                for a in SCHEMA_ANALYTES:
                    self.assertAlmostEqual(record[kind + "_factors"][a], (10000 * first + second) / 10001)
                    detail = record["provenance"]["factor_history"][kind][a]
                    self.assertEqual((detail["production_days"], detail["period_count"], detail["feed_wmt"]), (2, 2, 10001))
                    self.assertAlmostEqual(detail["confidence_percent"], expected_score)
        alternatives = result["auto_selection"]["best_by_approach"]
        self.assertAlmostEqual(alternatives["component_based"]["confidence_percent"], old["confidence_percent"])

    def test_pure_shifts_do_not_pool_into_a_perfect_mixture(self):
        history = period(block=HG) + period("2026-08-21 06:00", block=BA)
        result = resolve(history, min_production_days=1)
        self.assertAlmostEqual(result["confidence_percent"], 50)
        self.assertEqual(result["auto_selection"]["history_approach"], "component_based")
        self.assertFalse(result["auto_selection"]["best_by_approach"]["shared_history"]["eligible"])
        self.assertTrue(all(not c["eligible"] for c in result["auto_selection"]["shared_level_comparison"]))

    def test_any_positive_group_share_is_eligible_without_a_percentage_threshold(self):
        history = period(blocks=composition((HG, 99.999), (BA, .001)))
        result = resolve(history, min_production_days=1)
        common = result["auto_selection"]["best_by_approach"]["shared_history"]
        self.assertTrue(common["eligible"])
        self.assertAlmostEqual(common["confidence_percent"], 50.001)
        # Identical full ranks preserve the established component approach.
        self.assertEqual(result["auto_selection"]["history_approach"], "component_based")
        self.assertTrue(all(c["history_approach"] == "component_based" for c in result["auto_selection"]["best_by_family"].values()))

    def test_common_levels_allow_coarser_addresses_but_keep_the_fixed_score(self):
        history = period(blocks=composition((HG.replace("456", "457"), 50), (BA.replace("456", "458"), 50)))
        result = resolve(history, min_production_days=1)
        levels = result["auto_selection"]["shared_level_comparison"]
        self.assertFalse(levels[0]["eligible"])
        self.assertTrue(levels[1]["selected"])
        self.assertAlmostEqual(levels[1]["evidence_match_score_percent"], 100 * 4 / 6)
        self.assertTrue(all(c["eligible"] for c in levels[1:]))
        self.assertAlmostEqual(result["confidence_percent"], 100 * 4 / 6)

    def test_shared_blast_winner_materialises_common_factors_for_distinct_source_cells(self):
        history = shared_advantage_history()
        for row in [*history[:2], *history[-2:]]:
            for block in row["contributing_blocks"]:
                if block["grade_block_key"] in (HG, BA):
                    block["grade_block_key"] = block["grade_block_key"].replace("456", "457")
        result = resolve(history)
        self.assertEqual(result["auto_selection"]["history_approach"], "shared_history")
        self.assertGreater(result["confidence_percent"], component_only(history)["confidence_percent"])
        self.assertTrue(all(r["resolution_level"] == FACTOR_LEVELS[1] for r in result["records"]))
        self.assertEqual(result["records"][0]["blend_factors"], result["records"][1]["blend_factors"])
        self.assertAlmostEqual(result["confidence_percent"], (10000 * 100 * 4 / 6 + 40 * 4 / 6) / 10001)

    def test_ten_series_keep_their_own_values_when_using_identical_shifts(self):
        history = shared_advantage_history()
        for i, row in enumerate(history):
            row["factors"] = {a: .8 + i / 10 + j / 100 for j, a in enumerate(SCHEMA_ANALYTES)}
        result = resolve(history)
        self.assertEqual(result["auto_selection"]["history_approach"], "shared_history")
        for record in result["records"]:
            for offset, kind in enumerate(("blend", "regression")):
                for a in SCHEMA_ANALYTES:
                    expected = (10000 * history[offset]["factors"][a] + history[4 + offset]["factors"][a]) / 10001
                    self.assertAlmostEqual(record[kind + "_factors"][a], expected)

    def test_shared_checks_coarser_levels_even_when_fine_level_is_sufficient(self):
        history = period(blocks=composition((HG, 1), (BA, 1), (REMOTE, 98)))
        history += period("2026-08-21 06:00", blocks=composition((HG.replace("456", "457"), 50), (BA.replace("456", "458"), 50)))
        result = resolve(history, min_production_days=1)
        levels = result["auto_selection"]["shared_level_comparison"]
        self.assertTrue(levels[0]["eligible"])
        self.assertAlmostEqual(levels[0]["evidence_match_score_percent"], 2)
        self.assertTrue(levels[1]["selected"])
        self.assertAlmostEqual(levels[1]["evidence_match_score_percent"], 100 * 4 / 6)

    def test_all_ten_valid_series_required_on_each_common_shift(self):
        history = shared_advantage_history()
        history[0]["factors"]["fe"] = None
        result = resolve(history)
        self.assertFalse(result["auto_selection"]["best_by_approach"]["shared_history"]["eligible"])
        previous = component_only(history)
        self.assertEqual(result["confidence_percent"], previous["confidence_percent"])
        for current, old in zip(result["records"], previous["records"]):
            for field in ("blend_factors", "regression_factors", "source_history", "resolution_level"):
                self.assertEqual(current[field], old[field])

    def test_incomplete_shift_is_excluded_from_every_shared_series(self):
        history = shared_advantage_history()
        history += period("2026-08-21 06:00", blocks=MIXTURE, blend=9)
        history[-1]["factors"]["mn"] = None
        result = resolve(history)
        self.assertEqual(result["auto_selection"]["history_approach"], "shared_history")
        for record in result["records"]:
            self.assertEqual(len(record["source_history"]), 2)
            self.assertAlmostEqual(record["blend_factors"]["fe"], (11000 + 1.4) / 10001)

    def test_local_minimum_and_maximum_bound_the_whole_common_set(self):
        for config in ({"min_production_days": 3}, {"max_lookback_days": 2}):
            cells = [local(cell=spatial_cell(BA), analyte="mn", window=config)]
            result = resolve(cells=cells)
            self.assertEqual(result["auto_selection"]["history_approach"], "component_based")
            self.assertFalse(result["auto_selection"]["best_by_approach"]["shared_history"]["eligible"])
        cells = [local(cell=spatial_cell(BA), analyte="mn", window={"min_production_days": 2, "max_lookback_days": 3})]
        result = resolve(cells=cells, min_production_days=1)
        common = result["auto_selection"]["best_by_approach"]["shared_history"]
        self.assertTrue(common["eligible"])
        self.assertAlmostEqual(common["confidence_percent"], (1000000 + 40) / 10001)

    def test_shared_production_dates_may_be_nonconsecutive(self):
        history = shared_advantage_history()
        history[-2:] = period("2026-08-21 06:00", feed=1, blocks=composition((HG, .2), (BA, .2), (REMOTE, .6)))
        result = resolve(history)
        self.assertEqual(result["auto_selection"]["history_approach"], "shared_history")
        self.assertEqual({s["production_day"] for s in result["records"][0]["source_history"]}, {"2026-08-19", "2026-08-21"})
        campaign = result["auto_selection"]["best_by_family"]["latest_campaign"]
        self.assertEqual(campaign["confidence_percent"], 0)

    def test_manual_factors_apply_after_selection_and_unknown_mass_stays_global(self):
        partial = composition((HG, 45), (BA, 45))
        original = resolve(blocks=partial)
        result = resolve(blocks=partial, cells=[local(cell=spatial_cell(HG), blend=2)])
        self.assertEqual(result["auto_selection"], original["auto_selection"])
        self.assertEqual(result["auto_selection"]["history_approach"], "shared_history")
        self.assertAlmostEqual(result["confidence_percent"], .9 * (1000000 + 40) / 10001)
        records = {r["grade_block_key"]: r for r in result["records"]}
        self.assertEqual(records[HG]["blend_factors"]["fe"], 2)
        self.assertNotEqual(records[BA]["blend_factors"]["fe"], 2)
        unknown = result["records"][-1]
        self.assertEqual(unknown["resolution_level"], "global")
        self.assertEqual(unknown["confidence_percent"], 0)
        self.assertEqual(unknown["blend_factors"]["fe"], 1.07)

    def test_cache_is_source_specific_and_saved_audit_is_detached(self):
        engine = auto(shared_advantage_history(), min_production_days=2)
        first = engine.resolve_source("A", "inventory", MIXTURE, 100)
        engine.resolve_source("B", "inventory", composition((HG, 99), (BA, 1)), 100)
        again = engine.resolve_source("A", "inventory", MIXTURE, 100)
        self.assertEqual(first, again)
        saved = json.loads(json.dumps(first))
        self.assertEqual(saved, first)
        first["records"][0]["provenance"]["shared_history"]["production_days"] = 999
        self.assertEqual(engine.resolve_source("A", "inventory", MIXTURE, 100), saved)

    def test_no_history_no_lineage_and_zero_mass_have_no_shared_selection(self):
        for history, blocks, total, status in (([], MIXTURE, 100, "global_fallback"),
                                               (shared_advantage_history(), [], 100, "no_lineage"),
                                               (shared_advantage_history(), [], 0, "zero_mass")):
            result = auto(history).resolve_source("S", "inventory", blocks, total)
            self.assertEqual(result["auto_selection"]["status"], status)
            self.assertNotIn("shared_history", result["auto_selection"])
            self.assertEqual(result["confidence_percent"], 0 if total else None)

    def test_random_histories_match_exhaustive_raw_shift_oracle_and_never_reduce_auto_score(self):
        rng = random.Random(907)
        for case in range(12):
            history = []
            for day in range(16, 22):
                for hour in (6, 18):
                    h, b, extra = rng.randrange(4), rng.randrange(4), rng.randrange(4)
                    if h + b + extra == 0:
                        h = 1
                    feed = rng.choice([1, 100, 10000])
                    offset = rng.choice(["456", "457", "458"])
                    blocks = composition(*[(k.replace("456", offset), feed * value / (h + b + extra))
                                           for k, value in ((HG, h), (BA, b), (REMOTE, extra)) if value])
                    pair = period(f"2026-08-{day} {hour:02}:00", feed=feed, blocks=blocks, blend=rng.uniform(.7, 1.5))
                    if rng.random() < .2:
                        pair[rng.randrange(2)]["factors"][rng.choice(SCHEMA_ANALYTES)] = None
                    history += pair
            oracle = independent_shared_oracle(history, MIXTURE, 2, 7)
            previous = component_only(history, max_lookback_days=7)
            actual = resolve(history, max_lookback_days=7)
            self.assertAlmostEqual(actual["confidence_percent"], max(previous["confidence_percent"], oracle or 0), places=9, msg=f"case {case}")
            shared = actual["auto_selection"]["best_by_approach"]["shared_history"]
            self.assertEqual(shared["eligible"], oracle is not None)
            if oracle is not None:
                self.assertAlmostEqual(shared["confidence_percent"], oracle, places=9, msg=f"case {case}")


class SharedHistoryPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_applied_grades_aggregate_csv_and_database_keep_history_approach(self):
        values, shared = apply(application(samples=shared_advantage_history(), settings={"method": AUTO, "min_production_days": 2}), blocks=MIXTURE)
        _, component = apply(application(samples=period(blocks=MIXTURE), settings={"method": AUTO}), blocks=MIXTURE)
        self.assertAlmostEqual(values["adjusted_rom"]["SF"]["fe"], 50 * (11000 + 1.4) / 10001)
        chunk = aggregate_reconciliation([(shared, 100)])
        overall = aggregate_reconciliation([(chunk, 100), (component, 300)], source_kind="overall")
        self.assertEqual(reconciliation_columns(chunk)["recon_sf_history_selection"], "Shared history")
        self.assertEqual(reconciliation_columns(overall)["recon_sf_history_selection"], "Component-based; Shared history")
        self.assertEqual(reconciliation_report_rows([shared], overall)[1]["recon_sf_history_selection"], "Shared history")
        view = window()
        view.selected_data_stream = "adjusted_rom"
        row = view.database_view_record_with_streams({**reconciliation_columns(overall), "source_type": "AMT Chunk"}, streams())
        self.assertEqual(row["recon_sf_history_selection"], "Component-based; Shared history")
        self.assertEqual(json.loads(json.dumps(overall)), overall)

    def test_review_explains_winner_and_old_audits_remain_readable(self):
        _, audit = apply(application(samples=shared_advantage_history(), settings={"method": AUTO, "min_production_days": 2}), blocks=MIXTURE)
        panel = ReconciliationReview()
        self.addCleanup(panel.close)
        panel.set_context({"method": AUTO}, "CB OPF", ["SF"])
        panel.set_review([audit], aggregate_reconciliation([(audit, 100)]))
        source = panel.sources.topLevelItem(0)
        self.assertEqual(source.text(8), "Shared history")
        panel.sources.setCurrentItem(source.child(0))
        text = panel.evidence.toPlainText()
        for phrase in ("Best Component-based", "Best Shared history", "Shared set: 2 shifts over 2 production dates", "not pooled before scoring", "Shared level comparison"):
            self.assertIn(phrase, text)
        self.assertFalse(panel.sources.isColumnHidden(8))
        old = copy.deepcopy(audit)
        old["by_brand"]["SF"]["auto_selection"] = {"status": "selected", "selected_method": "spatial_compositional", "window_days": 30}
        panel.set_review([old], aggregate_reconciliation([(old, 100)]))
        self.assertEqual(panel.sources.topLevelItem(0).text(8), "Component-based")
        panel.set_context({"method": "spatial_compositional"}, "CB OPF", ["SF"])
        self.assertTrue(panel.sources.isColumnHidden(8))


if __name__ == "__main__":
    unittest.main()
