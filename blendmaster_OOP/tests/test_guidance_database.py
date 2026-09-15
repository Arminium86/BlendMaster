"""Loaded planner guidance stays on its local scenario database after relocation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from GUI.InitialiseGUI import UserInputs
from GUI.ProjectLoading import RestoreContext, prepare
from classes.ClosingROMStocksCompliance import ClosingROMStocksCompliance
from database.DatabaseContext import get_database_path, set_database_path
from database.SQLiteDatabase import DatabaseManager


class GuidanceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.previous_database = get_database_path()
        self.directory = TemporaryDirectory(prefix='guidance-db-test-')
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(set_database_path, self.previous_database)
        self.root = Path(self.directory.name)

    def host(self, database):
        host = SimpleNamespace(active_scenario_id='site', access_role='planner',
            scenario_session_directory=str(database.parent),
            site_scenarios={'site': {'database_path': str(database)}},
            stockpile_data={'SP': {'balance': 100}},
            two_wp_closing_stock_balances=[{'accepted': 'previous input'}],
            haul_cycle_file_path_choice='', stockpile_tab_index='stockpile_inventories',
            capture_guidance_schedule_controls=Mock(),
            validate_guidance_schedule_constraints=lambda: (True, ''),
            validate_form=Mock(), refresh_aps_stockpile_brand_map=Mock(),
            apply_aps_brand_guidance_to_stockpile_data=Mock(),
            apply_haul_cycle_routes_to_stockpile_data=Mock(), setup_stockpile_table=Mock(),
            auto_select_stockpiles=Mock(), save_active_scenario_state=Mock(),
            set_page_enabled=Mock(), advance_workspace=Mock())
        host.scenario_database_path = lambda site: UserInputs.scenario_database_path(host, site)
        return host

    def seed(self, path, value='saved plan'):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE retained_results (value TEXT)')
            db.execute('INSERT INTO retained_results VALUES (?)', (value,))
        return path.read_bytes()

    def test_relocated_inputs_submit_to_restored_site_and_preserve_saved_results(self):
        snapshot = self.seed(self.root / 'author.db')
        session = self.root / 'planner session'
        host = self.host(session / 'site.db')
        context = RestoreContext(host)
        context._host_type = UserInputs
        moved = self.root / 'new delivery location'
        moved.mkdir()
        inputs = [moved / name for name in ('2WP.csv', '24HR.csv', 'cycles.csv')]
        for path in inputs:
            path.write_text('same delivered input', encoding='utf-8')
        raw = dict(active_scenario_id='site', site_scenarios={'site': dict(
            database_path=str(self.root / 'removed author session' / 'site.db'),
            database_snapshot=snapshot, file_path_choice='old delivery/2WP.csv',
            file_path_24hr_choice='old delivery/24HR.csv', haul_cycle_file_path_choice='old delivery/cycles.csv')})
        # Exercise the real missing-input relink before the real database restore.
        with patch('GUI.InitialiseGUI.QFileDialog.getOpenFileName',
                   side_effect=[(str(path), '') for path in inputs]):
            self.assertTrue(UserInputs.resolve_missing_aps_mining_csv_paths(host, raw))
        with patch('GUI.GuidancePreparation.prepare', return_value={}), \
             patch('classes.HaulCycleDataHandler.HaulCycleDataHandler._read_cycles', return_value=[]), \
             patch('classes.HaulCycleDataHandler.HaulCycleDataHandler.build_nearest_crusher_routes', return_value={}), \
             patch('classes.HaulCycleDataHandler.HaulCycleDataHandler.build_destination_routes', return_value={}):
            active, scenarios, prepared = prepare(context, raw)
        host.active_scenario_id, host.site_scenarios = active, scenarios
        host.file_path_choice = prepared['file_path_choice']
        # Model a stale process-wide selection after restoration. The file
        # no longer exists, while the loaded site's database contains results.
        stale = self.root / 'expired session' / 'old.db'
        set_database_path(stale)
        stale.parent.rmdir()
        with patch('GUI.InitialiseGUI.QMessageBox.warning') as warning:
            UserInputs.handle_guidance_schedules_submit(host)
        warning.assert_not_called()
        self.assertEqual(host.file_path_choice, str(inputs[0]))
        self.assertEqual(get_database_path(), str(session / 'site.db'))
        self.assertFalse(stale.parent.exists())
        with closing(sqlite3.connect(get_database_path())) as db:
            self.assertEqual(db.execute('SELECT value FROM retained_results').fetchone(), ('saved plan',))
            columns = [row[1] for row in db.execute('PRAGMA table_info(two_wp_closing_rom_stocks)')]
            self.assertEqual(columns, ClosingROMStocksCompliance.NORMALIZED_COLUMNS)
        host.advance_workspace.assert_called_once_with('guidance_schedules')

    def test_submission_cannot_write_another_sites_database(self):
        correct, other = self.root / 'site.db', self.root / 'other.db'
        self.seed(correct)
        self.seed(other, 'other site')
        set_database_path(other)
        UserInputs.handle_guidance_schedules_submit(self.host(correct))
        self.assertEqual(get_database_path(), str(correct))
        with closing(sqlite3.connect(other)) as db:
            self.assertEqual(db.execute('SELECT value FROM retained_results').fetchone(), ('other site',))
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='two_wp_closing_rom_stocks'").fetchone())

    def test_missing_scenario_file_stops_without_creating_an_empty_replacement(self):
        for missing_parent in (False, True):
            with self.subTest(missing_parent=missing_parent):
                path = self.root / ('missing folder' if missing_parent else '.') / 'site.db'
                host = self.host(path)
                previous = host.two_wp_closing_stock_balances
                with patch('GUI.InitialiseGUI.QMessageBox.warning') as warning:
                    self.assertFalse(UserInputs.handle_guidance_schedules_submit(host))
                self.assertFalse(path.exists())
                self.assertIn(str(path), warning.call_args.args[2])
                self.assertIs(host.two_wp_closing_stock_balances, previous)
                host.refresh_aps_stockpile_brand_map.assert_not_called()
                host.save_active_scenario_state.assert_not_called()
                host.advance_workspace.assert_not_called()

    def test_write_failure_retains_accepted_guidance_and_does_not_advance(self):
        path = self.root / 'site.db'
        self.seed(path)
        host = self.host(path)
        previous = host.two_wp_closing_stock_balances
        with patch.object(DatabaseManager, 'write_two_wp_closing_rom_stocks',
                          side_effect=sqlite3.OperationalError('database is locked')), \
             patch('GUI.InitialiseGUI.QMessageBox.warning') as warning:
            self.assertFalse(UserInputs.handle_guidance_schedules_submit(host))
        self.assertIn(str(path), warning.call_args.args[2])
        self.assertIn('database is locked', warning.call_args.args[2])
        self.assertIs(host.two_wp_closing_stock_balances, previous)
        host.advance_workspace.assert_not_called()

    def test_input_only_project_gets_a_local_database_during_restore(self):
        path = self.root / 'new local session' / 'site.db'
        host = self.host(path)
        context = RestoreContext(host)
        context._host_type = UserInputs
        _, scenarios, _ = prepare(context, {'site_scenarios': {'site': {}}, 'active_scenario_id': 'site'})
        self.assertTrue(path.is_file())
        host.site_scenarios = scenarios
        UserInputs.handle_guidance_schedules_submit(host)
        host.advance_workspace.assert_called_once_with('guidance_schedules')

    def test_explicit_writer_supports_paths_with_spaces_and_uri_characters(self):
        path = self.root / 'site #1 % ?.db' if os.name != 'nt' else self.root / 'site #1 %.db'
        self.seed(path)
        rows = pd.DataFrame([['SP', '2026-09-15', '2026-09-16', 100]],
                            columns=ClosingROMStocksCompliance.NORMALIZED_COLUMNS)
        DatabaseManager().write_two_wp_closing_rom_stocks(rows, path, require_existing=True)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT two_wp_closing_rom_wmt FROM two_wp_closing_rom_stocks').fetchone(), (100,))


if __name__ == '__main__':
    unittest.main()
