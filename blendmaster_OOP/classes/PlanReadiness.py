"""Separate calculation completion from the evidence needed to release a plan."""
import math
import pandas as pd
from classes.ProductQualityReport import quality_report_rows
from classes.EquipmentLimits import equipment_violations


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def physical_violations(report):
    if report is None or report.empty:
        return ['No physical blend rows are available.']
    errors = []
    for column in ('source_actual_tonnes', 'source_closing_balance'):
        if column not in report:
            errors.append(f'The report lacks {column}.')
            continue
        values = pd.to_numeric(report[column], errors='coerce')
        bad = values.isna() | ~values.map(math.isfinite) | (values < -1e-4)
        if bad.any():
            names = report.loc[bad, 'source'].astype(str).unique() if 'source' in report else []
            errors.append(f'Invalid {column.replace("_", " ")}: ' + ', '.join(names[:8]))
    if {'start_datetime', 'end_datetime'}.issubset(report):
        start = pd.to_datetime(report.start_datetime, errors='coerce')
        end = pd.to_datetime(report.end_datetime, errors='coerce')
        if start.isna().any() or end.isna().any() or (end <= start).any():
            errors.append('Every blend needs a valid, positive time interval.')
    return errors


def evaluate(report, targets=None, *, destination=None, equipment_errors=None, run_status='complete'):
    dimensions = []
    def add(name, status, detail):
        dimensions.append(dict(check=name, status=status, detail=str(detail)))
    add('Calculation', 'pass' if run_status == 'complete' else 'review', run_status)
    errors = physical_violations(report)
    add('Physical inventory', 'blocked' if errors else 'pass', '; '.join(errors) or 'Source balances and tonnes are valid.')
    if equipment_errors is not None:
        add('Equipment', 'blocked' if equipment_errors else 'pass', '; '.join(equipment_errors) or 'Within Calendar limits.')
    else:
        add('Equipment', 'not_checked', 'Equipment audit was not supplied.')
    shortfalls = []
    frame = report if isinstance(report, pd.DataFrame) else pd.DataFrame()
    for target in targets or []:
        name = str(target.get('build_name') or '')
        lane = str(target.get('byproduct') or '').lower()
        prefix = 'product_build_' + (lane + '_' if lane in ('lump', 'fines') else '')
        selected = frame
        if prefix + 'name' in frame:
            selected = frame[frame[prefix + 'name'].fillna('').astype(str).eq(name)]
            opf = str(target.get('opf') or '')
            if opf and prefix + 'opf' in selected:
                selected = selected[selected[prefix + 'opf'].fillna('').astype(str).eq(opf)]
        else:
            selected = pd.DataFrame()
        delivered = max((finite(v) for v in selected.get(prefix + 'closing_tonnes', [])), default=finite(target.get('opening_tonnes')))
        remaining = max(finite(target.get('target_tonnes')) - delivered, 0)
        if remaining > .1:
            shortfalls.append(f'{name}: {remaining:,.1f} t remaining')
    add('Product targets', 'review' if shortfalls else 'pass' if targets else 'not_checked',
        '; '.join(shortfalls) or ('Targets attained.' if targets else 'No product targets configured.'))
    quality = [row for grain in ('steady_state', 'cumulative_build')
               for row in quality_report_rows(frame, grain=grain)
               if row.get('evaluation_basis') == row.get('grain')]
    # Cumulative policy is assessed on the last state for each build/analyte;
    # intermediate recovery trajectories remain available in the full audit.
    selected, cumulative = [], {}
    for row in quality:
        if row['grain'] == 'steady_state':
            selected.append(row)
        else:
            key = (row.get('opf'), row.get('build_name'), row.get('lane'), row.get('analyte'))
            if key not in cumulative or str(row.get('start_datetime', '')) >= str(cumulative[key].get('start_datetime', '')):
                cumulative[key] = row
    selected.extend(cumulative.values())
    breached = [r for r in selected if 'breached' in str(r.get('quality_status', '')).lower()]
    add('Product quality', 'review' if breached else 'pass' if selected else 'not_checked',
        '; '.join(dict.fromkeys(f"{r.get('build_name')}/{r.get('analyte')}: {r.get('quality_status')}" for r in breached))
        or ('Within the selected target policy.' if selected else 'Quality evidence is unavailable; regenerate the plan.'))
    if destination is None or destination.empty:
        add('Destinations', 'not_checked', 'Destination allocation evidence is unavailable.')
    else:
        columns = [c for c in destination if any(k in c.lower() for k in ('status', 'unresolved', 'outside'))]
        suspicious = False
        for column in columns:
            if 'status' in column.lower():
                suspicious |= destination[column].astype(str).str.contains('unresolved|outside|missing|unallocated', case=False).any()
            else:
                suspicious |= (pd.to_numeric(destination[column], errors='coerce').fillna(0) > 1e-5).any()
        add('Destinations', 'review' if suspicious else 'pass' if columns else 'not_checked',
            'Review unresolved/outside-horizon movements.' if suspicious else
            'Allocation evidence available.' if columns else 'Saved evidence has no allocation status.')
    ready = all(row['status'] == 'pass' for row in dimensions)
    return dict(status='ready' if ready else 'requires_review', checks=dimensions,
                message='Plan checks passed.' if ready else 'Run finished; plan requires review. ' +
                    ' '.join(row['check'] + ': ' + row['detail'] for row in dimensions if row['status'] != 'pass'))
