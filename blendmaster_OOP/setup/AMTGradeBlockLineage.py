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

CB_PROD1_SPLIT_ASSAYS = (
    "FE", "SIO2", "AL2O3", "MN", "P", "LOI_425", "LOI_TOTAL", "S", "AS",
)

# Cloudbreak's lump/fines properties are grade-block model outputs rather than
# columns on the EXPIT transaction.  They are joined to each inbound movement
# by the authoritative grade-block name in ``call_opening_AMT...``.
GRADE_CONTROL_PROPERTY_COLUMNS = {
    "PROD1_MINUS1MM_PCT": "gradeblock.PROD1_MINUS1MM_PCT",
    "PROD1_FINES_YIELD_PCT": "gradeblock.PROD1_FINES_YIELD_PCT",
    "PROD1_LUMP_YIELD_PCT": "gradeblock.PROD1_LUMP_YIELD_PCT",
    "PROD1_FINES_MOISTURE": "gradeblock.PROD1_FINES_MOISTURE",
    "PROD1_LUMP_MOISTURE": "gradeblock.PROD1_LUMP_MOISTURE",
    "GRADE_BLOCK_DRY_DENSITY": "gradeblock.GB_DRY_DENSITY",
    "GRADE_BLOCK_LOI_425": "gradeblock.LOI_425",
    **{
        f"PROD1_{size}_{assay}": f"gradeblock.PROD1_{size}_{assay}"
        for size in ("FINES", "LUMP")
        for assay in CB_PROD1_SPLIT_ASSAYS
    },
}

# Grade Control stores absolute modelled product tonnes for the whole grade
# block.  The AMT opening query allocates these to each inbound movement by its
# share of ``GB_WET_TONNES``.  They are then scaled with the remaining lineage
# share in ``align_amt_grade_block_lineage`` so reclaimed material cannot leave
# its product mass behind in a hex.
DIRECT_LINEAGE_TONNE_COLUMNS = {
    "FEED_DMT": "gradeblock.GB_DRY_TONNES",
    **{
        f"PROD{product}_{basis}": (
            f"gradeblock.PROD{product}_TONNES_{source_basis}"
        )
        for product in (1, 2, 3)
        for basis, source_basis in (("WMT", "WET"), ("DMT", "DRY"))
    },
    **{
        f"PROD1_{size}_{basis}": (
            f"gradeblock.PROD1_{size}_TONNES_{source_basis}"
        )
        for size in ("FINES", "LUMP")
        for basis, source_basis in (("WMT", "WET"), ("DMT", "DRY"))
    },
}

EXPIT_PRODUCT_PROPERTY_COLUMNS = {
    f"PROD{product}_{suffix}": f"expit.PROD{product}_{suffix}"
    for product in (1, 2)
    for suffix in PRODUCT_PROPERTY_SUFFIXES
}

LINEAGE_PROPERTY_COLUMNS = {
    **TRUCK_PROPERTY_COLUMNS,
    **EXPIT_FEED_PROPERTY_COLUMNS,
    **EXPIT_PRODUCT_PROPERTY_COLUMNS,
    **GRADE_CONTROL_PROPERTY_COLUMNS,
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


def direct_product_tonnage_select_sql():
    """Allocate whole-grade-block product tonnes to an inbound AMT movement."""
    return ",\n                    ".join(
        (
            "IFF(gradeblock.GB_WET_TONNES > 0 "
            f"AND {source} IS NOT NULL, "
            f"{source} * inbound.WMT / gradeblock.GB_WET_TONNES, NULL) "
            f"AS DIRECT_{alias}"
        )
        for alias, source in DIRECT_LINEAGE_TONNE_COLUMNS.items()
    )


def direct_product_tonnage_sum_sql():
    """Aggregate allocated direct product tonnes for a hex/grade-block lineage."""
    return ",\n                    ".join(
        f"SUM(DIRECT_{alias}) AS DIRECT_{alias}"
        for alias in DIRECT_LINEAGE_TONNE_COLUMNS
    )


def direct_product_tonnage_object_sql():
    """Build the direct additive-tonne lineage object stored for Python alignment."""
    return ",\n                                ".join(
        f"'{alias.lower()}', DIRECT_{alias}"
        for alias in DIRECT_LINEAGE_TONNE_COLUMNS
    )


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fraction(value):
    """Normalise model percentages/fractions to [0, 1]."""
    number = _number(value)
    if number is None or number < 0:
        return None
    if number > 1.0:
        if number > 100.0:
            return None
        number /= 100.0
    return min(number, 1.0)


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
        additive_totals = defaultdict(float)
        additive_coverage_wmt = defaultdict(float)
        split_grade_masses = defaultdict(float)
        split_grade_weights = defaultdict(float)
        split_grade_coverage_wmt = defaultdict(float)

        if final_wmt > 0:
            additive_totals["feed_wmt"] = final_wmt
            additive_coverage_wmt["feed_wmt"] = final_wmt

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
            properties = {
                str(name).strip().lower(): raw_value
                for name, raw_value in properties.items()
            }
            direct_product_tonnes = item.get("direct_product_tonnes")
            direct_product_tonnes = (
                {
                    str(name).strip().lower(): raw_value
                    for name, raw_value in direct_product_tonnes.items()
                }
                if isinstance(direct_product_tonnes, dict) else {}
            )
            remaining_share = (
                remaining_wmt / inbound_wmt if inbound_wmt > 0 else 0.0
            )
            direct_remaining_tonnes = {}
            # The SQL query has already allocated each Grade Control total to
            # the inbound AMT movement.  Scale that allocated amount once more
            # by the residual hex share after reclaim/spatial reconciliation.
            for property_name, raw_value in direct_product_tonnes.items():
                direct_amount = _number(raw_value)
                if direct_amount is None or direct_amount < 0:
                    continue
                direct_remaining_tonnes[property_name] = (
                    direct_amount * remaining_share
                )
                additive_totals[property_name] += direct_remaining_tonnes[
                    property_name
                ]
                additive_coverage_wmt[property_name] += remaining_wmt
            for property_name, raw_value in properties.items():
                value = _number(raw_value)
                if value is None or remaining_wmt <= 0:
                    continue
                key = str(property_name).strip().lower()
                weighted_sums[key] += value * remaining_wmt
                property_denominators[key] += remaining_wmt

            direct_feed_dmt = direct_remaining_tonnes.get("feed_dmt")
            feed_moisture = _fraction(properties.get("feed_moisture"))
            feed_dmt = direct_feed_dmt if direct_feed_dmt is not None else (
                remaining_wmt * (1.0 - feed_moisture)
                if feed_moisture is not None else None
            )
            if feed_dmt is not None and direct_feed_dmt is None:
                additive_totals["feed_dmt"] += feed_dmt
                additive_coverage_wmt["feed_dmt"] += remaining_wmt

            # Ore-type fields are feed fractions.  Retain both wet and dry
            # tonnes so constraints can use the appropriate mass basis.
            for ore_type in ("bid", "did", "cidl", "cidm", "cidu", "hc", "other"):
                fraction = _fraction(properties.get(f"oretype_{ore_type}"))
                if fraction is None:
                    continue
                additive_totals[f"oretype_{ore_type}_wmt"] += (
                    remaining_wmt * fraction
                )
                additive_coverage_wmt[f"oretype_{ore_type}_wmt"] += remaining_wmt
                if feed_dmt is not None:
                    additive_totals[f"oretype_{ore_type}_dmt"] += (
                        feed_dmt * fraction
                    )
                    additive_coverage_wmt[f"oretype_{ore_type}_dmt"] += (
                        remaining_wmt
                    )

            split_totals = {"wmt": 0.0, "dmt": 0.0}
            split_available = {"wmt": True, "dmt": feed_dmt is not None}
            for size in ("fines", "lump"):
                direct_wmt = direct_remaining_tonnes.get(
                    f"prod1_{size}_wmt"
                )
                direct_dmt = direct_remaining_tonnes.get(
                    f"prod1_{size}_dmt"
                )
                yield_fraction = _fraction(
                    properties.get(f"prod1_{size}_yield_pct")
                )
                size_wmt = direct_wmt
                size_dmt = direct_dmt
                if size_wmt is None or size_dmt is None:
                    if yield_fraction is None:
                        if size_wmt is None:
                            split_available["wmt"] = False
                        if size_dmt is None:
                            split_available["dmt"] = False
                    else:
                        if size_wmt is None:
                            size_wmt = remaining_wmt * yield_fraction
                            additive_totals[f"prod1_{size}_wmt"] += size_wmt
                            additive_coverage_wmt[
                                f"prod1_{size}_wmt"
                            ] += remaining_wmt
                        if size_dmt is None and feed_dmt is not None:
                            size_dmt = feed_dmt * yield_fraction
                            additive_totals[f"prod1_{size}_dmt"] += size_dmt
                            additive_coverage_wmt[
                                f"prod1_{size}_dmt"
                            ] += remaining_wmt
                if size_wmt is not None:
                    split_totals["wmt"] += size_wmt
                if size_dmt is not None:
                    split_totals["dmt"] += size_dmt

                grade_weight = size_dmt if size_dmt is not None else size_wmt
                if grade_weight is None or grade_weight <= 0:
                    continue
                for assay in (name.lower() for name in CB_PROD1_SPLIT_ASSAYS):
                    grade_key = f"prod1_{size}_{assay}"
                    grade = _number(properties.get(grade_key))
                    if grade is None:
                        continue
                    split_grade_masses[grade_key] += grade * grade_weight
                    split_grade_weights[grade_key] += grade_weight
                    split_grade_coverage_wmt[grade_key] += remaining_wmt

            for product in (1, 2):
                direct_product_wmt = direct_remaining_tonnes.get(
                    f"prod{product}_wmt"
                )
                direct_product_dmt = direct_remaining_tonnes.get(
                    f"prod{product}_dmt"
                )
                recovery = _fraction(
                    properties.get(f"prod{product}_mass_recovery")
                )
                product_moisture = _fraction(
                    properties.get(f"prod{product}_moisture")
                )
                product_dmt = direct_product_dmt if direct_product_dmt is not None else (
                    feed_dmt * recovery
                    if feed_dmt is not None and recovery is not None else None
                )
                product_wmt = direct_product_wmt if direct_product_wmt is not None else (
                    product_dmt / (1.0 - product_moisture)
                    if product_dmt is not None
                    and product_moisture is not None
                    and product_moisture < 1.0
                    else None
                )

                # For CB PROD1, the grade-control lump/fines yields are the
                # authoritative split.  Require both size yields, then make
                # the canonical product total equal their conserved sum.
                if (
                    product == 1
                    and direct_product_wmt is None
                    and split_available["wmt"]
                ):
                    product_wmt = split_totals["wmt"]
                if (
                    product == 1
                    and direct_product_dmt is None
                    and split_available["dmt"]
                ):
                    product_dmt = split_totals["dmt"]

                if product_wmt is not None and direct_product_wmt is None:
                    additive_totals[f"prod{product}_wmt"] += product_wmt
                    additive_coverage_wmt[f"prod{product}_wmt"] += remaining_wmt
                if product_dmt is not None and direct_product_dmt is None:
                    additive_totals[f"prod{product}_dmt"] += product_dmt
                    additive_coverage_wmt[f"prod{product}_dmt"] += remaining_wmt

                minus_1mm = _fraction(
                    properties.get(f"prod{product}_minus1mm_pct")
                )
                if minus_1mm is None:
                    minus_1mm = _fraction(
                        properties.get(
                            f"prod{product}_mudrush_ultrafines_1mm"
                        )
                    )
                if minus_1mm is not None and product_wmt is not None:
                    additive_totals[f"prod{product}_minus_1mm_wmt"] += (
                        product_wmt * minus_1mm
                    )
                    additive_coverage_wmt[
                        f"prod{product}_minus_1mm_wmt"
                    ] += remaining_wmt
                if minus_1mm is not None and product_dmt is not None:
                    additive_totals[f"prod{product}_minus_1mm_dmt"] += (
                        product_dmt * minus_1mm
                    )
                    additive_coverage_wmt[
                        f"prod{product}_minus_1mm_dmt"
                    ] += remaining_wmt

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

        # Component grades must be weighted by the component product mass, not
        # by the unsplit feed mass used by the generic property loop above.
        for property_name, weight in split_grade_weights.items():
            if weight <= 0:
                continue
            value = split_grade_masses[property_name] / weight
            coverage = (
                min(split_grade_coverage_wmt[property_name] / final_wmt, 1.0)
                if final_wmt > 0 else None
            )
            modelled_properties[property_name] = value
            property_coverage[property_name] = coverage
            column_name = f"MODELLED_{property_name.upper()}"
            row[column_name] = value
            row[f"{column_name}_COVERAGE_PCT"] = (
                coverage * 100.0 if coverage is not None else None
            )

        for property_name, value in additive_totals.items():
            coverage = (
                min(additive_coverage_wmt[property_name] / final_wmt, 1.0)
                if final_wmt > 0 else None
            )
            modelled_properties[property_name] = value
            property_coverage[property_name] = coverage
            column_name = f"MODELLED_{property_name.upper()}"
            row[column_name] = value
            row[f"{column_name}_COVERAGE_PCT"] = (
                coverage * 100.0 if coverage is not None else None
            )

        # Canonical physical-field aliases are shared with inventory sources.
        # Keep the PROD1-qualified names as the source of truth and retain the
        # shorter legacy lump/fines names for existing projects.
        canonical_aliases = {}
        for product in (1, 2):
            minus_1mm_value = modelled_properties.get(
                f"prod{product}_minus1mm_pct"
            )
            minus_1mm_source = f"prod{product}_minus1mm_pct"
            if minus_1mm_value is None:
                minus_1mm_source = (
                    f"prod{product}_mudrush_ultrafines_1mm"
                )
                minus_1mm_value = modelled_properties.get(minus_1mm_source)
            if minus_1mm_value is not None:
                canonical_aliases[f"prod{product}_minus_1mm_pct"] = (
                    minus_1mm_value
                )
        for size in ("fines", "lump"):
            for suffix in (
                "yield_pct", "wmt", "dmt", "moisture", *(
                    assay.lower() for assay in CB_PROD1_SPLIT_ASSAYS
                ),
            ):
                qualified = f"prod1_{size}_{suffix}"
                if modelled_properties.get(qualified) is not None:
                    canonical_aliases[f"{size}_{suffix}"] = (
                        modelled_properties[qualified]
                    )
        for property_name, value in canonical_aliases.items():
            if property_name.startswith("prod") and property_name.endswith(
                "_minus_1mm_pct"
            ):
                product = property_name.split("_", 1)[0]
                preferred = f"{product}_minus1mm_pct"
                qualified_name = (
                    preferred
                    if preferred in property_coverage
                    else f"{product}_mudrush_ultrafines_1mm"
                )
            else:
                qualified_name = f"prod1_{property_name}"
            coverage = property_coverage.get(qualified_name)
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
            ore_type.upper(): additive_totals[f"oretype_{ore_type}_wmt"]
            for ore_type in ("bid", "did", "cidl", "cidm", "cidu", "hc", "other")
            if additive_coverage_wmt.get(f"oretype_{ore_type}_wmt", 0.0) > 0
        }
        for ore_type, tonnes in ore_types.items():
            row[f"MODELLED_ORETYPE_{ore_type}_TONNES"] = tonnes
        row["MODELLED_DOMINANT_ORE_TYPE"] = (
            max(ore_types, key=ore_types.get) if ore_types else None
        )
    return aligned_rows
