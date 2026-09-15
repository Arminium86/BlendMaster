import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
import sqlite3
import tempfile
from pathlib import Path
from copy import deepcopy
from contextlib import closing
import pandas as pd
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtWidgets import QTableWidget,QTableWidgetItem,QMainWindow
from PyQt5.QtCore import Qt
from GUI.BlendSequenceTimeline import sequence_data, BlendSequenceTimeline, IntervalItem
from GUI.MultiManualWorkspace import MultiManualWorkspace
from GUI.MaterialFlowGraph import MaterialFlowGraph, compact_sources
from classes.MaterialFlowTopology import one_lane_topology
from classes.ManualAllocationEdits import recalculate
from classes.DirectTipLimits import direct_tip_violations
from classes.MultiFeedCalendar import calendar_key, apply_calendar
from classes.SavedResultViews import read_report,plan_names
from GUI.WorkflowViews import result_presence
from database.DatabaseContext import get_database_path,set_database_path
from tests.test_multi_lane_optimizer import settings,MultiLaneOptimizerTests
from tests.test_product_quality_limits import build
from classes.EventData import EventData


def report():
    return pd.DataFrame([dict(tipping_point=point,opf=opf,source=source,source_id=source,source_type='stockpile',
        steady_state_number=0,blend_ID=1,period='preplan',start_datetime='2026-01-01 06:00:00',
        end_datetime='2026-01-01 07:00:00',steady_state_duration=1,source_actual_tonnes=tonnes,
        source_opening_balance=1000,source_closing_balance=1000-tonnes,source_blend_ratio=.9,
        crusher_source_tonnes=tonnes,reclaimer_source_tonnes=tonnes,product_build_source_tonnes=tonnes*.8,
        source_property_modelled_product_wmt=tonnes*.8,
        **{f'source_grade_{a}':v for a,v in dict(fe=60,si=4,al=2,p=.05,mn=.1).items()})
        for point,opf,source,tonnes in [('A','OPF1','SP1',60),('A','OPF1','SP2',40),('B','OPF2','SP3',100)]])


def inputs():
    cfg=settings(); cfg['mode']='combined_opf'; cfg['tipping_points'][1]['opf']='OPF2'
    calendar={'solver_config':{'multi_feed_settings':cfg},'site_context':{'multi_feed_settings':cfg}}
    for point in ('A','B'):
        for field,val in [('crusher_rate',200),('max_reclaim_rate',200),('direct_feed_ratio_min',0),('direct_feed_ratio_max',1)]:
            calendar[calendar_key(point,field)]={'Preplan':val}
    periods={'preplan_start':pd.Timestamp('2026-01-01 06:00'),'preplan_end':pd.Timestamp('2026-01-01 08:00')}
    return calendar,periods


class BlendSequenceWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_simultaneous_blend_ids_keep_point_scoped_sources_and_clicks(self):
        frame,intervals=sequence_data(report())
        self.assertEqual(len(intervals),2)
        self.assertEqual([r['tonnes'] for r in intervals],[100,100])
        self.assertIn('60.00%',intervals[0]['sources'])
        self.assertNotIn('SP3',intervals[0]['sources'])
        view=BlendSequenceTimeline(); self.addCleanup(view.deleteLater)
        view.set_report(report(),[{'name':'C','opf':'OPF2'}])
        self.assertEqual(view.points.count(),4)
        self.assertEqual(sum(isinstance(i,IntervalItem) for i in view.scene.items()),2)
        view.select_interval(1)
        self.assertEqual(view.details.model().rowCount(),1)

    def test_calendar_cell_edits_are_retained_before_submission(self):
        from GUI.InitialiseGUI import UserInputs
        host=UserInputs.__new__(UserInputs); QMainWindow.__init__(host)
        self.addCleanup(host.deleteLater)
        host.calendar_inputs={}
        host.main_table=QTableWidget(1,4,host)
        host.main_table.setHorizontalHeaderLabels(['','Preplan','Period_1','Period_2'])
        caption=QTableWidgetItem('Direct Tip Ratio Min')
        key=calendar_key('OPF02_PC','direct_feed_ratio_min'); caption.setData(Qt.UserRole,key)
        host.main_table.setItem(0,0,caption)
        host.main_table.itemChanged.connect(host.on_calendar_cell_changed)
        for column in range(1,4): host.main_table.setItem(0,column,QTableWidgetItem('0.1'))
        self.assertEqual(host.calendar_inputs[key],dict.fromkeys(['Preplan','Period_1','Period_2'],.1))
        host.calendar_headers=['','Preplan','Period_1','Period_2']
        host.calendar_rows=[{key:('Direct Tip Ratio Min',[True]*3,'blue',[0]*3)}]
        host.current_multi_feed_configuration=lambda:settings()
        # Preparation renders defaults first, then hydrates saved targets.
        host.populate_calendar()
        host.load_calendar_inputs()
        self.assertEqual(host.calendar_inputs[key],dict.fromkeys(host.calendar_headers[1:],.1))
        self.assertEqual([host.main_table.item(0,c).text() for c in range(1,4)],['0.1']*3)

    def test_switching_multi_manual_plans_restores_their_own_state(self):
        from GUI.InitialiseGUI import UserInputs
        host=UserInputs.__new__(UserInputs); QMainWindow.__init__(host)
        self.addCleanup(host.deleteLater)
        host.multi_feed_configuration=settings()
        host.active_manual_plan_id='Primary'; host.manual_input_revision='primary revision'
        host.manual_plan_states={'Contingency 1':{'manual_input_revision':'contingency revision',
            'blend_plan_backup_destinations':{'B':'SP3'}}}
        host.blend_plan_backup_destinations={'A':'SP1'}
        host.activate_manual_plan('Contingency 1')
        self.assertEqual(host.manual_input_revision,'contingency revision')
        self.assertEqual(host.blend_plan_backup_destinations,{'B':'SP3'})
        host.activate_manual_plan('Primary')
        self.assertEqual(host.manual_input_revision,'primary revision')
        self.assertEqual(host.blend_plan_backup_destinations,{'A':'SP1'})

    def test_small_material_flow_source_groups_toggle_and_preserve_positions(self):
        graph=one_lane_topology(sources=[dict(source=name,source_type='stockpile') for name in ('S1','S2')])
        view=MaterialFlowGraph(); self.addCleanup(view.deleteLater)
        view.set_graph(graph)
        self.assertEqual(sum(i.node['node_type']=='source' for i in view.nodes.values()),1)
        view.show_sources.setChecked(True)
        sources=[i for i in view.nodes.values() if i.node['node_type']=='source']
        self.assertEqual(len(sources),2)
        sources[0].setPos(51,72); key=sources[0].node['node_id']
        view.show_sources.setChecked(False); view.show_sources.setChecked(True)
        self.assertEqual(view.positions()[key],[51,72])
        view.clear_graph(); view.show_sources.setChecked(False)
        self.assertEqual(view.nodes,{})
        self.assertFalse(view.show_sources.isEnabled())

    def test_metadata_placeholder_uses_primary_without_crossing_plan_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'results.db'
            with closing(sqlite3.connect(path)) as connection:
                pd.DataFrame(columns=['plan_id','plan_rank']).to_sql('optimisation_plan_blend_report',connection,index=False)
                report().to_sql('optimised_blend_report',connection,index=False)
            self.assertEqual(plan_names(path),['Primary'])
            self.assertEqual(len(read_report(path)),3)
            self.assertTrue(read_report(path,plan_id='Missing').empty)
            self.assertTrue(read_report(path,'manual').empty)

    def test_manual_dashboard_reads_named_saved_allocations_without_legacy_recipe(self):
        previous=get_database_path(); self.addCleanup(lambda:set_database_path(previous))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'results.db'; set_database_path(str(path))
            with closing(sqlite3.connect(path)) as connection:
                report().assign(plan_id='Primary').to_sql('manual_plan_blend_report',connection,index=False)
                report().assign(plan_id='Primary').to_sql('optimisation_plan_blend_report',connection,index=False)
            self.assertEqual(result_presence(str(path)),{'manual':True,'optimised':True})
            host=QWidget(); host.active_manual_plan_id='Primary'; host.multi_feed_configuration=inputs()[0]['site_context']['multi_feed_settings']
            host.selected_optimisation_plan_id=lambda:'Primary'
            host.run_background_task=lambda title,work,done,*args,**kwargs:done(work())
            view=MultiManualWorkspace(host); view.refresh()
            self.assertEqual(view.kind,'manual'); self.assertEqual(len(view.report),3)
            self.assertEqual(view.authoring.points.count(),2)
            self.assertEqual(view.workspace_tabs.count(), 1)
            self.assertEqual(view.workspace_tabs.tabText(0), 'Blend Configuration')
            self.assertFalse(hasattr(view, 'update_button'))
            self.assertNotIn('Run',view.status.text())
            host.deleteLater()

    def test_manual_quantity_edits_recalculate_mass_ratio_and_product(self):
        base=report(); edited=base.copy(); edited.loc[0,'source_actual_tonnes']=90
        calendar,periods=inputs()
        result=recalculate(base,edited,calendar,periods,[],{})
        self.assertAlmostEqual(result.loc[0,'product_build_source_tonnes'],72)
        self.assertAlmostEqual(result.loc[0,'source_property_modelled_product_wmt'],72)
        self.assertAlmostEqual(result.loc[0,'source_blend_ratio'],90/130)
        self.assertAlmostEqual(result.loc[2,'source_blend_ratio'],1)
        self.assertEqual(base.loc[0,'source_actual_tonnes'],60)
        self.assertEqual(result.loc[0,'source_closing_balance'],910)

    def test_manual_edits_reject_inventory_and_equipment_breaches(self):
        base=report(); edited=base.copy(); calendar,periods=inputs()
        edited.loc[0,'source_actual_tonnes']=1200
        with self.assertRaisesRegex(ValueError,'available'): recalculate(base,edited,calendar,periods,[],{})
        edited.loc[0,'source_actual_tonnes']=190
        with self.assertRaisesRegex(ValueError,'Calendar'): recalculate(base,edited,calendar,periods,[],{})

    def test_fractional_legacy_states_remain_contiguous_across_all_points(self):
        from classes.ReportTiming import restore_report_timing
        first=report(); first['end_datetime']='2026-01-01 06:00:01'; first['steady_state_duration']=1.5/3600
        second=first.copy(); second['steady_state_number']=1
        second['start_datetime']='2026-01-01 06:00:01'; second['end_datetime']='2026-01-01 06:00:03'
        result=restore_report_timing(pd.concat([first,second],ignore_index=True))
        self.assertEqual(result.iloc[0].end_datetime,pd.Timestamp('2026-01-01 06:00:01.500'))
        self.assertEqual(result.iloc[3].start_datetime,result.iloc[0].end_datetime)
        self.assertEqual(result.iloc[-1].end_datetime,pd.Timestamp('2026-01-01 06:00:03'))

    def test_multi_manual_edits_replay_delayed_arrivals_with_same_transport_engine(self):
        from tests.test_manual_transport import ManualTransportTests
        base,calendar,periods=ManualTransportTests().fixture()
        cfg=settings(); cfg['tipping_points'][0].update(name='C1',opf='OPF1'); cfg['tipping_points'][1].update(name='C2',opf='OPF1')
        calendar['site_context']['multi_feed_settings']=cfg
        calendar['solver_config']['multi_feed_settings']=cfg
        for point in ('C1','C2'):
            for field,value in [('crusher_rate',100),('max_reclaim_rate',100),('direct_feed_ratio_min',0),('direct_feed_ratio_max',1)]:
                calendar[calendar_key(point,field)]={'Period_1':value}
        base['tipping_point']='C1'; base['opf']='OPF1'
        edited=base.copy(); edited['source_actual_tonnes']=180
        result=recalculate(base,edited,calendar,periods,[],{'nodes':[],'edges':[]})
        arrivals=result.attrs['transport_frames']['transport_product_arrivals']
        self.assertAlmostEqual(result.source_actual_tonnes.sum(),180)
        self.assertAlmostEqual(arrivals.source_actual_tonnes.sum(),90)
        self.assertAlmostEqual(arrivals.product_build_source_tonnes.sum(),81)

    def test_minimum_is_hard_in_all_periods_and_missing_point_is_reported(self):
        calendar,periods=inputs(); calendar[calendar_key('B','direct_feed_ratio_min')]={'Preplan':.1}
        self.assertIn('B',direct_tip_violations(report(),calendar,periods,'combined_opf')[0])
        self.assertIn('no tipping',direct_tip_violations(report().iloc[:2],calendar,periods,'combined_opf')[0])
        for label in ('Preplan','Period_1','Period_2'):
            cfg=settings(); calendar={calendar_key('B','direct_feed_ratio_min'):{label:.1}}
            cfg=apply_calendar(cfg,calendar,['Preplan','Period_1','Period_2'])
            period='preplan' if label=='Preplan' else label.lower()
            cfg['tipping_points'][1]['targets_by_period']['preplan']=cfg['tipping_points'][1]['targets_by_period'][period]
            result=MultiLaneOptimizerTests().solve(cfg,target_product_build=build(target_mode='soft',target_fe_target=58))
            self.assertFalse(result['Linprog_result_object'].success)

    def test_empty_direct_tip_payload_cannot_idle_its_point_to_evade_minimum(self):
        cfg=settings(); cfg['tipping_points'][1]['targets_by_period']['preplan']['direct_feed_ratio_min']=.1
        from tests.test_decision_levers import DecisionLeverOptimizerTests as Fixtures
        def solve(balance):
            block=EventData(stockpile=None,grade_block='GB',event_type='grade_block',equipment='EX',cost=0,cash=0,rate=100,
                grade_fe=60,grade_si=4,grade_al=2,grade_p=.08,grade_mn=.1,balance=balance,max_quantity=balance,
                reclaim_threshold=0,state=None,auto_turnover_datetime=None,source_name='GB')
            return MultiLaneOptimizerTests().solve(cfg,[Fixtures.event('SP1'),Fixtures.event('SP2'),block],
                direct_tip_point_by_payload={'GB':['B']},target_product_build=build(target_mode='soft',target_fe_target=58))
        self.assertFalse(solve(0)['Linprog_result_object'].success)
        valid=solve(25)
        self.assertTrue(valid['Linprog_result_object'].success)
        local=valid['tipping_point_results']['B']['transactions']
        amount=sum(t['actual_tonnes'] for t in local)
        direct=sum(t['actual_tonnes'] for t in local if t['source_type']=='grade_block')
        self.assertGreaterEqual(direct/amount,.1-1e-7)


if __name__=='__main__': unittest.main()
