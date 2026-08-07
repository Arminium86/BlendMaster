"""Safe, linearizable user-defined blend ratio constraints."""

from __future__ import annotations

import ast
import json
import keyword
import math
import re
from typing import Any, Mapping

from classes.GradeStreams import flatten_grade_streams


class CustomConstraintError(ValueError):
    """A user-defined expression or ratio cannot be evaluated safely."""


def constraint_key(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return key or "constraint"


def canonical_property_key(value: Any) -> str:
    """Return a safe expression identifier for an imported field name."""
    key = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
    key = re.sub(r"_+", "_", key).strip("_")
    if not key:
        return ""
    if key[0].isdigit() or keyword.iskeyword(key):
        key = f"field_{key}"
    return key


_RUNTIME_PROPERTY_NAMES = {
    "balance",
    "tonnes",
    "payload",
    "sequence",
    "max_quantity",
    "max_reclaim_rate",
    "reclaim_threshold",
    "equipment_rate_input",
    "equipment_rate_output",
    "cost",
    "cash",
    "direct_tip_id",
    "stockpileid",
    "grade_block_count",
}
_ADDITIVE_SUFFIXES = (
    "_wmt",
    "_dmt",
    "_tonnes",
    "_mass",
    "_volume",
)
_INTENSIVE_MARKERS = (
    "grade_",
    "source_grade_",
    "selected_",
    "_wtavg",
    "_pct",
    "_percent",
    "_fraction",
    "_ratio",
    "_moisture",
    "_yield",
    "_recovery",
    "ultrafine",
    "minus_1mm",
    "minus1mm",
    "density",
)
_ASSAY_SUFFIXES = (
    "_fe",
    "_sio2",
    "_al2o3",
    "_p",
    "_mn",
    "_mgo",
    "_k2o",
    "_tio2",
    "_na2o",
    "_cao",
    "_loi",
)

BUILTIN_CONSTRAINT_FIELDS = {
    "one",
    "is_direct_tip",
    "is_grade_block",
    "is_stockpile",
    "is_amt",
    "is_inventory",
    "source_balance",
    "selected_fe",
    "selected_si",
    "selected_al",
    "selected_p",
    "selected_mn",
    "grade_fe",
    "grade_si",
    "grade_al",
    "grade_p",
    "grade_mn",
}


def source_property_kind(value: Any, property_kinds=None) -> str:
    """Classify an imported numeric field for safe mass-balance handling."""
    key = canonical_property_key(value)
    declared = {
        canonical_property_key(name): str(kind).strip().lower()
        for name, kind in dict(property_kinds or {}).items()
    }.get(key)
    if declared in {"additive", "weighted_average", "intensive", "runtime"}:
        return "intensive" if declared == "weighted_average" else declared
    if (
        not key
        or key in _RUNTIME_PROPERTY_NAMES
        or key.endswith(("_id", "_count", "_sequence", "_rate", "_threshold"))
        or "datetime" in key
        or "timestamp" in key
    ):
        return "runtime"
    if key == "balancedmt" or key.endswith(_ADDITIVE_SUFFIXES):
        return "additive"
    if (
        key.startswith(("grade_", "source_grade_", "selected_", "modelled_"))
        or any(marker in key for marker in _INTENSIVE_MARKERS)
        or key.endswith(_ASSAY_SUFFIXES)
    ):
        return "intensive"
    return "unknown"


def expand_required_property_keys(required_keys, property_weights=None) -> set:
    """Include additive weight dependencies for every required weighted field."""
    required = {
        canonical_property_key(key) for key in (required_keys or [])
        if canonical_property_key(key)
    }
    weights = {
        canonical_property_key(name): canonical_property_key(weight)
        for name, weight in dict(property_weights or {}).items()
        if canonical_property_key(name) and canonical_property_key(weight)
    }
    pending = list(required)
    while pending:
        dependency = weights.get(pending.pop())
        if dependency and dependency not in required:
            required.add(dependency)
            pending.append(dependency)
    return required


def constraint_property_fields(
    properties, source_balance=None, property_kinds=None
) -> dict:
    """Expose dimensionally safe per-tonne coefficients to expressions."""
    result = {}
    try:
        balance = float(source_balance)
    except (TypeError, ValueError):
        balance = 0.0
    if not math.isfinite(balance):
        balance = 0.0
    for raw_key, raw_value in (properties or {}).items():
        key = canonical_property_key(raw_key)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not key or not math.isfinite(value):
            continue
        kind = source_property_kind(key, property_kinds)
        if kind in {"intensive", "unknown"}:
            result[key] = value
        elif kind == "additive" and balance > 1e-12:
            coefficient = value / balance
            result[key] = coefficient
            result[f"{key}_per_source_wmt"] = coefficient
    return result


def merge_source_properties(
    current,
    current_tonnes,
    incoming,
    incoming_tonnes,
    property_kinds=None,
    property_weights=None,
) -> dict:
    """Merge source properties without averaging additive/control fields."""
    current = dict(current or {})
    incoming = dict(incoming or {})
    current_tonnes = max(float(current_tonnes or 0), 0.0)
    incoming_tonnes = max(float(incoming_tonnes or 0), 0.0)
    total_tonnes = current_tonnes + incoming_tonnes
    weight_fields = {
        canonical_property_key(name): canonical_property_key(weight)
        for name, weight in dict(property_weights or {}).items()
        if canonical_property_key(name) and canonical_property_key(weight)
    }
    merged = {
        key: value
        for key, value in current.items()
        if source_property_kind(key, property_kinds) == "runtime"
    }
    for key in set(current) | set(incoming):
        kind = source_property_kind(key, property_kinds)
        if kind not in {"intensive", "unknown", "additive"}:
            continue
        old_value = current.get(key)
        new_value = incoming.get(key)
        try:
            old_value = float(old_value) if old_value is not None else None
            new_value = float(new_value) if new_value is not None else None
        except (TypeError, ValueError):
            continue
        if old_value is not None and not math.isfinite(old_value):
            old_value = None
        if new_value is not None and not math.isfinite(new_value):
            new_value = None
        if kind == "additive":
            # A property missing from either positive-tonnage component is not
            # silently carried forward; doing so would overstate its coverage.
            if current_tonnes > 0 and old_value is None:
                continue
            if incoming_tonnes > 0 and new_value is None:
                continue
            merged[key] = (old_value or 0.0) + (new_value or 0.0)
        else:
            weight_field = weight_fields.get(canonical_property_key(key))
            if weight_field:
                try:
                    old_weight = (
                        float(current.get(weight_field))
                        if current.get(weight_field) is not None else None
                    )
                    new_weight = (
                        float(incoming.get(weight_field))
                        if incoming.get(weight_field) is not None else None
                    )
                except (TypeError, ValueError):
                    continue
                if (
                    (old_weight is not None and not math.isfinite(old_weight))
                    or (new_weight is not None and not math.isfinite(new_weight))
                ):
                    continue
                if current_tonnes > 0 and old_weight is None:
                    continue
                if incoming_tonnes > 0 and new_weight is None:
                    continue
                old_weight = max(old_weight or 0.0, 0.0)
                new_weight = max(new_weight or 0.0, 0.0)
            else:
                old_weight = current_tonnes
                new_weight = incoming_tonnes
            if old_weight > 0 and old_value is None:
                continue
            if new_weight > 0 and new_value is None:
                continue
            total_weight = old_weight + new_weight
            if total_weight <= 0:
                continue
            merged[key] = (
                (old_value or 0.0) * old_weight
                + (new_value or 0.0) * new_weight
            ) / total_weight
    return merged


def scale_additive_source_properties(
    properties, remaining_ratio, property_kinds=None
) -> dict:
    ratio = min(max(float(remaining_ratio or 0), 0.0), 1.0)
    result = dict(properties or {})
    for key, value in list(result.items()):
        if source_property_kind(key, property_kinds) != "additive":
            continue
        try:
            result[key] = float(value) * ratio
        except (TypeError, ValueError):
            result.pop(key, None)
    return result


def custom_constraint_property_keys(definitions) -> set:
    """Return raw source-property keys required by enabled expressions."""
    required = set()
    for definition in definitions or []:
        if not isinstance(definition, Mapping) or not definition.get(
            "enabled", True
        ):
            continue
        for expression_text in (
            definition.get("numerator"),
            definition.get("denominator") or "one",
        ):
            expression = SafeNumericExpression(expression_text)
            for raw_name in expression.names:
                name = canonical_property_key(raw_name)
                if not name or name in BUILTIN_CONSTRAINT_FIELDS:
                    continue
                suffix = "_per_source_wmt"
                if name.endswith(suffix):
                    name = name[:-len(suffix)]
                required.add(name)
    return required


def filter_source_properties(properties, required_keys=None) -> dict:
    """Project a full audit property mapping to the active solver fields."""
    properties = dict(properties or {})
    if required_keys is None:
        return properties
    required = {canonical_property_key(key) for key in required_keys}
    return {
        canonical_property_key(key): value
        for key, value in properties.items()
        if canonical_property_key(key) in required
    }


def source_property_report_fields(
    properties, prefix="source_property_", property_kinds=None,
    active_fields=None,
) -> dict:
    """Flatten the active numeric source properties into report columns."""
    result = {
        f"{prefix}{canonical_property_key(key)}": None
        for key in (active_fields or [])
        if canonical_property_key(key)
        and source_property_kind(key, property_kinds) != "runtime"
    }
    for raw_key, raw_value in dict(properties or {}).items():
        key = canonical_property_key(raw_key)
        if not key or source_property_kind(key, property_kinds) == "runtime":
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            result[f"{prefix}{key}"] = value
    return result


def source_property_balance_report_fields(
    opening_properties,
    depleted_properties,
    active_fields=None,
    prefix="source_property_",
    property_kinds=None,
) -> dict:
    """Expose opening, depleted and closing balances for additive fields.

    ``source_property_<field>`` is the amount depleted by the current solver
    transaction.  Only opening and closing columns are added here so the
    report does not contain a duplicate ``actual_depletion`` alias.
    """
    opening = dict(opening_properties or {})
    depleted = dict(depleted_properties or {})
    selected_keys = (
        set(opening) | set(depleted)
        if active_fields is None else active_fields
    )
    keys = {
        canonical_property_key(key)
        for key in selected_keys
        if canonical_property_key(key)
    }
    result = {}
    def finite_number(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    for key in sorted(keys):
        if source_property_kind(key, property_kinds) != "additive":
            continue
        opening_value = finite_number(opening.get(key))
        depleted_value = finite_number(depleted.get(key))
        result[f"{prefix}{key}_opening_balance"] = opening_value
        result[f"{prefix}{key}_closing_balance"] = (
            max(opening_value - depleted_value, 0.0)
            if opening_value is not None and depleted_value is not None
            else None
        )
    return result


class SafeNumericExpression:
    """Evaluate arithmetic over named numeric source fields without ``eval``."""

    _BINARY = {
        ast.Add: lambda left, right: left + right,
        ast.Sub: lambda left, right: left - right,
        ast.Mult: lambda left, right: left * right,
        ast.Div: lambda left, right: left / right,
    }
    _UNARY = {
        ast.UAdd: lambda value: value,
        ast.USub: lambda value: -value,
    }

    def __init__(self, expression: Any):
        self.expression = str(expression or "").strip()
        if not self.expression:
            raise CustomConstraintError("Expression cannot be blank.")
        try:
            self.tree = ast.parse(self.expression, mode="eval")
        except SyntaxError as exc:
            raise CustomConstraintError(
                f"Invalid expression '{self.expression}': {exc.msg}."
            ) from exc
        self.names = set()
        self._validate(self.tree)

    def _validate(self, node):
        if isinstance(node, ast.Expression):
            self._validate(node.body)
            return
        if isinstance(node, ast.Name):
            self.names.add(node.id)
            return
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise CustomConstraintError("Only numeric constants are allowed.")
            return
        if isinstance(node, ast.BinOp) and type(node.op) in self._BINARY:
            self._validate(node.left)
            self._validate(node.right)
            return
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._UNARY:
            self._validate(node.operand)
            return
        raise CustomConstraintError(
            "Expressions may contain field names, numbers, parentheses, and +, -, *, /."
        )

    def evaluate(self, fields: Mapping[str, Any]) -> float:
        normalized = {str(key).lower(): value for key, value in (fields or {}).items()}

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Name):
                key = node.id.lower()
                if key not in normalized:
                    raise CustomConstraintError(
                        f"Field '{node.id}' is unavailable for this source."
                    )
                try:
                    value = float(normalized[key])
                except (TypeError, ValueError) as exc:
                    raise CustomConstraintError(
                        f"Field '{node.id}' is not numeric for this source."
                    ) from exc
                if not math.isfinite(value):
                    raise CustomConstraintError(
                        f"Field '{node.id}' is not finite for this source."
                    )
                return value
            if isinstance(node, ast.Constant):
                return float(node.value)
            if isinstance(node, ast.UnaryOp):
                return self._UNARY[type(node.op)](visit(node.operand))
            if isinstance(node, ast.BinOp):
                left = visit(node.left)
                right = visit(node.right)
                if isinstance(node.op, ast.Div) and abs(right) <= 1e-12:
                    raise CustomConstraintError(
                        f"Expression '{self.expression}' divides by zero."
                    )
                return self._BINARY[type(node.op)](left, right)
            raise CustomConstraintError("Unsupported expression element.")

        result = float(visit(self.tree))
        if not math.isfinite(result):
            raise CustomConstraintError(
                f"Expression '{self.expression}' did not produce a finite value."
            )
        return result


def normalize_custom_constraints(values):
    """Return deterministic, unique constraint definitions for saved projects."""
    normalized = []
    used_keys = set()
    for index, raw in enumerate(values or []):
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"Constraint {index + 1}").strip()
        key = constraint_key(raw.get("key") or name)
        base_key = key
        suffix = 2
        while key in used_keys:
            key = f"{base_key}_{suffix}"
            suffix += 1
        numerator = str(raw.get("numerator") or "").strip()
        denominator = str(raw.get("denominator") or "1").strip()
        if not numerator:
            continue
        SafeNumericExpression(numerator)
        SafeNumericExpression(denominator)
        normalized.append({
            "name": name,
            "key": key,
            "numerator": numerator,
            "denominator": denominator,
            "enabled": bool(raw.get("enabled", True)),
        })
        used_keys.add(key)
    return normalized


def event_constraint_fields(event) -> dict:
    """Return numeric expression inputs for one optimiser source event."""
    fields = constraint_property_fields(
        getattr(event, "source_properties", None),
        getattr(event, "balance", None),
        getattr(event, "source_property_kinds", None),
    )
    fields.update({
        "one": 1.0,
        "is_direct_tip": 1.0 if getattr(event, "is_grade_block", False) else 0.0,
        "is_grade_block": 1.0 if getattr(event, "is_grade_block", False) else 0.0,
        "is_stockpile": 1.0 if getattr(event, "is_stockpile", False) else 0.0,
        "is_amt": 1.0 if getattr(event, "is_amt", False) else 0.0,
        "is_inventory": (
            1.0
            if getattr(event, "is_stockpile", False)
            and not getattr(event, "is_amt", False)
            else 0.0
        ),
        "source_balance": getattr(event, "balance", None),
        "selected_fe": getattr(event, "grade_fe", None),
        "selected_si": getattr(event, "grade_si", None),
        "selected_al": getattr(event, "grade_al", None),
        "selected_p": getattr(event, "grade_p", None),
        "selected_mn": getattr(event, "grade_mn", None),
        "grade_fe": getattr(event, "grade_fe", None),
        "grade_si": getattr(event, "grade_si", None),
        "grade_al": getattr(event, "grade_al", None),
        "grade_p": getattr(event, "grade_p", None),
        "grade_mn": getattr(event, "grade_mn", None),
    })
    return fields


def mapping_constraint_fields(record, property_kinds=None) -> dict:
    """Return expression inputs for a manual/report source mapping."""
    record = record or {}
    source_type = str(record.get("source_type") or "").strip().lower()
    is_grade_block = source_type == "grade_block"
    is_stockpile = source_type == "stockpile"
    is_amt = bool(record.get("is_amt", False))
    properties = record.get("source_properties") or {}
    fields = constraint_property_fields(
        properties,
        record.get(
            "constraint_source_balance",
            record.get(
                "source_opening_balance",
                record.get("balance"),
            ),
        ),
        property_kinds or record.get("source_property_kinds"),
    )
    fields.update({
        "one": 1.0,
        "is_direct_tip": 1.0 if is_grade_block else 0.0,
        "is_grade_block": 1.0 if is_grade_block else 0.0,
        "is_stockpile": 1.0 if is_stockpile else 0.0,
        "is_amt": 1.0 if is_amt else 0.0,
        "is_inventory": 1.0 if is_stockpile and not is_amt else 0.0,
        "source_balance": record.get(
            "source_opening_balance", record.get("balance")
        ),
    })
    for analyte in ("fe", "si", "al", "p", "mn"):
        value = record.get(
            f"source_grade_{analyte}", record.get(f"grade_{analyte}")
        )
        fields[f"selected_{analyte}"] = value
        fields[f"grade_{analyte}"] = value
    return fields


def source_properties_from_mapping(record) -> dict:
    """Flatten numeric source inputs using the same names as Database View."""
    if hasattr(record, "to_dict") and not isinstance(record, Mapping):
        record = record.to_dict()
    if not isinstance(record, Mapping):
        record = {}
    properties = {}
    nested_properties = record.get("source_properties") or record.get(
        "SOURCE_PROPERTIES"
    ) or {}
    if not nested_properties:
        raw_json = record.get("source_properties_json") or record.get(
            "SOURCE_PROPERTIES_JSON"
        )
        if isinstance(raw_json, str) and raw_json.strip():
            try:
                nested_properties = json.loads(raw_json)
            except (TypeError, ValueError):
                nested_properties = {}
    if isinstance(nested_properties, Mapping):
        for key, raw_value in nested_properties.items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                safe_key = canonical_property_key(key)
                if safe_key:
                    properties[safe_key] = value
    for key, raw_value in record.items():
        if isinstance(raw_value, bool):
            safe_key = canonical_property_key(key)
            if safe_key:
                properties[safe_key] = float(raw_value)
            continue
        if isinstance(raw_value, (dict, list, tuple, set)):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            safe_key = canonical_property_key(key)
            if safe_key:
                properties[safe_key] = value

    modelled = record.get("modelled_properties") or record.get(
        "MODELLED_PROPERTIES"
    ) or {}
    if isinstance(modelled, Mapping):
        for name, raw_value in (modelled.get("values") or {}).items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                raw_name = canonical_property_key(name)
                if raw_name:
                    # Canonical raw aliases make inventory, AMT and mapped APS
                    # properties interchangeable in custom expressions.  Keep
                    # the explicit modelled_* alias for audit/backward use.
                    properties.setdefault(raw_name, value)
                    properties[f"modelled_{raw_name}"] = value
        for name, raw_value in (modelled.get("coverage") or {}).items():
            try:
                value = float(raw_value) * 100.0
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                properties[
                    f"modelled_{str(name).lower()}_coverage_pct"
                ] = value

    grade_streams = record.get("grade_streams") or record.get("GRADE_STREAMS")
    if isinstance(grade_streams, Mapping):
        for key, raw_value in flatten_grade_streams(grade_streams).items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                properties[key] = value
    return properties


def custom_constraint_coefficients(event_pool, definition):
    """Compatibility wrapper returning the compiled linear ratio terms."""
    compiled = compile_event_custom_constraint(event_pool, definition)
    return compiled["numerator_values"], compiled["denominator_values"]


def _declared_property_kind(name, property_kinds):
    return {
        canonical_property_key(key): str(value).strip().lower()
        for key, value in dict(property_kinds or {}).items()
    }.get(canonical_property_key(name))


def _compiled_expression_side(
    sources,
    expression_text,
    *,
    fields_for_source,
    properties_for_source,
    balance_for_source,
    kinds_for_source,
    weights_for_source,
    source_name,
    constraint_name,
):
    expression = SafeNumericExpression(expression_text)
    canonical_names = {
        canonical_property_key(name) for name in expression.names
    }

    # ``one`` is retained for old saved projects, but is a scalar constant --
    # it is not repeated once for every selected physical tonne.
    if not canonical_names or canonical_names <= {"one"}:
        try:
            constant = expression.evaluate({"one": 1.0})
        except CustomConstraintError as exc:
            raise CustomConstraintError(
                f"{constraint_name}: {exc}"
            ) from exc
        return {
            "kind": "constant",
            "constant": constant,
            "expression_values": [constant for _ in sources],
            "weight_values": [0.0 for _ in sources],
            "linear_values": [0.0 for _ in sources],
            "weight_field": None,
        }

    has_additive = False
    intensive_weight_fields = set()
    for source in sources:
        kinds = kinds_for_source(source)
        weights = {
            canonical_property_key(key): canonical_property_key(value)
            for key, value in dict(weights_for_source(source) or {}).items()
            if canonical_property_key(key) and canonical_property_key(value)
        }
        for name in canonical_names:
            if name in BUILTIN_CONSTRAINT_FIELDS:
                if name in {"source_balance"}:
                    has_additive = True
                else:
                    intensive_weight_fields.add("__physical_source_wmt__")
                continue
            kind = source_property_kind(name, kinds)
            if kind == "additive":
                has_additive = True
                continue
            if kind == "runtime":
                raise CustomConstraintError(
                    f"{constraint_name}: field '{name}' is runtime metadata, "
                    "not an additive or weighted-average Define Fields value."
                )
            weight_field = weights.get(name)
            if _declared_property_kind(name, kinds) == "weighted_average" and not weight_field:
                raise CustomConstraintError(
                    f"{constraint_name}: weighted-average field '{name}' has no "
                    "declared additive weight field."
                )
            intensive_weight_fields.add(
                weight_field or "__physical_source_wmt__"
            )

    expression_values = []
    for source in sources:
        try:
            expression_values.append(
                expression.evaluate(fields_for_source(source))
            )
        except CustomConstraintError as exc:
            raise CustomConstraintError(
                f"{constraint_name}: {source_name(source)}: {exc}"
            ) from exc

    if has_additive:
        # Additive expressions are already expressed per physical source WMT
        # by constraint_property_fields(). Multiplying by the selected
        # physical WMT therefore gives the selected additive contribution.
        return {
            "kind": "sum",
            "constant": 0.0,
            "expression_values": expression_values,
            "weight_values": [1.0 for _ in sources],
            "linear_values": expression_values,
            "weight_field": None,
        }

    if len(intensive_weight_fields) != 1:
        fields = ", ".join(sorted(intensive_weight_fields))
        raise CustomConstraintError(
            f"{constraint_name}: weighted-average fields in expression "
            f"'{expression.expression}' do not share one weight field ({fields})."
        )
    weight_field = next(iter(intensive_weight_fields))
    weight_values = []
    for source in sources:
        balance = float(balance_for_source(source) or 0.0)
        if balance <= 1e-12:
            weight_values.append(0.0)
            continue
        if weight_field == "__physical_source_wmt__":
            weight_values.append(1.0)
            continue
        properties = {
            canonical_property_key(key): value
            for key, value in dict(properties_for_source(source) or {}).items()
        }
        if weight_field not in properties:
            raise CustomConstraintError(
                f"{constraint_name}: {source_name(source)}: additive weight "
                f"field '{weight_field}' is unavailable."
            )
        try:
            weight = float(properties[weight_field])
        except (TypeError, ValueError) as exc:
            raise CustomConstraintError(
                f"{constraint_name}: {source_name(source)}: additive weight "
                f"field '{weight_field}' is not numeric."
            ) from exc
        if not math.isfinite(weight) or weight < 0:
            raise CustomConstraintError(
                f"{constraint_name}: {source_name(source)}: additive weight "
                f"field '{weight_field}' must be finite and non-negative."
            )
        weight_values.append(weight / balance)
    return {
        "kind": "weighted_average",
        "constant": 0.0,
        "expression_values": expression_values,
        "weight_values": weight_values,
        "linear_values": [
            value * weight
            for value, weight in zip(expression_values, weight_values)
        ],
        "weight_field": weight_field,
    }


def _combine_compiled_sides(numerator, denominator, constraint_name):
    numerator_kind = numerator["kind"]
    denominator_kind = denominator["kind"]
    zeroes = [0.0 for _ in numerator["linear_values"]]
    numerator_constant = 0.0
    denominator_constant = 0.0

    if numerator_kind == "constant" and denominator_kind == "constant":
        numerator_values = zeroes
        denominator_values = zeroes
        numerator_constant = numerator["constant"]
        denominator_constant = denominator["constant"]
    elif denominator_kind == "constant":
        if numerator_kind == "weighted_average":
            numerator_values = numerator["linear_values"]
            denominator_values = [
                denominator["constant"] * value
                for value in numerator["weight_values"]
            ]
        else:
            numerator_values = numerator["linear_values"]
            denominator_values = zeroes
            denominator_constant = denominator["constant"]
    elif numerator_kind == "constant":
        if denominator_kind == "weighted_average":
            numerator_values = [
                numerator["constant"] * value
                for value in denominator["weight_values"]
            ]
            denominator_values = denominator["linear_values"]
        else:
            numerator_values = zeroes
            denominator_values = denominator["linear_values"]
            numerator_constant = numerator["constant"]
    elif numerator_kind == "sum" and denominator_kind == "sum":
        numerator_values = numerator["linear_values"]
        denominator_values = denominator["linear_values"]
    elif (
        numerator_kind == "weighted_average"
        and denominator_kind == "weighted_average"
        and numerator["weight_field"] == denominator["weight_field"]
    ):
        # The common weighted-average denominator cancels.
        numerator_values = numerator["linear_values"]
        denominator_values = denominator["linear_values"]
    else:
        raise CustomConstraintError(
            f"{constraint_name}: a ratio between a summed additive expression "
            "and a weighted-average expression is nonlinear and cannot be "
            "passed to the linear solver. Use an additive weighted-mass field "
            "or a constant on the other side."
        )

    if denominator_constant < 0 or any(value < -1e-12 for value in denominator_values):
        raise CustomConstraintError(
            f"{constraint_name}: denominator must be non-negative."
        )
    if denominator_constant <= 1e-12 and not any(
        value > 1e-12 for value in denominator_values
    ):
        raise CustomConstraintError(
            f"{constraint_name}: denominator is zero for every available source."
        )
    return {
        "numerator_values": numerator_values,
        "denominator_values": denominator_values,
        "numerator_constant": numerator_constant,
        "denominator_constant": denominator_constant,
    }


def _compile_custom_constraint(
    sources,
    definition,
    *,
    fields_for_source,
    properties_for_source,
    balance_for_source,
    kinds_for_source,
    weights_for_source,
    source_name,
):
    sources = list(sources or [])
    constraint_name = str(
        definition.get("name") or definition.get("key") or "Custom constraint"
    )
    common = {
        "sources": sources,
        "fields_for_source": fields_for_source,
        "properties_for_source": properties_for_source,
        "balance_for_source": balance_for_source,
        "kinds_for_source": kinds_for_source,
        "weights_for_source": weights_for_source,
        "source_name": source_name,
        "constraint_name": constraint_name,
    }
    numerator = _compiled_expression_side(
        expression_text=definition.get("numerator"), **common
    )
    denominator = _compiled_expression_side(
        expression_text=definition.get("denominator") or "1", **common
    )
    compiled = _combine_compiled_sides(
        numerator, denominator, constraint_name
    )
    compiled.update({
        "definition": definition,
        "numerator_side": numerator,
        "denominator_side": denominator,
    })
    return compiled


def compile_event_custom_constraint(event_pool, definition):
    return _compile_custom_constraint(
        event_pool,
        definition,
        fields_for_source=event_constraint_fields,
        properties_for_source=lambda event: getattr(
            event, "source_properties", None
        ),
        balance_for_source=lambda event: getattr(event, "balance", None),
        kinds_for_source=lambda event: getattr(
            event, "source_property_kinds", None
        ),
        weights_for_source=lambda event: getattr(
            event, "source_property_weights", None
        ),
        source_name=lambda event: (
            getattr(event, "source_name", None)
            or getattr(event, "stockpile", None)
            or getattr(event, "grade_block", None)
            or "source"
        ),
    )


def compile_mapping_custom_constraint(
    source_rows, definition, property_kinds=None, property_weights=None
):
    return _compile_custom_constraint(
        source_rows,
        definition,
        fields_for_source=lambda row: mapping_constraint_fields(
            row, property_kinds
        ),
        properties_for_source=lambda row: row.get("source_properties") or {},
        balance_for_source=lambda row: row.get(
            "constraint_source_balance",
            row.get("source_opening_balance", row.get("balance")),
        ),
        kinds_for_source=lambda row: property_kinds
        or row.get("source_property_kinds"),
        weights_for_source=lambda row: property_weights
        or row.get("source_property_weights"),
        source_name=lambda row: row.get("source") or row.get("source_id")
        or "source",
    )


def custom_constraint_aggregate_totals(compiled, selected_quantities):
    quantities = list(selected_quantities or [])

    def aggregate(side):
        if side["kind"] == "constant":
            return side["constant"]
        if side["kind"] == "sum":
            return sum(
                value * quantity
                for value, quantity in zip(
                    side["linear_values"], quantities
                )
            )
        weighted_mass = sum(
            value * quantity
            for value, quantity in zip(side["linear_values"], quantities)
        )
        selected_weight = sum(
            value * quantity
            for value, quantity in zip(side["weight_values"], quantities)
        )
        return (
            weighted_mass / selected_weight
            if selected_weight > 1e-12 else None
        )

    return aggregate(compiled["numerator_side"]), aggregate(
        compiled["denominator_side"]
    )


def custom_constraint_source_report_values(compiled, index, quantity):
    result = []
    for side_name in ("numerator_side", "denominator_side"):
        side = compiled[side_name]
        if side["kind"] == "constant":
            result.extend((None, 0.0))
        elif side["kind"] == "weighted_average":
            result.extend((
                side["expression_values"][index],
                side["linear_values"][index] * quantity,
            ))
        else:
            result.extend((
                side["linear_values"][index],
                side["linear_values"][index] * quantity,
            ))
    return tuple(result)


def constraint_report_fields(definition, numerator_total, denominator_total, minimum=None, maximum=None):
    key = constraint_key(definition.get("key") or definition.get("name"))
    prefix = f"custom_constraint_{key}"
    return {
        f"{prefix}_name": str(definition.get("name") or key),
        f"{prefix}_numerator_expression": str(
            definition.get("numerator") or ""
        ),
        f"{prefix}_denominator_expression": str(
            definition.get("denominator") or "1"
        ),
        f"{prefix}_numerator": numerator_total,
        f"{prefix}_denominator": denominator_total,
        f"{prefix}_actual_ratio": (
            numerator_total / denominator_total
            if numerator_total is not None
            and denominator_total is not None
            and abs(denominator_total) > 1e-12 else None
        ),
        f"{prefix}_target_min": minimum,
        f"{prefix}_target_max": maximum,
    }
