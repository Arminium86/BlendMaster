"""Persist transport reports by plan without confusing arrivals with source depletion."""
import json
from contextlib import closing
import sqlite3
import pandas as pd
from database.DatabaseContext import get_database_path

TABLES = ('transport_movements', 'transport_contents', 'transport_product_arrivals')


def write_transport_reports(case, database_name=None):
    flow = getattr(case,'transport',None)
    if hasattr(case,'planning_horizon_end') and hasattr(case,'start_time'):
        from classes.CrossFeatureReports import write_case_audits
        write_case_audits(case,database_name)
    frames = [pd.DataFrame(flow.movements) if flow else pd.DataFrame(),
              pd.DataFrame(flow.snapshots) if flow else pd.DataFrame(),
              case.product_arrival_results.copy() if flow else pd.DataFrame()]
    plan = str(getattr(case,'plan_id','Primary'))
    with closing(sqlite3.connect(database_name or get_database_path())) as connection, connection:
        graph = getattr(case,'material_flow_topology',None)
        if graph:
            connection.execute('CREATE TABLE IF NOT EXISTS material_flow_topology (plan_id TEXT PRIMARY KEY, topology_json TEXT, warnings_json TEXT)')
            connection.execute('INSERT OR REPLACE INTO material_flow_topology VALUES (?,?,?)',
                               (plan,json.dumps(graph,default=str),json.dumps(flow.warnings if flow else [])))
        for table, frame in zip(TABLES,frames):
            frame['plan_id'] = plan
            for column in frame:
                if frame[column].dtype == object:
                    frame[column] = frame[column].map(lambda value: json.dumps(value,default=str)
                        if isinstance(value,(dict,list,tuple)) else value)
            existing = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
            if not existing:
                frame.head(0).to_sql(table,connection,index=False)
                existing = set(frame.columns)
            for column in set(frame.columns)-existing:
                quoted = column.replace('"','""')
                dtype = 'REAL' if pd.api.types.is_numeric_dtype(frame[column]) else 'TEXT'
                connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{quoted}" {dtype}')
            connection.execute(f'DELETE FROM "{table}" WHERE plan_id=?',(plan,))
            if not frame.empty:
                frame.to_sql(table,connection,if_exists='append',index=False)


def read_transport_reports(database_name=None, plan_id='Primary'):
    result = {}
    with closing(sqlite3.connect('file:'+str(database_name or get_database_path()).replace('\\','/')+'?mode=ro',uri=True)) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            result[table] = pd.read_sql_query(f'SELECT * FROM "{table}" WHERE plan_id=?',connection,params=(plan_id,)) if table in tables else pd.DataFrame()
    return result
