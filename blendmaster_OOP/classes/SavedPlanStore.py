"""Publish all manual review frames in one SQLite transaction."""
from contextlib import closing
import json
import math
import sqlite3
import pandas as pd
from classes.SavedResultViews import product_from_feed


def sql_value(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, sort_keys=True)
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp) or hasattr(value, 'isoformat'):
        return value.isoformat(sep=' ') if isinstance(value, pd.Timestamp) else value.isoformat()
    return value.item() if hasattr(value, 'item') else value


def replace_plan(connection, table, frame, plan_id, *, named=True):
    def quote(value):
        return '"' + str(value).replace('"', '""') + '"'
    # Audit frames are published separately below. Pandas propagates attrs
    # into each column access, multiplying large attached transport reports.
    frame = pd.DataFrame(frame, copy=True)
    frame.attrs = {}
    if named:
        frame['plan_id'] = str(plan_id)
    if not len(frame.columns):
        frame = pd.DataFrame(columns=['plan_id'])
    types = {column: 'REAL' if pd.api.types.is_numeric_dtype(frame[column]) else 'TEXT' for column in frame}
    connection.execute(f'CREATE TABLE IF NOT EXISTS {quote(table)} (' + ','.join(f'{quote(column)} {kind}' for column, kind in types.items()) + ')')
    existing = {row[1] for row in connection.execute(f'PRAGMA table_info({quote(table)})')}
    for column, kind in types.items():
        if column not in existing:
            connection.execute(f'ALTER TABLE {quote(table)} ADD COLUMN {quote(column)} {kind}')
    if named:
        connection.execute(f'DELETE FROM {quote(table)} WHERE plan_id=?', (str(plan_id),))
    else:
        connection.execute(f'DELETE FROM {quote(table)}')
    if not frame.empty:
        columns = ','.join(quote(column) for column in frame)
        parameters = ','.join('?' for _ in frame)
        connection.executemany(f'INSERT INTO {quote(table)} ({columns}) VALUES ({parameters})',
            (tuple(sql_value(value) for value in row) for row in frame.itertuples(index=False, name=None)))


def physical_profile(history):
    return pd.DataFrame([dict(stockpile=name, time=row['snapshot_datetime'], closing_balance=balance,
                              steady_state_number=row.get('steady_state_number'))
                         for row in history for name, balance in row.get('balances', {}).items()],
                        columns=['stockpile', 'time', 'closing_balance', 'steady_state_number'])


def write_manual_snapshot(database, plan_id, feed):
    attributes = feed.attrs
    product = attributes.get('manual_product_report')
    product = product if isinstance(product, pd.DataFrame) else product_from_feed(pd.DataFrame(feed, copy=False))
    graph = attributes.get('material_flow_topology')
    transport = attributes.get('transport_frames') or {}
    with closing(sqlite3.connect(database)) as connection, connection:
        replace_plan(connection, 'manual_blend_report', feed, plan_id, named=False)
        replace_plan(connection, 'manual_plan_blend_report', feed, plan_id)
        replace_plan(connection, 'manual_plan_product_build_report', product, plan_id)
        build = attributes.get('manual_build_report')
        build = build if isinstance(build, pd.DataFrame) else physical_profile(attributes.get('physical_balance_history', []))
        replace_plan(connection, 'manual_plan_build_report', build, plan_id)
        connection.execute('CREATE TABLE IF NOT EXISTS manual_material_flow_topology (plan_id TEXT PRIMARY KEY, topology_json TEXT, warnings_json TEXT)')
        connection.execute('DELETE FROM manual_material_flow_topology WHERE plan_id=?', (str(plan_id),))
        if graph:
            connection.execute('INSERT INTO manual_material_flow_topology VALUES (?,?,?)',
                (str(plan_id), json.dumps(graph, default=str), json.dumps(attributes.get('transport_warnings', []))))
        for table in ('transport_movements', 'transport_contents', 'transport_product_arrivals'):
            replace_plan(connection, 'manual_' + table, transport.get(table, pd.DataFrame()), plan_id)


def manual_copy(database, plan_id='Primary'):
    """Create an independent manual starting plan from one consistent saved solve.

    The feed, product, physical balances and FIFO state are copied together.
    Later optimiser runs cannot change this manual snapshot.
    """
    from classes.MaterialFlowReview import saved_flow_data
    data = saved_flow_data(plan_id, database)
    report = data['frames']['feed'].copy()
    if report.empty:
        raise ValueError('Run the optimiser before creating its manual starting plan.')
    report['blend_option'] = 'Manual'
    report['manual_origin'] = 'Copied optimised allocations'
    report.attrs.update(manual_product_report=data['frames']['product'].copy(),
        manual_build_report=data['frames']['build'].copy(), material_flow_topology=data['graph'],
        transport_frames={k:v.copy() for k,v in data['frames'].items() if k.startswith('transport_')},
        transport_warnings=data['warnings'])
    return report
