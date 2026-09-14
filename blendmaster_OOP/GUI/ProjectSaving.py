"""Capture controls on the UI thread; snapshot and write projects in a worker."""
from pathlib import Path
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QDialog, QFileDialog, QMessageBox
from classes.WorkflowCheckpoint import write_checkpoint
from classes.SharedProjects import settings, filename


def set_enabled(host, enabled):
    for name in ('save_button', 'save_as_button'):
        button = vars(host).get(name)
        if button is not None:
            button.setEnabled(enabled)


def default_destination(host):
    current = vars(host).get('current_project_path')
    if current:
        return Path(current)
    shared = settings(vars(host).get('shared_project_settings'))
    display_name = getattr(host, 'scenario_display_name', None)
    state = host.site_scenarios[host.active_scenario_id]
    name = shared['model_name'] or (display_name(state) if display_name else 'BlendMaster')
    return Path.cwd() / filename(name)


def restore_destination(host, source, shared_settings=None):
    """Bind local working copies to their file; published inputs keep local saves."""
    path = Path(str(source or ''))
    shared_folder = Path(settings(shared_settings)['folder']).resolve()
    host.current_project_path = (
        str(path.resolve()) if path.suffix.lower() == '.prj' and path.is_file()
        and path.resolve().parent != shared_folder else None
    )
    host.last_project_save_path = None


def choose_destination(host):
    dialog = QFileDialog(host, 'Save Project As', str(default_destination(host)),
                         'Project Files (*.prj)')
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setAcceptMode(QFileDialog.AcceptSave)
    dialog.setFileMode(QFileDialog.AnyFile)
    dialog.setDefaultSuffix('prj')
    if dialog.exec_() != QDialog.Accepted:
        return None
    selected = Path(dialog.selectedFiles()[0])
    destination = selected if selected.suffix.lower() == '.prj' else Path(str(selected) + '.prj')
    # Qt confirms replacement of its selected filename. If a different suffix
    # was typed, confirm the final .prj target as well before starting the write.
    if destination != selected and destination.exists():
        if QMessageBox.question(host, 'Replace Project?',
                f'{destination}\n\nThis project already exists. Replace it?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return None
    return destination


def begin(host, legacy_state, *, show_success=True, close_after=False, destination=None):
    # capture_scenario_state has already copied editable controls. Accepted
    # evidence is replaced as a whole, and the worker locks the input widgets.
    scenarios = dict(host.site_scenarios)
    site = host.active_scenario_id
    shared = settings(vars(host).get('shared_project_settings'))
    destination = Path(destination) if destination is not None else default_destination(host)
    host._project_save_pending = True

    def completed(path):
        host._project_save_pending = False
        host.last_project_save_path = path
        host.current_project_path = path
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
                                 legacy_state=legacy_state, metadata={
                                     'shared_project_settings': shared,
                                     'shared_project_subscription': (vars(host).get('shared_project_subscription') or {})
                                         if vars(host).get('access_role') == 'planner' else {}}), completed, failed)
    return True
