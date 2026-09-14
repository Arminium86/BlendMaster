"""Hard Calendar direct-tip limits, independent of product grade preferences."""
import math
import pandas as pd
from classes.EquipmentLimits import period_limit


def direct_tip_violations(report, calendar, periods, mode='single'):
    if report is None or report.empty:
        return []
    frame = report.copy()
    if not {'start_datetime','end_datetime','source_actual_tonnes','source_type'}.issubset(frame):
        return ['The saved plan lacks the feed evidence needed to check direct-tip limits.']
    frame = frame.loc[frame.source_type.ne('transport')].copy()
    frame['_start'] = pd.to_datetime(frame.start_datetime, errors='coerce')
    frame['_end'] = pd.to_datetime(frame.end_datetime, errors='coerce')
    frame['_point'] = frame.get('tipping_point','')
    frame['_tonnes'] = pd.to_numeric(frame.source_actual_tonnes,errors='coerce').fillna(0)
    errors = []
    if mode != 'single':
        from urllib.parse import unquote
        for key in calendar:
            if not key.startswith('tipping_point_') or not key.endswith('_direct_feed_ratio_min'):
                continue
            point=unquote(key[len('tipping_point_'):-len('_direct_feed_ratio_min')])
            if point in set(frame._point):
                continue
            for period_key,begin in periods.items():
                if not period_key.endswith('_start'): continue
                period=period_key[:-6]
                minimum=period_limit(calendar,'direct_feed_ratio_min',period,point,mode)
                rate=period_limit(calendar,'crusher_rate',period,point,mode)
                if (minimum and rate and pd.Timestamp(begin)<frame._end.max()
                        and pd.Timestamp(periods[period+'_end'])>frame._start.min()):
                    errors.append(f'{point}, {period}: no tipping is recorded; Calendar requires at least {minimum:.2%} direct tip.')
    for (point, start, end), rows in frame.groupby(['_point','_start','_end'],dropna=False):
        total = rows._tonnes.sum()
        ratio = rows.loc[rows.source_type.eq('grade_block'),'_tonnes'].sum()/total if total > 1e-7 else 0
        for key, begin in periods.items():
            if not key.endswith('_start'):
                continue
            period = key[:-6]
            if not (pd.Timestamp(begin) < end and pd.Timestamp(periods[period+'_end']) > start):
                continue
            values=[]
            for bound in ('min','max'):
                field = 'direct_feed_ratio_'+bound if mode!='single' else 'crusher_direct_tip_ratio_'+bound
                values.append(period_limit(calendar,field,period,point,mode))
            lo, hi = values
            if lo is None and hi is None:
                continue
            lo,hi = (0 if lo is None else lo),(1 if hi is None else hi)
            if not 0 <= lo <= hi <= 1 or not math.isfinite(ratio) or ratio < lo-1e-7 or ratio > hi+1e-7:
                errors.append(f'{point or "Crusher"}, {period}, {start}: direct tip {ratio:.2%}; Calendar requires {lo:.2%}–{hi:.2%}.')
    return list(dict.fromkeys(errors))
