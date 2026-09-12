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
    def test_repeated_guidance_dates_reuse_parsing_without_changing_timezone(self):
        from classes.DestinationRules import timestamp, _text_timestamp
        _text_timestamp.cache_clear()
        self.assertEqual(timestamp('18/08/2026 07:30'), timestamp('2026-08-18T07:30:00+08:00'))
        before = _text_timestamp.cache_info().hits
        timestamp('18/08/2026 07:30')
        self.assertEqual(_text_timestamp.cache_info().hits, before + 1)
        self.assertIsNone(timestamp('invalid'))

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

    def test_no_cross_mine_waste_or_invented_address_fallback(self):
        history = guidance((block(mine="CC2"), "OTHER_MINE", 100),
                           (block(material="WS01"), "WASTE", 100), (block(material="BA01"), "FLAGGED", 100, {"ore_type": "Waste"}),
                           ("Reserves/CC1/PIT_A/BROKEN", "MALFORMED", 100))
        history.update(pit_destinations={"PIT_A": {"destination": "Stockpiles/OLD"}}, last_destination={"destination": "Stockpiles/LAST"})
        result = DestinationRuleEngine(history).resolve(SOURCE)
        self.assertEqual(result["resolution"], "unresolved")
        self.assertEqual(result["candidates"], [])

    def test_missing_pit_stage_matches_pit_area_and_material_by_total_wmt(self):
        source = block(pit="YOU80", material="SO69", mine="CC2")
        history = guidance((block(pit="YOU02", material="SO01", mine="CC2"), "A", 60),
                           (block(pit="YOU02", material="SO02", mine="CC2"), "A", 60),
                           (block(pit="YOU13", material="SO03", mine="CC2"), "B", 119),
                           (block(pit="YOU13", material="HG01", mine="CC2"), "OTHER_MATERIAL", 10000),
                           (block(pit="YOU130", material="SO01", mine="CC1"), "OTHER_MINE", 10000),
                           (block(pit="YOUWEST02", material="SO01", mine="CC2"), "OTHER_PIT", 10000))
        result = DestinationRuleEngine(history).resolve(source)
        self.assertEqual(result["selected_destination"], "Stockpiles/A")
        self.assertEqual(result["fallback_1_rule"], "Pit area + material")
        self.assertEqual(result["candidates"][0]["level"], 6)
        self.assertEqual(result["candidates"][0]["history_wmt"], 120)
        self.assertEqual(result["candidates"][0]["history_rows"], 2)
        self.assertEqual(result["alternate_destinations"], ["Stockpiles/B"])
        self.assertEqual(result["last_resort_destination"], "")

    def test_specific_pit_history_precedes_broader_pit_area(self):
        source = block(pit="YOU80", material="SO69")
        history = guidance((block(pit="YOU80", material="BA01"), "SPECIFIC", 1),
                           (block(pit="YOU02", material="SO01"), "AREA", 10000))
        result = DestinationRuleEngine(history).resolve(source)
        self.assertEqual(result["fallback_1_destination"], "Stockpiles/SPECIFIC")
        self.assertEqual(result["fallback_1_rule"], LEVELS[4][0])

    def test_last_resort_uses_latest_dated_movement_within_same_mine(self):
        history = guidance((block(pit="OTHER01"), "HEAVY", 10000, {"guidance_end_datetime": "2026-09-03T07:00:00"}),
                           (block(pit="OTHER02"), "LATEST", 1, {"guidance_end_datetime": "2026-09-04T07:00:00"}),
                           (block(pit="OTHER03", mine="CC2"), "OTHER_MINE", 100, {"guidance_end_datetime": "2026-09-05T07:00:00"}))
        result = DestinationRuleEngine(history).resolve(SOURCE, "2026-09-01")
        self.assertEqual(result["resolution"], "last_destination_fallback")
        self.assertEqual(result["selected_destination"], "Stockpiles/LATEST")
        self.assertEqual(result["last_resort_destination"], "Stockpiles/LATEST")
        self.assertEqual(result["primary_destination"], "")
        self.assertEqual(result["fallback_1_destination"], "")
        self.assertEqual(result["fallback_2_destination"], "")
        self.assertEqual(result["alternate_destinations"], [])
        evidence = result["candidates"][0]
        self.assertEqual(evidence["mine"], "CC1")
        self.assertEqual(evidence["evidence_source"], block(pit="OTHER02"))
        self.assertEqual(evidence["latest_use_datetime"], "2026-09-04T07:00:00+08:00")

    def test_last_resort_requires_eligible_positive_dated_history_and_valid_mine(self):
        source = block(pit="UNSCHEDULED80")
        history = guidance((block(), "VALID", 1, {"guidance_datetime": "2026-08-31"}),
                           (block(), "UNDATED", 100, {"guidance_datetime": None}),
                           (block(), "ZERO", 0), (block(), "NAN", float("nan")),
                           (block(), "UNMAPPED", 100),
                           (block(material="WS01"), "WASTE", 100),
                           (block(), "FLAGGED", 100, {"ore_type": "Waste"}),
                           (block(), "ROUTE_WASTE", 100, {"route_only_waste": True}))
        areas = {name: "CR1" for name in ["VALID", "UNDATED", "ZERO", "NAN", "WASTE", "FLAGGED", "ROUTE_WASTE"]}
        rules = DestinationRuleEngine(history, areas=areas)
        result = rules.resolve(source)
        self.assertEqual(result["selected_destination"], "Stockpiles/VALID")
        self.assertEqual(result["candidates"][0]["latest_use_datetime"], "2026-08-31T00:00:00+08:00")
        for invalid in ("Reserves/CC1/BROKEN", "UNSCHEDULED80/1/100/21/105/HG01", block(pit="UNSCHEDULED80", material="WS01")):
            with self.subTest(source=invalid):
                self.assertEqual(rules.resolve(invalid)["selected_destination"], "")
        self.assertEqual(rules.resolve(source, route_only_waste=True)["selected_destination"], "")

    def test_last_resort_ties_use_start_time_then_file_order(self):
        history = guidance((block(pit="OTHER01"), "A", 100, {"guidance_datetime": "2026-09-01T07:00:00", "row_order": 99}),
                           (block(pit="OTHER02"), "B", 100, {"guidance_datetime": "2026-09-01T08:00:00", "row_order": 1}),
                           (block(pit="OTHER03"), "C", 1, {"guidance_datetime": "2026-09-01T08:00:00", "row_order": 2}))
        for rows in history["source_destinations"].values():
            rows[0]["guidance_end_datetime"] = "2026-09-01T09:00:00"
        result = DestinationRuleEngine(history).resolve(SOURCE)
        self.assertEqual(result["selected_destination"], "Stockpiles/C")

    def test_last_resort_does_not_override_nearby_destination(self):
        rules = DestinationRuleEngine(guidance((block(pit="OTHER01"), "LATEST", 100)),
                                      areas={name: "CR1" for name in ["ORIGIN", "NEAR", "LATEST"]},
                                      haul_routes={"ORIGIN": [route("NEAR", 3, "ORIGIN")]})
        result = rules.resolve(SOURCE, anchor_destination="ORIGIN")
        self.assertEqual(result["selected_destination"], "Stockpiles/NEAR")
        self.assertEqual(result["resolution"], "nearby_fallback")
        self.assertEqual(result["last_resort_destination"], "")

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

    def test_pit_area_and_last_resort_survive_ingestion_without_inventing_exact_guidance(self):
        for pit, resolution in (("YOU80", "spatial_fallback"), ("OTHER80", "last_destination_fallback")):
            with self.subTest(pit=pit):
                source = block(pit=pit, material="SO69", mine="CC2")
                history = guidance((block(pit="YOU02", material="SO01", mine="CC2"), "BUILD", 100))
                row = reserve_row(source, pit, "Stockpiles/CC2_ROM", 150, "09/09/2026 06:00", "09/09/2026 07:00")
                with TemporaryDirectory() as temp:
                    path = Path(temp)/"24hr.csv"
                    pd.DataFrame([row]).to_csv(path, index=False)
                    handler = ExpitDataHandler(path, destination_guidance=history,
                                               destination_rule_context=dict(areas={"BUILD": "CR1"}))
                    payloads = handler.process_transactions()
                self.assertAlmostEqual(payloads["payload"].sum(), 150)
                self.assertEqual(set(payloads["destination"]), {"Stockpiles/BUILD"})
                self.assertEqual(set(payloads["two_wp_destination_resolution"]), {resolution})
                for record in payloads.to_dict("records"):
                    decision = json.loads(record["destination_rule_trace"])
                    self.assertEqual(decision["resolution"], resolution)
                    self.assertEqual(decision["primary_destination"], "")
                    self.assertEqual(decision["candidates"][0]["evidence_source"], block(pit="YOU02", material="SO01", mine="CC2"))
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

    def test_fallback_candidates_do_not_bypass_unavailable_primary_capacity(self):
        ctx = context(capacity=None)
        for entry in ctx["order"]["orders"]:
            entry.pop("inbound_windows")
        ctx["destination_rules"] = dict(guidance=guidance((block(), "SP2", 100)))
        row = payload(1, 100); row["source"] = SOURCE
        result = allocate_final_plan(transactions([row]), pd.DataFrame(), ctx)
        assigned = result["assignments"][0]
        self.assertEqual(assigned["primary_destination"], "")
        self.assertEqual(assigned["fallback_1_destination"], "Stockpiles/SP2")
        self.assertEqual(assigned["unresolved_wmt"], 100)
        self.assertEqual(result["ledger"], [])

    def test_last_resort_cannot_bypass_unavailable_primary_capacity(self):
        ctx = context(capacity=None)
        for entry in ctx["order"]["orders"]:
            entry.pop("inbound_windows")
        ctx["destination_rules"] = dict(guidance=guidance((block(pit="OTHER01"), "SP2", 100)))
        row = payload(1, 100); row["source"] = SOURCE
        result = allocate_final_plan(transactions([row]), pd.DataFrame(), ctx)
        assigned = result["assignments"][0]
        decision = json.loads(assigned["destination_rule_trace"])
        self.assertEqual(decision["last_resort_destination"], "Stockpiles/SP2")
        self.assertEqual(assigned["primary_destination"], "")
        self.assertEqual(assigned["unresolved_wmt"], 100)
        self.assertEqual(result["ledger"], [])


if __name__ == "__main__":
    unittest.main()
