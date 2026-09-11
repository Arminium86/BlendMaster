import unittest
from datetime import datetime, timedelta

import pandas as pd

from classes.ManualBlendSummary import ManualBlendSummary
from GUI.ManualBlendDash import ManualBlendDash


class ManualBlendSummaryTests(unittest.TestCase):
    def test_adjacent_same_blend_bars_do_not_double_count_second_precision_reports(self):
        start = datetime(2026, 8, 20, 3, 6, 46, 250000)
        middle = datetime(2026, 8, 20, 3, 25, 33, 625000)
        end = datetime(2026, 8, 20, 3, 57, 31, 250000)
        sequence, records = [], []
        for state, (left, right) in enumerate(((start, middle), (middle, end)), 8):
            hours = (right - left).total_seconds() / 3600
            sequence.append({
                "Blend ID": "8", "_fixed_steady_state": True,
                "_exact_start": left.isoformat(), "_exact_end": right.isoformat(),
                "Start Datetime": left.strftime("%Y-%m-%d %H:%M"),
                "End Datetime": right.strftime("%Y-%m-%d %H:%M"),
                "Duration (hrs)": round(hours, 1),
            })
            for source, ratio in (("CAT27", .525), ("OPF02", .475)):
                records.append({
                    "steady_state_number": state, "blend_ID": 8,
                    "start_datetime": left, "end_datetime": right,
                    "source": source, "source_type": "stockpile",
                    "source_actual_tonnes": 5176.888869 * ratio * hours,
                    "crusher_actual_tonnes": 5176.888869 * hours,
                    "source_blend_ratio": ratio,
                    "crusher_actual_grade_fe": 54.69,
                })
        precise = pd.DataFrame(records)
        saved = precise.copy()
        for column in ("start_datetime", "end_datetime"):
            saved[column] = saved[column].dt.strftime("%Y-%m-%d %H:%M:%S")
        expected = ManualBlendSummary.build(sequence, precise)
        actual = ManualBlendSummary.build(sequence, saved)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0]["Source Rates"], actual[1]["Source Rates"])
        self.assertNotEqual(actual[0]["Source Tonnes"], actual[1]["Source Tonnes"])
        totals = sum(sum(float(n) for n in row["Source Tonnes"].split(",")) for row in actual)
        self.assertAlmostEqual(totals, precise.source_actual_tonnes.sum(), places=5)

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
