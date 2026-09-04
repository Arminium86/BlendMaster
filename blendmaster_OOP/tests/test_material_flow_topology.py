import copy
import io
import json
import pickle
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from classes.CaseModeller import CaseModeller
from classes.DataLoader import DataLoader
from classes.ManualBlendPlanner import ManualBlendPlanner
from classes.MaterialFlowTopology import model_sources, one_lane_topology
from classes.PeriodManager import PeriodManager


SITE = {"hub": "Chichester", "mine": "CB", "opf": "CB OPF", "crusher": "OPF01"}


def nodes_of(graph, kind):
    return [node for node in graph["nodes"] if node["node_type"] == kind]


def planning_inputs():
    start = datetime(2026, 9, 1, 5)
    periods = PeriodManager()
    periods.periods = {
        f"{key}_{field}": value
        for index, key in enumerate(periods.period_keys())
        for field, value in (
            ("start", start + timedelta(hours=index)),
            ("end", start + timedelta(hours=index + 1)),
            ("duration", 1.0),
        )
    }

    def per_period(value):
        return dict.fromkeys(periods.period_labels(), value)

    calendar = {
        "site_context": dict(SITE),
        "crusher_rate": per_period(100.0),
        "crusher_brand": per_period("CCFB"),
        "reclaim_equipment_max_reclaim_rate": per_period(1000.0),
        "solver_config": {
            "throughput_incentive_per_tonne": 100.0,
            "max_blend_options_per_steady_state": 1,
        },
    }
    stockpiles = {}
    for name, fe in (("SP1", 60.0), ("SP2", 56.0)):
        stockpiles[name] = {
            "name": name.lower(),
            "balance": 1000.0, "reclaim_threshold": 0.0, "amt": False,
            "grade_fe": fe, "grade_si": 4.0, "grade_al": 2.0,
            "grade_p": 0.08, "grade_mn": 0.1,
        }
        calendar[f"stockpiles_{name.lower()}_state"] = per_period("Reclaim")
        calendar[f"stockpiles_{name.lower()}_maximum_quantity"] = per_period(1000.0)
    for analyte in ("fe", "si", "al", "p", "mn"):
        calendar[f"crusher_target_{analyte}_min"] = per_period(58.0 if analyte == "fe" else 0.0)
        calendar[f"crusher_target_{analyte}_max"] = per_period(58.0 if analyte == "fe" else 100.0)
    builds = [
        {"build_id": 1, "build_name": "First", "brand": "CCFB", "target_tonnes": 150.0},
        {"build_id": 2, "build_name": "Second", "brand": "CCFB", "target_tonnes": 1000.0},
    ]
    return start, periods, calendar, stockpiles, builds


def make_case(case_class=CaseModeller, include_site=True):
    _, periods, calendar, stockpiles, builds = planning_inputs()
    loader = DataLoader(stockpiles, calendar, pd.DataFrame(), [], periods)
    piles, blocks, equipment, targets = loader.load_data()
    kwargs = {"site_context": SITE} if include_site else {}
    return case_class(
        piles, blocks, equipment, targets, pd.DataFrame(), periods, 1, [],
        solver_config=calendar["solver_config"], product_build_settings=builds,
        **kwargs,
    )


def make_manual(calendar_override=None, hexes=None, payloads=None):
    start, periods, calendar, stockpiles, builds = planning_inputs()
    calendar.update(calendar_override or {})
    return ManualBlendPlanner(
        [{"Blend ID": "1", "Start Datetime": start, "End Datetime": start + timedelta(hours=3)}],
        [{"Blend ID": "1", "Sources": "SP1, SP2", "Source Ratios": "0.5, 0.5"}],
        stockpiles, hexes or [], payloads, periods.get_periods(), builds, 100.0,
        calendar_inputs=calendar,
    )


class OneLaneTopologyTests(unittest.TestCase):
    def test_sources_reach_product_through_one_tip_conveyor_cos_and_opf(self):
        graph = one_lane_topology(site_context=SITE, sources=[
            {"source": "SP1", "source_type": "stockpile"},
            {"source": "BLOCK_1", "source_type": "grade_block"},
        ])
        nodes = {node["node_id"]: node for node in graph["nodes"]}
        outgoing = {}
        for edge in graph["edges"]:
            self.assertIn(edge["source_node_id"], nodes)
            self.assertIn(edge["target_node_id"], nodes)
            outgoing.setdefault(edge["source_node_id"], []).append(edge["target_node_id"])
            self.assertEqual(edge["latency_hours"], 0.0)
            self.assertIsNone(edge["capacity_wmt"])
        for source in nodes_of(graph, "source"):
            path = []
            current = source["node_id"]
            while current in outgoing:
                self.assertNotIn(current, path)
                path.append(current)
                self.assertEqual(len(outgoing[current]), 1)
                current = outgoing[current][0]
            path.append(current)
            self.assertEqual([nodes[node]["node_type"] for node in path], [
                "source", "tipping_point", "conveyor", "cos", "opf", "product_build_lane",
            ])
        self.assertEqual(len({edge["edge_id"] for edge in graph["edges"]}), len(graph["edges"]))

    def test_source_order_and_repeated_payloads_do_not_change_graph_identity(self):
        sources = [
            {"source": "BLOCK_1", "source_type": "grade_block", "source_id": "GB_2"},
            {"source": "BLOCK_1", "source_type": "grade_block", "source_id": "GB_1"},
            {"source": "BLOCK_2", "source_type": "grade_block", "source_id": "GB_3"},
            {"source": "BLOCK_1", "source_type": "stockpile"},
        ]
        graph = one_lane_topology(site_context=SITE, sources=sources)
        self.assertEqual(graph, one_lane_topology(site_context=SITE, sources=reversed(sources)))
        self.assertEqual(len(nodes_of(graph, "source")), 3)
        block = next(node for node in nodes_of(graph, "source")
                     if node["properties"]["source_type"] == "grade_block" and node["label"] == "BLOCK_1")
        self.assertEqual(block["properties"]["source_ids"], ["GB_1", "GB_2"])

    def test_site_and_separator_names_cannot_collide(self):
        first = one_lane_topology(site_context={"mine": "A:B", "opf": "C"})
        second = one_lane_topology(site_context={"mine": "A", "opf": "B:C"})
        self.assertNotEqual(first["topology_id"], second["topology_id"])
        self.assertTrue(set(node["node_id"] for node in first["nodes"]).isdisjoint(
            node["node_id"] for node in second["nodes"]))

    def test_total_feed_keeps_the_legacy_synthetic_lane(self):
        graph = one_lane_topology(site_context={
            **SITE, "crusher": "Total_Feed_PC", "selected_site_crushers": ["OPF01", "OPF02"],
        })
        self.assertEqual(graph["mode"], "one_lane")
        self.assertEqual(len(nodes_of(graph, "tipping_point")), 1)
        self.assertTrue(graph["properties"]["legacy_synthetic_total_feed"])

    def test_build_order_and_lump_fines_ownership_are_preserved(self):
        settings = [{"byproduct": "fines"}, {"byproduct": "lump"}, {"byproduct": "fines"}]
        graph = one_lane_topology(product_build_settings=settings, byproducts_enabled=True)
        lanes = {node["properties"]["lane"]: node for node in nodes_of(graph, "product_build_lane")}
        self.assertEqual(lanes["lump"]["properties"]["build_indices"], [1])
        self.assertEqual(lanes["fines"]["properties"]["build_indices"], [0, 2])
        opf_id = nodes_of(graph, "opf")[0]["node_id"]
        self.assertTrue(all(node["properties"]["opf_node_ids"] == [opf_id] for node in lanes.values()))
        product = one_lane_topology(product_build_settings=settings)
        self.assertEqual(nodes_of(product, "product_build_lane")[0]["properties"]["build_indices"], [0, 1, 2])

    def test_snapshot_is_serializable_and_detached_from_inputs(self):
        targets = {"preplan": {"brand": "CCFB", "crusher_rate": 0.0, "custom_constraints": [{"maximum": 0.0}]}}
        graph = one_lane_topology(site_context=SITE, crusher_targets=targets)
        self.assertEqual(json.loads(json.dumps(graph)), graph)
        self.assertEqual(pickle.loads(pickle.dumps(graph)), graph)
        tip = nodes_of(graph, "tipping_point")[0]
        tip["properties"]["targets_by_period"]["preplan"]["custom_constraints"][0]["maximum"] = 99
        self.assertEqual(targets["preplan"]["custom_constraints"][0]["maximum"], 0.0)
        self.assertEqual(tip["properties"]["targets_by_period"]["preplan"]["crusher_rate"], 0.0)

    def test_model_sources_keep_amt_and_payload_identity(self):
        graph = one_lane_topology(sources=model_sources(
            [SimpleNamespace(name="SP1", is_AMT=True)],
            [SimpleNamespace(name="GB_000001", source="Reserves/Mine/Pit/LG01_123")],
        ))
        source_nodes = {node["label"]: node for node in nodes_of(graph, "source")}
        self.assertTrue(source_nodes["SP1"]["properties"]["is_amt"])
        self.assertEqual(source_nodes["Reserves/Mine/Pit/LG01_123"]["properties"]["source_ids"], ["GB_000001"])

    def test_unnamed_or_unknown_source_is_rejected(self):
        for source in ({"source_type": "stockpile"}, {"source": "SP1", "source_type": "unknown"}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                one_lane_topology(sources=[source])


class TopologyIntegrationTests(unittest.TestCase):
    def test_legacy_case_without_site_context_remains_readable(self):
        case = make_case(include_site=False)
        del case.site_context  # Old object restored without the new attribute.
        graph = case.material_flow_topology
        self.assertEqual(nodes_of(graph, "tipping_point")[0]["label"], "Unspecified tipping point")
        self.assertEqual(len(nodes_of(graph, "source")), 2)

    def test_manual_and_optimized_share_node_and_edge_ids(self):
        optimized = make_case().material_flow_topology
        manual = make_manual().material_flow_topology
        self.assertEqual(optimized["topology_id"], manual["topology_id"])
        self.assertEqual({node["node_id"] for node in optimized["nodes"]}, {node["node_id"] for node in manual["nodes"]})
        self.assertEqual(optimized["edges"], manual["edges"])

    def test_manual_filters_direct_tip_and_retains_amt_chunks(self):
        payloads = pd.DataFrame([
            {"source": "GB1", "direct_tip_id": "p1", "direct_tip_eligible": True},
            {"source": "GB2", "direct_tip_id": "p2", "direct_tip_eligible": False},
        ])
        planner = make_manual(hexes=[
            {"footprint": "SP1", "hex": "CHUNK1", "sequence": 1, "balance": 500},
            {"footprint": "SP1", "hex": "CHUNK2", "sequence": 2, "balance": 500},
        ], payloads=payloads)
        nodes = {node["label"]: node for node in nodes_of(planner.material_flow_topology, "source")}
        self.assertEqual(set(nodes), {"SP1", "SP2", "GB1"})
        self.assertEqual(nodes["SP1"]["properties"]["source_ids"], ["CHUNK1", "CHUNK2"])
        planner.payload_transactions = payloads.drop(columns="direct_tip_eligible")
        self.assertEqual(len(nodes_of(planner.material_flow_topology, "source")), 2)

    def test_snapshot_does_not_change_solve_quantities_grades_or_build_timing(self):
        case = make_case()
        pristine = make_case()
        original_targets = copy.deepcopy(case.crusher_targets)
        snapshot = case.material_flow_topology
        nodes_of(snapshot, "tipping_point")[0]["properties"]["targets_by_period"]["preplan"]["crusher_rate"] = 1.0
        self.assertEqual(case.crusher_targets, original_targets)
        with redirect_stdout(io.StringIO()):
            case.run()
            pristine.run()
        pd.testing.assert_frame_equal(case.results, pristine.results)
        self.assertEqual(case.product_build_runtime_states, pristine.product_build_runtime_states)
        self.assertAlmostEqual(case.results["source_actual_tonnes"].sum(), 300.0)
        self.assertEqual(case.current_time, datetime(2026, 9, 1, 8))
        self.assertAlmostEqual(case.product_build_runtime_states[0]["tonnes"], 150.0)
        self.assertAlmostEqual(case.product_build_runtime_states[1]["tonnes"], 150.0)
        produced = case.results[case.results["source_actual_tonnes"] > 0]
        self.assertTrue((produced["crusher_actual_grade_fe"].sub(58.0).abs() < 1e-7).all())

    def test_manual_snapshot_preserves_reports_and_build_boundary(self):
        planner = make_manual()
        states_before = planner.build_steady_states()
        report_before = planner.build_report(states_before)
        graph = planner.material_flow_topology
        graph["edges"].clear()
        states_after = planner.build_steady_states()
        self.assertEqual(states_before, states_after)
        pd.testing.assert_frame_equal(report_before, planner.build_report(states_after))
        self.assertIn(datetime(2026, 9, 1, 6, 30), [state["end_datetime"] for state in states_after])


if __name__ == "__main__":
    unittest.main()
