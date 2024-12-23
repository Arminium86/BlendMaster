import snowflake.connector

class OpeningStockpileInventories:

    # Function to fetch query result and return dictionary
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

            return data_dict
        finally:
            # Close the connection
            conn.close()

