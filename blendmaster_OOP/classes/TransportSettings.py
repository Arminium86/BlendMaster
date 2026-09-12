"""Versioned, scenario-owned conveyor and coarse ore stockpile settings."""

from copy import deepcopy
from classes.MultiFeedSettings import number

TRANSPORT_VERSION = 1


def transport_settings(value=None, *, tipping_points=None):
    value = value or {}
    if not isinstance(value,dict) or type(value.get('schema_version',1)) is not int or value.get('schema_version', 1) != TRANSPORT_VERSION:
        raise ValueError('Unsupported conveyor/COS settings version. Update BlendMaster before opening this project.')
    points = {}
    for name, original in (value.get('tipping_points') or {}).items():
        if tipping_points is not None and name not in tipping_points:
            raise ValueError(f'{name}: transport settings refer to an unselected operating crusher.')
        row = deepcopy(original)
        enabled = row.get('enabled', False)
        if not isinstance(enabled,bool):
            raise ValueError(f'{name}: Enable conveyor/COS must be true or false.')
        conveyor = number(row.get('conveyor_capacity_wmt', 0), f'{name}: Conveyor capacity')
        cos = number(row.get('cos_capacity_wmt', 0), f'{name}: COS capacity')
        chunks = number(row.get('cos_chunks', 10), f'{name}: COS chunks', positive=True)
        if chunks != int(chunks) or chunks > 1000:
            raise ValueError(f'{name}: COS chunks must be a whole number between 1 and 1000.')
        payload = number(row.get('rehandle_payload_wmt', 200), f'{name}: Rehandle payload', positive=True)
        spot = number(row.get('spot_seconds', 30), f'{name}: Spot time')
        dump = number(row.get('dump_seconds', 30), f'{name}: Dump time')
        if enabled and conveyor + cos <= 0:
            raise ValueError(f'{name}: enter a positive conveyor or COS capacity, or disable transport.')
        points[str(name)] = dict(enabled=enabled, conveyor_capacity_wmt=conveyor,
            cos_capacity_wmt=cos, cos_chunks=int(chunks), rehandle_payload_wmt=payload,
            spot_seconds=spot, dump_seconds=dump)
    return dict(schema_version=TRANSPORT_VERSION, tipping_points=points)


def transport_enabled(value):
    return any(row['enabled'] for row in transport_settings(value)['tipping_points'].values())


def reference_rate(targets):
    """Use the first operating Calendar rate when the scenario opens stopped."""
    rates = [float(row.get('crusher_rate', 0) or 0) for row in (targets or {}).values()]
    return next((rate for rate in rates if rate > 0), 0.0)


def history_lookback_hours(point, rate):
    if not point['enabled']:
        return 0.0
    rate = number(rate, 'Opening crusher rate', positive=True)
    return (point['conveyor_capacity_wmt'] + point['cos_capacity_wmt']) / rate
