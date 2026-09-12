import unittest
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from classes.ManualBlendPlanner import (
    ManualBlendPlanner,
    ManualBlendPlanningError,
)
from classes.OptimisedToManualPlan import OptimisedToManualPlan
from classes.ReportColumns import balance_triplet_columns
from database.DatabaseContext import get_database_path, set_database_path
from GUI.InitialiseGUI import UserInputs
from GUI.ManualBlendDash import ManualBlendDash


class OptimisedManualPrepopulationTests(unittest.TestCase):
    def report_row(
        self,
        state,
        source,
        tonnes,
        source_type="stockpile",
        source_id=None,
        start=None,
        duration=1,
        crusher_tonnes=100,
    ):
        start = start or datetime(2025, 1, 1, 6) + timedelta(
            hours=state - 1
        )
        return {
            "steady_state_number": state,
            "start_datetime": start,
            "end_datetime": start + timedelta(hours=duration),
            "blend_option": 1,
            "blend_ID": state,
            "source": source,
            "source_id": source_id or source,
            "source_type": source_type,
            "source_actual_tonnes": tonnes,
            "crusher_actual_tonnes": crusher_tonnes,
            "crusher_rate_output": crusher_tonnes / duration,
            "source_grade_fe": 60 if source_type == "stockpile" else 64,
            "source_grade_si": 4,
            "source_grade_al": 2,
            "source_grade_p": 0.08,
            "source_grade_mn": 0.1,
            "equipment": "RC" if source_type == "stockpile" else "EX",
        }

    def test_distinct_stockpile_ratios_become_distinct_manual_blends(self):
        report = pd.DataFrame([
            self.report_row(1, "SP1", 60),
            self.report_row(1, "SP2", 40),
            self.report_row(2, "SP1", 50),
            self.report_row(2, "SP2", 50),
        ])

        transfer = OptimisedToManualPlan(report).build()

        self.assertEqual(2, transfer["blend_count"])
        self.assertEqual(
            ["1", "2"],
            [row["Blend ID"] for row in transfer["sequence_rows"]],
        )
        self.assertEqual(
            "SP1, SP2",
            transfer["blend_definitions"][0]["Sources"],
        )
        self.assertEqual(
            "0.600000, 0.400000",
            transfer["blend_definitions"][0]["Source Ratios"],
        )

    def test_truncated_saved_timestamps_preserve_exact_duration_and_calendar_caps(self):
        start = datetime(2025, 1, 1, 6)
        durations = [1 + .75 / 3600, 1 - .75 / 3600]
        rows = []
        for index, duration in enumerate(durations):
            row = self.report_row(index + 1, 'SP1', 2000 * duration,
                start=start + timedelta(hours=index), duration=1, crusher_tonnes=2000 * duration)
            row.update(steady_state_duration=duration, crusher_rate_output=2000)
            rows.append(row)
        original = pd.DataFrame(rows)
        transfer = OptimisedToManualPlan(original).build()
        sequence = transfer['sequence_rows']
        self.assertEqual(sequence[0]['_exact_end'], '2025-01-01 07:00:00.750000')
        self.assertEqual(sequence[1]['_exact_start'], sequence[0]['_exact_end'])
        self.assertEqual(sequence[1]['_exact_end'], '2025-01-01 08:00:00')
        planner = ManualBlendPlanner(sequence, transfer['blend_definitions'],
            {'SP1': {'balance': 10000, 'grade_fe': 60}}, [], pd.DataFrame(),
            {'preplan_start': start, 'preplan_end': start + timedelta(hours=2)}, [], 2000,
            {'crusher_rate': {'Preplan': 6000}, 'reclaim_equipment_max_reclaim_rate': {'Preplan': 2000}})
        report = planner.build_report(planner.build_steady_states(), {})
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 4000)
        self.assertLessEqual(report.equipment_rate_output.max(), 2000.000001)
        self.assertEqual(original.iloc[0].end_datetime, start + timedelta(hours=1))

    def test_fractional_duration_keeps_a_boundary_truck_in_its_optimised_state(self):
        start = datetime(2025, 1, 1, 6)
        durations = [1 + .75 / 3600, 1 - .75 / 3600]
        rows = []
        for index, duration in enumerate(durations):
            total = 2000 * duration + (100 if index else 0)
            row = self.report_row(index + 1, 'SP1', 2000 * duration,
                start=start + timedelta(hours=index), duration=1, crusher_tonnes=total)
            row.update(steady_state_duration=duration, crusher_rate_output=total / duration)
            rows.append(row)
        truck = self.report_row(2, 'GB1', 100, source_type='grade_block', source_id='TRUCK1',
            start=start + timedelta(hours=1), crusher_tonnes=total)
        truck.update(steady_state_duration=durations[1], crusher_rate_output=total / durations[1])
        rows.append(truck)
        transfer = OptimisedToManualPlan(pd.DataFrame(rows)).build()
        payloads = pd.DataFrame([dict(source='GB1', payload=100, direct_tip_id='TRUCK1',
            direct_tip_eligible=True, delivered_datetime=start + timedelta(hours=1))])
        planner = ManualBlendPlanner(transfer['sequence_rows'], transfer['blend_definitions'],
            {'SP1': {'balance': 10000, 'grade_fe': 60}}, [], payloads,
            {'preplan_start': start, 'preplan_end': start + timedelta(hours=2)}, [], 6000,
            {'crusher_rate': {'Preplan': 6000}, 'reclaim_equipment_max_reclaim_rate': {'Preplan': 2000}})
        states = planner.build_steady_states()
        self.assertEqual(states[0]['direct_tip_candidates'], [])
        self.assertEqual(states[1]['direct_tip_candidates'][0]['direct_tip_ids'], ['TRUCK1'])
        allocations = OptimisedToManualPlan.direct_tip_allocations(states, transfer['direct_tip_rows'])
        report = planner.build_report(states, allocations)
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 4100)
        self.assertAlmostEqual(sum(sum(values.values()) for values in allocations.values()), 100)
        # A manually changed window cannot admit earlier trucks.
        states[1]['payload_start_datetime'] = start
        planner.attach_direct_tip_candidates(states)
        self.assertEqual(states[1]['direct_tip_candidates'], [])

    def test_amt_chunk_report_rows_restore_parent_for_manual_inventory(self):
        row = self.report_row(
            1,
            "GAR01_RP00_0002_CHUNK_001",
            10786.7,
            source_id="GAR01_RP00_0002_CHUNK_001",
        )
        row["parent_stockpile"] = "GAR01_RP00_0002"

        transfer = OptimisedToManualPlan(pd.DataFrame([row])).build()

        self.assertEqual(
            "GAR01_RP00_0002",
            transfer["blend_definitions"][0]["Sources"],
        )

    def test_report_balance_triplets_are_adjacent_and_ordered(self):
        columns = [
            "source_property_modelled_product_wmt",
            "source_closing_balance",
            "source_property_modelled_product_wmt_closing_balance",
            "source_actual_tonnes",
            "source_opening_balance",
            "source_property_modelled_product_wmt_opening_balance",
            "source_grade_fe",
        ]

        ordered = balance_triplet_columns(columns)

        self.assertEqual(
            [
                "source_property_modelled_product_wmt_opening_balance",
                "source_property_modelled_product_wmt",
                "source_property_modelled_product_wmt_closing_balance",
            ],
            ordered[:3],
        )
        source_start = ordered.index("source_opening_balance")
        self.assertEqual(
            [
                "source_opening_balance",
                "source_actual_tonnes",
                "source_closing_balance",
            ],
            ordered[source_start:source_start + 3],
        )

    def test_optimised_blend_ids_are_not_collapsed_by_same_ratio(self):
        report = pd.DataFrame([
            self.report_row(1, "SP1", 60),
            self.report_row(1, "SP2", 40),
            self.report_row(2, "SP1", 60),
            self.report_row(2, "SP2", 40),
        ])

        transfer = OptimisedToManualPlan(report).build()

        self.assertEqual(2, transfer["blend_count"])
        self.assertEqual(
            ["1", "2"],
            [row["Blend ID"] for row in transfer["sequence_rows"]],
        )

    def test_direct_tip_blend_preserves_full_feed_ratios_and_rates(self):
        start = datetime(2025, 1, 1, 6)
        report = pd.DataFrame([
            self.report_row(
                1, "SP1", 180, start=start, duration=0.1,
                crusher_tonnes=400,
            ),
            self.report_row(
                1, "SP2", 180, start=start, duration=0.1,
                crusher_tonnes=400,
            ),
            self.report_row(
                1, "GB1", 40, "grade_block", source_id="DT1",
                start=start, duration=0.1, crusher_tonnes=400,
            ),
        ])
        report["period"] = "Preplan"

        transfer = OptimisedToManualPlan(report).build()
        definition = transfer["blend_definitions"][0]
        config = transfer["blend_config_table_inputs"]["1"]

        self.assertEqual("0.450000, 0.450000", definition["Source Ratios"])
        self.assertEqual("GB1", definition["Direct Tip Sources"])
        self.assertEqual("0.100000", definition["Direct Tip Ratios"])
        self.assertEqual("40.0", definition["Direct Tip Tonnes"])
        self.assertAlmostEqual(0.9, sum(config["source_ratios"]))
        self.assertEqual([1800.0, 1800.0], config["reclaim_rates"])
        self.assertEqual(4000.0, config["crusher_rate"])
        self.assertEqual(
            4000.0, transfer["period_crusher_rates"]["Preplan"]
        )

    def test_optimised_handover_preserves_physical_and_product_quantities(self):
        rows = [
            self.report_row(1, "SP1", 60, crusher_tonnes=80),
            self.report_row(1, "SP2", 40, crusher_tonnes=80),
        ]
        rows[0]["product_build_source_tonnes"] = 30
        rows[1]["product_build_source_tonnes"] = 20

        state = OptimisedToManualPlan(pd.DataFrame(rows)).build()[
            "sequence_rows"
        ][0]

        self.assertEqual(100.0, state["_physical_feed_tonnes"])
        self.assertEqual(
            {"SP1": 60.0, "SP2": 40.0},
            state["_stockpile_source_tonnes"],
        )
        self.assertEqual(50.0, state["_product_build_actual_tonnes"])

    def test_selected_direct_tip_tonnes_transfer_by_payload_id(self):
        report = pd.DataFrame([
            self.report_row(1, "SP1", 80),
            self.report_row(
                1, "GB1", 20, "grade_block", source_id="DT1"
            ),
        ])
        transfer = OptimisedToManualPlan(report).build()
        sequence = transfer["sequence_rows"]
        payloads = pd.DataFrame([{
            "source": "GB1",
            "direct_tip_id": "DT1",
            "payload": 40,
            "delivered_datetime": datetime(2025, 1, 1, 6, 10),
            "direct_tip_eligible": True,
            "source_grade_fe": 64,
        }])
        planner = ManualBlendPlanner(
            sequence,
            transfer["blend_definitions"],
            {"SP1": {"balance": 1000, "grade_fe": 60}},
            [],
            payloads,
            {},
            [],
            999,
        )
        states = planner.build_steady_states()
        allocations = OptimisedToManualPlan.direct_tip_allocations(
            states, transfer["direct_tip_rows"]
        )
        report = planner.build_report(states, allocations)

        self.assertEqual(20, allocations[states[0]["state_key"]]["GB1"])
        self.assertEqual(
            20, sequence[0]["Direct Tip Tonnes"]
        )
        self.assertEqual(
            0.2, sequence[0]["Direct Tip Ratio"]
        )
        direct_tip = report[report["source_type"] == "grade_block"]
        self.assertEqual(20, direct_tip.iloc[0]["source_actual_tonnes"])
        self.assertEqual(100, report.iloc[0]["crusher_actual_tonnes"])
        self.assertEqual(100, report.iloc[0]["crusher_rate_output"])

    def test_parent_grade_block_transfer_allocates_to_underlying_slices(self):
        parent = "Reserves/CC2/EYR88/01/441/104/444/LG04"
        start = datetime(2025, 1, 1, 6)
        state_key = "state_0"
        manual_states = [{
            "state_key": state_key,
            "start_datetime": start,
            "end_datetime": start + timedelta(hours=1),
            "direct_tip_candidates": [
                {
                    "source": f"{parent}_627",
                    "direct_tip_ids": ["DT1"],
                    "available_tonnes": 400,
                },
                {
                    "source": f"{parent}_124",
                    "direct_tip_ids": ["DT2"],
                    "available_tonnes": 600,
                },
            ],
        }]
        selected = [{
            "optimised_steady_state": 0,
            "start_datetime": start,
            "end_datetime": start + timedelta(hours=1),
            "source": parent,
            "source_ids": ["DT1", "DT2"],
            "selected_tonnes": 872.5,
        }]

        allocations = OptimisedToManualPlan.direct_tip_allocations(
            manual_states, selected
        )

        self.assertEqual(
            {
                f"{parent}_627": 400,
                f"{parent}_124": 472.5,
            },
            allocations[state_key],
        )

    def roundoff_direct_tip_plan(self, stockpile_tonnes=0):
        # A real optimiser result rounds the sum of 35 payloads up by 12 g.
        requested = 6838.426312
        available = 6838.426299986162
        source = "Reserves/CC2/EYR88/01/441/133/444/BA01"
        rows = [self.report_row(
            1, source, requested, "grade_block", source_id="DT1",
            crusher_tonnes=requested + stockpile_tonnes,
        )]
        if stockpile_tonnes:
            rows.append(self.report_row(
                1, "SP1", stockpile_tonnes,
                crusher_tonnes=requested + stockpile_tonnes,
            ))
        transfer = OptimisedToManualPlan(pd.DataFrame(rows)).build()
        planner = ManualBlendPlanner(
            transfer["sequence_rows"],
            transfer["blend_definitions"],
            {"SP1": {"balance": stockpile_tonnes, "grade_fe": 60}},
            [],
            pd.DataFrame([{
                "source": source,
                "direct_tip_id": "DT1",
                "payload": available,
                "delivered_datetime": datetime(2025, 1, 1, 6, 10),
                "direct_tip_eligible": True,
                "source_grade_fe": 64,
            }]),
            {},
            [],
            requested + stockpile_tonnes,
        )
        return planner, transfer, available, source

    def test_solver_roundoff_transfers_only_available_payload_tonnes(self):
        for stockpile_tonnes in (0, 100):
            with self.subTest(stockpile_tonnes=stockpile_tonnes):
                planner, transfer, available, source = (
                    self.roundoff_direct_tip_plan(stockpile_tonnes)
                )
                states = planner.build_steady_states()
                allocations = OptimisedToManualPlan.direct_tip_allocations(
                    states, transfer["direct_tip_rows"]
                )
                report = planner.build_report(states, allocations)

                self.assertEqual(
                    available, allocations[states[0]["state_key"]][source]
                )
                direct_tip = report[report["source_type"] == "grade_block"]
                self.assertEqual(
                    available, direct_tip.iloc[0]["source_actual_tonnes"]
                )
                self.assertEqual(0, direct_tip.iloc[0]["source_closing_balance"])
                self.assertAlmostEqual(
                    available + stockpile_tonnes,
                    report.iloc[0]["crusher_actual_tonnes"],
                )

    def test_direct_tip_shortage_exceeding_solver_roundoff_is_rejected(self):
        planner, transfer, available, _source = self.roundoff_direct_tip_plan()
        states = planner.build_steady_states()
        transfer["direct_tip_rows"][0]["selected_tonnes"] = available + 0.01

        with self.assertRaisesRegex(
            ManualBlendPlanningError, r"Could not transfer 0\.01 t"
        ):
            OptimisedToManualPlan.direct_tip_allocations(
                states, transfer["direct_tip_rows"]
            )

    def test_roundoff_tolerance_does_not_allow_reusing_payload_capacity(self):
        planner, transfer, available, _source = self.roundoff_direct_tip_plan()
        states = planner.build_steady_states()
        selected = transfer["direct_tip_rows"][0]
        selected["selected_tonnes"] = available

        with self.assertRaises(ManualBlendPlanningError):
            OptimisedToManualPlan.direct_tip_allocations(
                states, [selected, selected.copy()]
            )

    def test_roundoff_tolerance_does_not_allow_missing_direct_tip_source(self):
        planner, transfer, _available, _source = self.roundoff_direct_tip_plan()
        states = planner.build_steady_states()
        states[0]["direct_tip_candidates"] = []

        with self.assertRaises(ManualBlendPlanningError):
            OptimisedToManualPlan.direct_tip_allocations(
                states, transfer["direct_tip_rows"]
            )

    def test_direct_tip_only_validation_handles_roundoff_without_overallocation(self):
        planner, _transfer, available, source = self.roundoff_direct_tip_plan()
        states = planner.build_steady_states()
        key = states[0]["state_key"]
        self.assertTrue(planner.validate_allocations(
            states, {key: {source: available}}
        ))
        for amount in (available + 0.00001, available - 0.01):
            with self.subTest(amount=amount):
                with self.assertRaises(ManualBlendPlanningError):
                    planner.validate_allocations(states, {key: {source: amount}})

    def test_fixed_import_preserves_exact_seconds(self):
        start = datetime(2025, 1, 1, 6, 0, 30)
        report = pd.DataFrame([
            self.report_row(
                1, "SP1", 75, start=start, duration=0.75,
                crusher_tonnes=75,
            ),
        ])
        transfer = OptimisedToManualPlan(report).build()
        planner = ManualBlendPlanner(
            transfer["sequence_rows"],
            transfer["blend_definitions"],
            {"SP1": {"balance": 1000, "grade_fe": 60}},
            [],
            pd.DataFrame(),
            {},
            [],
            999,
        )
        state = planner.build_steady_states()[0]

        self.assertEqual(start, state["start_datetime"])
        self.assertEqual(
            start + timedelta(minutes=45),
            state["end_datetime"],
        )
        self.assertEqual("Optimised decision point", state["trigger"])
        self.assertEqual(100, state["crusher_rate"])

    def test_direct_tip_only_state_is_transferred_at_full_feed(self):
        report = pd.DataFrame([
            self.report_row(
                1, "GB1", 100, "grade_block", source_id="DT1"
            ),
        ])
        transfer = OptimisedToManualPlan(report).build()
        payloads = pd.DataFrame([{
            "source": "GB1",
            "direct_tip_id": "DT1",
            "payload": 100,
            "delivered_datetime": datetime(2025, 1, 1, 6, 10),
            "direct_tip_eligible": True,
            "source_grade_fe": 64,
        }])
        planner = ManualBlendPlanner(
            transfer["sequence_rows"],
            transfer["blend_definitions"],
            {},
            [],
            payloads,
            {},
            [],
            100,
        )
        states = planner.build_steady_states()
        allocations = OptimisedToManualPlan.direct_tip_allocations(
            states, transfer["direct_tip_rows"]
        )
        manual_report = planner.build_report(states, allocations)

        self.assertEqual(
            "", transfer["blend_definitions"][0]["Sources"]
        )
        self.assertEqual(1, len(manual_report))
        self.assertEqual(
            "grade_block", manual_report.iloc[0]["source_type"]
        )
        self.assertEqual(
            1, manual_report.iloc[0]["actual_direct_tip_ratio"]
        )

    def test_optimised_payload_ids_restore_manual_eligibility(self):
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "scenario.db")
            connection = sqlite3.connect(database_path)
            try:
                pd.DataFrame([{
                    "direct_tip_id": "GB_000123",
                    "source": "GB1",
                    "payload": 100,
                    "delivered_datetime": "2025-01-01 06:10:00",
                    "aps_direct_tip_candidate": 0,
                }]).to_sql(
                    "expit_payload_transactions",
                    connection,
                    index=False,
                )
            finally:
                connection.close()
            try:
                set_database_path(database_path)
                fake = SimpleNamespace(
                    is_direct_tip_enabled=lambda: True,
                    file_path_24hr_choice="",
                    fetch_optimised_blend_report=lambda: pd.DataFrame([{
                        "source_type": "grade_block",
                        "source_id": "GB_000123",
                    }]),
                )
                transactions = (
                    UserInputs.manual_expit_payload_transactions(fake)
                )
            finally:
                set_database_path(previous_path)

        self.assertTrue(
            bool(transactions.iloc[0]["direct_tip_eligible"])
        )

    def test_manual_gantt_formats_ratios_and_durations(self):
        html = ManualBlendDash.build_timeline_html(
            ManualBlendDash.__new__(ManualBlendDash)
        )

        self.assertIn("numericRatio * 100", html)
        self.assertIn("(numericRatio * 100).toFixed(2)", html)
        self.assertIn("durationHours.toFixed(1)", html)
        self.assertIn("Direct Tip Grade Blocks:", html)
        self.assertIn('row["Direct Tip Sources"]', html)

    def test_unchanged_sequence_submission_preserves_optimised_rate(self):
        existing = {
            "Blend ID": "4",
            "Start Datetime": "2025-01-01 06:00",
            "Duration (hrs)": 2.5,
            "End Datetime": "2025-01-01 08:30",
            "_fixed_steady_state": True,
            "_crusher_rate": 2181.6667,
            "_exact_start": "2025-01-01 06:00:15",
            "_exact_end": "2025-01-01 08:30:15",
        }
        submitted = {
            "Blend ID": "4",
            "Start Datetime": "2025-01-01 06:00",
            "Duration (hrs)": "2.5",
            "End Datetime": "2025-01-01 08:30",
        }
        fake = SimpleNamespace(
            stored_blend_sequence_table_for_gantt=[existing]
        )

        rows = UserInputs.preserve_optimised_sequence_metadata(
            fake, [submitted]
        )

        self.assertTrue(rows[0]["_fixed_steady_state"])
        self.assertEqual(2181.6667, rows[0]["_crusher_rate"])
        self.assertEqual(
            "2025-01-01 06:00:15", rows[0]["_exact_start"]
        )

    def test_gantt_payload_converts_pandas_scalars_for_json(self):
        value = pd.Series([7], dtype="int64").iloc[0]
        safe = ManualBlendDash.json_safe_value({
            "_optimised_steady_state": value,
            "at": pd.Timestamp("2025-01-01 06:00:00"),
        })

        self.assertIs(type(safe["_optimised_steady_state"]), int)
        self.assertEqual("2025-01-01T06:00:00", safe["at"])

    def test_setup_resubmit_can_preserve_optimised_sequence(self):
        existing = [{
            "Blend ID": "6",
            "_fixed_steady_state": True,
            "Direct Tip Tonnes": 250,
        }]
        saved_blends = [{"Blend ID": "6"}]

        self.assertTrue(
            UserInputs.can_preserve_optimised_sequence(
                existing, saved_blends
            )
        )
        self.assertFalse(
            UserInputs.can_preserve_optimised_sequence(
                existing, [{"Blend ID": "5"}]
            )
        )

    def test_manual_direct_tip_allocations_populate_legend_metadata(self):
        states = [
            {
                "state_key": "one",
                "blend_ID": "6",
                "feed_capacity_tonnes": 100,
            },
            {
                "state_key": "two",
                "blend_ID": "6",
                "feed_capacity_tonnes": 200,
            },
        ]
        allocations = {
            "one": {"GB2": 10},
            "two": {"GB1": 30, "GB2": 20},
        }

        metadata = UserInputs.manual_direct_tip_legend_metadata(
            states, allocations
        )["6"]

        self.assertEqual("GB1, GB2", metadata["Direct Tip Sources"])
        self.assertEqual(
            "0.100000, 0.100000",
            metadata["Direct Tip Ratios"],
        )
        self.assertEqual("30.00, 30.00", metadata["Direct Tip Tonnes"])

    def test_manual_direct_tip_legend_aggregates_slice_allocations(self):
        parent = "Reserves/CC1/CUE01/01/414/111/417/LG46"
        states = [{
            "state_key": "one",
            "blend_ID": "6",
            "feed_capacity_tonnes": 100,
        }]
        allocations = {
            "one": {
                f"{parent}_627": 20,
                f"{parent}_124": 30,
            }
        }

        metadata = UserInputs.manual_direct_tip_legend_metadata(
            states, allocations
        )["6"]

        self.assertEqual(parent, metadata["Direct Tip Sources"])
        self.assertEqual("0.500000", metadata["Direct Tip Ratios"])
        self.assertEqual("50.00", metadata["Direct Tip Tonnes"])

    def test_fresh_manual_session_resets_all_manual_plan_state(self):
        fake = SimpleNamespace(
            blend_config_table_inputs={"1": {"sources": ["SP1"]}},
            blend_data_from_config_table_inputs={"1": {}},
            saved_blends_for_schedule=[{"Blend ID": "1"}],
            stored_blend_sequence_table_for_gantt=[{"Blend ID": "1"}],
            stored_blend_sequence_table_for_gantt_default=[{"Blend ID": "1"}],
            manual_direct_tip_allocations={"state": {"GB1": 10}},
            manual_steady_states=[{"state_key": "state"}],
            manual_blend_report=pd.DataFrame([{"source": "SP1"}]),
            manual_gantt_legend_and_tooltip=[{"Blend ID": "1"}],
        )

        UserInputs.reset_manual_blending_plan_state(fake)

        self.assertEqual({}, fake.blend_config_table_inputs)
        self.assertEqual([], fake.saved_blends_for_schedule)
        self.assertEqual(
            [], fake.stored_blend_sequence_table_for_gantt
        )
        self.assertEqual({}, fake.manual_direct_tip_allocations)
        self.assertEqual([], fake.manual_steady_states)
        self.assertTrue(fake.manual_blend_report.empty)
        self.assertEqual(
            ManualBlendPlanner.REPORT_COLUMNS,
            list(fake.manual_blend_report.columns),
        )

    def test_reclaim_recalculation_tolerates_stale_imported_blend_id(self):
        class Table:
            def __init__(self):
                self.blocked = False

            def blockSignals(self, blocked):
                previous = self.blocked
                self.blocked = blocked
                return previous

            @staticmethod
            def rowCount():
                return 1

            @staticmethod
            def cellWidget(_row, _column):
                return None

            @staticmethod
            def item(_row, _column):
                return None

            @staticmethod
            def resizeColumnsToContents():
                return None

        fake = SimpleNamespace(
            blend_config_table=Table(),
            manual_crusher_rate_values=lambda: {
                "Preplan": 100,
                "Period_1": 100,
                "Period_2": 100,
            },
            manual_blend_id_options=lambda: ["1", "2", "3", "4", "5"],
            manual_blend_weights_for_row=lambda _row: {"6": 100},
            selected_manual_blend_ids=lambda _widget: [],
        )

        UserInputs.update_reclaim_rate(fake)

        self.assertFalse(fake.blend_config_table.blocked)


if __name__ == "__main__":
    unittest.main()
