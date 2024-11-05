import snowflake.connector

def get_snowflake_data(query):
    # Establish Snowflake connection
    conn = snowflake.connector.connect(
        user = 'armin.sabet@fortescue.com',
        account = 'wn74261.ap-southeast-2',
        warehouse = 'WH_EDW_SELFSERVICE',
        database = 'AA_OPERATIONS_MANAGEMENT',
        authenticator='externalbrowser',
        role = 'EDW_ARMIN.SABET',
        login_timeout= 60, # Increase login timeout
        network_timeout= 300  # Increase network timeout

    )
    
    cursor = conn.cursor()
    cursor.execute(query)
    
    # Fetch all results
    result = cursor.fetchall()
    
    # Close the connection
    cursor.close()
    conn.close()
    
    return result
