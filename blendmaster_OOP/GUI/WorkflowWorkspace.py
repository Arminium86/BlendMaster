"""Compose the role-specific workspace from the existing planning controls."""
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QFrame, QScrollArea, QLabel, QPushButton, QTabWidget, QDialog, QTableView)
from classes.SiteWorkflow import require_action


def form_page(host, page_id, caption):
    page = QScrollArea()
    page.setWidgetResizable(True)
    content = QWidget()
    form = QFormLayout(content)
    form.setContentsMargins(18, 16, 18, 18)
    form.setVerticalSpacing(12)
    page.setWidget(content)
    host.register_page(page_id, host.setup_tabs, page, caption)
    return form


def move_rows(source, target, labels):
    """Take layout items without deleting their widgets, signals or state."""
    for index in reversed(range(source.rowCount())):
        label = source.itemAt(index, QFormLayout.LabelRole)
        widget = label.widget() if label else None
        if isinstance(widget, QLabel) and widget.text().strip() in labels:
            taken = source.takeRow(index)
            field = taken.fieldItem
            target.insertRow(0, widget, field.widget() or field.layout())


def hide_rows(source, labels):
    for index in range(source.rowCount()):
        item = source.itemAt(index, QFormLayout.LabelRole)
        if item and isinstance(item.widget(), QLabel) and item.widget().text() in labels:
            set_row_visible(source, index, False)


def set_row_visible(form, index, visible):
    def visit(item):
        if not item:
            return
        if item.widget():
            item.widget().setVisible(visible)
        elif item.layout():
            for child in range(item.layout().count()):
                visit(item.layout().itemAt(child))
    for role in (QFormLayout.LabelRole, QFormLayout.FieldRole):
        visit(form.itemAt(index, role))


def install(host):
    site_form = host.site_config_tab.findChild(QFrame, 'siteConfigCard').layout()
    model_form = form_page(host, 'site_model', 'Site Model Settings')
    move_rows(site_form, model_form, {'Hub:', 'Mine:', 'Plan Mode:', 'OPF:',
        'Operating Crusher:', 'Product Brands:', 'Product Build Targets:'})
    hide_rows(site_form, {'Optimised Blend Choices:', 'PoC Agent:'})
    host.blend_mode.setCurrentIndex(0)
    host.blend_mode_choice = 1
    host.site_model_context_label = QLabel()
    host.site_model_context_label.setWordWrap(True)
    site_form.insertRow(2, host.site_model_context_label)
    help_label = QLabel('Configure and submit the site model before planner handoff. '
                        'Planners select a configured site using the toolbar above.')
    help_label.setWordWrap(True)
    model_form.addRow(help_label)
    submit = QPushButton('Submit Site Model')
    submit.clicked.connect(host.handle_site_config_submit)
    model_form.addRow(submit)
    planner = host.access_role == 'planner'
    host.add_scenario_button.setVisible(not planner)
    host.remove_scenario_button.setVisible(not planner)
    role_label = QLabel('Role: ' + host.access_role.title())
    host.scenario_toolbar.layout().insertWidget(0, role_label)

    guidance_form = host.guidance_schedules_tab.findChild(QFrame, 'guidanceSchedulesCard').layout()
    guidance_support = form_page(host, 'guidance_settings', 'Guidance Settings')
    move_rows(guidance_form, guidance_support, {'Crusher Contribution:',
        'Select All Product Crushers in Haul Infinity:', 'Expit Transactions (optional):',
        'Grade Block Completion Tolerance:', 'Expit Sequence Refresh Tolerance:',
        '2WP Direct Tip:', 'Select All Tipping Point Crushers in Haul Infinity:',
        'Select Planned Tipping Point Crusher(s) in this Scenario:'})
    host._guidance_support_form = guidance_support
    submit = QPushButton('Submit Guidance Settings')
    submit.clicked.connect(host.handle_guidance_schedules_submit)
    guidance_support.addRow(submit)
    host.guidance_change_label = QLabel('Imports retain matching selections and movement rules.')
    host.guidance_change_label.setWordWrap(True)
    guidance_form.addRow(host.guidance_change_label)

    # Reparent the reconciliation section intact; signal handlers and table
    # references remain shared with the technical Data Streams controls.
    page = QScrollArea()
    page.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    for widget in host.grade_reconciliation_widgets:
        layout.addWidget(widget)
    buttons = QHBoxLayout()
    buttons.addWidget(host.refresh_data_streams_button)
    buttons.addWidget(host.data_streams_submit_button)
    buttons.addStretch()
    layout.addLayout(buttons)
    layout.addStretch()
    page.setWidget(content)
    host.register_page('grade_reconciliation', host.workspace_tabs, page, 'Grade Reconciliation')
    technical_submit = QPushButton('Submit Data Stream Settings')
    technical_submit.clicked.connect(host.handle_data_streams_submit)
    host.data_streams_tab.layout().addWidget(technical_submit)

    # Split the former Reports tab into its actual workflow destinations.
    database_page = host.reports_child_tabs.widget(0)
    pages = [('database_reports', database_page, 'Database Reports'),
             ('material_destination_plan', host.material_destination_plan_view, 'Material Destination Plan'),
             ('material_flow_results', host.material_flow_results, 'Material Flow')]
    for key, widget, caption in pages:
        host.reports_child_tabs.removeTab(host.reports_child_tabs.indexOf(widget))
        host.register_page(key, host.results_tabs, widget, caption)
    plan_tabs = QTabWidget()
    for widget, caption in ((host.operational_blend_plans, 'Optimised Plan'),
                            (host.blend_plan_page, 'Manual Plan')):
        host.reports_child_tabs.removeTab(host.reports_child_tabs.indexOf(widget))
        plan_tabs.addTab(widget, caption)
    plan_page = QWidget()
    plan_layout = QVBoxLayout(plan_page)
    host.blend_plan_mode_label = QLabel()
    plan_layout.addWidget(host.blend_plan_mode_label)
    host.plan_readiness_label = QLabel('Run completion and plan readiness are checked separately.')
    host.plan_readiness_label.setWordWrap(True)
    plan_layout.addWidget(host.plan_readiness_label)
    plan_layout.addWidget(plan_tabs)
    host.blend_plan_workflow_tabs = plan_tabs
    host.register_page('blend_plan', host.workspace_tabs, plan_page, 'Blend Plan')
    plan_tabs.currentChanged.connect(lambda: enter_page(host, 'blend_plan'))
    tabs, index = host.page_locations['reports']
    tabs.setTabVisible(index, False)
    # Old saved page IDs are mapped to these pages by WorkflowNavigation.
    host._workflow_page_enter = lambda page_id: enter_page(host, page_id)
    host._workflow_context_refresh = lambda: refresh_context(host)
    for old, new in (('data_streams', 'grade_reconciliation'), ('reports', 'blend_plan'),
                     ('reports', 'material_destination_plan'), ('reports', 'material_flow_results')):
        host.set_page_enabled(new, host._page_enabled_state.get(old, False))
    host.calendar_workflow_status = QLabel('Ready for Calendar submission.')
    host.calendar_workflow_status.setWordWrap(True)
    host.page_widgets['calendar'].layout().insertWidget(0, host.calendar_workflow_status)
    review = QPushButton('Review Refresh Changes')
    review.clicked.connect(lambda: show_refresh_changes(host))
    host.product_build_layout.insertWidget(1, review)
    refresh_context(host)


def show_refresh_changes(host):
    import pandas as pd
    from GUI.MaterialFlowResults import FrameModel
    dialog = QDialog(host)
    dialog.setWindowTitle('Product target refresh changes')
    dialog.resize(1050, 500)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel('Policies and manual overrides are retained. Unedited imported values may refresh.'))
    table = QTableView()
    table.setModel(FrameModel(pd.DataFrame(getattr(host, 'target_refresh_changes', None) or []), table))
    table.resizeColumnsToContents()
    layout.addWidget(table)
    dialog.exec_()


def refresh_context(host):
    host.blend_mode.setCurrentIndex(0)
    host.blend_mode_choice = 1
    state = {key: getattr(host, key, '') for key in
             ('hub_input_choice', 'mine_input_choice', 'opf_input_choice', 'crusher_input_choice',
              'multi_feed_configuration')}
    host.site_model_context_label.setText('Configured site: ' + host.scenario_display_name(state))
    mode = (state.get('multi_feed_configuration') or {}).get('mode', 'single')
    caption = {'single': 'Single tipping point', 'multi_tipping_point': 'Multiple tipping points',
               'combined_opf': 'Combined OPF'}.get(mode, mode)
    host.blend_plan_mode_label.setText('Blend Plan · ' + caption)
    form = host._guidance_support_form
    for index in range(form.rowCount()):
        label = form.itemAt(index, QFormLayout.LabelRole)
        if label and isinstance(label.widget(), QLabel) and label.widget().text() == 'Crusher Contribution:':
            set_row_visible(form, index, mode == 'single')
    changes = getattr(host, 'guidance_import_audit', [])
    if changes:
        host.guidance_change_label.setText(str(changes[-1].get('message') or
            'Last import validated. Matching selections and movement rules retained.'))


def enter_page(host, page_id):
    if getattr(host, 'scenario_switch_in_progress', False):
        return True
    controller = vars(host).get('site_workflow_controller')
    if controller and controller.active:
        return True
    refresh_context(host)
    if page_id == 'blend_plan':
        if host.blend_plan_workflow_tabs.currentWidget() is host.operational_blend_plans:
            QTimer.singleShot(0, host.operational_blend_plans.refresh)
        else:
            QTimer.singleShot(0, host.refresh_manual_blend_plan_report)
            if getattr(host, 'start_time_choice', None):
                from GUI.PlanReadiness import refresh
                QTimer.singleShot(0, lambda: refresh(host, plan_type='manual',
                    plan_id=getattr(host, 'active_manual_plan_id', None) or 'Primary'))
    elif page_id == 'material_flow_results':
        QTimer.singleShot(0, host.material_flow_results.refresh)
    elif page_id == 'material_destination_plan':
        QTimer.singleShot(0, host.refresh_material_destination_plan_view)
    elif page_id == 'database_reports':
        QTimer.singleShot(0, host.refresh_sqlite_reports)
    elif page_id == 'grade_reconciliation':
        return True  # Refresh is explicit and reports the evidence freshness.
    elif page_id == 'site_automation':
        host.site_automation_panel.refresh()
    else:
        return False
    return True
