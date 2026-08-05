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
    "insitu": "insitu_<grades>",
    "modelled_rom": "modelled_rom_<grades>",
    "adjusted_rom": "adjusted_rom_<grades>",
    "modelled_product": "modelled_product_<grades>",
    "adjusted_product": "adjusted_product_<grades>",
}
DEFAULT_STREAM = "adjusted_product"
UNBRANDED = "*"
DEFAULT_PLANNING_CATEGORIES = {
    "rom": "OPF Feed",
    "product": "OPF Production",
}


def normalise_planning_categories(value: Any = None) -> Dict[str, str]:
    """Apply confirmed defaults while preserving configured overrides."""
    value = value if isinstance(value, Mapping) else {}
    return {
        key: str(value.get(key) or default).strip() or default
        for key, default in DEFAULT_PLANNING_CATEGORIES.items()
    }


def normalise_aps_grade_field_mappings(
    value: Any = None, brands: Iterable[str] = ()
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Return brand-keyed ROM/product APS header mappings.

    Projects created before brand-aware ROM mapping stored ``rom`` directly as
    an analyte/header dictionary. Replicate that mapping for every configured
    brand so those projects continue to produce the same grades.
    """
    value = value if isinstance(value, Mapping) else {}
    configured = configured_brands(brands)

    def analyte_fields(fields: Any) -> Dict[str, str]:
        fields = fields if isinstance(fields, Mapping) else {}
        return {
            analyte: str(fields.get(analyte) or "").strip()
            for analyte in ANALYTES
        }

    raw_rom = value.get("rom", {})
    raw_rom = raw_rom if isinstance(raw_rom, Mapping) else {}
    legacy_rom = any(analyte in raw_rom for analyte in ANALYTES)
    raw_product = value.get("product", {})
    raw_product = raw_product if isinstance(raw_product, Mapping) else {}

    if not configured:
        nested_brands = [
            normalise_brand(brand)
            for section in (raw_rom, raw_product)
            for brand, fields in section.items()
            if isinstance(fields, Mapping) and normalise_brand(brand) != UNBRANDED
        ]
        configured = list(dict.fromkeys(nested_brands)) or [UNBRANDED]

    rom = {}
    product = {}
    for brand in configured:
        rom_fields = raw_rom if legacy_rom else (
            raw_rom.get(brand) or raw_rom.get(brand.lower()) or {}
        )
        product_fields = (
            raw_product.get(brand) or raw_product.get(brand.lower()) or {}
        )
        rom[brand] = analyte_fields(rom_fields)
        product[brand] = analyte_fields(product_fields)
    return {"rom": rom, "product": product}


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
        names = (
            f"insitu_{analyte}", f"grade_{analyte}", analyte,
            f"GRADE_{analyte.upper()}",
        )
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


def grade_stream_vector(
    grade_streams: Any,
    stream: str,
    brand: Any = None,
    fallback: Any = None,
) -> Dict[str, Optional[float]]:
    """Return one raw stream vector without falling through other streams.

    Brand-specific values are preferred, while the unbranded vector remains
    valid for common streams such as inventory/AMT insitu and modelled ROM.
    This is intentionally different from :func:`resolve_grade_vector`, which
    performs the operational fallback chain used by the optimiser.
    """
    streams = normalise_grade_streams(grade_streams, fallback)
    if stream not in STREAMS:
        return grade_vector()
    requested_brand = normalise_brand(brand)
    brand_map = streams.get(stream, {}) or {}
    candidates = (
        (requested_brand, UNBRANDED)
        if requested_brand != UNBRANDED
        else (UNBRANDED,)
    )
    for candidate_brand in candidates:
        values = brand_map.get(candidate_brand)
        if isinstance(values, Mapping):
            return grade_vector(values)
    return grade_vector()


def grade_stream_audit_fields(
    grade_streams: Any,
    brand: Any = None,
    prefix: str = "source_grade_",
    fallback: Any = None,
) -> Dict[str, Optional[float]]:
    """Flatten all five raw vectors for a selected brand into report fields."""
    return {
        f"{prefix}{stream}_{analyte}": value
        for stream in STREAMS
        for analyte, value in grade_stream_vector(
            grade_streams, stream, brand, fallback
        ).items()
    }


def flatten_grade_streams(
    grade_streams: Any,
) -> Dict[str, Optional[float]]:
    """Flatten every stored stream/brand/analyte for SQLite audit tables."""
    streams = normalise_grade_streams(grade_streams)
    flattened: Dict[str, Optional[float]] = {}
    for stream in STREAMS:
        for brand, values in (streams.get(stream, {}) or {}).items():
            brand_suffix = ""
            if brand != UNBRANDED:
                safe_brand = re.sub(r"[^a-z0-9]+", "_", brand.lower()).strip("_")
                brand_suffix = f"_{safe_brand}" if safe_brand else ""
            for analyte, value in grade_vector(values).items():
                flattened[f"grade_{stream}{brand_suffix}_{analyte}"] = value
    return flattened


def format_grade_stream_vector(
    grade_streams: Any,
    stream: str,
    brand: Any = None,
) -> str:
    """Return a compact five-analyte vector for inventory/AMT UI tables."""
    values = grade_stream_vector(grade_streams, stream, brand)
    labels = {
        "fe": "Fe",
        "si": "SiO₂",
        "al": "Al₂O₃",
        "p": "P",
        "mn": "Mn",
    }
    parts = []
    for analyte in ANALYTES:
        value = values.get(analyte)
        rendered = "—" if value is None else f"{value:.2f}"
        parts.append(f"{labels[analyte]} {rendered}")
    return " | ".join(parts)


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


def amt_modelled_product_slot(opf: Any) -> Optional[str]:
    """Return the EXPIT modelled-product slot applicable to an AMT source.

    EXPIT exposes only PROD1 and PROD2.  This differs from the inventory table,
    where CC OPF02's canonical Product2 is physically stored in PROD3.
    """
    opf_key = normalise_opf(opf)
    if opf_key in {"CB_OPF", "CC_OPF01"}:
        return "prod1"
    if opf_key in {"CC_OPF02", "VK_OPF"}:
        return "prod2"
    return None


def inventory_product_property_aliases(
    row: Mapping[str, Any], opf: Any
) -> Dict[str, Any]:
    """Return canonical physical-property aliases for an inventory source.

    Inventory stores CC OPF02's applicable product in ``PROD3`` while EXPIT
    and AMT use ``PROD2`` for that same logical product channel.  Custom
    constraints must see one stable name across source modes, so every raw
    PROD3 property is also projected onto its PROD2 spelling for CC OPF02.
    The raw fields are retained for audit.  Common moisture/recovery aliases
    are added for every confirmed inventory product slot.
    """
    row = row if isinstance(row, Mapping) else {}
    raw_slot = internal_product_slot(opf)
    canonical_slot = amt_modelled_product_slot(opf)
    aliases: Dict[str, Any] = {}

    if raw_slot and canonical_slot and raw_slot != canonical_slot:
        token = re.compile(
            rf"(?<![a-z0-9]){re.escape(raw_slot)}(?![a-z0-9])",
            re.IGNORECASE,
        )
        for raw_name, value in row.items():
            name = str(raw_name or "").strip().lower()
            canonical_name = token.sub(canonical_slot, name)
            if canonical_name and canonical_name != name:
                aliases[canonical_name] = value

    if raw_slot and canonical_slot:
        mass_recovery = _row_value(
            row,
            f"{raw_slot}_mass_recovery",
            f"dryyield_{raw_slot}_wtavg",
            f"dryyield_{raw_slot}",
        )
        moisture = _row_value(
            row,
            f"{raw_slot}_moisture",
            f"moisture_{raw_slot}_wtavg",
            f"moisture_{raw_slot}",
        )
        if mass_recovery is not None:
            aliases[f"{canonical_slot}_mass_recovery"] = mass_recovery
        if moisture is not None:
            aliases[f"{canonical_slot}_moisture"] = moisture

        minus_1mm = _row_value(
            row,
            f"{raw_slot}_minus_1mm_pct",
            f"{raw_slot}_minus1mm_pct",
            f"{raw_slot}_mudrush_ultrafines_1mm",
            "minus_1mm_pct",
            "prod1_minus_1mm_pct",
        )
        if minus_1mm is not None:
            aliases[f"{canonical_slot}_minus_1mm_pct"] = minus_1mm
            fraction = minus_1mm / 100.0 if minus_1mm > 1.0 else minus_1mm
            if 0.0 <= fraction <= 1.0:
                for mass_basis in ("wmt", "dmt"):
                    product_tonnes = numeric(aliases.get(
                        f"{canonical_slot}_{mass_basis}"
                    ))
                    if product_tonnes is None:
                        product_tonnes = _row_value(
                            row, f"{raw_slot}_{mass_basis}"
                        )
                    if product_tonnes is not None:
                        aliases[
                            f"{canonical_slot}_minus_1mm_{mass_basis}"
                        ] = product_tonnes * fraction

    feed_moisture = _row_value(
        row, "feed_moisture", "moisture_insitu_wtavg"
    )
    if feed_moisture is not None:
        aliases["feed_moisture"] = feed_moisture
    return aliases


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

    Inventory insitu is the modelled ROM baseline, matching the AMT stream
    convention. Historical blend adjusts ROM; historical regression adjusts
    the imported inventory product. Blend is deliberately not applied to
    inventory product.
    """
    brands = configured_brands(brands) or [UNBRANDED]
    insitu = {a: _row_value(row, f"grade_{a}", f"{a}_insitu", f"insitu_{a}") for a in ANALYTES}
    modelled_rom = {
        a: _row_value(row, f"modelled_rom_{a}") for a in ANALYTES
    }
    slot = internal_product_slot(opf)
    product = {
        a: _row_value(row, f"{slot}_{a}", f"{a}_{slot}", f"grade_{slot}_{a}") if slot else None
        for a in ANALYTES
    }
    # Preserve legacy data when new Snowflake fields have not been populated.
    for a in ANALYTES:
        if insitu[a] is None:
            insitu[a] = legacy_vector(row)[a]
        if modelled_rom[a] is None:
            modelled_rom[a] = insitu[a]
        mapped_product = _row_value(row, f"modelled_product_{a}")
        if mapped_product is not None:
            product[a] = mapped_product

    result = empty_streams()
    result["insitu"][UNBRANDED] = insitu
    result["modelled_rom"][UNBRANDED] = copy.deepcopy(modelled_rom)
    for brand in brands:
        blend = _factor_vector(historical_factors, brand, "blend")
        regression = _factor_vector(historical_factors, brand, "regression")
        # Stockpile ROM is physically unbranded, but expose an identical
        # brand-keyed modelled stream so Database View and downstream audit
        # schemas match APS sources.  Historical blend reconciliation remains
        # confined to adjusted_rom below.
        result["modelled_rom"][brand] = copy.deepcopy(modelled_rom)
        adjusted_rom = {}
        for a in ANALYTES:
            adjusted_rom[a] = (
                modelled_rom[a] * blend[a]
                if modelled_rom[a] is not None else None
            )
        result["adjusted_rom"][brand] = adjusted_rom
        if is_dry_plant(opf) or slot is None:
            result["modelled_product"][brand] = copy.deepcopy(adjusted_rom)
            result["adjusted_product"][brand] = copy.deepcopy(adjusted_rom)
        else:
            result["modelled_product"][brand] = copy.deepcopy(product)
            result["adjusted_product"][brand] = {
                a: (
                    product[a] * regression[a]
                    if product[a] is not None else None
                )
                for a in ANALYTES
            }
    return result


def amt_grade_streams(
    insitu_source: Any,
    lineage_source: Mapping[str, Any],
    brands: Iterable[str],
    historical_factors: Any,
    opf: Any,
):
    """Build AMT streams from insitu grades and grade-block lineage products.

    ``lineage_source`` is expected to carry ``MODELLED_PROD1_<analyte>`` and/or
    ``MODELLED_PROD2_<analyte>`` fields calculated from the grade blocks that
    remain in the hex.  Legacy PROD1/PROD2 field spellings remain accepted so
    saved projects can still be interpreted, but no inventory-derived blend or
    upgrade factor is calculated here.
    """
    brands = configured_brands(brands) or [UNBRANDED]
    insitu = legacy_vector(insitu_source)
    slot = amt_modelled_product_slot(opf)
    lineage_source = lineage_source if isinstance(lineage_source, Mapping) else {}

    def nested_product_value(analyte: str) -> Optional[float]:
        if not slot:
            return None
        source_analyte = {"si": "sio2", "al": "al2o3"}.get(
            analyte, analyte
        )
        for key in (
            f"modelled_{slot}",
            f"MODELLED_{slot.upper()}",
            slot,
            slot.upper(),
        ):
            values = lineage_source.get(key)
            if isinstance(values, Mapping):
                value = _row_value(
                    values,
                    source_analyte,
                    analyte,
                    f"grade_{source_analyte}",
                    f"grade_{analyte}",
                )
                if value is not None:
                    return value
        return _row_value(
            lineage_source,
            f"modelled_{slot}_{source_analyte}",
            f"modelled_{slot}_{analyte}",
            f"{slot}_{source_analyte}",
            f"{slot}_{analyte}",
            f"{source_analyte}_{slot}",
            f"{analyte}_{slot}",
            f"grade_{slot}_{source_analyte}",
            f"grade_{slot}_{analyte}",
        )

    modelled_rom = {
        analyte: _row_value(lineage_source, f"modelled_rom_{analyte}")
        for analyte in ANALYTES
    }
    for analyte in ANALYTES:
        if modelled_rom[analyte] is None:
            modelled_rom[analyte] = insitu[analyte]
    modelled_product = {}
    for analyte in ANALYTES:
        mapped = _row_value(lineage_source, f"modelled_product_{analyte}")
        modelled_product[analyte] = (
            mapped if mapped is not None else nested_product_value(analyte)
        )
    result = empty_streams()
    result["insitu"][UNBRANDED] = insitu
    # Historical blend reconciliation now forms the only AMT ROM adjustment.
    # Keeping modelled ROM equal to insitu preserves the five-stream audit
    # boundary while removing the obsolete inventory ROM/insitu workaround.
    result["modelled_rom"][UNBRANDED] = copy.deepcopy(modelled_rom)
    for brand in brands:
        blend = _factor_vector(historical_factors, brand, "blend")
        regression = _factor_vector(historical_factors, brand, "regression")
        # AMT ROM is also unbranded at source.  Preserve that source vector and
        # publish equal brand-keyed copies for a consistent cross-source audit
        # schema; only adjusted_rom receives the brand-specific blend factor.
        result["modelled_rom"][brand] = copy.deepcopy(modelled_rom)
        adjusted_rom = {}
        for a in ANALYTES:
            adjusted_rom[a] = (
                modelled_rom[a] * blend[a]
                if modelled_rom[a] is not None else None
            )
        result["adjusted_rom"][brand] = adjusted_rom
        if is_dry_plant(opf):
            result["modelled_product"][brand] = copy.deepcopy(adjusted_rom)
            result["adjusted_product"][brand] = copy.deepcopy(adjusted_rom)
        elif slot is None:
            # IB and any unconfirmed OPF retain an empty product vector so the
            # normal per-analyte stream resolver explicitly falls back to ROM.
            result["modelled_product"][brand] = grade_vector()
            result["adjusted_product"][brand] = grade_vector()
        else:
            result["modelled_product"][brand] = copy.deepcopy(modelled_product)
            result["adjusted_product"][brand] = {
                a: (
                    modelled_product[a] * regression[a]
                    if modelled_product[a] is not None else None
                )
                for a in ANALYTES
            }
    return result


def aps_grade_streams(
    row: Mapping[str, Any], mappings: Any, brands: Iterable[str]
):
    """Build authoritative APS ROM/product streams from exact user mappings."""
    brands = configured_brands(brands) or [UNBRANDED]
    mappings = normalise_aps_grade_field_mappings(mappings, brands)
    rom_mapping = mappings["rom"]
    product_mapping = mappings["product"]
    insitu = legacy_vector(row)
    result = empty_streams()
    result["insitu"][UNBRANDED] = insitu
    for brand in brands:
        rom_fields = rom_mapping.get(brand, {})
        rom = {
            analyte: _row_value(row, str(rom_fields.get(analyte, "")))
            for analyte in ANALYTES
        }
        for analyte in ANALYTES:
            if rom[analyte] is None:
                rom[analyte] = insitu[analyte]
        result["modelled_rom"][brand] = copy.deepcopy(rom)
        result["adjusted_rom"][brand] = copy.deepcopy(rom)

        product_fields = product_mapping.get(brand, {})
        product = {
            analyte: _row_value(
                row, str(product_fields.get(analyte, ""))
            )
            for analyte in ANALYTES
        }
        result["modelled_product"][brand] = copy.deepcopy(product)
        result["adjusted_product"][brand] = copy.deepcopy(product)
    return result


def weighted_merge_grade_streams(
    current: Any,
    current_tonnes: Any,
    incoming: Any,
    incoming_tonnes: Any,
    current_properties: Any = None,
    incoming_properties: Any = None,
    property_weights: Any = None,
):
    left = normalise_grade_streams(current)
    right = normalise_grade_streams(incoming)
    left_tonnes = max(numeric(current_tonnes) or 0.0, 0.0)
    right_tonnes = max(numeric(incoming_tonnes) or 0.0, 0.0)
    left_properties = (
        current_properties if isinstance(current_properties, Mapping) else {}
    )
    right_properties = (
        incoming_properties if isinstance(incoming_properties, Mapping) else {}
    )
    weight_fields = {
        str(name).strip().lower(): str(weight).strip().lower()
        for name, weight in dict(property_weights or {}).items()
        if str(name).strip() and str(weight).strip()
    }
    result = empty_streams()
    for stream in STREAMS:
        brands = set(left.get(stream, {})) | set(right.get(stream, {}))
        for brand in brands:
            vector: Dict[str, Optional[float]] = {}
            for analyte in ANALYTES:
                old = numeric((left.get(stream, {}).get(brand) or {}).get(analyte))
                new = numeric((right.get(stream, {}).get(brand) or {}).get(analyte))
                weight_field = weight_fields.get(f"{stream}_{analyte}")
                if weight_field:
                    old_weight = numeric(left_properties.get(weight_field))
                    new_weight = numeric(right_properties.get(weight_field))
                    if (
                        (left_tonnes > 0 and old_weight is None)
                        or (right_tonnes > 0 and new_weight is None)
                    ):
                        vector[analyte] = None
                        continue
                    old_weight = max(old_weight or 0.0, 0.0)
                    new_weight = max(new_weight or 0.0, 0.0)
                    # A partially covered grade stream must be averaged over
                    # the mass that actually has that grade.  Product mass can
                    # legitimately exist where grade-block lineage did not
                    # supply a product assay; treating that uncovered mass as
                    # fatal erased both modelled and adjusted streams for the
                    # entire AMT chunk.  Exclude only the uncovered side here.
                    # The independent coverage fields continue to quantify and
                    # warn about the excluded mass.
                    if old_weight > 0 and old is None:
                        old_weight = 0.0
                    if new_weight > 0 and new is None:
                        new_weight = 0.0
                    total_weight = old_weight + new_weight
                    vector[analyte] = (
                        (old or 0.0) * old_weight + (new or 0.0) * new_weight
                    ) / total_weight if total_weight > 0 else None
                elif old is None:
                    vector[analyte] = new
                elif new is None:
                    vector[analyte] = old
                elif left_tonnes + right_tonnes > 0:
                    vector[analyte] = (old * left_tonnes + new * right_tonnes) / (left_tonnes + right_tonnes)
                else:
                    vector[analyte] = old
            result[stream][brand] = vector
    return result


def reweight_grade_streams_from_properties(streams: Any, properties: Any):
    """Overlay canonical weighted stream fields while preserving brand factors.

    Canonical modelled fields may use a product-mass basis that differs from
    source WMT.  After source properties have been aggregated with their
    declared weights, this helper makes those values authoritative for the
    selected grade streams and reapplies the already-calculated per-brand
    adjustment ratio.
    """
    result = normalise_grade_streams(streams)
    properties = properties if isinstance(properties, Mapping) else {}
    values = {str(key).strip().lower(): value for key, value in properties.items()}
    for analyte in ANALYTES:
        insitu_value = numeric(values.get(f"insitu_{analyte}"))
        if insitu_value is not None:
            result.setdefault("insitu", {}).setdefault(
                UNBRANDED, {}
            )[analyte] = insitu_value
        rom_value = numeric(values.get(f"modelled_rom_{analyte}"))
        if rom_value is not None:
            old_rom_by_brand = {
                brand: dict(grades or {})
                for brand, grades in result.get("modelled_rom", {}).items()
            }
            old_unbranded = old_rom_by_brand.get(UNBRANDED, {})
            result.setdefault("modelled_rom", {}).setdefault(
                UNBRANDED, {}
            )[analyte] = rom_value
            for brand, adjusted in result.get("adjusted_rom", {}).items():
                old_model = numeric(
                    (old_rom_by_brand.get(brand) or old_unbranded).get(analyte)
                )
                old_adjusted = numeric(adjusted.get(analyte))
                if old_model not in (None, 0.0) and old_adjusted is not None:
                    adjusted[analyte] = rom_value * old_adjusted / old_model

        product_value = numeric(values.get(f"modelled_product_{analyte}"))
        if product_value is None:
            continue
        product_brands = set(result.get("modelled_product", {})) | set(
            result.get("adjusted_product", {})
        )
        for brand in product_brands or {UNBRANDED}:
            modelled = result.setdefault("modelled_product", {}).setdefault(
                brand, {}
            )
            adjusted = result.setdefault("adjusted_product", {}).setdefault(
                brand, {}
            )
            old_model = numeric(modelled.get(analyte))
            old_adjusted = numeric(adjusted.get(analyte))
            modelled[analyte] = product_value
            if old_model not in (None, 0.0) and old_adjusted is not None:
                adjusted[analyte] = product_value * old_adjusted / old_model
    return result


def grade_streams_to_legacy(streams: Any, selected_stream: str, brand: Any = None):
    return {f"grade_{a}": v for a, v in resolve_grade_vector(streams, selected_stream, brand)[0].items()}
