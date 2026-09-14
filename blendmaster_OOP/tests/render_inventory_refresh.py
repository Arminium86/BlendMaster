"""Explicit full-window QA for staged inventory refresh; no warehouse access."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch
import json
import gc
import sqlite3
import tempfile
import time
import unittest

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication
from GUI.InitialiseGUI import UserInputs
from GUI.InventoryRefresh import issue
from GUI.WorkflowViews import schedule
from GUI.BlendSequenceTimeline import IntervalItem
from database.DatabaseContext import get_database_path, set_database_path
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from tests.render_planning_delivery import ChartPlaceholder
from tests.test_blend_sequence_workspace import report, inputs
from tests.test_reconciliation_application import raw_hex

OUTPUT = Path(__file__).resolve().parents[1] / 'docs' / 'screenshots' / 'inventory_refresh'


class InventoryWindowQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ('segoeui.ttf', 'segoeuib.ttf'):
            QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / name))
        cls.app.setFont(QFont('Segoe UI', 10))
        OUTPUT.mkdir(parents=True, exist_ok=True)

    def wait(self, predicate):
        deadline = time.monotonic() + 25
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), 'Native refresh did not settle')

    def test_previous_combined_plan_remains_viewable_through_failure_and_retry(self):
        warehouse = patch('snowflake.connector.connect', side_effect=AssertionError('Warehouse access is disabled in this UI fixture'))
        warehouse.start()
        self.addCleanup(warehouse.stop)
        previous = get_database_path()
        with tempfile.TemporaryDirectory() as folder:
            original_mkdtemp = tempfile.mkdtemp
            def owned_temp(*args, **kwargs):
                kwargs['dir'] = folder
                return original_mkdtemp(*args, **kwargs)
            with patch('GUI.InitialiseGUI.tempfile.mkdtemp', side_effect=owned_temp), patch(
                    'GUI.InitialiseGUI.CustomWebEngineView', ChartPlaceholder):
                host = UserInputs()
            resume = Event()
            try:
                host.site_workflow_controller.scheduler.stop()
                host.manual_gantt_poll_timer.stop()
                host.expit_sequence_refresh_timer.stop()
                host.opening_stockpile_inventories = OpeningStockpileInventories()
                host.hub_input_choice, host.mine_input_choice = 'Chichester', 'CC'
                host.opf_input_choice, host.crusher_input_choice = 'CC OPF01', 'OPF01_PC'
                host.selected_site_crushers = ['OPF01_PC']
                host.start_time_choice = datetime(2026, 1, 1, 6)
                host.calendar_inputs = inputs()[0]
                host.multi_feed_configuration = host.calendar_inputs['site_context']['multi_feed_settings']
                host.stockpile_data = {name: dict(NAME=name, BUILD=name+'_1', BALANCE=100,
                    GRADE_FE=60, GRADE_SI=4, GRADE_AL=2, GRADE_P=.05, GRADE_MN=.1)
                    for name in ('SP1', 'SP2')}
                host.updated_stockpile_data = {'SP1': dict(name='sp1', build='SP1_1', balance=100, amt=False)}
                host.stockpile_data_use_column = {'SP1': True, 'SP2': False}
                host.stockpile_data_AMT_column = {'SP1': False, 'SP2': False}
                host.setup_stockpile_table()
                host.set_page_enabled('stockpile_inventories', True)
                database = get_database_path()
                host.opening_stockpile_inventories.save_to_database(host.stockpile_data)
                host.opening_stockpile_inventories.save_AMT_to_database({})
                with closing(sqlite3.connect(database)) as connection:
                    report().to_sql('optimised_blend_report', connection, if_exists='replace', index=False)
                schedule(host, results=True)
                self.wait(lambda: not host.background_tasks and host._workflow_views.results['optimised'])
                host.show_page('optimised_blend_sequence', force=True)
                host.load_gantt_chart()
                self.wait(lambda: not host.background_tasks)
                self.assertEqual(sum(isinstance(i, IntervalItem) for i in host.gantt_chart_view.scene.items()), 2)
                host.resize(1560, 930)
                host.show()
                for row in range(host.stockpile_table.rowCount()):
                    if host.stockpile_table.item(row, 2).text() == 'SP2':
                        host.set_stockpile_checkbox(row, 1, True)
                entered = Event()
                def unavailable(*args):
                    entered.set()
                    resume.wait(20)
                    raise ValueError('Validation warehouse is unavailable')
                ticks = []
                timer = QTimer(host)
                timer.timeout.connect(lambda: ticks.append(time.monotonic()))
                timer.start(10)
                with patch.object(host.opening_stockpile_inventories,
                        'call_opening_AMT_stockpile_inventories', side_effect=unavailable):
                    self.assertTrue(host.store_stockpile_table())
                    self.wait(entered.is_set)
                    self.wait(lambda: len(ticks) >= 10)
                    self.assertTrue(host.tabs.isEnabled())
                    self.assertTrue(host.page_widgets['optimised_blend_sequence'].isEnabled())
                    self.assertFalse(host.page_widgets['stockpile_inventories'].isEnabled())
                    host.gantt_chart_view.select_interval(1)
                    self.assertEqual(host.gantt_chart_view.details.model().rowCount(), 1)
                    host.grab().save(str(OUTPUT / 'preparing_with_previous_plan.png'))
                    resume.set()
                    self.wait(lambda: not host.background_tasks)
                self.assertIn('Retry', issue(host))
                self.assertEqual(set(host.updated_stockpile_data), {'SP1'})
                controller = host._inventory_refresh_controller
                host.grab().save(str(OUTPUT / 'refresh_failed_previous_plan_retained.png'))
                fetched = {'SP2': [{**raw_hex(), 'FOOTPRINT':'SP2'}]}
                # The native AMT table is real. Legacy Dash hosting is excluded
                # because Chromium is replaced in this headless fixture.
                with patch.object(host.opening_stockpile_inventories,
                        'call_opening_AMT_stockpile_inventories', return_value=fetched), patch.object(
                        host, 'start_dash_AMT_map_thread'), patch.object(host, 'refresh_AMT_map_data_from_database'), patch(
                        'GUI.InitialiseGUI.CustomWebEngineView', ChartPlaceholder), patch.object(host, 'prepare_data_streams'):
                    self.assertTrue(controller.start())
                    self.wait(lambda: not host.background_tasks)
                self.assertEqual(host._inventory_refresh_request['status'], 'ready', controller.label.text())
                self.assertNotIn('needs refreshing', controller.label.text())
                self.assertEqual(set(host.updated_stockpile_data), {'SP1', 'SP2'})
                self.assertEqual(host.AMT_stockpile_table.rowCount(), 1)
                self.assertTrue(host.page_widgets['stockpile_inventories'].isEnabled())
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM optimised_blend_report').fetchone()[0], 3)
                host.grab().save(str(OUTPUT / 'refresh_applied_previous_plan_retained.png'))
                timer.stop()
                (OUTPUT / 'validation.json').write_text(json.dumps(dict(
                    previous_combined_plan_intervals=2, saved_report_rows=3,
                    responsive_timer_ticks=len(ticks), failure_retained_previous_selection=True,
                    retry_published_new_selection=True,
                    scope='Real native window and workers; warehouse and legacy Dash hosting stubbed.'), indent=2))
            finally:
                resume.set()
                self.wait(lambda: not host.background_tasks)
                host.hide()
                for child in host.findChildren(QTimer):
                    child.stop()
                host.deleteLater()
                self.app.processEvents()
                set_database_path(previous)
                gc.collect()


if __name__ == '__main__':
    unittest.main()
