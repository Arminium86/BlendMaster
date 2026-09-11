from __future__ import annotations

from typing import Iterable, Mapping, Optional

import pandas as pd
from classes.ProductQualityLimits import QUALITY_FIELDS, with_quality_configuration
from classes.ProductTargetModes import require_supported_target_modes, target_mode_fields
from classes.SoftProductGrades import analyte_audit, objective_config, source_preference, SIMILARITY_TOTALS
from classes.ProductQualityReport import QUALITY_REPORT_SUFFIXES, quality_state_audit, serialize_audit

from classes.ProductBuildLanes import (
    normalized_build_lane, lane_kind,
    BYPRODUCT_LANES,
    PRODUCT_LANE,
    lane_grade_column,
    lane_grade_weight_column,
    lane_source_tonnes_column,
)


_PRODUCT_BUILD_GRADES = ("fe", "si", "al", "p", "mn")
_PRODUCT_BUILD_BASE_SUFFIXES = [
    "id", "name", "brand", "opf", "target_tonnes", "opening_tonnes",
    "added_tonnes", "closing_tonnes", "remaining_tonnes", "complete",
    "current_on_spec", "complete_on_spec",
    *[f"grade_{grade}" for grade in _PRODUCT_BUILD_GRADES],
    *[
        f"target_{grade}_{bound}"
        for grade in _PRODUCT_BUILD_GRADES for bound in ("min", "max")
    ],
    *QUALITY_FIELDS,
    *QUALITY_REPORT_SUFFIXES,
]


class ProductBuildProgress:
    """Annotate source-level blend rows with cumulative product-build state."""

    GRADES = _PRODUCT_BUILD_GRADES
    TOLERANCE = 0.1
    BASE_SUFFIXES = _PRODUCT_BUILD_BASE_SUFFIXES
    COLUMNS = [
        *[f"product_build_{suffix}" for suffix in _PRODUCT_BUILD_BASE_SUFFIXES],
        *[
            f"product_build_{lane}_{suffix}"
            for lane in BYPRODUCT_LANES
            for suffix in _PRODUCT_BUILD_BASE_SUFFIXES
        ],
    ]

    @staticmethod
    def _number(value, default=0.0):
        try:
            if pd.isna(value):
                return float(default)
        except (TypeError, ValueError):
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @classmethod
    def ensure_columns(cls, report):
        """Add the complete audit schema together, avoiding fragmented frames."""
        missing = [column for column in cls.COLUMNS if column not in report.columns]
        if not missing:
            return report
        return pd.concat([report, pd.DataFrame({
            column: pd.Series(None, index=report.index, dtype=object) for column in missing
        })], axis=1)

    @classmethod
    def normalize_builds(
        cls, product_build_settings: Optional[Iterable[Mapping]]
    ):
        builds = []
        for index, setting in enumerate(product_build_settings or []):
            if not isinstance(setting, Mapping):
                continue
            target_tonnes = cls._number(
                setting.get("target_tonnes"), 0.0
            )
            if target_tonnes <= 0:
                continue
            build = {
                **dict(setting),
                "build_id": setting.get("build_id") or index + 1,
                "build_name": str(
                    setting.get("build_name") or f"Build {index + 1}"
                ),
                "brand": str(setting.get("brand") or "").strip().upper(),
                "byproduct": str(
                    setting.get("byproduct") or ""
                ).strip().lower(),
                "target_tonnes": target_tonnes,
            }
            for grade in cls.GRADES:
                build[f"target_{grade}_min"] = cls._number(
                    setting.get(f"target_{grade}_min"), 0.0
                )
                build[f"target_{grade}_max"] = cls._number(
                    setting.get(f"target_{grade}_max"), 100.0
                )
            builds.append(with_quality_configuration(build))
        return builds

    @classmethod
    def _is_on_spec(cls, tonnes, grade_metal, build, grade_weights=None):
        require_supported_target_modes([build])
        if tonnes <= 0:
            return False
        for grade in cls.GRADES:
            denominator = (grade_weights or {}).get(grade, tonnes)
            if denominator <= 0:
                return False
            value = grade_metal[grade] / denominator
            if target_mode_fields(build)["target_mode"] == "soft":
                if not analyte_audit(build, grade, grade_metal[grade], denominator)["within_limits"]:
                    return False
            elif (
                value < build[f"target_{grade}_min"] - 1e-7
                or value > build[f"target_{grade}_max"] + 1e-7
            ):
                return False
        return True

    @classmethod
    def annotate(
        cls,
        report,
        product_build_settings: Optional[Iterable[Mapping]],
        byproducts_enabled=None,
        solver_config=None,
    ):
        result = (
            report.copy()
            if isinstance(report, pd.DataFrame)
            else pd.DataFrame(report or [])
        ).reset_index(drop=True)
        result = cls.ensure_columns(result)

        # Reports written before quantity-stream support have no explicit
        # product-build quantity. Preserve their historical readability only;
        # current reports always carry the column and a present zero remains
        # zero rather than falling back to ROM/crusher tonnes.
        if (
            "product_build_source_tonnes" not in result.columns
            and "source_actual_tonnes" in result.columns
        ):
            result["product_build_source_tonnes"] = result[
                "source_actual_tonnes"
            ]

        builds = cls.normalize_builds(product_build_settings)
        if byproducts_enabled is None:
            byproducts_enabled = any(
                build.get("byproduct") in BYPRODUCT_LANES for build in builds
            )
        if any(build.get("opf_scope") for build in builds):
            for lane in dict.fromkeys(normalized_build_lane(b, byproducts_enabled) for b in builds):
                lane_builds = [b for b in builds if normalized_build_lane(b, byproducts_enabled) == lane]
                result = cls._annotate_lane(result, lane_builds, lane=lane, prefix=f"product_build_{lane}", solver_config=solver_config)
                # Existing report controls show the build associated with each
                # physical row; scoped audit columns retain every build lane.
                eligible = result.get("opf", pd.Series("", index=result.index)).isin(lane_builds[0].get("contributing_opfs", []))
                prefix = "product_build" if lane_kind(lane) == PRODUCT_LANE else f"product_build_{lane_kind(lane)}"
                for suffix in cls.BASE_SUFFIXES:
                    column = f"product_build_{lane}_{suffix}"
                    if column in result:
                        result.loc[eligible, f"{prefix}_{suffix}"] = result.loc[eligible, column]
            return result
        if byproducts_enabled:
            for lane in BYPRODUCT_LANES:
                lane_builds = [
                    build for build in builds if build.get("byproduct") == lane
                ]
                result = cls._annotate_lane(
                    result,
                    lane_builds,
                    lane=lane,
                    prefix=f"product_build_{lane}",
                    solver_config=solver_config,
                )
            return result
        return cls._annotate_lane(
            result,
            builds,
            lane=PRODUCT_LANE,
            prefix="product_build",
            solver_config=solver_config,
        )

    @classmethod
    def _annotate_lane(cls, result, builds, *, lane, prefix, solver_config=None):
        tonnes_column = lane_source_tonnes_column(lane)
        required = {
            "crusher_actual_tonnes",
            tonnes_column,
            *(
                lane_grade_column(lane, grade)
                for grade in cls.GRADES
            ),
        }
        if result.empty or not builds or not required.issubset(result.columns):
            return result

        data = result.copy()
        data["_original_index"] = data.index
        data["_start_sort"] = pd.to_datetime(
            data.get("start_datetime"), errors="coerce"
        )
        sort_columns = [
            column for column in (
                "_start_sort",
                "steady_state_number",
                "blend_ID",
                "blend_option",
                "_original_index",
            )
            if column in data.columns
        ]
        data = data.sort_values(sort_columns, kind="stable")
        group_columns = [
            column for column in (
                "steady_state_number",
                "start_datetime",
                "end_datetime",
                "blend_ID",
                "blend_option",
            )
            if column in data.columns
        ]
        if not group_columns:
            data["_single_state"] = 1
            group_columns = ["_single_state"]

        build_index = 0
        build_tonnes = 0.0
        grade_metal = {grade: 0.0 for grade in cls.GRADES}
        grade_weights = {grade: 0.0 for grade in cls.GRADES}
        preferences = objective_config((solver_config or {}).get("soft_grade_preferences"))
        cumulative_sources = {a: dict.fromkeys(SIMILARITY_TOTALS, 0.0) for a in cls.GRADES}

        for _, state_rows in data.groupby(
            group_columns, sort=False, dropna=False
        ):
            if build_index >= len(builds):
                break

            build = builds[build_index]
            opening_tonnes = build_tonnes
            product_tonnes = cls._number(
                pd.to_numeric(
                    state_rows.get(tonnes_column, pd.Series()),
                    errors="coerce",
                ).fillna(0).sum(),
                0.0,
            )
            remaining_before = max(
                build["target_tonnes"] - opening_tonnes, 0.0
            )
            added_tonnes = min(
                max(product_tonnes, 0.0), remaining_before
            )
            allocation_fraction = (
                added_tonnes / product_tonnes
                if product_tonnes > 0 else 0.0
            )

            opening_metal, opening_weights = dict(grade_metal), dict(grade_weights)
            source_totals = {a: dict.fromkeys(SIMILARITY_TOTALS, 0.0) for a in cls.GRADES}
            for grade in cls.GRADES:
                for _, row in state_rows.iterrows():
                    weight = cls._number(
                        row.get(lane_grade_weight_column(lane, grade)),
                        cls._number(
                            row.get(tonnes_column),
                            0.0,
                        ),
                    ) * allocation_fraction
                    grade_weights[grade] += weight
                    grade_metal[grade] += weight * cls._number(
                        row.get(lane_grade_column(lane, grade)), 0.0
                    )
                    if target_mode_fields(build)["target_mode"] == "soft":
                        source = source_preference(grade, cls._number(row.get(lane_grade_column(lane, grade))),
                                  build.get(f"target_{grade}_target"), weight,
                                  str(row.get("source_type", "")).lower() in {"grade_block", "direct_tip"}, preferences)
                        for key in SIMILARITY_TOTALS:
                            source_totals[grade][key] += source[key]
                            cumulative_sources[grade][key] += source[key]

            build_tonnes = min(
                opening_tonnes + added_tonnes,
                build["target_tonnes"],
            )
            remaining_tonnes = max(
                build["target_tonnes"] - build_tonnes, 0.0
            )
            complete = remaining_tonnes <= cls.TOLERANCE
            current_on_spec = cls._is_on_spec(
                build_tonnes, grade_metal, build, grade_weights
            )
            values = {
                f"{prefix}_opf": build.get("opf", ""),
                **{f"{prefix}_{key}": build[key] for key in QUALITY_FIELDS},
                f"{prefix}_id": build["build_id"],
                f"{prefix}_name": build["build_name"],
                f"{prefix}_brand": build["brand"],
                f"{prefix}_target_tonnes": build["target_tonnes"],
                f"{prefix}_opening_tonnes": opening_tonnes,
                f"{prefix}_added_tonnes": added_tonnes,
                f"{prefix}_closing_tonnes": build_tonnes,
                f"{prefix}_remaining_tonnes": remaining_tonnes,
                f"{prefix}_complete": complete,
                f"{prefix}_current_on_spec": current_on_spec,
                f"{prefix}_complete_on_spec": (
                    current_on_spec if complete else None
                ),
            }
            audit = quality_state_audit(build, opening_metal, opening_weights,
                       {a: grade_metal[a] - opening_metal[a] for a in cls.GRADES},
                       {a: grade_weights[a] - opening_weights[a] for a in cls.GRADES},
                       source_totals, cumulative_sources, preferences)
            selected = [r for r in audit["rows"] if r["grain"] == r["evaluation_basis"]]
            status = ("Hard limit breached" if any(r["quality_status"] == "Hard limit breached" for r in selected)
                      else "Soft limit breached" if any(r["quality_status"] == "Soft limit breached" for r in selected)
                      else "No production" if not added_tonnes else "Within limits")
            values.update({f"{prefix}_target_mode": build["target_mode"],
                           f"{prefix}_evaluation_basis": build["target_evaluation_basis"],
                           f"{prefix}_quality_status": status,
                           f"{prefix}_hard_limits_satisfied": all(r["hard_limits_satisfied"] for r in selected),
                           f"{prefix}_soft_grade_penalty": sum(r["applied_penalty"] for r in selected),
                           f"{prefix}_source_similarity_penalty": sum(r["applied_similarity_penalty"] for r in selected),
                           f"{prefix}_quality_audit": serialize_audit(audit)})
            for grade in cls.GRADES:
                values[f"{prefix}_grade_{grade}"] = (
                    grade_metal[grade] / grade_weights[grade]
                    if grade_weights[grade] > 0 else 0.0
                )
                values[f"{prefix}_target_{grade}_min"] = (
                    build[f"target_{grade}_min"]
                )
                values[f"{prefix}_target_{grade}_max"] = (
                    build[f"target_{grade}_max"]
                )

            for original_index in state_rows["_original_index"]:
                for column, value in values.items():
                    result.at[original_index, column] = value

            if complete:
                cumulative_sources = {a: dict.fromkeys(SIMILARITY_TOTALS, 0.0) for a in cls.GRADES}
                build_index += 1
                build_tonnes = 0.0
                grade_metal = {
                    grade: 0.0 for grade in cls.GRADES
                }
                grade_weights = {
                    grade: 0.0 for grade in cls.GRADES
                }

        return result
