import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from classes.PeriodManager import PeriodManager
from database.DatabaseContext import get_database_path

class DatabaseManager:
    @staticmethod
    def clear_all_tables(database_name=None):
        database_name = database_name or get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            )
            table_names = [row[0] for row in cursor.fetchall()]

            for table_name in table_names:
                safe_table_name = table_name.replace('"', '""')
                cursor.execute(f'DELETE FROM "{safe_table_name}"')

            conn.commit()
        finally:
            conn.close()

    def write_optimised_blend_report_to_database (self, results: pd.DataFrame, periods: PeriodManager):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table or use if it already exists
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS optimised_blend_report (
            start_datetime TEXT,
            end_datetime TEXT,
            steady_state_number INTEGER,
            blend_option TEXT,
            blend_ID TEXT,
            steady_state_duration INTEGER,
            period INTEGER,
            actual_direct_tip_ratio REAL,
            source TEXT,
            source_id TEXT,
            source_type TEXT,
            source_blend_ratio REAL,
            source_opening_balance REAL,
            source_actual_tonnes REAL,
            source_closing_balance REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_p REAL,
            source_grade_mn REAL,
            equipment TEXT,
            equipment_rate_input REAL,
            equipment_rate_output REAL,
            crusher_actual_tonnes REAL,
            crusher_rate_input REAL,
            crusher_rate_output REAL,
            crusher_actual_grade_fe REAL,
            crusher_actual_grade_si REAL,
            crusher_actual_grade_al REAL,
            crusher_actual_grade_p REAL,
            crusher_actual_grade_mn REAL,
            crusher_grade_target_min_fe REAL,
            crusher_grade_target_max_fe REAL,
            crusher_grade_target_min_si REAL,
            crusher_grade_target_max_si REAL,
            crusher_grade_target_min_al REAL,
            crusher_grade_target_max_al REAL,
            crusher_grade_target_min_p REAL,
            crusher_grade_target_max_p REAL,
            crusher_grade_target_min_mn REAL,
            crusher_grade_target_max_mn REAL
        )
        ''')

        cursor.execute("PRAGMA table_info(optimised_blend_report)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "source_id" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN source_id TEXT")
        if "source_type" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN source_type TEXT")
        if "actual_direct_tip_ratio" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN actual_direct_tip_ratio REAL")

        cursor.execute('DELETE FROM optimised_blend_report')

        if results.empty:
            conn.commit()
            conn.close()
            print(f"Optimised blend report saved to database {database_name}")
            return

        if "source_id" not in results.columns:
            results["source_id"] = results["source"]
        if "source_type" not in results.columns:
            results["source_type"] = ""
        if "actual_direct_tip_ratio" not in results.columns:
            results["actual_direct_tip_ratio"] = 0

        results['start_datetime'] = pd.to_datetime(results['start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
        results['end_datetime'] = pd.to_datetime(results['end_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # Insert each row from the DataFrame into the database
        for _, row in results.iterrows():
            cursor.execute('''
            INSERT INTO optimised_blend_report (
                start_datetime,
                end_datetime,
                steady_state_number,
                blend_option,
                blend_ID,
                steady_state_duration,
                period,
                actual_direct_tip_ratio,
                source,
                source_id,
                source_type,
                source_blend_ratio,
                source_opening_balance,
                source_actual_tonnes,
                source_closing_balance,
                source_grade_fe,
                source_grade_si,
                source_grade_al,
                source_grade_p,
                source_grade_mn,
                equipment,
                equipment_rate_input,
                equipment_rate_output,
                crusher_actual_tonnes,
                crusher_rate_input,
                crusher_rate_output,
                crusher_actual_grade_fe,
                crusher_actual_grade_si,
                crusher_actual_grade_al,
                crusher_actual_grade_p,
                crusher_actual_grade_mn,
                crusher_grade_target_min_fe,
                crusher_grade_target_max_fe,
                crusher_grade_target_min_si,
                crusher_grade_target_max_si,
                crusher_grade_target_min_al,
                crusher_grade_target_max_al,
                crusher_grade_target_min_p,
                crusher_grade_target_max_p,
                crusher_grade_target_min_mn,
                crusher_grade_target_max_mn
            ) VALUES (
                :start_datetime, :end_datetime, :steady_state_number, :blend_option, :blend_ID,
                :steady_state_duration, :period, :actual_direct_tip_ratio, :source, :source_id, :source_type, :source_blend_ratio, :source_opening_balance,
                :source_actual_tonnes, :source_closing_balance, :source_grade_fe, :source_grade_si,
                :source_grade_al, :source_grade_p, :source_grade_mn, :equipment, :equipment_rate_input,
                :equipment_rate_output, :crusher_actual_tonnes, :crusher_rate_input, :crusher_rate_output,
                :crusher_actual_grade_fe, :crusher_actual_grade_si, :crusher_actual_grade_al,
                :crusher_actual_grade_p, :crusher_actual_grade_mn, :crusher_grade_target_min_fe,
                :crusher_grade_target_max_fe, :crusher_grade_target_min_si, :crusher_grade_target_max_si,
                :crusher_grade_target_min_al, :crusher_grade_target_max_al, :crusher_grade_target_min_p,
                :crusher_grade_target_max_p, :crusher_grade_target_min_mn, :crusher_grade_target_max_mn
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Optimised blend report saved to database {database_name}")
        
        self.write_optimised_stockpile_depletion_report_to_database(results)
        StockpileProfileReport.write_optimised_stockpile_profile_report_to_database(periods)

    def write_product_build_report_to_database(self, results: pd.DataFrame):
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        try:
            if results is None:
                results = pd.DataFrame()
            results = results.copy()
            for column in results.columns:
                if pd.api.types.is_datetime64_any_dtype(results[column]):
                    results[column] = pd.to_datetime(results[column]).dt.strftime("%Y-%m-%d %H:%M:%S")
            results.to_sql("product_build_report", conn, if_exists="replace", index=False)
            conn.commit()
        finally:
            conn.close()
        print(f"Product build report saved to database {database_name}")

    def write_build_report_to_database (self, results: pd.DataFrame):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table or use if it already exists
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS build_report (
            steady_state_number INTEGER,
            steady_state_start_datetime TEXT,
            steady_state_end_datetime TEXT,
            agent TEXT,
            mining_start_datetime TEXT,
            source TEXT,
            stockpile TEXT,
            payload REAL,
            delivered_datetime TEXT,
            closing_balance REAL,
            grade_fe REAL,
            grade_si REAL,
            grade_al REAL,
            grade_p REAL,
            grade_mn REAL
        )
        ''')

        cursor.execute('DELETE FROM build_report')

        if not results.empty:

            results['steady_state_start_datetime'] = pd.to_datetime(results['steady_state_start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
            results['steady_state_end_datetime'] = pd.to_datetime(results['steady_state_end_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
            results['mining_start_datetime'] = pd.to_datetime(results['mining_start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
            results['delivered_datetime'] = pd.to_datetime(results['delivered_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

            # Insert each row from the DataFrame into the database
            for _, row in results.iterrows():
                cursor.execute('''
                INSERT INTO build_report VALUES (
                :steady_state_number, 
                :steady_state_start_datetime, 
                :steady_state_end_datetime, 
                :agent, 
                :mining_start_datetime, 
                :source, 
                :stockpile, 
                :payload, 
                :delivered_datetime, 
                :closing_balance, 
                :grade_fe, 
                :grade_si, 
                :grade_al, 
                :grade_p, 
                :grade_mn
                )
                ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Build report (if used) saved to database {database_name}")
        print(f"Expit payload transactions (if used) saved to database {database_name}")

    def write_expit_payload_transactions_to_database (self, results: pd.DataFrame):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table or use if it already exists
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS expit_payload_transactions (
            agent TEXT,
            source TEXT,
            start_datetime TEXT,
            payload REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_mn REAL,
            source_grade_p REAL,
            destination TEXT,
            delivered_datetime TEXT,
            direct_tip_id TEXT,
            destination_type TEXT,
            planned_destination TEXT,
            fallback_destination TEXT,
            aps_direct_tip_candidate INTEGER
        )
        ''')

        cursor.execute("PRAGMA table_info(expit_payload_transactions)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        optional_columns = {
            "direct_tip_id": "TEXT",
            "destination_type": "TEXT",
            "planned_destination": "TEXT",
            "fallback_destination": "TEXT",
            "aps_direct_tip_candidate": "INTEGER",
        }
        for column_name, column_type in optional_columns.items():
            if column_name not in existing_columns:
                cursor.execute(
                    f"ALTER TABLE expit_payload_transactions ADD COLUMN {column_name} {column_type}"
                )

        cursor.execute('DELETE FROM expit_payload_transactions')

        if "direct_tip_id" not in results.columns:
            results["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(results))
            ]
        for column_name in optional_columns:
            if column_name not in results.columns:
                results[column_name] = "" if column_name != "aps_direct_tip_candidate" else 0
        results["aps_direct_tip_candidate"] = results["aps_direct_tip_candidate"].map(
            lambda value: str(value).strip().lower() in {"true", "1", "yes"}
        ).astype(int)

        results['start_datetime'] = pd.to_datetime(results['start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
        results['delivered_datetime'] = pd.to_datetime(results['delivered_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # Insert each row from the DataFrame into the database
        for _, row in results.iterrows():
            cursor.execute('''
            INSERT INTO expit_payload_transactions (
                agent,
                source,
                start_datetime,
                payload,
                source_grade_fe,
                source_grade_si,
                source_grade_al,
                source_grade_mn,
                source_grade_p,
                destination,
                delivered_datetime,
                direct_tip_id,
                destination_type,
                planned_destination,
                fallback_destination,
                aps_direct_tip_candidate
            ) VALUES (
                :agent, 
                :source, 
                :start_datetime, 
                :payload, 
                :source_grade_fe, 
                :source_grade_si, 
                :source_grade_al, 
                :source_grade_mn, 
                :source_grade_p, 
                :destination, 
                :delivered_datetime,
                :direct_tip_id,
                :destination_type,
                :planned_destination,
                :fallback_destination,
                :aps_direct_tip_candidate
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Expit payload transactions saved to database {database_name}")

    def write_optimised_stockpile_depletion_report_to_database(self, blend_report: pd.DataFrame):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table for granular transactions if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS optimised_stockpile_depletion_report (
            start_datetime TEXT,
            end_datetime TEXT,
            steady_state_number INTEGER,
            period INTEGER,
            source TEXT,
            source_opening_balance REAL,
            source_actual_tonnes REAL,
            source_closing_balance REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_p REAL,
            source_grade_mn REAL,
            source_type TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(optimised_stockpile_depletion_report)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "source_type" not in existing_columns:
            cursor.execute(
                "ALTER TABLE optimised_stockpile_depletion_report ADD COLUMN source_type TEXT"
            )

        # Clear the table
        cursor.execute('DELETE FROM optimised_stockpile_depletion_report')

        # Placeholder for second-level transactions
        second_transactions = []

        for _, steady_state in blend_report.iterrows():
            steady_state_number = steady_state['steady_state_number']
            period = steady_state['period']
            source = steady_state['source']
            equipment_rate_output = steady_state['equipment_rate_output']
            duration_seconds = steady_state['steady_state_duration'] * 3600
            grades = {
                "fe": steady_state['source_grade_fe'],
                "si": steady_state['source_grade_si'],
                "al": steady_state['source_grade_al'],
                "p": steady_state['source_grade_p'],
                "mn": steady_state['source_grade_mn'],
            }
            source_opening_balance = steady_state['source_opening_balance']
            source_closing_balance = steady_state['source_closing_balance']
            source_type = steady_state.get('source_type', 'stockpile')

            # Calculate per-second depletion
            source_actual_tonnes_per_second = equipment_rate_output / 3600

            # Initialize start_datetime for this steady state
            start_datetime = datetime.strptime(steady_state['start_datetime'], '%Y-%m-%d %H:%M:%S')

            current_balance = source_opening_balance
            for second in range(int(duration_seconds)):
                end_datetime = start_datetime + timedelta(seconds=1)
                
                # Ensure balance integrity
                source_actual_tonnes = (
                    source_actual_tonnes_per_second if current_balance >= source_actual_tonnes_per_second 
                    else current_balance
                )
                source_closing_balance = current_balance - source_actual_tonnes

                # Append to transactions
                second_transactions.append({
                    "start_datetime": start_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                    "end_datetime": end_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                    "steady_state_number": steady_state_number,
                    "period": period,
                    "source": source,
                    "source_opening_balance": current_balance,
                    "source_actual_tonnes": source_actual_tonnes,
                    "source_closing_balance": source_closing_balance,
                    "source_type": source_type,
                    **{f"source_grade_{k}": v for k, v in grades.items()}
                })

                # Update for next second
                start_datetime = end_datetime
                current_balance = source_closing_balance

            # Check balance alignment with last transaction
            assert abs(current_balance - source_closing_balance) < 1e-6, \
                f"Balance mismatch for steady state {steady_state_number} and source {source}"

        # Convert transactions to DataFrame
        transactions_df = pd.DataFrame(second_transactions)

        # Insert second transactions into the database
        for _, row in transactions_df.iterrows():
            cursor.execute('''
            INSERT INTO optimised_stockpile_depletion_report (
                start_datetime,
                end_datetime,
                steady_state_number,
                period,
                source,
                source_opening_balance,
                source_actual_tonnes,
                source_closing_balance,
                source_grade_fe,
                source_grade_si,
                source_grade_al,
                source_grade_p,
                source_grade_mn,
                source_type
            ) VALUES (
                :start_datetime,
                :end_datetime,
                :steady_state_number,
                :period,
                :source,
                :source_opening_balance,
                :source_actual_tonnes,
                :source_closing_balance,
                :source_grade_fe,
                :source_grade_si,
                :source_grade_al,
                :source_grade_p,
                :source_grade_mn,
                :source_type
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Optimised stockpile depletion report saved to database {database_name}")

class StockpileProfileReport:
    @staticmethod
    def read_table_or_empty(conn, table_name, columns):
        try:
            data = pd.read_sql(f'SELECT * FROM {table_name}', conn)
        except (sqlite3.Error, pd.errors.DatabaseError) as error:
            print(f"Warning: could not read {table_name}: {error}")
            data = pd.DataFrame(columns=columns)

        for column in columns:
            if column not in data.columns:
                data[column] = pd.NA
        return data

    @staticmethod
    def weighted_average(group, value_column, weight_column):
        values = pd.to_numeric(group.get(value_column), errors="coerce")
        weights = pd.to_numeric(group.get(weight_column), errors="coerce").fillna(0)
        valid = values.notna() & (weights > 0)
        if valid.any() and weights[valid].sum() > 0:
            return float((values[valid] * weights[valid]).sum() / weights[valid].sum())
        return float(values.dropna().mean()) if values.dropna().any() else 0

    @staticmethod
    def assign_steady_state_window(data, time_column, steady_states):
        data = data.copy()
        data[time_column] = pd.to_datetime(data[time_column], errors="coerce")
        data["_steady_state_start"] = pd.NaT
        data["_steady_state_end"] = pd.NaT

        if data.empty or steady_states.empty:
            return data

        for _, state in steady_states.iterrows():
            start_time = state["start_datetime"]
            end_time = state["end_datetime"]
            mask = (data[time_column] >= start_time) & (data[time_column] < end_time)
            data.loc[mask, "steady_state_number"] = state["steady_state_number"]
            data.loc[mask, "_steady_state_start"] = start_time
            data.loc[mask, "_steady_state_end"] = end_time

        return data

    @staticmethod
    def aggregate_grade_block_movements(data, tonnes_column, grade_prefix, extra_columns=None):
        if data.empty:
            return pd.DataFrame()

        extra_columns = extra_columns or []
        data = data.copy()
        data[tonnes_column] = pd.to_numeric(data[tonnes_column], errors="coerce").fillna(0)
        data = data[
            data["source"].notna()
            & data["steady_state_number"].notna()
            & (data[tonnes_column] > 0)
        ]
        if data.empty:
            return pd.DataFrame()

        records = []
        for (source, steady_state_number), group in data.groupby(
            ["source", "steady_state_number"], dropna=False, sort=False
        ):
            record = {
                "source": source,
                "steady_state_number": steady_state_number,
                "_steady_state_start": group["_steady_state_start"].dropna().iloc[0]
                if not group["_steady_state_start"].dropna().empty
                else pd.NaT,
                "_steady_state_end": group["_steady_state_end"].dropna().iloc[0]
                if not group["_steady_state_end"].dropna().empty
                else pd.NaT,
                tonnes_column: float(group[tonnes_column].sum()),
            }
            for grade in ["fe", "si", "al", "p", "mn"]:
                record[f"grade_{grade}"] = StockpileProfileReport.weighted_average(
                    group,
                    f"{grade_prefix}{grade}",
                    tonnes_column,
                )
            for column in extra_columns:
                values = [
                    str(value)
                    for value in group[column].dropna().unique()
                    if str(value).strip()
                ]
                record[column] = ", ".join(values)
            if "delivered_datetime" in group:
                delivered = pd.to_datetime(group["delivered_datetime"], errors="coerce").dropna()
                if not delivered.empty:
                    record["first_delivery_datetime"] = delivered.min()
                    record["last_delivery_datetime"] = delivered.max()
            records.append(record)

        return pd.DataFrame(records)

    @staticmethod
    def create_grade_block_profile_rows(
        expit_payload_transactions,
        build_report,
        optimised_blend_report,
        steady_states,
        profile_start_datetime,
    ):
        profile_columns = [
            "steady_state_number", "agent", "time", "stockpile", "balance",
            "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn",
            "source_or_destination", "source_type", "arrival_tonnes",
            "tonnes_to_crusher", "tonnes_to_stockpile", "movement_tonnes",
            "movement_destination", "steady_state_start_datetime",
            "steady_state_end_datetime"
        ]
        if steady_states.empty:
            return pd.DataFrame(columns=profile_columns)

        expit_payload_transactions = expit_payload_transactions.copy()
        build_report = build_report.copy()
        optimised_blend_report = optimised_blend_report.copy()

        arrival_groups = pd.DataFrame()
        if not expit_payload_transactions.empty:
            for column in ["payload", "source_grade_fe", "source_grade_si", "source_grade_al", "source_grade_p", "source_grade_mn"]:
                if column in expit_payload_transactions:
                    expit_payload_transactions[column] = pd.to_numeric(
                        expit_payload_transactions[column], errors="coerce"
                    ).fillna(0)
            expit_payload_transactions = StockpileProfileReport.assign_steady_state_window(
                expit_payload_transactions,
                "delivered_datetime",
                steady_states,
            )
            arrival_groups = StockpileProfileReport.aggregate_grade_block_movements(
                expit_payload_transactions,
                "payload",
                "source_grade_",
            ).rename(columns={"payload": "arrival_tonnes"})

        crusher_groups = pd.DataFrame()
        if not optimised_blend_report.empty and "source_type" in optimised_blend_report:
            direct_tip_rows = optimised_blend_report[
                optimised_blend_report["source_type"].astype(str).str.lower().eq("grade_block")
            ].copy()
            if not direct_tip_rows.empty:
                direct_tip_rows["source_actual_tonnes"] = pd.to_numeric(
                    direct_tip_rows["source_actual_tonnes"], errors="coerce"
                ).fillna(0)
                direct_tip_rows["_steady_state_start"] = pd.to_datetime(
                    direct_tip_rows["start_datetime"], errors="coerce"
                )
                direct_tip_rows["_steady_state_end"] = pd.to_datetime(
                    direct_tip_rows["end_datetime"], errors="coerce"
                )
                crusher_groups = StockpileProfileReport.aggregate_grade_block_movements(
                    direct_tip_rows,
                    "source_actual_tonnes",
                    "source_grade_",
                ).rename(columns={"source_actual_tonnes": "tonnes_to_crusher"})

        stockpile_groups = pd.DataFrame()
        if not build_report.empty:
            build_report["payload"] = pd.to_numeric(build_report["payload"], errors="coerce").fillna(0)
            build_report["delivered_datetime"] = pd.to_datetime(
                build_report["delivered_datetime"], errors="coerce"
            )
            build_report["_steady_state_start"] = pd.to_datetime(
                build_report.get("steady_state_start_datetime"), errors="coerce"
            )
            build_report["_steady_state_end"] = pd.to_datetime(
                build_report.get("steady_state_end_datetime"), errors="coerce"
            )

            if not expit_payload_transactions.empty:
                grade_lookup = expit_payload_transactions[[
                    column for column in [
                        "source", "delivered_datetime", "source_grade_fe",
                        "source_grade_si", "source_grade_al", "source_grade_p",
                        "source_grade_mn"
                    ]
                    if column in expit_payload_transactions.columns
                ]].copy()
                grade_lookup["delivered_datetime"] = pd.to_datetime(
                    grade_lookup["delivered_datetime"], errors="coerce"
                )
                build_report = build_report.merge(
                    grade_lookup.drop_duplicates(subset=["source", "delivered_datetime"]),
                    how="left",
                    on=["source", "delivered_datetime"],
                    suffixes=("", "_expit"),
                )

            for grade in ["fe", "si", "al", "p", "mn"]:
                source_grade_column = f"source_grade_{grade}"
                if source_grade_column not in build_report:
                    build_report[source_grade_column] = build_report.get(f"grade_{grade}", 0)
                build_report[source_grade_column] = pd.to_numeric(
                    build_report[source_grade_column], errors="coerce"
                ).fillna(pd.to_numeric(build_report.get(f"grade_{grade}", 0), errors="coerce"))

            if build_report["steady_state_number"].isna().any():
                build_report = StockpileProfileReport.assign_steady_state_window(
                    build_report,
                    "delivered_datetime",
                    steady_states,
                )

            stockpile_groups = StockpileProfileReport.aggregate_grade_block_movements(
                build_report,
                "payload",
                "source_grade_",
                extra_columns=["stockpile"],
            ).rename(columns={"payload": "tonnes_to_stockpile"})

        movement_groups = arrival_groups
        for frame in [crusher_groups, stockpile_groups]:
            if frame.empty:
                continue
            if movement_groups.empty:
                movement_groups = frame
            else:
                movement_groups = movement_groups.merge(
                    frame,
                    how="outer",
                    on=["source", "steady_state_number"],
                    suffixes=("", "_movement"),
                )
                for column in ["_steady_state_start", "_steady_state_end"]:
                    movement_column = f"{column}_movement"
                    if movement_column in movement_groups:
                        movement_groups[column] = movement_groups[column].combine_first(
                            movement_groups[movement_column]
                        )
                        movement_groups = movement_groups.drop(columns=[movement_column])
                for grade in ["fe", "si", "al", "p", "mn"]:
                    movement_column = f"grade_{grade}_movement"
                    if movement_column in movement_groups:
                        movement_groups[f"grade_{grade}"] = movement_groups[f"grade_{grade}"].combine_first(
                            movement_groups[movement_column]
                        )
                        movement_groups = movement_groups.drop(columns=[movement_column])

        if movement_groups.empty:
            return pd.DataFrame(columns=profile_columns)

        for column in ["arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile"]:
            if column not in movement_groups:
                movement_groups[column] = 0
            movement_groups[column] = pd.to_numeric(movement_groups[column], errors="coerce").fillna(0)

        movement_groups["effective_arrival_tonnes"] = movement_groups[[
            "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile"
        ]].assign(
            total_depleted=movement_groups["tonnes_to_crusher"] + movement_groups["tonnes_to_stockpile"]
        )[["arrival_tonnes", "total_depleted"]].max(axis=1)

        records = []
        for source, group in movement_groups.sort_values(
            ["source", "_steady_state_start"]
        ).groupby("source", sort=False):
            running_balance = 0.0
            records.append({
                "steady_state_number": None,
                "agent": "EX",
                "time": profile_start_datetime,
                "stockpile": source,
                "balance": running_balance,
                "grade_fe": group["grade_fe"].dropna().iloc[0] if group["grade_fe"].dropna().any() else 0,
                "grade_si": group["grade_si"].dropna().iloc[0] if group["grade_si"].dropna().any() else 0,
                "grade_al": group["grade_al"].dropna().iloc[0] if group["grade_al"].dropna().any() else 0,
                "grade_p": group["grade_p"].dropna().iloc[0] if group["grade_p"].dropna().any() else 0,
                "grade_mn": group["grade_mn"].dropna().iloc[0] if group["grade_mn"].dropna().any() else 0,
                "source_or_destination": "Not yet delivered",
                "source_type": "grade_block",
                "arrival_tonnes": 0,
                    "tonnes_to_crusher": 0,
                    "tonnes_to_stockpile": 0,
                    "movement_tonnes": 0,
                    "movement_destination": "",
                    "steady_state_start_datetime": pd.NaT,
                    "steady_state_end_datetime": pd.NaT,
                })

            for _, row in group.sort_values("_steady_state_start").iterrows():
                start_time = row["_steady_state_start"]
                end_time = row["_steady_state_end"]
                if pd.isna(start_time) or pd.isna(end_time):
                    continue

                arrived = float(row["effective_arrival_tonnes"] or 0)
                to_crusher = float(row["tonnes_to_crusher"] or 0)
                to_stockpile = float(row["tonnes_to_stockpile"] or 0)
                total_depleted = to_crusher + to_stockpile
                opening_balance = running_balance + arrived
                closing_balance = max(opening_balance - total_depleted, 0)

                destinations = []
                if to_crusher > 0:
                    destinations.append("Crusher")
                if to_stockpile > 0:
                    stockpile_text = row.get("stockpile", "")
                    destinations.append(f"Stockpile ({stockpile_text})" if stockpile_text else "Stockpile")
                movement_destination = " + ".join(destinations)

                records.append({
                    "steady_state_number": row["steady_state_number"],
                    "agent": "EX",
                    "time": start_time,
                    "stockpile": source,
                    "balance": opening_balance,
                    "grade_fe": row.get("grade_fe", 0),
                    "grade_si": row.get("grade_si", 0),
                    "grade_al": row.get("grade_al", 0),
                    "grade_p": row.get("grade_p", 0),
                    "grade_mn": row.get("grade_mn", 0),
                    "source_or_destination": "Payloads arrived at ROM / Crusher Area",
                    "source_type": "grade_block",
                    "arrival_tonnes": arrived,
                    "tonnes_to_crusher": 0,
                    "tonnes_to_stockpile": 0,
                    "movement_tonnes": 0,
                    "movement_destination": "Arrived at ROM / Crusher Area",
                    "steady_state_start_datetime": start_time,
                    "steady_state_end_datetime": end_time,
                })
                records.append({
                    "steady_state_number": row["steady_state_number"],
                    "agent": "EX",
                    "time": start_time,
                    "stockpile": source,
                    "balance": closing_balance,
                    "grade_fe": row.get("grade_fe", 0),
                    "grade_si": row.get("grade_si", 0),
                    "grade_al": row.get("grade_al", 0),
                    "grade_p": row.get("grade_p", 0),
                    "grade_mn": row.get("grade_mn", 0),
                    "source_or_destination": movement_destination or "No depletion",
                    "source_type": "grade_block",
                    "arrival_tonnes": 0,
                    "tonnes_to_crusher": to_crusher,
                    "tonnes_to_stockpile": to_stockpile,
                    "movement_tonnes": total_depleted,
                    "movement_destination": movement_destination,
                    "steady_state_start_datetime": start_time,
                    "steady_state_end_datetime": end_time,
                })
                running_balance = closing_balance

        return pd.DataFrame(records, columns=profile_columns)

    @staticmethod
    def write_optimised_stockpile_profile_report_to_database(periods: PeriodManager):
        
        start_datetime = periods.get_periods()["preplan_start"]
        end_datetime = periods.get_periods()["period_2_end"]
        
        # Connect to the SQLite database
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)

        try:
            build_report = StockpileProfileReport.read_table_or_empty(
                conn,
                "build_report",
                [
                    "steady_state_number", "agent", "delivered_datetime", "stockpile",
                    "closing_balance", "grade_fe", "grade_si", "grade_al", "grade_p",
                    "grade_mn", "source"
                ],
            )
            optimised_stockpile_depletion_report = StockpileProfileReport.read_table_or_empty(
                conn,
                "optimised_stockpile_depletion_report",
                [
                    "start_datetime", "steady_state_number", "period", "source",
                    "source_opening_balance", "source_grade_fe", "source_grade_si",
                    "source_grade_al", "source_grade_p", "source_grade_mn",
                    "source_type"
                ],
            )
            opening_stockpile_inventories = StockpileProfileReport.read_table_or_empty(
                conn,
                "opening_stockpile_inventories",
                [
                    "name", "balance", "grade_fe", "grade_si", "grade_al",
                    "grade_p", "grade_mn"
                ],
            )
            opening_amt_stockpile_inventories = StockpileProfileReport.read_table_or_empty(
                conn,
                "opening_AMT_stockpile_inventories",
                [
                    "footprint", "balance", "grade_fe", "grade_si", "grade_al",
                    "grade_p", "grade_mn"
                ],
            )
            optimised_blend_report = StockpileProfileReport.read_table_or_empty(
                conn,
                "optimised_blend_report",
                [
                    "start_datetime", "end_datetime", "steady_state_number",
                    "source", "source_id", "source_type", "source_actual_tonnes",
                    "source_grade_fe", "source_grade_si", "source_grade_al",
                    "source_grade_p", "source_grade_mn", "crusher_actual_tonnes",
                    "actual_direct_tip_ratio"
                ],
            )
            expit_payload_transactions = StockpileProfileReport.read_table_or_empty(
                conn,
                "expit_payload_transactions",
                [
                    "agent", "source", "start_datetime", "payload",
                    "source_grade_fe", "source_grade_si", "source_grade_al",
                    "source_grade_mn", "source_grade_p", "destination",
                    "delivered_datetime", "direct_tip_id", "destination_type",
                    "planned_destination", "fallback_destination",
                    "aps_direct_tip_candidate"
                ],
            )

            steady_states = optimised_blend_report[[
                "steady_state_number", "start_datetime", "end_datetime"
            ]].dropna(subset=["steady_state_number", "start_datetime", "end_datetime"]).drop_duplicates()
            if not steady_states.empty:
                steady_states = steady_states.copy()
                steady_states["start_datetime"] = pd.to_datetime(
                    steady_states["start_datetime"], errors="coerce"
                )
                steady_states["end_datetime"] = pd.to_datetime(
                    steady_states["end_datetime"], errors="coerce"
                )
                steady_states = steady_states.dropna(
                    subset=["start_datetime", "end_datetime"]
                ).sort_values("start_datetime")

            # Prepare data from build_report (table 1)
            build_report_prepared = build_report.rename(columns={
                'delivered_datetime': 'time',
                'closing_balance': 'balance',
                'source': 'source_or_destination',
                'agent': 'agent',
            })
            build_report_prepared['source_type'] = 'stockpile'
            build_report_prepared = build_report_prepared[[
                'steady_state_number', 'agent', 'time', 'stockpile', 'balance',
                'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn',
                'source_or_destination', 'source_type'
            ]]
            build_report_prepared["source_or_destination"] = (
                "From " + build_report_prepared["source_or_destination"].astype(str)
            )

            # Prepare data from optimised_stockpile_depletion_report (table 2)
            stockpile_depletion_report = optimised_stockpile_depletion_report[
                ~optimised_stockpile_depletion_report["source_type"].astype(str).str.lower().eq("grade_block")
            ].copy()
            depletion_report_prepared = stockpile_depletion_report.rename(columns={
                'start_datetime': 'time',
                'source_opening_balance': 'balance',
                'source': 'stockpile',
                'source_grade_fe': 'grade_fe',
                'source_grade_si': 'grade_si',
                'source_grade_al': 'grade_al',
                'source_grade_p': 'grade_p',
                'source_grade_mn': 'grade_mn',
            })
            depletion_report_prepared['agent'] = "RC"
            depletion_report_prepared['source_or_destination'] = 'Crusher'
            depletion_report_prepared['source_type'] = (
                depletion_report_prepared['source_type']
                .fillna('')
                .replace('', 'stockpile')
            )
            depletion_report_prepared = depletion_report_prepared[[
                'steady_state_number', 'agent', 'time', 'stockpile', 'balance',
                'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn',
                'source_or_destination', 'source_type'
            ]]

            grade_block_profile_prepared = StockpileProfileReport.create_grade_block_profile_rows(
                expit_payload_transactions,
                build_report,
                optimised_blend_report,
                steady_states,
                start_datetime,
            )

            movement_columns = [
                "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile",
                "movement_tonnes", "movement_destination"
            ]
            for frame in [build_report_prepared, depletion_report_prepared]:
                for column in movement_columns:
                    frame[column] = 0 if column != "movement_destination" else ""

            # Combine the two reports
            combined_report = pd.concat(
                [build_report_prepared, depletion_report_prepared, grade_block_profile_prepared],
                ignore_index=True
            )

            # Backfill baseline rows only for stockpiles that are actually used as
            # crusher feed sources or build destinations. AMT openings are stored
            # at hex level, so aggregate them by footprint before matching.
            used_stockpiles = set()

            def add_names(values):
                for value in values:
                    if pd.isna(value):
                        continue
                    text = str(value).strip()
                    if text:
                        used_stockpiles.add(text)

            add_names(build_report["stockpile"].dropna().unique())

            if not optimised_stockpile_depletion_report.empty:
                source_types = (
                    optimised_stockpile_depletion_report["source_type"]
                    .fillna("")
                    .astype(str)
                    .str.lower()
                )
                add_names(
                    optimised_stockpile_depletion_report.loc[
                        ~source_types.isin({"grade_block", "grade block", "gradeblock"}),
                        "source",
                    ].dropna().unique()
                )

            if not optimised_blend_report.empty:
                source_types = (
                    optimised_blend_report["source_type"]
                    .fillna("")
                    .astype(str)
                    .str.lower()
                )
                add_names(
                    optimised_blend_report.loc[
                        ~source_types.isin({"grade_block", "grade block", "gradeblock"}),
                        "source",
                    ].dropna().unique()
                )

            weighted_opening_rows = opening_stockpile_inventories.rename(columns={
                'name': 'stockpile',
                'balance': 'balance',
                'grade_fe': 'grade_fe',
                'grade_si': 'grade_si',
                'grade_al': 'grade_al',
                'grade_p': 'grade_p',
                'grade_mn': 'grade_mn'
            }).copy()
            weighted_opening_rows["opening_source_priority"] = 1

            amt_opening_rows = pd.DataFrame(columns=[
                "stockpile", "balance", "grade_fe", "grade_si", "grade_al",
                "grade_p", "grade_mn", "opening_source_priority"
            ])
            if not opening_amt_stockpile_inventories.empty:
                amt_openings = opening_amt_stockpile_inventories.copy()
                amt_openings["balance"] = (
                    pd.to_numeric(amt_openings["balance"], errors="coerce")
                    .fillna(0)
                    .clip(lower=0)
                )
                amt_openings = amt_openings[
                    amt_openings["footprint"].notna()
                    & (amt_openings["footprint"].astype(str).str.strip() != "")
                    & (amt_openings["balance"] > 0)
                ]
                amt_records = []
                for footprint, group in amt_openings.groupby("footprint", sort=False):
                    record = {
                        "stockpile": str(footprint).strip(),
                        "balance": float(group["balance"].sum()),
                        "opening_source_priority": 0,
                    }
                    for grade in ["fe", "si", "al", "p", "mn"]:
                        record[f"grade_{grade}"] = StockpileProfileReport.weighted_average(
                            group,
                            f"grade_{grade}",
                            "balance",
                        )
                    amt_records.append(record)
                if amt_records:
                    amt_opening_rows = pd.DataFrame(amt_records)

            opening_sources = pd.concat(
                [weighted_opening_rows, amt_opening_rows],
                ignore_index=True,
                sort=False,
            )
            expected_opening_columns = [
                "stockpile", "balance", "grade_fe", "grade_si", "grade_al",
                "grade_p", "grade_mn", "opening_source_priority"
            ]
            for column in expected_opening_columns:
                if column not in opening_sources:
                    opening_sources[column] = pd.NA
            opening_sources["stockpile"] = opening_sources["stockpile"].astype(str).str.strip()
            opening_sources = opening_sources[
                opening_sources["stockpile"].isin(used_stockpiles)
            ].copy()
            opening_sources = (
                opening_sources
                .sort_values(["opening_source_priority", "stockpile"])
                .drop_duplicates(subset=["stockpile"], keep="first")
            )

            combined_times = combined_report[["stockpile", "time"]].copy()
            combined_times["stockpile"] = combined_times["stockpile"].astype(str).str.strip()
            combined_times["time"] = pd.to_datetime(combined_times["time"], errors="coerce")
            profile_start = pd.to_datetime(start_datetime, errors="coerce")
            stockpiles_with_start_profile_row = {
                str(value).strip()
                for value in combined_times.loc[
                    combined_times["time"].notna()
                    & (combined_times["time"] <= profile_start),
                    "stockpile",
                ].dropna().unique()
                if str(value).strip()
            }
            opening_stockpiles_not_in_combined = opening_sources[
                ~opening_sources['stockpile'].isin(stockpiles_with_start_profile_row)
            ].copy()
            opening_stockpiles_not_in_combined = opening_stockpiles_not_in_combined[
                [
                    "stockpile", "balance", "grade_fe", "grade_si",
                    "grade_al", "grade_p", "grade_mn"
                ]
            ]
            opening_stockpiles_not_in_combined['time'] = start_datetime
            opening_stockpiles_not_in_combined['steady_state_number'] = None
            opening_stockpiles_not_in_combined['agent'] = None
            opening_stockpiles_not_in_combined['source_or_destination'] = None
            opening_stockpiles_not_in_combined['source_type'] = 'stockpile'
            for column in movement_columns:
                opening_stockpiles_not_in_combined[column] = 0 if column != "movement_destination" else ""

            # Combine all reports
            report_frames = [combined_report]
            if not opening_stockpiles_not_in_combined.empty:
                report_frames.append(opening_stockpiles_not_in_combined)
            final_combined_report = pd.concat(report_frames, ignore_index=True)
            for column in movement_columns:
                if column not in final_combined_report:
                    final_combined_report[column] = 0 if column != "movement_destination" else ""

            # Extend the transactions to minute-level granularity
            extended_report = []
            movement_quantity_columns = [
                "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile",
                "movement_tonnes"
            ]
            movement_text_columns = ["movement_destination"]
            for stockpile, group in final_combined_report.groupby('stockpile'):
                group = group.copy()
                # Parse time column
                group['time'] = pd.to_datetime(group['time'], errors='coerce')
                group = group.dropna(subset=['time'])
                if group.empty:
                    continue

                # Create minute range
                all_times = pd.date_range(start=start_datetime, end=end_datetime, freq='min')
                extended_group = pd.DataFrame({'time': all_times})

                # Truncate seconds in both DataFrames to only honor hours and minutes
                extended_group['time'] = pd.to_datetime(extended_group['time']).dt.floor('min')
                group['time'] = pd.to_datetime(group['time']).dt.floor('min')

                # Perform the merge on the truncated time
                extended_group = extended_group.merge(group, how='left', on='time')
                movement_quantity_values = (
                    extended_group[movement_quantity_columns]
                    .apply(pd.to_numeric, errors="coerce")
                    .fillna(0)
                )
                movement_text_values = extended_group[movement_text_columns].fillna("")

                # Fill forward and backward with the first and last transaction values
                extended_group = extended_group.infer_objects(copy=False).ffill().bfill()
                extended_group[movement_quantity_columns] = movement_quantity_values
                extended_group[movement_text_columns] = movement_text_values

                # Append to the extended report list
                extended_report.append(extended_group)

            # Filter each DataFrame in the list to exclude rows where 'stockpile' is null
            extended_report = [df[df['stockpile'].notna()] for df in extended_report]

            profile_columns = [
                "time", "steady_state_number", "agent", "stockpile", "balance",
                "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn",
                "source_or_destination", "source_type", "arrival_tonnes",
                "tonnes_to_crusher", "tonnes_to_stockpile", "movement_tonnes",
                "movement_destination"
            ]
            if extended_report:
                extended_combined_report = pd.concat(extended_report, ignore_index=True)
            else:
                extended_combined_report = pd.DataFrame(columns=profile_columns)

            # Reset steady_state_number based on optimised_blend_report
            optimised_blend_report['start_datetime'] = pd.to_datetime(optimised_blend_report['start_datetime'])
            optimised_blend_report['end_datetime'] = pd.to_datetime(optimised_blend_report['end_datetime'])

            # Truncate to minutes for comparison with extended_combined_report
            optimised_blend_report['start_datetime'] = optimised_blend_report['start_datetime'].dt.floor('min')
            optimised_blend_report['end_datetime'] = optimised_blend_report['end_datetime'].dt.floor('min')

            # Convert columns to NumPy arrays for faster operations
            start_times = optimised_blend_report['start_datetime'].to_numpy()
            end_times = optimised_blend_report['end_datetime'].to_numpy()
            steady_states = optimised_blend_report['steady_state_number'].to_numpy()
            times = extended_combined_report['time'].to_numpy()

            # Create an empty array to store results
            steady_state_numbers = np.full(len(times), np.nan)

            # Vectorized interval checks
            for i, time in enumerate(times):
                mask = (start_times <= time) & (end_times > time)
                if np.any(mask):
                    steady_state_numbers[i] = steady_states[np.argmax(mask)]  # Get the first match

            # Assign results back to the DataFrame
            extended_combined_report['steady_state_number'] = steady_state_numbers
            
            def map_steady_state_number(time):
                match = optimised_blend_report[
                    (optimised_blend_report['start_datetime'] <= time) &
                    (optimised_blend_report['end_datetime'] > time)
                ]
                return match['steady_state_number'].iloc[0] if not match.empty else None

            #extended_combined_report['steady_state_number'] = extended_combined_report['time'].apply(map_steady_state_number)

            # Save the extended report to the database
            extended_combined_report.to_sql('optimised_stockpile_profile_report', conn, if_exists='replace', index=False)

            print(f"Optimised stockpile profile report saved to database {database_name}")

        finally:
            # Close the database connection
            conn.close()

