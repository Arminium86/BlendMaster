import snowflake.connector
import sqlite3
from datetime import datetime

class OpeningStockpileInventories:
    def call_opening_stockpile_inventories(self, hub, area_name, start_time):
        # Snowflake connection
        conn = snowflake.connector.connect(
            user='armin.sabet@fortescue.com',
            account='wn74261.ap-southeast-2',
            warehouse='WH_EDW_SELFSERVICE',
            database='AA_OPERATIONS_MANAGEMENT',
            authenticator='externalbrowser',
            role='EDW_ARMIN.SABET',
            login_timeout=60,  # Increase login timeout
            network_timeout=300  # Increase network timeout
        )
        
        start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # SQL Query
        query = f"""
        SELECT 
        
        STOCKPILENAME AS name,
        BALANCEWMT AS balance, 
        FE_INSITU_WTAVG AS grade_fe, 
        SIO2_INSITU_WTAVG AS grade_si, 
        AL2O3_INSITU_WTAVG AS grade_al, 
        P_INSITU_WTAVG AS grade_p, 
        MN_INSITU_WTAVG AS grade_mn
        
        FROM (
            SELECT *, 
            ROW_NUMBER() OVER (PARTITION BY STOCKPILENAME ORDER BY TRANSACTIONDATETIME DESC) AS rn
            
            FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS
            
            WHERE TRANSACTIONDATETIME <= '{start_time}'
        ) ranked

        WHERE rn = 1 
        AND TRANSACTIONDIRECTION IN ('Stack', 'Reclaim') 
        AND (STOCKPILETYPE IN ('RomStockpile') OR CONTAINS(STOCKPILENAME, 'LT')) 
        AND HUB = '{hub}'
        AND AREANAME = '{area_name}'
        
        ORDER BY 
            HUB,
            STOCKPILENAME,
            TRANSACTIONDATETIME
        """

        try:
            # Execute query
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchall()

            # Fetch column names
            columns = [col[0] for col in cursor.description]

            # Convert to dictionary with STOCKPILENAME as the key
            data_dict = {
                row[0]: dict(zip(columns, row))  # Use STOCKPILENAME as key (row[0])
                for row in result
            }

            # Store results in SQLite database
            self.save_to_database(data_dict)

            return data_dict
        finally:
            # Close the connection
            conn.close()

    def save_to_database(self, data_dict):
        # SQLite connection
        database_name = 'blendmaster.db'
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create table for stockpile inventories
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS opening_stockpile_inventories (
            name TEXT PRIMARY KEY,
            balance REAL,
            grade_fe REAL,
            grade_si REAL,
            grade_al REAL,
            grade_p REAL,
            grade_mn REAL
        )
        ''')

        # Clear the table
        cursor.execute('DELETE FROM opening_stockpile_inventories')

        # Insert data into the database
        for key, row in data_dict.items():
            # Map uppercase keys to expected database column names
            mapped_row = {
                "name": row.get("NAME", None),  # Adjusted for uppercase column names
                "balance": row.get("BALANCE", 0.0),
                "grade_fe": row.get("GRADE_FE", None),
                "grade_si": row.get("GRADE_SI", None),
                "grade_al": row.get("GRADE_AL", None),
                "grade_p": row.get("GRADE_P", None),
                "grade_mn": row.get("GRADE_MN", None)
            }

            # Skip insertion if mandatory fields (e.g., name) are missing
            if not mapped_row["name"]:
                print(f"Skipping row with missing name: {mapped_row}")
                continue

            cursor.execute('''
            INSERT INTO opening_stockpile_inventories (name, balance, grade_fe, grade_si, grade_al, grade_p, grade_mn)
            VALUES (:name, :balance, :grade_fe, :grade_si, :grade_al, :grade_p, :grade_mn)
            ''', mapped_row)

        # Commit and close the connection
        conn.commit()
        conn.close()
        print(f"Stockpile inventories saved to database {database_name}")

