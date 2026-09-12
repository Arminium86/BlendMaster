import unittest
import os
import sqlite3
import tempfile

import pandas as pd

from GUI.DrawCharts import DrawGanttChart


class OptimisedGanttSourceSummaryTests(unittest.TestCase):
    def test_profile_paging_preserves_every_source_and_search_can_select_any_source(self):
        from GUI.DrawCharts import DrawStockProfiles
        data = pd.DataFrame([{'stockpile': f'S{i:03}', 'balance': 100 - j}
                             for i in range(29) for j in range(2)])
        combined = []
        for page in (1, 2, 3):
            frame, _, options, pages = DrawStockProfiles.profile_page(data, page=page)
            self.assertLessEqual(frame.stockpile.nunique(), 12)
            self.assertEqual((len(options), pages), (29, 3))
            combined.extend(frame.index)
        self.assertEqual(sorted(combined), list(data.index))
        selected, _, _, _ = DrawStockProfiles.profile_page(data, source='S028')
        self.assertEqual(selected.balance.tolist(), [100, 99])
        self.assertEqual(DrawStockProfiles.profile_page(data, page=500)[0].stockpile.nunique(), 5)

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

    def report(self):
        return pd.DataFrame([{
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

    def test_legend_includes_source_tonnes_and_rate(self):
        report = self.report()
        second = report.copy()
        second['source'], second['source_actual_tonnes'] = 'SP2', 1000
        prepared = DrawGanttChart.__new__(DrawGanttChart).prepare_gantt_data(
            pd.concat([report, second], ignore_index=True)
        )

        self.assertIn(
            "SP1 @ 75.00% (3,000 t, 1,500 t/h)",
            prepared.iloc[0]["Legend"],
        )

    def test_legend_reconstructs_physical_ratios_without_mutating_legacy_report(self):
        report = pd.concat([self.report()] * 3, ignore_index=True)
        report['source'] = ['A', 'B', 'Direct tip']
        report['source_actual_tonnes'] = [425, 425, 150]
        report['source_blend_ratio'] = [.43, .43, .15]
        prepared = DrawGanttChart.__new__(DrawGanttChart).prepare_gantt_data(report)
        self.assertIn('A @ 42.50%', prepared.iloc[0]['Legend'])
        self.assertIn('Direct tip @ 15.00%', prepared.iloc[0]['Legend'])
        self.assertEqual(report.source_blend_ratio.tolist(), [.43, .43, .15])

    def test_timeline_click_retains_end_timestamp_after_plotly_duration_conversion(self):
        import json
        from plotly.utils import PlotlyJSONEncoder
        from classes.BlendSnapshot import selected_state
        data = self.report()
        second = data.copy()
        second['steady_state_number'] = 1
        second['start_datetime'], second['end_datetime'] = '2026-01-01 08:00', '2026-01-01 09:00'
        data = pd.concat([data, second], ignore_index=True)
        chart = DrawGanttChart(':memory:', 18906)
        chart.fetch_data = lambda: data.copy()
        callback = next(value['callback'].__wrapped__ for key, value in chart.app.callback_map.items()
                        if 'gantt-chart.figure' in key)
        figure, _ = callback(None)
        click = json.loads(json.dumps({'points': [{'customdata': figure.data[0].customdata[0].tolist()}]},
                                     cls=PlotlyJSONEncoder))
        selected = selected_state(chart.prepare_gantt_data(data.copy()), click)
        self.assertEqual(selected.steady_state_number.tolist(), [0])
        self.assertEqual(selected.iloc[0].end_datetime, pd.Timestamp('2026-01-01 08:00'))

    def test_snapshot_keeps_four_decimal_grade_precision(self):
        chart = DrawGanttChart(':memory:', 18907)
        report = self.report()
        report['crusher_actual_grade_p'] = .052210357
        chart.fetch_data = lambda: report.copy()
        chart.report_selected_columns = ['source', 'crusher_actual_grade_p', 'source_actual_tonnes']
        callback = next(value['callback'].__wrapped__ for key, value in chart.app.callback_map.items()
                        if 'property-table.data' in key)
        rows, _, _, _ = callback(None, 0)
        self.assertEqual(rows[0]['crusher_actual_grade_p'], '0.0522')
        self.assertEqual(rows[0]['source_actual_tonnes'], '3,000')


if __name__ == "__main__":
    unittest.main()
