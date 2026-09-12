"""Run file generation away from the desktop event loop."""
from PyQt5.QtWidgets import QMessageBox


def run(host, title, path, work, *, completed=None, failed=None):
    done = completed or (lambda _: QMessageBox.information(host, title, f'Exported to:\n{path}'))
    error = failed or (lambda detail: QMessageBox.warning(host, title, detail['message']))
    if hasattr(host, 'run_background_task') and isinstance(vars(host).get('background_tasks'), list):
        host.run_background_task(title + '…', work, done, error)
    else:
        # Standalone report widgets and non-Qt callers retain a synchronous API.
        try:
            done(work())
        except Exception as exc:
            error({'message': str(exc)})
