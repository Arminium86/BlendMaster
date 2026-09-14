"""Worker lifetime, database scope and GUI-thread result delivery in one place."""
import traceback
from PyQt5 import sip
from PyQt5.QtCore import QObject, QThread, pyqtSlot
from database.DatabaseContext import database_scope, get_database_path


class Worker(QThread):
    """Run plain work; keep every Qt object owned and destroyed by the UI thread.

    Deleting a moved QObject in its worker thread can hold Qt's connection
    mutex while SIP waits for the GIL. A result callback constructing the next
    progress dialog can then deadlock holding the GIL and waiting for that mutex.
    QThread itself retains the creating thread's affinity, so its normal finished
    signal lets the UI deliver the result and dispose of it after run() returns.
    """
    def __init__(self, host, work, database):
        super().__init__(host)
        self.work, self.database = work, database
        self.value, self.error = None, None

    @pyqtSlot()
    def run(self):
        try:
            with database_scope(self.database):
                self.value = self.work()
        except Exception as exc:
            self.error = dict(title=getattr(exc, 'title', 'Error'),
                message=getattr(exc, 'user_message', traceback.format_exc()))


class Delivery(QObject):
    """An explicit QObject receiver guarantees queued callbacks on the UI thread."""
    def __init__(self, host, worker, success, failure, progress):
        super().__init__(host)
        self.worker = worker
        self.host, self.success, self.failure, self.progress = host, success, failure, progress
        self.context = (get_database_path(), getattr(host, 'active_scenario_id', None))

    def current(self):
        return not sip.isdeleted(self.host) and self.context == (
            get_database_path(), getattr(self.host, 'active_scenario_id', None))

    @pyqtSlot()
    def finished(self):
        if self.worker.error is not None:
            self.failed(self.worker.error)
        else:
            self.completed(self.worker.value)

    @pyqtSlot(object)
    def completed(self, value):
        if not self.current():
            return
        if self.progress:
            self.host.close_progress_dialog()
        try:
            self.success(value)
        except Exception:
            self.failed(dict(title='Result could not be applied', message=traceback.format_exc()))

    @pyqtSlot(object)
    def failed(self, error):
        if not self.current():
            return
        if self.progress:
            self.host.close_progress_dialog()
        controller = vars(self.host).get('site_workflow_controller')
        workflow_active = bool(controller and controller.active)
        if workflow_active:
            controller.fail(error)
        if self.failure:
            self.failure(error)
        elif not workflow_active:
            self.host.show_error_popup(error)


def run(host, message, work, success, failure=None, cancel_callback=None, show_progress=True,
        *, readable_results=False):
    held = []
    if show_progress:
        from GUI.InputPreparationLocks import acquire
        host._background_input_locks = vars(host).get('_background_input_locks', 0) + 1
        held = acquire(host, readable_results)
        host.show_progress_dialog(message, cancel_callback)
    thread = Worker(host, work, get_database_path())
    delivery = Delivery(host, thread, success, failure, show_progress)
    task = (thread, delivery)
    host.background_tasks.append(task)
    def clean():
        if task in host.background_tasks:
            host.background_tasks.remove(task)
        if show_progress:
            from GUI.InputPreparationLocks import release
            host._background_input_locks = max(0, vars(host).get('_background_input_locks', 1) - 1)
            release(host, held)
        delivery.deleteLater()
    thread.finished.connect(delivery.finished)
    thread.finished.connect(clean)
    thread.finished.connect(thread.deleteLater)
    thread.start()
