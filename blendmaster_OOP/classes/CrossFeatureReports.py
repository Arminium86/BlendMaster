"""Plan-owned cross-feature audit snapshots and export sheets."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import pandas as pd
from database.DatabaseContext import get_database_path
from classes.ProductQualityReport import quality_report_rows
from classes.PrimaryDestinationAllocator import AUDIT_COLUMNS
from classes.DestinationPlanReport import PUBLICATION_COLUMNS
from classes.TransportReports import read_transport_reports

def input_audit_snapshot(state, *, copy_evidence=True):
    audits = []
    for source,row in (state.get('updated_stockpile_data') or state.get('stockpile_data') or {}).items():
        if row.get('reconciliation'):
            audits.append(row['reconciliation'])
    outcomes = {}
    for footprint,rows in (state.get('AMT_stockpile_data') or {}).items():
        for row in rows:
            if row.get('reconciliation'):
                audits.append(row['reconciliation'])
            audit = row.get('AMT_FOOTPRINT_AUDIT')
            if audit:
                outcomes[footprint] = audit
    outcomes.update({name:audit for name,audit in (state.get('AMT_footprint_exclusions') or {}).items() if audit.get('excluded')})
    result = dict(reconciliation=audits, amt=list(outcomes.values()))
    return deepcopy(result) if copy_evidence else result

def factor_rows(audits):
    result,seen = [],set()
    for audit in audits:
        identity = (audit.get('opf'),audit.get('source_id'),audit.get('hex_id'),audit.get('source_kind'))
        if identity in seen:
            continue
        seen.add(identity)
        for brand,details in audit.get('by_brand',{}).items():
            for component in details.get('records') or [details]:
                row = dict(opf=audit.get('opf'),source=audit.get('source_id'),hex=audit.get('hex_id'),
                    source_kind=audit.get('source_kind'),brand=brand,method=audit.get('method'),
                    source_wmt=audit.get('source_wmt'),grade_block=component.get('grade_block_key'),
                    resolution_level=component.get('resolution_level','aggregate'),
                    lineage_fraction=component.get('lineage_fraction'),
                    evidence_match_score_percent=component.get('confidence_percent',details.get('confidence_percent')),
                    score_basis='Evidence match; not statistical confidence',
                    global_fallback_fraction=details.get('global_fraction'),
                    manual_override=component.get('manual_override',False),
                    provenance=component.get('provenance',{}),warnings=audit.get('warnings',[]))
                for kind in ('blend','regression'):
                    factors = component.get(kind+'_factors') or details.get('applied_factors',{}).get(kind,{})
                    row.update({kind+'_'+a: factors.get(a) for a in ('fe','si','al','p','mn')})
                result.append(row)
    return result

def case_audits(case):
    context = getattr(case,'site_context',{}) or {}
    snapshot = context.get('reporting_input_audits') or {}
    audits = list(snapshot.get('reconciliation',[]))
    for profile in (context.get('opf_profiles') or {}).values():
        audits.extend(profile.get('reconciliation_audits',[]))
        for rows in ('inventory','chunks'):
            audits.extend(r['reconciliation'] for r in profile.get(rows,{}).values() if r.get('reconciliation'))
    factors = {}
    factors[context.get('opf','OPF')] = context.get('historical_recon_factors',{})
    factors.update({opf:bundle.get('factors',{}) for opf,bundle in context.get('opf_reconciliation_inputs',{}).items()
                    if opf != context.get('opf')})
    global_rows = []
    for opf,brands in factors.items():
        for brand,kinds in brands.items():
            for kind,values in kinds.items():
                if kind not in ('blend','regression') or not isinstance(values,dict):
                    continue
                for analyte,value in values.items():
                    global_rows.append(dict(opf=opf,brand=brand,factor_kind=kind,analyte=analyte,
                                            **(value if isinstance(value,dict) else {'effective':value})))
    flow = getattr(case,'transport',None)
    if flow:
        audits.extend(flow.opening_reconciliation_audits)
    from classes.PlanningPersistence import PLANNING_SEMANTICS_VERSION, settings_signature
    caveats = [dict(topic='Planning inputs',note=f'Semantics version {PLANNING_SEMANTICS_VERSION}; settings fingerprint {settings_signature({**context,"solver_config":case.solver_config})}.'),
               dict(topic='Physical balances',note='Source depletion uses physical ROM WMT. Product quantities and grade weights use the selected mapped fields.'),
               dict(topic='Reconciliation score',note='Evidence match score is a descriptive match to historical source composition; it is not statistical uncertainty.'),
               dict(topic='Plan horizon',note=f'Starts {case.start_time}; computed through {case.current_time}; schedule ends {case.planning_horizon_end()}.')]
    if flow:
        caveats += [dict(topic='Transport',note='Product Targets use OPF arrivals; crusher targets use tipping. Closing material stays in transit or COS. No drain extension.'),
                    dict(topic='Opening evidence',note='Actual movement tonnes use mapped modelled chemistry and OPF reconciliation. Unobserved opening capacity remains empty. Inferred opening feed = 0 ROM WMT.'),
                    dict(topic='Transport planning',note='Future tips are selected in successive decision states. Arrival feasibility is checked at each state; a fixed off-spec opening queue can make a plan infeasible.')]
        caveats.extend(dict(topic='Opening coverage',note=text) for text in flow.warnings)
    return {'Reconciliation Factors':factor_rows(audits),'Global Factors':global_rows,
            'AMT Outcomes':snapshot.get('amt',[]),'Plan Notes':caveats}

def write_case_audits(case,database_name=None):
    plan = str(getattr(case,'plan_id','Primary'))
    data = case_audits(case)
    with closing(sqlite3.connect(database_name or get_database_path())) as connection,connection:
        connection.execute('CREATE TABLE IF NOT EXISTS plan_feature_audits (plan_id TEXT, section TEXT, records_json TEXT, PRIMARY KEY (plan_id,section))')
        connection.execute('DELETE FROM plan_feature_audits WHERE plan_id=?',(plan,))
        connection.executemany('INSERT INTO plan_feature_audits VALUES (?,?,?)',
            [(plan,key,json.dumps(rows,default=str)) for key,rows in data.items()])

def cross_feature_sheets(plan_id='Primary',database_name=None,product_report=None,plan_type='optimised'):
    path = database_name or get_database_path()
    result = []
    with closing(sqlite3.connect('file:'+str(path).replace('\\','/')+'?mode=ro',uri=True)) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'plan_feature_audits' in tables:
            result += [(name,pd.DataFrame(json.loads(rows))) for name,rows in connection.execute(
                       'SELECT section,records_json FROM plan_feature_audits WHERE plan_id=? ORDER BY section',(plan_id,))]
        labels = dict(destination_allocation_runs='Destination Activity',destination_primary_assignments='Destination Assignments',
                      destination_capacity_ledger='Destination Build Order',destination_capacity_balances='Destination Balances',
                      material_destination_plan='Material Destination Plan')
        if 'plan_readiness' in tables:
            result.append(('Plan Readiness', pd.read_sql_query(
                'SELECT "check",status,detail FROM plan_readiness WHERE plan_id=? AND plan_type=?',
                connection, params=(plan_id, plan_type))))
        for table in dict.fromkeys([*AUDIT_COLUMNS,*PUBLICATION_COLUMNS,'material_destination_plan']):
            if table not in tables:
                continue
            cols = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
            if not {'plan_id','plan_type'}.issubset(cols):
                continue
            frame = pd.read_sql_query(f'SELECT * FROM "{table}" WHERE plan_id=? AND lower(plan_type)=?',connection,params=(plan_id,plan_type))
            result.append((labels.get(table,table),frame))
    if product_report is not None:
        result.append(('OPF Contributions',product_report))
        result.append(('Product Quality',pd.DataFrame(quality_report_rows(product_report))))
        result.append(('Cumulative Quality',pd.DataFrame(quality_report_rows(product_report,grain='cumulative_build'))))
    if plan_type=='optimised':
        result += [(name.replace('transport_','').replace('_',' ').title(),frame)
                   for name,frame in read_transport_reports(path,plan_id).items()]
    sections = dict(result)
    expected = ('Reconciliation Factors', 'Global Factors', 'AMT Outcomes', 'Plan Notes',
                'Destination Activity', 'Destination Assignments', 'Product Quality', 'Cumulative Quality')
    coverage = [dict(plan_id=plan_id, plan_type=plan_type, section=name,
                     status=('missing saved snapshot' if name not in sections else
                             'empty saved section' if sections[name].empty else 'available'),
                     rows=len(sections[name]) if name in sections else 0)
                for name in dict.fromkeys([*expected, *sections])]
    result.insert(0, ('Audit Coverage', pd.DataFrame(coverage)))
    return result
