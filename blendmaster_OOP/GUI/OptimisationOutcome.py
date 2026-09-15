"""Keep an empty optimisation on Calendar instead of submitting a blank plan."""
from PyQt5.QtWidgets import QMessageBox


def no_plan(host):
    message = ('No optimised blend sequence was generated.\n\n' +
               str(host.last_run_outcome.get('message') or 'No feasible steady state was solved.') +
               '\n\nCalendar still needs submission. Material Flow contains only the opening state.')
    host.last_run_outcome = {**host.last_run_outcome, 'status': 'failed', 'title': 'No optimised plan'}
    host.optimisation_reuse_receipt = None
    vars(host).pop('_pending_optimisation_signature', None)
    from GUI.WorkflowSubmissions import return_to
    return_to(host, 'calendar', message)
    # Completion is separate from success; automation must stop with failure.
    host._workflow_optimisation_finished = True
    for key in ('calendar_workflow_status', 'decision_status_label'):
        label = vars(host).get(key)
        if label is not None:
            label.setText(message)
    host.refresh_sqlite_reports()
    host.refresh_optimisation_plan_selectors()
    host.save_active_scenario_state()
    controller = vars(host).get('site_workflow_controller')
    if controller and controller.active:
        controller.fail(message)
    if vars(host).get('project_load_continuation_pending'):
        host.project_load_continuation_pending = False
        host.finish_project_load_ui(success=False)
    QMessageBox.information(host, 'No optimised plan', message)
