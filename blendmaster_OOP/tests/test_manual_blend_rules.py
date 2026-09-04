import unittest

from classes.ManualBlendRules import ManualBlendRules
from GUI.ManualBlendDash import ManualBlendDash


class ManualBlendOverlapTests(unittest.TestCase):
    @staticmethod
    def blends():
        return [
            {
                "Blend ID": "1",
                "Sources": "SP_SHARED, SP_ONE",
            },
            {
                "Blend ID": "2",
                "Sources": "SP_SHARED, SP_TWO",
            },
            {
                "Blend ID": "3",
                "Sources": "SP_THREE",
            },
        ]

    @staticmethod
    def row(blend_id, start, end):
        return {
            "Blend ID": str(blend_id),
            "Start Datetime": start,
            "End Datetime": end,
        }

    def test_blend_source_sets_allow_one_stockpile_in_multiple_blends(self):
        source_sets = ManualBlendRules.blend_source_sets(self.blends())

        self.assertIn("SP_SHARED", source_sets["1"])
        self.assertIn("SP_SHARED", source_sets["2"])

    def test_overlapping_blends_with_shared_stockpile_are_rejected(self):
        conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
            [
                self.row(
                    1,
                    "2026-01-01 00:00",
                    "2026-01-01 04:00",
                ),
                self.row(
                    2,
                    "2026-01-01 03:00",
                    "2026-01-01 06:00",
                ),
            ],
        )

        self.assertEqual(len(conflicts), 1)

    def test_touching_intervals_are_allowed(self):
        conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
            [
                self.row(
                    1,
                    "2026-01-01 00:00",
                    "2026-01-01 04:00",
                ),
                self.row(
                    2,
                    "2026-01-01 04:00",
                    "2026-01-01 06:00",
                ),
            ],
        )

        self.assertEqual(conflicts, [])

    def test_overlapping_blends_without_shared_stockpiles_are_rejected(self):
        conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
            [
                self.row(
                    1,
                    "2026-01-01 00:00",
                    "2026-01-01 04:00",
                ),
                self.row(
                    3,
                    "2026-01-01 01:00",
                    "2026-01-01 03:00",
                ),
            ],
        )

        self.assertEqual(len(conflicts), 1)

    def test_overlapping_rows_of_the_same_blend_are_rejected(self):
        conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
            [
                self.row(
                    1,
                    "2026-01-01 00:00",
                    "2026-01-01 04:00",
                ),
                self.row(
                    1,
                    "2026-01-01 01:00",
                    "2026-01-01 03:00",
                ),
            ],
        )

        self.assertEqual(len(conflicts), 1)

    def test_duration_is_used_when_end_datetime_is_missing(self):
        conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
            [
                {
                    "Blend ID": "1",
                    "Start Datetime": "2026-01-01 00:00",
                    "Duration (hrs)": 4,
                },
                {
                    "Blend ID": "2",
                    "Start Datetime": "2026-01-01 03:30",
                    "Duration (hrs)": 1,
                },
            ],
        )

        self.assertEqual(len(conflicts), 1)


class ManualBlendGanttRouteTests(unittest.TestCase):
    def test_drag_update_rejects_any_blend_bar_overlap(self):
        blends = [
            {"Blend ID": "1", "Sources": "SP_SHARED, SP_ONE"},
            {"Blend ID": "2", "Sources": "SP_SHARED, SP_TWO"},
        ]
        original_rows = [
            {
                "Blend ID": "1",
                "Start Datetime": "2026-01-01 00:00",
                "End Datetime": "2026-01-01 02:00",
                "Duration (hrs)": 2,
            },
            {
                "Blend ID": "2",
                "Start Datetime": "2026-01-01 02:00",
                "End Datetime": "2026-01-01 04:00",
                "Duration (hrs)": 2,
            },
        ]
        dash = ManualBlendDash(original_rows, blends, 8052, 1000)
        client = dash.app.server.test_client()

        response = client.post(
            "/manual-gantt-update",
            json={
                "rows": [
                    original_rows[0],
                    {
                        **original_rows[1],
                        "Start Datetime": "2026-01-01 01:00",
                        "End Datetime": "2026-01-01 03:00",
                    },
                ]
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            dash.stored_blend_sequence_table_for_gantt,
            original_rows,
        )
        self.assertIn("Blend 1 and Blend 2 overlap", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
