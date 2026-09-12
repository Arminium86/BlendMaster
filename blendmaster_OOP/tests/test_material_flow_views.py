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
from PyQt5.QtCore import Qt, QModelIndex
from classes.MaterialFlowTopology import planning_topology, one_lane_topology
from classes.MaterialFlowReview import FlowTimeline,saved_flow_data,saved_flow_plans
from classes.TransportPlanning import initialise_transport
from classes.TransportReports import write_transport_reports
from GUI.MaterialFlowGraph import MaterialFlowGraph
from GUI.MaterialFlowResults import FrameModel, MaterialFlowResults
from tests.test_multi_feed_integration import make_multi_case
from tests.test_multi_lane_optimizer import settings
from tests.test_conveyor_cos import configuration

class FrameModelTests(unittest.TestCase):
    def test_headers_outside_frame_return_none(self):
        for frame in (pd.DataFrame(), pd.DataFrame(columns=['source']),
                      pd.DataFrame({'source': ['SP1']})):
            model = FrameModel(frame)
            for orientation, count in ((Qt.Horizontal, len(frame.columns)), (Qt.Vertical, len(frame))):
                for section in (-1, count, count + 1):
                    for role in (Qt.DisplayRole, Qt.ToolTipRole):
                        with self.subTest(shape=frame.shape, orientation=orientation, section=section, role=role):
                            self.assertIsNone(model.headerData(section, orientation, role))

    def test_valid_headers_keep_labels_and_tooltips_even_without_rows(self):
        model = FrameModel(pd.DataFrame(columns=['steady_state_duration', 'source_name']))
        self.assertEqual(model.headerData(0, Qt.Horizontal), 'Duration (h)')
        self.assertEqual(model.headerData(0, Qt.Horizontal, Qt.ToolTipRole), 'steady_state_duration')
        self.assertEqual(model.headerData(1, Qt.Horizontal), 'Source Name')
        self.assertIsNone(model.headerData(0, Qt.Horizontal, Qt.DecorationRole))
        populated = FrameModel(pd.DataFrame({'source': ['SP1']}))
        self.assertEqual(populated.headerData(0, Qt.Vertical), '1')

    def test_cells_outside_frame_return_none(self):
        for frame in (pd.DataFrame(), pd.DataFrame({'source': ['SP1']})):
            model = FrameModel(frame)
            for index in (QModelIndex(), model.createIndex(len(frame), 0),
                          model.createIndex(0, len(frame.columns))):
                for role in (Qt.DisplayRole, Qt.ToolTipRole):
                    with self.subTest(shape=frame.shape, row=index.row(), column=index.column(), role=role):
                        self.assertIsNone(model.data(index, role))
        self.assertEqual(model.data(model.index(0, 0)), 'SP1')

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

    def test_clearing_populated_results_leaves_safe_empty_models(self):
        view = MaterialFlowResults()
        self.addCleanup(view.deleteLater)
        view.resize(900, 600)
        view.show()
        self.addCleanup(view.close)
        for table in view.tables.values():
            table.setModel(FrameModel(pd.DataFrame({'source': ['SP1']}), table))
        view.cos_profile.setModel(FrameModel(pd.DataFrame({'source': ['SP1']}), view.cos_profile))
        self.app.processEvents()
        view.load_plan()
        self.app.processEvents()
        self.assertEqual(view.time_label.text(), 'No saved plan is available.')
        for table in (*view.tables.values(), view.cos_profile):
            model = table.model()
            self.assertEqual((model.rowCount(), model.columnCount()), (0, 0))
            self.assertIsNone(model.headerData(0, Qt.Horizontal))
            self.assertIsNone(model.headerData(0, Qt.Horizontal, Qt.ToolTipRole))
            self.assertFalse(table.grab().isNull())

    @staticmethod
    def feed_row(**changes):
        return dict(start_datetime='2026-09-13 00:00:00', end_datetime='2026-09-13 01:00:00',
                    steady_state_number=0, source_type='stockpile', source_actual_tonnes=100.,
                    source_grade_fe=58., source_closing_balance=900., **changes)

    def test_chunk_transactions_activate_one_physical_stockpile_and_its_route(self):
        graph = one_lane_topology(sources=[dict(source='SP1', source_type='stockpile', is_amt=True)])
        feed = pd.DataFrame([
            self.feed_row(source='SP1_CHUNK_001', source_id='SP1_CHUNK_001', parent_stockpile='SP1'),
            {**self.feed_row(source='SP1_CHUNK_002', source_id='SP1_CHUNK_002', parent_stockpile='SP1'),
             'source_actual_tonnes': 50., 'source_grade_fe': 62., 'source_closing_balance': 450.},
        ])
        original = feed.copy(deep=True)
        timeline = FlowTimeline(graph, {'feed': feed})
        source = next(n for n in graph['nodes'] if n['node_type'] == 'source')
        frame = timeline.frame(0)
        annotation = frame['annotations'][source['node_id']]
        self.assertTrue(annotation['active'])
        self.assertIn('150.0 ROM WMT', annotation['text'])
        self.assertIn('150.0 t/h', annotation['text'])
        self.assertIn('Fe 59.333%', annotation['text'])
        route = next(e for e in graph['edges'] if e['source_node_id'] == source['node_id'])
        self.assertIn(route['edge_id'], frame['active_edges'])
        self.assertEqual(frame['tables']['Tipping transactions'].source.tolist(), feed.source.tolist())
        pd.testing.assert_frame_equal(feed, original)
        closing = timeline.frame(len(timeline.times) - 1)['annotations'][source['node_id']]
        self.assertFalse(closing['active'])
        self.assertIn('Reported chunks closing 1,350.0 WMT', closing['text'])

    def test_parent_stockpile_takes_precedence_and_source_types_do_not_cross_match(self):
        graph = one_lane_topology(sources=[
            dict(source='SP1', source_type='stockpile'),
            dict(source='SP2', source_type='stockpile'),
            dict(source='SP1', source_type='grade_block'),
        ])
        feed = pd.DataFrame([
            self.feed_row(source='SP1', source_id='SP1', parent_stockpile='SP2'),
            {**self.feed_row(source='SP1', source_id='payload1'), 'source_type': 'grade_block'},
        ])
        frame = FlowTimeline(graph, {'feed': feed}).frame(0)
        nodes = {(n['properties']['source_type'], n['label']): n['node_id']
                 for n in graph['nodes'] if n['node_type'] == 'source'}
        self.assertFalse(frame['annotations'][nodes['stockpile', 'SP1']]['active'])
        self.assertTrue(frame['annotations'][nodes['stockpile', 'SP2']]['active'])
        self.assertTrue(frame['annotations'][nodes['grade_block', 'SP1']]['active'])
        self.assertIn('100.0 ROM WMT', frame['annotations'][nodes['stockpile', 'SP2']]['text'])

    def test_saved_chunk_and_payload_ids_resolve_when_parent_or_display_name_is_missing(self):
        graph = one_lane_topology(sources=[
            dict(source='SP1', source_id='chunk-id-1', source_type='stockpile', is_amt=True),
            dict(source='GB1', source_id='payload-id-1', source_type='grade_block'),
            dict(source='SP10', source_type='stockpile'),
        ])
        feed = pd.DataFrame([
            self.feed_row(source='Legacy chunk label', source_id='chunk-id-1', parent_stockpile=None),
            {**self.feed_row(source='', source_id='payload-id-1'), 'source_type': 'grade_block'},
            # A name resembling a chunk is not proof that it belongs to SP10.
            self.feed_row(source='SP10_CHUNK_001', source_id='unregistered', parent_stockpile=''),
        ])
        frame = FlowTimeline(graph, {'feed': feed}).frame(0)
        nodes = {n['label']: n['node_id'] for n in graph['nodes'] if n['node_type'] == 'source'}
        self.assertTrue(frame['annotations'][nodes['SP1']]['active'])
        self.assertTrue(frame['annotations'][nodes['GB1']]['active'])
        self.assertFalse(frame['annotations'][nodes['SP10']]['active'])

    def test_chunk_route_highlight_is_limited_to_its_actual_tipping_point(self):
        graph = planning_topology(multi_feed=settings(), sources=[
            dict(source='SP1', source_type='stockpile', is_amt=True),
            dict(source='SP1', source_type='grade_block'),
        ])
        feed = pd.DataFrame([
            self.feed_row(source='SP1_CHUNK_001', source_id='SP1_CHUNK_001', parent_stockpile='SP1', tipping_point='A'),
            {**self.feed_row(source='SP1_CHUNK_001', source_id='SP1_CHUNK_001', parent_stockpile='SP1', tipping_point='B'),
             'source_actual_tonnes': 0.},
            {**self.feed_row(source='SP1', source_id='payload', tipping_point='B'), 'source_type': 'grade_block'},
        ])
        frame = FlowTimeline(graph, {'feed': feed}).frame(0)
        source = next(n for n in graph['nodes'] if n['node_type'] == 'source' and n['properties']['source_type'] == 'stockpile')
        tips = {n['node_id']: n['properties']['crusher'] for n in graph['nodes'] if n['node_type'] == 'tipping_point'}
        active = {tips[e['target_node_id']] for e in graph['edges']
                  if e['source_node_id'] == source['node_id'] and e['edge_id'] in frame['active_edges']}
        self.assertEqual(active, {'A'})

    def test_compact_source_count_and_highlights_survive_display_changes(self):
        graph = one_lane_topology(sources=[dict(source=f'SP{i}', source_type='stockpile', is_amt=True) for i in range(21)])
        feed = pd.DataFrame([self.feed_row(source=f'SP{i}_CHUNK_001', source_id=f'SP{i}_CHUNK_001', parent_stockpile=f'SP{i}')
                             for i in (1, 2)])
        view = MaterialFlowResults()
        self.addCleanup(view.deleteLater)
        view.set_data(dict(graph=graph, frames={'feed': feed}))
        grouped = next(item for item in view.graph.nodes.values() if item.node['properties'].get('members'))
        self.assertEqual(grouped.metrics, '2 of 21 sources active')
        view.graph.show_sources.setChecked(True)
        self.assertEqual(sum(n.active for n in view.graph.nodes.values() if n.node['node_type'] == 'source'), 2)
        self.assertTrue(any(edge.active for edge in view.graph.edges))
        view.graph.auto_arrange()
        self.assertEqual(sum(n.active for n in view.graph.nodes.values() if n.node['node_type'] == 'source'), 2)
        view.graph.show_sources.setChecked(False)
        grouped = next(item for item in view.graph.nodes.values() if item.node['properties'].get('members'))
        self.assertEqual(grouped.metrics, '2 of 21 sources active')
        view.slider.setValue(view.slider.maximum())
        self.assertEqual(grouped.metrics, '0 of 21 sources active')
