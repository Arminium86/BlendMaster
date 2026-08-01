from datetime import datetime, timedelta

import pandas as pd

from classes.PeriodManager import PeriodManager
from setup.OpeningStockpileInventories import OpeningStockpileInventories


class PlanningPlanTargets:
    """Load 2WP OPF-feed targets and map them to BlendMaster product builds."""

    OPERATION_BY_MINE_CRUSHER = {
        ("CC", "OPF01"): {"CHRISTMAS CREEK 1"},
        ("CC", "OPF02"): {"CHRISTMAS CREEK 2"},
        ("CB", "OPF01"): {"CLOUDBREAK CENTRAL"},
        ("CB", "OPF02"): {"CLOUDBREAK WEST"},
        ("CB", "OPF03"): {"CLOUDBREAK WEST"},
        ("CB", "OPF04"): {"CLOUDBREAK WEST"},
        ("EW", "EW_OPF"): {"ELIWANA"},
        ("FT", "FT_OPF"): {"FIRETAIL"},
        ("IB", "CRUSHER"): {"IRON BRIDGE"},
        ("KV", "VK_OPF"): {"KINGS"},
    }

    OPERATION_BY_MINE_OPF = {
        ("CC", "CC OPF01"): {"CHRISTMAS CREEK 1"},
        ("CC", "CC OPF02"): {"CHRISTMAS CREEK 2"},
        ("CB", "CB OPF"): {"CLOUDBREAK CENTRAL", "CLOUDBREAK WEST"},
        ("EW", "EW OPF"): {"ELIWANA"},
        ("FT", "FT OPF"): {"FIRETAIL"},
        ("IB", "IB OPF"): {"IRON BRIDGE"},
        ("KV", "KV OPF"): {"KINGS"},
    }

    QUERY = """
        SELECT
            OPERATION,
            PERIOD_START,
            PERIOD_END,
            PRODUCT_TYPE,
            VALUE,
            FE,
            SIO2,
            AL2O3,
            P,
            MN
        FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.PLANNING_PLAN_DATA
        WHERE UPPER(TRIM(PLANNING_CATEGORY)) = UPPER(TRIM(%s))
          AND UPPER(TRIM(HORIZON)) = '2 WEEK'
          AND CONTAINS(SCENARIO, %s)
          AND PERIOD_START < %s
          AND PERIOD_END > %s
        QUALIFY COALESCE(VERSION, 0) = MAX(COALESCE(VERSION, 0)) OVER (
            PARTITION BY SCENARIO, PLANNING_CATEGORY, HORIZON
        )
        ORDER BY ALL
    """

    def __init__(self, inventory_loader=None):
        self.inventory_loader = inventory_loader or OpeningStockpileInventories()

    @staticmethod
    def latest_wednesday_scenario(start_time):
        if hasattr(start_time, "toPyDateTime"):
            start_time = start_time.toPyDateTime()
        if not isinstance(start_time, datetime):
            start_time = pd.to_datetime(start_time).to_pydatetime()
        latest_wednesday = start_time - timedelta(days=(start_time.weekday() - 2) % 7)
        return latest_wednesday.strftime("%Y%m%d")

    @staticmethod
    def normalize_crusher(crusher):
        value = str(crusher or "").strip().upper().replace("-", "_")
        aliases = {
            "OPF1": "OPF01",
            "OPF2": "OPF02",
            "OPF3": "OPF03",
            "OPF4": "OPF04",
            "OPF": "EW_OPF",
        }
        return aliases.get(value, value)

    @classmethod
    def operations_for(cls, mine, crusher=None, opf=None):
        mine = str(mine or "").strip().upper()
        normalized_opf = " ".join(str(opf or "").strip().upper().split())
        if normalized_opf:
            return cls.OPERATION_BY_MINE_OPF.get((mine, normalized_opf), set())
        return cls.OPERATION_BY_MINE_CRUSHER.get(
            (mine, cls.normalize_crusher(crusher)),
            set(),
        )

    @staticmethod
    def normalize_brand(product_type, mine, configured_brands):
        mine = str(mine or "").strip().upper()
        product = str(product_type or "").strip().upper()
        brands = [str(value).strip().upper() for value in configured_brands or [] if str(value).strip()]

        if mine == "IB" and not product:
            return "IBC"
        if mine == "CB" and (not product or "CBSF" in product):
            return "SF"
        for brand in sorted(brands, key=len, reverse=True):
            if brand and brand in product:
                return brand
        return product or ("IBC" if mine == "IB" else "")

    @staticmethod
    def _number(value, default=0.0):
        if pd.isna(value):
            return float(default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _perth_wall_clock(value):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("Australia/Perth").tz_localize(None)
        return timestamp.to_pydatetime()

    @staticmethod
    def _perth_wall_clock_series(values):
        converted = pd.to_datetime(values, errors="coerce")
        if getattr(converted.dt, "tz", None) is not None:
            converted = converted.dt.tz_convert("Australia/Perth").dt.tz_localize(None)
        return converted

    def fetch(
        self,
        mine,
        crusher,
        start_time,
        configured_brands=None,
        opf=None,
        crusher_contribution_ratio=1.0,
        planning_period_count=3,
        planning_category="OPF Feed",
    ):
        if hasattr(start_time, "toPyDateTime"):
            start_time = start_time.toPyDateTime()
        if not isinstance(start_time, datetime):
            start_time = pd.to_datetime(start_time).to_pydatetime()
        start_time = self._perth_wall_clock(start_time)
        operations = self.operations_for(mine, crusher, opf=opf)
        if not operations:
            context = f"{mine} / {opf} / {crusher}" if opf else f"{mine} / {crusher}"
            raise ValueError(f"No 2WP operation mapping exists for {context}.")

        try:
            crusher_contribution_ratio = float(crusher_contribution_ratio)
        except (TypeError, ValueError):
            raise ValueError("Crusher contribution ratio must be numeric.")
        if not 0 < crusher_contribution_ratio <= 1:
            raise ValueError("Crusher contribution ratio must be greater than 0 and no more than 100%.")

        scenario = self.latest_wednesday_scenario(start_time)
        periods = PeriodManager(planning_period_count)
        periods.calculate_periods(start_time)
        horizon_end = periods.horizon_end()
        connection = self.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise ConnectionError("Unable to connect to Snowflake for 2WP product-build targets.")

        try:
            cursor = connection.cursor()
            try:
                planning_category = str(planning_category or "OPF Feed")
                cursor.execute(
                    self.QUERY,
                    (planning_category, scenario, horizon_end, start_time),
                )
                rows = cursor.fetchall()
                columns = [column[0].upper() for column in cursor.description]
            finally:
                cursor.close()
        finally:
            connection.close()

        data = pd.DataFrame(rows, columns=columns)
        if data.empty:
            return []

        data["OPERATION_KEY"] = data["OPERATION"].astype(str).str.strip().str.upper()
        data = data[data["OPERATION_KEY"].isin(operations)].copy()
        if data.empty:
            return []

        data["PERIOD_START"] = self._perth_wall_clock_series(data["PERIOD_START"])
        data["PERIOD_END"] = self._perth_wall_clock_series(data["PERIOD_END"])
        data = data[
            data["PERIOD_START"].notna()
            & data["PERIOD_END"].notna()
            & (data["PERIOD_START"] < pd.Timestamp(horizon_end))
            & (data["PERIOD_END"] > pd.Timestamp(start_time))
        ].sort_values(["PERIOD_START", "PERIOD_END", "PRODUCT_TYPE"])

        builds = []
        for _, row in data.iterrows():
            planning_target_tonnes = self._number(row.get("VALUE"))
            target_tonnes = planning_target_tonnes * crusher_contribution_ratio
            if target_tonnes <= 0:
                continue
            brand = self.normalize_brand(row.get("PRODUCT_TYPE"), mine, configured_brands)
            grades = {
                "fe": self._number(row.get("FE")),
                "si": self._number(row.get("SIO2")),
                "al": self._number(row.get("AL2O3")),
                "p": self._number(row.get("P")),
                "mn": self._number(row.get("MN")),
            }
            build = {
                "build_id": len(builds) + 1,
                "brand": brand,
                "target_tonnes": target_tonnes,
                "planning_target_tonnes": planning_target_tonnes,
                "crusher_contribution_ratio": crusher_contribution_ratio,
                "opf": str(opf or "").strip(),
                "crusher": str(crusher or "").strip(),
                "planning_operation": str(row.get("OPERATION") or "").strip(),
                "planning_period_start": row["PERIOD_START"].to_pydatetime(),
                "planning_period_end": row["PERIOD_END"].to_pydatetime(),
                "planning_scenario": scenario,
            }
            # Planning Plan provides a lower-bound target for Fe and
            # upper-bound targets for the contaminants. Keep the opposite
            # bounds open instead of turning each target into an exact-grade
            # equality.
            build["target_fe_min"] = grades["fe"]
            build["target_fe_max"] = 100.0
            for grade in ("si", "al", "p", "mn"):
                build[f"target_{grade}_min"] = 0.0
                build[f"target_{grade}_max"] = grades[grade]
            builds.append(build)

        brand_counts = {}
        for build in builds:
            brand = build["brand"]
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
            build["build_name"] = f"{brand} Build {brand_counts[brand]}" if brand else f"Build {build['build_id']}"
        return builds

    @classmethod
    def group_builds_by_brand(cls, builds):
        """Combine only consecutive 2WP rows that share the same brand."""
        grade_fields = [
            f"target_{grade}_{bound}"
            for grade in ("fe", "si", "al", "p", "mn")
            for bound in ("min", "max")
        ]
        groups = []
        for build in builds or []:
            brand = str(build.get("brand") or "").strip().upper()
            if not groups or groups[-1]["brand"] != brand:
                groups.append({
                    "brand": brand,
                    "target_tonnes": 0.0,
                    "planning_target_tonnes": 0.0,
                    "_weighted_grades": {
                        field: 0.0 for field in grade_fields
                    },
                    "_operations": [],
                    "_template": dict(build),
                    "_starts": [],
                    "_ends": [],
                })
            group = groups[-1]
            tonnes = cls._number(build.get("target_tonnes"))
            group["target_tonnes"] += tonnes
            group["planning_target_tonnes"] += cls._number(
                build.get("planning_target_tonnes")
            )
            for field in grade_fields:
                group["_weighted_grades"][field] += (
                    cls._number(build.get(field)) * tonnes
                )
            operation = str(
                build.get("planning_operation") or ""
            ).strip()
            if operation and operation not in group["_operations"]:
                group["_operations"].append(operation)
            if build.get("planning_period_start") is not None:
                group["_starts"].append(
                    build["planning_period_start"]
                )
            if build.get("planning_period_end") is not None:
                group["_ends"].append(
                    build["planning_period_end"]
                )

        combined = []
        brand_counts = {}
        for group in groups:
            brand = group["brand"]
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
            total = group["target_tonnes"]
            result = group["_template"]
            result.update({
                "build_id": len(combined) + 1,
                "build_name": (
                    f"{brand} Build {brand_counts[brand]}"
                    if brand else f"Build {len(combined) + 1}"
                ),
                "brand": brand,
                "target_tonnes": total,
                "planning_target_tonnes": (
                    group["planning_target_tonnes"]
                ),
                "planning_operation": ", ".join(
                    group["_operations"]
                ),
            })
            if group["_starts"]:
                result["planning_period_start"] = min(
                    group["_starts"]
                )
            if group["_ends"]:
                result["planning_period_end"] = max(
                    group["_ends"]
                )
            for field in grade_fields:
                result[field] = (
                    group["_weighted_grades"][field] / total
                    if total > 0 else 0.0
                )
            combined.append(result)
        return combined
