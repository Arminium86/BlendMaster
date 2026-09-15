"""Source-level refresh, append, and bounded reconciliation reuse contracts."""
from copy import deepcopy
from contextlib import closing
from datetime import timedelta
import pickle
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
import sqlite3
import tempfile
import unittest

from classes.SourceSnapshots import source_signature, within_tolerance
from classes.ApprovedReconciliation import missing_sources
from GUI.InventoryRefresh import prepare, publish, TABLES
from GUI.InitialiseGUI import UserInputs
from database.DatabaseContext import database_scope
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from tests.test_reconciliation_application import window, raw_hex
from tests.test_manual_grade_reconciliation import state, manual
from classes.ReconciliationFactorResolver import ReconciliationFactorResolver


class SourceSnapshotTests(unittest.TestCase):
    def test_only_observation_dates_are_ignored(self):
        row = dict(NAME='SP1', BALANCE=100, FE_ROM=55,
                   transaction_datetime='2026-09-01', lineage=[dict(time='2026-08-30', tonnes=100)])
        other = deepcopy(row)
        other['transaction_datetime'] = '2026-09-02'
        self.assertEqual(source_signature(row), source_signature(other))
        for key, value in [('BALANCE', 101), ('FE_ROM', 56), ('lineage', [])]:
            self.assertNotEqual(source_signature(row), source_signature({**row, key: value}))

    def test_reconciliation_time_tolerance_does_not_roll_forward(self):
        values = state()
        manual(values)
        registry = deepcopy(values['grade_reconciliation_registry'])
        anchor = values['start_time_choice']
        for minutes in (30, 60):
            values['start_time_choice'] = anchor + timedelta(minutes=minutes)
            self.assertFalse(missing_sources(values))
            with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('unexpected search')):
                manual(values)
            self.assertEqual(registry, values['grade_reconciliation_registry'])
        values['start_time_choice'] = anchor + timedelta(minutes=61)
        self.assertTrue(missing_sources(values))
        values['reconciliation_settings']['lookback_refresh_tolerance_minutes'] = 0
        values['start_time_choice'] = anchor + timedelta(seconds=1)
        self.assertTrue(missing_sources(values))
        values['start_time_choice'] = anchor
        self.assertFalse(missing_sources(values))

    def test_reconciliation_only_changed_source_is_searched(self):
        values = state()
        manual(values)
        values['AMT_stockpile_data']['SP'][0]['FE_ROM'] = 42
        missing = missing_sources(values)
        self.assertEqual(len(missing), 2)
        self.assertTrue(all(row['hex_id'] == 'a' for row in missing))
        original = ReconciliationFactorResolver.resolve_source
        calls = []
        def search(engine, *args, **kwargs):
            calls.append((args[0], kwargs.get('hex_id')))
            return original(engine, *args, **kwargs)
        with patch.object(ReconciliationFactorResolver, 'resolve_source', search):
            manual(values)
        self.assertEqual(calls, [('SP', 'a'), ('SP', 'a')])
        self.assertFalse(missing_sources(values))

    def test_evidence_change_overrides_time_tolerance(self):
        values = state()
        manual(values)
        values['start_time_choice'] += timedelta(minutes=30)
        values['reconciliation_inputs']['samples'] = []
        missing = missing_sources(values)
        self.assertTrue(missing)
        self.assertEqual({r['opf'] for r in missing}, {values['opf_input_choice']})

    def fixture(self, amt):
        view = window()
        view._manual_grade_reconciliation = False
        view.hub_input_choice='Chichester'; view.mine_input_choice='CB'
        view.stockpile_data={'SP1':dict(NAME='SP1', BUILD='SP1_1', BALANCE=100)}
        view.updated_stockpile_data={'SP1':dict(name='sp1', build='SP1_1', balance=100, amt=amt)}
        view.stockpile_data_use_column={'SP1':True}; view.stockpile_data_AMT_column={'SP1':amt}
        view.AMT_stockpile_data={}; view.AMT_data_request_signature=''
        view.AMT_enrichment_signature=''; view.AMT_chunk_settings={}
        view.hex_sequence_table=[]; view.hex_sequence_table_argument=[]
        view.field_mapping_schema_version=3; view.field_definitions=[]; view.field_mappings=[]
        service = OpeningStockpileInventories()
        service.call_opening_AMT_stockpile_inventories=Mock(side_effect=lambda builds, when:
            {build[:-2]: [{**raw_hex(), 'FOOTPRINT':build[:-2], 'LAST_UPDATE':str(when)}] for build in builds})
        return dict(vars(view)), service

    def test_timestamp_only_submission_skips_processing_and_database_writes(self):
        for amt in (False, True):
            with self.subTest(amt=amt), tempfile.TemporaryDirectory() as directory, database_scope(str(Path(directory)/'active.db')):
                values, service = self.fixture(amt)
                first = prepare(UserInputs, values, {}, service, Event())
                result, _, _ = publish(first, str(Path(directory)/'active.db'), Event())
                values.update(result)
                values.update(pickle.loads(pickle.dumps(result)))
                before = Path(directory, 'active.db').read_bytes()
                values['start_time_choice'] += timedelta(minutes=30)
                values['stockpile_data']['SP1']['TRANSACTION_DATETIME'] = 'new observation'
                values['updated_stockpile_data']['SP1']['transaction_datetime'] = 'new observation'
                with patch.object(UserInputs, 'apply_canonical_field_mappings', side_effect=AssertionError('unchanged source processed')):
                    again = prepare(UserInputs, values, {}, service, Event())
                self.assertTrue(again.unchanged)
                publish(again, str(Path(directory)/'active.db'), Event())
                self.assertEqual(before, Path(directory, 'active.db').read_bytes())

    def test_new_source_is_appended_without_rewriting_existing_rows(self):
        for amt in (False, True):
            with self.subTest(amt=amt), tempfile.TemporaryDirectory() as directory, database_scope(str(Path(directory)/'active.db')):
                database = str(Path(directory)/'active.db')
                values, service = self.fixture(amt)
                first = prepare(UserInputs, values, {}, service, Event())
                result, _, _ = publish(first, database, Event())
                values.update(result)
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("CREATE TRIGGER keep_source BEFORE DELETE ON opening_stockpile_inventories WHEN OLD.name='SP1' BEGIN SELECT RAISE(ABORT, 'unchanged source rewritten'); END")
                    connection.execute("CREATE TABLE saved_report (value TEXT)")
                    connection.execute("INSERT INTO saved_report VALUES ('keep')")
                values['stockpile_data']['SP2']=dict(NAME='SP2', BUILD='SP2_1', BALANCE=100)
                values['updated_stockpile_data']['SP2']=dict(name='sp2',build='SP2_1',balance=100,amt=amt)
                values['stockpile_data_use_column']['SP2']=True
                values['stockpile_data_AMT_column']['SP2']=amt
                stage = prepare(UserInputs, values, {}, service, Event())
                self.assertEqual(stage.changed[TABLES[0]], ['SP2'])
                publish(stage, database, Event())
                with closing(sqlite3.connect(database)) as connection, connection:
                    self.assertEqual(connection.execute('SELECT name FROM opening_stockpile_inventories ORDER BY name').fetchall(), [('SP1',), ('SP2',)])
                    self.assertEqual(connection.execute('SELECT value FROM saved_report').fetchone(), ('keep',))

    def test_refresh_button_checks_content_without_forcing_reprocessing(self):
        with tempfile.TemporaryDirectory() as directory, database_scope(str(Path(directory)/'active.db')):
            database = str(Path(directory)/'active.db')
            values, service = self.fixture(True)
            first = prepare(UserInputs, values, {}, service, Event())
            result, _, _ = publish(first, database, Event())
            values.update(result)
            with patch.object(UserInputs, 'apply_canonical_field_mappings', side_effect=AssertionError('unchanged source processed')):
                refreshed = prepare(UserInputs, values, {}, service, Event(), force=True)
            self.assertTrue(refreshed.unchanged)
            self.assertEqual(service.call_opening_AMT_stockpile_inventories.call_count, 2)
            publish(refreshed, database, Event())

    def test_reconciliation_tolerance_setting_round_trips_in_ui(self):
        from PyQt5.QtWidgets import QApplication
        from GUI.ReconciliationReview import ReconciliationReview
        from classes.ReconciliationControls import normalise_reconciliation_settings
        app = QApplication.instance() or QApplication([])
        widget = ReconciliationReview()
        try:
            self.assertEqual(widget.refresh_tolerance.value(), 60)
            widget.refresh_tolerance.setValue(0)
            self.assertEqual(widget.settings()['lookback_refresh_tolerance_minutes'], 0)
            widget.set_context({'lookback_refresh_tolerance_minutes': 120}, 'CB OPF', ['SF'])
            self.assertEqual(widget.refresh_tolerance.value(), 120)
            for value in (-1, .5, True):
                with self.assertRaises(ValueError):
                    normalise_reconciliation_settings({'lookback_refresh_tolerance_minutes': value})
        finally:
            widget.close()
            widget.deleteLater()
