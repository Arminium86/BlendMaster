"""FIFO arrival accounting for the existing joint tipping-point solve."""
from copy import deepcopy, copy
from datetime import timedelta
import pandas as pd
from classes.ConveyorCOS import ConveyorCOS
from classes.MultiFeedSettings import multi_feed_settings, period_lanes
from classes.MultiLaneOptimizer import MultiLaneOptimizer
from classes.TransportSettings import transport_enabled, reference_rate


def initialise_transport(case):
    case.transport = None
    case.transport_candidates = {}
    case.product_arrival_results = pd.DataFrame()
    case.transport_selected_arrivals = pd.DataFrame()
    settings = case.solver_config.get('transport_settings')
    if not transport_enabled(settings):
        return
    feed = deepcopy(case.multi_feed_configuration)
    if feed['mode'] == 'single':
        point = case.site_context.get('crusher') or 'Crusher'
        opf = case.site_context.get('opf') or 'OPF'
        feed['tipping_points'] = [dict(name=point, opf=opf, rom_area=point,
            targets_by_period=deepcopy(case.crusher_targets), direct_tip_enabled=case.solver_config.get('direct_tip_enabled', True))]
        feed['source_subsets'] = {source.name: point for source in case.stockpiles}
        case.solver_config['direct_tip_point_by_payload'] = {str(block.name): [point] for block in case.grade_blocks}
    feed = multi_feed_settings(feed)
    points = period_lanes(feed, case.period_tracker, case.crusher_targets[case.period_tracker])
    rates = {p['name']: reference_rate(next(q['targets_by_period'] for q in feed['tipping_points'] if q['name'] == p['name'])) for p in points}
    unknown = set(settings.get('tipping_points', {})) - set(rates)
    if unknown:
        raise ValueError('Transport settings refer to unselected crushers: ' + ', '.join(sorted(unknown)))
    case.transport = ConveyorCOS(settings, case.start_time, rates, case.solver_config.get('transport_history', []))
    case.transport_feed_settings = feed
    case.optimizer = MultiLaneOptimizer(feed)


def transport_rates(case):
    period = case.period_for_time(case.current_time)
    return {p['name']: p['target']['crusher_rate'] for p in period_lanes(
        case.transport_feed_settings, period, case.crusher_targets[period])}


def arrival_frame(case, result, blend_id):
    rows = []
    duration = float(result['steady_state_duration'])
    for transaction in result.get('transport_arrivals', []):
        quantity = float(transaction['actual_tonnes'])
        if quantity <= 1e-8:
            continue
        rows.append(dict(start_datetime=case.current_time, end_datetime=case.current_time+timedelta(hours=duration),
            steady_state_number=case.steady_state_tracker, steady_state_duration=duration,
            blend_option=case.user_blend_choice or case.blend_option, blend_ID=blend_id, period=case.period_tracker,
            source=transaction['source'], source_id=transaction.get('source_id', transaction['source']),
            source_type='transport', source_actual_tonnes=quantity, source_arrival_wmt=quantity,
            source_blend_ratio=0.0, source_opening_balance=quantity, source_closing_balance=0.0,
            crusher_actual_tonnes=sum(float(t['actual_tonnes']) for t in result.get('transport_arrivals', [])),
            tipping_point=transaction['tipping_point'], opf=transaction['opf'],
            transport_provenance=transaction.get('transport_provenance', 'modelled'),
            transport_chunk_id=transaction.get('transport_chunk_id'),
            **{f'source_grade_{a}': transaction.get(f'grade_{a}') for a in ('fe','si','al','p','mn')},
            **{key: value for key, value in transaction.items() if key.startswith(('product_build_', 'source_property_', 'selected_grade_'))}))
    return pd.DataFrame(rows)


def commit_transport(case, selected):
    if not getattr(case, 'transport', None):
        return
    choice = case.user_blend_choice
    if choice not in case.transport_candidates and len(case.transport_candidates) == 1:
        choice = next(iter(case.transport_candidates))
    result = case.transport_candidates.get(choice)
    if result is None:
        case.transport_selected_arrivals = pd.DataFrame()
        return
    end = case.current_time + timedelta(hours=float(result['steady_state_duration']))
    trial = case.transport.fork()
    trial.prepare_rates(result['transport_rates'])
    for tip in result['transport_tips']:
        trial.add_feed(tip['point'], tip['material'], tip['wmt'], tip.get('start', case.current_time), end,
                       result['transport_rates'][tip['point']])
    actual = trial.advance(end, result['transport_rates'])
    expected = sum(float(t['actual_tonnes']) for t in result['transport_arrivals'] if t['tipping_point'] in trial.points)
    if abs(sum(r['wmt'] for r in actual)-expected) > 1e-4:
        raise ValueError(f'FIFO arrivals ({sum(r["wmt"] for r in actual):.6f} WMT) differ from the '
                         f'product-target solve ({expected:.6f} WMT) at {case.current_time}; the candidate was not committed.')
    from collections import defaultdict
    observed,planned = defaultdict(float),defaultdict(float)
    for row in actual:
        observed[(row['material']['tipping_point'],str(row['material']['source']).casefold())] += row['wmt']
    for row in result['transport_arrivals']:
        if row['tipping_point'] in trial.points:
            planned[(row['tipping_point'],str(row['source']).casefold())] += float(row['actual_tonnes'])
    if any(abs(observed[key]-planned[key])>1e-4 for key in observed.keys()|planned.keys()):
        raise ValueError('FIFO source composition differs from the product-target solve; candidate was not committed.')
    blend = selected['blend_ID'].iloc[0] if not selected.empty else case.blend_ID
    frame = arrival_frame(case, result, blend)
    case.transport = trial
    case.transport_selected_arrivals = frame
    if not frame.empty:
        case.product_arrival_results = (frame.copy() if case.product_arrival_results.empty else
            pd.concat([case.product_arrival_results, frame], ignore_index=True))
    case.transport_candidates = {}


def accept_transport_idle(case, result):
    """Advance a validated arrival-only/off state without depleting a source."""
    case.user_blend_choice = case.blend_option
    case.transport_candidates[case.blend_option] = result
    end = case.current_time + timedelta(hours=float(result['steady_state_duration']))
    marker = pd.DataFrame([dict(start_datetime=case.current_time, end_datetime=end,
        steady_state_number=case.steady_state_tracker, steady_state_duration=result['steady_state_duration'],
        period=case.period_tracker, blend_option=case.blend_option, blend_ID='Transport only',
        source='', source_id='', source_type='transport_idle', source_actual_tonnes=0.0,
        source_blend_ratio=0.0, source_opening_balance=0.0, source_closing_balance=0.0, crusher_actual_tonnes=0.0)])
    case.append_results(marker)
    completed = case.update_product_build_runtime_state(marker)
    case.validate_completed_product_build(completed)
    case.balance_tracker.update_balances(marker, case.expit_payload_transactions, case.current_time, end, case.steady_state_tracker)
    case.total_AMT_stockpile_balances = case.balance_tracker.return_total_AMT_stockpile_balances()
    case.current_time = end
    case.period_tracker = case.period_for_time(end) or case.period_tracker
    case.decision_point_results = pd.DataFrame()
    case.blend_option = 1


def product_report_case(case):
    if not getattr(case, 'transport', None):
        return case
    view = copy(case)
    view.results = case.product_arrival_results
    view.transport = None
    return view
