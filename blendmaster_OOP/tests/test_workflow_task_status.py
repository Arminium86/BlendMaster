"""Legacy submission evidence and a single neutral, applicable next task."""
import unittest
from types import SimpleNamespace
from copy import deepcopy

from GUI.WorkflowNavigation import WORKSPACE, SUPPORT
from GUI.WorkflowSubmissions import task_status, order, edited, submitted


class TaskStatusTests(unittest.TestCase):
    def host(self, **values):
        return SimpleNamespace(**values)

    def statuses(self, host):
        return {page: task_status(host, page) for page in WORKSPACE}

    def test_reported_inventory_submission_makes_earlier_tasks_green(self):
        host = self.host(workflow_submission_state={
            'completed': ['stockpile_inventories'],
            'required': list(WORKSPACE[3:])})
        statuses = self.statuses(host)
        for page in WORKSPACE[:3]:
            self.assertEqual(statuses[page], 'ready')
        self.assertEqual([p for p, s in statuses.items() if s == 'next'], ['grade_reconciliation'])
        self.assertEqual(statuses['amt_stockpiles'], 'not_required')
        self.assertEqual(statuses['destination_progress'], 'not_required')
        self.assertEqual(statuses['calendar'], 'resubmit')

    def test_new_project_has_one_next_task_and_remaining_applicable_tasks_red(self):
        host = self.host()
        statuses = self.statuses(host)
        self.assertEqual([p for p, s in statuses.items() if s == 'next'], ['site_configuration'])
        for page in order(host)[1:]:
            self.assertEqual(statuses[page], 'resubmit')
        self.assertNotIn('next', [task_status(host, p) for p in SUPPORT])

    def test_legacy_saved_gate_recovers_prerequisites_but_not_current_ui_flags(self):
        host = self.host(active_scenario_id='A', site_scenarios={
            'A': {'tab_states': {'calendar': True, 'blend_plan': True}}},
            _page_enabled_state={p: True for p in WORKSPACE})
        original = deepcopy(vars(host))
        self.assertEqual(task_status(host, 'site_configuration'), 'ready')
        self.assertEqual(task_status(host, 'decision_levers'), 'ready')
        self.assertEqual(task_status(host, 'calendar'), 'next')
        self.assertEqual(task_status(host, 'blend_plan'), 'resubmit')
        self.assertEqual(vars(host), original)
        host.active_scenario_id = 'B'
        self.assertEqual(task_status(host, 'site_configuration'), 'next')

    def test_inferred_ready_tasks_can_be_invalidated_by_edits(self):
        host = self.host(workflow_submission_state={'completed': ['stockpile_inventories']})
        edited(host, 'site_configuration')
        self.assertEqual(task_status(host, 'site_configuration'), 'next')
        self.assertEqual(task_status(host, 'stockpile_inventories'), 'resubmit')

    def test_next_task_follows_amt_order_and_optional_selection(self):
        host = self.host(workflow_submission_state={'completed': ['stockpile_inventories']},
                         updated_stockpile_data={'A': {'amt': True, 'balance': 100}},
                         multi_feed_configuration={'amt_reconcile_after_chunking': True})
        self.assertEqual(task_status(host, 'amt_stockpiles'), 'next')
        self.assertEqual(task_status(host, 'grade_reconciliation'), 'resubmit')
        submitted(host, 'amt_stockpiles')
        self.assertEqual(task_status(host, 'grade_reconciliation'), 'next')

    def test_dirty_support_does_not_inherit_ready_owner(self):
        host = self.host(workflow_submission_state={'completed': ['calendar'], 'dirty': ['site_model']})
        self.assertEqual(task_status(host, 'site_model'), 'resubmit')
        self.assertEqual(task_status(host, 'guidance_settings'), 'ready')


if __name__ == '__main__':
    unittest.main()
