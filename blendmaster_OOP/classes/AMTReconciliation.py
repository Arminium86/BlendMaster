"""Reconciliation grain and submitted-chunk readiness shared by UI and workers."""
from collections import defaultdict
from classes.GradeStreams import numeric


def after_chunking(state):
    return bool((state.get('multi_feed_configuration') or {}).get('amt_reconcile_after_chunking'))


def footprints(state):
    from classes.AMTFootprintExclusions import included_footprints
    from setup.AMTSpatialReconciliation import zeroed_amt_footprints
    selected = included_footprints(state.get('updated_stockpile_data'), state.get('AMT_footprint_exclusions'))
    zeroed = zeroed_amt_footprints(state.get('AMT_stockpile_data'))
    return {name for name, row in selected.items() if row.get('amt', row.get('AMT', False))
            and str(name).upper() not in zeroed and (numeric(row.get('balance', row.get('BALANCE'))) or 0) > 0}


def submission_signature(state, chunks=None):
    from classes.ApprovedReconciliation import fingerprint, member_hexes
    chunks = (state.get('hex_sequence_table') or []) if chunks is None else chunks
    return fingerprint([sorted(footprints(state)), sorted([
        [str(c.get('footprint')), str(c.get('hex')), sorted(map(str, member_hexes(c))), c.get('balance')]
        for c in chunks], key=str)])


def chunks_ready(state):
    required = footprints(state)
    if not required:
        return True
    chunks = state.get('hex_sequence_table') or []
    if not required <= {c.get('footprint') for c in chunks if (numeric(c.get('balance')) or 0) > 0}:
        return False
    signature = (state.get('multi_feed_configuration') or {}).get('amt_submission_signature')
    if signature:
        return signature == submission_signature(state)
    # Older projects retain a separate, explicitly submitted scheduling copy.
    submitted = state.get('hex_sequence_table_argument') or []
    return bool(submitted) and submission_signature(state, submitted) == submission_signature(state)


def chunk_build(state, chunk):
    from classes.ApprovedReconciliation import source_build, fingerprint, member_hexes
    inventory = {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}
    return source_build(chunk, inventory.get(chunk.get('footprint'))) + ':' + fingerprint(sorted(map(str, member_hexes(chunk))))


def chunk_lineage(state, chunk):
    from classes.ApprovedReconciliation import member_hexes
    from classes.ReconciliationApplication import amt_reconciliation_lineage
    members = {str(r.get('HEX', r.get('hex'))): r for r in (state.get('AMT_stockpile_data') or {}).get(chunk.get('footprint'), [])}
    blocks, warnings = defaultdict(float), []
    rows = [members.get(str(h)) for h in member_hexes(chunk)]
    if not rows or any(row is None for row in rows):
        raise ValueError(f"{chunk.get('hex')}: original AMT members are unavailable. Refresh AMT data and resubmit chunks.")
    total = sum(max(numeric(row.get('FINAL_WMT', row.get('balance'))) or 0, 0) for row in rows)
    if abs(total - (numeric(chunk.get('balance')) or 0)) > .1:
        raise ValueError(f"{chunk.get('hex')}: AMT member tonnes changed. Resubmit the chunks before reconciliation.")
    for row in rows:
        lineage, messages = amt_reconciliation_lineage(row)
        warnings.extend(messages)
        for entry in lineage:
            blocks[entry['grade_block_key']] += entry['feed_wmt']
    return [dict(grade_block_key=key, feed_wmt=wmt) for key, wmt in blocks.items()], list(dict.fromkeys(warnings))
