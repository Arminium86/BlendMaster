import os
import sqlite3
import sys
import types

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without pandas
    pd = types.SimpleNamespace(
        DataFrame=lambda *args, **kwargs: type("FakeDF", (), {"empty": True})(),
        to_datetime=lambda *args, **kwargs: None,
    )
    sys.modules["pandas"] = pd

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without numpy
    np = types.SimpleNamespace()
    sys.modules["numpy"] = np

# Ensure the blendmaster_OOP package is on the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'blendmaster_OOP'))

from database.SQLiteDatabase import DatabaseManager


def test_blank_build_report_table_created(tmp_path):
    os.chdir(tmp_path)
    db_manager = DatabaseManager()
    empty_df = pd.DataFrame()
    db_manager.write_build_report_to_database(empty_df)

    conn = sqlite3.connect('blendmaster.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='build_report'")
    table_exists = cursor.fetchone() is not None
    cursor.execute('SELECT COUNT(*) FROM build_report')
    row_count = cursor.fetchone()[0]
    conn.close()

    assert table_exists
    assert row_count == 0


def test_empty_optimised_blend_report_table_created(tmp_path):
    os.chdir(tmp_path)
    db_manager = DatabaseManager()
    empty_df = pd.DataFrame()
    db_manager.write_optimised_blend_report_to_database(empty_df, periods=None)

    conn = sqlite3.connect('blendmaster.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimised_blend_report'")
    table_exists = cursor.fetchone() is not None
    cursor.execute('SELECT COUNT(*) FROM optimised_blend_report')
    row_count = cursor.fetchone()[0]
    conn.close()

    assert table_exists
    assert row_count == 0
