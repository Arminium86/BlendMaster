import snowflake.connector
import sqlite3
import json
from datetime import datetime
from decimal import Decimal
import os
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from database.DatabaseContext import get_database_path
from classes.GradeStreams import ANALYTES, flatten_grade_streams
from setup.AMTSpatialReconciliation import reconcile_amt_hex_rows
from setup.AMTGradeBlockLineage import (
    align_amt_grade_block_lineage,
    direct_product_tonnage_object_sql,
    direct_product_tonnage_select_sql,
    direct_product_tonnage_sum_sql,
    expit_select_sql,
    property_object_sql,
    weighted_property_sql,
)


_EXTENDED_CHEMISTRY = (
    "FE", "SIO2", "AL2O3", "P", "MN", "MGO", "K2O", "TIO2", "NA2O",
    "S", "CAO", "LOI_371", "LOI_650", "LOI_1000", "LOI_TOTAL", "AS",
    "CL", "CU", "ZN", "PB", "BAO", "TOTALOXIDES", "MOISTURE",
)
_CORE_ANALYTES = {"FE", "SIO2", "AL2O3", "P", "MN"}
_INVENTORY_EXTRA_SELECTS = [
    ("stockpileid", "LT.STOCKPILEID"),
    ("areaname", "LT.AREANAME"),
    ("hub", "LT.HUB"),
    ("stockpilesubcategory", "LT.STOCKPILESUBCATEGORY"),
    ("stockpiletype", "LT.STOCKPILETYPE"),
    ("transactiondirection", "SR.TRANSACTIONDIRECTION"),
    ("material", "LOWER(LT.MATERIAL)"),
    ("product", "LT.PRODUCT"),
    ("isfeedable", "LT.ISFEEDABLE"),
    ("isinbuildpurity", "LT.ISINBUILDPURITY"),
]
for _stream, _properties in (
    ("INSITU", (*_EXTENDED_CHEMISTRY, "LOI_425", "WETYIELD", "DRYYIELD")),
    ("ROM", (*_EXTENDED_CHEMISTRY, "WETYIELD", "DRYYIELD", "WHIMS_MINUS1", "WHIMS_PLUS1")),
    ("PROD1", (*_EXTENDED_CHEMISTRY, "WETYIELD", "DRYYIELD")),
    ("PROD2", (*_EXTENDED_CHEMISTRY, "WETYIELD", "DRYYIELD")),
    ("OPF", (*_EXTENDED_CHEMISTRY, "WETYIELD", "DRYYIELD")),
    ("PROD3", (*_EXTENDED_CHEMISTRY, "WETYIELD", "DRYYIELD")),
    ("TRAIN", _EXTENDED_CHEMISTRY),
):
    for _property in _properties:
        if _stream in {"INSITU", "ROM", "PROD1", "PROD2", "PROD3"} and _property in _CORE_ANALYTES:
            continue
        _source = f"{_property}_{_stream}_WTAVG"
        _INVENTORY_EXTRA_SELECTS.append((_source.lower(), f"LT.{_source}"))

for _ore_type in ("BID", "CIDL", "CIDM", "CIDU", "DID", "HC", "OTHER"):
    _INVENTORY_EXTRA_SELECTS.extend([
        (
            f"oretype_{_ore_type.lower()}_wmt",
            f"LT.ORETYPE_{_ore_type}_INSITU_WTAVG * LT.BALANCEWMT",
        ),
        (
            f"oretype_{_ore_type.lower()}_dmt",
            f"LT.ORETYPE_{_ore_type}_INSITU_WTAVG * "
            "(LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG))",
        ),
        (
            f"oretype_{_ore_type.lower()}_insitu_wtavg_pc",
            f"LT.ORETYPE_{_ore_type}_INSITU_WTAVG",
        ),
    ])

_INVENTORY_EXTRA_SELECTS.extend([
    ("feed_wmt", "LT.BALANCEWMT"),
    ("feed_dmt", "LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG)"),
    *[
        (
            f"prod{product}_wmt",
            f"LT.WETYIELD_PROD{product}_WTAVG * LT.BALANCEWMT",
        )
        for product in (1, 2, 3)
    ],
    *[
        (
            f"prod{product}_dmt",
            f"LT.DRYYIELD_PROD{product}_WTAVG * "
            "(LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG))",
        )
        for product in (1, 2, 3)
    ],
    ("minus_1mm_pct", "M.MINUS_1MM_PCT"),
    ("prod1_minus_1mm_pct", "M.MINUS_1MM_PCT"),
    (
        "prod1_minus_1mm_wmt",
        "LT.WETYIELD_PROD1_WTAVG * LT.BALANCEWMT * "
        "CASE WHEN ABS(M.MINUS_1MM_PCT) > 1 "
        "THEN M.MINUS_1MM_PCT / 100 ELSE M.MINUS_1MM_PCT END",
    ),
    (
        "prod1_minus_1mm_dmt",
        "LT.DRYYIELD_PROD1_WTAVG * "
        "(LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG)) * "
        "CASE WHEN ABS(M.MINUS_1MM_PCT) > 1 "
        "THEN M.MINUS_1MM_PCT / 100 ELSE M.MINUS_1MM_PCT END",
    ),
    ("lump_yield_pct", "M.LUMP_YIELD_PCT"),
    ("fines_yield_pct", "M.FINES_YIELD_PCT"),
    ("prod1_lump_yield_pct", "M.LUMP_YIELD_PCT"),
    ("prod1_fines_yield_pct", "M.FINES_YIELD_PCT"),
    ("lump_wmt", "LT.BALANCEWMT * M.LUMP_YIELD_PCT"),
    ("fines_wmt", "LT.BALANCEWMT * M.FINES_YIELD_PCT"),
    ("prod1_lump_wmt", "LT.BALANCEWMT * M.LUMP_YIELD_PCT"),
    ("prod1_fines_wmt", "LT.BALANCEWMT * M.FINES_YIELD_PCT"),
    (
        "balancedmt",
        "LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG)",
    ),
    (
        "lump_dmt",
        "LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG) * M.LUMP_YIELD_PCT",
    ),
    (
        "fines_dmt",
        "LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG) * M.FINES_YIELD_PCT",
    ),
    (
        "prod1_lump_dmt",
        "LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG) * M.LUMP_YIELD_PCT",
    ),
    (
        "prod1_fines_dmt",
        "LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG) * M.FINES_YIELD_PCT",
    ),
    ("fines_moisture", "M.FINES_MOISTURE"),
    ("lump_moisture", "M.LUMP_MOISTURE"),
    ("prod1_fines_moisture", "M.FINES_MOISTURE"),
    ("prod1_lump_moisture", "M.LUMP_MOISTURE"),
    ("gb_dry_density", "M.GB_DRY_DENSITY"),
    (
        "lump_volume",
        "(LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG) * "
        "M.LUMP_YIELD_PCT) / NULLIF(M.GB_DRY_DENSITY, 0)",
    ),
    (
        "fines_volume",
        "(LT.BALANCEWMT * (1 - LT.MOISTURE_INSITU_WTAVG) * "
        "M.FINES_YIELD_PCT) / NULLIF(M.GB_DRY_DENSITY, 0)",
    ),
    ("loi_425", "M.LOI_425"),
])
for _size in ("FINES", "LUMP"):
    for _property in (
        "FE", "SIO2", "AL2O3", "MN", "P", "LOI_425", "LOI_TOTAL", "S", "AS",
    ):
        _alias = f"{_size}_{_property}".lower()
        _INVENTORY_EXTRA_SELECTS.append((_alias, f"M.{_size}_{_property}"))
        _INVENTORY_EXTRA_SELECTS.append((
            f"prod1_{_alias}", f"M.{_size}_{_property}"
        ))

INVENTORY_ADDITIONAL_FIELDS = tuple(
    [
        "cbmaterial",
        *[alias for alias, _expression in _INVENTORY_EXTRA_SELECTS],
        "cb_lump_fraction_input",
        "cb_split_method",
        "cb_split_warning",
    ]
)
INVENTORY_TEXT_FIELDS = {
    "stockpileid", "areaname", "hub", "stockpilesubcategory",
    "stockpiletype", "transactiondirection", "material", "cbmaterial",
    "product",
    "cb_split_method", "cb_split_warning",
}
INVENTORY_INTEGER_FIELDS = {"isfeedable", "isinbuildpurity"}


def _sqlite_scalar(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list, tuple, set)):
        serializable = sorted(value, key=str) if isinstance(value, set) else value
        return json.dumps(serializable, default=str)
    return value


def inventory_additional_values(row):
    row = row or {}
    values = {}
    for field in INVENTORY_ADDITIONAL_FIELDS:
        value = row.get(field.upper())
        if value is None:
            value = row.get(field)
        if value is not None:
            values[field] = _sqlite_scalar(value)
    return values


_CB_MATERIAL_CASE = """
CASE
    WHEN LT.FE_INSITU_WTAVG <= 0 THEN 'ws'
    WHEN LT.FE_INSITU_WTAVG >= 58.0 AND LT.FE_INSITU_WTAVG < 100.0
         AND LT.AL2O3_INSITU_WTAVG >= 4.5 AND LT.AL2O3_INSITU_WTAVG < 8.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 20.5 THEN 'so'
    WHEN LT.FE_INSITU_WTAVG >= 58.0 AND LT.FE_INSITU_WTAVG < 100.0
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 4.5
         AND LT.SIO2_INSITU_WTAVG >= 8.0 AND LT.SIO2_INSITU_WTAVG < 20.5 THEN 'so'
    WHEN LT.FE_INSITU_WTAVG >= 58.0 AND LT.FE_INSITU_WTAVG < 100.0
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 4.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 8.0 THEN 'hg'
    WHEN LT.FE_INSITU_WTAVG >= 55.5 AND LT.FE_INSITU_WTAVG < 58.0
         AND LT.AL2O3_INSITU_WTAVG >= 4.0 AND LT.AL2O3_INSITU_WTAVG < 8.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 6.5 THEN 'bs'
    WHEN LT.FE_INSITU_WTAVG >= 55.5 AND LT.FE_INSITU_WTAVG < 58.0
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 4.0
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 7.0 THEN 'so'
    WHEN LT.FE_INSITU_WTAVG >= 55.5 AND LT.FE_INSITU_WTAVG < 58.0
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 4.0
         AND LT.SIO2_INSITU_WTAVG >= 7.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'ba'
    WHEN LT.FE_INSITU_WTAVG >= 55.5 AND LT.FE_INSITU_WTAVG < 58.0
         AND LT.AL2O3_INSITU_WTAVG >= 4.0 AND LT.AL2O3_INSITU_WTAVG < 8.5
         AND LT.SIO2_INSITU_WTAVG >= 6.5 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 3.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 12.0 THEN 'ba'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 4.0 AND LT.AL2O3_INSITU_WTAVG < 7.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 7.0 THEN 'bs'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 2.0
         AND LT.SIO2_INSITU_WTAVG >= 12.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 2.0 AND LT.AL2O3_INSITU_WTAVG < 3.0
         AND LT.SIO2_INSITU_WTAVG >= 12.0 AND LT.SIO2_INSITU_WTAVG < 16.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 3.0 AND LT.AL2O3_INSITU_WTAVG < 3.5
         AND LT.SIO2_INSITU_WTAVG >= 12.0 AND LT.SIO2_INSITU_WTAVG < 15.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 3.5 AND LT.AL2O3_INSITU_WTAVG < 4.0
         AND LT.SIO2_INSITU_WTAVG >= 7.0 AND LT.SIO2_INSITU_WTAVG < 13.5 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 2.0
         AND LT.SIO2_INSITU_WTAVG >= 17.0 AND LT.SIO2_INSITU_WTAVG < 20.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 2.0 AND LT.AL2O3_INSITU_WTAVG < 3.0
         AND LT.SIO2_INSITU_WTAVG >= 16.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 3.0 AND LT.AL2O3_INSITU_WTAVG < 3.5
         AND LT.SIO2_INSITU_WTAVG >= 15.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 3.5 AND LT.AL2O3_INSITU_WTAVG < 4.0
         AND LT.SIO2_INSITU_WTAVG >= 13.5 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 7.5 AND LT.AL2O3_INSITU_WTAVG < 8.0
         AND LT.SIO2_INSITU_WTAVG >= 8.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 7.0 AND LT.AL2O3_INSITU_WTAVG < 7.5
         AND LT.SIO2_INSITU_WTAVG >= 11.5 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 8.0 AND LT.AL2O3_INSITU_WTAVG < 8.5
         AND LT.SIO2_INSITU_WTAVG >= 7.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 7.5 AND LT.AL2O3_INSITU_WTAVG < 8.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 6.5 THEN 'bs'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 8.0 AND LT.AL2O3_INSITU_WTAVG < 8.5
         AND LT.SIO2_INSITU_WTAVG >= 6.5 AND LT.SIO2_INSITU_WTAVG < 7.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 7.5 AND LT.AL2O3_INSITU_WTAVG < 8.0
         AND LT.SIO2_INSITU_WTAVG >= 6.5 AND LT.SIO2_INSITU_WTAVG < 8.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 4.0 AND LT.AL2O3_INSITU_WTAVG < 5.5
         AND LT.SIO2_INSITU_WTAVG >= 13.0 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 6.0 AND LT.AL2O3_INSITU_WTAVG < 7.0
         AND LT.SIO2_INSITU_WTAVG >= 11.5 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 4.0 AND LT.AL2O3_INSITU_WTAVG < 5.5
         AND LT.SIO2_INSITU_WTAVG >= 7.0 AND LT.SIO2_INSITU_WTAVG < 13.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 5.5 AND LT.AL2O3_INSITU_WTAVG < 6.0
         AND LT.SIO2_INSITU_WTAVG >= 11.5 AND LT.SIO2_INSITU_WTAVG < 17.0 THEN 'sg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 5.5 AND LT.AL2O3_INSITU_WTAVG < 7.5
         AND LT.SIO2_INSITU_WTAVG >= 7.0 AND LT.SIO2_INSITU_WTAVG < 11.5 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 53.0 AND LT.FE_INSITU_WTAVG < 55.5
         AND LT.AL2O3_INSITU_WTAVG >= 3.5 AND LT.AL2O3_INSITU_WTAVG < 4.0
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 7.0 THEN 'ba'
    WHEN LT.FE_INSITU_WTAVG >= 51.0 AND LT.FE_INSITU_WTAVG < 53.0
         AND LT.AL2O3_INSITU_WTAVG >= 0.0 AND LT.AL2O3_INSITU_WTAVG < 3.5
         AND LT.SIO2_INSITU_WTAVG >= 0.0 AND LT.SIO2_INSITU_WTAVG < 10.0 THEN 'ba'
    WHEN LT.FE_INSITU_WTAVG >= 51.0 AND LT.FE_INSITU_WTAVG < 53.0
         AND LT.AL2O3_INSITU_WTAVG >= 4.5 AND LT.AL2O3_INSITU_WTAVG < 5.0
         AND LT.SIO2_INSITU_WTAVG >= 10.0 AND LT.SIO2_INSITU_WTAVG < 13.5 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 51.0 AND LT.FE_INSITU_WTAVG < 53.0
         AND LT.AL2O3_INSITU_WTAVG >= 5.0 AND LT.AL2O3_INSITU_WTAVG < 5.5
         AND LT.SIO2_INSITU_WTAVG >= 10.0 AND LT.SIO2_INSITU_WTAVG < 13.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 51.0 AND LT.FE_INSITU_WTAVG < 53.0
         AND LT.AL2O3_INSITU_WTAVG >= 5.5 AND LT.AL2O3_INSITU_WTAVG < 6.0
         AND LT.SIO2_INSITU_WTAVG >= 10.0 AND LT.SIO2_INSITU_WTAVG < 12.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 51.0 AND LT.FE_INSITU_WTAVG < 53.0
         AND LT.AL2O3_INSITU_WTAVG >= 6.0 AND LT.AL2O3_INSITU_WTAVG < 7.5
         AND LT.SIO2_INSITU_WTAVG >= 10.0 AND LT.SIO2_INSITU_WTAVG < 11.0 THEN 'lg'
    WHEN LT.FE_INSITU_WTAVG >= 51.0 AND LT.FE_INSITU_WTAVG < 53.0
         AND LT.AL2O3_INSITU_WTAVG >= 5.0 AND LT.AL2O3_INSITU_WTAVG < 5.5
         AND LT.SIO2_INSITU_WTAVG >= 13.0 AND LT.SIO2_INSITU_WTAVG < 15.0 THEN 'sg'
    ELSE 'ws'
END
"""


def opening_inventory_query():
    """Build the scenario-time opening inventory query with APS properties."""
    additional_select = ",\n            ".join(
        f"{expression} AS {alias}"
        for alias, expression in _INVENTORY_EXTRA_SELECTS
    )
    return f"""
        WITH INVENTORY_FILTERED AS (
            SELECT *
            FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS
            WHERE TRANSACTIONDATETIME <= %s
              AND (STOCKPILETYPE IN ('RomStockpile') OR CONTAINS(STOCKPILENAME, 'LT'))
              AND HUB = %s
              AND AREANAME = %s
        ),
        ADJUSTMENT_TRANSACTIONS_REMOVED AS (
            SELECT
                STOCKPILEID,
                STOCKPILEBUILDNAME,
                MAX(TRANSACTIONDATETIME) AS TRANSACTIONDATETIME
            FROM INVENTORY_FILTERED
            WHERE TRANSACTIONDIRECTION != 'Adjustment'
            GROUP BY STOCKPILEID, STOCKPILEBUILDNAME
        ),
        STACK_OR_RECLAIM AS (
            SELECT
                inventory.STOCKPILEID,
                inventory.STOCKPILEBUILDNAME,
                inventory.TRANSACTIONDATETIME AS STACK_OR_RECLAIM_DATETIME,
                inventory.TRANSACTIONDIRECTION
            FROM ADJUSTMENT_TRANSACTIONS_REMOVED latest
            INNER JOIN INVENTORY_FILTERED inventory
                ON inventory.STOCKPILEID = latest.STOCKPILEID
               AND inventory.STOCKPILEBUILDNAME = latest.STOCKPILEBUILDNAME
               AND inventory.TRANSACTIONDATETIME = latest.TRANSACTIONDATETIME
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY inventory.STOCKPILEID, inventory.STOCKPILEBUILDNAME
                ORDER BY inventory.TRANSACTIONDATETIME DESC
            ) = 1
        ),
        LATEST_TRANSACTION_KEY AS (
            SELECT
                STOCKPILEID,
                STOCKPILEBUILDNAME,
                MAX(TRANSACTIONDATETIME) AS TRANSACTIONDATETIME
            FROM INVENTORY_FILTERED
            GROUP BY STOCKPILEID, STOCKPILEBUILDNAME
        ),
        LATEST_TRANSACTIONS AS (
            SELECT inventory.*
            FROM LATEST_TRANSACTION_KEY latest
            INNER JOIN INVENTORY_FILTERED inventory
                ON inventory.STOCKPILEID = latest.STOCKPILEID
               AND inventory.STOCKPILEBUILDNAME = latest.STOCKPILEBUILDNAME
               AND inventory.TRANSACTIONDATETIME = latest.TRANSACTIONDATETIME
        ),
        PS_BASE AS (
            SELECT
                STOCKPILEID,
                STOCKPILENAME,
                STOCKPILEBUILDNAME,
                TRANSACTIONDATETIME
            FROM INVENTORY_FILTERED
            WHERE TRANSACTIONDIRECTION != 'Adjustment'
        ),
        BUILD_MAX AS (
            SELECT
                STOCKPILEID,
                STOCKPILENAME,
                STOCKPILEBUILDNAME,
                MAX(TRANSACTIONDATETIME) AS BUILD_MAX_DT
            FROM PS_BASE
            GROUP BY STOCKPILEID, STOCKPILENAME, STOCKPILEBUILDNAME
        ),
        LATEST_BUILD AS (
            SELECT
                STOCKPILEID,
                STOCKPILENAME,
                STOCKPILEBUILDNAME,
                BUILD_MAX_DT
            FROM BUILD_MAX
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY STOCKPILEID
                ORDER BY BUILD_MAX_DT DESC, STOCKPILEBUILDNAME
            ) = 1
        ),
        BUILD_SPAN AS (
            SELECT
                base.STOCKPILEID,
                base.STOCKPILENAME,
                base.STOCKPILEBUILDNAME,
                MIN(base.TRANSACTIONDATETIME) AS MIN_DT,
                MAX(base.TRANSACTIONDATETIME) AS MAX_DT
            FROM PS_BASE base
            INNER JOIN LATEST_BUILD latest
                ON base.STOCKPILEID = latest.STOCKPILEID
               AND base.STOCKPILEBUILDNAME = latest.STOCKPILEBUILDNAME
            GROUP BY base.STOCKPILEID, base.STOCKPILENAME, base.STOCKPILEBUILDNAME
        ),
        EXPIT AS (
            SELECT
                SHIFT_DATE::DATE AS SHIFT_DATE,
                SOURCE AS SOURCE_GRADEBLOCK_NAME,
                DESTINATION_FMS AS STOCKPILE,
                WMT_REPORTING AS WMT,
                WMT_REPORTING * (1 - MOISTURE) AS DMT
            FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS
            WHERE DESTINATION_STOCKPILE_SUBCATEGORY NOT IN ('null')
        ),
        PS AS (
            SELECT
                expit.SOURCE_GRADEBLOCK_NAME,
                span.STOCKPILEBUILDNAME AS STOCKPILE_BUILD_NAME,
                SUM(expit.WMT) AS WMT,
                SUM(expit.DMT) AS DMT
            FROM EXPIT expit
            INNER JOIN BUILD_SPAN span
                ON UPPER(TRIM(expit.STOCKPILE)) = UPPER(TRIM(span.STOCKPILENAME))
               AND expit.SHIFT_DATE BETWEEN CAST(span.MIN_DT AS DATE) AND CAST(span.MAX_DT AS DATE)
            GROUP BY expit.SOURCE_GRADEBLOCK_NAME, span.STOCKPILEBUILDNAME
        ),
        GB AS (
            SELECT
                CONCAT(
                    MINE_CODE, '_', LOCATION_NO, '_', PHASE, '_', BLAST_RL, '_',
                    BLAST_NO, '_',
                    CASE
                        WHEN TRY_TO_NUMBER(BLAST_NO) BETWEEN 600 AND 699
                         AND TRY_TO_NUMBER(FLITCH_RL) IS NOT NULL
                            THEN TO_VARCHAR(TRY_TO_NUMBER(FLITCH_RL) + 1)
                        ELSE FLITCH_RL
                    END,
                    '_', GB_NAME
                ) AS FULL_NAME_WITH_SITE,
                TRY_TO_DOUBLE(PROD1_MINUS1MM_PCT) AS PROD1_MINUS1MM_PCT,
                PROD1_FINES_YIELD_PCT,
                PROD1_LUMP_YIELD_PCT,
                PROD1_FINES_MOISTURE,
                PROD1_LUMP_MOISTURE,
                PROD1_FINES_FE,
                PROD1_FINES_SIO2,
                PROD1_FINES_AL2O3,
                PROD1_FINES_MN,
                PROD1_FINES_P,
                PROD1_FINES_LOI_425,
                PROD1_FINES_LOI_TOTAL,
                PROD1_FINES_S,
                PROD1_FINES_AS,
                PROD1_LUMP_FE,
                PROD1_LUMP_SIO2,
                PROD1_LUMP_AL2O3,
                PROD1_LUMP_MN,
                PROD1_LUMP_P,
                PROD1_LUMP_LOI_425,
                PROD1_LUMP_LOI_TOTAL,
                PROD1_LUMP_S,
                PROD1_LUMP_AS,
                GB_DRY_DENSITY,
                LOI_425
            FROM DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS
        ),
        MINUS1MM_BY_STOCKPILE AS (
            SELECT
                ps.STOCKPILE_BUILD_NAME,
                SUM(COALESCE(gb.PROD1_MINUS1MM_PCT, 0) * ps.WMT)
                    / NULLIF(SUM(ps.WMT), 0) AS MINUS_1MM_PCT,
                SUM(COALESCE(gb.PROD1_FINES_YIELD_PCT, 0) * ps.DMT)
                    / NULLIF(SUM(ps.DMT), 0) AS FINES_YIELD_PCT,
                SUM(COALESCE(gb.PROD1_LUMP_YIELD_PCT, 0) * ps.DMT)
                    / NULLIF(SUM(ps.DMT), 0) AS LUMP_YIELD_PCT,
                SUM(COALESCE(gb.PROD1_FINES_MOISTURE, 0) * ps.WMT)
                    / NULLIF(SUM(ps.WMT), 0) AS FINES_MOISTURE,
                SUM(COALESCE(gb.PROD1_LUMP_MOISTURE, 0) * ps.WMT)
                    / NULLIF(SUM(ps.WMT), 0) AS LUMP_MOISTURE,
                SUM(COALESCE(gb.GB_DRY_DENSITY, 0) * ps.DMT)
                    / NULLIF(SUM(ps.DMT), 0) AS GB_DRY_DENSITY,
                SUM(COALESCE(gb.LOI_425, 0) * ps.DMT)
                    / NULLIF(SUM(ps.DMT), 0) AS LOI_425,
                {', '.join(
                    f'SUM(COALESCE(gb.PROD1_{size}_{prop}, 0) * ps.DMT) '
                    f'/ NULLIF(SUM(ps.DMT), 0) AS {size}_{prop}'
                    for size in ('FINES', 'LUMP')
                    for prop in (
                        'FE', 'SIO2', 'AL2O3', 'MN', 'P', 'LOI_425',
                        'LOI_TOTAL', 'S', 'AS'
                    )
                )}
            FROM PS ps
            INNER JOIN GB gb
                ON UPPER(TRIM(ps.SOURCE_GRADEBLOCK_NAME)) = UPPER(TRIM(gb.FULL_NAME_WITH_SITE))
            GROUP BY ps.STOCKPILE_BUILD_NAME
        )
        SELECT
            LT.STOCKPILENAME AS name,
            LT.STOCKPILEBUILDNAME AS build,
            LT.TRANSACTIONDATETIME AS transaction_datetime,
            LT.BALANCEWMT AS balance,
            LT.FE_INSITU_WTAVG AS grade_fe,
            LT.SIO2_INSITU_WTAVG AS grade_si,
            LT.AL2O3_INSITU_WTAVG AS grade_al,
            LT.P_INSITU_WTAVG AS grade_p,
            LT.MN_INSITU_WTAVG AS grade_mn,
            LT.FE_ROM_WTAVG AS fe_rom,
            LT.SIO2_ROM_WTAVG AS si_rom,
            LT.AL2O3_ROM_WTAVG AS al_rom,
            LT.P_ROM_WTAVG AS p_rom,
            LT.MN_ROM_WTAVG AS mn_rom,
            LT.FE_PROD1_WTAVG AS fe_prod1,
            LT.SIO2_PROD1_WTAVG AS si_prod1,
            LT.AL2O3_PROD1_WTAVG AS al_prod1,
            LT.P_PROD1_WTAVG AS p_prod1,
            LT.MN_PROD1_WTAVG AS mn_prod1,
            LT.FE_PROD2_WTAVG AS fe_prod2,
            LT.SIO2_PROD2_WTAVG AS si_prod2,
            LT.AL2O3_PROD2_WTAVG AS al_prod2,
            LT.P_PROD2_WTAVG AS p_prod2,
            LT.MN_PROD2_WTAVG AS mn_prod2,
            LT.FE_PROD3_WTAVG AS fe_prod3,
            LT.SIO2_PROD3_WTAVG AS si_prod3,
            LT.AL2O3_PROD3_WTAVG AS al_prod3,
            LT.P_PROD3_WTAVG AS p_prod3,
            LT.MN_PROD3_WTAVG AS mn_prod3,
            {_CB_MATERIAL_CASE} AS cbmaterial,
            {additional_select}
        FROM LATEST_TRANSACTIONS LT
        LEFT JOIN STACK_OR_RECLAIM SR
            ON LT.STOCKPILEID = SR.STOCKPILEID
           AND LT.STOCKPILEBUILDNAME = SR.STOCKPILEBUILDNAME
        LEFT JOIN MINUS1MM_BY_STOCKPILE M
            ON M.STOCKPILE_BUILD_NAME = LT.STOCKPILEBUILDNAME
        WHERE LT.BALANCEWMT > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY LT.STOCKPILENAME
            ORDER BY LT.TRANSACTIONDATETIME DESC, LT.STOCKPILEBUILDNAME DESC
        ) = 1
        ORDER BY LT.STOCKPILENAME
    """

class OpeningStockpileInventories:
    def call_opening_stockpile_inventories(self, hub, area_name, start_time):
        
        # Call the function to connect (service account)
        conn = self.connect_snowflake_with_service_account()
        
        start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        query = opening_inventory_query()

        try:
            # Execute query
            cursor = conn.cursor()
            cursor.execute(query, (start_time, hub, area_name))
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
        conn = self.connect_snowflake_with_service_account()
        requested_builds = build if isinstance(build, list) else [build]
        requested_builds = list(dict.fromkeys(
            str(value).strip() for value in requested_builds if str(value).strip()
        ))
        if not requested_builds:
            if conn is not None:
                conn.close()
            self.save_AMT_to_database({})
            return {}

        if hasattr(start_time, "toPyDateTime"):
            start_time = start_time.toPyDateTime()
        if start_time is None:
            start_time = datetime.now()
        if isinstance(start_time, datetime):
            start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            start_time = str(start_time)

        requested_values = ", ".join(["(%s)"] * len(requested_builds))
        lineage_property_select = expit_select_sql()
        lineage_property_averages = weighted_property_sql()
        lineage_property_object = property_object_sql()
        direct_product_tonnage_select = direct_product_tonnage_select_sql()
        direct_product_tonnage_sums = direct_product_tonnage_sum_sql()
        direct_product_tonnage_object = direct_product_tonnage_object_sql()
        query = f"""
            WITH REQUESTED_BUILDS AS (
                SELECT COLUMN1::VARCHAR AS REQUESTED_BUILD
                FROM VALUES {requested_values}
            ),
            PARAMS AS (
                SELECT TO_TIMESTAMP_TZ(
                    %s || ' +08:00',
                    'YYYY-MM-DD HH24:MI:SS TZH:TZM'
                ) AS AS_OF_TS
            ),
            INVENTORY_INSTANCE_CANDIDATES AS (
                SELECT
                    requested.REQUESTED_BUILD,
                    inventory.STOCKPILENAME AS FOOTPRINT,
                    inventory.STOCKPILEBUILDNAME AS LOCATION_NAME,
                    inventory.TRANSACTIONDATETIME AS INVENTORY_TRANSACTION_DATETIME,
                    inventory.BALANCEWMT AS INVENTORY_BALANCE_WMT
                FROM REQUESTED_BUILDS requested
                CROSS JOIN PARAMS params
                INNER JOIN AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS inventory
                    ON  inventory.STOCKPILEBUILDNAME = requested.REQUESTED_BUILD
                    OR  inventory.STOCKPILENAME = requested.REQUESTED_BUILD
                WHERE inventory.TRANSACTIONDATETIME <= params.AS_OF_TS
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY requested.REQUESTED_BUILD
                    ORDER BY inventory.TRANSACTIONDATETIME DESC
                ) = 1
            ),
            SELECTED_INSTANCES AS (
                SELECT * FROM INVENTORY_INSTANCE_CANDIDATES
            ),
            INBOUND_RAW AS (
                SELECT
                    selected.FOOTPRINT,
                    selected.LOCATION_NAME,
                    movement.INTERNALID,
                    COALESCE(movement.TARGETHEX, '__UNATTRIBUTED__') AS HEX,
                    movement.DUMPEDDATETIME AS MOVEMENT_DATETIME,
                    AVG(movement.TONNES) OVER (
                        PARTITION BY selected.LOCATION_NAME, movement.INTERNALID
                    ) AS WMT,
                    movement.TARGETHEXLAT AS LATITUDE,
                    movement.TARGETHEXLNG AS LONGITUDE,
                    movement.TARGETHEXEASTING AS EASTING,
                    movement.TARGETHEXNORTHING AS NORTHING
                FROM SELECTED_INSTANCES selected
                CROSS JOIN PARAMS params
                INNER JOIN AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT movement
                    ON movement.TARGETLOCATIONNAME = selected.LOCATION_NAME
                WHERE movement.DUMPEDDATETIME <= params.AS_OF_TS
                  AND movement.TONNES > 0
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY selected.LOCATION_NAME, movement.INTERNALID
                    ORDER BY movement.DUMPEDDATETIME DESC
                ) = 1
            ),
            EXPIT_DETAILS AS (
                SELECT expit.*
                FROM INBOUND_RAW inbound
                CROSS JOIN PARAMS params
                INNER JOIN AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS expit
                    ON expit.INTERNAL_ID = inbound.INTERNALID
                WHERE expit.IS_DELETED = FALSE
                  AND expit.DISCRIMINATOR = 'PrimaryMovement'
                  AND expit.TRANSACTION_DATETIME <= params.AS_OF_TS
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY inbound.LOCATION_NAME, inbound.INTERNALID
                    ORDER BY expit.TRANSACTION_DATETIME DESC
                ) = 1
            ),
            TRUCK_LIST_ATTRIBUTES AS (
                SELECT truck.*
                FROM SELECTED_INSTANCES selected
                CROSS JOIN PARAMS params
                INNER JOIN AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT_STOCKPILE_HEX_TRUCK_LIST truck
                    ON truck.LOCATION_NAME = selected.LOCATION_NAME
                WHERE truck.DUMPEDDATETIME <= params.AS_OF_TS
                  AND truck.TRUCK_WMT > 0
            ),
            GRADE_CONTROL_BLOCKS AS (
                SELECT
                    CONCAT(
                        MINE_CODE, '_', LOCATION_NO, '_', PHASE, '_',
                        BLAST_RL, '_', BLAST_NO, '_',
                        CASE
                            WHEN TRY_TO_NUMBER(BLAST_NO) BETWEEN 600 AND 699
                             AND TRY_TO_NUMBER(FLITCH_RL) IS NOT NULL
                                THEN TO_VARCHAR(TRY_TO_NUMBER(FLITCH_RL) + 1)
                            ELSE FLITCH_RL
                        END,
                        '_', GB_NAME
                    ) AS FULL_NAME_WITH_SITE,
                    GB_WET_TONNES,
                    GB_DRY_TONNES,
                    PROD1_TONNES_WET,
                    PROD1_TONNES_DRY,
                    PROD2_TONNES_WET,
                    PROD2_TONNES_DRY,
                    PROD3_TONNES_WET,
                    PROD3_TONNES_DRY,
                    PROD1_FINES_TONNES_WET,
                    PROD1_FINES_TONNES_DRY,
                    PROD1_LUMP_TONNES_WET,
                    PROD1_LUMP_TONNES_DRY,
                    TRY_TO_DOUBLE(PROD1_MINUS1MM_PCT) AS PROD1_MINUS1MM_PCT,
                    PROD1_FINES_YIELD_PCT,
                    PROD1_LUMP_YIELD_PCT,
                    PROD1_FINES_MOISTURE,
                    PROD1_LUMP_MOISTURE,
                    PROD1_FINES_FE,
                    PROD1_FINES_SIO2,
                    PROD1_FINES_AL2O3,
                    PROD1_FINES_MN,
                    PROD1_FINES_P,
                    PROD1_FINES_LOI_425,
                    PROD1_FINES_LOI_TOTAL,
                    PROD1_FINES_S,
                    PROD1_FINES_AS,
                    PROD1_LUMP_FE,
                    PROD1_LUMP_SIO2,
                    PROD1_LUMP_AL2O3,
                    PROD1_LUMP_MN,
                    PROD1_LUMP_P,
                    PROD1_LUMP_LOI_425,
                    PROD1_LUMP_LOI_TOTAL,
                    PROD1_LUMP_S,
                    PROD1_LUMP_AS,
                    GB_DRY_DENSITY,
                    LOI_425
                FROM DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS
            ),
            INBOUND_ENRICHED AS (
                SELECT
                    inbound.*,
                    expit.SOURCE_GRADEBLOCK_ID,
                    COALESCE(truck.GRADE_BLOCK, expit.SOURCE_FMS) AS GRADE_BLOCK_NAME,
                    truck.ROM_MATS,
                    CASE
                        WHEN expit.SOURCE_GRADEBLOCK_ID IS NOT NULL
                            THEN 'EXPIT_INTERNAL_ID'
                        WHEN truck.GRADE_BLOCK IS NOT NULL
                            THEN 'TRUCK_LIST_TIME_HEX'
                        ELSE 'UNMATCHED'
                    END AS MATCH_METHOD,
                    {lineage_property_select},
                    {direct_product_tonnage_select}
                FROM INBOUND_RAW inbound
                LEFT JOIN EXPIT_DETAILS expit
                    ON expit.INTERNAL_ID = inbound.INTERNALID
                LEFT JOIN TRUCK_LIST_ATTRIBUTES truck
                    ON  truck.LOCATION_NAME = inbound.LOCATION_NAME
                    AND truck.HEX = inbound.HEX
                    AND truck.DUMPEDDATETIME = inbound.MOVEMENT_DATETIME
                LEFT JOIN GRADE_CONTROL_BLOCKS gradeblock
                    ON UPPER(TRIM(gradeblock.FULL_NAME_WITH_SITE)) IN (
                        UPPER(TRIM(expit.SOURCE_FMS)),
                        UPPER(TRIM(truck.GRADE_BLOCK))
                    )
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY inbound.LOCATION_NAME, inbound.INTERNALID
                    ORDER BY
                        CASE WHEN truck.GRADE_BLOCK IS NULL THEN 1 ELSE 0 END,
                        ABS(COALESCE(truck.TRUCK_WMT, inbound.WMT) - inbound.WMT),
                        truck.LAST_UPDATE DESC NULLS LAST,
                        CASE
                            WHEN UPPER(TRIM(gradeblock.FULL_NAME_WITH_SITE))
                               = UPPER(TRIM(expit.SOURCE_FMS)) THEN 0
                            ELSE 1
                        END
                ) = 1
            ),
            LINEAGE_BY_GRADE_BLOCK AS (
                SELECT
                    FOOTPRINT,
                    LOCATION_NAME,
                    HEX,
                    COALESCE(
                        'ID:' || TO_VARCHAR(SOURCE_GRADEBLOCK_ID),
                        'NAME:' || UPPER(GRADE_BLOCK_NAME),
                        'UNMATCHED'
                    ) AS LINEAGE_KEY,
                    SOURCE_GRADEBLOCK_ID AS GRADE_BLOCK_ID,
                    MAX(GRADE_BLOCK_NAME) AS GRADE_BLOCK_NAME,
                    MAX(ROM_MATS) AS ROM_MATS,
                    MAX(MATCH_METHOD) AS MATCH_METHOD,
                    SUM(WMT) AS GB_INBOUND_WMT,
                    COUNT(DISTINCT INTERNALID) AS TRIP_COUNT,
                    MIN(MOVEMENT_DATETIME) AS FIRST_DUMP_DATETIME,
                    MAX(MOVEMENT_DATETIME) AS LAST_DUMP_DATETIME,
                    {lineage_property_averages},
                    {direct_product_tonnage_sums}
                FROM INBOUND_ENRICHED
                WHERE HEX <> '__UNATTRIBUTED__'
                GROUP BY
                    FOOTPRINT,
                    LOCATION_NAME,
                    HEX,
                    COALESCE(
                        'ID:' || TO_VARCHAR(SOURCE_GRADEBLOCK_ID),
                        'NAME:' || UPPER(GRADE_BLOCK_NAME),
                        'UNMATCHED'
                    ),
                    SOURCE_GRADEBLOCK_ID
            ),
            LINEAGE_BY_HEX AS (
                SELECT
                    FOOTPRINT,
                    LOCATION_NAME,
                    HEX,
                    TO_JSON(ARRAY_AGG(
                        OBJECT_CONSTRUCT_KEEP_NULL(
                            'lineage_key', LINEAGE_KEY,
                            'grade_block_id', GRADE_BLOCK_ID,
                            'grade_block_name', GRADE_BLOCK_NAME,
                            'rom_mats', ROM_MATS,
                            'match_method', MATCH_METHOD,
                            'inbound_wmt', GB_INBOUND_WMT,
                            'trip_count', TRIP_COUNT,
                            'first_dump_datetime', FIRST_DUMP_DATETIME,
                            'last_dump_datetime', LAST_DUMP_DATETIME,
                            'direct_product_tonnes', OBJECT_CONSTRUCT_KEEP_NULL(
                                {direct_product_tonnage_object}
                            ),
                            'properties', OBJECT_CONSTRUCT_KEEP_NULL(
                                {lineage_property_object}
                            )
                        )
                    ) WITHIN GROUP (ORDER BY GB_INBOUND_WMT DESC))
                        AS GRADE_BLOCK_LINEAGE_JSON
                FROM LINEAGE_BY_GRADE_BLOCK
                GROUP BY FOOTPRINT, LOCATION_NAME, HEX
            ),
            OUTBOUND_RAW AS (
                SELECT
                    selected.FOOTPRINT,
                    selected.LOCATION_NAME,
                    movement.INTERNALID,
                    COALESCE(movement.SOURCEHEX, '__UNATTRIBUTED__') AS HEX,
                    movement.DUMPEDDATETIME AS MOVEMENT_DATETIME,
                    AVG(movement.TONNES) OVER (
                        PARTITION BY selected.LOCATION_NAME, movement.INTERNALID
                    ) AS WMT,
                    movement.SOURCEHEXLAT AS LATITUDE,
                    movement.SOURCEHEXLNG AS LONGITUDE,
                    movement.SOURCEHEXEASTING AS EASTING,
                    movement.SOURCEHEXNORTHING AS NORTHING
                FROM SELECTED_INSTANCES selected
                CROSS JOIN PARAMS params
                INNER JOIN AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT movement
                    ON movement.SOURCELOCATIONNAME = selected.LOCATION_NAME
                WHERE movement.DUMPEDDATETIME <= params.AS_OF_TS
                  AND movement.TONNES > 0
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY selected.LOCATION_NAME, movement.INTERNALID
                    ORDER BY movement.DUMPEDDATETIME DESC
                ) = 1
            ),
            HEX_MOVEMENTS AS (
                SELECT FOOTPRINT, LOCATION_NAME, HEX, WMT AS SIGNED_WMT
                FROM INBOUND_RAW
                UNION ALL
                SELECT FOOTPRINT, LOCATION_NAME, HEX, -WMT AS SIGNED_WMT
                FROM OUTBOUND_RAW
            ),
            HEX_BALANCES AS (
                SELECT
                    FOOTPRINT,
                    LOCATION_NAME,
                    HEX,
                    SUM(SIGNED_WMT) AS RAW_WMT
                FROM HEX_MOVEMENTS
                GROUP BY FOOTPRINT, LOCATION_NAME, HEX
            ),
            UNATTRIBUTED_MOVEMENTS AS (
                SELECT
                    FOOTPRINT,
                    LOCATION_NAME,
                    COALESCE(SUM(RAW_WMT), 0) AS UNATTRIBUTED_MOVEMENT_WMT
                FROM HEX_BALANCES
                WHERE HEX = '__UNATTRIBUTED__'
                GROUP BY FOOTPRINT, LOCATION_NAME
            ),
            HEX_GRADES AS (
                SELECT
                    grades.FOOTPRINT,
                    grades.LOCATION_NAME,
                    grades.HEX,
                    grades.FE,
                    grades.SIO2,
                    grades.AL2O3,
                    grades.MN,
                    grades.P,
                    grades.LONGITUDE,
                    grades.LATITUDE,
                    grades.LAST_UPDATE
                FROM AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT_HEX_GRADES grades
                CROSS JOIN PARAMS params
                INNER JOIN SELECTED_INSTANCES selected
                    ON grades.LOCATION_NAME = selected.LOCATION_NAME
                WHERE grades.LAST_UPDATE <= params.AS_OF_TS
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY grades.LOCATION_NAME, grades.HEX
                    ORDER BY grades.LAST_UPDATE DESC
                ) = 1
            ),
            HEX_COORDINATE_CANDIDATES AS (
                SELECT FOOTPRINT, LOCATION_NAME, HEX, MOVEMENT_DATETIME,
                       LATITUDE, LONGITUDE, EASTING, NORTHING
                FROM INBOUND_RAW
                WHERE HEX <> '__UNATTRIBUTED__'
                UNION ALL
                SELECT FOOTPRINT, LOCATION_NAME, HEX, MOVEMENT_DATETIME,
                       LATITUDE, LONGITUDE, EASTING, NORTHING
                FROM OUTBOUND_RAW
                WHERE HEX <> '__UNATTRIBUTED__'
            ),
            HEX_COORDINATES AS (
                SELECT * FROM HEX_COORDINATE_CANDIDATES
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY LOCATION_NAME, HEX
                    ORDER BY
                        CASE WHEN EASTING IS NULL OR NORTHING IS NULL THEN 1 ELSE 0 END,
                        MOVEMENT_DATETIME DESC
                ) = 1
            ),
            HEX_UNIVERSE AS (
                SELECT FOOTPRINT, LOCATION_NAME, HEX
                FROM HEX_BALANCES
                WHERE HEX <> '__UNATTRIBUTED__'
                UNION
                SELECT FOOTPRINT, LOCATION_NAME, HEX
                FROM HEX_GRADES
            )
            SELECT
                universe.FOOTPRINT,
                universe.LOCATION_NAME,
                universe.HEX,
                grades.FE,
                grades.SIO2,
                grades.AL2O3,
                grades.MN,
                grades.P,
                COALESCE(balance.RAW_WMT, 0) AS RAW_WMT,
                COALESCE(balance.RAW_WMT, 0) AS FINAL_WMT,
                COALESCE(grades.LONGITUDE, coordinates.LONGITUDE) AS LONGITUDE,
                COALESCE(grades.LATITUDE, coordinates.LATITUDE) AS LATITUDE,
                grades.LAST_UPDATE,
                coordinates.EASTING AS SOURCEHEXEASTING,
                coordinates.NORTHING AS SOURCEHEXNORTHING,
                CASE WHEN balance.HEX IS NULL THEN 'False' ELSE 'True' END AS HEX_UPDATED,
                selected.INVENTORY_BALANCE_WMT,
                selected.INVENTORY_TRANSACTION_DATETIME,
                COALESCE(unattributed.UNATTRIBUTED_MOVEMENT_WMT, 0)
                    AS UNATTRIBUTED_MOVEMENT_WMT,
                lineage.GRADE_BLOCK_LINEAGE_JSON
            FROM HEX_UNIVERSE universe
            INNER JOIN SELECTED_INSTANCES selected
                ON universe.LOCATION_NAME = selected.LOCATION_NAME
            LEFT JOIN HEX_BALANCES balance
                ON  balance.LOCATION_NAME = universe.LOCATION_NAME
                AND balance.HEX = universe.HEX
            LEFT JOIN HEX_GRADES grades
                ON  grades.LOCATION_NAME = universe.LOCATION_NAME
                AND grades.HEX = universe.HEX
            LEFT JOIN HEX_COORDINATES coordinates
                ON  coordinates.LOCATION_NAME = universe.LOCATION_NAME
                AND coordinates.HEX = universe.HEX
            LEFT JOIN UNATTRIBUTED_MOVEMENTS unattributed
                ON unattributed.LOCATION_NAME = universe.LOCATION_NAME
            LEFT JOIN LINEAGE_BY_HEX lineage
                ON  lineage.LOCATION_NAME = universe.LOCATION_NAME
                AND lineage.HEX = universe.HEX
            ORDER BY universe.FOOTPRINT, universe.HEX
        """

        try:
            # Execute query
            cursor = conn.cursor()
            cursor.execute(query, (*requested_builds, start_time))
            result = cursor.fetchall()

            # Fetch column names
            columns = [col[0] for col in cursor.description]

            data_dict = {}
            for row in result:
                key = row[0]
                data_dict.setdefault(key, []).append(dict(zip(columns, row)))

            data_dict = {
                footprint: align_amt_grade_block_lineage(
                    reconcile_amt_hex_rows(rows)
                )
                for footprint, rows in data_dict.items()
            }
            for footprint, rows in data_dict.items():
                for row in rows:
                    inventory_matched = bool(
                        row.get("LOCATION_NAME")
                        and row.get("INVENTORY_BALANCE_WMT") is not None
                    )
                    row["AMT_INVENTORY_MATCHED"] = inventory_matched
                    row["AMT_INVENTORY_STOCKPILE"] = str(
                        row.get("FOOTPRINT") or footprint or ""
                    )
                    row["AMT_INVENTORY_BUILD"] = str(
                        row.get("LOCATION_NAME") or ""
                    )
                    row["AMT_INVENTORY_TRANSACTION_DATETIME"] = str(
                        row.get("INVENTORY_TRANSACTION_DATETIME") or ""
                    )
                    row["AMT_INVENTORY_MATCH_RULE"] = (
                        "latest inventory build at or before scenario start"
                        if inventory_matched else "no inventory instance"
                    )

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
            transaction_datetime TEXT,
            balance REAL,
            grade_fe REAL,
            grade_si REAL,
            grade_al REAL,
            grade_p REAL,
            grade_mn REAL,
            fe_rom REAL, si_rom REAL, al_rom REAL, p_rom REAL, mn_rom REAL,
            fe_prod1 REAL, si_prod1 REAL, al_prod1 REAL, p_prod1 REAL, mn_prod1 REAL,
            fe_prod2 REAL, si_prod2 REAL, al_prod2 REAL, p_prod2 REAL, mn_prod2 REAL,
            fe_prod3 REAL, si_prod3 REAL, al_prod3 REAL, p_prod3 REAL, mn_prod3 REAL,
            grade_streams_json TEXT,
            grade_stream_warnings_json TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(opening_stockpile_inventories)")
        existing_columns = {column[1] for column in cursor.fetchall()}
        extra_columns = [
            f"{analyte}_{stream}"
            for stream in ("rom", "prod1", "prod2", "prod3")
            for analyte in ("fe", "si", "al", "p", "mn")
        ] + [
            "grade_streams_json", "grade_stream_warnings_json",
            *INVENTORY_ADDITIONAL_FIELDS,
        ]
        if "transaction_datetime" not in existing_columns:
            cursor.execute(
                "ALTER TABLE opening_stockpile_inventories "
                "ADD COLUMN transaction_datetime TEXT"
            )
            existing_columns.add("transaction_datetime")
        flattened_streams = {
            key: flatten_grade_streams(
                (row or {}).get("GRADE_STREAMS")
                or (row or {}).get("grade_streams")
            )
            for key, row in (data_dict or {}).items()
        }
        extra_columns += sorted({
            column
            for values in flattened_streams.values()
            for column in values
        })
        for column in extra_columns:
            if column not in existing_columns:
                column_type = (
                    "TEXT"
                    if column.endswith("_json") or column in INVENTORY_TEXT_FIELDS
                    else "INTEGER"
                    if column in INVENTORY_INTEGER_FIELDS
                    else "REAL"
                )
                cursor.execute(
                    f'ALTER TABLE opening_stockpile_inventories '
                    f'ADD COLUMN "{column}" {column_type}'
                )

        # Clear the table
        cursor.execute('DELETE FROM opening_stockpile_inventories')

        # Insert data into the database
        for key, row in data_dict.items():
            # Map uppercase keys to expected database column names
            mapped_row = {
                "name": row.get("NAME", None),  # Adjusted for uppercase column names
                "build": row.get("BUILD", None),
                "transaction_datetime": row.get("TRANSACTION_DATETIME", None),
                "balance": row.get("BALANCE", 0.0),
                "grade_fe": row.get("GRADE_FE", None),
                "grade_si": row.get("GRADE_SI", None),
                "grade_al": row.get("GRADE_AL", None),
                "grade_p": row.get("GRADE_P", None),
                "grade_mn": row.get("GRADE_MN", None),
                **{
                    f"{analyte}_{stream}": row.get(f"{analyte}_{stream}".upper())
                    for stream in ("rom", "prod1", "prod2", "prod3")
                    for analyte in ("fe", "si", "al", "p", "mn")
                },
                "grade_streams_json": json.dumps(row.get("GRADE_STREAMS")) if row.get("GRADE_STREAMS") else None,
                "grade_stream_warnings_json": json.dumps(
                    row.get("GRADE_STREAM_WARNINGS")
                    or row.get("grade_stream_warnings")
                    or []
                ),
            }

            # Skip insertion if mandatory fields (e.g., name) are missing
            if not mapped_row["name"]:
                print(f"Skipping row with missing name: {mapped_row}")
                continue

            cursor.execute('''
            INSERT INTO opening_stockpile_inventories (
                name, build, transaction_datetime, balance,
                grade_fe, grade_si, grade_al, grade_p, grade_mn,
                fe_rom, si_rom, al_rom, p_rom, mn_rom,
                fe_prod1, si_prod1, al_prod1, p_prod1, mn_prod1,
                fe_prod2, si_prod2, al_prod2, p_prod2, mn_prod2,
                fe_prod3, si_prod3, al_prod3, p_prod3, mn_prod3,
                grade_streams_json, grade_stream_warnings_json
            ) VALUES (
                :name, :build, :transaction_datetime, :balance,
                :grade_fe, :grade_si, :grade_al, :grade_p, :grade_mn,
                :fe_rom, :si_rom, :al_rom, :p_rom, :mn_rom,
                :fe_prod1, :si_prod1, :al_prod1, :p_prod1, :mn_prod1,
                :fe_prod2, :si_prod2, :al_prod2, :p_prod2, :mn_prod2,
                :fe_prod3, :si_prod3, :al_prod3, :p_prod3, :mn_prod3,
                :grade_streams_json, :grade_stream_warnings_json
            )
            ''', mapped_row)
            audit_values = {
                **inventory_additional_values(row),
                **flattened_streams.get(key, {}),
            }
            if audit_values:
                assignments = ", ".join(
                    f'"{column}" = ?' for column in audit_values
                )
                cursor.execute(
                    f'UPDATE opening_stockpile_inventories SET {assignments} '
                    'WHERE name = ?',
                    [*audit_values.values(), mapped_row["name"]],
                )

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
            hex_updated TEXT,
            grade_streams_json TEXT,
            internal_recon_matched INTEGER,
            internal_recon_inventory_stockpile TEXT,
            internal_recon_inventory_build TEXT,
            internal_recon_inventory_transaction_datetime TEXT,
            internal_recon_match_rule TEXT,
            internal_recon_warning TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(opening_AMT_stockpile_inventories)")
        existing_columns = {column[1] for column in cursor.fetchall()}
        if "last_update" not in existing_columns:
            cursor.execute("ALTER TABLE opening_AMT_stockpile_inventories ADD COLUMN last_update TEXT")
        if "grade_streams_json" not in existing_columns:
            cursor.execute("ALTER TABLE opening_AMT_stockpile_inventories ADD COLUMN grade_streams_json TEXT")
        amt_audit_by_hex = {}
        for footprint, rows in (data_dict or {}).items():
            for row in rows or []:
                modelled_audit = {
                    str(column).lower(): value
                    for column, value in row.items()
                    if str(column).upper().startswith("MODELLED_")
                }
                property_payload = (
                    row.get("MODELLED_PROPERTIES_JSON")
                    or row.get("modelled_properties_json")
                    or row.get("modelled_properties")
                    or {}
                )
                if isinstance(property_payload, str):
                    try:
                        property_payload = json.loads(property_payload)
                    except (TypeError, ValueError):
                        property_payload = {}
                property_payload = (
                    dict(property_payload)
                    if isinstance(property_payload, dict) else {}
                )
                property_values = dict(property_payload.get("values") or {})
                property_coverage = dict(
                    property_payload.get("coverage") or {}
                )
                for field_name, field_value in dict(
                    row.get("defined_fields") or {}
                ).items():
                    if field_value is None:
                        continue
                    property_values[str(field_name)] = field_value
                    property_coverage[str(field_name)] = 1.0
                property_payload["values"] = property_values
                property_payload["coverage"] = property_coverage
                audit = {
                    "amt_inventory_matched": int(bool(row.get("AMT_INVENTORY_MATCHED"))),
                    "amt_inventory_stockpile": row.get("AMT_INVENTORY_STOCKPILE"),
                    "amt_inventory_build": row.get("AMT_INVENTORY_BUILD"),
                    "amt_inventory_transaction_datetime": row.get(
                        "AMT_INVENTORY_TRANSACTION_DATETIME"
                    ),
                    "amt_inventory_match_rule": row.get("AMT_INVENTORY_MATCH_RULE"),
                    "internal_recon_matched": int(bool(row.get("INTERNAL_RECON_MATCHED"))),
                    "internal_recon_inventory_stockpile": row.get("INTERNAL_RECON_INVENTORY_STOCKPILE"),
                    "internal_recon_inventory_build": row.get("INTERNAL_RECON_INVENTORY_BUILD"),
                    "internal_recon_inventory_transaction_datetime": row.get(
                        "INTERNAL_RECON_INVENTORY_TRANSACTION_DATETIME"
                    ),
                    "internal_recon_match_rule": row.get("INTERNAL_RECON_MATCH_RULE"),
                    "internal_recon_warning": row.get("INTERNAL_RECON_WARNING"),
                    "location_name": row.get("LOCATION_NAME"),
                    "inventory_balance_wmt": row.get("INVENTORY_BALANCE_WMT"),
                    "raw_wmt": row.get("RAW_WMT"),
                    "spatially_corrected_wmt": row.get("SPATIALLY_CORRECTED_WMT"),
                    "spatial_adjustment_wmt": row.get("SPATIAL_ADJUSTMENT_WMT"),
                    "ledger_adjustment_wmt": row.get("LEDGER_ADJUSTMENT_WMT"),
                    "spatial_deficit_wmt": row.get("SPATIAL_DEFICIT_WMT"),
                    "spatial_deficit_filled_wmt": row.get("SPATIAL_DEFICIT_FILLED_WMT"),
                    "spatial_donor_wmt": row.get("SPATIAL_DONOR_WMT"),
                    "spatial_unresolved_wmt": row.get("SPATIAL_UNRESOLVED_WMT"),
                    "raw_stockpile_wmt": row.get("RAW_STOCKPILE_WMT"),
                    "raw_positive_stockpile_wmt": row.get("RAW_POSITIVE_STOCKPILE_WMT"),
                    "spatially_corrected_stockpile_wmt": row.get(
                        "SPATIALLY_CORRECTED_STOCKPILE_WMT"
                    ),
                    "final_stockpile_wmt": row.get("FINAL_STOCKPILE_WMT"),
                    "unattributed_movement_wmt": row.get("UNATTRIBUTED_MOVEMENT_WMT"),
                    "spatial_recon_status": row.get("SPATIAL_RECON_STATUS"),
                    "spatial_recon_method": row.get("SPATIAL_RECON_METHOD"),
                    "reclaim_direction_easting": row.get("RECLAIM_DIRECTION_EASTING"),
                    "reclaim_direction_northing": row.get("RECLAIM_DIRECTION_NORTHING"),
                    "grade_block_lineage_json": row.get("GRADE_BLOCK_LINEAGE_JSON"),
                    "grade_block_count": row.get("GRADE_BLOCK_COUNT"),
                    "lineage_entry_count": row.get("LINEAGE_ENTRY_COUNT"),
                    "lineage_inbound_wmt": row.get("LINEAGE_INBOUND_WMT"),
                    "lineage_matched_wmt": row.get("LINEAGE_MATCHED_WMT"),
                    "lineage_unmatched_wmt": row.get("LINEAGE_UNMATCHED_WMT"),
                    "lineage_final_wmt": row.get("LINEAGE_FINAL_WMT"),
                    "lineage_matched_final_wmt": row.get(
                        "LINEAGE_MATCHED_FINAL_WMT"
                    ),
                    "lineage_unmatched_final_wmt": row.get(
                        "LINEAGE_UNMATCHED_FINAL_WMT"
                    ),
                    "lineage_coverage_pct": row.get("LINEAGE_COVERAGE_PCT"),
                    "lineage_warning": row.get("LINEAGE_WARNING"),
                    "grade_stream_warnings_json": row.get(
                        "GRADE_STREAM_WARNINGS"
                    ),
                    "cb_split_method": row.get("CB_SPLIT_METHOD"),
                    "cb_split_warning": row.get("CB_SPLIT_WARNING"),
                    **modelled_audit,
                    "modelled_properties_json": property_payload,
                    **flatten_grade_streams(
                        row.get("GRADE_STREAMS") or row.get("grade_streams")
                    ),
                }
                amt_audit_by_hex[(str(footprint), str(row.get("HEX") or row.get("hex")))] = audit
        text_audit_columns = {
            "amt_inventory_stockpile",
            "amt_inventory_build",
            "amt_inventory_transaction_datetime",
            "amt_inventory_match_rule",
            "internal_recon_inventory_stockpile",
            "internal_recon_inventory_build",
            "internal_recon_inventory_transaction_datetime",
            "internal_recon_match_rule",
            "internal_recon_warning",
            "location_name",
            "spatial_recon_status",
            "spatial_recon_method",
            "grade_block_lineage_json",
            "lineage_warning",
            "grade_stream_warnings_json",
            "modelled_properties_json",
            "modelled_rom_mats",
            "modelled_dominant_ore_type",
            "cb_split_method",
            "cb_split_warning",
        }
        audit_columns = sorted({
            column for values in amt_audit_by_hex.values() for column in values
        })
        existing_columns = {column[1] for column in cursor.execute(
            "PRAGMA table_info(opening_AMT_stockpile_inventories)"
        ).fetchall()}
        for column in audit_columns:
            if column in existing_columns:
                continue
            column_type = (
                "TEXT" if column in text_audit_columns or column.endswith("_json")
                else "INTEGER" if column in {
                    "amt_inventory_matched", "internal_recon_matched", "grade_block_count",
                    "lineage_entry_count",
                }
                else "REAL"
            )
            cursor.execute(
                f'ALTER TABLE opening_AMT_stockpile_inventories '
                f'ADD COLUMN "{column}" {column_type}'
            )

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
                    "hex_updated": row.get("HEX_UPDATED", None),
                    "grade_streams_json": json.dumps(row.get("GRADE_STREAMS")) if row.get("GRADE_STREAMS") else None,
                }

                # Skip insertion if mandatory fields (e.g., footprint) are missing
                if not mapped_row["footprint"]:
                    print(f"Skipping row with missing footprint: {mapped_row}")
                    continue

                cursor.execute('''
                INSERT INTO opening_AMT_stockpile_inventories (footprint, hex, balance, grade_fe, grade_si, grade_al, grade_p, grade_mn, lat, long, northing, easting, last_update, hex_updated, grade_streams_json)
                VALUES (:footprint, :hex, :balance, :grade_fe, :grade_si, :grade_al, :grade_p, :grade_mn, :lat, :long, :northing, :easting, :last_update, :hex_updated, :grade_streams_json)
                ''', mapped_row)
                audit_values = amt_audit_by_hex.get(
                    (str(key), str(row.get("HEX") or row.get("hex"))), {}
                )
                if audit_values:
                    audit_values = {
                        column: (
                            json.dumps(value, default=str, separators=(",", ":"))
                            if isinstance(value, (dict, list, tuple)) else value
                        )
                        for column, value in audit_values.items()
                    }
                    assignments = ", ".join(
                        f'"{column}" = ?' for column in audit_values
                    )
                    cursor.execute(
                        f'UPDATE opening_AMT_stockpile_inventories '
                        f'SET {assignments} WHERE footprint = ? AND hex = ?',
                        [
                            *audit_values.values(),
                            mapped_row["footprint"],
                            mapped_row["hex"],
                        ],
                    )

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
            hex_updated TEXT,
            grade_streams_json TEXT,
            internal_recon_matched INTEGER,
            internal_recon_inventory_stockpile TEXT,
            internal_recon_inventory_build TEXT,
            internal_recon_inventory_transaction_datetime TEXT,
            internal_recon_match_rule TEXT,
            internal_recon_warning TEXT,
            internal_blend_recon_fe REAL,
            internal_blend_recon_si REAL,
            internal_blend_recon_al REAL,
            internal_blend_recon_p REAL,
            internal_blend_recon_mn REAL,
            internal_upgrade_fe REAL,
            internal_upgrade_si REAL,
            internal_upgrade_al REAL,
            internal_upgrade_p REAL,
            internal_upgrade_mn REAL
        )
        ''')

        cursor.execute("PRAGMA table_info(opening_AMT_stockpile_inventories)")
        existing_columns = {column[1] for column in cursor.fetchall()}
        required_columns = {
            "last_update": "TEXT",
            "grade_streams_json": "TEXT",
            "internal_recon_matched": "INTEGER",
            "internal_recon_inventory_stockpile": "TEXT",
            "internal_recon_inventory_build": "TEXT",
            "internal_recon_inventory_transaction_datetime": "TEXT",
            "internal_recon_match_rule": "TEXT",
            "internal_recon_warning": "TEXT",
            **{
                f"internal_blend_recon_{analyte}": "REAL"
                for analyte in ANALYTES
            },
            **{
                f"internal_upgrade_{analyte}": "REAL"
                for analyte in ANALYTES
            },
        }
        for column, column_type in required_columns.items():
            if column not in existing_columns:
                cursor.execute(
                    f"ALTER TABLE opening_AMT_stockpile_inventories "
                    f"ADD COLUMN {column} {column_type}"
                )

        # Clear the table
        cursor.execute('DELETE FROM opening_AMT_stockpile_inventories')
        # Commit and close the connection
        conn.commit()
        conn.close()
        
