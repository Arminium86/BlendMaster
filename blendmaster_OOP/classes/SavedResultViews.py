"""Read a named result without crossing optimised/manual or plan boundaries."""
from contextlib import closing
from pathlib import Path
import sqlite3
import pandas as pd
from classes.ProductBuildProgress import ProductBuildProgress

REPORT_TABLES = {
    'optimised': {'feed': ('optimisation_plan_blend_report', 'optimised_blend_report'),
                  'product': ('optimisation_plan_product_build_report', 'product_build_report'),
                  'build': ('optimisation_plan_build_report', 'build_report')},
    'manual': {'feed': ('manual_plan_blend_report', 'manual_blend_report'),
               'product': ('manual_plan_product_build_report', 'manual_product_build_report'),
               'build': ('manual_plan_build_report', 'manual_build_report')},
}


def read_from_connection(connection, plan_type='optimised', plan_id='Primary', kind='feed'):
    table, fallback = REPORT_TABLES[plan_type][kind]
    names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table in names:
        return pd.read_sql_query(f'SELECT * FROM "{table}" WHERE plan_id=?', connection, params=(plan_id,))
    if plan_id == 'Primary' and fallback in names:
        return pd.read_sql_query(f'SELECT * FROM "{fallback}"', connection)
    if kind == 'product' and plan_type == 'manual':
        return product_from_feed(read_from_connection(connection, plan_type, plan_id))
    return pd.DataFrame()


def read_reports(database, plan_type='optimised', plan_id='Primary', kinds=('feed', 'product', 'build')):
    if not Path(database).is_file():
        return {kind: pd.DataFrame() for kind in kinds}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        return {kind: read_from_connection(connection, plan_type, plan_id, kind) for kind in kinds}


def read_report(database, plan_type='optimised', plan_id='Primary', kind='feed'):
    return read_reports(database, plan_type, plan_id, (kind,))[kind]


def plan_names(database, plan_type='optimised'):
    if not Path(database).is_file():
        return []
    table, fallback = REPORT_TABLES[plan_type]['feed']
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table in names:
            result = [r[0] for r in connection.execute(f'SELECT DISTINCT plan_id FROM "{table}"')]
        elif fallback in names and connection.execute(f'SELECT 1 FROM "{fallback}" LIMIT 1').fetchone():
            result = ['Primary']
        else:
            result = []
    return sorted(result, key=lambda name: (name != 'Primary', name))


def product_from_feed(feed):
    """Project saved cumulative manual build values; never recalculate from current targets."""
    rows = []
    if feed.empty:
        return pd.DataFrame()
    for lane in ('product', 'lump', 'fines'):
        prefix = 'product_build' + ('_' + lane if lane != 'product' else '')
        identity = prefix + '_id'
        if identity not in feed:
            continue
        selected = feed.loc[feed[identity].notna() & feed[identity].astype(str).str.strip().ne('')]
        for row in selected.to_dict('records'):
            record = {key: row.get(key) for key in ('steady_state_number', 'blend_ID', 'tipping_point', 'opf', 'steady_state_duration')}
            record.update(product_build_lane=lane, product_build_id=row[identity],
                          product_build_name=row.get(prefix + '_name'),
                          steady_state_start_datetime=row.get('start_datetime'),
                          steady_state_end_datetime=row.get('end_datetime'))
            for suffix in ProductBuildProgress.BASE_SUFFIXES:
                value = row.get(prefix + '_' + suffix)
                if suffix in ('id', 'name', 'brand'):
                    target = 'product_build_' + suffix
                elif suffix in ('opening_tonnes', 'added_tonnes', 'closing_tonnes', 'remaining_tonnes') or suffix.startswith('grade_'):
                    target = 'build_' + suffix
                else:
                    target = suffix
                record[target] = value
            # Target OPF belongs to the build and may combine several contributors.
            record['opf'] = next((row.get(key) for key in (prefix + '_opf', 'opf')
                                  if pd.notna(row.get(key)) and str(row.get(key)).strip()), '')
            rows.append(record)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    keys = ['opf', 'product_build_lane', 'product_build_id', 'steady_state_number',
            'steady_state_start_datetime', 'steady_state_end_datetime']
    return frame.drop_duplicates(keys).reset_index(drop=True)
