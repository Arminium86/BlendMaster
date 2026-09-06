import unittest

from classes.CustomConstraints import (
    constraint_property_fields,
    expand_required_property_keys,
    merge_source_properties,
    scale_additive_source_properties,
)
from classes.FieldDefinitions import (
    apply_field_mappings,
    default_field_definitions,
    flatten_available_source_fields,
    mandatory_field_names,
    mapping_lookup,
    normalize_field_mappings,
    optimization_field_names,
    standardize_available_mapping_fields,
    validate_field_definitions,
)
from classes.GradeStreams import (
    STREAM_LABELS,
    UNBRANDED,
    inventory_grade_streams,
    reweight_grade_streams_from_properties,
    weighted_merge_grade_streams,
)
from classes.SourcePropertyMappings import AMT_MODELLED_ADDITIVE_FIELDS


class FieldDefinitionTests(unittest.TestCase):
    def test_default_schema_contains_required_stream_names(self):
        definitions = default_field_definitions()
        names = {row["name"] for row in definitions}
        required = mandatory_field_names()
        for stream in (
            "insitu", "modelled_rom", "adjusted_rom",
            "modelled_product", "adjusted_product",
        ):
            for analyte in ("fe", "si", "al", "p", "mn"):
                self.assertIn(f"{stream}_{analyte}", names)
                self.assertIn(f"{stream}_{analyte}", required)
        self.assertTrue({
            "modelled_rom_wmt", "modelled_rom_dmt",
            "modelled_product_wmt", "modelled_product_dmt",
        } <= required)
        self.assertNotIn("source_wmt", required)
        weights = {row["name"]: row["weight_field"] for row in definitions}
        self.assertEqual("modelled_rom_wmt", weights["modelled_rom_fe"])
        self.assertEqual("modelled_rom_wmt", weights["insitu_fe"])
        self.assertEqual("modelled_rom_wmt", weights["adjusted_rom_fe"])
        self.assertEqual("modelled_product_dmt", weights["modelled_product_fe"])
        self.assertEqual("modelled_product_dmt", weights["adjusted_product_fe"])

    def test_data_stream_labels_use_canonical_field_patterns(self):
        self.assertEqual("insitu_<grades>", STREAM_LABELS["insitu"])
        self.assertEqual(
            "adjusted_product_<grades>",
            STREAM_LABELS["adjusted_product"],
        )

    def test_mapped_insitu_property_overlays_insitu_stream(self):
        streams = {
            "insitu": {UNBRANDED: {"fe": 50.0}},
        }
        updated = reweight_grade_streams_from_properties(
            streams, {"insitu_fe": 61.5}
        )
        self.assertEqual(61.5, updated["insitu"][UNBRANDED]["fe"])

    def test_amt_lineage_json_exposes_per_hex_product_mass_for_mapping(self):
        fields = flatten_available_source_fields({
            "MODELLED_PROPERTIES_JSON": (
                '{"values":{"prod1_dmt":750,"prod1_wmt":900},'
                '"coverage":{"prod1_dmt":0.8}}'
            ),
        })
        self.assertEqual(750, fields["prod1_dmt"])
        self.assertEqual(900, fields["prod1_wmt"])
        self.assertEqual(750, fields["MODELLED_PROD1_DMT"])
        self.assertEqual(900, fields["MODELLED_PROD1_WMT"])
        self.assertEqual(80.0, fields["MODELLED_PROD1_DMT_COVERAGE_PCT"])

    def test_source_wmt_and_modelled_rom_wmt_mirror_when_one_is_mapped(self):
        values = apply_field_mappings(
            {"feed_wmt": 123.0},
            default_field_definitions(),
            [{
                "source_family": "amt",
                "target_field": "modelled_rom_wmt",
                "source_field": "feed_wmt",
            }],
            "amt",
        )
        self.assertEqual(123.0, values["modelled_rom_wmt"])
        self.assertEqual(123.0, values["source_wmt"])

    def test_descriptive_rom_mass_mapping_resolves_parenthesised_raw_id(self):
        values = apply_field_mappings(
            {"feed_wmt": 123.0, "feed_dmt": 110.0},
            default_field_definitions(),
            [
                {
                    "source_family": "amt",
                    "target_field": "modelled_rom_wmt",
                    "source_field": "ROM / opening stockpile WMT (feed_wmt)",
                },
                {
                    "source_family": "amt",
                    "target_field": "modelled_rom_dmt",
                    "source_field": "ROM / opening stockpile DMT (feed_dmt)",
                },
            ],
            "amt",
        )

        self.assertEqual(123.0, values["modelled_rom_wmt"])
        self.assertEqual(110.0, values["modelled_rom_dmt"])

    def test_available_mapping_fields_are_only_grades_and_tonnes(self):
        fields = standardize_available_mapping_fields([
            "feed_wmt", "feed_dmt", "source_wmt", "tonnes", "FINAL_WMT",
            "prod1_wmt", "prod1_dmt", "prod1_fe", "grade_si",
            "MGO_OPF_WTAVG", "prod1_lump_loi_total", "prod1_lump_as",
            "fines_fe", "prod1_fines_fe",
            "modelled_fines_fe", "modelled_prod1_fines_fe",
            "MODELLED_PROD1_FINES_FE", "MODELLED_PROD1_FE",
            "modelled_product_fe", "adjusted_rom_fe", "adjusted_product_fe",
            "adjusted_product_fb_fe", "grade_adjusted_rom_ss_fe",
            "grade_adjusted_product_fe",
            "grade_block_fe", "MODELLED_GRADE_BLOCK_FE",
            "feed_loi_total", "MODELLED_FEED_LOI_TOTAL",
            "prod1_lump_sio2", "MODELLED_PROD1_LUMP_SIO2",
            "prod2_al2o3", "MODELLED_PROD2_AL2O3",
            "lump_wmt", "prod1_lump_wmt",
            "oretype_bid_wmt", "MODELLED_ORETYPE_BID_WMT",
            "prod1_wmt", "MODELLED_PROD1_WMT", "MODELLED_PROD1_DMT",
            "modelled_product_wmt", "modelled_product_dmt",
            "FINAL_STOCKPILE_WMT", "INVENTORY_BALANCE_WMT",
            "LEDGER_ADJUSTMENT_WMT", "RAW_POSITIVE_STOCKPILE_WMT",
            "RAW_STOCKPILE_WMT", "RAW_HEX_STOCKPILE_WMT", "RAW_WMT", "SPATIAL_ADJUSTMENT_WMT",
            "SPATIAL_DEFICIT_FILLED_WMT", "SPATIAL_DEFICIT_WMT",
            "SPATIAL_DONOR_WMT", "SPATIAL_UNRESOLVED_WMT",
            "SPATIALLY_CORRECTED_STOCKPILE_WMT", "SPATIALLY_CORRECTED_WMT",
            "UNATTRIBUTED_MOVEMENT_WMT",
            "lineage_coverage_pct", "prod1_fe_coverage_pct",
            "internal_recon_matched", "internal_blend_recon_fe",
            "latitude", "last_update", "as_of_date", "mass_recovery", "moisture",
        ], "amt")
        expected = {
                "feed_dmt", "feed_wmt", "grade_si", "MGO_OPF_WTAVG",
                "MODELLED_FEED_LOI_TOTAL", "MODELLED_GRADE_BLOCK_FE",
                "MODELLED_PROD1_FE", "MODELLED_PROD1_FINES_FE",
                "MODELLED_PROD1_LUMP_SIO2", "MODELLED_PROD2_AL2O3",
                "prod1_lump_as", "prod1_lump_loi_total",
        }
        expected.update(
            f"MODELLED_{name.upper()}"
            for name in AMT_MODELLED_ADDITIVE_FIELDS
            if name not in {"feed_wmt", "feed_dmt"}
        )
        self.assertEqual(sorted(expected, key=str.lower), fields)

    def test_aps_source_headers_are_not_treated_as_canonical_outputs(self):
        fields = standardize_available_mapping_fields(
            ["modelled_rom_fe", "modelled_product_fe", "adjusted_product_fe"],
            "aps",
        )
        self.assertEqual(
            ["modelled_product_fe", "modelled_rom_fe"], fields
        )

    def test_legacy_source_wmt_definition_and_mapping_migrate_to_rom_wmt(self):
        definitions = [
            {"name": "source_wmt", "kind": "additive"},
            {"name": "modelled_rom_wmt", "kind": "additive"},
            {
                "name": "custom_grade", "kind": "weighted_average",
                "weight_field": "source_wmt",
            },
        ]
        mappings = normalize_field_mappings([{
            "source_family": "inventory",
            "target_field": "source_wmt",
            "source_field": "BALANCEWMT",
        }])
        self.assertEqual("modelled_rom_wmt", mappings[0]["target_field"])
        self.assertEqual("feed_wmt", mappings[0]["source_field"])
        values = apply_field_mappings(
            {"BALANCEWMT": 123.0}, definitions, mappings, "inventory"
        )
        self.assertEqual(123.0, values["modelled_rom_wmt"])
        self.assertEqual(123.0, values["source_wmt"])

    def test_adjusted_streams_cannot_be_source_mapped(self):
        mappings = normalize_field_mappings([{
            "source_family": "amt",
            "target_field": "adjusted_product_fe",
            "source_field": "raw_adjusted_fe",
        }])
        self.assertEqual([], mappings)
        values = apply_field_mappings(
            {"raw_adjusted_fe": 65.0},
            default_field_definitions(),
            [{
                "source_family": "amt",
                "target_field": "adjusted_product_fe",
                "source_field": "raw_adjusted_fe",
            }],
            "amt",
        )
        self.assertIsNone(values["adjusted_product_fe"])

    def test_weight_field_must_be_an_earlier_additive(self):
        with self.assertRaisesRegex(ValueError, "higher row"):
            validate_field_definitions([
                {
                    "name": "custom_grade",
                    "kind": "weighted_average",
                    "weight_field": "custom_tonnes",
                },
                {"name": "custom_tonnes", "kind": "additive"},
            ])

    def test_field_names_allow_underscores_but_not_spaces(self):
        rows = default_field_definitions()
        rows.append({
            "name": "custom_ore_tonnes",
            "kind": "additive",
            "use_in_optimisation": True,
        })
        validated = validate_field_definitions(rows)
        self.assertIn("custom_ore_tonnes", optimization_field_names(validated))
        rows[-1]["name"] = "custom ore tonnes"
        with self.assertRaisesRegex(ValueError, "letters, numbers, and underscores"):
            validate_field_definitions(rows)

    def test_duplicate_names_are_rejected_before_normalization(self):
        rows = default_field_definitions()
        rows.extend([
            {"name": "custom_mass", "kind": "additive"},
            {"name": "CUSTOM_MASS", "kind": "additive"},
        ])
        with self.assertRaisesRegex(ValueError, "duplicate field 'custom_mass'"):
            validate_field_definitions(rows)

    def test_mapping_keeps_unmapped_canonical_fields_visible(self):
        definitions = [
            {"name": "modelled_rom_wmt", "kind": "additive"},
            {
                "name": "custom_grade",
                "kind": "weighted_average",
                "weight_field": "modelled_rom_wmt",
            },
        ]
        mappings = [{
            "source_family": "inventory",
            "target_field": "modelled_rom_wmt",
            "source_field": "BALANCE",
        }]
        values = apply_field_mappings(
            {"BALANCE": 123.0}, definitions, mappings, "inventory"
        )
        self.assertEqual(123.0, values["modelled_rom_wmt"])
        self.assertIsNone(values["custom_grade"])

    def test_aps_brand_mapping_overrides_unbranded_mapping(self):
        mappings = normalize_field_mappings([
            {
                "source_family": "aps", "brand": "",
                "target_field": "modelled_product_fe",
                "source_field": "Default.Product.Fe",
            },
            {
                "source_family": "aps", "brand": "CCFB",
                "target_field": "modelled_product_fe",
                "source_field": "CCFB.Product.Fe",
            },
        ])
        self.assertEqual(
            "CCFB.Product.Fe",
            mapping_lookup(mappings, "aps", "CCFB")["modelled_product_fe"],
        )
        self.assertEqual(
            "Default.Product.Fe",
            mapping_lookup(mappings, "aps", "CBFL")["modelled_product_fe"],
        )

    def test_declared_additive_type_controls_merge_depletion_and_constraint(self):
        kinds = {"custom_mass": "additive", "custom_quality": "weighted_average"}
        merged = merge_source_properties(
            {"custom_mass": 20.0, "custom_quality": 2.0},
            100.0,
            {"custom_mass": 60.0, "custom_quality": 4.0},
            300.0,
            kinds,
        )
        self.assertEqual(80.0, merged["custom_mass"])
        self.assertEqual(3.5, merged["custom_quality"])
        depleted = scale_additive_source_properties(merged, 0.25, kinds)
        self.assertEqual(20.0, depleted["custom_mass"])
        self.assertEqual(3.5, depleted["custom_quality"])
        fields = constraint_property_fields(merged, 400.0, kinds)
        self.assertEqual(0.2, fields["custom_mass"])
        self.assertEqual(3.5, fields["custom_quality"])

    def test_weighted_average_uses_its_declared_additive_weight(self):
        kinds = {"product_dmt": "additive", "product_fe": "weighted_average"}
        weights = {"product_fe": "product_dmt"}
        merged = merge_source_properties(
            {"product_dmt": 20.0, "product_fe": 60.0},
            100.0,
            {"product_dmt": 80.0, "product_fe": 55.0},
            100.0,
            kinds,
            weights,
        )
        self.assertEqual(100.0, merged["product_dmt"])
        self.assertAlmostEqual(56.0, merged["product_fe"])
        self.assertEqual(
            {"product_dmt", "product_fe"},
            expand_required_property_keys({"product_fe"}, weights),
        )
        streams = weighted_merge_grade_streams(
            {"modelled_product": {"CCFB": {"fe": 60.0}}},
            100.0,
            {"modelled_product": {"CCFB": {"fe": 55.0}}},
            100.0,
            {"product_dmt": 20.0},
            {"product_dmt": 80.0},
            {"modelled_product_fe": "product_dmt"},
        )
        self.assertAlmostEqual(
            56.0, streams["modelled_product"]["CCFB"]["fe"]
        )
        definitions = default_field_definitions()
        definitions.extend([
            {
                "name": "custom_dmt",
                "kind": "additive",
                "use_in_optimisation": False,
            },
            {
                "name": "custom_fe",
                "kind": "weighted_average",
                "weight_field": "custom_dmt",
                "use_in_optimisation": True,
            },
        ])
        validated = validate_field_definitions(definitions)
        self.assertIn("custom_dmt", optimization_field_names(validated))

    def test_partial_product_grade_coverage_preserves_adjustment_ratio(self):
        covered = {
            "modelled_product": {"FB": {"fe": 60.0}},
            "adjusted_product": {"FB": {"fe": 66.0}},
        }
        uncovered = {
            "modelled_product": {"FB": {"fe": None}},
            "adjusted_product": {"FB": {"fe": None}},
        }
        weights = {
            "modelled_product_fe": "modelled_product_dmt",
            "adjusted_product_fe": "modelled_product_dmt",
        }

        merged = weighted_merge_grade_streams(
            covered,
            20.0,
            uncovered,
            80.0,
            {"modelled_product_dmt": 20.0},
            {"modelled_product_dmt": 80.0},
            weights,
        )
        self.assertEqual(60.0, merged["modelled_product"]["FB"]["fe"])
        self.assertEqual(66.0, merged["adjusted_product"]["FB"]["fe"])

        reweighted = reweight_grade_streams_from_properties(
            merged,
            {"modelled_product_fe": 55.0},
        )
        self.assertEqual(55.0, reweighted["modelled_product"]["FB"]["fe"])
        self.assertAlmostEqual(
            60.5, reweighted["adjusted_product"]["FB"]["fe"]
        )

    def test_inventory_streams_prefer_canonical_mapped_values(self):
        factors = {
            "CCFB": {
                "blend": {"fe": {"effective": 1.1}},
                "regression": {"fe": {"effective": 0.9}},
            }
        }
        streams = inventory_grade_streams(
            {
                "grade_fe": 55.0,
                "modelled_rom_fe": 60.0,
                "modelled_product_fe": 62.0,
            },
            ["CCFB"],
            factors,
            "CC OPF01",
        )
        self.assertEqual(60.0, streams["modelled_rom"][UNBRANDED]["fe"])
        self.assertAlmostEqual(66.0, streams["adjusted_rom"]["CCFB"]["fe"])
        self.assertEqual(62.0, streams["modelled_product"]["CCFB"]["fe"])
        self.assertAlmostEqual(55.8, streams["adjusted_product"]["CCFB"]["fe"])


if __name__ == "__main__":
    unittest.main()
