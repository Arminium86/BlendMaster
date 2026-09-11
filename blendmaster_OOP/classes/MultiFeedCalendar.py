"""Calendar owns per-tipping-point targets and total reclaim capacity."""
from copy import deepcopy
from urllib.parse import quote


FIELDS = [('crusher_rate', 'Rate', 1000), ('max_reclaim_rate', 'Max Reclaim Rate', 1000),
          ('direct_feed_ratio_min', 'Direct Tip Ratio Min', 0), ('direct_feed_ratio_max', 'Direct Tip Ratio Max', 1)]
FIELDS += [(f'target_{a}_{b}', f'{a.upper()} {b.title()}', 0 if b == 'min' else 100)
           for a in ('fe', 'si', 'al', 'p', 'mn') for b in ('min', 'max')]


def calendar_key(point, field):
    return f'tipping_point_{quote(point, safe="")}_{field}'


def apply_calendar(settings, calendar, labels):
    result = deepcopy(settings)
    for point in result['tipping_points']:
        for index, label in enumerate(labels):
            period = 'preplan' if index == 0 else f'period_{index}'
            target = point['targets_by_period'].setdefault(period, {})
            for field, _, default in FIELDS:
                key = calendar_key(point['name'], field)
                value = (calendar.get(key) or {}).get(label)
                if value not in (None, ''):
                    target[field] = float(value)
                elif field not in target:
                    legacy = 'reclaim_equipment_max_reclaim_rate' if field == 'max_reclaim_rate' else 'crusher_' + field if field != 'crusher_rate' else field
                    if field.startswith('direct_feed_ratio_'):
                        legacy = 'crusher_direct_tip_ratio_' + field.rsplit('_', 1)[-1]
                    old = (calendar.get(legacy) or {}).get(label)
                    try:
                        target[field] = float(old) if old not in (None, '') else default
                    except (TypeError, ValueError):
                        target[field] = default
            target['brand'] = ''  # The active Product Target supplies the brand.
    return result


def calendar_rows(settings, labels, builds):
    config = apply_calendar(settings, {}, labels)
    rows = []
    for point in config['tipping_points']:
        rows.append((f"Crusher — {point['name']} ({point['opf']})", [False] * len(labels), 'blue', [''] * len(labels)))
        brands = list(dict.fromkeys(str(b.get('brand') or '') for b in builds
            if point['opf'] in [v.strip() for v in str(b.get('opf') or point['opf']).split(',')] and b.get('brand')))
        caption = 'From Product Targets' + (': ' + ' → '.join(brands) if brands else '')
        rows.append({calendar_key(point['name'], 'brand'): ('  Brand', [False] * len(labels), 'blue', [caption] * len(labels))})
        for field, title, default in FIELDS:
            values = [point['targets_by_period']['preplan' if i == 0 else f'period_{i}'].get(field, default) for i in range(len(labels))]
            rows.append({calendar_key(point['name'], field): ('  ' + title, [True] * len(labels), 'blue', values)})
    return rows


def legacy_aggregate_calendar(settings, calendar, labels):
    """Keep the existing physical event loader supplied with aggregate equipment."""
    result = deepcopy(calendar)
    for field, key in [('crusher_rate', 'crusher_rate'), ('max_reclaim_rate', 'reclaim_equipment_max_reclaim_rate')]:
        result[key] = {label: sum(p['targets_by_period']['preplan' if i == 0 else f'period_{i}'][field]
                               for p in settings['tipping_points']) for i, label in enumerate(labels)}
    result['crusher_brand'] = dict.fromkeys(labels, '')
    for field, _, default in FIELDS:
        if field.startswith('target_'):
            result['crusher_' + field] = dict.fromkeys(labels, default)
    result['crusher_direct_tip_ratio_min'] = dict.fromkeys(labels, 0)
    result['crusher_direct_tip_ratio_max'] = dict.fromkeys(labels, 1)
    return result
