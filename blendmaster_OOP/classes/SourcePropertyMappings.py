"""Canonical source-property mappings shared by APS, inventory and AMT.

The APS 24HR models use site-specific header names.  BlendMaster keeps a
small, stable catalogue for properties that are useful in blend constraints;
users map the applicable APS header to each canonical name in Data Streams.
Unmapped numeric APS columns continue to flow through under their normalised
header name, so this catalogue does not limit the audit dataset.
"""

from __future__ import annotations

from collections.abc import Mapping

from classes.CustomConstraints import canonical_property_key


ORE_TYPES = ("bid", "cidl", "cidm", "cidu", "did", "hc", "other")
SIZE_ASSAYS = (
    "fe", "sio2", "al2o3", "p", "mn", "loi_425", "loi_total", "s", "as",
)


def _catalogue():
    fields = [
        ("Feed", "feed_dmt", "Feed dry tonnes"),
        ("Feed", "feed_moisture", "Feed moisture"),
    ]
    for ore_type in ORE_TYPES:
        label = ore_type.upper()
        fields.extend([
            ("Ore Type", f"oretype_{ore_type}_wmt", f"{label} wet tonnes"),
            ("Ore Type", f"oretype_{ore_type}_dmt", f"{label} dry tonnes"),
        ])
    for product in (1, 2, 3):
        prefix = f"prod{product}"
        label = f"Product {product}"
        fields.extend([
            (label, f"{prefix}_wmt", f"{label} wet tonnes"),
            (label, f"{prefix}_dmt", f"{label} dry tonnes"),
            (label, f"{prefix}_mass_recovery", f"{label} mass recovery"),
            (label, f"{prefix}_moisture", f"{label} moisture"),
            (label, f"{prefix}_minus_1mm_pct", f"{label} minus 1 mm %"),
            (label, f"{prefix}_minus_1mm_wmt", f"{label} minus 1 mm wet tonnes"),
            (label, f"{prefix}_minus_1mm_dmt", f"{label} minus 1 mm dry tonnes"),
        ])
    for size in ("fines", "lump"):
        title = size.title()
        prefix = f"prod1_{size}"
        fields.extend([
            ("CB Product 1 Split", f"{prefix}_yield_pct", f"{title} yield"),
            ("CB Product 1 Split", f"{prefix}_wmt", f"{title} wet tonnes"),
            ("CB Product 1 Split", f"{prefix}_dmt", f"{title} dry tonnes"),
            ("CB Product 1 Split", f"{prefix}_moisture", f"{title} moisture"),
        ])
        fields.extend(
            (
                "CB Product 1 Split",
                f"{prefix}_{assay}",
                f"{title} {assay.upper().replace('_', ' ')} grade",
            )
            for assay in SIZE_ASSAYS
        )
    return tuple(fields)


APS_SOURCE_PROPERTY_CATALOGUE = _catalogue()
APS_SOURCE_PROPERTY_FIELDS = tuple(
    field for _group, field, _label in APS_SOURCE_PROPERTY_CATALOGUE
)


def normalise_aps_source_property_mappings(value=None):
    """Return canonical-property -> exact APS header mappings.

    Unknown saved keys are retained.  This allows future or site-specific
    properties to survive project round trips even before they are promoted
    into the standard UI catalogue.
    """
    value = value if isinstance(value, Mapping) else {}
    result = {}
    for raw_field, raw_header in value.items():
        field = canonical_property_key(raw_field)
        header = str(raw_header or "").strip()
        if field:
            result[field] = header
    for field in APS_SOURCE_PROPERTY_FIELDS:
        result.setdefault(field, "")
    return result
