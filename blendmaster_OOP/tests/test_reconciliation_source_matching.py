"""Source-directed shift selection, method comparisons and legacy presentation."""

import copy
import json
import math
import random
import unittest
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication

from classes.ReconciliationApplication import aggregate_reconciliation
from classes.ReconciliationControls import evidence_display_record, reconciliation_columns, reconciliation_report_rows
from GUI.ReconciliationReview import ReconciliationReview
from tests.test_reconciliation_factor_resolver import GB, REMOTE, composition, period, resolver
from tests.test_reconciliation_application import application, apply, window, streams
from tests.test_reconciliation_controls import local


def calendar_advantage_history():
    return (period("2026-08-19 06:00", feed=10000, blocks=composition((GB, 8000), (REMOTE, 2000)), blend=1.1)
            + period("2026-08-20 00:00", feed=10000, blocks=composition((GB, 10000)), blend=1.4)
            + period("2026-08-21 06:00", feed=1, blocks=composition((GB, .6), (REMOTE, .4)), blend=1.2))


class SourceMatchingTests(unittest.TestCase):
    def test_spatial_selects_source_context_while_lookback_keeps_all_matching_periods(self):
        history = (period(blocks=composition((GB, 50), (REMOTE, 50)), blend=1.1)
                   + period("2026-08-21 06:00", blocks=composition((GB, 90), (REMOTE, 10)), blend=1.4))
        engine = resolver(history)
        pure = engine.resolve(GB)
        mixed = engine.resolve(GB, source_composition=composition((GB, 50), (REMOTE, 50)), source_wmt=100)
        fixed = resolver(history, method="lookback", lookback_days=7).resolve(GB)
        self.assertEqual(pure["blend_factors"]["fe"], 1.4)
        self.assertEqual(mixed["blend_factors"]["fe"], 1.1)
        self.assertAlmostEqual(fixed["blend_factors"]["fe"], 1.25)
        self.assertEqual(len(fixed["source_history"]), 2)
        self.assertEqual(engine.resolve(GB), pure)  # Cache cannot leak the mixed source's selection.
        self.assertEqual(pure["provenance"]["spatial_selection"]["excluded_period_count"], 1)

    def test_minimum_dates_can_be_nonconsecutive_and_all_score_ties_are_retained(self):
        history = []
        for day in range(17, 22):
            share = 100 if day % 2 else 20
            history += period(f"2026-08-{day} 06:00", blocks=composition((GB, share), (REMOTE, 100 - share)))
        result = resolver(history, min_production_days=2).resolve(GB)
        self.assertEqual([p["production_day"] for p in result["source_history"]],
                         ["2026-08-17", "2026-08-19", "2026-08-21"])
        self.assertEqual(result["provenance"]["factor_history"]["blend"]["fe"]["production_days"], 3)
        self.assertEqual(result, resolver(list(reversed(history)), min_production_days=2).resolve(GB))

    def test_one_cutoff_supports_both_kinds_all_analytes_and_local_minimum(self):
        history = period("2026-08-19 06:00", blocks=composition((GB, 20), (REMOTE, 80)))
        history += period(blocks=composition((GB, 80), (REMOTE, 20)))
        history += period("2026-08-21 06:00")
        history[-2]["factors"]["fe"] = None
        result = resolver(history).resolve(GB)
        self.assertEqual(result["provenance"]["spatial_selection"]["cutoff_evidence_match_score_percent"], 80)
        self.assertEqual(len(result["source_history"]), 2)
        guarded = resolver(history, cells=[local(window={"min_production_days": 2})]).resolve(GB)
        self.assertEqual(guarded["provenance"]["spatial_selection"]["cutoff_evidence_match_score_percent"], 20)
        self.assertEqual(guarded["provenance"]["factor_history"]["blend"]["fe"]["production_days"], 2)
        self.assertEqual(len(guarded["source_history"]), 3)

    def test_calendar_can_beat_ranked_spatial_and_auto_selects_it(self):
        history = calendar_advantage_history()
        spatial = resolver(history, min_production_days=2).resolve(GB)
        calendar = resolver(history, method="lookback", lookback_days=2, min_production_days=2).resolve(GB)
        best = resolver(history, method="auto_max_confidence", min_production_days=2).resolve(GB)
        self.assertAlmostEqual(spatial["confidence_percent"], 90)
        self.assertAlmostEqual(calendar["confidence_percent"], (10000 * 100 + 60) / 10001)
        self.assertEqual(best["blend_factors"], calendar["blend_factors"])
        choice = best["provenance"]["auto_selection"]
        self.assertEqual((choice["selected_method"], choice["window_mode"], choice["window_days"]), ("lookback", "calendar_days", 2))
        self.assertAlmostEqual(choice["improvement_percent"], calendar["confidence_percent"] - 90)

    def test_auto_compares_spatial_and_lookback_even_when_time_membership_is_identical(self):
        history = period(blocks=composition((GB, 80), (REMOTE, 20)))
        history += period("2026-08-21 06:00", blocks=composition((GB, 20), (REMOTE, 80)))
        # Calendar/production families have their own all-period selections,
        # even though both families can see exactly the same two shifts.
        result = resolver(history, method="auto_max_confidence").resolve(GB)
        self.assertAlmostEqual(result["confidence_percent"], 80)
        calendar = result["provenance"]["auto_selection"]["best_by_family"]["calendar_days"]
        self.assertEqual(calendar["window_days"], 2)
        self.assertAlmostEqual(calendar["confidence_percent"], 50)

    def test_ranked_selection_matches_independent_shift_prefix_oracle(self):
        rng = random.Random(905)
        for case in range(12):
            history, shifts = [], []
            for day in range(14, 22):
                share, feed = rng.choice([20, 50, 80, 100]), rng.randint(20, 400)
                pair = period(f"2026-08-{day} 06:00", feed=feed,
                              blocks=composition((GB, share * feed / 100), (REMOTE, (100 - share) * feed / 100)),
                              blend=rng.uniform(.8, 1.4), regression=rng.uniform(.8, 1.2))
                if day % 3 == 0:
                    pair[0]["factors"]["fe"] = None
                if day % 4 == 0:
                    pair[1]["factors"]["mn"] = None
                history += pair
                shifts.append((share, feed, pair))
            chosen = None
            for cutoff in sorted({s[0] for s in shifts}, reverse=True):
                chosen = [s for s in shifts if s[0] >= cutoff]
                if all(sum(s[2][k]["factors"][a] is not None for s in chosen) >= 3
                       for k in (0, 1) for a in ("fe", "si", "al", "p", "mn")):
                    break
            result = resolver(history, min_production_days=3).resolve(GB)
            self.assertEqual(result["provenance"]["spatial_selection"]["cutoff_evidence_match_score_percent"], cutoff, case)
            for k, kind in enumerate(("blend", "regression")):
                for a in ("fe", "si", "al", "p", "mn"):
                    valid = [s for s in chosen if s[2][k]["factors"][a] is not None]
                    expected = math.fsum(s[1] * s[2][k]["factors"][a] for s in valid) / math.fsum(s[1] for s in valid)
                    self.assertAlmostEqual(result[kind + "_factors"][a], expected, msg=f"{case}/{kind}/{a}")

    def test_auto_gain_survives_mixed_fixed_source_and_chunk_aggregation(self):
        _, auto = apply(application(samples=calendar_advantage_history(), settings={"method": "auto_max_confidence", "min_production_days": 2}),
                        blocks=composition((GB, 100)))
        _, fixed = apply(blocks=composition((GB, 100)))
        chunk = aggregate_reconciliation([(auto, 100)])
        overall = aggregate_reconciliation([(chunk, 100), (fixed, 300)], source_kind="overall")
        source_gain = auto["by_brand"]["SF"]["auto_selection"]["improvement_percent"]
        self.assertGreater(source_gain, 9)
        self.assertAlmostEqual(reconciliation_columns(overall)["recon_sf_evidence_match_score_gain_pp"], source_gain / 4)
        self.assertEqual(overall, json.loads(json.dumps(overall)))

    def test_algorithm_revision_invalidates_applied_cache_without_refetching_history(self):
        view = window()
        first = view.AMT_enrichment_request_signature()
        before = view.reconciliation_application()
        with patch("GUI.InitialiseGUI.RECONCILIATION_ALGORITHM_VERSION", 3):
            self.assertNotEqual(view.AMT_enrichment_request_signature(), first)
            self.assertIsNot(view.reconciliation_application(), before)


class EvidencePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_audit_displays_new_terminology_without_mutating_saved_data(self):
        _, audit = apply(application(settings={"method": "auto_max_confidence", "cells": [local(blend=1.5)]}))
        record = audit["by_brand"]["SF"]["records"][0]
        record["provenance"]["local_override"]["confidence_note"] = "Evidence confidence is unchanged by manual factor edits."
        old = copy.deepcopy(audit)
        panel = ReconciliationReview()
        self.addCleanup(panel.close)
        panel.set_context({"method": "auto_max_confidence"}, "CB OPF", ["SF"])
        overall = aggregate_reconciliation([(audit, 100)])
        panel.set_review([audit], overall)
        panel.sources.setCurrentItem(panel.sources.topLevelItem(0).child(0))
        headers = [panel.sources.headerItem().text(i) for i in range(panel.sources.columnCount())]
        text = " ".join([*headers, panel.summary.text(), panel.evidence.toPlainText(), panel.method.currentText(), panel.help.text()]).lower()
        self.assertIn("evidence match score", text)
        self.assertNotIn("confidence", text)
        self.assertNotIn("uncertainty", text)
        self.assertEqual(panel.sources.columnCount(), 8)
        self.assertTrue(panel.sources.topLevelItem(0).text(7).startswith("Spatial"))
        rows = reconciliation_report_rows([audit], overall)
        self.assertFalse(any("confidence" in k or "uncertainty" in k for row in rows for k in row))
        self.assertNotIn("confidence", rows[1]["method"])
        self.assertEqual(audit, old)

    def test_database_view_migrates_legacy_fields_and_saved_column_choices(self):
        old = {"source_type": "AMT Chunk", "recon_sf_confidence_pct": 42, "recon_sf_uncertainty_pct": 58,
               "recon_sf_baseline_confidence_pct": 30, "recon_sf_confidence_gain_pp": 12}
        view = window()
        view.selected_data_stream = "adjusted_rom"
        row = view.database_view_record_with_streams(old, streams())
        self.assertEqual(row["recon_sf_evidence_match_score_pct"], 42)
        self.assertFalse(any("uncertainty" in k or "confidence" in k for k in row))
        view.database_view_rows = [row]
        view.database_view_show_coverage_fields = False
        view.database_view_selected_columns = list(old)
        view.database_view_known_columns = list(old)
        headers = view.database_view_headers()
        self.assertIn("recon_sf_evidence_match_score_pct", headers)
        self.assertIn("recon_sf_baseline_evidence_match_score_pct", headers)
        self.assertFalse(any("uncertainty" in k or "confidence" in k for k in headers))
        self.assertEqual(evidence_display_record({**old, "recon_sf_evidence_match_score_pct": 80})["recon_sf_evidence_match_score_pct"], 80)


if __name__ == "__main__":
    unittest.main()
