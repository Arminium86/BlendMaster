import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io
import json
import pickle
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pandas as pd
from PyQt5.QtWidgets import QApplication
from classes.PlanningPersistence import migrate_project_state,settings_signature,PROJECT_FORMAT_VERSION,PLANNING_SEMANTICS_VERSION
from classes.OperationalBlendPlans import split_blend_plans,operational_sheets
from classes.CrossFeatureReports import write_case_audits,cross_feature_sheets,factor_rows,input_audit_snapshot
from classes.TransportReports import write_transport_reports
from classes.MaterialFlowReview import saved_flow_data,FlowTimeline
from classes.TransportPlanning import initialise_transport
from classes.ConveyorCOS import ConveyorCOS
from GUI.OperationalBlendPlanView import OperationalBlendPlanView
from database.SQLiteDatabase import DatabaseManager
from database.DatabaseContext import get_database_path,set_database_path
from tests.test_multi_feed_integration import make_multi_case
from tests.test_multi_lane_optimizer import settings
from tests.test_conveyor_cos import START,configuration,mat

class PlanningPersistenceTests(unittest.TestCase):
    def test_legacy_defaults_preserve_inputs_and_inactive_scenarios(self):
        raw = dict(stockpile_data={'SP':{'balance':123}},database_snapshot=b'original bytes',
                   AMT_enrichment_signature='old',data_stream_input_cache_result={'stale':1},
                   product_build_settings=[dict(build_name='Legacy',target_fe_min=55)],
                   site_scenarios={'inactive':{'product_targets':[{'target_mode':'soft'}]}})
        before=deepcopy(raw)
        new=migrate_project_state(raw)
        self.assertEqual(raw,before)
        self.assertIs(new['stockpile_data'],raw['stockpile_data'])
        self.assertEqual(new['database_snapshot'],raw['database_snapshot'])
        self.assertEqual(new['multi_feed_configuration']['mode'],'single')
        self.assertEqual(new['reconciliation_settings']['method'],'standard')
        self.assertEqual(new['transport_settings']['tipping_points'],{})
        self.assertEqual(new['AMT_footprint_exclusions'],{})
        self.assertEqual(new['product_targets'][0]['target_mode'],'hard')
        self.assertEqual(new['site_scenarios']['inactive']['product_targets'][0]['target_mode'],'soft')
        self.assertEqual(new['AMT_enrichment_signature'],'')
        self.assertIsNone(new['data_stream_input_cache_result'])

    def test_current_state_roundtrip_preserves_all_new_settings(self):
        state=dict(project_format_version=PROJECT_FORMAT_VERSION,planning_semantics_version=PLANNING_SEMANTICS_VERSION,
            multi_feed_configuration=settings(),transport_settings=configuration(100,200),
            transport_opening_history={'request':{'version':1},'records':[]},
            flow_node_positions={'tip:A':[12.5,45]},operational_plan_backups={'Primary':{'A':'SP1'}},
            AMT_footprint_exclusions={'EX':{'schema_version':1,'excluded':True,'exclusion_reason':'Test'}},
            reconciliation_settings={'method':'auto_max_confidence'},
            product_targets=[{'target_mode':'soft','target_fe_target':58}],
            AMT_enrichment_signature='current')
        migrated=migrate_project_state(pickle.loads(pickle.dumps(state)))
        again=migrate_project_state(migrated)
        self.assertEqual(again,migrated)
        for key in ('flow_node_positions','operational_plan_backups','transport_opening_history','AMT_enrichment_signature'):
            self.assertEqual(migrated[key],state[key])

    def test_future_versions_rejected_before_any_restore_side_effect(self):
        from GUI.InitialiseGUI import UserInputs
        host=SimpleNamespace(prompt_loaded_project_start_time=Mock())
        variants=[{'project_format_version':999},{'planning_semantics_version':999},
                  {'field_mapping_schema_version':999},{'transport_settings':{'schema_version':999}},
                  {'multi_feed_configuration':{'schema_version':999}},
                  {'AMT_footprint_exclusions':{'X':{'schema_version':999}}},
                  {'product_targets':[{'product_target_schema_version':999}]},
                  {'flow_node_positions':{'bad':[float('nan'),0]}}]
        for value in variants:
            with self.subTest(value=value),self.assertRaises(ValueError):
                UserInputs.restore_loaded_state(host,{'site_scenarios':{'inactive':value}})
        host.prompt_loaded_project_start_time.assert_not_called()

    def test_fingerprints_include_operational_settings_and_exclude_presentation(self):
        original=dict(transport_settings=configuration(),product_targets=[{'target_mode':'hard'}])
        baseline=settings_signature(original)
        for key,value in [('transport_settings',configuration(200)),
                          ('product_targets',[{'target_mode':'soft'}]),
                          ('AMT_footprint_exclusions',{'SP':{'excluded':True}}),
                          ('reconciliation_settings',{'method':'lookback'}),
                          ('field_mappings',[{'target_field':'modelled_rom_wmt','source_field':'x'}]),
                          ('solver_config',{'soft_grade_preferences':{'similarity_mode':'both'}})]:
            self.assertNotEqual(baseline,settings_signature({**original,key:value}),key)
        self.assertEqual(baseline,settings_signature({**original,'flow_node_positions':{'node':[400,12]},'operational_plan_backups':{'Primary':{'A':'SP'}}}))

    def test_report_invalidation_clears_transport_and_audits_together(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'state.db'
            names=['transport_movements','transport_contents','transport_product_arrivals','material_flow_topology','plan_feature_audits']
            with closing(sqlite3.connect(path)) as connection,connection:
                for name in names:
                    connection.execute(f'CREATE TABLE {name} (plan_id TEXT)')
                    connection.execute(f"INSERT INTO {name} VALUES ('Primary')")
                connection.execute('CREATE TABLE input_material (wmt REAL)')
                connection.execute('INSERT INTO input_material VALUES (123)')
            DatabaseManager.clear_scheduling_reports(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('SELECT wmt FROM input_material').fetchone()[0],123)
                for name in names:
                    self.assertEqual(connection.execute(f'SELECT count(*) FROM {name}').fetchone()[0],0)
            DatabaseManager().clear_optimisation_plan_results(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual({r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},{'input_material'})

class CrossFeatureIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def case(self,combined=False,cos=0):
        cfg=settings()
        builds=[dict(build_name='Product',target_tonnes=2000)]
        if combined:
            cfg['mode']='combined_opf'
            cfg['tipping_points'][1]['opf']='OPF2'
            cfg['allow_opf_compensation']=True
            builds[0]['opf']='OPF1, OPF2'
        with redirect_stdout(io.StringIO()):
            case=make_multi_case(cfg,builds)
            case.solver_config['transport_settings']=configuration(100,cos)
            initialise_transport(case)
            case.run()
        return case

    def test_each_point_layout_preserves_physical_recipes_and_combined_arrivals(self):
        case=self.case(combined=True)
        plans=split_blend_plans(case.results)
        self.assertEqual(list(plans),['A','B'])
        self.assertEqual({key:value['report'].source_actual_tonnes.sum() for key,value in plans.items()},{'A':300,'B':300})
        for point,plan in plans.items():
            self.assertEqual(set(plan['report'].tipping_point),{point})
            self.assertTrue((plan['ratios']['Original ratio (%)']==plan['ratios']['Operational ratio (%)']).all())
            self.assertTrue((plan['ratios'].groupby('Steady state')['Operational ratio (%)'].sum()==100).all())
        report=case.build_product_build_report()
        self.assertEqual(report.groupby('contributing_opf').source_actual_tonnes_to_build.sum().to_dict(),{'OPF1':200,'OPF2':300})

    def test_factor_and_amt_audits_are_plan_owned(self):
        case=self.case()
        audit=dict(opf='OPF1',source_id='SP',hex_id='H1',source_kind='amt',method='lookback',
            by_brand={'SS':dict(global_fraction=.25,records=[dict(resolution_level='cell',confidence_percent=75,
                manual_override=True,lineage_fraction=.75,blend_factors={'fe':1.1},regression_factors={'fe':.9},
                provenance={'history':['sample-1']})])})
        case.site_context['reporting_input_audits']=dict(reconciliation=[audit],amt=[{'footprint_id':'EX','excluded':True,'outcome':'excluded'}])
        with TemporaryDirectory() as folder:
            path=Path(folder)/'state.db'
            write_case_audits(case,path)
            case.plan_id='Contingency 1'
            case.site_context['reporting_input_audits']={}
            write_case_audits(case,path)
            sheets=dict(cross_feature_sheets('Primary',path,case.build_product_build_report()))
            factor=sheets['Reconciliation Factors'].iloc[0]
            self.assertEqual(factor.blend_fe,1.1)
            self.assertEqual(factor.resolution_level,'cell')
            self.assertEqual(factor.global_fallback_fraction,.25)
            self.assertTrue(sheets['AMT Outcomes'].iloc[0].excluded)
            self.assertIn('not statistical',factor.score_basis)
            self.assertTrue(dict(cross_feature_sheets('Contingency 1',path))['Reconciliation Factors'].empty)

    def test_product_contributions_at_closing_and_partial_cos_boundary(self):
        case=self.case(cos=100)
        frames=dict(feed=case.results,product=case.build_product_build_report(),
            transport_product_arrivals=case.product_arrival_results,transport_contents=pd.DataFrame(case.transport.snapshots),
            transport_movements=pd.DataFrame(case.transport.movements))
        view=FlowTimeline(case.material_flow_topology,frames)
        self.assertFalse(view.frame(len(view.times)-1)['tables']['Product contributions'].empty)
        flow=ConveyorCOS(configuration(0,100,2),START,{'A':100})
        flow.add_feed('A',mat(),25,START,START+timedelta(hours=.25),100)
        flow.advance(START+timedelta(hours=.25),{'A':100})
        self.assertAlmostEqual(flow.next_chunk_boundary_hours({'A':100},1),.25)
        self.assertEqual(flow.feed_fraction('A',.25,100),0)

    def test_opening_payload_straddles_belt_arrival_without_inventing_mass(self):
        flow=ConveyorCOS(configuration(100,100),START,{'A':100},
            [dict(time=START-timedelta(hours=1.5),wmt=100,material=mat())])
        self.assertAlmostEqual(flow.balance('A'),100)
        self.assertAlmostEqual(sum(r['remaining'] for r in flow.points['A']['conveyor']),50)
        self.assertAlmostEqual(sum(r['wmt'] for r in flow.points['A']['chunks']),50)
        flow.assert_balance()


    def test_single_point_latency_preserves_sequential_build_boundaries(self):
        from tests.test_material_flow_topology import make_case
        with redirect_stdout(io.StringIO()):
            case=make_case()
            case.solver_config['transport_settings']={'tipping_points':{'OPF01':configuration()['tipping_points']['A']}}
            initialise_transport(case)
            case.run()
        self.assertAlmostEqual(case.results.source_actual_tonnes.sum(),300)
        self.assertAlmostEqual(case.product_arrival_results.source_arrival_wmt.sum(),200)
        self.assertEqual([s['tonnes'] for s in case.product_build_runtime_states],[150,50])
        self.assertEqual(case.current_time,case.planning_horizon_end())

    def test_arrival_weighting_and_soft_similarity_include_fixed_stockpile_feed(self):
        from tests import test_decision_levers as fixtures
        from tests.test_product_quality_limits import build
        from classes.ConveyorCOS import material
        from classes.MultiLaneOptimizer import MultiLaneOptimizer
        from classes.PeriodManager import PeriodManager
        event=fixtures.DecisionLeverOptimizerTests.event
        old=event('HISTORY',grade_fe=56)
        old.source_properties={'modelled_product_wmt':800,'modelled_product_dmt':500}
        history=[dict(time=START-timedelta(minutes=30),wmt=100,material=material(old,point='A',opf='OPF1'))]
        flow=ConveyorCOS(configuration(0,100,1),START,{'A':100},history)
        low,high=event('SP1',grade_fe=50),event('SP2',grade_fe=60)
        low.source_properties={'modelled_product_wmt':800,'modelled_product_dmt':500}
        high.source_properties={'modelled_product_wmt':400,'modelled_product_dmt':200}
        target=build(target_mode='soft',target_fe_target=4000/70,target_tonnes=1000,target_fe_limit_mode='soft')
        periods=PeriodManager(); periods.calculate_periods(START)
        result=MultiLaneOptimizer(settings()).run_blending_optimization(
            [low,high],fixtures.DecisionLeverOptimizerTests.target(),1,None,None,periods,'preplan',
            solver_config=dict(_transport_engine=flow,current_steady_state_datetime=START,target_product_build=target,
                source_property_weights={f'adjusted_product_{a}':'modelled_product_dmt' for a in ('fe','si','al','p','mn')},
                soft_grade_preferences={'similarity_mode':'both'},throughput_incentive_per_tonne=1e6))
        self.assertTrue(result['Linprog_result_object'].success)
        self.assertAlmostEqual(result['product_build_actual_tonnes'],120)
        audit=next(r for r in result['diagnostics']['product_quality'] if r['analyte']=='fe')
        self.assertAlmostEqual(audit['actual_grade'],4000/70)
        self.assertAlmostEqual(audit['grade_weight_tonnes'],70)
        self.assertAlmostEqual(sum(r['selected_grade_weight_fe_tonnes'] for r in result['transport_arrivals']),70)

    def test_decision_levers_only_expose_enabled_conveyor_payload_controls(self):
        from GUI.TransportRehandleControls import TransportRehandleControls
        control=TransportRehandleControls()
        self.addCleanup(control.deleteLater)
        control.set_settings(configuration(0,100))
        self.assertTrue(control.isHidden())
        self.assertEqual(control.table.rowCount(),0)
        control.set_settings(configuration(100))
        self.assertEqual(control.table.rowCount(),1)
        changed=Mock(); control.changed.connect(changed)
        control.table.cellWidget(0,1).setValue(225)
        self.assertEqual(changed.call_args.args[0]['tipping_points']['A']['rehandle_payload_wmt'],225)

    def test_opening_belt_payloads_are_fifo_and_do_not_overbook_new_feed(self):
        from classes.MultiLaneOptimizer import MultiLaneOptimizer
        from classes.PeriodManager import PeriodManager
        from tests import test_decision_levers as fixtures
        history=[dict(time=START-timedelta(minutes=45),wmt=50,material=mat(56,'OLD')),
                 dict(time=START-timedelta(minutes=30),wmt=50,material=mat(60,'NEW'))]
        flow=ConveyorCOS(configuration(100),START,{'A':100},history)
        queued=list(flow.points['A']['conveyor'])
        self.assertGreaterEqual(queued[1]['start'],queued[0]['end'])
        periods=PeriodManager(); periods.calculate_periods(START)
        result=MultiLaneOptimizer(settings()).run_blending_optimization(
            [fixtures.DecisionLeverOptimizerTests.event('SP1')],fixtures.DecisionLeverOptimizerTests.target(),
            1,None,None,periods,'preplan',solver_config=dict(_transport_engine=flow,current_steady_state_datetime=START))
        # Opening service lasts until 01:15. New interval 01:00-02:00
        # cannot overtake it; the next decision may start new feed at 01:00.
        self.assertAlmostEqual(sum(t['wmt'] for t in result['transport_tips']),0)

    def test_native_export_buttons_write_both_plans_and_their_audits(self):
        from openpyxl import load_workbook
        case=self.case()
        plans=split_blend_plans(case.results)
        view=OperationalBlendPlanView()
        self.addCleanup(view.deleteLater)
        with TemporaryDirectory() as folder:
            path=Path(folder)
            write_transport_reports(case,path/'state.db')
            sheets=cross_feature_sheets('Primary',path/'state.db',case.build_product_build_report())
            view.set_data({'plan_id':'Primary'},plans,sheets)
            self.assertEqual(view.points.count(),2)
            self.assertGreater(view.tables['Sequence'].model().rowCount(),0)
            view.choices={'A':['SP_A'],'B':['SP_B']}
            view.selections={'A':'SP_A','B':'SP_B'}
            with patch('GUI.OperationalBlendPlanView.QFileDialog.getSaveFileName',return_value=(str(path/'all.xlsx'),'Excel')):
                view.export_xlsx()
            self.assertTrue((path/'all.xlsx').exists(),view.status.text())
            book=load_workbook(path/'all.xlsx',read_only=True)
            self.assertTrue({'A Summary','B Summary','Backup Destinations','OPF Contributions','Product Quality','Plan Notes'}<=set(book.sheetnames))
            book.close()
            with patch('GUI.OperationalBlendPlanView.QFileDialog.getSaveFileName',return_value=(str(path/'A.pdf'),'PDF')):
                view.export_pdf()
            self.assertTrue((path/'A.pdf').exists(),view.status.text())
            self.assertGreater((path/'A.pdf').stat().st_size,1000)

if __name__=='__main__':
    unittest.main()
