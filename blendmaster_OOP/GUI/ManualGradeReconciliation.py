"""One explicit review action addresses missing sources for every selected OPF."""
from classes.ApprovedReconciliation import opfs, opening_inventory_sources
from classes.CombinedOPFReconciliation import SourceContext, opf_field_mappings


def calculate(context, implementation):
    from classes.ApprovedReconciliation import selected_sources
    from classes.MultiFeedSettings import source_opfs, source_routing
    from classes.GradeStreams import normalise_opf
    state = vars(context)
    from classes.AcceptedEvidence import fork_registry
    registry = fork_registry(state.get('grade_reconciliation_registry'))
    state['grade_reconciliation_registry'] = registry
    refresh = state.get('_grade_reconciliation_refresh_sources') or []
    if refresh:
        registry['sources'] = {key: value for key, value in registry.get('sources', {}).items()
                               if value.get('detail', {}).get('source_identity') not in refresh}
    primary = state.get('opf_input_choice')
    audits, warnings, overall = [], [], {}
    routing = source_routing(state)
    relevant_opfs = {opf for name, _, row, _, _ in selected_sources(state)
                     for opf in source_opfs(state, name, row, settings=routing)}
    for opf in opfs(state):
        if normalise_opf(opf) not in relevant_opfs:
            continue
        values = {key: value for key, value in state.items() if not key.startswith('_')}
        values.update(_ui_class=implementation, _manual_grade_reconciliation=True,
                      grade_reconciliation_registry=registry, opf_input_choice=opf)
        values['updated_stockpile_data'] = {**(state.get('updated_stockpile_data') or {}),
                                            **opening_inventory_sources(state, opf)}
        if opf != primary:
            bundle = (state.get('opf_reconciliation_inputs') or {}).get(opf) or {}
            values.update(historical_recon_factors=bundle.get('factors') or {},
                          reconciliation_inputs=bundle.get('reconciliation_inputs') or {},
                          field_mappings=opf_field_mappings(state.get('field_mappings'), primary, opf))
        view = SourceContext(**values)
        rows, summary, messages = implementation.calculate_reconciliation_review(view)
        if opf == primary:
            overall = summary
        for row in rows:
            row['review_label'] = f"{opf} · {row.get('review_label', row.get('source_id', ''))}"
        audits.extend(rows)
        warnings.extend(messages)
    return (audits, overall, list(dict.fromkeys(warnings))), None, registry


def saved_review(state):
    """Render approved and missing rows without creating a factor resolver."""
    from classes.ApprovedReconciliation import (selected_sources, source_identity, source_key,
        policy_signature, member_hexes, continuous_chunk, continuous_inventory, continuous_members)
    from classes.GradeStreams import configured_brands, normalise_opf, numeric
    from classes.ReconciliationApplication import aggregate_reconciliation
    from classes.AMTReconciliation import after_chunking
    from classes.MultiFeedSettings import source_opfs, source_routing
    from copy import deepcopy
    records = (state.get('grade_reconciliation_registry') or {}).get('sources') or {}
    policy = policy_signature(state.get('reconciliation_settings'), state.get('grade_reconciliation_policy_revision'))
    brands = configured_brands(state.get('product_brand_labels_choice'))
    audits, overall, evidence_cache = [], {}, {}
    routing = source_routing(state)
    for opf in opfs(state):
        members, local = {}, []
        excluded = continuous_members(state, opf)
        for name, kind, row, hex_id, build in selected_sources(state):
            if normalise_opf(opf) not in source_opfs(state, name, row, settings=routing):
                continue
            if row.get('reconciliation_opf') and normalise_opf(row['reconciliation_opf']) != normalise_opf(opf):
                continue
            if kind == 'amt' and (str(name).upper(), str(hex_id)) in excluded:
                continue
            identity = source_identity(state.get('mine_input_choice'), opf, kind, name, build, hex_id)
            managed = (continuous_inventory(state, opf, name, row) if kind == 'inventory' else
                       continuous_chunk(state, opf, row) if kind == 'amt_chunk' else None)
            if managed:
                audit = deepcopy(managed['reconciliation'])
                audit.update(prediction_scope=kind, last_adjusted=managed.get('last_adjusted') or audit.get('last_adjusted'))
            else:
                details, stale = {}, False
                from classes.SourceSnapshots import source_dependencies, approval_valid
                from classes.ReconciliationControls import normalise_reconciliation_settings
                # Content and history are independent of product brand. Keep
                # this local to this review so in-place edits still invalidate.
                if brands:
                    content, evidence = source_dependencies(state, opf, kind, row, evidence_cache=evidence_cache)
                    tolerance = normalise_reconciliation_settings(state.get('reconciliation_settings'))['lookback_refresh_tolerance_minutes']
                for brand in brands:
                    approved = records.get(source_key(identity, brand)) or {}
                    detail = dict(approved.get('detail') or {})
                    stale |= not approval_valid(approved, policy, content, evidence, state.get('start_time_choice'),
                        tolerance)
                    detail.setdefault('source_identity', identity)
                    detail.setdefault('confidence_percent', None)
                    detail.setdefault('lineage_coverage', 0)
                    detail['global_fraction'] = sum(r['lineage_fraction'] for r in detail.get('records', []) if r.get('resolution_level') == 'global')
                    details[brand] = detail
                dates = [d['calculated_at'] for d in details.values() if d.get('calculated_at')]
                audit = dict(opf=normalise_opf(opf), method=(state.get('reconciliation_settings') or {}).get('method', 'standard'),
                    source_id=name, source_kind=kind, hex_id=hex_id, by_brand=details,
                    last_adjusted=min(dates) if dates else None, status='pending' if stale else 'approved',
                    warnings=['Manual Grade Reconciliation update required.'] if stale else [])
            audit.update(source_wmt=max(numeric(row.get('FINAL_WMT', row.get('balance', row.get('BALANCE')))) or 0, 0),
                         review_label=f'{opf} · {name}' + (f' · hex {hex_id}' if hex_id is not None else '')
                         + (' · Update required' if audit.get('status') == 'pending' else ''))
            if kind in ('inventory', 'amt_chunk'):
                local.append(audit)
            else:
                members[name, str(hex_id)] = audit
        used = set()
        for chunk in ([] if after_chunking(state) else state.get('hex_sequence_table') or []):
            name = chunk.get('footprint')
            if normalise_opf(opf) not in source_opfs(state, name, settings=routing):
                continue
            keys = [(name, str(h)) for h in member_hexes(chunk)]
            children = [members[k] for k in keys if k in members]
            used.update(keys)
            managed = continuous_chunk(state, opf, chunk)
            if managed:
                audit = deepcopy(managed['reconciliation'])
                audit.update(prediction_scope='amt_chunk', last_adjusted=managed.get('last_adjusted') or audit.get('last_adjusted'))
            elif children:
                audit = aggregate_reconciliation([(a, a['source_wmt']) for a in children], source_id=name, source_kind='amt_chunk')
                audit['review_children'] = children
            else:
                continue
            audit.update(source_wmt=max(numeric(chunk.get('balance')) or 0, 0),
                review_label=f"{opf} · {name} · chunk {chunk.get('sequence', '')}" + (' · Continuous assays' if managed else ''))
            local.append(audit)
        local.extend(audit for key, audit in members.items() if key not in used)
        if opf == state.get('opf_input_choice'):
            overall = aggregate_reconciliation([(a, a['source_wmt']) for a in local], source_kind='overall')
        audits.extend(local)
    return audits, overall, []
