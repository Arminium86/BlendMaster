import unittest
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

from classes.EventData import EventData
from classes.CaseModeller import CaseModeller
from classes.MaterialDestinationPlan import MaterialDestinationPlan
from classes.Optimizer import Optimizer
from classes.PeriodManager import PeriodManager


class MaterialDestinationPlanTests(unittest.TestCase):
    def test_simultaneous_direct_tip_keeps_each_physical_destination(self):
        payloads = pd.DataFrame([dict(direct_tip_id='P1', source='GB1', payload=100,
            destination='Stockpiles/SP1', planned_destination='Stockpiles/SP1')])
        feed = pd.DataFrame([dict(source_type='grade_block', source='GB1', source_id='P1',
            source_actual_tonnes=tonnes, tipping_point=point) for point, tonnes in [('A', 40), ('B', 30)]])
        report = MaterialDestinationPlan.build(payloads, feed, 'optimised', crusher_destination='Total_Feed_PC')
        direct = report[report.assigned_destination_type.eq('Crusher')]
        self.assertEqual(dict(zip(direct.assigned_destination, direct.assigned_tonnes)), {'A': 40, 'B': 30})
        self.assertAlmostEqual(report.assigned_tonnes.sum(), 100)

    def test_alternate_destinations_follow_fallback_chain(self):
        payloads = pd.DataFrame([{
            "direct_tip_id": "P1",
            "source": "GB1",
            "payload": 100,
            "destination": "Stockpiles/PLANNED",
            "planned_destination": "Stockpiles/PLANNED",
            "alternate_destination_1": "Stockpiles/PIT_FALLBACK",
            "alternate_destination_2": "Stockpiles/LAST_FALLBACK",
            "two_wp_destination_resolution": "exact_2wp",
            "destination_type": "stockpile",
        }])
        blend_report = pd.DataFrame([{
            "source_type": "grade_block",
            "source": "GB1",
            "source_id": "P1",
            "source_actual_tonnes": 40,
        }])

        report = MaterialDestinationPlan.build(
            payloads,
            blend_report,
            "optimised",
            crusher_destination="Crushers/RCH",
        )

        crusher = report[
            report["assigned_destination_type"].eq("Crusher")
        ].iloc[0]
        stockpile = report[
            report["assigned_destination_type"].eq("Stockpile")
        ].iloc[0]
        self.assertEqual(
            (crusher["alternate_destination_1"], crusher["alternate_destination_2"]),
            ("Stockpiles/PLANNED", "Stockpiles/PIT_FALLBACK"),
        )
        self.assertEqual(
            (stockpile["alternate_destination_1"], stockpile["alternate_destination_2"]),
            ("Stockpiles/PIT_FALLBACK", "Stockpiles/LAST_FALLBACK"),
        )

    def test_assigned_fallback_only_exposes_next_fallback(self):
        payloads = pd.DataFrame([{
            "direct_tip_id": "P1",
            "source": "GB1",
            "payload": 100,
            "destination": "Stockpiles/PIT_FALLBACK",
            "planned_destination": "Stockpiles/PIT_FALLBACK",
            "alternate_destination_1": "Stockpiles/LAST_FALLBACK",
            "two_wp_destination_resolution": "pit_fallback",
        }])

        report = MaterialDestinationPlan.build(
            payloads, pd.DataFrame(), "optimised"
        )

        self.assertEqual(
            report.iloc[0]["assigned_destination"],
            "Stockpiles/PIT_FALLBACK",
        )
        self.assertEqual(
            report.iloc[0]["alternate_destination_1"],
            "Stockpiles/LAST_FALLBACK",
        )
        self.assertEqual(report.iloc[0]["alternate_destination_2"], "")

    def test_report_aggregates_payloads_by_source_and_destination(self):
        payloads = pd.DataFrame([
            {
                "direct_tip_id": f"P{index}",
                "source": "GB1",
                "payload": 100,
                "destination": "Stockpiles/SP1",
                "planned_destination": "Stockpiles/SP1",
                "destination_type": "stockpile",
            }
            for index in range(1, 4)
        ])
        blend_report = pd.DataFrame([{
            "source_type": "grade_block",
            "source": "GB1",
            "source_id": "P1, P2, P3",
            "source_actual_tonnes": 150,
            "steady_state_number": 8,
        }])

        report = MaterialDestinationPlan.build(
            payloads,
            blend_report,
            "optimised",
            crusher_destination="Crushers/RCH",
        )

        self.assertNotIn("payload_id", report.columns)
        self.assertNotIn("steady_state_number", report.columns)
        self.assertEqual(len(report), 2)
        self.assertEqual(
            set(report["assigned_destination"]),
            {"Crushers/RCH", "Stockpiles/SP1"},
        )
        self.assertAlmostEqual(report["assigned_tonnes"].sum(), 300)

    def test_report_supplements_incomplete_grouped_payload_id_list(self):
        payloads = pd.DataFrame([
            {
                "direct_tip_id": f"P{index}",
                "source": "GB1",
                "payload": 100,
                "destination": "Stockpiles/SP1",
                "planned_destination": "Stockpiles/SP1",
                "destination_type": "stockpile",
                "delivered_datetime": datetime(2026, 1, 1, index),
            }
            for index in range(1, 4)
        ])
        blend_report = pd.DataFrame([{
            "source_type": "grade_block",
            "source": "GB1",
            # The grouped report retained all 300 t but omitted P1 from its
            # display-oriented identifier list.
            "source_id": "P2, P3",
            "source_actual_tonnes": 300,
            "steady_state_number": 8,
            "start_datetime": datetime(2026, 1, 1, 0),
            "end_datetime": datetime(2026, 1, 1, 4),
        }])

        report = MaterialDestinationPlan.build(
            payloads,
            blend_report,
            "optimised",
            crusher_destination="Crushers/RCH",
        )

        self.assertEqual(len(report), 1)
        self.assertEqual(report.iloc[0]["assigned_destination"], "Crushers/RCH")
        self.assertAlmostEqual(report.iloc[0]["assigned_tonnes"], 300)
        self.assertAlmostEqual(report.iloc[0]["assigned_ratio"], 1)

    def test_report_tolerates_database_rounding_at_source_capacity(self):
        payloads = pd.DataFrame([{
            "direct_tip_id": "P1",
            "source": "GB1",
            "payload": 1205.4799060839691,
            "destination": "Stockpiles/SP1",
            "planned_destination": "Stockpiles/SP1",
        }])
        blend_report = pd.DataFrame([{
            "source_type": "grade_block",
            "source": "GB1",
            "source_id": "P1",
            "source_actual_tonnes": 1205.47991,
        }])

        report = MaterialDestinationPlan.build(
            payloads,
            blend_report,
            "optimised",
            crusher_destination="Crushers/RCH",
        )

        self.assertEqual(len(report), 1)
        self.assertAlmostEqual(
            report.iloc[0]["assigned_tonnes"],
            payloads.iloc[0]["payload"],
        )

    def test_report_groups_slices_by_full_parent_grade_block_path(self):
        parent = "Reserves/CC1/CUE01/01/414/111/417/LG46"
        payloads = pd.DataFrame([
            {
                "direct_tip_id": "P1",
                "source": f"{parent}_627",
                "payload": 100,
                "planned_destination": "Stockpiles/SP1",
            },
            {
                "direct_tip_id": "P2",
                "source": f"{parent}_124",
                "payload": 200,
                "planned_destination": "Stockpiles/SP1",
            },
        ])
        blend_report = pd.DataFrame([{
            "source_type": "grade_block",
            "source": parent,
            # Force the fallback lookup to find both underlying slices from
            # the parent source rather than relying on explicit payload IDs.
            "source_id": "",
            "source_actual_tonnes": 150,
        }])

        report = MaterialDestinationPlan.build(
            payloads,
            blend_report,
            "optimised",
            crusher_destination="Crushers/RCH",
        )

        self.assertEqual(set(report["grade_block"]), {parent})
        self.assertEqual(len(report), 2)
        self.assertTrue((report["source_tonnes"] == 300).all())
        self.assertAlmostEqual(report["assigned_tonnes"].sum(), 300)
        self.assertAlmostEqual(report["assigned_ratio"].sum(), 1)

    def test_existing_slice_report_is_safely_summarized_to_parent(self):
        parent = "Reserves/CC1/CUE01/01/414/111/417/LG46"
        rows = []
        for suffix, source_tonnes, crusher_tonnes in (
            ("627", 100, 40),
            ("124", 200, 60),
        ):
            for destination, assigned_tonnes in (
                ("Crushers/RCH", crusher_tonnes),
                ("Stockpiles/SP1", source_tonnes - crusher_tonnes),
            ):
                rows.append({
                    "plan_type": "optimised",
                    "plan_id": "Primary",
                    "grade_block": f"{parent}_{suffix}",
                    "planned_2wp_destination": "Stockpiles/SP1",
                    "fallback_destination": "",
                    "two_wp_destination_resolution": "exact",
                    "assigned_destination": destination,
                    "assigned_destination_type": (
                        "Crusher" if destination.startswith("Crushers/")
                        else "Stockpile"
                    ),
                    "source_tonnes": source_tonnes,
                    "assigned_tonnes": assigned_tonnes,
                    "assigned_ratio": assigned_tonnes / source_tonnes,
                    "assignment_source": (
                        "Optimised Direct Tip"
                        if destination.startswith("Crushers/") else "2WP"
                    ),
                })

        report = MaterialDestinationPlan.summarize_parent_grade_blocks(
            pd.DataFrame(rows)
        )

        self.assertEqual(set(report["grade_block"]), {parent})
        self.assertEqual(len(report), 2)
        self.assertTrue((report["source_tonnes"] == 300).all())
        self.assertAlmostEqual(report["assigned_tonnes"].sum(), 300)
        self.assertAlmostEqual(report["assigned_ratio"].sum(), 1)


class MinimumPayloadCommitmentTests(unittest.TestCase):
    @staticmethod
    def payload_event(identifier):
        return EventData(
            stockpile=None,
            grade_block=identifier,
            event_type="grade_block",
            equipment="EX",
            cost=0,
            cash=0,
            rate=1000,
            grade_fe=60,
            grade_si=4,
            grade_al=2,
            grade_p=0.08,
            grade_mn=0.1,
            balance=100,
            max_quantity=100,
            reclaim_threshold=None,
            state=None,
            auto_turnover_datetime=None,
            source_name="GB1",
            delivered_datetime=datetime(2026, 1, 1, 0, 0),
        )

    @staticmethod
    def optimize(crusher_rate):
        periods = PeriodManager()
        periods.calculate_periods(datetime(2026, 1, 1, 0, 0))
        target = {
            "crusher_rate": crusher_rate,
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
        return Optimizer.run_blending_optimization(
            [
                MinimumPayloadCommitmentTests.payload_event("P1"),
                MinimumPayloadCommitmentTests.payload_event("P2"),
            ],
            target,
            1,
            None,
            None,
            periods,
            "preplan",
            solver_config={
                "direct_tip_enabled": True,
                "require_whole_direct_tip_payloads": True,
            },
            excluded_source_sets=[],
        )

    def test_one_and_a_half_payloads_is_allowed(self):
        result = self.optimize(150)
        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            sum(row["actual_tonnes"] for row in result["transactions"]),
            150,
        )

    def test_less_than_one_payload_is_rejected(self):
        result = self.optimize(50)
        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            sum(row["actual_tonnes"] for row in result["transactions"]),
            0,
        )


class SteadyStateBoundaryTests(unittest.TestCase):
    def test_short_parent_delivery_window_is_removed_before_solving(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.current_time = datetime(2026, 8, 14, 18)
        modeller.solver_config = {
            "min_grade_block_pair_duration_hours": 1,
        }
        short_parent = "Reserves/CC2/EYR88/01/441/133/447/SO66"
        valid_parent = "Reserves/CC2/EYR88/01/441/133/447/BA67"
        stockpile = SimpleNamespace(is_grade_block=False)
        events = [
            stockpile,
            SimpleNamespace(
                is_grade_block=True,
                source_name=short_parent + "_225",
                grade_block="GB1",
                delivered_datetime=datetime(2026, 8, 14, 18, 44),
            ),
            SimpleNamespace(
                is_grade_block=True,
                source_name=short_parent + "_232",
                grade_block="GB2",
                delivered_datetime=datetime(2026, 8, 14, 18, 30),
            ),
            SimpleNamespace(
                is_grade_block=True,
                source_name=valid_parent + "_101",
                grade_block="GB3",
                delivered_datetime=datetime(2026, 8, 14, 20),
            ),
        ]

        filtered, descriptions = (
            modeller.filter_grade_block_events_by_pair_duration(events, 12)
        )

        self.assertIn(stockpile, filtered)
        self.assertEqual(
            [valid_parent + "_101"],
            [event.source_name for event in filtered if event.is_grade_block],
        )
        self.assertEqual(1, len(descriptions))
        self.assertIn("SO66", descriptions[0])
        self.assertIn("0.73 hrs", descriptions[0])

    def test_amt_candidate_exclusion_uses_parent_solver_identity(self):
        modeller = CaseModeller.__new__(CaseModeller)
        result = {
            "transactions": [{
                "source": "OPF02_RP01_0207_CHUNK_001",
                "source_id": "OPF02_RP01_0207_CHUNK_001",
                "parent_stockpile": "OPF02_RP01_0207",
                "balance_tracker_source_id": "OPF02_RP01_0207",
                "source_type": "stockpile",
                "actual_tonnes": 100,
            }]
        }

        self.assertEqual(
            ["OPF02_RP01_0207"],
            modeller.active_source_ids_from_result(result),
        )

    def test_grade_block_pair_duration_validation_uses_grouped_window(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.current_time = datetime(2026, 1, 1, 0)
        modeller.solver_config = {
            "min_grade_block_pair_duration_hours": 4,
        }
        result = {
            "steady_state_duration": 12,
            "transactions": [{
                "source": "GB1",
                "source_type": "grade_block",
                "actual_tonnes": 100,
                "opening_balance": 100,
                "estimated_delivery_datetime": datetime(2026, 1, 1, 3),
            }],
        }

        issues = modeller.grade_block_pair_duration_issues_from_result(result)

        self.assertEqual(len(issues), 1)
        self.assertIn("GB1 delivery window is 3.00 hrs", issues[0])

    def test_grade_block_pair_duration_combines_candidate_sibling_slices(self):
        modeller = CaseModeller.__new__(CaseModeller)
        modeller.current_time = datetime(2026, 1, 1, 0)
        modeller.solver_config = {
            "min_grade_block_pair_duration_hours": 4,
        }
        result = {
            "steady_state_duration": 12,
            "transactions": [
                {
                    "source": "Reserves/CC1/CUE01/LG46_627",
                    "source_type": "grade_block",
                    "actual_tonnes": 100,
                    "opening_balance": 100,
                    "estimated_delivery_datetime": datetime(2026, 1, 1, 1),
                },
                {
                    "source": "Reserves/CC1/CUE01/LG46_124",
                    "source_type": "grade_block",
                    "actual_tonnes": 0,
                    "opening_balance": 100,
                    "estimated_delivery_datetime": datetime(2026, 1, 1, 5),
                },
            ],
        }

        issues = modeller.grade_block_pair_duration_issues_from_result(result)

        self.assertEqual(issues, [])

    def test_grouped_payload_delivery_window_remains_available_for_validation(self):
        duration = Optimizer.calculate_grouped_payload_depletion_duration(
            [
                datetime(2026, 1, 1, 1),
                datetime(2026, 1, 1, 3),
            ],
            datetime(2026, 1, 1, 0),
        )

        self.assertAlmostEqual(duration, 3 + (1 / 3600))

    def test_payload_arrival_and_depletion_do_not_shorten_steady_state(self):
        duration, controller, tonnes = Optimizer.update_steady_state_duration(
            [{
                "source": "GB1",
                "source_id": "P1",
                "source_type": "grade_block",
                "opening_balance": 100,
                "actual_tonnes": 100,
                "equipment_rate_input": 1000,
                "estimated_delivery_datetime": datetime(2026, 1, 1, 3),
            }],
            12,
            datetime(2026, 1, 1, 0),
            [],
            "preplan",
        )

        self.assertEqual(duration, 12)
        self.assertEqual(controller, "Null")
        self.assertEqual(tonnes, "Null")

    def test_stockpile_depletion_still_shortens_steady_state(self):
        duration, controller, tonnes = Optimizer.update_steady_state_duration(
            [{
                "source": "SP1",
                "source_id": "SP1",
                "source_type": "stockpile",
                "opening_balance": 100,
                "actual_tonnes": 100,
                "equipment_rate_input": 50,
            }],
            12,
            datetime(2026, 1, 1, 0),
            [],
            "preplan",
        )

        self.assertEqual(duration, 2)
        self.assertEqual(controller, "SP1")
        self.assertEqual(tonnes, 100)


if __name__ == "__main__":
    unittest.main()
