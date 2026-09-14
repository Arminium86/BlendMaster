"""Stable project publication and three-way imports of prepared inputs."""
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

DEFAULT_FOLDER = r'C:\BlendMaster\SharedProjects'
GROUPS = {
    'Planning window': ('start_time_choice', 'time_mode_choice', 'planning_period_count_choice'),
    'Site model and quantity streams': ('hub_input_choice', 'mine_input_choice', 'opf_input_choice',
        'crusher_input_choice', 'selected_site_crushers', 'multi_feed_configuration', 'selected_data_stream',
        'crusher_tonnes_stream', 'reclaimer_tonnes_stream', 'product_build_tonnes_stream',
        'byproducts_enabled', 'byproduct_quantity_fields', 'byproduct_grade_fields', 'transport_settings'),
    'Guidance schedules': ('file_path_choice', 'file_path_24hr_choice', 'haul_cycle_file_path_choice',
        'two_wp_closing_stocks_path_choice', 'guidance_import_audit', 'two_wp_closing_stock_balances',
        'available_24hr_expit_agents', 'available_haul_cycle_crushers', 'available_two_wp_product_crushers',
        'haul_cycle_routes', 'destination_haul_routes', 'aps_stockpile_brand_map', 'aps_stockpile_timing_guidance',
        'aps_active_blend_guidance', 'aps_destination_guidance', 'aps_guidance_request_signature'),
    'Stockpile inventories': ('stockpile_data', 'updated_stockpile_data', 'inventory_data_request_signature'),
    'AMT inventories and chunks': ('AMT_stockpile_data', 'hex_sequence_table', 'hex_sequence_table_argument',
        'AMT_data_request_signature', 'AMT_enrichment_signature', 'AMT_chunk_reconciliation_signature', 'AMT_last_refresh_datetime'),
    'Grade reconciliation': ('historical_recon_factors', 'historical_recon_warnings', 'reconciliation_inputs',
        'opf_reconciliation_inputs', 'data_stream_input_cache_signature', 'data_stream_input_cache_result',
        'reconciliation_applied_revision', 'continuous_assay_state'),
    'Destination activity': ('destination_progress_snapshot',),
    'Conveyor opening contents': ('transport_opening_history',),
    'Field mappings and grade policy': ('field_definitions', 'field_mappings', 'field_mapping_schema_version',
        'aps_grade_field_mappings', 'aps_source_property_field_mappings', 'reconciliation_settings',
        'data_stream_planning_categories', 'continuous_assay_settings'),
    'Solver presets': ('solver_presets',),
}
TABLE_GROUPS = {
    'Guidance schedules': ('expit_payload_transactions', 'expit_sequence_', 'expit_reconciliation_', 'two_wp_grade_block_turnover_audit'),
    'Stockpile inventories': ('opening_stockpile_inventories',),
    'AMT inventories and chunks': ('opening_AMT_stockpile_inventories',),
    'Grade reconciliation': ('continuous_assay_',),
    'Destination activity': ('destination_build_order', 'destination_build_order_audit'),
}
PLANNING_GROUPS = set(GROUPS) - {'Solver presets'}


def settings(value=None):
    value = value if isinstance(value, dict) else {}
    folder = str(value.get('folder') or DEFAULT_FOLDER).strip()
    if not Path(folder).is_absolute():
        raise ValueError('The shared project folder must be an absolute path.')
    name = str(value.get('model_name') or '').strip()
    if len(name) > 120 or any(ord(char) < 32 for char in name):
        raise ValueError('Use a model name of at most 120 characters without control characters.')
    return dict(folder=folder, model_name=name)


def filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(name or 'BlendMaster')).strip(' ._')[:150]
    name = name or 'BlendMaster'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
        name = 'BlendMaster_' + name
    return name + '.prj'


def revision(path):
    info = Path(path).stat()
    return (info.st_size, info.st_mtime_ns, info.st_ino)


def value_hash(value):
    """Stream canonical values into SHA256, including every row of large evidence."""
    digest = hashlib.sha256()
    def add(item):
        if isinstance(item, dict):
            digest.update(b'{')
            for key in sorted(item, key=str):
                add(str(key)); add(item[key])
            digest.update(b'}')
        elif isinstance(item, (list, tuple)):
            digest.update(b'[')
            for child in item:
                add(child)
            digest.update(b']')
        elif isinstance(item, set):
            add(sorted(item, key=str))
        elif isinstance(item, (datetime, date)):
            add(item.isoformat())
        elif hasattr(item, 'to_dict') and hasattr(item, 'columns'):
            add(list(item.columns)); add(item.to_dict('records'))
        elif hasattr(item, 'tolist'):
            add(item.tolist())
        else:
            digest.update(json.dumps(item, default=str, ensure_ascii=False, allow_nan=True).encode('utf-8'))
            digest.update(b'\0')
    add(value)
    return digest.hexdigest()


def group_hashes(state):
    return {group: value_hash({key: state.get(key) for key in fields}) for group, fields in GROUPS.items()}


def changes(baseline, local, incoming):
    left, right = group_hashes(local), group_hashes(incoming)
    return [dict(group=group, conflict=left[group] != baseline.get(group) and left[group] != right[group],
                 incoming_hash=right[group]) for group in GROUPS
            if right[group] != baseline.get(group) and left[group] != right[group]]


def merge_inputs(local, incoming, selected):
    """Selected groups are explicit replacements; every other local field survives."""
    unknown = set(selected) - set(GROUPS)
    if unknown:
        raise ValueError('Unknown input groups: ' + ', '.join(sorted(unknown)))
    dependencies = {'Stockpile inventories', 'AMT inventories and chunks', 'Grade reconciliation',
                    'Destination activity', 'Conveyor opening contents'}
    if set(selected) & dependencies:
        for group in ('Planning window', 'Site model and quantity streams', 'Field mappings and grade policy'):
            if group not in selected and value_hash({k: local.get(k) for k in GROUPS[group]}) != value_hash({k: incoming.get(k) for k in GROUPS[group]}):
                raise ValueError(f'Updated evidence uses a different {group.lower()}. Select that item too, or keep the current evidence.')
    result = dict(local)
    for group in selected:
        for key in GROUPS[group]:
            result[key] = deepcopy(incoming.get(key))
    if set(selected) & PLANNING_GROUPS:
        result.update(optimisation_input_revision='shared-inputs-changed', manual_input_revision='shared-inputs-changed')
        result['last_run_outcome'] = dict(status='inputs_updated', message='Prepared inputs updated. Recalculate to refresh results.')
        # Current Calendar, participation, targets and manual recipes remain
        # authoritative. Derived contexts are rebuilt by normal site activation.
        result['database_view_snapshot_signature'] = None
    if selected:
        from classes.CombinedOPFReconciliation import reusable_cache
        candidate = {**result, '_combined_opf_profile_cache': incoming.get('_combined_opf_profile_cache')}
        cache = reusable_cache(candidate)
        if cache:
            # Reuse Support's work only if it describes the complete merged
            # inputs, including the Planner fields that were left untouched.
            result['_combined_opf_profile_cache'] = deepcopy(cache)
    return result


def clone_database(source, destination):
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(destination)) as target:
        if source and Path(source).is_file():
            with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as original:
                original.backup(target)


def merge_database(local, incoming, destination, selected):
    """Build an isolated database; preserve all local plan/report tables."""
    clone_database(local, destination)
    patterns = [name for group in selected for name in TABLE_GROUPS.get(group, ())]
    if not patterns or not incoming or not Path(incoming).is_file():
        return
    def matches(name):
        return any(name.startswith(pattern) if pattern.endswith('_') else name == pattern for pattern in patterns)
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    with closing(sqlite3.connect(destination)) as connection:
        connection.execute('ATTACH DATABASE ? AS prepared', (str(incoming),))
        with connection:
            incoming_tables = {row[0]: row[1] for row in connection.execute("SELECT name, sql FROM prepared.sqlite_master WHERE type='table'") if matches(row[0])}
            existing = [row[0] for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'") if matches(row[0])]
            for name in existing:
                connection.execute('DROP TABLE ' + quoted(name))
            for name, schema in incoming_tables.items():
                connection.execute(schema)
                connection.execute(f'INSERT INTO main.{quoted(name)} SELECT * FROM prepared.{quoted(name)}')
            # Input caches and data-browser snapshots cannot describe a merge
            # of revisions. They are cheap to rebuild on next use.
            for name, in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall():
                if name in {'expit_input_cache', 'database_view_snapshot'}:
                    connection.execute('DELETE FROM ' + quoted(name))
