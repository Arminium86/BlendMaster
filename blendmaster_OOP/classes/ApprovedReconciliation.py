"""User-approved factors tied to physical sources and historical evidence."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json

from classes.GradeStreams import configured_brands, normalise_opf, numeric
from classes.ReconciliationControls import normalise_reconciliation_settings
from classes.AMTFootprintExclusions import included_footprints


class ReconciliationRequired(RuntimeError):
    title = 'Grade Reconciliation required'
    workflow_page = 'grade_reconciliation'

    def __init__(self, message, workflow_page='grade_reconciliation'):
        super().__init__(message)
        self.user_message, self.workflow_page = message, workflow_page


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def policy_signature(settings, revision=None):
    policy = normalise_reconciliation_settings(settings)
    policy.pop('lookback_refresh_tolerance_minutes', None)
    return fingerprint([1, policy, revision])


def source_identity(mine, opf, kind, name, build='', hex_id=None):
    return [str(mine or '').strip().upper(), normalise_opf(opf), kind,
            str(name or '').strip().upper(), str(build or '').strip().upper(), str(hex_id or '')]


def source_key(identity, brand):
    return fingerprint([identity, str(brand).upper()])


def source_build(row, fallback=None):
    fallback = fallback or {}
    return str(row.get('LOCATION_NAME') or row.get('build') or row.get('BUILD')
               or fallback.get('build') or fallback.get('BUILD') or '').strip().upper()


def opfs(state):
    feed = state.get('multi_feed_configuration') or {}
    return sorted({p['opf'] for p in feed.get('tipping_points', [])}) if feed.get('mode') == 'combined_opf' else [state.get('opf_input_choice')]


def opening_inventory_sources(state, opf):
    """Expose closed builds still present in opening conveyors for manual review."""
    selected = state.get('updated_stockpile_data') or {}
    current = {source_build(row) for row in selected.values() if not row.get('amt', row.get('AMT', False))
               and (numeric(row.get('balance', row.get('BALANCE'))) or 0) > 0}
    rows = {}
    for movement in (state.get('transport_opening_history') or {}).get('records') or []:
        fields = movement.get('OPENING_INVENTORY_FIELDS')
        build = str(movement.get('SOURCE') or '').strip().upper()
        if not fields or not build or build in current or normalise_opf(movement.get('opf')) != normalise_opf(opf):
            continue
        name = f'Opening transport / {build}'
        rows[name] = {**fields, 'build': build, 'balance': numeric(fields.get('BASIS_WMT')) or 0,
                      'amt': False, 'reconciliation_opf': opf}
    return rows


def selected_sources(state, *, planning=False):
    """Physical builds and the chosen AMT reconciliation grain."""
    selected = included_footprints(state.get('updated_stockpile_data'), state.get('AMT_footprint_exclusions'))
    chunks = state.get('hex_sequence_table') or state.get('hex_sequence_table_argument') or []
    for name, row in selected.items():
        if not row.get('amt', row.get('AMT', False)):
            if (numeric(row.get('balance', row.get('BALANCE'))) or 0) > 0:
                yield name, 'inventory', row, None, source_build(row)
            continue
        rows = (state.get('AMT_stockpile_data') or {}).get(name) or []
        from classes.AMTReconciliation import after_chunking, chunk_build
        if after_chunking(state):
            for chunk in chunks:
                if chunk.get('footprint') == name and (numeric(chunk.get('balance')) or 0) > 0:
                    yield name, 'amt_chunk', chunk, chunk.get('hex'), chunk_build(state, chunk)
            continue
        members = None
        if planning:
            matching = [c for c in chunks if c.get('footprint') == name]
            if matching:
                members = {str(h) for c in matching for h in member_hexes(c)}
        for hex_row in rows:
            identity = hex_row.get('HEX', hex_row.get('hex'))
            if (numeric(hex_row.get('FINAL_WMT', hex_row.get('balance'))) or 0) > 0 and (members is None or str(identity) in members):
                yield name, 'amt', hex_row, identity, source_build(hex_row, row)
        if not rows and (numeric(row.get('balance', row.get('BALANCE'))) or 0) > 0:
            yield name, 'amt', {}, None, source_build(row)
    for opf in opfs(state):
        for name, row in opening_inventory_sources(state, opf).items():
            yield name, 'inventory', row, None, source_build(row)


def member_hexes(chunk):
    members = chunk.get('member_hexes') or []
    return [h.strip() for h in members.split(',') if h.strip()] if isinstance(members, str) else list(members)


def missing_sources(state, *, planning=False):
    from classes.MultiFeedSettings import source_opfs, source_routing
    routing = source_routing(state)
    registry = state.get('grade_reconciliation_registry') or {}
    records = registry.get('sources') or {}
    policy = policy_signature(state.get('reconciliation_settings'), state.get('grade_reconciliation_policy_revision'))
    brands = configured_brands(state.get('product_brand_labels_choice'))
    missing, evidence_cache = [], {}
    for opf in opfs(state):
        excluded = continuous_members(state, opf)
        for name, kind, row, hex_id, build in selected_sources(state, planning=planning):
            if normalise_opf(opf) not in source_opfs(state, name, row, settings=routing):
                continue
            if row.get('reconciliation_opf') and normalise_opf(row['reconciliation_opf']) != normalise_opf(opf):
                continue
            if kind == 'inventory' and continuous_inventory(state, opf, name, row):
                continue
            if kind == 'amt' and (str(name).upper(), str(hex_id)) in excluded:
                continue
            if kind == 'amt_chunk' and continuous_chunk(state, opf, row):
                continue
            identity = source_identity(state.get('mine_input_choice'), opf, kind, name, build, hex_id)
            from classes.SourceSnapshots import source_dependencies, approval_valid
            content, evidence = source_dependencies(state, opf, kind, row, evidence_cache=evidence_cache)
            tolerance = normalise_reconciliation_settings(state.get('reconciliation_settings'))['lookback_refresh_tolerance_minutes']
            absent = [brand for brand in brands if not approval_valid(records.get(source_key(identity, brand)),
                policy, content, evidence, state.get('start_time_choice'), tolerance)]
            if absent:
                missing.append(dict(opf=opf, source=name, kind=kind, build=build, hex_id=hex_id, brands=absent))
    return missing


def required_message(missing):
    labels = [f"{r['opf']} / {r['source']}" + (f" / hex {r['hex_id']}" if r.get('hex_id') is not None else '') for r in missing[:8]]
    more = f"\n…and {len(missing)-8} more sources." if len(missing) > 8 else ''
    return 'Open Grade Reconciliation and select Update missing sources before planning.\n' + '\n'.join(labels) + more


def require_approved(state, *, planning=True):
    from classes.MultiFeedSettings import unrouted_sources
    from classes.AMTReconciliation import after_chunking, chunks_ready
    unrouted = unrouted_sources(state)
    if unrouted:
        raise ReconciliationRequired('Assign a Subset / permitted feed point in Stockpile Inventories for: ' + ', '.join(unrouted), 'stockpile_inventories')
    if (planning or after_chunking(state)) and not chunks_ready(state):
        raise ReconciliationRequired('Submit chunks for all selected AMT stockpiles before continuing.', 'amt_stockpiles')
    missing = missing_sources(state, planning=planning)
    if missing:
        raise ReconciliationRequired(required_message(missing))


def migrate_saved_approvals(state):
    """Adopt auditable, submitted legacy results before any opening refresh.

    A saved applied revision proves which settings/source snapshot was submitted.
    Unverified or missing evidence remains pending; loading never searches or
    invents an adjustment timestamp for an older result.
    """
    if 'grade_reconciliation_registry' in state:
        return state.get('grade_reconciliation_registry') or {}
    from types import SimpleNamespace
    from GUI.WorkflowDependencies import reconciliation_input_revision
    if not state.get('reconciliation_applied_revision') or state['reconciliation_applied_revision'] != reconciliation_input_revision(SimpleNamespace(**state)):
        return {}
    candidates = {}
    def collect(audit):
        if not isinstance(audit, dict):
            return
        kind = audit.get('source_kind')
        if kind in ('inventory', 'amt') and audit.get('status') != 'pending':
            candidates[normalise_opf(audit.get('opf')), kind, str(audit.get('source_id') or '').upper(), str(audit.get('hex_id') or '')] = audit
        for child in audit.get('review_children') or []:
            collect(child)
    for row in {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}.values():
        collect(row.get('reconciliation'))
    for rows in (state.get('AMT_stockpile_data') or {}).values():
        for row in rows:
            collect(row.get('reconciliation'))
    cache = state.get('_combined_opf_profile_cache')
    profiles = cache[1] if isinstance(cache, (list, tuple)) and len(cache) == 2 else {}
    for profile in (profiles or {}).values():
        for row in (profile.get('inventory') or {}).values():
            collect(row.get('reconciliation'))
        for audit in profile.get('reconciliation_audits') or []:
            collect(audit)
    policy = policy_signature(state.get('reconciliation_settings'), state.get('grade_reconciliation_policy_revision'))
    records, evidence_cache = {}, {}
    for opf in opfs(state):
        for name, kind, row, hex_id, build in selected_sources(state):
            if row.get('reconciliation_opf') and normalise_opf(row['reconciliation_opf']) != normalise_opf(opf):
                continue
            audit = candidates.get((normalise_opf(opf), kind, str(name).upper(), str(hex_id or '')), {})
            identity = source_identity(state.get('mine_input_choice'), opf, kind, name, build, hex_id)
            for brand, detail in audit.get('by_brand', {}).items():
                if not detail.get('records'):
                    continue
                detail = deepcopy(detail)
                detail.update(approval_policy=policy, source_identity=identity,
                              evidence_as_of=str(state.get('start_time_choice') or ''))
                from classes.SourceSnapshots import source_dependencies
                content, evidence = source_dependencies(state, opf, kind, row, evidence_cache=evidence_cache)
                records[source_key(identity, brand)] = dict(policy=policy, detail=detail, source_content=content,
                    historical_evidence=evidence, lookback_anchor=str(state.get('start_time_choice')))
    return dict(sources=records)


def chunk_key(state, opf, chunk):
    inventory = {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}
    identity = source_identity(state.get('mine_input_choice'), opf, 'amt_chunk', chunk.get('footprint'),
                               source_build(chunk, inventory.get(chunk.get('footprint'))), chunk.get('hex') or chunk.get('chunk_id'))
    # A different physical membership is a new scheduling source, even if its label is reused.
    return fingerprint([identity, sorted(str(h) for h in member_hexes(chunk))])


def continuous_chunk(state, opf, chunk):
    record = ((state.get('grade_reconciliation_registry') or {}).get('active_chunks') or {}).get(chunk_key(state, opf, chunk))
    return record if _covers_brands(state, record) else None


def continuous_inventory(state, opf, name, row):
    identity = source_identity(state.get('mine_input_choice'), opf, 'inventory', name, source_build(row))
    record = ((state.get('grade_reconciliation_registry') or {}).get('active_inventory') or {}).get(fingerprint(identity))
    return record if _covers_brands(state, record) else None


def _covers_brands(state, record):
    return record and set(configured_brands(state.get('product_brand_labels_choice'))) <= set((record.get('reconciliation') or {}).get('by_brand') or {})


def continuous_members(state, opf):
    return {(str(chunk.get('footprint')).upper(), str(h))
            for chunk in state.get('hex_sequence_table') or state.get('hex_sequence_table_argument') or [] if continuous_chunk(state, opf, chunk)
            for h in member_hexes(chunk)}


def record_active_sources(state, scope, profiles=None):
    """Freeze approved build/chunk priors after actual activity; never adjust hexes."""
    from classes.AcceptedEvidence import fork_registry
    registry = fork_registry(state.get('grade_reconciliation_registry'))
    state['grade_reconciliation_registry'] = registry
    active = registry.setdefault('active_chunks', {})
    policy = policy_signature(state.get('reconciliation_settings'), state.get('grade_reconciliation_policy_revision'))
    for opf, names in scope.items():
        profile = next((p for key, p in (profiles or {}).items() if normalise_opf(key) == normalise_opf(opf)), None)
        inventory = (profile.get('inventory') if profile else None) or {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}
        for name, row in inventory.items():
            if str(name).upper() not in names or row.get('amt', row.get('AMT', False)):
                continue
            audit = row.get('reconciliation') or {}
            if not audit.get('by_brand') or any(d.get('approval_policy') != policy for d in audit['by_brand'].values()):
                continue
            identity = source_identity(state.get('mine_input_choice'), opf, 'inventory', name, source_build(row))
            if not continuous_inventory(state, opf, name, row):
                registry.setdefault('active_inventory', {})[fingerprint(identity)] = dict(
                    policy=policy, activated_at=datetime.now().isoformat(),
                    grade_streams=deepcopy(row.get('grade_streams') or {}), reconciliation=deepcopy(audit))
        chunks = list(profile['chunks'].values()) if profile else state.get('hex_sequence_table') or []
        for chunk in chunks:
            if str(chunk.get('hex') or chunk.get('chunk_id') or '').upper() not in names:
                continue
            audit = chunk.get('reconciliation') or {}
            if not audit.get('by_brand') or audit.get('status') == 'pending':
                continue  # Activity never approves an unreconciled source.
            footprint = chunk.get('footprint')
            build = source_build(chunk, inventory.get(footprint))
            from classes.AMTReconciliation import after_chunking, chunk_build
            identities = ([source_identity(state.get('mine_input_choice'), opf, 'amt_chunk', footprint, chunk_build(state, chunk), chunk.get('hex'))]
                          if after_chunking(state) else [source_identity(state.get('mine_input_choice'), opf, 'amt', footprint, build, h) for h in member_hexes(chunk)])
            if any((registry.get('sources', {}).get(source_key(identity, brand)) or {}).get('policy') != policy
                   for identity in identities for brand in configured_brands(state.get('product_brand_labels_choice'))):
                continue
            key = chunk_key(state, opf, chunk)
            if not continuous_chunk(state, opf, chunk):
                active[key] = dict(policy=policy, activated_at=datetime.now().isoformat(),
                    grade_streams=deepcopy(chunk.get('grade_streams') or {}), reconciliation=deepcopy(audit))


def accept_continuous_update(state, bundle, profiles=None):
    """Activity and adjustment timestamps come from feed/accepted estimates, not polling."""
    bundles = bundle.get('profiles') or {state.get('opf_input_choice'): bundle}
    scope = {}
    for opf, result in bundles.items():
        names = set(result.get('activity_sources') or [])
        names.update(s for observation in result.get('evidence') or [] for s, weight in observation.get('weights', {}).items() if weight > 0)
        scope[normalise_opf(opf)] = sorted(names)
    record_active_sources(state, scope, profiles)
    registry = state.get('grade_reconciliation_registry') or {}
    inventory = {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}
    for opf, result in bundles.items():
        for update in result.get('timeline') or []:
            timestamp = update.get('available_at')
            if not timestamp:
                continue
            adjusted = update.get('adjusted_sources')
            if adjusted is None:  # Compatibility with previously accepted timelines.
                adjusted = [s for offsets in update.get('offsets', {}).values() for s in offsets]
            names = {str(s).upper() for s in adjusted}
            for name, row in inventory.items():
                record = continuous_inventory(state, opf, name, row)
                if record and str(name).upper() in names:
                    record['last_adjusted'] = max(record.get('last_adjusted') or '', timestamp)
            for chunk in state.get('hex_sequence_table') or []:
                record = (registry.get('active_chunks') or {}).get(chunk_key(state, opf, chunk))
                if record and str(chunk.get('hex') or chunk.get('chunk_id') or '').upper() in names:
                    record['last_adjusted'] = max(record.get('last_adjusted') or '', timestamp)
