import unittest

from classes.BlendPlanPDF import BlendPlanPDF


class BlendPlanPDFTests(unittest.TestCase):
    def test_wide_report_is_split_into_readable_column_bands(self):
        columns = ["steady_state_number", "source"] + [
            f"property_{index}" for index in range(12)
        ]
        bands = BlendPlanPDF._column_bands(
            columns,
            {column: 180 for column in columns},
            available_width=600,
        )

        self.assertGreater(len(bands), 1)
        self.assertTrue(all("steady_state_number" in band for band in bands))
        self.assertTrue(all("source" in band for band in bands))
        self.assertEqual(
            {column for band in bands for column in band},
            set(columns),
        )

    def test_value_formatting_uses_report_mass_and_grade_precision(self):
        self.assertEqual(
            BlendPlanPDF._format_value("source_actual_tonnes", 1234.56),
            "1,235",
        )
        self.assertEqual(
            BlendPlanPDF._format_value("source_grade_fe", 58.126),
            "58.1260",
        )

    def test_blend_detail_sources_are_split_without_splitting_quantities(self):
        value = (
            "SP1 @ 60.00% (12,000 t, 1,000 t/h); "
            "SP2 @ 40.00% (8,000 t, 667 t/h)"
        )

        self.assertEqual(
            [
                "SP1 @ 60.00% (12,000 t, 1,000 t/h)",
                "SP2 @ 40.00% (8,000 t, 667 t/h)",
            ],
            BlendPlanPDF._detail_source_lines(value),
        )

    def test_duration_uses_explicit_value_then_datetime_fallback(self):
        self.assertEqual(
            3.5,
            BlendPlanPDF._duration_hours({
                "Steady State Duration (hrs)": 3.5,
                "Start Datetime": "2026-08-18 06:00",
                "End Datetime": "2026-08-18 08:00",
            }),
        )
        self.assertEqual(
            2.0,
            BlendPlanPDF._duration_hours({
                "Start Datetime": "2026-08-18 06:00",
                "End Datetime": "2026-08-18 08:00",
            }),
        )


if __name__ == "__main__":
    unittest.main()
