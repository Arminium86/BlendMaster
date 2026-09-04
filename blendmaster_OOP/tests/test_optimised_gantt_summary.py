import unittest
import os
import sqlite3
import tempfile

import pandas as pd

from GUI.DrawCharts import DrawGanttChart


class OptimisedGanttSourceSummaryTests(unittest.TestCase):
    def test_metadata_only_plan_table_falls_back_without_sort_error(self):
        with tempfile.TemporaryDirectory() as folder:
            database_path = os.path.join(folder, "plans.db")
            connection = sqlite3.connect(database_path)
            pd.DataFrame(columns=["plan_id", "plan_rank"]).to_sql(
                "optimisation_plan_blend_report",
                connection,
                index=False,
            )
            expected = pd.DataFrame([{
                "steady_state_number": 0,
                "source": "SP1",
            }])
            expected.to_sql(
                "optimised_blend_report",
                connection,
                index=False,
            )
            connection.close()

            chart = DrawGanttChart.__new__(DrawGanttChart)
            chart.db_path = database_path
            chart.plan_id = "Primary"

            result = chart.fetch_data()

            self.assertEqual(["SP1"], result["source"].tolist())

    def test_legend_includes_source_tonnes_and_rate(self):
        data = pd.DataFrame([{
            "start_datetime": "2026-01-01 06:00",
            "end_datetime": "2026-01-01 08:00",
            "blend_ID": 1,
            "steady_state_number": 0,
            "steady_state_duration": 2,
            "source": "SP1",
            "source_type": "stockpile",
            "source_blend_ratio": 0.75,
            "source_actual_tonnes": 3000,
            "blend_option": 1,
            "period": "Period_1",
            "actual_direct_tip_ratio": 0,
            "crusher_rate_output": 2000,
            "crusher_actual_tonnes": 4000,
            "crusher_actual_grade_fe": 60,
            "crusher_actual_grade_si": 4,
            "crusher_actual_grade_al": 2,
            "crusher_actual_grade_p": 0.08,
            "crusher_actual_grade_mn": 0.1,
        }])

        prepared = DrawGanttChart.__new__(DrawGanttChart).prepare_gantt_data(
            data
        )

        self.assertIn(
            "SP1 @ 75.00% (3,000 t, 1,500 t/h)",
            prepared.iloc[0]["Legend"],
        )


if __name__ == "__main__":
    unittest.main()
