"""Operation checks at the desktop boundary; role comes from the trusted launcher."""
from functools import wraps
from PyQt5.QtCore import Qt
from classes.SiteWorkflow import require_action


def control_available(widget):
    """Control's own availability, unaffected by a temporary parent input lock."""
    if hasattr(widget, 'testAttribute'):
        return not widget.testAttribute(Qt.WA_ForceDisabled)
    return widget.isEnabled()

SUPPORT_OPERATIONS = (
    'add_blank_site_scenario', 'remove_active_site_scenario',
    'handle_define_fields_submit', 'handle_map_fields_submit',
    'handle_solver_configuration_submit', 'submit_multi_feed_setup',
    'capture_data_stream_configuration',
    'execute_sqlite_report_query',
    'start_agent_bridge', 'submit_agent_request', 'apply_selected_agent_proposals',
    'start_agent_workflow_apply', 'apply_agent_site_configuration_payload',
    'apply_agent_target_value', 'apply_agent_project_load',
    'apply_agent_amt_chunking_payload', 'apply_agent_selected_stockpiles',
    'apply_agent_calendar_target', 'apply_agent_amt_chunking_target',
)


def protect_support_operations(cls):
    for name in SUPPORT_OPERATIONS:
        original = getattr(cls, name, None)
        if original is None:
            continue
        def protect(fn, action):
            @wraps(fn)
            def wrapped(self, *args, **kwargs):
                # Adapters without a UI session retain compatibility. A real
                # UserInputs session always has an explicitly assigned role.
                require_action(vars(self).get('access_role', 'support'), action)
                return fn(self, *args, **kwargs)
            return wrapped
        setattr(cls, name, protect(original, name))
    return cls


def validate_planner_site_controls(host):
    if vars(host).get('access_role') != 'planner':
        return
    if not getattr(host, 'mine_input_choice', None) or not getattr(host, 'crusher_input_choice', None):
        raise PermissionError('Load or select a configured site model before setting the planning time.')
    expected = (host.hub_input_choice, host.mine_input_choice,
                (getattr(host, 'multi_feed_configuration', {}) or {}).get('mode', 'single'))
    current = (host.hub_input.currentText(), host.mine_input.currentText(), host.plan_mode_input.currentData())
    if current != expected:
        raise PermissionError('Site mode and equipment are configured by Support. Select the configured site again.')
    feed = getattr(host, 'multi_feed_configuration', None) or {}
    expected_opfs = {point['opf'] for point in feed.get('tipping_points', [])} or {host.opf_input_choice}
    opfs = set(host.opf_input.selectedTexts()) if hasattr(host.opf_input, 'selectedTexts') else {host.opf_input.currentText().strip()}
    if (opfs != expected_opfs or set(host.selected_site_crusher_names()) != set(host.selected_site_crushers)
            or host.parse_product_brand_labels(host.product_brand_labels_input.text()) != host.product_brand_labels_choice):
        raise PermissionError('OPFs, operating crushers and product brands are configured by Support.')
