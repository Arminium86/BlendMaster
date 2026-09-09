"""Scenario-relative ROM activity with an exact-request, bounded offline cache."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from classes.DestinationBuildOrder import digest, stockpile_key
from setup.InventoryBuildLineage import canonical_block, clean_text, finite_number
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from setup.ProductAssayHistory import awst


class DestinationActivityUnavailable(ConnectionError):
    pass


class RecentDestinationActivity:
    VERSION = 1
    MAX_HOURS = 24 * 31
    MAX_RECORDS = 200000
    SQL_PATH = Path(__file__).with_name("sql") / "recent_destination_activity.sql"
    SOURCE = "AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS"
    # Scenario site codes differ from the warehouse's descriptive OPERATION values.
    SITE_OPERATIONS = {"CC": "CHRISTMAS CREEK", "CB": "CLOUDBREAK", "KV": "KINGS", "VK": "KINGS",
                       "FT": "FIRETAIL", "EW": "ELIWANA", "IB": "IRON BRIDGE"}

    @classmethod
    def warehouse_operation(cls, site):
        site = clean_text(site).upper()
        return cls.SITE_OPERATIONS.get(site, site)

    def __init__(self, inventory_loader=None, cache_directory=None, *, clock=None, cache_seconds=300):
        self.inventory_loader = inventory_loader or OpeningStockpileInventories()
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
        self.cache_directory = Path(cache_directory) if cache_directory is not None else base / "BlendMaster" / "destination_activity"
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.cache_seconds = cache_seconds
        self._lock = threading.RLock()

    def request(self, site, scenario_start, lookback_hours, areas, source_signature):
        end = awst(scenario_start)
        hours = finite_number(lookback_hours)
        if hours is None or not 0 < hours <= self.MAX_HOURS:
            raise ValueError("Activity lookback must be greater than 0 and at most 744 hours.")
        site = clean_text(site).upper()
        if not site or not source_signature:
            raise ValueError("Site and source-data signature are required.")
        areas = {stockpile_key(k): clean_text(v).upper() for k, v in areas.items() if clean_text(v)}
        return dict(version=self.VERSION, source=self.SOURCE, site=site, operation=self.warehouse_operation(site),
                    start=(end-timedelta(hours=hours)).isoformat(),
                    end=end.isoformat(), lookback_hours=hours, areas=areas, source_signature=source_signature,
                    query_signature=digest(self.SQL_PATH.read_text(encoding="utf-8")))

    def _path(self, request):
        return self.cache_directory / f"destinations-v{self.VERSION}-{digest(request)}.json"

    def _read_cache(self, request):
        try:
            payload = json.loads(self._path(request).read_text(encoding="utf-8"))
            if payload["request"] != request or not isinstance(payload["records"], list):
                return None
            awst(payload["fetched_at"])
            if payload["data_signature"] != digest(payload["records"]):
                return None
            for row in payload["records"]:
                if (not request["start"] <= row["observed_at"] < request["end"] or
                    row["destination"] not in request["areas"] or row["rom_area"] != request["areas"][row["destination"]] or
                    finite_number(row["wmt"]) is None or row["wmt"] <= 0 or not row["material_type"]):
                    return None
            return payload
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _write_cache(self, payload):
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, allow_nan=False)
            temporary.replace(self._path(payload["request"]))
            files = sorted((p for p in self.cache_directory.iterdir() if re.fullmatch(r"destinations-v1-[a-f0-9]{64}\.json", p.name)),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files[24:]:
                path.unlink(missing_ok=True)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _query(self, request):
        if not request["areas"]:
            return []
        connection = self.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise DestinationActivityUnavailable("Snowflake connection unavailable.")
        try:
            with connection.cursor() as cursor:
                cursor.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60")
                cursor.execute(self.SQL_PATH.read_text(encoding="utf-8"),
                               (request["start"] + " +08:00", request["end"] + " +08:00", request["operation"], json.dumps(sorted(request["areas"]))))
                rows = cursor.fetchall()
                if len(rows) > self.MAX_RECORDS:
                    raise ValueError("More than 200,000 inbound movements match. Reduce the activity lookback.")
                return [dict(zip((c[0].upper() for c in cursor.description), row)) for row in rows]
        finally:
            connection.close()

    @staticmethod
    def normalize(rows, request):
        records, warnings, seen = [], [], set()
        for row in rows:
            if (clean_text(row.get("OPERATION")).upper() != request["operation"] or row.get("MOVEMENT_TYPE") != "ExPit" or
                row.get("MOVEMENT_CLASSIFICATION") != "Expit Ore" or row.get("MOVEMENT_SUBCLASSIFICATION") != "Expit Ore"):
                continue
            destination = stockpile_key(clean_text(row.get("DESTINATION_FMS")))
            if destination not in request["areas"]:
                continue
            try:
                observed = awst(row.get("OBSERVED_AT")).isoformat()
            except (ValueError, TypeError):
                warnings.append("Some movements have invalid timestamps and were excluded.")
                continue
            if not request["start"] <= observed < request["end"]:
                continue
            block = canonical_block(row.get("SOURCE")) or canonical_block(row.get("SOURCE_FMS"))
            tonnes = finite_number(row.get("WMT"))
            if not block or tonnes is None or tonnes <= 0:
                warnings.append("Some movements lack a grade-block identity or positive ROM WMT and were excluded.")
                continue
            identity = clean_text(row.get("INTERNAL_ID"))
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            material = re.match(r"[A-Z]+", block.split("|")[-1]).group()
            records.append(dict(movement_id=identity, observed_at=observed, source_block=block, destination=destination,
                                destination_build=clean_text(row.get("DESTINATION")), rom_area=request["areas"][destination],
                                material_type=material, wmt=tonnes))
        return sorted(records, key=lambda r: (r["observed_at"], r["destination"], r["movement_id"])), list(dict.fromkeys(warnings))

    def fetch(self, site, scenario_start, lookback_hours, areas, source_signature, *, force_refresh=False):
        request = self.request(site, scenario_start, lookback_hours, areas, source_signature)
        with self._lock:
            cached = self._read_cache(request)
            if cached and not force_refresh:
                age = (awst(self.clock()) - awst(cached["fetched_at"])).total_seconds()
                if 0 <= age <= self.cache_seconds:
                    return {**deepcopy(cached), "status": "cached", "error": None}
            try:
                records, warnings = self.normalize(self._query(request), request)
            except ValueError:
                raise
            except Exception as exc:
                if cached:
                    return {**deepcopy(cached), "status": "offline_cached", "error": str(exc)}
                raise DestinationActivityUnavailable("Snowflake is unavailable and no cached activity matches this site, scenario time, lookback and source data.") from exc
            payload = dict(request=request, fetched_at=awst(self.clock()).isoformat(), records=records, warnings=warnings,
                           data_signature=digest(records))
            try:
                self._write_cache(payload)
            except OSError:
                payload["warnings"].append("Activity loaded, but the offline cache could not be saved.")
            return {**payload, "status": "fresh", "error": None}
