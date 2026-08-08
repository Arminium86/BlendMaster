from copy import deepcopy
from typing import Iterable, Mapping


PRODUCT_LANE = "product"
BYPRODUCT_LANES = ("lump", "fines")
ANALYTES = ("fe", "si", "al", "p", "mn")


def default_byproduct_quantity_fields():
    return {
        "lump": "prod1_lump_wmt",
        "fines": "prod1_fines_wmt",
    }


def default_byproduct_grade_fields():
    return {
        lane: {
            analyte: f"prod1_{lane}_{analyte}"
            for analyte in ANALYTES
        }
        for lane in BYPRODUCT_LANES
    }


def normalize_byproduct_quantity_fields(value):
    defaults = default_byproduct_quantity_fields()
    source = value if isinstance(value, Mapping) else {}
    return {
        lane: str(source.get(lane) or defaults[lane]).strip().lower()
        for lane in BYPRODUCT_LANES
    }


def normalize_byproduct_grade_fields(value):
    defaults = default_byproduct_grade_fields()
    source = value if isinstance(value, Mapping) else {}
    normalized = {}
    for lane in BYPRODUCT_LANES:
        lane_source = source.get(lane) if isinstance(source.get(lane), Mapping) else {}
        normalized[lane] = {
            analyte: str(
                lane_source.get(analyte) or defaults[lane][analyte]
            ).strip().lower()
            for analyte in ANALYTES
        }
    return normalized


def normalized_build_lane(setting, byproducts_enabled=False):
    if not byproducts_enabled:
        return PRODUCT_LANE
    lane = str((setting or {}).get("byproduct") or "").strip().lower()
    return lane if lane in BYPRODUCT_LANES else ""


def active_build_indices(settings, states, byproducts_enabled=False):
    """Return the first incomplete build in each sequential build lane."""
    lanes = BYPRODUCT_LANES if byproducts_enabled else (PRODUCT_LANE,)
    active = {}
    for lane in lanes:
        for index, setting in enumerate(settings or []):
            if normalized_build_lane(setting, byproducts_enabled) != lane:
                continue
            state = states[index] if index < len(states or []) else {}
            target = float((setting or {}).get("target_tonnes") or 0.0)
            tonnes = float((state or {}).get("tonnes") or 0.0)
            if tonnes < target - 1e-6:
                active[lane] = index
                break
    return active


def lane_build_settings(settings: Iterable[Mapping], lane, byproducts_enabled=False):
    return [
        deepcopy(dict(setting))
        for setting in settings or []
        if normalized_build_lane(setting, byproducts_enabled) == lane
    ]


def lane_source_tonnes_column(lane):
    return (
        "product_build_source_tonnes"
        if lane == PRODUCT_LANE
        else f"product_build_{lane}_source_tonnes"
    )


def lane_actual_tonnes_column(lane):
    return (
        "product_build_actual_tonnes"
        if lane == PRODUCT_LANE
        else f"product_build_{lane}_actual_tonnes"
    )


def lane_grade_column(lane, analyte):
    return (
        f"source_grade_{analyte}"
        if lane == PRODUCT_LANE
        else f"product_build_{lane}_grade_{analyte}"
    )


def lane_grade_weight_column(lane, analyte):
    return (
        f"selected_grade_weight_{analyte}_tonnes"
        if lane == PRODUCT_LANE
        else f"product_build_{lane}_grade_weight_{analyte}_tonnes"
    )
