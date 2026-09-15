"""Editable per-point recipes using the established manual material equations.

Every point advances on one chronological stock ledger. OPF chemistry remains
independent; consuming a footprint advances its inventory in every profile.
The combined report then passes the existing shared Calendar and FIFO replay.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import math
import pandas as pd
from classes.ManualBlendPlanner import ManualBlendPlanner, ManualBlendPlanningError
from classes.MultiFeedSettings import multi_feed_settings, route_allowed, build_opfs
from classes.MultiFeedCalendar import apply_calendar
from classes.OptimisedToManualPlan import OptimisedToManualPlan


def payload_at_point(row, point, context):
    from classes.ExpitDataHandler import ExpitDataHandler
    destination = row.get('tipping_point') or row.get('direct_tip_crusher') or row.get('crusher') or row.get('destination', '')
    return (ExpitDataHandler.crusher_destination_names_match(destination, point['name'])
        or ExpitDataHandler.crusher_destination_matches(destination, context.get('mine'), point['name'], point['opf'])
        or any(str(rule.get('grade_block_source') or '').strip().upper() in str(row.get('source') or '').upper()
            and str(rule.get('grade_block_source') or '').strip()
            and ExpitDataHandler.crusher_destination_names_match(rule.get('crusher_destination'), point['name'])
            for rule in context.get('direct_tip_movement_rules', [])))


def number(value, label, *, positive=False):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ManualBlendPlanningError(f'{label} must be numeric.') from None
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ManualBlendPlanningError(f'{label} must be finite and {"positive" if positive else "non-negative"}.')
    return value


def from_report(report, stockpiles=None):
    """Import recipes and editable sequence, preserving exact transfer evidence."""
    drafts = {}
    for point, rows in report.groupby('tipping_point', sort=False):
        transfer = OptimisedToManualPlan(rows, stockpiles, preserve_blend_ids=True).build()
        recipes = []
        for definition in transfer['blend_definitions']:
            sources = ManualBlendPlanner._split_values(definition.get('Sources'))
            ratios = ManualBlendPlanner._split_values(definition.get('Source Ratios'))
            recipes.append(dict(id=str(definition['Blend ID']), sources=[
                dict(source=s, weight=float(r), reclaim_rate=None, projected=False) for s, r in zip(sources, ratios)]))
        drafts[str(point)] = dict(authoring_initialized=True, recipes=recipes, sequence=deepcopy(transfer['sequence_rows']),
            rates=transfer['period_crusher_rates'], direct_tip_rows=transfer['direct_tip_rows'], allocations={})
    return drafts


def has_drafts(drafts):
    return any(d.get('authoring_initialized') or d.get('recipes') or d.get('sequence') or d.get('rates')
               for d in (drafts or {}).values())


def reset_imported_timing(draft):
    """Release the copied solution when its point's authored schedule changes."""
    had_selections = bool(draft.get('direct_tip_rows') or draft.get('allocations'))
    draft['direct_tip_rows'] = []
    draft['allocations'] = {}
    for row in draft.get('sequence', []):
        if row.get('_exact_start') and row.get('_exact_end'):
            start, end = pd.Timestamp(row['_exact_start']), pd.Timestamp(row['_exact_end'])
            row.update({'Start Datetime': start.to_pydatetime(), 'End Datetime': end.to_pydatetime(),
                        'Duration (hrs)': (end-start).total_seconds()/3600})
        for key in list(row):
            if key.startswith('_'):
                row.pop(key)
        row['Origin'] = 'Manual'
    return had_selections


def definitions(draft):
    result = []
    seen = set()
    for recipe in draft.get('recipes', []):
        name = str(recipe.get('id', '')).strip()
        if not name or name in seen:
            raise ManualBlendPlanningError('Each recipe needs a unique Blend ID within its tipping point.')
        seen.add(name)
        sources = [s for s in recipe.get('sources', []) if number(s.get('weight'), f'{name} source weight') > 0]
        if len({s['source'] for s in sources}) != len(sources):
            raise ManualBlendPlanningError(f'Blend {name} lists a source more than once.')
        weights = [number(s['weight'], 'Source weight') for s in sources]
        total = sum(weights)
        result.append({'Blend ID': name, 'Sources': [s['source'] for s in sources],
                       'Source Ratios': [w / total for w in weights] if total else []})
    return result


class MultiManualPlanner:
    GRADES = ManualBlendPlanner.GRADES

    def __init__(self, drafts, stockpiles, chunks, payloads, periods, targets, calendar, topology):
        self.drafts = deepcopy(drafts)
        self.reset_direct_tip_points = []
        # Older saved drafts retained copied direct-tip tonnes after timing or
        # recipe edits. They must be reviewed against the new state windows.
        for point, draft in self.drafts.items():
            if draft.get('direct_tip_rows') and any(
                    not row.get('_fixed_steady_state') for row in draft.get('sequence', [])):
                reset_imported_timing(draft)
                self.reset_direct_tip_points.append(point)
        self.periods = {k: v for k, v in periods.items() if k.endswith(('_start', '_end'))}
        self.targets, self.calendar, self.topology = targets, deepcopy(calendar), topology
        context = self.calendar.get('site_context') or {}
        labels = ['Preplan' if k == 'preplan_start' else k[:-6].capitalize() for k in periods if k.endswith('_start')]
        self.settings = apply_calendar(multi_feed_settings(context.get('multi_feed_settings')), self.calendar, labels)
        self.calendar.setdefault('site_context', {})['multi_feed_settings'] = self.settings
        self.calendar.setdefault('solver_config', {})['multi_feed_settings'] = self.settings
        self.planners, self.source_options = {}, {}
        profiles = context.get('opf_profiles') or {}
        for point in self.settings['tipping_points']:
            name, opf = point['name'], point['opf']
            draft = self.drafts.get(name) or {}
            if not draft.get('sequence'):
                continue
            if self.settings['mode'] == 'combined_opf' and opf not in profiles:
                raise ManualBlendPlanningError(f'{opf}: prepare Grade Reconciliation before calculating the manual plan.')
            profile = profiles.get(opf) or {}
            inventory = deepcopy(profile.get('inventory') or stockpiles)
            inventory = {s: v for s, v in inventory.items() if s in stockpiles}
            point_chunks = deepcopy(list(profile['chunks'].values()) if profile.get('chunks') is not None else chunks)
            options = {}
            for recipe in draft.get('recipes', []):
                for source in recipe.get('sources', []):
                    if number(source.get('weight'), 'Source weight') == 0:
                        continue
                    if not route_allowed(self.settings, source['source'], name):
                        raise ManualBlendPlanningError(f"{source['source']} is not permitted to feed {name}. Review its Subset / movement rules.")
                    key = source['source'].upper()
                    if key in options and bool(options[key].get('projected')) != bool(source.get('projected')):
                        raise ManualBlendPlanningError(f'{key}: use the same opening/projected balance in every recipe at {name}.')
                    options[key] = source
                    if source.get('projected'):
                        projection = source.get('projection') or {}
                        if not projection or pd.isna(pd.to_datetime(projection.get('available'), errors='coerce')):
                            raise ManualBlendPlanningError(f'{key}: refresh the projected stockpile evidence first.')
                        if any(str(c.get('footprint', '')).upper() == key for c in point_chunks):
                            raise ManualBlendPlanningError(f'{key}: use prepared AMT chunk balances; projected receipts require AMT resubmission.')
                        inventory[key] = {**inventory.get(key, {}), **deepcopy(projection.get('material') or {}),
                                          'balance': number(projection['balance'], f'{key} projected balance')}
                        if self.settings['mode'] == 'combined_opf':
                            from classes.OPFSourceProfiles import register_profiles, apply_opf_profile
                            from classes.CustomConstraints import source_properties_from_mapping
                            from types import SimpleNamespace
                            mapped = deepcopy(self.calendar.get('solver_config') or {})
                            register_profiles(mapped, profiles)
                            event = SimpleNamespace(balance=inventory[key]['balance'], source_name=key,
                                                    source_properties=source_properties_from_mapping(inventory[key]))
                            apply_opf_profile(event, opf, mapped)
                            inventory[key].update(grade_streams=event.grade_streams, source_properties=event.source_properties)
            self.source_options[name] = options
            lane_calendar = deepcopy(self.calendar)
            lane_calendar['site_context'] = {**context, **point.get('solver_config', {}), 'opf': opf, 'crusher': name}
            lane_calendar['solver_config'] = {**self.calendar.get('solver_config', {}), **point.get('solver_config', {})}
            rates = {}
            for i, label in enumerate(labels):
                period = 'preplan' if i == 0 else f'period_{i}'
                limit = point['targets_by_period'][period]['crusher_rate']
                rate = number((draft.get('rates') or {}).get(label, (draft.get('rates') or {}).get(period, limit)), f'{name} / {label} rate')
                if rate > limit + 1e-7:
                    raise ManualBlendPlanningError(f'{name} / {label}: manual rate {rate:g} exceeds Calendar limit {limit:g}.')
                rates[label] = rate
                for grade in self.GRADES:
                    for bound in ('min', 'max'):
                        lane_calendar.setdefault(f'crusher_target_{grade}_{bound}', {})[label] = point['targets_by_period'][period].get(f'target_{grade}_{bound}', 0 if bound == 'min' else 100)
            lane_calendar['crusher_rate'] = rates
            lane_payloads = payloads.copy() if isinstance(payloads, pd.DataFrame) else pd.DataFrame(payloads or [])
            if not point.get('direct_tip_enabled', True):
                lane_payloads = lane_payloads.iloc[0:0]
            # Prepared payload routing is authoritative. Never offer a payload
            # at an unrelated crusher simply because its delivery overlaps.
            if not lane_payloads.empty:
                lane_payloads = lane_payloads.loc[[payload_at_point(row, point, context) for row in lane_payloads.to_dict('records')]].copy()
            from classes.ExpitDataHandler import ExpitDataHandler
            lane_calendar['site_context']['direct_tip_movement_rules'] = [r for r in context.get('direct_tip_movement_rules', [])
                if ExpitDataHandler.crusher_destination_names_match(r.get('crusher_destination'), name)]
            if self.settings['mode'] == 'combined_opf' and not lane_payloads.empty:
                from classes.OPFSourceProfiles import register_profiles, apply_opf_profile
                from classes.CustomConstraints import source_properties_from_mapping
                from types import SimpleNamespace
                config = deepcopy(lane_calendar['solver_config']); register_profiles(config, profiles)
                records = []
                for row in lane_payloads.to_dict('records'):
                    event = SimpleNamespace(balance=row['payload'], source_name=row.get('source'),
                                            source_properties=source_properties_from_mapping(row))
                    apply_opf_profile(event, opf, config)
                    records.append({**row, 'source_properties': event.source_properties, 'grade_streams': event.grade_streams})
                lane_payloads = pd.DataFrame(records)
            lane_targets = [t for t in targets if not build_opfs(t) or opf in build_opfs(t)]
            # This helper only selects the active brand; the combined report
            # applies original target/opening quantities in ProductBuildProgress.
            lane_targets = [{**t, 'target_tonnes': max(0, float(t.get('target_tonnes') or 0)-float(t.get('opening_tonnes') or 0)),
                             'opening_tonnes': 0} for t in lane_targets]
            self.planners[name] = ManualBlendPlanner(draft['sequence'], definitions(draft), inventory, point_chunks,
                lane_payloads, periods, lane_targets, max([*rates.values(), 1e-9]), lane_calendar)
        if not self.planners:
            raise ManualBlendPlanningError('Add a recipe and at least one sequence row at a tipping point.')
        self._check_schedule()

    def build_group(self, opf):
        return next((build_opfs(t) for t in self.targets if opf in build_opfs(t)), (opf,))

    def _check_schedule(self):
        for point, planner in self.planners.items():
            previous = None
            for row in planner.sequence_rows:
                if previous is not None and row['_start'] < previous:
                    raise ManualBlendPlanningError(f'{point}: sequence intervals overlap.')
                if row['Blend ID'] not in planner.blends:
                    raise ManualBlendPlanningError(f"{point}: Blend {row['Blend ID']} has no recipe.")
                if row['_start'] < min(self.periods.values()) or row['_end'] > max(self.periods.values()):
                    raise ManualBlendPlanningError(f'{point}: sequence must stay inside the planning periods.')
                previous = row['_end']

    def _inventories(self):
        inventories = {p: deepcopy(planner._inventory_template) for p, planner in self.planners.items()}
        # Profiles must describe the same physical stock, even when grades differ.
        balances = {}
        for point, inventory in inventories.items():
            for source, rows in inventory.items():
                values = [(r['source_id'], r['balance']) for r in rows]
                previous = balances.get(source)
                if previous is not None and (len(values) != len(previous) or any(
                        name != old_name or abs(value-old_value) > max(1e-5, abs(old_value)*1e-7)
                        for (name, value), (old_name, old_value) in zip(values, previous))):
                    raise ManualBlendPlanningError(f'{source}: opening/projected balances or AMT chunks differ between tipping points.')
                balances[source] = values
        return inventories

    def _consume(self, inventories, source, amount, except_point=None):
        for point, inventory in inventories.items():
            if point != except_point and source in inventory:
                self.planners[point]._consume_inventory(inventory, source, amount)

    def build_steady_states(self):
        inventories = self._inventories()
        from classes.ContinuousAssays import boundaries
        scheduled = [(p, row) for p, planner in self.planners.items() for row in planner.sequence_rows]
        edges = sorted({t for _, row in scheduled for t in (row['_start'], row['_end'])} |
                       {pd.Timestamp(t).to_pydatetime() for t in self.periods.values()} |
                       {t for planner in self.planners.values() for t in boundaries(planner.solver_config)})
        states = []
        produced = defaultdict(float)
        for begin, end in zip(edges, edges[1:]):
            active = [(p, r) for p, r in scheduled if r['_start'] <= begin < r['_end']]
            current = begin
            while active and current < end - timedelta(microseconds=1):
                stop, plans, used_sources = end, [], set()
                for point, row in active:
                    planner = self.planners[point]
                    blend = planner.blends[row['Blend ID']]
                    fixed = row.get('_fixed_steady_state') and isinstance(row.get('_stockpile_source_tonnes'), dict)
                    row_hours = (row['_end']-row['_start']).total_seconds()/3600
                    rate = number(row['_crusher_rate'], 'Imported crusher rate') if fixed and row.get('_crusher_rate') is not None else planner._crusher_rate_for(current)
                    if rate <= 0:
                        raise ManualBlendPlanningError(f'{point}: a scheduled blend has zero crusher rate.')
                    physical_rate = (number(row['_physical_feed_tonnes'], 'Imported physical feed')/row_hours if fixed
                        else planner._physical_feed_rate(inventories[point], blend, rate) if blend['_sources'] else rate)
                    stock_rates = ({s: number(v, 'Imported stockpile tonnes')/row_hours for s, v in row['_stockpile_source_tonnes'].items()}
                                   if fixed else {s: physical_rate*r for s, r in zip(blend['_sources'], blend['_ratios'])})
                    for source, ratio in zip(blend['_sources'], blend['_ratios']):
                        if stock_rates.get(source, 0) <= 0: continue
                        if source in used_sources:
                            raise ManualBlendPlanningError(f'{source}: overlapping reclaim to different tipping points is not allowed.')
                        used_sources.add(source)
                        option = self.source_options[point].get(source, {})
                        if option.get('projected') and current < pd.Timestamp(option['projection']['available']):
                            raise ManualBlendPlanningError(f'{source}: projected balance is only available after {option["projection"]["available"]}.')
                        chunk = planner._current_chunk(inventories[point].get(source))
                        if not chunk:
                            raise ManualBlendPlanningError(f'{source}: insufficient shared inventory for Blend {row["Blend ID"]}.')
                        stop = min(stop, current + timedelta(hours=chunk['balance'] / stock_rates[source]))
                    plans.append((point, row, blend, rate, physical_rate, stock_rates, fixed))
                product_rates = defaultdict(float)
                trigger = 'AMT chunk / stockpile depletion' if stop < end else 'Sequence / period boundary'
                for point, row, blend, rate, physical_rate, stock_rates, fixed in plans:
                    planner = self.planners[point]
                    group = self.build_group(planner.opf)
                    if fixed and row.get('_product_build_actual_tonnes') is not None:
                        product_rate = number(row['_product_build_actual_tonnes'], 'Imported product tonnes') / ((row['_end']-row['_start']).total_seconds()/3600)
                    elif blend['_sources']:
                        product_rate = physical_rate*planner._blend_quantity_coefficient(inventories[point], blend, planner.product_build_tonnes_stream)
                    else:
                        product_rate = physical_rate
                    product_rates[group] += product_rate
                for point, *_ in plans:
                    planner = self.planners[point]; group = self.build_group(planner.opf)
                    remaining = planner._build_completion_distance(produced[group])
                    if remaining is not None and product_rates[group] > 1e-12:
                        completion = current+timedelta(hours=remaining/product_rates[group])
                        if completion <= stop:
                            stop, trigger = completion, 'Product build completion'
                if stop <= current + timedelta(microseconds=1):
                    raise ManualBlendPlanningError('A source is depleted. Shorten the blend or choose another source.')
                for point, row, blend, rate, physical_rate, stock_rates, fixed in plans:
                    hours = (stop-current).total_seconds()/3600
                    state = dict(steady_state_number=len(states)+1, tipping_point=point,
                        opf=self.planners[point].opf, blend_ID=row['Blend ID'], start_datetime=current, end_datetime=stop,
                        steady_state_duration=hours, period=self.planners[point]._period_number(current),
                        feed_capacity_tonnes=physical_rate*hours, crusher_rate=rate,
                        trigger=trigger,
                        state_key=f"{point}|{current.isoformat(timespec='microseconds')}|{stop.isoformat(timespec='microseconds')}|{row['Blend ID']}")
                    if fixed:
                        state['stockpile_source_tonnes'] = {s: value*hours for s, value in stock_rates.items()}
                    states.append(state)
                    for source, value in stock_rates.items():
                        self._consume(inventories, source, value*hours)
                for group, rate in product_rates.items():
                    produced[group] += rate*(stop-current).total_seconds()/3600
                current = stop
        for point, planner in self.planners.items():
            planner.attach_direct_tip_candidates([s for s in states if s['tipping_point'] == point])
        return states

    def imported_allocations(self, states):
        allocations = {}
        for point, draft in self.drafts.items():
            local = [s for s in states if s['tipping_point'] == point]
            if draft.get('direct_tip_rows'):
                allocations.update(OptimisedToManualPlan.direct_tip_allocations(local, draft['direct_tip_rows']))
            allocations.update({k: v for k, v in (draft.get('allocations') or {}).items() if k in {s['state_key'] for s in local}})
        return allocations

    def build_report(self, states, allocations=None):
        allocations = allocations or {}
        inventories = self._inventories()
        frames, payload_use, produced = [], defaultdict(float), defaultdict(float)
        history = []
        previous_start, opening_production = None, {}
        try:
            for state in sorted(states, key=lambda s: (s['start_datetime'], s['tipping_point'])):
                point = state['tipping_point']; planner = self.planners[point]
                if state['start_datetime'] != previous_start:
                    previous_start = state['start_datetime']; opening_production = dict(produced)
                group = self.build_group(state['opf'])
                selected = allocations.get(state['state_key'], {})
                for candidate in state.get('direct_tip_candidates', []):
                    amount = number(selected.get(candidate['source'], 0), 'Direct-tip tonnes')
                    available = candidate['available_tonnes']
                    ids = candidate.get('direct_tip_ids') or [candidate['source']+'|'+str(state['start_datetime'])]
                    # The same prepared payload may be eligible at multiple points.
                    for payload_id in ids:
                        payload_use[payload_id] += amount / available if available else 0
                        if payload_use[payload_id] > 1 + 1e-7:
                            raise ManualBlendPlanningError(f'{candidate["source"]}: direct-tip payload allocated more than once across tipping points.')
                frame = planner.build_report([state], allocations, inventory=inventories[point], finalize=False,
                                             produced_tonnes=opening_production.get(group, 0))
                frame['tipping_point'], frame['opf'] = point, state['opf']
                recipe = next(r for r in self.drafts[point]['recipes'] if r['id'] == state['blend_ID'])
                for row in frame.loc[frame.source_type.eq('stockpile')].to_dict('records'):
                    option = next((s for s in recipe['sources'] if s['source'].upper() == row['source']), {})
                    limit = option.get('reclaim_rate')
                    if limit is not None and row['equipment_rate_output'] > number(limit, 'Reclaim rate') + 1e-6:
                        raise ManualBlendPlanningError(f'{point} / {row["source"]}: recipe reclaim rate exceeded. Reduce crusher rate or source weight.')
                    self._consume(inventories, row['source'], row['source_actual_tonnes'], except_point=point)
                produced[group] += frame.product_build_source_tonnes.sum()
                frame.attrs = {}; frames.append(frame)
                history.append(dict(snapshot_datetime=state['end_datetime'], steady_state_number=state['steady_state_number'],
                    balances={s: sum(c['balance'] for c in rows) for inv in inventories.values() for s, rows in inv.items()}))
            if not frames:
                raise ManualBlendPlanningError('The manual sequence contains no positive-duration intervals.')
            report = pd.concat(frames, ignore_index=True)
            from classes.ManualAllocationEdits import recalculate
            result = recalculate(report, report, self.calendar, self.periods, self.targets, self.topology)
            result['manual_origin'] = 'Manual recipe'
            result.attrs['physical_balance_history'] = history
            return result
        except ValueError as exc:
            raise ManualBlendPlanningError(str(exc)) from exc

    def state_summaries(self, states, allocations=None):
        report = self.build_report(states, allocations)
        result = {}
        for state in states:
            rows = report[report.steady_state_number.eq(state['steady_state_number'])]
            direct = sum((allocations or {}).get(state['state_key'], {}).values())
            capacity = state['feed_capacity_tonnes']
            result[state['state_key']] = {**state, 'stockpile_feed_tonnes': capacity-direct,
                'direct_tip_ratio': direct/capacity if capacity else 0,
                **{f'output_grade_{g}': rows.iloc[0]['crusher_actual_grade_'+g] if not rows.empty else 0 for g in self.GRADES}}
        return result
