"""Worker lifetime, database scope and GUI-thread result delivery in one place."""
import traceback
from PyQt5 import sip
from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from database.DatabaseContext import database_scope, get_database_path


class Worker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, work, database):
        super().__init__()
        self.work, self.database = work, database

    @pyqtSlot()
    def run(self):
        try:
            with database_scope(self.database):
                self.finished.emit(self.work())
        except Exception as exc:
            self.failed.emit(dict(title=getattr(exc, 'title', 'Error'),
                message=getattr(exc, 'user_message', traceback.format_exc())))


class Delivery(QObject):
    """An explicit QObject receiver guarantees queued callbacks on the UI thread."""
    def __init__(self, host, success, failure, progress):
        super().__init__(host)
        self.host, self.success, self.failure, self.progress = host, success, failure, progress
        self.context = (get_database_path(), getattr(host, 'active_scenario_id', None))

    def current(self):
        return not sip.isdeleted(self.host) and self.context == (
            get_database_path(), getattr(self.host, 'active_scenario_id', None))

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


def run(host, message, work, success, failure=None, cancel_callback=None, show_progress=True):
    if show_progress:
        host._background_input_locks = vars(host).get('_background_input_locks', 0) + 1
        for name in ('tabs', 'scenario_toolbar'):
            widget = vars(host).get(name)
            if widget is not None:
                widget.setEnabled(False)
        host.show_progress_dialog(message, cancel_callback)
    thread = QThread(host)
    worker = Worker(work, get_database_path())
    delivery = Delivery(host, success, failure, show_progress)
    worker.moveToThread(thread)
    task = (thread, worker)
    host.background_tasks.append(task)
    def clean():
        if task in host.background_tasks:
            host.background_tasks.remove(task)
        if show_progress:
            host._background_input_locks = max(0, vars(host).get('_background_input_locks', 1) - 1)
            controller = vars(host).get('site_workflow_controller')
            if not host._background_input_locks and not (controller and controller.active):
                for name in ('tabs', 'scenario_toolbar'):
                    widget = vars(host).get(name)
                    if widget is not None:
                        widget.setEnabled(True)
        delivery.deleteLater()
    thread.started.connect(worker.run)
    worker.finished.connect(delivery.completed)
    worker.failed.connect(delivery.failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(clean)
    thread.finished.connect(thread.deleteLater)
    thread.start()
