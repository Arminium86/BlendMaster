import os
import sys
import unittest
from datetime import datetime

import pandas as pd


PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from setup.DataStreamReconciliation import DataStreamReconciliation  # noqa: E402


class ReconciliationTests(unittest.TestCase):
    def test_snowflake_sql_escapes_literal_percent_wildcards(self):
        query = DataStreamReconciliation.SQL_PATH.read_text(encoding="utf-8")
        self.assertEqual(query.count("%s"), 6)
        self.assertNotIn("_%'", query)
        self.assertNotIn("PC%'", query)

        # Snowflake's default pyformat binding performs this interpolation
        # before submitting SQL. Literal wildcard percents must survive it.
        rendered = query % (("2026-07-01", "2026-08-01") * 3)
        self.assertIn("LIKE 'OP1_%'", rendered)
        self.assertIn("LIKE 'IB_PC%'", rendered)

    def daily_frame(self, opf="CB_OPF", brand="CCFB"):
        rows = []
        for shift_date, feed, product, blend, regression in (
            ("2026-07-30", 100.0, 40.0, 1.1, 0.9),
            ("2026-07-29", 300.0, 60.0, 1.3, 1.1),
        ):
            row = {
                "DERIVED_OPERATION": opf,
                "BRAND": brand,
                "SHIFT_DATE": pd.Timestamp(shift_date),
                "FEED_WMT": feed,
                "PROD_WMT": product,
            }
            for suffix in ("FE", "SIO2", "AL2O3", "P", "MN"):
                row[f"BLEND_RECON_{suffix}"] = blend
                row[f"REGRESSION_RECON_{suffix}"] = regression
            rows.append(row)
        return pd.DataFrame(rows)

    def test_weighted_factors_and_shortest_window(self):
        factors, warnings = DataStreamReconciliation().aggregate(
            self.daily_frame(), "CB OPF", ["CCFB"], datetime(2026, 8, 1, 12)
        )
        record = factors["CCFB"]
        self.assertAlmostEqual(record["blend"]["fe"]["calculated"], 1.25)
        self.assertAlmostEqual(record["regression"]["fe"]["calculated"], 1.02)
        self.assertEqual(record["lookback_days"]["fe"], {"blend": 7, "regression": 7})
        self.assertEqual(warnings, [])

    def test_missing_brand_uses_reproducible_substitute(self):
        loader = DataStreamReconciliation()
        first, first_warnings = loader.aggregate(
            self.daily_frame(), "CB OPF", ["OTHER"], datetime(2026, 8, 1)
        )
        second, _ = loader.aggregate(
            self.daily_frame(), "CB OPF", ["OTHER"], datetime(2026, 8, 1)
        )
        self.assertEqual(first["OTHER"]["source_brand"], "CCFB")
        self.assertEqual(first, second)
        self.assertTrue(first_warnings)

    def test_site_prefix_brand_is_matched_before_random_fallback(self):
        factors, warnings = DataStreamReconciliation().aggregate(
            self.daily_frame(opf="CC_OPF02", brand="CCFB"),
            "CC OPF02",
            ["FB"],
            datetime(2026, 8, 1),
        )

        self.assertEqual(factors["FB"]["source_brand"], "CCFB")
        self.assertFalse(factors["FB"]["substituted_brand"])
        self.assertEqual(warnings, [])

    def test_dry_regression_is_one_and_locked(self):
        factors, _ = DataStreamReconciliation().aggregate(
            self.daily_frame(opf="EW_OPF"), "EW OPF", ["CCFB"], datetime(2026, 8, 1)
        )
        regression = factors["CCFB"]["regression"]["fe"]
        self.assertEqual(regression["effective"], 1.0)
        self.assertTrue(regression["locked"])

    def test_missing_analyte_uses_other_brand_independently(self):
        requested = self.daily_frame(brand="OTHER")
        available = self.daily_frame(brand="CCFB")
        daily = pd.concat([requested, available], ignore_index=True)
        daily.loc[daily["BRAND"] == "OTHER", "BLEND_RECON_FE"] = None
        factors, warnings = DataStreamReconciliation().aggregate(
            daily,
            "CB OPF",
            ["OTHER"],
            datetime(2026, 8, 1),
        )
        record = factors["OTHER"]
        self.assertEqual(
            record["source_brand_by_analyte"]["fe"]["blend"], "CCFB"
        )
        self.assertEqual(
            record["source_brand_by_analyte"]["si"]["blend"], "OTHER"
        )
        self.assertTrue(any("blend recon uses CCFB" in warning for warning in warnings))

    def test_cb_campaign_fines_uses_paired_history_through_sixty_days(self):
        daily = self.daily_frame(opf="CB_OPF", brand="CBSF")
        daily["CBFL_CAMPAIGN"] = 0
        campaign = daily.iloc[[0]].copy()
        campaign["SHIFT_DATE"] = pd.Timestamp("2026-06-20")
        campaign["CBFL_CAMPAIGN"] = 1
        campaign["PROD_WMT"] = 50.0
        for suffix in ("FE", "SIO2", "AL2O3", "P", "MN"):
            campaign[f"REGRESSION_RECON_{suffix}"] = 1.2
        factors, warnings = DataStreamReconciliation().aggregate(
            pd.concat([daily, campaign], ignore_index=True),
            "CB OPF",
            ["SF"],
            datetime(2026, 8, 1),
        )
        special = factors["SF"][DataStreamReconciliation.CB_CAMPAIGN_FACTOR]["fe"]
        self.assertAlmostEqual(special["calculated"], 1.2)
        self.assertEqual(
            factors["SF"]["lookback_days"]["fe"][
                DataStreamReconciliation.CB_CAMPAIGN_FACTOR
            ],
            60,
        )
        self.assertFalse(any("no paired CBSF+CBFL" in item for item in warnings))

    def test_cb_campaign_fines_warns_and_falls_back_to_standard_sf(self):
        daily = self.daily_frame(opf="CB_OPF", brand="CBSF")
        daily["CBFL_CAMPAIGN"] = 0
        factors, warnings = DataStreamReconciliation().aggregate(
            daily, "CB OPF", ["SF"], datetime(2026, 8, 1)
        )
        standard = factors["SF"]["regression"]["fe"]["effective"]
        special = factors["SF"][DataStreamReconciliation.CB_CAMPAIGN_FACTOR]["fe"]
        self.assertAlmostEqual(special["effective"], standard)
        self.assertTrue(any("no paired CBSF+CBFL" in item for item in warnings))


if __name__ == "__main__":
    unittest.main()
