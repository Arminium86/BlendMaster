import json
import unittest

import pandas as pd

from setup.AMTGradeBlockLineage import (
    align_amt_grade_block_lineage,
    compact_amt_stockpile_data,
    direct_product_tonnage_select_sql,
    expit_required_select_sql,
    truck_required_select_sql,
)
from classes.SourcePropertyMappings import AMT_MODELLED_ADDITIVE_FIELDS
from GUI.DrawCharts import DrawAMTStockpile


class AMTGradeBlockLineageTests(unittest.TestCase):
    def test_compaction_keeps_raw_catalogue_and_canonical_fields_once(self):
        snapshot = {"SP1": [{
            "MODELLED_PROPERTIES_JSON": {
                "values": {"prod1_fe": 61.0},
                "coverage": {"prod1_fe": 0.8},
            },
            "MODELLED_PROD1_FE": 61.0,
            "MODELLED_PROD1_FE_COVERAGE_PCT": 80.0,
            "MODELLED_ROM_MATS": "A",
            "modelled_product_fe": 61.0,
        }]}

        compact_amt_stockpile_data(snapshot)

        row = snapshot["SP1"][0]
        self.assertNotIn("MODELLED_PROD1_FE", row)
        self.assertNotIn("MODELLED_PROD1_FE_COVERAGE_PCT", row)
        self.assertEqual(row["MODELLED_ROM_MATS"], "A")
        self.assertEqual(row["modelled_product_fe"], 61.0)
        self.assertEqual(
            row["MODELLED_PROPERTIES_JSON"]["values"]["prod1_fe"],
            61.0,
        )

    def test_direct_tonnes_fall_back_to_historical_inventory_grade_block_streams(self):
        sql = direct_product_tonnage_select_sql()
        self.assertIn(
            "COALESCE(gradeblock.PROD1_TONNES_WET, "
            "gradeblock_history.PROD1_WMT)",
            sql,
        )
        self.assertIn(
            "COALESCE(gradeblock.GB_WET_TONNES, "
            "gradeblock_history.FEED_WMT)",
            sql,
        )

    def test_opening_query_selects_only_required_expit_and_truck_columns(self):
        expit_columns = expit_required_select_sql()
        truck_columns = truck_required_select_sql()
        self.assertNotIn("*", expit_columns)
        self.assertNotIn("*", truck_columns)
        self.assertIn("expit.SOURCE_GRADEBLOCK_ID", expit_columns)
        self.assertIn("expit.PROD1_FE", expit_columns)
        self.assertIn("truck.GRADE_BLOCK", truck_columns)
        self.assertIn("truck.TRUCK_WMT", truck_columns)

    def test_aligns_contributions_and_uses_property_specific_coverage(self):
        lineage = [
            {
                "lineage_key": "ID:1",
                "match_method": "EXPIT_INTERNAL_ID",
                "inbound_wmt": 60.0,
                "rom_mats": "BID",
                "properties": {"prod1_fe": 60.0, "oretype_bid": 1.0},
            },
            {
                "lineage_key": "ID:2",
                "match_method": "EXPIT_INTERNAL_ID",
                "inbound_wmt": 30.0,
                "rom_mats": "CID",
                "properties": {
                    "prod1_fe": 40.0,
                    "prod1_sio2": 5.0,
                    "oretype_cidm": 1.0,
                },
            },
            {
                "lineage_key": "UNMATCHED",
                "match_method": "UNMATCHED",
                "inbound_wmt": 10.0,
                "properties": {},
            },
        ]
        row = align_amt_grade_block_lineage([{
            "FINAL_WMT": 50.0,
            "GRADE_BLOCK_LINEAGE_JSON": json.dumps(lineage),
        }])[0]

        self.assertAlmostEqual(row["MODELLED_PROD1_FE"], 160 / 3)
        self.assertAlmostEqual(row["MODELLED_PROD1_FE_COVERAGE_PCT"], 90.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_SIO2"], 5.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_SIO2_COVERAGE_PCT"], 30.0)
        self.assertAlmostEqual(row["LINEAGE_COVERAGE_PCT"], 90.0)
        self.assertAlmostEqual(row["LINEAGE_MATCHED_FINAL_WMT"], 45.0)
        self.assertAlmostEqual(row["LINEAGE_UNMATCHED_FINAL_WMT"], 5.0)
        self.assertEqual(row["GRADE_BLOCK_COUNT"], 2)
        self.assertEqual(row["LINEAGE_ENTRY_COUNT"], 3)
        self.assertEqual(row["MODELLED_ROM_MATS"], "BID")
        self.assertEqual(row["MODELLED_DOMINANT_ORE_TYPE"], "BID")
        aligned = json.loads(row["GRADE_BLOCK_LINEAGE_JSON"])
        self.assertAlmostEqual(sum(item["remaining_wmt"] for item in aligned), 50.0)

    def test_positive_balance_without_lineage_stays_null_and_warns(self):
        row = align_amt_grade_block_lineage([{
            "FINAL_WMT": 25.0,
            "GRADE_BLOCK_LINEAGE_JSON": None,
        }])[0]

        self.assertIsNone(row["MODELLED_PROD1_FE"])
        for property_name in AMT_MODELLED_ADDITIVE_FIELDS:
            key = f"MODELLED_{property_name.upper()}"
            self.assertIn(key, row)
            if property_name != "feed_wmt":
                self.assertIsNone(row[key])
        self.assertIsNone(row["LINEAGE_COVERAGE_PCT"])
        self.assertEqual(row["LINEAGE_UNMATCHED_FINAL_WMT"], 25.0)
        self.assertIn("No inbound grade-block lineage", row["LINEAGE_WARNING"])

    def test_zero_final_balance_has_zero_remaining_lineage(self):
        row = align_amt_grade_block_lineage([{
            "FINAL_WMT": 0.0,
            "GRADE_BLOCK_LINEAGE_JSON": json.dumps([{
                "lineage_key": "ID:1",
                "match_method": "EXPIT_INTERNAL_ID",
                "inbound_wmt": 100.0,
                "properties": {"prod2_fe": 62.0},
            }]),
        }])[0]

        self.assertIsNone(row["MODELLED_PROD2_FE"])
        self.assertEqual(
            json.loads(row["GRADE_BLOCK_LINEAGE_JSON"])[0]["remaining_wmt"],
            0.0,
        )
        self.assertEqual(row["LINEAGE_FINAL_WMT"], 0.0)

    def test_derives_additive_product_size_ultrafines_and_oretype_tonnes(self):
        row = align_amt_grade_block_lineage([{
            "FINAL_WMT": 100.0,
            "GRADE_BLOCK_LINEAGE_JSON": json.dumps([{
                "lineage_key": "ID:1",
                "match_method": "EXPIT_INTERNAL_ID",
                "inbound_wmt": 100.0,
                "properties": {
                    "feed_moisture": 10.0,
                    "prod1_mass_recovery": 80.0,
                    "prod1_moisture": 5.0,
                    "prod1_minus1mm_pct": 25.0,
                    "prod1_fines_yield_pct": 0.4,
                    "prod1_lump_yield_pct": 0.3,
                    "prod1_fines_fe": 55.0,
                    "prod1_lump_fe": 65.0,
                    "oretype_bid": 0.6,
                },
            }]),
        }])[0]

        self.assertAlmostEqual(row["MODELLED_FEED_WMT"], 100.0)
        self.assertAlmostEqual(row["MODELLED_FEED_DMT"], 90.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_DMT"], 63.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_WMT"], 70.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_MINUS_1MM_DMT"], 15.75)
        self.assertAlmostEqual(
            row["MODELLED_PROD1_MINUS_1MM_WMT"], 17.5
        )
        self.assertAlmostEqual(row["MODELLED_PROD1_FINES_WMT"], 40.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_LUMP_DMT"], 27.0)
        self.assertAlmostEqual(row["MODELLED_ORETYPE_BID_WMT"], 60.0)
        self.assertAlmostEqual(row["MODELLED_ORETYPE_BID_DMT"], 54.0)
        self.assertAlmostEqual(row["MODELLED_ORETYPE_BID_TONNES"], 60.0)

        payload = json.loads(row["MODELLED_PROPERTIES_JSON"])
        self.assertAlmostEqual(payload["values"]["prod1_wmt"], 70.0)
        self.assertAlmostEqual(payload["values"]["prod1_minus_1mm_pct"], 25.0)
        self.assertAlmostEqual(payload["values"]["lump_wmt"], 30.0)
        self.assertAlmostEqual(payload["coverage"]["prod1_wmt"], 1.0)

    def test_uses_direct_grade_block_product_tonnes_before_yield_fallback(self):
        row = align_amt_grade_block_lineage([{
            # Half of the inbound lineage remains after reclaim.  Direct
            # product tonnes in the lineage are already allocated to the
            # inbound movement and must be scaled by that same half.
            "FINAL_WMT": 50.0,
            "GRADE_BLOCK_LINEAGE_JSON": json.dumps([{
                "lineage_key": "ID:1",
                "match_method": "EXPIT_INTERNAL_ID",
                "inbound_wmt": 100.0,
                "properties": {
                    "feed_moisture": 10.0,
                    "prod1_mass_recovery": 0.95,
                    "prod1_moisture": 0.05,
                    "prod1_fines_fe": 54.0,
                    "prod1_lump_fe": 66.0,
                },
                "direct_product_tonnes": {
                    "feed_dmt": 70.0,
                    "prod1_wmt": 90.0,
                    "prod1_dmt": 80.0,
                    "prod2_wmt": 70.0,
                    "prod2_dmt": 65.0,
                    "prod3_wmt": 60.0,
                    "prod3_dmt": 55.0,
                    "prod1_fines_wmt": 35.0,
                    "prod1_fines_dmt": 30.0,
                    "prod1_lump_wmt": 55.0,
                    "prod1_lump_dmt": 50.0,
                },
            }]),
        }])[0]

        # The recovery fallback would produce 42.75 DMT; direct Grade Control
        # Product 1 DMT instead produces 80 x 50% = 40 t.
        self.assertAlmostEqual(row["MODELLED_PROD1_DMT"], 40.0)
        self.assertAlmostEqual(row["MODELLED_FEED_DMT"], 35.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_WMT"], 45.0)
        self.assertAlmostEqual(row["MODELLED_PROD2_DMT"], 32.5)
        self.assertAlmostEqual(row["MODELLED_PROD3_WMT"], 30.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_FINES_DMT"], 15.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_LUMP_WMT"], 27.5)
        # Component assays use the direct component DMT as their denominator.
        self.assertAlmostEqual(row["MODELLED_PROD1_FINES_FE"], 54.0)
        self.assertAlmostEqual(row["MODELLED_PROD1_LUMP_FE"], 66.0)
        payload = json.loads(row["MODELLED_PROPERTIES_JSON"])
        self.assertAlmostEqual(payload["coverage"]["prod3_dmt"], 1.0)

    def test_amt_chunk_carries_mapped_fields_not_the_raw_catalogue(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.get_chunk_setting = lambda _footprint, _key, default=None: default
        chart.get_chunk_plan = lambda _footprint: {"chunk_count": 1}
        chart.source_property_kinds = {
            "mapped_product_wmt": "additive",
            "mapped_product_fe": "weighted_average",
        }
        chart.source_property_weights = {
            "mapped_product_fe": "mapped_product_wmt",
        }
        common = {
            "grade_fe": 50.0,
            "grade_si": 4.0,
            "grade_al": 2.0,
            "grade_p": 0.08,
            "grade_mn": 0.1,
            "grade_streams": None,
        }
        chunk = chart.build_chunk_row("SP1", 1, [
            {
                **common,
                "hex": "H1",
                "_positive_balance": 60.0,
                "modelled_properties": {
                    "values": {"prod2_fe": 60.0, "prod1_wmt": 30.0},
                    "coverage": {"prod2_fe": 1.0, "prod1_wmt": 1.0},
                },
                "defined_fields": {
                    "mapped_product_wmt": 30.0,
                    "mapped_product_fe": 60.0,
                },
                "grade_block_lineage": [{"lineage_key": "ID:1"}],
                "lineage_matched_final_wmt": 60.0,
            },
            {
                **common,
                "hex": "H2",
                "_positive_balance": 40.0,
                "modelled_properties": {
                    "values": {"prod2_fe": 40.0, "prod1_wmt": 20.0},
                    "coverage": {"prod2_fe": 0.5, "prod1_wmt": 1.0},
                },
                "defined_fields": {
                    "mapped_product_wmt": 20.0,
                    "mapped_product_fe": 40.0,
                },
                "grade_block_lineage": [{"lineage_key": "ID:2"}],
                "lineage_matched_final_wmt": 30.0,
            },
        ], 100.0)

        values = chunk["modelled_properties"]["values"]
        self.assertAlmostEqual(values["mapped_product_wmt"], 50.0)
        self.assertAlmostEqual(values["mapped_product_fe"], 52.0)
        self.assertNotIn("prod2_fe", values)
        self.assertNotIn("prod1_wmt", values)
        self.assertEqual(chunk["grade_block_count"], 2)
        self.assertAlmostEqual(chunk["lineage_coverage_pct"], 90.0)
        self.assertAlmostEqual(chunk["lineage_unmatched_wmt"], 10.0)

    def test_saved_chunk_refreshes_all_mapped_properties_from_member_hexes(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.get_chunk_setting = lambda _footprint, _key, default=None: default
        chart.get_chunk_plan = lambda _footprint: {"chunk_count": 1}
        chart.source_property_kinds = {
            "modelled_rom_wmt": "additive",
            "modelled_product_wmt": "additive",
            "modelled_product_fe": "weighted_average",
        }
        chart.source_property_weights = {
            "modelled_product_fe": "modelled_product_wmt",
        }
        chart.data = pd.DataFrame([
            {
                "footprint": "SP1", "hex": "H1", "balance": 60.0,
                "grade_fe": 50.0, "grade_si": 4.0, "grade_al": 2.0,
                "grade_p": 0.08, "grade_mn": 0.1,
                "grade_streams": None,
                "defined_fields": {
                    "modelled_rom_wmt": 60.0,
                    "modelled_product_wmt": 30.0,
                    "modelled_product_fe": 60.0,
                },
                "modelled_properties": {
                    "values": {
                        "modelled_rom_wmt": 60.0,
                        "modelled_product_wmt": 30.0,
                        "modelled_product_fe": 60.0,
                    },
                    "coverage": {
                        "modelled_rom_wmt": 1.0,
                        "modelled_product_wmt": 1.0,
                        "modelled_product_fe": 1.0,
                    },
                },
            },
            {
                "footprint": "SP1", "hex": "H2", "balance": 40.0,
                "grade_fe": 50.0, "grade_si": 4.0, "grade_al": 2.0,
                "grade_p": 0.08, "grade_mn": 0.1,
                "grade_streams": None,
                "defined_fields": {
                    "modelled_rom_wmt": 40.0,
                    "modelled_product_wmt": 10.0,
                    "modelled_product_fe": 40.0,
                },
                "modelled_properties": {
                    "values": {
                        "modelled_rom_wmt": 40.0,
                        "modelled_product_wmt": 10.0,
                        "modelled_product_fe": 40.0,
                    },
                    "coverage": {
                        "modelled_rom_wmt": 1.0,
                        "modelled_product_wmt": 1.0,
                        "modelled_product_fe": 1.0,
                    },
                },
            },
        ])
        stale = [{
            "footprint": "SP1",
            "sequence": 1,
            "hex": "SP1_CHUNK_001",
            "member_hexes": "H1,H2",
            "balance": 100.0,
            "modelled_properties": {"values": {}, "coverage": {}},
        }]

        rows, count = chart.rebuild_saved_chunk_records(stale)

        self.assertEqual(count, 1)
        values = rows[0]["modelled_properties"]["values"]
        self.assertAlmostEqual(values["modelled_rom_wmt"], 100.0)
        self.assertAlmostEqual(values["modelled_product_wmt"], 40.0)
        self.assertAlmostEqual(values["modelled_product_fe"], 55.0)
        defined_fields = rows[0]["defined_fields"]
        self.assertAlmostEqual(defined_fields["modelled_rom_wmt"], 100.0)
        self.assertAlmostEqual(defined_fields["modelled_product_wmt"], 40.0)
        self.assertAlmostEqual(defined_fields["modelled_product_fe"], 55.0)
        self.assertEqual(rows[0]["member_hexes"], "H1,H2")

    def test_chunk_retains_partially_covered_mapped_fields(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.get_chunk_setting = lambda _footprint, _key, default=None: default
        chart.get_chunk_plan = lambda _footprint: {"chunk_count": 1}
        chart.source_property_kinds = {
            "modelled_product_wmt": "additive",
            "modelled_product_fe": "weighted_average",
        }
        chart.source_property_weights = {
            "modelled_product_fe": "modelled_product_wmt",
        }
        common = {
            "grade_fe": 50.0,
            "grade_si": 4.0,
            "grade_al": 2.0,
            "grade_p": 0.08,
            "grade_mn": 0.1,
            "grade_streams": None,
            "modelled_properties": {"values": {}, "coverage": {}},
        }

        chunk = chart.build_chunk_row("SP1", 1, [
            {
                **common,
                "hex": "H1",
                "_positive_balance": 50.0,
                "defined_fields": {
                    "modelled_product_wmt": 25.0,
                    "modelled_product_fe": 60.0,
                },
            },
            {
                **common,
                "hex": "H2",
                "_positive_balance": 30.0,
                "defined_fields": {
                    "modelled_product_wmt": 15.0,
                    "modelled_product_fe": None,
                },
            },
            {
                **common,
                "hex": "H3",
                "_positive_balance": 20.0,
                "defined_fields": {
                    "modelled_product_wmt": 10.0,
                    "modelled_product_fe": 40.0,
                },
            },
        ], 100.0)

        fields = chunk["defined_fields"]
        self.assertAlmostEqual(fields["modelled_product_wmt"], 50.0)
        self.assertAlmostEqual(
            fields["modelled_product_fe"],
            (60.0 * 25.0 + 40.0 * 10.0) / 35.0,
        )
        coverage = chunk["modelled_properties"]["coverage"]
        self.assertAlmostEqual(coverage["modelled_product_wmt"], 1.0)
        self.assertAlmostEqual(coverage["modelled_product_fe"], 0.7)
        self.assertTrue(any(
            "modelled_product_fe 70.00%" in warning
            for warning in chunk["grade_stream_warnings"]
        ))

    def test_chunk_sums_available_additive_when_some_hexes_are_uncovered(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.get_chunk_setting = lambda _footprint, _key, default=None: default
        chart.get_chunk_plan = lambda _footprint: {"chunk_count": 1}
        chart.source_property_kinds = {
            "modelled_product_wmt": "additive",
            "modelled_product_fe": "weighted_average",
        }
        chart.source_property_weights = {
            "modelled_product_fe": "modelled_product_wmt",
        }
        common = {
            "grade_fe": 50.0,
            "grade_si": 4.0,
            "grade_al": 2.0,
            "grade_p": 0.08,
            "grade_mn": 0.1,
            "grade_streams": None,
            "modelled_properties": {"values": {}, "coverage": {}},
        }

        chunk = chart.build_chunk_row("SP1", 1, [
            {
                **common,
                "hex": "H1",
                "_positive_balance": 60.0,
                "defined_fields": {
                    "modelled_product_wmt": 30.0,
                    "modelled_product_fe": 60.0,
                },
            },
            {
                **common,
                "hex": "H2",
                "_positive_balance": 40.0,
                "defined_fields": {
                    "modelled_product_wmt": None,
                    "modelled_product_fe": None,
                },
            },
        ], 100.0)

        fields = chunk["defined_fields"]
        self.assertAlmostEqual(fields["modelled_product_wmt"], 30.0)
        self.assertAlmostEqual(fields["modelled_product_fe"], 60.0)
        coverage = chunk["modelled_properties"]["coverage"]
        self.assertAlmostEqual(coverage["modelled_product_wmt"], 0.6)
        self.assertAlmostEqual(coverage["modelled_product_fe"], 0.6)


if __name__ == "__main__":
    unittest.main()
