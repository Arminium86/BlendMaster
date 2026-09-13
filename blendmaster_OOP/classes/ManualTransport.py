"""Replay an accepted manual feed through the same Conveyor/COS FIFO engine."""
from copy import deepcopy
import math
import pandas as pd
from classes.ConveyorCOS import ConveyorCOS, moment, material, material_event
from classes.EventData import EventData
from classes.GradeStreams import ANALYTES, STREAMS, apply_selected_stream
from classes.ProductBuildProgress import ProductBuildProgress
from classes.SavedResultViews import product_from_feed
from classes.TransportSettings import transport_settings, transport_enabled, reference_rate
from classes.MultiFeedSettings import multi_feed_settings
from classes.EquipmentLimits import period_limit


def event_from_row(row, config):
    quantity = float(row['source_actual_tonnes'])
    properties = {k[len('source_property_'):]: float(v) for k,v in row.items()
        if k.startswith('source_property_') and not k.endswith(('_opening_balance','_closing_balance'))
        and v is not None and pd.notna(v) and isinstance(v, (int,float)) and math.isfinite(v)}
    brand = str(row.get('selected_grade_brand') or '*')
    streams = {s:{brand:{a:row.get(f'source_grade_{s}_{a}', row.get(f'source_grade_{a}')) for a in ANALYTES}} for s in STREAMS}
    event = EventData(stockpile=row.get('source') if row.get('source_type')=='stockpile' else None,
        grade_block=row.get('source_id') if row.get('source_type')!='stockpile' else None,
        event_type='stockpile' if row.get('source_type')=='stockpile' else 'grade_block',
        equipment=row.get('equipment'), cost=0, cash=0, rate=0,
        grade_fe=row.get('source_grade_fe'), grade_si=row.get('source_grade_si'), grade_al=row.get('source_grade_al'),
        grade_p=row.get('source_grade_p'), grade_mn=row.get('source_grade_mn'), balance=quantity,
        max_quantity=quantity, reclaim_threshold=0, state=1, auto_turnover_datetime=None,
        source_name=row.get('source_id') or row.get('source'), grade_streams=streams, source_properties=properties,
        source_property_kinds=config.get('source_property_kinds'), source_property_weights=config.get('source_property_weights'))
    apply_selected_stream(event, row.get('selected_grade_stream') or 'adjusted_product', brand)
    return event


def arrival_row(item, state, config):
    mat, amount = item['material'], item['wmt']
    event = material_event(mat['event'], amount)
    original = mat.get('manual_row')
    result = {key:state.get(key) for key in ('steady_state_number','blend_ID','period','steady_state_duration')}
    result.update(start_datetime=item['start'], end_datetime=item['end'], source=mat['source'], source_id=mat['source'],
        source_type='transport', source_actual_tonnes=amount, source_arrival_wmt=amount,
        source_opening_balance=amount, source_closing_balance=0, source_blend_ratio=0,
        tipping_point=mat['tipping_point'], opf=mat['opf'], transport_provenance=mat['provenance'],
        transport_chunk_id=item['chunk_id'], selected_grade_stream=event.selected_grade_stream,
        selected_grade_brand=event.selected_grade_brand,
        **{f'source_grade_{a}':getattr(event, 'grade_'+a) for a in ANALYTES},
        **{'source_property_'+k:v for k,v in event.source_properties.items()})
    if original:
        fraction = amount/float(original['source_actual_tonnes'])
        for key, value in original.items():
            if (key.startswith(('selected_grade_weight_', 'product_build_')) and key.endswith(('_tonnes', '_weight'))
                    and key not in ProductBuildProgress.COLUMNS and pd.notna(value)):
                result[key] = float(value)*fraction
            elif key.startswith('product_build_') and '_grade_' in key and key not in ProductBuildProgress.COLUMNS:
                result[key] = value
    else:
        field = config.get('product_build_tonnes_stream', 'modelled_product_wmt')
        if field not in event.source_properties:
            raise ValueError(f'{mat["source"]}: opening transport lacks the selected product quantity ({field}).')
        result['product_build_source_tonnes'] = event.source_properties[field]
        for a in ANALYTES:
            weight = (config.get('source_property_weights') or {}).get(f'{event.selected_grade_stream}_{a}')
            if weight and weight not in event.source_properties:
                raise ValueError(f'{mat["source"]}: opening transport lacks the {a} grade weight ({weight}).')
            result[f'selected_grade_weight_{a}_tonnes'] = event.source_properties.get(weight, amount)
        for lane, field in (config.get('byproduct_quantity_fields') or {}).items():
            if config.get('byproducts_enabled'):
                result[f'product_build_{lane}_source_tonnes'] = event.source_properties.get(field)
                for a, field in ((config.get('byproduct_grade_fields') or {}).get(lane) or {}).items():
                    result[f'product_build_{lane}_source_grade_{a}'] = event.source_properties.get(field)
    return result


def replay(report, calendar, periods, targets, topology):
    """Return detached feed/product/transport snapshots; do not write on failure."""
    context = calendar.get('site_context') or {}
    config = {**(calendar.get('solver_config') or {})}
    settings = transport_settings(context.get('transport_settings') or config.get('transport_settings'))
    if report.empty or not transport_enabled(settings):
        return report
    feed = multi_feed_settings(context.get('multi_feed_settings') or config.get('multi_feed_settings'))
    point = context.get('crusher') or 'Crusher'
    opf = context.get('opf') or 'OPF'
    frame = report.copy()
    frame['tipping_point'] = frame.get('tipping_point', point)
    frame['opf'] = frame.get('opf', opf)
    frame['start_datetime'] = pd.to_datetime(frame.start_datetime)
    frame['end_datetime'] = pd.to_datetime(frame.end_datetime)
    scheduled = frame.copy()
    scheduled['_tip_start'] = scheduled.start_datetime
    if 'manual_feed_available_at' in scheduled:
        available = pd.to_datetime(scheduled.manual_feed_available_at, errors='coerce')
        mask = scheduled.source_type.eq('grade_block') & available.notna()
        scheduled.loc[mask,'_tip_start'] = pd.concat([scheduled.loc[mask,'start_datetime'],available[mask]],axis=1).max(axis=1)
    enabled_points = {name for name,cfg in settings['tipping_points'].items() if cfg['enabled']}
    for row in scheduled.loc[scheduled.source_type.eq('grade_block') & scheduled.tipping_point.isin(enabled_points)].to_dict('records'):
        if pd.isna(row.get('manual_feed_available_at')) or row['_tip_start'] >= row['end_datetime']:
            raise ValueError('Rebuild manual direct-tip candidates with current route arrival times before replaying transport.')
    start = min(moment(v) for k,v in periods.items() if k.endswith('_start')) if periods else moment(frame.start_datetime.min())
    horizon = max(moment(v) for k,v in periods.items() if k.endswith('_end')) if periods else moment(frame.end_datetime.max())
    periods_by_time = [(k[:-6], moment(v), moment(periods[k[:-6]+'_end'])) for k,v in periods.items() if k.endswith('_start')]
    def rates(at):
        period = next((k for k,s,e in periods_by_time if s <= at < e), periods_by_time[-1][0] if periods_by_time else 'period_1')
        return {name: float(period_limit(calendar, 'crusher_rate', period, name, feed['mode']) or 0) for name in settings['tipping_points']}
    references = {name:next((rates(s)[name] for _,s,_ in periods_by_time if rates(s)[name]>0), 0) for name in settings['tipping_points']}
    history = config.get('transport_history')
    if history is None:
        from setup.TransportOpeningHistory import opening_history_events
        bundle = context.get('transport_opening_history') or {}
        if not bundle.get('request'):
            raise ValueError('Refresh Conveyor/COS opening contents before generating this manual plan.')
        if moment(bundle['request']['end']) != start or bundle['request'].get('settings') != settings:
            raise ValueError('Conveyor/COS opening contents are stale for this manual start or transport setup.')
        history = opening_history_events(bundle, context, config)
    flow = ConveyorCOS(settings, start, references, history)
    arrivals, passthrough = [], frame.loc[~frame.tipping_point.isin(flow.points)].copy()
    boundaries = sorted({start, horizon, *scheduled._tip_start.map(moment), *frame.start_datetime.map(moment), *frame.end_datetime.map(moment),
        *(s for _,s,_ in periods_by_time), *(e for _,_,e in periods_by_time)})
    for begin, end in zip(boundaries, boundaries[1:]):
        if begin < start or end > horizon:
            continue
        current_rates = rates(begin)
        flow.prepare_rates(current_rates)
        active = scheduled.loc[(scheduled._tip_start < end) & (scheduled.end_datetime > begin)]
        for name, rows in active.loc[active.tipping_point.isin(flow.points)].groupby('tipping_point'):
            hours = (rows.end_datetime-rows._tip_start).dt.total_seconds()/3600
            physical_rate = (rows.source_actual_tonnes/hours).sum()
            if physical_rate > current_rates[name]+1e-5:
                raise ValueError(f'{name}: manual feed exceeds crusher capacity after actual direct-tip arrivals. Reduce direct-tip tonnes or extend the blend.')
            cfg = flow.points[name]['config']
            service = cfg['spot_seconds']+cfg['dump_seconds']
            stock = rows.source_type.eq('stockpile')
            if service and (rows.loc[stock,'source_actual_tonnes']/hours[stock]).sum() > cfg['rehandle_payload_wmt']*3600/service+1e-5:
                raise ValueError(f'{name}: manual rehandle demand exceeds the configured payload, spotting and dumping capacity.')
        state = active.iloc[0].to_dict() if not active.empty else dict(steady_state_number=len(arrivals)+1, blend_ID='Transport only', period=0)
        state['steady_state_duration'] = (end-begin).total_seconds()/3600
        for row in active.to_dict('records'):
            name = row['tipping_point']
            if name not in flow.points or float(row['source_actual_tonnes']) <= 1e-8:
                continue
            overlap_start, overlap_end = max(begin,moment(row['_tip_start'])), min(end,moment(row['end_datetime']))
            fraction = (overlap_end-overlap_start).total_seconds()/(row['end_datetime']-row['_tip_start']).total_seconds()
            mat = material(event_from_row(row, config), point=name, opf=row['opf'], provenance='manual', payload_id=row.get('source_id',''))
            mat['manual_row'] = row
            flow.add_feed(name, mat, float(row['source_actual_tonnes'])*fraction, overlap_start, overlap_end, current_rates[name])
        for item in flow.advance(end, current_rates):
            arrivals.append(arrival_row(item,state,config))
    arrival_frame = pd.DataFrame(arrivals)
    combined = pd.concat([passthrough, arrival_frame], ignore_index=True) if not passthrough.empty else arrival_frame
    combined = ProductBuildProgress.annotate(combined, targets, byproducts_enabled=config.get('byproducts_enabled'), solver_config=config)
    result = frame.copy()
    # Feed has no arrival-based cumulative product figures. They live in the
    # separate product report and are not falsely placed at crusher tip time.
    result = result.drop(columns=[c for c in ProductBuildProgress.COLUMNS if c in result])
    result.attrs = deepcopy(report.attrs)
    result.attrs.update(manual_product_report=product_from_feed(combined), material_flow_topology=topology,
        transport_frames=dict(transport_movements=pd.DataFrame(flow.movements), transport_contents=pd.DataFrame(flow.snapshots),
                              transport_product_arrivals=combined), transport_warnings=flow.warnings)
    return result
