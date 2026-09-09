import copy
import pickle
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GUI.InitialiseGUI import UserInputs
from classes.ProductTargets import (
    PRODUCT_TARGET_KEYS,
    migrate_product_target_state,
    product_targets_value,
)
from classes.ProductTargetModes import migrate_target_row


def targets():
    return [{
        "build_id": 7, "build_name": "FB Lump Build 1", "brand": "FB",
        "byproduct": "lump", "target_tonnes": 12000,
        "target_fe_min": 58, "target_fe_max": 62,
        "planning_scenario": "2WCC_20260902", "planning_target_tonnes": 24000,
        "crusher_contribution_ratio": 0.5, "opf": "OPF1", "crusher": "RCH",
        "cbfl_campaign": "campaign_1",
    }]


class ProductTargetStateTests(unittest.TestCase):
    def test_legacy_multi_site_state_round_trip_preserves_targets_and_metadata(self):
        legacy = {
            "product_build_settings": targets(),
            "calendar_inputs": {"product_build_settings": targets()},
            "tab_states": {"product_build_settings": True, "calendar": False},
            "site_scenarios": {
                "inactive": {
                    "product_build_settings": targets(),
                    "calendar_inputs": {"product_build_settings": targets()},
                },
            },
        }
        original = copy.deepcopy(legacy)
        window = UserInputs.__new__(UserInputs)
        loaded = window.normalized_agent_project_state(pickle.loads(pickle.dumps(legacy)))
        saved = pickle.loads(pickle.dumps(migrate_product_target_state(loaded)))
        for record in (saved, saved["site_scenarios"]["inactive"]):
            self.assertEqual(record["product_targets"], [migrate_target_row(r) for r in targets()])
            self.assertEqual(record["calendar_inputs"]["product_targets"], [migrate_target_row(r) for r in targets()])
            self.assertNotIn("product_build_settings", record)
            self.assertNotIn("product_build_settings", record["calendar_inputs"])
        self.assertEqual(saved["tab_states"], {"product_targets": True, "calendar": False})
        self.assertEqual(legacy, original)
        self.assertEqual(migrate_product_target_state(saved), saved)

    def test_current_empty_targets_override_legacy_values_at_each_boundary(self):
        mixed = {"product_targets": [], "product_build_settings": targets()}
        state = dict(mixed, calendar_inputs=mixed, site_scenarios={"inactive": mixed})
        migrated = migrate_product_target_state(state)
        for record in (migrated, migrated["calendar_inputs"], migrated["site_scenarios"]["inactive"]):
            self.assertEqual(product_targets_value(record), [])
            self.assertNotIn("product_build_settings", record)

    def test_solver_input_reader_accepts_both_names_and_respects_explicit_empty(self):
        for key in ("product_targets", "product_build_settings"):
            self.assertEqual(product_targets_value({key: targets()}), targets())
        self.assertEqual(product_targets_value({"product_targets": [], "product_build_settings": targets()}), [])
        self.assertEqual(product_targets_value({}, []), [])

    def test_page_restore_accepts_named_and_indexed_legacy_state(self):
        navigation = SimpleNamespace(
            page_locations={"product_targets": object()},
            legacy_tab_page_ids=["product_build_settings"],
        )
        for raw in ({"product_build_settings": True}, {0: True}, {"0": True}):
            self.assertEqual(UserInputs.normalized_page_states(navigation, raw), {"product_targets": True})
        for raw in (
            {"product_targets": False, "product_build_settings": True, 0: True},
            {0: True, "product_build_settings": True, "product_targets": False},
        ):
            self.assertEqual(UserInputs.normalized_page_states(navigation, raw), {"product_targets": False})

    def test_legacy_navigation_selects_and_enables_current_page(self):
        tabs, page = Mock(), Mock()
        navigation = SimpleNamespace(
            page_locations={"product_targets": (tabs, 1)},
            page_widgets={"product_targets": page}, navigation_parents={},
        )
        UserInputs.set_page_enabled(navigation, "product_build_settings", True)
        tabs.setTabEnabled.assert_called_once_with(1, True)
        UserInputs.show_page(navigation, "product_build_settings")
        tabs.setCurrentIndex.assert_called_once_with(1)
        self.assertTrue(UserInputs.is_page_enabled(navigation, "product_build_settings"))


class ProductTargetAgentTests(unittest.TestCase):
    def setUp(self):
        self.window = UserInputs.__new__(UserInputs)

    def test_workflow_sections_and_proposals_accept_current_and_legacy_aliases(self):
        expected = self.window.normalized_agent_product_targets(targets())
        for alias in PRODUCT_TARGET_KEYS:
            for result in (
                {alias: targets()},
                {alias: {"rows": targets()}},
                {alias: {"builds": [], "rows": targets()}},
                {"proposed_constraints": [{"target": alias, "value": targets()}]},
            ):
                with self.subTest(alias=alias, result=result):
                    self.assertEqual(self.window.extract_agent_workflow_payload(result), {"product_targets": expected})
                    self.assertTrue(self.window.is_agent_workflow_target(alias))
            self.assertTrue(self.window.is_agent_workflow_target(alias + ".rows"))

    def test_empty_current_workflow_and_wrapped_rows_win_over_legacy_values(self):
        mixed = {"product_build_settings": targets(), "product_targets": []}
        self.assertEqual(self.window.extract_agent_workflow_payload(mixed), {"product_targets": []})
        proposed = {"proposed_constraints": [
            {"target": key, "value": value} for key, value in mixed.items()
        ]}
        self.assertEqual(self.window.extract_agent_workflow_payload(proposed), {"product_targets": []})
        cross_section = {
            "product_build_settings": targets(),
            "proposed_constraints": [{"target": "product_targets", "value": []}],
        }
        self.assertEqual(self.window.extract_agent_workflow_payload(cross_section), {"product_targets": []})
        self.assertEqual(self.window.normalized_agent_product_targets(dict(mixed, rows=targets())), [])
        self.assertEqual(self.window.summarize_agent_workflow_payload(mixed), "Product Targets (0)")

    def test_review_and_direct_apply_share_canonical_targets_for_all_aliases(self):
        window = self.window
        window.product_build_table = object()
        window.populate_product_build_table = Mock()
        window.store_product_targets = Mock(return_value=True)
        for alias in PRODUCT_TARGET_KEYS:
            self.assertTrue(window.is_known_agent_target(alias))
            proposal = {"target": alias, "value": targets()}
            expanded = window.expand_agent_proposals([proposal])
            self.assertEqual(expanded[0]["target"], "product_targets")
            self.assertEqual(proposal["target"], alias)
            self.assertTrue(window.apply_agent_target_value(alias, targets()))
            self.assertEqual(window.get_agent_target_current_value(alias), window.product_targets)
            self.assertEqual(window.product_targets[0]["target_tonnes"], 12000)

    def test_workflow_omitted_targets_preserve_existing_but_explicit_empty_clears(self):
        window = self.window
        window.populate_product_build_table = Mock()
        window.store_product_targets = Mock(return_value=True)
        window.setup_calendar = Mock()
        window.set_page_enabled = Mock()
        window.show_page = Mock()
        window.calendar_tab_index = "calendar"
        with patch("GUI.InitialiseGUI.QTimer.singleShot"):
            window.product_targets = targets()
            window.agent_workflow_payload = {}
            window.agent_workflow_apply_product_targets()
            self.assertEqual(window.product_targets, targets())
            window.agent_workflow_payload = {"product_targets": [], "product_build_settings": targets()}
            window.agent_workflow_apply_product_build_settings()
            self.assertEqual(window.product_targets, [])
            window.agent_workflow_payload = {"product_build_settings": targets()}
            window.agent_workflow_apply_product_targets()
            self.assertEqual(window.product_targets[0]["target_tonnes"], 12000)

    def test_legacy_python_alias_shares_state_and_normalization(self):
        self.window.product_build_settings = targets()
        self.assertIs(self.window.product_build_settings, self.window.product_targets)
        self.window.product_targets = []
        self.assertEqual(self.window.product_build_settings, [])
        self.assertEqual(
            self.window.normalized_agent_product_build_settings(targets()),
            self.window.normalized_agent_product_targets(targets()),
        )

    def test_store_targets_migrates_calendar_without_changing_tonnes_or_grades(self):
        window = self.window
        window.product_targets = targets()
        window.calendar_inputs = {"product_build_settings": targets()}
        window.read_product_targets_from_table = Mock(return_value=targets())
        window.product_brand_options = Mock(return_value=["FB"])
        window.refresh_product_build_plan_scenario_label = Mock()
        self.assertTrue(window.store_product_targets())
        self.assertEqual(window.calendar_inputs["product_targets"], targets())
        self.assertNotIn("product_build_settings", window.calendar_inputs)

    def test_outgoing_agent_context_uses_current_vocabulary(self):
        window = SimpleNamespace(
            default_product_brand_labels=self.window.default_product_brand_labels,
            normalized_solver_config=self.window.normalized_solver_config,
            make_agent_json_safe=self.window.make_agent_json_safe,
        )
        window.stockpile_data_use_column = {}
        window.stockpile_data_AMT_column = {}
        window.product_targets = targets()
        window.calendar_inputs = {"product_build_settings": targets()}
        context = UserInputs.build_agent_context(window)
        self.assertEqual(context["product_targets"], [migrate_target_row(r) for r in targets()])
        self.assertEqual(context["calendar_inputs"]["product_targets"], [migrate_target_row(r) for r in targets()])
        self.assertNotIn("product_build_settings", str(context))
        self.assertNotIn("Product Build Settings", str(context))
        self.assertIn("product_targets_contract", context["agent_result_contract"])


if __name__ == "__main__":
    unittest.main()
