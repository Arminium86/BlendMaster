import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from classes.PeriodManager import PeriodManager

class DatabaseManager:

    def write_optimised_blend_report_to_database (self, results: pd.DataFrame, periods: PeriodManager):
        # Connect to the SQLite database or create it
        database_name = 'blendmaster.db'
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
            source TEXT,
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

        cursor.execute('DELETE FROM optimised_blend_report')

        if results.empty:
            conn.commit()
            conn.close()
            print(f"Optimised blend report saved to database {database_name}")
            return

        results['start_datetime'] = pd.to_datetime(results['start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
        results['end_datetime'] = pd.to_datetime(results['end_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # Insert each row from the DataFrame into the database
        for _, row in results.iterrows():
            cursor.execute('''
            INSERT INTO optimised_blend_report VALUES (
                :start_datetime, :end_datetime, :steady_state_number, :blend_option, :blend_ID,
                :steady_state_duration, :period, :source, :source_blend_ratio, :source_opening_balance,
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

    def write_build_report_to_database (self, results: pd.DataFrame):
        # Connect to the SQLite database or create it
        database_name = 'blendmaster.db'
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
        database_name = 'blendmaster.db'
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
            delivered_datetime TEXT
        )
        ''')

        cursor.execute('DELETE FROM expit_payload_transactions')

        results['start_datetime'] = pd.to_datetime(results['start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
        results['delivered_datetime'] = pd.to_datetime(results['delivered_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # Insert each row from the DataFrame into the database
        for _, row in results.iterrows():
            cursor.execute('''
            INSERT INTO expit_payload_transactions VALUES (
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
                :delivered_datetime
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Expit payload transactions saved to database {database_name}")

    def write_optimised_stockpile_depletion_report_to_database(self, blend_report: pd.DataFrame):
        # Connect to the SQLite database or create it
        database_name = 'blendmaster.db'
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
            source_grade_mn REAL
        )
        ''')

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
            INSERT INTO optimised_stockpile_depletion_report VALUES (
                :start_datetime, :end_datetime, :steady_state_number, :period, :source,
                :source_opening_balance, :source_actual_tonnes, :source_closing_balance,
                :source_grade_fe, :source_grade_si, :source_grade_al, :source_grade_p, :source_grade_mn
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Optimised stockpile depletion report saved to database {database_name}")

class StockpileProfileReport:
    @staticmethod
    def write_optimised_stockpile_profile_report_to_database(periods: PeriodManager):
        
        start_datetime = periods.get_periods()["preplan_start"]
        end_datetime = periods.get_periods()["period_2_end"]
        
        # Connect to the SQLite database
        database_name = 'blendmaster.db'
        conn = sqlite3.connect(database_name)

        try:
            # Load data from the two reports
            build_report = pd.read_sql('SELECT * FROM build_report', conn)
            optimised_stockpile_depletion_report = pd.read_sql('SELECT * FROM optimised_stockpile_depletion_report', conn)
            opening_stockpile_inventories = pd.read_sql('SELECT * FROM opening_stockpile_inventories', conn)
            optimised_blend_report = pd.read_sql('SELECT * FROM optimised_blend_report', conn)

            # Prepare data from build_report (table 1)
            build_report_prepared = build_report.rename(columns={
                'delivered_datetime': 'time',
                'closing_balance': 'balance',
                'source': 'source_or_destination',
                'agent': 'agent',
            })
            build_report_prepared = build_report_prepared[[
                'steady_state_number', 'agent', 'time', 'stockpile', 'balance',
                'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn', 'source_or_destination'
            ]]

            # Prepare data from optimised_stockpile_depletion_report (table 2)
            depletion_report_prepared = optimised_stockpile_depletion_report.rename(columns={
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
            depletion_report_prepared = depletion_report_prepared[[
                'steady_state_number', 'agent', 'time', 'stockpile', 'balance',
                'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn', 'source_or_destination'
            ]]

            # Combine the two reports
            combined_report = pd.concat([build_report_prepared, depletion_report_prepared], ignore_index=True)

            # Add data from opening_stockpile_inventories for stockpiles not in the combined report
            stockpiles_in_combined = combined_report['stockpile'].unique()
            opening_stockpiles_not_in_combined = opening_stockpile_inventories[
                ~opening_stockpile_inventories['name'].isin(stockpiles_in_combined)
            ].rename(columns={
                'name': 'stockpile',
                'balance': 'balance',
                'grade_fe': 'grade_fe',
                'grade_si': 'grade_si',
                'grade_al': 'grade_al',
                'grade_p': 'grade_p',
                'grade_mn': 'grade_mn'
            })
            opening_stockpiles_not_in_combined['time'] = start_datetime  # Set time to start_datetime
            opening_stockpiles_not_in_combined['steady_state_number'] = None
            opening_stockpiles_not_in_combined['agent'] = None
            opening_stockpiles_not_in_combined['source_or_destination'] = None

            # Combine all reports
            final_combined_report = pd.concat([combined_report, opening_stockpiles_not_in_combined], ignore_index=True)

            # Extend the transactions to minute-level granularity
            extended_report = []
            for stockpile, group in final_combined_report.groupby('stockpile'):
                # Parse time column
                group['time'] = pd.to_datetime(group['time'], errors='coerce')

                # Create minute range
                all_times = pd.date_range(start=start_datetime, end=end_datetime, freq='min')
                extended_group = pd.DataFrame({'time': all_times})

                # Truncate seconds in both DataFrames to only honor hours and minutes
                extended_group['time'] = pd.to_datetime(extended_group['time']).dt.floor('min')
                group['time'] = pd.to_datetime(group['time']).dt.floor('min')

                # Perform the merge on the truncated time
                extended_group = extended_group.merge(group, how='left', on='time')

                # Fill forward and backward with the first and last transaction values
                extended_group = extended_group.infer_objects(copy=False).ffill().bfill()

                # Append to the extended report list
                extended_report.append(extended_group)

            # Filter each DataFrame in the list to exclude rows where 'stockpile' is null
            extended_report = [df[df['stockpile'].notna()] for df in extended_report]

            # Concatenate the list of DataFrames into a single DataFrame
            extended_combined_report = pd.concat(extended_report, ignore_index=True)

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

