import unittest
import json

from setup.AMTSpatialReconciliation import reconcile_amt_hex_rows


def row(hex_id, raw_wmt, easting, northing, inventory_balance, unattributed=0):
    return {
        "FOOTPRINT": "TEST_PILE",
        "HEX": hex_id,
        "RAW_WMT": raw_wmt,
        "SOURCEHEXEASTING": easting,
        "SOURCEHEXNORTHING": northing,
        "INVENTORY_BALANCE_WMT": inventory_balance,
        "UNATTRIBUTED_MOVEMENT_WMT": unattributed,
    }


class AMTSpatialReconciliationTests(unittest.TestCase):
    @staticmethod
    def with_lineage(value, coverage):
        value = dict(value)
        value["GRADE_BLOCK_LINEAGE_JSON"] = json.dumps([
            {
                "inbound_wmt": coverage,
                "match_method": "EXPIT_GRADEBLOCK_ID_MATCHED",
            },
            {
                "inbound_wmt": 1.0 - coverage,
                "match_method": "UNMATCHED",
            },
        ])
        return value

    def test_adjacent_positive_hexes_absorb_overdraw_progressively(self):
        rows = [
            row("negative", -50, 0, 0, 20),
            row("adjacent", 30, 1, 0, 20),
            row("next_row", 40, 2, 0, 20),
        ]

        corrected = reconcile_amt_hex_rows(rows)
        by_hex = {value["HEX"]: value for value in corrected}

        self.assertAlmostEqual(by_hex["negative"]["FINAL_WMT"], 0)
        self.assertAlmostEqual(by_hex["adjacent"]["SPATIAL_DONOR_WMT"], 30)
        self.assertAlmostEqual(by_hex["next_row"]["SPATIAL_DONOR_WMT"], 20)
        self.assertAlmostEqual(sum(value["FINAL_WMT"] for value in corrected), 20)

    def test_negative_row_prefers_material_behind_reclaim_front(self):
        rows = [
            row("negative_1", -10, 0, 0, 20),
            row("negative_2", -10, 0, 1, 20),
            row("behind_1", 10, 1, 0, 20),
            row("behind_2", 10, 1, 1, 20),
            row("behind_next", 10, 2, 0, 20),
            row("wrong_side", 10, -1, 0, 20),
        ]

        corrected = reconcile_amt_hex_rows(rows)
        by_hex = {value["HEX"]: value for value in corrected}

        self.assertAlmostEqual(by_hex["wrong_side"]["SPATIAL_DONOR_WMT"], 0)
        self.assertAlmostEqual(by_hex["wrong_side"]["FINAL_WMT"], 10)
        self.assertAlmostEqual(
            sum(value["SPATIAL_DONOR_WMT"] for value in corrected), 20
        )
        self.assertAlmostEqual(sum(value["FINAL_WMT"] for value in corrected), 20)

    def test_inventory_layer_reconciles_exactly_and_preserves_raw_audit(self):
        rows = [
            row("negative", -20, 0, 0, 65, unattributed=-5),
            row("positive_1", 60, 1, 0, 65, unattributed=-5),
            row("positive_2", 40, 2, 0, 65, unattributed=-5),
        ]

        corrected = reconcile_amt_hex_rows(rows)

        self.assertAlmostEqual(corrected[0]["RAW_STOCKPILE_WMT"], 75)
        self.assertAlmostEqual(corrected[0]["RAW_POSITIVE_STOCKPILE_WMT"], 100)
        self.assertAlmostEqual(sum(value["FINAL_WMT"] for value in corrected), 65)
        self.assertTrue(all(value["FINAL_WMT"] >= 0 for value in corrected))
        self.assertEqual(
            corrected[0]["SPATIAL_RECON_STATUS"],
            "OK_SPATIAL_AND_INVENTORY_RECONCILED",
        )

    def test_inventory_deduction_prefers_poor_lineage_coverage(self):
        rows = [
            self.with_lineage(row("poor", 100, 0, 0, 240), 0.0),
            self.with_lineage(row("partial", 100, 1, 0, 240), 0.5),
            self.with_lineage(row("complete", 100, 2, 0, 240), 1.0),
        ]

        corrected = reconcile_amt_hex_rows(rows)
        by_hex = {value["HEX"]: value for value in corrected}

        self.assertAlmostEqual(
            sum(value["FINAL_WMT"] for value in corrected), 240
        )
        self.assertGreater(
            by_hex["poor"]["INVENTORY_RECON_DEDUCTION_WMT"],
            by_hex["partial"]["INVENTORY_RECON_DEDUCTION_WMT"],
        )
        self.assertGreater(
            by_hex["partial"]["INVENTORY_RECON_DEDUCTION_WMT"],
            by_hex["complete"]["INVENTORY_RECON_DEDUCTION_WMT"],
        )
        self.assertAlmostEqual(
            by_hex["poor"]["INVENTORY_RECON_LINEAGE_COVERAGE_PCT"], 0
        )
        self.assertAlmostEqual(
            by_hex["complete"]["INVENTORY_RECON_LINEAGE_COVERAGE_PCT"], 100
        )

    def test_large_inventory_deduction_spills_into_better_covered_hexes(self):
        rows = [
            self.with_lineage(row("poor", 100, 0, 0, 50), 0.0),
            self.with_lineage(row("partial", 100, 1, 0, 50), 0.5),
            self.with_lineage(row("complete", 100, 2, 0, 50), 1.0),
        ]

        corrected = reconcile_amt_hex_rows(rows)

        self.assertAlmostEqual(
            sum(value["FINAL_WMT"] for value in corrected), 50
        )
        self.assertTrue(all(value["FINAL_WMT"] >= 0 for value in corrected))
        self.assertGreater(
            corrected[2]["INVENTORY_RECON_DEDUCTION_WMT"], 0
        )


if __name__ == "__main__":
    unittest.main()
