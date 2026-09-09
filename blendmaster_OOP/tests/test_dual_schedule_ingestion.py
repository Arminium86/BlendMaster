import tempfile
import unittest
from pathlib import Path

import pandas as pd

from classes.EventData import EventData
from classes.ExpitDataHandler import ExpitDataHandler
from classes.Optimizer import Optimizer
from classes.PeriodManager import PeriodManager
from execute.Run import Run


def reserve_row(
    source,
    pit,
    destination,
    tonnes,
    start,
    end,
    destination_type="Stockpile",
):
    return {
        "Agent.Name": "EX01",
        "Source.Type": "Reserve",
        "Source.FullName": source,
        "Source.Pit": pit,
        "OriginalSource.Name": "",
        "Destination.Type": destination_type,
        "Destination.Name": destination.rsplit("/", 1)[-1],
        "Destination.FullName": destination,
        "Time.StartTime": start,
        "Time.EndTime": end,
        "Mining.wetTonnes": tonnes,
        "Mining.grades_fe": 60.0,
        "Mining.grades_si": 4.0,
        "Mining.grades_al": 2.0,
        "Mining.grades_mn": 0.1,
        "Mining.grades_p": 0.08,
        "HaulageResult.Times.Dumping": 1.0,
        "HaulageResult.Times.LoadedTravel": 10.0,
        "HaulageResult.LoaderProductionRate.Wtph": 1000.0,
        "HaulageResult.Times.SpotAtDump": 1.0,
        "HaulageResult.Times.SpotAtLoader": 1.0,
        "HaulageResult.TruckPayload": None,
        "HaulageResult.NumberOfTrips": None,
    }


def feed_row(stockpile, brand, tonnes, start, end):
    row = reserve_row(
        source="Flow/Plant",
        pit="",
        destination=f"Crushers/{brand}",
        tonnes=tonnes,
        start=start,
        end=end,
        destination_type="Crusher",
    )
    row.update({
        "Agent.Name": "PlantAgent",
        "Source.Type": "Flow",
        "OriginalSource.Name": stockpile,
        "Destination.Name": brand,
    })
    return row


class DualScheduleDestinationTests(unittest.TestCase):
    def write_csv(self, directory, name, rows):
        path = Path(directory) / name
        pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def test_destination_guidance_preserves_waste_as_route_context_only(self):
        ore = reserve_row(
            "Reserves/CC1/CUE01/01/414/111/417/LG46_1",
            "CUE01",
            "Stockpiles/SP_A",
            100,
            "01/01/2026 00:00",
            "01/01/2026 01:00",
        )
        waste = reserve_row(
            "Reserves/CC1/CUE01/01/414/112/417/WA01_1",
            "CUE01",
            "WasteDumps/WD_A",
            100,
            "01/01/2026 01:00",
            "01/01/2026 02:00",
            destination_type="WasteDump",
        )
        waste["Source.Type"] = "Waste"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self.write_csv(temp_dir, "24hr.csv", [ore, waste])
            handler = ExpitDataHandler(
                path,
                destination_guidance=(
                    ExpitDataHandler.build_2wp_destination_guidance(path)
                ),
                preserve_source_payloads_for_reconciliation=True,
            )
            transactions = handler.process_transactions()
        self.assertTrue(transactions["route_only_waste"].astype(bool).any())
        self.assertEqual(
            set(handler.data["Source.Type"].astype(str)),
            {"Reserve", "Waste"},
        )

    def test_24hr_destination_uses_closest_dated_2wp_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = "Reserves/EW/PIT_A/01/100/1"
            two_wp_path = self.write_csv(
                temp_dir,
                "2wp.csv",
                [
                    reserve_row(
                        source,
                        "PIT_A",
                        "Stockpiles/SP_A",
                        750,
                        "01/01/2026 00:00",
                        "01/01/2026 01:00",
                    ),
                    reserve_row(
                        source,
                        "PIT_A",
                        "Stockpiles/SP_B",
                        250,
                        "02/01/2026 00:00",
                        "02/01/2026 01:00",
                    ),
                ],
            )
            twenty_four_hour_path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [
                    reserve_row(
                        source,
                        "PIT_A",
                        "Crushers/IGNORED_24HR_DESTINATION",
                        1000,
                        "03/01/2026 00:00",
                        "03/01/2026 01:00",
                        destination_type="Crusher",
                    )
                ],
            )

            guidance = ExpitDataHandler.build_2wp_destination_guidance(
                two_wp_path
            )
            transactions = ExpitDataHandler(
                twenty_four_hour_path,
                destination_guidance=guidance,
            ).process_transactions()

            self.assertEqual(len(transactions), 1)
            self.assertEqual(
                transactions.iloc[0]["destination"],
                "Stockpiles/SP_B",
            )
            self.assertEqual(
                set(transactions["two_wp_destination_resolution"]),
                {"exact_2wp"},
            )
            self.assertAlmostEqual(transactions["payload"].sum(), 1000.0)

    def test_exact_destination_receives_full_horizon_turnover_priority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = "Reserves/CC/PIT_A/BLOCK_1"
            two_wp_path = self.write_csv(
                temp_dir,
                "2wp.csv",
                [
                    reserve_row(
                        source,
                        "PIT_A",
                        "Stockpiles/SP_LATE",
                        100,
                        "01/01/2026 00:00",
                        "01/01/2026 01:00",
                    ),
                    feed_row(
                        "Stockpiles/SP_LATE",
                        "FB",
                        100,
                        "08/01/2026 00:00",
                        "08/01/2026 12:00",
                    ),
                    # Establish the actual schedule horizon beyond the first
                    # reclaim so the expected score is not trivially 1.0.
                    feed_row(
                        "Stockpiles/OTHER",
                        "FB",
                        100,
                        "15/01/2026 00:00",
                        "15/01/2026 12:00",
                    ),
                ],
            )
            twenty_four_hour_path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [reserve_row(
                    source,
                    "PIT_A",
                    "Crushers/IGNORED",
                    100,
                    "01/01/2026 00:00",
                    "01/01/2026 01:00",
                    destination_type="Crusher",
                )],
            )

            guidance = ExpitDataHandler.build_2wp_destination_guidance(
                two_wp_path
            )
            transactions = ExpitDataHandler(
                twenty_four_hour_path,
                destination_guidance=guidance,
            ).process_transactions()

            row = transactions.iloc[0]
            self.assertTrue(row["two_wp_turnover_guidance_applicable"])
            self.assertEqual(
                row["two_wp_first_reclaim_datetime"],
                pd.Timestamp("2026-01-08").isoformat(),
            )
            self.assertGreater(
                row["two_wp_destination_turnover_priority"], 0.4
            )
            self.assertLess(
                row["two_wp_destination_turnover_priority"], 0.6
            )

    def test_no_reclaim_gets_highest_priority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = "Reserves/CC/PIT_A/BLOCK_1"
            path = self.write_csv(
                temp_dir,
                "2wp.csv",
                [reserve_row(
                    source,
                    "PIT_A",
                    "Stockpiles/UNTOUCHED",
                    100,
                    "01/01/2026 00:00",
                    "15/01/2026 00:00",
                )],
            )
            guidance = ExpitDataHandler.build_2wp_destination_guidance(path)
            allocation = next(iter(
                guidance["source_destinations"].values()
            ))[0]
            self.assertTrue(
                allocation["two_wp_turnover_guidance_applicable"]
            )
            self.assertEqual(
                allocation["two_wp_destination_turnover_priority"], 1.0
            )
            self.assertEqual(
                allocation["two_wp_first_reclaim_datetime"], ""
            )

    def test_run_payload_preparation_assigns_auditable_ids_and_streams(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = "Reserves/CC/PIT_A/BLOCK_1"
            path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [
                    {
                        **reserve_row(
                            source,
                            "PIT_A",
                            "Stockpiles/SP_A",
                            100,
                            "01/01/2026 00:00",
                            "01/01/2026 01:00",
                        ),
                        "ROM_Fe": 59.0,
                        "Product_Fe": 61.0,
                    }
                ],
            )
            guidance = ExpitDataHandler.build_2wp_destination_guidance(path)
            runner = Run.__new__(Run)

            transactions = runner.prepare_expit_payload_transactions(
                pd.Timestamp("2026-01-01").to_pydatetime(),
                1,
                path,
                site_context={
                    "mine": "CC",
                    "opf": "CC OPF02",
                    "crusher": "OPF02_PC",
                    "direct_tip_movement_rules": [{
                        "grade_block_source": "BLOCK_1",
                        "crusher_destination": "OPF02_PC",
                    }],
                    "aps_destination_guidance": guidance,
                    "product_brands": ["FB"],
                    "aps_grade_field_mappings": {
                        "rom": {"FB": {"fe": "ROM_Fe"}},
                        "product": {"FB": {"fe": "Product_Fe"}},
                    },
                },
            )

            self.assertEqual(transactions.iloc[0]["direct_tip_id"], "GB_000001")
            streams = transactions.iloc[0]["grade_streams"]
            self.assertEqual(streams["adjusted_rom"]["FB"]["fe"], 59.0)
            self.assertEqual(
                streams["adjusted_product"]["FB"]["fe"], 61.0
            )

    def test_suffix_is_ignored_then_date_and_tonnes_break_ties(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_base = "Reserves/CC1/CUE01/01/402/123/411/SO32"
            two_wp_path = self.write_csv(
                temp_dir,
                "2wp.csv",
                [
                    reserve_row(
                        f"{source_base}_174",
                        "CUE01",
                        "Stockpiles/SP_OLD",
                        900,
                        "01/01/2026 00:00",
                        "01/01/2026 01:00",
                    ),
                    reserve_row(
                        f"{source_base}_175",
                        "CUE01",
                        "Stockpiles/SP_NEAR_LOW",
                        100,
                        "03/01/2026 00:00",
                        "03/01/2026 01:00",
                    ),
                    reserve_row(
                        f"{source_base}_176",
                        "CUE01",
                        "Stockpiles/SP_NEAR_HIGH",
                        500,
                        "03/01/2026 04:00",
                        "03/01/2026 05:00",
                    ),
                ],
            )
            twenty_four_hour_path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [
                    reserve_row(
                        f"{source_base}_999",
                        "CUE01",
                        "Stockpiles/IGNORED",
                        243,
                        "04/01/2026 00:00",
                        "04/01/2026 01:00",
                    )
                ],
            )

            guidance = ExpitDataHandler.build_2wp_destination_guidance(
                two_wp_path
            )
            transactions = ExpitDataHandler(
                twenty_four_hour_path,
                destination_guidance=guidance,
            ).process_transactions()

            self.assertEqual(len(transactions), 1)
            self.assertEqual(
                transactions.iloc[0]["destination"],
                "Stockpiles/SP_NEAR_HIGH",
            )
            self.assertEqual(
                transactions.iloc[0]["two_wp_destination_resolution"],
                "exact_2wp",
            )

    def test_missing_block_uses_same_material_spatial_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            two_wp_path = self.write_csv(
                temp_dir,
                "2wp.csv",
                [
                    reserve_row(
                        "Reserves/EW/PIT_A/1/100/1/105/HG01_1",
                        "PIT_A",
                        "Stockpiles/SP_A",
                        900,
                        "01/01/2026 00:00",
                        "01/01/2026 01:00",
                    ),
                    reserve_row(
                        "Reserves/EW/PIT_A/1/100/2/105/HG02_2",
                        "PIT_A",
                        "Stockpiles/SP_B",
                        100,
                        "01/01/2026 01:00",
                        "01/01/2026 02:00",
                    ),
                ],
            )
            twenty_four_hour_path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [
                    reserve_row(
                        "Reserves/EW/PIT_A/1/100/3/105/HG99_1",
                        "PIT_A",
                        "Stockpiles/IGNORED",
                        500,
                        "02/01/2026 00:00",
                        "02/01/2026 01:00",
                    )
                ],
            )

            guidance = ExpitDataHandler.build_2wp_destination_guidance(
                two_wp_path
            )
            transactions = ExpitDataHandler(
                twenty_four_hour_path,
                destination_guidance=guidance,
            ).process_transactions()

            self.assertEqual(
                transactions.iloc[0]["destination"], "Stockpiles/SP_A"
            )
            self.assertEqual(
                transactions.iloc[0]["two_wp_destination_resolution"],
                "spatial_fallback",
            )
            self.assertEqual(
                transactions.iloc[0]["alternate_destination_1"],
                "Stockpiles/SP_B",
            )
            self.assertEqual(
                transactions.iloc[0]["alternate_destination_2"], ""
            )

    def test_missing_pit_cannot_use_unrelated_last_2wp_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            two_wp_path = self.write_csv(
                temp_dir,
                "2wp.csv",
                [
                    reserve_row(
                        "Reserves/EW/PIT_A/1/100/1/105/HG01_1",
                        "PIT_A",
                        "Stockpiles/SP_A",
                        100,
                        "02/01/2026 00:00",
                        "02/01/2026 01:00",
                    ),
                    reserve_row(
                        "Reserves/EW/PIT_B/BLOCK_1",
                        "PIT_B",
                        "Stockpiles/SP_LAST",
                        100,
                        "03/01/2026 00:00",
                        "03/01/2026 01:00",
                    ),
                ],
            )
            twenty_four_hour_path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [
                    reserve_row(
                        "Reserves/EW/PIT_UNKNOWN/BLOCK",
                        "PIT_UNKNOWN",
                        "Stockpiles/IGNORED",
                        100,
                        "04/01/2026 00:00",
                        "04/01/2026 01:00",
                    )
                ],
            )

            guidance = ExpitDataHandler.build_2wp_destination_guidance(
                two_wp_path
            )
            with self.assertRaisesRegex(ValueError, "No exact 2WP destination"):
                ExpitDataHandler(twenty_four_hour_path, destination_guidance=guidance)


class ExpitDigCircuitSelectionTests(unittest.TestCase):
    def write_csv(self, directory, name, rows):
        path = Path(directory) / name
        pd.DataFrame(rows).to_csv(path, index=False)
        return path

    def test_distinct_expit_agents_include_only_reserve_movements(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = reserve_row(
                "Reserves/EW/PIT_A/BLOCK_1",
                "PIT_A",
                "Stockpiles/SP_A",
                100,
                "01/01/2026 00:00",
                "01/01/2026 01:00",
            )
            second = reserve_row(
                "Reserves/EW/PIT_A/BLOCK_2",
                "PIT_A",
                "Stockpiles/SP_A",
                100,
                "01/01/2026 01:00",
                "01/01/2026 02:00",
            )
            second["Agent.Name"] = "EX02"
            path = self.write_csv(
                temp_dir,
                "24hr.csv",
                [
                    first,
                    second,
                    feed_row(
                        "SP_A",
                        "BRAND_X",
                        100,
                        "01/01/2026 02:00",
                        "01/01/2026 03:00",
                    ),
                ],
            )

            self.assertEqual(
                ExpitDataHandler.get_distinct_expit_agent_names(path),
                ["EX01", "EX02"],
            )

    def test_only_selected_agents_create_expit_payload_transactions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = reserve_row(
                "Reserves/EW/PIT_A/BLOCK_1",
                "PIT_A",
                "Stockpiles/SP_A",
                100,
                "01/01/2026 00:00",
                "01/01/2026 01:00",
            )
            second = reserve_row(
                "Reserves/EW/PIT_A/BLOCK_2",
                "PIT_A",
                "Stockpiles/SP_B",
                250,
                "01/01/2026 01:00",
                "01/01/2026 02:00",
            )
            second["Agent.Name"] = "EX02"
            path = self.write_csv(temp_dir, "24hr.csv", [first, second])

            transactions = ExpitDataHandler(
                path,
                selected_agent_names=["EX02"],
            ).process_transactions()

            self.assertEqual(set(transactions["agent"]), {"EX02"})
            self.assertAlmostEqual(transactions["payload"].sum(), 250.0)


class TwoWpGuidanceTests(unittest.TestCase):
    def test_product_crusher_discovery_is_independent_of_handler_instance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2wp.csv"
            pd.DataFrame([
                feed_row(
                    "SP_A",
                    "BRAND_X",
                    100,
                    "01/01/2026 00:00",
                    "01/01/2026 01:00",
                )
            ]).to_csv(path, index=False)

            self.assertEqual(
                ExpitDataHandler.get_distinct_2wp_guidance_crushers(path),
                ["Crushers/BRAND_X"],
            )

    def test_missing_mapped_grade_header_is_validated_during_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "24hr.csv"
            pd.DataFrame([
                reserve_row(
                    "Reserves/EW/PIT_A/BLOCK_1",
                    "PIT_A",
                    "Stockpiles/SP_A",
                    100,
                    "01/01/2026 00:00",
                    "01/01/2026 01:00",
                )
            ]).to_csv(path, index=False)

            with self.assertRaisesRegex(ValueError, "Missing_ROM_Fe"):
                ExpitDataHandler(
                    path,
                    grade_field_mappings={"rom": {"fe": "Missing_ROM_Fe"}},
                )

    def test_mapped_grade_fields_are_tonne_weighted_when_rows_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "24hr.csv"
            first = reserve_row(
                "Reserves/EW/PIT_A/BLOCK_1", "PIT_A", "Stockpiles/SP_A",
                100, "01/01/2026 00:00", "01/01/2026 01:00",
            )
            second = reserve_row(
                "Reserves/EW/PIT_A/BLOCK_1", "PIT_A", "Stockpiles/SP_A",
                300, "01/01/2026 01:00", "01/01/2026 02:00",
            )
            first["ROM_Fe"] = 10.0
            second["ROM_Fe"] = 20.0
            pd.DataFrame([first, second]).to_csv(path, index=False)

            handler = ExpitDataHandler(
                path,
                grade_field_mappings={
                    "rom": {"FB": {"fe": "ROM_Fe"}}
                },
                configured_product_brands=["FB"],
            )

            self.assertEqual(len(handler.data), 1)
            self.assertAlmostEqual(handler.data.iloc[0]["ROM_Fe"], 17.5)

    def test_partial_payload_top_up_weights_only_the_mixed_tonnes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "24hr.csv"
            first = reserve_row(
                "Reserves/CC/PIT_A/BLOCK_1", "PIT_A", "Stockpiles/SP_A",
                150, "01/01/2026 00:00", "01/01/2026 01:00",
            )
            second = reserve_row(
                "Reserves/CC/PIT_A/BLOCK_2", "PIT_A", "Stockpiles/SP_A",
                50, "01/01/2026 01:00", "01/01/2026 02:00",
            )
            for row, grade in ((first, 60.0), (second, 40.0)):
                row["Mining.grades_fe"] = grade
                row["ROM_Fe"] = grade + 1
                row["Product_Fe"] = grade + 2
                row["HaulageResult.TruckPayload"] = 100.0
            pd.DataFrame([first, second]).to_csv(path, index=False)

            transactions = ExpitDataHandler(
                path,
                grade_field_mappings={
                    "rom": {"FB": {"fe": "ROM_Fe"}},
                    "product": {"FB": {"fe": "Product_Fe"}},
                },
                configured_product_brands=["FB"],
            ).process_transactions()

            self.assertEqual(2, len(transactions))
            topped_up = transactions.iloc[1]
            self.assertAlmostEqual(50.0, topped_up["source_grade_fe"])
            streams = topped_up["grade_streams"]
            self.assertAlmostEqual(
                51.0, streams["adjusted_rom"]["FB"]["fe"]
            )
            self.assertAlmostEqual(
                52.0, streams["adjusted_product"]["FB"]["fe"]
            )

    def test_feed_rows_create_independent_brand_timing_and_active_blend_guidance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2wp.csv"
            rows = [
                feed_row(
                    "SP_A",
                    "BRAND_X",
                    100,
                    "01/01/2026 00:00",
                    "01/01/2026 01:00",
                ),
                feed_row(
                    "SP_B",
                    "BRAND_X",
                    200,
                    "01/01/2026 00:00",
                    "01/01/2026 01:00",
                ),
                feed_row(
                    "SP_A",
                    "BRAND_X",
                    100,
                    "01/01/2026 01:00",
                    "01/01/2026 02:00",
                ),
            ]
            pd.DataFrame(rows).to_csv(path, index=False)

            guidance = ExpitDataHandler.get_2wp_schedule_guidance(
                path,
                ["BRAND_X"],
            )

            self.assertEqual(
                guidance["brand_guidance"]["SP_A"]["primary_brand"],
                "BRAND_X",
            )
            timing_windows = guidance["stockpile_timing_guidance"]["SP_A"][
                "windows"
            ]
            self.assertEqual(len(timing_windows), 1)
            self.assertAlmostEqual(timing_windows[0]["duration_hours"], 2.0)

            active_windows = guidance["active_blend_guidance"]
            self.assertEqual(len(active_windows), 2)
            self.assertEqual(active_windows[0]["stockpiles"], ["SP_A", "SP_B"])
            self.assertEqual(active_windows[1]["stockpiles"], ["SP_A"])


class GuidanceScoringTests(unittest.TestCase):
    @staticmethod
    def event(stockpile):
        return EventData(
            stockpile=stockpile,
            grade_block=None,
            event_type="stockpile",
            equipment="RC",
            cost=0,
            cash=0,
            rate=1000,
            grade_fe=60,
            grade_si=4,
            grade_al=2,
            grade_p=0.08,
            grade_mn=0.1,
            balance=1000,
            max_quantity=1000,
            reclaim_threshold=0,
            state="Reclaim",
            auto_turnover_datetime=None,
            source_name=stockpile,
        )

    def test_active_blend_guidance_rewards_the_exact_stockpile_set(self):
        periods = PeriodManager()
        current_time = pd.Timestamp("2026-01-01 00:30:00").to_pydatetime()
        periods.calculate_periods(current_time)
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
        solver_config = {
            "target_product_brand": "BRAND_X",
            "current_steady_state_datetime": current_time,
            "active_blend_guidance_enabled": True,
            "active_blend_guidance_incentive": 5,
            "active_blend_guidance": [{
                "product_brand": "BRAND_X",
                "stockpiles": ["SP_A", "SP_B"],
                "start_datetime": pd.Timestamp("2026-01-01 00:00:00"),
                "end_datetime": pd.Timestamp("2026-01-01 01:00:00"),
            }],
        }

        result = Optimizer.run_blending_optimization(
            [self.event("SP_A"), self.event("SP_B"), self.event("SP_C")],
            target,
            1,
            None,
            None,
            periods,
            "preplan",
            solver_config=solver_config,
            excluded_source_sets=[],
        )

        selected = {
            transaction["source"]
            for transaction in result["transactions"]
            if transaction["actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE
        }
        self.assertEqual(selected, {"SP_A", "SP_B"})


if __name__ == "__main__":
    unittest.main()
