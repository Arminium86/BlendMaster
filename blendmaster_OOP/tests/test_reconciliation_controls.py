import copy
import csv
import json
import os
import pickle
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from GUI.InitialiseGUI import UserInputs
from GUI.ReconciliationReview import ReconciliationReview
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase, QFont
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton, QTableWidget

from classes.PhaseSchemas import FACTOR_LEVELS
from classes.ReconciliationApplication import aggregate_reconciliation
from classes.ReconciliationControls import (
    normalise_reconciliation_settings, required_history_days, spatial_cell, reconciliation_columns,
)
from tests.test_reconciliation_application import application, apply, raw_hex, window, streams
from tests.test_reconciliation_factor_resolver import GB, REMOTE, period, composition, resolver, standard


def local(**kwargs):
    return {"opf": "CB OPF", "brand": "SF", "cell": spatial_cell(GB), "analyte": "fe", **kwargs}


class LocalReconciliationTests(unittest.TestCase):
    def test_overrides_apply_to_exact_cell_and_analyte_without_changing_evidence(self):
        original = resolver().resolve(GB)
        engine = resolver(cells=[local(blend=1.4, regression=.8)])
        result = engine.resolve(GB)
        self.assertEqual(result["blend_factors"]["fe"], 1.4)
        self.assertEqual(result["regression_factors"]["fe"], .8)
        self.assertEqual(result["blend_factors"]["si"], original["blend_factors"]["si"])
        for field in ("confidence_percent", "source_history", "resolution_level"):
            self.assertEqual(result[field], original[field])
        self.assertEqual(result["provenance"]["local_override"]["factors"]["blend"]["fe"]["automatic"], 1.1)
        self.assertEqual(engine.resolve(GB.replace("LG01", "LG99"))["blend_factors"]["fe"], 1.4)
        for block in (REMOTE, GB.replace("456", "457"), GB.replace("LG01", "HG01")):
            self.assertNotIn("local_override", engine.resolve(block)["provenance"])

    def test_other_opf_and_brand_cannot_supply_local_settings(self):
        for settings in (local(opf="SO OPF", blend=9), local(brand="WF", blend=9)):
            self.assertEqual(resolver(cells=[settings]).resolve(GB)["blend_factors"]["fe"], 1.1)

    def test_global_fallback_and_unknown_lineage_preserve_confidence(self):
        engine = resolver(samples=[], cells=[local(blend=1.8)])
        known = engine.resolve(GB)
        unknown = engine.resolve("")
        self.assertEqual(known["resolution_level"], "global")
        self.assertEqual(known["confidence_percent"], 0)
        self.assertEqual(known["blend_factors"]["fe"], 1.8)
        self.assertEqual(unknown["blend_factors"]["fe"], 1.07)
        self.assertFalse(unknown["manual_override"])

    def test_standard_ignores_saved_local_windows_and_factors(self):
        engine = resolver(method="standard", cells=[local(blend=8, window={"max_lookback_days": 60})])
        self.assertEqual(engine.resolve(GB)["blend_factors"]["fe"], 1.07)
        self.assertEqual(engine.periods, [])

    def test_local_window_applies_per_analyte_to_both_factor_kinds(self):
        history = period() + period("2026-08-21 06:00", blend=1.5, regression=1.3)
        engine = resolver(history, method="lookback", lookback_days=2,
                          cells=[local(window={"lookback_days": 1})])
        result = engine.resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
        self.assertAlmostEqual(result["blend_factors"]["fe"], 1.5)
        self.assertAlmostEqual(result["regression_factors"]["fe"], 1.3)
        self.assertAlmostEqual(result["blend_factors"]["si"], 1.3)
        self.assertEqual(result["provenance"]["factor_history"]["blend"]["fe"]["window"]["lookback_days"], 1)

    def test_local_guardrail_still_requires_one_level_for_all_ten_series(self):
        result = resolver(cells=[local(window={"min_production_days": 2})]).resolve(GB)
        self.assertEqual(result["resolution_level"], "global")
        self.assertEqual(result["blend_factors"]["si"], 1.07)

    def test_local_production_and_campaign_windows(self):
        history = period("2026-08-15 06:00", blend=1.8) + period(blend=1.2)
        production = resolver(history, method="lookback", lookback_days=3,
                              cells=[local(window={"window_mode": "production_days", "lookback_days": 2})]).resolve(GB)
        campaign = resolver(history, method="lookback", lookback_days=3,
                            cells=[local(window={"window_mode": "latest_campaign", "lookback_days": 2})]).resolve(GB)
        self.assertAlmostEqual(production["blend_factors"]["fe"], 1.5)
        self.assertEqual(campaign["blend_factors"]["fe"], 1.2)

    def test_wider_local_window_fetches_and_uses_older_evidence_only_for_that_analyte(self):
        history = period("2026-07-15 06:00", blend=1.2) + period(blend=1.5)
        for sample in history[-2:]:
            sample["factors"]["fe"] = None
        result = resolver(history, cells=[local(window={"max_lookback_days": 45})]).resolve(GB)
        self.assertEqual(result["resolution_level"], FACTOR_LEVELS[0])
        self.assertEqual(result["blend_factors"]["fe"], 1.2)
        self.assertEqual(result["blend_factors"]["si"], 1.5)
        settings = {"cells": [local(window={"max_lookback_days": 45}), local(brand="WF", window={"max_lookback_days": 99})]}
        self.assertEqual(required_history_days(settings, "CB OPF", ["SF"]), 45)
        self.assertEqual(required_history_days(settings, "SO OPF", ["SF"]), 30)

    def test_override_is_weighted_before_application_and_aggregated_as_manual_coverage(self):
        engine = application(settings={"method": "spatial_compositional", "cells": [local(blend=2)]})
        result, audit = apply(engine)
        self.assertAlmostEqual(result["adjusted_rom"]["SF"]["fe"], 50 * (.4 * 2 + .6 * 1.5))
        self.assertAlmostEqual(audit["by_brand"]["SF"]["manual_override_fraction"], .4)
        aggregate = aggregate_reconciliation([(audit, 100)])
        overall = aggregate_reconciliation([(aggregate, 100)], source_kind="overall")
        columns = reconciliation_columns(overall)
        self.assertEqual(columns["recon_sf_manual_override_pct"], 40)
        self.assertIn(FACTOR_LEVELS[0], columns["recon_sf_fallback_levels"])

    def test_reset_restores_automatic_grade_result(self):
        settings = normalise_reconciliation_settings({"method": "spatial_compositional", "cells": [local(blend=2)]})
        changed, _ = apply(application(settings=settings))
        restored, _ = apply(application(settings={**settings, "cells": [local()]}))
        self.assertNotEqual(changed, restored)
        self.assertEqual(restored, apply()[0])

    def test_settings_round_trip_and_validation(self):
        settings = normalise_reconciliation_settings({"method": "lookback", "cells": [local(blend=1.123456789, window={"lookback_days": 3})]})
        self.assertEqual(normalise_reconciliation_settings(json.loads(json.dumps(settings))), settings)
        self.assertEqual(pickle.loads(pickle.dumps(settings)), settings)
        for entry in (local(blend=0), local(blend=float("nan")), local(blend=True), local(cell="SP1", blend=1),
                      local(window={"min_production_days": 1.1}), local(analyte="s", blend=1),
                      local(opf="EW OPF", regression=1.1)):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                normalise_reconciliation_settings({"cells": [entry]})
        with self.assertRaises(ValueError):
            normalise_reconciliation_settings({"cells": [local(blend=1), local(blend=2)]})


class ReviewIntegrationTests(unittest.TestCase):
    def test_scenario_snapshot_copies_local_settings_and_restore_does_not_capture_stale_widgets(self):
        settings = normalise_reconciliation_settings({"method": "lookback", "cells": [local(blend=1.4)]})
        context = SimpleNamespace(
            project_load_restore_in_progress=True, active_scenario_id="test", reconciliation_settings=settings,
            reconciliation_inputs={"samples": period()}, reconciliation_review=Mock(),
            capture_stockpile_table_choices=lambda: None, capture_active_manual_plan_state=lambda: None,
            capture_calendar_table_inputs=lambda: {}, scenario_database_path=lambda _: "test.db",
            capture_table_snapshot=UserInputs.capture_table_snapshot,
        )
        snapshot = UserInputs.capture_scenario_state(context)
        self.assertEqual(snapshot["reconciliation_settings"], settings)
        snapshot["reconciliation_settings"]["cells"][0]["blend"] = 9
        self.assertEqual(settings["cells"][0]["blend"], 1.4)
        context.reconciliation_review.settings.assert_not_called()

    def test_local_factor_change_reuses_history_but_invalidates_enrichment(self):
        view = window()
        view.mine_input_choice = "TEST"
        view.selected_site_crushers = []
        view.crusher_contribution_ratio_choice = 1
        view.planning_period_count = lambda: 2
        view.selected_planning_category = lambda: "OPF Feed"
        view.auto_load_2wp_targets_choice = False
        view.group_2wp_build_targets_by_brand_choice = False
        view.byproducts_enabled = False
        first_history = view.data_stream_input_request_signature()
        first_enrichment = view.AMT_enrichment_request_signature()
        view.reconciliation_settings["cells"] = [local(blend=1.4)]
        self.assertEqual(first_history, view.data_stream_input_request_signature())
        self.assertNotEqual(first_enrichment, view.AMT_enrichment_request_signature())
        view.reconciliation_settings["cells"][0]["window"] = {"max_lookback_days": 45}
        self.assertNotEqual(first_history, view.data_stream_input_request_signature())

    def test_preview_uses_inventory_and_chunk_grain_without_mutating_sources(self):
        view = window()
        view.updated_stockpile_data = {"INV": {"balance": 100, "build": "INV_01"}, "SP1": {"balance": 999, "amt": True}}
        view.reconciliation_inputs["inventory_lineage"] = {"INV_01": {"inventory_wmt": 100, "contributing_blocks": composition((GB, 100))}}
        view.AMT_stockpile_data = {"SP1": [raw_hex(), raw_hex("H2", block=REMOTE)]}
        view.hex_sequence_table = [{"footprint": "SP1", "sequence": 1, "member_hexes": "H1,H2", "balance": 200}]
        before = copy.deepcopy((view.updated_stockpile_data, view.AMT_stockpile_data, view.hex_sequence_table))
        audits, overall, _ = view.calculate_reconciliation_review()
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[1]["source_kind"], "amt_chunk")
        self.assertEqual(overall["source_wmt"], 300)
        self.assertEqual(len(audits[1]["review_children"]), 2)
        self.assertEqual(before, (view.updated_stockpile_data, view.AMT_stockpile_data, view.hex_sequence_table))

    def test_preview_discloses_missing_hex_members_and_retains_chunk_mass(self):
        view = window()
        view.updated_stockpile_data = {"SP1": {"balance": 100, "amt": True}}
        view.AMT_stockpile_data = {"SP1": [raw_hex()]}
        view.hex_sequence_table = [{"footprint": "SP1", "sequence": 1, "member_hexes": ["H2"], "balance": 100}]
        audits, overall, warnings = view.calculate_reconciliation_review()
        self.assertEqual(overall["source_wmt"], 100)
        self.assertEqual(overall["by_brand"]["SF"]["global_fraction"], 1)
        self.assertTrue(any("member hexes" in w for w in warnings))

    def test_standard_preview_never_resolves_or_mutates_sources(self):
        view = window()
        view.reconciliation_settings = {}
        self.assertEqual(view.calculate_reconciliation_review(), ([], {}, []))

    def test_database_view_default_fields_show_evidence_excluding_aps(self):
        view = window()
        view.selected_data_stream = "adjusted_rom"
        _, audit = apply()
        record = view.database_view_record_with_streams({"source_type": "AMT Chunk"}, streams(), {"reconciliation": audit})
        self.assertIn("recon_sf_confidence_pct", record)
        self.assertIn("recon_sf_fallback_levels", record)
        view.database_view_rows = [record]
        headers = view.database_view_all_headers(include_coverage=False)
        self.assertIn("recon_sf_confidence_pct", view.default_database_view_columns(headers))
        aps = view.database_view_record_with_streams({"source_type": "APS Grade Block"}, streams(), {"reconciliation": audit})
        self.assertNotIn("recon_sf_confidence_pct", aps)

    def test_fetch_snapshot_does_not_read_mutated_window_or_site(self):
        view = window()
        view.mine_input_choice = "TEST"
        view.selected_site_crushers = []
        view.auto_load_2wp_targets_choice = False
        view.data_stream_reconciliation = Mock()
        view.data_stream_reconciliation.fetch.return_value = ({"SF": standard()}, [])
        view.planning_plan_targets = Mock()
        view.planning_period_count = lambda: 2
        view.selected_planning_category = lambda: "OPF Feed"
        view.updated_stockpile_data = {"SP1": {"build": "SP1_01"}}
        fetch = view.data_stream_fetch_snapshot()
        view.opf_input_choice = "SO OPF"
        view.reconciliation_settings["max_lookback_days"] = 99
        view.updated_stockpile_data["SP1"]["build"] = "SP1_02"
        with patch("GUI.InitialiseGUI.ReconciliationHistory") as service:
            service.return_value.fetch.return_value = ([], [])
            service.return_value.fetch_inventory_lineage.return_value = ([], [])
            fetch()
            self.assertEqual(service.return_value.fetch.call_args.args[1], "CB OPF")
            self.assertEqual(service.return_value.fetch.call_args.kwargs["max_lookback_days"], 30)
            self.assertEqual(service.return_value.fetch_inventory_lineage.call_args.args[1], ["SP1_01"])

    def test_late_worker_result_is_ignored(self):
        context = SimpleNamespace(data_stream_input_request_inflight="new", data_stream_input_request_signature=lambda: "new",
                                  finish_data_stream_inputs=Mock())
        UserInputs.finish_cached_data_stream_inputs(context, "old", {"factors": {"wrong": {}}})
        context.finish_data_stream_inputs.assert_not_called()
        self.assertEqual(context.data_stream_input_request_inflight, "new")


class NativeReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if not QFontDatabase().families() and os.path.isfile(r"C:\Windows\Fonts\segoeui.ttf"):
            QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\segoeui.ttf")
            cls.app.setFont(QFont("Segoe UI", 9))

    def setUp(self):
        self.widget = ReconciliationReview()
        self.widget.set_context({"method": "spatial_compositional"}, "CB OPF", ["SF"])
        self.widget.resize(1150, 800)
        self.widget.show()
        self.app.processEvents()
        self.addCleanup(self.widget.close)

    def populate(self):
        _, audit = apply()
        self.widget.set_review([audit], aggregate_reconciliation([(audit, 100)]))
        self.app.processEvents()
        return audit

    def test_modes_enable_only_relevant_controls_and_signals_do_not_fire_on_restore(self):
        spy = QSignalSpy(self.widget.settingsChanged)
        self.widget.set_context({"method": "lookback", "lookback_days": 3}, "CB OPF", ["SF"])
        self.assertEqual(len(spy), 0)
        self.assertTrue(self.widget.days.isEnabled())
        self.assertEqual(self.widget.days.value(), 3)
        self.widget.method.setCurrentIndex(0)
        self.assertEqual(len(spy), 1)
        self.assertFalse(self.widget.tabs.isVisible())
        self.assertFalse(self.widget.minimum.isEnabled())

    def test_source_filter_evidence_and_navigation(self):
        self.populate()
        root = self.widget.sources.topLevelItem(0)
        self.widget.sources.setCurrentItem(root.child(0))
        self.assertIn("Blend:", self.widget.evidence.toPlainText())
        QTest.mouseClick(self.widget.edit_component, Qt.LeftButton)
        self.assertEqual(self.widget.tabs.currentIndex(), 1)
        self.assertEqual(self.widget.cell.currentData(), spatial_cell(GB))
        self.widget.source_filter.setText("PIT02")
        self.assertFalse(root.isHidden())
        self.assertTrue(root.child(0).isHidden())
        self.assertFalse(root.child(1).isHidden())

    def test_invalid_factor_does_not_save_and_reset_restores_inheritance(self):
        self.populate()
        self.widget.tabs.setCurrentIndex(1)
        spy = QSignalSpy(self.widget.settingsChanged)
        self.widget.blend.setText("0")
        QTest.mouseClick(self.widget.save_cell, Qt.LeftButton)
        self.assertEqual(len(spy), 0)
        self.assertIn("greater than zero", self.widget.local_status.text())
        self.widget.blend.setText("1.23456789")
        self.widget.custom_window.setChecked(True)
        self.widget.local_minimum.setValue(2)
        QTest.mouseClick(self.widget.save_cell, Qt.LeftButton)
        self.assertEqual(len(spy), 1)
        record = self.widget.settings()["cells"][0]
        self.assertEqual(record["blend"], 1.23456789)
        self.assertEqual(record["window"]["min_production_days"], 2)
        QTest.mouseClick(self.widget.reset_cell, Qt.LeftButton)
        self.assertNotIn("cells", self.widget.settings())

    def test_saved_cells_remain_navigable_without_loaded_sources(self):
        self.widget.set_context({"method": "lookback", "cells": [local(blend=1.3)]}, "CB OPF", ["SF"])
        self.assertEqual(self.widget.cell.currentData(), spatial_cell(GB))
        self.assertEqual(self.widget.blend.text(), "1.3")
        self.widget.cell_filter.setText("nothing")
        self.assertEqual(self.widget.cell.count(), 0)
        self.assertFalse(self.widget.save_cell.isEnabled())

    def test_restore_site_scope_does_not_leak_local_cells(self):
        spy = QSignalSpy(self.widget.settingsChanged)
        self.widget.set_context({"method": "lookback", "cells": [local(blend=1.3)]}, "CB OPF", ["SF"])
        self.widget.set_context({}, "SO OPF", ["WF"])
        self.assertEqual(self.widget.cell.count(), 0)
        self.assertEqual(self.widget.settings()["method"], "standard")
        self.assertEqual(len(spy), 0)

    def test_review_then_submit_advances_once_and_stale_settings_require_recalculation(self):
        view = window()
        view.reconciliation_review = self.widget
        view.recon_factor_table = QTableWidget(0, 14)
        view.data_streams_submit_button = QPushButton()
        view.populate_recon_factor_table()
        view.updated_stockpile_data = {"INV": {"balance": 100}}
        for name in ("data_stream_selector", "crusher_tonnes_selector", "reclaimer_tonnes_selector", "product_build_tonnes_selector"):
            setattr(view, name, SimpleNamespace(currentData=lambda: None))
        view.rom_planning_category_input = SimpleNamespace(text=lambda: "OPF Feed")
        view.product_planning_category_input = SimpleNamespace(text=lambda: "OPF Production")
        for name in ("capture_cb_lump_fines_settings", "capture_byproduct_build_settings", "apply_canonical_field_mappings",
                     "apply_grade_streams_to_inventory", "refresh_AMT_enrichment_if_needed", "save_active_scenario_state",
                     "set_page_enabled", "open_database_view", "prepare_data_streams"):
            setattr(view, name, Mock())
        view.data_stream_reconciliation = Mock()
        view.data_stream_pending_build_targets = {}
        view.stockpile_data_AMT_column = {}
        for name in ("data_streams_tab_index", "stockpile_tab_index", "define_fields_tab_index", "map_fields_tab_index", "guidance_schedules_tab_index"):
            setattr(view, name, name)
        view.update_reconciliation_review()
        self.assertTrue(view.data_streams_submit_button.isEnabled())
        view.handle_data_streams_submit()
        view.apply_grade_streams_to_inventory.assert_called_once()
        view.refresh_AMT_enrichment_if_needed.assert_called_once_with(persist=True, refresh_map=True)
        view.open_database_view.assert_called_once_with(navigate=True)
        view.prepare_data_streams.assert_not_called()
        view.reconciliation_settings["cells"] = [local(blend=2)]
        view.handle_data_streams_submit()
        view.prepare_data_streams.assert_called_once()
        view.apply_grade_streams_to_inventory.assert_called_once()

    def test_default_and_local_changes_invalidate_submit_and_keep_model_in_sync(self):
        view = window()
        QMainWindow.__init__(view)
        view.reconciliation_review = self.widget
        view.data_streams_submit_button = QPushButton()
        self.widget.settingsChanged.connect(view.reconciliation_controls_changed)
        view._reconciliation_review_signature = "old"
        self.widget.maximum.setValue(60)
        self.assertFalse(view.data_streams_submit_button.isEnabled())
        self.assertEqual(view.reconciliation_settings["max_lookback_days"], 60)
        self.assertEqual(view._reconciliation_review_signature, "")

    def test_global_display_rounding_does_not_change_effective_factors(self):
        view = window()
        view.recon_factor_table = QTableWidget(0, 14)
        original = 1.123456789
        view.historical_recon_factors["SF"]["blend"]["fe"]["effective"] = original
        view.populate_recon_factor_table()
        view.capture_recon_factor_table()
        self.assertEqual(view.historical_recon_factors["SF"]["blend"]["fe"]["effective"], original)
        view.recon_factor_table.item(0, 5).setText("1.5")
        view.capture_recon_factor_table()
        self.assertEqual(view.historical_recon_factors["SF"]["blend"]["fe"]["effective"], 1.5)
        view.recon_factor_table.item(0, 5).setText(f"{original:.4f}")
        view.capture_recon_factor_table()
        self.assertEqual(view.historical_recon_factors["SF"]["blend"]["fe"]["effective"], original)

    def test_review_error_keeps_submission_disabled(self):
        view = window()
        view.reconciliation_review = self.widget
        view.data_streams_submit_button = QPushButton()
        view.calculate_reconciliation_review = Mock(side_effect=ValueError("Invalid history pair"))
        messages = view.update_reconciliation_review()
        self.assertFalse(view.data_streams_submit_button.isEnabled())
        self.assertIn("Invalid history pair", messages[0])
        self.assertTrue(self.widget.review_button.isEnabled())

    def test_csv_export_keeps_source_grain_confidence_and_levels_and_disables_when_stale(self):
        audit = self.populate()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "review.csv")
            with patch("GUI.ReconciliationReview.QFileDialog.getSaveFileName", return_value=(path, "CSV")):
                QTest.mouseClick(self.widget.export_button, Qt.LeftButton)
            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["source_id"], audit["source_id"])
        self.assertEqual(float(rows[1]["recon_sf_confidence_pct"]), audit["by_brand"]["SF"]["confidence_percent"])
        self.assertIn(FACTOR_LEVELS[0], rows[1]["recon_sf_fallback_levels"])
        self.assertEqual(rows[1]["method"], "spatial_compositional")
        self.widget.mark_stale()
        self.assertFalse(self.widget.export_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
