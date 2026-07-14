import snowflake.connector
import sqlite3
from datetime import datetime
import os
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from database.DatabaseContext import get_database_path

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

    def call_opening_AMT_stockpile_inventories(self, build, start_time=None):
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

        restore_movement_filter = "1 = 0"
        if start_time is not None:
            if hasattr(start_time, "toPyDateTime"):
                start_time = start_time.toPyDateTime()
            if isinstance(start_time, datetime):
                start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
            restore_movement_filter = (
                f"TO_TIMESTAMP_NTZ(fq.LAST_UPDATE) > TO_TIMESTAMP_NTZ('{start_time}')\n"
                f"                    AND TO_TIMESTAMP_NTZ(mr.LOADEDDATETIME) >= TO_TIMESTAMP_NTZ('{start_time}')\n"
                f"                    AND TO_TIMESTAMP_NTZ(mr.LOADEDDATETIME) < TO_TIMESTAMP_NTZ(fq.LAST_UPDATE)"
            )
        
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
                    P,
                    MAX(LAST_UPDATE) AS LAST_UPDATE
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
            movement_rows AS (
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
            ),
            second_query AS (
                SELECT
                    fq.HEX AS SOURCEHEX,
                    SUM(mr.WMT) AS WMT
                FROM
                    first_query fq
                    JOIN movement_rows mr ON fq.HEX = mr.SOURCEHEX
                WHERE
                    {restore_movement_filter}
                GROUP BY
                    fq.HEX
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
                    WHEN sq.SOURCEHEX IS NOT NULL THEN fq.WMT + sq.WMT
                    ELSE fq.WMT
                END AS FINAL_WMT,
                fq.LONGITUDE,
                fq.LATITUDE,
                fq.LAST_UPDATE,
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
        database_name = get_database_path()
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
        database_name = get_database_path()
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
            last_update TEXT,
            hex_updated TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(opening_AMT_stockpile_inventories)")
        existing_columns = {column[1] for column in cursor.fetchall()}
        if "last_update" not in existing_columns:
            cursor.execute("ALTER TABLE opening_AMT_stockpile_inventories ADD COLUMN last_update TEXT")

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
                    "last_update": row.get("LAST_UPDATE", None),
                    "hex_updated": row.get("HEX_UPDATED", None)
                }

                # Skip insertion if mandatory fields (e.g., footprint) are missing
                if not mapped_row["footprint"]:
                    print(f"Skipping row with missing footprint: {mapped_row}")
                    continue

                cursor.execute('''
                INSERT INTO opening_AMT_stockpile_inventories (footprint, hex, balance, grade_fe, grade_si, grade_al, grade_p, grade_mn, lat, long, northing, easting, last_update, hex_updated)
                VALUES (:footprint, :hex, :balance, :grade_fe, :grade_si, :grade_al, :grade_p, :grade_mn, :lat, :long, :northing, :easting, :last_update, :hex_updated)
                ''', mapped_row)

        # Commit and close the connection
        conn.commit()
        conn.close()
        print(f"Stockpile AMT inventories saved to database {database_name}")

    def connect_snowflake_with_service_account(
        self,
        key_path=r"C:\APSConnectionKey\SVC_APS_Private_Key.p8",
        key_passphrase: str | None = None,
        user: str = "SVC_APS",
        warehouse: str = "WH_EDW_SELFSERVICE",
        database: str = "AA_OPERATIONS_MANAGEMENT",
        schema: str = "SELFSERVICE",
        role: str = "SVC_APS",
    ):
        """
        Maintains the original name but switches to Snowflake key-pair (JWT) authentication.
        Returns an open connection or None if all attempts fail (with detailed errors printed).
        """

        # 1) Validate key file upfront to avoid returning None later without a clear reason
        if not os.path.exists(key_path):
            print(f"[Snowflake] Private key file not found: {key_path}")
            return None

        try:
            with open(key_path, "rb") as f:
                key_bytes = f.read()

            # Load PEM or DER PKCS#8 key
            if key_bytes.strip().startswith(b"-----BEGIN"):
                private_key_obj = serialization.load_pem_private_key(
                    key_bytes,
                    password=None if key_passphrase is None else key_passphrase.encode("utf-8"),
                    backend=default_backend(),
                )
            else:
                private_key_obj = serialization.load_der_private_key(
                    key_bytes,
                    password=None if key_passphrase is None else key_passphrase.encode("utf-8"),
                    backend=default_backend(),
                )

            # Snowflake expects PKCS#8 DER bytes
            private_key_der = private_key_obj.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        except Exception as e:
            print(f"[Snowflake] Failed to load private key: {e}")
            return None

        # 2) Try both common account identifier formats (matches your old code and your ODBC DSN)
        account_candidates = [
            "FMG-WN74261",            # from your ODBC snippet
            "wn74261.ap-southeast-2"  # from your original password-based code
        ]

        errors = []
        for account in account_candidates:
            try:
                conn = snowflake.connector.connect(
                    user=user,
                    account=account,
                    authenticator="SNOWFLAKE_JWT",
                    private_key=private_key_der,
                    warehouse=warehouse,
                    database=database,
                    schema=schema,
                    role=role,
                    login_timeout=60,
                    network_timeout=300,
                )

                # Run a lightweight sanity check so we only return a truly usable connection
                cur = conn.cursor()
                try:
                    cur.execute("select current_user(), current_role(), current_account(), current_region()")
                    _ = cur.fetchone()
                finally:
                    cur.close()

                if conn.is_closed():
                    raise RuntimeError("Connection reported closed right after opening.")

                print(f"[Snowflake] Connected with key-pair auth (account='{account}').")
                return conn

            except Exception as e:
                errors.append(f"account='{account}': {e!s}")

        # If we got here, all attempts failed — print all reasons so you can fix quickly
        print("[Snowflake] All key-pair connection attempts failed:")
        for msg in errors:
            print("  - " + msg)
        return None
   
    def clear_AMT_stockpile_database(self):
        # SQLite connection
        database_name = get_database_path()
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
            last_update TEXT,
            hex_updated TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(opening_AMT_stockpile_inventories)")
        existing_columns = {column[1] for column in cursor.fetchall()}
        if "last_update" not in existing_columns:
            cursor.execute("ALTER TABLE opening_AMT_stockpile_inventories ADD COLUMN last_update TEXT")

        # Clear the table
        cursor.execute('DELETE FROM opening_AMT_stockpile_inventories')
        # Commit and close the connection
        conn.commit()
        conn.close()
        
