"""Auto must compare levels even when a more specific level has enough history."""

import copy
import itertools
import math
import random
import unittest
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication

from GUI.ReconciliationReview import ReconciliationReview
from classes.PhaseSchemas import FACTOR_LEVELS
from classes.ReconciliationApplication import aggregate_reconciliation
from classes.ReconciliationConfidenceSearch import ConfidenceSearch
from classes.ReconciliationControls import reconciliation_columns
from classes.ReconciliationFactorResolver import _distributions, _similarity
from tests.test_reconciliation_application import application, apply
from tests.test_reconciliation_confidence_search import AUTO, auto, fixed_policy
from tests.test_reconciliation_controls import local
from tests.test_reconciliation_factor_resolver import GB, REMOTE, composition, period, resolver


NEIGHBOURS = [
    "PIT01|1|453|426|457|LG02",
    "PIT01|1|453|999|457|LG02",
    "PIT01|1|999|999|457|LG02",
    "PIT01|2|999|999|457|LG02",
]


def broader_history(neighbour=NEIGHBOURS[0]):
    # The weak fine-level evidence is newer. Every window reaching the better
    # broad evidence therefore also contains a sufficient fine-level match.
    return (period("2026-08-20 06:00", block=neighbour, blend=1.5, regression=1.2)
            + period("2026-08-21 06:00", blocks=composition((GB, 1), (REMOTE, 99)), blend=1.1))


def replay_levels(history, family, n, blocks, *, cells=()):
    """Oracle: replay ordinary resolution with one spatial index enabled at a time.

    This does not invoke Auto's level evaluation or ranking. The full source
    composition remains the scoring reference for every component/level.
    """
    engine = fixed_policy(history, family, n, cells=cells)
    index = engine._index
    components = []
    for block in blocks:
        candidates = []
        for depth in range(5):
            engine._index = {key: rows for key, rows in index.items() if key[0] == depth}
            engine._selection_cache.clear()
            candidates.append(engine.resolve(block["grade_block_key"], source_id="S", source_kind="amt",
                                             source_composition=blocks, source_wmt=100))
        components.append(candidates)
    return components


class LevelSearchTests(unittest.TestCase):
    def test_each_broader_level_can_beat_sufficient_fine_history_without_a_score_penalty(self):
        for depth, neighbour in enumerate(NEIGHBOURS, 1):
            with self.subTest(depth=depth):
                history = broader_history(neighbour)
                baseline = resolver(history).resolve(GB)
                chosen = auto(history).resolve(GB)
                self.assertEqual(baseline["resolution_level"], FACTOR_LEVELS[0])
                self.assertAlmostEqual(baseline["confidence_percent"], 1)
                self.assertEqual(chosen["resolution_level"], FACTOR_LEVELS[depth])
                # Exact and finer overlaps are zero, with no extra depth deduction.
                expected = (5 - depth) * 100 / 6
                self.assertAlmostEqual(chosen["confidence_percent"], expected)
                self.assertAlmostEqual(chosen["blend_factors"]["fe"], 1.5)
                self.assertAlmostEqual(chosen["regression_factors"]["fe"], 1.2)
                search = chosen["provenance"]["auto_selection"]
                self.assertEqual(search["search_version"], 3)
                self.assertAlmostEqual(search["baseline_confidence_percent"], 1)
                self.assertAlmostEqual(search["improvement_percent"], expected - 1)
                levels = chosen["provenance"]["level_search"]["candidates"]
                self.assertEqual(len(levels), 5)
                self.assertTrue(all(item["eligible"] for item in levels))
                self.assertEqual([i for i, item in enumerate(levels) if item["selected"]], [depth])

    def test_ordinary_spatial_and_lookback_keep_first_sufficient_level(self):
        for method in ("spatial_compositional", "lookback"):
            result = resolver(broader_history(), method=method).resolve(GB)
            self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
            self.assertAlmostEqual(result["confidence_percent"], 1)
            self.assertNotIn("level_search", result["provenance"])

    def test_lookback_candidates_also_compare_every_level_and_keep_all_window_evidence(self):
        engine = auto(broader_history())
        weights = {GB: 100}
        distributions = _distributions(weights, 100)
        scores = {i: _similarity(distributions, p["distributions"]) for i, p in enumerate(engine.periods)}
        for family in ("calendar_days", "production_days", "latest_campaign"):
            result = ConfidenceSearch(engine).evaluate(weights, 100, (family, 2), scores)
            selection = result["selections"][GB]
            self.assertEqual(selection["depth"], 1)
            self.assertEqual(len(selection["indices"]), 2)
            self.assertAlmostEqual(result["confidence_percent"], (1 + 100 * 4 / 6) / 2)
            self.assertAlmostEqual(selection["factors"]["blend"]["fe"], 1.3)

    def test_score_ties_prefer_finer_level_even_with_more_days_and_feed_at_broader_level(self):
        # Give both shifts an equal diagnostic score while retaining different
        # factor values and feed amounts to expose a hidden factor/feed preference.
        history = broader_history()
        engine = auto(history)
        result = ConfidenceSearch(engine).evaluate({GB: 100}, 100, ("spatial_compositional", 30), {0: 50, 1: 50})
        self.assertEqual(result["selections"][GB]["depth"], 0)
        self.assertEqual(result["confidence_percent"], 50)
        self.assertEqual(result["selections"][GB]["factors"]["blend"]["fe"], 1.1)

    def test_shared_ten_series_rule_prevents_cherry_picking_broader_valid_analytes(self):
        history = broader_history()
        history[1]["factors"]["mn"] = None  # Only regression/Mn loses the stronger shift.
        result = auto(history).resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
        self.assertAlmostEqual(result["confidence_percent"], 1)
        self.assertTrue(all(v["production_days"] >= 1 for k in result["provenance"]["factor_history"].values() for v in k.values()))

    def test_local_minimum_and_maximum_apply_at_every_level_and_global_is_terminal(self):
        for config in ({"max_lookback_days": 1}, {"min_production_days": 3}):
            result = auto(broader_history(), cells=[local(window=config)]).resolve(GB)
            if "max_lookback_days" in config:
                self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
                self.assertAlmostEqual(result["confidence_percent"], 1)
            else:
                self.assertEqual(result["resolution_level"], "global")
                self.assertEqual(result["confidence_percent"], 0)
                self.assertEqual(result["blend_factors"]["fe"], 1.07)
                candidates = result["provenance"]["level_search"]["candidates"]
                self.assertEqual(len(candidates), 5)
                self.assertTrue(all(not c["eligible"] for c in candidates))

    def test_source_components_choose_levels_independently_under_one_window_policy(self):
        history = broader_history()
        history[0]["contributing_blocks"] = composition((NEIGHBOURS[0], 90), (REMOTE, 10))
        history[1]["contributing_blocks"] = copy.deepcopy(history[0]["contributing_blocks"])
        result = auto(history).resolve_source("S", "amt", composition((GB, 90), (REMOTE, 10)), 100)
        self.assertEqual([r["resolution_level"] for r in result["records"]], [FACTOR_LEVELS[1], FACTOR_LEVELS[0]])
        self.assertEqual(len({(r["provenance"]["auto_selection"]["selected_method"],
                              r["provenance"]["auto_selection"]["window_days"]) for r in result["records"]}), 1)

    def test_level_and_source_caches_do_not_reuse_another_selection(self):
        engine = auto(broader_history())
        a = engine.resolve_source("A", "amt", composition((GB, 100)), 100)
        b = engine.resolve_source("B", "amt", composition((GB, 1), (REMOTE, 99)), 100)
        self.assertEqual(a["records"][0]["resolution_level"], FACTOR_LEVELS[1])
        self.assertEqual(b["records"][0]["resolution_level"], FACTOR_LEVELS[0])
        self.assertEqual(a, engine.resolve_source("A", "amt", composition((GB, 100)), 100))
        self.assertEqual(engine._selection(GB, method="lookback")["depth"], 0)

    def test_manual_override_and_unknown_mass_do_not_inflate_match_or_change_level(self):
        engine = auto(broader_history(), cells=[local(blend=2)])
        result = engine.resolve_source("partial", "amt", composition((GB, 60)), 100)
        self.assertAlmostEqual(result["confidence_percent"], 0.6 * 100 * 4 / 6)
        self.assertEqual(result["records"][0]["blend_factors"]["fe"], 2)
        self.assertEqual(result["records"][0]["resolution_level"], FACTOR_LEVELS[1])
        self.assertEqual(result["records"][-1]["resolution_level"], "global")

    def test_exhaustive_window_and_component_level_combinations_match_auto(self):
        rng = random.Random(9206)
        blocks = composition((GB, 70), (REMOTE, 20))  # 10% unknown remains unscored.
        for case in range(6):
            history = []
            for day in range(16, 22):
                feed, fraction = rng.randint(10, 500), rng.uniform(.01, .99)
                block = rng.choice([GB, *NEIGHBOURS])
                history += period(f"2026-08-{day} 06:00", feed=feed,
                                  blocks=composition((block, feed * fraction), (REMOTE, feed * (1 - fraction))),
                                  blend=rng.uniform(.8, 1.4), regression=rng.uniform(.8, 1.4))
            history[rng.randrange(len(history))]["factors"]["mn"] = None
            cells = [local(window={"min_production_days": 2, "max_lookback_days": 4})] if case % 2 else []
            expected = 0
            for family in ("spatial_compositional", "calendar_days", "production_days", "latest_campaign"):
                for n in range(1, 8):
                    candidates = replay_levels(history, family, n, blocks, cells=cells)
                    expected = max(expected, max(math.fsum(r["lineage_fraction"] * r["confidence_percent"] for r in combo)
                                                 for combo in itertools.product(*candidates)))
            actual = auto(history, max_lookback_days=7, cells=cells).resolve_source("S", "amt", blocks, 100)
            self.assertAlmostEqual(actual["confidence_percent"], expected, places=10, msg=f"case {case}")
            choice = actual["auto_selection"]
            replay = replay_levels(history, choice["window_mode"] or choice["selected_method"], choice["window_days"], blocks, cells=cells)
            for chosen, candidates in zip(actual["records"], replay):
                matching = next(r for r in candidates if r["resolution_level"] == chosen["resolution_level"])
                self.assertEqual(chosen["blend_factors"], matching["blend_factors"])
                self.assertEqual(chosen["regression_factors"], matching["regression_factors"])


class LevelSearchPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_review_explains_eligible_losing_levels_and_reports_preserve_gain(self):
        _, audit = apply(application(samples=broader_history(), settings={"method": AUTO}), blocks=composition((GB, 100)))
        chunk = aggregate_reconciliation([(audit, 100)], source_kind="amt_chunk")
        overall = aggregate_reconciliation([(chunk, 100)], source_kind="overall")
        columns = reconciliation_columns(overall)
        self.assertAlmostEqual(columns["recon_sf_evidence_match_score_gain_pp"], 100 * 4 / 6 - 1)
        self.assertIn(FACTOR_LEVELS[1], columns["recon_sf_fallback_levels"])
        panel = ReconciliationReview()
        self.addCleanup(panel.close)
        panel.set_context({"method": AUTO}, "CB OPF", ["SF"])
        panel.set_review([audit], overall)
        panel.sources.setCurrentItem(panel.sources.topLevelItem(0).child(0))
        text = panel.evidence.toPlainText()
        self.assertIn("Flitch + material: 1.0% · eligible", text)
        self.assertIn("Blast + material: 66.7% · selected", text)
        self.assertIn("All five spatial levels compete on the same score", text)
        self.assertIn("all five spatial fallback levels", panel.help.text())

