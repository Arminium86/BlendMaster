"""Cloudbreak PROD1 lump/fines property calculation.

The grade-block-derived mode is assembled by ``AMTGradeBlockLineage``.  This
module implements the optional Data Streams user-calculated mode without any
UI dependency so inventory stockpiles and AMT hexes use the same formulas.
APS sources retain their authoritative mapped lump/fines values.
"""

from __future__ import annotations

import math
from typing import Mapping


CB_SPLIT_ASSAYS = (
    "fe", "sio2", "al2o3", "mn", "p", "loi_425", "loi_total", "s", "as",
)

_TOTAL_GRADE_ALIASES = {
    "fe": ("fe",),
    "sio2": ("sio2", "si"),
    "al2o3": ("al2o3", "al"),
    "mn": ("mn",),
    "p": ("p",),
    "loi_425": ("loi_425",),
    "loi_total": ("loi_total", "loi"),
    "s": ("s",),
    "as": ("as",),
}


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def normalise_lump_fraction(value):
    """Accept either a 0..1 fraction or 0..100 percentage."""
    value = _number(value)
    if value is None or value < 0:
        raise ValueError("Cloudbreak lump percentage must be a finite value >= 0.")
    if value > 1.0:
        value /= 100.0
    if value > 1.0:
        raise ValueError("Cloudbreak lump percentage cannot exceed 100%.")
    return value


def _first_number(properties, *keys):
    for key in keys:
        value = _number(properties.get(key))
        if value is not None:
            return value
    return None


def calculate_cb_lump_fines(
    properties: Mapping,
    lump_percentage,
    *,
    product_wmt=None,
    product_dmt=None,
    head_grades=None,
    fines_grades=None,
    source_wmt=None,
    source_dmt=None,
):
    """Return canonical calculated CB lump/fines fields and provenance.

    Total product WMT/DMT is split directly by the user lump percentage.
    ``head_grades`` is the standard-SF-adjusted total product and
    ``fines_grades`` is adjusted with the dedicated CBSF-on-CBFL-campaign
    factor. Lump grades are back-calculated so the two by-products recombine
    to the adjusted head grade. DMT is preferred for that mass balance and WMT
    is used only when DMT is unavailable.

    ``source_wmt`` and ``source_dmt`` remain compatibility aliases for older
    callers; new code must provide product tonnes explicitly.
    """
    properties = {
        str(key).strip().lower(): value
        for key, value in dict(properties or {}).items()
    }
    lump_fraction = normalise_lump_fraction(lump_percentage)
    fines_fraction = 1.0 - lump_fraction
    head_grades = {
        str(key).strip().lower(): value
        for key, value in dict(head_grades or {}).items()
    }
    fines_grades = {
        str(key).strip().lower(): value
        for key, value in dict(fines_grades or {}).items()
    }
    product_wmt = _number(product_wmt)
    if product_wmt is None:
        product_wmt = _first_number(
            properties,
            "modelled_product_wmt", "prod1_wmt", "product_wmt",
        )
    if product_wmt is None:
        product_wmt = _number(source_wmt)
    product_dmt = _number(product_dmt)
    if product_dmt is None:
        product_dmt = _first_number(
            properties,
            "modelled_product_dmt", "prod1_dmt", "product_dmt",
        )
    if product_dmt is None:
        product_dmt = _number(source_dmt)
    if product_wmt is None and product_dmt is None:
        raise ValueError(
            "Cloudbreak calculated lump/fines requires mapped total product "
            "WMT or DMT."
        )

    result = dict(properties)
    result.update({
        "prod1_lump_yield_pct": lump_fraction,
        "prod1_fines_yield_pct": fines_fraction,
        "lump_yield_pct": lump_fraction,
        "fines_yield_pct": fines_fraction,
        "cb_lump_fraction_input": lump_fraction,
    })
    if product_wmt is not None:
        result.update({
            "prod1_wmt": product_wmt,
            "prod1_lump_wmt": product_wmt * lump_fraction,
            "prod1_fines_wmt": product_wmt * fines_fraction,
            "lump_wmt": product_wmt * lump_fraction,
            "fines_wmt": product_wmt * fines_fraction,
        })
    if product_dmt is not None:
        result.update({
            "prod1_dmt": product_dmt,
            "prod1_lump_dmt": product_dmt * lump_fraction,
            "prod1_fines_dmt": product_dmt * fines_fraction,
            "lump_dmt": product_dmt * lump_fraction,
            "fines_dmt": product_dmt * fines_fraction,
        })

    minus_1mm_pct = _first_number(
        properties,
        "prod1_minus_1mm_pct",
        "prod1_minus1mm_pct",
        "minus_1mm_pct",
        "prod1_mudrush_ultrafines_1mm",
    )
    if minus_1mm_pct is not None:
        minus_1mm_fraction = (
            minus_1mm_pct / 100.0
            if minus_1mm_pct > 1.0 else minus_1mm_pct
        )
        if 0.0 <= minus_1mm_fraction <= 1.0:
            result["prod1_minus_1mm_pct"] = minus_1mm_pct
            if product_wmt is not None:
                result["prod1_minus_1mm_wmt"] = (
                    product_wmt * minus_1mm_fraction
                )
            if product_dmt is not None:
                result["prod1_minus_1mm_dmt"] = (
                    product_dmt * minus_1mm_fraction
                )

    used_back_calculation = False
    equal_grade_fallbacks = []
    warnings = []
    lump_weight = result.get("prod1_lump_dmt", result.get("prod1_lump_wmt"))
    fines_weight = result.get("prod1_fines_dmt", result.get("prod1_fines_wmt"))
    total_weight = (
        (lump_weight + fines_weight)
        if lump_weight is not None and fines_weight is not None else None
    )

    for assay in CB_SPLIT_ASSAYS:
        aliases = _TOTAL_GRADE_ALIASES[assay]
        total_grade = _number(
            head_grades.get(assay)
        )
        if total_grade is None:
            total_grade = _first_number(properties, *(
            key
            for alias in aliases
            for key in (
                f"prod1_{alias}",
                f"modelled_prod1_{alias}",
                f"{alias}_prod1",
            )
        ))
        if total_grade is None:
            continue
        fines_grade = _number(fines_grades.get(assay))
        if fines_grade is None:
            fines_grade = _first_number(
                properties,
                f"prod1_fines_{assay}",
                f"fines_{assay}",
                f"modelled_prod1_fines_{assay}",
            )
        if (
            fines_grade is not None
            and lump_weight is not None
            and fines_weight is not None
            and total_weight is not None
            and lump_weight > 1e-12
        ):
            lump_grade = (
                total_grade * total_weight - fines_grade * fines_weight
            ) / lump_weight
            used_back_calculation = True
            if not 0.0 <= lump_grade <= 100.0:
                raise ValueError(
                    "Cloudbreak calculated lump/fines produced an invalid "
                    f"PROD1 lump {assay} grade ({lump_grade:.6g}). Review "
                    "the lump percentage and SF reconciliation factors."
                )
        else:
            fines_grade = total_grade
            lump_grade = total_grade
            equal_grade_fallbacks.append(assay)

        for key, value in (
            (f"prod1_fines_{assay}", fines_grade),
            (f"prod1_lump_{assay}", lump_grade),
            (f"fines_{assay}", fines_grade),
            (f"lump_{assay}", lump_grade),
        ):
            result[key] = value

    if equal_grade_fallbacks:
        warnings.append(
            "No independent fines grade was available for "
            + ", ".join(equal_grade_fallbacks)
            + "; total PROD1 grade was assigned to both lump and fines."
        )
    method = (
        "calculated_user_lump_pct_backcalculated_lump"
        if used_back_calculation
        else "calculated_user_lump_pct_equal_grade_fallback"
    )
    if used_back_calculation and equal_grade_fallbacks:
        method = "calculated_user_lump_pct_mixed_grade_methods"
    result["cb_split_method"] = method
    result["cb_split_warning"] = " ".join(warnings)
    return result
