"""Resolve the current saved-plan feed sources without loading wide reports."""
from contextlib import closing
from pathlib import Path
import math
import sqlite3

from classes.GradeStreams import normalise_opf
from setup.ProductAssayHistory import awst


def live_mode(state):
    try:
        return int(state.get('time_mode_choice') or 0) == 1
    except (ValueError, TypeError):
        return False


def plan_rows(database, plan_id='Primary'):
    if not database or not Path(database).is_file():
        return []
    fields = ('source', 'source_id', 'source_type', 'parent_stockpile', 'opf',
              'tipping_point', 'start_datetime', 'end_datetime', 'source_actual_tonnes')
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            table = 'optimisation_plan_blend_report'
            named = table in names
            if not named:
                table = 'optimised_blend_report'
                if plan_id != 'Primary' or table not in names:
                    return []
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            selected = [name for name in fields if name in columns]
            if not {'start_datetime', 'end_datetime', 'source_actual_tonnes'} <= set(selected):
                return []
            sql = 'SELECT ' + ','.join(f'"{name}"' for name in selected) + f' FROM "{table}"'
            rows = connection.execute(sql + (' WHERE plan_id=?' if named else ''), (plan_id,) if named else ())
            return [dict(zip(selected, row)) for row in rows]
    except sqlite3.Error:
        # No verified active blend means no continuous corrections.
        return []


def active_sources(rows, now, default_opf=None):
    result = {}
    now = awst(now)
    for row in rows:
        try:
            amount = float(row.get('source_actual_tonnes') or 0)
            if not math.isfinite(amount) or amount <= 0:
                continue
            if str(row.get('source_type') or 'stockpile').lower() not in ('stockpile', 'amt', 'inventory'):
                continue
            if not awst(row['start_datetime']) <= now < awst(row['end_datetime']):
                continue
            opf = normalise_opf(row.get('opf') or default_opf)
            source = str(row.get('source_id') or row.get('source') or '').strip().upper()
            if opf and source:
                result.setdefault(opf, set()).add(source)
        except (ValueError, TypeError, KeyError):
            continue
    return {opf: sorted(sources) for opf, sources in sorted(result.items())}


def current_sources(state, database, now):
    if not live_mode(state) or (state.get('continuous_assay_settings') or {}).get('enabled') is False:
        return {}
    return active_sources(plan_rows(database, state.get('selected_optimisation_plan_id') or 'Primary'),
                          now, state.get('opf_input_choice'))
