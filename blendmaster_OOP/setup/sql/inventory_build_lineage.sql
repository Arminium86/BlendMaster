-- Exact build names retain turnover identity. Never join by footprint or a
-- date-only build span. Read the entire selected build's inbound history.
WITH params AS (
    SELECT TO_TIMESTAMP_TZ(%s) AS AS_OF_TS
),
requested AS (
    SELECT DISTINCT UPPER(TRIM(value::VARCHAR)) AS BUILD
    FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s)))
),
inventory AS (
    SELECT requested.BUILD, inventory.STOCKPILENAME AS FOOTPRINT,
        inventory.BALANCEWMT AS INVENTORY_WMT,
        CONVERT_TIMEZONE('Australia/Perth', inventory.TRANSACTIONDATETIME)::TIMESTAMP_NTZ
            AS INVENTORY_DATETIME
    FROM requested
    CROSS JOIN params
    JOIN AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS inventory
        ON UPPER(TRIM(inventory.STOCKPILEBUILDNAME)) = requested.BUILD
    WHERE inventory.TRANSACTIONDATETIME <= params.AS_OF_TS
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY requested.BUILD ORDER BY inventory.TRANSACTIONDATETIME DESC
    ) = 1
),
inbound AS (
    SELECT requested.BUILD, movement.SOURCE AS GRADE_BLOCK_NAME,
        movement.SHIFT_DATE, movement.SHIFT,
        MAX(CONVERT_TIMEZONE('Australia/Perth', movement.TRANSACTION_DATETIME)::TIMESTAMP_NTZ)
            AS AVAILABLE_AT,
        SUM(movement.WMT_REPORTING) AS INBOUND_WMT
    FROM requested
    CROSS JOIN params
    JOIN AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS movement
        ON UPPER(TRIM(movement.DESTINATION)) = requested.BUILD
    WHERE movement.IS_DELETED = FALSE
      AND movement.DISCRIMINATOR = 'PrimaryMovement'
      AND movement.TRANSACTION_DATETIME <= params.AS_OF_TS
    GROUP BY 1, 2, 3, 4
)
SELECT requested.BUILD, inventory.FOOTPRINT, inventory.INVENTORY_WMT,
    inventory.INVENTORY_DATETIME, inbound.GRADE_BLOCK_NAME,
    inbound.AVAILABLE_AT, inbound.INBOUND_WMT
FROM requested
LEFT JOIN inventory USING (BUILD)
LEFT JOIN inbound USING (BUILD)
ORDER BY requested.BUILD, inbound.AVAILABLE_AT, inbound.GRADE_BLOCK_NAME
