import unittest
from datetime import datetime, timedelta

import pandas as pd

from classes.ManualBlendSummary import ManualBlendSummary
from GUI.ManualBlendDash import ManualBlendDash


class ManualBlendSummaryTests(unittest.TestCase):
    def test_bar_grades_use_selected_stream_analyte_weights(self):
        start = datetime(2026, 1, 1, 6)
        sequence = [{
            "Blend ID": "1",
            "Start Datetime": start.strftime("%Y-%m-%d %H:%M"),
            "End Datetime": (start + timedelta(hours=2)).strftime(
                "%Y-%m-%d %H:%M"
            ),
        }]
        report = pd.DataFrame([
            {
                "steady_state_number": 0,
                "start_datetime": start,
                "end_datetime": start + timedelta(hours=1),
                "blend_ID": 1,
                "source_type": "stockpile",
                "source": "SP1",
                "source_actual_tonnes": 80,
                "source_blend_ratio": 0.8,
                "crusher_actual_tonnes": 100,
                "selected_grade_stream": "adjusted_product",
                "source_grade_fe": 60,
                "selected_grade_weight_fe_tonnes": 80,
            },
            {
                "steady_state_number": 0,
                "start_datetime": start,
                "end_datetime": start + timedelta(hours=1),
                "blend_ID": 1,
                "source_type": "stockpile",
                "source": "SP2",
                "source_actual_tonnes": 20,
                "source_blend_ratio": 0.2,
                "crusher_actual_tonnes": 100,
                "selected_grade_stream": "adjusted_product",
                "source_grade_fe": 50,
                "selected_grade_weight_fe_tonnes": 20,
            },
            {
                "steady_state_number": 1,
                "start_datetime": start + timedelta(hours=1),
                "end_datetime": start + timedelta(hours=2),
                "blend_ID": 1,
                "source_type": "stockpile",
                "source": "SP1",
                "source_actual_tonnes": 100,
                "source_blend_ratio": 1.0,
                "crusher_actual_tonnes": 100,
                "selected_grade_stream": "adjusted_product",
                "source_grade_fe": 55,
                "selected_grade_weight_fe_tonnes": 100,
            },
        ])

        summary = ManualBlendSummary.build(sequence, report)[0]

        self.assertEqual("adjusted_product", summary["Optimiser Grade Stream"])
        self.assertAlmostEqual(2.0, summary["Steady State Duration (hrs)"])
        self.assertAlmostEqual(56.5, summary["Grade Fe"])
        self.assertEqual("SP1|SP2", summary["_stockpile_mix_key"])
        self.assertEqual(
            "SP1 @ 90.00% (180 t, 90 t/h); "
            "SP2 @ 10.00% (20 t, 10 t/h)",
            summary["Sources and Ratios"],
        )

    def test_stockpile_mix_key_ignores_ratio_and_blend_id(self):
        sequence = [
            {
                "Blend ID": "1",
                "Start Datetime": "2026-01-01 06:00",
                "End Datetime": "2026-01-01 07:00",
            },
            {
                "Blend ID": "2",
                "Start Datetime": "2026-01-01 07:00",
                "End Datetime": "2026-01-01 08:00",
            },
        ]
        legend = [
            {
                "Blend ID": "1",
                "Sources": "SP1, SP2",
                "Source Ratios": "0.5, 0.5",
            },
            {
                "Blend ID": "2",
                "Sources": "SP2, SP1",
                "Source Ratios": "0.8, 0.2",
            },
        ]
        summaries = ManualBlendSummary.build(
            sequence, pd.DataFrame(), legend
        )

        self.assertEqual(
            summaries[0]["_stockpile_mix_key"],
            summaries[1]["_stockpile_mix_key"],
        )

        rows = [
            {**row, "Blend Summary": summary}
            for row, summary in zip(sequence, summaries)
        ]
        dash = ManualBlendDash(rows, legend, 8052, 1000)
        payload = dash.build_chart_payload()
        self.assertEqual(
            payload["rows"][0]["_stockpile_mix_key"],
            payload["rows"][1]["_stockpile_mix_key"],
        )

    def test_direct_tip_summary_aggregates_slices_to_parent(self):
        parent = "Reserves/CC1/CUE01/01/414/111/417/LG46"
        sequence = [{
            "Blend ID": "1",
            "Start Datetime": "2026-01-01 06:00",
            "End Datetime": "2026-01-01 07:00",
        }]
        report = pd.DataFrame([
            {
                "start_datetime": "2026-01-01 06:00",
                "end_datetime": "2026-01-01 07:00",
                "blend_ID": "1",
                "source_type": "grade_block",
                "source": f"{parent}_627",
                "source_actual_tonnes": 20,
                "crusher_actual_tonnes": 100,
            },
            {
                "start_datetime": "2026-01-01 06:00",
                "end_datetime": "2026-01-01 07:00",
                "blend_ID": "1",
                "source_type": "grade_block",
                "source": f"{parent}_124",
                "source_actual_tonnes": 30,
                "crusher_actual_tonnes": 100,
            },
        ])

        summary = ManualBlendSummary.build(sequence, report)[0]

        self.assertEqual(
            f"{parent} @ 50.00% (50 t, 50 t/h)",
            summary["Direct Tip Grade Blocks"],
        )

    def test_legend_uses_selected_stream_output_not_legacy_amt(self):
        html = ManualBlendDash.__new__(ManualBlendDash).build_timeline_html()

        self.assertIn("Optimiser Grade Stream", html)
        self.assertNotIn('value === "AMT"', html)
        self.assertIn("colorForRow", html)


if __name__ == "__main__":
    unittest.main()
