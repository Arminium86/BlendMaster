"""Recover sub-second intervals from legacy reports that retained exact duration."""
import pandas as pd


def restore_report_timing(report):
    data=report.copy()
    if data.empty or not {'start_datetime','end_datetime'}.issubset(data): return data
    for key in ('start_datetime','end_datetime'):
        data[key]=pd.to_datetime(data[key],errors='coerce',format='mixed')
    if 'steady_state_duration' not in data: return data
    contexts=[c for c in ('plan_id','blend_option') if c in data]
    groups=data.groupby(contexts,sort=False,dropna=False) if contexts else [(None,data)]
    for _,plan in groups:
        previous_end=previous_recorded_end=None
        keys=[c for c in ('steady_state_number','start_datetime','end_datetime') if c in data]
        ordered=plan.sort_values(['start_datetime','end_datetime'],kind='stable')
        for _,rows in ordered.groupby(keys,sort=False,dropna=False):
            first=rows.iloc[0]; start,end=first.start_datetime,first.end_datetime
            duration=pd.to_numeric(first.steady_state_duration,errors='coerce')
            if pd.isna(start) or pd.isna(end): continue
            if pd.notna(duration) and duration>0 and abs(duration*3600-(end-start).total_seconds())<1.001:
                begin=previous_end if previous_recorded_end==start else start
                finish=(begin+pd.Timedelta(hours=duration)).round('us')
                if abs((finish-end).total_seconds())<1.001:
                    if abs((finish-end).total_seconds())<.00001: finish=end
                    data.loc[rows.index,'start_datetime']=begin
                    data.loc[rows.index,'end_datetime']=finish
                    previous_end,previous_recorded_end=finish,end
                    continue
            previous_end,previous_recorded_end=end,end
    return data
