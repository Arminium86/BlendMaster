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
    'multi_feed_setup': 'decision_levers', 'material_flow': 'decision_levers',
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


def return_to(host, page, reason):
    page = SUPPORT_OWNER.get(page, page)
    pages = order(host, include_optional=True)
    if page not in pages:
        return False
    required = pending(host)
    first = min([pages.index(page), *[pages.index(p) for p in required]])
    required = pages[first:]
    previous = vars(host).get('workflow_submission_state') or {}
    revision = int(previous.get('revision') or 0) + (previous.get('required') != required)
    host.workflow_submission_state = dict(required=required, reason=str(reason), return_to=page, revision=revision)
    host._workflow_optimisation_finished = False
    for target in required:
        host.set_page_enabled(target, target == required[0])
    label = vars(host).get('calendar_workflow_status')
    if label is not None:
        label.setText(str(reason) + '\nSubmit the remaining Workspace tasks in order; saved inputs are retained.')
    persist(host)
    host.show_page(required[0], force=True)
    return True


def submitted(host, page):
    required = pending(host)
    if required and required[0] == page:
        host.workflow_submission_state = {**(vars(host).get('workflow_submission_state') or {}),
            'required': [p for p in host.workflow_submission_state['required'] if p != page]}
        persist(host)


def can_submit(host, page):
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
                        return fn(self)
                    return False
            else:
                @wraps(fn)
                def wrapped(self, *args, **kwargs):
                    if can_submit(self, target):
                        from GUI.SupportSubmission import prepare
                        prepare(self, target)
                        return fn(self, *args, **kwargs)
                    return False
            return wrapped
        setattr(cls, name, protect(original, page))
    return cls
