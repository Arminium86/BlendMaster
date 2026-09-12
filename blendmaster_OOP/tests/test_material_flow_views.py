import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io
import json
from copy import deepcopy
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
import pandas as pd
from PyQt5.QtWidgets import QApplication
from classes.MaterialFlowTopology import planning_topology
from classes.MaterialFlowReview import FlowTimeline,saved_flow_data,saved_flow_plans
from classes.TransportPlanning import initialise_transport
from classes.TransportReports import write_transport_reports
from GUI.MaterialFlowGraph import MaterialFlowGraph
from GUI.MaterialFlowResults import MaterialFlowResults
from tests.test_multi_feed_integration import make_multi_case
from tests.test_multi_lane_optimizer import settings
from tests.test_conveyor_cos import configuration

class MaterialFlowViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def case(self):
        with redirect_stdout(io.StringIO()):
            case = make_multi_case(builds=[dict(build_name='Product',target_tonnes=2000)])
            case.solver_config['transport_settings']=configuration(100)
            initialise_transport(case)
            case.run()
        return case

    def test_derived_graph_has_storage_and_routes_without_changing_settings(self):
        cfg=settings()
        before=deepcopy(cfg)
        graph=planning_topology(multi_feed=cfg,transport_settings=configuration(100,200),
            sources=[dict(source='SP1',source_type='stockpile'),dict(source='GB1',source_type='grade_block',allowed_tipping_points=['A'])])
        self.assertEqual(cfg,before)
        self.assertTrue(graph['properties']['latency_enabled'])
        enabled=[n for n in graph['nodes'] if n['node_type']=='conveyor' and n['properties']['latency_enabled']]
        self.assertEqual(len(enabled),1)
        self.assertEqual(enabled[0]['properties']['latency_hours'],1)
        gradeblock=next(n for n in graph['nodes'] if n['label']=='GB1')
        self.assertEqual(sum(e['source_node_id']==gradeblock['node_id'] for e in graph['edges']),1)

    def test_node_positions_roundtrip_without_mutating_topology(self):
        graph=planning_topology(multi_feed=settings())
        before=deepcopy(graph)
        view=MaterialFlowGraph()
        self.addCleanup(view.deleteLater)
        view.set_graph(graph)
        first=next(iter(view.nodes))
        old_path=view.edges[0].path()
        view.nodes[first].setPos(27,43)
        saved=json.loads(json.dumps(view.positions()))
        self.assertEqual(saved[first],[27,43])
        self.assertNotEqual(view.edges[0].path(),old_path)
        view.set_graph(graph,saved)
        self.assertEqual(view.positions()[first],[27,43])
        self.assertEqual(graph,before)

    def test_time_slider_separates_tip_arrival_and_closing_contents(self):
        case=self.case()
        frames=dict(feed=case.results,product=case.build_product_build_report(),
                    transport_product_arrivals=case.product_arrival_results,
                    transport_contents=pd.DataFrame(case.transport.snapshots),
                    transport_movements=pd.DataFrame(case.transport.movements))
        view=MaterialFlowResults()
        self.addCleanup(view.deleteLater)
        view.set_data(dict(graph=case.material_flow_topology,frames=frames,warnings=case.transport.warnings))
        first=view.timeline.frame(0)
        self.assertEqual(first['tables']['Tipping transactions'].source_actual_tonnes.sum(),200)
        self.assertEqual(first['tables']['OPF arrivals'].source_arrival_wmt.sum(),100)
        view.slider.setValue(view.slider.maximum())
        last=view.timeline.frame(view.slider.maximum())
        self.assertTrue(last['tables']['Tipping transactions'].empty)
        self.assertEqual(last['tables']['Conveyor / COS contents'].physical_rom_wmt.sum(),100)
        self.assertTrue(view.slider.isEnabled())

    def test_saved_topology_is_scoped_to_the_plan(self):
        case=self.case()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'plan.db'
            write_transport_reports(case,path)
            self.assertEqual(saved_flow_plans(path),['Primary'])
            saved=saved_flow_data('Primary',path)
            self.assertEqual(saved['graph'],json.loads(json.dumps(case.material_flow_topology,default=str)))
            self.assertAlmostEqual(saved['frames']['transport_product_arrivals'].source_arrival_wmt.sum(),500)
