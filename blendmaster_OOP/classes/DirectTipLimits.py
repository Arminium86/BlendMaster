"""Hard Calendar direct-tip limits, independent of product grade preferences."""
import math
import pandas as pd
from classes.EquipmentLimits import period_limit


def validate_direct_tip_sources(config, blocks, targets, calendar=None, payload_count=0):
    """Reject positive minima with no eligible source at the physical point."""
    from classes.MultiFeedSettings import multi_feed_settings, period_lanes, build_opfs
    from classes.ProductTargets import product_targets_value
    from classes.PlanningPrerequisites import PlanningPrerequisiteError
    settings = multi_feed_settings(config.get('multi_feed_settings'))
    routing = config.get('direct_tip_point_by_payload') or {}
    usable = [block for block in blocks if float(block.balance or 0) > 0]
    missing = {}
    # Future periods may never be reached (a build can finish earlier). The
    # solver owns their hard limits when it reaches them; preflight checks only
    # the opening period and OPFs with work to do.
    opening = 'preplan' if 'preplan' in targets else next(iter(targets), None)
    for period, target in targets.items():
        if period != opening:
            continue
        if settings['mode'] == 'single':
            required = period_limit(calendar or {}, 'crusher_direct_tip_ratio_min', period)
            target = {**target, 'direct_feed_ratio_min': target.get('direct_feed_ratio_min', 0) if required is None else required}
            points = [dict(name='Crusher', target=target, direct_tip_enabled=config.get('direct_tip_enabled', True))]
        else:
            points = period_lanes(settings, period, target)
        for point in points:
            if config.get('product_builds_configured') and settings['mode'] == 'combined_opf':
                builds = product_targets_value(calendar or {}, [])
                if not any(not build_opfs(build) or point['opf'] in build_opfs(build) for build in builds):
                    continue
            minimum = float(point['target'].get('direct_feed_ratio_min') or 0)
            if minimum <= 0 or float(point['target'].get('crusher_rate') or 0) <= 0:
                continue
            eligible = point['direct_tip_enabled'] and any(settings['mode'] == 'single'
                or point['name'] in routing.get(str(block.name), []) for block in usable)
            if not eligible:
                missing.setdefault(point['name'], []).append(f'{period}: {minimum:.1%}')
    if missing:
        details = '; '.join(f'{point} ({", ".join(values)})' for point, values in missing.items())
        raise PlanningPrerequisiteError(
            f'Calendar requires a hard direct-tip minimum at {details}, but no eligible direct-tip '
            f'grade-block payload is routed there. {payload_count} payloads were prepared; '
            f'{len(usable)} became eligible direct-tip sources. Database View also includes stockpile-bound '
            'payloads, which are not automatically permitted to direct tip. Review Direct Tip Movement Rules '
            'and selected crushers in Guidance Schedules, then resubmit. The minimum has not been relaxed.',
            'guidance_schedules')


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
