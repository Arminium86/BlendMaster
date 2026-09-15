"""Reuse only a completed plan whose effective solver inputs still match."""
import hashlib
import sqlite3
from pathlib import Path

from classes.SiteWorkflow import fingerprint
from classes.SourceSnapshots import OBSERVATION_FIELDS


def content(value):
    """Encode full tables; transaction/evidence dates remain solver inputs."""
    if hasattr(value, 'to_dict') and hasattr(value, 'columns'):
        return {'columns': list(value.columns), 'rows': content(value.to_dict('records'))}
    if isinstance(value, dict):
        return {str(key): content(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [content(item) for item in value]
    return value


def source(row):
    # Only a physical source's observation timestamp is incidental. Dates in
    # lineage and actual movement history must still invalidate the plan.
    return {key: value for key, value in row.items()
            if str(key).lower() not in OBSERVATION_FIELDS | {'reconciliation', '_source_snapshot'}}


def signature(host):
    state = vars(host)
    context = dict((state.get('calendar_inputs') or {}).get('site_context') or {})
    for key in ('reporting_input_audits', 'flow_node_positions', 'historical_recon_warnings'):
        context.pop(key, None)
    # Approved evidence is immutable and often contains millions of historical
    # candidate values. Reuse its encoding instead of walking those audits.
    from classes.AcceptedEvidence import fingerprint_fields
    for key in ('grade_reconciliation_registry',):
        context[key] = fingerprint_fields({key: context.get(key)}, state, accepted=(key,))
    context['opf_profiles'] = {opf: {**profile,
        'inventory': {name: source(row) for name, row in (profile.get('inventory') or {}).items()},
        'chunks': {name: source(row) for name, row in (profile.get('chunks') or {}).items()}}
        for opf, profile in (context.get('opf_profiles') or {}).items()}
    files = {}
    for key in ('file_path_choice', 'file_path_24hr_choice'):
        path = state.get(key)
        if path and Path(path).is_file():
            with open(path, 'rb') as stream:
                files[key] = hashlib.file_digest(stream, 'sha256').hexdigest()
        else:
            files[key] = None
    fields = ('active_scenario_id', 'start_time_choice', 'expit_mode_choice', 'blend_mode_choice',
              'min_stockpiles', 'max_stockpiles', 'min_stockpile_contribution_ratio', 'solver_config',
              'reevaluate_aps_direct_tip_choice', 'aps_direct_tip_crusher_choice',
              'selected_24hr_expit_agents', 'AMT_footprint_exclusions',
              'database_view_expit_payload_transactions', 'opening_inputs_revision')
    calendar = {key: value for key, value in (state.get('calendar_inputs') or {}).items()
                if key != 'site_context'}
    return fingerprint(content(dict(schema=1, inputs={key: state.get(key) for key in fields},
        inventory={name: source(row) for name, row in host.included_stockpile_data().items()},
        chunks=[source(row) for row in state.get('hex_sequence_table_argument') or []],
        calendar=calendar, context=context, files=files)))


def reusable(host, current):
    receipt = vars(host).get('optimisation_reuse_receipt') or {}
    if receipt.get('signature') != current or receipt.get('status') != 'complete':
        return False
    from database.DatabaseContext import get_database_path
    from GUI.WorkflowViews import result_presence
    try:
        return result_presence(get_database_path())['optimised']
    except (OSError, sqlite3.Error):
        return False


def begin(host, current):
    host.optimisation_reuse_receipt = None
    host._pending_optimisation_signature = current


def completed(host, outcome):
    current = vars(host).pop('_pending_optimisation_signature', None)
    host.optimisation_reuse_receipt = None
    if current and (outcome.get('status') or 'complete') == 'complete' and not outcome.get('partial_plan_restored'):
        host.optimisation_reuse_receipt = {'signature': current, 'status': 'complete'}
