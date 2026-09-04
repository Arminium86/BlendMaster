import unittest

from classes.DataQualityWarnings import format_chunk_quality_warning


class DataQualityWarningTests(unittest.TestCase):
    def test_fixed_chunk_warning_order_and_statuses(self):
        text = format_chunk_quality_warning({
            "lineage_coverage_pct": 97.4,
            "mapped_field_coverage_pct": {"product_wmt": 96.54},
            "product_grade_coverage_pct": {"PROD1 Fe": 92.29},
            "geometry_quarantine_count": 0,
        })
        self.assertEqual(
            text,
            "Lineage: Partial 97.40% | Mapped fields: Partial - product_wmt 96.54% "
            "| Product grades: Partial - PROD1 Fe 92.29% | Fallbacks: None | Geometry: OK",
        )

    def test_missing_not_applicable_fallback_and_geometry(self):
        text = format_chunk_quality_warning(
            {
                "lineage_coverage_pct": None,
                "mapped_field_coverage_pct": {},
                "geometry_quarantine_count": 2,
                "geometry_quarantine_hexes": "A,B",
            },
            fallbacks=["SF Fe: adjusted_product -> adjusted_rom"],
            product_grades_applicable=False,
        )
        self.assertIn("Lineage: Missing", text)
        self.assertIn("Mapped fields: Missing", text)
        self.assertIn("Product grades: Not applicable", text)
        self.assertIn("Fallbacks: SF Fe", text)
        self.assertIn("Geometry: 2 quarantined hex(es) - A,B", text)


if __name__ == "__main__":
    unittest.main()
