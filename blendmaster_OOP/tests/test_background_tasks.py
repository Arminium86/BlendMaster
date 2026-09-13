"""Exercise real Qt worker completion, chained progress windows and disposal."""
import time
import unittest
from PyQt5.QtCore import QCoreApplication, QEvent, QThread, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget
from GUI.BackgroundTasks import run
from GUI.InitialiseGUI import UserInputs


class Host(QMainWindow):
    show_progress_dialog = UserInputs.show_progress_dialog
    close_progress_dialog = UserInputs.close_progress_dialog
    resize_progress_dialog_for_message = UserInputs.resize_progress_dialog_for_message

    def __init__(self):
        super().__init__()
        self.background_tasks = []
        self.progress_dialog = None
        self.tabs, self.scenario_toolbar = QWidget(self), QWidget(self)
        self.active_scenario_id = 'test'
        self.errors = []

    def show_error_popup(self, error):
        self.errors.append(error)


class BackgroundTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def drain(self, host):
        deadline = time.monotonic() + 15
        while host.background_tasks and time.monotonic() < deadline:
            QTest.qWait(1)
        self.assertFalse(host.background_tasks, 'Background work did not finish')
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_chained_jobs_dispose_qt_objects_on_ui_thread(self):
        host = Host()
        ui = int(QThread.currentThreadId())
        worked, delivered, disposed = [], [], []

        def start(index):
            def work():
                worked.append(int(QThread.currentThreadId()))
                return index

            def done(value):
                delivered.append((value, int(QThread.currentThreadId())))
                if value < 99:
                    start(value + 1)
                    self.assertFalse(host.tabs.isEnabled())

            run(host, f'Preparing step {index}', work, done)
            for obj in host.background_tasks[-1]:
                obj.destroyed.connect(lambda: disposed.append(int(QThread.currentThreadId())), Qt.DirectConnection)

        start(0)
        self.drain(host)
        self.assertEqual(delivered, [(i, ui) for i in range(100)])
        self.assertTrue(all(thread != ui for thread in worked))
        self.assertEqual(disposed, [ui] * 200)
        self.assertEqual(host.errors, [])
        self.assertEqual(host._background_input_locks, 0)
        self.assertTrue(host.tabs.isEnabled())
        host.deleteLater()

    def test_worker_and_callback_failures_release_controls(self):
        for fail_in_work in (True, False):
            host = Host()

            def fail():
                raise ValueError('deliberate failure')

            run(host, 'Preparing inputs', fail if fail_in_work else lambda: 1,
                lambda value: fail())
            self.drain(host)
            self.assertEqual(len(host.errors), 1)
            self.assertIn('deliberate failure', host.errors[0]['message'])
            self.assertTrue(host.tabs.isEnabled())
            host.deleteLater()

    def test_changed_site_does_not_receive_old_result(self):
        host = Host()
        delivered = []
        run(host, 'Background read', lambda: 42, delivered.append, show_progress=False)
        host.active_scenario_id = 'other'
        self.drain(host)
        self.assertEqual(delivered, [])
        host.deleteLater()
