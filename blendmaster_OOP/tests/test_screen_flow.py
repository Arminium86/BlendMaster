import sqlite3
import os
import json
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from GUI.InitialiseGUI import UserInputs
from classes.GradeStreams import format_grade_stream_vector
from classes.ExpitSequenceReconciler import grade_block_key
from classes.ExpitDataHandler import ExpitDataHandler
from database.DatabaseContext import get_database_path, set_database_path
from database.SQLiteDatabase import DatabaseManager


class FakeMappingTable:
    def __init__(self):
        self.rows = []

    def setRowCount(self, count):
        self.rows = [[None] * 7 for _ in range(count)]

    def rowCount(self):
        return len(self.rows)

    def setItem(self, row, column, item):
        self.rows[row][column] = item

    def item(self, row, column):
        return self.rows[row][column]


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class FakeLineEdit:
    def __init__(self, value=""):
        self.value = value

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value


class FakeVisibleWidget:
    def __init__(self):
        self.show_count = 0

    def show(self):
        self.show_count += 1


class FakeUrl:
    def __init__(self, empty):
        self.empty = empty

    def isEmpty(self):
        return self.empty


class FakeWebView(FakeVisibleWidget):
    def __init__(self, empty=True):
        super().__init__()
        self.current_url = FakeUrl(empty)
        self.set_urls = []
        self.reload_count = 0

    def url(self):
        return self.current_url

    def setUrl(self, url):
        self.set_urls.append(url.toString())
        self.current_url = FakeUrl(False)

    def reload(self):
        self.reload_count += 1


class ScreenFlowStateTests(unittest.TestCase):
    def test_expit_input_cache_round_trip_is_lossless(self):
        previous_database = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "cache.db")
            set_database_path(database_path)
            try:
                transactions = pd.DataFrame([{
                    "source": "Reserves/CC1/Block_001",
                    "payload": 240.0,
                    "direct_tip_eligible": True,
                    "grade_streams": {"adjusted_product": {"FB": {"fe": 58.1}}},
                    "source_properties": {"modelled_product_wmt": 200.0},
                }])
                transactions.attrs["expit_sequence_audit"] = pd.DataFrame([
                    {"agent": "EX01", "completion_status": "Partial"}
                ])
                transactions.attrs["expit_sequence_summary"] = {
                    "agents": {"EX01": {"remaining": 1}}
                }

                DatabaseManager().write_expit_input_cache(
                    transactions, "exact-signature", {"source": "test"}
                )
                restored, metadata = DatabaseManager().read_expit_input_cache(
                    "exact-signature"
                )

                self.assertTrue(restored.loc[0, "direct_tip_eligible"])
                self.assertEqual(
                    58.1,
                    restored.loc[0, "grade_streams"]["adjusted_product"]["FB"]["fe"],
                )
                self.assertEqual(
                    "Partial",
                    restored.attrs["expit_sequence_audit"].loc[
                        0, "completion_status"
                    ],
                )
                self.assertEqual("test", metadata["source"])
                missed, _ = DatabaseManager().read_expit_input_cache("different")
                self.assertIsNone(missed)
            finally:
                set_database_path(previous_database)

    def test_expit_cache_policy_only_bypasses_live_updated_sequences(self):
        window = SimpleNamespace(time_mode_choice=1, expit_mode_choice=2)
        self.assertFalse(UserInputs.expit_input_cache_allowed(window))
        window.expit_refresh_tolerance_minutes = 30
        self.assertTrue(UserInputs.expit_input_cache_allowed(window))
        window.expit_refresh_tolerance_minutes = 0
        window.time_mode_choice = 2
        self.assertTrue(UserInputs.expit_input_cache_allowed(window))
        window.time_mode_choice = 1
        window.expit_mode_choice = 1
        self.assertTrue(UserInputs.expit_input_cache_allowed(window))

    def test_current_time_expit_cache_reuses_fresh_equivalent_signature(self):
        descriptor, database_path = tempfile.mkstemp(suffix=".db")
        os.close(descriptor)
        previous_database = get_database_path()
        try:
            set_database_path(database_path)
            old_signature = json.dumps({
                "cache_version": 1,
                "start_time": "2026-08-17T10:00:00",
                "site": {"mine": "CC"},
            }, sort_keys=True)
            new_signature = json.dumps({
                "cache_version": 1,
                "start_time": "2026-08-17T10:10:00",
                "site": {"mine": "CC"},
            }, sort_keys=True)
            DatabaseManager().write_expit_input_cache(
                pd.DataFrame([{"source": "GB1"}]), old_signature
            )
            window = SimpleNamespace(
                time_mode_choice=1,
                expit_mode_choice=2,
                expit_refresh_tolerance_minutes=30,
                expit_signatures_match_except_start_time=(
                    UserInputs.expit_signatures_match_except_start_time
                ),
            )

            restored, metadata = (
                UserInputs.read_reusable_expit_input_cache(
                    window, new_signature
                )
            )

            self.assertEqual(restored.iloc[0]["source"], "GB1")
            self.assertEqual(metadata["cache_match"], "fresh_current_time")
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    "UPDATE expit_input_cache "
                    "SET created_at = '2000-01-01 00:00:00'"
                )
                connection.commit()
            finally:
                connection.close()
            stale, _ = UserInputs.read_reusable_expit_input_cache(
                window, old_signature
            )
            self.assertIsNone(stale)
        finally:
            set_database_path(previous_database)
            os.remove(database_path)

    def test_amt_tolerance_reuses_only_timestamp_change_in_now_mode(self):
        cached = json.dumps({
            "cache_version": 1,
            "hub": "CH",
            "mine": "CC",
            "opf": "CC OPF02",
            "start_time": "2026-08-17T10:00:00",
            "selections": [{"footprint": "RP01", "build": "26001"}],
        }, sort_keys=True)
        requested = json.dumps({
            "cache_version": 1,
            "hub": "CH",
            "mine": "CC",
            "opf": "CC OPF02",
            "start_time": "2026-08-17T10:10:00",
            "selections": [{"footprint": "RP01", "build": "26001"}],
        }, sort_keys=True)
        window = SimpleNamespace(
            time_mode_choice=1,
            AMT_refresh_tolerance_minutes=30,
            AMT_last_refresh_datetime=datetime.now(),
            AMT_opening_timestamp_key=UserInputs.AMT_opening_timestamp_key,
            opening_request_signatures_match=(
                lambda left, right: UserInputs.opening_request_signatures_match(
                    left, right
                )
            ),
        )

        reusable, reason = UserInputs.AMT_cached_snapshot_is_reusable(
            window, cached, requested
        )

        self.assertTrue(reusable)
        self.assertIn("fresh", reason)

    def test_amt_cache_reuses_reduced_selection_at_fixed_timestamp(self):
        cached = json.dumps({
            "cache_version": 1,
            "hub": "CH",
            "mine": "CC",
            "opf": "CC OPF02",
            "start_time": "2026-08-17T10:00:00",
            "selections": [
                {"footprint": "RP01", "build": "26001"},
                {"footprint": "RP02", "build": "26002"},
            ],
        }, sort_keys=True)
        requested = json.dumps({
            "cache_version": 1,
            "hub": "CH",
            "mine": "CC",
            "opf": "CC OPF02",
            "start_time": "2026-08-17T10:00:00",
            "selections": [{"footprint": "RP01", "build": "26001"}],
        }, sort_keys=True)
        window = UserInputs.__new__(UserInputs)
        window.time_mode_choice = 2

        reusable, reason = window.AMT_cached_snapshot_is_reusable(
            cached, requested
        )

        self.assertTrue(reusable)
        self.assertEqual("selection subset", reason)

    def test_amt_cache_does_not_reuse_added_or_changed_build(self):
        cached_payload = {
            "cache_version": 1,
            "hub": "CH",
            "mine": "CC",
            "opf": "CC OPF02",
            "start_time": "2026-08-17T10:00:00",
            "selections": [{"footprint": "RP01", "build": "26001"}],
        }
        window = UserInputs.__new__(UserInputs)
        window.time_mode_choice = 2
        requests = [
            [
                {"footprint": "RP01", "build": "26001"},
                {"footprint": "RP02", "build": "26002"},
            ],
            [{"footprint": "RP01", "build": "26002"}],
        ]

        for selections in requests:
            requested_payload = dict(cached_payload, selections=selections)
            with self.subTest(selections=selections):
                reusable, reason = window.AMT_cached_snapshot_is_reusable(
                    json.dumps(cached_payload, sort_keys=True),
                    json.dumps(requested_payload, sort_keys=True),
                )
                self.assertFalse(reusable)
                self.assertEqual("", reason)

    def test_amt_snapshot_filter_removes_deselected_footprints(self):
        snapshot = {
            "RP01": [{"HEX": "A"}],
            "rp02": [{"HEX": "B"}],
        }

        filtered = UserInputs.AMT_snapshot_for_selected_footprints(
            snapshot,
            {"RP01": {"build": "26001", "amt": True}},
        )

        self.assertEqual({"RP01": [{"HEX": "A"}]}, filtered)

    def test_saved_2wp_guidance_is_reused_for_matching_inputs(self):
        selected_time = datetime(2026, 8, 1, 6, 0)
        with tempfile.NamedTemporaryFile(suffix=".csv") as two_wp:
            window = UserInputs.__new__(UserInputs)
            window.file_path_choice = two_wp.name
            window.start_time_choice = selected_time
            window.mine_input_choice = "CC"
            window.opf_input_choice = "CC OPF02"
            window.crusher_input_choice = "PC02"
            window.selected_two_wp_product_crusher_names = lambda: ["OPF02"]
            window.product_brand_options = lambda: ["FB", "SS"]
            window.aps_stockpile_brand_map = {"RP01": {"primary_brand": "FB"}}
            window.aps_stockpile_timing_guidance = {"RP01": {"first_reclaim": selected_time}}
            window.aps_active_blend_guidance = [{"brand": "FB"}]
            window.aps_destination_guidance = {
                "matching_version": ExpitDataHandler.DESTINATION_GUIDANCE_VERSION
            }
            window.aps_brand_guidance_cache = {}
            window.aps_guidance_request_signature = (
                UserInputs.aps_guidance_input_signature(window)
            )

            with patch.object(
                ExpitDataHandler, "get_2wp_schedule_guidance"
            ) as guidance_query:
                UserInputs.refresh_aps_stockpile_brand_map(window)

            guidance_query.assert_not_called()
            self.assertIn("RP01", window.aps_stockpile_brand_map)

    def test_current_time_project_choice_refreshes_only_saved_now_scenarios(self):
        saved_time = datetime(2026, 8, 1, 6, 0)
        current_time = datetime(2026, 8, 14, 9, 30)
        state = {
            "time_mode_choice": 1,
            "start_time_choice": saved_time,
            "inventory_data_request_signature": "saved-root",
            "site_scenarios": {
                "now": {
                    "time_mode_choice": 1,
                    "start_time_choice": saved_time,
                    "inventory_data_request_signature": "saved-now",
                    "AMT_data_request_signature": "saved-amt",
                    "data_stream_input_cache_result": {"saved": True},
                },
                "fixed": {
                    "time_mode_choice": 2,
                    "start_time_choice": datetime(2026, 8, 2, 6, 0),
                    "inventory_data_request_signature": "saved-fixed",
                },
            },
        }

        count = UserInputs.apply_loaded_project_now_time_choice(
            state, use_current_time=True, current_time=current_time
        )

        self.assertEqual(count, 2)
        self.assertEqual(state["start_time_choice"], current_time)
        self.assertEqual(state["time_mode_choice"], 1)
        self.assertEqual(state["inventory_data_request_signature"], "")
        self.assertTrue(state["project_load_refresh_current_time"])
        self.assertEqual(
            state["site_scenarios"]["now"]["start_time_choice"],
            current_time,
        )
        self.assertEqual(
            state["site_scenarios"]["now"]["data_stream_input_cache_result"],
            {},
        )
        self.assertEqual(
            state["site_scenarios"]["fixed"]["start_time_choice"],
            datetime(2026, 8, 2, 6, 0),
        )
        self.assertEqual(
            state["site_scenarios"]["fixed"]["inventory_data_request_signature"],
            "saved-fixed",
        )

    def test_saved_timestamp_project_choice_converts_saved_now_to_set_time(self):
        saved_time = datetime(2026, 8, 1, 6, 0)
        state = {
            "time_mode_choice": 1,
            "start_time_choice": saved_time,
            "inventory_data_request_signature": "saved",
        }

        count = UserInputs.apply_loaded_project_now_time_choice(
            state, use_current_time=False
        )

        self.assertEqual(count, 1)
        self.assertEqual(state["time_mode_choice"], 2)
        self.assertEqual(state["start_time_choice"], saved_time)
        self.assertEqual(state["inventory_data_request_signature"], "saved")
        self.assertNotIn("project_load_refresh_current_time", state)

    def test_loaded_project_now_states_ignores_legacy_mode_without_evidence(self):
        state = {"start_time_choice": datetime(2026, 8, 1, 6, 0)}

        self.assertEqual(UserInputs.loaded_project_now_states(state), [])

    def test_loaded_project_now_states_detects_nested_saved_now_scenario(self):
        state = {
            "time_mode_choice": 2,
            "start_time_choice": datetime(2026, 8, 1, 6, 0),
            "site_scenarios": {
                "saved_now": {
                    "time_mode_choice": 1,
                    "start_time_choice": datetime(2026, 8, 1, 7, 0),
                },
            },
        }

        records = UserInputs.loaded_project_now_states(state)

        self.assertEqual(len(records), 1)
        self.assertIs(records[0], state["site_scenarios"]["saved_now"])

    def test_project_normalization_preserves_explicit_now_mode(self):
        window = SimpleNamespace()
        window.default_product_brand_labels = lambda: ["FB"]
        window.parse_product_brand_labels = lambda value: list(value or [])
        window.default_opf_for_site = lambda mine, crusher: "OPF01"
        window.normalized_crusher_ratio_mode = lambda value: "manual"
        window.normalized_crusher_ratio = lambda value, default=1.0: default
        window.normalized_aps_crusher_choice = lambda value: list(value or [])
        window.normalized_expit_agent_names = lambda value: list(value or [])
        window.normalized_solver_config = lambda value: dict(value or {})
        window.parse_agent_datetime_value = lambda value: value
        state = {
            "time_mode_choice": 1,
            "start_time_choice": datetime(2026, 8, 1, 6, 0),
            "product_brand_labels_choice": ["FB"],
        }

        normalized = UserInputs.normalized_agent_project_state(window, state)

        self.assertEqual(normalized["time_mode_choice"], 1)
        self.assertEqual(
            normalized["start_time_choice"], datetime(2026, 8, 1, 6, 0)
        )

    def test_guidance_submission_populates_inventory_before_navigating(self):
        window = UserInputs.__new__(UserInputs)
        window.stockpile_data = {"SP_A": {}}
        window.haul_cycle_file_path_choice = ""
        window.stockpile_data_use_column = {"STALE": False}
        window.stockpile_data_AMT_column = {"STALE": False}
        window.stockpile_tab_index = "stockpile_inventories"
        events = []
        window.capture_guidance_schedule_controls = lambda: events.append("capture")
        window.validate_guidance_schedule_constraints = lambda: (True, "")
        window.refresh_aps_stockpile_brand_map = lambda: events.append("brands")
        window.apply_aps_brand_guidance_to_stockpile_data = lambda: events.append("apply_brands")
        window.apply_haul_cycle_routes_to_stockpile_data = lambda: events.append("routes")
        window.setup_stockpile_table = lambda: events.append((
            "table",
            dict(window.stockpile_data_use_column),
            dict(window.stockpile_data_AMT_column),
        ))
        window.save_active_scenario_state = lambda: events.append("save_state")
        window.set_page_enabled = lambda page, enabled: events.append(("enable", page, enabled))
        window.show_page = lambda page, force=False: events.append(("show", page, force))
        window.validate_form = lambda: events.append("validate")

        UserInputs.handle_guidance_schedules_submit(window)

        table_event = ("table", {"STALE": False}, {"STALE": False})
        self.assertLess(events.index("apply_brands"), events.index(table_event))
        self.assertLess(events.index("routes"), events.index(table_event))
        self.assertIn(table_event, events)
        self.assertIn(("enable", "stockpile_inventories", True), events)
        self.assertIn(("show", "stockpile_inventories", True), events)

    def test_data_stream_submission_does_not_rewrite_inventory_table(self):
        window = UserInputs.__new__(UserInputs)
        selector = lambda value: SimpleNamespace(currentData=lambda: value)
        text = lambda value: SimpleNamespace(text=lambda: value)
        window.data_stream_selector = selector("adjusted_product")
        window.crusher_tonnes_selector = selector("modelled_rom_wmt")
        window.reclaimer_tonnes_selector = selector("modelled_rom_wmt")
        window.product_build_tonnes_selector = selector("modelled_product_wmt")
        window.rom_planning_category_input = text("OPF Feed")
        window.product_planning_category_input = text("OPF Production")
        window.capture_cb_lump_fines_settings = Mock()
        window.capture_byproduct_build_settings = Mock()
        window.capture_recon_factor_table = Mock()
        window.reconcile_saved_AMT_chunk_grade_streams = Mock()
        window.apply_canonical_field_mappings = Mock()
        window.apply_grade_streams_to_inventory = Mock()
        window.refresh_AMT_enrichment_if_needed = Mock()
        window.data_stream_pending_build_targets = {}
        window.data_stream_reconciliation = SimpleNamespace(save_to_database=Mock())
        window.opf_input_choice = "OPF01"
        window.start_time_choice = datetime(2026, 8, 9)
        window.historical_recon_factors = {}
        window.historical_recon_warnings = []
        window.save_active_scenario_state = Mock()
        window.set_page_enabled = Mock()
        window.stockpile_tab_index = "stockpile_inventories"
        window.define_fields_tab_index = "define_fields"
        window.map_fields_tab_index = "map_fields"
        window.data_streams_tab_index = "data_streams"
        window.guidance_schedules_tab_index = "guidance_schedules"
        window.stockpile_data_AMT_column = {}
        window.hex_sequence_table = [1]
        window.hex_sequence_table_argument = [1]
        window.open_database_view = Mock()
        window.opening_stockpile_inventories = SimpleNamespace(
            save_to_database=Mock()
        )

        UserInputs.finish_data_stream_submission(window)

        window.opening_stockpile_inventories.save_to_database.assert_not_called()
        window.open_database_view.assert_called_once_with(navigate=True)

    def test_field_definition_exchange_round_trip_preserves_contract(self):
        definitions = [{
            "name": "custom_tonnes",
            "kind": "additive",
            "weight_field": "",
            "required": False,
            "use_in_optimisation": True,
        }]

        payload = UserInputs.field_definition_exchange_payload(definitions)
        restored = UserInputs.field_definitions_from_exchange_payload(payload)

        self.assertEqual(payload["format"], "blendmaster_field_definitions")
        self.assertIn("custom_tonnes", {row["name"] for row in restored})
        self.assertTrue(next(
            row for row in restored if row["name"] == "custom_tonnes"
        )["use_in_optimisation"])

    def test_field_mapping_exchange_round_trip_preserves_all_contexts(self):
        definitions = [{
            "name": "custom_tonnes",
            "kind": "additive",
            "weight_field": "",
            "required": False,
            "use_in_optimisation": True,
        }]
        mappings = [
            {
                "source_family": "inventory",
                "brand": "",
                "target_field": "custom_tonnes",
                "source_field": "CUSTOM_WMT",
            },
            {
                "source_family": "aps",
                "brand": "FB",
                "target_field": "custom_tonnes",
                "source_field": "Mining.Custom.FB.WMT",
            },
        ]

        payload = UserInputs.field_mapping_exchange_payload(mappings)
        restored = UserInputs.field_mappings_from_exchange_payload(
            payload, definitions
        )

        self.assertEqual(payload["format"], "blendmaster_field_mappings")
        self.assertEqual(restored, mappings)

    def test_mapping_import_rejects_targets_missing_from_define_fields(self):
        payload = UserInputs.field_mapping_exchange_payload([{
            "source_family": "amt",
            "brand": "",
            "target_field": "missing_tonnes",
            "source_field": "MISSING_WMT",
        }])

        with self.assertRaisesRegex(ValueError, "Import its field definition first"):
            UserInputs.field_mappings_from_exchange_payload(payload, [])

    def test_project_restore_does_not_capture_stale_field_widgets(self):
        window = SimpleNamespace()
        window.project_load_restore_in_progress = True
        window.define_fields_table = object()
        window.field_mapping_table = object()
        window.data_stream_selector = object()
        window.active_scenario_id = "site_test"
        window.field_definitions = [{
            "name": "saved_field",
            "kind": "additive",
            "weight_field": "",
            "required": False,
            "use_in_optimisation": True,
        }]
        window.field_mappings = [{
            "source_family": "inventory",
            "brand": "",
            "target_field": "saved_field",
            "source_field": "SAVED_RAW_FIELD",
        }]
        window.selected_data_stream = "adjusted_product"
        window.data_stream_planning_categories = {
            "rom": "Saved ROM",
            "product": "Saved Product",
        }
        window.calendar_inputs = {
            "crusher_rate": {"Preplan": 6000.0},
            "crusher_direct_tip_ratio_min": {"Preplan": 0.25},
        }
        captured = []
        window.capture_stockpile_table_choices = lambda: None
        window.capture_active_manual_plan_state = lambda: None
        window.capture_define_fields_table = (
            lambda: captured.append("definitions")
        )
        window.capture_map_fields_table = (
            lambda: captured.append("mappings")
        )
        window.capture_calendar_table_inputs = lambda: {}
        window.scenario_database_path = lambda _scenario_id: "scenario.db"
        window.capture_table_snapshot = UserInputs.capture_table_snapshot

        state = UserInputs.capture_scenario_state(window)

        self.assertEqual(captured, [])
        self.assertEqual(state["field_definitions"], window.field_definitions)
        self.assertEqual(state["field_mappings"], window.field_mappings)
        self.assertEqual(state["selected_data_stream"], "adjusted_product")
        self.assertEqual(
            state["data_stream_planning_categories"],
            {"rom": "Saved ROM", "product": "Saved Product"},
        )
        self.assertEqual(state["calendar_inputs"], window.calendar_inputs)

    def test_current_time_inventory_refresh_preserves_calendar_configuration(self):
        window = SimpleNamespace()
        window.calendar_inputs = {
            "crusher_rate": {"Preplan": 6000.0, "Period_1": 5500.0},
            "crusher_target_fe_min": {"Preplan": 57.5, "Period_1": 58.0},
            "crusher_direct_tip_ratio_min": {
                "Preplan": 0.1,
                "Period_1": 0.0,
            },
            "min_stockpiles": 2,
            "max_stockpiles": 4,
            "min_stockpile_contribution_ratio": 0.15,
            "solver_config": {"throughput_incentive": 1000000.0},
        }
        window.solver_config = {"throughput_incentive": 1000000.0}
        window.min_stockpiles = 2
        window.max_stockpiles = 4
        window.min_stockpile_contribution_ratio = 0.15
        window.normalized_solver_config = lambda value: dict(value or {})
        window.restore_table_snapshot = lambda *_args, **_kwargs: None

        UserInputs.reset_downstream_inputs_for_new_site_configuration(
            window, preserve_calendar=True
        )

        self.assertEqual(window.calendar_inputs["crusher_rate"]["Preplan"], 6000.0)
        self.assertEqual(
            window.calendar_inputs["crusher_direct_tip_ratio_min"]["Preplan"],
            0.1,
        )
        self.assertEqual(window.min_stockpiles, 2)
        self.assertEqual(window.max_stockpiles, 4)
        self.assertEqual(window.min_stockpile_contribution_ratio, 0.15)
        self.assertEqual(window.solver_config["throughput_incentive"], 1000000.0)
        self.assertTrue(window.calendar_table_refresh_pending)

    def test_pending_calendar_restore_ignores_stale_rendered_table(self):
        saved = {"crusher_rate": {"Preplan": 6000.0}}
        window = SimpleNamespace(
            calendar_inputs=saved,
            calendar_table_refresh_pending=True,
            main_table=object(),
            calendar_headers=["", "Preplan"],
        )

        captured = UserInputs.capture_calendar_table_inputs(window)

        self.assertEqual(captured, saved)

    def test_expit_route_collapses_sibling_slice_chevrons(self):
        points = [
            {"key": "A", "source": "A_001", "x": 0.0, "y": 0.0},
            {"key": "A", "source": "A_002", "x": 0.1, "y": 0.1},
            {"key": "A", "source": "A_003", "x": -0.1, "y": 0.1},
            {"key": "B", "source": "B_001", "x": 1.0, "y": 0.0},
            {"key": "A", "source": "A_004", "x": 0.0, "y": -0.1},
        ]

        route = UserInputs.expit_parent_transition_route_points(points)

        self.assertEqual([point["key"] for point in route], ["A", "B", "A"])
        self.assertEqual(route[0]["source"], "A_003")

    def test_feed_source_fields_use_insitu_rom_labels_in_the_ui(self):
        self.assertEqual(
            "Insitu / ROM WMT (feed_wmt)",
            UserInputs.user_facing_field_label("feed_wmt"),
        )
        self.assertEqual(
            "Insitu / ROM DMT (feed_dmt)",
            UserInputs.user_facing_field_label("feed_dmt"),
        )

    def test_snapshot_columns_hide_semantic_duplicates_and_keep_raw_labels(self):
        report = pd.DataFrame(columns=[
            "source_actual_tonnes",
            "source_property_source_wmt",
            "source_property_modelled_rom_wmt",
            "source_property_modelled_rom_wmt_opening_balance",
            "source_property_modelled_rom_wmt_closing_balance",
            "source_property_modelled_product_wmt",
            "source_property_modelled_product_wmt_actual_depletion",
            "source_property_modelled_product_wmt_opening_balance",
            "custom_constraint_yield_source_numerator",
            "custom_constraint_yield_source_numerator_coefficient",
            "custom_constraint_yield_source_numerator_contribution",
        ])

        columns = UserInputs.optimisation_snapshot_available_columns(report)

        self.assertNotIn("source_property_source_wmt", columns)
        self.assertIn("source_property_modelled_rom_wmt", columns)
        self.assertIn(
            "source_property_modelled_rom_wmt_opening_balance", columns
        )
        self.assertIn(
            "source_property_modelled_rom_wmt_closing_balance", columns
        )
        self.assertNotIn(
            "source_property_modelled_product_wmt_actual_depletion", columns
        )
        self.assertNotIn(
            "custom_constraint_yield_source_numerator", columns
        )
        self.assertIn("source_property_modelled_product_wmt", columns)
        self.assertIn(
            "custom_constraint_yield_source_numerator_coefficient", columns
        )
        self.assertEqual(
            UserInputs.optimisation_snapshot_column_label(
                None, "custom_constraint_yield_numerator"
            ),
            "custom_constraint_yield_numerator",
        )

    def test_amt_stockpile_stream_display_aggregates_all_positive_hexes(self):
        rows = [
            {
                "FINAL_WMT": 100.0,
                "GRADE_STREAMS": {"modelled_rom": {"*": {"fe": None}}},
            },
            {
                "FINAL_WMT": 300.0,
                "GRADE_STREAMS": {
                    "modelled_rom": {"*": {"fe": 58.0, "si": 4.25}}
                },
            },
        ]

        streams = UserInputs.aggregate_AMT_footprint_grade_streams(rows)

        self.assertEqual(streams["modelled_rom"]["*"]["fe"], 58.0)
        self.assertEqual(streams["modelled_rom"]["*"]["si"], 4.25)

    def test_database_view_formats_sums_and_weighted_averages(self):
        self.assertEqual(
            UserInputs.database_view_display_value("tonnes", 149801.55),
            "149,802",
        )
        self.assertEqual(
            UserInputs.database_view_display_value("lineage_unmatched_wmt", 18.7),
            "19",
        )
        self.assertEqual(
            UserInputs.database_view_display_value("modelled_prod1_fe", 58.126),
            "58.13",
        )
        self.assertEqual(
            UserInputs.database_view_display_value("lineage_coverage_pct", 97.456),
            "97.46",
        )
        self.assertEqual(
            UserInputs.database_view_display_value("fines_wmt", 1000.6),
            "1,001",
        )
        self.assertEqual(
            UserInputs.database_view_display_value("minus_1mm_pct", 12.345),
            "12.35",
        )

    def test_database_view_amt_properties_use_one_canonical_name(self):
        fields = UserInputs.database_view_modelled_property_audit(
            {
                "modelled_product_fe": 58.25,
                "adjusted_rom_si": 4.5,
                "oretype_bid_wmt": 1200.0,
                "prod1_minus_1mm_wmt": 175.0,
            },
            {
                "modelled_product_fe": 0.9,
                "oretype_bid_wmt": 0.75,
            },
        )

        self.assertEqual(fields["grade_modelled_product_fe"], 58.25)
        self.assertEqual(fields["grade_adjusted_rom_si"], 4.5)
        self.assertEqual(fields["oretype_bid_wmt"], 1200.0)
        self.assertEqual(fields["prod1_minus_1mm_wmt"], 175.0)
        self.assertEqual(
            fields["grade_modelled_product_fe_coverage_pct"], 90.0
        )
        self.assertNotIn("modelled_modelled_product_fe", fields)
        self.assertNotIn("modelled_oretype_bid_wmt", fields)

    def test_database_view_coverage_headers_are_hidden_by_default(self):
        window = UserInputs.__new__(UserInputs)
        window.database_view_rows = [{
            "source_type": "AMT Chunk",
            "source_id": "SP01_CHUNK_001",
            "oretype_bid_wmt": 100.0,
            "oretype_bid_wmt_coverage_pct": 80.0,
            "lineage_coverage_pct": 95.0,
            "source_wmt": 100.0,
            "tonnes": 100.0,
            "feed_wmt": 100.0,
            "internal_recon_matched": True,
            "latitude": -22.0,
        }]
        window.database_view_show_coverage_fields = False

        hidden_headers = window.database_view_all_headers()
        visible_headers = window.database_view_all_headers(
            include_coverage=True
        )

        self.assertIn("oretype_bid_wmt", hidden_headers)
        self.assertNotIn("oretype_bid_wmt_coverage_pct", hidden_headers)
        self.assertNotIn("lineage_coverage_pct", hidden_headers)
        self.assertIn("oretype_bid_wmt_coverage_pct", visible_headers)
        self.assertIn("lineage_coverage_pct", visible_headers)
        for raw_only in (
            "source_wmt", "tonnes", "feed_wmt", "internal_recon_matched",
            "latitude",
        ):
            self.assertNotIn(raw_only, visible_headers)

    def test_optimised_snapshot_formats_additive_and_weighted_fields(self):
        window = UserInputs.__new__(UserInputs)
        window.source_property_kinds = {}

        self.assertEqual(
            window.format_optimisation_snapshot_value(
                "source_actual_tonnes", 12345.67
            ),
            "12,346",
        )
        self.assertEqual(
            window.format_optimisation_snapshot_value(
                "source_grade_fe", 55.2861
            ),
            "55.29",
        )
        self.assertEqual(
            window.format_optimisation_snapshot_value(
                "custom_constraint_test_actual_ratio", 0.45678
            ),
            "0.46",
        )
        self.assertEqual(
            UserInputs.default_optimisation_snapshot_columns(
                ["source", "source_actual_tonnes", "source_grade_fe"]
            ),
            ["source", "source_actual_tonnes", "source_grade_fe"],
        )

    def test_optimised_snapshot_accepts_pandas_column_index_on_project_load(self):
        window = UserInputs.__new__(UserInputs)
        window.optimisation_snapshot_selected_columns = None
        window.optimisation_snapshot_known_columns = None
        columns = window.optimisation_snapshot_columns(pd.DataFrame({
            "source": ["SP01"],
            "source_actual_tonnes": [1200.0],
            "source_grade_fe": [55.0],
        }))

        self.assertEqual(
            columns,
            ["source", "source_actual_tonnes", "source_grade_fe"],
        )

    def test_database_view_source_descriptors_have_stable_unique_columns(self):
        window = UserInputs.__new__(UserInputs)
        window.database_view_rows = [
            {
                "source_type": "AMT Chunk",
                "source_id": "CHUNK_001",
                "parent_stockpile": "SP01",
                "build_or_chunk": "CHUNK_001",
                "sequence": 1,
            },
            {
                "source_type": "AMT Chunk",
                "source_id": "CHUNK_001",
                "parent_stockpile": "SP02",
                "build_or_chunk": "CHUNK_001",
                "sequence": 1,
            },
        ]

        first = window.database_view_source_descriptors()
        second = window.database_view_source_descriptors()

        self.assertEqual(
            [descriptor["key"] for descriptor in first],
            [descriptor["key"] for descriptor in second],
        )
        self.assertEqual(len({descriptor["key"] for descriptor in first}), 2)
        self.assertEqual(len({descriptor["label"] for descriptor in first}), 2)
        self.assertIn("SP01", first[0]["label"])
        self.assertIn("SP02", first[1]["label"])

    def test_database_view_source_selection_keeps_hidden_and_adds_new(self):
        window = UserInputs.__new__(UserInputs)
        window.database_view_rows = [
            {"source_type": "Inventory Stockpile", "source_id": "SP01"},
            {"source_type": "Inventory Stockpile", "source_id": "SP02"},
        ]
        window.database_view_selected_sources = None
        window.database_view_known_sources = None

        initial = window.database_view_selected_source_descriptors()
        first_key = initial[0]["key"]
        window.database_view_selected_sources = [first_key]
        window.database_view_rows.append({
            "source_type": "APS Grade Block",
            "source_id": "GB01",
        })

        selected = window.database_view_selected_source_descriptors()

        self.assertEqual(
            [descriptor["label"] for descriptor in selected],
            ["SP01", "GB01"],
        )
        self.assertNotIn(
            "SP02", [descriptor["label"] for descriptor in selected]
        )

    def test_tonnes_render_as_whole_separated_values_and_grades_as_two_decimals(self):
        window = UserInputs.__new__(UserInputs)

        self.assertEqual(
            window.format_table_display_value(12345.6, "Source Tonnes"),
            "12,346",
        )
        self.assertEqual(
            window.format_table_display_value("12345.6", "PROD1 WMT"),
            "12,346",
        )
        self.assertEqual(
            window.format_table_display_value(12345.6, "Closing Balance"),
            "12,346",
        )
        self.assertEqual(
            window.parse_formatted_number("12,346"),
            12346.0,
        )
        self.assertEqual(
            window.format_table_display_value("58", "Grade Fe (%)"),
            "58.00",
        )
        self.assertIn(
            "Fe 58.12",
            format_grade_stream_vector(
                {"adjusted_product": {"FB": {"fe": 58.12345}}},
                "adjusted_product",
                "FB",
            ),
        )

    def test_legacy_zero_haulage_cost_uses_new_default(self):
        window = UserInputs.__new__(UserInputs)
        window.solver_config = {
            "haulage_cost_per_hour": 0.0,
            "rehandle_cycle_time_penalty_enabled": False,
        }

        normalized = window.normalized_solver_config()

        self.assertEqual(normalized["haulage_cost_per_hour"], 5.0)

    def test_aps_header_browser_reads_distinct_csv_fields_in_file_order(self):
        descriptor, path = tempfile.mkstemp(suffix=".csv")
        os.close(descriptor)
        try:
            with open(path, "w", encoding="utf-8") as csv_file:
                csv_file.write("Field.A,Field.B,Field.A,Mining.grades_fe\n")

            headers = UserInputs.distinct_aps_csv_headers(path)

            self.assertEqual(
                headers,
                ["Field.A", "Field.B", "Mining.grades_fe"],
            )
        finally:
            os.remove(path)

    def test_loaded_aps_grade_field_mappings_repopulate_and_round_trip(self):
        window = UserInputs.__new__(UserInputs)
        window.product_brand_labels_choice = ["FB", "SS"]
        window.aps_grade_field_mappings = {
            "rom": {
                "FB": {"fe": "fb_rom_fe"},
                "SS": {"fe": "ss_rom_fe"},
            },
            "product": {
                "FB": {"fe": "fb_prod_fe"},
                "SS": {"fe": "ss_prod_fe"},
            },
        }
        window.aps_grade_mapping_table = FakeMappingTable()

        window.populate_aps_grade_mapping_table()

        self.assertEqual(
            window.aps_grade_mapping_table.item(0, 2).text(), "fb_rom_fe"
        )
        self.assertEqual(
            window.aps_grade_mapping_table.item(3, 2).text(), "ss_prod_fe"
        )
        window.aps_grade_field_mappings = {"rom": {}, "product": {}}
        window.capture_aps_grade_mapping_table()
        self.assertEqual(
            window.aps_grade_field_mappings["rom"]["SS"]["fe"],
            "ss_rom_fe",
        )
        self.assertEqual(
            window.aps_grade_field_mappings["product"]["FB"]["fe"],
            "fb_prod_fe",
        )

    def test_missing_aps_mappings_are_summarised_by_brand_and_stream(self):
        window = UserInputs.__new__(UserInputs)
        window.file_path_24hr_choice = "Mining.csv"
        window.product_brand_labels_choice = ["FB"]
        window.aps_grade_field_mappings = {
            "rom": {"FB": {"fe": "ROM_Fe"}},
            "product": {"FB": {}},
        }

        warnings = window.aps_grade_mapping_warnings()

        self.assertEqual(2, len(warnings))
        self.assertIn("FB ROM is missing si, al, p, mn", warnings[0])
        self.assertIn("FB Product is missing fe, si, al, p, mn", warnings[1])

    def test_amt_headers_are_available_to_async_and_restore_completion(self):
        window = UserInputs.__new__(UserInputs)
        window.product_brand_labels_choice = ["FB", "SS"]

        headers = window.amt_stockpile_headers()

        self.assertIn("Inventory Match", headers)
        self.assertIn("AMT Total WMT", headers)
        self.assertIn("Inventory Stockpile Total WMT", headers)
        self.assertIn("Calculated Number of Chunks", headers)
        self.assertIn("Adjusted Product Grades (FB)", headers)
        self.assertIn("Adjusted Product Grades (SS)", headers)

    def test_new_amt_chunk_inputs_default_to_2000_tph_and_72_hours(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_chunk_settings = {}

        setting = window.get_AMT_chunk_setting("SP1")

        self.assertEqual(setting["average_reclaim_rate"], 2000.0)
        self.assertEqual(setting["chunk_reclaim_hours"], 72.0)

    def test_existing_amt_map_panel_is_recovered_during_project_load(self):
        window = UserInputs.__new__(UserInputs)
        window.setup_AMT_stockpile_table_first_call = False
        window._amt_chunk_cell_change_connected = False
        window.AMT_stockpile_table = SimpleNamespace(cellChanged=FakeSignal())
        window.AMT_map_view = FakeWebView()
        window.AMT_map_frame = FakeVisibleWidget()
        window.AMT_stockpile_tab_vertical_layout = object()
        window.AMT_cache_status_label = FakeVisibleWidget()
        window.refresh_AMT_data_button = FakeVisibleWidget()
        window.load_AMT_button = FakeVisibleWidget()
        window.submit_AMT_button = FakeVisibleWidget()

        window.ensure_AMT_map_panel()

        self.assertEqual(window.AMT_map_frame.show_count, 1)
        self.assertEqual(window.AMT_map_view.show_count, 1)
        self.assertEqual(window.refresh_AMT_data_button.show_count, 1)
        self.assertEqual(window.load_AMT_button.show_count, 1)
        self.assertEqual(window.submit_AMT_button.show_count, 1)
        self.assertEqual(
            len(window.AMT_stockpile_table.cellChanged.callbacks), 1
        )

    def test_amt_opening_cache_signature_tracks_only_snowflake_inputs(self):
        window = UserInputs.__new__(UserInputs)
        window.hub_input_choice = "Chichester Hub"
        window.mine_input_choice = "CB"
        window.opf_input_choice = "CB OPF"
        window.start_time_choice = datetime(2026, 8, 7, 6, 0)
        data_source = {
            "SP_B": {"build": "SP_B_26002", "amt": True},
            "SP_A": {"build": "SP_A_26001", "amt": True},
        }

        first = window.AMT_opening_request_signature(data_source)
        reordered = window.AMT_opening_request_signature(dict(
            reversed(list(data_source.items()))
        ))
        window.start_time_choice = datetime(2026, 8, 7, 7, 0)
        changed_time = window.AMT_opening_request_signature(data_source)

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, changed_time)

    def test_opening_cache_signature_ignores_saved_microsecond_noise(self):
        window = UserInputs.__new__(UserInputs)
        window.hub_input_choice = "Chichester Hub"
        window.mine_input_choice = "CC"
        window.opf_input_choice = "CC OPF02"
        window.start_time_choice = datetime(2026, 8, 14, 2, 7)
        data_source = {
            "SP_A": {"build": "SP_A_26001", "amt": True},
        }
        current_signature = window.AMT_opening_request_signature(data_source)
        saved_payload = json.loads(current_signature)
        saved_payload["start_time"] = "2026-08-14T02:07:00.581442"
        saved_signature = json.dumps(
            saved_payload, sort_keys=True, separators=(",", ":")
        )

        self.assertTrue(window.opening_request_signatures_match(
            saved_signature, current_signature
        ))

        saved_payload["start_time"] = "2026-08-14T02:07:01.581442"
        changed_signature = json.dumps(
            saved_payload, sort_keys=True, separators=(",", ":")
        )
        self.assertFalse(window.opening_request_signatures_match(
            changed_signature, current_signature
        ))

    def test_inventory_opening_signature_changes_with_loaded_project_time(self):
        window = UserInputs.__new__(UserInputs)
        window.hub_input_choice = "Chichester Hub"
        window.mine_input_choice = "CC"
        window.opf_input_choice = "OPF02"
        window.crusher_input_choice = "PC02"
        window.start_time_choice = datetime(2026, 8, 9, 6, 0)

        loaded_snapshot = window.inventory_opening_request_signature()
        window.start_time_choice = datetime(2026, 8, 7, 6, 0)
        earlier_snapshot = window.inventory_opening_request_signature()

        self.assertNotEqual(loaded_snapshot, earlier_snapshot)

    def test_cloudbreak_byproduct_checkbox_and_explicit_fields_are_revealed(self):
        class ToggleWidget:
            def __init__(self, checked=False):
                self.checked = checked
                self.visible = None
                self.enabled = None

            def isChecked(self):
                return self.checked

            def setVisible(self, value):
                self.visible = bool(value)

            def setEnabled(self, value):
                self.enabled = bool(value)

        window = UserInputs.__new__(UserInputs)
        window.mine_input_choice = "CB"
        window.byproducts_enabled_label = ToggleWidget()
        window.byproducts_enabled_checkbox = ToggleWidget(checked=True)
        window.byproduct_build_rows = [ToggleWidget() for _ in range(12)]
        window.product_build_tonnes_selector = ToggleWidget()

        window.update_byproduct_build_controls()

        self.assertTrue(window.byproducts_enabled_label.visible)
        self.assertTrue(window.byproducts_enabled_checkbox.visible)
        self.assertTrue(window.byproducts_enabled_checkbox.enabled)
        self.assertTrue(all(widget.visible for widget in window.byproduct_build_rows))
        self.assertTrue(all(widget.enabled for widget in window.byproduct_build_rows))
        self.assertFalse(window.product_build_tonnes_selector.enabled)

    def test_loaded_byproduct_settings_restore_checkbox_and_custom_fields(self):
        class Checkbox:
            def __init__(self):
                self.checked = False

            def setChecked(self, value):
                self.checked = bool(value)

        class Selector:
            def __init__(self):
                self.items = []
                self.current = ""

            def findText(self, value, _flags):
                try:
                    return self.items.index(value)
                except ValueError:
                    return -1

            def addItem(self, value):
                self.items.append(value)

            def setCurrentText(self, value):
                self.current = value

        window = UserInputs.__new__(UserInputs)
        window.byproducts_enabled = True
        window.byproduct_quantity_fields = {
            "lump": "loaded_lump_wmt",
            "fines": "loaded_fines_wmt",
        }
        window.byproduct_grade_fields = {
            lane: {
                analyte: f"loaded_{lane}_{analyte}"
                for analyte in ("fe", "si", "al", "p", "mn")
            }
            for lane in ("lump", "fines")
        }
        window.byproducts_enabled_checkbox = Checkbox()
        window.byproduct_quantity_selectors = {
            lane: Selector() for lane in ("lump", "fines")
        }
        window.byproduct_grade_selectors = {
            lane: {
                analyte: Selector()
                for analyte in ("fe", "si", "al", "p", "mn")
            }
            for lane in ("lump", "fines")
        }
        window.update_byproduct_build_controls = lambda: setattr(
            window, "byproduct_controls_updated", True
        )

        window.load_byproduct_build_settings()

        self.assertTrue(window.byproducts_enabled_checkbox.checked)
        self.assertEqual(
            "loaded_lump_wmt",
            window.byproduct_quantity_selectors["lump"].current,
        )
        self.assertEqual(
            "loaded_fines_al",
            window.byproduct_grade_selectors["fines"]["al"].current,
        )
        self.assertTrue(window.byproduct_controls_updated)

    def test_compatible_amt_cache_check_does_not_write_database(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_stockpile_data = {
            "SP_A": [{"GRADE_BLOCK_LINEAGE_JSON": "[]"}]
        }

        self.assertTrue(window.has_compatible_AMT_data({"SP_A": {}}))

    def test_compatible_amt_cache_accepts_lowercase_sqlite_lineage_columns(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_stockpile_data = {
            "SP_A": [{
                "grade_block_lineage_json": "[]",
                "lineage_entry_count": 0,
            }]
        }

        self.assertTrue(window.has_compatible_AMT_data({"sp_a": {}}))
        self.assertEqual("", window.AMT_data_compatibility_issue({"SP_A": {}}))

    def test_amt_cache_compatibility_reports_missing_lineage(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_stockpile_data = {"SP_A": [{"final_wmt": 100.0}]}

        self.assertEqual(
            "cached AMT rows for SP_A predate grade-block lineage",
            window.AMT_data_compatibility_issue({"SP_A": {}}),
        )

    def test_saved_amt_snapshot_accepts_selected_footprint_with_no_rows(self):
        window = UserInputs.__new__(UserInputs)
        window.hub_input_choice = "Hub"
        window.mine_input_choice = "CC"
        window.opf_input_choice = "CC OPF02"
        window.start_time_choice = datetime(2026, 8, 14, 2, 7)
        data_source = {
            "SP_A": {"build": "SP_A_26001"},
            "SP_EMPTY": {"build": "SP_EMPTY_26001"},
        }
        window.AMT_stockpile_data = {
            "SP_A": [{"GRADE_BLOCK_LINEAGE_JSON": "[]"}]
        }
        saved_signature = json.loads(
            window.AMT_opening_request_signature(data_source)
        )
        saved_signature["start_time"] = "2026-08-14T02:07:00.581442"
        window.AMT_data_request_signature = json.dumps(
            saved_signature, sort_keys=True, separators=(",", ":")
        )
        saved = []
        window.opening_stockpile_inventories = SimpleNamespace(
            save_AMT_to_database=lambda value: saved.append(value)
        )

        restored = window.restore_loaded_AMT_data_to_database(data_source)

        self.assertTrue(restored)
        self.assertEqual([window.AMT_stockpile_data], saved)

    def test_manual_max_duration_is_floored_to_display_precision(self):
        self.assertEqual(
            111.3,
            UserInputs.safe_manual_max_duration_hours(111.355241),
        )

    def test_unchanged_amt_enrichment_skips_copy_save_and_map_reload(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_stockpile_data = {"SP_A": [{"FINAL_WMT": 100.0}]}
        window.AMT_enrichment_signature = "same"
        window.AMT_enrichment_request_signature = lambda: "same"
        calls = []
        window.enrich_AMT_grade_streams = lambda *_args: calls.append("enrich")
        window.opening_stockpile_inventories = SimpleNamespace(
            save_AMT_to_database=lambda *_args: calls.append("save")
        )
        window.refresh_AMT_map_data_from_database = (
            lambda: calls.append("refresh")
        )

        changed = UserInputs.refresh_AMT_enrichment_if_needed(window)

        self.assertFalse(changed)
        self.assertEqual(calls, [])

    def test_changed_amt_enrichment_is_saved_and_reloaded_once(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_stockpile_data = {"SP_A": [{"FINAL_WMT": 100.0}]}
        window.AMT_enrichment_signature = "old"
        window.AMT_enrichment_request_signature = lambda: "new"
        window.selected_AMT_data_source = lambda: {"SP_A": {"amt": True}}
        calls = []

        def enrich(data_source, rows):
            calls.append(("enrich", data_source, rows))
            return {"SP_A": [{"FINAL_WMT": 100.0, "enriched": True}]}

        window.enrich_AMT_grade_streams = enrich
        window.opening_stockpile_inventories = SimpleNamespace(
            save_AMT_to_database=lambda rows: calls.append(("save", rows))
        )
        window.refresh_AMT_map_data_from_database = (
            lambda: calls.append(("refresh",))
        )

        changed = UserInputs.refresh_AMT_enrichment_if_needed(window)

        self.assertTrue(changed)
        self.assertEqual([call[0] for call in calls], ["enrich", "save", "refresh"])
        self.assertEqual(window.AMT_enrichment_signature, "new")

    def test_restored_amt_map_view_reconnects_when_url_is_empty(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_map_view = FakeWebView(empty=True)

        window.reload_AMT_map_view()

        self.assertEqual(
            window.AMT_map_view.set_urls,
            ["http://localhost:8054"],
        )
        self.assertEqual(window.AMT_map_view.reload_count, 0)

    def test_amt_submit_ignores_button_checked_state_and_navigates(self):
        window = UserInputs.__new__(UserInputs)
        calls = []
        window.store_hex_sequence_table = (
            lambda navigate=True: calls.append(navigate)
        )

        window.handle_AMT_submit_clicked(False)

        self.assertEqual(calls, [True])

    def test_database_view_replaces_amt_inventory_with_selected_chunks(self):
        window = UserInputs.__new__(UserInputs)
        window.selected_data_stream = "adjusted_product"
        window.product_brand_labels_choice = ["FB"]
        window.updated_stockpile_data = {
            "AMT01": {
                "amt": True,
                "balance": 5000,
                "grade_fe": 55,
            },
            "INV01": {
                "amt": False,
                "balance": 8000,
                "grade_fe": 56,
                "minus_1mm_pct": 14.25,
                "fines_wmt": 5000.0,
            },
        }
        streams = {
            "adjusted_product": {
                "FB": {
                    "fe": 58.0, "si": 5.0, "al": 3.0,
                    "p": 0.05, "mn": 0.4,
                }
            }
        }
        window.hex_sequence_table = [{
            "footprint": "AMT01",
            "hex": "AMT01_CHUNK_001",
            "sequence": 1,
            "balance": 1000,
            "grade_fe": 55,
            "grade_si": 7,
            "grade_al": 3,
            "grade_p": 0.05,
            "grade_mn": 0.4,
            "grade_streams": streams,
        }]
        window.AMT_stockpile_data = {"AMT01": []}

        rows = window.database_view_stockpile_rows()

        self.assertEqual(
            [row["source_type"] for row in rows],
            ["AMT Chunk", "Inventory Stockpile"],
        )
        self.assertNotIn(
            "AMT01",
            [
                row["source_id"] for row in rows
                if row["source_type"] == "Inventory Stockpile"
            ],
        )
        self.assertEqual(rows[0]["selected_fb_fe"], 58.0)
        inventory_row = next(
            row for row in rows if row["source_type"] == "Inventory Stockpile"
        )
        self.assertEqual(inventory_row["minus_1mm_pct"], 14.25)
        self.assertEqual(inventory_row["fines_wmt"], 5000.0)

    def test_database_view_consolidates_grade_block_payloads_by_source(self):
        window = UserInputs.__new__(UserInputs)
        window.selected_data_stream = "adjusted_product"
        window.product_brand_labels_choice = ["FB"]
        first_streams = {
            "adjusted_product": {"FB": {"fe": 58.0}}
        }
        second_streams = {
            "adjusted_product": {"FB": {"fe": 62.0}}
        }
        transactions = pd.DataFrame([
            {
                "source": "Reserves/Block01",
                "payload": 100.0,
                "source_grade_fe": 55.0,
                "grade_streams": first_streams,
            },
            {
                "source": "Reserves/Block01",
                "payload": 300.0,
                "source_grade_fe": 57.0,
                "grade_streams": second_streams,
            },
        ])

        rows = window.database_view_grade_block_rows(transactions)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_type"], "APS Grade Block")
        self.assertEqual(rows[0]["tonnes"], 400.0)
        self.assertEqual(rows[0]["modelled_rom_wmt"], 400.0)
        self.assertAlmostEqual(rows[0]["grade_fe"], 56.5)
        self.assertAlmostEqual(rows[0]["selected_fb_fe"], 61.0)

    def test_database_view_publishes_branded_modelled_rom_for_stockpiles(self):
        window = UserInputs.__new__(UserInputs)
        window.selected_data_stream = "adjusted_rom"
        window.product_brand_labels_choice = ["FB", "SS"]
        unbranded_streams = {
            "modelled_rom": {
                "*": {
                    "fe": 55.0, "si": 6.0, "al": 3.0,
                    "p": 0.05, "mn": 0.4,
                }
            },
            "adjusted_rom": {
                "FB": {"fe": 56.0},
                "SS": {"fe": 57.0},
            },
        }

        for source_type in ("AMT Chunk", "Inventory Stockpile"):
            with self.subTest(source_type=source_type):
                row = window.database_view_record_with_streams(
                    {"source_type": source_type, "source_id": "SP01"},
                    unbranded_streams,
                )
                self.assertEqual(row["grade_modelled_rom_fe"], 55.0)
                self.assertEqual(row["grade_modelled_rom_fb_fe"], 55.0)
                self.assertEqual(row["grade_modelled_rom_ss_fe"], 55.0)

        aps_row = window.database_view_record_with_streams(
            {"source_type": "APS Grade Block", "source_id": "GB01"},
            {
                "modelled_rom": {
                    "FB": {"fe": 54.0},
                    "SS": {"fe": 58.0},
                }
            },
        )
        self.assertEqual(aps_row["grade_modelled_rom_fb_fe"], 54.0)
        self.assertEqual(aps_row["grade_modelled_rom_ss_fe"], 58.0)

    def test_database_view_preserves_canonical_define_field_grades(self):
        window = UserInputs.__new__(UserInputs)
        window.selected_data_stream = "adjusted_product"
        window.product_brand_labels_choice = ["FB"]
        streams = {
            "insitu": {"*": {"fe": 50.0}},
            "modelled_rom": {"*": {"fe": 51.0}},
            "adjusted_rom": {"FB": {"fe": 52.0}},
            "modelled_product": {"FB": {"fe": 53.0}},
            "adjusted_product": {"FB": {"fe": 54.0}},
        }

        row = window.database_view_record_with_streams(
            {
                "source_type": "AMT Chunk",
                "source_id": "SP01_CHUNK_001",
                "tonnes": 100.0,
            },
            streams,
        )

        self.assertEqual(row["insitu_fe"], 50.0)
        self.assertEqual(row["modelled_rom_fe"], 51.0)
        self.assertEqual(row["adjusted_rom_fe"], 52.0)
        self.assertEqual(row["modelled_product_fe"], 53.0)
        self.assertEqual(row["adjusted_product_fe"], 54.0)

    def test_legacy_amt_chunk_rebuilds_adjusted_product_from_current_factor(self):
        window = UserInputs.__new__(UserInputs)
        window.product_brand_labels_choice = ["FB"]
        window.opf_input_choice = "CC OPF02"
        window.historical_recon_factors = {
            "FB": {
                "blend": {
                    analyte: {"effective": 1.1}
                    for analyte in ("fe", "si", "al", "p", "mn")
                },
                "regression": {
                    analyte: {"effective": 0.9}
                    for analyte in ("fe", "si", "al", "p", "mn")
                },
            }
        }
        legacy_chunk = {
            "footprint": "OPF02_RP01_0501",
            "hex": "OPF02_RP01_0501_CHUNK_001",
            "grade_streams": {
                "modelled_rom": {
                    "*": {
                        "fe": 50.0, "si": 6.0, "al": 3.0,
                        "p": 0.05, "mn": 0.4,
                    }
                },
                "modelled_product": {
                    "FB": {
                        "fe": 60.0, "si": 5.0, "al": 2.5,
                        "p": 0.04, "mn": 0.3,
                    }
                },
                "adjusted_product": {"FB": {"fe": None}},
            },
        }
        window.hex_sequence_table = [legacy_chunk]
        window.hex_sequence_table_argument = [legacy_chunk]

        window.reconcile_saved_AMT_chunk_grade_streams()

        for rows in (
            window.hex_sequence_table,
            window.hex_sequence_table_argument,
        ):
            streams = rows[0]["grade_streams"]
            self.assertEqual(streams["modelled_rom"]["FB"]["fe"], 50.0)
            self.assertAlmostEqual(
                streams["adjusted_rom"]["FB"]["fe"], 55.0
            )
            self.assertAlmostEqual(
                streams["adjusted_product"]["FB"]["fe"], 54.0
            )

    def test_amt_chunk_reconciliation_rebuilds_once_per_input_signature(self):
        window = UserInputs.__new__(UserInputs)
        window.product_brand_labels_choice = ["FB"]
        window.opf_input_choice = "CC OPF02"
        window.mine_input_choice = "CC"
        window.AMT_data_request_signature = "opening-v1"
        window.field_definitions = []
        window.field_mappings = []
        window.historical_recon_factors = {}
        window.cb_lump_fines_mode = "derived"
        window.hex_sequence_table = [{
            "footprint": "SP1",
            "sequence": 1,
            "hex": "SP1_CHUNK_001",
            "member_hexes": "H1,H2",
            "grade_streams": {
                "modelled_rom": {"*": {"fe": 50.0}},
                "modelled_product": {"FB": {"fe": 60.0}},
            },
        }]
        window.hex_sequence_table_argument = []
        window.AMT_chunk_reconciliation_signature = ""
        calls = []

        class FakeAMTMap:
            data = pd.DataFrame([{"hex": "H1"}])

            @staticmethod
            def rebuild_saved_chunk_records(rows):
                calls.append("rebuild")
                return rows, len(rows)

        window.draw_AMT_map = FakeAMTMap()

        window.reconcile_saved_AMT_chunk_grade_streams()
        window.reconcile_saved_AMT_chunk_grade_streams()
        self.assertEqual(calls, ["rebuild"])
        self.assertEqual(
            window.hex_sequence_table_argument,
            window.hex_sequence_table,
        )
        self.assertIsNot(
            window.hex_sequence_table_argument,
            window.hex_sequence_table,
        )

        window.field_mappings = [{
            "source_family": "amt",
            "target_field": "modelled_rom_wmt",
            "source_field": "feed_wmt",
        }]
        window.reconcile_saved_AMT_chunk_grade_streams()
        self.assertEqual(calls, ["rebuild", "rebuild"])

    def test_v2_mapping_migration_prepopulates_required_amt_product_masses(self):
        window = UserInputs.__new__(UserInputs)
        window.field_mapping_schema_version = 2
        window.opf_input_choice = "CB OPF"
        window.field_mappings = [{
            "source_family": "amt",
            "brand": "",
            "target_field": "modelled_rom_wmt",
            "source_field": "feed_wmt",
        }]

        window.ensure_field_mapping_migration()

        lookup = {
            (row["source_family"], row["target_field"]): row["source_field"]
            for row in window.field_mappings
        }
        self.assertEqual(window.field_mapping_schema_version, 3)
        self.assertEqual(
            lookup[("amt", "modelled_product_wmt")],
            "MODELLED_PROD1_WMT",
        )
        self.assertEqual(
            lookup[("amt", "modelled_product_dmt")],
            "MODELLED_PROD1_DMT",
        )
        self.assertEqual(lookup[("amt", "modelled_rom_dmt")], "feed_dmt")

    def test_submitted_amt_chunks_override_stale_inventory_flag(self):
        window = UserInputs.__new__(UserInputs)
        window.selected_data_stream = "adjusted_product"
        window.product_brand_labels_choice = ["FB"]
        window.updated_stockpile_data = {
            "AMT01": {"amt": False, "balance": 5000, "grade_fe": 55}
        }
        window.stockpile_data_AMT_column = {"AMT01": False}
        window.hex_sequence_table = []
        window.hex_sequence_table_argument = [
            {
                "footprint": "amt01",
                "chunk_id": "AMT01_CHUNK_001",
                "sequence": 1,
                "tonnes": 2000,
                "grade_streams": {
                    "adjusted_product": {"FB": {"fe": 58.0}}
                },
            },
            {
                "footprint": "AMT01",
                "hex": "AMT01_CHUNK_002",
                "sequence": 2,
                "balance": 3000,
                "grade_streams": {
                    "adjusted_product": {"FB": {"fe": 59.0}}
                },
            },
        ]
        window.AMT_stockpile_data = {"AMT01": []}

        rows = window.database_view_stockpile_rows()

        self.assertEqual(
            [row["source_id"] for row in rows],
            ["AMT01_CHUNK_001", "AMT01_CHUNK_002"],
        )
        self.assertEqual([row["tonnes"] for row in rows], [2000, 3000])
        self.assertTrue(all(row["source_type"] == "AMT Chunk" for row in rows))

    def test_database_view_recovers_chunk_from_current_amt_map(self):
        window = UserInputs.__new__(UserInputs)
        window.selected_data_stream = "adjusted_product"
        window.product_brand_labels_choice = ["FB"]
        window.updated_stockpile_data = {
            "AMT01": {"amt": True, "balance": 5000, "grade_fe": 55}
        }
        window.stockpile_data_AMT_column = {"AMT01": True}
        window.hex_sequence_table = []
        window.hex_sequence_table_argument = []
        window.draw_AMT_map = SimpleNamespace(selected_points=[{
            "footprint": "amt01",
            "chunk_id": "AMT01_CHUNK_001",
            "sequence": 1,
            "tonnes": 5000,
            "grade_streams": {
                "adjusted_product": {"FB": {"fe": 58.0}}
            },
        }])
        window.AMT_stockpile_data = {"AMT01": []}

        rows = window.database_view_stockpile_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_type"], "AMT Chunk")
        self.assertEqual(rows[0]["source_id"], "AMT01_CHUNK_001")

    @patch("GUI.InitialiseGUI.QMessageBox.warning")
    def test_amt_submit_blocks_selected_footprint_without_chunk(
        self, warning
    ):
        window = UserInputs.__new__(UserInputs)
        window.store_AMT_chunk_settings = lambda: True
        window.stockpile_data_AMT_column = {
            "AMT01": True,
            "AMT02": True,
        }
        window.updated_stockpile_data = {}
        window.hex_sequence_table = [{
            "footprint": "PREVIOUS",
            "hex": "PREVIOUS_CHUNK_001",
        }]
        window.draw_AMT_map = SimpleNamespace(
            return_hex_sequence=lambda: [{
                "footprint": "AMT01",
                "hex": "AMT01_CHUNK_001",
                "sequence": 1,
            }]
        )

        result = window.store_hex_sequence_table(navigate=False)

        self.assertFalse(result)
        self.assertIn("AMT02", warning.call_args.args[2])
        self.assertEqual(
            window.hex_sequence_table[0]["footprint"], "PREVIOUS"
        )

    def test_deselected_amt_footprint_prunes_chunks_and_cached_state(self):
        window = UserInputs.__new__(UserInputs)
        window.hex_sequence_table = [
            {"footprint": "KEEP", "hex": "KEEP_CHUNK_001"},
            {"footprint": "REMOVE", "hex": "REMOVE_CHUNK_001"},
        ]
        window.hex_sequence_table_argument = [
            {"footprint": "KEEP", "hex": "KEEP_CHUNK_001"},
            {"footprint": "REMOVE", "hex": "REMOVE_CHUNK_001"},
        ]
        window.AMT_chunk_settings = {"KEEP": {}, "REMOVE": {}}
        window.AMT_stockpile_data = {"KEEP": [{}], "REMOVE": [{}]}

        window.prune_unselected_amt_state({"KEEP"})

        self.assertEqual(
            [row["footprint"] for row in window.hex_sequence_table],
            ["KEEP"],
        )
        self.assertEqual(
            [row["footprint"] for row in window.hex_sequence_table_argument],
            ["KEEP"],
        )
        self.assertEqual(set(window.AMT_chunk_settings), {"KEEP"})
        self.assertEqual(set(window.AMT_stockpile_data), {"KEEP"})

    def test_database_view_recovers_chunk_lineage_audit_from_member_hexes(self):
        chunk = {
            "member_hexes": "HEX_A,HEX_B",
            "grade_block_count": None,
            "lineage_unmatched_wmt": None,
        }
        rows = [
            {
                "HEX": "HEX_A",
                "FINAL_WMT": 60.0,
                "LINEAGE_MATCHED_FINAL_WMT": 60.0,
                "GRADE_BLOCK_LINEAGE_JSON": '[{"lineage_key":"GB:1"}]',
            },
            {
                "HEX": "HEX_B",
                "FINAL_WMT": 40.0,
                "LINEAGE_MATCHED_FINAL_WMT": 35.0,
                "GRADE_BLOCK_LINEAGE_JSON": (
                    '[{"lineage_key":"GB:1"},{"lineage_key":"GB:2"}]'
                ),
            },
            {
                "HEX": "OTHER_CHUNK",
                "FINAL_WMT": 500.0,
                "LINEAGE_MATCHED_FINAL_WMT": 0.0,
                "GRADE_BLOCK_LINEAGE_JSON": '[{"lineage_key":"GB:3"}]',
            },
        ]

        audit = UserInputs.database_view_chunk_lineage_audit(chunk, rows)

        self.assertEqual(audit["grade_block_count"], 2)
        self.assertEqual(audit["lineage_coverage_pct"], 95.0)
        self.assertEqual(audit["lineage_unmatched_wmt"], 5.0)

    def test_24hr_path_is_shared_between_data_streams_and_guidance(self):
        window = UserInputs.__new__(UserInputs)
        window.file_path_24hr_choice = "old.csv"
        window.data_streams_file_path_24hr = FakeLineEdit("old.csv")
        window.file_path_24hr = FakeLineEdit("old.csv")
        window.available_24hr_expit_agents = ["EX01"]
        window.selected_24hr_expit_agents = ["EX01"]
        window.expit_agent_input = object()
        window.set_24hr_expit_agent_items = lambda *_args: None
        refreshed = []
        window.refresh_aps_grade_field_headers = (
            lambda show_errors=False: refreshed.append(show_errors)
        )

        window.set_24hr_mining_path(
            "new.csv", reset_agents=True, show_mapping_errors=True
        )

        self.assertEqual(window.file_path_24hr_choice, "new.csv")
        self.assertEqual(window.data_streams_file_path_24hr.text(), "new.csv")
        self.assertEqual(window.file_path_24hr.text(), "new.csv")
        self.assertEqual(window.selected_24hr_expit_agents, [])
        self.assertEqual(refreshed, [True])

    @patch(
        "GUI.InitialiseGUI.HaulCycleDataHandler.get_distinct_crusher_names",
        return_value=["Crusher A", "Crusher B"],
    )
    def test_imported_cycles_retain_existing_crusher_selections(
        self, _get_crushers
    ):
        window = UserInputs.__new__(UserInputs)
        window.haul_cycle_file_path = FakeLineEdit("Cycles.csv")
        window.stockpile_data = None
        window.selected_haul_cycle_crusher_names = lambda: ["Crusher A"]
        window.haul_cycle_crusher_mapping_choice = ["Crusher A"]
        selected_calls = []
        mapping_calls = []
        window.set_haul_cycle_crusher_items = (
            lambda names, selected: selected_calls.append((names, selected))
        )
        window.set_haul_cycle_crusher_mapping_items = (
            lambda names, selected, use_default=True: mapping_calls.append(
                (names, selected, use_default)
            )
        )
        window.refresh_haul_cycle_routes = lambda show_errors=False: None
        window.validate_form = lambda: None

        window.load_haul_cycle_crusher_names(show_messages=False)

        self.assertEqual(
            selected_calls,
            [(["Crusher A", "Crusher B"], ["Crusher A"])],
        )
        self.assertEqual(
            mapping_calls,
            [(["Crusher A", "Crusher B"], ["Crusher A"], False)],
        )

    def test_database_view_exposes_auto_stockpile_turnover_block(self):
        window = UserInputs.__new__(UserInputs)
        window.updated_stockpile_data = {
            "SP01": {
                "name": "sp01",
                "balance": 1000,
                "reclaim_threshold": 500,
            }
        }
        window.calendar_inputs = {}
        window.database_view_calendar_inputs = {
            "stockpiles_sp01_state": {"Preplan": "Auto"},
            "stockpiles_sp01_maximum_quantity": {"Preplan": 5000},
        }
        window.start_time_choice = datetime(2026, 7, 27, 10)
        window.database_view_start_time_snapshot = window.start_time_choice
        window.database_view_period_count_snapshot = 3
        records = [{
            "source_type": "Inventory Stockpile",
            "source_id": "SP01",
            "parent_stockpile": "SP01",
            "tonnes": 1000,
            "warnings": "",
        }]
        transactions = pd.DataFrame([{
            "destination": "Stockpiles/SP01",
            "payload": 200,
            "delivered_datetime": datetime(2026, 7, 27, 14),
        }])

        window.apply_database_view_stockpile_calculations(
            records, transactions
        )

        self.assertEqual(records[0]["aps_incoming_tonnes"], 200)
        self.assertEqual(records[0]["projected_balance_after_aps"], 1200)
        self.assertFalse(records[0]["available_at_scenario_start"])
        self.assertIn("withheld until APS deliveries", records[0]["warnings"])

    def test_stockpile_defaults_require_planned_crusher_and_brand_guidance(self):
        window = UserInputs.__new__(UserInputs)
        window.current_haul_cycle_crusher_node = lambda: ["Crushers/RCH"]
        stockpiles = {
            "MATCH": {
                "nearest_crusher": "RCH",
                "aps_brand_proportions": {"CCFB": 1.0},
            },
            "WRONG_CRUSHER": {
                "nearest_crusher": "OPF1 Crusher",
                "aps_brand_proportions": {"CCFB": 1.0},
            },
            "NO_BRAND": {
                "nearest_crusher": "RCH",
                "aps_brand_proportions": {},
            },
        }

        selected = window.default_stockpile_use_for_active_crusher(stockpiles)

        self.assertEqual(
            selected,
            {"MATCH": True, "WRONG_CRUSHER": False, "NO_BRAND": False},
        )

    def test_stockpile_guidance_defaults_select_use_and_amt_together(self):
        window = UserInputs.__new__(UserInputs)
        window.current_haul_cycle_crusher_node = lambda: ["Crushers/RCH"]
        window.stockpile_data_use_column = {}
        window.stockpile_data_AMT_column = {}
        stockpiles = {
            "MATCH": {
                "nearest_crusher": "RCH",
                "aps_brand_summary": "SF",
            },
            "NO_BRAND": {
                "nearest_crusher": "RCH",
                "aps_brand_summary": "",
            },
        }

        defaults = window.apply_default_stockpile_preselection(stockpiles)

        expected = {"MATCH": True, "NO_BRAND": False}
        self.assertEqual(defaults, expected)
        self.assertEqual(window.stockpile_data_use_column, expected)
        self.assertEqual(window.stockpile_data_AMT_column, expected)

    def test_stockpile_guidance_defaults_select_none_without_tipping_point(self):
        window = UserInputs.__new__(UserInputs)
        window.current_haul_cycle_crusher_node = lambda: []
        window.stockpile_data_use_column = {}
        window.stockpile_data_AMT_column = {}
        stockpiles = {
            "BRANDED": {
                "nearest_crusher": "RCH",
                "aps_brand_summary": "SF",
            }
        }

        defaults = window.apply_default_stockpile_preselection(stockpiles)

        self.assertEqual(defaults, {"BRANDED": False})
        self.assertEqual(window.stockpile_data_use_column, {"BRANDED": False})
        self.assertEqual(window.stockpile_data_AMT_column, {"BRANDED": False})

    def test_legacy_flat_tab_states_are_mapped_to_stable_page_ids(self):
        navigation = SimpleNamespace(
            page_locations={
                "site_configuration": object(),
                "reports": object(),
                "manual_grade_profiles": object(),
            },
            legacy_tab_page_ids=[
                "site_configuration",
                "stockpile_inventories",
                "amt_stockpiles",
                "solver_configuration",
                "product_build_settings",
                "calendar",
                "decision_point",
                "optimised_blend_sequence",
                "build_depletion_profiles",
                "optimised_grade_profiles",
                "reports",
                "setup_blends",
                "blend_sequence",
                "manual_grade_profiles",
                "agent",
            ],
        )

        states = UserInputs.normalized_page_states(
            navigation,
            {0: True, "10": False, 13: True},
        )

        self.assertEqual(
            states,
            {
                "site_configuration": True,
                "reports": False,
                "manual_grade_profiles": True,
            },
        )

    def test_stable_page_states_are_preserved(self):
        navigation = SimpleNamespace(
            page_locations={"reports": object()},
            legacy_tab_page_ids=[],
        )

        states = UserInputs.normalized_page_states(
            navigation,
            {"reports": True, "unknown_page": False},
        )

        self.assertEqual(states, {"reports": True})


class ExpitSequenceMapTests(unittest.TestCase):
    def test_expit_refresh_uses_selected_historical_scenario_time(self):
        selected_time = datetime(2026, 8, 1, 6, 0)
        with tempfile.NamedTemporaryFile(suffix=".csv") as schedule:
            window = SimpleNamespace(
                file_path_24hr_choice=schedule.name,
                expit_mode_choice=2,
                time_mode_choice=2,
                start_time_choice=selected_time,
                file_path_choice="",
                selected_24hr_expit_agents=[],
            )
            window.current_site_start_time = lambda: selected_time
            window.active_site_context = lambda: {}

            inputs = UserInputs.expit_sequence_refresh_inputs(window)

        self.assertEqual(selected_time, inputs["start_time"])

    def test_map_toggles_geological_complete_and_uses_excavator_marker(self):
        complete_name = "CUE01_01_0414_111_0417_LG46"
        partial_name = "CUE01_01_0414_111_0417_LG47"
        complete_key = grade_block_key(complete_name)
        partial_key = grade_block_key(partial_name)
        audit = pd.DataFrame([
            {
                "agent": "EX01", "record_type": "planned_parent",
                "parent_grade_block": complete_name,
                "grade_block_key": complete_key, "material_class": "Ore",
                "original_sequence": 1, "updated_sequence": None,
                "centroid_easting": 0, "centroid_northing": 0,
                "completion_status": "Complete",
                "geological_completion_status": "Complete",
                "geological_remaining_fraction": 0.05,
            },
            {
                "agent": "EX01", "record_type": "planned_parent",
                "parent_grade_block": partial_name,
                "grade_block_key": partial_key, "material_class": "Ore",
                "original_sequence": 2, "updated_sequence": 1,
                "centroid_easting": 20, "centroid_northing": 0,
                "completion_status": "Partial",
                "geological_completion_status": "Partial",
                "geological_remaining_fraction": 0.5,
                "nominal_geological_wmt": 100,
                "cumulative_actual_wmt": 50,
                "estimated_geological_remaining_wmt": 50,
            },
        ])
        geometry_rows = []
        for name, centre in ((complete_name, 0), (partial_name, 20)):
            for point, (x, y) in enumerate((
                (centre - 5, -5), (centre + 5, -5),
                (centre + 5, 5), (centre - 5, 5),
            ), start=1):
                geometry_rows.append({
                    "full_name": name, "point": point,
                    "easting": x, "northing": y,
                })
        actual = pd.DataFrame([{
            "agent": "EX01", "source_fms": partial_name,
            "transaction_datetime": "2026-08-14 03:00",
            "actual_wmt": 10, "movement_type": "ExPit",
        }])
        window = UserInputs.__new__(UserInputs)

        figure = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {
                "ore_blocks", "original_route", "corrected_route",
                "actual_route", "excavator_marker",
            },
        )

        polygon_traces = [trace for trace in figure.data if trace.fill == "toself"]
        self.assertEqual(1, len(polygon_traces))
        self.assertGreater(min(polygon_traces[0].x), 10)
        self.assertEqual(1, len(figure.layout.images))
        self.assertTrue(
            str(figure.layout.images[0].source).startswith(
                "data:image/png;base64,"
            )
        )
        self.assertLess(float(figure.layout.images[0].sizex), 10.0)
        self.assertEqual("top", figure.layout.title.yanchor)
        self.assertGreater(float(figure.layout.legend.y), 1.0)
        self.assertGreaterEqual(int(figure.layout.margin.t), 175)

        with_depleted = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {"ore_blocks", "depleted_portions"},
        )
        depleted_traces = [
            trace for trace in with_depleted.data
            if trace.name == "Estimated depleted portion (north to south)"
        ]
        self.assertEqual(1, len(depleted_traces))
        self.assertAlmostEqual(0.0, min(depleted_traces[0].y), places=6)

        window.__dict__["expit_excavator_size_pct"] = 25
        smaller_marker = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {"ore_blocks", "actual_route", "excavator_marker"},
        )
        self.assertAlmostEqual(
            2.5, float(smaller_marker.layout.images[0].sizex), places=6
        )

        with_completed = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {
                "ore_blocks", "completed_blocks", "original_route",
                "corrected_route", "actual_route", "excavator_marker",
            },
        )
        completed_traces = [
            trace for trace in with_completed.data
            if str(trace.name).startswith("Geologically complete —")
        ]
        self.assertEqual(1, len(completed_traces))
        self.assertEqual("dot", completed_traces[0].line.dash)

        no_routes = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {"ore_blocks"},
        )
        self.assertFalse(any(
            trace.name == "Actual route" for trace in no_routes.data
        ))
        self.assertEqual(0, len(no_routes.layout.images))

        planned_slices = pd.DataFrame([
            {
                "agent": "EX01",
                "source": "Reserves/CC1/CUE01/01/414/111/417/LG46_1",
                "start_datetime": "2026-08-14 01:00",
            },
            {
                "agent": "EX01",
                "source": "Reserves/CC1/CUE01/01/414/111/417/LG46_2",
                "start_datetime": "2026-08-14 02:00",
            },
            {
                "agent": "EX01",
                "source": "Reserves/CC1/CUE01/01/414/111/417/LG47_1",
                "start_datetime": "2026-08-14 03:00",
            },
        ])
        sliced_route = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {"ore_blocks", "original_route"},
            planned_slices, pd.DataFrame(),
        )
        original_trace = next(
            trace for trace in sliced_route.data
            if trace.name == "Original APS sliced route"
        )
        self.assertEqual(2, len(original_trace.x))
        self.assertEqual(0, len(sliced_route.layout.annotations))
        route_traces = [
            trace for trace in sliced_route.data
            if "route" in str(getattr(trace, "name", "")).lower()
        ]
        self.assertTrue(route_traces)
        self.assertTrue(all(trace.mode == "lines" for trace in route_traces))

        marker_without_route = window.expit_sequence_figure(
            "EX01", audit, pd.DataFrame(geometry_rows), actual,
            {"ore_blocks", "excavator_marker"},
        )
        self.assertEqual(1, len(marker_without_route.layout.images))
        self.assertFalse(any(
            trace.name == "Actual route" for trace in marker_without_route.data
        ))
        self.assertEqual(0, len(no_routes.layout.images))


class ReportsQueryTests(unittest.TestCase):
    def test_default_query_quotes_table_name_and_limits_to_100_rows(self):
        self.assertEqual(
            UserInputs.default_sqlite_report_query('report"name'),
            'SELECT * FROM "report""name" LIMIT 100',
        )

    def test_only_single_select_or_with_query_is_accepted(self):
        self.assertTrue(
            UserInputs.is_read_only_report_query(
                "WITH values_cte AS (SELECT 1) SELECT * FROM values_cte;"
            )
        )
        self.assertTrue(UserInputs.is_read_only_report_query("SELECT * FROM report"))
        self.assertFalse(UserInputs.is_read_only_report_query("DELETE FROM report"))
        self.assertFalse(
            UserInputs.is_read_only_report_query(
                "SELECT * FROM report; DELETE FROM report"
            )
        )

    def test_sqlite_connection_blocks_writes(self):
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE report (value INTEGER)")
            connection.execute("INSERT INTO report VALUES (1)")
            UserInputs.configure_read_only_sqlite_connection(connection)

            self.assertEqual(
                connection.execute("SELECT value FROM report").fetchall(),
                [(1,)],
            )
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM report")
        finally:
            connection.close()

    def test_saved_optimisation_rows_are_detected_for_project_restore(self):
        descriptor, database_path = tempfile.mkstemp(suffix=".db")
        os.close(descriptor)
        try:
            connection = sqlite3.connect(database_path)
            connection.execute(
                "CREATE TABLE optimisation_plan_status "
                "(plan_id TEXT, status TEXT)"
            )
            connection.execute(
                "INSERT INTO optimisation_plan_status VALUES "
                "('Primary', 'complete')"
            )
            connection.commit()
            connection.close()

            self.assertTrue(
                UserInputs.database_has_saved_optimisation_results(
                    database_path
                )
            )
        finally:
            os.remove(database_path)


if __name__ == "__main__":
    unittest.main()
