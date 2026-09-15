"""Work budgets and stale-delivery safeguards for the conservative UI fixes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication, QWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QLineEdit, QLabel
from GUI.InitialiseGUI import UserInputs
from GUI.WorkflowNavigation import WORKSPACE, SUPPORT
from GUI.WorkflowSubmissions import task_statuses
from GUI import ReportQuery, PlannerPresentation
from database.DatabaseContext import get_database_path, set_database_path


class StatusBudgetTests(unittest.TestCase):
    def test_one_scan_and_no_stale_cache_after_in_place_inventory_change(self):
        import setup.AMTSpatialReconciliation as spatial
        host = SimpleNamespace(updated_stockpile_data={'A': {'amt': True, 'balance': 100}},
            AMT_stockpile_data={'A': [{'RAW_WMT': 100, 'INVENTORY_BALANCE_WMT': 100}]},
            workflow_submission_state={'completed': ['stockpile_inventories'],
                'required': ['amt_stockpiles', 'calendar']})
        with patch.object(spatial, 'raw_amt_hex_total', wraps=spatial.raw_amt_hex_total) as scan:
            statuses = task_statuses(host, (*WORKSPACE, *SUPPORT))
            self.assertEqual(scan.call_count, 1)
        self.assertEqual(statuses['amt_stockpiles'], 'next')
        self.assertEqual(statuses['calendar'], 'resubmit')
        host.AMT_stockpile_data['A'][0]['RAW_WMT'] = 0
        statuses = task_statuses(host, (*WORKSPACE, *SUPPORT))
        self.assertEqual(statuses['amt_stockpiles'], 'not_required')
        self.assertEqual(statuses['calendar'], 'next')

    def test_source_dependency_work_does_not_scale_with_brand_count(self):
        from tests.test_manual_grade_reconciliation import state
        from GUI.ManualGradeReconciliation import saved_review
        from classes.SourceSnapshots import source_dependencies
        values = state()
        counts = []
        for brands in (['FB'], ['FB', 'SF']):
            values['product_brand_labels_choice'] = brands
            original = deepcopy(values)
            with patch('classes.SourceSnapshots.source_dependencies', wraps=source_dependencies) as dependencies:
                audits, _, _ = saved_review(values)
                counts.append(dependencies.call_count)
            self.assertTrue(audits)
            self.assertEqual(values, original)
        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])


class QueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = get_database_path()
        self.addCleanup(set_database_path, old)
        self.database = str(Path(self.temp.name) / 'report.db')
        set_database_path(self.database)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE report (value INTEGER)')
            db.executemany('INSERT INTO report VALUES (?)', [(1,), (2,)])
        self.jobs = []
        self.host = SimpleNamespace(active_scenario_id='A', sqlite_report_query=QLineEdit(),
            sqlite_report_status=QLabel(), sqlite_report_table=QTableWidget(),
            configure_read_only_sqlite_connection=UserInputs.configure_read_only_sqlite_connection,
            populate_dataframe_table=Mock(),
            run_background_task=lambda message, work, done, failed, **kw: self.jobs.append((work, done, failed)))
        for name in ('sqlite_report_query', 'sqlite_report_status', 'sqlite_report_table'):
            self.addCleanup(getattr(self.host, name).deleteLater)

    def begin(self, query):
        self.host.sqlite_report_query.setText(query)
        ReportQuery.begin(self.host, query)
        return self.jobs[-1]

    def test_query_is_deferred_and_preserves_result(self):
        work, done, _ = self.begin('SELECT * FROM report ORDER BY value')
        self.host.populate_dataframe_table.assert_not_called()
        with ThreadPoolExecutor(max_workers=1) as pool:
            frame = pool.submit(work).result()
        done(frame)
        self.assertEqual(self.host.current_sqlite_report_df.value.tolist(), [1, 2])
        self.host.populate_dataframe_table.assert_called_once()

    def test_out_of_order_and_edited_queries_cannot_replace_current_result(self):
        old_work, old_done, old_failed = self.begin('SELECT 1 AS value')
        work, done, _ = self.begin('SELECT 2 AS value')
        done(work())
        old_done(old_work())
        with patch.object(ReportQuery.QMessageBox, 'warning') as warning:
            old_failed({'message': 'old query failed'})
            warning.assert_not_called()
        self.assertEqual(self.host.current_sqlite_report_df.value.tolist(), [2])
        work, done, _ = self.begin('SELECT 3 AS value')
        self.host.sqlite_report_query.setText('SELECT 4 AS value')
        done(work())
        self.assertEqual(self.host.current_sqlite_report_df.value.tolist(), [2])

    def test_site_or_database_changes_reject_delivery(self):
        for change in ('site', 'database'):
            set_database_path(self.database)
            self.host.active_scenario_id = 'A'
            work, done, _ = self.begin('SELECT * FROM report')
            frame = work()
            if change == 'site':
                self.host.active_scenario_id = 'B'
            else:
                set_database_path(str(Path(self.temp.name) / 'other.db'))
            done(frame)
        self.host.populate_dataframe_table.assert_not_called()

    def test_worker_cannot_write_database(self):
        work, _, _ = self.begin('DELETE FROM report')
        with self.assertRaises(Exception):
            work()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM report').fetchone()[0], 2)

    def test_presentation_navigation_only_visits_destination_tables(self):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        layout = QVBoxLayout(host)
        host.page_widgets = {}
        tables = []
        for key in ('calendar', 'blend_plan'):
            page = QWidget(host)
            layout.addWidget(page)
            page_layout = QVBoxLayout(page)
            table = QTableWidget(page)
            page_layout.addWidget(table)
            host.page_widgets[key] = page
            tables.append(table)
        with patch.object(PlannerPresentation, 'apply_table') as apply:
            PlannerPresentation.refresh(host, page='calendar')
            apply.assert_called_once_with(tables[0])
            apply.reset_mock()
            PlannerPresentation.refresh(host)
            self.assertEqual(apply.call_count, 2)

    def test_manual_item_edit_recalculates_once(self):
        table = QTableWidget()
        self.addCleanup(table.deleteLater)
        host = SimpleNamespace(blend_config_table=table, populate_blend_config_table=Mock(),
            format_blend_config_table=Mock(), update_reclaim_rate=Mock(), update_blend_results=Mock())
        host.on_blend_data_change = lambda: UserInputs.on_blend_data_change(host)
        UserInputs.setup_blend_config_table(host)
        table.setRowCount(1)
        table.setItem(0, 11, QTableWidgetItem('1'))
        host.update_reclaim_rate.reset_mock()
        host.update_blend_results.reset_mock()
        table.item(0, 11).setText('2')
        host.update_reclaim_rate.assert_called_once()
        host.update_blend_results.assert_called_once()

    def test_string_format_cache_is_local_to_render_and_column(self):
        import pandas as pd
        table = QTableWidget()
        self.addCleanup(table.deleteLater)
        host = SimpleNamespace(format_table_display_value=Mock(side_effect=lambda value, header: f'{header}:{value}'))
        frame = pd.DataFrame({'first': ['same'] * 10, 'second': ['same'] * 10})
        UserInputs.populate_dataframe_table(host, table, frame)
        self.assertEqual(host.format_table_display_value.call_count, 2)
        self.assertEqual(table.item(9, 1).text(), 'second:same')
        host.format_table_display_value = Mock(return_value='new format')
        UserInputs.populate_dataframe_table(host, table, frame)
        self.assertEqual(table.item(0, 0).text(), 'new format')


if __name__ == '__main__':
    unittest.main()
