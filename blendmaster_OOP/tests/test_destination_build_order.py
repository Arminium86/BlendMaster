import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from classes.DestinationBuildOrder import build_order, extract_build_order, inventory_areas, write_order_audit


def movement(destination="SP1", start="2026-09-08 06:00", end="2026-09-08 07:00", material="HG", **changes):
    row = {"Source.Type": "Reserve", "Source.FullName": f"Reserves/CC1/PITA/01/396/118/405/{material}12_313",
           "Destination.Type": "Stockpile", "Destination.Name": destination,
           "Time.StartTime": start, "Time.EndTime": end, "Mining.wetTonnes": 100}
    return {**row, **changes}


def fixture():
    return pd.DataFrame([
        movement(), movement("SP2", "2026-09-08 07:00", "2026-09-08 08:00"),
        movement("SP1", "2026-09-08 08:00", "2026-09-08 09:00"),
        movement("CR1", "2026-09-08 09:00", "2026-09-08 10:00", **{"Source.Type": "Flow", "Agent.Name": "PlantAgent", "OriginalSource.Name": "SP1", "Destination.Type": "Crusher"}),
        movement("SP1", "2026-09-08 10:00", "2026-09-08 11:00"),
        movement("SP3", material="BA"),
    ])


class BuildOrderTests(unittest.TestCase):
    def test_turnover_creates_instances_but_interleaving_does_not(self):
        result = build_order(fixture(), {"SP1": "CR1", "SP2": "CR1", "SP3": "CR2"})
        hg = [r for r in result["orders"] if r["material_type"] == "HG"]
        self.assertEqual([(r["destination"], r["build_instance"], r["order_position"]) for r in hg], [("SP1", 1, 1), ("SP2", 1, 2), ("SP1", 2, 3)])
        self.assertEqual(hg[0]["planned_wmt"], 200)
        self.assertEqual(hg[0]["csv_records"], [2, 4])
        self.assertEqual(result["audit"][2]["order_position"], 1)
        self.assertEqual(result["audit"][3]["outcome"], "reclaim")
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_stockpile_instance_is_shared_across_material_types(self):
        frame = fixture()
        frame.loc[2, "Source.FullName"] = movement(material="BA")["Source.FullName"]
        result = build_order(frame, {"SP1": "CR1", "SP2": "CR1", "SP3": "CR2"})
        self.assertEqual(result["audit"][0]["instance_id"], result["audit"][2]["instance_id"])
        self.assertNotEqual(result["audit"][0]["instance_id"], result["audit"][4]["instance_id"])

    def test_reclaim_audit_preserves_actual_route_and_identifies_reclaimed_stockpile(self):
        for source_type, source in (("Stockpile", "Stockpiles/SP1"), ("Flow", "Flow/CR1_Product")):
            with self.subTest(source_type=source_type):
                frame = fixture()
                frame.loc[3, "Source.Type"] = source_type
                frame.loc[3, "Source.FullName"] = source
                result = build_order(frame, {"SP1": "CR1", "SP2": "CR1", "SP3": "CR2"})
                row = result["audit"][3]
                self.assertEqual((row["source"], row["destination"], row["reclaimed_stockpile"]), (source, "CR1", "SP1"))
                self.assertEqual((row["row_type"], row["rom_area"]), ("reclaim", "CR1"))
                self.assertEqual(result["audit"][4]["build_instance"], 2)

    def test_overlap_cannot_invent_turnover(self):
        frame = fixture()
        frame.loc[3, "Time.StartTime"] = "2026-09-08 08:30"
        result = build_order(frame, {"SP1": "CR1", "SP2": "CR1", "SP3": "CR2"})
        self.assertEqual(result["audit"][4]["build_instance"], 1)
        self.assertTrue(any("overlap" in w for w in result["warnings"]))

    def test_missing_mapping_bad_tonnes_and_direct_feed_are_audited(self):
        frame = pd.DataFrame([movement(), movement("SP2", **{"Mining.wetTonnes": -1}), movement("CR1", **{"Destination.Type": "Crusher"})])
        result = build_order(frame, {})
        self.assertEqual(result["orders"], [])
        self.assertEqual(len(result["audit"]), 3)
        self.assertIn("Nearest Crusher", result["audit"][0]["reason"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertEqual([r["row_type"] for r in result["audit"]], ["rom_inbound", "rom_inbound", "other"])

    def test_file_bytes_mapping_and_audit_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Mining.csv"
            fixture().to_csv(path, index=False)
            inventories = {"SP1": {"nearest_crusher": "CR1"}, "SP2": {"nearest_crusher": "CR1"}, "SP3": {"nearest_crusher": "CR2"}}
            first = extract_build_order(path, inventories)
            inventories["SP2"]["nearest_crusher"] = "CR2"
            self.assertNotEqual(first["signature"], extract_build_order(path, inventories)["signature"])
            database = str(Path(temp) / "audit.db")
            write_order_audit(first, database)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("select count(*) from destination_build_order_audit").fetchone()[0], 6)
                self.assertEqual(connection.execute("select sum(planned_wmt) from destination_build_order").fetchone()[0], 500)

    def test_conflicting_aliases_and_missing_columns(self):
        self.assertEqual(inventory_areas({"SP1": {"nearest_crusher": "CR1"}, "Stockpiles/SP1": {"nearest_crusher": "CR2"}}), {"SP1": ""})
        with self.assertRaisesRegex(ValueError, "missing"):
            build_order(pd.DataFrame(), {})


if __name__ == "__main__":
    unittest.main()
