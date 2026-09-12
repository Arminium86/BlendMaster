"""Operational Blend Plan layouts preserve each physical tipping point."""
from copy import deepcopy
import pandas as pd
from classes.OptimisedToManualPlan import OptimisedToManualPlan
from classes.ManualBlendSummary import ManualBlendSummary

DETAIL_COLUMNS = ['start_datetime','end_datetime','steady_state_number','blend_ID','tipping_point','opf',
                  'source','source_type','source_actual_tonnes','source_opening_balance','source_closing_balance',
                  'source_blend_ratio','source_grade_fe','source_grade_si','source_grade_al','source_grade_p',
                  'source_grade_mn','crusher_rate_output','selected_grade_stream']


def split_blend_plans(report,stockpile_data=None,default_point=None):
    if report is None or report.empty:
        return {}
    report = report.copy()
    if 'tipping_point' not in report or report['tipping_point'].fillna('').astype(str).str.strip().eq('').any():
        if not default_point:
            candidates = report.get('crusher', pd.Series(dtype=str)).dropna().astype(str).unique()
            default_point = candidates[0] if len(candidates) == 1 else None
        if not default_point:
            raise ValueError('This saved plan has no tipping-point identity. Select its configured site model before exporting.')
        if 'tipping_point' not in report:
            report['tipping_point'] = str(default_point)
        else:
            missing = report['tipping_point'].fillna('').astype(str).str.strip().eq('')
            report.loc[missing, 'tipping_point'] = str(default_point)
    result = {}
    for point,rows in report.groupby('tipping_point',sort=False,dropna=False):
        rows = rows[pd.to_numeric(rows.source_actual_tonnes,errors='coerce').fillna(0)>0].copy()
        if rows.empty:
            continue
        transfer = OptimisedToManualPlan(rows,stockpile_data).build()
        summaries = ManualBlendSummary.build(transfer['sequence_rows'],rows,transfer['blend_definitions'])
        for summary in summaries:
            summary['Tipping point'] = str(point)
            summary['OPF'] = ', '.join(rows.get('opf',pd.Series(dtype=str)).dropna().astype(str).unique())
        ratios = []
        for _,state in rows.groupby(['steady_state_number','start_datetime','end_datetime'],dropna=False,sort=False):
            physical = pd.to_numeric(state.source_actual_tonnes,errors='coerce').fillna(0)
            total = physical.sum()
            for (index,row),tonnes in zip(state.iterrows(),physical):
                actual = tonnes/total if total else 0
                # Detail, summary and ratio sheets must use the same physical
                # recipe even when legacy saved ratios were display-rounded.
                rows.loc[index, 'source_blend_ratio'] = actual
                original = pd.to_numeric(row.get('original_source_feed_ratio',actual),errors='coerce')
                original = actual if pd.isna(original) else float(original)
                rounded = pd.to_numeric(row.get('rounded_source_feed_ratio',actual),errors='coerce')
                rounded = actual if pd.isna(rounded) else float(rounded)
                ratios.append({'Tipping point':point,'Steady state':row['steady_state_number'],'Blend ID':row.get('blend_ID',''),
                    'Source':row['source'],'Source type':row.get('source_type',''),
                    'Original ratio (%)':original*100,'Operational ratio (%)':rounded*100,
                    'Increment (%)':row.get('ratio_rounding_increment'),
                    'Timing adjustment':row.get('rounding_timing_reason',''),
                    'Sequence status':row.get('rounding_stop_reason',''),
                    'Original start':row.get('original_state_start',row['start_datetime']),'Original end':row.get('original_state_end',row['end_datetime']),
                    'Recalculated start':row['start_datetime'],'Recalculated end':row['end_datetime']})
        result[str(point)] = dict(transfer=transfer,report=rows,summary=pd.DataFrame(summaries),ratios=pd.DataFrame(ratios))
    return result


def operational_sheets(plans):
    sheets = []
    for point,plan in plans.items():
        sequence = [{key:value for key,value in row.items() if not key.startswith('_')}
                    for row in plan['transfer']['sequence_rows']]
        sheets.extend([(f'{point} Summary',plan['summary']),
                       (f'{point} Sequence',pd.DataFrame(sequence)),
                       (f'{point} Ratios',plan['ratios']),
                       (f'{point} Detail',plan['report'])])
    return sheets
