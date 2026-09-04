import json
import unittest

from classes.CloudbreakProductSplit import calculate_cb_lump_fines
from classes.ProductBuildLanes import (
    default_byproduct_grade_fields,
    normalize_byproduct_grade_fields,
)
from GUI.InitialiseGUI import UserInputs


class CloudbreakProductSplitTests(unittest.TestCase):
    def test_byproduct_grade_defaults_use_explicit_chemistry_suffixes(self):
        defaults = default_byproduct_grade_fields()
        self.assertEqual(defaults["lump"]["si"], "prod1_lump_sio2")
        self.assertEqual(defaults["lump"]["al"], "prod1_lump_al2o3")
        migrated = normalize_byproduct_grade_fields({
            "fines": {
                "si": "prod1_fines_si",
                "al": "prod1_fines_al",
            }
        })
        self.assertEqual(migrated["fines"]["si"], "prod1_fines_sio2")
        self.assertEqual(migrated["fines"]["al"], "prod1_fines_al2o3")

    @staticmethod
    def calculated_view():
        view = UserInputs.__new__(UserInputs)
        view.mine_input_choice = "CB"
        view.cb_lump_fines_mode = "calculated"
        view.cb_lump_percentage = 25.0
        view.field_definitions = []
        view.historical_recon_factors = {
            "SF": {
                "cbfl_campaign_fines_regression": {
                    analyte: {"effective": (55.0 / 60.0 if analyte == "fe" else 1.0)}
                    for analyte in ("fe", "si", "al", "p", "mn")
                }
            }
        }
        return view

    @staticmethod
    def grade_streams():
        modelled = {grade: 60.0 for grade in ("fe", "si", "al", "p", "mn")}
        return {
            "modelled_product": {"SF": modelled},
            "adjusted_product": {"SF": dict(modelled)},
        }

    def test_back_calculates_lump_and_preserves_total_product_grade(self):
        result = calculate_cb_lump_fines(
            {
                "prod1_fe": 60.0,
                "prod1_fines_fe": 55.0,
                "prod1_sio2": 4.0,
            },
            30.0,
            source_wmt=100.0,
            source_dmt=90.0,
        )

        self.assertAlmostEqual(result["lump_wmt"], 30.0)
        self.assertAlmostEqual(result["fines_wmt"], 70.0)
        self.assertAlmostEqual(result["lump_dmt"], 27.0)
        self.assertAlmostEqual(result["fines_dmt"], 63.0)
        self.assertAlmostEqual(result["prod1_lump_fe"], 215.0 / 3.0)
        recombined_fe = (
            result["prod1_lump_fe"] * result["lump_dmt"]
            + result["prod1_fines_fe"] * result["fines_dmt"]
        ) / result["prod1_dmt"]
        self.assertAlmostEqual(recombined_fe, 60.0)
        self.assertEqual(result["prod1_lump_sio2"], 4.0)
        self.assertEqual(result["prod1_fines_sio2"], 4.0)
        self.assertEqual(
            result["cb_split_method"],
            "calculated_user_lump_pct_mixed_grade_methods",
        )
        self.assertIn("No independent fines grade", result["cb_split_warning"])

    def test_fraction_and_percentage_inputs_are_equivalent(self):
        fraction = calculate_cb_lump_fines(
            {"prod1_fe": 60.0}, 0.25, source_wmt=80.0
        )
        percent = calculate_cb_lump_fines(
            {"prod1_fe": 60.0}, 25.0, source_wmt=80.0
        )
        self.assertEqual(fraction["lump_wmt"], percent["lump_wmt"])
        self.assertEqual(fraction["lump_wmt"], 20.0)

    def test_accepts_inventory_total_grade_aliases(self):
        result = calculate_cb_lump_fines(
            {
                "si_prod1": 4.5,
                "al_prod1": 2.2,
                "prod1_minus_1mm_pct": 20.0,
            },
            50.0,
            source_wmt=100.0,
            source_dmt=90.0,
        )
        self.assertEqual(result["prod1_lump_sio2"], 4.5)
        self.assertEqual(result["prod1_fines_al2o3"], 2.2)
        self.assertEqual(result["prod1_minus_1mm_wmt"], 20.0)
        self.assertEqual(result["prod1_minus_1mm_dmt"], 18.0)

    def test_rejects_out_of_range_lump_percentage(self):
        with self.assertRaises(ValueError):
            calculate_cb_lump_fines({}, 101.0, source_wmt=100.0)

    def test_inventory_calculated_mode_updates_canonical_and_query_aliases(self):
        view = self.calculated_view()
        row = {
            "BALANCE": 100.0,
            "FEED_DMT": 80.0,
            "FE_PROD1": 60.0,
            "PROD1_FINES_FE": 55.0,
            "defined_fields": {
                "modelled_product_wmt": 100.0,
                "modelled_product_dmt": 80.0,
            },
            "grade_streams": self.grade_streams(),
        }

        warning = view.apply_cb_split_to_inventory_row(row)

        self.assertEqual(row["PROD1_LUMP_WMT"], 25.0)
        self.assertEqual(row["PROD1_FINES_WMT"], 75.0)
        self.assertEqual(row["PROD1_LUMP_DMT"], 20.0)
        self.assertEqual(row["PROD1_FINES_DMT"], 60.0)
        self.assertEqual(row["PROD1_LUMP_FE"], 75.0)
        self.assertEqual(
            row["CB_SPLIT_METHOD"],
            "calculated_user_lump_pct_backcalculated_lump",
        )
        self.assertEqual(warning, "")

    def test_amt_calculated_mode_updates_modelled_json_and_coverage(self):
        view = self.calculated_view()
        row = {
            "FINAL_WMT": 100.0,
            "defined_fields": {
                "modelled_product_wmt": 100.0,
                "modelled_product_dmt": 80.0,
            },
            "grade_streams": self.grade_streams(),
            "MODELLED_PROPERTIES_JSON": json.dumps({
                "values": {
                    "feed_dmt": 80.0,
                    "prod1_fe": 60.0,
                    "prod1_fines_fe": 55.0,
                },
                "coverage": {"prod1_fe": 0.8},
            }),
        }

        warning = view.apply_cb_split_to_amt_row(row)
        modelled = json.loads(row["MODELLED_PROPERTIES_JSON"])

        self.assertEqual(modelled["values"]["prod1_lump_wmt"], 25.0)
        self.assertEqual(modelled["values"]["prod1_fines_wmt"], 75.0)
        self.assertEqual(modelled["values"]["prod1_lump_fe"], 75.0)
        self.assertEqual(modelled["coverage"]["prod1_lump_fe"], 0.8)
        self.assertEqual(row["MODELLED_PROD1_LUMP_WMT"], 25.0)
        self.assertEqual(
            row["CB_SPLIT_METHOD"],
            "calculated_user_lump_pct_backcalculated_lump",
        )
        self.assertEqual(warning, "")

    def test_amt_calculated_mode_runs_on_aggregated_chunk(self):
        view = self.calculated_view()
        view.historical_recon_factors["SF"]["regression"] = {
            analyte: {
                "effective": (
                    1.1 if analyte == "fe" else
                    0.9 if analyte == "si" else
                    1.0
                )
            }
            for analyte in ("fe", "si", "al", "p", "mn")
        }
        view.historical_recon_factors["SF"][
            "cbfl_campaign_fines_regression"
        ]["si"] = {"effective": 0.8}
        view.field_mappings = []
        view.field_definitions = [
            {"name": "prod1_lump_wmt", "kind": "additive"},
            {"name": "prod1_fines_wmt", "kind": "additive"},
            *[
                {
                    "name": (
                        f"prod1_{lane}_sio2" if analyte == "si" else
                        f"prod1_{lane}_al2o3" if analyte == "al" else
                        f"prod1_{lane}_{analyte}"
                    ),
                    "kind": "weighted_average",
                    "weight_field": f"prod1_{lane}_wmt",
                }
                for lane in ("lump", "fines")
                for analyte in ("fe", "si", "al", "p", "mn")
            ],
        ]
        chunk = {
            "footprint": "CB01_RP00_0001",
            "hex": "CB01_RP00_0001_CHUNK_001",
            "defined_fields": {
                "modelled_product_wmt": 100.0,
                "modelled_product_dmt": 80.0,
            },
            "source_properties": {
                "modelled_product_wmt": 100.0,
                "modelled_product_dmt": 80.0,
            },
            "modelled_properties": {
                "values": {
                    "modelled_product_wmt": 100.0,
                    "modelled_product_dmt": 80.0,
                    **{
                        f"modelled_product_{analyte}": 60.0
                        for analyte in ("fe", "si", "al", "p", "mn")
                    },
                },
                "coverage": {
                    "modelled_product_wmt": 0.9,
                    "modelled_product_dmt": 0.8,
                    **{
                        f"modelled_product_{analyte}": 0.75
                        for analyte in ("fe", "si", "al", "p", "mn")
                    },
                },
            },
        }

        warning = view.apply_cb_split_to_amt_chunk(chunk)

        self.assertEqual(warning, "")
        self.assertEqual(
            chunk["grade_streams"]["modelled_product"]["SF"]["fe"],
            60.0,
        )
        self.assertAlmostEqual(
            chunk["grade_streams"]["adjusted_product"]["SF"]["fe"],
            66.0,
        )
        self.assertAlmostEqual(
            chunk["defined_fields"]["prod1_lump_sio2"], 72.0
        )
        self.assertAlmostEqual(
            chunk["defined_fields"]["prod1_fines_sio2"], 48.0
        )
        self.assertEqual(chunk["defined_fields"]["prod1_lump_wmt"], 25.0)
        self.assertEqual(chunk["defined_fields"]["prod1_fines_wmt"], 75.0)
        self.assertAlmostEqual(chunk["defined_fields"]["prod1_lump_fe"], 99.0)
        self.assertEqual(
            chunk["modelled_properties"]["coverage"]["prod1_lump_wmt"],
            0.9,
        )
        self.assertEqual(
            chunk["modelled_properties"]["coverage"]["prod1_lump_fe"],
            0.75,
        )
        self.assertEqual(
            chunk["CB_SPLIT_METHOD"],
            "calculated_user_lump_pct_backcalculated_lump",
        )

    def test_rebuilt_chunk_reapplies_calculated_split_before_database_view(self):
        view = self.calculated_view()
        view.product_brand_labels_choice = ["SF", "FL"]
        view.opf_input_choice = "CB OPF"
        view.field_mappings = []
        view.field_definitions = [
            {"name": "modelled_product_wmt", "kind": "additive"},
            {"name": "modelled_product_dmt", "kind": "additive"},
            {"name": "prod1_lump_wmt", "kind": "additive"},
            {"name": "prod1_fines_wmt", "kind": "additive"},
            *[
                {
                    "name": (
                        f"prod1_{lane}_sio2" if analyte == "si" else
                        f"prod1_{lane}_al2o3" if analyte == "al" else
                        f"prod1_{lane}_{analyte}"
                    ),
                    "kind": "weighted_average",
                    "weight_field": f"prod1_{lane}_wmt",
                }
                for lane in ("lump", "fines")
                for analyte in ("fe", "si", "al", "p", "mn")
            ],
        ]
        raw_rebuilt_chunk = {
            "footprint": "CB01_RP00_0001",
            "hex": "CB01_RP00_0001_CHUNK_001",
            "defined_fields": {
                "modelled_product_wmt": 100.0,
                "modelled_product_dmt": 80.0,
                "prod1_lump_wmt": 999.0,
                "prod1_fines_wmt": 0.0,
            },
            "modelled_properties": {
                "values": {
                    "modelled_product_wmt": 100.0,
                    "modelled_product_dmt": 80.0,
                    "prod1_lump_wmt": 999.0,
                    "prod1_fines_wmt": 0.0,
                    **{
                        f"modelled_product_{analyte}": 60.0
                        for analyte in ("fe", "si", "al", "p", "mn")
                    },
                },
                "coverage": {
                    "modelled_product_wmt": 1.0,
                    "modelled_product_dmt": 1.0,
                    **{
                        f"modelled_product_{analyte}": 1.0
                        for analyte in ("fe", "si", "al", "p", "mn")
                    },
                },
            },
            "grade_streams": self.grade_streams(),
        }

        class RebuildingMap:
            @staticmethod
            def rebuild_saved_chunk_records(rows):
                return [dict(raw_rebuilt_chunk)], len(rows or [])

        view.draw_AMT_map = RebuildingMap()
        view.hex_sequence_table = [dict(raw_rebuilt_chunk)]
        view.hex_sequence_table_argument = [dict(raw_rebuilt_chunk)]

        view.reconcile_saved_AMT_chunk_grade_streams()

        for chunk in (
            view.hex_sequence_table[0],
            view.hex_sequence_table_argument[0],
        ):
            self.assertEqual(chunk["defined_fields"]["prod1_lump_wmt"], 25.0)
            self.assertEqual(chunk["defined_fields"]["prod1_fines_wmt"], 75.0)
            self.assertEqual(
                chunk["modelled_properties"]["values"]["prod1_lump_wmt"],
                25.0,
            )

    def test_missing_grade_error_names_amt_source_build_hex_and_field(self):
        view = self.calculated_view()
        streams = self.grade_streams()
        streams["modelled_product"]["SF"]["si"] = None
        row = {
            "FOOTPRINT": "CB01_RP00_0001",
            "LOCATION_NAME": "CB01_RP00_0001_26001",
            "HEX": "8ca79c000000001",
            "grade_streams": streams,
        }

        with self.assertRaises(ValueError) as raised:
            view.apply_cb_split_to_amt_row(row)

        message = str(raised.exception)
        self.assertIn("AMT stockpile CB01_RP00_0001", message)
        self.assertIn("build CB01_RP00_0001_26001", message)
        self.assertIn("hex 8ca79c000000001", message)
        self.assertIn("modelled_product_si", message)
        self.assertNotIn("standard SF reconciliation for", message)

    def test_unselected_inventory_missing_product_does_not_block_amt_run(self):
        view = self.calculated_view()
        view.opf_input_choice = "CB_OPF"
        view.product_brand_labels_choice = "SF, FL"
        view.historical_recon_warnings = []
        view.file_path_24hr_choice = ""
        view.stockpile_data = {
            "UNSELECTED": {
                "name": "UNSELECTED",
                "build": "UNSELECTED_26001",
            }
        }
        view.updated_stockpile_data = {
            "SELECTED_AMT": {
                "name": "SELECTED_AMT",
                "build": "SELECTED_AMT_26001",
                "amt": True,
            }
        }

        view.apply_grade_streams_to_inventory()

        self.assertIn("grade_streams", view.stockpile_data["UNSELECTED"])
        self.assertIn(
            "grade_streams", view.updated_stockpile_data["SELECTED_AMT"]
        )

    def test_calculated_mode_populates_explicit_user_selected_lane_fields(self):
        view = self.calculated_view()
        view.byproduct_quantity_fields = {
            "lump": "custom_lump_dmt",
            "fines": "custom_fines_wmt",
        }
        view.byproduct_grade_fields = {
            lane: {
                analyte: f"custom_{lane}_{analyte}"
                for analyte in ("fe", "si", "al", "p", "mn")
            }
            for lane in ("lump", "fines")
        }
        view.field_definitions = [
            {"name": "custom_lump_dmt", "kind": "additive"},
            {"name": "custom_fines_wmt", "kind": "additive"},
            *[
                {
                    "name": f"custom_{lane}_{analyte}",
                    "kind": "weighted_average",
                    "weight_field": (
                        "custom_lump_dmt" if lane == "lump"
                        else "custom_fines_wmt"
                    ),
                }
                for lane in ("lump", "fines")
                for analyte in ("fe", "si", "al", "p", "mn")
            ],
        ]
        row = {
            "defined_fields": {
                "modelled_product_wmt": 100.0,
                "modelled_product_dmt": 80.0,
            },
            "grade_streams": self.grade_streams(),
        }

        view.apply_cb_split_to_inventory_row(row)

        self.assertEqual(row["defined_fields"]["custom_lump_dmt"], 20.0)
        self.assertEqual(row["defined_fields"]["custom_fines_wmt"], 75.0)
        self.assertEqual(row["defined_fields"]["custom_lump_fe"], 75.0)
        self.assertEqual(row["defined_fields"]["custom_fines_fe"], 55.0)


if __name__ == "__main__":
    unittest.main()
