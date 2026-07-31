"""Load and aggregate scenario-relative OPF reconciliation factors."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from classes.GradeStreams import ANALYTES, configured_brands, is_dry_plant, normalise_brand, normalise_opf, numeric
from database.DatabaseContext import get_database_path
from setup.OpeningStockpileInventories import OpeningStockpileInventories


class DataStreamReconciliation:
    LOOKBACK_DAYS = (7, 14, 21, 28, 30)
    SQL_PATH = Path(__file__).with_name("sql") / "opf_daily_reconciliation.sql"
    SQL_ANALYTE = {"fe": "FE", "si": "SIO2", "al": "AL2O3", "p": "P", "mn": "MN"}

    def __init__(self, inventory_loader=None):
        self.inventory_loader = inventory_loader or OpeningStockpileInventories()

    @staticmethod
    def _scenario_datetime(value: Any) -> datetime:
        if hasattr(value, "toPyDateTime"):
            value = value.toPyDateTime()
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("Australia/Perth").tz_localize(None)
        return timestamp.to_pydatetime()

    def fetch_daily(self, scenario_start: Any) -> pd.DataFrame:
        scenario_start = self._scenario_datetime(scenario_start)
        # Completed SHIFT_DATEs only: the upper bound is the scenario calendar
        # date, never the partially-completed scenario day.
        end_date = scenario_start.date()
        start_date = end_date - timedelta(days=max(self.LOOKBACK_DAYS))
        query = self.SQL_PATH.read_text(encoding="utf-8")
        parameters = (start_date, end_date, start_date, end_date, start_date, end_date)
        connection = self.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise ConnectionError("Unable to connect to Snowflake for OPF reconciliation factors.")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()
                columns = [column[0].upper() for column in cursor.description]
            finally:
                cursor.close()
        finally:
            connection.close()
        result = pd.DataFrame(rows, columns=columns)
        if not result.empty:
            result["DERIVED_OPERATION"] = result["DERIVED_OPERATION"].map(normalise_opf)
            result["BRAND"] = result["BRAND"].map(normalise_brand)
            result["SHIFT_DATE"] = pd.to_datetime(result["SHIFT_DATE"], errors="coerce")
        return result

    @staticmethod
    def _weighted_factor(frame: pd.DataFrame, value_column: str, weight_column: str):
        if frame.empty or value_column not in frame or weight_column not in frame:
            return None
        values = pd.to_numeric(frame[value_column], errors="coerce")
        weights = pd.to_numeric(frame[weight_column], errors="coerce")
        valid = values.notna() & weights.notna() & (values > 0) & (weights > 0)
        if not valid.any():
            return None
        return float((values[valid] * weights[valid]).sum() / weights[valid].sum())

    def _shortest_factor(self, frame, scenario_start, value_column, weight_column):
        if frame is None or frame.empty or "SHIFT_DATE" not in frame.columns:
            return None, None, 0
        end_date = pd.Timestamp(self._scenario_datetime(scenario_start).date())
        for days in self.LOOKBACK_DAYS:
            start_date = end_date - pd.Timedelta(days=days)
            window = frame[(frame["SHIFT_DATE"] >= start_date) & (frame["SHIFT_DATE"] < end_date)]
            value = self._weighted_factor(window, value_column, weight_column)
            if value is not None:
                return value, days, int(len(window))
        return None, None, 0

    def aggregate(self, daily: pd.DataFrame, opf: Any, brands: Iterable[str], scenario_start: Any):
        opf_key = normalise_opf(opf)
        requested_brands = configured_brands(brands)
        frame = daily[daily["DERIVED_OPERATION"] == opf_key].copy() if not daily.empty else daily.copy()
        available_brands = sorted(
            brand for brand in frame.get("BRAND", pd.Series(dtype=str)).dropna().unique()
            if brand and brand != "*"
        )
        result = {}
        warnings = []
        for brand in requested_brands:
            source_brand = brand
            brand_frame = frame[frame["BRAND"] == brand] if not frame.empty else frame
            substituted = False
            if brand_frame.empty and available_brands:
                # Random as requested, but seeded to make the choice reproducible
                # within saved/reloaded scenarios.
                rng = random.Random(f"{opf_key}|{brand}|{self._scenario_datetime(scenario_start).date()}")
                source_brand = rng.choice(available_brands)
                brand_frame = frame[frame["BRAND"] == source_brand]
                substituted = True
                warnings.append(
                    f"{opf_key} / {brand}: no history in 30 days; using {source_brand}."
                )
            record = {
                "source_brand": source_brand,
                "substituted_brand": substituted,
                "source_brand_by_analyte": {},
                "blend": {},
                "regression": {},
                "lookback_days": {},
            }
            for analyte, sql_analyte in self.SQL_ANALYTE.items():
                blend, blend_days, blend_rows = self._shortest_factor(
                    brand_frame, scenario_start, f"BLEND_RECON_{sql_analyte}", "FEED_WMT"
                )
                if is_dry_plant(opf_key):
                    regression, regression_days, regression_rows = 1.0, blend_days, blend_rows
                else:
                    regression, regression_days, regression_rows = self._shortest_factor(
                        brand_frame, scenario_start, f"REGRESSION_RECON_{sql_analyte}", "PROD_WMT"
                    )
                blend_source = source_brand
                regression_source = source_brand
                if blend is None:
                    candidates = []
                    for alternative in available_brands:
                        candidate_frame = frame[frame["BRAND"] == alternative]
                        candidate = self._shortest_factor(
                            candidate_frame,
                            scenario_start,
                            f"BLEND_RECON_{sql_analyte}",
                            "FEED_WMT",
                        )
                        if candidate[0] is not None:
                            candidates.append((alternative, candidate))
                    if candidates:
                        rng = random.Random(
                            f"{opf_key}|{brand}|blend|{analyte}|{self._scenario_datetime(scenario_start).date()}"
                        )
                        blend_source, (blend, blend_days, blend_rows) = rng.choice(candidates)
                        warnings.append(
                            f"{opf_key} / {brand} / {analyte}: blend recon uses {blend_source}."
                        )
                if regression is None and not is_dry_plant(opf_key):
                    candidates = []
                    for alternative in available_brands:
                        candidate_frame = frame[frame["BRAND"] == alternative]
                        candidate = self._shortest_factor(
                            candidate_frame,
                            scenario_start,
                            f"REGRESSION_RECON_{sql_analyte}",
                            "PROD_WMT",
                        )
                        if candidate[0] is not None:
                            candidates.append((alternative, candidate))
                    if candidates:
                        rng = random.Random(
                            f"{opf_key}|{brand}|regression|{analyte}|{self._scenario_datetime(scenario_start).date()}"
                        )
                        regression_source, (regression, regression_days, regression_rows) = rng.choice(candidates)
                        warnings.append(
                            f"{opf_key} / {brand} / {analyte}: regression recon uses {regression_source}."
                        )
                if blend is None:
                    blend = 1.0
                    warnings.append(f"{opf_key} / {brand} / {analyte}: blend recon defaulted to 1.0.")
                if regression is None:
                    regression = 1.0
                    warnings.append(f"{opf_key} / {brand} / {analyte}: regression recon defaulted to 1.0.")
                record["blend"][analyte] = {
                    "calculated": blend, "effective": blend, "locked": False, "row_count": blend_rows
                }
                record["regression"][analyte] = {
                    "calculated": regression,
                    "effective": regression,
                    "locked": is_dry_plant(opf_key),
                    "row_count": regression_rows,
                }
                record["lookback_days"][analyte] = {
                    "blend": blend_days,
                    "regression": regression_days,
                }
                record["source_brand_by_analyte"][analyte] = {
                    "blend": blend_source,
                    "regression": regression_source,
                }
            result[brand] = record
        if opf_key == "IB_OPF" or (not available_brands and requested_brands):
            warnings.append(f"{opf_key}: no assayed brand history; factors defaulted to 1.0.")
        return result, list(dict.fromkeys(warnings))

    def fetch(self, scenario_start: Any, opf: Any, brands: Iterable[str]):
        factors, warnings = self.aggregate(self.fetch_daily(scenario_start), opf, brands, scenario_start)
        self.save_to_database(opf, scenario_start, factors, warnings)
        return factors, warnings

    @staticmethod
    def default_factors(opf: Any, brands: Iterable[str], warning: str = ""):
        opf_key = normalise_opf(opf)
        factors = {}
        for brand in configured_brands(brands):
            factors[brand] = {
                "source_brand": brand,
                "substituted_brand": False,
                "source_brand_by_analyte": {
                    a: {"blend": brand, "regression": brand} for a in ANALYTES
                },
                "lookback_days": {a: {"blend": None, "regression": None} for a in ANALYTES},
                "blend": {
                    a: {"calculated": 1.0, "effective": 1.0, "locked": False, "row_count": 0}
                    for a in ANALYTES
                },
                "regression": {
                    a: {"calculated": 1.0, "effective": 1.0, "locked": is_dry_plant(opf_key), "row_count": 0}
                    for a in ANALYTES
                },
            }
        return factors, ([warning] if warning else [])

    @staticmethod
    def save_to_database(opf, scenario_start, factors, warnings):
        connection = sqlite3.connect(get_database_path())
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS data_stream_reconciliation (
                    opf TEXT NOT NULL,
                    scenario_start TEXT NOT NULL,
                    factors_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (opf, scenario_start)
                )"""
            )
            connection.execute(
                """INSERT OR REPLACE INTO data_stream_reconciliation
                   (opf, scenario_start, factors_json, warnings_json, saved_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    normalise_opf(opf),
                    str(pd.Timestamp(scenario_start)),
                    json.dumps(factors),
                    json.dumps(warnings),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            connection.commit()
        finally:
            connection.close()
