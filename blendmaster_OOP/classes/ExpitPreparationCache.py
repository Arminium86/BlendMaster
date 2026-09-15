"""Separate schedule parsing from actual-sequence refresh and transport modelling."""
import json
import os
import pandas as pd
from database.SQLiteDatabase import DatabaseManager


class ParsedPayloadCache(DatabaseManager):
    EXPIT_INPUT_CACHE_TABLE = 'expit_parsed_payload_cache'


def file_identity(path):
    path = os.path.normcase(os.path.abspath(str(path))) if path else ''
    try:
        stat = os.stat(path)
        return dict(path=path, exists=True, size=stat.st_size, modified_ns=stat.st_mtime_ns)
    except OSError:
        return dict(path=path, exists=False)


def feed_identity(feed):
    feed = feed or {}
    return dict(mode=feed.get('mode', 'single'), tipping_points=[
        {key: point.get(key) for key in ('name', 'opf', 'direct_tip_enabled')}
        for point in feed.get('tipping_points', [])])


def reusable_sequence(host, signature):
    """The saved model-time anchor never advances on a cache read."""
    transactions, previous, metadata = DatabaseManager().read_latest_expit_input_cache()
    if transactions is None:
        return None, {}
    try:
        old, new = json.loads(previous), json.loads(signature)
        anchor, current = old.pop('start_time', None), new.pop('start_time', None)
        if old != new:
            return None, {}
        reconcile = int(getattr(host, 'expit_mode_choice', 1) or 1) == 2
        tolerance = max(int(getattr(host, 'expit_refresh_tolerance_minutes', 30) or 0), 0)
        if reconcile and tolerance == 0:
            return None, {}
        shift = abs((pd.Timestamp(current) - pd.Timestamp(anchor)).total_seconds()) / 60 if current != anchor else 0
        if pd.isna(shift) or (shift and (not reconcile or shift > tolerance)):
            return None, {}
    except (ValueError, TypeError, AttributeError):
        return None, {}
    return transactions, {**metadata, 'cache_match': 'exact' if not shift else 'within_sequence_tolerance',
                          'sequence_anchor': anchor, 'cache_time_shift_minutes': shift,
                          'cache_tolerance_minutes': tolerance}
