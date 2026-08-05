"""User-defined canonical source fields and source-specific mappings.

The registry is the contract between raw Inventory/AMT/APS columns and the
stable names used by Database View, custom constraints, optimisation, and
reports.  A definition describes mass-balance behaviour; a mapping says which
raw field supplies it for one source family (and, for APS, optionally a brand).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping

from classes.CustomConstraints import canonical_property_key, source_property_kind
from classes.GradeStreams import ANALYTES, UNBRANDED, normalise_brand
from classes.SourcePropertyMappings import APS_SOURCE_PROPERTY_CATALOGUE


FIELD_KINDS = ("additive", "weighted_average")
SOURCE_FAMILIES = ("inventory", "amt", "aps")
STREAM_PREFIXES = (
    "modelled_rom",
    "adjusted_rom",
    "modelled_product",
    "adjusted_product",
)
CALCULATED_STREAM_PREFIXES = ("adjusted_rom", "adjusted_product")


def is_calculated_stream_field(value) -> bool:
    name = canonical_property_key(value)
    return any(name.startswith(f"{prefix}_") for prefix in CALCULATED_STREAM_PREFIXES)


def _definition(
    name,
    kind,
    weight_field="",
    *,
    required=False,
    use_in_optimisation=False,
    description="",
):
    return {
        "name": canonical_property_key(name),
        "kind": str(kind),
        "weight_field": canonical_property_key(weight_field),
        "required": bool(required),
        "use_in_optimisation": bool(use_in_optimisation),
        "description": str(description or ""),
    }


def default_field_definitions():
    """Return the editable default schema in valid dependency order."""
    rows = [
        _definition(
            "source_wmt", "additive", required=True,
            use_in_optimisation=True,
            description="Opening/source wet tonnes. Mirrors modelled ROM WMT when only one is mapped.",
        ),
        _definition(
            "modelled_rom_wmt", "additive", required=True,
            use_in_optimisation=True,
            description="Modelled ROM wet tonnes (the opening insitu/ROM balance).",
        ),
        _definition(
            "modelled_rom_dmt", "additive", required=True,
            use_in_optimisation=True,
            description="Modelled ROM dry tonnes (the opening insitu/ROM dry balance).",
        ),
        _definition(
            "modelled_product_wmt", "additive", required=True,
            description="Modelled product wet tonnes for the active OPF product stream.",
        ),
        _definition(
            "modelled_product_dmt", "additive", required=True,
            use_in_optimisation=True,
            description="Modelled product dry tonnes used to weight product grades.",
        ),
    ]

    # Retain the useful physical/additive catalogue introduced before the
    # configurable registry.  It is optional and removable, but immediately
    # makes ore type, product mass, and ultrafines available for mapping.
    known = {row["name"] for row in rows}
    deferred_weighted = []
    for _group, raw_name, description in APS_SOURCE_PROPERTY_CATALOGUE:
        name = canonical_property_key(raw_name)
        if not name or name in known:
            continue
        kind = source_property_kind(name)
        if kind == "additive":
            rows.append(_definition(name, "additive", description=description))
            known.add(name)
        elif kind in {"intensive", "unknown"}:
            deferred_weighted.append((name, description))

    for stream in ("insitu", *STREAM_PREFIXES):
        weight = (
            "modelled_product_dmt"
            if "product" in stream else "modelled_rom_wmt"
        )
        for analyte in ANALYTES:
            rows.append(_definition(
                f"{stream}_{analyte}",
                "weighted_average",
                weight,
                required=True,
                description=f"{stream.replace('_', ' ').title()} {analyte.upper()} grade.",
            ))
            known.add(f"{stream}_{analyte}")

    def default_weight(name):
        if name.startswith("prod1_lump_") and "prod1_lump_dmt" in known:
            return "prod1_lump_dmt"
        if name.startswith("prod1_fines_") and "prod1_fines_dmt" in known:
            return "prod1_fines_dmt"
        for product in (1, 2, 3):
            if name.startswith(f"prod{product}_"):
                candidate = f"prod{product}_dmt"
                if candidate in known:
                    return candidate
        return "source_wmt"

    for name, description in deferred_weighted:
        if name in known:
            continue
        rows.append(_definition(
            name, "weighted_average", default_weight(name),
            description=description,
        ))
        known.add(name)
    return rows


def mandatory_field_names():
    return {
        row["name"] for row in default_field_definitions() if row["required"]
    }


def normalize_field_definitions(values=None):
    """Validate/migrate definitions and restore missing mandatory rows."""
    defaults = default_field_definitions()
    default_by_name = {row["name"]: row for row in defaults}
    values = values if isinstance(values, (list, tuple)) else []
    normalized = []
    seen = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        name = canonical_property_key(raw.get("name"))
        if not name or name in seen:
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in FIELD_KINDS:
            kind = (
                "additive" if source_property_kind(name) == "additive"
                else "weighted_average"
            )
        template = default_by_name.get(name, {})
        required = bool(template.get("required", raw.get("required", False)))
        weight = canonical_property_key(
            raw.get("weight_field") or template.get("weight_field")
        )
        normalized.append(_definition(
            name,
            kind,
            weight,
            required=required,
            use_in_optimisation=raw.get("use_in_optimisation", False),
            description=raw.get("description") or template.get("description", ""),
        ))
        seen.add(name)

    # A new project gets the complete useful template.  Existing user schemas
    # retain their optional choices while mandatory rows are repaired.
    if not normalized:
        return defaults
    for row in defaults:
        if row["required"] and row["name"] not in seen:
            normalized.append(dict(row))
            seen.add(row["name"])
    required_weights = {
        row.get("weight_field")
        for row in normalized
        if row.get("weight_field")
        and (row.get("required") or row.get("use_in_optimisation"))
    }
    for row in normalized:
        if row["name"] in required_weights:
            row["use_in_optimisation"] = True
    return normalized


def validate_field_definitions(values):
    """Return normalized rows or raise a concise dependency error."""
    raw_seen = set()
    for index, raw in enumerate(values or [], start=1):
        raw_name = str((raw or {}).get("name") or "").strip() if isinstance(raw, Mapping) else ""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", raw_name):
            raise ValueError(
                f"Define Fields row {index} has invalid field name '{raw_name}'. "
                "Use letters, numbers, and underscores, starting with a letter."
            )
        name = canonical_property_key(raw_name)
        if name in raw_seen:
            raise ValueError(f"Define Fields contains duplicate field '{name}'.")
        raw_seen.add(name)
    rows = normalize_field_definitions(values)
    earlier_additives = set()
    seen = set()
    for index, row in enumerate(rows, start=1):
        name = row["name"]
        if not name:
            raise ValueError(f"Define Fields row {index} has no field name.")
        if name in seen:
            raise ValueError(f"Define Fields contains duplicate field '{name}'.")
        seen.add(name)
        if row["kind"] == "additive":
            earlier_additives.add(name)
            continue
        weight = row.get("weight_field") or ""
        if not weight:
            raise ValueError(
                f"Weighted-average field '{name}' requires an additive weight field."
            )
        if weight not in earlier_additives:
            raise ValueError(
                f"Weighted-average field '{name}' must use an additive field "
                f"defined in a higher row; '{weight}' is not available there."
            )
    missing = sorted(mandatory_field_names() - seen)
    if missing:
        raise ValueError("Mandatory fields are missing: " + ", ".join(missing))
    return rows


def optimization_field_names(values):
    return {
        row["name"]
        for row in normalize_field_definitions(values)
        if row.get("use_in_optimisation")
    }


def field_weight_map(values):
    """Return weighted field -> additive weight field for the canonical schema."""
    return {
        row["name"]: row["weight_field"]
        for row in normalize_field_definitions(values)
        if row.get("kind") == "weighted_average" and row.get("weight_field")
    }


def normalize_field_mappings(values=None):
    """Return a deterministic list of source/brand/target/raw mappings."""
    if isinstance(values, Mapping):
        flattened = []
        for source_family, family_values in values.items():
            if not isinstance(family_values, Mapping):
                continue
            for target, source_field in family_values.items():
                flattened.append({
                    "source_family": source_family,
                    "brand": "",
                    "target_field": target,
                    "source_field": source_field,
                })
        values = flattened
    values = values if isinstance(values, (list, tuple)) else []
    result = []
    seen = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        family = str(raw.get("source_family") or "").strip().lower()
        target = canonical_property_key(raw.get("target_field"))
        source_field = str(raw.get("source_field") or "").strip()
        brand = normalise_brand(raw.get("brand"))
        if (
            family not in SOURCE_FAMILIES
            or not target
            or is_calculated_stream_field(target)
        ):
            continue
        if family != "aps" or brand == UNBRANDED:
            brand = ""
        key = (family, brand, target)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "source_family": family,
            "brand": brand,
            "target_field": target,
            "source_field": source_field,
        })
    return result


def mapping_lookup(values, source_family, brand=None):
    family = str(source_family or "").strip().lower()
    brand = normalise_brand(brand)
    result = {}
    # Unbranded mappings establish defaults; brand-specific APS rows override.
    for mapping in normalize_field_mappings(values):
        if mapping["source_family"] != family or mapping["brand"]:
            continue
        result[mapping["target_field"]] = mapping["source_field"]
    if family == "aps" and brand != UNBRANDED:
        for mapping in normalize_field_mappings(values):
            if mapping["source_family"] == family and mapping["brand"] == brand:
                result[mapping["target_field"]] = mapping["source_field"]
    return result


def flatten_available_source_fields(record):
    """Flatten raw and nested modelled values for mapping/discovery."""
    record = record if isinstance(record, Mapping) else {}
    result = {}
    for raw_name, raw_value in record.items():
        if isinstance(raw_value, (dict, list, tuple, set)):
            continue
        result[str(raw_name)] = raw_value
    for key in ("source_properties", "SOURCE_PROPERTIES"):
        nested = record.get(key)
        if isinstance(nested, Mapping):
            result.update({str(name): value for name, value in nested.items()})
    for key in (
        "modelled_properties", "MODELLED_PROPERTIES",
        "modelled_properties_json", "MODELLED_PROPERTIES_JSON",
    ):
        nested = record.get(key)
        if isinstance(nested, str) and nested.strip():
            try:
                nested = json.loads(nested)
            except (TypeError, ValueError):
                nested = {}
        if isinstance(nested, Mapping):
            values = nested.get("values") or {}
            if isinstance(values, Mapping):
                result.update({str(name): value for name, value in values.items()})
    return result


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def apply_field_mappings(
    record,
    definitions,
    mappings,
    source_family,
    brand=None,
):
    """Return every defined canonical field, preserving unmapped fields as None."""
    raw_fields = flatten_available_source_fields(record)
    raw_by_canonical = {
        canonical_property_key(name): value for name, value in raw_fields.items()
    }
    lookup = mapping_lookup(mappings, source_family, brand)
    result = {}
    for definition in normalize_field_definitions(definitions):
        target = definition["name"]
        if is_calculated_stream_field(target):
            result[target] = None
            continue
        source_field = lookup.get(target, "")
        if source_field:
            value = raw_fields.get(source_field)
            if value is None:
                value = raw_by_canonical.get(canonical_property_key(source_field))
            if value is None:
                # Map Fields displays a few compatibility raw names with an
                # explanatory caption, e.g. ``ROM / opening stockpile DMT
                # (feed_dmt)``. Older projects persisted that caption instead
                # of the raw field ID; recover the parenthesised ID here.
                match = re.search(r"\(([^()]+)\)\s*$", str(source_field))
                if match:
                    alias = match.group(1).strip()
                    value = raw_fields.get(
                        alias, raw_by_canonical.get(canonical_property_key(alias))
                    )
        else:
            # Exact-name fallback is a migration aid, not a hidden alias: it
            # lets canonical data already produced by BlendMaster survive.
            value = raw_fields.get(target, raw_by_canonical.get(target))
        result[target] = _number(value)
    # The opening source balance and modelled ROM WMT describe the same
    # material.  Users can map either canonical name once without creating
    # conflicting copies of the stockpile/hex balance.
    if result.get("source_wmt") is None:
        result["source_wmt"] = result.get("modelled_rom_wmt")
    if result.get("modelled_rom_wmt") is None:
        result["modelled_rom_wmt"] = result.get("source_wmt")
    return result


def legacy_aps_mappings(definitions, mappings, brands):
    """Project the registry onto the existing APS importer interfaces."""
    grade_mappings = {"rom": {}, "product": {}}
    property_mappings = {}
    brands = [normalise_brand(value) for value in brands or []]
    brands = [value for value in brands if value != UNBRANDED] or [UNBRANDED]
    for brand in brands:
        lookup = mapping_lookup(mappings, "aps", brand)
        grade_mappings["rom"][brand] = {
            analyte: lookup.get(f"modelled_rom_{analyte}", "")
            for analyte in ANALYTES
        }
        grade_mappings["product"][brand] = {
            analyte: lookup.get(f"modelled_product_{analyte}", "")
            for analyte in ANALYTES
        }
    defined = {row["name"] for row in normalize_field_definitions(definitions)}
    for target, source in mapping_lookup(mappings, "aps").items():
        if target in defined and not any(
            target.startswith(f"{stream}_") for stream in STREAM_PREFIXES
        ):
            property_mappings[target] = source
    # Include brand-only physical fields as the APS importer accepts one raw
    # header per canonical property. Conflicts are left to explicit validation.
    for brand in brands:
        for target, source in mapping_lookup(mappings, "aps", brand).items():
            if target in defined and target not in property_mappings and not any(
                target.startswith(f"{stream}_") for stream in STREAM_PREFIXES
            ):
                property_mappings[target] = source
    return grade_mappings, property_mappings
