"""Bounded product records, exact-window offline cache and DMT aggregation.

Records use AWST production transaction time. Assay sampling and warehouse update
times are retained independently; neither moves production to a different date.
"""

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

import pandas as pd

from classes.GradeStreams import normalise_opf
from setup.InventoryBuildLineage import clean_text, finite_number
from setup.OpeningStockpileInventories import OpeningStockpileInventories


ANALYTE_COLUMNS = dict(fe="FE", si="SIO2", al="AL2O3", p="P", mn="MN")
GRAINS = {"observations": "Raw observations", "shift": "Shift (DMT weighted)", "day": "Daily (DMT weighted)"}


def awst(value):
    """Timezone-naive input is already AWST, matching scenario/UI conventions."""
    if hasattr(value, "toPyDateTime"):
        value = value.toPyDateTime()
    if value is None or value == "":
        raise ValueError("A valid date and time is required.")
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("A valid date and time is required.")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Australia/Perth").tz_localize(None)
    return timestamp.to_pydatetime()


def optional_time(value):
    try:
        return awst(value).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class ProductAssayUnavailable(ConnectionError):
    pass


class ProductAssayHistory:
    VERSION = 2
    MAX_DAYS = 93
    MAX_RECORDS = 200000
    CACHE_ENTRIES = 24
    SQL_PATH = Path(__file__).with_name("sql") / "opf_product_assay_history.sql"
    SOURCE = "AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_OPF_PRODUCT"

    def __init__(self, inventory_loader=None, cache_directory=None, *, clock=None, cache_seconds=300):
        self.inventory_loader = inventory_loader or OpeningStockpileInventories()
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
        self.cache_directory = Path(cache_directory) if cache_directory is not None else base / "BlendMaster" / "product_assay_history"
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.cache_seconds = cache_seconds
        self._lock = threading.RLock()

    def request(self, start, end, opfs):
        start, end = awst(start), awst(end)
        if not timedelta(0) < end - start <= timedelta(days=self.MAX_DAYS):
            raise ValueError(f"Choose a positive report window of at most {self.MAX_DAYS} days.")
        if isinstance(opfs, str):
            opfs = [opfs]
        opfs = sorted({normalise_opf(opf) for opf in opfs if clean_text(opf)})
        if not opfs:
            raise ValueError("Select at least one OPF.")
        return dict(version=self.VERSION, source=self.SOURCE, start=start.isoformat(), end=end.isoformat(), opfs=opfs,
                    timestamp_basis="production_transaction_awst", query_hash=hashlib.sha256(self.SQL_PATH.read_bytes()).hexdigest())

    def _path(self, request):
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        return self.cache_directory / f"assay-v{self.VERSION}-{key}.json"

    def _read_cache(self, request):
        try:
            cached = json.loads(self._path(request).read_text(encoding="utf-8"))
            if cached["request"] == request and isinstance(cached["records"], list):
                awst(cached["fetched_at"])
                required = {"opf", "brand", "observed_at", "grades", "dmt"}
                if all(isinstance(r, dict) and required <= r.keys()
                       and r["opf"] in request["opfs"] and isinstance(r["brand"], str)
                       and request["start"] <= awst(r["observed_at"]).isoformat() < request["end"]
                       and isinstance(r["grades"], dict) and ANALYTE_COLUMNS.keys() <= r["grades"].keys()
                       and all(v is None or (finite_number(v) is not None and 0 <= v <= 100) for v in r["grades"].values())
                       and (r["dmt"] is None or (isinstance(r["dmt"], (int, float)) and finite_number(r["dmt"]) is not None))
                       for r in cached["records"]):
                    return cached
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _write_cache(self, payload):
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, allow_nan=False)
            temporary.replace(self._path(payload["request"]))
            files = sorted((p for p in self.cache_directory.iterdir()
                            if re.fullmatch(r"assay-v[12]-[a-f0-9]{64}\.json", p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[self.CACHE_ENTRIES:]:
                old.unlink(missing_ok=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _query(self, request):
        connection = self.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise ProductAssayUnavailable("Snowflake connection could not be established.")
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60")
                cursor.execute(self.SQL_PATH.read_text(encoding="utf-8"),
                               (request["start"] + " +08:00", request["end"] + " +08:00", json.dumps(request["opfs"])))
                rows = cursor.fetchall()
                if len(rows) > self.MAX_RECORDS:
                    raise ValueError("More than 200,000 product records match. Choose a shorter window or fewer OPFs.")
                return [dict(zip((c[0].upper() for c in cursor.description), row)) for row in rows]
            finally:
                cursor.close()
        finally:
            connection.close()

    @staticmethod
    def normalize(rows, request):
        records, invalid_grades, invalid_dates, missing_weights = [], 0, 0, 0
        start, end = awst(request["start"]), awst(request["end"])
        for row in rows:
            opf, brand = normalise_opf(row.get("OPF")), clean_text(row.get("BRAND")).upper()
            if opf not in request["opfs"] or not brand:
                continue
            timestamp = optional_time(row.get("OBSERVED_AT"))
            if timestamp is None:
                invalid_dates += 1
                continue
            if not start <= awst(timestamp) < end:
                continue
            grades = {}
            for analyte, column in ANALYTE_COLUMNS.items():
                value = finite_number(row.get(column))
                if value is not None and not 0 <= value <= 100:
                    value = None
                    invalid_grades += 1
                grades[analyte] = value
            dmt = finite_number(row.get("DMT"))
            if dmt is None or dmt <= 0:
                missing_weights += 1
            shift = clean_text(row.get("SHIFT")).title()
            shift_date = optional_time(row.get("SHIFT_DATE"))
            shift_start = None
            if shift_date and shift in {"Day", "Night"}:
                shift_start = awst(shift_date).replace(hour=6 if shift == "Day" else 18).isoformat()
            records.append(dict(opf=opf, brand=brand, observed_at=timestamp, sampled_at=optional_time(row.get("SAMPLED_AT")),
                                last_updated=optional_time(row.get("LAST_UPDATED")), period_start=optional_time(row.get("PERIOD_START")),
                                period_end=optional_time(row.get("PERIOD_END")), shift_start=shift_start,
                                build=clean_text(row.get("BUILD")), destination=clean_text(row.get("DESTINATION")),
                                dmt=dmt, wmt=finite_number(row.get("WMT")), grades=grades))
        warnings = []
        if invalid_dates:
            warnings.append(f"{invalid_dates} records without a valid production timestamp were excluded.")
        if invalid_grades:
            warnings.append(f"{invalid_grades} out-of-range assay values are shown as missing.")
        if missing_weights:
            warnings.append(f"{missing_weights} records lack positive DMT and cannot contribute to weighted averages.")
        if records and any(any(v is None for v in r["grades"].values()) for r in records):
            warnings.append("Some assay values are missing. Each analyte average uses only its valid, positive-DMT records.")
        return sorted(records, key=lambda r: (r["observed_at"], r["opf"], r["brand"])), warnings

    def fetch(self, start, end, opfs, *, force_refresh=False):
        """Return records plus freshness metadata; offline fallback is exact-request only."""
        request = self.request(start, end, opfs)
        with self._lock:
            cached = self._read_cache(request)
            if cached and not force_refresh:
                age = (awst(self.clock()) - awst(cached["fetched_at"])).total_seconds()
                if 0 <= age <= self.cache_seconds:
                    return {**deepcopy(cached), "status": "cached", "error": None}
            try:
                rows = self._query(request)
                records, warnings = self.normalize(rows, request)
            except ValueError:
                raise  # Invalid/oversized requests must not be disguised as outages.
            except Exception as exc:
                if cached:
                    return {**deepcopy(cached), "status": "offline_cached", "error": str(exc)}
                raise ProductAssayUnavailable("Snowflake is unavailable and no cached data matches this window and OPF selection.") from exc
            payload = dict(request=request, fetched_at=awst(self.clock()).isoformat(), records=records, warnings=warnings,
                           data_last_updated=max((r["last_updated"] for r in records if r["last_updated"]), default=None))
            try:
                self._write_cache(payload)
            except OSError:
                payload["warnings"].append("Data loaded, but the local offline cache could not be saved.")
            return {**payload, "status": "fresh", "error": None}

    @staticmethod
    def select_brand(records, brand):
        """Resolve an exact or unique suffix alias separately for each OPF."""
        brand = clean_text(brand).upper()
        result, warnings = [], []
        for opf in sorted({r["opf"] for r in records}):
            available = {r["brand"] for r in records if r["opf"] == opf}
            matches = {brand} if brand in available else {b for b in available if b.endswith(brand)} if brand else set()
            if len(matches) > 1:
                warnings.append(f"{opf}: {brand} matches several warehouse products; select an exact product brand.")
                continue
            result.extend({**r, "warehouse_brand": r["brand"], "brand": brand} for r in records if r["opf"] == opf and r["brand"] in matches)
        return result, warnings

    @staticmethod
    def aggregate(records, grain="shift", *, combined=False, expected_opfs=()):
        if grain not in GRAINS:
            raise ValueError("Unsupported product-assay aggregation.")
        groups = defaultdict(list)
        for i, row in enumerate(records):
            timestamp = awst(row["observed_at"])
            if grain == "shift":
                # Source shift assignment is authoritative, including the night shift across midnight.
                timestamp = awst(row["shift_start"]) if row.get("shift_start") else (
                    (timestamp - timedelta(hours=6)).replace(hour=0, minute=0, second=0, microsecond=0)
                    + timedelta(hours=6 if 6 <= timestamp.hour < 18 else 18))
            elif grain == "day":
                timestamp = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            key = (row["opf"], row["brand"], timestamp.isoformat(), i if grain == "observations" else None)
            groups[key].append(row)
            if combined and len(set(expected_opfs)) > 1:
                groups[("Combined", row["brand"], timestamp.isoformat(), None)].append(row)
        output = []
        for (opf, brand, timestamp, _), rows in sorted(groups.items()):
            dmt = math.fsum(max(r["dmt"] or 0, 0) for r in rows)
            grades, coverage = {}, {}
            for analyte in ANALYTE_COLUMNS:
                valid = [r for r in rows if r["grades"].get(analyte) is not None and (r["dmt"] or 0) > 0]
                weight = math.fsum(r["dmt"] for r in valid)
                grades[analyte] = (rows[0]["grades"].get(analyte) if grain == "observations" and opf != "Combined" else
                                   math.fsum(r["grades"][analyte] * (r["dmt"] / weight) for r in valid) if weight else None)
                coverage[analyte] = weight / dmt if dmt else 0
            output.append(dict(opf=opf, brand=brand, timestamp=timestamp, dmt=dmt, grades=grades, coverage=coverage,
                               observation_count=len(rows), contributing_opfs=sorted({r["opf"] for r in rows}),
                               expected_opfs=sorted(set(expected_opfs)),
                               sampled_at=max((r["sampled_at"] for r in rows if r.get("sampled_at")), default=None)))
        return output
