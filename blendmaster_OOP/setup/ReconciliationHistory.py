"""Read shift factors and their complete feed composition for advanced recon.

Standard DataStreamReconciliation retains its own daily query and aggregation.
This service emits two versioned samples per OPF/brand/shift, never one sample
per contributing block. Both factor kinds carry total-period feed as the weight.
Spatial selection, confidence scoring, application and UI are later tasks.
"""

from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
import json
import math
from pathlib import Path

import pandas as pd

from classes.GradeStreams import configured_brands, normalise_opf
from classes.PhaseSchemas import reconciliation_sample, SAMPLE_GRAIN_SHIFT
from setup.DataStreamReconciliation import DataStreamReconciliation
from setup.InventoryBuildLineage import (
    InventoryBuildLineage, LINEAGE_BASIS, block_record,
    canonical_block, clean_text, finite_number,
)
from setup.OpeningStockpileInventories import OpeningStockpileInventories


class ReconciliationHistory:
    SQL_PATH = Path(__file__).with_name("sql") / "opf_shift_reconciliation.sql"
    LINEAGE_SQL_PATH = SQL_PATH.with_name("inventory_build_lineage.sql")
    DEFAULT_LOOKBACK_DAYS = 30
    BUILD_BATCH_SIZE = 100
    REQUIRED_COLUMNS = {
        "DERIVED_OPERATION", "BRAND", "PERIOD_START", "PERIOD_END", "SHIFT",
        "FEED_WMT", "PROD_DMT", "FEED_SOURCES_JSON", "SOURCE_ROWS",
        *[f"{kind}_RECON_{suffix}" for kind in ("BLEND", "REGRESSION")
          for suffix in DataStreamReconciliation.SQL_ANALYTE.values()],
    }

    def __init__(self, inventory_loader=None):
        self.inventory_loader = inventory_loader or OpeningStockpileInventories()

    @staticmethod
    def _bounds(scenario_start, max_lookback_days):
        if isinstance(max_lookback_days, bool):
            raise ValueError("Maximum lookback must be a positive whole number of days.")
        days = finite_number(max_lookback_days)
        if days is None or days < 1 or not days.is_integer():
            raise ValueError("Maximum lookback must be a positive whole number of days.")
        end = DataStreamReconciliation._scenario_datetime(scenario_start)
        if pd.isna(end):
            raise ValueError("A valid scenario start is required.")
        return end - timedelta(days=int(days)), end

    @contextmanager
    def _connection(self):
        connection = self.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise ConnectionError("Unable to connect to Snowflake for reconciliation history.")
        try:
            # This is a dedicated read connection; bound expensive queries too.
            cursor = connection.cursor()
            try:
                cursor.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60")
            finally:
                cursor.close()
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _query(connection, query, parameters):
        cursor = connection.cursor()
        try:
            cursor.execute(query, parameters)
            return pd.DataFrame(cursor.fetchall(), columns=[column[0].upper() for column in cursor.description])
        finally:
            cursor.close()

    @staticmethod
    def _feed_sources(value):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as error:
                raise ValueError("Invalid feed-source lineage JSON.") from error
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError("Feed-source lineage must be an array of source records.")
        return value

    def _fetch_lineage(self, connection, builds, as_of):
        frames = []
        builds = sorted(set(builds))
        for offset in range(0, len(builds), self.BUILD_BATCH_SIZE):
            frames.append(self._query(
                connection, self.LINEAGE_SQL_PATH.read_text(encoding="utf-8"),
                (as_of.strftime("%Y-%m-%d %H:%M:%S.%f") + " +08:00",
                 json.dumps(builds[offset:offset + self.BUILD_BATCH_SIZE])),
            ))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def fetch(self, scenario_start, opf, brands=(), *, max_lookback_days=DEFAULT_LOOKBACK_DAYS):
        """Return (samples, warnings); no SQLite writes or global-factor changes.

        Only fully completed 06:00/18:00 Perth shifts inside the requested
        lookback are used. Returned brands keep their warehouse names (e.g.
        CCFB); a unique existing suffix alias such as FB may select them.
        """
        start, end = self._bounds(scenario_start, max_lookback_days)
        opf = normalise_opf(opf)
        if not opf:
            raise ValueError("An OPF is required for reconciliation history.")
        with self._connection() as connection:
            frame = self._query(connection, self.SQL_PATH.read_text(encoding="utf-8"),
                                (str(start), str(end), opf))
            builds = set()
            for raw_sources in frame.get("FEED_SOURCES_JSON", []):
                for source in self._feed_sources(raw_sources):
                    name = clean_text(source.get("source")).upper()
                    if name and not canonical_block(name):
                        builds.add(name)
            lineage = self._fetch_lineage(connection, builds, end)
        return self.build_samples(frame, lineage, end, opf, brands,
                                  max_lookback_days=max_lookback_days)

    def fetch_inventory_lineage(self, scenario_start, builds):
        """Read exact opening-build lineage for conventional inventory sources.

        Pass each opening inventory row's `build`, never its footprint name.
        Old and current builds of one footprint are deliberately independent.
        """
        _, end = self._bounds(scenario_start, self.DEFAULT_LOOKBACK_DAYS)
        if isinstance(builds, str):
            builds = [builds]
        builds = sorted({clean_text(build).upper() for build in builds if clean_text(build)})
        if not builds:
            return [], []
        with self._connection() as connection:
            frame = self._fetch_lineage(connection, builds, end)
        records = InventoryBuildLineage(frame).opening_records(builds, end)
        return records, list(dict.fromkeys(warning for row in records for warning in row["warnings"]))

    @staticmethod
    def _select_brands(available, brands, warnings):
        requested = configured_brands(brands)
        if not requested:
            return available
        selected = set()
        for brand in requested:
            matches = [brand] if brand in available else [item for item in available if item.endswith(brand)]
            if len(matches) == 1:
                selected.update(matches)
            elif matches:
                warnings.append(f"{brand}: ambiguous warehouse brand alias; no advanced samples selected.")
            else:
                warnings.append(f"{brand}: no advanced history in the requested window; use standard fallback.")
        return selected

    def build_samples(self, frame, lineage_frame, scenario_start, opf, brands=(), *,
                      max_lookback_days=DEFAULT_LOOKBACK_DAYS):
        """Pure conversion seam for fixtures, saved extracts and live SQL rows."""
        start, end = self._bounds(scenario_start, max_lookback_days)
        opf = normalise_opf(opf)
        if frame is None or frame.empty:
            return [], [f"{opf}: no shift history in the requested window; use standard fallback."]
        missing = self.REQUIRED_COLUMNS - set(frame.columns)
        if missing:
            raise ValueError(f"Reconciliation history missing columns: {sorted(missing)}")
        rows, warnings = [], []
        for row in frame.to_dict("records"):
            if normalise_opf(row["DERIVED_OPERATION"]) != opf:
                continue
            period_start = DataStreamReconciliation._scenario_datetime(row["PERIOD_START"])
            period_end = DataStreamReconciliation._scenario_datetime(row["PERIOD_END"])
            if pd.isna(period_start) or pd.isna(period_end) or period_end - period_start != timedelta(hours=12):
                raise ValueError("History contains an invalid shift interval.")
            if period_start < start or period_end > end:
                continue
            row.update(PERIOD_START=period_start, PERIOD_END=period_end,
                       BRAND=clean_text(row["BRAND"]).upper())
            rows.append(row)
        available = {row["BRAND"] for row in rows if row["BRAND"]}
        selected = self._select_brands(available, brands, warnings)
        lineage = InventoryBuildLineage(lineage_frame)
        samples, seen = [], set()
        for row in sorted(rows, key=lambda r: (r["PERIOD_START"], r["BRAND"])):
            brand = row["BRAND"]
            if not brand:
                warnings.append(f"{opf}: feed at {row['PERIOD_START']} has no matching product assay brand.")
                continue
            if brand not in selected:
                continue
            key = (opf, brand, row["PERIOD_START"])
            if key in seen:
                raise ValueError(f"Duplicate reconciliation period: {key}.")
            seen.add(key)
            feed = finite_number(row["FEED_WMT"])
            if feed is None or feed <= 0.0:
                warnings.append(f"{opf} / {brand}: non-positive or invalid period feed; sample skipped.")
                continue
            source_totals = defaultdict(float)
            quality = []
            for source in self._feed_sources(row["FEED_SOURCES_JSON"]):
                name = clean_text(source.get("source")).upper()
                tonnes = finite_number(source.get("feed_wmt"))
                if tonnes is None:
                    quality.append("A feed source has invalid tonnes; lineage unavailable.")
                else:
                    source_totals[name] += tonnes
            if any(value < 0 for value in source_totals.values()):
                quality.append("Negative net feed-source tonnes; lineage unavailable.")
            if not math.isclose(math.fsum(source_totals.values()), feed, rel_tol=1e-8, abs_tol=1e-6):
                quality.append("Feed-source tonnes do not reconcile to total period feed; lineage unavailable.")
            attributed = defaultdict(float)
            if not quality:
                for name, tonnes in sorted(source_totals.items()):
                    if tonnes <= 0:
                        continue
                    block = canonical_block(name)
                    if block:
                        attributed[block] += tonnes
                    elif name:
                        fractions, reasons = lineage.composition(name, row["PERIOD_END"])
                        quality.extend(reasons)
                        for block, fraction in fractions.items():
                            attributed[block] += tonnes * fraction
                    else:
                        quality.append("An unnamed feed source has no lineage.")
            known = math.fsum(attributed.values())
            unknown = max(feed - known, 0.0)
            if unknown > max(1e-6, feed * 1e-8):
                quality.append(f"{unknown:.6f} WMT of period feed has no attributed grade-block lineage.")
            for kind in ("blend", "regression"):
                factors = {}
                factor_warnings = []
                for analyte, suffix in DataStreamReconciliation.SQL_ANALYTE.items():
                    factor = finite_number(row[f"{kind.upper()}_RECON_{suffix}"])
                    factors[analyte] = factor if factor is not None and factor > 0 else None
                    if factors[analyte] is None:
                        factor_warnings.append(f"{kind} / {analyte}: no valid period factor.")
                sample_warnings = [*dict.fromkeys(quality), *factor_warnings]
                sample = reconciliation_sample(
                    opf, brand, kind, row["PERIOD_START"], row["PERIOD_END"],
                    grain=SAMPLE_GRAIN_SHIFT, factors=factors, feed_wmt=feed,
                    product_dmt=finite_number(row["PROD_DMT"]),
                    source_rows=row["SOURCE_ROWS"],
                    contributing_blocks=[block_record(block, tonnes) for block, tonnes in sorted(attributed.items())],
                    provenance={
                        "sample_id": f"{opf}|{brand}|{row['PERIOD_START'].isoformat()}|{kind}",
                        "shift": clean_text(row["SHIFT"]),
                        "lineage_basis": LINEAGE_BASIS,
                        "attributed_feed_wmt": known,
                        "unattributed_feed_wmt": unknown,
                        "lineage_coverage": min(known / feed, 1.0),
                        "product_wmt": finite_number(row.get("PROD_WMT")),
                        "cbfl_campaign": bool(finite_number(row.get("CBFL_CAMPAIGN")) or 0),
                        "warnings": sample_warnings,
                    },
                )
                samples.append(sample)
                warnings.extend(f"{opf} / {brand} / {row['PERIOD_START']}: {message}" for message in sample_warnings)
        if not samples and not warnings:
            warnings.append(f"{opf}: no completed shift history in the requested window; use standard fallback.")
        return samples, list(dict.fromkeys(warnings))
