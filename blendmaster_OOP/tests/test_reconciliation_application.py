import copy
from contextlib import closing
import json
import os
import pickle
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from classes.GradeStreams import ANALYTES, amt_grade_streams, inventory_grade_streams, reweight_grade_streams_from_properties
from classes.ReconciliationApplication import (
    ReconciliationApplication, aggregate_reconciliation, amt_reconciliation_lineage,
    normalise_reconciliation_settings, reconciliation_fingerprint,
)
from database.DatabaseContext import get_database_path, set_database_path
from GUI.DrawCharts import DrawAMTStockpile
from GUI.InitialiseGUI import UserInputs
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from tests.test_reconciliation_factor_resolver import AS_OF, GB, REMOTE, composition, period, standard


def samples():
    return period(blend=1.1, regression=0.9) + period("2026-08-21 06:00", block=REMOTE, blend=1.5, regression=1.3)


def application(**kwargs):
    args = dict(samples=samples(), standard_factors={"SF": standard()}, opf="CB OPF", brands=["SF"],
                scenario_start=AS_OF, settings={"method": "spatial_compositional"})
    args.update(kwargs)
    return ReconciliationApplication(**args)


def streams(rom=50, product=60, opf="CB OPF"):
    return amt_grade_streams({f"grade_{a}": rom for a in ANALYTES},
                            {f"MODELLED_PROD1_{a}": product for a in ("FE", "SIO2", "AL2O3", "P", "MN")},
                            ["SF"], {"SF": standard()}, opf)


def apply(engine=None, values=None, blocks=None, total=100, **kwargs):
    return (engine or application()).apply(
        streams() if values is None else values, source_id="SP1", source_kind="amt", source_wmt=total,
        contributing_blocks=composition((GB, 40), (REMOTE, 60)) if blocks is None else blocks,
        hex_id="H1", **kwargs,
    )


def raw_hex(hex_id="H1", *, block=GB, total=100, rom=50, product=60):
    return {"FOOTPRINT": "SP1", "HEX": hex_id, "FINAL_WMT": total,
            **{a: rom for a in ("FE", "SIO2", "AL2O3", "P", "MN")},
            **{f"MODELLED_PROD1_{a}": product for a in ("FE", "SIO2", "AL2O3", "P", "MN")},
            "MODELLED_PROPERTIES_JSON": {
                "values": {f"prod1_{a}": product for a in ("fe", "sio2", "al2o3", "p", "mn")},
                "coverage": {f"prod1_{a}": 1.0 for a in ("fe", "sio2", "al2o3", "p", "mn")}},
            "GRADE_BLOCK_LINEAGE_JSON": [{"grade_block_name": block, "remaining_wmt": total,
                                          "inbound_wmt": total * 2, "lineage_key": block}]}


def window():
    view = UserInputs.__new__(UserInputs)
    view.product_brand_labels_choice = ["SF"]
    view.opf_input_choice = "CB OPF"
    view.start_time_choice = AS_OF
    view.historical_recon_factors = {"SF": standard()}
    view.historical_recon_warnings = []
    view.reconciliation_settings = {"method": "spatial_compositional"}
    view.reconciliation_inputs = {"samples": samples()}
    view.is_cloudbreak_site = lambda: False
    return view


def chart():
    value = DrawAMTStockpile.__new__(DrawAMTStockpile)
    value.get_chunk_setting = lambda _footprint, _key, default=None: default
    value.get_chunk_plan = lambda _footprint: {"chunk_count": 1}
    value.source_property_kinds = {"product_dmt": "additive"}
    value.source_property_weights = {}
    for a in ANALYTES:
        for stream in ("modelled_rom", "adjusted_rom", "modelled_product", "adjusted_product"):
            key = f"{stream}_{a}"
            value.source_property_kinds[key] = "weighted_average"
            if "product" in stream:
                value.source_property_weights[key] = "product_dmt"
    return value


def chunk_row(hex_id, block, tonnes, rom, product, product_dmt, signature="current"):
    calculated, audit = apply(values=streams(rom, product), blocks=composition((block, tonnes)), total=tonnes)
    audit["hex_id"] = hex_id
    audit["enrichment_signature"] = signature
    defined = {f"{s}_{a}": calculated[s]["SF"][a] for s in
               ("modelled_rom", "adjusted_rom", "modelled_product", "adjusted_product") for a in ANALYTES}
    return {"footprint": "SP1", "hex": hex_id, "balance": tonnes, "_positive_balance": tonnes,
            **{f"grade_{a}": rom for a in ANALYTES}, "grade_streams": calculated,
            "defined_fields": {**defined, "product_dmt": product_dmt}, "reconciliation": audit}


class ApplicationTests(unittest.TestCase):
    def test_component_factors_are_rom_wmt_weighted_then_applied_to_source_baselines(self):
        original = streams()
        adjusted, audit = apply(values=original)
        self.assertAlmostEqual(adjusted["adjusted_rom"]["SF"]["fe"], 50 * (0.4 * 1.1 + 0.6 * 1.5))
        self.assertAlmostEqual(adjusted["adjusted_product"]["SF"]["fe"], 60 * (0.4 * 0.9 + 0.6 * 1.3))
        for name in ("insitu", "modelled_rom", "modelled_product"):
            self.assertEqual(adjusted[name], original[name])
        self.assertEqual(original, streams())
        self.assertEqual(audit["source_wmt"], 100)
        self.assertEqual(len(audit["by_brand"]["SF"]["records"]), 2)
        self.assertAlmostEqual(audit["by_brand"]["SF"]["applied_factors"]["regression"]["fe"], 1.14)

    def test_repeated_application_uses_modelled_baseline_and_is_idempotent(self):
        engine = application()
        first, _ = apply(engine)
        second, _ = apply(engine, first)
        self.assertEqual(first, second)

    def test_missing_lineage_retains_global_factor_and_mass(self):
        adjusted, audit = apply(blocks=composition((GB, 75)))
        self.assertAlmostEqual(adjusted["adjusted_rom"]["SF"]["fe"], 50 * (0.75 * 1.1 + 0.25 * 1.07))
        detail = audit["by_brand"]["SF"]
        self.assertEqual(detail["global_fraction"], 0.25)
        self.assertEqual(detail["lineage_coverage"], 0.75)
        self.assertEqual(detail["confidence_percent"], 75)
        self.assertEqual(detail["grade_coverage"]["adjusted_product"]["fe"], 1)
        self.assertTrue(audit["warnings"])

    def test_grade_coverage_stays_separate_from_factor_lineage(self):
        baseline = streams()
        baseline["modelled_product"]["SF"]["mn"] = None
        adjusted, audit = apply(values=baseline, blocks=[], grade_coverage={"modelled_product": {"fe": 0.3}})
        detail = audit["by_brand"]["SF"]
        self.assertEqual(detail["lineage_coverage"], 0)
        self.assertEqual(detail["grade_coverage"]["adjusted_product"]["fe"], 0.3)
        self.assertEqual(detail["grade_coverage"]["adjusted_product"]["mn"], 0)
        self.assertIsNone(adjusted["adjusted_product"]["SF"]["mn"])
        self.assertAlmostEqual(adjusted["adjusted_product"]["SF"]["fe"], 60 * 0.97)

    def test_standard_mode_does_not_require_advanced_inputs_or_change_streams(self):
        original = streams()
        result, audit = apply(application(samples=["unused"], standard_factors={}, settings={}), values=original)
        self.assertEqual(result, original)
        self.assertIsNot(result, original)
        self.assertEqual(audit, {})
        self.assertEqual(normalise_reconciliation_settings()["method"], "standard")

    def test_dry_plant_product_follows_adjusted_rom(self):
        engine = application(opf="EW OPF", samples=period(opf="EW OPF", brand="SF", blend=1.2, regression=9))
        result, _ = apply(engine, streams(opf="EW OPF"), blocks=composition((GB, 100)))
        self.assertEqual(result["adjusted_rom"]["SF"]["fe"], 60)
        self.assertEqual(result["modelled_product"]["SF"], result["adjusted_rom"]["SF"])
        self.assertEqual(result["adjusted_product"]["SF"], result["adjusted_rom"]["SF"])

    def test_amt_unconfirmed_product_stays_unavailable(self):
        engine = application(opf="IB OPF", samples=period(opf="IB OPF", brand="SF"))
        result, _ = apply(engine, streams(opf="IB OPF"))
        self.assertIsNone(result["adjusted_product"]["SF"]["fe"])
        self.assertIsNotNone(result["adjusted_rom"]["SF"]["fe"])

    def test_invalid_source_lineage_falls_back_but_missing_standard_factors_raise(self):
        for blocks in (composition((GB, 101)), composition((GB, -1)), [{"feed_wmt": None}]):
            with self.subTest(blocks=blocks):
                result, audit = apply(blocks=blocks)
                self.assertAlmostEqual(result["adjusted_rom"]["SF"]["fe"], 53.5)
                self.assertEqual(audit["by_brand"]["SF"]["global_fraction"], 1)
                self.assertIn("Invalid source lineage", audit["warnings"][0])
        with self.assertRaises(ValueError):
            application(standard_factors={})

    def test_zero_mass_is_not_inflated_and_has_no_adjustment_contribution(self):
        original = streams()
        result, audit = apply(values=original, blocks=[], total=0)
        self.assertEqual(result, original)
        self.assertEqual(audit["source_wmt"], 0)
        self.assertEqual(audit["by_brand"]["SF"]["records"], [])
        self.assertIsNone(audit["by_brand"]["SF"]["confidence_percent"])

    def test_aps_cannot_enter_the_stockpile_application_api(self):
        with self.assertRaisesRegex(ValueError, "only to inventory"):
            application().apply(streams(), source_id="GB", source_kind="aps", source_wmt=100, contributing_blocks=[])

    def test_aligned_remaining_tonnes_are_used_not_original_inbound(self):
        blocks, warnings = amt_reconciliation_lineage({"GRADE_BLOCK_LINEAGE_JSON": json.dumps([
            {"grade_block_name": GB, "remaining_wmt": 40, "inbound_wmt": 9999},
            {"grade_block_name": REMOTE, "remaining_wmt": 60, "inbound_wmt": 1},
        ])})
        self.assertEqual(blocks, composition((GB, 40), (REMOTE, 60)))
        self.assertEqual(warnings, [])

    def test_missing_or_malformed_aligned_amt_lineage_is_unknown(self):
        for raw in ("bad JSON", [{"grade_block_name": GB, "inbound_wmt": 100}], "{}"):
            blocks, warnings = amt_reconciliation_lineage({"GRADE_BLOCK_LINEAGE_JSON": raw})
            self.assertEqual(blocks, [])
            self.assertTrue(warnings)

    def test_multiple_brands_keep_distinct_adjustment_factors(self):
        history = samples() + period(brand="CBFL", blend=1.6, regression=0.8)
        engine = application(samples=history, brands=["SF", "FL"], standard_factors={"SF": standard(), "FL": standard()})
        original = streams()
        for name in ("modelled_rom", "modelled_product"):
            original[name]["FL"] = copy.deepcopy(original[name]["SF"])
        result, audit = apply(engine, original, blocks=composition((GB, 100)))
        self.assertAlmostEqual(result["adjusted_product"]["SF"]["fe"], 54)
        self.assertAlmostEqual(result["adjusted_product"]["FL"]["fe"], 48)
        self.assertEqual(set(audit["by_brand"]), {"SF", "FL"})

    def test_output_audit_is_strict_json_and_pickle_safe(self):
        _, audit = apply()
        self.assertEqual(json.loads(json.dumps(audit, allow_nan=False)), audit)
        self.assertEqual(pickle.loads(pickle.dumps(audit)), audit)


class ChunkAndIntegrationTests(unittest.TestCase):
    @staticmethod
    def restoring_view(*, current_time=False):
        view = SimpleNamespace(**vars(window()))
        view.is_project_loaded = True
        view.project_load_restore_in_progress = True
        view.project_load_waiting_for_inventory = True
        view.project_load_refresh_current_time = current_time
        view.site_scenarios = {"site_test": {}}
        view.active_scenario_id = "site_test"
        view.hub_input_choice = "Chichester"
        view.mine_input_choice = "CB"
        view.crusher_input_choice = "CB1"
        view.crusher_contribution_ratio_choice = 1.0
        view.haul_cycle_file_path_choice = ""
        view.project_load_saved_stockpile_use_column = {"SP1": True}
        view.project_load_saved_stockpile_AMT_column = {"SP1": True}
        view.planning_period_count = lambda: 3
        view.normalized_solver_config = lambda values: dict(values or {})
        view.included_stockpile_data = lambda: {}
        view.included_AMT_snapshot = lambda rows: rows
        view.aps_grade_mapping_warnings = lambda: []
        for name in (
            "submit_button", "save_button", "opening_stockpile_inventories",
            "restore_table_snapshot", "populate_product_build_table",
            "refresh_aps_stockpile_brand_map", "apply_aps_brand_guidance_to_stockpile_data",
            "populate_aps_grade_mapping_table", "populate_aps_source_property_mapping_table",
            "load_cb_lump_fines_settings", "load_byproduct_build_settings",
            "populate_recon_factor_table", "apply_haul_cycle_routes_to_stockpile_data",
            "save_active_scenario_state", "setup_stockpile_table", "populate_define_fields_table",
            "refresh_map_field_brands", "ensure_field_mapping_migration", "populate_map_fields_table",
            "refresh_map_available_fields", "set_page_enabled", "apply_canonical_field_mappings",
            "validate_form", "reset_workflow_tabs_for_scenario", "finish_project_load_ui",
            "show_page", "store_stockpile_table", "continue_project_load_after_stockpile_setup",
        ):
            setattr(view, name, Mock())
        view.site_config_tab_index = "site_configuration"
        view.guidance_schedules_tab_index = "guidance_schedules"
        view.stockpile_tab_index = "stockpile_inventories"
        view.reconciliation_factors_pending = lambda: UserInputs.reconciliation_factors_pending(view)
        view.reconciliation_application = lambda: UserInputs.reconciliation_application(view)
        view.apply_grade_streams_to_inventory = Mock(
            side_effect=lambda **kw: UserInputs.apply_grade_streams_to_inventory(view, **kw)
        )
        view.reset_downstream_inputs_for_new_site_configuration = lambda **kw: (
            UserInputs.reset_downstream_inputs_for_new_site_configuration(view, **kw)
        )
        return view

    def test_refreshed_project_defers_advanced_factors_and_resumes_inventory_setup(self):
        for current_time in (True, False):
            with self.subTest(current_time=current_time):
                view = self.restoring_view(current_time=current_time)
                inventory = {"SP1": {"BALANCE": 100}}
                with patch("GUI.InitialiseGUI.DatabaseManager.clear_all_tables"):
                    UserInputs.finish_site_config_submit(view, {
                        "stockpile_data": inventory, "inventory_data_request_signature": "new-opening",
                    })
                self.assertEqual(view.historical_recon_factors, {})
                self.assertEqual(view.reconciliation_inputs, {})
                self.assertEqual(view.reconciliation_settings["method"], "spatial_compositional")
                self.assertNotIn("GRADE_STREAMS", inventory["SP1"])
                self.assertFalse(view.project_load_restore_in_progress)
                self.assertFalse(view.project_load_waiting_for_inventory)
                view.finish_project_load_ui.assert_called_once_with(success=True)
                view.show_page.assert_called_once_with("stockpile_inventories", force=True)
                view.store_stockpile_table.assert_not_called()
                view.continue_project_load_after_stockpile_setup.assert_not_called()
                if current_time:
                    self.assertEqual(view.stockpile_data_use_column, {"SP1": True})
                    self.assertEqual(view.stockpile_data_AMT_column, {"SP1": True})

    def test_saved_opening_restore_keeps_factors_and_continues(self):
        view = self.restoring_view()
        expected = copy.deepcopy(view.historical_recon_factors)
        UserInputs.finish_site_config_submit(view, {})
        self.assertEqual(view.historical_recon_factors, expected)
        self.assertIsInstance(view.reconciliation_application(), ReconciliationApplication)
        view.apply_grade_streams_to_inventory.assert_called_once_with(allow_pending=True)
        view.store_stockpile_table.assert_called_once_with()
        view.continue_project_load_after_stockpile_setup.assert_called_once_with()

    def test_map_fields_reaches_data_streams_before_advanced_factors_are_loaded(self):
        view = window()
        view.historical_recon_factors = {}
        for name in ("capture_map_fields_table", "apply_canonical_field_mappings",
                     "set_page_enabled", "save_active_scenario_state", "show_page"):
            setattr(view, name, Mock())
        view.data_streams_tab_index = "data_streams"
        UserInputs.handle_map_fields_submit(view)
        view.show_page.assert_called_once_with("data_streams", force=True)
        # Data Streams submission still requires an actual factor record.
        with self.assertRaisesRegex(ValueError, "standard factor record"):
            view.apply_grade_streams_to_inventory()

    def test_raw_amt_waits_for_factors_then_enriches_on_data_streams_submission(self):
        view = window()
        view.historical_recon_factors = {}
        view.AMT_stockpile_data = {"SP1": [raw_hex()]}
        raw = copy.deepcopy(view.AMT_stockpile_data)
        view.AMT_enrichment_signature = "old"
        view.prune_excluded_AMT_state = Mock()
        view.prune_zeroed_amt_chunks = Mock()
        view.opening_stockpile_inventories = Mock()
        view.refresh_AMT_map_data_from_database = Mock()
        self.assertFalse(view.refresh_AMT_enrichment_if_needed({}, allow_pending=True))
        self.assertEqual(view.AMT_stockpile_data, raw)
        self.assertEqual(view.AMT_enrichment_signature, "")
        view.opening_stockpile_inventories.save_AMT_to_database.assert_called_once_with(raw)
        with self.assertRaisesRegex(ValueError, "standard factor record"):
            view.refresh_AMT_enrichment_if_needed({})
        view.historical_recon_factors = {"SF": standard()}
        self.assertTrue(view.refresh_AMT_enrichment_if_needed({}))
        self.assertTrue(view.AMT_enrichment_signature)
        self.assertIn("reconciliation", view.AMT_stockpile_data["SP1"][0])

    def test_chunk_grades_use_per_hex_adjustments_and_declared_product_weights(self):
        rows = [chunk_row("H1", GB, 40, 50, 60, 10), chunk_row("H2", REMOTE, 60, 30, 40, 90)]
        chunk = chart().build_chunk_row("SP1", 1, rows, 100)
        self.assertAlmostEqual(chunk["grade_streams"]["adjusted_rom"]["SF"]["fe"], 49)
        self.assertAlmostEqual(chunk["grade_streams"]["adjusted_product"]["SF"]["fe"], 52.2)
        self.assertEqual(chunk["balance"], 100)
        self.assertEqual(chunk["source_properties"]["product_dmt"], 100)
        self.assertEqual(chunk["reconciliation"]["by_brand"]["SF"]["confidence_percent"], 100)
        self.assertEqual(chunk["reconciliation"]["enrichment_signature"], "current")

    def test_chunk_missing_product_assay_keeps_covered_grade_and_reports_coverage(self):
        rows = [chunk_row("H1", GB, 40, 50, 60, 10), chunk_row("H2", REMOTE, 60, 30, None, 90)]
        chunk = chart().build_chunk_row("SP1", 1, rows, 100)
        self.assertEqual(chunk["grade_streams"]["adjusted_product"]["SF"]["fe"], 54)
        self.assertEqual(chunk["reconciliation"]["by_brand"]["SF"]["grade_coverage"]["adjusted_product"]["fe"], 0.4)

    def test_chunk_missing_declared_weight_does_not_claim_grade_coverage(self):
        rows = [chunk_row("H1", GB, 40, 50, 60, 10), chunk_row("H2", REMOTE, 60, 30, 40, None)]
        chunk = chart().build_chunk_row("SP1", 1, rows, 100)
        self.assertEqual(chunk["grade_streams"]["adjusted_product"]["SF"]["fe"], 54)
        self.assertEqual(chunk["reconciliation"]["by_brand"]["SF"]["grade_coverage"]["adjusted_product"]["fe"], 0.4)

    def test_reweighting_does_not_reapply_ratio_to_an_advanced_grade(self):
        original, _ = apply()
        result = reweight_grade_streams_from_properties(original, {"modelled_product_fe": 80}, preserve_adjusted=True)
        self.assertEqual(result["adjusted_product"], original["adjusted_product"])
        self.assertEqual(result["modelled_product"]["SF"]["fe"], 80)

    def test_missing_source_audit_retains_mass_in_chunk_confidence(self):
        _, audit = apply(blocks=composition((GB, 100)))
        summary = aggregate_reconciliation([(audit, 40), ({}, 60)])
        self.assertEqual(summary["by_brand"]["SF"]["confidence_percent"], 40)
        self.assertEqual(summary["by_brand"]["SF"]["global_fraction"], 0.6)

    def test_legacy_dataframe_nan_audit_is_an_unscored_source(self):
        _, audit = apply(blocks=composition((GB, 100)))
        summary = aggregate_reconciliation([(audit, 40), (float("nan"), 60)])
        self.assertEqual(summary["by_brand"]["SF"]["confidence_percent"], 40)

    def test_mixed_opfs_are_not_aggregated_before_reconciliation(self):
        _, audit = apply()
        other = copy.deepcopy(audit)
        other["opf"] = "CC_OPF01"
        with self.assertRaisesRegex(ValueError, "one OPF"):
            aggregate_reconciliation([(audit, 40), (other, 60)])

    def test_gui_enriches_each_hex_and_repeated_refresh_is_idempotent(self):
        view = window()
        raw = {"SP1": [raw_hex(), raw_hex("H2", block=REMOTE, rom=30, product=40)]}
        enriched = view.enrich_AMT_grade_streams({}, raw)
        again = view.enrich_AMT_grade_streams({}, enriched)
        self.assertEqual(enriched, again)
        self.assertEqual(raw["SP1"][0]["FINAL_WMT"], 100)
        self.assertNotIn("reconciliation", raw["SP1"][0])
        self.assertAlmostEqual(enriched["SP1"][0]["GRADE_STREAMS"]["adjusted_rom"]["SF"]["fe"], 55)
        self.assertAlmostEqual(enriched["SP1"][1]["GRADE_STREAMS"]["adjusted_rom"]["SF"]["fe"], 45)

    def test_gui_inventory_scales_exact_build_lineage_to_current_physical_balance(self):
        view = window()
        view.reconciliation_inputs["inventory_lineage"] = {
            "SP1_26001": {"inventory_wmt": 200, "contributing_blocks": composition((GB, 80), (REMOTE, 120))}}
        row = {"BUILD": "SP1_26001", "BALANCE": 100}
        result = view.apply_source_reconciliation(view.reconciliation_application(), row, streams(), "SP1", "inventory")
        self.assertAlmostEqual(result["adjusted_product"]["SF"]["fe"], 68.4)
        self.assertEqual(row["reconciliation"]["source_wmt"], 100)
        row["BUILD"] = "SP1_25001"
        result = view.apply_source_reconciliation(view.reconciliation_application(), row, streams(), "SP1", "inventory")
        self.assertAlmostEqual(result["adjusted_product"]["SF"]["fe"], 58.2)
        self.assertEqual(row["reconciliation"]["by_brand"]["SF"]["global_fraction"], 1)

    def test_gui_cache_changes_with_effective_factors_and_settings(self):
        view = window()
        first = view.reconciliation_application()
        self.assertIs(first, view.reconciliation_application())
        view.historical_recon_factors["SF"]["blend"]["fe"]["effective"] = 1.2
        second = view.reconciliation_application()
        self.assertIsNot(first, second)
        signature = view.AMT_enrichment_request_signature()
        view.reconciliation_settings["min_production_days"] = 3
        self.assertNotEqual(signature, view.AMT_enrichment_request_signature())
        self.assertIsNot(second, view.reconciliation_application())

    def test_standard_gui_fetch_issues_no_advanced_queries(self):
        view = window()
        view.reconciliation_settings = {}
        with patch("GUI.InitialiseGUI.ReconciliationHistory") as history:
            self.assertEqual(view.fetch_advanced_reconciliation_inputs(), {})
        history.assert_not_called()

    def test_bulk_fetch_keeps_history_when_inventory_lineage_fails(self):
        view = window()
        view.updated_stockpile_data = {"SP1": {"BUILD": "SP1_26001"}, "SP2": {"BUILD": "SP2_26001", "amt": True}}
        view.data_stream_reconciliation = Mock()
        with patch("GUI.InitialiseGUI.ReconciliationHistory") as history:
            service = history.return_value
            service.fetch.return_value = (samples(), [])
            service.fetch_inventory_lineage.side_effect = ConnectionError("offline")
            result = view.fetch_advanced_reconciliation_inputs()
        self.assertEqual(result["samples"], samples())
        service.fetch_inventory_lineage.assert_called_once_with(AS_OF, ["SP1_26001"])
        self.assertIn("Inventory lineage unavailable", result["warnings"][0])

    def test_standard_mode_removes_previous_advanced_audit(self):
        view = window()
        enriched = view.enrich_AMT_grade_streams({}, {"SP1": [raw_hex()]})
        view.reconciliation_settings = {"method": "standard"}
        standard_rows = view.enrich_AMT_grade_streams({}, enriched)
        self.assertNotIn("reconciliation", standard_rows["SP1"][0])
        self.assertAlmostEqual(standard_rows["SP1"][0]["grade_streams"]["adjusted_rom"]["SF"]["fe"], 53.5)

    def test_mapping_change_clears_stale_product_grade_and_inherits_raw_coverage(self):
        view = window()
        view.field_definitions = [
            {"name": f"{s}_fe", "kind": "weighted_average"}
            for s in ("insitu", "modelled_rom", "modelled_product", "adjusted_rom", "adjusted_product")]
        view.field_mappings = [{"source_family": "amt", "target_field": f"{s}_fe", "source_field": raw}
                              for s, raw in (("insitu", "FE"), ("modelled_rom", "FE"),
                                             ("modelled_product", "MODELLED_PROD1_FE"))]
        row = raw_hex()
        row["MODELLED_PROPERTIES_JSON"]["coverage"]["prod1_fe"] = 0.3
        enriched = view.enrich_AMT_grade_streams({}, {"SP1": [row]})
        first = enriched["SP1"][0]
        self.assertEqual(first["reconciliation"]["by_brand"]["SF"]["grade_coverage"]["adjusted_product"]["fe"], 0.3)
        self.assertEqual(first["adjusted_product_fe"], 54)
        view.field_mappings[-1]["source_field"] = ""
        cleared = view.enrich_AMT_grade_streams({}, enriched)["SP1"][0]
        self.assertIsNone(cleared["modelled_product_fe"])
        self.assertIsNone(cleared["adjusted_product_fe"])
        self.assertNotIn("adjusted_product_fe", cleared["source_properties"])
        self.assertEqual(cleared["reconciliation"]["by_brand"]["SF"]["grade_coverage"]["adjusted_product"]["fe"], 0)

    def test_calculated_cb_split_preserves_advanced_sf_head_grade(self):
        from tests.test_cloudbreak_product_split import CloudbreakProductSplitTests
        view = CloudbreakProductSplitTests.calculated_view()
        adjusted, audit = apply(application(samples=period(regression=0.95)), blocks=composition((GB, 100)))
        chunk = {"hex": "SP1_CHUNK_001", "grade_streams": adjusted, "reconciliation": audit,
                 "modelled_properties": {"values": {
                     "modelled_product_wmt": 100, "modelled_product_dmt": 80,
                     **{f"modelled_product_{a}": 60 for a in ANALYTES}}, "coverage": {}}}
        view.apply_cb_split_to_amt_chunk(chunk)
        self.assertAlmostEqual(chunk["grade_streams"]["adjusted_product"]["SF"]["fe"], 57)
        values = chunk["modelled_properties"]["values"]
        self.assertAlmostEqual((values["prod1_lump_fe"] * 20 + values["prod1_fines_fe"] * 60) / 80, 57)

    def test_scenario_snapshot_retains_reconciliation_settings_and_history(self):
        view = SimpleNamespace(
            project_load_restore_in_progress=True, active_scenario_id="site_test",
            reconciliation_settings={"method": "lookback", "lookback_days": 3},
            reconciliation_inputs={"samples": samples()},
            capture_stockpile_table_choices=lambda: None, capture_active_manual_plan_state=lambda: None,
            capture_calendar_table_inputs=lambda: {}, scenario_database_path=lambda _: "scenario.db",
            capture_table_snapshot=UserInputs.capture_table_snapshot,
        )
        state = UserInputs.capture_scenario_state(view)
        self.assertEqual(state["reconciliation_settings"], view.reconciliation_settings)
        self.assertEqual(state["reconciliation_inputs"], view.reconciliation_inputs)
        state["reconciliation_inputs"]["samples"][0]["feed_wmt"] = -1
        self.assertGreater(view.reconciliation_inputs["samples"][0]["feed_wmt"], 0)

    def test_current_advanced_chunk_is_not_overwritten_by_global_refresh(self):
        view = window()
        signature = reconciliation_fingerprint(view.AMT_enrichment_request_signature())
        row = chunk_row("H1", GB, 100, 50, 60, 100, signature)
        chunk = chart().build_chunk_row("SP1", 1, [row], 100)
        view.hex_sequence_table = [chunk]
        view.reconcile_saved_AMT_chunk_grade_streams(force=True)
        self.assertEqual(view.hex_sequence_table[0]["grade_streams"]["adjusted_product"]["SF"]["fe"], 54)
        self.assertEqual(view.hex_sequence_table, view.hex_sequence_table_argument)

    def test_stale_chunk_without_current_member_hexes_has_explicit_global_fallback(self):
        view = window()
        chunk = chart().build_chunk_row("SP1", 1, [chunk_row("H1", GB, 100, 50, 60, 100)], 100)
        view.hex_sequence_table = [chunk]
        view.reconcile_saved_AMT_chunk_grade_streams(force=True)
        result = view.hex_sequence_table[0]
        self.assertAlmostEqual(result["grade_streams"]["adjusted_product"]["SF"]["fe"], 58.2)
        self.assertEqual(result["reconciliation"]["by_brand"]["SF"]["confidence_percent"], 0)
        self.assertIn("member-hex", result["reconciliation"]["warnings"][0])

    def test_overall_confidence_counts_submitted_amt_chunks_once(self):
        view = window()
        _, good = apply(blocks=composition((GB, 100)))
        _, unknown = apply(blocks=[])
        view.updated_stockpile_data = {"INV": {"balance": 100, "reconciliation": good}, "SP1": {"amt": True}}
        view.AMT_stockpile_data = {"SP1": [{"FINAL_WMT": 9999, "reconciliation": good}]}
        view.hex_sequence_table = [{"footprint": "SP1", "balance": 100, "reconciliation": unknown}]
        result = view.overall_reconciliation_confidence()
        self.assertEqual(result["source_wmt"], 200)
        self.assertEqual(result["by_brand"]["SF"]["confidence_percent"], 50)

    def test_sqlite_roundtrip_keeps_hex_audit_and_advanced_grades_for_chunking(self):
        view = window()
        rows = view.enrich_AMT_grade_streams({}, {"SP1": [raw_hex()]})
        previous = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "recon.db")
            try:
                set_database_path(path)
                OpeningStockpileInventories().save_AMT_to_database(rows)
                reader = chart()
                reader.db_path = path
                stored = reader.fetch_data().iloc[0]
            finally:
                set_database_path(previous)
        self.assertEqual(stored["reconciliation"], rows["SP1"][0]["reconciliation"])
        self.assertEqual(stored["grade_streams"], rows["SP1"][0]["grade_streams"])

    def test_inventory_audit_can_be_persisted_with_existing_opening_table(self):
        _, audit = apply()
        previous = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "inventory.db")
            try:
                set_database_path(path)
                OpeningStockpileInventories().save_to_database({"SP1": {"NAME": "SP1", "BALANCE": 100, "reconciliation": audit}})
                with closing(sqlite3.connect(path)) as conn:
                    saved = conn.execute("SELECT reconciliation_json FROM opening_stockpile_inventories").fetchone()[0]
            finally:
                set_database_path(previous)
        self.assertEqual(json.loads(saved), audit)


if __name__ == "__main__":
    unittest.main()
