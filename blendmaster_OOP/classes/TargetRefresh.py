"""Merge warehouse targets without changing a planner's target policy.

Imported values have a small baseline for subsequent three-way merges. Legacy
rows have no baseline, so their values are conservatively treated as overrides.
"""
from copy import deepcopy
from datetime import datetime


def target_identity(row):
    scope = tuple(str(row.get(key) or '').strip().upper()
                  for key in ('opf', 'crusher', 'brand', 'byproduct'))
    # The combined-OPF table renumbers display names across all points, whereas
    # warehouse names restart at each point. Match the underlying build period
    # so a display renumber cannot duplicate a target or detach its policy.
    period = [row.get(key) for key in ('planning_period_start', 'planning_period_end')]
    if all(value is not None and str(value).strip() for value in period):
        times = tuple(datetime.fromisoformat(str(value)).isoformat() for value in period)
        return scope + ('period', *times, str(row.get('planning_operation') or '').strip().upper())
    return scope + ('name', str(row.get('build_name') or '').strip().upper())


def editable_values(row):
    return {key: deepcopy(value) for key, value in row.items()
            if key.startswith('target_') or key in ('quality_limits', 'product_target_schema_version')}


def merge_refreshed_targets(existing, incoming):
    """Return merged rows and a reviewable list of retained/applied differences."""
    previous = {}
    for row in existing or []:
        identity = target_identity(row)
        if identity in previous:
            raise ValueError('More than one product target matches the same build. '
                             'Review duplicate targets before refreshing.')
        previous[identity] = row
    merged, changes, seen = [], [], set()
    for supplied in incoming or []:
        row = deepcopy(supplied)
        identity = target_identity(row)
        old = previous.get(identity)
        imported = editable_values(row)
        if old is not None:
            baseline = old.get('_imported_target_values') or {}
            for key, value in editable_values(old).items():
                policy = key in ('target_mode', 'target_evaluation_basis', 'quality_limits',
                                 'product_target_schema_version') or key.endswith('_limit_mode')
                overridden = key not in baseline or value != baseline[key]
                if policy or overridden:
                    row[key] = deepcopy(value)
                if key in imported and value != imported[key]:
                    changes.append(dict(build=row.get('build_name', ''), field=key,
                                        existing=value, imported=imported[key],
                                        action='preserved' if policy or overridden else 'updated'))
            # Keep manual metadata not supplied by the warehouse.
            row = {**deepcopy(old), **row}
        row['_imported_target_values'] = imported
        merged.append(row)
        seen.add(identity)
    for old in existing or []:
        if target_identity(old) not in seen:
            merged.append(deepcopy(old))
            changes.append(dict(build=old.get('build_name', ''), field='build',
                                action='review', reason='Not present in refreshed targets; retained for review.'))
    return merged, changes
