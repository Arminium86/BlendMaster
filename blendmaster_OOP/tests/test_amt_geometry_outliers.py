from copy import deepcopy
import pickle
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


def path_crossings(path):
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    return sum(
        side(a, b, c) * side(a, b, d) < -1e-28
        and side(c, d, a) * side(c, d, b) < -1e-28
        for index, (a, b) in enumerate(zip(path, path[1:]))
        for c, d in zip(path[index + 2:], path[index + 3:])
    )


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

    def test_restored_path_matches_generated_path_and_excludes_invalid_coordinates(self):
        original = self.chart()
        missing = footprint_rows()[0].copy()
        missing.update(hex="MISSING", lat=None, long=None)
        original.data = pd.DataFrame([*footprint_rows(), missing])
        reclaim, cut, error = original.automatic_directions_for_footprint("SP1")
        self.assertEqual(error, "")
        chunks, _ = original.build_chunks_for_footprint(
            "SP1", reclaim["start"], reclaim["end"], cut["start"], cut["end"]
        )
        restored = self.chart()
        restored.data = original.data.sample(frac=1, random_state=42).reset_index(drop=True)
        restored.selected_points = pickle.loads(pickle.dumps(chunks))

        self.assertEqual(
            restored.dig_path_from_selected_points("SP1"), original.dig_paths["SP1"]
        )
        self.assertAlmostEqual(sum(row["balance"] for row in restored.selected_points), 1400.0)

    def test_refresh_preserves_saved_member_order_instead_of_database_order(self):
        original = self.chart()
        reclaim, cut, error = original.automatic_directions_for_footprint("SP1")
        self.assertEqual(error, "")
        chunks, _ = original.build_chunks_for_footprint(
            "SP1", reclaim["start"], reclaim["end"], cut["start"], cut["end"]
        )
        restored = self.chart()
        restored.data = original.data.sample(frac=1, random_state=42).reset_index(drop=True)
        saved = pickle.loads(pickle.dumps(chunks))
        saved[0]["member_hexes"] = saved[0]["member_hexes"].split(",")
        rebuilt, count = restored.rebuild_saved_chunk_records(saved)

        self.assertEqual(count, len(chunks))
        self.assertEqual(
            [row["member_hexes"] for row in rebuilt], [row["member_hexes"] for row in chunks]
        )
        self.assertEqual([row["balance"] for row in rebuilt], [row["balance"] for row in chunks])
        restored.selected_points = rebuilt
        self.assertEqual(
            restored.dig_path_from_selected_points("SP1"), original.dig_paths["SP1"]
        )

    def test_plot_uses_loaded_sequence_instead_of_previous_cached_path(self):
        chart = self.chart()
        chart.reclaim_directions = {}
        chart.cut_directions = {}
        chart.dig_paths["SP1"] = [(0.0, 0.0, "STALE")]
        chart.selected_points = [{
            "footprint": "SP1", "sequence": 1, "member_hexes": "H20,H10,H00,OUTLIER",
        }]

        figure = chart.generate_scatter_plot("SP1", None, None)

        path = next(trace for trace in figure.data if trace.name == "Dig Path")
        self.assertEqual(list(path.x), [119.78, 119.78, 119.78])
        self.assertEqual(list(path.y), [-22.4198, -22.4199, -22.42])

    def test_legacy_crossed_path_is_recovered_without_changing_chunks(self):
        chart = self.chart()
        chart.selected_points = [
            {"footprint": "SP1", "sequence": 1, "balance": 600,
             "member_hexes": "H00,H21,H01,H20,H10,H11"},
            {"footprint": "SP1", "sequence": 2, "balance": 700,
             "member_hexes": "H02,H23,H03,H22,H12,H13,OUTLIER"},
        ]
        original_chunks = deepcopy(chart.selected_points)
        lookup = {row["hex"]: (row["long"], row["lat"], row["hex"])
                  for row in footprint_rows() if row["hex"] != "OUTLIER"}
        old_path = [lookup[hex_id] for chunk in original_chunks
                    for hex_id in chunk["member_hexes"].split(",") if hex_id in lookup]
        self.assertGreater(path_crossings(old_path), 0)

        recovered = chart.dig_path_from_selected_points("SP1")

        self.assertEqual(path_crossings(recovered), 0)
        self.assertCountEqual(recovered, old_path)
        self.assertEqual([point[2] for point in recovered[:6]], chart.selected_points[0]["dig_path_hexes"])
        for before, after in zip(original_chunks, chart.selected_points):
            self.assertEqual(before, {key: value for key, value in after.items() if key != "dig_path_hexes"})
            self.assertEqual(set(after["dig_path_hexes"]), set(before["member_hexes"].split(",")) - {"OUTLIER"})
        self.assertEqual(recovered, chart.dig_path_from_selected_points("SP1"))

    def test_explicit_saved_path_survives_member_reordering_and_refresh(self):
        chart = self.chart()
        reclaim, cut, _ = chart.automatic_directions_for_footprint("SP1")
        chunks, _ = chart.build_chunks_for_footprint(
            "SP1", reclaim["start"], reclaim["end"], cut["start"], cut["end"]
        )
        expected = chart.dig_paths["SP1"]
        saved = pickle.loads(pickle.dumps(chunks))
        for chunk in saved:
            chunk["member_hexes"] = ",".join(sorted(chunk["member_hexes"].split(",")))
        chart.data = chart.data.sample(frac=1, random_state=42).reset_index(drop=True)
        chart.selected_points, _ = chart.rebuild_saved_chunk_records(saved)

        self.assertEqual(expected, chart.dig_path_from_selected_points("SP1"))
        self.assertEqual([c["dig_path_hexes"] for c in chunks],
                         [c["dig_path_hexes"] for c in chart.selected_points])

    def test_uncrossed_legacy_path_keeps_its_saved_order(self):
        chart = self.chart()
        chart.selected_points = [{
            "footprint": "SP1", "sequence": 1,
            "member_hexes": "H20,H10,H00,H01,H11,H21",
        }]
        path = chart.dig_path_from_selected_points("SP1")
        self.assertEqual([p[2] for p in path], ["H20", "H10", "H00", "H01", "H11", "H21"])

    def test_explicit_custom_path_is_not_reconstructed(self):
        chart = self.chart()
        explicit = ["H00", "H21", "H01", "H20"]
        chart.selected_points = [{
            "footprint": "SP1", "sequence": 1,
            "member_hexes": ",".join(sorted(explicit)), "dig_path_hexes": explicit,
        }]
        self.assertEqual([p[2] for p in chart.dig_path_from_selected_points("SP1")], explicit)

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
