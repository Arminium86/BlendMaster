import sys
import unittest
from unittest.mock import patch
from PyQt5.QtWidgets import QApplication, QPushButton
from GUI.WorkflowPermissions import protect_support_operations


@protect_support_operations
class Session:
    def __init__(self, role):
        self.access_role, self.calls = role, []

    def submit_multi_feed_setup(self):
        self.calls.append('submitted')

    def apply_agent_site_configuration_payload(self, site_config):
        self.calls.append(site_config)


class WorkflowPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_qt_click_keeps_no_argument_handler_signature(self):
        host, errors = Session('support'), []
        button = QPushButton('Submit')
        button.clicked.connect(host.submit_multi_feed_setup)
        with patch.object(sys, 'excepthook', lambda *error: errors.append(error)):
            button.click()
        self.assertEqual(errors, [])
        self.assertEqual(host.calls, ['submitted'])

    def test_payload_is_forwarded_and_role_checks_still_apply(self):
        support, planner = Session('support'), Session('planner')
        payload = {'site': 'CC'}
        support.apply_agent_site_configuration_payload(payload)
        self.assertIs(support.calls[0], payload)
        with self.assertRaises(PermissionError):
            planner.submit_multi_feed_setup()
        with self.assertRaises(PermissionError):
            planner.apply_agent_site_configuration_payload(payload)
        self.assertEqual(planner.calls, [])
