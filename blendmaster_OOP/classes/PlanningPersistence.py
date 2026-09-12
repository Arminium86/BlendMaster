"""Compatibility boundary and dependency fingerprints for saved planning state.

Only named state containers are migrated. Raw source tables and embedded database
snapshots are retained without copying them merely to add settings defaults.
Presentation positions never influence an input/cache fingerprint.
"""
from copy import deepcopy
import hashlib
import json
import math

from classes.ProductTargets import migrate_product_target_state
from classes.MultiFeedSettings import multi_feed_settings
from classes.TransportSettings import transport_settings
from classes.ReconciliationControls import normalise_reconciliation_settings
from classes.AMTFootprintExclusions import normalize_amt_exclusions

PROJECT_FORMAT_VERSION = 16
PLANNING_SEMANTICS_VERSION = 1
FLOW_LAYOUT_SCHEMA_VERSION = 1
CACHE_SIGNATURE_FIELDS = (
    'inventory_data_request_signature', 'AMT_data_request_signature',
    'AMT_enrichment_signature', 'AMT_chunk_reconciliation_signature',
    'data_stream_input_cache_signature', 'aps_guidance_request_signature',
    'expit_input_cache_signature', 'opf_profile_signature',
)

# Raw warehouse evidence is reusable across solver/visual changes. Derived
# preparations additionally depend on mappings, participation and objectives.
EVIDENCE_FIELDS = ('field_definitions', 'field_mappings',
    'aps_grade_field_mappings', 'aps_source_property_field_mappings',
    'data_stream_planning_categories', 'cb_lump_fines_mode', 'cb_lump_percentage',
    'byproducts_enabled', 'byproduct_quantity_fields', 'byproduct_grade_fields')
PLAN_FIELDS = (*EVIDENCE_FIELDS, 'reconciliation_settings', 'multi_feed_configuration', 'multi_feed_settings',
    'transport_settings', 'product_targets', 'product_build_settings',
    'AMT_footprint_exclusions', 'AMT_chunk_settings', 'selected_data_stream',
    'crusher_tonnes_stream', 'reclaimer_tonnes_stream', 'product_build_tonnes_stream',
    'destination_progress_settings', 'direct_tip_movement_rules', 'destination_haul_routes',
    'manual_ratio_rounding', 'solver_config', 'min_stockpiles', 'max_stockpiles',
    'min_stockpile_contribution_ratio')

def settings_signature(state, *, evidence=False):
    state = state if isinstance(state, dict) else vars(state)
    names = EVIDENCE_FIELDS if evidence else PLAN_FIELDS
    selected = {name:state.get(name) for name in names}
    if isinstance(selected.get('solver_config'), dict):
        selected['solver_config'] = {k:v for k,v in selected['solver_config'].items()
                                     if not k.startswith('_') and not callable(v)}
    encoded = json.dumps(dict(version=PLANNING_SEMANTICS_VERSION,settings=selected),
                         sort_keys=True,default=str,separators=(',',':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

def _version(state, name, maximum, default=1):
    raw = state.get(name, default)
    try:
        number = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'Invalid {name}: {raw!r}.') from None
    if isinstance(raw, bool) or str(raw) != str(number) or not 0 <= number <= maximum:
        raise ValueError(f'Unsupported {name}: {raw!r}; this application supports up to {maximum}.')
    return number

def _schema(value, maximum, label):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a mapping.')
    try:
        _version(value, 'schema_version', maximum)
    except ValueError as exc:
        raise ValueError(f'{label}: {exc}') from None

def _migrate_one(state):
    if not isinstance(state, dict):
        raise ValueError('Project scenarios must be dictionaries.')
    _version(state,'project_format_version',PROJECT_FORMAT_VERSION)
    old = _version(state,'planning_semantics_version',PLANNING_SEMANTICS_VERSION,0)
    _version(state,'flow_layout_schema_version',FLOW_LAYOUT_SCHEMA_VERSION)
    _version(state,'field_mapping_schema_version',3)
    for name in ('reconciliation_settings','multi_feed_configuration','multi_feed_settings',
                 'transport_settings','destination_progress_settings','AMT_chunk_settings'):
        _schema(state.get(name),1,name)
    for record in (state.get('AMT_footprint_exclusions') or {}).values():
        _schema(record,1,'AMT footprint exclusion')
    result = dict(state)
    result.update(project_format_version=PROJECT_FORMAT_VERSION,
                  planning_semantics_version=PLANNING_SEMANTICS_VERSION,
                  flow_layout_schema_version=FLOW_LAYOUT_SCHEMA_VERSION)
    result['reconciliation_settings'] = normalise_reconciliation_settings(state.get('reconciliation_settings'))
    result['multi_feed_configuration'] = multi_feed_settings(state.get('multi_feed_configuration') or state.get('multi_feed_settings'))
    result['transport_settings'] = transport_settings(state.get('transport_settings'))
    result['AMT_footprint_exclusions'] = normalize_amt_exclusions(state.get('AMT_footprint_exclusions'))
    positions = state.get('flow_node_positions') or {}
    if not isinstance(positions,dict):
        raise ValueError('Flow node positions must be a mapping.')
    for key,xy in positions.items():
        if not isinstance(xy,(list,tuple)) or len(xy)!=2 or any(
                isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1e6 for v in xy):
            raise ValueError(f'Invalid flow node position: {key}.')
    result['flow_node_positions'] = deepcopy(positions)
    result['operational_plan_backups'] = deepcopy(state.get('operational_plan_backups') or {})
    if old < PLANNING_SEMANTICS_VERSION:
        for name in CACHE_SIGNATURE_FIELDS:
            if name in result:
                result[name] = ''
        result['data_stream_input_cache_result'] = None
        result['transport_opening_history'] = {}
    solver = state.get('solver_config')
    if isinstance(solver,dict):
        for name in ('transport_settings','multi_feed_settings'):
            _schema(solver.get(name),1,'Solver '+name)
    return result

def migrate_project_state(state):
    """Reject incompatible inactive scenarios before any database restore."""
    if not isinstance(state,dict):
        raise ValueError('Project state must be a dictionary.')
    result = _migrate_one(migrate_product_target_state(state))
    scenarios = result.get('site_scenarios')
    if scenarios is not None:
        if not isinstance(scenarios,dict):
            raise ValueError('Saved site scenarios must be a mapping.')
        result['site_scenarios'] = {key:migrate_project_state(value) for key,value in scenarios.items()}
    return result
