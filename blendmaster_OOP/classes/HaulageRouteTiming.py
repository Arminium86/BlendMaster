"""Destination-sensitive APS haulage, with frozen 2WP route evidence per payload."""
from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from classes.DestinationRules import address, source_key, stockpile, text

VERSION = 1
COMPONENTS = ('LoadedTravel', 'SpotAtDump', 'Dumping')
FIELDS = tuple('HaulageResult.Times.' + name for name in COMPONENTS)
LEVELS = (('flitch', 5), ('blast', 4), ('bench', 3), ('stage', 2), ('pit', 1), ('mine', 0))


def minutes(row):
    result = {}
    for short, full in zip(COMPONENTS, FIELDS):
        try:
            value = float(row.get(full, row.get(short)))
        except (ValueError, TypeError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        result[short] = value
    return result


class RouteTiming:
    def __init__(self, rows=()):
        self.exact = defaultdict(list)
        self.levels = {name: defaultdict(list) for name, _ in LEVELS}
        self.destinations = set()
        self.rows = []
        for number, raw in enumerate(rows, 1):
            duration = minutes(raw)
            source = source_key(raw.get('Source.FullName'))
            destination = stockpile(raw.get('Destination.FullName') or raw.get('Destination.Name'))
            if not source or not destination or duration is None:
                continue
            location = address(source)
            material = location[1][-1] if location else ''
            ore_type = text(raw.get('MutexParcel.ORETYPE')).upper()
            item = dict(duration, source=source, destination=destination,
                        truck=text(raw.get('Haulage.Truck')), row=number,
                        material=material, ore=(bool(material) and material not in {'WA', 'WS', 'WASTE', 'OB'}
                        and 'WASTE' not in ore_type and text(raw.get('Source.Type')).upper() != 'WASTE'))
            self.rows.append(item)
            self.destinations.add(destination)
            self.exact[(source, destination)].append(item)
            if location:
                mine, parts = location
                for name, count in LEVELS:
                    # Mine fallback is meaningful only for a full mine address.
                    if name == 'mine' and not mine:
                        continue
                    self.levels[name][(mine, *parts[:count], destination)].append(item)
        self.signature = hashlib.sha256(json.dumps([VERSION, self.rows], sort_keys=True).encode()).hexdigest()

    @staticmethod
    def choose(rows, truck, basis):
        if not rows:
            return None
        matching = [r for r in rows if text(r['truck']).casefold() == text(truck).casefold()] if text(truck) else []
        row = (matching or rows)[0]
        return {**{key: row[key] for key in (*COMPONENTS, 'source', 'destination', 'truck', 'row')},
                'basis': basis, 'truck_basis': '24HR truck match' if matching else 'First matching 2WP row'}

    def lookup(self, source, destination, truck=''):
        source, destination = source_key(source), stockpile(destination)
        exact = self.choose(self.exact.get((source, destination), []), truck, 'Exact 2WP route')
        if exact:
            return exact
        location = address(source)
        if not location:
            return None
        mine, parts = location
        for same_material in (True, False):
            for name, count in LEVELS:
                choices = self.levels[name].get((mine, *parts[:count], destination), [])
                choices = [r for r in choices if (r['material'] == parts[-1] if same_material
                                                  else r['ore'] and r['material'] != parts[-1])]
                selected = self.choose(choices, truck, f"2WP {'same material' if same_material else 'other ore'} / {name}")
                if selected:
                    return selected
        return None

    def payload_context(self, row, loading_hours):
        fallback = minutes(row)
        if fallback is None:
            raise ValueError('24HR travel, spot-at-dump and dumping times must be finite, non-negative minutes.')
        if not math.isfinite(loading_hours) or loading_hours < 0:
            raise ValueError('Payload loading duration must be finite and non-negative.')
        source, truck = row.get('Source.FullName'), text(row.get('Haulage.Truck'))
        routes = {destination: result for destination in sorted(self.destinations)
                  if (result := self.lookup(source, destination, truck)) is not None}
        return dict(version=VERSION, truck=truck, loading_hours=loading_hours,
                    fallback=fallback, routes=routes, signature=self.signature)


@lru_cache(maxsize=8)
def _read(path, size, modified_ns):
    fields = {'Source.FullName', 'Source.Type', 'Destination.FullName', 'Destination.Name',
              'Haulage.Truck', 'MutexParcel.ORETYPE', *FIELDS}
    frame = pd.read_csv(path, usecols=lambda column: column in fields)
    return RouteTiming(frame.to_dict('records'))


def from_csv(path):
    if not path:
        return RouteTiming()
    source = Path(path)
    revision = source.stat()
    return _read(str(source.resolve()), revision.st_size, revision.st_mtime_ns)


def payload_context(payload):
    value = payload.get('haulage')
    if not isinstance(value, dict):
        value = payload.get('haulage_json')
        try:
            value = json.loads(value) if isinstance(value, str) and value else {}
        except (ValueError, TypeError):
            raise ValueError('Saved payload haulage evidence is invalid; prepare inputs again.')
    return value if isinstance(value, dict) else {}


def arrival(payload, destination):
    """Recompute from loading start, never add a new leg to an earlier ETA."""
    context = payload_context(payload)
    if not context:
        return dict(delivered_datetime=payload.get('delivered_datetime'),
                    haulage_basis='Legacy payload; prepare inputs to resolve 2WP route timing')
    route = context.get('routes', {}).get(stockpile(destination))
    if route is None:
        route = dict(context['fallback'], basis='24HR fallback: no valid 2WP route within the configured hierarchy',
                     truck=context.get('truck', ''), truck_basis='24HR fallback', source=payload.get('source'), row=None)
    duration = minutes(route)
    if duration is None:
        raise ValueError('Saved payload route times are invalid; prepare inputs again.')
    start = pd.Timestamp(payload.get('start_datetime'))
    if pd.isna(start):
        raise ValueError('Loading start is required to recalculate payload arrival.')
    eta = start + pd.Timedelta(hours=float(context['loading_hours']), minutes=sum(duration.values()))
    return dict(delivered_datetime=eta, haulage_basis=route['basis'], haulage_truck=route.get('truck', ''),
                haulage_truck_basis=route.get('truck_basis', ''), haulage_route_source=route.get('source', ''),
                haulage_route_row=route.get('row'), haulage_destination=stockpile(destination),
                haulage_loaded_travel_minutes=duration['LoadedTravel'], haulage_spot_at_dump_minutes=duration['SpotAtDump'],
                haulage_dumping_minutes=duration['Dumping'], haulage_loading_minutes=float(context['loading_hours']) * 60,
                haulage_signature=context.get('signature', ''))


def reroute(payload, destination):
    result = dict(payload, destination=destination)
    result.update(arrival(result, destination))
    return result
