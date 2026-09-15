"""Publish prepared chemistry to source records and the AMT view without searches."""
from classes.AcceptedEvidence import copy_prepared_source


def publish(host, profiles):
    # A source routed only to a secondary OPF has no row in the primary profile.
    # Keep the independent profiles intact; label the OPF used in the main view.
    preferred = vars(host).get('opf_input_choice')
    ordered = [opf for opf in (preferred, *(opf for opf in profiles if opf != preferred))
               if isinstance(profiles.get(opf), dict)]
    for field, section in (('stockpile_data', 'inventory'), ('updated_stockpile_data', 'inventory'),
                           ('hex_sequence_table', 'chunks'), ('hex_sequence_table_argument', 'chunks')):
        values = vars(host).get(field)
        if values is None:
            continue
        def prepared(identity, original):
            for opf in ordered:
                row = (profiles.get(opf) or {}).get(section, {}).get(identity)
                if row is not None:
                    row = {**row, 'prepared_grade_opf': opf}
                    # Observation time does not invalidate chemistry. Keep the
                    # latest source observation when hydrating a cached profile.
                    from classes.SourceSnapshots import OBSERVATION_FIELDS
                    row = {key: value for key, value in row.items() if str(key).lower() not in OBSERVATION_FIELDS}
                    row.update({key: value for key, value in original.items()
                                if str(key).lower() in OBSERVATION_FIELDS})
                    if section == 'chunks':
                        row['GRADE_STREAMS'] = row.get('grade_streams') or {}
                    # Cache hits still hydrate restored/missing rows, but leave
                    # unchanged rows in place without copying their evidence.
                    return original if original == row else copy_prepared_source(row)
            return original
        if section == 'inventory':
            for identity in values:
                values[identity] = prepared(identity, values[identity])
        else:
            vars(host)[field] = [prepared(row.get('hex'), row) for row in values]
    sync_map(host)


def sync_map(host):
    """Update chemistry only for unchanged chunks; retain edits to the dig plan."""
    chart = vars(host).get('draw_AMT_map')
    if chart is None:
        return
    from classes.ApprovedReconciliation import member_hexes
    def identity(row):
        return (row.get('footprint'), row.get('hex'), float(row.get('balance') or 0),
                tuple(sorted(str(h) for h in member_hexes(row))))
    prepared = {identity(row): row for row in vars(host).get('hex_sequence_table') or []}
    controls = {'footprint', 'hex', 'sequence', 'balance', 'member_hexes', 'chunk_size',
                'average_reclaim_rate', 'chunk_reclaim_hours', 'dig_path', 'reclaim_direction',
                'cut_direction', 'excluded_hexes', 'excluded_hex_count', 'excluded_hex_wmt'}
    changed = False
    rows = []
    for original in chart.selected_points:
        row = prepared.get(identity(original))
        updates = {key: value for key, value in row.items() if key not in controls} if row else {}
        if any(original.get(key) != value for key, value in updates.items()):
            original = {**original, **copy_prepared_source(updates)}
            changed = True
        rows.append(original)
    if changed:
        chart.selected_points = rows
        chart.refresh_call = True
        chart.init_layout()
        from GUI.WorkflowViews import schedule
        schedule(host, charts=True)
