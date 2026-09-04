import os
import sqlite3
import tempfile
import unittest

import pandas as pd

from database.DatabaseContext import get_database_path, set_database_path
from database.SQLiteDatabase import DatabaseManager


class StockpileDepletionReportTests(unittest.TestCase):
    def setUp(self):
        self.previous_database = get_database_path()
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.directory.name, "report.db")
        set_database_path(self.database_path)

    def tearDown(self):
        set_database_path(self.previous_database)
        self.directory.cleanup()

    @staticmethod
    def report_row(duration_hours, actual_tonnes):
        opening = 113127.58817400002
        return {
            "steady_state_number": 6,
            "period": 2,
            "source": "CAT27_RP01_0001_CHUNK_001",
            "steady_state_duration": duration_hours,
            "equipment_rate_output": 2200.000004125695,
            "source_opening_balance": opening,
            "source_actual_tonnes": actual_tonnes,
            "source_closing_balance": opening - actual_tonnes,
            "source_grade_fe": 58.0,
            "source_grade_si": 4.0,
            "source_grade_al": 2.5,
            "source_grade_p": 0.05,
            "source_grade_mn": 0.4,
            "source_type": "AMT Chunk",
            "start_datetime": "2026-08-19 22:56:20",
        }

    def read_rows(self):
        connection = sqlite3.connect(self.database_path)
        try:
            return pd.read_sql_query(
                "SELECT * FROM optimised_stockpile_depletion_report "
                "ORDER BY start_datetime",
                connection,
            )
        finally:
            connection.close()

    def test_subsecond_steady_state_is_written_and_closes_exactly(self):
        actual = 0.44871694
        report = pd.DataFrame([
            self.report_row(0.00020396224507205181, actual)
        ])

        DatabaseManager().write_optimised_stockpile_depletion_report_to_database(
            report
        )

        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows["source_actual_tonnes"].sum(), actual)
        self.assertAlmostEqual(
            rows.iloc[-1]["source_closing_balance"],
            report.iloc[0]["source_closing_balance"],
        )
        self.assertIn(".", rows.iloc[0]["end_datetime"])

    def test_fractional_final_interval_preserves_total_actual_tonnes(self):
        actual = 2.75
        report = pd.DataFrame([
            self.report_row(1.25 / 3600.0, actual)
        ])

        DatabaseManager().write_optimised_stockpile_depletion_report_to_database(
            report
        )

        rows = self.read_rows()
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows["source_actual_tonnes"].sum(), actual)
        self.assertAlmostEqual(
            rows.iloc[-1]["source_closing_balance"],
            report.iloc[0]["source_closing_balance"],
        )


if __name__ == "__main__":
    unittest.main()
