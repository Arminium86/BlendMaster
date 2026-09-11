from copy import deepcopy
from typing import Iterable, Mapping


PRODUCT_LANE = "product"
BYPRODUCT_LANES = ("lump", "fines")
ANALYTES = ("fe", "si", "al", "p", "mn")
GRADE_FIELD_SUFFIXES = {
    "fe": "fe",
    "si": "sio2",
    "al": "al2o3",
    "p": "p",
    "mn": "mn",
}


def default_byproduct_quantity_fields():
    return {
        "lump": "prod1_lump_wmt",
        "fines": "prod1_fines_wmt",
    }


def default_byproduct_grade_fields():
    return {
        lane: {
            analyte: f"prod1_{lane}_{GRADE_FIELD_SUFFIXES[analyte]}"
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
        normalized[lane] = {}
        for analyte in ANALYTES:
            field = str(
                lane_source.get(analyte) or defaults[lane][analyte]
            ).strip().lower()
            # Migrate the short chemistry suffixes emitted by the first
            # by-product implementation to the established source-field
            # convention used by Define Fields and Snowflake/APS mappings.
            legacy_default = f"prod1_{lane}_{analyte}"
            if analyte in {"si", "al"} and field == legacy_default:
                field = defaults[lane][analyte]
            normalized[lane][analyte] = field
    return normalized


def byproduct_grade_source_field(lane, analyte):
    return f"prod1_{lane}_{GRADE_FIELD_SUFFIXES[analyte]}"


def normalized_build_lane(setting, byproducts_enabled=False):
    lane = str((setting or {}).get("byproduct") or "").strip().lower() if byproducts_enabled else PRODUCT_LANE
    if byproducts_enabled and lane not in BYPRODUCT_LANES:
        return ""
    scope = (setting or {}).get("opf_scope")
    return f"{lane}@{scope}" if scope else lane


def lane_kind(lane):
    return str(lane).split("@", 1)[0]


def active_build_indices(settings, states, byproducts_enabled=False):
    """Return the first incomplete build in each sequential build lane."""
    lanes = list(dict.fromkeys(normalized_build_lane(s, byproducts_enabled) for s in settings or []))
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
