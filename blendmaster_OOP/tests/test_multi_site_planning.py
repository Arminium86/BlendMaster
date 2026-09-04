import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from classes.ExpitDataHandler import ExpitDataHandler
from classes.DataLoader import DataLoader
from classes.EquipmentData import EquipmentData
from classes.EventPoolGenerator import EventPoolGenerator
from classes.AMTChunking import calculate_amt_chunk_plan
from GUI.DrawCharts import DrawAMTStockpile
from GUI.InitialiseGUI import UserInputs
from setup.PlanningPlanTargets import PlanningPlanTargets


class FakeCursor:
    COLUMNS = [
        "SCENARIO",
        "OPERATION",
        "PERIOD_START",
        "PERIOD_END",
        "PRODUCT_TYPE",
        "VALUE",
        "FE",
        "SIO2",
        "AL2O3",
        "P",
        "MN",
    ]

    def __init__(self, rows):
        self.rows = rows
        self.description = [(column,) for column in self.COLUMNS]
        self.executed_query = None
        self.executed_parameters = None

    def execute(self, query, parameters):
        self.executed_query = query
        self.executed_parameters = parameters

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class SequencedFakeCursor(FakeCursor):
    def __init__(self, responses):
        super().__init__([])
        self.responses = list(responses)
        self.executed_calls = []

    def execute(self, query, parameters):
        super().execute(query, parameters)
        self.executed_calls.append((query, parameters))

    def fetchall(self):
        index = max(len(self.executed_calls) - 1, 0)
        return self.responses[index] if index < len(self.responses) else []


class SequencedFakeInventoryLoader:
    def __init__(self, responses):
        self.cursor = SequencedFakeCursor(responses)

    def connect_snowflake_with_service_account(self):
        return FakeConnection(self.cursor)


class AMTAgentTableProjectionTests(unittest.TestCase):
    def test_chunk_count_goal_seek_uses_total_wmt_rate_and_hours(self):
        below_target = calculate_amt_chunk_plan(100000, 2000, 72)
        nearer_two = calculate_amt_chunk_plan(200000, 2000, 72)
        exact_two = calculate_amt_chunk_plan(288000, 2000, 72)

        self.assertEqual(below_target["chunk_count"], 1)
        self.assertEqual(below_target["chunk_size"], 100000)
        self.assertEqual(nearer_two["chunk_count"], 2)
        self.assertEqual(nearer_two["chunk_size"], 100000)
        self.assertEqual(exact_two["chunk_count"], 2)
        self.assertEqual(exact_two["resulting_chunk_hours"], 72)

    def test_amt_and_inventory_footprint_totals_are_resolved(self):
        window = UserInputs.__new__(UserInputs)
        window.AMT_stockpile_data = {
            "SP1": [
                {
                    "FINAL_WMT": 100.0,
                    "AMT_INVENTORY_STOCKPILE": "SP1_INV",
                },
                {"FINAL_WMT": 150.0},
            ]
        }
        window.stockpile_data = {"SP1_INV": {"BALANCE": 300.0}}
        window.updated_stockpile_data = {}

        self.assertEqual(
            window.AMT_footprint_totals("SP1"),
            (250.0, 300.0),
        )

    def test_generated_chunks_use_goal_seek_count_and_resulting_size(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.chunk_settings = {
            "SP1": {
                "average_reclaim_rate": 2000.0,
                "chunk_reclaim_hours": 72.0,
                "amt_total_wmt": 300000.0,
                "inventory_total_wmt": 310000.0,
                "chunk_count": 2,
                "chunk_size": 150000.0,
            }
        }
        chart.data = pd.DataFrame([
            {
                "footprint": "SP1",
                "hex": f"H{index}",
                "balance": 50000.0,
                "long": float(index % 3),
                "lat": float(index // 3),
                "grade_fe": 60.0,
                "grade_si": 4.0,
                "grade_al": 2.0,
                "grade_p": 0.08,
                "grade_mn": 0.1,
                "grade_streams": None,
            }
            for index in range(6)
        ])
        chart.dig_paths = {}

        chunks, message = chart.build_chunks_for_footprint(
            "SP1", (0.0, 0.0), (1.0, 0.0), (0.0, 0.0), (0.0, 1.0)
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(sum(row["balance"] for row in chunks), 300000.0)
        self.assertTrue(all(row["chunk_size"] == 150000.0 for row in chunks))
        self.assertIn("Calculated target: 2 chunks", message)
        self.assertIn("AMT Total WMT: 300,000 t", chart.footprint_tonnage_summary("SP1"))
        self.assertIn("Inventory Stockpile Total WMT: 310,000 t", chart.footprint_tonnage_summary("SP1"))

    def test_manual_exclusions_reduce_goal_seek_tonnage(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.chunk_settings = {
            "SP1": {
                "average_reclaim_rate": 100.0,
                "chunk_reclaim_hours": 1.0,
                "amt_total_wmt": 300.0,
                "chunk_size": 100.0,
            }
        }
        chart.data = pd.DataFrame([
            {
                "footprint": "SP1",
                "hex": hex_id,
                "balance": 100.0,
            }
            for hex_id in ("H1", "H2", "H3")
        ])
        chart.excluded_hexes = {"SP1": {"H3"}}

        plan = chart.get_chunk_plan("SP1")

        self.assertEqual(plan["chunk_count"], 2)
        self.assertEqual(plan["chunk_size"], 100.0)

    def test_agent_chunk_vectors_are_not_sent_to_dash_table(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.selected_points = [{
            "footprint": "SP1",
            "sequence": 1,
            "hex": "SP1_CHUNK_001",
            "balance": np.float64(100.0),
            "grade_streams": {"adjusted_product": {"CCFB": {"fe": 60.0}}},
            "reclaim_axis_vector": [1.0, 0.0],
            "cut_axis_vector": {"x": 0.0, "y": 1.0},
        }]

        displayed = chart.selected_table_data()

        self.assertEqual(displayed[0]["balance"], "100")
        self.assertNotIn("reclaim_axis_vector", displayed[0])
        self.assertNotIn("cut_axis_vector", displayed[0])
        self.assertNotIn("grade_streams", displayed[0])
        self.assertEqual(chart.selected_points[0]["reclaim_axis_vector"], [1.0, 0.0])

    def test_generated_chunks_keep_streams_off_dash_callback_rows(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.selected_points = []
        chart.reclaim_directions = {
            "SP1": {"start": (0, 0), "end": (1, 0)}
        }
        chart.cut_directions = {
            "SP1": {"start": (0, 0), "end": (0, 1)}
        }
        chart.build_chunks_for_footprint = lambda *_args: ([{
            "footprint": "SP1",
            "sequence": 1,
            "hex": "SP1_CHUNK_001",
            "balance": 100.0,
            "grade_streams": {"adjusted_product": {"CCFB": {"fe": 60.0}}},
        }], "Generated")
        chart.update_sequence_counter = lambda: None

        displayed, _ = chart.generate_chunks_from_directions("SP1", [])

        self.assertNotIn("grade_streams", displayed[0])
        self.assertIn("grade_streams", chart.selected_points[0])

    def test_amt_inventory_match_and_lineage_product_are_retained(self):
        window = UserInputs.__new__(UserInputs)
        window.stockpile_data = {
            "SP1": {
                "NAME": "SP1",
                "BUILD": "SP1_26001",
                **{
                    f"GRADE_{grade.upper()}": 50.0
                    for grade in ("fe", "si", "al", "p", "mn")
                },
                **{
                    f"{grade.upper()}_ROM": 55.0
                    for grade in ("fe", "si", "al", "p", "mn")
                },
                **{
                    f"{grade.upper()}_PROD1": 60.0
                    for grade in ("fe", "si", "al", "p", "mn")
                },
            }
        }
        window.product_brand_labels_choice = ["FB"]
        window.opf_input_choice = "CB OPF"
        window.historical_recon_factors = {}
        window.historical_recon_warnings = []
        amt = {
            "SP1": [{
                "FOOTPRINT": "SP1",
                "LOCATION_NAME": "SP1_26001",
                "INVENTORY_BALANCE_WMT": 1000.0,
                "INVENTORY_TRANSACTION_DATETIME": "2026-07-31 12:00:00",
                "HEX": "H1",
                "FE": 40.0,
                "SIO2": 4.0,
                "AL2O3": 2.0,
                "P": 0.08,
                "MN": 0.1,
                "MODELLED_PROD1_FE": 61.0,
                "MODELLED_PROD1_SIO2": 3.0,
                "MODELLED_PROD1_AL2O3": 1.5,
                "MODELLED_PROD1_P": 0.07,
                "MODELLED_PROD1_MN": 0.08,
            }]
        }

        enriched = window.enrich_AMT_grade_streams(
            window.stockpile_data, amt
        )
        row = enriched["SP1"][0]

        self.assertTrue(row["AMT_INVENTORY_MATCHED"])
        self.assertEqual(row["AMT_INVENTORY_STOCKPILE"], "SP1")
        self.assertEqual(row["AMT_INVENTORY_BUILD"], "SP1_26001")
        self.assertNotIn("INTERNAL_RECON_MATCHED", row)
        self.assertNotIn("INTERNAL_BLEND_RECON_FE", row)
        self.assertNotIn("INTERNAL_UPGRADE_FE", row)
        self.assertAlmostEqual(
            row["GRADE_STREAMS"]["modelled_rom"]["*"]["fe"], 40.0
        )
        self.assertAlmostEqual(
            row["GRADE_STREAMS"]["modelled_product"]["FB"]["fe"], 61.0
        )

    def test_auto_directions_use_short_axis_for_reclaim(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.data = pd.DataFrame([
            {"footprint": "SP1", "long": x, "lat": y}
            for x in (0.0, 4.0)
            for y in (0.0, 1.0)
        ])

        reclaim, cut, message = chart.automatic_directions_for_footprint(
            "SP1"
        )

        self.assertEqual(message, "")
        reclaim_dx = abs(reclaim["end"][0] - reclaim["start"][0])
        reclaim_dy = abs(reclaim["end"][1] - reclaim["start"][1])
        cut_dx = abs(cut["end"][0] - cut["start"][0])
        cut_dy = abs(cut["end"][1] - cut["start"][1])
        self.assertGreater(reclaim_dy, reclaim_dx)
        self.assertGreater(cut_dx, cut_dy)


class TotalFeedStockpileReclaimRateTests(unittest.TestCase):
    @staticmethod
    def calendar_inputs():
        return {
            "site_context": {"crusher": "Total_Feed_PC"},
            "stockpiles_sp1_state": {
                "Preplan": "Auto",
                "Period_1": "Auto",
                "Period_2": "Auto",
            },
            "stockpiles_sp1_maximum_quantity": {
                "Preplan": 100000,
                "Period_1": 100000,
                "Period_2": 100000,
            },
            "stockpiles_sp1_cost": {
                "Preplan": 0,
                "Period_1": 0,
                "Period_2": 0,
            },
            "stockpiles_sp1_cash": {
                "Preplan": 0,
                "Period_1": 0,
                "Period_2": 0,
            },
        }

    @staticmethod
    def stockpile(max_reclaim_rate=650):
        return {
            "SP1": {
                "name": "sp1",
                "balance": 5000,
                "reclaim_threshold": 0,
                "grade_fe": 60,
                "grade_si": 4,
                "grade_al": 2,
                "grade_p": 0.08,
                "grade_mn": 0.1,
                "amt": True,
                "max_reclaim_rate": max_reclaim_rate,
                "auto_turnover_datetime": None,
                "is_ready": True,
            }
        }

    def test_total_feed_stockpile_rate_overrides_global_reclaim_equipment(self):
        loader = DataLoader(
            self.stockpile(),
            self.calendar_inputs(),
            pd.DataFrame(),
            [],
        )
        stockpile = loader.create_stockpile_data_objects(
            loader.stockpile_data,
            loader.calendar_inputs,
        )[0]
        equipment = EquipmentData(
            name="RC",
            priority_preplan=0,
            priority_period_1=0,
            priority_period_2=0,
            rate_preplan=1000,
            rate_period_1=1000,
            rate_period_2=1000,
        )

        class Balance:
            @staticmethod
            def get_balance(_name):
                return 5000, 60, 4, 2, 0.1, 0.08

        events = EventPoolGenerator(
            [stockpile],
            [],
            [equipment],
        ).generate_initial_event_pool(
            "preplan",
            datetime(2026, 7, 14, 6),
            datetime(2026, 7, 14, 7),
            Balance(),
        )

        self.assertEqual(events[0]["rate"], 650)
        self.assertTrue(events[0]["is_amt"])

    def test_total_feed_reclaimable_stockpile_requires_positive_rate(self):
        loader = DataLoader(
            self.stockpile(max_reclaim_rate=0),
            self.calendar_inputs(),
            pd.DataFrame(),
            [],
        )

        with self.assertRaisesRegex(
            ValueError,
            "Max Reclaim Rate must be greater than 0 t/h.*SP1",
        ):
            loader.create_stockpile_data_objects(
                loader.stockpile_data,
                loader.calendar_inputs,
            )

    def test_individual_crusher_keeps_the_calendar_equipment_rate(self):
        calendar_inputs = self.calendar_inputs()
        calendar_inputs["site_context"]["crusher"] = "OPF01_PC"
        loader = DataLoader(
            self.stockpile(max_reclaim_rate=650),
            calendar_inputs,
            pd.DataFrame(),
            [],
        )

        stockpile = loader.create_stockpile_data_objects(
            loader.stockpile_data,
            loader.calendar_inputs,
        )[0]

        self.assertIsNone(stockpile.max_reclaim_rate)


class FakeInventoryLoader:
    def __init__(self, rows):
        self.cursor = FakeCursor(rows)

    def connect_snowflake_with_service_account(self):
        return FakeConnection(self.cursor)


class PlanningPlanTargetsTests(unittest.TestCase):
    def test_latest_wednesday_uses_the_app_start_date(self):
        self.assertEqual(
            PlanningPlanTargets.latest_wednesday_scenario(datetime(2026, 7, 14, 6)),
            "20260708",
        )
        self.assertEqual(
            PlanningPlanTargets.latest_wednesday_scenario(datetime(2026, 7, 15, 18)),
            "20260715",
        )
        self.assertEqual(
            PlanningPlanTargets.previous_wednesday_scenario(
                datetime(2026, 7, 15, 18)
            ),
            "20260708",
        )

    def test_wednesday_uses_previous_week_when_current_plan_has_no_rows(self):
        perth = ZoneInfo("Australia/Perth")
        previous_week_rows = [(
            "2WCB_20260708",
            "Cloudbreak West",
            datetime(2026, 7, 15, 6, tzinfo=perth),
            datetime(2026, 7, 15, 18, tzinfo=perth),
            "CBSF",
            24000,
            57.5,
            6.2,
            2.8,
            0.1,
            0.05,
        )]
        loader = SequencedFakeInventoryLoader([[], previous_week_rows])

        targets = PlanningPlanTargets(loader).fetch(
            "CB",
            "OPF02",
            datetime(2026, 7, 15, 6),
            ["SF"],
        )

        self.assertEqual(2, len(loader.cursor.executed_calls))
        self.assertEqual(
            ["20260715", "20260708"],
            [call[1][1] for call in loader.cursor.executed_calls],
        )
        self.assertEqual("2WCB_20260708", targets[0]["planning_scenario"])
        self.assertEqual("20260708", targets[0]["planning_scenario_key"])
        self.assertEqual(
            "20260715", targets[0]["planning_scenario_requested_key"]
        )
        self.assertTrue(
            targets[0]["planning_scenario_previous_week_fallback"]
        )

    def test_published_current_plan_for_another_opf_does_not_fallback(self):
        perth = ZoneInfo("Australia/Perth")
        current_rows = [(
            "2WCB_20260715",
            "Cloudbreak Central",
            datetime(2026, 7, 15, 6, tzinfo=perth),
            datetime(2026, 7, 15, 18, tzinfo=perth),
            "CBSF",
            24000,
            57.5,
            6.2,
            2.8,
            0.1,
            0.05,
        )]
        loader = SequencedFakeInventoryLoader([current_rows])

        targets = PlanningPlanTargets(loader).fetch(
            "CB",
            "OPF02",
            datetime(2026, 7, 15, 6),
            ["SF"],
        )

        self.assertEqual([], targets)
        self.assertEqual(1, len(loader.cursor.executed_calls))

    def test_product_build_normalization_preserves_plan_provenance(self):
        window = UserInputs.__new__(UserInputs)
        rows = window.normalized_agent_product_build_settings([{
            "brand": "SF",
            "target_tonnes": 24000,
            "planning_scenario": "2WCB_20260708",
            "planning_scenario_previous_week_fallback": True,
        }])

        self.assertEqual("2WCB_20260708", rows[0]["planning_scenario"])
        self.assertTrue(
            rows[0]["planning_scenario_previous_week_fallback"]
        )

    def test_mine_and_crusher_operation_mappings(self):
        self.assertEqual(
            PlanningPlanTargets.operations_for("CC", "OPF1"),
            {"CHRISTMAS CREEK 1"},
        )
        self.assertEqual(
            PlanningPlanTargets.operations_for("CB", "OPF04"),
            {"CLOUDBREAK WEST"},
        )
        self.assertEqual(
            PlanningPlanTargets.operations_for("IB", "Crusher"),
            {"IRON BRIDGE"},
        )
        self.assertEqual(
            PlanningPlanTargets.operations_for("CB", opf="CB OPF"),
            {"CLOUDBREAK CENTRAL", "CLOUDBREAK WEST"},
        )

    def test_special_product_brand_mappings(self):
        brands = ["FB", "SS", "SF", "FF", "KF"]
        self.assertEqual(PlanningPlanTargets.normalize_brand("CBSF", "CB", brands), "SF")
        self.assertEqual(PlanningPlanTargets.normalize_brand(None, "IB", brands), "IBC")
        self.assertEqual(PlanningPlanTargets.normalize_brand("VK_OPF_KGKF", "KV", brands), "KF")

    def test_fetch_filters_operation_and_overlapping_period(self):
        perth = ZoneInfo("Australia/Perth")
        rows = [
            (
                "2WCB_20260708",
                "Cloudbreak West",
                datetime(2026, 7, 14, 6, tzinfo=perth),
                datetime(2026, 7, 14, 18, tzinfo=perth),
                "CBSF",
                24000,
                57.5,
                6.2,
                2.8,
                0.1,
                0.05,
            ),
            (
                "2WCB_20260708",
                "Cloudbreak Central",
                datetime(2026, 7, 14, 6, tzinfo=perth),
                datetime(2026, 7, 14, 18, tzinfo=perth),
                "CBSF",
                18000,
                58.0,
                5.8,
                2.6,
                0.09,
                0.04,
            ),
            (
                "2WCB_20260708",
                "Cloudbreak West",
                datetime(2026, 7, 20, 6, tzinfo=perth),
                datetime(2026, 7, 20, 18, tzinfo=perth),
                "CBSF",
                99999,
                57.0,
                6.5,
                3.0,
                0.11,
                0.06,
            ),
        ]
        loader = FakeInventoryLoader(rows)
        targets = PlanningPlanTargets(loader).fetch(
            "CB",
            "OPF02",
            datetime(2026, 7, 14, 6),
            ["FB", "SS", "SF", "FF", "KF"],
        )

        self.assertEqual(
            loader.cursor.executed_parameters,
            (
                "OPF Feed",
                "20260708",
                datetime(2026, 7, 15, 18),
                datetime(2026, 7, 14, 6),
            ),
        )
        self.assertIn(
            "UPPER(TRIM(PLANNING_CATEGORY)) = UPPER(TRIM(%s))",
            loader.cursor.executed_query,
        )
        self.assertIn("PERIOD_START < %s", loader.cursor.executed_query)
        self.assertIn("PERIOD_END > %s", loader.cursor.executed_query)
        self.assertIn("MAX(COALESCE(VERSION, 0))", loader.cursor.executed_query)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["build_name"], "SF Build 1")
        self.assertEqual(targets[0]["target_tonnes"], 24000)
        self.assertEqual(targets[0]["target_fe_min"], 57.5)
        self.assertEqual(targets[0]["target_fe_max"], 100)
        self.assertEqual(targets[0]["target_si_min"], 0)
        self.assertEqual(targets[0]["target_si_max"], 6.2)
        self.assertEqual(targets[0]["target_al_min"], 0)
        self.assertEqual(targets[0]["target_al_max"], 2.8)
        self.assertEqual(targets[0]["target_p_min"], 0)
        self.assertEqual(targets[0]["target_p_max"], 0.1)
        self.assertEqual(targets[0]["target_mn_min"], 0)
        self.assertEqual(targets[0]["target_mn_max"], 0.05)

    def test_opf_target_tonnes_are_split_by_crusher_contribution(self):
        perth = ZoneInfo("Australia/Perth")
        rows = [
            (
                "2WCB_20260708",
                "Cloudbreak Central",
                datetime(2026, 7, 14, 6, tzinfo=perth),
                datetime(2026, 7, 14, 18, tzinfo=perth),
                "CBSF",
                20000,
                58.0,
                5.8,
                2.6,
                0.09,
                0.04,
            ),
            (
                "2WCB_20260708",
                "Cloudbreak West",
                datetime(2026, 7, 14, 6, tzinfo=perth),
                datetime(2026, 7, 14, 18, tzinfo=perth),
                "CBSF",
                30000,
                57.5,
                6.2,
                2.8,
                0.1,
                0.05,
            ),
        ]
        targets = PlanningPlanTargets(FakeInventoryLoader(rows)).fetch(
            "CB",
            "OPF02",
            datetime(2026, 7, 14, 6),
            ["SF"],
            opf="CB OPF",
            crusher_contribution_ratio=0.4,
        )

        self.assertEqual([target["target_tonnes"] for target in targets], [8000, 12000])
        self.assertTrue(all(target["crusher_contribution_ratio"] == 0.4 for target in targets))

    def test_cb_paired_product_rows_become_concurrent_lump_and_fines_builds(self):
        perth = ZoneInfo("Australia/Perth")
        period_start = datetime(2026, 7, 14, 6, tzinfo=perth)
        period_end = datetime(2026, 7, 14, 18, tzinfo=perth)
        rows = [
            (
                "2WCB_20260708", "Cloudbreak Central", period_start, period_end, product,
                tonnes, fe, 5.0, 3.0, 0.1, 0.05,
            )
            for product, tonnes, fe in (
                ("CBFL", 12000, 61.0),
                ("CBSF", 8000, 56.0),
            )
        ]
        targets = PlanningPlanTargets(FakeInventoryLoader(rows)).fetch(
            "CB",
            "OPF01",
            datetime(2026, 7, 14, 6),
            ["SF", "FL"],
            opf="CB OPF",
            planning_category="OPF Production",
            byproducts_enabled=True,
        )
        self.assertEqual(["lump", "fines"], [row["byproduct"] for row in targets])
        self.assertEqual(["FL", "SF"], [row["brand"] for row in targets])
        self.assertEqual(
            ["FL Lump Build 1", "SF Fines Build 1"],
            [row["build_name"] for row in targets],
        )
        self.assertTrue(all(row["cbfl_campaign"] for row in targets))

    def test_builds_can_be_grouped_by_brand_with_weighted_targets(self):
        builds = [
            {
                "brand": "SF",
                "target_tonnes": 100,
                "planning_target_tonnes": 200,
                "target_fe_min": 56,
                "target_fe_max": 100,
                "target_si_min": 0,
                "target_si_max": 8,
                "target_al_min": 0,
                "target_al_max": 4,
                "target_p_min": 0,
                "target_p_max": 0.1,
                "target_mn_min": 0,
                "target_mn_max": 0.1,
                "planning_operation": "West",
            },
            {
                "brand": "SF",
                "target_tonnes": 300,
                "planning_target_tonnes": 600,
                "target_fe_min": 60,
                "target_fe_max": 100,
                "target_si_min": 0,
                "target_si_max": 4,
                "target_al_min": 0,
                "target_al_max": 2,
                "target_p_min": 0,
                "target_p_max": 0.08,
                "target_mn_min": 0,
                "target_mn_max": 0.06,
                "planning_operation": "Central",
            },
        ]

        grouped = PlanningPlanTargets.group_builds_by_brand(builds)

        self.assertEqual(1, len(grouped))
        self.assertEqual(400, grouped[0]["target_tonnes"])
        self.assertEqual(800, grouped[0]["planning_target_tonnes"])
        self.assertEqual(59, grouped[0]["target_fe_min"])
        self.assertEqual(5, grouped[0]["target_si_max"])
        self.assertEqual(
            "West, Central", grouped[0]["planning_operation"]
        )

    def test_brand_grouping_stops_when_another_brand_intervenes(self):
        def build(brand, tonnes, fe):
            return {
                "brand": brand,
                "target_tonnes": tonnes,
                "planning_target_tonnes": tonnes,
                "target_fe_min": fe,
                "target_fe_max": 100,
                "target_si_min": 0,
                "target_si_max": 6,
                "target_al_min": 0,
                "target_al_max": 3,
                "target_p_min": 0,
                "target_p_max": 0.1,
                "target_mn_min": 0,
                "target_mn_max": 0.1,
            }

        grouped = PlanningPlanTargets.group_builds_by_brand([
            build("SF", 100, 56),
            build("SF", 100, 60),
            build("FF", 200, 58),
            build("SF", 300, 59),
        ])

        self.assertEqual(
            ["SF Build 1", "FF Build 1", "SF Build 2"],
            [row["build_name"] for row in grouped],
        )
        self.assertEqual(
            [200, 200, 300],
            [row["target_tonnes"] for row in grouped],
        )
        self.assertEqual(58, grouped[0]["target_fe_min"])


class APSCrusherRatioAndMovementRuleTests(unittest.TestCase):
    @staticmethod
    def _write_mining_csv(path):
        rows = [
            {
                "Time.StartTime": "14/07/2026 06:00:00 AM",
                "Time.EndTime": "14/07/2026 07:00:00 AM",
                "Source.Type": "Reserve",
                "Source.NamePart2": "PIT_A_STAGE_1",
                "Destination.Type": "Crusher",
                "Destination.Name": "Crushers/CB_OPF1",
                "Destination.FullName": "Crushers/CB_OPF1",
                "Mining.wetTonnes": 600,
            },
            {
                "Time.StartTime": "14/07/2026 07:00:00 AM",
                "Time.EndTime": "14/07/2026 08:00:00 AM",
                "Source.Type": "Reserve",
                "Source.NamePart2": "PIT_B_STAGE_2",
                "Destination.Type": "Crusher",
                "Destination.Name": "Crushers/CB_OPF2",
                "Destination.FullName": "Crushers/CB_OPF2",
                "Mining.wetTonnes": 400,
            },
            {
                "Time.StartTime": "20/07/2026 06:00:00 AM",
                "Time.EndTime": "20/07/2026 07:00:00 AM",
                "Source.Type": "Reserve",
                "Source.NamePart2": "OUTSIDE_WINDOW",
                "Destination.Type": "Crusher",
                "Destination.Name": "Crushers/CB_OPF1",
                "Destination.FullName": "Crushers/CB_OPF1",
                "Mining.wetTonnes": 9000,
            },
        ]
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_ratio_uses_blendmaster_window_but_movement_lists_use_full_file(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "Mining.csv"
            self._write_mining_csv(csv_path)

            ratios = ExpitDataHandler.calculate_crusher_feed_ratios(
                csv_path,
                datetime(2026, 7, 14, 6),
                ["Crushers/CB_OPF1", "Crushers/CB_OPF2"],
            )
            options = ExpitDataHandler.get_direct_tip_movement_options(
                csv_path,
                datetime(2026, 7, 14, 6),
            )

        self.assertAlmostEqual(ratios["Crushers/CB_OPF1"], 0.6)
        self.assertAlmostEqual(ratios["Crushers/CB_OPF2"], 0.4)
        self.assertEqual(
            options["grade_block_sources"],
            ["OUTSIDE_WINDOW", "PIT_A_STAGE_1", "PIT_B_STAGE_2"],
        )
        self.assertEqual(
            options["crusher_destinations"],
            ["Crushers/CB_OPF1", "Crushers/CB_OPF2"],
        )

    def test_direct_tip_rule_matches_source_substring_and_active_crusher(self):
        handler = ExpitDataHandler.__new__(ExpitDataHandler)
        handler.selected_crusher_names = {"Crushers/CB_OPF1"}
        handler.operational_mine = "CB"
        handler.operational_crusher = "OPF01"
        handler.direct_tip_movement_rules = [{
            "grade_block_source": "PIT_A_STAGE_1",
            "crusher_destination": "Crushers/CB_OPF1",
        }]

        self.assertTrue(handler._direct_tip_rule_matches(
            "Reserves/Cloudbreak/PIT_A_STAGE_1/BLOCK_001"
        ))
        self.assertFalse(handler._direct_tip_rule_matches(
            "Reserves/Cloudbreak/PIT_B_STAGE_2/BLOCK_001"
        ))

    def test_direct_tip_rule_accepts_short_aps_destination_for_separate_rch(self):
        handler = ExpitDataHandler.__new__(ExpitDataHandler)
        handler.selected_crusher_names = {"Crushers/RCH"}
        handler.operational_mine = "CC"
        handler.operational_crusher = "OPF02_PC"
        handler.operational_opf = "CC OPF02"
        handler.direct_tip_movement_rules = [{
            "grade_block_source": "PIT_A_STAGE_1",
            "crusher_destination": "RCH",
        }]

        self.assertTrue(handler._direct_tip_rule_matches(
            "Reserves/Christmas Creek/PIT_A_STAGE_1/BLOCK_001"
        ))

    def test_total_feed_rule_accepts_any_selected_child_destination(self):
        handler = ExpitDataHandler.__new__(ExpitDataHandler)
        handler.selected_crusher_names = {"Crushers/OPF1 Crusher", "Crushers/Hal Crusher"}
        handler.operational_mine = "CC"
        handler.operational_crusher = "Total_Feed_PC"
        handler.operational_opf = "CC OPF01"
        handler.direct_tip_movement_rules = [{
            "grade_block_source": "PIT_A_STAGE_1",
            "crusher_destination": "Hal Crusher",
        }]

        self.assertTrue(handler._direct_tip_rule_matches(
            "Reserves/Christmas Creek/PIT_A_STAGE_1/BLOCK_001"
        ))


class DirectTipEligibilityTests(unittest.TestCase):
    @staticmethod
    def _payload_frame(eligible_marker=True, include_marker=True):
        row = {
            "direct_tip_id": "GB_000001",
            "payload": 200.0,
            "source_grade_fe": 58.0,
            "source_grade_si": 6.0,
            "source_grade_al": 3.0,
            "source_grade_p": 0.1,
            "source_grade_mn": 0.05,
            "delivered_datetime": datetime(2026, 7, 14, 7),
            "destination": "ROM01",
            "agent": "EX01",
            "start_datetime": datetime(2026, 7, 14, 6),
            "source": "Reserves/Cloudbreak/PIT_A_STAGE_1/BLOCK_001",
        }
        if include_marker:
            row["direct_tip_eligible"] = eligible_marker
        return pd.DataFrame([row])

    @staticmethod
    def _loader():
        return DataLoader(
            {},
            {"solver_config": {"direct_tip_enabled": True}},
            pd.DataFrame(),
            [],
        )

    def test_only_rule_eligible_payloads_become_grade_block_objects(self):
        loader = self._loader()

        self.assertEqual(
            loader.create_grade_block_data_objects(
                self._payload_frame(include_marker=False)
            ),
            [],
        )
        self.assertEqual(
            loader.create_grade_block_data_objects(
                self._payload_frame(eligible_marker=False)
            ),
            [],
        )
        self.assertEqual(
            len(loader.create_grade_block_data_objects(
                self._payload_frame(eligible_marker=True)
            )),
            1,
        )


class OperationalCrusherMatchingTests(unittest.TestCase):
    def test_aps_destination_aliases_match_the_operational_crusher(self):
        self.assertTrue(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/CC_OPF1_FB",
                "CC",
                "OPF01",
            )
        )
        self.assertFalse(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/CC_OPF2_SS",
                "CC",
                "OPF01",
            )
        )
        self.assertTrue(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/VK_OPF_KGKF",
                "KV",
                "VK_OPF",
            )
        )
        self.assertTrue(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/RCH",
                "CC",
                "OPF02_PC",
                "CC OPF02",
            )
        )
        self.assertTrue(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/EW01",
                "EW",
                "EW_PC",
                "EW OPF",
            )
        )
        self.assertTrue(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/VK_OPF_KGKF",
                "KV",
                "Total_Feed_PC",
                "KV OPF",
            )
        )
        self.assertTrue(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/VQ_OPF",
                "KV",
                "Total_Feed_PC",
                "KV OPF",
            )
        )
        self.assertFalse(
            ExpitDataHandler.crusher_destination_matches(
                "Crushers/Unrelated_PC",
                "KV",
                "Total_Feed_PC",
                "KV OPF",
            )
        )


class APSDatetimeParsingTests(unittest.TestCase):
    def test_parser_accepts_day_first_month_first_and_iso_values(self):
        values = pd.Series([
            "13/07/2026 12:00:00 AM",
            "08/07/2026 06:30:00 PM",
            "07/16/2026 01:45:00 PM",
            "2026-07-17T06:00:00",
        ])

        parsed = ExpitDataHandler._parse_datetime_column(values, "Time.StartTime")

        self.assertEqual(parsed.iloc[0], pd.Timestamp("2026-07-13 00:00:00"))
        self.assertEqual(parsed.iloc[1], pd.Timestamp("2026-07-08 18:30:00"))
        self.assertEqual(parsed.iloc[2], pd.Timestamp("2026-07-16 13:45:00"))
        self.assertEqual(parsed.iloc[3], pd.Timestamp("2026-07-17 06:00:00"))

    def test_parser_reports_invalid_values_with_column_and_row(self):
        values = pd.Series(["13/07/2026 12:00:00 AM", "not-a-date"], index=[8, 9])

        with self.assertRaisesRegex(
            ValueError,
            "Time.EndTime contains 1 invalid timestamp.*row 9",
        ):
            ExpitDataHandler._parse_datetime_column(values, "Time.EndTime")


class APSPayloadFallbackTests(unittest.TestCase):
    @staticmethod
    def aps_row(source_type="Reserve", destination_type="Stockpile", payload=200):
        return {
            "Agent.Name": "EX01",
            "Source.Type": source_type,
            "Source.FullName": "Reserves/Test/Block01",
            "Mining.wetTonnes": 1000,
            "Mining.grades_fe": 58,
            "Mining.grades_si": 6,
            "Mining.grades_al": 3,
            "Mining.grades_mn": 0.05,
            "Mining.grades_p": 0.1,
            "Destination.Type": destination_type,
            "Destination.FullName": "ROM01" if destination_type == "Stockpile" else "Crusher01",
            "Time.StartTime": "14/07/2026 06:00:00 AM",
            "Time.EndTime": "14/07/2026 07:00:00 AM",
            "HaulageResult.Times.Dumping": 1,
            "HaulageResult.Times.LoadedTravel": 10,
            "HaulageResult.LoaderProductionRate.Wtph": 1000,
            "HaulageResult.Times.SpotAtDump": 1,
            "HaulageResult.Times.SpotAtLoader": 1,
            "HaulageResult.TruckPayload": payload,
            "HaulageResult.NumberOfTrips": 5,
        }

    def test_preprocess_excludes_non_reserve_and_unselected_crusher_rows(self):
        handler = ExpitDataHandler.__new__(ExpitDataHandler)
        handler.include_crusher_destinations = False
        handler.selected_crusher_names = set()
        handler.operational_mine = ""
        handler.operational_crusher = ""
        handler.source_stockpile_fallbacks = {}
        handler.data = pd.DataFrame([
            self.aps_row(),
            self.aps_row(source_type="Flow", payload=0),
            self.aps_row(destination_type="Crusher", payload=0),
        ])

        handler._preprocess_data()

        self.assertEqual(len(handler.data), 1)
        self.assertEqual(handler.data.iloc[0]["Source.Type"], "Reserve")
        self.assertEqual(handler.data.iloc[0]["Destination.Type"], "Stockpile")

    def test_valid_payload_and_loader_rate_are_unchanged(self):
        row = pd.Series({
            "HaulageResult.TruckPayload": 200,
            "HaulageResult.NumberOfTrips": 5,
            "HaulageResult.LoaderProductionRate.Wtph": 1000,
        })

        payload, load_time = ExpitDataHandler._resolve_payload_and_load_time(row, 1000)

        self.assertEqual(payload, 200)
        self.assertEqual(load_time, 0.2)

    def test_payload_is_inferred_from_reported_trips(self):
        row = pd.Series({
            "HaulageResult.TruckPayload": 0,
            "HaulageResult.NumberOfTrips": 5,
            "HaulageResult.LoaderProductionRate.Wtph": 1000,
        })

        payload, load_time = ExpitDataHandler._resolve_payload_and_load_time(row, 1000)

        self.assertEqual(payload, 200)
        self.assertEqual(load_time, 0.2)

    def test_missing_haulage_values_use_one_finite_aggregated_payload(self):
        row = pd.Series({
            "HaulageResult.TruckPayload": 0,
            "HaulageResult.NumberOfTrips": 0,
            "HaulageResult.LoaderProductionRate.Wtph": 0,
        })

        payload, load_time = ExpitDataHandler._resolve_payload_and_load_time(row, 1000)

        self.assertEqual(payload, 1000)
        self.assertEqual(load_time, 0)

    def test_non_finite_transaction_tonnes_are_rejected_clearly(self):
        row = pd.Series({
            "HaulageResult.TruckPayload": 200,
            "HaulageResult.NumberOfTrips": 5,
            "HaulageResult.LoaderProductionRate.Wtph": 1000,
        })

        with self.assertRaisesRegex(ValueError, "positive finite"):
            ExpitDataHandler._resolve_payload_and_load_time(row, float("inf"))
if __name__ == "__main__":
    unittest.main()
