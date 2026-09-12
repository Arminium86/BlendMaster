"""Reproducible native UI/export QA. Run explicitly with unittest; no warehouse calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QPA_FONTDIR',r'C:\Windows\Fonts')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu')
from contextlib import redirect_stdout,closing
from copy import deepcopy
import io,json,pickle,sqlite3,subprocess,tempfile,time,unittest,sys,faulthandler
faulthandler.enable()
from pathlib import Path
from unittest.mock import patch,Mock
import pandas as pd
from PyQt5.QtWidgets import QApplication,QMessageBox,QWidget
from PyQt5.QtCore import Qt,QUrl
from PyQt5.QtGui import QFontDatabase,QFont
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
from GUI.InitialiseGUI import UserInputs
from GUI.MaterialFlowIntegration import sync_setup
from classes.TransportPlanning import initialise_transport
from classes.TransportReports import write_transport_reports
from classes.MaterialFlowReview import saved_flow_data,FlowTimeline
from classes.OperationalBlendPlans import split_blend_plans
from classes.CrossFeatureReports import cross_feature_sheets
from database.DatabaseContext import get_database_path,set_database_path
from tests.test_multi_feed_integration import make_multi_case
from tests.test_material_flow_topology import make_case
from tests.test_multi_lane_optimizer import settings
from tests.test_conveyor_cos import configuration

OUTPUT=Path(__file__).resolve().parents[1]/'docs'/'screenshots'/'task_30_35'

class ChartPlaceholder(QWidget):
    # Legacy Chromium cannot initialize Windows TSF in this headless host.
    def page(self):
        return Mock()
    def url(self):
        return QUrl()
    def setUrl(self,*args):
        pass
    def setHtml(self,*args):
        pass
    def reload(self):
        pass

class NativeDeliveryQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for file in ('segoeui.ttf','segoeuib.ttf','arial.ttf'):
            QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts')/file))
        cls.app.setFont(QFont('Segoe UI',10))
        OUTPUT.mkdir(parents=True,exist_ok=True)

    def test_native_application_save_load_flow_and_exports(self):
        previous=get_database_path()
        oldcwd=Path.cwd()
        metrics={}
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            original_mkdtemp=tempfile.mkdtemp
            def owned_temp(*args,**kwargs):
                kwargs['dir']=str(folder)
                return original_mkdtemp(*args,**kwargs)
            with patch('GUI.InitialiseGUI.tempfile.mkdtemp',side_effect=owned_temp),patch('GUI.InitialiseGUI.CustomWebEngineView',ChartPlaceholder):
                print('Constructing native window',file=sys.stderr,flush=True)
                host=UserInputs()
                print('Native window constructed',file=sys.stderr,flush=True)
            try:
                host.manual_gantt_poll_timer.stop()
                host.expit_sequence_refresh_timer.stop()
                host.multi_feed_configuration=settings()
                host.transport_settings=configuration(100,100)
                host.calendar_inputs={'crusher_rate':{'Preplan':100,'Period_1':100,'Period_2':100}}
                host.operational_plan_backups={'Primary':{'A':'SP_A','B':'SP_B'}}
                sync_setup(host)
                graph=host.setup_flow_graph
                key=next(iter(graph.nodes))
                graph.nodes[key].setPos(40,300)
                graph.positions_changed.emit(graph.positions())
                saved_positions=deepcopy(host.flow_node_positions)
                host.transport_setup.resize(1500,850)
                host.transport_setup.grab().save(str(OUTPUT/'setup_flow.png'))
                host.transport_setup.tabs.setCurrentIndex(1)
                host.transport_setup.grab().save(str(OUTPUT/'transport_settings.png'))
                host.transport_setup.tabs.setCurrentIndex(0)
                with redirect_stdout(io.StringIO()):
                    case=make_multi_case(builds=[dict(build_name='Product',target_tonnes=2000)])
                    case.solver_config['transport_settings']=deepcopy(host.transport_settings)
                    initialise_transport(case)
                    started=time.perf_counter()
                    case.run()
                    metrics['multi_belt_cos_seconds']=time.perf_counter()-started
                database=get_database_path()
                write_transport_reports(case,database)
                with closing(sqlite3.connect(database)) as connection:
                    case.results.assign(plan_id='Primary').to_sql('optimisation_plan_blend_report',connection,index=False,if_exists='replace')
                    case.build_product_build_report().assign(plan_id='Primary').to_sql('optimisation_plan_product_build_report',connection,index=False,if_exists='replace')
                data=saved_flow_data('Primary',database)
                result=host.material_flow_results
                result.run_async=None
                result.refresh()
                result.resize(1550,1050)
                result.grab()
                self.app.processEvents()
                result.graph.fit_graph()
                result.slider.setValue(min(3,result.slider.maximum()))
                result.grab().save(str(OUTPUT/'results_flow.png'))
                result.slider.setValue(result.slider.maximum())
                result.tabs.setCurrentIndex(2)
                result.grab().save(str(OUTPUT/'closing_cos.png'))
                result.tabs.setCurrentWidget(result.cos_profile)
                result.grab().save(str(OUTPUT/'cos_profile.png'))
                view=host.operational_blend_plans
                view.backup_context=lambda data,sheets:({'A':['SP_A'],'B':['SP_B']},host.operational_plan_backups['Primary'])
                view.run_async=None
                view.refresh()
                view.resize(1600,800)
                view.grab().save(str(OUTPUT/'point_blend_plans.png'))
                with patch('GUI.OperationalBlendPlanView.QFileDialog.getSaveFileName',return_value=(str(OUTPUT/'all_points.xlsx'),'Excel')):
                    view.export_xlsx()
                self.assertTrue((OUTPUT/'all_points.xlsx').exists(),view.status.text())
                for point in ('A','B'):
                    view.points.setCurrentText(point)
                    with patch('GUI.OperationalBlendPlanView.QFileDialog.getSaveFileName',return_value=(str(OUTPUT/(point+'.pdf')),'PDF')):
                        view.export_pdf()
                    self.assertTrue((OUTPUT/(point+'.pdf')).exists(),view.status.text())
                    subprocess.run(['pdftoppm','-scale-to','1500','-png',str(OUTPUT/(point+'.pdf')),str(OUTPUT/point)],check=True,capture_output=True)
                # Save through the real project action; the generated file is confined
                # to this test directory. Warehouse hydration is the sole load stub.
                os.chdir(folder)
                with patch('GUI.InitialiseGUI.QMessageBox.critical') as failure:
                    self.assertTrue(host.save_state(show_success=False),str(failure.call_args))
                project=next(folder.glob('*.prj'))
                with project.open('rb') as stream:
                    saved=pickle.load(stream)
                self.assertEqual(saved['flow_node_positions'],saved_positions)
                self.assertEqual(saved['operational_plan_backups']['Primary']['A'],'SP_A')
                self.assertIn('database_snapshot',saved['site_scenarios'][saved['active_scenario_id']])
                host.transport_settings={}
                host.flow_node_positions={}
                host.operational_plan_backups={}
                def stop_at_hydration():
                    host.project_load_waiting_for_inventory=True
                with patch.object(host,'prompt_loaded_project_start_time',return_value=True),patch.object(host,'handle_site_config_submit',side_effect=stop_at_hydration),patch('GUI.InitialiseGUI.QMessageBox.critical') as failure:
                    self.assertTrue(host.restore_loaded_state(saved),str(failure.call_args))
                self.assertEqual(host.flow_node_positions,saved_positions)
                self.assertEqual(host.operational_plan_backups['Primary']['B'],'SP_B')
                self.assertEqual(host.transport_settings['tipping_points']['A']['cos_capacity_wmt'],100)
                self.assertEqual(saved_flow_data('Primary')['frames']['transport_product_arrivals'].shape,data['frames']['transport_product_arrivals'].shape)
                metrics['project_save_restore']='passed; warehouse hydration stubbed'
                metrics['native_validation_scope']='Native setup/results/export controls; legacy Chromium charts replaced for headless TSF compatibility'
                # Exercise real solver paths in every supported topology.
                for mode in ('single','multi_tipping_point','combined_opf'):
                    with redirect_stdout(io.StringIO()):
                        if mode=='single':
                            model=make_case()
                        else:
                            cfg=settings()
                            if mode=='combined_opf':
                                cfg['mode']=mode
                                cfg['tipping_points'][1]['opf']='OPF2'
                            model=make_multi_case(cfg)
                        started=time.perf_counter()
                        model.run()
                    metrics[mode+'_seconds']=time.perf_counter()-started
                    self.assertGreater(model.results.source_actual_tonnes.sum(),0)
                    self.assertLess(metrics[mode+'_seconds'],15)
                # Native table virtualization and repeated slider calculations.
                frames=deepcopy(data['frames'])
                frames['feed']=pd.concat([frames['feed']]*1000,ignore_index=True)
                started=time.perf_counter()
                timeline=FlowTimeline(data['graph'],frames)
                for i in range(20):
                    timeline.frame(i%len(timeline.times))
                metrics['slider_rows']=len(frames['feed'])
                metrics['slider_20_frames_seconds']=time.perf_counter()-started
                self.assertLess(metrics['slider_20_frames_seconds'],10)
                (OUTPUT/'validation.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
                print(json.dumps(metrics))
            finally:
                os.chdir(oldcwd)
                # QWidget destruction avoids the user-facing close/save dialog.
                host.hide()
                host.deleteLater()
                self.app.processEvents()
                set_database_path(previous)

if __name__=='__main__':
    unittest.main()
