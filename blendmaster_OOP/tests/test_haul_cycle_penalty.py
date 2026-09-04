import tempfile
import unittest
from pathlib import Path

import pandas as pd

from classes.DataLoader import DataLoader
from classes.HaulCycleDataHandler import HaulCycleDataHandler


class HaulCycleIngestionTests(unittest.TestCase):
    @staticmethod
    def write_cycles(directory, rows):
        path = Path(directory) / "cycles.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def test_distinct_crushers_exclude_stockpile_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_cycles(directory, [
                {
                    "Source Node": "Stockpiles/SP1",
                    "Dest Node": "Crushers/CR01",
                    "Total Cycle Time (min)": 30.0,
                },
                {
                    "Source Node": "Stockpiles/SP1",
                    "Dest Node": "Crushers/CR02",
                    "Total Cycle Time (min)": 40.0,
                },
                {
                    "Source Node": "Stockpiles/SP1",
                    "Dest Node": "Stockpiles/SP2",
                    "Total Cycle Time (min)": 10.0,
                },
            ])

            self.assertEqual(
                HaulCycleDataHandler.get_distinct_crusher_names(path),
                ["CR01", "CR02"],
            )

    def test_nearest_route_uses_shortest_selected_crusher_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_cycles(directory, [
                {
                    "Source Node": "Stockpiles/SP1",
                    "Dest Node": "Crushers/CR01",
                    "Total Cycle Time (min)": 30.0,
                },
                {
                    "Source Node": "Stockpiles/SP1",
                    "Dest Node": "Crushers/CR02",
                    "Total Cycle Time (min)": 20.0,
                },
                {
                    "Source Node": "Stockpiles/SP2",
                    "Dest Node": "Crushers/CR01",
                    "Total Cycle Time (min)": 45.0,
                },
            ])

            routes = HaulCycleDataHandler.build_nearest_crusher_routes(
                path,
                ["CR01", "CR02"],
            )

            self.assertEqual(routes["SP1"]["nearest_crusher"], "CR02")
            self.assertEqual(routes["SP1"]["cycle_time_minutes"], 20.0)
            self.assertEqual(routes["SP2"]["nearest_crusher"], "CR01")

    def test_stockpile_out_route_is_canonical_and_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_cycles(directory, [
                {
                    "Source Node": "Stockpiles/FTN00_RP01_0406:In",
                    "Dest Node": "Crushers/CR01",
                    "Total Cycle Time (min)": 5.0,
                },
                {
                    "Source Node": "Stockpiles/FTN00_RP01_0406:Out",
                    "Dest Node": "Crushers/CR02",
                    "Total Cycle Time (min)": 40.0,
                },
                {
                    "Source Node": "Stockpiles/FTN00_RP01_0406",
                    "Dest Node": "Crushers/CR03",
                    "Total Cycle Time (min)": 10.0,
                },
            ])

            routes = HaulCycleDataHandler.build_nearest_crusher_routes(
                path,
                ["CR01", "CR02", "CR03"],
            )

            route = routes["FTN00_RP01_0406"]
            self.assertEqual(route["nearest_crusher"], "CR02")
            self.assertEqual(route["cycle_time_minutes"], 40.0)
            self.assertEqual(
                route["source_node"],
                "Stockpiles/FTN00_RP01_0406:Out",
            )

    def test_stockpile_in_route_is_ignored_without_out_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_cycles(directory, [{
                "Source Node": "Stockpiles/FTN00_RP01_0406:In",
                "Dest Node": "Crushers/CR01",
                "Total Cycle Time (min)": 5.0,
            }])

            routes = HaulCycleDataHandler.build_nearest_crusher_routes(
                path,
                ["CR01"],
            )

            self.assertEqual(routes, {})

    def test_cost_conversion_uses_100_t_nominal_payload(self):
        self.assertAlmostEqual(
            HaulCycleDataHandler.cost_per_tonne(30, 240),
            1.2,
        )


class HaulCycleOptimizerCostTests(unittest.TestCase):
    @staticmethod
    def calendar_inputs():
        return {
            "site_context": {"crusher": "OPF01_PC"},
            "solver_config": {
                "rehandle_cycle_time_penalty_enabled": True,
                "haulage_cost_per_hour": 240.0,
            },
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
            "stockpiles_sp1_cash": {
                "Preplan": 0,
                "Period_1": 0,
                "Period_2": 0,
            },
        }

    @staticmethod
    def stockpile(cycle_time=30.0):
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
                "amt": False,
                "rehandle_cycle_time_minutes": cycle_time,
                "nearest_crusher": "CR01",
                "auto_turnover_datetime": None,
                "is_ready": True,
            }
        }

    def test_enabled_penalty_becomes_stockpile_cost_per_tonne(self):
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

        self.assertAlmostEqual(stockpile.cost_preplan, 1.2)
        self.assertAlmostEqual(stockpile.cost_period_1, 1.2)
        self.assertAlmostEqual(stockpile.cost_period_2, 1.2)

    def test_disabled_penalty_has_zero_cost(self):
        calendar = self.calendar_inputs()
        calendar["solver_config"][
            "rehandle_cycle_time_penalty_enabled"
        ] = False
        loader = DataLoader(
            self.stockpile(),
            calendar,
            pd.DataFrame(),
            [],
        )

        stockpile = loader.create_stockpile_data_objects(
            loader.stockpile_data,
            loader.calendar_inputs,
        )[0]

        self.assertEqual(stockpile.cost_preplan, 0.0)

    def test_legacy_calendar_cash_is_forced_to_zero(self):
        calendar = self.calendar_inputs()
        calendar["stockpiles_sp1_cash"] = {
            "Preplan": 999,
            "Period_1": -250,
            "Period_2": 10,
        }
        loader = DataLoader(
            self.stockpile(),
            calendar,
            pd.DataFrame(),
            [],
        )

        stockpile = loader.create_stockpile_data_objects(
            loader.stockpile_data,
            loader.calendar_inputs,
        )[0]

        self.assertEqual(stockpile.cash_preplan, 0.0)
        self.assertEqual(stockpile.cash_period_1, 0.0)
        self.assertEqual(stockpile.cash_period_2, 0.0)

    def test_enabled_penalty_rejects_missing_route(self):
        loader = DataLoader(
            self.stockpile(cycle_time=None),
            self.calendar_inputs(),
            pd.DataFrame(),
            [],
        )

        with self.assertRaisesRegex(
            ValueError,
            "No selected-crusher haul cycle.*SP1",
        ):
            loader.create_stockpile_data_objects(
                loader.stockpile_data,
                loader.calendar_inputs,
            )


if __name__ == "__main__":
    unittest.main()
