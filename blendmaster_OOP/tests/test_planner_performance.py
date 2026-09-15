"""Regression checks for reuse without changing planning or persistence semantics."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import sqlite3
import unittest

import pandas as pd
from PyQt5.QtWidgets import QApplication, QWidget
from classes.AcceptedEvidence import copy_preparation_state, fingerprint_fields, fork_registry
from classes.SiteWorkflow import fingerprint
from classes.ExpitDataHandler import ExpitDataHandler
from classes.HaulCycleDataHandler import HaulCycleDataHandler
from GUI.InitialiseGUI import UserInputs
from GUI.InventoryRefresh import InventoryRefresh, Prepared, collect
from GUI.WorkflowSnapshot import copy_active_state
from database.DatabaseContext import get_database_path, set_database_path
from tests.test_inventory_refresh import Host, seed


class EvidenceTests(unittest.TestCase):
    def test_preparation_owns_mutable_sources_and_reuses_only_accepted_evidence(self):
        values = {'stockpile_data': {'A': {'balance': 100}},
                  'AMT_stockpile_data': {'A': [{'FINAL_WMT': 100}]},
                  'grade_reconciliation_registry': {'sources': {'A': {'detail': {'grade': 60}}}}}
        worker = copy_preparation_state(values)
        self.assertIs(worker['grade_reconciliation_registry'], values['grade_reconciliation_registry'])
        worker['stockpile_data']['A']['balance'] = 50
        worker['AMT_stockpile_data']['A'][0]['FINAL_WMT'] = 50
        self.assertEqual(values['stockpile_data']['A']['balance'], 100)
        self.assertEqual(values['AMT_stockpile_data']['A'][0]['FINAL_WMT'], 100)

    def test_calendar_applies_saved_model_before_one_population(self):
        for loaded in (False, True):
            with self.subTest(loaded=loaded):
                host = SimpleNamespace(
                    planning_period_labels=lambda: ['Preplan'],
                    is_direct_tip_enabled=lambda: False,
                    is_total_feed_operating_crusher=lambda: False,
                    product_brand_labels_choice=['FB'],
                    normalized_solver_config=lambda *args: {'custom_constraints': []},
                    current_multi_feed_configuration=lambda: {'mode': 'single'},
                    calendar_inputs={'crusher_rate': {'Preplan': 4321}},
                    solver_config={}, load_solver_config_inputs=Mock(),
                    updated_stockpile_data_keys=[], setup_calendar_first_call=False,
                    is_project_loaded=loaded, submit_calendar_first_call=True,
                    populate_calendar=Mock(), store_calendar_inputs_no_run=Mock())
                host.load_calendar_inputs = lambda: UserInputs.load_calendar_inputs(host)
                UserInputs.setup_calendar(host)
                host.populate_calendar.assert_called_once()
                rate = next(row['crusher_rate'][3] for row in host.calendar_rows
                            if isinstance(row, dict) and 'crusher_rate' in row)
                self.assertEqual(rate, [4321] if loaded else ['1000'])

    def test_cached_hash_preserves_existing_saved_result_revisions(self):
        registry = {'sources': {'é': {'detail': {'value': 1.2, 'at': datetime(2026, 9, 15)}}}}
        state = {}
        fields = {'z': [True, None], 'registry': registry, 'a': '雪'}
        expected = fingerprint(fields)
        self.assertEqual(fingerprint_fields(fields, state, accepted=('registry',)), expected)
        with patch('classes.AcceptedEvidence.json.dumps', wraps=json.dumps) as encode:
            self.assertEqual(fingerprint_fields(fields, state, accepted=('registry',)), expected)
        self.assertFalse(any(call.args[0] is registry for call in encode.call_args_list))
        fields['registry'] = fork_registry(registry)
        fields['registry']['sources']['new'] = {'detail': {'value': 2}}
        self.assertEqual(fingerprint_fields(fields, state, accepted=('registry',)), fingerprint(fields))
        self.assertNotEqual(fingerprint(fields), expected)

    def test_evidence_replacement_preserves_other_scenario_snapshot(self):
        registry = {'sources': {'one': {'detail': {'grade': 60}}},
                    'active_chunks': {'chunk': {'last_adjusted': 'old', 'grade_streams': {'fe': 60}}}}
        host = SimpleNamespace(grade_reconciliation_registry=registry,
                               data_stream_input_cache_result={'samples': [1, 2]},
                               calendar_inputs={'rate': 100})
        saved = copy_active_state(host, vars(host))
        self.assertIs(saved['grade_reconciliation_registry'], registry)
        host.grade_reconciliation_registry = fork_registry(registry)
        host.grade_reconciliation_registry['sources']['one'] = {'detail': {'grade': 61}}
        host.grade_reconciliation_registry['active_chunks']['chunk']['last_adjusted'] = 'new'
        host.data_stream_input_cache_result = {'samples': [3]}
        host.calendar_inputs['rate'] = 200
        self.assertEqual(saved['grade_reconciliation_registry']['sources']['one']['detail']['grade'], 60)
        self.assertEqual(saved['grade_reconciliation_registry']['active_chunks']['chunk']['last_adjusted'], 'old')
        self.assertEqual(saved['data_stream_input_cache_result']['samples'], [1, 2])
        self.assertEqual(saved['calendar_inputs']['rate'], 100)

    def test_checkpoint_compacts_copy_and_preserves_live_database(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'live.db'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE keep (value TEXT)')
                db.execute("INSERT INTO keep VALUES ('saved plan')")
                db.execute('CREATE TABLE discarded (value BLOB)')
                db.execute('INSERT INTO discarded VALUES (zeroblob(1000000))')
                db.commit()
                db.execute('DROP TABLE discarded')
            original = path.read_bytes()
            result = UserInputs.snapshot_database(str(path))
            self.assertEqual(path.read_bytes(), original)
            self.assertLess(len(result), len(original) / 2)
            restored = Path(directory) / 'restored.db'
            restored.write_bytes(result)
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute('SELECT value FROM keep').fetchone()[0], 'saved plan')
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_one_csv_read_produces_equivalent_guidance_views(self):
        from tests.test_dual_schedule_ingestion import feed_row
        rows = [feed_row('SP_A', 'BRAND_X', 100, '01/01/2026 00:00', '01/01/2026 01:00')]
        rows[0].update({'Source.FullName': 'Flows/SP_A', 'Source.Pit': 'PIT'})
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'Mining.csv'
            pd.DataFrame(rows).to_csv(path, index=False)
            def derive(source):
                return (ExpitDataHandler.get_2wp_schedule_guidance(source, ['BRAND_X']),
                        ExpitDataHandler.build_2wp_destination_guidance(source),
                        ExpitDataHandler.get_distinct_stockpile_destinations(source))
            expected = derive(path)
            with patch('classes.ExpitDataHandler.pd.read_csv', wraps=pd.read_csv) as read:
                actual = derive(ExpitDataHandler.read_2wp_guidance(path))
            self.assertEqual(actual, expected)
            self.assertEqual(read.call_count, 1)


class InventoryReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / 'active.db')
        seed(self.database, 'old')
        previous = get_database_path()
        set_database_path(self.database)
        self.addCleanup(lambda: set_database_path(previous))
        self.host = Host()
        self.addCleanup(self.host.deleteLater)
        def dispatch(message, work, done, failed=None, **kwargs):
            done(work())
        self.host.run_background_task = dispatch
        self.controller = InventoryRefresh(self.host)
        def stage(*args, **kwargs):
            result = Prepared(TemporaryDirectory(), {}, {}, 'Prepared')
            seed(result.database, 'new')
            result.values = {**collect(self.host), 'AMT_stockpile_data': {}, 'AMT_footprint_exclusions': {},
                             'AMT_chunk_settings': {}, 'hex_sequence_table': [], 'hex_sequence_table_argument': [],
                             'opening_inputs_revision': 'accepted'}
            return result
        self.prepare = self.enterContext(patch('GUI.InventoryRefresh.prepare', side_effect=stage))
        self.controller.start()
        self.assertIsNotNone(self.controller.accepted)

    def test_unchanged_submit_skips_preparation_publication_and_view_rebuilds(self):
        original = Path(self.database).read_bytes()
        with patch('GUI.InventoryRefresh.publish', side_effect=AssertionError('must not publish')):
            self.assertTrue(self.controller.start())
        self.assertEqual(self.prepare.call_count, 1)
        self.host.setup_calendar.assert_called_once()
        self.host.finish_AMT_stockpile_table.assert_called_once()
        self.assertEqual(Path(self.database).read_bytes(), original)
        self.assertEqual(self.host.advance_workspace.call_count, 2)

    def test_changed_control_refreshes(self):
        self.host.stockpile_table.item(0, 4).setText('1200')
        self.controller.start()
        self.assertEqual(self.prepare.call_count, 2)

    def test_explicit_refresh_bypasses_reuse(self):
        self.controller.start(force=True)
        self.assertEqual(self.prepare.call_count, 2)
        self.assertTrue(self.prepare.call_args.kwargs['force'])

    def test_nested_mapping_edit_invalidates_reuse(self):
        self.host.field_mappings = [{'source_field': 'A', 'target_field': 'fe'}]
        self.controller.remember_pending = True
        self.controller.remember()
        self.host.field_mappings[0]['source_field'] = 'B'
        self.controller.start()
        self.assertEqual(self.prepare.call_count, 2)

    def test_unrelated_map_refresh_cannot_accept_changed_preparation_inputs(self):
        self.host.field_mappings = [{'source_field': 'changed', 'target_field': 'fe'}]
        self.controller.remember()
        self.controller.start()
        self.assertEqual(self.prepare.call_count, 2)

    def test_inventory_receipt_waits_for_its_map_delivery(self):
        self.host.finish_AMT_stockpile_table.side_effect = lambda *args, **kwargs: setattr(self.host, '_amt_map_pending', True)
        self.controller.start(force=True)
        self.assertIsNone(self.controller.accepted)
        self.assertTrue(self.controller.remember_pending)
        self.controller.remember()
        self.assertIsNone(self.controller.accepted)
        self.host._amt_map_pending = False
        self.controller.remember()
        self.assertIsNotNone(self.controller.accepted)
        self.assertFalse(self.controller.remember_pending)

    def test_database_write_invalidates_reuse(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE opening_stockpile_inventories SET value='external update'")
        self.controller.start()
        self.assertEqual(self.prepare.call_count, 2)

    def test_database_write_before_reuse_delivery_is_rejected(self):
        def dispatch(message, work, done, failed=None, **kwargs):
            result = work()
            self.assertIsNone(result)
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("UPDATE opening_stockpile_inventories SET value='changed before delivery'")
            done(result)
        self.host.run_background_task = dispatch
        self.controller.start()
        self.assertEqual(self.host._inventory_refresh_request['status'], 'failed')
        self.assertEqual(self.host.advance_workspace.call_count, 1)


class ManualLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_identical_requests_coalesce_and_failed_requests_retry(self):
        from GUI.MultiManualWorkspace import MultiManualWorkspace
        from tests.test_blend_sequence_workspace import report
        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.active_scenario_id = 'site'
        host.multi_feed_configuration = {'mode': 'combined_opf', 'tipping_points': []}
        pending = []
        host.run_background_task = lambda *args, **kwargs: pending.append(args)
        widget = MultiManualWorkspace(host, sequence=True)
        widget.plans.addItem('Primary')
        widget.load_plan()
        widget.load_plan(force=True)
        self.assertEqual(len(pending), 1)
        pending.pop()[3]('failed')
        widget.load_plan()
        self.assertEqual(len(pending), 1)
        pending.pop()[2](('manual', {'frames': {'feed': report()}}))
        widget.load_plan()
        self.assertEqual(len(pending), 0)
        widget.load_plan(force=True)
        self.assertEqual(len(pending), 1)


if __name__ == '__main__':
    unittest.main()
