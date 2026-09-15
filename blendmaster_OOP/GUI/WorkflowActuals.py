"""Refresh operational evidence using saved Support settings for either role."""
from copy import deepcopy
from classes.DestinationBuildOrder import digest
from setup.TransportOpeningHistory import TransportOpeningHistory
from GUI.WorkflowMessages import WorkflowMessageBox
from database.DatabaseContext import get_database_path


def ensure(host, *, force=False):
    """Return True when the submission must wait; never edit Support controls."""
    state = vars(host)
    database = get_database_path()
    if state.get('_support_actuals_pending'):
        return True
    settings = state.get('transport_settings') or {}
    if not any(row.get('enabled') for row in settings.get('tipping_points', {}).values()):
        return False
    from GUI.MaterialFlowIntegration import points_for_gui
    service = TransportOpeningHistory()
    try:
        points = deepcopy(points_for_gui(host))
        settings = deepcopy(settings)
        mine, start, site = state.get('mine_input_choice'), state.get('start_time_choice'), state.get('active_scenario_id')
        request = service.request(mine, start, points, settings)
    except (ValueError, TypeError) as exc:
        WorkflowMessageBox.information(host, 'Support configuration required',
            'Conveyors & COS settings need correction by Support before this task can run.\n' + str(exc))
        return True
    cached = state.get('transport_opening_history') or {}
    if not force and cached.get('request') == request and cached.get('data_signature') == digest(cached.get('records', [])):
        return False
    state['_support_actuals_pending'] = True
    def failed(error):
        state['_support_actuals_pending'] = False
        WorkflowMessageBox.information(host, 'Actuals refresh did not complete',
            'Previous actuals were retained. Run the task again to retry.\n' + str(error))
    def done(result):
        try:
            current = service.request(state.get('mine_input_choice'), state.get('start_time_choice'),
                                      points_for_gui(host), state.get('transport_settings') or {})
        except (ValueError, TypeError):
            current = None
        if current != request or site != state.get('active_scenario_id') or database != get_database_path():
            failed('The site or transport request changed while loading.')
            return
        state['_support_actuals_pending'] = False
        state['transport_opening_history'] = result
        from GUI.WorkflowSubmissions import return_to
        return_to(host, 'grade_reconciliation', 'Opening conveyor/COS actuals refreshed. Review and submit Grade Reconciliation, then resubmit the following Workspace tasks.')
        save = state.get('save_active_scenario_state') or getattr(host, 'save_active_scenario_state', None)
        if callable(save):
            save()
        WorkflowMessageBox.information(host, 'Actuals refreshed',
            'Opening conveyor/COS actuals have been refreshed using the saved Support settings.\n'
            'Review and run Grade Reconciliation, then continue through the highlighted Workspace tasks.')
    try:
        host.run_background_task('Refreshing opening conveyor/COS actuals…',
            lambda: service.fetch(mine, start, points, settings), done, failed, readable_results=True)
    except Exception:
        state['_support_actuals_pending'] = False
        raise
    return True
