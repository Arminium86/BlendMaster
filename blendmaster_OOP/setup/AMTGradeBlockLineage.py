"""Grade-block-attributed modelled properties for opening AMT hexes.

Snowflake returns one JSON lineage collection per hex.  This module aligns the
lineage tonnes to the spatially reconciled ``FINAL_WMT`` and derives every
numeric property with its own non-null tonnage denominator.  AMT insitu grades
are deliberately not replaced by these modelled properties.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict


TRUCK_PROPERTY_COLUMNS = {
    "GRADE_BLOCK_GBI": "truck.GBI",
    "GRADE_BLOCK_FE": "truck.FE",
    "GRADE_BLOCK_SIO2": "truck.SIO2",
    "GRADE_BLOCK_AL2O3": "truck.AL2O3",
    "GRADE_BLOCK_MN": "truck.MN",
    "GRADE_BLOCK_P": "truck.P",
    "GRADE_BLOCK_CAO": "truck.CAO",
    "GRADE_BLOCK_K2O": "truck.K2O",
    "GRADE_BLOCK_MGO": "truck.MGO",
    "GRADE_BLOCK_NA2O": "truck.NA2O",
    "GRADE_BLOCK_S": "truck.S",
    "GRADE_BLOCK_TIO2": "truck.TIO2",
    "GRADE_BLOCK_AS": 'truck."AS"',
    "GRADE_BLOCK_LOI371": "truck.LOI371",
    "GRADE_BLOCK_LOI650": "truck.LOI650",
    "GRADE_BLOCK_LOI1000": "truck.LOI1000",
    "GRADE_BLOCK_LOITOTAL": "truck.LOITOTAL",
}

EXPIT_FEED_PROPERTY_COLUMNS = {
    "FEED_MASS_RECOVERY": "expit.MASS_RECOVERY",
    "FEED_MUDRUSH_GOETHITE": "expit.MUDRUSH_GOETHITE",
    "FEED_MUDRUSH_ULTRAFINES_1MM": "expit.MUDRUSH_ULTRAFINES_1MM",
    "FEED_MOISTURE": "expit.MOISTURE",
    "FEED_LOI_TOTAL": "expit.LOI_TOTAL",
    "ORETYPE_BID": "expit.ORETYPE_BID",
    "ORETYPE_DID": "expit.ORETYPE_DID",
    "ORETYPE_CIDL": "expit.ORETYPE_CIDL",
    "ORETYPE_CIDM": "expit.ORETYPE_CIDM",
    "ORETYPE_CIDU": "expit.ORETYPE_CIDU",
    "ORETYPE_HC": "expit.ORETYPE_HC",
    "ORETYPE_OTHER": "expit.ORETYPE_OTHER",
}

PRODUCT_PROPERTY_SUFFIXES = (
    "AL2O3", "AS", "BAO", "CAO", "CL", "CO", "CR", "CU", "FE",
    "FE3O4", "FEO", "K2O", "LOI_1000", "LOI_105", "LOI_371",
    "LOI_425", "LOI_650", "LOI_TOTAL", "MGO", "MN", "MOISTURE",
    "NA2O", "NI", "P", "PB", "S", "SIO2", "SN", "SR", "TIO2",
    "TOTALOXIDES", "V", "ZN", "ZR", "MASS_RECOVERY",
    "MUDRUSH_GOETHITE", "MUDRUSH_ULTRAFINES_1MM", "P80MICRON",
    "WETDENSITY", "DRYDENSITY",
)

EXPIT_PRODUCT_PROPERTY_COLUMNS = {
    f"PROD{product}_{suffix}": f"expit.PROD{product}_{suffix}"
    for product in (1, 2)
    for suffix in PRODUCT_PROPERTY_SUFFIXES
}

LINEAGE_PROPERTY_COLUMNS = {
    **TRUCK_PROPERTY_COLUMNS,
    **EXPIT_FEED_PROPERTY_COLUMNS,
    **EXPIT_PRODUCT_PROPERTY_COLUMNS,
}


def expit_select_sql():
    return ",\n                    ".join(
        f"{source} AS {alias}"
        for alias, source in LINEAGE_PROPERTY_COLUMNS.items()
    )


def weighted_property_sql(weight_column="WMT"):
    return ",\n                    ".join(
        (
            f"DIV0(SUM(CASE WHEN {alias} IS NOT NULL "
            f"THEN {alias} * {weight_column} END), "
            f"SUM(CASE WHEN {alias} IS NOT NULL THEN {weight_column} END)) "
            f"AS {alias}"
        )
        for alias in LINEAGE_PROPERTY_COLUMNS
    )


def property_object_sql():
    return ",\n                                    ".join(
        f"'{alias.lower()}', {alias}"
        for alias in LINEAGE_PROPERTY_COLUMNS
    )


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decode_lineage(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def align_amt_grade_block_lineage(rows):
    """Align lineage to final tonnes and attach flattened modelled properties."""
    aligned_rows = [dict(row or {}) for row in rows or []]
    for row in aligned_rows:
        final_wmt = max(_number(row.get("FINAL_WMT")) or 0.0, 0.0)
        lineage = [
            dict(item) for item in _decode_lineage(row.get("GRADE_BLOCK_LINEAGE_JSON"))
            if isinstance(item, dict)
        ]
        inbound_total = sum(
            max(_number(item.get("inbound_wmt")) or 0.0, 0.0)
            for item in lineage
        )
        matched_wmt = 0.0
        weighted_sums = defaultdict(float)
        property_denominators = defaultdict(float)
        rom_mats_tonnes = defaultdict(float)

        for item in lineage:
            inbound_wmt = max(_number(item.get("inbound_wmt")) or 0.0, 0.0)
            share = inbound_wmt / inbound_total if inbound_total > 0 else 0.0
            remaining_wmt = final_wmt * share
            item["inbound_share"] = share
            item["remaining_wmt"] = remaining_wmt
            if str(item.get("match_method") or "").upper() != "UNMATCHED":
                matched_wmt += inbound_wmt
            rom_mats = str(item.get("rom_mats") or "").strip()
            if rom_mats:
                rom_mats_tonnes[rom_mats] += remaining_wmt
            properties = item.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            for property_name, raw_value in properties.items():
                value = _number(raw_value)
                if value is None or remaining_wmt <= 0:
                    continue
                key = str(property_name).strip().lower()
                weighted_sums[key] += value * remaining_wmt
                property_denominators[key] += remaining_wmt

        modelled_properties = {}
        property_coverage = {}
        for property_name in (
            alias.lower() for alias in LINEAGE_PROPERTY_COLUMNS
        ):
            denominator = property_denominators.get(property_name, 0.0)
            value = (
                weighted_sums[property_name] / denominator
                if denominator > 0 else None
            )
            coverage = denominator / final_wmt if final_wmt > 0 else None
            modelled_properties[property_name] = value
            property_coverage[property_name] = coverage
            column_name = f"MODELLED_{property_name.upper()}"
            row[column_name] = value
            row[f"{column_name}_COVERAGE_PCT"] = (
                coverage * 100.0 if coverage is not None else None
            )

        lineage_coverage = matched_wmt / inbound_total if inbound_total > 0 else None
        warning = ""
        if final_wmt > 0 and inbound_total <= 0:
            warning = "No inbound grade-block lineage was found for a positive hex balance."
        elif lineage_coverage is not None and lineage_coverage < 0.999999:
            warning = (
                f"Grade-block lineage covers {lineage_coverage:.2%} of inbound WMT; "
                "null modelled properties retain independent coverage."
            )

        row["GRADE_BLOCK_LINEAGE_JSON"] = json.dumps(
            lineage, default=str, separators=(",", ":")
        )
        row["MODELLED_PROPERTIES_JSON"] = json.dumps({
            "values": modelled_properties,
            "coverage": property_coverage,
        }, default=str, separators=(",", ":"))
        row["LINEAGE_ENTRY_COUNT"] = len(lineage)
        row["GRADE_BLOCK_COUNT"] = len({
            str(item.get("lineage_key") or "") for item in lineage
            if item.get("lineage_key")
            and str(item.get("lineage_key")).upper() != "UNMATCHED"
        })
        row["LINEAGE_INBOUND_WMT"] = inbound_total
        row["LINEAGE_MATCHED_WMT"] = matched_wmt
        row["LINEAGE_UNMATCHED_WMT"] = max(inbound_total - matched_wmt, 0.0)
        row["LINEAGE_FINAL_WMT"] = final_wmt
        row["LINEAGE_MATCHED_FINAL_WMT"] = (
            final_wmt * lineage_coverage
            if lineage_coverage is not None else 0.0
        )
        row["LINEAGE_UNMATCHED_FINAL_WMT"] = (
            final_wmt * (1.0 - lineage_coverage)
            if lineage_coverage is not None else final_wmt
        )
        row["LINEAGE_COVERAGE_PCT"] = (
            lineage_coverage * 100.0
            if lineage_coverage is not None else None
        )
        row["LINEAGE_WARNING"] = warning
        row["MODELLED_ROM_MATS"] = (
            max(rom_mats_tonnes, key=rom_mats_tonnes.get)
            if rom_mats_tonnes else None
        )
        ore_types = {
            name.removeprefix("oretype_").upper(): weighted_sums.get(name, 0.0)
            for name in modelled_properties
            if name.startswith("oretype_")
            and property_denominators.get(name, 0.0) > 0
        }
        for ore_type, tonnes in ore_types.items():
            row[f"MODELLED_ORETYPE_{ore_type}_TONNES"] = tonnes
        row["MODELLED_DOMINANT_ORE_TYPE"] = (
            max(ore_types, key=ore_types.get) if ore_types else None
        )
    return aligned_rows
