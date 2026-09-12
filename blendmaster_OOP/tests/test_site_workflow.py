import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from classes.SiteWorkflow import (default_contract, require_action, source_arrival_status,
                                  validate_contract, WorkflowEngine, PREPARATION_STEPS, PLAN_STEPS)


class SiteWorkflowTests(unittest.TestCase):
    def test_planner_cannot_change_site_model_or_support_settings(self):
        for action in ('planning_context', 'import_guidance', 'prepare', 'optimise'):
            require_action('planner', action)
        for action in ('site_model', 'solver_configuration', 'map_fields', 'automation'):
            with self.assertRaises(PermissionError):
                require_action('planner', action)
            for role in ('support', 'owner', 'agent'):
                require_action(role, action)

    def test_previous_daily_plan_remains_usable_during_and_after_replacement_window(self):
        source = default_contract('CC')['sources']['day_plan']
        for hour in (14, 16):
            status = source_arrival_status(source, datetime(2026, 9, 12, hour, 30, tzinfo=ZoneInfo('Australia/Perth')),
                                           timezone='Australia/Perth', imported_at='2026-09-11T15:00:00+08:00')
            self.assertTrue(status['previous_usable'])
            self.assertNotEqual(status['status'], 'current delivery available')

    def test_contract_defaults_and_validation(self):
        contract = default_contract('CC')
        self.assertEqual(validate_contract(contract), contract)
        self.assertEqual(contract['sources']['two_wp']['weekday'], 2)
        with self.assertRaises(ValueError):
            validate_contract({**contract, 'handoff': 'unvalidated_publish'})

    def test_unconfigured_optional_source_has_no_missed_delivery(self):
        source = default_contract('CC')['sources']['closing_balance']
        status = source_arrival_status(source, datetime(2026, 9, 12, 16, tzinfo=ZoneInfo('Australia/Perth')),
                                       timezone='Australia/Perth')
        self.assertEqual(status['status'], 'optional source not configured')
        self.assertIsNone(status['expected_by'])
        source['path'] = 'closing.csv'
        self.assertEqual(source_arrival_status(source,
            datetime(2026, 9, 12, 16, tzinfo=ZoneInfo('Australia/Perth')),
            timezone='Australia/Perth')['status'], 'expected replacement not received')

    def test_contract_rejects_malformed_sources_with_actionable_errors(self):
        for bad in ([], None, 'contract'):
            with self.assertRaisesRegex(ValueError, 'JSON object'):
                validate_contract(bad)
        contract = default_contract('CC')
        contract['sources']['day_plan'] = []
        with self.assertRaisesRegex(ValueError, 'day_plan.*JSON object'):
            validate_contract(contract)
        contract = default_contract('CC')
        del contract['sources']['day_plan']['window_start']
        with self.assertRaisesRegex(ValueError, 'HH:MM'):
            validate_contract(contract)

    def test_one_start_and_snapshot_used_throughout_complete_pipeline(self):
        seen, inputs = [], {'file_revision': 'v1'}
        start = datetime(2026, 9, 12, 14, tzinfo=ZoneInfo('Australia/Perth'))
        def action(snapshot, run):
            inputs['file_revision'] = 'v2'
            seen.append((snapshot['file_revision'], run.start_time))
            return 'ok'
        run = WorkflowEngine().run(default_contract('CC'), dict.fromkeys(PLAN_STEPS, action), inputs,
                                   endpoint='plan', now=start)
        self.assertEqual(run.status, 'plan_prepared')
        self.assertEqual(seen, [('v1', start)] * len(PLAN_STEPS))
        self.assertEqual(len(run.stages), len(PLAN_STEPS))

    def test_default_handoff_and_failure_do_not_continue_into_optimisation(self):
        actions = {stage: lambda snapshot, run: True for stage in PLAN_STEPS}
        engine = WorkflowEngine()
        run = engine.run(default_contract('CC'), actions, {})
        self.assertEqual(run.status, 'inputs_ready')
        self.assertEqual(tuple(run.outputs), PREPARATION_STEPS)
        def fail(*_):
            raise ValueError('Missing destination evidence')
        actions['destination'] = fail
        run = engine.run(default_contract('CC'), actions, {}, endpoint='plan')
        self.assertEqual(run.status, 'failed')
        self.assertNotIn('optimise', run.outputs)
