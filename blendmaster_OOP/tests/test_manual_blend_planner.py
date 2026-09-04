import unittest
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

from classes.ManualBlendPlanner import (
    ManualBlendPlanner,
    ManualBlendPlanningError,
)
from classes.ReportColumns import balance_triplet_columns
from GUI.ManualSteadyStateDialog import ManualSteadyStateDialog
from database.DatabaseContext import get_database_path, set_database_path
from database.SQLiteDatabase import DatabaseManager


class ManualBlendPlannerTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2025, 1, 1, 5, 0)
        self.periods = {
            "preplan_start": self.start,
            "preplan_end": datetime(2025, 1, 1, 6, 0),
            "period_1_start": datetime(2025, 1, 1, 6, 0),
            "period_1_end": datetime(2025, 1, 1, 18, 0),
            "period_2_start": datetime(2025, 1, 1, 18, 0),
            "period_2_end": datetime(2025, 1, 2, 6, 0),
        }
        self.sequence = [{
            "Blend ID": "1",
            "Start Datetime": self.start,
            "End Datetime": self.start + timedelta(hours=2),
        }]
        self.blends = [{
            "Blend ID": "1",
            "Sources": "SP1, SP2",
            "Source Ratios": "0.5, 0.5",
        }]
        self.stockpiles = {
            "SP1": {
                "balance": 1000,
                "grade_fe": 60,
                "grade_si": 4,
                "grade_al": 2,
                "grade_p": 0.08,
                "grade_mn": 0.1,
            },
            "SP2": {
                "balance": 1000,
                "grade_fe": 58,
                "grade_si": 6,
                "grade_al": 3,
                "grade_p": 0.1,
                "grade_mn": 0.2,
            },
        }

    def planner(self, payloads=None, builds=None, stockpiles=None):
        return ManualBlendPlanner(
            self.sequence,
            self.blends,
            stockpiles or self.stockpiles,
            [],
            payloads,
            self.periods,
            builds or [],
            crusher_rate=100,
        )

    def test_direct_tip_panel_groups_parent_grade_block_candidates(self):
        parent = "Reserves/CC2/CAT03/01/453/426/456/LG09"
        candidates = [
            {
                "source": f"{parent}_453",
                "available_tonnes": 100,
                "grade_fe": 50,
                "grade_si": 10,
            },
            {
                "source": f"{parent}_285",
                "available_tonnes": 300,
                "grade_fe": 60,
                "grade_si": 6,
            },
        ]

        grouped = ManualSteadyStateDialog.aggregate_parent_candidates(
            candidates
        )

        self.assertEqual(1, len(grouped))
        self.assertEqual(parent, grouped[0]["source"])
        self.assertEqual(400, grouped[0]["available_tonnes"])
        self.assertAlmostEqual(57.5, grouped[0]["grade_fe"])
        self.assertAlmostEqual(7.0, grouped[0]["grade_si"])

    def test_direct_tip_panel_expands_parent_ratio_to_slices(self):
        candidate = {
            "_slice_candidates": [
                {"source": "LG09_453", "available_tonnes": 100},
                {"source": "LG09_285", "available_tonnes": 300},
            ]
        }
        allocations = {}

        ManualSteadyStateDialog.apply_parent_ratio(
            allocations, "state-1", candidate, 0.25
        )

        self.assertEqual(
            {"LG09_453": 25.0, "LG09_285": 75.0},
            allocations["state-1"],
        )
        self.assertEqual(
            100.0,
            ManualSteadyStateDialog.selected_parent_tonnes(
                allocations, "state-1", candidate
            ),
        )

    def test_period_boundary_creates_state_but_payload_arrival_does_not(self):
        payloads = pd.DataFrame([{
            "source": "GB1",
            "payload": 20,
            "delivered_datetime": self.start + timedelta(minutes=30),
            "direct_tip_eligible": True,
            "source_grade_fe": 64,
        }])
        states = self.planner(payloads).build_steady_states()

        self.assertEqual(2, len(states))
        self.assertEqual(datetime(2025, 1, 1, 6, 0), states[0]["end_datetime"])
        self.assertEqual("Period boundary", states[0]["trigger"])
        self.assertEqual(1, len(states[0]["direct_tip_candidates"]))

    def test_period_states_use_their_calendar_crusher_rates(self):
        planner = ManualBlendPlanner(
            self.sequence,
            self.blends,
            self.stockpiles,
            [],
            pd.DataFrame(),
            self.periods,
            [],
            crusher_rate=100,
            calendar_inputs={
                "crusher_rate": {
                    "Preplan": 100,
                    "Period_1": 200,
                    "Period_2": 300,
                }
            },
        )

        states = planner.build_steady_states()

        self.assertEqual([100, 200], [
            state["crusher_rate"] for state in states
        ])
        self.assertEqual([100, 200], [
            state["feed_capacity_tonnes"] for state in states
        ])

    def test_build_completion_creates_state(self):
        builds = [{"target_tonnes": 50}, {"target_tonnes": 1000}]
        states = self.planner(builds=builds).build_steady_states()

        self.assertEqual(
            self.start + timedelta(minutes=30),
            states[0]["end_datetime"],
        )
        self.assertEqual("Product build completion", states[0]["trigger"])

    def test_build_completion_uses_configured_product_quantity(self):
        streams = {
            stream: {"SS": {grade: 60 for grade in ManualBlendPlanner.GRADES}}
            for stream in (
                "insitu", "modelled_rom", "adjusted_rom",
                "modelled_product", "adjusted_product",
            )
        }
        planner = ManualBlendPlanner(
            self.sequence,
            [{
                "Blend ID": "1", "Sources": "SP1", "Source Ratios": "1",
            }],
            {},
            [{
                "footprint": "SP1", "sequence": 1, "hex": "C1",
                "balance": 200,
                "grade_streams": streams,
                "source_properties": {
                    "modelled_rom_wmt": 200,
                    "modelled_product_wmt": 100,
                },
            }],
            pd.DataFrame(),
            self.periods,
            [{"brand": "SS", "target_tonnes": 75}],
            crusher_rate=100,
            calendar_inputs={
                "site_context": {
                    "selected_data_stream": "adjusted_product",
                    "crusher_tonnes_stream": "modelled_rom_wmt",
                    "product_build_tonnes_stream": "modelled_product_wmt",
                },
                "solver_config": {
                    "strict_mapped_fields": True,
                    "source_property_kinds": {
                        "modelled_rom_wmt": "additive",
                        "modelled_product_wmt": "additive",
                    },
                },
            },
        )

        states = planner.build_steady_states()

        self.assertEqual(
            self.start + timedelta(hours=1.5), states[1]["end_datetime"]
        )
        self.assertEqual("Product build completion", states[1]["trigger"])

    def test_repeated_chunk_consumption_depletes_additive_properties(self):
        planner = ManualBlendPlanner(
            self.sequence,
            [{
                "Blend ID": "1", "Sources": "SP1", "Source Ratios": "1",
            }],
            {},
            [{
                "footprint": "SP1", "sequence": 1, "hex": "C1",
                "balance": 200,
                "source_properties": {
                    "modelled_rom_wmt": 200,
                    "modelled_product_wmt": 100,
                },
            }],
            pd.DataFrame(),
            self.periods,
            [],
            crusher_rate=100,
            calendar_inputs={
                "site_context": {
                    "crusher_tonnes_stream": "modelled_rom_wmt",
                    "product_build_tonnes_stream": "modelled_product_wmt",
                },
                "solver_config": {
                    "source_property_kinds": {
                        "modelled_rom_wmt": "additive",
                        "modelled_product_wmt": "additive",
                    },
                    "optimisation_source_property_fields": [
                        "modelled_rom_wmt", "modelled_product_wmt",
                    ],
                },
            },
        )

        report = planner.build_report(planner.build_steady_states(), {})

        self.assertEqual(2, report["steady_state_number"].nunique())
        self.assertAlmostEqual(
            100.0, report["product_build_source_tonnes"].sum()
        )
        self.assertEqual(
            [50.0, 50.0], report["product_build_source_tonnes"].tolist()
        )

    def test_fixed_handover_depletes_inventory_before_user_blend(self):
        sequence = [
            {
                "Blend ID": "1",
                "Start Datetime": self.start,
                "End Datetime": self.start + timedelta(hours=1),
                "_fixed_steady_state": True,
                "_crusher_rate": 50,
                "_physical_feed_tonnes": 100,
                "_stockpile_source_tonnes": {"SP1": 100},
                "_product_build_actual_tonnes": 50,
            },
            {
                "Blend ID": "1",
                "Start Datetime": self.start + timedelta(hours=1),
                "End Datetime": self.start + timedelta(hours=2),
            },
        ]
        planner = ManualBlendPlanner(
            sequence,
            [{"Blend ID": "1", "Sources": "SP1", "Source Ratios": "1"}],
            {},
            [{
                "footprint": "SP1", "sequence": 1, "hex": "C1",
                "balance": 200,
                "source_properties": {
                    "modelled_rom_wmt": 200,
                    "modelled_product_wmt": 100,
                },
            }],
            pd.DataFrame(),
            self.periods,
            [],
            crusher_rate=100,
            calendar_inputs={
                "site_context": {
                    "crusher_tonnes_stream": "modelled_rom_wmt",
                    "product_build_tonnes_stream": "modelled_product_wmt",
                },
                "solver_config": {
                    "source_property_kinds": {
                        "modelled_rom_wmt": "additive",
                        "modelled_product_wmt": "additive",
                    },
                },
            },
        )

        states = planner.build_steady_states()

        self.assertEqual([100, 100], [
            state["feed_capacity_tonnes"] for state in states
        ])
        with self.assertRaises(ManualBlendPlanningError):
            ManualBlendPlanner(
                [*sequence, {
                    "Blend ID": "1",
                    "Start Datetime": self.start + timedelta(hours=2),
                    "End Datetime": self.start + timedelta(hours=3),
                }],
                [{"Blend ID": "1", "Sources": "SP1", "Source Ratios": "1"}],
                {},
                [{
                    "footprint": "SP1", "sequence": 1, "hex": "C1",
                    "balance": 200,
                    "source_properties": {
                        "modelled_rom_wmt": 200,
                        "modelled_product_wmt": 100,
                    },
                }],
                pd.DataFrame(), self.periods, [], crusher_rate=100,
                calendar_inputs=planner.calendar_inputs,
            ).build_steady_states()

    def test_manual_report_tracks_cumulative_build_progress_each_state(self):
        builds = [{
            "build_id": 7,
            "build_name": "SF Build 1",
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
            "target_mn_max": 0.2,
        }]
        planner = self.planner(builds=builds)
        report = planner.build_report(
            planner.build_steady_states(), {}
        )
        progression = (
            report.groupby("steady_state_number", sort=True)
            .first()
            .reset_index()
        )

        self.assertEqual(
            [0, 100],
            progression[
                "product_build_opening_tonnes"
            ].tolist(),
        )
        self.assertEqual(
            [100, 200],
            progression[
                "product_build_closing_tonnes"
            ].tolist(),
        )
        self.assertEqual(
            [100, 0],
            progression[
                "product_build_remaining_tonnes"
            ].tolist(),
        )
        self.assertEqual(
            [False, True],
            progression["product_build_complete"].tolist(),
        )
        self.assertEqual(
            [59, 59],
            progression["product_build_grade_fe"].tolist(),
        )
        self.assertEqual(
            58,
            progression.iloc[1]["product_build_target_fe_min"],
        )
        self.assertTrue(
            bool(
                progression.iloc[1][
                    "product_build_complete_on_spec"
                ]
            )
        )

    def test_amt_chunk_turnover_creates_state(self):
        planner = ManualBlendPlanner(
            self.sequence,
            [{
                "Blend ID": "1",
                "Sources": "SP1",
                "Source Ratios": "1",
            }],
            {"SP1": {"balance": 200, "AMT": True}},
            [
                {"footprint": "SP1", "sequence": 1, "hex": "C1",
                 "balance": 50, "grade_fe": 60},
                {"footprint": "SP1", "sequence": 2, "hex": "C2",
                 "balance": 200, "grade_fe": 55},
            ],
            pd.DataFrame(),
            self.periods,
            [],
            100,
        )
        states = planner.build_steady_states()

        self.assertEqual(
            self.start + timedelta(minutes=30),
            states[0]["end_datetime"],
        )
        self.assertEqual("AMT chunk turnover", states[0]["trigger"])

    def test_direct_tip_displaces_stockpile_and_combines_grades(self):
        payloads = pd.DataFrame([{
            "source": "GB1",
            "direct_tip_id": "DT1",
            "payload": 20,
            "delivered_datetime": self.start + timedelta(minutes=15),
            "direct_tip_eligible": True,
            "source_grade_fe": 64,
            "source_grade_si": 2,
            "source_grade_al": 1,
            "source_grade_p": 0.05,
            "source_grade_mn": 0.05,
        }])
        planner = self.planner(payloads)
        states = planner.build_steady_states()
        first = states[0]
        allocations = {first["state_key"]: {"GB1": 20}}
        report = planner.build_report(states, allocations)
        rows = report[report["steady_state_number"] == 1]

        self.assertAlmostEqual(100, rows.iloc[0]["crusher_actual_tonnes"])
        self.assertAlmostEqual(80, rows["source_actual_tonnes"].sum() - 20)
        self.assertAlmostEqual(1, rows["source_blend_ratio"].sum())
        # 40t SP1 @60 + 40t SP2 @58 + 20t direct tip @64 = 60%.
        self.assertAlmostEqual(60, rows.iloc[0]["crusher_actual_grade_fe"])
        self.assertAlmostEqual(0.2, rows.iloc[0]["actual_direct_tip_ratio"])

    def test_manual_report_resolves_and_exposes_selected_grade_stream(self):
        streams = {
            "insitu": {"*": {grade: 50 for grade in ManualBlendPlanner.GRADES}},
            "modelled_rom": {"*": {grade: 55 for grade in ManualBlendPlanner.GRADES}},
            "adjusted_rom": {"FB": {grade: 57 for grade in ManualBlendPlanner.GRADES}},
            "modelled_product": {"FB": {grade: 62 for grade in ManualBlendPlanner.GRADES}},
            "adjusted_product": {"FB": {grade: 65 for grade in ManualBlendPlanner.GRADES}},
        }
        stockpiles = {
            name: {**values, "grade_streams": streams}
            for name, values in self.stockpiles.items()
        }
        planner = ManualBlendPlanner(
            self.sequence,
            self.blends,
            stockpiles,
            [],
            pd.DataFrame(),
            self.periods,
            [{"brand": "FB", "target_tonnes": 1000}],
            crusher_rate=100,
            calendar_inputs={
                "site_context": {"selected_data_stream": "adjusted_product"}
            },
        )

        report = planner.build_report(planner.build_steady_states(), {})

        self.assertTrue((report["source_grade_fe"] == 65).all())
        self.assertTrue((report["source_grade_insitu_fe"] == 50).all())
        self.assertTrue((report["source_grade_adjusted_product_fe"] == 65).all())
        self.assertTrue((report["selected_grade_brand"] == "FB").all())
        self.assertTrue((report["selected_grade_stream"] == "adjusted_product").all())

    def test_aggregates_payloads_by_source_with_weighted_grades(self):
        payloads = pd.DataFrame([
            {
                "source": "GB1", "payload": 10,
                "delivered_datetime": self.start + timedelta(minutes=10),
                "direct_tip_eligible": True, "source_grade_fe": 60,
            },
            {
                "source": "GB1", "payload": 30,
                "delivered_datetime": self.start + timedelta(minutes=20),
                "direct_tip_eligible": True, "source_grade_fe": 64,
            },
        ])
        state = self.planner(payloads).build_steady_states()[0]
        candidate = state["direct_tip_candidates"][0]

        self.assertEqual(40, candidate["available_tonnes"])
        self.assertAlmostEqual(63, candidate["grade_fe"])

    def test_manual_report_aggregates_slices_to_parent_grade_block(self):
        parent = "Reserves/CC1/CUE01/01/414/111/417/LG46"
        payloads = pd.DataFrame([
            {
                "source": f"{parent}_627",
                "direct_tip_id": "DT1",
                "payload": 10,
                "delivered_datetime": self.start + timedelta(minutes=10),
                "direct_tip_eligible": True,
                "source_grade_fe": 60,
            },
            {
                "source": f"{parent}_124",
                "direct_tip_id": "DT2",
                "payload": 30,
                "delivered_datetime": self.start + timedelta(minutes=20),
                "direct_tip_eligible": True,
                "source_grade_fe": 64,
            },
        ])
        planner = self.planner(payloads)
        state = planner.build_steady_states()[0]
        allocations = {
            state["state_key"]: {
                f"{parent}_627": 10,
                f"{parent}_124": 30,
            }
        }

        report = planner.build_report([state], allocations)
        grade_blocks = report[report["source_type"] == "grade_block"]

        self.assertEqual(1, len(grade_blocks))
        self.assertEqual(parent, grade_blocks.iloc[0]["source"])
        self.assertEqual(40, grade_blocks.iloc[0]["source_actual_tonnes"])
        self.assertAlmostEqual(63, grade_blocks.iloc[0]["source_grade_fe"])
        self.assertEqual("DT1, DT2", grade_blocks.iloc[0]["source_id"])

    def test_rejects_direct_tip_above_available(self):
        payloads = pd.DataFrame([{
            "source": "GB1", "payload": 10,
            "delivered_datetime": self.start + timedelta(minutes=10),
            "direct_tip_eligible": True,
        }])
        planner = self.planner(payloads)
        state = planner.build_steady_states()[0]
        with self.assertRaisesRegex(
            ManualBlendPlanningError, "exceeds its available"
        ):
            planner.build_report(
                [state],
                {state["state_key"]: {"GB1": 11}},
            )

    def test_manual_report_is_written_with_optimised_compatible_schema(self):
        planner = self.planner()
        report = planner.build_report(planner.build_steady_states(), {})
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "manual.db")
            try:
                set_database_path(database_path)
                DatabaseManager().write_manual_blend_report_to_database(
                    report
                )
                connection = sqlite3.connect(database_path)
                try:
                    stored = pd.read_sql(
                        "SELECT * FROM manual_blend_report", connection
                    )
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertEqual(
            balance_triplet_columns(report.columns), list(stored.columns)
        )
        self.assertIn("actual_direct_tip_ratio", stored.columns)
        self.assertIn("crusher_actual_grade_fe", stored.columns)
        self.assertIn("selected_grade_stream", stored.columns)
        self.assertIn("source_grade_adjusted_product_fe", stored.columns)
        self.assertEqual(len(report), len(stored))

    def test_optimised_database_schema_accepts_build_progress_columns(self):
        builds = [{
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
            "target_mn_max": 0.2,
        }]
        planner = self.planner(builds=builds)
        report = planner.build_report(
            planner.build_steady_states(), {}
        )
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "optimised.db")
            try:
                set_database_path(database_path)
                with patch.object(
                    DatabaseManager,
                    "write_optimised_stockpile_depletion_report_to_database",
                ), patch(
                    "database.SQLiteDatabase.StockpileProfileReport."
                    "write_optimised_stockpile_profile_report_to_database"
                ):
                    DatabaseManager().write_optimised_blend_report_to_database(
                        report, None
                    )
                connection = sqlite3.connect(database_path)
                try:
                    stored = pd.read_sql(
                        "SELECT * FROM optimised_blend_report",
                        connection,
                    )
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertIn(
            "product_build_closing_tonnes", stored.columns
        )
        self.assertIn("product_build_grade_fe", stored.columns)
        self.assertIn("selected_grade_stream", stored.columns)
        self.assertIn("source_grade_adjusted_product_fe", stored.columns)
        self.assertEqual(
            200,
            stored.iloc[-1]["product_build_closing_tonnes"],
        )

    def test_legacy_reports_are_enriched_without_rerunning_plan(self):
        builds = [{
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
            "target_mn_max": 0.2,
        }]
        planner = self.planner(builds=builds)
        legacy_report = planner.build_report(
            planner.build_steady_states(), {}
        ).drop(
            columns=[
                column for column in ManualBlendPlanner.REPORT_COLUMNS
                if column.startswith("product_build_")
            ]
        )
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "legacy.db")
            connection = sqlite3.connect(database_path)
            try:
                legacy_report.to_sql(
                    "optimised_blend_report",
                    connection,
                    if_exists="replace",
                    index=False,
                )
            finally:
                connection.close()
            try:
                set_database_path(database_path)
                DatabaseManager().add_product_build_progress_to_existing_reports(
                    builds
                )
                connection = sqlite3.connect(database_path)
                try:
                    upgraded = pd.read_sql(
                        "SELECT * FROM optimised_blend_report",
                        connection,
                    )
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertIn(
            "product_build_remaining_tonnes", upgraded.columns
        )
        self.assertEqual(
            0,
            upgraded.iloc[-1]["product_build_remaining_tonnes"],
        )
        self.assertEqual(
            59,
            upgraded.iloc[-1]["product_build_grade_fe"],
        )

    def test_manual_report_keeps_build_targets_out_of_crusher_targets(self):
        planner = ManualBlendPlanner(
            self.sequence,
            self.blends,
            self.stockpiles,
            [],
            pd.DataFrame(),
            self.periods,
            [{
                "target_tonnes": 1000,
                "target_fe_min": 58,
                "target_fe_max": 100,
                "target_si_min": 0,
                "target_si_max": 6,
            }],
            crusher_rate=100,
            calendar_inputs={
                "crusher_target_fe_min": {"Preplan": 55},
                "crusher_target_fe_max": {"Preplan": 65},
                "crusher_target_si_min": {"Preplan": 1},
                "crusher_target_si_max": {"Preplan": 8},
            },
        )

        report = planner.build_report(
            planner.build_steady_states(), {}
        )

        self.assertEqual(55, report.iloc[0]["crusher_grade_target_min_fe"])
        self.assertEqual(65, report.iloc[0]["crusher_grade_target_max_fe"])
        self.assertEqual(1, report.iloc[0]["crusher_grade_target_min_si"])
        self.assertEqual(8, report.iloc[0]["crusher_grade_target_max_si"])


if __name__ == "__main__":
    unittest.main()
