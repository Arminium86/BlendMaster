"""Versioned configuration and audit schemas for the production-streams scope.

Task 3 of the BlendMaster major implementation plan. This module defines
forward-compatible record shapes for work that lands in Phases 1 to 5. It is
intentionally free of behavior: nothing here reads Snowflake, touches SQLite or
mutates solver state. Later tasks import these constructors and constants so a
single definition governs persistence, audit reporting and project migration.

Design rules

* Every schema carries an integer ``schema_version``. Readers must tolerate an
  older version by filling documented defaults, and must refuse a newer one
  rather than guess. ``is_readable`` implements that check.
* Every constructor returns a plain ``dict`` so records stay picklable for the
  existing ``.prj`` format and JSON-serialisable for SQLite audit columns.
* Absent optional values are ``None``, never ``0``. A falsy numeric bound is
  ambiguous in this codebase: ``PlanningPlanTargets`` emits ``0.0`` for an
  unset grade bound and ``CaseModeller`` only rescues it with ``or 100``. New
  schemas must not repeat that pattern.
* Field names use the existing lower-snake convention for project state and
  UPPER_SNAKE only where a Snowflake column is mirrored verbatim.

Contracts: docs/AUTHORITATIVE_DATA_AND_BEHAVIOR_CONTRACTS.md
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


# Analyte tuple is duplicated deliberately. Importing GradeStreams here would
# make a schema definition depend on solver code.
SCHEMA_ANALYTES = ("fe", "si", "al", "p", "mn")


# --------------------------------------------------------------------------
# Schema versions
# --------------------------------------------------------------------------
# Increment a version when its record shape changes incompatibly. Adding an
# optional field with a documented default is compatible and does not require
# an increment.

RECONCILIATION_SAMPLE_SCHEMA_VERSION = 1
RESOLVED_FACTOR_SCHEMA_VERSION = 1
QUALITY_LIMIT_SCHEMA_VERSION = 1
AMT_FOOTPRINT_EXCLUSION_SCHEMA_VERSION = 1
TOPOLOGY_SCHEMA_VERSION = 1
DESTINATION_PROGRESS_SCHEMA_VERSION = 1


def is_readable(record: Optional[Mapping], current_version: int) -> bool:
    """Return True when a persisted record is safe for this build to read.

    A missing version is treated as version 1 so pre-Task-3 projects remain
    loadable. A newer version returns False; callers must warn rather than
    silently misinterpret fields they do not understand.
    """
    if not isinstance(record, Mapping):
        return False
    version = record.get("schema_version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        return False
    return 1 <= version <= current_version


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _optional_number(value: Any) -> Optional[float]:
    """Return a float, or None when absent. Zero is preserved as zero."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_integer(value: Any) -> Optional[int]:
    """Return an integer, or None when absent or not exactly integral."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer():
        return None
    return int(number)


def _timestamp(value: Any) -> Any:
    """Return a JSON-safe timestamp while leaving ordinary scalars unchanged."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return value


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(values: Optional[Iterable[Mapping]]) -> list[Dict[str, Any]]:
    return [dict(value) for value in (values or []) if isinstance(value, Mapping)]


def _analyte_map(values: Optional[Mapping]) -> Dict[str, Optional[float]]:
    """Return a full five-analyte map with None for anything unsupplied."""
    supplied = {
        _clean(key).lower(): value
        for key, value in _mapping(values).items()
    }
    return {
        analyte: _optional_number(supplied.get(analyte))
        for analyte in SCHEMA_ANALYTES
    }


# --------------------------------------------------------------------------
# 1. Historical reconciliation samples
# --------------------------------------------------------------------------
# Task 5. The current daily reconciliation SQL aggregates to OPF, brand and
# shift date, discarding which grade blocks contributed. Advanced
# reconciliation needs that detail retained so a factor can later be matched
# to spatially relevant sources.

SAMPLE_KIND_BLEND = "blend"
SAMPLE_KIND_REGRESSION = "regression"
SAMPLE_KINDS = (SAMPLE_KIND_BLEND, SAMPLE_KIND_REGRESSION)

# Finest grain the assay tables support (Q38).
SAMPLE_GRAIN_SHIFT = "shift"
SAMPLE_GRAIN_DAY = "day"
SAMPLE_GRAINS = (SAMPLE_GRAIN_SHIFT, SAMPLE_GRAIN_DAY)


def reconciliation_sample(
    opf: Any,
    brand: Any,
    kind: Any,
    period_start: Any,
    period_end: Any,
    *,
    grain: Any = SAMPLE_GRAIN_SHIFT,
    factors: Optional[Mapping] = None,
    feed_wmt: Any = None,
    product_dmt: Any = None,
    contributing_blocks: Optional[Iterable[Mapping]] = None,
    source_rows: Any = None,
    provenance: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """One period of reconciliation history with its contributing blocks.

    ``factors`` maps analyte to the derived factor for this period and kind.
    ``contributing_blocks`` holds :func:`contributing_block` entries whose
    ``feed_wmt`` values describe the composition of the feed that produced
    those factors. Composition is what later lets Task 6 judge whether a
    factor is compositionally relevant to a source being adjusted.
    ``provenance`` records shift identity, reconstruction basis, attributed
    and unknown feed tonnes, source assay metadata and quality warnings.
    """
    blocks = _mapping_list(contributing_blocks)
    return {
        "schema_version": RECONCILIATION_SAMPLE_SCHEMA_VERSION,
        "opf": _clean(opf),
        "brand": _clean(brand).upper(),
        "kind": _clean(kind).lower(),
        "grain": _clean(grain).lower() or SAMPLE_GRAIN_SHIFT,
        "period_start": _timestamp(period_start),
        "period_end": _timestamp(period_end),
        "feed_wmt": _optional_number(feed_wmt),
        "product_dmt": _optional_number(product_dmt),
        "factors": _analyte_map(factors),
        "contributing_blocks": blocks,
        "source_rows": _optional_integer(source_rows),
        # Optional v1 extension: shift identity, lineage coverage, original
        # warehouse brand and data-quality warnings supplied by Task 5.
        "provenance": _mapping(provenance),
    }


def contributing_block(
    grade_block_key: Any,
    *,
    feed_wmt: Any = None,
    material_type: Any = None,
    spatial_key: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """One grade block that contributed feed during a reconciliation period.

    ``grade_block_key`` is the six-token pipe-joined key produced by
    ``ExpitSequenceReconciler.grade_block_key``. ``spatial_key`` is the same
    tokens as an ordered sequence so callers can compare prefixes without
    re-splitting, ordered pit, stage, bench, blast, flitch, material_slice.
    """
    tokens = [
        _clean(token) for token in (spatial_key or [])
        if _clean(token)
    ]
    if not tokens:
        tokens = [
            token for token in _clean(grade_block_key).split("|") if token
        ]
    return {
        "grade_block_key": _clean(grade_block_key),
        "spatial_key": tokens,
        "material_type": _clean(material_type).upper(),
        "feed_wmt": _optional_number(feed_wmt),
    }


# --------------------------------------------------------------------------
# 2. Resolved per-source, per-hex and per-lineage factors
# --------------------------------------------------------------------------
# Tasks 6 and 7. One record holds every analyte and both factor kinds because
# the chosen spatial level is shared across analytes, blend and regression.

FACTOR_METHOD_STANDARD = "standard"
FACTOR_METHOD_LOOKBACK = "lookback"
FACTOR_METHOD_SPATIAL_COMPOSITIONAL = "spatial_compositional"
FACTOR_METHOD_MAX_CONFIDENCE = "auto_max_confidence"
FACTOR_METHODS = (
    FACTOR_METHOD_STANDARD,
    FACTOR_METHOD_LOOKBACK,
    FACTOR_METHOD_SPATIAL_COMPOSITIONAL,
    FACTOR_METHOD_MAX_CONFIDENCE,
)

FACTOR_LEVEL_PIT_STAGE_BENCH_BLAST_FLITCH_MATERIAL = (
    "pit_stage_bench_blast_flitch_material_type"
)
FACTOR_LEVEL_PIT_STAGE_BENCH_BLAST_MATERIAL = (
    "pit_stage_bench_blast_material_type"
)
FACTOR_LEVEL_PIT_STAGE_BENCH_MATERIAL = "pit_stage_bench_material_type"
FACTOR_LEVEL_PIT_STAGE_MATERIAL = "pit_stage_material_type"
FACTOR_LEVEL_PIT_MATERIAL = "pit_material_type"
FACTOR_LEVEL_GLOBAL = "global"
FACTOR_LEVELS = (
    FACTOR_LEVEL_PIT_STAGE_BENCH_BLAST_FLITCH_MATERIAL,
    FACTOR_LEVEL_PIT_STAGE_BENCH_BLAST_MATERIAL,
    FACTOR_LEVEL_PIT_STAGE_BENCH_MATERIAL,
    FACTOR_LEVEL_PIT_STAGE_MATERIAL,
    FACTOR_LEVEL_PIT_MATERIAL,
    FACTOR_LEVEL_GLOBAL,
)


def resolved_factor(
    source_id: Any,
    source_kind: Any,
    grade_block_key: Any,
    opf: Any,
    brand: Any,
    *,
    hex_id: Any = None,
    lineage_fraction: Any = None,
    method: Any = FACTOR_METHOD_STANDARD,
    resolution_level: Any = FACTOR_LEVEL_GLOBAL,
    matched_spatial_key: Optional[Sequence[Any]] = None,
    blend_factors: Optional[Mapping] = None,
    regression_factors: Optional[Mapping] = None,
    source_history: Optional[Iterable[Mapping]] = None,
    source_rows: Any = None,
    source_feed_wmt: Any = None,
    lookback_start: Any = None,
    lookback_end: Any = None,
    lookback_days: Any = None,
    fallback_reason: Any = None,
    confidence_percent: Any = None,
    uncertainty_percent: Any = None,
    manual_override: Any = False,
    provenance: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """Return one resolved factor set at source/hex/lineage grain.

    ``resolution_level`` is deliberately singular. The contract requires the
    best spatial level to be selected once, after which every analyte and both
    factor kinds are resolved at that level. ``source_history`` contains the
    sample references or audit summaries that contributed to the result.
    ``provenance`` is an optional v1 extension (default empty mapping) for
    resolver configuration, per-factor evidence and confidence methodology.
    """
    spatial_key = [
        _clean(token) for token in (matched_spatial_key or []) if _clean(token)
    ]
    return {
        "schema_version": RESOLVED_FACTOR_SCHEMA_VERSION,
        "source_id": _clean(source_id),
        "source_kind": _clean(source_kind).lower(),
        "hex_id": _clean(hex_id),
        "grade_block_key": _clean(grade_block_key),
        "lineage_fraction": _optional_number(lineage_fraction),
        "opf": _clean(opf),
        "brand": _clean(brand).upper(),
        "method": _clean(method).lower() or FACTOR_METHOD_STANDARD,
        "resolution_level": (
            _clean(resolution_level).lower() or FACTOR_LEVEL_GLOBAL
        ),
        "matched_spatial_key": spatial_key,
        "blend_factors": _analyte_map(blend_factors),
        "regression_factors": _analyte_map(regression_factors),
        "source_history": _mapping_list(source_history),
        "source_rows": _optional_integer(source_rows),
        "source_feed_wmt": _optional_number(source_feed_wmt),
        "lookback_start": _timestamp(lookback_start),
        "lookback_end": _timestamp(lookback_end),
        "lookback_days": _optional_integer(lookback_days),
        "fallback_reason": _clean(fallback_reason),
        "confidence_percent": _optional_number(confidence_percent),
        "uncertainty_percent": _optional_number(uncertainty_percent),
        "manual_override": bool(manual_override),
        "provenance": _mapping(provenance),
    }


# --------------------------------------------------------------------------
# 3. Product quality limits and target modes
# --------------------------------------------------------------------------
# Tasks 12, 16 and 17. Hard mode retains the legacy minimum/maximum fields.
# Soft mode adds target and LQL/HQL without changing what those legacy fields
# mean. Each product row owns one record containing all five analytes.

TARGET_MODE_HARD = "hard"
TARGET_MODE_SOFT = "soft"
TARGET_MODES = (TARGET_MODE_HARD, TARGET_MODE_SOFT)

QUALITY_LIMIT_MODE_HARD = "hard"
QUALITY_LIMIT_MODE_SOFT = "soft"
QUALITY_LIMIT_MODES = (QUALITY_LIMIT_MODE_HARD, QUALITY_LIMIT_MODE_SOFT)

QUALITY_EVALUATION_STEADY_STATE = "steady_state"
QUALITY_EVALUATION_CUMULATIVE_BUILD = "cumulative_build"
QUALITY_EVALUATION_BASES = (
    QUALITY_EVALUATION_STEADY_STATE,
    QUALITY_EVALUATION_CUMULATIVE_BUILD,
)


def analyte_quality_limit(
    *,
    minimum: Any = None,
    maximum: Any = None,
    target: Any = None,
    lql: Any = None,
    hql: Any = None,
    limit_mode: Any = QUALITY_LIMIT_MODE_HARD,
    target_source: Any = None,
    target_penalty_weight: Any = None,
    limit_penalty_weight: Any = None,
) -> Dict[str, Any]:
    """Return the quality settings for one analyte.

    ProductQualityLimits validates numerical lower <= target <= upper at input
    and runtime boundaries. Legacy lql/hql keys store numerical lower/upper;
    UI captions reverse for contaminants (HQL <= Target <= LQL).
    Keeping absent bounds as ``None`` prevents an open
    upper bound from becoming a literal zero.
    """
    return {
        "minimum": _optional_number(minimum),
        "maximum": _optional_number(maximum),
        "target": _optional_number(target),
        "lql": _optional_number(lql),
        "hql": _optional_number(hql),
        "limit_mode": (
            _clean(limit_mode).lower() or QUALITY_LIMIT_MODE_HARD
        ),
        "target_source": _clean(target_source).lower(),
        "target_penalty_weight": _optional_number(target_penalty_weight),
        "limit_penalty_weight": _optional_number(limit_penalty_weight),
    }


def product_quality_limits(
    opf: Any,
    brand: Any,
    lane: Any = "product",
    *,
    target_mode: Any = TARGET_MODE_HARD,
    evaluation_basis: Any = QUALITY_EVALUATION_STEADY_STATE,
    limits: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """Return one OPF/brand/product-lane quality configuration."""
    supplied = {
        _clean(analyte).lower(): value
        for analyte, value in _mapping(limits).items()
    }
    normalized_limits = {}
    for analyte in SCHEMA_ANALYTES:
        values = _mapping(supplied.get(analyte))
        normalized_limits[analyte] = analyte_quality_limit(
            minimum=values.get("minimum"),
            maximum=values.get("maximum"),
            target=values.get("target"),
            lql=values.get("lql"),
            hql=values.get("hql"),
            limit_mode=values.get("limit_mode", QUALITY_LIMIT_MODE_HARD),
            target_source=values.get("target_source"),
            target_penalty_weight=values.get("target_penalty_weight"),
            limit_penalty_weight=values.get("limit_penalty_weight"),
        )
    return {
        "schema_version": QUALITY_LIMIT_SCHEMA_VERSION,
        "opf": _clean(opf),
        "brand": _clean(brand).upper(),
        "lane": _clean(lane).lower() or "product",
        "target_mode": _clean(target_mode).lower() or TARGET_MODE_HARD,
        "evaluation_basis": (
            _clean(evaluation_basis).lower()
            or QUALITY_EVALUATION_STEADY_STATE
        ),
        "limits": normalized_limits,
    }


# --------------------------------------------------------------------------
# 4. AMT footprint exclusions and reconciliation outcomes
# --------------------------------------------------------------------------

AMT_OUTCOME_RECONCILED = "reconciled"
AMT_OUTCOME_EXCLUDED = "excluded"
AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW = "zeroed_non_positive_raw"
AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY = (
    "zeroed_non_positive_inventory"
)
AMT_OUTCOME_RETAINED_INVENTORY_UNAVAILABLE = (
    "retained_inventory_unavailable"
)
AMT_OUTCOMES = (
    AMT_OUTCOME_RECONCILED,
    AMT_OUTCOME_EXCLUDED,
    AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW,
    AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY,
    AMT_OUTCOME_RETAINED_INVENTORY_UNAVAILABLE,
)
AMT_ZEROED_OUTCOMES = (
    AMT_OUTCOME_EXCLUDED,
    AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW,
    AMT_OUTCOME_ZEROED_NON_POSITIVE_INVENTORY,
)


def amt_footprint_audit(
    footprint_id: Any,
    *,
    excluded: Any = False,
    exclusion_reason: Any = None,
    excluded_by: Any = None,
    excluded_at: Any = None,
    outcome: Any = AMT_OUTCOME_RECONCILED,
    outcome_reason: Any = None,
    raw_wmt: Any = None,
    spatially_reconciled_wmt: Any = None,
    inventory_wmt: Any = None,
    final_wmt: Any = None,
    source_rows: Any = None,
    eligible_for_processing: Any = None,
) -> Dict[str, Any]:
    """Return the complete audit summary for one AMT footprint.

    The three tonne stages preserve the evidence needed to explain a zeroed
    footprint. When eligibility is omitted it is derived conservatively from
    exclusion, outcome and final tonnes.
    """
    excluded = bool(excluded)
    normalized_outcome = _clean(outcome).lower() or AMT_OUTCOME_RECONCILED
    normalized_final_wmt = _optional_number(final_wmt)
    if eligible_for_processing is None:
        eligible_for_processing = (
            not excluded
            and normalized_outcome not in AMT_ZEROED_OUTCOMES
            and (
                normalized_final_wmt is None
                or normalized_final_wmt > 0.0
            )
        )
    return {
        "schema_version": AMT_FOOTPRINT_EXCLUSION_SCHEMA_VERSION,
        "footprint_id": _clean(footprint_id),
        "excluded": excluded,
        "exclusion_reason": _clean(exclusion_reason),
        "excluded_by": _clean(excluded_by),
        "excluded_at": _timestamp(excluded_at),
        "outcome": normalized_outcome,
        "outcome_reason": _clean(outcome_reason),
        "raw_wmt": _optional_number(raw_wmt),
        "spatially_reconciled_wmt": _optional_number(
            spatially_reconciled_wmt
        ),
        "inventory_wmt": _optional_number(inventory_wmt),
        "final_wmt": normalized_final_wmt,
        "source_rows": _optional_integer(source_rows),
        "eligible_for_processing": bool(eligible_for_processing),
    }


# --------------------------------------------------------------------------
# 5. Material-flow topology
# --------------------------------------------------------------------------
# Task 4 starts with ONE_LANE. The same shape can represent Total_Feed and
# combined OPFs later without inventing a second material-flow vocabulary.

TOPOLOGY_MODE_ONE_LANE = "one_lane"
TOPOLOGY_MODE_TOTAL_FEED = "total_feed"
TOPOLOGY_MODE_COMBINED_OPF = "combined_opf"
TOPOLOGY_MODES = (
    TOPOLOGY_MODE_ONE_LANE,
    TOPOLOGY_MODE_TOTAL_FEED,
    TOPOLOGY_MODE_COMBINED_OPF,
)

TOPOLOGY_NODE_SOURCE = "source"
TOPOLOGY_NODE_TIPPING_POINT = "tipping_point"
TOPOLOGY_NODE_CONVEYOR = "conveyor"
TOPOLOGY_NODE_COS = "cos"
TOPOLOGY_NODE_OPF = "opf"
TOPOLOGY_NODE_PRODUCT_BUILD_LANE = "product_build_lane"
TOPOLOGY_NODE_TYPES = (
    TOPOLOGY_NODE_SOURCE,
    TOPOLOGY_NODE_TIPPING_POINT,
    TOPOLOGY_NODE_CONVEYOR,
    TOPOLOGY_NODE_COS,
    TOPOLOGY_NODE_OPF,
    TOPOLOGY_NODE_PRODUCT_BUILD_LANE,
)

TOPOLOGY_EDGE_MATERIAL_FLOW = "material_flow"
TOPOLOGY_EDGE_CONVEYOR = "conveyor"
TOPOLOGY_EDGE_COS = "cos"
TOPOLOGY_EDGE_TYPES = (
    TOPOLOGY_EDGE_MATERIAL_FLOW,
    TOPOLOGY_EDGE_CONVEYOR,
    TOPOLOGY_EDGE_COS,
)


def topology_node(
    node_id: Any,
    node_type: Any,
    *,
    label: Any = None,
    properties: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """Return an operational topology node; UI position is stored elsewhere."""
    return {
        "node_id": _clean(node_id),
        "node_type": _clean(node_type).lower(),
        "label": _clean(label),
        "properties": _mapping(properties),
    }


def topology_edge(
    edge_id: Any,
    source_node_id: Any,
    target_node_id: Any,
    *,
    edge_type: Any = TOPOLOGY_EDGE_MATERIAL_FLOW,
    capacity_wmt: Any = None,
    max_rate_wmtph: Any = None,
    latency_hours: Any = None,
    enabled: Any = True,
    properties: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """Return one directed material-flow edge and its operational limits."""
    return {
        "edge_id": _clean(edge_id),
        "source_node_id": _clean(source_node_id),
        "target_node_id": _clean(target_node_id),
        "edge_type": (
            _clean(edge_type).lower() or TOPOLOGY_EDGE_MATERIAL_FLOW
        ),
        "capacity_wmt": _optional_number(capacity_wmt),
        "max_rate_wmtph": _optional_number(max_rate_wmtph),
        "latency_hours": _optional_number(latency_hours),
        "enabled": bool(enabled),
        "properties": _mapping(properties),
    }


def material_flow_topology(
    topology_id: Any,
    *,
    mode: Any = TOPOLOGY_MODE_ONE_LANE,
    nodes: Optional[Iterable[Mapping]] = None,
    edges: Optional[Iterable[Mapping]] = None,
    properties: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """Return a versioned directed topology without presentation layout."""
    return {
        "schema_version": TOPOLOGY_SCHEMA_VERSION,
        "topology_id": _clean(topology_id),
        "mode": _clean(mode).lower() or TOPOLOGY_MODE_ONE_LANE,
        "nodes": _mapping_list(nodes),
        "edges": _mapping_list(edges),
        "properties": _mapping(properties),
    }


# --------------------------------------------------------------------------
# 6. Destination build-order progress and remaining capacity
# --------------------------------------------------------------------------

DESTINATION_CAPACITY_SOURCE_USER = "user"
DESTINATION_CAPACITY_SOURCE_INFERRED = "inferred"
DESTINATION_CAPACITY_SOURCES = (
    DESTINATION_CAPACITY_SOURCE_USER,
    DESTINATION_CAPACITY_SOURCE_INFERRED,
)


def destination_build(
    destination: Any,
    sequence_index: Any,
    *,
    build_instance: Any = 1,
    build_id: Any = None,
    planned_wmt: Any = None,
    source_row_keys: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """Return one occurrence in an authoritative 2WP destination order."""
    normalized_index = _optional_integer(sequence_index)
    normalized_instance = _optional_integer(build_instance)
    normalized_destination = _clean(destination)
    if build_id is None:
        build_id = ":".join(
            (
                str(normalized_index if normalized_index is not None else ""),
                normalized_destination,
                str(
                    normalized_instance
                    if normalized_instance is not None
                    else ""
                ),
            )
        )
    return {
        "build_id": _clean(build_id),
        "sequence_index": normalized_index,
        "destination": normalized_destination,
        "build_instance": normalized_instance,
        "planned_wmt": _optional_number(planned_wmt),
        "source_row_keys": [
            _clean(key) for key in (source_row_keys or []) if _clean(key)
        ],
    }


def destination_progress(
    rom_area: Any,
    material_type: Any,
    *,
    build_order: Optional[Iterable[Mapping]] = None,
    active_build_index: Any = None,
    detected_active_destination: Any = None,
    user_entered_remaining_capacity_wmt: Any = None,
    remaining_capacity_wmt: Any = None,
    capacity_source: Any = DESTINATION_CAPACITY_SOURCE_USER,
    activity_evidence: Optional[Iterable[Mapping]] = None,
    ambiguity_warnings: Optional[Iterable[Any]] = None,
    capacity_transitions: Optional[Iterable[Mapping]] = None,
    source_signature: Any = None,
    updated_at: Any = None,
) -> Dict[str, Any]:
    """Return progress through one ROM-area/material-type destination order."""
    order = _mapping_list(build_order)
    active_index = _optional_integer(active_build_index)

    def build_at(index: Optional[int]) -> Dict[str, Any]:
        if index is None or index < 0 or index >= len(order):
            return {}
        return order[index]

    active = build_at(active_index)
    previous = build_at(active_index - 1 if active_index is not None else None)
    following = build_at(active_index + 1 if active_index is not None else None)
    entered_capacity = _optional_number(user_entered_remaining_capacity_wmt)
    current_capacity = _optional_number(remaining_capacity_wmt)
    if current_capacity is None:
        current_capacity = entered_capacity
    return {
        "schema_version": DESTINATION_PROGRESS_SCHEMA_VERSION,
        "rom_area": _clean(rom_area),
        "material_type": _clean(material_type).upper(),
        "build_order": order,
        "active_build_index": active_index,
        "active_build_id": _clean(active.get("build_id")),
        "active_destination": _clean(active.get("destination")),
        "previous_build_id": _clean(previous.get("build_id")),
        "previous_destination": _clean(previous.get("destination")),
        "next_build_id": _clean(following.get("build_id")),
        "next_destination": _clean(following.get("destination")),
        "detected_active_destination": _clean(
            detected_active_destination
        ),
        "user_entered_remaining_capacity_wmt": entered_capacity,
        "remaining_capacity_wmt": current_capacity,
        "capacity_source": (
            _clean(capacity_source).lower()
            or DESTINATION_CAPACITY_SOURCE_USER
        ),
        "activity_evidence": _mapping_list(activity_evidence),
        "ambiguity_warnings": [
            _clean(warning)
            for warning in (ambiguity_warnings or [])
            if _clean(warning)
        ],
        "capacity_transitions": _mapping_list(capacity_transitions),
        "source_signature": _clean(source_signature),
        "updated_at": _timestamp(updated_at),
    }
