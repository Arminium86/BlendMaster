"""Require downstream resubmission after a preparation dependency sends users back.

Submission validity is separate from cached evidence: returning to a task never
discards approved factors, source data, chunks, or saved reports.
"""
from copy import deepcopy
from functools import wraps
from inspect import signature

SUPPORT_OWNER = {
    'site_model': 'site_configuration', 'guidance_settings': 'guidance_schedules',
    'define_fields': 'grade_reconciliation', 'map_fields': 'grade_reconciliation',
    'data_streams': 'grade_reconciliation', 'solver_configuration': 'decision_levers',
    'multi_feed_setup': 'decision_levers', 'material_flow': 'grade_reconciliation',
    'continuous_assays': 'grade_reconciliation',
}
OPERATIONS = {
    'handle_site_config_submit': 'site_configuration',
    'handle_guidance_schedules_submit': 'guidance_schedules',
    'store_stockpile_table': 'stockpile_inventories',
    'request_manual_grade_reconciliation': 'grade_reconciliation',
    'handle_data_streams_submit': 'grade_reconciliation',
    'store_hex_sequence_table': 'amt_stockpiles',
    'handle_product_targets_submit': 'product_targets',
    'continue_from_destination_progress': 'destination_progress',
    'handle_decision_levers_submit': 'decision_levers',
    'store_calendar_inputs': 'calendar',
    'store_blend_results': 'setup_blends',
    'submit_blend_sequence_table_to_gantt': 'blend_sequence',
    'generate_manual_blend_plan': 'blend_sequence',
}


def order(host, *, include_optional=False):
    from GUI.WorkflowNavigation import WORKSPACE
    from classes.AMTReconciliation import after_chunking, footprints
    pages = list(WORKSPACE)
    if after_chunking(vars(host)):
        pages[3:5] = ['amt_stockpiles', 'grade_reconciliation']
    if not include_optional and not footprints(vars(host)):
        pages.remove('amt_stockpiles')
    if not include_optional and not vars(host).get('file_path_choice'):
        pages.remove('destination_progress')
    return pages


def pending(host):
    required = (vars(host).get('workflow_submission_state') or {}).get('required') or []
    if not required:
        return []
    return [page for page in order(host) if page in required]


def blocked(host, page):
    required = pending(host)
    return page in required[1:]


def persist(host):
    # Submission handlers already save the large site snapshot. Update this
    # small receipt after navigation without copying all evidence a second time.
    state = vars(host)
    site = (state.get('site_scenarios') or {}).get(state.get('active_scenario_id'))
    if isinstance(site, dict):
        site['workflow_submission_state'] = deepcopy(state.get('workflow_submission_state'))


def return_to(host, page, reason, *, navigate=True):
    if page == 'material_flow':
        from GUI.WorkflowActuals import ensure
        if ensure(host):
            return True
        page = 'grade_reconciliation'
    page = SUPPORT_OWNER.get(page, page)
    pages = order(host, include_optional=True)
    if page not in pages:
        return False
    required = pending(host)
    first = min([pages.index(page), *[pages.index(p) for p in required]])
    required = pages[first:]
    previous = vars(host).get('workflow_submission_state') or {}
    revision = int(previous.get('revision') or 0) + (previous.get('required') != required)
    host.workflow_submission_state = {**previous, 'required': required, 'reason': str(reason),
        'return_to': page, 'revision': revision,
        'completed': [p for p in previous.get('completed', []) if p not in required]}
    host._workflow_optimisation_finished = False
    enable = getattr(host, 'set_page_enabled', None)
    if callable(enable):
        for target in required:
            enable(target, target == required[0])
    label = vars(host).get('calendar_workflow_status')
    if label is not None:
        label.setText(str(reason) + '\nSubmit the remaining Workspace tasks in order; saved inputs are retained.')
    persist(host)
    if navigate:
        host.show_page(required[0], force=True)
    return True


def submitted(host, page):
    state = vars(host).get('workflow_submission_state') or {}
    host.workflow_submission_state = {**state, 'completed': list(dict.fromkeys([*state.get('completed', []), page]))}
    host.workflow_submission_state['dirty'] = [p for p in state.get('dirty', []) if p != page]
    required = pending(host)
    if required and required[0] == page:
        host.workflow_submission_state = {**(vars(host).get('workflow_submission_state') or {}),
            'required': [p for p in host.workflow_submission_state['required'] if p != page]}
    persist(host)


def support_submitted(host, page):
    owner = SUPPORT_OWNER.get(page)
    if owner:
        return_to(host, owner, 'Support settings submitted. Resubmit the dependent Workspace tasks.', navigate=False)
    submitted(host, page)


def edited(host, page):
    from GUI.WorkflowNavigation import SUPPORT
    state = vars(host).get('workflow_submission_state') or {}
    if task_status(host, page) != 'ready':
        return
    if page in SUPPORT:
        host.workflow_submission_state = {**state, 'dirty': list(dict.fromkeys([*state.get('dirty', []), page]))}
        persist(host)
    return_to(host, SUPPORT_OWNER.get(page, page), 'Inputs changed. Resubmit this task and the following Workspace tasks.', navigate=False)


def task_status(host, page):
    """Only the next applicable Workspace task is neutral.

    A later submission proves its prerequisites were submitted, including in
    projects saved before individual completion receipts were introduced.
    Explicit invalidations always take precedence over that evidence.
    """
    from GUI.WorkflowNavigation import WORKSPACE, SUPPORT
    pages = order(host)
    if page in WORKSPACE and page not in pages:
        return 'not_required'
    required = pending(host)
    if page in required:
        return 'next' if page == required[0] else 'resubmit'
    state = vars(host).get('workflow_submission_state') or {}
    if page in state.get('dirty', []):
        return 'resubmit'
    if page in state.get('completed', []):
        return 'ready'
    completed = [pages.index(p) for p in state.get('completed', []) if p in pages]
    frontier = max(completed, default=-1)
    if required:
        frontier = max(frontier, pages.index(required[0]) - 1)
    elif not completed:
        # Use saved task gates, not current UI flags (views can enable themselves).
        values = vars(host)
        saved = (values.get('site_scenarios') or {}).get(values.get('active_scenario_id')) or {}
        gates = saved.get('tab_states') or {}
        for task in pages[:pages.index('calendar') + 1]:
            if gates.get(task) is True:
                frontier = max(frontier, pages.index(task) - 1)
        if values.get('updated_stockpile_data'):
            frontier = max(frontier, pages.index('stockpile_inventories'))
    if page in pages:
        index = pages.index(page)
        if index <= frontier:
            return 'ready'
        return 'next' if index == frontier + 1 else 'resubmit'
    if page in SUPPORT:
        owner = SUPPORT_OWNER.get(page)
        if owner and task_status(host, owner) == 'ready':
            return 'ready'
        if page in ('database_reports', 'decision_point', 'reports'):
            return 'ready' if (vars(host).get('_page_enabled_state') or {}).get(page) else 'resubmit'
        return 'resubmit'
    return 'resubmit'


def actuals_required(host, target):
    from GUI.WorkflowNavigation import SUPPORT
    if vars(host).get('_workflow_run_origin') in SUPPORT:
        return False
    if target in ('grade_reconciliation', 'blend_sequence'):
        # Calendar captures edited opening rates before checking this dependency.
        # On a new single-feed model those rates do not exist at grade review yet.
        if target == 'grade_reconciliation' and not vars(host).get('calendar_inputs'):
            return False
        from GUI.WorkflowActuals import ensure
        return ensure(host)
    return False


def can_submit(host, page):
    from GUI.WorkflowNavigation import SUPPORT, page_allowed
    origin = vars(host).get('_workflow_run_origin')
    if origin in SUPPORT and page_allowed(host, origin):
        return True
    required = pending(host)
    if not required:
        return True
    pages = order(host)
    if not required or page not in pages or pages.index(required[0]) >= pages.index(page):
        return True
    from GUI.WorkflowMessages import WorkflowMessageBox
    message = 'Submit ' + required[0].replace('_', ' ').title() + ' and the following Workspace tasks before continuing.'
    return_to(host, required[0], message)
    WorkflowMessageBox.information(host, 'Workspace submission required', message)
    return False


def guard_submissions(cls):
    for name, page in OPERATIONS.items():
        original = getattr(cls, name, None)
        if original is None:
            continue
        def protect(fn, target):
            if len(signature(fn).parameters) == 1:
                @wraps(fn)
                def wrapped(self):
                    if can_submit(self, target):
                        from GUI.SupportSubmission import prepare
                        prepare(self, target)
                        if actuals_required(self, target):
                            return False
                        return fn(self)
                    return False
            else:
                @wraps(fn)
                def wrapped(self, *args, **kwargs):
                    if can_submit(self, target):
                        from GUI.SupportSubmission import prepare
                        prepare(self, target)
                        if actuals_required(self, target):
                            return False
                        return fn(self, *args, **kwargs)
                    return False
            return wrapped
        setattr(cls, name, protect(original, page))
    return cls
