"""Joint MILP for simultaneous physical tipping points and shared ROM balances."""

from collections import defaultdict
from copy import deepcopy

from pulp import LpBinary, LpMinimize, LpProblem, LpVariable, lpSum, value

from classes.Optimizer import Optimizer, RetryingCBCSolver
from classes.MultiFeedSettings import multi_feed_settings, period_lanes, route_allowed


class MultiLaneOptimizer(Optimizer):
    def __init__(self, settings):
        self.settings = multi_feed_settings(settings)

    @staticmethod
    def _resume(steps):
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value
        raise RuntimeError("Unexpected repeated optimisation boundary.")

    def run_blending_optimization(self, event_pool, period_crusher_target, steady_state_duration,
                                  steady_state_controller_source, steady_state_controller_tonnes,
                                  periods, period_tracker, min_stockpiles=None, max_stockpiles=None,
                                  min_stockpile_contribution_ratio=None, solver_config=None,
                                  excluded_source_sets=None, excluded_stockpile_sets=None):
        config = dict(solver_config or {})
        points = period_lanes(self.settings, period_tracker, period_crusher_target)
        target_builds = config.get("target_product_builds") or {}
        for point in points:
            builds = [b for b in target_builds.values() if not b.get("contributing_opfs") or point["opf"] in b["contributing_opfs"]]
            brands = {b.get("brand") for b in builds if b.get("brand")}
            if len(brands) > 1:
                raise ValueError(f"{point['opf']}: active product builds must use the same brand.")
            point["active_brand"] = next(iter(brands), point["target"].get("brand", ""))
            if config.get("product_builds_configured") and not builds:
                point["target"]["crusher_rate"] = 0.0
        flow = config.get('_transport_engine')
        start = config.get('current_steady_state_datetime')
        rates = {p['name']: p['target']['crusher_rate'] for p in points}
        models, all_events = [], []
        for point_index, point in enumerate(points):
            events = []
            for event_index, original in enumerate(event_pool):
                source = Optimizer.selection_source_name(original)
                if original.is_stockpile and not route_allowed(self.settings, source, point["name"]):
                    continue
                if original.is_grade_block:
                    allowed = (config.get("direct_tip_point_by_payload") or {}).get(str(original.grade_block), [])
                    if not point["direct_tip_enabled"] or point["name"] not in allowed:
                        continue
                event = deepcopy(original)
                event._multi_point = point["name"]
                event._multi_opf = point["opf"]
                event._multi_key = (point_index, event_index)
                if self.settings["mode"] == "combined_opf":
                    from classes.OPFSourceProfiles import apply_opf_profile
                    apply_opf_profile(event, point["opf"], config)
                if event.is_stockpile:
                    rate = point['target'].get('max_reclaim_rate', self.settings["route_reclaim_rates"].get(source, {}).get(point["name"]))
                    if rate is not None:
                        event._rate = rate
                events.append(event)
            lane_config = {**config, **point["solver_config"], "direct_tip_enabled": point["direct_tip_enabled"],
                           "target_product_brand": point["active_brand"],
                           "target_product_builds": {}, "target_product_build_states": {}}
            for key in ("target_product_build", "target_product_build_state", "product_build_completion_constraint"):
                lane_config.pop(key, None)
            steps = Optimizer.blending_problem_steps(events, point["target"], steady_state_duration,
                      None, None, periods, period_tracker,
                      (point.get("min_stockpiles") if point.get("min_stockpiles") is not None else min_stockpiles) if point["target"]["crusher_rate"] > 0 and events else None,
                      point.get("max_stockpiles") if point.get("max_stockpiles") is not None else max_stockpiles,
                      min_stockpile_contribution_ratio, lane_config)
            try:
                packet = next(steps)
            except StopIteration as finished:
                return finished.value
            packet["problem"].name = f"tip_{point_index}"
            models.append((point, steps, packet))
            for event in packet["events"]:
                aggregate_event = deepcopy(event)
                aggregate_event._multi_materialized = True
                if flow:
                    from datetime import timedelta
                    from classes.ConveyorCOS import moment
                    feed_start = max(start, moment(event.delivered_datetime)) if event.is_grade_block and event.delivered_datetime else start
                    aggregate_event._transport_start = min(feed_start, start+timedelta(hours=steady_state_duration))
                    available = steady_state_duration - (aggregate_event._transport_start-start).total_seconds()/3600
                    aggregate_event._transport_fraction = flow.feed_fraction(point['name'], available, rates[point['name']])
                all_events.append(aggregate_event)

        if flow:
            from datetime import timedelta
            from classes.ConveyorCOS import material_event
            known_arrivals = flow.preview(start + timedelta(hours=steady_state_duration), rates)
            for index, incoming in enumerate(known_arrivals):
                mat = incoming['material']
                event = material_event(mat['event'], incoming['wmt'])
                from classes.GradeStreams import apply_selected_stream, DEFAULT_STREAM
                point = next(p for p in points if p['name'] == mat['tipping_point'])
                warnings = apply_selected_stream(event, config.get('selected_data_stream') or DEFAULT_STREAM, point['active_brand'])
                if config.get('strict_mapped_fields') and any(w.get('used_stream') != (config.get('selected_data_stream') or DEFAULT_STREAM) for w in warnings):
                    raise ValueError(f'{mat["source"]}: arrival chemistry is unavailable for the receiving build brand.')
                event._transport_arrival = True
                event._transport_fraction = 1.0
                event._transport_material = mat
                event._transport_chunk = incoming['chunk_id']
                event._multi_materialized = True
                event._multi_key = ('arrival', index)
                event._multi_point, event._multi_opf = mat['tipping_point'], mat['opf']
                event._type = 'transport'
                event._stockpile = None
                event._grade_block = f'flow:{index}'
                event._rate = event.balance / steady_state_duration
                event._equipment = 'Conveyor / COS'
                event._cost = event._cash = 0
                all_events.append(event)

        # The aggregate model owns physical balances, the common equipment
        # boundary and product-build constraints. Crusher constraints stay local.
        aggregate_target = deepcopy(period_crusher_target)
        aggregate_target.update(crusher_rate=sum(p["target"]["crusher_rate"] for p in points),
                                direct_feed_ratio_min=0.0, direct_feed_ratio_max=1.0, custom_constraints=[])
        aggregate_config = {**config, "enforce_calendar_crusher_grade_targets": False,
                            "direct_tip_enabled": True, "prefer_fewer_stockpiles": False}
        aggregate_steps = Optimizer.blending_problem_steps(all_events, aggregate_target, steady_state_duration,
                           steady_state_controller_source, steady_state_controller_tonnes, periods, period_tracker,
                           None, None, min_stockpile_contribution_ratio, aggregate_config,
                           excluded_source_sets, excluded_stockpile_sets)
        try:
            aggregate = next(aggregate_steps)
        except StopIteration as finished:
            return finished.value
        aggregate["problem"].name = "shared"
        joint = LpProblem("simultaneous_tipping_points", LpMinimize)
        joint.extend(aggregate["problem"], use_objective=False)
        objective = aggregate["product_objective"]
        aggregate_vars = {event._multi_key: variable for event, variable in zip(aggregate["events"], aggregate["variables"])}
        for point, steps, packet in models:
            joint.extend(packet["problem"], use_objective=False)
            objective += packet["problem"].objective
            if point['target'].get('max_reclaim_rate') is not None:
                joint += lpSum(v * c for e, v, c in zip(packet['events'], packet['variables'], packet['reclaimer_coefficients'])
                               if e.is_stockpile) <= point['target']['max_reclaim_rate'] * steady_state_duration
            for event, variable in zip(packet["events"], packet["variables"]):
                joint += variable == aggregate_vars[event._multi_key]

        physical = defaultdict(list)
        point_usage = defaultdict(list)
        for event, variable in zip(aggregate["events"], aggregate["variables"]):
            if getattr(event, '_transport_arrival', False):
                continue
            identity = ("stockpile", event.stockpile) if event.is_stockpile else ("payload", event.grade_block)
            physical[identity].append((event, variable))
            if event.is_stockpile:
                point_usage[(event.stockpile, event._multi_point)].append((event, variable))
        for identity, entries in physical.items():
            joint += lpSum(v for _, v in entries) <= max(float(e.balance) for e, _ in entries)
            if identity[0] == "stockpile":
                quantity = max(float(e.max_quantity) for e, _ in entries)
                duration = periods.get_periods()[f"{period_tracker}_duration"]
                joint += lpSum(v for _, v in entries) <= quantity * steady_state_duration / duration
        stockpile_choices = defaultdict(list)
        reclaim_coefficients = {e._multi_key: c for e, c in zip(aggregate['events'], aggregate['reclaimer_coefficients'])}
        for index, ((source, point), entries) in enumerate(point_usage.items()):
            chosen = LpVariable(f"route_{index}", cat=LpBinary)
            capacity = max(float(e.balance) for e, _ in entries)
            joint += lpSum(v for _, v in entries) <= capacity * chosen
            route_rate = self.settings['route_reclaim_rates'].get(source, {}).get(point)
            if route_rate is not None:
                joint += lpSum(v * reclaim_coefficients[e._multi_key] for e, v in entries) <= route_rate * steady_state_duration
            stockpile_choices[source].append(chosen)
        for choices in stockpile_choices.values():
            joint += lpSum(choices) <= 1
        if flow:
            for point, _, packet in models:
                if point['name'] in flow.points:
                    cfg = flow.points[point['name']]['config']
                    from datetime import timedelta
                    from classes.ConveyorCOS import moment
                    end = start + timedelta(hours=steady_state_duration)
                    available = [(e, v, max(start, moment(e.delivered_datetime)) if e.is_grade_block and e.delivered_datetime else start)
                                 for e, v in zip(packet['events'], packet['variables'])]
                    for _, variable, begin in available:
                        if begin >= end:
                            joint += variable == 0
                    for boundary in sorted({begin for _, _, begin in available if begin < end}):
                        joint += lpSum(v/((end-begin).total_seconds()/3600) for _, v, begin in available if begin <= boundary < end) <= rates[point['name']]
                    # Opening payload service can extend beyond the nominal lag.
                    # Reserve that outlet rate before admitting new uniform feed,
                    # preserving FIFO and the conveyor's physical throughput.
                    if cfg['conveyor_capacity_wmt'] > 0 and rates[point['name']] > 0:
                        aligned = flow.fork(audit=False)
                        aligned.prepare_rates(rates)
                        queued = aligned.points[point['name']]['conveyor']
                        lag = timedelta(hours=cfg['conveyor_capacity_wmt']/rates[point['name']])
                        outlet_end = end+lag
                        boundaries = {begin+lag for _,_,begin in available if begin < end}
                        boundaries.update(t for row in queued for t in (row['start'],row['end'])
                                          if start+lag <= t < outlet_end)
                        for boundary in sorted(boundaries):
                            occupied = sum(row['rate'] for row in queued if row['start'] <= boundary < row['end'])
                            joint += lpSum(v/((end-begin).total_seconds()/3600) for _,v,begin in available
                                           if begin+lag <= boundary < outlet_end) <= max(0,rates[point['name']]-occupied)
                    # Total storage cannot exceed measured capacity, even at a rate transition.
                    cfg = flow.points[point['name']]['config']
                    departing = sum(r['wmt'] for r in known_arrivals if r['material']['tipping_point'] == point['name'])
                    free = cfg['conveyor_capacity_wmt'] + cfg['cos_capacity_wmt'] - flow.balance(point['name'])
                    retained = [v*(1-getattr(e, '_transport_fraction', 0)) for e, v in zip(aggregate['events'], aggregate['variables'])
                                if e._multi_point == point['name'] and not getattr(e, '_transport_arrival', False)]
                    joint += lpSum(retained) <= max(0, free+departing)
                    service = cfg['spot_seconds'] + cfg['dump_seconds']
                    if cfg['conveyor_capacity_wmt'] > 0 and service > 0:
                        joint += lpSum(v for e, v in zip(packet['events'], packet['variables']) if e.is_stockpile) <= (
                            cfg['rehandle_payload_wmt'] * 3600 / service * steady_state_duration)
        joint += objective
        limits = [p["time_limit"] for _, _, p in models if p["time_limit"] is not None]
        joint.solve(RetryingCBCSolver(msg=False, timeLimit=min(limits) if limits else None))
        aggregate["problem"].status = joint.status
        result = self._resume(aggregate_steps)
        lane_results = {}
        for point, steps, packet in models:
            packet["problem"].status = joint.status
            lane_results[point["name"]] = self._resume(steps)
        result["tipping_point_results"] = lane_results
        result["solver_objective_value"] = value(joint.objective)
        quantity = sum(float(r.get("actual_tonnes") or 0) for r in result.get("transactions", []))
        result["solver_score"] = -float(value(joint.objective) or 0) / quantity if quantity else 0.0
        if result["Linprog_result_object"].success:
            # Aggregate reporting keeps shared product-build contributions;
            # each transaction also carries its physical tipping-point identity.
            reported = [(e, v) for e, v in zip(aggregate["events"], aggregate["variables"])
                        if float(v.value() or 0) >= 0]
            if len(reported) != len(result["transactions"]):
                raise RuntimeError("Joint solve transaction identity mismatch.")
            for transaction, (event, _) in zip(result["transactions"], reported):
                transaction.update(tipping_point=event._multi_point, opf=event._multi_opf,
                                   reconciliation_opf=event._multi_opf,
                                   reconciliation_scenario=getattr(event, "_reconciliation_scenario", ""))
            if flow:
                from classes.ConveyorCOS import material
                tips, arrivals, physical_transactions = [], [], []
                for transaction, (event, _) in zip(result['transactions'], reported):
                    if getattr(event, '_transport_arrival', False):
                        transaction.update(source_type='transport', transport_provenance=event._transport_material['provenance'],
                                           transport_chunk_id=event._transport_chunk)
                        arrivals.append(transaction)
                    else:
                        physical_transactions.append(transaction)
                        quantity = float(transaction['actual_tonnes'])
                        if quantity > 1e-8:
                            fraction = getattr(event, '_transport_fraction', 1.0)
                            if fraction > 0:
                                arrival = deepcopy(transaction)
                                arrival['actual_tonnes'] = quantity * fraction
                                arrival['transport_provenance'] = 'modelled'
                                arrivals.append(arrival)
                            if event._multi_point in flow.points:
                                event._source_property_weights = {**event.source_property_weights, **(config.get('source_property_weights') or {})}
                                tips.append(dict(point=event._multi_point, material=material(event, point=event._multi_point,
                                    opf=event._multi_opf, payload_id=transaction.get('source_id', '')), wmt=quantity,
                                    start=getattr(event, '_transport_start', start)))
                for arrival in arrivals:
                    for analyte, weight in arrival.get('transport_product_weights', {}).items():
                        arrival[f'selected_grade_weight_{analyte}_tonnes'] = weight
                result['transactions'] = physical_transactions
                result['transport_arrivals'] = arrivals
                result['transport_tips'] = tips
                result['transport_rates'] = rates
        return result
