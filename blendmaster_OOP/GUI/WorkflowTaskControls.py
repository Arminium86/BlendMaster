"""One Run action for the selected task, with persistent submission receipts."""
from contextlib import contextmanager
from functools import wraps

from PyQt5.QtCore import QObject, QTimer, Qt
from PyQt5.QtWidgets import (QPushButton, QWidget, QHBoxLayout, QLabel, QToolButton, QMenu,
                            QLineEdit, QComboBox, QAbstractSpinBox, QCheckBox, QTableWidget, QPlainTextEdit)

from GUI.WorkflowNavigation import WORKSPACE, SUPPORT, page_allowed
from GUI.WorkflowSubmissions import task_status


class WorkflowTaskControls(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.running = False
        self.bound_context = None
        self.bar = QWidget(host.tabs)
        layout = QHBoxLayout(self.bar)
        layout.setContentsMargins(8, 0, 8, 0)
        self.label = QLabel('Green: ready · Red: resubmit', self.bar)
        self.run = QPushButton('Run', self.bar)
        self.run.setToolTip('Submit or continue the active task')
        layout.addWidget(self.label)
        self.load = QToolButton(self.bar)
        self.load.setText('Load views')
        self.load.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.load)
        for title, key in (('Database View', 'database'), ('Expit Sequence', 'expit'), ('OPF Production Report', 'production')):
            menu.addAction(title, lambda checked=False, name=key: self.load_view(name))
        self.load.setMenu(menu)
        layout.addWidget(self.load)
        layout.addWidget(self.run)
        host.tabs.setCornerWidget(self.bar, Qt.TopRightCorner)
        self.run.clicked.connect(self.execute)
        self.wrap_background()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def load_view(self, name):
        host = self.host
        if name == 'production':
            host.sync_opf_production_report_context()
            host.opf_production_report.request_refresh()
        elif name == 'expit':
            host.refresh_expit_sequence_live()
        else:
            host.refresh_database_view()

    def bind_edits(self, page):
        from GUI.PlannerPresentation import page_for
        widget = self.host.page_widgets.get(page)
        if widget is None:
            return
        for field in widget.findChildren(QWidget):
            if field.property('workflowEditConnected') or page_for(self.host, field) != page:
                continue
            signal = None
            if isinstance(field, QLineEdit):
                signal = field.textEdited
            elif isinstance(field, QComboBox):
                signal = field.activated
            elif isinstance(field, QCheckBox):
                signal = field.clicked
            elif isinstance(field, QTableWidget):
                signal = field.itemChanged
            elif isinstance(field, QPlainTextEdit) and not field.isReadOnly():
                signal = field.textChanged
            elif isinstance(field, QAbstractSpinBox):
                signal = getattr(field, 'valueChanged', None) or getattr(field, 'dateTimeChanged', None)
            if signal is None:
                continue
            field.setProperty('workflowEditConnected', True)
            def edit(*_, target=page, control=field):
                state = vars(self.host)
                focus = self.host.focusWidget()
                user_edit = control.hasFocus() or (focus is not None and control.isAncestorOf(focus))
                if user_edit and not (self.running or state.get('_workflow_run_origin')
                        or state.get('project_load_restore_in_progress') or state.get('scenario_switch_in_progress')
                        or state.get('_preparation_widget_locks')):
                    from GUI.WorkflowSubmissions import edited
                    edited(self.host, target)
            signal.connect(edit)

    def current_page(self):
        return self.host._workflow_views.current_page()

    def buttons(self, page):
        """Use the actual page buttons so validation and enabled state stay authoritative."""
        from GUI.PlannerPresentation import page_for
        widget = self.host.page_widgets.get(page)
        if widget is None:
            return []
        result = []
        for button in widget.findChildren(QPushButton):
            text = button.text().replace('&', '').strip().lower()
            primary = text == 'submit' or text.startswith(('submit ', 'continue ')) or (page == 'site_automation' and text == 'save contract')
            if primary and page_for(self.host, button) == page:
                # Ignore controls inside an inactive nested mode tab.
                if button.parentWidget() is widget or button.parentWidget().isVisibleTo(widget):
                    result.append(button)
                button.setProperty('workflowPrimaryAction', True)
                button.hide()
        return result

    def refresh(self):
        host = self.host
        for page in (*WORKSPACE, *SUPPORT):
            location = host.page_locations.get(page)
            if location is None:
                continue
            tabs, index = location
            status = task_status(host, page)
            if page in ('database_reports', 'decision_point', 'reports'):
                status = 'ready' if host.is_page_enabled(page) else 'next'
            tabs.tabBar().setTabData(index, status)
            tabs.setTabToolTip(index, {'ready': 'Ready — submitted', 'resubmit': 'Needs resubmission',
                                      'next': 'Run this task when its inputs are ready'}[status])
            tabs.tabBar().update()
        page = self.current_page()
        buttons = self.buttons(page) if page in (*WORKSPACE, *SUPPORT) else []
        context = (page, tuple(id(button) for button in buttons))
        if context != self.bound_context:
            self.bound_context = context
            if page in (*WORKSPACE, *SUPPORT):
                self.bind_edits(page)
        state = vars(host)
        controller = state.get('site_workflow_controller')
        update = state.get('refresh_data_streams_button') if page == 'grade_reconciliation' else None
        can_update = update is not None and update.isEnabled()
        busy = (self.running or state.get('_preparation_widget_locks') or state.get('_support_actuals_pending')
                or state.get('project_load_restore_in_progress') or state.get('scenario_switch_in_progress')
                or (controller and controller.active))
        enabled = (page_allowed(host, page) and host.is_page_enabled(page)
                   and (any(b.isEnabled() for b in buttons) or can_update or page == 'material_destination_plan') and not busy)
        self.run.setEnabled(bool(enabled))
        self.load.setVisible(host.tabs.currentWidget() is host.results_navigation_page)
        self.load.setEnabled(not bool(busy) and bool(state.get('mine_input_choice') and state.get('start_time_choice')))
        host._workflow_views.apply_input_results()
        self.run.setToolTip('Run ' + str(page or '').replace('_', ' ').title() if enabled
                            else 'Select an available task with a submission or continuation action.')

    @contextmanager
    def origin(self, page):
        state = vars(self.host)
        previous = state.get('_workflow_run_origin')
        state['_workflow_run_origin'] = page
        try:
            yield
        finally:
            state['_workflow_run_origin'] = previous

    def wrap_background(self):
        """Retain the submission origin across asynchronous completion callbacks."""
        original = self.host.run_background_task
        @wraps(original)
        def launch(message, work_fn, on_success, on_error=None, *args, **kwargs):
            page = vars(self.host).get('_workflow_run_origin')
            def scoped(callback):
                if callback is None:
                    return None
                @wraps(callback)
                def complete(*values, **options):
                    with self.origin(page):
                        return callback(*values, **options)
                return complete
            return original(message, work_fn, scoped(on_success), scoped(on_error), *args, **kwargs)
        self.host.run_background_task = launch

    def execute(self):
        self.refresh()
        if not self.run.isEnabled():
            return
        page = self.current_page()
        if page == 'material_destination_plan':
            self.host.advance_workspace(page)
            self.refresh()
            return
        button = next((b for b in self.buttons(page) if b.isEnabled()), None)
        action = button.click if button is not None else (
            self.host.request_manual_grade_reconciliation if page == 'grade_reconciliation' else None)
        if action is None:
            return
        self.running = True
        try:
            with self.origin(page):
                action()
        finally:
            self.running = False
            self.refresh()
