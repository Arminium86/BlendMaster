import copy
import csv
import json
import os
import random
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from GUI.ReconciliationReview import ReconciliationReview
from GUI.InitialiseGUI import UserInputs
from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel
from PyQt5.QtCore import Qt, QThread
from PyQt5.QtTest import QTest

from classes.PhaseSchemas import FACTOR_LEVELS
from classes.ReconciliationApplication import aggregate_reconciliation, reconciliation_fingerprint
from classes.ReconciliationControls import normalise_reconciliation_settings, reconciliation_columns
from tests.test_reconciliation_controls import local
from tests.test_reconciliation_factor_resolver import GB, REMOTE, AS_OF, composition, period, resolver
from tests.test_reconciliation_application import application, apply, raw_hex, window, chart


AUTO = "auto_max_confidence"


def auto(history=None, **kwargs):
    return resolver(history, method=AUTO, **kwargs)


def fixed_policy(history, family, n, *, maximum=7, minimum=1, cells=(), **kwargs):
    """Independent replay through the ordinary resolver, without search helpers."""
    settings = copy.deepcopy(list(cells))
    for row in settings:
        window = row.setdefault("window", {})
        if family == "spatial_compositional":
            window["max_lookback_days"] = min(window.get("max_lookback_days", maximum), n)
        else:
            window.update(window_mode=family, lookback_days=n)
    return resolver(history, method="spatial_compositional" if family == "spatial_compositional" else "lookback",
                    max_lookback_days=min(maximum, n) if family == "spatial_compositional" else maximum,
                    min_production_days=minimum, lookback_days=n,
                    window_mode="calendar_days" if family == "spatial_compositional" else family,
                    cells=settings, **kwargs)


class ConfidenceSearchTests(unittest.TestCase):
    def history(self):
        return (period(blocks=composition((GB, 50), (REMOTE, 50)), blend=1.1, regression=.9) +
                period("2026-08-21 06:00", blocks=composition((GB, 90), (REMOTE, 10)), blend=1.4, regression=1.2))

    def test_different_sources_choose_different_historical_contexts(self):
        engine = auto(self.history())
        a = engine.resolve_source("A", "amt", composition((GB, 100)), 100)
        b = engine.resolve_source("B", "amt", composition((REMOTE, 100)), 100)
        self.assertAlmostEqual(a["confidence_percent"], 90)
        self.assertAlmostEqual(a["auto_selection"]["baseline_confidence_percent"], 90)
        self.assertAlmostEqual(a["auto_selection"]["improvement_percent"], 0)
        self.assertEqual(a["auto_selection"]["window_days"], 30)
        self.assertEqual(a["records"][0]["blend_factors"]["fe"], 1.4)
        self.assertAlmostEqual(b["confidence_percent"], 50)
        self.assertEqual(b["auto_selection"]["window_days"], 30)
        self.assertAlmostEqual(b["records"][0]["blend_factors"]["fe"], 1.1)

    def test_whole_source_policy_is_not_selected_independently_per_component(self):
        result = auto(self.history()).resolve_source("mix", "inventory", composition((GB, 40), (REMOTE, 60)), 100)
        policies = {(r["provenance"]["auto_selection"]["selected_method"], r["provenance"]["auto_selection"]["window_days"])
                    for r in result["records"]}
        self.assertEqual(len(policies), 1)
        self.assertAlmostEqual(result["confidence_percent"], sum(r["lineage_fraction"] * r["confidence_percent"] for r in result["records"]))

    def test_spatial_excludes_less_relevant_completed_feed_on_current_date(self):
        history = period("2026-08-21 06:00", blocks=composition((GB, 100)), blend=1.1)
        history += period("2026-08-22 06:00", blocks=composition((GB, 10), (REMOTE, 90)), blend=1.5)
        result = auto(history, scenario_start="2026-08-22 18:00").resolve(GB)
        choice = result["provenance"]["auto_selection"]
        self.assertEqual(choice["selected_method"], "spatial_compositional")
        self.assertIsNone(choice["window_mode"])
        self.assertEqual(result["confidence_percent"], 100)
        self.assertEqual(result["blend_factors"]["fe"], 1.1)

    def test_brute_force_oracle_over_all_integer_windows_with_local_guardrails(self):
        rng = random.Random(813)
        for case in range(10):
            history = []
            for day in (16, 18, 19, 20, 21):
                share = rng.randint(1, 99)
                feed = rng.randint(10, 500)
                history += period(f"2026-08-{day} 06:00", feed=feed,
                                  blocks=composition((GB, feed * share / 100), (REMOTE, feed * (100 - share) / 100)),
                                  blend=rng.uniform(.8, 1.3), regression=rng.uniform(.8, 1.2))
            # Different analyte eligibility forces the shared fallback checks.
            history[rng.randrange(len(history))]["factors"]["mn"] = None
            blocks = composition((GB, 33), (REMOTE, 57))  # 10% missing lineage remains in the objective.
            cells = [local(window={"min_production_days": 2, "max_lookback_days": 5})] if case % 2 else []
            actual = auto(history, max_lookback_days=7, cells=cells).resolve_source("S", "amt", blocks, 100)
            best = max(fixed_policy(history, family, n, cells=cells).resolve_source("S", "amt", blocks, 100)["confidence_percent"]
                       for family in ("spatial_compositional", "calendar_days", "production_days", "latest_campaign")
                       for n in range(1, 9))
            self.assertAlmostEqual(actual["confidence_percent"], best, places=10, msg=f"oracle case {case}")
            self.assertGreaterEqual(actual["confidence_percent"] + 1e-10, actual["auto_selection"]["baseline_confidence_percent"])
            chosen = actual["auto_selection"]
            family = chosen["window_mode"] or chosen["selected_method"]
            replay = fixed_policy(history, family, chosen["window_days"], cells=cells).resolve_source("S", "amt", blocks, 100)
            self.assertAlmostEqual(actual["confidence_percent"], replay["confidence_percent"], places=10)
            for left, right in zip(actual["records"], replay["records"]):
                self.assertEqual(left["blend_factors"], right["blend_factors"])
                self.assertEqual(left["regression_factors"], right["regression_factors"])
                self.assertEqual(left["resolution_level"], right["resolution_level"])

    def test_minimum_distinct_dates_and_shared_level_are_not_relaxed(self):
        history = period() + period("2026-08-20 18:00")
        result = auto(history, min_production_days=2).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")
        history += period("2026-08-21 06:00", block=GB.replace("|456|", "|457|"))
        result = auto(history, min_production_days=2).resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[1])
        self.assertTrue(all(v["production_days"] >= 2 for kind in result["provenance"]["factor_history"].values() for v in kind.values()))

    def test_boundaries_exclude_old_and_uncompleted_periods(self):
        history = period("2026-08-01 06:00", blend=9) + self.history() + period("2026-08-22 06:00", blend=8)
        result = auto(history, max_lookback_days=2).resolve(GB)
        self.assertTrue(all(h["period_start"] >= "2026-08-20T06:00:00" and h["period_end"] <= AS_OF.isoformat()
                            for h in result["source_history"]))
        self.assertEqual(result["blend_factors"]["fe"], 1.4)

    def test_local_maximum_can_extend_search_and_local_minimum_is_kept(self):
        history = period("2026-08-17 06:00", blend=1.2) + period(blend=1.4)
        for sample in history[-2:]:
            sample["factors"]["fe"] = None
        result = auto(history, max_lookback_days=3, cells=[local(window={"max_lookback_days": 6})]).resolve(GB)
        self.assertEqual(result["blend_factors"]["fe"], 1.2)
        self.assertEqual(result["blend_factors"]["si"], 1.4)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])

    def test_unknown_lineage_and_zero_mass_cannot_gain_confidence(self):
        engine = auto(self.history(), cells=[local(blend=2)])
        partial = engine.resolve_source("S", "amt", composition((GB, 60)), 100)
        self.assertAlmostEqual(partial["confidence_percent"], .6 * 90)
        self.assertEqual(partial["records"][-1]["blend_factors"]["fe"], 1.07)
        unknown = engine.resolve_source("U", "amt", [], 100)
        self.assertEqual(unknown["auto_selection"]["status"], "no_lineage")
        self.assertEqual(unknown["auto_selection"]["candidate_count"], 0)
        self.assertEqual(unknown["confidence_percent"], 0)
        zero = engine.resolve_source("Z", "amt", [], 0)
        self.assertIsNone(zero["confidence_percent"])
        self.assertEqual(zero["auto_selection"]["status"], "zero_mass")

    def test_unrelated_cells_wider_guardrail_does_not_change_this_sources_window(self):
        other = local(cell=REMOTE, window={"max_lookback_days": 180})
        result = auto(cells=[other]).resolve(GB)
        self.assertEqual(result["provenance"]["auto_selection"]["window_days"], 30)
        self.assertEqual(result["confidence_percent"], 100)

    def test_manual_factor_does_not_change_the_winner_or_evidence_confidence(self):
        before = auto(self.history()).resolve(GB)
        after = auto(self.history(), cells=[local(blend=2, regression=3)]).resolve(GB)
        self.assertEqual(before["provenance"]["auto_selection"], after["provenance"]["auto_selection"])
        self.assertEqual(after["blend_factors"]["fe"], 2)
        self.assertEqual(after["regression_factors"]["fe"], 3)
        self.assertEqual(after["provenance"]["local_override"]["factors"]["blend"]["fe"]["automatic"], 1.4)

    def test_equal_confidence_prefers_more_history_deterministically(self):
        history = period() + period("2026-08-21 06:00", blend=1.3)
        result = auto(history).resolve(GB)
        self.assertEqual(len(result["source_history"]), 2)
        self.assertEqual(result, auto(list(reversed(history))).resolve(GB))
        self.assertAlmostEqual(result["blend_factors"]["fe"], 1.2)

    def test_component_api_matches_source_and_results_are_detached_and_serialisable(self):
        engine = auto(self.history())
        blocks = composition((GB, 60), (REMOTE, 40))
        source = engine.resolve_source("S", "amt", blocks, 100, hex_id="H")
        component = engine.resolve(GB, source_id="S", source_kind="amt", hex_id="H", source_composition=blocks, source_wmt=100)
        self.assertEqual(component, source["records"][0])
        self.assertEqual(source, json.loads(json.dumps(source, allow_nan=False)))
        source["records"][0]["blend_factors"]["fe"] = 999
        source["auto_selection"]["window_days"] = 999
        self.assertEqual(engine.resolve_source("S", "amt", blocks, 100, hex_id="H")["records"][0], component)

    def test_similarity_computed_once_per_period_for_whole_source(self):
        from classes.ReconciliationFactorResolver import _similarity
        engine = auto(self.history())
        with patch("classes.ReconciliationFactorResolver._similarity", wraps=_similarity) as similarity:
            engine.resolve_source("S", "amt", composition((GB, 50), (REMOTE, 50)), 100)
            self.assertEqual(similarity.call_count, len(engine.periods))

    def test_empty_or_foreign_history_stays_global(self):
        for history in ([], period(opf="CC OPF01"), period(brand="WF")):
            result = auto(history).resolve(GB)
            self.assertEqual(result["blend_factors"]["fe"], 1.07)
            self.assertEqual(result["provenance"]["auto_selection"]["status"], "global_fallback")


class SearchApplicationTests(unittest.TestCase):
    def test_application_applies_selected_factors_and_nested_aggregation_keeps_baseline(self):
        history = ConfidenceSearchTests().history()
        app = application(samples=history, settings={"method": AUTO})
        adjusted, audit = apply(app, blocks=composition((GB, 100)))
        self.assertEqual(adjusted["adjusted_rom"]["SF"]["fe"], 70)
        self.assertEqual(adjusted["adjusted_product"]["SF"]["fe"], 72)
        chunk = aggregate_reconciliation([(audit, 100)], source_kind="amt_chunk")
        overall = aggregate_reconciliation([(chunk, 100)], source_kind="overall")
        columns = reconciliation_columns(overall)
        self.assertAlmostEqual(columns["recon_sf_baseline_evidence_match_score_pct"], 90)
        self.assertAlmostEqual(columns["recon_sf_evidence_match_score_gain_pp"], 0)
        self.assertIn("Spatial", columns["recon_sf_selected_window"])

    def test_settings_roundtrip_and_enrichment_reacts_to_auto_and_guardrail_changes(self):
        settings = normalise_reconciliation_settings({"method": AUTO, "cells": [local(window={"min_production_days": 2})]})
        self.assertEqual(normalise_reconciliation_settings(json.loads(json.dumps(settings))), settings)
        view = window()
        previous = view.AMT_enrichment_request_signature()
        view.reconciliation_settings = settings
        self.assertNotEqual(previous, view.AMT_enrichment_request_signature())
        view.reconciliation_settings["min_production_days"] = 2
        self.assertEqual(view.reconciliation_application().settings["method"], AUTO)

    def test_mixed_auto_and_fixed_aggregation_only_counts_actual_auto_gain(self):
        _, chosen = apply(application(samples=ConfidenceSearchTests().history(), settings={"method": AUTO}), blocks=composition((GB, 100)))
        _, fixed = apply(blocks=composition((GB, 100)))
        mixed = aggregate_reconciliation([(chosen, 100), (fixed, 100)])
        search = mixed["by_brand"]["SF"]["auto_selection"]
        self.assertAlmostEqual(search["improvement_percent"], 0)
        self.assertAlmostEqual(search["baseline_confidence_percent"], 95)

    def test_hex_enrichment_repeat_and_chunk_metadata_retain_auto_choice(self):
        view = window()
        view.reconciliation_settings = {"method": AUTO}
        view.reconciliation_inputs = {"samples": ConfidenceSearchTests().history()}
        rows = view.enrich_AMT_grade_streams({}, {"SP1": [raw_hex()]})
        repeated = view.enrich_AMT_grade_streams({}, rows)
        self.assertEqual(rows["SP1"][0]["grade_streams"], repeated["SP1"][0]["grade_streams"])
        self.assertEqual(rows["SP1"][0]["reconciliation"], repeated["SP1"][0]["reconciliation"])
        row = rows["SP1"][0]
        chunk = chart().build_chunk_row("SP1", 1, [{**row, **{f"grade_{a}": 50 for a in ("fe", "si", "al", "p", "mn")},
                                                  "hex": "H1", "footprint": "SP1", "balance": 100, "_positive_balance": 100}], 100)
        self.assertAlmostEqual(chunk["reconciliation"]["by_brand"]["SF"]["auto_selection"]["improvement_percent"], 0)
        self.assertIn("auto_selection", row["reconciliation"]["by_brand"]["SF"])


class AutoReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = ReconciliationReview()
        self.panel.set_context({"method": AUTO}, "CB OPF", ["SF"])
        self.panel.resize(1100, 900)
        self.panel.show()
        self.app.processEvents()
        self.addCleanup(self.panel.close)

    def view(self):
        view = window()
        view.reconciliation_settings = {"method": AUTO}
        view.reconciliation_inputs = {"samples": ConfidenceSearchTests().history()}
        view.reconciliation_review = self.panel
        view.data_streams_submit_button = QPushButton()
        view.data_stream_warning_label = QLabel()
        view.updated_stockpile_data = {"SP1": {"amt": True, "balance": 100}}
        view.AMT_stockpile_data = {"SP1": [raw_hex()]}
        view.hex_sequence_table = []
        view.run_background_task = Mock()
        return view

    def test_controls_have_auto_mode_and_only_guardrails_are_editable(self):
        self.assertEqual(self.panel.method.currentData(), AUTO)
        self.assertFalse(self.panel.days.isEnabled())
        self.assertFalse(self.panel.window.isEnabled())
        self.assertTrue(self.panel.maximum.isEnabled())
        self.assertTrue(self.panel.minimum.isEnabled())
        self.assertIn("N is chosen automatically", self.panel.help.text())

    def test_background_review_uses_frozen_sources_and_completes_on_callback(self):
        view = self.view()
        self.assertEqual(view.update_reconciliation_review(), [])
        _, work, success, _ = view.run_background_task.call_args.args
        self.assertFalse(view.data_streams_submit_button.isEnabled())
        self.assertFalse(self.panel.review_button.isEnabled())
        result = work()
        self.assertFalse(view.data_streams_submit_button.isEnabled())
        success(result)
        self.assertTrue(view.data_streams_submit_button.isEnabled())
        self.assertTrue(self.panel.export_button.isEnabled())
        self.assertIn("evidence match score 90.0%", self.panel.summary.text())
        self.assertEqual(view._reconciliation_review_signature, view.reconciliation_review_signature())
        self.assertIsNotNone(view._reconciliation_application_cache)

    def test_real_qt_worker_completes_review_on_the_ui_thread(self):
        view = self.view()
        QMainWindow.__init__(view)
        view.background_tasks = []
        view.show_progress_dialog = Mock()
        view.close_progress_dialog = Mock()
        view.show_error_popup = Mock()
        view.run_background_task = UserInputs.run_background_task.__get__(view)
        ui_threads = []
        original = self.panel.set_review
        def record_thread(*args):
            ui_threads.append(QThread.currentThread() == self.app.thread())
            original(*args)
        with patch.object(self.panel, "set_review", side_effect=record_thread):
            view.update_reconciliation_review()
            deadline = time.monotonic() + 5
            while view.background_tasks and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertEqual(view.background_tasks, [])
        self.assertEqual(ui_threads, [True])
        self.assertTrue(view.data_streams_submit_button.isEnabled())
        view.show_error_popup.assert_not_called()

    def test_stale_worker_cannot_restore_old_settings_or_enable_submit(self):
        view = self.view()
        view.update_reconciliation_review()
        _, work, success, _ = view.run_background_task.call_args.args
        view.reconciliation_settings["max_lookback_days"] = 1
        view.AMT_stockpile_data["SP1"][0]["FINAL_WMT"] = 0
        result = work()
        self.assertEqual(result[0][1]["source_wmt"], 100)
        success(result)
        self.assertFalse(view.data_streams_submit_button.isEnabled())
        self.assertEqual(view.reconciliation_settings["max_lookback_days"], 1)
        self.assertIn("Sources or settings changed", self.panel.status.text())

    def test_worker_error_keeps_submit_disabled_and_review_available(self):
        view = self.view()
        view.update_reconciliation_review()
        failure = view.run_background_task.call_args.args[3]
        failure("invalid paired history")
        self.assertFalse(view.data_streams_submit_button.isEnabled())
        self.assertTrue(self.panel.review_button.isEnabled())
        self.assertIn("invalid paired history", self.panel.status.text())

    def test_local_matrix_shows_selected_source_instead_of_overwriting_shared_cell(self):
        history = ConfidenceSearchTests().history()
        engine = application(samples=history, settings={"method": AUTO})
        _, a = apply(engine, blocks=composition((GB, 100)))
        _, b = apply(engine, blocks=composition((GB, 50), (REMOTE, 50)))
        b["hex_id"] = "H2"
        self.panel.set_review([a, b], aggregate_reconciliation([(a, 100), (b, 100)]))
        self.panel.sources.setCurrentItem(self.panel.sources.topLevelItem(0).child(0))
        QTest.mouseClick(self.panel.edit_component, Qt.LeftButton)
        self.assertIn("H1", self.panel.source_context.currentText())
        first = self.panel.matrix.item(0, 1).text()
        self.panel.sources.setCurrentItem(self.panel.sources.topLevelItem(1).child(0))
        self.panel.open_component()
        self.assertIn("H2", self.panel.source_context.currentText())
        self.assertNotEqual(first, self.panel.matrix.item(0, 1).text())
        self.assertIn("Compared", self.panel.evidence.toPlainText())

    def test_csv_exports_auto_selection_baseline_and_percentage_point_gain(self):
        view = self.view()
        audits, overall, warnings = view.calculate_reconciliation_review()
        self.panel.set_review(audits, overall, warnings)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "auto.csv")
            with patch("GUI.ReconciliationReview.QFileDialog.getSaveFileName", return_value=(path, "CSV")):
                self.panel.export_review()
            with open(path, encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
        self.assertEqual(rows[0]["method"], "Auto · maximise evidence match score")
        self.assertAlmostEqual(float(rows[0]["recon_sf_evidence_match_score_gain_pp"]), 0)
        self.assertIn("Spatial", rows[0]["recon_sf_selected_window"])

    def test_method_change_reuses_history_input_cache(self):
        view = self.view()
        view.mine_input_choice = "TEST"
        view.selected_site_crushers = []
        view.crusher_contribution_ratio_choice = 1
        view.planning_period_count = lambda: 2
        view.selected_planning_category = lambda: "OPF Feed"
        view.auto_load_2wp_targets_choice = False
        view.group_2wp_build_targets_by_brand_choice = False
        view.byproducts_enabled = False
        auto_signature = view.data_stream_input_request_signature()
        view.reconciliation_settings["method"] = "spatial_compositional"
        self.assertEqual(auto_signature, view.data_stream_input_request_signature())


if __name__ == "__main__":
    unittest.main()
