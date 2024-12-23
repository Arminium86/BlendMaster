import sqlite3
import pandas as pd

class DatabaseManager:

    def write_optimised_blend_report_to_database (self, results: pd.DataFrame):
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

        print(f"Build report saved to database {database_name}")

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