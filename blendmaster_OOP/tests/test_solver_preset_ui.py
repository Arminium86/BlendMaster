"""The native Planner dropdown follows restored solver state immediately."""
from copy import deepcopy
import tempfile
import unittest
from unittest.mock import Mock, patch
from PyQt5.QtCore import QCoreApplication, QEvent, QUrl
from PyQt5.QtWidgets import QApplication, QWidget
from GUI.InitialiseGUI import UserInputs
from classes.SolverPresets import make_preset
from database.DatabaseContext import get_database_path, set_database_path


class ChartPlaceholder(QWidget):
    def page(self):
        return Mock()

    def url(self):
        return QUrl()

    def setUrl(self, *args):
        pass

    def setHtml(self, *args):
        pass


class SolverPresetUITests(unittest.TestCase):
    def test_loaded_library_is_available_without_submitting_product_targets(self):
        app = QApplication.instance() or QApplication([])
        previous = get_database_path()
        original_mkdtemp = tempfile.mkdtemp
        with tempfile.TemporaryDirectory() as directory:
            def owned_temp(*args, **kwargs):
                kwargs['dir'] = directory
                return original_mkdtemp(*args, **kwargs)

            with patch('GUI.InitialiseGUI.tempfile.mkdtemp', side_effect=owned_temp), \
                    patch('GUI.InitialiseGUI.CustomWebEngineView', ChartPlaceholder):
                host = UserInputs()
            try:
                host.access_role = 'planner'
                self.assertEqual(host.solver_preset_controls.choice.count(), 1)
                # These fields arrive through Load Project after the controls
                # have already been built with the new-session empty library.
                record = make_preset('Support baseline', host.normalized_solver_config(), vars(host))
                host.solver_presets = [record]
                host.solver_config = deepcopy(record['solver_config'])
                host.calendar_inputs = {'solver_config': deepcopy(record['solver_config'])}
                host.load_solver_config_inputs()
                choice = host.solver_preset_controls.choice
                self.assertEqual(choice.itemText(1), 'Support baseline')
                self.assertEqual(choice.itemData(1), 'Support baseline')
                self.assertEqual(choice.currentText(), 'Support baseline')
                # Loading another project must also remove the prior library.
                host.solver_presets = []
                host.load_solver_config_inputs()
                self.assertEqual(choice.count(), 1)
            finally:
                host.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                set_database_path(previous)
