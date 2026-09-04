import unittest
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

from GUI.InitialiseGUI import UserInputs
from GUI.ManualBlendDash import DrawOptimisedGradeProfiles
from classes.CaseModeller import (
    CaseModeller,
    ProductBuildRepairFailed,
    ProductBuildRepairRequired,
    SteadyStateInfeasible,
)
from classes.EventData import EventData
from classes.EventPoolGenerator import EventPoolGenerator
from classes.Optimizer import Optimizer
from classes.PeriodManager import PeriodManager
from classes.ProductBuildProgress import ProductBuildProgress


class DecisionLeverConfigurationTests(unittest.TestCase):
    def test_failed_repair_restores_last_consistent_solved_checkpoint(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.solver_config = {
            "enable_product_build_repair_loop": True,
            "allow_offspec_steady_states_for_product_build": True,
            "min_grade_block_pair_duration_hours": 0,
            "min_feed_duration_hours": 0,
            "blend_option_timeout_seconds": 0,
        }
        modeller.product_build_settings = []
        modeller.current_time = 0
        modeller.steady_state_tracker = 0
        modeller.results = pd.DataFrame()
        modeller.check_abort_requested = lambda: None
        modeller.planning_horizon_end = lambda: 3
        modeller.product_build_repair_enabled = lambda: True
        modeller.capture_product_build_repair_checkpoint = lambda: {
            "current_time": modeller.current_time,
            "steady_state_tracker": modeller.steady_state_tracker,
            "results": modeller.results.copy(),
        }

        def run_step():
            if modeller.steady_state_tracker == 0:
                modeller.results = pd.DataFrame([{
                    "steady_state_number": 0,
                    "source": "SP1",
                }])
                modeller.current_time = 1
                return
            # Simulate an exhausted repair path after the modeller has already
            # completed and checkpointed steady state zero.
            modeller.results = pd.DataFrame()
            modeller.current_time = 0
            raise ProductBuildRepairFailed("Build 1", "Repair exhausted.")

        modeller.run_optimization_step = run_step

        with self.assertRaises(ProductBuildRepairFailed):
            modeller.run()

        self.assertEqual(1, modeller.current_time)
        self.assertEqual(1, modeller.steady_state_tracker)
        self.assertEqual(["SP1"], modeller.results["source"].tolist())

    def test_shorter_terminal_repair_restores_farther_original_checkpoint(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.solver_config = {
            "enable_product_build_repair_loop": True,
            "allow_offspec_steady_states_for_product_build": True,
            "min_grade_block_pair_duration_hours": 0,
            "min_feed_duration_hours": 0,
            "blend_option_timeout_seconds": 0,
        }
        modeller.product_build_settings = [{
            "build_id": 1,
            "build_name": "Build 1",
            "target_tonnes": 100,
            "byproduct": "",
        }]
        modeller.product_build_runtime_states = [{"tonnes": 10}]
        modeller.product_build_repair_from_states = {}
        modeller.byproducts_enabled = False
        modeller.current_time = 0
        modeller.steady_state_tracker = 0
        modeller.results = pd.DataFrame()
        modeller.check_abort_requested = lambda: None
        modeller.planning_horizon_end = lambda: 4
        modeller.product_build_repair_enabled = lambda: True
        modeller.current_product_build_index = lambda: 0
        modeller.capture_product_build_repair_checkpoint = lambda: {
            "current_time": modeller.current_time,
            "steady_state_tracker": modeller.steady_state_tracker,
            "results": modeller.results.copy(),
            "product_build_runtime_states": [
                dict(modeller.product_build_runtime_states[0])
            ],
        }
        repair_requested = False

        def run_step():
            nonlocal repair_requested
            if not repair_requested and modeller.steady_state_tracker < 3:
                state = modeller.steady_state_tracker
                modeller.results = pd.concat([
                    modeller.results,
                    pd.DataFrame([{
                        "steady_state_number": state,
                        "source": f"SP{state}",
                    }]),
                ], ignore_index=True)
                modeller.current_time += 1
                return
            if not repair_requested:
                repair_requested = True
                raise ProductBuildRepairRequired(
                    0,
                    [2],
                    "Repair required.",
                )
            raise SteadyStateInfeasible(2, "Repair became infeasible.")

        modeller.run_optimization_step = run_step

        with self.assertRaises(SteadyStateInfeasible) as raised:
            modeller.run()

        self.assertEqual(3, modeller.current_time)
        self.assertEqual(3, modeller.steady_state_tracker)
        self.assertEqual(
            ["SP0", "SP1", "SP2"],
            modeller.results["source"].tolist(),
        )
        self.assertTrue(modeller.partial_plan_restored_after_repair)
        self.assertEqual(3, raised.exception.steady_state_number)

    def test_product_build_capacity_rate_reads_event_pool_generator_sources(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.solver_config = {
            "crusher_tonnes_stream": "modelled_rom_wmt",
            "product_build_tonnes_stream": "modelled_product_wmt",
        }
        stockpile = SimpleNamespace(
            balance=100.0,
            source_properties={
                "modelled_rom_wmt": 100.0,
                "modelled_product_wmt": 80.0,
            },
        )
        modeller.event_pool = EventPoolGenerator([stockpile], [], [])

        self.assertEqual(800.0, modeller.product_build_capacity_rate(1000.0))

    def test_preference_incentive_defaults_preserve_previous_weights(self):
        config = UserInputs.normalized_solver_config(
            SimpleNamespace(solver_config={}),
            {},
        )

        self.assertEqual(config["throughput_incentive_per_tonne"], 1_000_100.0)
        self.assertEqual(config["source_selection_tie_break_penalty"], 0.001)
        self.assertEqual(config["fewer_stockpiles_incentive"], 10.0)
        self.assertEqual(config["balance_preference_incentive"], 1.0)
        self.assertEqual(config["amt_preference_incentive"], 1.0)
        self.assertEqual(
            config["contaminated_preference_incentive"],
            1.0,
        )
        self.assertEqual(config["low_fe_preference_incentive"], 1.0)
        self.assertEqual(
            config["contingency_max_blend_options_per_steady_state"],
            12,
        )
        self.assertFalse(
            config["two_wp_destination_turnover_guidance_enabled"]
        )
        self.assertEqual(
            config["two_wp_destination_turnover_incentive"], 10.0
        )

    def test_legacy_primary_option_limit_becomes_contingency_default(self):
        config = UserInputs.normalized_solver_config(
            SimpleNamespace(solver_config={}),
            {"max_blend_options_per_steady_state": 7},
        )

        self.assertEqual(config["max_blend_options_per_steady_state"], 7)
        self.assertEqual(
            config["contingency_max_blend_options_per_steady_state"],
            7,
        )

    def test_contingency_uses_its_own_option_limit(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.plan_id = "Contingency 1"
        modeller.solver_config = {
            "max_blend_options_per_steady_state": 3,
            "contingency_max_blend_options_per_steady_state": 9,
        }

        self.assertEqual(modeller.configured_max_decision_blend_options(), 9)

    def test_contingency_reuses_only_when_no_unused_mix_is_feasible(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.plan_id = "Contingency 1"
        modeller.steady_state_tracker = 4
        modeller.solver_config = {
            "contingency_distinctness_mode": "stockpile_mix_only"
        }
        modeller.decision_point_results = pd.DataFrame([{
            "blend_option": 1,
            "source": "SP1",
            "source_type": "stockpile",
            "source_actual_tonnes": 100,
        }])
        signature = modeller.blend_signature_from_dataframe(
            modeller.decision_point_results
        )
        modeller.reserved_blend_signatures = {signature}
        modeller.selected_blend_signatures = set()
        modeller.contingency_reuse_fallbacks = 0

        self.assertEqual(modeller.select_automatic_blend_option(), 1)
        self.assertEqual(modeller.contingency_reuse_fallbacks, 1)
        self.assertIn(signature, modeller.selected_blend_signatures)

    def test_grouped_grade_blocks_weight_raw_stream_audit_fields(self):
        modeller = CaseModeller.__new__(CaseModeller)
        rows = pd.DataFrame([
            {
                "steady_state_number": 1,
                "source": "BLOCK_A",
                "source_id": "PAYLOAD_1",
                "source_type": "grade_block",
                "source_actual_tonnes": 25.0,
                "crusher_actual_tonnes": 100.0,
                "source_grade_fe": 60.0,
                "source_grade_adjusted_product_fe": 62.0,
                "source_property_prod1_wmt": 20.0,
                "source_property_prod1_minus_1mm_pct": 10.0,
            },
            {
                "steady_state_number": 1,
                "source": "BLOCK_A",
                "source_id": "PAYLOAD_2",
                "source_type": "grade_block",
                "source_actual_tonnes": 75.0,
                "crusher_actual_tonnes": 100.0,
                "source_grade_fe": 64.0,
                "source_grade_adjusted_product_fe": 66.0,
                "source_property_prod1_wmt": 60.0,
                "source_property_prod1_minus_1mm_pct": 20.0,
            },
        ])

        grouped = modeller.group_grade_block_rows(rows)

        self.assertEqual(1, len(grouped))
        self.assertAlmostEqual(63.0, grouped.iloc[0]["source_grade_fe"])
        self.assertAlmostEqual(
            65.0,
            grouped.iloc[0]["source_grade_adjusted_product_fe"],
        )
        self.assertAlmostEqual(
            80.0, grouped.iloc[0]["source_property_prod1_wmt"]
        )
        self.assertAlmostEqual(
            17.5,
            grouped.iloc[0]["source_property_prod1_minus_1mm_pct"],
        )

    def test_grouped_grade_block_report_collapses_operational_slices(self):
        modeller = CaseModeller.__new__(CaseModeller)
        rows = pd.DataFrame([
            {
                "steady_state_number": 1,
                "source": "Reserves/CC1/CUE01/LG46_627",
                "source_id": "PAYLOAD_1",
                "source_type": "grade_block",
                "source_actual_tonnes": 40.0,
                "crusher_actual_tonnes": 100.0,
                "crusher_source_tonnes": 32.0,
                "reclaimer_source_tonnes": 40.0,
                "product_build_source_tonnes": 30.0,
                "source_grade_fe": 60.0,
            },
            {
                "steady_state_number": 1,
                "source": "Reserves/CC1/CUE01/LG46_124",
                "source_id": "PAYLOAD_2",
                "source_type": "grade_block",
                "source_actual_tonnes": 60.0,
                "crusher_actual_tonnes": 100.0,
                "crusher_source_tonnes": 48.0,
                "reclaimer_source_tonnes": 60.0,
                "product_build_source_tonnes": 45.0,
                "source_grade_fe": 62.0,
            },
        ])

        grouped = modeller.group_grade_block_rows(rows)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(
            grouped.iloc[0]["source"], "Reserves/CC1/CUE01/LG46"
        )
        self.assertEqual(grouped.iloc[0]["source_actual_tonnes"], 100.0)
        self.assertEqual(grouped.iloc[0]["crusher_source_tonnes"], 80.0)
        self.assertEqual(grouped.iloc[0]["reclaimer_source_tonnes"], 100.0)
        self.assertEqual(
            grouped.iloc[0]["product_build_source_tonnes"], 75.0
        )
        self.assertAlmostEqual(grouped.iloc[0]["source_grade_fe"], 61.2)


class DecisionLeverOptimizerTests(unittest.TestCase):
    @staticmethod
    def event(
        name,
        *,
        balance=1000,
        rate=1000,
        grade_fe=60,
        grade_si=4,
        is_amt=False,
        cost=0,
    ):
        return EventData(
            stockpile=name,
            grade_block=None,
            event_type="stockpile",
            equipment="RC",
            cost=cost,
            cash=0,
            rate=rate,
            grade_fe=grade_fe,
            grade_si=grade_si,
            grade_al=2,
            grade_p=0.08,
            grade_mn=0.1,
            balance=balance,
            max_quantity=1000,
            reclaim_threshold=0,
            state="Reclaim",
            auto_turnover_datetime=None,
            is_amt=is_amt,
            source_name=name,
        )

    @staticmethod
    def target(**overrides):
        target = {
            "crusher_rate": 100,
            "target_fe_min": 0,
            "target_fe_max": 100,
            "target_si_min": 0,
            "target_si_max": 100,
            "target_al_min": 0,
            "target_al_max": 100,
            "target_p_min": 0,
            "target_p_max": 100,
            "target_mn_min": 0,
            "target_mn_max": 100,
            "direct_feed_ratio_min": 0,
            "direct_feed_ratio_max": 1,
        }
        target.update(overrides)
        return target

    def optimize(self, events, solver_config, **target_overrides):
        periods = PeriodManager()
        periods.calculate_periods(datetime(2026, 1, 1, 0, 0))
        return Optimizer.run_blending_optimization(
            events,
            self.target(**target_overrides),
            1,
            None,
            None,
            periods,
            "preplan",
            solver_config=solver_config,
            excluded_source_sets=[],
        )

    @staticmethod
    def selected_sources(result):
        return {
            transaction["source"]
            for transaction in result["transactions"]
            if transaction["actual_tonnes"]
            > Optimizer.SOLUTION_TOLERANCE
        }

    def test_amt_incentive_prefers_amt_stockpile(self):
        result = self.optimize(
            [
                self.event("WEIGHTED_AVERAGE"),
                self.event("AMT", is_amt=True),
            ],
            {
                "prefer_amt_stockpiles": True,
                "amt_preference_incentive": 7.5,
            },
        )

        self.assertEqual(self.selected_sources(result), {"AMT"})
        self.assertEqual(
            result["diagnostics"]["amt_preference_incentive"],
            7.5,
        )

    def test_destination_turnover_incentive_prefers_later_2wp_reclaim(self):
        def grade_block(name, priority):
            return EventData(
                stockpile=None,
                grade_block=name,
                event_type="grade_block",
                equipment="EX",
                cost=0,
                cash=0,
                rate=100,
                grade_fe=60,
                grade_si=4,
                grade_al=2,
                grade_p=0.08,
                grade_mn=0.1,
                balance=100,
                max_quantity=100,
                reclaim_threshold=0,
                state=None,
                auto_turnover_datetime=None,
                source_name=name,
                two_wp_planned_stockpile_destination=f"SP_{name}",
                two_wp_destination_turnover_priority=priority,
                two_wp_turnover_guidance_applicable=True,
            )

        result = self.optimize(
            [grade_block("EARLY", 0.0), grade_block("LATE", 1.0)],
            {
                "direct_tip_enabled": True,
                "direct_tip_cash_incentive": 0.0,
                "two_wp_destination_turnover_guidance_enabled": True,
                "two_wp_destination_turnover_incentive": 10.0,
            },
        )

        self.assertEqual(self.selected_sources(result), {"LATE"})
        late = next(
            row for row in result["transactions"]
            if row["source"] == "LATE"
        )
        self.assertEqual(
            late["two_wp_destination_turnover_incentive_applied"], 10.0
        )

    def test_material_type_brand_incentive_prefers_matching_expit_block(self):
        def grade_block(source):
            return EventData(
                stockpile=None,
                grade_block=source,
                event_type="grade_block",
                equipment="EX",
                cost=0,
                cash=0,
                rate=100,
                grade_fe=60,
                grade_si=4,
                grade_al=2,
                grade_p=0.08,
                grade_mn=0.1,
                balance=100,
                max_quantity=100,
                reclaim_threshold=0,
                state=None,
                auto_turnover_datetime=None,
                source_name=source,
            )

        ba = "Reserves/CC1/CUE01/01/414/111/417/BA72_63"
        lg = "Reserves/CC1/CUE01/01/414/111/417/LG46_12"
        result = self.optimize(
            [grade_block(ba), grade_block(lg)],
            {
                "direct_tip_enabled": True,
                "direct_tip_cash_incentive": 0.0,
                "target_product_brand": "SF",
            },
            expit_material_brand_incentives=[{
                "material_type": "BA",
                "brand": "SF",
                "incentive_per_tonne": 25.0,
            }],
        )

        self.assertEqual(self.selected_sources(result), {ba})
        selected = next(
            row for row in result["transactions"]
            if row["source"] == ba
        )
        self.assertEqual(selected["expit_material_type"], "BA")
        self.assertEqual(
            selected["expit_material_brand_incentive_applied"], 25.0
        )

    def test_negative_destination_turnover_value_penalises_earlier_reclaim(self):
        def grade_block(name, priority):
            return EventData(
                stockpile=None,
                grade_block=name,
                event_type="grade_block",
                equipment="EX",
                cost=0,
                cash=0,
                rate=100,
                grade_fe=60,
                grade_si=4,
                grade_al=2,
                grade_p=0.08,
                grade_mn=0.1,
                balance=100,
                max_quantity=100,
                reclaim_threshold=0,
                state=None,
                auto_turnover_datetime=None,
                source_name=name,
                two_wp_planned_stockpile_destination=f"SP_{name}",
                two_wp_destination_turnover_priority=priority,
                two_wp_turnover_guidance_applicable=True,
            )

        result = self.optimize(
            [grade_block("EARLY", 0.0), grade_block("LATE", 1.0)],
            {
                "direct_tip_enabled": True,
                "direct_tip_cash_incentive": 0.0,
                "two_wp_destination_turnover_guidance_enabled": True,
                "two_wp_destination_turnover_incentive": -10.0,
            },
        )

        self.assertEqual(self.selected_sources(result), {"LATE"})
        early = next(
            row for row in result["transactions"]
            if row["source"] == "EARLY"
        )
        late = next(
            row for row in result["transactions"]
            if row["source"] == "LATE"
        )
        self.assertEqual(
            early["two_wp_destination_turnover_incentive_applied"], -10.0
        )
        self.assertEqual(
            late["two_wp_destination_turnover_incentive_applied"], 0.0
        )

    def test_same_grade_block_pair_incentive_uses_parent_slice_name(self):
        def grade_block(name):
            return EventData(
                stockpile=None,
                grade_block=name,
                event_type="grade_block",
                equipment="EX",
                cost=0,
                cash=0,
                rate=100,
                grade_fe=60,
                grade_si=4,
                grade_al=2,
                grade_p=0.08,
                grade_mn=0.1,
                balance=100,
                max_quantity=100,
                reclaim_threshold=0,
                state=None,
                auto_turnover_datetime=None,
                source_name=name,
            )

        sibling = "Reserves/CC1/CUE01/LG46_124"
        alternative = "Reserves/CC1/CUE01/LG47_999"
        result = self.optimize(
            [grade_block(sibling), grade_block(alternative)],
            {
                "direct_tip_enabled": True,
                "direct_tip_cash_incentive": 0.0,
                "stay_on_same_grade_block_pair_incentive": 10.0,
                "previous_grade_block_pairs": {
                    "Reserves/CC1/CUE01/LG46_627": ["SP1"]
                },
            },
        )

        self.assertEqual(self.selected_sources(result), {sibling})

    def test_throughput_incentive_controls_cost_tradeoff(self):
        no_incentive_result = self.optimize(
            [self.event("COSTLY", cost=10)],
            {
                "throughput_incentive_per_tonne": 0,
                "source_selection_tie_break_penalty": 0.25,
            },
        )
        positive_incentive_result = self.optimize(
            [self.event("COSTLY", cost=10)],
            {
                "throughput_incentive_per_tonne": 20,
                "source_selection_tie_break_penalty": 0.25,
            },
        )

        self.assertEqual(self.selected_sources(no_incentive_result), set())
        self.assertEqual(
            self.selected_sources(positive_incentive_result),
            {"COSTLY"},
        )
        self.assertEqual(
            no_incentive_result["diagnostics"][
                "throughput_incentive_per_tonne"
            ],
            0,
        )
        self.assertEqual(
            no_incentive_result["diagnostics"][
                "source_selection_tie_break_penalty"
            ],
            0.25,
        )

    def test_product_build_grade_targets_constrain_completion_state(self):
        without_build_target = self.optimize(
            [self.event("LOW_FE", grade_fe=58)],
            {"throughput_incentive_per_tonne": 100},
        )
        with_build_target = self.optimize(
            [self.event("LOW_FE", grade_fe=58)],
            {
                "throughput_incentive_per_tonne": 100,
                "target_product_build": {
                    "target_tonnes": 100,
                    "target_fe_min": 60,
                    "target_fe_max": 100,
                    "target_si_min": 0,
                    "target_si_max": 100,
                    "target_al_min": 0,
                    "target_al_max": 100,
                    "target_p_min": 0,
                    "target_p_max": 100,
                    "target_mn_min": 0,
                    "target_mn_max": 100,
                },
                "target_product_build_state": {
                    "tonnes": 0,
                },
            },
        )

        self.assertEqual(
            {"LOW_FE"}, self.selected_sources(without_build_target)
        )
        self.assertEqual(set(), self.selected_sources(with_build_target))

    def test_byproduct_builds_accumulate_both_lane_quantities_and_grades(self):
        field_suffix = {
            "fe": "fe", "si": "sio2", "al": "al2o3", "p": "p", "mn": "mn"
        }
        event = self.event("CB_SOURCE")
        event.source_properties = {
            "modelled_rom_wmt": 1000.0,
            "prod1_lump_wmt": 400.0,
            "prod1_fines_wmt": 600.0,
            **{
                f"prod1_lump_{field_suffix[grade]}": value
                for grade, value in {
                    "fe": 61.0, "si": 4.0, "al": 2.0, "p": 0.08, "mn": 0.1,
                }.items()
            },
            **{
                f"prod1_fines_{field_suffix[grade]}": value
                for grade, value in {
                    "fe": 55.0, "si": 7.0, "al": 3.0, "p": 0.1, "mn": 0.1,
                }.items()
            },
        }
        open_targets = {
            "target_tonnes": 1000,
            **{
                f"target_{grade}_{bound}": value
                for grade in ("fe", "si", "al", "p", "mn")
                for bound, value in (("min", 0), ("max", 100))
            },
        }
        config = {
            "throughput_incentive_per_tonne": 100,
            "byproducts_enabled": True,
            "crusher_tonnes_stream": "modelled_rom_wmt",
            "reclaimer_tonnes_stream": "modelled_rom_wmt",
            "byproduct_quantity_fields": {
                "lump": "prod1_lump_wmt",
                "fines": "prod1_fines_wmt",
            },
            "byproduct_grade_fields": {
                lane: {
                    grade: f"prod1_{lane}_{field_suffix[grade]}"
                    for grade in ("fe", "si", "al", "p", "mn")
                }
                for lane in ("lump", "fines")
            },
            "source_property_weights": {
                f"prod1_{lane}_{field_suffix[grade]}": f"prod1_{lane}_wmt"
                for lane in ("lump", "fines")
                for grade in ("fe", "si", "al", "p", "mn")
            },
            "target_product_builds": {
                "lump": {**open_targets, "build_name": "FL Lump Build 1"},
                "fines": {**open_targets, "build_name": "SF Fines Build 1"},
            },
            "target_product_build_states": {
                "lump": {"tonnes": 0},
                "fines": {"tonnes": 0},
            },
        }
        result = self.optimize([event], config)
        transaction = result["transactions"][0]
        self.assertAlmostEqual(
            transaction["product_build_lump_source_tonnes"], 40.0
        )
        self.assertAlmostEqual(
            transaction["product_build_fines_source_tonnes"], 60.0
        )
        self.assertEqual(transaction["product_build_lump_grade_fe"], 61.0)
        self.assertEqual(transaction["product_build_fines_grade_fe"], 55.0)

        modeller = CaseModeller.__new__(CaseModeller)
        modeller.current_time = datetime(2026, 1, 1, 6)
        modeller.product_build_settings = []
        modeller.product_build_runtime_states = []
        modeller.byproducts_enabled = True
        modeller.solver_config = {}
        modeller.decision_point_results = pd.DataFrame()
        modeller.steady_state_tracker = 0
        modeller.blend_option = 1
        modeller.blend_ID = 1
        modeller.period_tracker = 0
        modeller.record_results(result)

        recorded = modeller.decision_point_results.iloc[0]
        self.assertAlmostEqual(
            recorded["product_build_lump_source_tonnes"], 40.0
        )
        self.assertAlmostEqual(
            recorded["product_build_fines_source_tonnes"], 60.0
        )
        self.assertAlmostEqual(
            recorded["product_build_lump_grade_weight_fe_tonnes"], 40.0
        )
        self.assertAlmostEqual(
            recorded["product_build_fines_grade_weight_fe_tonnes"], 60.0
        )

        modeller.results = modeller.decision_point_results.copy()
        modeller.product_build_settings = [
            {
                **open_targets,
                "build_id": 1,
                "build_name": "FL Lump Build 1",
                "brand": "FL",
                "byproduct": "lump",
            },
            {
                **open_targets,
                "build_id": 2,
                "build_name": "SF Fines Build 1",
                "brand": "SF",
                "byproduct": "fines",
            },
        ]
        build_report = modeller.build_product_build_report()
        self.assertEqual(
            set(build_report["product_build_lane"]), {"lump", "fines"}
        )
        lane_added = build_report.groupby("product_build_lane")[
            "build_added_tonnes"
        ].first().to_dict()
        self.assertAlmostEqual(lane_added["lump"], 40.0)
        self.assertAlmostEqual(lane_added["fines"], 60.0)

        profile = DrawOptimisedGradeProfiles.__new__(
            DrawOptimisedGradeProfiles
        )
        transformed_builds = profile.transform_product_build_data(
            build_report,
            ["Grade Fe", "Grade Si", "Grade Al", "Grade P", "Grade Mn"],
        )
        self.assertEqual(
            set(transformed_builds["series"]),
            {"Lump Build: FL Lump Build 1", "Fines Build: SF Fines Build 1"},
        )

    def test_earliest_byproduct_completion_shortens_steady_state(self):
        duration, controller, remaining = (
            Optimizer.update_steady_state_duration_for_product_build_completion(
                {
                    "product_build_lump_actual_tonnes": 40.0,
                    "product_build_fines_actual_tonnes": 60.0,
                },
                2.0,
                {
                    "target_product_builds": {
                        "lump": {"build_name": "Lump 1", "target_tonnes": 100},
                        "fines": {"build_name": "Fines 1", "target_tonnes": 100},
                    },
                    "target_product_build_states": {
                        "lump": {"tonnes": 70},
                        "fines": {"tonnes": 70},
                    },
                },
            )
        )
        self.assertAlmostEqual(duration, 1.0)
        self.assertEqual(controller, "Fines 1 complete")
        self.assertEqual(remaining, 30.0)

    def test_shortened_solve_is_pinned_to_product_build_completion_tonnes(self):
        event = self.event("PRODUCT_SOURCE", balance=1000)
        event.max_quantity = 6000
        event.source_properties = {
            "modelled_rom_wmt": 1000.0,
            "modelled_product_wmt": 800.0,
        }
        result = self.optimize(
            [event],
            {
                "throughput_incentive_per_tonne": 100,
                "crusher_tonnes_stream": "modelled_rom_wmt",
                "product_build_tonnes_stream": "modelled_product_wmt",
                "target_product_build": {
                    "build_name": "Build 1",
                    "target_tonnes": 1000,
                    **{
                        f"target_{grade}_{bound}": value
                        for grade in ("fe", "si", "al", "p", "mn")
                        for bound, value in (("min", 0), ("max", 100))
                    },
                },
                "target_product_build_state": {"tonnes": 750},
                "product_build_completion_constraint": {
                    "lane": "product",
                    "tonnes": 250,
                },
            },
            crusher_rate=400,
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            result["product_build_actual_tonnes"],
            250.0,
            places=5,
        )

    def test_dynamic_product_boundary_cannot_return_an_underfilled_micro_state(self):
        optimizer = Optimizer()
        solve_configs = []

        def fake_solve(*args, **kwargs):
            config = dict(args[10] or {})
            solve_configs.append(config)
            if len(solve_configs) == 1:
                return {
                    "Linprog_result_object": SimpleNamespace(success=True),
                    "transactions": [],
                    "product_build_actual_tonnes": 200.0,
                    "steady_state_duration": 2.0,
                }
            return {
                "Linprog_result_object": SimpleNamespace(success=True),
                "transactions": [],
                "product_build_actual_tonnes": 100.0,
                "steady_state_duration": 1.0,
            }

        optimizer.run_blending_optimization = fake_solve
        optimizer.update_steady_state_duration = (
            lambda transactions, duration, current_time, stocks, period:
            (duration, None, None)
        )
        result = optimizer.run_with_dynamic_steady_state(
            [],
            self.target(crusher_rate=100),
            2.0,
            None,
            "preplan",
            datetime(2026, 1, 1),
            [],
            solver_config={
                "target_product_build": {
                    "build_name": "Build 1",
                    "target_tonnes": 100,
                },
                "target_product_build_state": {"tonnes": 0},
            },
        )

        self.assertEqual(2, len(solve_configs))
        self.assertEqual(
            solve_configs[1]["product_build_completion_constraint"],
            {
                "lane": "product",
                "tonnes": 100.0,
                "build_boundary": "Build 1 complete",
            },
        )
        self.assertEqual(result["product_build_actual_tonnes"], 100.0)
        self.assertEqual(result["steady_state_duration"], 1.0)

    def test_byproduct_progress_tracks_lanes_independently(self):
        report = pd.DataFrame([{
            "steady_state_number": 0,
            "crusher_actual_tonnes": 100.0,
            "product_build_lump_source_tonnes": 40.0,
            "product_build_fines_source_tonnes": 60.0,
            **{
                f"product_build_lump_grade_{grade}": 60.0
                for grade in ("fe", "si", "al", "p", "mn")
            },
            **{
                f"product_build_fines_grade_{grade}": 55.0
                for grade in ("fe", "si", "al", "p", "mn")
            },
            **{
                f"product_build_lump_grade_weight_{grade}_tonnes": 40.0
                for grade in ("fe", "si", "al", "p", "mn")
            },
            **{
                f"product_build_fines_grade_weight_{grade}_tonnes": 60.0
                for grade in ("fe", "si", "al", "p", "mn")
            },
        }])
        build_bounds = {
            **{
                f"target_{grade}_{bound}": value
                for grade in ("fe", "si", "al", "p", "mn")
                for bound, value in (("min", 0), ("max", 100))
            }
        }
        annotated = ProductBuildProgress.annotate(report, [
            {
                **build_bounds, "build_id": 1, "build_name": "Lump 1",
                "brand": "FL", "byproduct": "lump", "target_tonnes": 40,
            },
            {
                **build_bounds, "build_id": 2, "build_name": "Fines 1",
                "brand": "SF", "byproduct": "fines", "target_tonnes": 100,
            },
        ])
        self.assertTrue(annotated.iloc[0]["product_build_lump_complete"])
        self.assertEqual(40.0, annotated.iloc[0]["product_build_lump_closing_tonnes"])
        self.assertFalse(annotated.iloc[0]["product_build_fines_complete"])
        self.assertEqual(60.0, annotated.iloc[0]["product_build_fines_closing_tonnes"])

    def test_calendar_targets_remain_hard_when_offspec_builds_are_enabled(self):
        result = self.optimize(
            [self.event("LOW_FE", grade_fe=58)],
            {
                "throughput_incentive_per_tonne": 100,
                "allow_offspec_steady_states_for_product_build": True,
                "product_builds_configured": True,
                "enforce_calendar_crusher_grade_targets": True,
            },
            target_fe_min=60,
        )

        self.assertEqual(set(), self.selected_sources(result))

    def test_build_targets_are_hard_when_completion_is_outside_horizon(self):
        build_target = {
            "target_tonnes": 1000,
            "target_fe_min": 60,
            "target_fe_max": 100,
            "target_si_min": 0,
            "target_si_max": 100,
            "target_al_min": 0,
            "target_al_max": 100,
            "target_p_min": 0,
            "target_p_max": 100,
            "target_mn_min": 0,
            "target_mn_max": 100,
        }
        common_config = {
            "throughput_incentive_per_tonne": 100,
            "allow_offspec_steady_states_for_product_build": True,
            "enforce_calendar_crusher_grade_targets": False,
            "target_product_build": build_target,
            "target_product_build_state": {"tonnes": 0},
        }
        outside_horizon = self.optimize(
            [self.event("LOW_FE", grade_fe=58)],
            {**common_config, "active_product_build_completes_within_horizon": False},
        )
        within_horizon = self.optimize(
            [self.event("LOW_FE", grade_fe=58)],
            {**common_config, "active_product_build_completes_within_horizon": True},
        )
        hard_repair = self.optimize(
            [self.event("LOW_FE", grade_fe=58)],
            {
                **common_config,
                "active_product_build_completes_within_horizon": True,
                "force_product_build_state_grades_on_spec": True,
            },
        )

        self.assertEqual(set(), self.selected_sources(outside_horizon))
        self.assertEqual({"LOW_FE"}, self.selected_sources(within_horizon))
        self.assertEqual(set(), self.selected_sources(hard_repair))

    def test_active_product_build_target_and_grade_metal_persist(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.solver_config = {}
        modeller.current_time = datetime(2026, 1, 1, 6)
        modeller.periods = PeriodManager()
        modeller.periods.calculate_periods(datetime(2026, 1, 1, 0))
        modeller.crusher_targets = {
            period: {"crusher_rate": 100}
            for period in ("preplan", "period_1", "period_2")
        }
        modeller.product_build_settings = [{
            "build_id": 1,
            "build_name": "Build 1",
            "brand": "SF",
            "target_tonnes": 1000,
            "target_fe_min": 58,
            "target_fe_max": 100,
            "target_si_min": 0,
            "target_si_max": 6,
            "target_al_min": 0,
            "target_al_max": 3,
            "target_p_min": 0,
            "target_p_max": 0.1,
            "target_mn_min": 0,
            "target_mn_max": 0.1,
        }]
        modeller.product_build_runtime_states = [{
            "tonnes": 0.0,
            "grade_fe_metal": 0.0,
            "grade_si_metal": 0.0,
            "grade_al_metal": 0.0,
            "grade_p_metal": 0.0,
            "grade_mn_metal": 0.0,
        }]
        modeller.previous_selected_stockpile_source_ids = set()
        modeller.previous_selected_grade_block_pairs = {}
        modeller.grade_block_pair_locks = {}

        first_config = modeller.solver_config_for_current_step()
        modeller.update_product_build_runtime_state(pd.DataFrame([{
            "source_actual_tonnes": 100,
            "crusher_actual_tonnes": 100,
            "source_grade_fe": 57,
            "source_grade_si": 7,
            "source_grade_al": 3,
            "source_grade_p": 0.1,
            "source_grade_mn": 0.1,
        }]))
        next_config = modeller.solver_config_for_current_step()

        self.assertEqual(
            first_config["target_product_build"],
            next_config["target_product_build"],
        )
        self.assertEqual(
            100,
            next_config["target_product_build_state"]["tonnes"],
        )
        self.assertEqual(
            5700,
            next_config["target_product_build_state"][
                "grade_fe_metal"
            ],
        )

    def test_optimised_report_is_enriched_with_build_progress(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.results = pd.DataFrame([
            {
                "start_datetime": datetime(2026, 1, 1, 6),
                "end_datetime": datetime(2026, 1, 1, 7),
                "steady_state_number": 1,
                "blend_option": 1,
                "blend_ID": 1,
                "source": "SP1",
                "source_type": "stockpile",
                "source_actual_tonnes": 100,
                "crusher_actual_tonnes": 100,
                "source_grade_fe": 57,
                "source_grade_si": 7,
                "source_grade_al": 3,
                "source_grade_p": 0.1,
                "source_grade_mn": 0.1,
            },
            {
                "start_datetime": datetime(2026, 1, 1, 7),
                "end_datetime": datetime(2026, 1, 1, 8),
                "steady_state_number": 2,
                "blend_option": 1,
                "blend_ID": 2,
                "source": "SP2",
                "source_type": "stockpile",
                "source_actual_tonnes": 100,
                "crusher_actual_tonnes": 100,
                "source_grade_fe": 59,
                "source_grade_si": 5,
                "source_grade_al": 2,
                "source_grade_p": 0.08,
                "source_grade_mn": 0.08,
            },
        ])
        modeller.product_build_settings = [{
            "build_id": 1,
            "build_name": "Build 1",
            "brand": "SF",
            "target_tonnes": 200,
            "target_fe_min": 58,
            "target_fe_max": 100,
            "target_si_min": 0,
            "target_si_max": 6,
            "target_al_min": 0,
            "target_al_max": 3,
            "target_p_min": 0,
            "target_p_max": 0.1,
            "target_mn_min": 0,
            "target_mn_max": 0.1,
        }]
        captured = {}

        class Database:
            @staticmethod
            def write_optimised_blend_report_to_database(
                report, _periods
            ):
                captured["report"] = report

        modeller.database_manager = Database()
        modeller.periods = None

        modeller.save_optimised_blend_report()

        progression = captured["report"].sort_values(
            "steady_state_number"
        )
        self.assertEqual(
            [100, 200],
            progression[
                "product_build_closing_tonnes"
            ].tolist(),
        )
        self.assertEqual(
            [57, 58],
            progression["product_build_grade_fe"].tolist(),
        )
        self.assertFalse(
            bool(
                progression.iloc[0][
                    "product_build_current_on_spec"
                ]
            )
        )
        self.assertTrue(
            bool(
                progression.iloc[1][
                    "product_build_complete_on_spec"
                ]
            )
        )

    def test_runtime_offspec_state_is_always_a_repair_candidate(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.results = pd.DataFrame()
        modeller.steady_state_tracker = 4
        modeller.product_build_settings = [{
            "build_id": 1,
            "build_name": "Build 1",
            "brand": "SF",
            "target_tonnes": 100,
            "target_fe_min": 58,
            "target_fe_max": 100,
            "target_si_min": 0,
            "target_si_max": 6,
            "target_al_min": 0,
            "target_al_max": 3,
            "target_p_min": 0,
            "target_p_max": 0.1,
            "target_mn_min": 0,
            "target_mn_max": 0.1,
        }]
        modeller.product_build_runtime_states = [{
            "tonnes": 100,
            "grade_fe_metal": 5700,
            "grade_si_metal": 400,
            "grade_al_metal": 200,
            "grade_p_metal": 8,
            "grade_mn_metal": 8,
        }]

        self.assertEqual(
            modeller.product_build_offspec_steady_states(0),
            [4],
        )

    def test_repair_can_expand_into_earlier_onspec_checkpoints(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.PRODUCT_BUILD_TONNES_TOLERANCE = 0.1
        modeller.product_build_settings = [{
            "target_tonnes": 100,
        }]
        checkpoints = {
            0: {
                "product_build_runtime_states": [{"tonnes": 0}],
            },
            1: {
                "product_build_runtime_states": [{"tonnes": 40}],
            },
            2: {
                "product_build_runtime_states": [{"tonnes": 80}],
            },
            3: {
                "product_build_runtime_states": [{"tonnes": 100}],
            },
        }

        self.assertEqual(
            modeller.product_build_repair_checkpoint_states(
                checkpoints,
                0,
                before_state=3,
            ),
            [2, 1, 0],
        )

    def test_balance_incentive_honours_selected_direction(self):
        result = self.optimize(
            [
                self.event("LOW_BALANCE", balance=100),
                self.event("HIGH_BALANCE", balance=1000),
            ],
            {
                "balance_preference": "higher",
                "balance_preference_incentive": 4.0,
            },
        )

        self.assertEqual(self.selected_sources(result), {"HIGH_BALANCE"})

    def test_contaminated_and_low_fe_incentives_are_configurable(self):
        contaminated_result = self.optimize(
            [
                self.event("CLEAN", grade_si=4),
                self.event("CONTAMINATED", grade_si=8),
            ],
            {
                "prefer_contaminated_stockpiles": True,
                "contaminated_preference_incentive": 3.0,
                "contaminant_thresholds": {
                    "si": 5,
                    "al": 100,
                    "p": 100,
                    "mn": 100,
                },
            },
        )
        low_fe_result = self.optimize(
            [
                self.event("HIGH_FE", grade_fe=62),
                self.event("LOW_FE", grade_fe=55),
            ],
            {
                "prefer_low_fe_stockpiles": True,
                "low_fe_preference_incentive": 6.0,
                "low_fe_threshold": 58,
            },
        )

        self.assertEqual(
            self.selected_sources(contaminated_result),
            {"CONTAMINATED"},
        )
        self.assertEqual(self.selected_sources(low_fe_result), {"LOW_FE"})

    def test_fewer_stockpiles_incentive_prefers_single_source_solution(self):
        result = self.optimize(
            [
                self.event("FULL_CAPACITY", balance=100, rate=100),
                self.event("HALF_ONE", balance=50, rate=50),
                self.event("HALF_TWO", balance=50, rate=50),
            ],
            {
                "prefer_fewer_stockpiles": True,
                "fewer_stockpiles_incentive": 25.0,
            },
        )

        self.assertEqual(
            self.selected_sources(result),
            {"FULL_CAPACITY"},
        )
        self.assertEqual(
            result["diagnostics"]["fewer_stockpiles_incentive"],
            25.0,
        )


if __name__ == "__main__":
    unittest.main()
