"""Reuse prepared sources independently of submission/snapshot timestamps."""
from copy import deepcopy
from classes.SourceSnapshots import digest, snapshot_signature, within_tolerance
from classes.ReconciliationControls import normalise_reconciliation_settings

DEPENDENCIES = ('mine_input_choice', 'hub_input_choice', 'opf_input_choice',
    'field_definitions', 'field_mappings', 'field_mapping_schema_version',
    'product_brand_labels_choice', 'historical_recon_factors',
    'grade_reconciliation_policy_revision', 'cb_lump_fines_mode',
    'cb_lump_percentage', 'cb_lump_fines_settings', 'byproducts_enabled',
    'byproduct_quantity_fields', 'byproduct_grade_fields', 'selected_data_stream')


def dependency_context(state):
    from classes.SourceSnapshots import evidence_signature
    fields = {key: state.get(key) for key in DEPENDENCIES}
    settings = normalise_reconciliation_settings(state.get('reconciliation_settings'))
    settings.pop('lookback_refresh_tolerance_minutes')
    fields['settings'] = settings
    inputs = state.get('reconciliation_inputs') or {}
    fields['history'] = evidence_signature(inputs.get('samples'), state.get('historical_recon_factors'))
    fields['opf_history'] = {opf: evidence_signature((bundle.get('reconciliation_inputs') or {}).get('samples'), bundle.get('factors'))
                            for opf, bundle in (state.get('opf_reconciliation_inputs') or {}).items()}
    approvals = {}
    for key, record in ((state.get('grade_reconciliation_registry') or {}).get('sources') or {}).items():
        identity = (record.get('detail') or {}).get('source_identity') or []
        if len(identity) > 3:
            approvals.setdefault(str(identity[3]).upper(), {})[key] = record
    return digest(fields), approvals


def dependencies(state, name, row, common):
    base, approvals = common
    build = str(row.get('build') or row.get('BUILD') or '').upper()
    def lineage(inputs):
        return ((inputs or {}).get('inventory_lineage') or {}).get(build)
    return digest([base, lineage(state.get('reconciliation_inputs')),
        {opf: lineage(bundle.get('reconciliation_inputs')) for opf, bundle in (state.get('opf_reconciliation_inputs') or {}).items()},
        approvals.get(name.upper()), {str(k).lower(): v for k, v in row.items()
            if str(k).lower() in ('amt', 'subset', 'max_reclaim_rate', 'reclaim_threshold')}])


def inputs(state, name):
    return [snapshot_signature((state.get('stockpile_data') or {}).get(name)),
            snapshot_signature((state.get('updated_stockpile_data') or {}).get(name)),
            snapshot_signature((state.get('AMT_stockpile_data') or {}).get(name, []))]


def partition(state, force=False):
    cache = state.get('inventory_source_cache') or {}
    entries = cache.get('sources') or {}
    dirty, reused, identities = set(), set(), {}
    common = dependency_context(state)
    tolerance = normalise_reconciliation_settings(state.get('reconciliation_settings'))['lookback_refresh_tolerance_minutes']
    for name, row in (state.get('stockpile_data') or {}).items():
        entry = entries.get(name) or {}
        identity = inputs(state, name)
        dependency = dependencies(state, name, (state.get('updated_stockpile_data') or {}).get(name) or row, common)
        identities[name] = (identity, dependency)
        matches = len(entry.get('raw', [])) == 3 and len(entry.get('prepared', [])) == 3 and all(value in {old, prepared} for value, old, prepared in
                      zip(identity, entry.get('raw', []), entry.get('prepared', []))) if entry else False
        if not force and matches and dependency == entry.get('dependencies') and within_tolerance(
                entry.get('anchor'), state.get('start_time_choice'), tolerance):
            reused.add(name)
            for field in ('stockpile_data', 'updated_stockpile_data', 'AMT_stockpile_data'):
                if name in (state.get(field) or {}) and field in entry:
                    state[field][name] = deepcopy(entry[field])
        else:
            dirty.add(name)
    return dirty, reused, identities


def capture(state, dirty, identities):
    entries = dict((state.get('inventory_source_cache') or {}).get('sources') or {})
    for name in dirty:
        raw, dependency = identities[name]
        entries[name] = dict(raw=raw, prepared=inputs(state, name), dependencies=dependency,
                             anchor=str(state.get('start_time_choice')))
        for field in ('stockpile_data', 'updated_stockpile_data', 'AMT_stockpile_data'):
            if name in (state.get(field) or {}):
                entries[name][field] = deepcopy(state[field][name])
    return dict(version=1, sources=entries)
