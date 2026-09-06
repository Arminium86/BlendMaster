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
from classes.SourcePropertyMappings import (
    AMT_MODELLED_ADDITIVE_FIELDS,
    APS_SOURCE_PROPERTY_CATALOGUE,
)


FIELD_KINDS = ("additive", "weighted_average")
SOURCE_FAMILIES = ("inventory", "amt", "aps")
STREAM_PREFIXES = (
    "modelled_rom",
    "adjusted_rom",
    "modelled_product",
    "adjusted_product",
)
CALCULATED_STREAM_PREFIXES = ("adjusted_rom", "adjusted_product")

_GRADE_TOKENS = {
    "fe", "sio2", "si", "al2o3", "al", "p", "mn", "mgo", "k2o",
    "tio2", "na2o", "cao", "loi", "s",
}
_ROM_WMT_ALIASES = {
    "balance", "balancewmt", "final_wmt", "tonnes", "source_wmt",
    "modelled_feed_wmt", "modelled_rom_wmt",
}
_ROM_DMT_ALIASES = {
    "balancedmt", "source_dmt", "modelled_feed_dmt", "modelled_rom_dmt",
}

# Per-hex reconciliation diagnostics are valuable audit metadata, but they are
# not material properties that can safely supply a Define Fields mapping.  In
# particular, the *_STOCKPILE_WMT values are footprint totals repeated on every
# hex and would be multiplied by the hex count if treated as additive masses.
_AMT_RECONCILIATION_QUANTITY_FIELDS = {
    "final_stockpile_wmt",
    "inventory_balance_wmt",
    "inventory_recon_deduction_wmt",
    "ledger_adjustment_wmt",
    "raw_positive_stockpile_wmt",
    "raw_stockpile_wmt",
    "raw_hex_stockpile_wmt",
    "raw_wmt",
    "spatial_adjustment_wmt",
    "spatial_deficit_filled_wmt",
    "spatial_deficit_wmt",
    "spatial_donor_wmt",
    "spatial_unresolved_wmt",
    "spatially_corrected_stockpile_wmt",
    "spatially_corrected_wmt",
    "unattributed_movement_wmt",
}


def is_grade_source_field(value) -> bool:
    """Return whether a raw field represents an assay/grade value."""
    raw = str(value or "").strip()
    name = canonical_property_key(raw)
    if not name:
        return False
    if (
        name in _ROM_WMT_ALIASES | _ROM_DMT_ALIASES
        or name.endswith(("_wmt", "_dmt", "_tonnes"))
        or "wettonnes" in name
        or "drytonnes" in name
    ):
        return False
    tokens = [token for token in re.split(r"[^a-z0-9]+", raw.lower()) if token]
    return bool(
        name in _GRADE_TOKENS
        or "grade" in name
        or (tokens and (tokens[0] in _GRADE_TOKENS or tokens[-1] in _GRADE_TOKENS))
        or "loi" in tokens
        or (tokens and tokens[-1] == "as")
        or re.match(r"^(fe|sio2|si|al2o3|al|p|mn)_prod[123]$", name)
        or re.match(r"^prod[123]_(fe|sio2|si|al2o3|al|p|mn)$", name)
    )


def is_mappable_source_field(value) -> bool:
    """Return whether a raw field is a grade or additive tonnes quantity."""
    raw = str(value or "").strip()
    name = canonical_property_key(raw)
    if not name:
        return False
    if (
        "internal_recon" in name
        or name.startswith(("internal_blend_recon_", "internal_upgrade_"))
        or re.search(r"(?:^|_)adjusted_(?:rom|product)_", name)
        or "coverage" in name
        or "lineage" in name
    ):
        return False
    if (
        name in _ROM_WMT_ALIASES | _ROM_DMT_ALIASES
        or name.endswith(("_wmt", "_dmt", "_tonnes"))
        or "wettonnes" in name
        or "drytonnes" in name
    ):
        return True
    return is_grade_source_field(raw)


def standardize_available_mapping_fields(values, source_family=None):
    """Filter mapping discovery and collapse compatibility aliases."""
    family = str(source_family or "").strip().lower()
    fields = {
        str(value).strip() for value in (values or [])
        if is_mappable_source_field(value)
    }
    canonical_lookup = {
        canonical_property_key(field): field for field in sorted(fields)
    }
    if "feed_wmt" in canonical_lookup:
        fields -= {
            field for field in fields
            if canonical_property_key(field) in _ROM_WMT_ALIASES
        }
    if "feed_dmt" in canonical_lookup:
        fields -= {
            field for field in fields
            if canonical_property_key(field) in _ROM_DMT_ALIASES
        }
    # Cloudbreak Product 1 lump/fines data historically had both qualified
    # (prod1_fines_fe) and short (fines_fe) names. The MODELLED_* AMT audit
    # columns repeat the same pattern. Show only the Product 1-qualified field
    # whenever both exist; saved mappings to the short alias remain readable.
    available_keys = {
        canonical_property_key(field) for field in fields
    }
    duplicate_size_aliases = set()
    for field in fields:
        name = canonical_property_key(field)
        match = re.match(r"^(modelled_)?(fines|lump)_(.+)$", name)
        if not match:
            continue
        prefix, size, suffix = match.groups()
        qualified = f"{prefix or ''}prod1_{size}_{suffix}"
        if qualified in available_keys:
            duplicate_size_aliases.add(field)
    fields -= duplicate_size_aliases

    if family in {"inventory", "amt"}:
        # Canonical Define Fields outputs are attached to source records after
        # mapping/calculation. They are downstream results, not raw inputs, and
        # must not loop back into Available Source Fields.
        canonical_outputs = set()
        for field in fields:
            name = canonical_property_key(field)
            candidate = name[6:] if name.startswith("grade_") else name
            if (
                candidate in {
                    "modelled_product_wmt", "modelled_product_dmt",
                }
                or (
                    is_grade_source_field(candidate)
                    and candidate.startswith((
                        "insitu_", "modelled_rom_", "modelled_product_",
                    ))
                )
            ):
                canonical_outputs.add(field)
        fields -= canonical_outputs

    if family == "amt":
        fields -= {
            field for field in fields
            if canonical_property_key(field)
            in _AMT_RECONCILIATION_QUANTITY_FIELDS
        }
        # Keep the supported additive contract visible for older projects and
        # sparse selections whose saved/current AMT rows do not contain a
        # populated value for every grade-block-lineage property.
        fields.update({"feed_wmt", "feed_dmt"})
        fields.update({
            f"MODELLED_{property_name.upper()}"
            for property_name in AMT_MODELLED_ADDITIVE_FIELDS
            if property_name not in {"feed_wmt", "feed_dmt"}
        })
        # The AMT lineage payload exposes each derived grade twice: a nested
        # canonical property (prod1_fe/prod1_wmt) and a flattened audit column
        # (MODELLED_PROD1_FE/MODELLED_PROD1_WMT). Keep the explicit MODELLED_*
        # source for both weighted-average and additive properties.
        by_key = {}
        for field in sorted(fields, key=lambda item: item.lower()):
            key = canonical_property_key(field)
            current = by_key.get(key)
            priority = (
                0 if str(field).startswith("MODELLED_") else
                1 if str(field).isupper() else 2
            )
            if current is None or priority < current[0]:
                by_key[key] = (priority, field)
        fields = {item[1] for item in by_key.values()}
        available_keys = {canonical_property_key(field) for field in fields}
        fields -= {
            field for field in fields
            if (
                not canonical_property_key(field).startswith("modelled_")
                and f"modelled_{canonical_property_key(field)}" in available_keys
            )
        }
    return sorted(fields, key=lambda field: field.lower())


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
        return "modelled_rom_wmt"

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
        # source_wmt remains an internal physical-balance alias. The canonical
        # user schema has one ROM WMT field: modelled_rom_wmt.
        if name == "source_wmt":
            continue
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
        if weight == "source_wmt":
            weight = "modelled_rom_wmt"
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
        if target == "source_wmt":
            target = "modelled_rom_wmt"
        source_field = str(raw.get("source_field") or "").strip()
        source_key = canonical_property_key(source_field)
        if family in {"inventory", "amt"}:
            if target == "modelled_rom_wmt" and source_key in _ROM_WMT_ALIASES:
                source_field = "feed_wmt"
            elif target == "modelled_rom_dmt" and source_key in _ROM_DMT_ALIASES:
                source_field = "feed_dmt"
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
                for name, value in values.items():
                    raw_name = str(name)
                    result[raw_name] = value
                    # AMT snapshots persist the raw lineage catalogue once in
                    # modelled-properties JSON. Recreate the former flattened
                    # source name on demand so existing MODELLED_* mappings and
                    # the Map Fields browser remain backward compatible.
                    canonical = canonical_property_key(raw_name)
                    if canonical:
                        result.setdefault(
                            f"MODELLED_{canonical.upper()}", value
                        )
                        if canonical.startswith("oretype_") and canonical.endswith("_wmt"):
                            result.setdefault(
                                f"MODELLED_{canonical[:-4].upper()}_TONNES",
                                value,
                            )
            coverage = nested.get("coverage") or {}
            if isinstance(coverage, Mapping):
                for name, value in coverage.items():
                    canonical = canonical_property_key(name)
                    number = _number(value)
                    if canonical and number is not None:
                        result.setdefault(
                            f"MODELLED_{canonical.upper()}_COVERAGE_PCT",
                            number * 100.0,
                        )
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
            if value is None and canonical_property_key(source_field) == "feed_wmt":
                value = next(
                    (raw_by_canonical[key] for key in (
                        "balancewmt", "balance", "final_wmt", "tonnes",
                        "modelled_rom_wmt", "modelled_feed_wmt", "source_wmt",
                    )
                     if raw_by_canonical.get(key) is not None),
                    None,
                )
            if value is None and canonical_property_key(source_field) == "feed_dmt":
                value = next(
                    (raw_by_canonical[key] for key in (
                        "balancedmt", "modelled_rom_dmt", "modelled_feed_dmt",
                        "source_dmt",
                    )
                     if raw_by_canonical.get(key) is not None),
                    None,
                )
            if value is None:
                # Map Fields displays a few compatibility raw names with an
                # explanatory caption, e.g. ``Insitu / ROM DMT
                # (feed_dmt)``. Older projects persisted that caption instead
                # of the raw field ID; recover the parenthesised ID here.
                match = re.search(r"\(([^()]+)\)\s*$", str(source_field))
                if match:
                    alias = match.group(1).strip()
                    value = raw_fields.get(
                        alias, raw_by_canonical.get(canonical_property_key(alias))
                    )
        else:
            # An empty Map Fields cell is an explicit empty mapping.  Do not
            # let a same-named raw/cached column silently bypass that contract.
            # Calculated fields are populated later by the grade-stream layer.
            value = None
        result[target] = _number(value)
    # Internal balance tracking still consumes source_wmt, but it is derived
    # from the one user-facing ROM WMT field and is never separately mapped.
    result["source_wmt"] = result.get("modelled_rom_wmt")
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

    def core_grade_field(target):
        return any(
            target == f"{stream}_{analyte}"
            for stream in STREAM_PREFIXES
            for analyte in ANALYTES
        )

    for target, source in mapping_lookup(mappings, "aps").items():
        if target in defined and not core_grade_field(target):
            property_mappings[target] = source
    # Include brand-only physical fields as the APS importer accepts one raw
    # header per canonical property. Conflicts are left to explicit validation.
    for brand in brands:
        for target, source in mapping_lookup(mappings, "aps", brand).items():
            if (
                target in defined
                and target not in property_mappings
                and not core_grade_field(target)
            ):
                property_mappings[target] = source
    return grade_mappings, property_mappings
