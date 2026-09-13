from contextlib import closing
from copy import deepcopy
from pathlib import Path
import pickle
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from classes.SharedProjects import changes, filename, group_hashes, merge_database, merge_inputs, revision, value_hash
from classes.WorkflowCheckpoint import write_checkpoint
from classes.SiteWorkflow import default_contract
from GUI.ScheduledSiteBatch import enabled_contracts


class SharedProjectTests(unittest.TestCase):
    def test_opening_shared_file_watches_it_instead_of_its_authors_template(self):
        from GUI.SharedProjects import SharedProjectWatcher
        with tempfile.TemporaryDirectory() as directory:
            shared_folder = Path(directory) / 'Shared'
            shared_folder.mkdir()
            source = shared_folder / 'Site.prj'
            source.write_bytes(b'published model')
            template = Path(directory) / 'Template.prj'
            template.write_bytes(b'old template')
            prepared = dict(shared_project_settings=dict(folder=str(shared_folder)),
                shared_project_subscription=dict(path=str(template), revision=revision(template), baselines={'old': {}}),
                _shared_read_revision=revision(source), _shared_input_baselines={'site': {'AMT inventories and chunks': 'current'}})
            watcher = SimpleNamespace(host=SimpleNamespace(), button=Mock(), check=Mock())
            SharedProjectWatcher.loaded(watcher, str(source), prepared, {})
            self.assertEqual(watcher.host.shared_project_subscription['path'], str(source.resolve()))
            self.assertEqual(watcher.host.shared_project_subscription['revision'], revision(source))
            self.assertEqual(watcher.host.shared_project_subscription['baselines'], prepared['_shared_input_baselines'])
            working = Path(directory) / 'Planner.prj'
            working.write_bytes(b'local work')
            prepared['shared_project_subscription'] = deepcopy(watcher.host.shared_project_subscription)
            SharedProjectWatcher.loaded(watcher, str(working), prepared, {})
            self.assertEqual(watcher.host.shared_project_subscription, prepared['shared_project_subscription'])

    def test_three_way_comparison_flags_local_conflicts_and_preserves_manual_edits(self):
        initial = {'stockpile_data': {'SP1': {'balance': 100}}, 'solver_presets': [],
                   'calendar_inputs': {'crusher_rate': [100]}, 'manual_plan_states': {'P': 'draft'},
                   'product_targets': [{'target_fe': 58}], 'stored_blend_sequence_table_for_gantt': ['edited']}
        local, incoming = deepcopy(initial), deepcopy(initial)
        local['stockpile_data']['SP1']['balance'] = 110
        local['calendar_inputs']['crusher_rate'] = [80]
        incoming['stockpile_data']['SP1']['balance'] = 120
        incoming['solver_presets'] = [{'name': 'Support preset'}]
        rows = changes(group_hashes(initial), local, incoming)
        self.assertEqual({r['group']: r['conflict'] for r in rows}, {'Stockpile inventories': True, 'Solver presets': False})
        presets_only = merge_inputs(local, incoming, ['Solver presets'])
        self.assertEqual(presets_only['stockpile_data'], local['stockpile_data'])
        merged = merge_inputs(local, incoming, ['Stockpile inventories'])
        self.assertEqual(merged['stockpile_data']['SP1']['balance'], 120)
        for key in ('calendar_inputs', 'manual_plan_states', 'product_targets', 'stored_blend_sequence_table_for_gantt'):
            self.assertEqual(merged[key], local[key])
        self.assertEqual(local['stockpile_data']['SP1']['balance'], 110)
        self.assertEqual(merged['manual_input_revision'], 'shared-inputs-changed')

    def test_import_prepares_new_opf_profiles_before_releasing_unsaved_work(self):
        from GUI.SharedProjects import SharedProjectWatcher
        with tempfile.TemporaryDirectory() as directory:
            local = {'site': dict(database_path=str(Path(directory) / 'old.db'),
                solver_config={'contingency_plan_max_blend_options': 14},
                destination_progress_snapshot={'local': True})}
            incoming = {'site': dict(hex_sequence_table=[{'hex': 'new chunk'}])}
            host = SimpleNamespace(active_scenario_id='site', scenario_session_directory=directory,
                restore_site_scenario=Mock(), refresh_scenario_selector=Mock(),
                update_chart_database_context=Mock(), preparation_status_label=Mock())
            host.run_background_task = lambda message, work, done, failed: done(work())
            watcher = SimpleNamespace(host=host, pending=False, button=Mock(), check=Mock(), failed=Mock())
            with patch('GUI.SharedProjects.tempfile.mkdtemp', return_value=str(Path(directory) / 'merged')), \
                 patch('GUI.OPFProfileLoading.ensure', return_value=True) as prepare:
                SharedProjectWatcher.apply(watcher, local, incoming,
                    {'site': ['AMT inventories and chunks']}, (1, 2, 3), {})
                self.assertTrue(watcher.pending)
                host.preparation_status_label.setText.assert_not_called()
                self.assertEqual(host.site_scenarios['site']['solver_config']['contingency_plan_max_blend_options'], 14)
                self.assertEqual(host.site_scenarios['site']['destination_progress_snapshot'], {'local': True})
                self.assertEqual(host.site_scenarios['site']['hex_sequence_table'], [{'hex': 'new chunk'}])
                prepare.call_args.args[1]()
                self.assertFalse(watcher.pending)
                host.preparation_status_label.setText.assert_called_once()

    def test_incompatible_evidence_cannot_bypass_planning_time_or_mapping_choice(self):
        local = dict(start_time_choice='2026-09-13 06:00', field_mappings=[{'name': 'old'}])
        incoming = dict(start_time_choice='2026-09-13 18:00', field_mappings=[{'name': 'new'}])
        with self.assertRaisesRegex(ValueError, 'planning window'):
            merge_inputs(local, incoming, ['Stockpile inventories'])
        with self.assertRaisesRegex(ValueError, 'field mappings'):
            merge_inputs(local, incoming, ['Stockpile inventories', 'Planning window'])

    def test_canonical_hash_includes_all_rows_and_ignores_dictionary_order(self):
        self.assertEqual(value_hash({'a': 1, 'b': [2, 3]}), value_hash({'b': [2, 3], 'a': 1}))
        rows = [{'grade': i} for i in range(10000)]
        original = value_hash(rows)
        rows[5432]['grade'] = -1
        self.assertNotEqual(value_hash(rows), original)

    def test_database_import_keeps_both_local_plan_types_and_isolated_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            local, incoming, target = [str(Path(directory)/(name+'.db')) for name in ('local', 'incoming', 'merged')]
            for path, inventory, plan in ((local, 100, 'my unsaved plan'), (incoming, 120, 'Support plan')):
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute('CREATE TABLE opening_stockpile_inventories (balance REAL)')
                    connection.execute('INSERT INTO opening_stockpile_inventories VALUES (?)', (inventory,))
                    for table in ('optimised_blend_report', 'manual_plan_blend_report'):
                        connection.execute(f'CREATE TABLE {table} (plan TEXT)')
                        connection.execute(f'INSERT INTO {table} VALUES (?)', (plan,))
            merge_database(local, incoming, target, ['Stockpile inventories'])
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual(connection.execute('SELECT balance FROM opening_stockpile_inventories').fetchone()[0], 120)
                for table in ('optimised_blend_report', 'manual_plan_blend_report'):
                    self.assertEqual(connection.execute(f'SELECT plan FROM {table}').fetchone()[0], 'my unsaved plan')
            with closing(sqlite3.connect(local)) as connection:
                self.assertEqual(connection.execute('SELECT balance FROM opening_stockpile_inventories').fetchone()[0], 100)

    def test_stable_atomic_overwrite_and_failure_retains_previous_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)/filename('Combined / CC1:CC2')
            states = {'one': {'solver_presets': []}, 'two': {}}
            first = write_checkpoint(states, 'one', destination, lambda _: None, metadata={'publication': 'first'})
            token = revision(first)
            write_checkpoint(states, 'one', destination, lambda _: None, metadata={'publication': 'second'})
            self.assertNotEqual(revision(first), token)
            with patch('classes.WorkflowCheckpoint.os.replace', side_effect=PermissionError('File is open')):
                with self.assertRaises(PermissionError):
                    write_checkpoint(states, 'one', destination, lambda _: None)
            with destination.open('rb') as stream:
                self.assertEqual(pickle.load(stream)['publication'], 'second')
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_scheduler_collects_enabled_sites_independent_of_active_site(self):
        scenarios = {site: {'site_workflow_contract': default_contract(site)} for site in ('one', 'two', 'three')}
        scenarios['two']['site_workflow_contract']['enabled'] = True
        scenarios['three']['site_workflow_contract']['enabled'] = True
        self.assertEqual(set(enabled_contracts(scenarios)), {'two', 'three'})
        scenarios['three']['site_workflow_contract']['site_id'] = 'wrong'
        with self.assertRaises(ValueError):
            enabled_contracts(scenarios)


if __name__ == '__main__':
    unittest.main()
