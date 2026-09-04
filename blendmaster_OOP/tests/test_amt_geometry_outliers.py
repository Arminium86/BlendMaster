import unittest

import pandas as pd

from GUI.DrawCharts import DrawAMTStockpile


def footprint_rows():
    rows = []
    for row_index in range(3):
        for column_index in range(4):
            rows.append({
                "footprint": "SP1",
                "hex": f"H{row_index}{column_index}",
                "balance": 100.0,
                "lat": -22.42 + row_index * 0.0001,
                "long": 119.78 + column_index * 0.0001,
                "grade_fe": 55.0,
                "grade_si": 4.0,
                "grade_al": 2.0,
                "grade_p": 0.08,
                "grade_mn": 0.1,
                "grade_streams": None,
            })
    rows.append({
        "footprint": "SP1",
        "hex": "OUTLIER",
        "balance": 100.0,
        "lat": -22.39,
        "long": 119.82,
        "grade_fe": 55.0,
        "grade_si": 4.0,
        "grade_al": 2.0,
        "grade_p": 0.08,
        "grade_mn": 0.1,
        "grade_streams": None,
    })
    return rows


class AMTGeometryOutlierTests(unittest.TestCase):
    def chart(self):
        chart = DrawAMTStockpile.__new__(DrawAMTStockpile)
        chart.data = pd.DataFrame(footprint_rows())
        chart.geometry_outliers = {}
        chart.dig_paths = {}
        chart.get_chunk_setting = lambda _footprint, key, default=0.0: (
            650.0 if key == "chunk_size" else default
        )
        chart.get_chunk_plan = lambda _footprint: {
            "chunk_count": 2,
            "chunk_size": 650.0,
        }
        return chart

    def test_remote_point_is_quarantined_from_shared_geometry(self):
        chart = self.chart()

        accepted, outliers, missing = chart.footprint_geometry_rows(
            "SP1", positive_only=True
        )

        self.assertEqual(len(accepted), 12)
        self.assertEqual(outliers["hex"].tolist(), ["OUTLIER"])
        self.assertTrue(missing.empty)
        self.assertEqual(chart.geometry_outliers["SP1"]["outlier_wmt"], 100.0)

    def test_auto_path_excludes_bad_coordinate_but_chunks_keep_its_tonnes(self):
        chart = self.chart()
        reclaim, cut, error = chart.automatic_directions_for_footprint("SP1")
        self.assertEqual(error, "")

        chunks, message = chart.build_chunks_for_footprint(
            "SP1", reclaim["start"], reclaim["end"], cut["start"], cut["end"]
        )

        self.assertAlmostEqual(sum(chunk["balance"] for chunk in chunks), 1300.0)
        self.assertNotIn("OUTLIER", [point[2] for point in chart.dig_paths["SP1"]])
        self.assertIn(
            "OUTLIER",
            [
                hex_id
                for chunk in chunks
                for hex_id in chunk["member_hexes"].split(",")
            ],
        )
        quarantined_chunk = next(
            chunk for chunk in chunks if chunk["geometry_quarantine_count"]
        )
        self.assertEqual(quarantined_chunk["geometry_quarantine_hexes"], "OUTLIER")
        self.assertEqual(quarantined_chunk["geometry_quarantine_wmt"], 100.0)
        self.assertIn("Quarantined 1 invalid coordinate hex", message)

    def test_manual_exclusion_removes_hex_from_axes_path_and_chunks(self):
        chart = self.chart()
        chart.excluded_hexes = {"SP1": {"H00"}}

        reclaim, cut, error = chart.automatic_directions_for_footprint("SP1")
        self.assertEqual(error, "")
        chunks, message = chart.build_chunks_for_footprint(
            "SP1", reclaim["start"], reclaim["end"], cut["start"], cut["end"]
        )

        member_hexes = {
            hex_id
            for chunk in chunks
            for hex_id in chunk["member_hexes"].split(",")
        }
        self.assertNotIn("H00", member_hexes)
        self.assertNotIn("H00", [point[2] for point in chart.dig_paths["SP1"]])
        self.assertAlmostEqual(sum(chunk["balance"] for chunk in chunks), 1200.0)
        self.assertEqual(chunks[0]["excluded_hex_count"], 1)
        self.assertEqual(chunks[0]["excluded_hex_wmt"], 100.0)
        self.assertEqual(chunks[0]["excluded_hexes"], "H00")
        self.assertIn("Excluded 1 user-selected hex", message)

    def test_exclusion_toggle_restores_hex(self):
        chart = self.chart()
        chart.excluded_hexes = {}

        self.assertTrue(chart.toggle_hex_exclusion("SP1", "H00"))
        self.assertIn("H00", chart.excluded_hex_ids("SP1"))
        self.assertFalse(chart.toggle_hex_exclusion("SP1", "H00"))
        self.assertNotIn("H00", chart.excluded_hex_ids("SP1"))


if __name__ == "__main__":
    unittest.main()
