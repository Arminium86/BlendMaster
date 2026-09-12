"""Calendar equipment limits applied to the final physical manual recipe."""
import math
import pandas as pd
from classes.MultiFeedCalendar import calendar_key


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def period_limit(calendar, field, period, point='', mode='single'):
    label = 'Preplan' if period == 'preplan' else period.title()
    if mode != 'single':
        key = calendar_key(point, field)
    else:
        key = 'reclaim_equipment_max_reclaim_rate' if field == 'max_reclaim_rate' else field
    values = calendar.get(key)
    if not isinstance(values, dict):
        return number(values)
    return number(values.get(label, values.get(label.replace('_', ' '), values.get(period))))


def equipment_violations(report, calendar, periods, *, mode='single', require_limits=False):
    if report is None or report.empty:
        return []
    # Pure recipe replay may have no Calendar; UI runs and exports require it.
    configured = any(key in ('crusher_rate', 'reclaim_equipment_max_reclaim_rate')
                     or key.startswith('tipping_point_') for key in calendar)
    if (not periods or not configured) and not require_limits:
        return []
    frame = report.copy()
    required = {'start_datetime', 'end_datetime', 'source_actual_tonnes'}
    if not required.issubset(frame):
        return ['The saved manual report lacks the timestamps/tonnes required for equipment validation.']
    frame['_start'] = pd.to_datetime(frame.start_datetime, errors='coerce')
    frame['_end'] = pd.to_datetime(frame.end_datetime, errors='coerce')
    frame['_point'] = frame.get('tipping_point', '')
    frame['_tonnes'] = pd.to_numeric(frame.source_actual_tonnes, errors='coerce')
    if frame[['_start', '_end', '_tonnes']].isna().any().any():
        return ['The manual report contains invalid timestamps or source tonnes.']
    if (frame['_tonnes'] < -1e-6).any() or not frame['_tonnes'].map(math.isfinite).all():
        return ['Source tonnes must be finite and non-negative.']
    if (frame['_end'] <= frame['_start']).any():
        return ['Manual state duration must be positive.']
    boundaries = [(key[:-6], pd.Timestamp(start), pd.Timestamp(periods[key[:-6] + '_end']))
                  for key, start in periods.items() if key.endswith('_start') and key[:-6] + '_end' in periods]
    errors = []
    group_keys = ['_point', '_start', '_end']
    if 'steady_state_number' in frame:
        group_keys.append('steady_state_number')
    for _, state in frame.groupby(group_keys, dropna=False, sort=False):
        first = state.iloc[0]
        duration = number(first.get('steady_state_duration'))
        if duration is None:
            duration = (first['_end'] - first['_start']).total_seconds() / 3600
        if duration <= 0:
            errors.append('Manual state duration must be positive.')
            continue
        point = str(first['_point'] or '')
        blend = first.get('blend_ID', first.get('steady_state_number', '?'))
        source_type = state.get('source_type', pd.Series('', index=state.index)).fillna('').astype(str).str.lower()
        if 'equipment' in state:
            source_type = source_type.mask(source_type.eq(''), state.equipment.fillna('').astype(str).map(
                lambda value: 'stockpile' if value.upper().startswith('RC') else 'grade_block'))
        stock = state[source_type.eq('stockpile')].copy()
        if not stock.empty:
            names = stock.get('parent_stockpile', pd.Series('', index=stock.index)).fillna('').astype(str)
            names = names.mask(names.eq(''), stock.get('source', pd.Series('source', index=stock.index)))
            reclaim = pd.to_numeric(stock.get('reclaimer_source_tonnes', stock['_tonnes']), errors='coerce')
            source_rates = (reclaim.groupby(names).sum(min_count=1) / duration).to_dict()
        else:
            source_rates = {}
        crusher_tonnes = pd.to_numeric(state.get('crusher_source_tonnes', state['_tonnes']), errors='coerce')
        checks = [('crusher_rate', 'crusher', crusher_tonnes.sum(min_count=1) / duration)]
        checks += ([('max_reclaim_rate', source, rate) for source, rate in source_rates.items()]
                   if mode == 'single' else [('max_reclaim_rate', 'aggregate reclaim', sum(source_rates.values()))])
        if require_limits and 'crusher_rate_input' in state:
            rate = pd.to_numeric(state.crusher_rate_input, errors='coerce').max()
            if pd.notna(rate):
                checks.append(('crusher_rate', 'configured crusher rate', float(rate)))
        overlapping = [key for key, start, end in boundaries if start < first['_end'] and end > first['_start']]
        if not overlapping:
            errors.append(f'Blend {blend}: state is outside the configured Calendar horizon.')
        elif boundaries:
            cursor = first['_start']
            for _, start, end in sorted(boundaries, key=lambda value: value[1]):
                if start <= cursor < end:
                    cursor = end
            if cursor < first['_end']:
                errors.append(f'Blend {blend}: Calendar periods do not cover the full blend interval.')
        for period in overlapping:
            for field, source, rate in checks:
                limit = period_limit(calendar, field, period, point, mode)
                if limit is None:
                    if require_limits:
                        errors.append(f'Blend {blend}, {point or source}, {period}: Calendar {field} is missing or invalid.')
                    continue
                tolerance = max(1e-6, abs(limit) * 1e-7)
                if limit < 0 or not math.isfinite(rate) or rate < -tolerance or rate > limit + tolerance:
                    errors.append(f'Blend {blend}, {point + "/" if point else ""}{source}, {period}: '
                                  f'{rate:,.4f} t/h exceeds Calendar {limit:,.4f} t/h.')
    return list(dict.fromkeys(errors))


def require_equipment_limits(report, calendar, periods, **kwargs):
    errors = equipment_violations(report, calendar, periods, **kwargs)
    if errors:
        raise ValueError('Manual equipment limits:\n' + '\n'.join(errors[:20]))
