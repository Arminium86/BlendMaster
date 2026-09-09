import math
from collections import defaultdict

import pandas as pd

from classes.GradeBlockIdentity import parent_grade_block_name


class MaterialDestinationPlan:
    """Build final material destinations from APS payloads and blend decisions."""

    TOLERANCE = 1e-7
    COLUMNS = [
        "plan_type",
        "plan_id",
        "grade_block",
        "planned_2wp_destination",
        "fallback_destination",
        "two_wp_destination_resolution",
        "assigned_destination",
        "alternate_destination_1",
        "alternate_destination_2",
        "assigned_destination_type",
        "source_tonnes",
        "assigned_tonnes",
        "assigned_ratio",
        "assignment_source",
    ]

    @staticmethod
    def _frame(value):
        if isinstance(value, pd.DataFrame):
            return value.copy()
        return pd.DataFrame(value or [])

    @staticmethod
    def _number(value, default=0.0):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _text(value):
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        return str(value).strip()

    @staticmethod
    def _truthy(value):
        return str(value).strip().lower() in {"true", "1", "yes"}

    @classmethod
    def _tonnage_tolerance(cls, *values):
        """Allow harmless solver/database rounding, not material shortfalls."""
        scale = max(
            [abs(cls._number(value)) for value in values] + [1.0]
        )
        return max(cls.TOLERANCE, scale * 1e-6)

    @classmethod
    def _payload_ids(cls, value):
        return [
            item.strip()
            for item in cls._text(value).split(",")
            if item.strip()
        ]

    @classmethod
    def _normalized_rules(cls, rules):
        normalized = []
        for rule in rules or []:
            if isinstance(rule, dict):
                source = (
                    rule.get("grade_block_source")
                    or rule.get("source_pattern")
                )
                destination = (
                    rule.get("crusher_destination")
                    or rule.get("destination")
                )
            elif isinstance(rule, (list, tuple)) and len(rule) >= 2:
                source, destination = rule[0], rule[1]
            else:
                continue
            source = cls._text(source)
            destination = cls._text(destination)
            if source and destination:
                normalized.append((source.upper(), destination))
        return normalized

    @classmethod
    def _crusher_destination(
        cls,
        payload,
        default_destination,
        movement_rules,
    ):
        source = cls._text(payload.get("source")).upper()
        for source_pattern, destination in movement_rules:
            if source_pattern in source:
                return destination

        if isinstance(default_destination, (list, tuple, set)):
            choices = [
                cls._text(value)
                for value in default_destination
                if cls._text(value)
            ]
            if choices:
                return choices[0]
        else:
            selected_destination = cls._text(default_destination)
            if selected_destination:
                return selected_destination

        planned_type = cls._text(payload.get("destination_type")).lower()
        planned_destination = cls._text(payload.get("planned_destination"))
        if planned_type == "crusher" and planned_destination:
            return planned_destination
        return "Crusher"

    @classmethod
    def _destination_columns(cls, payload):
        resolution = cls._text(
            payload.get("two_wp_destination_resolution")
        )
        resolved_destination = (
            cls._text(payload.get("destination"))
            or cls._text(payload.get("fallback_destination"))
            or cls._text(payload.get("planned_destination"))
        )

        if resolution in {"pit_fallback", "last_destination_fallback", "spatial_fallback", "nearby_fallback"}:
            return "", resolved_destination, resolution

        planned_destination = (
            cls._text(payload.get("planned_destination"))
            or resolved_destination
        )
        fallback_destination = cls._text(
            payload.get("fallback_destination")
        )
        if fallback_destination == planned_destination:
            fallback_destination = ""
        return planned_destination, fallback_destination, resolution

    @classmethod
    def _base_row(cls, payload, plan_type, plan_id):
        planned, fallback, resolution = cls._destination_columns(payload)
        return {
            "plan_type": cls._text(plan_type).lower(),
            "plan_id": cls._text(plan_id) or "Primary",
            "payload_id": cls._text(payload.get("direct_tip_id")),
            "grade_block": cls._text(payload.get("source")),
            "mining_start_datetime": payload.get("start_datetime"),
            "delivered_datetime": payload.get("delivered_datetime"),
            "payload_tonnes": cls._number(payload.get("payload")),
            "planned_2wp_destination": planned,
            "fallback_destination": fallback,
            "two_wp_destination_resolution": resolution,
            "alternate_destination_1": cls._text(
                payload.get("alternate_destination_1")
            ),
            "alternate_destination_2": cls._text(
                payload.get("alternate_destination_2")
            ),
        }

    @classmethod
    def _row_alternates(cls, base_row, assigned_destination, direct_tip=False):
        candidates = []
        if direct_tip:
            candidates.append(
                base_row.get("planned_2wp_destination")
                or base_row.get("fallback_destination")
            )
        candidates.extend([
            base_row.get("alternate_destination_1"),
            base_row.get("alternate_destination_2"),
        ])
        assigned_key = cls._text(assigned_destination).upper()
        seen = {assigned_key} if assigned_key else set()
        alternates = []
        for candidate in candidates:
            destination = cls._text(candidate)
            key = destination.upper()
            if not destination or key in seen:
                continue
            seen.add(key)
            alternates.append(destination)
        return (alternates + ["", ""])[:2]

    @classmethod
    def _prepared_payloads(cls, payload_transactions):
        payloads = cls._frame(payload_transactions).reset_index(drop=True)
        if payloads.empty:
            return payloads
        if "direct_tip_id" not in payloads.columns:
            payloads["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(payloads))
            ]
        payloads["direct_tip_id"] = payloads["direct_tip_id"].map(cls._text)
        payloads["payload"] = pd.to_numeric(
            payloads.get("payload"), errors="coerce"
        ).fillna(0.0)
        payloads = payloads[payloads["payload"] > cls.TOLERANCE].copy()
        return payloads

    @classmethod
    def _allocation_candidates(
        cls,
        report_row,
        payloads,
        remaining_by_id,
        requested_tonnes=0.0,
    ):
        known_ids = set(payloads["direct_tip_id"])
        identifiers = [
            identifier
            for identifier in cls._payload_ids(report_row.get("source_id"))
            if identifier in known_ids
            and remaining_by_id[identifier] > cls.TOLERANCE
        ]
        requested_tonnes = cls._number(requested_tonnes)
        identified_tonnes = sum(
            remaining_by_id[identifier] for identifier in identifiers
        )
        if (
            identifiers
            and identified_tonnes
            + cls._tonnage_tolerance(identified_tonnes, requested_tonnes)
            >= requested_tonnes
        ):
            return identifiers

        # Grade-block rows are grouped for reporting by source. In that
        # process the displayed source_id list can occasionally omit one of
        # the payload IDs even though its tonnes remain in the grouped total.
        # Keep the explicit IDs first, then supplement them from payloads for
        # the same source and steady-state window when they cannot cover the
        # reported tonnes.
        source = parent_grade_block_name(report_row.get("source"))
        candidates = payloads[
            payloads["source"].map(parent_grade_block_name).eq(source)
        ].copy()
        if candidates.empty:
            return []

        state_start = pd.to_datetime(
            report_row.get("start_datetime"), errors="coerce"
        )
        state_end = pd.to_datetime(
            report_row.get("end_datetime"), errors="coerce"
        )
        delivered = pd.to_datetime(
            candidates.get("delivered_datetime"), errors="coerce"
        )
        if pd.notna(state_start) and pd.notna(state_end):
            in_state = candidates[
                (delivered >= state_start) & (delivered < state_end)
            ]
            if not in_state.empty:
                candidates = in_state

        source_identifiers = [
            identifier
            for identifier in candidates["direct_tip_id"].tolist()
            if remaining_by_id[identifier] > cls.TOLERANCE
        ]
        return identifiers + [
            identifier
            for identifier in source_identifiers
            if identifier not in identifiers
        ]

    @classmethod
    def summarize_parent_grade_blocks(cls, report):
        """Collapse sliced grade blocks while preserving report dimensions.

        Source tonnes are repeated across a grade block's destination rows, so
        take one source total per original slice before summing those slice
        totals to the parent. Assigned tonnes are additive at the full report
        dimensionality. Assigned ratio is derived again after aggregation.
        """
        result = cls._frame(report).reindex(columns=cls.COLUMNS)
        if result.empty:
            return result

        result["_slice_grade_block"] = result["grade_block"].map(cls._text)
        result["grade_block"] = result["_slice_grade_block"].map(
            parent_grade_block_name
        )
        for column in ("source_tonnes", "assigned_tonnes"):
            result[column] = pd.to_numeric(
                result[column], errors="coerce"
            ).fillna(0.0)

        source_totals = (
            result.groupby(
                [
                    "plan_type",
                    "plan_id",
                    "_slice_grade_block",
                    "grade_block",
                ],
                dropna=False,
                as_index=False,
            )["source_tonnes"]
            .max()
            .groupby(
                ["plan_type", "plan_id", "grade_block"],
                dropna=False,
                as_index=False,
            )["source_tonnes"]
            .sum()
        )

        group_columns = [
            column
            for column in cls.COLUMNS
            if column not in {
                "source_tonnes",
                "assigned_tonnes",
                "assigned_ratio",
            }
        ]
        result = (
            result.groupby(
                group_columns, dropna=False, as_index=False
            )["assigned_tonnes"]
            .sum()
            .merge(
                source_totals,
                on=["plan_type", "plan_id", "grade_block"],
                how="left",
            )
        )
        result["assigned_ratio"] = (
            result["assigned_tonnes"]
            / result["source_tonnes"].replace(0, pd.NA)
        ).fillna(0.0)
        return (
            result.reindex(columns=cls.COLUMNS)
            .sort_values(
                [
                    "grade_block",
                    "assigned_destination_type",
                    "assigned_destination",
                ],
                kind="stable",
                na_position="last",
            )
            .reset_index(drop=True)
        )

    @classmethod
    def build_payload_assignments(
        cls,
        payload_transactions,
        blend_report,
        plan_type,
        plan_id="Primary",
        crusher_destination=None,
        direct_tip_movement_rules=None,
    ):
        payloads = cls._prepared_payloads(payload_transactions)
        if payloads.empty:
            return pd.DataFrame(columns=cls.COLUMNS)

        if payloads["direct_tip_id"].duplicated().any():
            duplicates = sorted(
                payloads.loc[
                    payloads["direct_tip_id"].duplicated(keep=False),
                    "direct_tip_id",
                ].unique()
            )
            raise ValueError(
                "Material destination plan requires unique payload IDs. "
                f"Duplicates: {', '.join(duplicates)}"
            )

        payload_by_id = {
            row["direct_tip_id"]: row
            for row in payloads.to_dict(orient="records")
        }
        remaining_by_id = {
            identifier: cls._number(row.get("payload"))
            for identifier, row in payload_by_id.items()
        }
        direct_tip_rows = defaultdict(list)

        report = cls._frame(blend_report)
        if not report.empty and "source_type" in report.columns:
            report = report[
                report["source_type"].astype(str).str.strip().str.lower()
                == "grade_block"
            ].copy()
            report["_assigned_tonnes"] = pd.to_numeric(
                report.get("source_actual_tonnes"), errors="coerce"
            ).fillna(0.0)
            report = report[
                report["_assigned_tonnes"] > cls.TOLERANCE
            ]
            sort_columns = [
                column
                for column in (
                    "steady_state_number",
                    "start_datetime",
                    "source",
                )
                if column in report.columns
            ]
            if sort_columns:
                report = report.sort_values(sort_columns, kind="stable")

            for report_row in report.to_dict(orient="records"):
                requested = cls._number(report_row.get("_assigned_tonnes"))
                identifiers = cls._allocation_candidates(
                    report_row,
                    payloads,
                    remaining_by_id,
                    requested,
                )
                available = sum(
                    remaining_by_id[identifier]
                    for identifier in identifiers
                )
                reconciliation_tolerance = cls._tonnage_tolerance(
                    requested, available
                )
                if requested > available + reconciliation_tolerance:
                    raise ValueError(
                        "Direct-tip tonnes in the blend report exceed the "
                        f"available payload tonnes for "
                        f"'{cls._text(report_row.get('source'))}'."
                    )
                requested = min(requested, available)
                unallocated = requested
                for position, identifier in enumerate(identifiers):
                    if unallocated <= cls.TOLERANCE:
                        break
                    capacity = remaining_by_id[identifier]
                    capacity_total = sum(
                        remaining_by_id[candidate]
                        for candidate in identifiers[position:]
                    )
                    if position == len(identifiers) - 1:
                        assigned = min(unallocated, capacity)
                    else:
                        assigned = min(
                            capacity,
                            requested * capacity / available
                            if available > cls.TOLERANCE
                            else 0.0,
                        )
                        assigned = min(assigned, unallocated)
                        if capacity_total <= unallocated + cls.TOLERANCE:
                            assigned = capacity
                    if assigned <= cls.TOLERANCE:
                        continue
                    remaining_by_id[identifier] -= assigned
                    unallocated -= assigned
                    direct_tip_rows[identifier].append(
                        (assigned, report_row)
                    )
                if unallocated > cls.TOLERANCE:
                    raise ValueError(
                        "Unable to reconcile all direct-tip tonnes with APS "
                        f"payload IDs for '{cls._text(report_row.get('source'))}'."
                    )

        rows = []
        movement_rules = cls._normalized_rules(
            direct_tip_movement_rules
        )
        direct_tip_label = (
            "Manual Direct Tip"
            if cls._text(plan_type).lower() == "manual"
            else "Optimised Direct Tip"
        )

        for identifier, payload in payload_by_id.items():
            payload_tonnes = cls._number(payload.get("payload"))
            base_row = cls._base_row(payload, plan_type, plan_id)

            for assigned_tonnes, report_row in direct_tip_rows[identifier]:
                assigned_destination = cls._crusher_destination(
                    payload,
                    crusher_destination,
                    movement_rules,
                )
                alternate_1, alternate_2 = cls._row_alternates(
                    base_row, assigned_destination, direct_tip=True
                )
                rows.append({
                    **base_row,
                    "assigned_destination": assigned_destination,
                    "alternate_destination_1": alternate_1,
                    "alternate_destination_2": alternate_2,
                    "assigned_destination_type": "Crusher",
                    "assigned_tonnes": assigned_tonnes,
                    "assigned_ratio": (
                        assigned_tonnes / payload_tonnes
                        if payload_tonnes > cls.TOLERANCE else 0.0
                    ),
                    "assignment_source": direct_tip_label,
                    "steady_state_number": report_row.get(
                        "steady_state_number"
                    ),
                })

            stockpile_tonnes = remaining_by_id[identifier]
            if stockpile_tonnes > cls.TOLERANCE:
                planned = base_row["planned_2wp_destination"]
                fallback = base_row["fallback_destination"]
                assigned_destination = planned or fallback
                assignment_source = "2WP" if planned else "Fallback"
                alternate_1, alternate_2 = cls._row_alternates(
                    base_row, assigned_destination
                )
                rows.append({
                    **base_row,
                    "assigned_destination": assigned_destination,
                    "alternate_destination_1": alternate_1,
                    "alternate_destination_2": alternate_2,
                    "assigned_destination_type": "Stockpile",
                    "assigned_tonnes": stockpile_tonnes,
                    "assigned_ratio": (
                        stockpile_tonnes / payload_tonnes
                        if payload_tonnes > cls.TOLERANCE else 0.0
                    ),
                    "assignment_source": assignment_source,
                    "steady_state_number": None,
                })

        if not rows:
            return pd.DataFrame(columns=cls.COLUMNS)

        return pd.DataFrame(rows)

    @classmethod
    def build(
        cls,
        payload_transactions,
        blend_report,
        plan_type,
        plan_id="Primary",
        crusher_destination=None,
        direct_tip_movement_rules=None,
    ):
        """Publish the existing source summary from final payload decisions."""
        result = cls.build_payload_assignments(
            payload_transactions, blend_report, plan_type, plan_id,
            crusher_destination, direct_tip_movement_rules,
        )
        if result.empty:
            return pd.DataFrame(columns=cls.COLUMNS)
        payloads = cls._prepared_payloads(payload_transactions)
        # Payload IDs remain available to the primary allocator before this
        # display-oriented aggregation removes them.
        result["grade_block"] = result["grade_block"].map(
            parent_grade_block_name
        )
        group_columns = [
            "plan_type",
            "plan_id",
            "grade_block",
            "planned_2wp_destination",
            "fallback_destination",
            "two_wp_destination_resolution",
            "assigned_destination",
            "alternate_destination_1",
            "alternate_destination_2",
            "assigned_destination_type",
            "assignment_source",
        ]
        result = (
            result.groupby(group_columns, dropna=False, as_index=False)[
                "assigned_tonnes"
            ]
            .sum()
        )

        source_totals = (
            payloads.assign(
                grade_block=payloads["source"].map(
                    parent_grade_block_name
                )
            )
            .groupby("grade_block", dropna=False)["payload"]
            .sum()
            .to_dict()
        )
        result["source_tonnes"] = result["grade_block"].map(
            lambda source: cls._number(source_totals.get(source))
        )
        result["assigned_ratio"] = result.apply(
            lambda row: (
                cls._number(row["assigned_tonnes"])
                / cls._number(row["source_tonnes"])
                if cls._number(row["source_tonnes"]) > cls.TOLERANCE
                else 0.0
            ),
            axis=1,
        )
        return (
            result.reindex(columns=cls.COLUMNS)
            .sort_values(
                [
                    "grade_block",
                    "assigned_destination_type",
                    "assigned_destination",
                ],
                kind="stable",
                na_position="last",
            )
            .reset_index(drop=True)
        )
