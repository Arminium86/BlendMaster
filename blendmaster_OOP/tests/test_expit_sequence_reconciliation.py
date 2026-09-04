import unittest
import tempfile

import pandas as pd

from classes.ExpitSequenceReconciler import (
    ExpitSequenceReconciler,
    grade_block_key,
    polygon_lookup_name,
)
from classes.ExpitDataHandler import ExpitDataHandler


def source(block, slice_number=1):
    return (
        "Reserves/CC1/CUE01/01/414/111/417/"
        f"{block}_{slice_number}"
    )


def payload(agent, block, tonnes, hour, slice_number=1, waste=False):
    start = pd.Timestamp("2026-08-01 00:00") + pd.Timedelta(hours=hour)
    return {
        "agent": agent,
        "source": source(block, slice_number),
        "start_datetime": start,
        "delivered_datetime": start + pd.Timedelta(minutes=20),
        "payload": tonnes,
        "route_material": "Waste" if waste else "Ore",
        "route_only_waste": waste,
        "route_segment": hour + 1,
        "source_properties": {
            "modelled_rom_wmt": tonnes,
            "modelled_rom_fe": 58.0,
        },
    }


def actual(agent, block, tonnes, hour):
    return {
        "agent": agent,
        "source_fms": polygon_lookup_name(source(block)),
        "transaction_datetime": (
            pd.Timestamp("2026-08-01 00:00") + pd.Timedelta(hours=hour)
        ),
        "actual_wmt": tonnes,
        "movement_type": "ExPit",
    }


def square(block, centre_x, centre_y):
    name = polygon_lookup_name(source(block))
    return [
        {
            "full_name": name,
            "point": index,
            "easting": x,
            "northing": y,
        }
        for index, (x, y) in enumerate((
            (centre_x - 1, centre_y - 1),
            (centre_x + 1, centre_y - 1),
            (centre_x + 1, centre_y + 1),
            (centre_x - 1, centre_y + 1),
        ), start=1)
    ]


def geological(block, wet_tonnes, dry_tonnes=0, material="Ore"):
    return {
        "full_name": polygon_lookup_name(source(block)),
        "gb_wet_tonnes": wet_tonnes,
        "gb_dry_tonnes": dry_tonnes,
        "gb_material": material,
        "is_ore": material.lower() == "ore",
        "record_created_dt": pd.Timestamp("2026-07-31 12:00"),
    }


def cumulative_actual(block, tonnes):
    return {
        "full_name": polygon_lookup_name(source(block)),
        "cumulative_actual_wmt": tonnes,
        "cumulative_first_actual_datetime": pd.Timestamp("2026-07-20"),
        "cumulative_last_actual_datetime": pd.Timestamp("2026-08-01 01:00"),
    }


class ExpitSequenceReconciliationTests(unittest.TestCase):
    def test_material_types_are_derived_from_sliced_aps_sources(self):
        with tempfile.NamedTemporaryFile(
            suffix=".csv", mode="w", newline="", delete=False
        ) as handle:
            pd.DataFrame([
                {
                    "Source.Type": "GradeBlock",
                    "Source.FullName": source("BA72", 63),
                },
                {
                    "Source.Type": "GradeBlock",
                    "Source.FullName": source("LG46", 12),
                },
                {
                    "Source.Type": "Stockpile",
                    "Source.FullName": "Stockpiles/RP01",
                },
            ]).to_csv(handle, index=False)
            path = handle.name
        try:
            self.assertEqual(
                ExpitDataHandler.distinct_expit_material_types(path),
                ["BA", "LG"],
            )
        finally:
            import os
            os.remove(path)

    def test_keys_match_aps_source_fms_and_polygon_conventions(self):
        aps = source("LG46", 627)
        source_fms = "CUE01_01_0414_111_0417_LG46"
        self.assertEqual(grade_block_key(aps), grade_block_key(source_fms))
        self.assertEqual(
            polygon_lookup_name(aps),
            "CUE01_01_0414_111_0417_LG46",
        )

    def test_ninety_percent_marks_parent_complete_and_removes_all_slices(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 50, 0, 627),
            payload("EX01", "LG46", 50, 1, 124),
            payload("EX01", "LG47", 100, 2),
        ])
        movements = pd.DataFrame([actual("EX01", "LG46", 90, 1)])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        self.assertNotIn("LG46", " ".join(result.transactions["source"]))
        complete = result.audit[
            result.audit["parent_grade_block"].astype(str).str.contains("LG46")
        ].iloc[0]
        self.assertEqual(complete["completion_status"], "Complete")
        self.assertEqual(complete["remaining_wmt"], 0)

    def test_partial_parent_keeps_only_remaining_payload_and_additive_mass(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 50, 0, 627),
            payload("EX01", "LG46", 50, 1, 124),
        ])
        movements = pd.DataFrame([actual("EX01", "LG46", 25, 1)])
        result = ExpitSequenceReconciler(
            10,
            {"modelled_rom_wmt": "additive", "modelled_rom_fe": "weighted_average"},
        ).reconcile(
            plan, movements, schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        self.assertAlmostEqual(result.transactions["payload"].sum(), 75.0)
        first = result.transactions.iloc[0]
        self.assertAlmostEqual(first["payload"], 25.0)
        self.assertAlmostEqual(first["source_properties"]["modelled_rom_wmt"], 25.0)
        self.assertEqual(first["source_properties"]["modelled_rom_fe"], 58.0)

    def test_geological_tonnes_are_audit_only_and_do_not_create_payloads(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 40, 0, 627),
            payload("EX01", "LG46", 10, 1, 124),
        ])
        result = ExpitSequenceReconciler(10).reconcile(
            plan,
            pd.DataFrame([actual("EX01", "LG46", 20, 1)]),
            geological_blocks=pd.DataFrame([
                geological("LG46", 1000, 920),
            ]),
            cumulative_actual=pd.DataFrame([
                cumulative_actual("LG46", 200),
            ]),
            schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        row = result.audit.iloc[0]
        self.assertEqual(row["aps_planned_wmt"], 50)
        self.assertEqual(row["actual_schedule_wmt"], 20)
        self.assertEqual(row["aps_remaining_wmt"], 30)
        self.assertEqual(row["nominal_geological_wmt"], 1000)
        self.assertEqual(row["nominal_geological_dmt"], 920)
        self.assertEqual(row["cumulative_actual_wmt"], 200)
        self.assertEqual(row["estimated_geological_remaining_wmt"], 800)
        self.assertEqual(row["aps_share_of_nominal_pct"], 5)
        self.assertEqual(row["geological_depletion_pct"], 20)
        self.assertAlmostEqual(result.transactions["payload"].sum(), 30)

    def test_missing_geological_record_is_explicit_but_keeps_aps_plan(self):
        plan = pd.DataFrame([payload("EX01", "LG46", 50, 0)])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, pd.DataFrame(), geological_blocks=pd.DataFrame(),
            cumulative_actual=pd.DataFrame(),
            schedule_start="2026-08-01 00:00", as_of="2026-08-01 03:00",
        )
        row = result.audit.iloc[0]
        self.assertFalse(row["geological_data_available"])
        self.assertIn("No active nominal", row["geological_warning"])
        self.assertEqual(result.transactions["payload"].sum(), 50)

    def test_geological_overdraw_is_clamped_without_removing_schedule(self):
        plan = pd.DataFrame([payload("EX01", "LG46", 50, 0)])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, pd.DataFrame(),
            geological_blocks=pd.DataFrame([geological("LG46", 1000)]),
            cumulative_actual=pd.DataFrame([cumulative_actual("LG46", 1050)]),
            schedule_start="2026-08-01 00:00", as_of="2026-08-01 03:00",
        )
        row = result.audit.iloc[0]
        self.assertEqual(row["estimated_geological_remaining_wmt"], 0)
        self.assertEqual(row["geological_depletion_pct"], 105)
        self.assertEqual(row["geological_completion_status"], "Complete")
        self.assertEqual(result.transactions["payload"].sum(), 50)

    def test_geological_completion_uses_configured_tolerance(self):
        result = ExpitSequenceReconciler(10).reconcile(
            pd.DataFrame([payload("EX01", "LG46", 50, 0)]),
            pd.DataFrame(),
            geological_blocks=pd.DataFrame([geological("LG46", 1000)]),
            cumulative_actual=pd.DataFrame([cumulative_actual("LG46", 900)]),
            schedule_start="2026-08-01 00:00", as_of="2026-08-01 03:00",
        )

        row = result.audit.iloc[0]
        self.assertEqual("Complete", row["geological_completion_status"])
        self.assertAlmostEqual(0.1, row["geological_remaining_fraction"])
        # Geological completion affects the map only, not APS payloads.
        self.assertEqual(50, result.transactions["payload"].sum())

    def test_remaining_polygon_area_depletes_from_north_to_south(self):
        polygon = [(0, 0), (10, 0), (10, 10), (0, 10)]

        remaining = ExpitSequenceReconciler.remaining_polygon(polygon, 0.4)

        self.assertAlmostEqual(
            40.0,
            ExpitSequenceReconciler._polygon_area(remaining),
            places=6,
        )
        self.assertAlmostEqual(4.0, max(point[1] for point in remaining))

        depleted = ExpitSequenceReconciler.depleted_polygon(polygon, 0.4)
        self.assertAlmostEqual(
            60.0,
            ExpitSequenceReconciler._polygon_area(depleted),
            places=6,
        )
        self.assertAlmostEqual(4.0, min(point[1] for point in depleted))

    def test_geological_cumulative_query_is_lifetime_through_as_of(self):
        captured = {}

        class Cursor:
            description = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, query, parameters):
                captured["query"] = query
                captured["parameters"] = parameters

            def fetchall(self):
                return []

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        handler = ExpitDataHandler.__new__(ExpitDataHandler)
        handler.connect_snowflake_with_service_account = lambda: Connection()

        handler.fetch_grade_block_geological_audit(
            [source("LG46")], pd.Timestamp("2026-08-01")
        )

        self.assertIn("TRANSACTION_DATETIME <= %(as_of)s", captured["query"])
        self.assertEqual(
            pd.Timestamp("2026-08-01").to_pydatetime(),
            captured["parameters"]["as_of"],
        )
        self.assertNotIn("TRANSACTION_DATETIME >=", captured["query"])

    def test_unmatched_actual_is_audited_but_not_inserted(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 50, 0),
            payload("EX01", "LG47", 50, 1),
        ])
        movements = pd.DataFrame([
            actual("EX01", "LG99", 20, 0),
            actual("EX01", "LG46", 10, 1),
        ])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        self.assertFalse(
            result.transactions["source"].astype(str).str.contains("LG99").any()
        )
        unmatched = result.audit[
            result.audit["record_type"] == "unmatched_actual_context"
        ]
        self.assertEqual(len(unmatched), 1)
        self.assertIn("not inserted", unmatched.iloc[0]["warning"].lower())

    def test_waste_affects_time_route_but_does_not_flow_to_optimizer(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 50, 0),
            payload("EX01", "WA01", 50, 1, waste=True),
            payload("EX01", "LG47", 50, 2),
        ])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, pd.DataFrame(), schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        self.assertFalse(result.transactions["route_only_waste"].astype(bool).any())
        self.assertTrue(
            result.audit["parent_grade_block"].astype(str).str.contains("WA01").any()
        )

    def test_actual_planned_waste_is_matched_and_not_reported_as_unmatched(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 50, 0),
            payload("EX01", "WA01", 50, 1, waste=True),
            payload("EX01", "LG47", 50, 2),
        ])
        movements = pd.DataFrame([
            actual("EX01", "LG46", 50, 0),
            actual("EX01", "WA01", 50, 1),
        ])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        waste = result.audit[
            result.audit["parent_grade_block"].astype(str).str.contains("WA01")
        ].iloc[0]
        self.assertEqual(waste["completion_status"], "Complete")
        self.assertFalse(
            result.audit["record_type"].eq("unmatched_actual_context").any()
        )

    def test_repeated_planned_ore_parent_is_reconciled_once(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 25, 0, 1),
            payload("EX01", "LG47", 50, 1, 1),
            payload("EX01", "LG46", 25, 2, 2),
        ])
        movements = pd.DataFrame([actual("EX01", "LG46", 10, 0)])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        lg46_rows = result.audit[
            result.audit["parent_grade_block"].astype(str).str.contains("LG46")
            & result.audit["record_type"].eq("planned_parent")
        ]
        self.assertEqual(len(lg46_rows), 1)
        self.assertAlmostEqual(
            result.transactions[
                result.transactions["source"].astype(str).str.contains("LG46")
            ]["payload"].sum(),
            40.0,
        )
        self.assertEqual(
            [
                "LG46" if "LG46" in value else "LG47"
                for value in result.transactions["source"].astype(str)
            ],
            ["LG46", "LG47", "LG46"],
        )

    def test_planned_multi_parent_slice_pattern_is_not_a_false_reversal(self):
        plan = pd.DataFrame([
            payload("EX01", "A", 100, 0, 101),
            payload("EX01", "B", 100, 1, 101),
            payload("EX01", "B", 100, 2, 102),
            payload("EX01", "A", 100, 3, 102),
            payload("EX01", "A", 100, 4, 103),
            payload("EX01", "B", 100, 5, 103),
            payload("EX01", "B", 100, 6, 104),
            payload("EX01", "A", 100, 7, 104),
        ])
        movements = pd.DataFrame([
            actual("EX01", "A", 100, 0),
            actual("EX01", "B", 200, 1),
            actual("EX01", "A", 200, 2),
            actual("EX01", "B", 200, 3),
        ])

        result = ExpitSequenceReconciler(10).reconcile(
            plan,
            movements,
            schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 06:00",
        )

        agent = result.summary["agents"]["EX01"]
        self.assertEqual(0, agent["direction_reversals"])
        self.assertFalse(agent["course_correction_applied"])
        self.assertEqual(8, agent["planned_operational_occurrences"])
        self.assertEqual(1, agent["remaining_operational_occurrences"])
        self.assertIn("A_104", result.transactions.iloc[0]["source"])

    def test_operational_slice_inference_supports_more_than_two_parents(self):
        plan = pd.DataFrame([
            payload("EX01", "A", 100, 0, 101),
            payload("EX01", "B", 100, 1, 101),
            payload("EX01", "C", 100, 2, 101),
            payload("EX01", "A", 100, 3, 102),
            payload("EX01", "C", 100, 4, 102),
            payload("EX01", "B", 100, 5, 102),
            payload("EX01", "D", 100, 6, 101),
        ])
        movements = pd.DataFrame([
            actual("EX01", "A", 100, 0),
            actual("EX01", "B", 100, 1),
            actual("EX01", "C", 100, 2),
            actual("EX01", "A", 100, 3),
            actual("EX01", "C", 100, 4),
        ])

        result = ExpitSequenceReconciler(10).reconcile(
            plan,
            movements,
            schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 06:00",
        )

        agent = result.summary["agents"]["EX01"]
        self.assertEqual(0, agent["direction_reversals"])
        self.assertFalse(agent["course_correction_applied"])
        remaining_sources = result.transactions["source"].astype(str).tolist()
        self.assertIn("B_102", remaining_sources[0])
        self.assertIn("D_101", remaining_sources[1])

    def test_every_agent_is_retained_when_one_has_no_actual_match(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 50, 0),
            payload("EX02", "LG47", 50, 0),
        ])
        movements = pd.DataFrame([actual("EX01", "LG46", 10, 1)])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        self.assertEqual(set(result.transactions["agent"]), {"EX01", "EX02"})
        self.assertEqual(
            result.summary["agents"]["EX02"]["confidence"], "Fallback"
        )

    def test_back_and_forth_route_detects_reversal(self):
        blocks = ["LG46", "LG47", "LG48", "LG49"]
        plan = pd.DataFrame([
            payload("EX01", block, 100, index)
            for index, block in enumerate(blocks)
        ])
        movements = pd.DataFrame([
            actual("EX01", "LG46", 100, 0),
            actual("EX01", "LG47", 100, 1),
            actual("EX01", "LG48", 100, 2),
            actual("EX01", "LG49", 50, 3),
            actual("EX01", "LG48", 5, 4),
            actual("EX01", "LG47", 5, 5),
        ])
        geometry = pd.DataFrame(sum([
            square(block, index * 10, 0)
            for index, block in enumerate(blocks)
        ], []))
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, geometry,
            schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 06:00",
        )
        agent = result.summary["agents"]["EX01"]
        self.assertGreaterEqual(agent["direction_reversals"], 1)
        self.assertTrue(agent["direction"].startswith("reverse"))
        self.assertTrue(agent["course_correction_applied"])

    def test_geometry_does_not_reorder_an_aps_route_without_deviation(self):
        plan = pd.DataFrame([
            payload("EX01", "LG46", 100, 0),
            payload("EX01", "LG47", 100, 1),
            payload("EX01", "LG48", 100, 2),
        ])
        # LG48 is geometrically nearer to LG46 than LG47. A nearest-neighbour
        # algorithm would change the plan even though actual progress agrees.
        geometry = pd.DataFrame(sum([
            square("LG46", 0, 0),
            square("LG47", 100, 0),
            square("LG48", 1, 0),
        ], []))
        movements = pd.DataFrame([actual("EX01", "LG46", 100, 0)])
        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, geometry,
            schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 03:00",
        )
        sources = result.transactions.groupby(
            "updated_parent_sequence", sort=True
        )["source"].first().tolist()
        self.assertIn("LG47", sources[0])
        self.assertIn("LG48", sources[1])
        self.assertFalse(
            result.summary["agents"]["EX01"]["course_correction_applied"]
        )

    def test_alternating_distant_faces_are_projected_as_relocations(self):
        face_a = ["A01", "A02", "A03", "A04", "A05"]
        face_b = ["B01", "B02", "B03", "B04"]
        plan = pd.DataFrame([
            payload("EX01", block, 100, index)
            for index, block in enumerate(face_a + face_b)
        ])
        movements = pd.DataFrame([
            actual("EX01", "A01", 100, 0),
            actual("EX01", "A02", 100, 1),
            actual("EX01", "B01", 100, 2),
            actual("EX01", "B02", 100, 3),
            actual("EX01", "A03", 100, 4),
        ])
        geometry = pd.DataFrame(sum([
            square(block, index * 10, 0)
            for index, block in enumerate(face_a)
        ] + [
            square(block, 1000 + index * 10, 0)
            for index, block in enumerate(face_b)
        ], []))

        result = ExpitSequenceReconciler(10).reconcile(
            plan, movements, geometry,
            schedule_start="2026-08-01 00:00",
            as_of="2026-08-01 06:00",
        )

        agent = result.summary["agents"]["EX01"]
        self.assertTrue(agent["relocation_pattern_detected"])
        self.assertEqual(agent["relocation_face_count"], 2)
        remaining = result.transactions.groupby(
            "updated_parent_sequence", sort=True
        )["source"].first().tolist()
        self.assertIn("A04", remaining[0])
        self.assertIn("B03", remaining[1])
        self.assertIn("B04", remaining[2])
        self.assertIn("A05", remaining[3])


if __name__ == "__main__":
    unittest.main()
