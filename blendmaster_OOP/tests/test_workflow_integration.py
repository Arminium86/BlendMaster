import os
import pickle
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QComboBox, QDateTimeEdit, QTabWidget
from GUI.SiteAutomation import SiteWorkflowController
from GUI.ProjectLoading import RestoreContext, prepare
from GUI.InitialiseGUI import UserInputs
from GUI.WorkflowPermissions import control_available
from GUI.WorkflowDependencies import reconciliation_input_revision
from GUI.InventoryStreamApplication import InventoryContext, FIELDS
from classes.SiteWorkflow import default_contract, PREPARATION_STEPS
from classes.WorkflowCheckpoint import write_checkpoint


class WorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self, directory):
        h = QWidget()
        h.active_scenario_id = 'site_test'
        h.access_role = 'support'
        h.background_tasks = []
        h.scenario_session_directory = directory
        h.preparation_status_label = QLabel()
        h.tabs, h.scenario_toolbar = QTabWidget(), QWidget()
        h.scenario_selector = QComboBox()
        h.prepare_inputs_button, h.cancel_preparation_button = QPushButton(), QPushButton()
        h.time_mode = QComboBox(); h.time_mode.addItem('Now')
        h.start_time = QDateTimeEdit()
        h.mine_input_choice, h.crusher_input_choice, h.opf_input_choice = 'CC', 'PC', 'OPF'
        h.selected_site_crushers, h.product_brand_labels_choice = ['PC'], ['FB']
        h.stockpile_data_use_column, h.field_mappings = {'S': True}, {'grade': 'Fe'}
        h.multi_feed_configuration = {'mode': 'single'}
        h.site_workflow_contract = default_contract('site_test')
        for source in h.site_workflow_contract['sources'].values():
            source['required'] = False
        for attr in ('file_path', 'file_path_24hr', 'haul_cycle_file_path', 'two_wp_closing_stocks_path'):
            setattr(h, attr, SimpleNamespace(text=lambda: ''))
        h.scenario_display_name = lambda _: 'Test site'
        h.planning_period_count = lambda: 3
        h.save_active_scenario_state = Mock()
        h.run_program = SimpleNamespace(request_abort=Mock())
        h.site_workflow_controller = SiteWorkflowController(h)
        h.site_workflow_controller.scheduler.stop()
        return h

    def test_input_lock_does_not_change_operational_mode_or_reconciliation_readiness(self):
        parent = QWidget()
        child = QPushButton(parent)
        parent.setEnabled(False)
        self.assertFalse(child.isEnabled())
        self.assertTrue(control_available(child))
        child.setEnabled(False)
        self.assertFalse(control_available(child))

    def test_amt_display_decodes_each_hex_once_and_keeps_analyte_denominators(self):
        import json
        from classes.GradeStreams import amt_modelled_product_slot
        host = SimpleNamespace(opf_input_choice='CC OPF02')
        slot = amt_modelled_product_slot(host.opf_input_choice).lower()
        rows = [dict(FINAL_WMT=100, LINEAGE_FINAL_WMT=100,
                     LINEAGE_MATCHED_FINAL_WMT=75,
                     GRADE_BLOCK_LINEAGE_JSON=[{'lineage_key': 'GB1'}],
                     MODELLED_PROPERTIES_JSON=json.dumps({'coverage': {slot+'_fe': .5, slot+'_si': .8}})),
                dict(FINAL_WMT=300, LINEAGE_FINAL_WMT=300,
                     LINEAGE_MATCHED_FINAL_WMT=300,
                     GRADE_BLOCK_LINEAGE_JSON=[{'lineage_key': 'GB1'}, {'lineage_key': 'GB2'}],
                     MODELLED_PROPERTIES_JSON=json.dumps({'coverage': {slot+'_fe': 1}}))]
        with patch('GUI.InitialiseGUI.json.loads', wraps=json.loads) as decode:
            lineage, coverage = UserInputs.amt_lineage_summary(host, rows)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(lineage, '2 grade blocks | 93.75% attributed | 25.00 t unmatched')
        self.assertIn('Fe 87.50%', coverage)
        self.assertIn('SiO₂ 80.00%', coverage)
        self.assertIn('P unavailable', coverage)

    def test_generated_chunks_do_not_invalidate_applied_reconciliation_but_mappings_do(self):
        host = SimpleNamespace(field_mappings={'fe': 'Fe'}, reconciliation_settings={'method': 'standard'})
        original = reconciliation_input_revision(host)
        host.hex_sequence_table = [{'footprint': 'A', 'sequence': 1, 'reconciliation': {'score': 90}}]
        self.assertEqual(reconciliation_input_revision(host), original)
        host.field_mappings = {'fe': 'OtherFe'}
        self.assertNotEqual(reconciliation_input_revision(host), original)

    def test_worker_preserves_enrichment_dependencies_and_plan_edits_do_not_refetch_grades(self):
        from tests.test_reconciliation_application import window
        h = window()
        h.AMT_chunk_settings = {'SP1': {'average_reclaim_rate': 1000}}
        h.product_targets = [{'build_name': 'Product', 'target_tonnes': 100}]
        context = InventoryContext(UserInputs, {key: vars(h)[key] for key in FIELDS if key in vars(h)})
        expected = h.AMT_enrichment_request_signature()
        self.assertEqual(context.AMT_enrichment_request_signature(), expected)
        h.product_targets[0]['target_tonnes'] = 200
        h.AMT_chunk_settings['SP1']['average_reclaim_rate'] = 900
        self.assertEqual(h.AMT_enrichment_request_signature(), expected)
        h.field_mappings = [{'source_family': 'amt', 'target_field': 'modelled_rom_fe', 'source_field': 'OTHER'}]
        self.assertNotEqual(h.AMT_enrichment_request_signature(), expected)

    def test_amt_map_delays_publication_and_drops_a_replaced_chart(self):
        from GUI.AMTMapLoading import refresh
        import pandas as pd
        old = pd.DataFrame({'hex': ['old']})
        chart = SimpleNamespace(data=old, db_path='old', fetch_data=lambda: pd.DataFrame({'hex': ['new']}))
        h = SimpleNamespace(draw_AMT_map=chart, excluded_amt_footprints=lambda: set(), AMT_chunk_settings={}, hex_sequence_table=[])
        pending = []
        h.run_background_task = lambda message, work, done, failed: pending.append((work, done))
        refresh(h)
        self.assertIs(chart.data, old)
        self.assertTrue(h._amt_map_pending)
        h.draw_AMT_map = SimpleNamespace(data=pd.DataFrame({'hex': ['other site']}))
        work, done = pending.pop()
        done(work())
        self.assertEqual(h.draw_AMT_map.data.hex.tolist(), ['other site'])
        self.assertIs(chart.data, old)

    def test_chart_connection_can_restart_after_site_changes_and_deleted_views_are_ignored(self):
        from GUI.ChartReadiness import connect_view
        from PyQt5 import sip
        view = QWidget()
        view.setUrl = Mock()
        pending = []
        host = SimpleNamespace(active_scenario_id='first')
        host.run_background_task = lambda message, work, done, **kw: pending.append(done)
        connect_view(host, view, 'http://127.0.0.1:8050')
        connect_view(host, view, 'http://127.0.0.1:8050')
        self.assertEqual(len(pending), 1)
        host.active_scenario_id = 'second'
        connect_view(host, view, 'http://127.0.0.1:8050')
        self.assertEqual(len(pending), 2)
        pending[0](True)
        view.setUrl.assert_not_called()
        sip.delete(view)
        pending[1](True)
        view.setUrl.assert_not_called()

    def test_close_waits_for_workers_before_prompt_or_database_cleanup(self):
        from GUI.WorkflowShutdown import wait_for_workers
        from PyQt5.QtGui import QCloseEvent
        host = QWidget()
        host.background_tasks = ['warehouse query']
        host.close = Mock()
        host.site_workflow_controller = SimpleNamespace(active=True, cancel=Mock())
        event = QCloseEvent()
        self.assertTrue(wait_for_workers(host, event))
        self.assertFalse(event.isAccepted())
        host._close_wait_timer.timeout.emit()
        host.close.assert_not_called()
        host.background_tasks.clear()
        host.site_workflow_controller.active = False
        host._close_wait_timer.timeout.emit()
        host.close.assert_called_once()
        self.assertFalse(wait_for_workers(host, QCloseEvent()))

    def test_actual_desktop_coordinator_waits_for_work_and_uses_one_start(self):
        with tempfile.TemporaryDirectory() as directory:
            h = self.host(directory); c = h.site_workflow_controller
            seen = []
            for stage in PREPARATION_STEPS:
                def work(name=stage):
                    seen.append((name, h._workflow_run_start))
                    c.await_ready()
                setattr(c, 'stage_' + stage, work)
            self.assertTrue(c.start())
            h.background_tasks.append('busy')
            c.poll()
            self.assertEqual(len(seen), 1)
            h.background_tasks.clear()
            for _ in PREPARATION_STEPS:
                c.poll()
            self.assertEqual(c.run.status, 'inputs_ready')
            self.assertEqual([name for name, _ in seen], list(PREPARATION_STEPS))
            self.assertEqual(len({start for _, start in seen}), 1)

    def test_review_handoff_includes_readiness_details_and_final_arrivals(self):
        with tempfile.TemporaryDirectory() as directory:
            h = self.host(directory); c = h.site_workflow_controller
            c.stage_imports = lambda: c.await_ready()
            c.start('plan')
            h.plan_readiness = dict(status='requires_review', checks=[dict(check='Product targets', status='review')])
            final_arrivals = {'day_plan': {'previous_usable': True}}
            c.arrival_status = lambda: final_arrivals
            c.finish('plan_prepared')
            self.assertEqual(c.run.status, 'plan_requires_review')
            self.assertEqual(c.run.outputs['arrivals'], final_arrivals)
            self.assertEqual(c.run.outputs['plan_readiness'], h.plan_readiness)
            h.plan_readiness['checks'].clear()
            self.assertTrue(c.run.outputs['plan_readiness']['checks'])
            self.assertIsNone(h._workflow_run_start)
            self.assertEqual(len(list((Path(directory) / 'workflow_runs').glob('*.json'))), 1)

    def test_failure_and_cancellation_stop_before_downstream_work(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                h = self.host(directory); c = h.site_workflow_controller
                c.stage_imports = lambda: c.await_ready(lambda: False)
                c.stage_inventory = Mock()
                c.start()
                if cancel:
                    h.background_tasks.append('busy'); c.cancel(); c.poll()
                    self.assertTrue(c.active)
                    h.background_tasks.clear(); c.poll()
                else:
                    c.fail('Rejected replacement')
                self.assertFalse(c.active)
                c.stage_inventory.assert_not_called()
                self.assertEqual(c.run.status, 'cancelled' if cancel else 'failed')
                self.assertTrue(h.tabs.isEnabled())

    def test_saved_contract_and_input_versions_survive_worker_restore_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SimpleNamespace(active_scenario_id='site_test', scenario_session_directory=directory)
            context = RestoreContext(host); context._host_type = UserInputs
            row = dict(mine_input_choice='CC', hub_input_choice='Chichester Hub', opf_input_choice='CC OPF02',
                       crusher_input_choice='OPF02_PC', time_mode_choice=2, start_time_choice=datetime(2026,9,12),
                       site_workflow_contract=default_contract('site_test'), manual_input_revision='manual-v1',
                       optimisation_input_revision='optimised-v1', guidance_import_audit=[dict(kind='day_plan', revision=['day.csv', 12, 34])])
            active, scenarios, prepared = prepare(context, dict(active_scenario_id='site_test', site_scenarios={'site_test': row}))
            self.assertEqual(prepared['manual_input_revision'], 'manual-v1')
            self.assertEqual(prepared['site_workflow_contract'], row['site_workflow_contract'])
            path = write_checkpoint(scenarios, active, Path(directory) / 'prepared.prj', lambda _: None)
            with open(path, 'rb') as stream:
                restored = pickle.load(stream)
            self.assertEqual(restored['site_scenarios']['site_test']['guidance_import_audit'], row['guidance_import_audit'])
            self.assertNotIn('database_path', restored['site_scenarios']['site_test'])
            self.assertNotIn('database_path', row)

    def test_worker_restore_applies_fields_without_mutating_saved_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SimpleNamespace(active_scenario_id='site_test', scenario_session_directory=directory)
            context = RestoreContext(host)
            context._host_type = UserInputs
            stockpile = dict(balance=100, grade_fe=56, grade_si=5, grade_al=2,
                             grade_p=.05, grade_mn=.1, build='B', amt=False)
            row = dict(mine_input_choice='CC', hub_input_choice='Chichester Hub',
                opf_input_choice='CC OPF02', crusher_input_choice='OPF02_PC',
                start_time_choice=datetime(2026,9,12), product_brand_labels_choice=['FB'],
                stockpile_data={'S': stockpile}, updated_stockpile_data={'S': dict(stockpile)})
            _, _, prepared = prepare(context, dict(active_scenario_id='site_test', site_scenarios={'site_test': row}))
            self.assertTrue(prepared['_project_load_fields_prepared'])
            self.assertIn('defined_fields', prepared['stockpile_data']['S'])
            self.assertNotIn('defined_fields', stockpile)

    def test_calendar_destination_cache_changes_with_the_file(self):
        from GUI.GuidancePreparation import calendar_destinations
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'plan.csv'
            path.write_text('Destination.Type,Destination.Name\nStockpile,Stockpiles/A\n', encoding='utf-8')
            host = SimpleNamespace(file_path_choice=str(path))
            self.assertEqual(calendar_destinations(host), ['A'])
            with patch('GUI.GuidancePreparation.ExpitDataHandler.get_distinct_stockpile_destinations') as read:
                self.assertEqual(calendar_destinations(host), ['A'])
                read.assert_not_called()
            path.write_text('Destination.Type,Destination.Name\nStockpile,Stockpiles/BIG\n', encoding='utf-8')
            self.assertEqual(calendar_destinations(host), ['BIG'])

    def test_planner_report_refresh_never_executes_support_sql(self):
        host = SimpleNamespace(access_role='planner',
            refresh_material_destination_plan_view=Mock(), refresh_manual_blend_plan_report=Mock(),
            refresh_closing_rom_stocks_compliance=Mock(), execute_sqlite_report_query=Mock())
        UserInputs.refresh_sqlite_reports(host)
        host.execute_sqlite_report_query.assert_not_called()
        host.refresh_manual_blend_plan_report.assert_called_once()
        host.refresh_closing_rom_stocks_compliance.assert_called_once()

    def test_active_snapshot_copies_edits_and_retains_accepted_evidence_until_replacement(self):
        from GUI.WorkflowSnapshot import copy_active_state
        evidence = {'source_destinations': {'GB1': [{'destination': 'A', 'tonnes': 100}]}}
        host = SimpleNamespace(aps_destination_guidance=evidence, AMT_stockpile_data={'A': [{'HEX': '1'}]},
            calendar_inputs={'crusher_rate': {'P1': 6000}, 'site_context': {'aps_destination_guidance': evidence}})
        saved = copy_active_state(host, {'calendar': host.calendar_inputs, 'amt': host.AMT_stockpile_data})
        host.calendar_inputs['crusher_rate']['P1'] = 5000
        self.assertEqual(saved['calendar']['crusher_rate']['P1'], 6000)
        self.assertIs(saved['calendar']['site_context']['aps_destination_guidance'], evidence)
        self.assertIs(saved['amt'], host.AMT_stockpile_data)
        host.aps_destination_guidance = {'source_destinations': {}}
        self.assertIn('GB1', saved['calendar']['site_context']['aps_destination_guidance']['source_destinations'])

    def test_plan_freshness_ignores_view_hydration_but_tracks_planning_dependencies(self):
        from copy import deepcopy
        from GUI.WorkflowDependencies import input_revision
        values = dict(calendar_inputs={'crusher_rate': {'Preplan': 6000}, 'site_context': {'destination_reconciliation': None}},
            stockpile_data_AMT_column={'A': True}, historical_recon_factors={'FB': {'blend': {'fe': 1.1}}},
            expit_mode_choice=2)
        host = SimpleNamespace(**deepcopy(values))
        original = input_revision(host)
        host.calendar_inputs['site_context'] = {'destination_reconciliation': {'loaded_from_saved_database': True}}
        self.assertEqual(input_revision(host), original)
        for changed in (dict(calendar_inputs={'crusher_rate': {'Preplan': 5500}}),
                        dict(stockpile_data_AMT_column={'A': False}), dict(expit_mode_choice=1),
                        dict(historical_recon_factors={'FB': {'blend': {'fe': 1.2}}})):
            self.assertNotEqual(input_revision(SimpleNamespace(**{**values, **changed})), original)

    def test_dashboard_submission_preserves_exact_imported_maximum_and_sequence(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
        host = QWidget()
        host.blend_results_table = QTableWidget(1, 2)
        host.blend_results_table.setHorizontalHeaderLabels(['Blend ID', 'Max Duration (hrs)'])
        host.blend_results_table.setItem(0, 0, QTableWidgetItem('1'))
        maximum = QTableWidgetItem('10.1')
        maximum.setData(Qt.UserRole, 10.136389)
        host.blend_results_table.setItem(0, 1, maximum)
        sequence = [{'Blend ID': '1', '_fixed_steady_state': True,
                     '_exact_start': '2026-08-18 19:51:49', '_exact_end': '2026-08-19 06:00:00'}]
        host.stored_blend_sequence_table_for_gantt = sequence
        host.saved_blends_for_schedule = [{'Blend ID': '1', 'Max Duration (hrs)': 10.136389}]
        host.is_project_loaded = True
        host.can_preserve_optimised_sequence = UserInputs.can_preserve_optimised_sequence
        for name in ('setup_sequence_tab', 'populate_blend_sequence_table_if_project_is_loaded',
                     'apply_manual_gantt_rows_to_table', 'start_or_update_dash_manual_chart_thread',
                     'load_manual_gantt_chart', 'set_page_enabled', 'show_page'):
            setattr(host, name, Mock())
        host.manual_gantt_view = SimpleNamespace(reload=Mock())
        host.blend_sequence_tab_index = 1
        host.save_button = QPushButton()
        with patch('GUI.InitialiseGUI.QMessageBox.information'), patch('GUI.InitialiseGUI.QTimer.singleShot'):
            UserInputs.store_blend_results(host)
        self.assertEqual(host.saved_blends_for_schedule[0]['Max Duration (hrs)'], 10.136389)
        self.assertEqual(host.stored_blend_sequence_table_for_gantt, sequence)

    def test_manual_rate_and_recipe_edits_require_regeneration(self):
        from GUI.WorkflowDependencies import manual_revision
        host = SimpleNamespace(crusher_rate_input_values={'Preplan': 5000},
                               blend_config_table_inputs={'1': {'weights': [1, 2]}})
        original = manual_revision(host)
        host.crusher_rate_input_values['Preplan'] = 4000
        self.assertNotEqual(manual_revision(host), original)
        host.crusher_rate_input_values['Preplan'] = 5000
        host.blend_config_table_inputs['1']['weights'] = [2, 1]
        self.assertNotEqual(manual_revision(host), original)

    def test_manual_rounding_invalidates_only_the_manual_plan(self):
        from GUI.WorkflowDependencies import input_revision, manual_revision
        host = SimpleNamespace(manual_ratio_rounding={'enabled': True, 'increment': 5})
        optimised, manual = input_revision(host), manual_revision(host)
        host.manual_ratio_rounding['enabled'] = False
        self.assertEqual(input_revision(host), optimised)
        self.assertNotEqual(manual_revision(host), manual)

    def test_manual_recipe_freshness_survives_save_and_tracks_unsubmitted_edits(self):
        from copy import deepcopy
        from GUI.WorkflowDependencies import manual_revision, manual_recipe_inputs
        imported = {'1': {'sources': ['A'], 'weights': [1], 'source_ratios': [.4250000006561755]}}
        current = {'1': {**deepcopy(imported['1']), 'source_ratios': ['0.4250000006561755'],
                         'available': ['Now'], 'grades': [['AMT']], 'balances': [100]}}
        host = SimpleNamespace(blend_config_table=object(), blend_config_table_inputs=imported,
            blend_data_from_config_table_inputs=current, active_manual_plan_id='Primary',
            MANUAL_PLAN_STATE_FIELDS=('blend_config_table_inputs', 'manual_input_revision'))
        host.manual_input_revision = manual_revision(host)
        UserInputs.capture_active_manual_plan_state(host)
        saved = pickle.loads(pickle.dumps(host.manual_plan_states['Primary']))
        self.assertEqual(manual_revision(SimpleNamespace(**saved)), host.manual_input_revision)
        self.assertEqual(saved['blend_config_table_inputs']['1']['source_ratios'], ['0.4250000006561755'])
        current['1']['weights'] = [2]
        self.assertNotEqual(manual_revision(host), host.manual_input_revision)
        self.assertEqual(saved['blend_config_table_inputs']['1']['weights'], [1])
        host.project_load_restore_in_progress = True
        self.assertIs(manual_recipe_inputs(host), imported)

    def test_saved_manual_sequence_hydrates_without_submission_or_generation(self):
        host = SimpleNamespace(saved_blends_for_schedule=[{'Blend ID': '1'}], is_project_loaded=False,
            setup_sequence_tab=Mock(), populate_blend_sequence_table_if_project_is_loaded=Mock())
        host.setup_sequence_tab.side_effect = lambda: self.assertTrue(host.is_project_loaded)
        UserInputs.restore_manual_sequence_view(host)
        host.setup_sequence_tab.assert_called_once()
        host.populate_blend_sequence_table_if_project_is_loaded.assert_called_once()
        self.assertFalse(host.is_project_loaded)

    def test_guidance_preparation_fingerprint_ignores_unrelated_grade_mapping_settings(self):
        from GUI.GuidancePreparation import FIELDS as GUIDANCE_FIELDS
        values = dict(file_path_choice='', start_time_choice=datetime(2026,9,12),
                      mine_input_choice='CC', opf_input_choice='CC OPF02', crusher_input_choice='OPF02_PC',
                      product_brand_labels_choice=['FB'], selected_two_wp_product_crushers=['OPF02_PC'])
        prepared = InventoryContext(UserInputs, {key: values.get(key) for key in GUIDANCE_FIELDS})
        current = InventoryContext(UserInputs, {**values, 'field_mappings': [{'source_field': 'Fe'}],
                                               'byproducts_enabled': False, 'cb_lump_percentage': 50})
        self.assertEqual(prepared.aps_guidance_input_signature(), current.aps_guidance_input_signature())
        current.selected_two_wp_product_crushers = ['OPF03_PC']
        self.assertNotEqual(prepared.aps_guidance_input_signature(), current.aps_guidance_input_signature())

    def test_target_import_baseline_survives_restore_and_keeps_three_way_refresh(self):
        from classes.TargetRefresh import merge_refreshed_targets
        from GUI.WorkflowDependencies import input_revision
        supplied = dict(opf='OPF2', crusher='PC2', brand='SS', build_name='SS Build 1',
                        target_tonnes=100, target_p_max=.05)
        normalized = UserInputs.normalized_agent_product_targets(None, [supplied])
        rows, _ = merge_refreshed_targets([], normalized)
        rows[0]['target_p_max'] = .046
        restored = UserInputs.normalized_agent_product_targets(None, rows)
        self.assertEqual(input_revision(SimpleNamespace(product_targets=rows)),
                         input_revision(SimpleNamespace(product_targets=restored)))
        refreshed, _ = merge_refreshed_targets(restored, [{**normalized[0], 'target_tonnes': 120, 'target_p_max': .06}])
        self.assertEqual(refreshed[0]['target_tonnes'], 120)
        self.assertEqual(refreshed[0]['target_p_max'], .046)

    def test_background_save_defers_io_and_closes_only_after_success(self):
        from GUI.ProjectSaving import begin
        with tempfile.TemporaryDirectory() as directory:
            host = self.host(directory)
            host.site_scenarios = {'site_test': {'product_targets': [], 'database_path': 'active.db',
                'optimisation_input_revision': 'fresh', 'site_workflow_contract': host.site_workflow_contract}}
            host.snapshot_database = Mock(return_value=b'database snapshot')
            host.close = Mock()
            pending = []
            host.run_background_task = lambda message, work, done, failed: pending.append((work, done, failed))
            with patch('GUI.ProjectSaving.Path.cwd', return_value=Path(directory)):
                self.assertTrue(begin(host, {'agent_story_text': 'legacy context'}, show_success=False, close_after=True))
            self.assertTrue(host._project_save_pending)
            host.snapshot_database.assert_not_called()
            host.close.assert_not_called()
            work, done, failed = pending.pop()
            path = work()
            with open(path, 'rb') as stream:
                saved = pickle.load(stream)
            self.assertEqual(saved['optimisation_input_revision'], 'fresh')
            self.assertEqual(saved['agent_story_text'], 'legacy context')
            self.assertEqual(saved['database_snapshot'], b'database snapshot')
            self.assertIn('database_path', host.site_scenarios['site_test'])
            done(path)
            self.app.processEvents()
            self.assertFalse(host._project_save_pending)
            self.assertTrue(host._project_close_saved)
            host.close.assert_called_once()
            host.close.reset_mock()
            with patch('GUI.ProjectSaving.QMessageBox.critical') as message:
                failed({'message': 'Disk is full'})
            self.assertFalse(host._project_close_saved)
            host.close.assert_not_called()
            message.assert_called_once()

    def test_failed_checkpoint_keeps_the_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.prj'
            path.write_bytes(b'previous accepted project')
            with patch('classes.WorkflowCheckpoint.pickle.dump', side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):
                    write_checkpoint({'site': {}}, 'site', path, lambda _: None)
            self.assertEqual(path.read_bytes(), b'previous accepted project')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_unversioned_legacy_plan_cannot_be_exported_as_current(self):
        import pandas as pd
        from GUI.PlanReadiness import result
        with tempfile.TemporaryDirectory() as directory:
            host = SimpleNamespace(planning_period_count=lambda: 3, start_time_choice=datetime(2026,9,12),
                                   calendar_inputs={}, product_targets=[])
            readiness = result(host, pd.DataFrame(), plan_type='optimised', database=str(Path(directory)/'site.db'))
            check = next(row for row in readiness['checks'] if row['check']=='Input freshness')
            self.assertEqual(check['status'], 'blocked')
            self.assertIn('predates', check['detail'])

    def test_file_export_is_deferred_until_worker_and_failures_are_reported(self):
        from GUI.ReportExport import run
        host = SimpleNamespace(background_tasks=[])
        pending = []
        host.run_background_task = lambda title, work, done, failed: pending.append((work, done, failed))
        work, done, failed = Mock(return_value='file.xlsx'), Mock(), Mock()
        run(host, 'Export', 'file.xlsx', work, completed=done, failed=failed)
        work.assert_not_called()
        task, success, failure = pending.pop()
        success(task())
        done.assert_called_once_with('file.xlsx')
        failure({'message': 'Disk full'})
        failed.assert_called_once_with({'message': 'Disk full'})
