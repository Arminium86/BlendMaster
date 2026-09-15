"""Content identities for physical opening sources and reconciliation evidence.

Observation timestamps are not physical changes. Evidence sample dates and
dates inside lineage remain significant: they describe the material/history.
"""
from datetime import datetime
import hashlib
import json
import math

OBSERVATION_FIELDS = frozenset(('transaction_datetime', 'snapshot_datetime',
    'snapshot_time', 'snapshot_timestamp', 'as_of', 'as_at', 'last_update',
    'hex_updated', 'refreshed_at', 'fetched_at', 'retrieved_at',
    'inventory_transaction_datetime', 'amt_inventory_transaction_datetime'))
DERIVED_FIELDS = frozenset(('reconciliation', 'grade_streams', 'grade_streams_json',
    'grade_stream_warnings', 'grade_stream_warnings_json', 'defined_fields',
    'source_properties', 'modelled_properties', '_source_snapshot', '_source_derived_fields',
    'prepared_grade_opf', 'data_quality', 'is_ready', 'auto_turnover_datetime'))
CONTROL_FIELDS = frozenset(('amt', 'subset', 'max_reclaim_rate', 'reclaim_threshold'))


def canonical(value):
    if isinstance(value, dict):
        return {str(k).lower(): canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            return int(value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True,
        default=str, separators=(',', ':')).encode()).hexdigest()


def source_content(row):
    result = {}
    generated = set((row or {}).get('_source_derived_fields') or [])
    modelled = (row or {}).get('MODELLED_PROPERTIES_JSON', (row or {}).get('modelled_properties_json')) or {}
    if isinstance(modelled, str):
        try:
            modelled = json.loads(modelled)
        except ValueError:
            modelled = {}
    modelled_values = {str(k).lower() for k in (modelled.get('values') or {})} if isinstance(modelled, dict) else set()
    for key, value in (row or {}).items():
        name = str(key).lower()
        if name in OBSERVATION_FIELDS | DERIVED_FIELDS | CONTROL_FIELDS | generated:
            continue
        if name.startswith('modelled_') and name[len('modelled_'):] in modelled_values:
            continue
        if name.startswith(('adjusted_rom_', 'adjusted_product_')):
            continue
        if name.endswith('_json') and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        result[name] = str(value).upper() if name in ('name', 'footprint', 'build', 'location_name') else value
    streams = (row or {}).get('grade_streams', (row or {}).get('GRADE_STREAMS'))
    if isinstance(streams, dict) and 'grade_streams' not in generated:
        # Some imported sources supply their baselines as streams alone.
        raw_grades = any(k.startswith(('grade_', 'fe_', 'si_', 'al_', 'p_', 'mn_', 'modelled_'))
                         or k in ('fe', 'sio2', 'al2o3', 'p', 'mn') for k in result)
        if not raw_grades:
            result['baseline_streams'] = {k: v for k, v in streams.items()
                if k in ('insitu', 'modelled_rom', 'modelled_product')}
    return canonical(result)


def source_signature(row):
    return digest(source_content(row))


def snapshot_signature(rows):
    if isinstance(rows, dict):
        return source_signature(rows)
    return digest(sorted((source_signature(row) for row in (rows or []))))


def evidence_signature(samples, factors):
    # Only top-level fetch metadata is ignored. Sample and campaign timestamps
    # stay in the digest, including corrected/deleted historical observations.
    return digest([sorted(digest(sample) for sample in (samples or [])),
                   {k: v for k, v in (factors or {}).items() if str(k).lower() not in OBSERVATION_FIELDS}])


def within_tolerance(anchor, current, minutes):
    if str(anchor) == str(current):
        return True
    try:
        first = datetime.fromisoformat(str(anchor).replace('Z', '+00:00'))
        second = datetime.fromisoformat(str(current).replace('Z', '+00:00'))
        return abs((second - first).total_seconds()) <= minutes * 60
    except (TypeError, ValueError):
        return False


def approval_valid(record, policy, content, evidence, current, tolerance):
    return bool(record and record.get('policy') == policy
        and record.get('source_content') == content
        and record.get('historical_evidence') == evidence
        and within_tolerance(record.get('lookback_anchor'), current, tolerance))


def source_dependencies(state, opf, kind, row, *, evidence_cache=None, include_evidence=True):
    """Same physical/evidence identity used by the review and application gates."""
    from classes.GradeStreams import normalise_opf
    primary = normalise_opf(state.get('opf_input_choice')) == normalise_opf(opf)
    bundle = {} if primary else (state.get('opf_reconciliation_inputs') or {}).get(opf) or {}
    inputs = (state.get('reconciliation_inputs') if primary else bundle.get('reconciliation_inputs')) or {}
    factors = (state.get('historical_recon_factors') if primary else bundle.get('factors')) or {}
    lineage = None
    if kind == 'inventory':
        build = str(row.get('build') or row.get('BUILD') or '').strip().upper()
        lineage = (inputs.get('inventory_lineage') or {}).get(build)
    if kind == 'amt_chunk':
        members = row.get('member_hexes') or []
        members = [v.strip() for v in members.split(',')] if isinstance(members, str) else members
        ids = {str(v) for v in members}
        rows = (state.get('AMT_stockpile_data') or {}).get(row.get('footprint')) or []
        physical = [source_content(r) for r in rows if str(r.get('HEX', r.get('hex'))) in ids]
        if ids and len(physical) == len(ids):
            # OPF publication can replace a chunk's derived product fields.
            # Its member material and physical WMT identify the source instead.
            content = digest([row.get('balance'), sorted(ids), sorted(digest(r) for r in physical)])
        else:
            content = digest([source_content(row), lineage])
    else:
        content = digest([source_content(row), lineage])
    if not include_evidence:
        return content, None
    key = normalise_opf(opf)
    if evidence_cache is None:
        evidence_cache = {}
    if key not in evidence_cache:
        evidence_cache[key] = evidence_signature(inputs.get('samples'), factors)
    return content, evidence_cache[key]
