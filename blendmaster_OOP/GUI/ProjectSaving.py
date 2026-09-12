"""Capture controls on the UI thread; snapshot and write projects in a worker."""
from datetime import datetime
from pathlib import Path
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox
from classes.WorkflowCheckpoint import write_checkpoint


def begin(host, legacy_state, *, show_success=True, close_after=False):
    # capture_scenario_state has already copied editable controls. Accepted
    # evidence is replaced as a whole, and the worker locks the input widgets.
    scenarios = dict(host.site_scenarios)
    site = host.active_scenario_id
    destination = Path.cwd() / f'blendmaster_{datetime.now():%Y%m%d_%H%M%S_%f}.prj'
    host._project_save_pending = True

    def completed(path):
        host._project_save_pending = False
        host.last_project_save_path = path
        if close_after:
            host._project_close_saved = True
            QTimer.singleShot(0, host.close)
        elif show_success:
            QMessageBox.information(host, 'BlendMaster', f'Project saved successfully:\n{path}')

    def failed(error):
        host._project_save_pending = False
        host._project_close_saved = False
        # A failed close-time save leaves the application and databases intact.
        QMessageBox.critical(host, 'Project was not saved', error['message'])

    host.run_background_task('Saving project and site databases…',
        lambda: write_checkpoint(scenarios, site, destination, host.snapshot_database,
                                 legacy_state=legacy_state), completed, failed)
    return True
