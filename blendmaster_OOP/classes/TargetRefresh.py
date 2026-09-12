"""Merge warehouse targets without changing a planner's target policy.

Imported values have a small baseline for subsequent three-way merges. Legacy
rows have no baseline, so their values are conservatively treated as overrides.
"""
from copy import deepcopy


def target_identity(row):
    return tuple(str(row.get(key) or '').strip().upper()
                 for key in ('opf', 'crusher', 'brand', 'byproduct', 'build_name'))


def editable_values(row):
    return {key: deepcopy(value) for key, value in row.items()
            if key.startswith('target_') or key in ('quality_limits', 'product_target_schema_version')}


def merge_refreshed_targets(existing, incoming):
    """Return merged rows and a reviewable list of retained/applied differences."""
    previous = {target_identity(row): row for row in existing or []}
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
