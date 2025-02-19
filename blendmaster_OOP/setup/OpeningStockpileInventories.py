import snowflake.connector
import sqlite3
from datetime import datetime

class OpeningStockpileInventories:
    def call_opening_stockpile_inventories(self, hub, area_name, start_time):
        
        # Call the function to connect (service account)
        conn = self.connect_snowflake_with_service_account()
        
        start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # SQL Query
        query = f"""
        SELECT 
        
        STOCKPILENAME AS name,
        STOCKPILEBUILDNAME AS build,
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

    def call_opening_AMT_stockpile_inventories(self, build):
        # Call the function to connect
        conn = self.connect_snowflake_with_service_account()

        # Personal account Snowflake connection (has AMT access)
        # conn = snowflake.connector.connect(
        #     user='armin.sabet@fortescue.com',
        #     account='wn74261.ap-southeast-2',
        #     warehouse='WH_EDW_SELFSERVICE',
        #     database='AA_OPERATIONS_MANAGEMENT',
        #     authenticator='externalbrowser',
        #     role='EDW_ARMIN.SABET',
        #     login_timeout=60,  # Increase login timeout
        #     network_timeout=300  # Increase network timeout
        # )

        # Dynamically generate the filters for each query
        if isinstance(build, list):  # If build is a list
            location_filter = " OR ".join([f"CONTAINS(LOCATION_NAME, '{b}')" for b in build])
            source_location_filter = " OR ".join([f"CONTAINS(SOURCELOCATIONNAME, '{b}')" for b in build])
        else:  # If build is a single string
            location_filter = f"CONTAINS(LOCATION_NAME, '{build}')"
            source_location_filter = f"CONTAINS(SOURCELOCATIONNAME, '{build}')"
        
        # SQL Query with dynamic CONTAINS filter
        query = f"""
           WITH first_query AS (
                SELECT 
                    FOOTPRINT,
                    HEX, 
                    SUM(TOTAL_WMT) AS WMT, 
                    LONGITUDE, 
                    LATITUDE, 
                    FE, 
                    SIO2, 
                    AL2O3, 
                    MN, 
                    P
                FROM
                    AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT_HEX_GRADES
                WHERE 
                    {location_filter}
                GROUP BY 
                    FOOTPRINT,
                    HEX,
                    LONGITUDE,
                    LATITUDE,
                    FE, 
                    SIO2, 
                    AL2O3, 
                    MN, 
                    P
                ORDER BY 
                    HEX
            ),
            second_query AS (
                SELECT 
                    SOURCEHEX, 
                    SUM(WMT) AS WMT
                FROM (
                    SELECT 
                        SOURCELOCATIONNAME, 
                        LOADEDDATETIME, 
                        SOURCEHEX, 
                        AVG(TONNES) AS WMT
                    FROM
                        AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT
                    WHERE 
                        {source_location_filter}
                    GROUP BY
                        SOURCELOCATIONNAME,
                        LOADEDDATETIME,
                        DUMPEDDATETIME,
                        SOURCEHEX
                )
                GROUP BY 
                    SOURCEHEX
            ),
            hex_coordinates AS (
                SELECT DISTINCT 
                    SOURCEHEX, 
                    SOURCEHEXEASTING, 
                    SOURCEHEXNORTHING
                FROM 
                    AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT
            )
            SELECT 
                fq.FOOTPRINT,
                fq.HEX,
                fq.FE,    
                fq.SIO2,
                fq.AL2O3,
                fq.MN,
                fq.P,
                CASE 
                    WHEN sq.SOURCEHEX IS NOT NULL THEN fq.WMT - sq.WMT
                    ELSE fq.WMT
                END AS FINAL_WMT,
                fq.LONGITUDE,
                fq.LATITUDE,
                hc.SOURCEHEXEASTING,
                hc.SOURCEHEXNORTHING,
                CASE 
                    WHEN sq.SOURCEHEX IS NOT NULL THEN 'True'
                    ELSE 'False'
                END AS HEX_UPDATED
            FROM 
                first_query fq
                LEFT JOIN second_query sq ON fq.HEX = sq.SOURCEHEX
                LEFT JOIN hex_coordinates hc ON fq.HEX = hc.SOURCEHEX
            ORDER BY 
                fq.HEX;
        """

        try:
            # Execute query
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchall()

            # Fetch column names
            columns = [col[0] for col in cursor.description]

            # Convert to a dictionary with FOOTPRINT as the key, storing multiple rows in a list
            data_dict = {}
            for row in result:
                key = row[0]  # FOOTPRINT as the key
                record = dict(zip(columns, row))  # Convert row to dictionary

                if key in data_dict:
                    data_dict[key].append(record)  # Append to the list if the key exists
                else:
                    data_dict[key] = [record]  # Create a new list if the key does not exist

            # Store results in SQLite database
            self.save_AMT_to_database(data_dict)

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
            build TEXT,    
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
                "build": row.get("BUILD", None),
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
            INSERT INTO opening_stockpile_inventories (name, build, balance, grade_fe, grade_si, grade_al, grade_p, grade_mn)
            VALUES (:name, :build, :balance, :grade_fe, :grade_si, :grade_al, :grade_p, :grade_mn)
            ''', mapped_row)

        # Commit and close the connection
        conn.commit()
        conn.close()
        print(f"Stockpile inventories saved to database {database_name}")
    
    def save_AMT_to_database(self, data_dict):
        # SQLite connection
        database_name = 'blendmaster.db'
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create table for stockpile inventories
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS opening_AMT_stockpile_inventories (
            footprint TEXT,
            hex TEXT,    
            balance REAL,
            grade_fe REAL,
            grade_si REAL,
            grade_al REAL,
            grade_p REAL,
            grade_mn REAL,
            lat REAL,
            long REAL,
            northing REAL,
            easting REAL,
            hex_updated TEXT
        )
        ''')

        # Clear the table
        cursor.execute('DELETE FROM opening_AMT_stockpile_inventories')

        # Insert data into the database
        for key, rows in data_dict.items():
            for row in rows:  # Each key (FOOTPRINT) may now have multiple rows
                # Map uppercase keys to expected database column names
                mapped_row = {
                    "footprint": row.get("FOOTPRINT", None),  # Adjusted for uppercase column names
                    "hex": row.get("HEX", None),
                    "balance": row.get("FINAL_WMT", 0.0),
                    "grade_fe": row.get("FE", None),
                    "grade_si": row.get("SIO2", None),  # Fixed typo "SI02" -> "SIO2"
                    "grade_al": row.get("AL2O3", None),
                    "grade_p": row.get("P", None),
                    "grade_mn": row.get("MN", None),
                    "lat": row.get("LATITUDE", None),
                    "long": row.get("LONGITUDE", None),
                    "northing": row.get("SOURCEHEXNORTHING", None),
                    "easting": row.get("SOURCEHEXEASTING", None),
                    "hex_updated": row.get("HEX_UPDATED", None)
                }

                # Skip insertion if mandatory fields (e.g., footprint) are missing
                if not mapped_row["footprint"]:
                    print(f"Skipping row with missing footprint: {mapped_row}")
                    continue

                cursor.execute('''
                INSERT INTO opening_AMT_stockpile_inventories (footprint, hex, balance, grade_fe, grade_si, grade_al, grade_p, grade_mn, lat, long, northing, easting, hex_updated)
                VALUES (:footprint, :hex, :balance, :grade_fe, :grade_si, :grade_al, :grade_p, :grade_mn, :lat, :long, :northing, :easting, :hex_updated)
                ''', mapped_row)

        # Commit and close the connection
        conn.commit()
        conn.close()
        print(f"Stockpile AMT inventories saved to database {database_name}")

    def connect_snowflake_with_service_account(self):
        
        try:
            # Connect to Snowflake using service account credentials
            conn = snowflake.connector.connect(
                user='SVC_APS',  
                password='AlastriSnowflake123',  
                account='wn74261.ap-southeast-2',  
                warehouse='WH_EDW_SELFSERVICE', 
                database='AA_OPERATIONS_MANAGEMENT',  
                schema='SELFSERVICE',  
                role='SVC_APS',  
                login_timeout=60,  
                network_timeout=300 
            )

            # Confirm the connection is open
            if conn.is_closed():
                print("Failed to connect to Snowflake.")
                return None

            print("Connection established successfully.")
            return conn

        except snowflake.connector.errors.Error as e:
            print(f"Error connecting to Snowflake: {e}")
            return None
   
    def clear_AMT_stockpile_database(self):
        # SQLite connection
        database_name = 'blendmaster.db'
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS opening_AMT_stockpile_inventories (
            footprint TEXT,
            hex TEXT,    
            balance REAL,
            grade_fe REAL,
            grade_si REAL,
            grade_al REAL,
            grade_p REAL,
            grade_mn REAL,
            lat REAL,
            long REAL,
            northing REAL,
            easting REAL,
            hex_updated TEXT
        )
        ''')

        # Clear the table
        cursor.execute('DELETE FROM opening_AMT_stockpile_inventories')
        # Commit and close the connection
        conn.commit()
        conn.close()
        