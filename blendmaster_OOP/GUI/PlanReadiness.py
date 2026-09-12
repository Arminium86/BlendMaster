"""Visible and exported plan acceptance evidence, independent of solver success."""
from copy import deepcopy
from contextlib import closing
import sqlite3
import pandas as pd
from PyQt5.QtWidgets import QMessageBox
from classes.PlanReadiness import evaluate, physical_violations
from classes.EquipmentLimits import equipment_violations
from classes.PeriodManager import PeriodManager
from database.DatabaseContext import get_database_path


def read_table(connection, table, plan_id, plan_type):
    try:
        columns = [r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')]
        where, values = [], []
        for key, value in (('plan_id', plan_id), ('plan_type', plan_type)):
            if key in columns:
                where.append(f'lower("{key}") = lower(?)')
                values.append(value)
        return pd.read_sql_query(f'SELECT * FROM "{table}"' + (' WHERE ' + ' AND '.join(where) if where else ''), connection, params=values)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()


def result(host, report, plan_id='Primary', plan_type='manual', *, database=None):
    path = database or get_database_path()
    periods = PeriodManager(host.planning_period_count())
    periods.calculate_periods(host.start_time_choice)
    errors = equipment_violations(report, host.calendar_inputs or {}, periods.get_periods(),
        mode=(getattr(host, 'multi_feed_configuration', {}) or {}).get('mode', 'single'), require_limits=True)
    with closing(sqlite3.connect(path)) as connection:
        destination = read_table(connection, 'material_destination_plan', plan_id, plan_type)
    readiness = evaluate(report, getattr(host, 'product_targets', []) or [], destination=destination,
        equipment_errors=errors, run_status='complete' if plan_type == 'manual' else (getattr(host, 'last_run_outcome', {}) or {}).get('status', 'complete'))
    from GUI.WorkflowDependencies import input_revision, manual_revision
    recorded = vars(host).get('manual_input_revision' if plan_type == 'manual' else 'optimisation_input_revision')
    current = manual_revision(host) if plan_type == 'manual' else input_revision(host)
    status = 'pass' if recorded == current else 'blocked'
    detail = ('Input version matches the generated plan.' if status == 'pass' else
              'Inputs changed after this plan was generated. Regenerate the plan before export.' if recorded else
              'This saved plan predates input-version tracking. Regenerate it to validate freshness.')
    readiness['checks'].insert(1, dict(check='Input freshness', status=status, detail=detail))
    if status != 'pass':
        readiness['status'] = 'requires_review'
        readiness['message'] += ' ' + detail
    return readiness


def save(path, readiness, plan_id, plan_type):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS plan_readiness (plan_id TEXT, plan_type TEXT, "check" TEXT, status TEXT, detail TEXT)')
        connection.execute('DELETE FROM plan_readiness WHERE plan_id=? AND plan_type=?', (plan_id, plan_type))
        connection.executemany('INSERT INTO plan_readiness VALUES (?,?,?,?,?)',
            [(plan_id, plan_type, row['check'], row['status'], row['detail']) for row in readiness['checks']])
        connection.commit()


def refresh(host, report=None, plan_id='Primary', plan_type='optimised'):
    if not getattr(host, 'start_time_choice', None):
        return
    path = get_database_path()
    if report is None:
        with closing(sqlite3.connect(path)) as connection:
            table = 'manual_blend_report' if plan_type == 'manual' else 'optimisation_plan_blend_report'
            report = read_table(connection, table, plan_id, plan_type)
            if report.empty and plan_type == 'optimised' and plan_id == 'Primary':
                report = read_table(connection, 'optimised_blend_report', plan_id, plan_type)
    readiness = result(host, report, plan_id, plan_type, database=path)
    save(path, readiness, plan_id, plan_type)
    if plan_type == 'optimised':
        host.plan_readiness = readiness
    host.plan_readiness_stale = False
    label = vars(host).get('calendar_workflow_status')
    if label and plan_type == 'optimised':
        label.setText(readiness['message'])
    label = vars(host).get('plan_readiness_label')
    if label:
        label.setText(readiness['message'])
    return readiness


def publication_check(host, report, plan_id, plan_type):
    readiness = refresh(host, report, plan_id, plan_type)
    blocked = [row['detail'] for row in readiness['checks'] if row['status'] == 'blocked']
    if blocked:
        raise ValueError('Plan export blocked: ' + '; '.join(blocked))
    return readiness
