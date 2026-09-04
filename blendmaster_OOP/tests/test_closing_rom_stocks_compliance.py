import tempfile
import unittest
from pathlib import Path

import pandas as pd

from classes.ClosingROMStocksCompliance import ClosingROMStocksCompliance


class ClosingROMStocksComplianceTests(unittest.TestCase):
    def test_reads_explicit_four_column_workbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "closing.xlsx"
            pd.DataFrame([{
                "Source.Name": "Stockpiles/SP_A",
                "Period.Start DateTime": "2026-01-01 06:00:00",
                "Period.End DateTime": "2026-01-01 18:00:00",
                "Mining.wetTonnes": 1234.0,
            }]).to_excel(path, index=False)

            result = ClosingROMStocksCompliance.read_workbook(path)

            self.assertEqual(result.iloc[0]["stockpile"], "SP_A")
            self.assertEqual(
                result.iloc[0]["two_wp_closing_rom_wmt"], 1234.0
            )

    def test_uses_latest_completed_two_wp_period_at_boundary(self):
        targets = pd.DataFrame([
            {
                "stockpile": "SP_A",
                "two_wp_period_start_datetime": "2026-01-01 00:00:00",
                "two_wp_period_end_datetime": "2026-01-01 06:00:00",
                "two_wp_closing_rom_wmt": 900.0,
            },
            {
                "stockpile": "SP_A",
                "two_wp_period_start_datetime": "2026-01-01 06:00:00",
                "two_wp_period_end_datetime": "2026-01-01 18:00:00",
                "two_wp_closing_rom_wmt": 700.0,
            },
        ])
        report = ClosingROMStocksCompliance.build_report(
            plan_id="Primary",
            plan_type="optimised",
            periods={
                "preplan_start": pd.Timestamp("2026-01-01 02:00:00"),
                "preplan_end": pd.Timestamp("2026-01-01 12:00:00"),
            },
            physical_balance_history=[{
                "snapshot_datetime": pd.Timestamp("2026-01-01 12:00:00"),
                "balances": {"SP_A": 800.0},
            }],
            target_rows=targets,
            used_stockpiles={"SP_A"},
        )

        self.assertEqual(report.iloc[0]["two_wp_closing_rom_wmt"], 900.0)
        self.assertEqual(
            report.iloc[0]["blendmaster_closing_rom_wmt"], 800.0
        )
        self.assertEqual(report.iloc[0]["variance_wmt"], -100.0)

    def test_partial_plan_does_not_fill_a_later_period_boundary(self):
        report = ClosingROMStocksCompliance.build_report(
            plan_id="Primary",
            plan_type="optimised",
            periods={
                "period_1_start": pd.Timestamp("2026-08-01 06:00:00"),
                "period_1_end": pd.Timestamp("2026-08-01 18:00:00"),
            },
            physical_balance_history=[{
                "snapshot_datetime": pd.Timestamp("2026-08-01 12:00:00"),
                "balances": {"ROM_A": 950.0},
            }],
            target_rows=[{
                "stockpile": "ROM_A",
                "two_wp_period_start_datetime": pd.Timestamp(
                    "2026-08-01 06:00:00"
                ),
                "two_wp_period_end_datetime": pd.Timestamp(
                    "2026-08-01 18:00:00"
                ),
                "two_wp_closing_rom_wmt": 900.0,
            }],
            used_stockpiles={"ROM_A"},
        )

        self.assertTrue(pd.isna(
            report.iloc[0]["blendmaster_closing_rom_wmt"]
        ))
        self.assertEqual(
            report.iloc[0]["comparison_status"],
            "Missing BlendMaster balance",
        )


if __name__ == "__main__":
    unittest.main()
