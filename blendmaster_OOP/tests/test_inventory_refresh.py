import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sqlite3
import tempfile
import time
import unittest

from PyQt5.QtCore import QTimer, QThread
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QMainWindow, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget
from GUI.InitialiseGUI import UserInputs
from GUI.InventoryRefresh import Prepared, InventoryRefresh, collect, issue, prepare, publish, TABLES
from GUI.InputPreparationLocks import acquire, release
from GUI.WorkflowDependencies import input_revision, preparation_issues, reconciliation_input_revision
from database.DatabaseContext import database_scope, get_database_path, set_database_path
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from tests.test_reconciliation_application import window, raw_hex


def seed(path, text):
    with closing(sqlite3.connect(path)) as connection, connection:
        for table in (*TABLES, 'optimised_blend_report', 'manual_plan_blend_report'):
            connection.execute(f'CREATE TABLE "{table}" (value TEXT)')
            connection.execute(f'INSERT INTO "{table}" VALUES (?)', (text,))


def contents(path):
    with closing(sqlite3.connect(path, timeout=.2)) as connection:
        return {table: connection.execute(f'SELECT value FROM "{table}"').fetchone()[0]
                for table in (*TABLES, 'optimised_blend_report', 'manual_plan_blend_report')}


class Host(QMainWindow):
    run_background_task = UserInputs.run_background_task
    stockpile_table_column_index = lambda self, name: {'Subset':3, 'Max Reclaim Rate (t/h)':4, 'Reclaim Threshold (WMT)':5}.get(name)
    parse_formatted_number = staticmethod(lambda value, default: float(value))
    set_stockpile_checkbox = UserInputs.set_stockpile_checkbox
    selected_AMT_data_source = lambda self: {}
    def __init__(self):
        super().__init__()
        self.active_scenario_id = 'site'
        self.background_tasks = []; self.progress_dialog = None
        root = QWidget(); self.setCentralWidget(root); self.layout = QVBoxLayout(root)
        self.scenario_toolbar = QWidget(); self.layout.addWidget(self.scenario_toolbar)
        self.tabs = QTabWidget(); self.layout.addWidget(self.tabs)
        inputs, results = QWidget(), QWidget()
        self.tabs.addTab(inputs, 'Inventory'); self.tabs.addTab(results, 'Saved plan')
        self.page_widgets = {'stockpile_inventories':inputs, 'optimised_blend_sequence':results}
        self.stockpile_table = QTableWidget(2, 6, inputs)
        self.stockpile_data = {name:dict(NAME=name, BUILD=name+'_1', BALANCE=100) for name in ('SP1','SP2')}
        self.updated_stockpile_data = {'SP1':dict(name='sp1', build='SP1_1', balance=100, amt=False)}
        self.stockpile_data_use_column = {'SP1':True,'SP2':False}
        self.stockpile_data_AMT_column = {'SP1':False,'SP2':False}
        self.stockpile_tab_index = 'stockpile_inventories'; self.define_fields_tab_index = 'define_fields'
        self.opening_stockpile_inventories = OpeningStockpileInventories()
        for row, name in enumerate(('SP1','SP2')):
            for column in (0,1):
                wrapper = QWidget(); layout = QHBoxLayout(wrapper); box = QCheckBox(); box.setChecked(column == 0)
                layout.addWidget(box); self.stockpile_table.setCellWidget(row,column,wrapper)
            for column, value in ((2,name),(3,''),(4,'1000'),(5,'0')):
                self.stockpile_table.setItem(row,column,QTableWidgetItem(value))
        for name in ('save_active_scenario_state','setup_calendar','ensure_AMT_map_panel','finish_AMT_stockpile_table',
                     'populate_define_fields_table','set_page_enabled','prepare_data_streams','show_error_popup'):
            setattr(self,name,Mock())


class InventoryRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name)/'active.db'); seed(self.database,'old')
        previous = get_database_path(); set_database_path(self.database)
        self.addCleanup(lambda:set_database_path(previous))

    def stage(self):
        stage = Prepared(tempfile.TemporaryDirectory(),{}, {}, 'Inputs prepared.')
        seed(stage.database,'new')
        return stage

    def wait(self, predicate):
        deadline = time.monotonic()+10
        while not predicate() and time.monotonic()<deadline: QTest.qWait(5)
        self.assertTrue(predicate())

    def test_atomic_publication_preserves_saved_reports(self):
        stage = self.stage()
        publish(stage,self.database,Event())
        self.assertEqual(contents(self.database),{**dict.fromkeys(TABLES,'new'),
            'optimised_blend_report':'old','manual_plan_blend_report':'old'})
        self.assertFalse(Path(stage.database).exists())

    def test_cancel_after_first_table_rolls_back_both_opening_tables(self):
        stage = self.stage()
        cancel = Mock(); cancel.is_set.side_effect=[False,True]
        with self.assertRaises(InterruptedError): publish(stage,self.database,cancel)
        self.assertEqual(set(contents(self.database).values()),{'old'})

    def test_reader_can_view_previous_plan_during_publication(self):
        stage = self.stage(); writing, resume = Event(), Event(); errors=[]
        class Pause:
            calls=0
            def is_set(self):
                self.calls+=1
                if self.calls==2:
                    writing.set(); resume.wait(5)
                return False
        def write():
            try: publish(stage,self.database,Pause())
            except BaseException as exc: errors.append(exc)
        thread=Thread(target=write); thread.start()
        try:
            self.assertTrue(writing.wait(5))
            self.assertEqual(set(contents(self.database).values()),{'old'})
        finally:
            resume.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertFalse(errors)

    def test_selection_capture_does_not_change_accepted_inventory(self):
        host=Host(); self.addCleanup(host.deleteLater)
        original=deepcopy(host.updated_stockpile_data)
        chosen=collect(host)
        self.assertEqual(set(chosen['updated_stockpile_data']),{'SP1','SP2'})
        self.assertEqual(host.updated_stockpile_data,original)
        self.assertFalse(host.stockpile_data_use_column['SP2'])

    def test_pending_refresh_changes_freshness_then_discard_restores_it(self):
        host=Host(); self.addCleanup(host.deleteLater)
        original=input_revision(host)
        host._inventory_refresh_request={'token':'pending', 'status':'running'}
        self.assertNotEqual(input_revision(host),original)
        self.assertIn('being prepared',issue(host))
        host._inventory_refresh_request={}
        self.assertEqual(input_revision(host),original)
        host.opening_inputs_revision='new opening'
        self.assertNotEqual(input_revision(host),original)

    def test_locks_allow_result_navigation_and_preserve_disabled_inputs(self):
        host=Host(); self.addCleanup(host.deleteLater)
        held=acquire(host,True); nested=acquire(host,True)
        self.assertTrue(host.tabs.isEnabled())
        self.assertTrue(host.page_widgets['optimised_blend_sequence'].isEnabled())
        self.assertFalse(host.page_widgets['stockpile_inventories'].isEnabled())
        release(host,held)
        self.assertFalse(host.page_widgets['stockpile_inventories'].isEnabled())
        release(host,nested)
        self.assertTrue(host.page_widgets['stockpile_inventories'].isEnabled())

    def test_worker_failure_retains_inputs_and_reports_while_ui_keeps_painting(self):
        host=Host(); self.addCleanup(host.deleteLater)
        controller=InventoryRefresh(host); entered,resume=Event(),Event(); ticks=[]
        timer=QTimer(); timer.timeout.connect(lambda:ticks.append(1)); timer.start(5)
        def fail(*args,**kwargs): entered.set(); resume.wait(5); raise ValueError('warehouse unavailable')
        with patch('GUI.InventoryRefresh.prepare',side_effect=fail):
            self.assertTrue(controller.start()); self.wait(entered.is_set)
            QTest.qWait(30)
            self.assertTrue(ticks)
            self.assertTrue(host.page_widgets['optimised_blend_sequence'].isEnabled())
            self.assertEqual(set(contents(self.database).values()),{'old'})
            resume.set(); self.wait(lambda:not host.background_tasks)
        timer.stop()
        self.assertEqual(set(host.updated_stockpile_data),{'SP1'})
        self.assertIn('warehouse unavailable',controller.label.text())
        self.assertEqual(host._inventory_refresh_request['status'],'failed')
        controller.discard()
        self.assertFalse(issue(host)); self.assertFalse(host.stockpile_data_use_column['SP2'])

    def test_changed_request_never_publishes_prepared_data(self):
        host=Host(); self.addCleanup(host.deleteLater)
        controller=InventoryRefresh(host); entered,resume=Event(),Event(); stage=self.stage()
        def work(*args,**kwargs): entered.set(); resume.wait(5); return stage
        with patch('GUI.InventoryRefresh.prepare',side_effect=work):
            controller.start(); self.wait(entered.is_set)
            host.start_time_choice='different start'
            resume.set(); self.wait(lambda:not host.background_tasks)
        self.assertEqual(set(contents(self.database).values()),{'old'})
        self.assertFalse(Path(stage.database).exists())

    def test_cancellation_waits_for_worker_and_retains_previous_inputs(self):
        host=Host(); self.addCleanup(host.deleteLater)
        controller=InventoryRefresh(host); entered,resume=Event(),Event(); stage=self.stage()
        def work(*args,**kwargs): entered.set(); resume.wait(5); return stage
        with patch('GUI.InventoryRefresh.prepare',side_effect=work):
            controller.start(); self.wait(entered.is_set)
            controller.request_cancel()
            self.assertEqual(host._inventory_refresh_request['status'],'cancelling')
            self.assertFalse(host.page_widgets['stockpile_inventories'].isEnabled())
            resume.set(); self.wait(lambda:not host.background_tasks)
        self.assertEqual(set(contents(self.database).values()),{'old'})
        self.assertEqual(host._inventory_refresh_request['status'],'failed')
        self.assertTrue(host.page_widgets['stockpile_inventories'].isEnabled())
        controller.discard()
        self.assertFalse(issue(host))
        self.assertFalse(Path(stage.database).exists())

    def test_keep_previous_inputs_also_restores_edited_cells(self):
        host=Host(); self.addCleanup(host.deleteLater)
        host.updated_stockpile_data['SP1'].update(subset='original',max_reclaim_rate=700,reclaim_threshold=20)
        controller=InventoryRefresh(host)
        host._inventory_refresh_request={'status':'failed'}
        controller.discard()
        self.assertEqual([host.stockpile_table.item(0,c).text() for c in (3,4,5)],['original','700','20'])

    def test_manual_calculation_and_copy_wait_for_inventory_refresh(self):
        from classes.ManualBlendPlanner import ManualBlendPlanningError
        host=Host(); self.addCleanup(host.deleteLater)
        host._inventory_refresh_request={'status':'running'}
        with self.assertRaisesRegex(ManualBlendPlanningError,'being prepared'):
            UserInputs.create_manual_blend_planner(host)
        with patch('GUI.InitialiseGUI.QMessageBox.information'):
            self.assertFalse(UserInputs.prepopulate_manual_from_optimised_result(host))
            self.assertFalse(UserInputs.clear_manual_blending_plan(host))
            self.assertFalse(UserInputs.save_state(host))

    def test_success_publishes_selection_after_worker_validation(self):
        host=Host(); self.addCleanup(host.deleteLater)
        controller=InventoryRefresh(host); stage=self.stage()
        selected=collect(host)
        stage.values={**selected,'AMT_stockpile_data':{},'AMT_footprint_exclusions':{},'AMT_chunk_settings':{},
            'hex_sequence_table':[],'hex_sequence_table_argument':[], 'opening_inputs_revision':'new version'}
        with patch('GUI.InventoryRefresh.prepare',return_value=stage):
            controller.start(); self.wait(lambda:not host.background_tasks)
        self.assertEqual(host._inventory_refresh_request['status'],'ready')
        self.assertNotIn('needs refreshing',controller.label.text())
        self.assertEqual(set(host.updated_stockpile_data),{'SP1','SP2'})
        self.assertTrue(host.stockpile_data_use_column['SP2'])
        self.assertEqual(contents(self.database)['optimised_blend_report'],'old')
        self.assertTrue(host.page_widgets['stockpile_inventories'].isEnabled())
        host.finish_AMT_stockpile_table.assert_called_once()

    def test_real_preparation_only_fetches_new_amt_footprints(self):
        view=window()
        view.hub_input_choice='Chichester'; view.mine_input_choice='CB'
        view.stockpile_data={name:dict(NAME=name, BUILD=name+'_1', BALANCE=100) for name in ('SP1','SP2')}
        view.updated_stockpile_data={name:dict(name=name.lower(),build=name+'_1',balance=100,amt=True) for name in ('SP1','SP2')}
        view.stockpile_data_use_column=dict.fromkeys(view.stockpile_data,True)
        view.stockpile_data_AMT_column=dict.fromkeys(view.stockpile_data,True)
        view.AMT_stockpile_data={'SP1':[raw_hex()]}
        view.AMT_enrichment_signature=''; view.AMT_chunk_settings={}
        view.hex_sequence_table=[]; view.hex_sequence_table_argument=[]
        view.field_mapping_schema_version=3; view.field_definitions=[]; view.field_mappings=[]
        view.AMT_data_request_signature=view.AMT_opening_request_signature({'SP1':view.updated_stockpile_data['SP1']})
        service=OpeningStockpileInventories()
        service.call_opening_AMT_stockpile_inventories=Mock(return_value={'SP2':[{**raw_hex(),'FOOTPRINT':'SP2'}]})
        original=deepcopy(view.AMT_stockpile_data)
        stage=prepare(UserInputs,vars(view),{},service,Event())
        self.addCleanup(stage.close)
        service.call_opening_AMT_stockpile_inventories.assert_called_once_with(['SP2_1'],view.start_time_choice)
        self.assertEqual(set(stage.values['AMT_stockpile_data']),{'SP1','SP2'})
        self.assertIn('reconciliation',stage.values['AMT_stockpile_data']['SP2'][0])
        self.assertEqual(view.AMT_stockpile_data,original)
        self.assertEqual(set(contents(self.database).values()),{'old'})
        self.assertTrue(stage.values['opening_inputs_revision'])

    def test_amt_map_rebuilds_saved_chunks_in_worker(self):
        from GUI.AMTMapLoading import refresh
        from GUI.DrawCharts import DrawAMTStockpile
        view=window(); QMainWindow.__init__(view); self.addCleanup(view.deleteLater)
        view.background_tasks=[]; view.progress_dialog=None; view.active_scenario_id='test'
        view.show_progress_dialog=Mock(); view.close_progress_dialog=Mock(); view.show_error_popup=Mock()
        view.handle_AMT_stockpile_fetch_error=Mock()
        view.stockpile_data={'SP1':dict(NAME='SP1',BUILD='SP1_1',BALANCE=100)}
        view.updated_stockpile_data={'SP1':dict(name='sp1',build='SP1_1',balance=100,amt=True)}
        view.AMT_stockpile_data={'SP1':[raw_hex()]}
        view.AMT_chunk_settings={'SP1':dict(average_reclaim_rate=100,chunk_reclaim_hours=1)}
        view.hex_sequence_table=[dict(footprint='SP1',hex='SP1_chunk1',member_hexes=['H1'],balance=100,sequence=1)]
        view.hex_sequence_table_argument=deepcopy(view.hex_sequence_table)
        view.AMT_enrichment_signature=''; view.AMT_chunk_reconciliation_signature=''
        OpeningStockpileInventories().save_AMT_to_database(view.AMT_stockpile_data)
        view.draw_AMT_map=DrawAMTStockpile(self.database,0,deepcopy(view.hex_sequence_table),defer_load=True)
        original=UserInputs.reconcile_saved_AMT_chunk_grade_streams
        calls=[]; ui=int(QThread.currentThreadId())
        def reconcile(context,*args,**kwargs):
            calls.append(int(QThread.currentThreadId()))
            return original(context,*args,**kwargs)
        with patch.object(UserInputs,'reconcile_saved_AMT_chunk_grade_streams',new=reconcile):
            refresh(view); self.wait(lambda:not view.background_tasks)
        view.handle_AMT_stockpile_fetch_error.assert_not_called()
        self.assertFalse(view._amt_map_pending)
        self.assertEqual(len(calls),1)
        self.assertNotEqual(calls[0],ui)
        self.assertEqual(view.hex_sequence_table[0]['balance'],100)
        self.assertTrue(view.AMT_chunk_reconciliation_signature)


if __name__=='__main__': unittest.main()
