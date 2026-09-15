import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest

from PyQt5.QtWidgets import QApplication
from GUI.InputPreparationLocks import acquire, release
from GUI.WorkflowSubmissions import return_to
from GUI.WorkflowViews import WorkflowViews
from tests import test_workflow_navigation as navigation


class PreparationNavigationLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self):
        host = navigation.WorkflowNavigationTests().progression_host('planner')
        self.addCleanup(host.deleteLater)
        return host

    def test_refresh_handoff_unlocks_newly_enabled_grade_review(self):
        host = self.host()
        host.set_page_enabled('grade_reconciliation', False)
        held = acquire(host, readable_results=True)
        return_to(host, 'grade_reconciliation', 'Actuals refreshed; review grades')
        release(host, held)
        self.assertTrue(host.is_page_enabled('grade_reconciliation'))
        self.assertTrue(host.page_widgets['grade_reconciliation'].isEnabled())
        self.assertFalse(host.page_widgets['calendar'].isEnabled())
        self.assertFalse(host._preparation_widget_locks)

    def test_enabling_tab_during_work_does_not_release_its_input_lock(self):
        host = self.host()
        held = acquire(host, readable_results=True)
        host.set_page_enabled('grade_reconciliation', True)
        self.assertFalse(host.page_widgets['grade_reconciliation'].isEnabled())
        release(host, held)
        self.assertTrue(host.page_widgets['grade_reconciliation'].isEnabled())

    def test_new_dependency_stays_disabled_after_nested_jobs_finish(self):
        host = self.host()
        first = acquire(host, readable_results=True)
        second = acquire(host, readable_results=True)
        host.set_page_enabled('grade_reconciliation', False)
        release(host, first)
        self.assertFalse(host.page_widgets['grade_reconciliation'].isEnabled())
        release(host, second)
        self.assertFalse(host.page_widgets['grade_reconciliation'].isEnabled())

    def test_view_readiness_updates_obey_locks_and_latest_desired_state(self):
        host = self.host()
        host.set_page_enabled('destination_progress', False)
        held = acquire(host, readable_results=True)
        views = WorkflowViews(host)
        views.available('destination_progress', True)
        self.assertFalse(host.page_widgets['destination_progress'].isEnabled())
        release(host, held)
        self.assertTrue(host.page_widgets['destination_progress'].isEnabled())


if __name__ == '__main__':
    unittest.main()
