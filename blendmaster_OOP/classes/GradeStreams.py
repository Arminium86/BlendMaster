"""Grade stream calculation and selection helpers.

BlendMaster historically exposed a single grade vector.  This module keeps the
five supported analytes in every available stream and only projects the stream
selected for a solve back onto the legacy ``grade_*`` attributes.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple


ANALYTES = ("fe", "si", "al", "p", "mn")
STREAMS = (
    "insitu",
    "modelled_rom",
    "adjusted_rom",
    "modelled_product",
    "adjusted_product",
)
STREAM_LABELS = {
    "insitu": "Insitu",
    "modelled_rom": "Modelled ROM",
    "adjusted_rom": "Adjusted ROM",
    "modelled_product": "Modelled Product",
    "adjusted_product": "Adjusted Product",
}
DEFAULT_STREAM = "adjusted_product"
UNBRANDED = "*"


def normalise_brand(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text or UNBRANDED


def configured_brands(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif value is None:
        values = []
    else:
        values = list(value)
    result: list[str] = []
    for item in values:
        brand = normalise_brand(item)
        if brand != UNBRANDED and brand not in result:
            result.append(brand)
    return result


def numeric(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def factor(value: Any) -> float:
    result = numeric(value)
    return result if result is not None and result > 0 else 1.0


def grade_vector(values: Optional[Mapping[str, Any]] = None) -> Dict[str, Optional[float]]:
    values = values or {}
    return {analyte: numeric(values.get(analyte)) for analyte in ANALYTES}


def legacy_vector(source: Any) -> Dict[str, Optional[float]]:
    def read(analyte: str) -> Any:
        names = (f"grade_{analyte}", analyte, f"GRADE_{analyte.upper()}")
        if isinstance(source, Mapping):
            for name in names:
                if name in source:
                    return source.get(name)
            return None
        for name in names:
            if hasattr(source, name):
                return getattr(source, name)
        return None

    return {analyte: numeric(read(analyte)) for analyte in ANALYTES}


def empty_streams() -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    return {stream: {} for stream in STREAMS}


def legacy_grade_streams(source: Any) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    """Represent a legacy grade as every stream so older projects still solve."""
    vector = legacy_vector(source)
    return {stream: {UNBRANDED: copy.deepcopy(vector)} for stream in STREAMS}


def normalise_grade_streams(value: Any, fallback: Any = None):
    if not isinstance(value, Mapping):
        return legacy_grade_streams(fallback or {})
    result = empty_streams()
    for stream in STREAMS:
        brand_map = value.get(stream)
        if not isinstance(brand_map, Mapping):
            continue
        # Accept the compact {stream: {fe: ...}} representation too.
        if any(analyte in brand_map for analyte in ANALYTES):
            result[stream][UNBRANDED] = grade_vector(brand_map)
            continue
        for brand, grades in brand_map.items():
            if isinstance(grades, Mapping):
                result[stream][normalise_brand(brand)] = grade_vector(grades)
    if not any(result[stream] for stream in STREAMS):
        return legacy_grade_streams(fallback or {})
    return result


FALLBACK_ORDER = {
    "adjusted_product": ("adjusted_product", "modelled_product", "adjusted_rom", "modelled_rom", "insitu"),
    "modelled_product": ("modelled_product", "adjusted_rom", "modelled_rom", "insitu"),
    "adjusted_rom": ("adjusted_rom", "modelled_rom", "insitu"),
    "modelled_rom": ("modelled_rom", "insitu"),
    "insitu": ("insitu",),
}


def resolve_grade_vector(
    grade_streams: Any,
    selected_stream: str = DEFAULT_STREAM,
    brand: Any = None,
    fallback: Any = None,
) -> Tuple[Dict[str, float], list[dict]]:
    """Resolve grades independently per analyte, returning fallback provenance."""
    streams = normalise_grade_streams(grade_streams, fallback)
    selected_stream = selected_stream if selected_stream in STREAMS else DEFAULT_STREAM
    requested_brand = normalise_brand(brand)
    resolved: Dict[str, float] = {}
    warnings: list[dict] = []
    for analyte in ANALYTES:
        chosen = None
        chosen_stream = None
        chosen_brand = None
        for stream in FALLBACK_ORDER[selected_stream]:
            brand_map = streams.get(stream, {})
            brand_order = (requested_brand, UNBRANDED) if requested_brand != UNBRANDED else (UNBRANDED,)
            for candidate_brand in brand_order:
                candidate = numeric((brand_map.get(candidate_brand) or {}).get(analyte))
                if candidate is not None:
                    chosen = candidate
                    chosen_stream = stream
                    chosen_brand = candidate_brand
                    break
            if chosen is not None:
                break
        if chosen is None:
            chosen = numeric(legacy_vector(fallback or {}).get(analyte)) or 0.0
            chosen_stream = "legacy"
            chosen_brand = UNBRANDED
        resolved[analyte] = chosen
        if chosen_stream != selected_stream or (
            requested_brand != UNBRANDED and chosen_brand != requested_brand
        ):
            warnings.append({
                "analyte": analyte,
                "requested_stream": selected_stream,
                "used_stream": chosen_stream,
                "requested_brand": requested_brand,
                "used_brand": chosen_brand,
            })
    return resolved, warnings


def apply_selected_stream(target: Any, selected_stream: str, brand: Any = None) -> list[dict]:
    streams = getattr(target, "grade_streams", None)
    vector, warnings = resolve_grade_vector(streams, selected_stream, brand, target)
    for analyte, value in vector.items():
        setattr(target, f"grade_{analyte}", value)
    setattr(target, "selected_grade_stream", selected_stream)
    setattr(target, "selected_grade_brand", normalise_brand(brand))
    setattr(target, "grade_stream_warnings", warnings)
    return warnings


def historical_factor(
    registry: Any, brand: Any, factor_type: str, analyte: str
) -> float:
    if not isinstance(registry, Mapping):
        return 1.0
    brand_key = normalise_brand(brand)
    record = registry.get(brand_key) or registry.get(UNBRANDED) or {}
    factor_map = record.get(factor_type, {}) if isinstance(record, Mapping) else {}
    if isinstance(factor_map, Mapping):
        item = factor_map.get(analyte, 1.0)
        if isinstance(item, Mapping):
            item = item.get("effective", item.get("calculated", 1.0))
        return factor(item)
    return 1.0


def _row_value(row: Mapping[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name in row:
            value = numeric(row.get(name))
            if value is not None:
                return value
        upper = name.upper()
        if upper in row:
            value = numeric(row.get(upper))
            if value is not None:
                return value
    return None


def normalise_opf(value: Any) -> str:
    text = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if text.startswith("CC") and "02" in text:
        return "CC_OPF02"
    if text.startswith("CC"):
        return "CC_OPF01"
    if text.startswith("CB"):
        return "CB_OPF"
    if text.startswith(("VK", "KV")):
        return "VK_OPF"
    if text.startswith("EW"):
        return "EW_OPF"
    if text.startswith("FT"):
        return "FT_OPF"
    if text.startswith("IB"):
        return "IB_OPF"
    return str(value or "").strip().upper().replace(" ", "_")


def internal_product_slot(opf: Any) -> Optional[str]:
    """Return raw inventory slot; CC OPF02 PROD3 becomes canonical Product2."""
    opf_key = normalise_opf(opf)
    if opf_key in {"CB_OPF", "CC_OPF01"}:
        return "prod1"
    if opf_key in {"CC_OPF02", "VK_OPF"}:
        return "prod3" if opf_key == "CC_OPF02" else "prod2"
    return None


def canonical_product_channel(opf: Any) -> Optional[str]:
    slot = internal_product_slot(opf)
    if slot == "prod1":
        return "product1"
    if slot in {"prod2", "prod3"}:
        return "product2"
    return None


def is_dry_plant(opf: Any) -> bool:
    return normalise_opf(opf) in {"EW_OPF", "FT_OPF"}


def _factor_vector(registry: Any, brand: str, factor_type: str) -> Dict[str, float]:
    return {a: historical_factor(registry, brand, factor_type, a) for a in ANALYTES}


def inventory_grade_streams(
    row: Mapping[str, Any],
    brands: Iterable[str],
    historical_factors: Any,
    opf: Any,
):
    """Build all streams for an inventory stockpile.

    Inventory ROM and product are already modelled.  Historical blend adjusts
    ROM; historical regression adjusts product.  Blend is deliberately not
    applied to inventory product.
    """
    brands = configured_brands(brands) or [UNBRANDED]
    insitu = {a: _row_value(row, f"grade_{a}", f"{a}_insitu", f"insitu_{a}") for a in ANALYTES}
    rom = {a: _row_value(row, f"rom_{a}", f"{a}_rom", f"grade_rom_{a}") for a in ANALYTES}
    slot = internal_product_slot(opf)
    product = {
        a: _row_value(row, f"{slot}_{a}", f"{a}_{slot}", f"grade_{slot}_{a}") if slot else None
        for a in ANALYTES
    }
    # Preserve legacy data when new Snowflake fields have not been populated.
    for a in ANALYTES:
        if insitu[a] is None:
            insitu[a] = legacy_vector(row)[a]
        if rom[a] is None:
            rom[a] = insitu[a]

    result = empty_streams()
    result["insitu"][UNBRANDED] = insitu
    result["modelled_rom"][UNBRANDED] = rom
    for brand in brands:
        blend = _factor_vector(historical_factors, brand, "blend")
        regression = _factor_vector(historical_factors, brand, "regression")
        adjusted_rom = {a: (rom[a] * blend[a] if rom[a] is not None else None) for a in ANALYTES}
        result["adjusted_rom"][brand] = adjusted_rom
        if is_dry_plant(opf) or slot is None:
            result["modelled_product"][brand] = copy.deepcopy(adjusted_rom)
            result["adjusted_product"][brand] = copy.deepcopy(adjusted_rom)
        else:
            result["modelled_product"][brand] = copy.deepcopy(product)
            result["adjusted_product"][brand] = {
                a: (product[a] * regression[a] if product[a] is not None else None)
                for a in ANALYTES
            }
    return result


def amt_grade_streams(
    insitu_source: Any,
    inventory_row: Mapping[str, Any],
    brands: Iterable[str],
    historical_factors: Any,
    opf: Any,
):
    """Build AMT streams using the matching inventory build's internal factors."""
    brands = configured_brands(brands) or [UNBRANDED]
    insitu = legacy_vector(insitu_source)
    inventory_insitu = {
        a: _row_value(inventory_row, f"grade_{a}", f"{a}_insitu", f"insitu_{a}")
        for a in ANALYTES
    }
    inventory_rom = {
        a: _row_value(inventory_row, f"rom_{a}", f"{a}_rom", f"grade_rom_{a}")
        for a in ANALYTES
    }
    slot = internal_product_slot(opf)
    inventory_product = {
        a: _row_value(inventory_row, f"{slot}_{a}", f"{a}_{slot}", f"grade_{slot}_{a}") if slot else None
        for a in ANALYTES
    }
    internal_blend = {
        a: factor(inventory_rom[a] / inventory_insitu[a])
        if inventory_rom[a] is not None and inventory_insitu[a] not in {None, 0}
        else 1.0
        for a in ANALYTES
    }
    internal_upgrade = {
        a: factor(inventory_product[a] / inventory_rom[a])
        if inventory_product[a] is not None and inventory_rom[a] not in {None, 0}
        else 1.0
        for a in ANALYTES
    }
    modelled_rom = {
        a: (insitu[a] * internal_blend[a] if insitu[a] is not None else None)
        for a in ANALYTES
    }
    result = empty_streams()
    result["insitu"][UNBRANDED] = insitu
    result["modelled_rom"][UNBRANDED] = modelled_rom
    for brand in brands:
        blend = _factor_vector(historical_factors, brand, "blend")
        regression = _factor_vector(historical_factors, brand, "regression")
        adjusted_rom = {
            a: (modelled_rom[a] * blend[a] if modelled_rom[a] is not None else None)
            for a in ANALYTES
        }
        result["adjusted_rom"][brand] = adjusted_rom
        if is_dry_plant(opf) or slot is None:
            result["modelled_product"][brand] = copy.deepcopy(adjusted_rom)
            result["adjusted_product"][brand] = copy.deepcopy(adjusted_rom)
        else:
            modelled_product = {
                a: (adjusted_rom[a] * internal_upgrade[a] if adjusted_rom[a] is not None else None)
                for a in ANALYTES
            }
            result["modelled_product"][brand] = modelled_product
            result["adjusted_product"][brand] = {
                a: (modelled_product[a] * regression[a] if modelled_product[a] is not None else None)
                for a in ANALYTES
            }
    return result


def aps_grade_streams(
    row: Mapping[str, Any], mappings: Any, brands: Iterable[str]
):
    """Build authoritative APS ROM/product streams from exact user mappings."""
    mappings = mappings if isinstance(mappings, Mapping) else {}
    rom_mapping = mappings.get("rom", {}) if isinstance(mappings.get("rom", {}), Mapping) else {}
    product_mapping = mappings.get("product", {}) if isinstance(mappings.get("product", {}), Mapping) else {}
    insitu = legacy_vector(row)
    rom = {a: _row_value(row, str(rom_mapping.get(a, ""))) for a in ANALYTES}
    for a in ANALYTES:
        if rom[a] is None:
            rom[a] = insitu[a]
    result = empty_streams()
    result["insitu"][UNBRANDED] = insitu
    result["modelled_rom"][UNBRANDED] = copy.deepcopy(rom)
    result["adjusted_rom"][UNBRANDED] = copy.deepcopy(rom)
    for brand in configured_brands(brands):
        fields = product_mapping.get(brand, product_mapping.get(brand.lower(), {}))
        fields = fields if isinstance(fields, Mapping) else {}
        product = {a: _row_value(row, str(fields.get(a, ""))) for a in ANALYTES}
        result["modelled_product"][brand] = copy.deepcopy(product)
        result["adjusted_product"][brand] = copy.deepcopy(product)
    return result


def weighted_merge_grade_streams(
    current: Any, current_tonnes: Any, incoming: Any, incoming_tonnes: Any
):
    left = normalise_grade_streams(current)
    right = normalise_grade_streams(incoming)
    left_tonnes = max(numeric(current_tonnes) or 0.0, 0.0)
    right_tonnes = max(numeric(incoming_tonnes) or 0.0, 0.0)
    result = empty_streams()
    for stream in STREAMS:
        brands = set(left.get(stream, {})) | set(right.get(stream, {}))
        for brand in brands:
            vector: Dict[str, Optional[float]] = {}
            for analyte in ANALYTES:
                old = numeric((left.get(stream, {}).get(brand) or {}).get(analyte))
                new = numeric((right.get(stream, {}).get(brand) or {}).get(analyte))
                if old is None:
                    vector[analyte] = new
                elif new is None:
                    vector[analyte] = old
                elif left_tonnes + right_tonnes > 0:
                    vector[analyte] = (old * left_tonnes + new * right_tonnes) / (left_tonnes + right_tonnes)
                else:
                    vector[analyte] = old
            result[stream][brand] = vector
    return result


def grade_streams_to_legacy(streams: Any, selected_stream: str, brand: Any = None):
    return {f"grade_{a}": v for a, v in resolve_grade_vector(streams, selected_stream, brand)[0].items()}

