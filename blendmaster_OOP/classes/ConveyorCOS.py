"""Conservative FIFO material transport, independent of UI and source depletion.

Amounts are physical ROM WMT. A material record holds a one-WMT EventData template
so every mapped extensive property and every OPF grade stream survives transport.
Conveyor movements are rate intervals; COS chunks mix their inputs and drain FIFO.
Only accepted tipping decisions are committed. Previews and solver retries operate
on detached copies, including the transport state in partial-plan repair.
"""

from collections import deque
from copy import deepcopy, copy
from datetime import timedelta
import math

import pandas as pd

from classes.CustomConstraints import source_property_kind
from classes.TransportSettings import transport_settings, history_lookback_hours

EPS = 1e-8


def moment(value):
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError('Transport movements require a valid delivery datetime.')
    if result.tzinfo is not None:
        result = result.tz_convert('Australia/Perth').tz_localize(None)
    return result.to_pydatetime()


def material_event(event, tonnes):
    """Copy chemistry while scaling only extensive source properties."""
    result = deepcopy(event)
    balance = float(event.balance)
    if balance <= 0:
        raise ValueError('Cannot transport material with a non-positive physical basis.')
    result.source_properties = {key: float(value)*float(tonnes)/balance
        if source_property_kind(key, event.source_property_kinds) == 'additive' else value
        for key, value in event.source_properties.items()}
    result._balance = float(tonnes)
    result._max_quantity = float(tonnes)
    return result


def material(event, *, point, opf, provenance='modelled', payload_id=''):
    return dict(event=material_event(event, 1), source=event.source_name or event.stockpile or event.grade_block,
                source_type='stockpile' if event.is_stockpile else 'grade_block',
                tipping_point=point, opf=opf, provenance=provenance, payload_id=str(payload_id))


class ConveyorCOS:
    def __init__(self, settings, start, opening_rates, history=()):
        self.settings = transport_settings(settings)
        self.start = self.now = moment(start)
        self.points = {}
        self.movements = []
        self.snapshots = []
        self.warnings = []
        self.opening_reconciliation_audits = [deepcopy(row['material']['reconciliation'])
            for row in history if row['material'].get('reconciliation')]
        self._serial = 0
        for name, cfg in self.settings['tipping_points'].items():
            if not cfg['enabled']:
                continue
            rate = float(opening_rates.get(name, 0))
            lookback = history_lookback_hours(cfg, rate)
            self.points[name] = dict(config=cfg, conveyor=deque(), chunks=deque(),
                opening_wmt=0.0, tipped_wmt=0.0, arrived_wmt=0.0, opening_rate=rate,
                delay_hours=cfg['conveyor_capacity_wmt'] / rate, conveyor_rate=rate, next_chunk=1)
            eligible = sorted((r for r in history if r['material']['tipping_point'] == name
                               and self.start - timedelta(hours=lookback) <= moment(r['time']) < self.start),
                              key=lambda r: (moment(r['time']), str(r['material'].get('payload_id', ''))))
            cos_rows, belt_rows = [], []
            for row in eligible:
                quantity = float(row['wmt'])
                if not math.isfinite(quantity) or quantity <= 0:
                    continue
                arrival = moment(row['time']) + timedelta(hours=self.points[name]['delay_hours'])
                delivered = quantity if cfg['conveyor_capacity_wmt'] <= EPS else min(
                    quantity, max(0.0, (self.start-arrival).total_seconds()/3600*rate))
                if delivered > EPS:
                    cos_rows.append((row, delivered))
                if quantity-delivered > EPS:
                    belt_rows.append((row, quantity-delivered, max(arrival,self.start)))
            # Recent evidence identifies the surviving FIFO tail. Excess older
            # history departed before the horizon and is never opening inventory.
            remaining = cfg['cos_capacity_wmt']
            retained = []
            for row, quantity in reversed(cos_rows):
                take = min(remaining, quantity)
                if take > EPS:
                    retained.append((row, take))
                    remaining -= take
            for row, take in reversed(retained):
                self._fill(name, row['material'], take, self.start)
            for chunk in self.points[name]['chunks']:
                chunk['sealed'] = True  # A partial measured opening chunk is usable.
            remaining = cfg['conveyor_capacity_wmt']
            retained = []
            for row, quantity, arrival in reversed(belt_rows):
                take = min(remaining, quantity)
                if take > EPS:
                    retained.append((row, take, arrival))
                    remaining -= take
            for row, take, arrival in reversed(retained):
                # A payload starts arriving after its recorded tip plus the belt delay.
                queue = self.points[name]['conveyor']
                begin = max(self.start, arrival, queue[-1]['end'] if queue else self.start)
                self._interval(name, row['material'], take, begin, begin+timedelta(hours=take/rate))
            state = self.points[name]
            state['opening_wmt'] = self.balance(name)
            capacity = cfg['cos_capacity_wmt'] + cfg['conveyor_capacity_wmt']
            if state['opening_wmt'] < capacity - EPS:
                self.warnings.append(f'{name}: actual movements reconstruct {state["opening_wmt"]:,.1f} of '
                                     f'{capacity:,.1f} ROM WMT capacity. Unobserved contents remain empty; no grades are inferred.')
        self.snapshot(self.start, 'opening')

    def _interval(self, point, mat, wmt, start, end):
        if wmt <= EPS:
            return
        self._serial += 1
        if end <= start:
            end = start + timedelta(microseconds=1)
        self.points[point]['conveyor'].append(dict(id=self._serial, material=deepcopy(mat),
            wmt=wmt, remaining=wmt, start=start, end=end, rate=wmt / ((end-start).total_seconds()/3600)))

    def _fill(self, point, mat, quantity, at):
        state = self.points[point]
        capacity = state['config']['cos_capacity_wmt'] / state['config']['cos_chunks']
        while quantity > EPS:
            if not state['chunks'] or state['chunks'][-1]['sealed']:
                state['chunks'].append(dict(id=state['next_chunk'], wmt=0.0, filled_wmt=0.0,
                                           sealed=False, components=[], opened=at))
                state['next_chunk'] += 1
            chunk = state['chunks'][-1]
            take = min(quantity, capacity - chunk['filled_wmt'])
            if take <= EPS:
                chunk['sealed'] = True
                continue
            chunk['components'].append(dict(material=deepcopy(mat), wmt=take))
            chunk['wmt'] += take
            chunk['filled_wmt'] += take
            chunk['sealed'] = chunk['filled_wmt'] >= capacity - EPS
            quantity -= take

    def balance(self, point):
        state = self.points[point]
        return sum(r['remaining'] for r in state['conveyor']) + sum(c['wmt'] for c in state['chunks'])

    def next_chunk_boundary_hours(self, rates, maximum):
        """COS fill/depletion boundaries; conveyor payloads do not split a solve."""
        duration = maximum
        for name, state in self.points.items():
            rate = float(rates.get(name, 0))
            cfg = state['config']
            if rate <= EPS or cfg['cos_capacity_wmt'] <= EPS:
                continue
            chunk_size = cfg['cos_capacity_wmt'] / cfg['cos_chunks']
            duration = min(duration, chunk_size / rate)
            if state['chunks'] and not state['chunks'][-1]['sealed']:
                duration = min(duration, (chunk_size-state['chunks'][-1]['filled_wmt']) / rate)
            if state['chunks'] and state['chunks'][0]['sealed']:
                duration = min(duration, state['chunks'][0]['wmt'] / rate)
        return duration

    def feed_fraction(self, point, duration, rate):
        if point not in self.points:
            return 1.0
        state = self.points[point]
        if rate <= EPS:
            return 0.0
        if state['config']['cos_capacity_wmt'] > EPS:
            return 0.0  # The current COS fill cannot depart before the next chunk boundary.
        delay = state['config']['conveyor_capacity_wmt'] / rate if rate > EPS else state['delay_hours']
        return max(0.0, duration - delay) / duration if duration > EPS else 0.0

    def add_feed(self, point, mat, quantity, start, end, rate):
        state = self.points[point]
        if quantity <= EPS:
            return
        delay = state['config']['conveyor_capacity_wmt'] / rate if rate > EPS else state['delay_hours']
        state['tipped_wmt'] += quantity
        payload = state['config']['rehandle_payload_wmt']
        count = math.ceil(quantity / payload) if mat['source_type'] == 'stockpile' else 1
        for index in range(count):
            amount = min(payload, quantity-index*payload) if count > 1 else quantity
            offset = index*payload if count > 1 else 0
            begin = start + (end-start) * (offset/quantity)
            finish = start + (end-start) * ((offset+amount)/quantity)
            self._interval(point, mat, amount, begin+timedelta(hours=delay), finish+timedelta(hours=delay))
            self.movements.append(dict(tipping_point=point, opf=mat['opf'], source=mat['source'],
                source_type=mat['source_type'], provenance=mat['provenance'], movement='tip',
                payload_id=f'{point}:{start.isoformat()}:{self._serial}' if mat['source_type'] == 'stockpile' else mat['payload_id'], chunk_id=None,
                start_datetime=begin, end_datetime=finish, physical_rom_wmt=amount,
                conveyor_arrival_start=begin+timedelta(hours=delay),
                conveyor_arrival_end=finish+timedelta(hours=delay)))

    def fork(self, *, audit=True):
        trial = copy(self)
        trial.points = deepcopy(self.points)
        # Audit rows are append-only. Avoid copying the whole plan at every solve.
        trial.movements = list(self.movements) if audit else []
        trial.snapshots = list(self.snapshots) if audit else []
        trial.warnings = list(self.warnings)
        return trial

    def prepare_rates(self, rates):
        """Change belt speed with Calendar capacity; an off crusher pauses it."""
        for point, state in self.points.items():
            rate = max(float(rates.get(point, 0)), 0)
            previous = state['conveyor_rate']
            if rate > EPS and abs(rate-previous) > EPS:
                for row in state['conveyor']:
                    begin = max(row['start'], self.now)
                    row['start'] = self.now + (begin-self.now) * (previous/rate)
                    row['end'] = self.now + (row['end']-self.now) * (previous/rate)
                    row['rate'] = row['remaining'] / ((row['end']-row['start']).total_seconds()/3600)
                state['conveyor_rate'] = rate

    def preview(self, end, rates):
        trial = self.fork(audit=False)
        return trial.advance(end, rates)

    def advance(self, end, rates):
        end = moment(end)
        if end < self.now:
            raise ValueError('Transport cannot move backwards without restoring a checkpoint.')
        self.prepare_rates(rates)
        outputs = []
        for point, state in self.points.items():
            at = self.now
            capacity = state['config']['cos_capacity_wmt']
            rate = max(float(rates.get(point, 0)), 0)
            if rate <= EPS:
                for row in state['conveyor']:
                    row['start'] = max(row['start'], self.now) + (end-self.now)
                    row['end'] += end-self.now
                continue
            while at < end:
                intervals = state['conveyor']
                active = [r for r in intervals if r['start'] <= at < r['end'] and r['remaining'] > EPS]
                boundary = min([end] + [t for r in intervals for t in (r['start'], r['end']) if t > at])
                ready = state['chunks'][0] if state['chunks'] and state['chunks'][0]['sealed'] else None
                if capacity > EPS and ready and rate > EPS:
                    boundary = min(boundary, at + timedelta(hours=ready['wmt']/rate))
                incoming = sum(r['rate'] for r in active)
                if capacity > EPS and incoming > EPS:
                    chunk_capacity = capacity/state['config']['cos_chunks']
                    tail = state['chunks'][-1] if state['chunks'] and not state['chunks'][-1]['sealed'] else None
                    free = chunk_capacity - (tail['filled_wmt'] if tail else 0)
                    boundary = min(boundary, at + timedelta(hours=free/incoming))
                if boundary <= at:
                    raise ValueError(f'{point}: transport boundary did not advance.')
                hours = (boundary-at).total_seconds()/3600
                if capacity > EPS and ready and rate > EPS:
                    take = min(ready['wmt'], rate*hours)
                    proportion = take/ready['wmt']
                    for component in ready['components']:
                        amount = component['wmt']*proportion
                        if amount > EPS:
                            outputs.append(dict(material=deepcopy(component['material']), wmt=amount,
                                                start=at, end=boundary, chunk_id=ready['id']))
                        component['wmt'] -= amount
                    ready['wmt'] -= take
                    if ready['wmt'] <= 1e-6:
                        state['chunks'].popleft()
                for row in active:
                    amount = min(row['remaining'], row['rate']*hours)
                    row['remaining'] -= amount
                    if capacity > EPS:
                        mat = row['material']
                        self.movements.append(dict(tipping_point=point, opf=mat['opf'], source=mat['source'],
                            source_type=mat['source_type'], provenance=mat['provenance'], movement='cos_inflow',
                            payload_id=mat['payload_id'], start_datetime=at, end_datetime=boundary,
                            physical_rom_wmt=amount))
                        self._fill(point, row['material'], amount, at)
                    elif amount > EPS:
                        outputs.append(dict(material=deepcopy(row['material']), wmt=amount,
                                            start=at, end=boundary, chunk_id=None))
                state['conveyor'] = deque(r for r in intervals if r['remaining'] > 1e-6)
                if sum(c['wmt'] for c in state['chunks']) > capacity + 1e-4:
                    raise ValueError(f'{point}: COS capacity would be exceeded; reduce tipping or correct opening evidence.')
                at = boundary
        self.now = end
        for row in outputs:
            mat = row['material']
            self.points[mat['tipping_point']]['arrived_wmt'] += row['wmt']
            self.movements.append(dict(tipping_point=mat['tipping_point'], opf=mat['opf'], source=mat['source'],
                source_type=mat['source_type'], provenance=mat['provenance'], movement='opf_arrival',
                payload_id=mat['payload_id'], chunk_id=row['chunk_id'], start_datetime=row['start'],
                end_datetime=row['end'], physical_rom_wmt=row['wmt'],
                **{f'grade_{a}': getattr(mat['event'], f'grade_{a}') for a in ('fe','si','al','p','mn')}))
        self.assert_balance()
        self.snapshot(end, 'closing')
        return outputs

    def assert_balance(self):
        for name, state in self.points.items():
            difference = state['opening_wmt'] + state['tipped_wmt'] - state['arrived_wmt'] - self.balance(name)
            if abs(difference) > 1e-4:
                raise ValueError(f'{name}: transport mass balance differs by {difference:g} ROM WMT.')

    @staticmethod
    def component_grade(components, analyte):
        metal = weight = 0.0
        for component in components:
            event = component['material']['event']
            field = event.source_property_weights.get(f'{event.selected_grade_stream}_{analyte}')
            coefficient = float(event.source_properties.get(field, 0)) if field else 1.0
            tonnes = component['wmt']*coefficient
            weight += tonnes
            metal += tonnes*getattr(event, f'grade_{analyte}')
        return metal/weight if weight > EPS else None

    def snapshot(self, at, kind):
        for point, state in self.points.items():
            for stage, records in [('conveyor', state['conveyor']), ('cos', state['chunks'])]:
                if not records:
                    self.snapshots.append(dict(datetime=at, snapshot=kind, tipping_point=point, stage=stage,
                                               chunk_id=None, physical_rom_wmt=0, composition='[]'))
                for item in records:
                    components = item.get('components') or [dict(material=item['material'], wmt=item['remaining'])]
                    amount = sum(c['wmt'] for c in components)
                    import json
                    self.snapshots.append(dict(datetime=at, snapshot=kind, tipping_point=point, stage=stage,
                        chunk_id=item['id'], physical_rom_wmt=amount,
                        chunk_status=('Ready for reclaim' if item.get('sealed') else 'Filling') if stage=='cos' else 'In transit',
                        composition=json.dumps([dict(source=c['material']['source'], wmt=c['wmt'],
                            provenance=c['material']['provenance']) for c in components]),
                        **{f'grade_{a}': self.component_grade(components, a) for a in ('fe','si','al','p','mn')}))
