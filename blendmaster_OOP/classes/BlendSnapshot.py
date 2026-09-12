"""Stable Gantt selection across refreshes and repeated blend occurrences."""
import pandas as pd


def identity_matches(values, selected):
    # JSON turns 0.0 into 0; SQLite/Pandas may retain the floating dtype.
    number = pd.to_numeric(pd.Series([selected]), errors='coerce').iloc[0]
    if pd.notna(number):
        return pd.to_numeric(values, errors='coerce').eq(number)
    return values.astype(str).eq(str(selected))


def selected_state(data, click):
    points = (click or {}).get('points') or []
    if not points or data.empty:
        return data
    point = points[0]
    identity = point.get('customdata')
    if isinstance(identity, (list, tuple)) and len(identity) >= 4:
        state, start, end, lane = identity[:4]
        mask = identity_matches(data.steady_state_number, state)
        mask &= pd.to_datetime(data.start_datetime).eq(pd.Timestamp(start))
        mask &= pd.to_datetime(data.end_datetime).eq(pd.Timestamp(end))
        mask &= identity_matches(data.lane, lane)
        return data[mask].copy()
    if 'lane' in data and 'y' in point:
        return data[identity_matches(data.lane, point['y'])].copy()
    return data
