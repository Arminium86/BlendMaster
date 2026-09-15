"""Read report queries off-thread; apply only the latest request to the UI."""
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
from PyQt5.QtWidgets import QMessageBox
from database.DatabaseContext import get_database_path


def begin(host, query):
    generation = vars(host).get('_report_query_generation', 0) + 1
    host._report_query_generation = generation
    database = get_database_path()
    site = vars(host).get('active_scenario_id')
    configure = host.configure_read_only_sqlite_connection
    host.sqlite_report_status.setText('Loading query results…')

    def current():
        return (vars(host).get('_report_query_generation') == generation
                and get_database_path() == database
                and vars(host).get('active_scenario_id') == site
                and host.sqlite_report_query.text().strip() == query)

    def work():
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            configure(connection)
            return pd.read_sql_query(query, connection)

    def done(frame):
        if not current():
            return
        host.current_sqlite_report_df = frame
        host.populate_dataframe_table(host.sqlite_report_table, frame)
        host.sqlite_report_status.setText(f"Query returned {len(frame):,} row{'s' if len(frame) != 1 else ''}.")

    def failed(error):
        if not current():
            return
        message = error['message']
        host.sqlite_report_status.setText(f'Query failed: {message}')
        QMessageBox.warning(host, 'Reports', f'Unable to run query: {message}')

    host.run_background_task('Loading report query…', work, done, failed, show_progress=False)
