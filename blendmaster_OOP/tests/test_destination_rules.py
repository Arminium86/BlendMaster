import io
import json
import sqlite3
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from classes.DestinationRules import DestinationRuleEngine, LEVELS, address, source_key
from classes.ExpitDataHandler import ExpitDataHandler
from classes.HaulCycleDataHandler import HaulCycleDataHandler
from classes.MaterialDestinationPlan import MaterialDestinationPlan
from classes.PrimaryDestinationAllocator import allocate_final_plan, write_allocation_audit
from tests.test_dual_schedule_ingestion import reserve_row
from tests.test_primary_destination_allocator import context, payload, transactions


SOURCE = "Reserves/CC1/PIT_A/01/100/20/105/HG99_7"


def block(material="HG01", stage="1", bench="100", blast="21", flitch="105", pit="PIT_A", mine="CC1"):
    return f"Reserves/{mine}/{pit}/{stage}/{bench}/{blast}/{flitch}/{material}_1"


def guidance(*entries):
    result = {"source_destinations": {}}
    for source, destination, tonnes, *extra in entries:
        row = dict(source=source, destination=f"Stockpiles/{destination}", two_wp_tonnes=tonnes,
                   guidance_datetime="2026-09-01T06:00:00", row_order=0, ratio=1.0)
        row.update(extra[0] if extra else {})
        result["source_destinations"].setdefault(source_key(source), []).append(row)
    return result


def route(destination, minutes, origin="PRIMARY"):
    return dict(destination=f"Stockpiles/{destination}", cycle_time_minutes=minutes,
                source_node=f"Stockpiles/{origin}:In", destination_node=f"Stockpiles/{destination}:In")


class DestinationRuleTests(unittest.TestCase):
    def test_each_confirmed_level_and_blast_is_deliberately_ignored(self):
        candidates = [block(), block(flitch="106"), block(bench="110"), block(stage="2"), block(material="BA01")]
        for level in range(5):
            with self.subTest(level=level + 1):
                rules = DestinationRuleEngine(guidance(*[(s, f"SP{i}", 100) for i, s in enumerate(candidates[level:], level)]))
                result = rules.resolve(SOURCE)
                self.assertEqual(result["fallback_1_destination"], f"Stockpiles/SP{level}")
                self.assertEqual(result["fallback_1_rule"], LEVELS[level][0])
                self.assertEqual(result["primary_destination"], "")
        self.assertEqual(address(SOURCE)[1][:5], ("PIT_A", "1", "100", "20", "105"))

    def test_same_material_pit_precedes_other_material_at_finest_level(self):
        rules = DestinationRuleEngine(guidance((block(stage="2"), "SAME", 1), (block(material="BA01"), "OTHER", 10000)))
        self.assertEqual(rules.resolve(SOURCE)["fallback_1_destination"], "Stockpiles/SAME")

    def test_no_cross_mine_cross_pit_waste_or_invented_address_fallback(self):
        history = guidance((block(pit="PIT_B"), "OTHER_PIT", 100), (block(mine="CC2"), "OTHER_MINE", 100),
                           (block(material="WS01"), "WASTE", 100), (block(material="BA01"), "FLAGGED", 100, {"ore_type": "Waste"}),
                           ("Reserves/CC1/PIT_A/BROKEN", "MALFORMED", 100))
        history.update(pit_destinations={"PIT_A": {"destination": "Stockpiles/OLD"}}, last_destination={"destination": "Stockpiles/LAST"})
        result = DestinationRuleEngine(history).resolve(SOURCE)
        self.assertEqual(result["resolution"], "unresolved")
        self.assertEqual(result["candidates"], [])

    def test_entire_history_ranks_total_wmt_and_name_with_trace(self):
        history = guidance((block(), "B", 60), (block(), "B", 60), (block(material="HG02"), "A", 120),
                           (block(material="HG03"), "C", 119))
        history["source_destinations"][source_key(block())][0]["guidance_datetime"] = "2020-01-01"
        result = DestinationRuleEngine(history).resolve(SOURCE, "2030-01-01")
        self.assertEqual(result["fallback_1_destination"], "Stockpiles/A")
        self.assertEqual(result["alternate_destinations"], ["Stockpiles/B", "Stockpiles/C"])
        self.assertEqual(result["candidates"][1]["history_wmt"], 120)
        self.assertEqual(result["candidates"][1]["history_rows"], 2)

    def test_exact_parent_calendar_choice_and_distinct_roles(self):
        history = guidance((SOURCE, "PRIMARY", 50, {"guidance_datetime": "2026-09-09T06:00:00"}),
                           (SOURCE, "FAR", 500, {"guidance_datetime": "2026-09-01T06:00:00"}),
                           (block(), "FALLBACK", 1000))
        rules = DestinationRuleEngine(history, areas={k: "CR1" for k in ["PRIMARY", "FALLBACK", "NEAR", "FAR"]},
                                      haul_routes={"PRIMARY": [route("PRIMARY", 1), route("FALLBACK", 2), route("NEAR", 4)]})
        result = rules.resolve(SOURCE.replace("_7", "_9"), "09/09/2026 12:00")
        self.assertEqual([result[k] for k in ("primary_destination", "fallback_1_destination", "fallback_2_destination")],
                         ["Stockpiles/PRIMARY", "Stockpiles/FALLBACK", "Stockpiles/NEAR"])
        self.assertEqual(result["alternate_destinations"], ["Stockpiles/FALLBACK", "Stockpiles/NEAR"])
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][1]["cycle_time_minutes"], 4)
        result["candidates"][0]["destination"] = "MUTATED"
        self.assertEqual(rules.resolve(SOURCE, "2026-09-09T13:00")["candidates"][0]["destination"], "Stockpiles/FALLBACK")
        self.assertEqual(len(rules.cache), 1)

    def test_nearby_requires_origin_route_same_rom_and_positive_finite_cycle(self):
        routes = [route("OTHER_ROM", 1), route("UNMAPPED", 1), route("A", 8), route("B", 8),
                  route("BAD", float("inf")), route("ZERO", 0), route("NEG", -1)]
        areas = {k: "CR1" for k in ["ORIGIN", "A", "B", "BAD", "ZERO", "NEG"]}; areas["OTHER_ROM"] = "CR2"
        rules = DestinationRuleEngine(areas=areas, haul_routes={"ORIGIN": routes})
        result = rules.resolve(SOURCE, anchor_destination="ORIGIN")
        self.assertEqual(result["fallback_2_destination"], "Stockpiles/A")
        self.assertEqual(result["resolution"], "nearby_fallback")
        self.assertEqual(result["alternate_destinations"], ["Stockpiles/B"])
        self.assertEqual(rules.resolve(block(), anchor_destination="UNKNOWN")["fallback_2_destination"], "")
        self.assertEqual(rules.resolve(SOURCE)["fallback_2_destination"], "")

    def test_resolved_guidance_rom_area_precedes_replaced_24hr_destination(self):
        rules = DestinationRuleEngine(guidance((SOURCE, "PRIMARY", 100)),
                                      areas={"PRIMARY": "CR1", "NEAR": "CR1", "OLD_ROM": "CR2"},
                                      haul_routes={"PRIMARY": [route("OLD_ROM", 1), route("NEAR", 5)]})
        self.assertEqual(rules.resolve(SOURCE, rom_area="CR2")["fallback_2_destination"], "Stockpiles/NEAR")

    def test_haul_reader_uses_inbound_variants_and_never_reclaim_cycles(self):
        origin = "Stockpiles/ORIGIN:In"
        rows = [(origin, "Stockpiles/A", 1), (origin, "Stockpiles/A:In", 8), (origin, "Stockpiles/A:In", 7),
                (origin, "Stockpiles/A:Out", 0.5), (origin, "Crushers/CR1", 2),
                ("Stockpiles/ORIGIN:Out", "Stockpiles/A:In", 1), (origin, "Stockpiles/B", float("inf")),
                (origin, "Stockpiles/C", -2), (SOURCE, "Stockpiles/A:In", 1), (origin, origin, 1),
                ("Stockpiles/ORIGIN", "Stockpiles/A:In", 0.5)]
        stream = io.StringIO(pd.DataFrame(rows, columns=["Source Node", "Dest Node", "Total Cycle Time (min)"]).to_csv(index=False))
        result = HaulCycleDataHandler.build_destination_routes(stream)
        self.assertEqual(list(result), ["ORIGIN"])
        self.assertEqual(len(result["ORIGIN"]), 1)
        self.assertEqual(result["ORIGIN"][0]["cycle_time_minutes"], 7)

    def test_ingestion_preserves_roles_through_aggregation_and_payloads(self):
        history = guidance((block(), "F1", 100))
        row = reserve_row(SOURCE, "PIT_A", "Stockpiles/ORIGINAL", 150, "09/09/2026 06:00", "09/09/2026 07:00")
        with TemporaryDirectory() as temp:
            path = Path(temp)/"24hr.csv"; pd.DataFrame([row]).to_csv(path, index=False)
            handler = ExpitDataHandler(path, destination_guidance=history,
                                       destination_rule_context=dict(areas={"ORIGINAL": "CR1", "F1": "CR1", "F2": "CR1"},
                                                                     haul_routes={"F1": [route("F2", 3, "F1")]}))
            # The constructor groups APS rows before building payloads.
            payloads = handler.process_transactions()
        self.assertAlmostEqual(payloads["payload"].sum(), 150)
        for row in payloads.to_dict("records"):
            self.assertEqual(row["primary_destination"], "")
            self.assertEqual(row["fallback_1_destination"], "Stockpiles/F1")
            self.assertEqual(row["fallback_2_destination"], "Stockpiles/F2")
            self.assertEqual(row["alternate_destination_1"], "Stockpiles/F2")
            self.assertEqual(json.loads(row["destination_rule_trace"])["resolution"], "spatial_fallback")
        report = MaterialDestinationPlan.build(payloads, pd.DataFrame(), "manual")
        self.assertEqual(set(report["planned_2wp_destination"]), {""})

    def test_final_primary_recomputes_fallbacks_without_consuming_them(self):
        ctx = context()
        ctx["destination_rules"] = dict(guidance=guidance((block(), "SP1", 200), (block(material="HG02"), "SP2", 100)),
                                        areas={"SP1": "CR1", "SP2": "CR1", "NEAR": "CR1"},
                                        haul_routes={k: [route("NEAR", 3, k)] for k in ["SP1", "SP2"]})
        rows = [payload(1, 150), payload(2, 30)]
        for row in rows:
            row["source"] = SOURCE
        original = deepcopy(ctx)
        result = allocate_final_plan(transactions(rows), pd.DataFrame(), ctx)
        self.assertEqual(ctx, original)
        first, second = result["assignments"]
        self.assertEqual(json.loads(first["destination_rule_trace"])["resolution"], "primary_build_order")
        self.assertEqual((first["primary_destination"], first["fallback_1_destination"], first["fallback_2_destination"]),
                         ("Stockpiles/SP1", "Stockpiles/SP2", "Stockpiles/NEAR"))
        self.assertEqual((second["primary_destination"], second["fallback_1_destination"]), ("Stockpiles/SP2", "Stockpiles/SP1"))
        self.assertEqual(result["run"]["assigned_wmt"], 180)
        self.assertEqual(result["run"]["overrun_wmt"], 50)
        with TemporaryDirectory() as temp:
            path = str(Path(temp)/"plan.db")
            write_allocation_audit(result, path)
            with closing(sqlite3.connect(path)) as connection:
                stored = connection.execute("select fallback_1_destination, fallback_2_destination, destination_rule_trace from destination_primary_assignments order by payload_id").fetchall()
            self.assertEqual(stored[0][:2], ("Stockpiles/SP2", "Stockpiles/NEAR"))
            self.assertEqual(json.loads(stored[0][2])["candidates"][1]["cycle_time_minutes"], 3)

    def test_fallback_candidates_do_not_bypass_blank_primary_capacity(self):
        ctx = context(capacity=None)
        ctx["destination_rules"] = dict(guidance=guidance((block(), "SP2", 100)))
        row = payload(1, 100); row["source"] = SOURCE
        result = allocate_final_plan(transactions([row]), pd.DataFrame(), ctx)
        assigned = result["assignments"][0]
        self.assertEqual(assigned["primary_destination"], "")
        self.assertEqual(assigned["fallback_1_destination"], "Stockpiles/SP2")
        self.assertEqual(assigned["unresolved_wmt"], 100)
        self.assertEqual(result["ledger"], [])


if __name__ == "__main__":
    unittest.main()
