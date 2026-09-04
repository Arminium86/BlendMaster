import json
import os
import sqlite3
import sys
import tempfile
import unittest

import pandas as pd


PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from classes.GradeStreams import (  # noqa: E402
    amt_modelled_product_slot,
    amt_grade_streams,
    aps_grade_streams,
    flatten_grade_streams,
    inventory_grade_streams,
    inventory_product_property_aliases,
    internal_product_slot,
    normalise_aps_grade_field_mappings,
    normalise_planning_categories,
    resolve_grade_vector,
)
from database.DatabaseContext import get_database_path, set_database_path  # noqa: E402
from database.SQLiteDatabase import DatabaseManager  # noqa: E402
from GUI.DrawCharts import DrawAMTStockpile  # noqa: E402
from setup.OpeningStockpileInventories import (  # noqa: E402
    INVENTORY_ADDITIONAL_FIELDS,
    OpeningStockpileInventories,
    opening_inventory_query,
)


def factors(blend=1.1, regression=0.9):
    return {
        "CCFB": {
            "blend": {
                analyte: {"calculated": blend, "effective": blend}
                for analyte in ("fe", "si", "al", "p", "mn")
            },
            "regression": {
                analyte: {"calculated": regression, "effective": regression}
                for analyte in ("fe", "si", "al", "p", "mn")
            },
        }
    }


def inventory_row(product_slot="prod1"):
    row = {}
    for analyte in ("fe", "si", "al", "p", "mn"):
        row[f"grade_{analyte}"] = 50.0
        row[f"{analyte}_rom"] = 55.0
        row[f"{analyte}_{product_slot}"] = 60.0
    return row


class GradeStreamTests(unittest.TestCase):
    def test_opening_inventory_query_includes_extended_aps_properties(self):
        query = opening_inventory_query().upper()

        self.assertEqual(query.count("%S"), 3)
        self.assertIn("PROD1_MINUS1MM_PCT", query)
        self.assertIn("MGO_OPF_WTAVG", query)
        self.assertIn("ORETYPE_BID_DMT", query)
        self.assertIn("ORETYPE_BID_WMT", query)
        self.assertIn("PROD1_WMT", query)
        self.assertIn("PROD3_DMT", query)
        self.assertIn("PROD1_MINUS_1MM_WMT", query)
        self.assertIn("PROD1_LUMP_WMT", query)
        self.assertIn("PROD1_FINES_FE", query)
        self.assertIn("FINES_VOLUME", query)
        self.assertIn("DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS", query)
        self.assertIn("minus_1mm_pct", INVENTORY_ADDITIONAL_FIELDS)
        self.assertIn("prod1_minus_1mm_pct", INVENTORY_ADDITIONAL_FIELDS)
        self.assertIn("prod1_lump_wmt", INVENTORY_ADDITIONAL_FIELDS)

    def test_confirmed_planning_category_defaults_and_overrides(self):
        self.assertEqual(
            normalise_planning_categories(),
            {"rom": "OPF Feed", "product": "OPF Production"},
        )
        self.assertEqual(
            normalise_planning_categories({"rom": "", "product": "Site Product"}),
            {"rom": "OPF Feed", "product": "Site Product"},
        )
        self.assertEqual(
            normalise_planning_categories({"product": ""})["product"],
            "OPF Production",
        )

    def test_inventory_uses_insitu_for_rom_and_adjusts_product_independently(self):
        streams = inventory_grade_streams(
            inventory_row(), ["CCFB"], factors(), "CB OPF"
        )
        # Imported ROM is 55.0, but inventory now follows the AMT convention:
        # insitu is the modelled ROM baseline and historical blend adjusts it.
        self.assertAlmostEqual(streams["modelled_rom"]["*"]["fe"], 50.0)
        self.assertAlmostEqual(streams["modelled_rom"]["CCFB"]["fe"], 50.0)
        self.assertAlmostEqual(streams["adjusted_rom"]["CCFB"]["fe"], 55.0)
        self.assertAlmostEqual(streams["modelled_product"]["CCFB"]["fe"], 60.0)
        self.assertAlmostEqual(streams["adjusted_product"]["CCFB"]["fe"], 54.0)

    def test_amt_uses_insitu_blend_and_lineage_product_regression(self):
        streams = amt_grade_streams(
            {f"grade_{a}": 40.0 for a in ("fe", "si", "al", "p", "mn")},
            {
                f"MODELLED_PROD1_{suffix}": 60.0
                for suffix in ("FE", "SIO2", "AL2O3", "P", "MN")
            },
            ["CCFB"],
            factors(),
            "CB OPF",
        )
        self.assertAlmostEqual(streams["modelled_rom"]["*"]["fe"], 40.0)
        self.assertAlmostEqual(streams["modelled_rom"]["CCFB"]["fe"], 40.0)
        self.assertAlmostEqual(streams["adjusted_rom"]["CCFB"]["fe"], 44.0)
        self.assertAlmostEqual(streams["modelled_product"]["CCFB"]["fe"], 60.0)
        self.assertAlmostEqual(streams["adjusted_product"]["CCFB"]["fe"], 54.0)

    def test_stockpile_modelled_rom_is_flattened_for_every_brand(self):
        streams = inventory_grade_streams(
            inventory_row(), ["CCFB", "CCLB"], factors(), "CB OPF"
        )

        flattened = flatten_grade_streams(streams)

        self.assertEqual(flattened["grade_modelled_rom_fe"], 50.0)
        self.assertEqual(flattened["grade_modelled_rom_ccfb_fe"], 50.0)
        self.assertEqual(flattened["grade_modelled_rom_cclb_fe"], 50.0)

    def test_amt_cc_opf02_and_vk_use_lineage_prod2(self):
        lineage = {
            **{f"MODELLED_PROD1_{suffix}": 50.0 for suffix in ("FE", "SIO2", "AL2O3", "P", "MN")},
            **{f"MODELLED_PROD2_{suffix}": 65.0 for suffix in ("FE", "SIO2", "AL2O3", "P", "MN")},
        }
        for opf in ("CC OPF02", "VK OPF", "KV OPF"):
            with self.subTest(opf=opf):
                streams = amt_grade_streams(
                    {f"grade_{a}": 40.0 for a in ("fe", "si", "al", "p", "mn")},
                    lineage,
                    ["CCFB"],
                    factors(),
                    opf,
                )
                self.assertAlmostEqual(
                    streams["modelled_product"]["CCFB"]["fe"], 65.0
                )
                self.assertAlmostEqual(
                    streams["adjusted_product"]["CCFB"]["fe"], 58.5
                )

    def test_dry_plant_product_aliases_adjusted_rom(self):
        streams = amt_grade_streams(
            {f"grade_{a}": 40.0 for a in ("fe", "si", "al", "p", "mn")},
            inventory_row(),
            ["CCFB"],
            factors(),
            "EW OPF",
        )
        self.assertEqual(
            streams["adjusted_product"]["CCFB"],
            streams["adjusted_rom"]["CCFB"],
        )

    def test_amt_ib_product_falls_back_to_adjusted_rom(self):
        streams = amt_grade_streams(
            {f"grade_{a}": 40.0 for a in ("fe", "si", "al", "p", "mn")},
            {},
            ["CCFB"],
            factors(),
            "IB OPF",
        )

        selected, warnings = resolve_grade_vector(
            streams, "adjusted_product", "CCFB"
        )

        self.assertAlmostEqual(selected["fe"], 44.0)
        self.assertEqual(
            {warning["used_stream"] for warning in warnings}, {"adjusted_rom"}
        )

    def test_internal_product_slot_rules(self):
        self.assertEqual(internal_product_slot("CB OPF"), "prod1")
        self.assertEqual(internal_product_slot("CC OPF01"), "prod1")
        self.assertEqual(internal_product_slot("CC OPF02"), "prod3")
        self.assertEqual(internal_product_slot("KV OPF"), "prod2")
        self.assertIsNone(internal_product_slot("FT OPF"))
        self.assertEqual(amt_modelled_product_slot("CB OPF"), "prod1")
        self.assertEqual(amt_modelled_product_slot("CC OPF01"), "prod1")
        self.assertEqual(amt_modelled_product_slot("CC OPF02"), "prod2")
        self.assertEqual(amt_modelled_product_slot("VK OPF"), "prod2")
        self.assertIsNone(amt_modelled_product_slot("IB OPF"))

    def test_cc_opf02_inventory_properties_use_canonical_prod2_aliases(self):
        aliases = inventory_product_property_aliases({
            "prod2_wmt": 20.0,
            "prod3_wmt": 80.0,
            "prod3_dmt": 72.0,
            "fe_prod3": 61.0,
            "dryyield_prod3_wtavg": 0.8,
            "moisture_prod3_wtavg": 0.1,
            "minus_1mm_pct": 12.5,
        }, "CC OPF02")

        self.assertEqual(aliases["prod2_wmt"], 80.0)
        self.assertEqual(aliases["prod2_dmt"], 72.0)
        self.assertEqual(aliases["fe_prod2"], 61.0)
        self.assertEqual(aliases["prod2_mass_recovery"], 0.8)
        self.assertEqual(aliases["prod2_moisture"], 0.1)
        self.assertEqual(aliases["prod2_minus_1mm_pct"], 12.5)
        self.assertEqual(aliases["prod2_minus_1mm_wmt"], 10.0)
        self.assertEqual(aliases["prod2_minus_1mm_dmt"], 9.0)

    def test_aps_mapped_product_is_authoritative(self):
        mappings = {
            "rom": {a: f"rom_{a}_header" for a in ("fe", "si", "al", "p", "mn")},
            "product": {
                "CCFB": {a: f"ccfb_{a}_header" for a in ("fe", "si", "al", "p", "mn")}
            },
        }
        row = {
            **{f"grade_{a}": 45.0 for a in ("fe", "si", "al", "p", "mn")},
            **{f"rom_{a}_header": 50.0 for a in ("fe", "si", "al", "p", "mn")},
            **{f"ccfb_{a}_header": 60.0 for a in ("fe", "si", "al", "p", "mn")},
        }
        streams = aps_grade_streams(row, mappings, ["CCFB"])
        self.assertEqual(streams["adjusted_rom"]["CCFB"]["fe"], 50.0)
        self.assertEqual(streams["adjusted_product"]["CCFB"]["fe"], 60.0)

    def test_aps_rom_headers_are_brand_specific_with_legacy_migration(self):
        mappings = {
            "rom": {
                "CCFB": {a: f"ccfb_rom_{a}" for a in ("fe", "si", "al", "p", "mn")},
                "CBFL": {a: f"cbfl_rom_{a}" for a in ("fe", "si", "al", "p", "mn")},
            },
            "product": {},
        }
        row = {
            **{f"grade_{a}": 45.0 for a in ("fe", "si", "al", "p", "mn")},
            **{f"ccfb_rom_{a}": 50.0 for a in ("fe", "si", "al", "p", "mn")},
            **{f"cbfl_rom_{a}": 55.0 for a in ("fe", "si", "al", "p", "mn")},
        }

        streams = aps_grade_streams(row, mappings, ["CCFB", "CBFL"])

        self.assertEqual(streams["adjusted_rom"]["CCFB"]["fe"], 50.0)
        self.assertEqual(streams["adjusted_rom"]["CBFL"]["fe"], 55.0)
        migrated = normalise_aps_grade_field_mappings(
            {"rom": {"fe": "legacy_rom_fe"}}, ["CCFB", "CBFL"]
        )
        self.assertEqual(migrated["rom"]["CCFB"]["fe"], "legacy_rom_fe")
        self.assertEqual(migrated["rom"]["CBFL"]["fe"], "legacy_rom_fe")

    def test_fallback_is_independent_per_analyte(self):
        streams = {
            "adjusted_product": {"CCFB": {"fe": 61.0}},
            "adjusted_rom": {"CCFB": {"si": 5.0}},
            "insitu": {"*": {"al": 2.0, "p": 0.1, "mn": 0.2}},
        }
        resolved, warnings = resolve_grade_vector(
            streams, "adjusted_product", "CCFB"
        )
        self.assertEqual(resolved, {"fe": 61.0, "si": 5.0, "al": 2.0, "p": 0.1, "mn": 0.2})
        self.assertEqual({item["analyte"] for item in warnings}, {"si", "al", "p", "mn"})

    def test_opening_inventory_tables_persist_flat_streams_and_amt_match_audit(self):
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "streams.db")
            set_database_path(database_path)
            try:
                streams = inventory_grade_streams(
                    inventory_row(), ["CCFB"], factors(), "CB OPF"
                )
                inventory = {
                    "SP1": {
                        "NAME": "SP1",
                        "BUILD": "SP1_26001",
                        "TRANSACTION_DATETIME": "2026-07-31 12:00:00",
                        "BALANCE": 1000,
                        **{
                            f"GRADE_{analyte.upper()}": 50.0
                            for analyte in ("fe", "si", "al", "p", "mn")
                        },
                        **{
                            f"{analyte.upper()}_ROM": 55.0
                            for analyte in ("fe", "si", "al", "p", "mn")
                        },
                        **{
                            f"{analyte.upper()}_PROD1": 60.0
                            for analyte in ("fe", "si", "al", "p", "mn")
                        },
                        "MINUS_1MM_PCT": 12.345,
                        "FINES_WMT": 640.0,
                        "MGO_OPF_WTAVG": 0.55,
                        "GRADE_STREAMS": streams,
                    }
                }
                loader = OpeningStockpileInventories()
                loader.save_to_database(inventory)
                loader.save_AMT_to_database({
                    "SP1": [{
                        "FOOTPRINT": "SP1",
                        "HEX": "H1",
                        "FINAL_WMT": 100,
                        "FE": 40,
                        "SIO2": 4,
                        "AL2O3": 2,
                        "P": 0.08,
                        "MN": 0.1,
                        "GRADE_STREAMS": streams,
                        "AMT_INVENTORY_MATCHED": True,
                        "AMT_INVENTORY_STOCKPILE": "SP1",
                        "AMT_INVENTORY_BUILD": "SP1_26001",
                        "AMT_INVENTORY_TRANSACTION_DATETIME": "2026-07-31 12:00:00",
                        "AMT_INVENTORY_MATCH_RULE": "latest inventory build at or before scenario start",
                        "GRADE_BLOCK_LINEAGE_JSON": [{
                            "lineage_key": "ID:1", "remaining_wmt": 100
                        }],
                        "MODELLED_PROPERTIES_JSON": {
                            "values": {"prod1_fe": 61.0},
                            "coverage": {"prod1_fe": 1.0},
                        },
                        "GRADE_BLOCK_COUNT": 1,
                        "LINEAGE_COVERAGE_PCT": 100.0,
                        "MODELLED_PROD1_FE": 61.0,
                        "MODELLED_PROD1_FE_COVERAGE_PCT": 100.0,
                        "defined_fields": {
                            "modelled_rom_wmt": 100.0,
                            "modelled_product_wmt": 80.0,
                            "modelled_product_fe": 61.0,
                            "adjusted_product_fe": None,
                        },
                    }]
                })
                connection = sqlite3.connect(database_path)
                try:
                    inventory_row_stored = connection.execute(
                        "SELECT transaction_datetime, grade_adjusted_product_ccfb_fe, "
                        "minus_1mm_pct, fines_wmt, mgo_opf_wtavg "
                        "FROM opening_stockpile_inventories WHERE name = 'SP1'"
                    ).fetchone()
                    amt_row_stored = connection.execute(
                        "SELECT amt_inventory_matched, amt_inventory_build, "
                        "grade_block_count, lineage_coverage_pct, modelled_product_fe, "
                        "adjusted_product_fe, defined_fields_json, "
                        "modelled_properties_json, grade_streams_json "
                        "FROM opening_AMT_stockpile_inventories WHERE hex = 'H1'"
                    ).fetchone()
                    amt_columns = {
                        row[1] for row in connection.execute(
                            "PRAGMA table_info(opening_AMT_stockpile_inventories)"
                        )
                    }
                finally:
                    connection.close()

                chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
                chart.db_path = database_path
                fetched_amt_row = chart.fetch_data().iloc[0]
            finally:
                set_database_path(previous_path)

        self.assertEqual(inventory_row_stored[0], "2026-07-31 12:00:00")
        self.assertAlmostEqual(inventory_row_stored[1], 54.0)
        self.assertEqual(inventory_row_stored[2:], (12.345, 640.0, 0.55))
        self.assertEqual(amt_row_stored[:2], (1, "SP1_26001"))
        self.assertEqual(amt_row_stored[2], 1)
        self.assertAlmostEqual(amt_row_stored[3], 100.0)
        self.assertAlmostEqual(amt_row_stored[4], 61.0)
        self.assertIsNone(amt_row_stored[5])
        stored_defined_fields = json.loads(amt_row_stored[6])
        stored_modelled_properties = json.loads(amt_row_stored[7])
        stored_grade_streams = json.loads(amt_row_stored[8])
        self.assertEqual(stored_defined_fields["modelled_rom_wmt"], 100.0)
        self.assertEqual(stored_defined_fields["modelled_product_wmt"], 80.0)
        self.assertEqual(stored_defined_fields["modelled_product_fe"], 61.0)
        self.assertIsNone(stored_defined_fields["adjusted_product_fe"])
        self.assertEqual(
            fetched_amt_row["defined_fields"], stored_defined_fields
        )
        self.assertEqual(
            stored_modelled_properties["values"]["prod1_fe"], 61.0
        )
        self.assertEqual(
            stored_grade_streams["adjusted_product"]["CCFB"]["fe"], 54.0
        )
        self.assertEqual(
            fetched_amt_row["modelled_properties"],
            stored_modelled_properties,
        )
        self.assertNotIn("modelled_prod1_fe", amt_columns)
        self.assertNotIn("grade_adjusted_product_ccfb_fe", amt_columns)
        self.assertLess(len(amt_columns), 100)
        self.assertFalse(any(
            name.startswith("internal_recon_")
            or name.startswith("internal_blend_recon_")
            or name.startswith("internal_upgrade_")
            for name in amt_columns
        ))

    def test_plan_result_writer_serializes_structured_grade_streams(self):
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "plan-results.db")
            set_database_path(database_path)
            try:
                streams = {
                    "adjusted_product": {
                        "CCFB": {"fe": 61.0, "si": 4.0}
                    }
                }
                DatabaseManager().write_optimisation_plan_result(
                    "build",
                    pd.DataFrame([{
                        "source": "GB1",
                        "payload": 240.0,
                        "grade_streams": streams,
                    }]),
                    "Primary",
                    0,
                )
                connection = sqlite3.connect(database_path)
                try:
                    stored = connection.execute(
                        "SELECT grade_streams FROM "
                        "optimisation_plan_build_report"
                    ).fetchone()[0]
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertEqual(
            stored,
            '{"adjusted_product": {"CCFB": {"fe": 61.0, "si": 4.0}}}',
        )


if __name__ == "__main__":
    unittest.main()
