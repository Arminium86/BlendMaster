import math
import pandas as pd
import re
from bisect import bisect_right
from datetime import timedelta
import snowflake.connector
from datetime import datetime
from pandas import DataFrame
from classes.PeriodManager import PeriodManager
from classes.GradeStreams import (
    aps_grade_streams,
    normalise_aps_grade_field_mappings,
    reweight_grade_streams_from_properties,
    weighted_merge_grade_streams,
)
from classes.CustomConstraints import (
    canonical_property_key,
    merge_source_properties,
    source_property_kind,
)
from classes.SourcePropertyMappings import (
    normalise_aps_source_property_mappings,
)
from classes.GradeBlockIdentity import parent_grade_block_name

class ExpitDataHandler:
    DESTINATION_GUIDANCE_VERSION = 3
    TRANSACTION_COLUMNS = {
        "Agent.Name",
        "Source.Type",
        "Source.FullName",
        "Source.Pit",
        "Time.StartTime",
        "Time.EndTime",
        "Mining.wetTonnes",
        "Mining.grades_fe",
        "Mining.grades_si",
        "Mining.grades_al",
        "Mining.grades_mn",
        "Mining.grades_p",
        "Destination.Type",
        "Destination.Name",
        "Destination.FullName",
        "HaulageResult.Times.Dumping",
        "HaulageResult.Times.LoadedTravel",
        "HaulageResult.LoaderProductionRate.Wtph",
        "HaulageResult.Times.SpotAtDump",
        "HaulageResult.Times.SpotAtLoader",
        "HaulageResult.TruckPayload",
        "HaulageResult.NumberOfTrips",
    }

    def property_kind(self, value):
        return source_property_kind(value, self.source_property_kinds)

    def __init__(
        self,
        input_data,
        include_crusher_destinations=False,
        selected_crusher_name=None,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
        direct_tip_movement_rules=None,
        destination_guidance=None,
        selected_agent_names=None,
        grade_field_mappings=None,
        source_property_field_mappings=None,
        configured_product_brands=None,
        source_property_kinds=None,
        source_property_weights=None,
    ):
        self.include_crusher_destinations = bool(include_crusher_destinations)
        self.selected_crusher_names = self._normalize_selected_crusher_names(selected_crusher_name)
        self.selected_agent_names = self._normalize_selected_agent_names(
            selected_agent_names
        )
        self.operational_mine = str(operational_mine or "").strip().upper()
        self.operational_crusher = self._normalize_operational_crusher(operational_crusher)
        self.operational_opf = str(operational_opf or "").strip().upper()
        self.direct_tip_movement_rules = self._normalize_movement_rules(
            direct_tip_movement_rules
        )
        self.use_destination_guidance = destination_guidance is not None
        self.destination_guidance = destination_guidance or {}
        source_destination_lookup = (
            self.destination_guidance.get("source_destinations", {}) or {}
        )
        self._source_destination_lookup = {
            str(key).strip().upper(): value
            for key, value in source_destination_lookup.items()
        }
        self._pit_destination_lookup = {
            str(key).strip().upper(): value
            for key, value in (
                self.destination_guidance.get("pit_destinations", {}) or {}
            ).items()
        }
        self.source_stockpile_fallbacks = {}
        self.configured_product_brands = configured_product_brands or []
        self.source_property_kinds = dict(source_property_kinds or {})
        self.source_property_weights = dict(source_property_weights or {})
        self.grade_field_mappings = normalise_aps_grade_field_mappings(
            grade_field_mappings, self.configured_product_brands
        )
        self.source_property_field_mappings = (
            normalise_aps_source_property_mappings(
                source_property_field_mappings
            )
        )
        mapped_grade_columns = set()
        rom_mappings = self.grade_field_mappings.get("rom", {})
        if isinstance(rom_mappings, dict):
            for brand_mappings in rom_mappings.values():
                if isinstance(brand_mappings, dict):
                    mapped_grade_columns.update(
                        value for value in brand_mappings.values() if value
                    )
        product_mappings = self.grade_field_mappings.get("product", {})
        if isinstance(product_mappings, dict):
            for brand_mappings in product_mappings.values():
                if isinstance(brand_mappings, dict):
                    mapped_grade_columns.update(value for value in brand_mappings.values() if value)
        self.mapped_grade_columns = mapped_grade_columns
        configured_property_mappings = {
            field: header
            for field, header in self.source_property_field_mappings.items()
            if header
        }
        mapped_property_columns = set(configured_property_mappings.values())

        # Map Fields is the strict APS data boundary.  Read the columns needed
        # to construct schedule transactions plus only the grade/property
        # headers the user explicitly mapped.  APS exports can contain hundreds
        # of unrelated numeric planning columns; loading and auto-promoting
        # those columns used substantial memory and made them appear to flow
        # into Database View despite never being mapped.
        required_columns = (
            set(self.TRANSACTION_COLUMNS)
            | self.mapped_grade_columns
            | mapped_property_columns
        )
        self.data = pd.read_csv(
            input_data,
            usecols=lambda column: column in required_columns,
        )
        missing_mapped_columns = sorted(
            self.mapped_grade_columns - set(self.data.columns)
        )
        if missing_mapped_columns:
            raise ValueError(
                "APS 24HR grade field mapping column(s) were not found: "
                + ", ".join(missing_mapped_columns)
            )
        missing_property_columns = sorted(
            set(configured_property_mappings.values()) - set(self.data.columns)
        )
        if missing_property_columns:
            raise ValueError(
                "APS 24HR source-property mapping column(s) were not found: "
                + ", ".join(missing_property_columns)
            )
        self.mapped_property_column_keys = {}
        for field, column in configured_property_mappings.items():
            existing_field = self.mapped_property_column_keys.get(column)
            if (
                existing_field
                and self.property_kind(existing_field)
                != self.property_kind(field)
            ):
                raise ValueError(
                    "APS 24HR source-property header "
                    f"'{column}' is mapped to fields with incompatible "
                    f"mass-balance behaviour: '{existing_field}' and "
                    f"'{field}'."
                )
            self.mapped_property_column_keys.setdefault(column, field)

        # Kept for compatibility with the grouping/payload code, but automatic
        # numeric-property discovery is intentionally disabled.  Explicit Map
        # Fields mappings are supplied through mapped_property_column_keys.
        self.property_columns = []
        self.property_column_keys = {}
        self.property_warnings = []

        if not self.data.empty:
            self._preprocess_data()
            self._group_data()

    def _property_columns_with_keys(self):
        """Return every APS property header with its mass-balance key.

        Explicitly mapped headers are excluded from ``property_columns`` so
        they are not also exposed under a site-specific normalised alias. They
        still have to participate in destination splitting, row grouping and
        top-up depletion, however, otherwise the mapped value is discarded
        before payload records are created.
        """
        combined = {
            column: self.property_column_keys.get(column, "")
            for column in getattr(self, "property_columns", [])
        }
        combined.update(
            getattr(self, "mapped_property_column_keys", {}) or {}
        )
        return list(combined.items())

    @staticmethod
    def _normalize_selected_crusher_names(selected_crusher_name):
        if selected_crusher_name is None:
            return set()
        if isinstance(selected_crusher_name, (list, tuple, set)):
            return {
                str(name).strip()
                for name in selected_crusher_name
                if str(name).strip()
            }
        selected_crusher_name = str(selected_crusher_name).strip()
        return {selected_crusher_name} if selected_crusher_name else set()

    @staticmethod
    def _normalize_selected_agent_names(selected_agent_names):
        if selected_agent_names is None:
            return set()
        if isinstance(selected_agent_names, (list, tuple, set)):
            return {
                str(name).strip()
                for name in selected_agent_names
                if str(name).strip()
            }
        selected_agent_names = str(selected_agent_names).strip()
        return {selected_agent_names} if selected_agent_names else set()

    @staticmethod
    def _normalize_movement_rules(rules):
        normalized = []
        for rule in rules or []:
            if isinstance(rule, dict):
                source = rule.get("grade_block_source") or rule.get("source_pattern")
                destination = rule.get("crusher_destination") or rule.get("destination")
            elif isinstance(rule, (list, tuple)) and len(rule) >= 2:
                source, destination = rule[0], rule[1]
            else:
                continue
            source = str(source or "").strip()
            destination = str(destination or "").strip()
            if source and destination:
                normalized.append({
                    "grade_block_source": source,
                    "crusher_destination": destination,
                })
        return normalized

    @staticmethod
    def _normalize_operational_crusher(crusher):
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
    def crusher_destination_matches(cls, destination_name, mine, crusher, opf=None):
        """Match an APS crusher destination to a BlendMaster operational crusher."""
        destination = str(destination_name or "").strip().upper().replace("-", "_")
        mine = str(mine or "").strip().upper()
        crusher = cls._normalize_operational_crusher(crusher)
        opf = " ".join(str(opf or "").strip().upper().split())
        compact = "".join(character for character in destination if character.isalnum())

        if not mine or not crusher:
            return True
        if crusher == "TOTAL_FEED_PC":
            # Synthetic operating crusher representing the total feed to one OPF.
            # Keep the OPF boundary where it is available; CB OPF covers OPF1-4.
            if mine == "CB":
                return any(f"OPF{number}" in compact or f"OPF0{number}" in compact for number in range(1, 5))
            if mine == "CC" and opf == "CC OPF01":
                # CC OPF01 receives both OPF1 Crusher and Hal Crusher in APS.
                return "OPF1" in compact or "OPF01" in compact or "HAL" in compact
            if mine == "KV" and opf == "KV OPF":
                # Total KV OPF combines the VK and VQ feed points.
                return any(alias in compact for alias in ("VKOPF", "KVOPF", "VKPC", "VQOPF", "VQPC"))
            opf_match = re.search(r"OPF0?([1-4])$", opf)
            if opf_match:
                number = opf_match.group(1)
                return f"OPF{number}" in compact or f"OPF0{number}" in compact
            return False
        if mine == "CC" and crusher == "OPF02_PC":
            # Christmas Creek OPF02 is represented by RCH in APS Mining.csv.
            return "RCH" in compact or "OPF2" in compact or "OPF02" in compact
        if mine in {"CC", "CB"}:
            opf_match = re.search(r"OPF0?([1-4])(?:_?PC)?$", crusher)
            if opf_match:
                number = opf_match.group(1)
                return f"OPF{number}" in compact or f"OPF0{number}" in compact
        aliases = {
            ("EW", "EW_OPF"): ("EWOPF", "EWPC", "EW01"),
            ("EW", "EW_PC"): ("EWOPF", "EWPC", "EW01"),
            ("KV", "VK_OPF"): ("VKOPF", "KVOPF", "VKPC"),
            ("KV", "VK_PC"): ("VKOPF", "KVOPF", "VKPC"),
            ("KV", "VQ_PC"): ("VQOPF", "VQPC"),
            ("CC", "HAL_PC"): ("HALOPF", "HALPC", "HAL"),
            ("FT", "FT_OPF"): ("FTOPF", "FTPC"),
            ("FT", "FT_PC"): ("FTOPF", "FTPC"),
            ("IB", "CRUSHER"): ("CRUSHER",),
        }
        return any(alias in compact for alias in aliases.get((mine, crusher), (crusher.replace("_", ""),)))

    @staticmethod
    def crusher_destination_names_match(left, right):
        """Compare Haul Infinity nodes with APS destination paths.

        APS may store a node as ``RCH``, ``Crushers/RCH``, or inside a
        deeper destination path such as ``Crushers/RCH/Product``.  Port
        suffixes (``:In``/``:Out``), separators, case and punctuation are
        not part of the logical node identity.
        """
        generic_path_parts = {
            "CRUSHER", "CRUSHERS", "DESTINATION", "DESTINATIONS",
            "IN", "OUT", "INPUT", "OUTPUT",
        }

        def normalized_parts(value):
            text = str(value or "").strip().upper().replace("\\", "/")
            parts = set()
            for raw_part in text.split("/"):
                part = re.sub(r":(?:IN|OUT|INPUT|OUTPUT)$", "", raw_part.strip())
                compact = "".join(
                    character for character in part if character.isalnum()
                )
                if compact and compact not in generic_path_parts:
                    parts.add(compact)
            return parts

        left_parts = normalized_parts(left)
        right_parts = normalized_parts(right)
        return bool(left_parts and right_parts and left_parts.intersection(right_parts))

    @staticmethod
    def _planning_window(start_time, planning_period_count=3):
        if hasattr(start_time, "toPyDateTime"):
            start_time = start_time.toPyDateTime()
        if not isinstance(start_time, datetime):
            start_time = pd.to_datetime(start_time).to_pydatetime()
        periods = PeriodManager(planning_period_count)
        periods.calculate_periods(start_time)
        return start_time, periods.horizon_end()

    @classmethod
    def _read_planning_window_rows(cls, input_data, columns, start_time=None, planning_period_count=3):
        requested_columns = set(columns) | {"Time.StartTime", "Time.EndTime"}
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in requested_columns,
        )
        if data.empty or start_time is None:
            return data
        if not {"Time.StartTime", "Time.EndTime"}.issubset(data.columns):
            raise ValueError(
                "APS Mining.csv must contain Time.StartTime and Time.EndTime to filter the BlendMaster planning period."
            )
        data = data.copy()
        data["Time.StartTime"] = cls._parse_datetime_column(
            data["Time.StartTime"], "Time.StartTime"
        )
        data["Time.EndTime"] = cls._parse_datetime_column(
            data["Time.EndTime"], "Time.EndTime"
        )
        window_start, window_end = cls._planning_window(start_time, planning_period_count)
        return data[
            data["Time.StartTime"].notna()
            & data["Time.EndTime"].notna()
            & (data["Time.StartTime"] < pd.Timestamp(window_end))
            & (data["Time.EndTime"] > pd.Timestamp(window_start))
        ].copy()

    @staticmethod
    def _destination_name_column(data):
        if "Destination.Name" in data.columns:
            return "Destination.Name"
        if "Destination.FullName" in data.columns:
            return "Destination.FullName"
        return None

    @classmethod
    def get_distinct_crusher_destinations(cls, input_data, start_time=None):
        data = cls._read_planning_window_rows(
            input_data,
            {"Destination.Type", "Destination.Name", "Destination.FullName"},
            None,
        )
        destination_column = cls._destination_name_column(data)
        if data.empty or "Destination.Type" not in data or destination_column is None:
            return []

        destination_type = data["Destination.Type"].astype("string").str.strip().str.lower()
        crusher_names = (
            data.loc[destination_type == "crusher", destination_column]
            .astype("string")
            .str.strip()
            .dropna()
        )
        return sorted(name for name in crusher_names.unique() if name)

    @classmethod
    def get_distinct_2wp_guidance_crushers(cls, input_data):
        """Return product-crusher paths eligible for 2WP guidance."""
        columns = {
            "Destination.Type", "Destination.Name", "Destination.FullName",
            "Agent.Name", "Source.Type",
        }
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in columns,
        )
        required = {"Destination.Type", "Agent.Name", "Source.Type"}
        if data.empty or not required.issubset(data.columns):
            return []

        filtered = data[
            data["Destination.Type"].astype("string").str.strip().str.lower().eq("crusher")
            & data["Agent.Name"].astype("string").str.strip().str.lower().eq("plantagent")
            & data["Source.Type"].astype("string").str.strip().str.lower().eq("flow")
        ].copy()
        if filtered.empty:
            return []

        if "Destination.FullName" in filtered.columns:
            names = filtered["Destination.FullName"].astype("string").fillna("").str.strip()
            if "Destination.Name" in filtered.columns:
                fallback_names = (
                    filtered["Destination.Name"]
                    .astype("string").fillna("").str.strip()
                )
                names = names.mask(names.eq(""), fallback_names)
        elif "Destination.Name" in filtered.columns:
            names = filtered["Destination.Name"].astype("string").fillna("").str.strip()
        else:
            return []
        return sorted({name for name in names.tolist() if name})

    @classmethod
    def get_distinct_stockpile_destinations(cls, input_data):
        """Return APS stockpile destinations without limiting them to a planning window."""
        data = cls._read_planning_window_rows(
            input_data,
            {"Destination.Type", "Destination.Name", "Destination.FullName"},
            None,
        )
        destination_column = cls._destination_name_column(data)
        if data.empty or "Destination.Type" not in data or destination_column is None:
            return []

        destination_type = data["Destination.Type"].astype("string").str.strip().str.lower()
        stockpile_names = (
            data.loc[destination_type == "stockpile", destination_column]
            .astype("string")
            .str.strip()
            .str.replace(r"^Stockpiles/", "", regex=True)
            .dropna()
        )
        return sorted(name for name in stockpile_names.unique() if name)

    @classmethod
    def get_distinct_expit_agent_names(cls, input_data):
        """Return distinct 24HR agents that can produce expit transactions."""
        agent_names = set()
        chunks = pd.read_csv(
            input_data,
            usecols=lambda column: column in {"Agent.Name", "Source.Type"},
            chunksize=250_000,
        )
        for data in chunks:
            if "Agent.Name" not in data or "Source.Type" not in data:
                return []
            source_type = (
                data["Source.Type"].astype("string").str.strip().str.lower()
            )
            names = (
                data.loc[source_type == "reserve", "Agent.Name"]
                .astype("string")
                .str.strip()
                .dropna()
            )
            agent_names.update(name for name in names.unique() if name)
        return sorted(agent_names)

    @classmethod
    def get_direct_tip_movement_options(cls, input_data, start_time):
        data = cls._read_planning_window_rows(
            input_data,
            {
                "Source.Type", "Source.NamePart2", "Destination.Type",
                "Destination.Name", "Destination.FullName",
            },
            None,
        )
        if data.empty:
            return {"grade_block_sources": [], "crusher_destinations": []}

        source_type = data.get("Source.Type", pd.Series("", index=data.index)).astype("string").str.strip().str.lower()
        if "Source.NamePart2" in data.columns:
            sources = (
                data.loc[source_type == "reserve", "Source.NamePart2"]
                .astype("string").str.strip().dropna()
            )
            sources = sorted(value for value in sources.unique() if value)
        else:
            sources = []

        destination_column = cls._destination_name_column(data)
        if destination_column and "Destination.Type" in data.columns:
            destination_type = data["Destination.Type"].astype("string").str.strip().str.lower()
            destinations = (
                data.loc[destination_type == "crusher", destination_column]
                .astype("string").str.strip().dropna()
            )
            destinations = sorted(value for value in destinations.unique() if value)
        else:
            destinations = []
        return {
            "grade_block_sources": sources,
            "crusher_destinations": destinations,
        }

    @classmethod
    def calculate_crusher_feed_ratios(
        cls,
        input_data,
        start_time,
        selected_crusher_destinations,
        planning_period_count=3,
    ):
        data = cls._read_planning_window_rows(
            input_data,
            {
                "Destination.Type", "Destination.Name", "Destination.FullName",
                "Mining.wetTonnes",
            },
            start_time,
            planning_period_count,
        )
        destination_column = cls._destination_name_column(data)
        selected = {
            str(value).strip() for value in selected_crusher_destinations or []
            if str(value).strip()
        }
        if (
            data.empty
            or destination_column is None
            or "Destination.Type" not in data.columns
            or "Mining.wetTonnes" not in data.columns
            or not selected
        ):
            return {}

        destination_type = data["Destination.Type"].astype("string").str.strip().str.lower()
        names = data[destination_column].astype("string").str.strip()
        tonnes = pd.to_numeric(data["Mining.wetTonnes"], errors="coerce").fillna(0.0)
        filtered = pd.DataFrame({"destination": names, "tonnes": tonnes})[
            (destination_type == "crusher") & names.isin(selected) & (tonnes > 0)
        ]
        totals = filtered.groupby("destination")["tonnes"].sum()
        total_feed = float(totals.sum())
        if total_feed <= 0:
            return {}
        return {
            str(destination): float(value) / total_feed
            for destination, value in totals.items()
        }

    @staticmethod
    def normalize_brand_name(raw_brand, brand_labels):
        raw_text = str(raw_brand or "").strip().upper()
        brands = [
            str(brand or "").strip().upper()
            for brand in (brand_labels or [])
            if str(brand or "").strip()
        ]
        if not raw_text:
            return ""

        # Prefer the longest configured brand first so a specific label wins
        # when labels overlap.
        for brand in sorted(set(brands), key=len, reverse=True):
            if brand and brand in raw_text:
                return brand
        return raw_text

    @staticmethod
    def _source_pit(source_pit, source_full_name):
        pit = str(source_pit or "").strip().upper()
        if pit and pit not in {"<NA>", "NAN", "NONE"}:
            return pit
        parts = [
            part.strip().upper()
            for part in str(source_full_name or "").replace("\\", "/").split("/")
            if part.strip()
        ]
        if len(parts) >= 3 and parts[0] == "RESERVES":
            return parts[2]
        return ""

    @staticmethod
    def destination_guidance_source_key(source_full_name):
        """Ignore the APS instance suffix on the final grade-block part."""
        return parent_grade_block_name(source_full_name).upper()

    @staticmethod
    def destination_guidance_stockpile_key(value):
        """Return the canonical stockpile name used by both APS movement layers."""
        value = str(value or "").strip().replace("\\", "/")
        value = re.sub(r"/+", "/", value).strip(" /")
        value = re.sub(r"^stockpiles/", "", value, flags=re.IGNORECASE)
        return value.rsplit("/", 1)[-1].strip().upper()

    @classmethod
    def _destination_turnover_details(
        cls,
        destination,
        arrival_datetime,
        reclaim_windows,
        horizon_start,
        horizon_end,
    ):
        """Resolve first reclaim after arrival and its linear horizon priority."""
        destination_key = cls.destination_guidance_stockpile_key(destination)
        def timestamp(value):
            if isinstance(value, pd.Timestamp):
                return value
            try:
                return pd.Timestamp(value)
            except (TypeError, ValueError):
                return pd.NaT

        arrival = timestamp(arrival_datetime)
        start = timestamp(horizon_start)
        end = timestamp(horizon_end)
        if not destination_key or pd.isna(arrival) or pd.isna(start) or pd.isna(end):
            return {
                "two_wp_turnover_guidance_applicable": False,
                "two_wp_first_reclaim_datetime": "",
                "two_wp_destination_turnover_priority": None,
            }

        first_reclaim = None
        lookup = reclaim_windows.get(destination_key, {})
        if isinstance(lookup, dict) and "end_nanoseconds" in lookup:
            end_nanoseconds = lookup.get("end_nanoseconds") or []
            starts = lookup.get("start_timestamps") or []
            index = bisect_right(end_nanoseconds, int(arrival.value))
            if index < len(starts):
                first_reclaim = starts[index]
        else:
            # Compatibility for callers/tests supplying the persisted list
            # representation rather than the optimized in-memory lookup.
            for window in lookup if isinstance(lookup, list) else []:
                reclaim_start = timestamp(window.get("start_datetime"))
                reclaim_end = timestamp(window.get("end_datetime"))
                if pd.isna(reclaim_start) or pd.isna(reclaim_end):
                    continue
                if reclaim_end > arrival:
                    first_reclaim = reclaim_start
                    break

        horizon_seconds = max((end - start).total_seconds(), 0.0)
        if first_reclaim is None:
            priority = 1.0
            first_reclaim_text = ""
        else:
            priority = (
                (first_reclaim - start).total_seconds() / horizon_seconds
                if horizon_seconds > 0 else 0.0
            )
            priority = min(max(float(priority), 0.0), 1.0)
            first_reclaim_text = first_reclaim.isoformat()
        return {
            "two_wp_turnover_guidance_applicable": True,
            "two_wp_first_reclaim_datetime": first_reclaim_text,
            "two_wp_destination_turnover_priority": priority,
        }

    @classmethod
    def build_2wp_destination_guidance(cls, input_data):
        """Build dated grade-block destination candidates and fallbacks."""
        required_columns = {
            "Source.Type",
            "Source.FullName",
            "Source.Pit",
            "Destination.Type",
            "Destination.Name",
            "Destination.FullName",
            "Time.StartTime",
            "Time.EndTime",
            "Mining.wetTonnes",
        }
        read_columns = required_columns | {
            "Agent.Name",
            "OriginalSource.Name",
        }
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in read_columns,
        )
        if data.empty:
            return {
                "matching_version": cls.DESTINATION_GUIDANCE_VERSION,
                "source_destinations": {},
                "pit_destinations": {},
                "last_destination": {},
            }

        if "Destination.Name" not in data.columns:
            data["Destination.Name"] = data.get("Destination.FullName", "")
        if "Source.Pit" not in data.columns:
            data["Source.Pit"] = ""
        missing = required_columns - {"Source.Pit"} - set(data.columns)
        if missing:
            raise ValueError(
                "2WP Mining.csv is missing destination-guidance column(s): "
                + ", ".join(sorted(missing))
            )

        all_start_times = cls._parse_datetime_column(
            data["Time.StartTime"], "Time.StartTime"
        )
        all_end_times = cls._parse_datetime_column(
            data["Time.EndTime"], "Time.EndTime"
        )
        horizon_start = all_start_times.min()
        horizon_end = all_end_times.max()

        reclaim_windows = {}
        reclaim_lookup = {}
        if {"Agent.Name", "OriginalSource.Name"}.issubset(data.columns):
            reclaim_mask = (
                data["Destination.Type"].astype("string").str.strip().str.lower().eq("crusher")
                & data["Source.Type"].astype("string").str.strip().str.lower().eq("flow")
                & data["Agent.Name"].astype("string").str.strip().str.lower().eq("plantagent")
            )
            reclaim_rows = data[reclaim_mask].copy()
            if not reclaim_rows.empty:
                reclaim_rows["_start"] = all_start_times.loc[reclaim_rows.index]
                reclaim_rows["_end"] = all_end_times.loc[reclaim_rows.index]
                reclaim_rows["_stockpile_key"] = reclaim_rows[
                    "OriginalSource.Name"
                ].map(cls.destination_guidance_stockpile_key)
                reclaim_rows = reclaim_rows[
                    reclaim_rows["_stockpile_key"].ne("")
                    & reclaim_rows["_start"].notna()
                    & reclaim_rows["_end"].notna()
                    & (reclaim_rows["_end"] > reclaim_rows["_start"])
                ]
                for stockpile_key, group in reclaim_rows.groupby(
                    "_stockpile_key", sort=False
                ):
                    # Merge overlapping reclaim windows once. Their end times
                    # are then strictly increasing, allowing each destination
                    # allocation to use a logarithmic lookup instead of
                    # repeatedly reparsing and scanning every reclaim row.
                    merged_windows = []
                    ordered = group.sort_values(["_start", "_end"])
                    for reclaim_start, reclaim_end in ordered[
                        ["_start", "_end"]
                    ].itertuples(index=False, name=None):
                        reclaim_start = pd.Timestamp(reclaim_start)
                        reclaim_end = pd.Timestamp(reclaim_end)
                        if (
                            merged_windows
                            and reclaim_start <= merged_windows[-1][1]
                        ):
                            merged_windows[-1] = (
                                merged_windows[-1][0],
                                max(merged_windows[-1][1], reclaim_end),
                            )
                        else:
                            merged_windows.append(
                                (reclaim_start, reclaim_end)
                            )
                    reclaim_windows[str(stockpile_key)] = [
                        {
                            "start_datetime": reclaim_start.isoformat(),
                            "end_datetime": reclaim_end.isoformat(),
                        }
                        for reclaim_start, reclaim_end in merged_windows
                    ]
                    reclaim_lookup[str(stockpile_key)] = {
                        "end_nanoseconds": [
                            int(reclaim_end.value)
                            for _, reclaim_end in merged_windows
                        ],
                        "start_timestamps": [
                            reclaim_start
                            for reclaim_start, _ in merged_windows
                        ],
                    }

        source_type = data["Source.Type"].astype("string").str.strip().str.lower()
        destination_type = (
            data["Destination.Type"].astype("string").str.strip().str.lower()
        )
        stockpile_rows = data[
            (source_type == "reserve") & (destination_type == "stockpile")
        ].copy()
        if stockpile_rows.empty:
            return {
                "matching_version": cls.DESTINATION_GUIDANCE_VERSION,
                "source_destinations": {},
                "pit_destinations": {},
                "last_destination": {},
            }

        stockpile_rows["_row_order"] = range(len(stockpile_rows))
        stockpile_rows["source"] = (
            stockpile_rows["Source.FullName"].astype("string").str.strip()
        )
        stockpile_rows["source_key"] = stockpile_rows["source"].map(
            cls.destination_guidance_source_key
        )
        stockpile_rows["pit"] = [
            cls._source_pit(pit, source)
            for pit, source in zip(
                stockpile_rows["Source.Pit"],
                stockpile_rows["Source.FullName"],
            )
        ]
        stockpile_rows["destination"] = (
            stockpile_rows["Destination.FullName"].astype("string").str.strip()
        )
        stockpile_rows["destination_name"] = (
            stockpile_rows["Destination.Name"].astype("string").str.strip()
        )
        stockpile_rows["tonnes"] = pd.to_numeric(
            stockpile_rows["Mining.wetTonnes"], errors="coerce"
        ).fillna(0.0)
        stockpile_rows["guidance_datetime"] = cls._parse_datetime_column(
            stockpile_rows["Time.StartTime"],
            "Time.StartTime",
        )
        stockpile_rows["guidance_end_datetime"] = cls._parse_datetime_column(
            stockpile_rows["Time.EndTime"],
            "Time.EndTime",
        )
        stockpile_rows = stockpile_rows[
            stockpile_rows["source"].notna()
            & stockpile_rows["source"].ne("")
            & stockpile_rows["source_key"].ne("")
            & stockpile_rows["destination"].notna()
            & stockpile_rows["destination"].ne("")
            & (stockpile_rows["tonnes"] > 0)
        ].copy()
        if stockpile_rows.empty:
            return {
                "matching_version": cls.DESTINATION_GUIDANCE_VERSION,
                "source_destinations": {},
                "pit_destinations": {},
                "last_destination": {},
            }

        source_destinations = {}
        for source_key, group in stockpile_rows.groupby(
            "source_key", sort=False
        ):
            allocations = []
            for _, row in group.sort_values("_row_order").iterrows():
                turnover = cls._destination_turnover_details(
                    row["destination"],
                    row["guidance_end_datetime"],
                    reclaim_lookup,
                    horizon_start,
                    horizon_end,
                )
                allocations.append({
                    "destination": str(row["destination"]),
                    "destination_name": str(row["destination_name"]),
                    "ratio": 1.0,
                    "two_wp_tonnes": float(row["tonnes"]),
                    "pit": str(row["pit"]),
                    "guidance_datetime": (
                        row["guidance_datetime"].isoformat()
                        if pd.notna(row["guidance_datetime"])
                        else ""
                    ),
                    "guidance_end_datetime": (
                        row["guidance_end_datetime"].isoformat()
                        if pd.notna(row["guidance_end_datetime"])
                        else ""
                    ),
                    "source": str(row["source"]),
                    "row_order": int(row["_row_order"]),
                    **turnover,
                })
            source_destinations[str(source_key)] = allocations

        pit_destinations = {}
        pit_rows = stockpile_rows[stockpile_rows["pit"].ne("")]
        if not pit_rows.empty:
            pit_totals = (
                pit_rows
                .groupby(
                    ["pit", "destination", "destination_name"],
                    as_index=False,
                    dropna=False,
                )["tonnes"]
                .sum()
                .sort_values(
                    ["pit", "tonnes", "destination"],
                    ascending=[True, False, True],
                )
            )
            for pit, group in pit_totals.groupby("pit", sort=False):
                row = group.iloc[0]
                pit_destinations[str(pit)] = {
                    "destination": str(row["destination"]),
                    "destination_name": str(row["destination_name"]),
                    "ratio": 1.0,
                    "two_wp_tonnes": float(row["tonnes"]),
                    "pit": str(pit),
                }

        end_times = cls._parse_datetime_column(
            stockpile_rows["Time.EndTime"],
            "Time.EndTime",
        )
        start_times = cls._parse_datetime_column(
            stockpile_rows["Time.StartTime"],
            "Time.StartTime",
        )
        ordered = stockpile_rows.assign(
            _end_time=end_times,
            _start_time=start_times,
        ).sort_values(
            ["_end_time", "_start_time", "_row_order"],
            ascending=[True, True, True],
            na_position="first",
        )
        last_row = ordered.iloc[-1]
        last_destination = {
            "destination": str(last_row["destination"]),
            "destination_name": str(last_row["destination_name"]),
            "ratio": 1.0,
            "two_wp_tonnes": float(last_row["tonnes"]),
            "pit": str(last_row["pit"]),
        }
        return {
            "matching_version": cls.DESTINATION_GUIDANCE_VERSION,
            "source_destinations": source_destinations,
            "pit_destinations": pit_destinations,
            "last_destination": last_destination,
            "horizon_start_datetime": (
                horizon_start.isoformat() if pd.notna(horizon_start) else ""
            ),
            "horizon_end_datetime": (
                horizon_end.isoformat() if pd.notna(horizon_end) else ""
            ),
            "stockpile_reclaim_windows": reclaim_windows,
        }

    @classmethod
    def _read_2wp_feed_guidance_rows(
        cls,
        input_data,
        brand_labels,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
        operational_crusher_node=None,
    ):
        required_columns = {
            "Destination.Type",
            "Destination.Name",
            "Agent.Name",
            "Source.Type",
            "OriginalSource.Name",
            "Time.StartTime",
            "Time.EndTime",
            "Mining.wetTonnes",
        }
        read_columns = required_columns | {"Destination.FullName"}
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in read_columns,
        )
        if data.empty or not required_columns.issubset(set(data.columns)):
            return pd.DataFrame()
        if "Destination.FullName" not in data.columns:
            data["Destination.FullName"] = data["Destination.Name"]

        filtered = data[
            (
                data["Destination.Type"]
                .astype("string").str.strip().str.lower()
                == "crusher"
            )
            & (
                data["Agent.Name"].astype("string").str.strip().str.lower()
                == "plantagent"
            )
            & (
                data["Source.Type"].astype("string").str.strip().str.lower()
                == "flow"
            )
        ].copy()
        if filtered.empty:
            return filtered

        filtered["destination_full_name"] = (
            filtered["Destination.FullName"]
            .astype("string").fillna("").str.strip()
        )
        missing_full_name = filtered["destination_full_name"].eq("")
        filtered.loc[
            missing_full_name, "destination_full_name"
        ] = (
            filtered.loc[missing_full_name, "Destination.Name"]
            .astype("string").fillna("").str.strip()
        )
        if operational_crusher_node:
            mapped_nodes = operational_crusher_node if isinstance(
                operational_crusher_node, (list, tuple, set)
            ) else [operational_crusher_node]
            mapped_nodes = {
                str(node).strip().upper().replace("\\", "/")
                for node in mapped_nodes
                if str(node).strip()
            }
            destination_matches = filtered["destination_full_name"].apply(
                lambda destination: any(
                    cls.crusher_destination_names_match(destination, node)
                    for node in mapped_nodes
                )
            )
            filtered = filtered[destination_matches].copy()
        elif operational_mine and operational_crusher:
            destination_matches = filtered[
                "destination_full_name"
            ].apply(
                lambda destination: cls.crusher_destination_matches(
                    destination,
                    operational_mine,
                    operational_crusher,
                    operational_opf,
                )
            )
            filtered = filtered[destination_matches].copy()
        if filtered.empty:
            return filtered

        filtered["stockpile"] = (
            filtered["OriginalSource.Name"]
            .astype("string")
            .str.strip()
            .str.replace(r"^Stockpiles/", "", regex=True)
        )
        filtered["brand"] = filtered["Destination.Name"].apply(
            lambda value: cls.normalize_brand_name(value, brand_labels)
        )
        filtered["tonnes"] = pd.to_numeric(
            filtered["Mining.wetTonnes"], errors="coerce"
        ).fillna(0.0)
        filtered["start_datetime"] = cls._parse_datetime_column(
            filtered["Time.StartTime"],
            "Time.StartTime",
        )
        filtered["end_datetime"] = cls._parse_datetime_column(
            filtered["Time.EndTime"],
            "Time.EndTime",
        )
        return filtered[
            filtered["stockpile"].notna()
            & filtered["stockpile"].ne("")
            & filtered["brand"].ne("")
            & filtered["destination_full_name"].ne("")
            & (filtered["tonnes"] > 0)
            & filtered["start_datetime"].notna()
            & filtered["end_datetime"].notna()
        ].copy()

    @staticmethod
    def _merge_guidance_windows(rows):
        windows = []
        for row in rows:
            start = pd.Timestamp(row["start_datetime"])
            end = pd.Timestamp(row["end_datetime"])
            if end <= start:
                continue
            brands = set(row.get("brands") or [])
            tonnes = float(row.get("tonnes") or 0.0)
            if windows and start <= windows[-1]["end_datetime"]:
                windows[-1]["end_datetime"] = max(
                    windows[-1]["end_datetime"], end
                )
                windows[-1]["brands"].update(brands)
                windows[-1]["two_wp_tonnes"] += tonnes
            else:
                windows.append({
                    "start_datetime": start,
                    "end_datetime": end,
                    "brands": brands,
                    "two_wp_tonnes": tonnes,
                })
        for window in windows:
            window["duration_hours"] = (
                window["end_datetime"] - window["start_datetime"]
            ).total_seconds() / 3600
            window["brands"] = sorted(window["brands"])
        return windows

    @classmethod
    def _stockpile_timing_guidance_from_rows(cls, rows):
        if rows.empty:
            return {}

        guidance = {}
        for stockpile, group in rows.groupby("stockpile", sort=False):
            interval_rows = [
                {
                    "start_datetime": row["start_datetime"],
                    "end_datetime": row["end_datetime"],
                    "brands": [row["brand"]],
                    "tonnes": row["tonnes"],
                }
                for _, row in group.sort_values(
                    ["start_datetime", "end_datetime"]
                ).iterrows()
            ]
            windows = cls._merge_guidance_windows(interval_rows)
            if windows:
                guidance[str(stockpile)] = {"windows": windows}
        return guidance

    @classmethod
    def get_stockpile_timing_guidance(
        cls,
        input_data,
        brand_labels,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
    ):
        rows = cls._read_2wp_feed_guidance_rows(
            input_data,
            brand_labels,
            operational_mine,
            operational_crusher,
            operational_opf,
        )
        return cls._stockpile_timing_guidance_from_rows(rows)

    @classmethod
    def _active_blend_guidance_from_rows(cls, rows):
        if rows.empty:
            return []

        active_windows = []
        for (
            brand,
            destination_full_name,
        ), brand_rows in rows.groupby(
            ["brand", "destination_full_name"],
            sort=False,
        ):
            boundaries = sorted(set(
                pd.Timestamp(value)
                for value in pd.concat([
                    brand_rows["start_datetime"],
                    brand_rows["end_datetime"],
                ])
                if pd.notna(value)
            ))
            brand_windows = []
            for start, end in zip(boundaries, boundaries[1:]):
                if end <= start:
                    continue
                active = brand_rows[
                    (brand_rows["start_datetime"] < end)
                    & (brand_rows["end_datetime"] > start)
                ]
                stockpiles = sorted(set(active["stockpile"].astype(str)))
                if not stockpiles:
                    continue
                if (
                    brand_windows
                    and brand_windows[-1]["stockpiles"] == stockpiles
                    and brand_windows[-1]["end_datetime"] == start
                ):
                    brand_windows[-1]["end_datetime"] = end
                else:
                    brand_windows.append({
                        "product_brand": str(brand),
                        "destination_full_name": str(
                            destination_full_name
                        ),
                        "stockpiles": stockpiles,
                        "start_datetime": start,
                        "end_datetime": end,
                    })
            for window in brand_windows:
                window["duration_hours"] = (
                    window["end_datetime"] - window["start_datetime"]
                ).total_seconds() / 3600
            active_windows.extend(brand_windows)
        return sorted(
            active_windows,
            key=lambda window: (
                window["start_datetime"],
                window["product_brand"],
                window["destination_full_name"],
                tuple(window["stockpiles"]),
            ),
        )

    @classmethod
    def get_active_blend_guidance(
        cls,
        input_data,
        brand_labels,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
    ):
        rows = cls._read_2wp_feed_guidance_rows(
            input_data,
            brand_labels,
            operational_mine,
            operational_crusher,
            operational_opf,
        )
        return cls._active_blend_guidance_from_rows(rows)

    @staticmethod
    def _stockpile_brand_guidance_from_rows(filtered):
        if filtered.empty:
            return {}
        grouped = (
            filtered
            .groupby(["stockpile", "brand"], as_index=False)["tonnes"]
            .sum()
        )
        guidance = {}
        for stockpile, group in grouped.groupby("stockpile", sort=False):
            total_tonnes = float(group["tonnes"].sum())
            if total_tonnes <= 0:
                continue
            brand_tonnes = {
                str(row["brand"]): float(row["tonnes"])
                for _, row in group.sort_values("tonnes", ascending=False).iterrows()
            }
            brand_proportions = {
                brand: tonnes / total_tonnes
                for brand, tonnes in brand_tonnes.items()
            }
            primary_brand = max(brand_tonnes, key=brand_tonnes.get)
            guidance[str(stockpile)] = {
                "primary_brand": primary_brand,
                "brand_tonnes": brand_tonnes,
                "brand_proportions": brand_proportions,
                "total_tonnes": total_tonnes,
            }
        return guidance

    @classmethod
    def get_2wp_schedule_guidance(
        cls,
        input_data,
        brand_labels,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
        operational_crusher_node=None,
    ):
        """Read crusher-scoped 2WP rows once for all guidance layers."""
        rows = cls._read_2wp_feed_guidance_rows(
            input_data,
            brand_labels,
            operational_mine,
            operational_crusher,
            operational_opf,
            operational_crusher_node,
        )
        return {
            "brand_guidance": cls._stockpile_brand_guidance_from_rows(rows),
            "stockpile_timing_guidance": cls._stockpile_timing_guidance_from_rows(
                rows
            ),
            "active_blend_guidance": cls._active_blend_guidance_from_rows(rows),
        }

    @classmethod
    def get_stockpile_brand_guidance(
        cls,
        input_data,
        brand_labels,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
    ):
        """Map crusher-scoped APS plan rows to stockpile brands."""
        filtered = cls._read_2wp_feed_guidance_rows(
            input_data,
            brand_labels,
            operational_mine,
            operational_crusher,
            operational_opf,
        )
        return cls._stockpile_brand_guidance_from_rows(filtered)

    def _destination_allocations_for_row(self, row):
        source = str(row.get("Source.FullName", "") or "").strip()
        pit = self._source_pit(row.get("Source.Pit", ""), source)
        source_key = self.destination_guidance_source_key(source)
        source_lookup = getattr(self, "_source_destination_lookup", {})
        exact = source_lookup.get(source_key) or source_lookup.get(
            source.upper()
        )
        if exact:
            # Version 1 guidance stored full-horizon destination ratios and
            # no row dates. Preserve that behaviour only for old saved
            # projects whose 2WP file is no longer available to rebuild.
            if not any(
                allocation.get("guidance_datetime")
                for allocation in exact
            ):
                return exact, "exact_2wp"

            candidates = list(exact)
            if len(candidates) > 1:
                transaction_datetime = self._parse_datetime_column(
                    pd.Series([row.get("Time.StartTime")]),
                    "Time.StartTime",
                ).iloc[0]
                dated_candidates = []
                if pd.notna(transaction_datetime):
                    transaction_date = transaction_datetime.normalize()
                    for allocation in candidates:
                        guidance_datetime = pd.to_datetime(
                            allocation.get("guidance_datetime"),
                            errors="coerce",
                        )
                        if pd.notna(guidance_datetime):
                            dated_candidates.append(
                                (
                                    abs(
                                        (
                                            guidance_datetime.normalize()
                                            - transaction_date
                                        ).days
                                    ),
                                    allocation,
                                )
                            )
                if dated_candidates:
                    closest_days = min(
                        distance for distance, _ in dated_candidates
                    )
                    candidates = [
                        allocation
                        for distance, allocation in dated_candidates
                        if distance == closest_days
                    ]

            selected = max(
                candidates,
                key=lambda allocation: float(
                    allocation.get("two_wp_tonnes", 0.0) or 0.0
                ),
            )
            return [{**selected, "ratio": 1.0}], "exact_2wp"
        pit_destination = getattr(self, "_pit_destination_lookup", {}).get(
            pit.upper()
        )
        if pit_destination:
            return [pit_destination], "pit_fallback"
        last_destination = self.destination_guidance.get("last_destination") or {}
        if last_destination.get("destination"):
            return [last_destination], "last_destination_fallback"
        raise ValueError(
            f"24HR grade block '{source}' has no 2WP destination, no destination "
            f"for pit '{pit or 'unknown'}', and no last 2WP stockpile destination."
        )

    def _apply_2wp_destination_guidance(self, data):
        """Replace 24HR destinations and split tonnes using 2WP ratios."""
        if data.empty:
            return data
        source_type = data["Source.Type"].astype("string").str.strip().str.lower()
        destination_type = (
            data["Destination.Type"].astype("string").str.strip().str.lower()
        )
        movements = data[
            (source_type == "reserve")
            & destination_type.isin({"stockpile", "crusher"})
        ].copy()
        if movements.empty:
            return movements
        if "Source.Pit" not in movements.columns:
            movements["Source.Pit"] = ""

        expanded_rows = []
        for _, row in movements.iterrows():
            allocations, resolution = self._destination_allocations_for_row(row)
            ratio_total = sum(
                max(float(allocation.get("ratio", 0.0) or 0.0), 0.0)
                for allocation in allocations
            )
            if ratio_total <= 0:
                raise ValueError(
                    f"2WP destination ratios for '{row.get('Source.FullName', '')}' "
                    "do not contain a positive allocation."
                )
            for allocation in allocations:
                ratio = (
                    max(float(allocation.get("ratio", 0.0) or 0.0), 0.0)
                    / ratio_total
                )
                if ratio <= 0:
                    continue
                split_row = row.copy()
                destination = str(allocation.get("destination", "") or "").strip()
                destination_name = str(
                    allocation.get("destination_name", "") or ""
                ).strip()
                if not destination_name:
                    destination_name = destination.replace("\\", "/").rsplit("/", 1)[-1]
                split_row["Destination.Type"] = "Stockpile"
                split_row["Destination.Name"] = destination_name
                split_row["Destination.FullName"] = destination
                split_row["Mining.wetTonnes"] = (
                    pd.to_numeric(
                        pd.Series([row.get("Mining.wetTonnes")]),
                        errors="coerce",
                    ).fillna(0.0).iloc[0]
                    * ratio
                )
                for property_column, key in self._property_columns_with_keys():
                    if self.property_kind(key) != "additive":
                        continue
                    value = pd.to_numeric(
                        pd.Series([row.get(property_column)]),
                        errors="coerce",
                    ).iloc[0]
                    if pd.notna(value):
                        split_row[property_column] = float(value) * ratio
                reported_trips = pd.to_numeric(
                    pd.Series([row.get("HaulageResult.NumberOfTrips")]),
                    errors="coerce",
                ).iloc[0]
                if pd.notna(reported_trips):
                    split_row["HaulageResult.NumberOfTrips"] = reported_trips * ratio
                split_row["two_wp_destination_resolution"] = resolution
                split_row["two_wp_destination_ratio"] = ratio
                split_row["two_wp_turnover_guidance_applicable"] = bool(
                    resolution == "exact_2wp"
                    and allocation.get(
                        "two_wp_turnover_guidance_applicable", False
                    )
                )
                split_row["two_wp_first_reclaim_datetime"] = (
                    allocation.get("two_wp_first_reclaim_datetime", "")
                    if resolution == "exact_2wp" else ""
                )
                split_row["two_wp_destination_turnover_priority"] = (
                    allocation.get("two_wp_destination_turnover_priority")
                    if resolution == "exact_2wp" else None
                )
                expanded_rows.append(split_row)
        if not expanded_rows:
            return movements.iloc[0:0].copy()
        return pd.DataFrame(expanded_rows).reset_index(drop=True)

    def _preprocess_data(self):
        if "Destination.Name" not in self.data.columns:
            self.data["Destination.Name"] = self.data.get("Destination.FullName", "")
        if "Source.Pit" not in self.data.columns:
            self.data["Source.Pit"] = ""
        selected_agent_names = getattr(self, "selected_agent_names", set())
        if selected_agent_names:
            self.data = self.data[
                self.data["Agent.Name"].astype("string").str.strip().isin(
                    selected_agent_names
                )
            ].copy()
        if getattr(self, "use_destination_guidance", False):
            self.data = self._apply_2wp_destination_guidance(self.data)
        if "two_wp_destination_resolution" not in self.data.columns:
            self.data["two_wp_destination_resolution"] = "schedule_destination"
        if "two_wp_destination_ratio" not in self.data.columns:
            self.data["two_wp_destination_ratio"] = 1.0
        if "two_wp_turnover_guidance_applicable" not in self.data.columns:
            self.data["two_wp_turnover_guidance_applicable"] = False
        if "two_wp_first_reclaim_datetime" not in self.data.columns:
            self.data["two_wp_first_reclaim_datetime"] = ""
        if "two_wp_destination_turnover_priority" not in self.data.columns:
            self.data["two_wp_destination_turnover_priority"] = pd.NA
        self.data["Time.StartTime"] = self._parse_datetime_column(
            self.data["Time.StartTime"],
            "Time.StartTime",
        )
        self.data["Time.EndTime"] = self._parse_datetime_column(
            self.data["Time.EndTime"],
            "Time.EndTime",
        )
        
        # Continue with other preprocessing
        self.data = self.data.astype({
            "Agent.Name": "string",
            "Source.Type": "string",
            "Source.FullName": "string",
            "Mining.wetTonnes": "float64",
            "Mining.grades_fe": "float64",
            "Mining.grades_si": "float64",
            "Mining.grades_al": "float64",
            "Mining.grades_mn": "float64",
            "Mining.grades_p": "float64",
            "Destination.Type": "string",
            "Destination.Name": "string",
            "Destination.FullName": "string",
            "HaulageResult.Times.Dumping": "float64",
            "HaulageResult.Times.LoadedTravel": "float64",
            "HaulageResult.LoaderProductionRate.Wtph": "float64",
            "HaulageResult.Times.SpotAtDump": "float64",
            "HaulageResult.Times.SpotAtLoader": "float64",
            "HaulageResult.TruckPayload": "float64",
            "HaulageResult.NumberOfTrips": "float64"
        })
        self.source_stockpile_fallbacks = self._build_source_stockpile_fallbacks(self.data)

        destination_type = self.data["Destination.Type"].str.strip()
        destination_name = self.data["Destination.Name"].str.strip()
        destination_full_name = self.data["Destination.FullName"].str.strip()
        stockpile_destination_mask = destination_type == "Stockpile"
        crusher_destination_mask = pd.Series(False, index=self.data.index)
        if self.include_crusher_destinations and self.selected_crusher_names:
            crusher_destination_mask = (
                (destination_type == "Crusher")
                & (
                    destination_name.isin(self.selected_crusher_names)
                    | destination_full_name.isin(self.selected_crusher_names)
                )
            )
            if self.operational_mine and self.operational_crusher:
                crusher_destination_mask &= destination_name.apply(
                    lambda value: self.crusher_destination_matches(
                        value,
                        self.operational_mine,
                        self.operational_crusher,
                        self.operational_opf,
                    )
                )

        # Stockpile destinations remain the planned APS builds. Selected crusher
        # destinations are added as re-evaluable direct-tip candidates.
        transaction_mask = (
            (self.data["Source.Type"] == "Reserve")
            & (stockpile_destination_mask | crusher_destination_mask)
        )
        self.data = self.data[transaction_mask].sort_values(
            by=["Agent.Name", "Time.StartTime", "Source.FullName", "Destination.FullName"]
        )

    @staticmethod
    def _parse_datetime_column(values, column_name):
        """Parse APS timestamps while respecting the dominant DMY/MDY convention."""
        day_first_votes = 0
        month_first_votes = 0
        date_prefix = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})[/-]\d{2,4}(?:\D|$)")

        for value in values.dropna():
            match = date_prefix.match(str(value))
            if not match:
                continue
            first, second = (int(part) for part in match.groups())
            if first > 12 and second <= 12:
                day_first_votes += 1
            elif second > 12 and first <= 12:
                month_first_votes += 1

        # Australian APS exports are day-first when the column provides no
        # unambiguous evidence. ISO values are unaffected by this preference.
        day_first = day_first_votes >= month_first_votes
        try:
            parsed = pd.to_datetime(
                values,
                format="mixed",
                dayfirst=day_first,
                errors="coerce",
            )
        except (TypeError, ValueError):
            # Compatibility path for pandas versions without format="mixed".
            parsed = values.apply(
                lambda value: pd.to_datetime(value, dayfirst=day_first, errors="coerce")
            )

        text_values = values.astype("string").str.strip()
        invalid = values.notna() & text_values.ne("") & parsed.isna()
        if invalid.any():
            examples = ", ".join(
                f"row {index}: {values.loc[index]!r}"
                for index in values.index[invalid][:5]
            )
            raise ValueError(
                f"{column_name} contains {int(invalid.sum())} invalid timestamp(s). "
                f"Examples: {examples}"
            )
        return parsed

    def _build_source_stockpile_fallbacks(self, data):
        stockpile_rows = data[
            (data["Source.Type"].str.strip() == "Reserve")
            & (data["Destination.Type"].str.strip() == "Stockpile")
        ].copy()
        if stockpile_rows.empty:
            return {}

        stockpile_rows["Mining.wetTonnes"] = pd.to_numeric(
            stockpile_rows["Mining.wetTonnes"], errors="coerce"
        ).fillna(0)
        destination_totals = (
            stockpile_rows
            .groupby(["Source.FullName", "Destination.FullName"], as_index=False)["Mining.wetTonnes"]
            .sum()
            .sort_values(
                by=["Source.FullName", "Mining.wetTonnes", "Destination.FullName"],
                ascending=[True, False, True]
            )
        )
        return (
            destination_totals
            .drop_duplicates("Source.FullName")
            .set_index("Source.FullName")["Destination.FullName"]
            .to_dict()
        )

    def _payload_destination_metadata(self, row):
        destination_type = str(row.get("Destination.Type", "") or "").strip()
        planned_destination = str(row.get("Destination.FullName", "") or "").strip()
        source_name = str(row.get("Source.FullName", "") or "").strip()
        is_crusher_destination = destination_type == "Crusher"
        direct_tip_eligible = self._direct_tip_rule_matches(source_name)
        crusher_destination = str(
            row.get("Destination.Name", row.get("Destination.FullName", "")) or ""
        ).strip()
        fallback_destination = (
            self.source_stockpile_fallbacks.get(source_name, "")
            if is_crusher_destination
            else planned_destination
        )
        return {
            "destination": fallback_destination if is_crusher_destination else planned_destination,
            "destination_type": destination_type,
            "planned_destination": planned_destination,
            "fallback_destination": fallback_destination,
            # Stockpile-bound payloads are always available for direct-tip
            # consideration when their source has a rule. Crusher-bound rows
            # enter the same pool only when APS direct-tip re-evaluation is on.
            "aps_direct_tip_candidate": bool(
                direct_tip_eligible
                and (
                    not is_crusher_destination
                    or self.include_crusher_destinations
                )
            ),
            "crusher_destination": crusher_destination,
            "direct_tip_eligible": bool(
                direct_tip_eligible
                and (
                    not is_crusher_destination
                    or self.include_crusher_destinations
                )
            ),
            "two_wp_destination_resolution": str(
                row.get("two_wp_destination_resolution", "") or ""
            ),
            "two_wp_destination_ratio": float(
                row.get("two_wp_destination_ratio", 1.0) or 1.0
            ),
            "two_wp_turnover_guidance_applicable": (
                False
                if pd.isna(row.get(
                    "two_wp_turnover_guidance_applicable", False
                ))
                else bool(row.get(
                    "two_wp_turnover_guidance_applicable", False
                ))
            ),
            "two_wp_first_reclaim_datetime": str(
                row.get("two_wp_first_reclaim_datetime", "") or ""
            ),
            "two_wp_destination_turnover_priority": pd.to_numeric(
                pd.Series([
                    row.get("two_wp_destination_turnover_priority")
                ]),
                errors="coerce",
            ).iloc[0],
        }

    def _direct_tip_rule_matches(self, source_name):
        source_key = str(source_name or "").strip().upper()
        if not source_key or not self.direct_tip_movement_rules:
            return False
        selected_destinations = list(self.selected_crusher_names)
        for rule in self.direct_tip_movement_rules:
            source_pattern = str(rule["grade_block_source"]).strip().upper()
            destination = str(rule["crusher_destination"]).strip()
            if source_pattern not in source_key:
                continue
            if selected_destinations and not any(
                self.crusher_destination_names_match(destination, selected)
                for selected in selected_destinations
            ):
                continue
            return True
        return False

    def _group_data(self):
        # Create WeightedRate column without directly inserting into the fragmented DataFrame
        weighted_rate = self.data["HaulageResult.LoaderProductionRate.Wtph"] * self.data["Mining.wetTonnes"]

        # Concatenate the new column with the existing DataFrame
        self.data = pd.concat([self.data, weighted_rate.rename("WeightedRate")], axis=1)

        grade_columns = [
            column for column in [
                "Mining.grades_fe",
                "Mining.grades_si",
                "Mining.grades_al",
                "Mining.grades_mn",
                "Mining.grades_p",
                *sorted(self.mapped_grade_columns),
            ]
            if column in self.data.columns
        ]
        grade_targets_by_column = {
            f"Mining.grades_{analyte}": {f"insitu_{analyte}"}
            for analyte in ("fe", "si", "al", "mn", "p")
        }
        for stream, canonical_stream in (
            ("rom", "modelled_rom"),
            ("product", "modelled_product"),
        ):
            for brand_fields in (
                self.grade_field_mappings.get(stream, {}) or {}
            ).values():
                for analyte, column in (brand_fields or {}).items():
                    if column:
                        grade_targets_by_column.setdefault(column, set()).add(
                            f"{canonical_stream}_{analyte}"
                        )

        def configured_weight_values(target_fields, fallback):
            weight_fields = {
                canonical_property_key(
                    self.source_property_weights.get(target, "")
                )
                for target in target_fields
                if self.source_property_weights.get(target)
            }
            weight_fields.discard("")
            weight_columns = {
                self.source_property_field_mappings.get(field, "")
                for field in weight_fields
                if self.source_property_field_mappings.get(field)
            }
            if len(weight_columns) > 1:
                raise ValueError(
                    "APS fields mapped to more than one BlendMaster weighted-"
                    "average field must use a common additive weight. "
                    f"Targets: {', '.join(sorted(target_fields))}."
                )
            if weight_columns:
                column = next(iter(weight_columns))
                if column not in self.data.columns:
                    raise ValueError(
                        f"APS configured weight field header '{column}' was not found."
                    )
                return pd.to_numeric(
                    self.data[column], errors="coerce"
                ).fillna(0.0)
            return fallback

        weighted_grade_columns = {}
        weighted_grade_frames = []
        mining_wet_tonnes = pd.to_numeric(
            self.data["Mining.wetTonnes"], errors="coerce"
        ).fillna(0)
        for index, column in enumerate(grade_columns):
            numerator = f"__grade_mass_{index}"
            denominator = f"__grade_tonnes_{index}"
            numeric_grades = pd.to_numeric(self.data[column], errors="coerce")
            numeric_tonnes = configured_weight_values(
                grade_targets_by_column.get(column, set()),
                mining_wet_tonnes,
            )
            valid_tonnes = numeric_tonnes.where(numeric_grades.notna(), 0.0)
            weighted_grade_frames.extend([
                (numeric_grades.fillna(0.0) * valid_tonnes).rename(numerator),
                valid_tonnes.rename(denominator),
            ])
            weighted_grade_columns[column] = (numerator, denominator)
        if weighted_grade_frames:
            self.data = pd.concat([self.data, *weighted_grade_frames], axis=1)

        property_columns_with_keys = self._property_columns_with_keys()
        intensive_property_columns = [
            column
            for column, key in property_columns_with_keys
            if self.property_kind(key) in {"intensive", "unknown"}
        ]
        additive_property_columns = [
            column
            for column, key in property_columns_with_keys
            if self.property_kind(key) == "additive"
        ]
        for column in additive_property_columns:
            self.data[column] = pd.to_numeric(
                self.data[column], errors="coerce"
            )
        weighted_property_columns = {}
        weighted_property_frames = []
        property_keys_by_column = {
            column: canonical_property_key(key)
            for column, key in property_columns_with_keys
        }
        for index, column in enumerate(intensive_property_columns):
            numerator = f"__property_mass_{index}"
            denominator = f"__property_tonnes_{index}"
            numeric_values = pd.to_numeric(self.data[column], errors="coerce")
            numeric_tonnes = configured_weight_values(
                {property_keys_by_column.get(column, "")},
                mining_wet_tonnes,
            )
            valid_tonnes = numeric_tonnes.where(numeric_values.notna(), 0.0)
            weighted_property_frames.extend([
                (numeric_values.fillna(0.0) * valid_tonnes).rename(numerator),
                valid_tonnes.rename(denominator),
            ])
            weighted_property_columns[column] = (numerator, denominator)
        if weighted_property_frames:
            self.data = pd.concat(
                [self.data, *weighted_property_frames], axis=1
            )

        # Perform basic aggregation
        aggregation = {
            "Time.StartTime": "first",  # First row's start time
            "Time.EndTime": "last",    # Last row's end time
            "HaulageResult.Times.Dumping": "mean",
            "HaulageResult.Times.LoadedTravel": "mean",
            "HaulageResult.Times.SpotAtDump": "mean",
            "HaulageResult.Times.SpotAtLoader": "mean",
            "HaulageResult.TruckPayload": "mean",
            "HaulageResult.NumberOfTrips": "sum",  # Sum trips
            "Mining.wetTonnes": "sum",            # Sum wet tonnes
            "WeightedRate": "sum",
        }
        aggregation.update({
            weighted_column: "sum"
            for pair in weighted_grade_columns.values()
            for weighted_column in pair
        })
        aggregation.update({
            weighted_column: "sum"
            for pair in weighted_property_columns.values()
            for weighted_column in pair
        })
        aggregation.update({
            column: (lambda values: values.sum(min_count=1))
            for column in additive_property_columns
        })
        aggregated_data = self.data.groupby(
            [
                "Agent.Name", "Source.Type", "Source.FullName", "Destination.Type",
                "Destination.Name", "Destination.FullName",
                "two_wp_destination_resolution", "two_wp_destination_ratio",
                "two_wp_turnover_guidance_applicable",
                "two_wp_first_reclaim_datetime",
                "two_wp_destination_turnover_priority",
            ],
            as_index=False,
            dropna=False,
        ).agg(aggregation)

        # Calculate weighted average of LoaderProductionRate.Wtph
        aggregated_data["HaulageResult.LoaderProductionRate.Wtph"] = (
            aggregated_data["WeightedRate"] / aggregated_data["Mining.wetTonnes"]
        )

        for column, (numerator, denominator) in weighted_grade_columns.items():
            aggregated_data[column] = (
                aggregated_data[numerator]
                / aggregated_data[denominator].replace(0, pd.NA)
            )
        for column, (numerator, denominator) in weighted_property_columns.items():
            aggregated_data[column] = (
                aggregated_data[numerator]
                / aggregated_data[denominator].replace(0, pd.NA)
            )

        # Drop intermediate mass/tonnage columns.
        aggregated_data = aggregated_data.drop(columns=[
            "WeightedRate",
            *[
                weighted_column
                for pair in weighted_grade_columns.values()
                for weighted_column in pair
            ],
            *[
                weighted_column
                for pair in weighted_property_columns.values()
                for weighted_column in pair
            ],
        ])

        # Sort the aggregated data
        self.data = aggregated_data.sort_values(
            by=["Agent.Name", "Time.StartTime", "Source.FullName", "Destination.FullName"]
        )

    @staticmethod
    def _positive_finite(value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) and numeric > 0 else None

    @classmethod
    def _resolve_payload_and_load_time(cls, row, tonnes):
        """Return finite payload tonnes and loading hours for one grouped APS row."""
        tonnes = cls._positive_finite(tonnes)
        if tonnes is None:
            raise ValueError("APS transaction tonnes must be a positive finite number.")

        payload = cls._positive_finite(row.get("HaulageResult.TruckPayload"))
        if payload is None:
            reported_trips = cls._positive_finite(row.get("HaulageResult.NumberOfTrips"))
            payload = tonnes / reported_trips if reported_trips else tonnes

        loader_rate = cls._positive_finite(
            row.get("HaulageResult.LoaderProductionRate.Wtph")
        )
        load_time = payload / loader_rate if loader_rate else 0.0
        return payload, load_time

    def _payload_source_properties(self, row, payload_tonnes, group_tonnes):
        properties = {}
        try:
            group_tonnes = float(group_tonnes or 0)
            payload_tonnes = float(payload_tonnes or 0)
        except (TypeError, ValueError):
            return properties
        if group_tonnes <= 0 or payload_tonnes <= 0:
            return properties
        for field, column in self.source_property_field_mappings.items():
            if not column:
                continue
            try:
                value = float(row.get(column))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            kind = self.property_kind(field)
            if kind not in {"intensive", "unknown", "additive"}:
                continue
            properties[field] = (
                value * payload_tonnes / group_tonnes
                if kind == "additive"
                else value
            )
        for column in getattr(self, "property_columns", []):
            key = self.property_column_keys.get(column, "")
            kind = self.property_kind(key)
            if kind not in {"intensive", "unknown", "additive"}:
                continue
            try:
                value = float(row.get(column))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            properties[key] = (
                value * payload_tonnes / group_tonnes
                if kind == "additive"
                else value
            )
        return properties

    def process_transactions(self):
        if not self.data.empty:
            self.results = []
            for agent, group in self.data.groupby("Agent.Name"):
                group = group.reset_index(drop=True)
                for i in range(len(group)):
                    # Fetch row dynamically for current iteration
                    tonnes = group.at[i, "Mining.wetTonnes"]
                    row = group.iloc[i]  # For other attributes that remain static per row
                    
                    if tonnes <= 0:
                        continue  # Skip rows with no remaining tonnes
                    
                    payload, load_time = self._resolve_payload_and_load_time(row, tonnes)
                    start_time = row["Time.StartTime"]
                    destination = row["Destination.FullName"]
                    source_name = row["Source.FullName"]
                    destination_metadata = self._payload_destination_metadata(row)
                    row_grade_streams = aps_grade_streams(
                        row.to_dict(), self.grade_field_mappings, self.configured_product_brands
                    )
                    row_source_properties = self._payload_source_properties(
                        row, payload, tonnes
                    )
                    row_grade_streams = reweight_grade_streams_from_properties(
                        row_grade_streams, row_source_properties
                    )
                    num_trips = tonnes / payload
                    int_trips = int(num_trips)
                    delivery_time = None
                    mining_start_time = start_time

                    for trip in range(int_trips):
                        if trip == 0:
                            delivery_time = (
                                start_time +
                                timedelta(hours=load_time +
                                        row["HaulageResult.Times.LoadedTravel"] / 60 +
                                        row["HaulageResult.Times.SpotAtDump"] / 60 +
                                        row["HaulageResult.Times.Dumping"] / 60)
                            )
                            mining_start_time = start_time
                        else:
                            delivery_time = (
                                delivery_time +
                                timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                        load_time)
                            )
                            mining_start_time = (mining_start_time +
                            timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                        load_time)
                            )
                        self.results.append({
                            "agent": agent,
                            "source": source_name,
                            "start_datetime": mining_start_time,
                            "payload": payload,
                            "source_grade_fe": row["Mining.grades_fe"],
                            "source_grade_si": row["Mining.grades_si"],
                            "source_grade_al": row["Mining.grades_al"],
                            "source_grade_mn": row["Mining.grades_mn"],
                            "source_grade_p": row["Mining.grades_p"],
                            "grade_streams": row_grade_streams,
                            "source_properties": dict(row_source_properties),
                            "delivered_datetime": delivery_time,
                            **destination_metadata,
                        })

                    # Handle fractional tonnes (top-up case)
                    fractional_tonnes = tonnes % payload
                    weighted_grades = {
                        "Grade_fe": row["Mining.grades_fe"],
                        "Grade_si": row["Mining.grades_si"],
                        "Grade_al": row["Mining.grades_al"],
                        "Grade_mn": row["Mining.grades_mn"],
                        "Grade_p": row["Mining.grades_p"]
                    }  # Default to current row's grades in case no top-up happens
                    weighted_grade_streams = row_grade_streams
                    weighted_source_properties = self._payload_source_properties(
                        row, fractional_tonnes, tonnes
                    )

                    if fractional_tonnes > 0:
                        current_fractional_tonnes = fractional_tonnes
                        next_row = group.iloc[i + 1] if i + 1 < len(group) else None
                        if next_row is not None:
                            next_start_time = next_row["Time.StartTime"]
                            next_destination = next_row["Destination.FullName"]

                            if next_start_time == row["Time.EndTime"] and next_destination == destination:
                                top_up_tonnes = min(payload - fractional_tonnes, group.at[i + 1, "Mining.wetTonnes"])
                                fractional_tonnes += top_up_tonnes

                                # Only the partial payload from the current row
                                # is mixed with the next row's top-up tonnes.
                                total_tonnes = current_fractional_tonnes + top_up_tonnes
                                weighted_grades = {
                                    "Grade_fe": (row["Mining.grades_fe"] * current_fractional_tonnes +
                                                next_row["Mining.grades_fe"] * top_up_tonnes) / total_tonnes,
                                    "Grade_si": (row["Mining.grades_si"] * current_fractional_tonnes +
                                                next_row["Mining.grades_si"] * top_up_tonnes) / total_tonnes,
                                    "Grade_al": (row["Mining.grades_al"] * current_fractional_tonnes +
                                                next_row["Mining.grades_al"] * top_up_tonnes) / total_tonnes,
                                    "Grade_mn": (row["Mining.grades_mn"] * current_fractional_tonnes +
                                                next_row["Mining.grades_mn"] * top_up_tonnes) / total_tonnes,
                                    "Grade_p": (row["Mining.grades_p"] * current_fractional_tonnes +
                                                next_row["Mining.grades_p"] * top_up_tonnes) / total_tonnes,
                                }
                                next_grade_streams = aps_grade_streams(
                                    next_row.to_dict(), self.grade_field_mappings, self.configured_product_brands
                                )
                                next_source_properties = self._payload_source_properties(
                                    next_row,
                                    top_up_tonnes,
                                    group.at[i + 1, "Mining.wetTonnes"],
                                )
                                weighted_grade_streams = weighted_merge_grade_streams(
                                    row_grade_streams,
                                    current_fractional_tonnes,
                                    next_grade_streams,
                                    top_up_tonnes,
                                    weighted_source_properties,
                                    next_source_properties,
                                    self.source_property_weights,
                                )
                                weighted_source_properties = merge_source_properties(
                                    weighted_source_properties,
                                    current_fractional_tonnes,
                                    next_source_properties,
                                    top_up_tonnes,
                                    self.source_property_kinds,
                                    self.source_property_weights,
                                )

                                # Update the next grouped row without duplicating
                                # additive masses/volumes already consumed by the
                                # top-up payload.
                                next_opening_tonnes = float(
                                    group.at[i + 1, "Mining.wetTonnes"] or 0
                                )
                                group.at[i + 1, "Mining.wetTonnes"] -= top_up_tonnes
                                remaining_ratio = (
                                    max(group.at[i + 1, "Mining.wetTonnes"], 0)
                                    / next_opening_tonnes
                                    if next_opening_tonnes > 0 else 0
                                )
                                for (
                                    property_column,
                                    key,
                                ) in self._property_columns_with_keys():
                                    if self.property_kind(key) != "additive":
                                        continue
                                    try:
                                        group.at[i + 1, property_column] = (
                                            float(group.at[i + 1, property_column])
                                            * remaining_ratio
                                        )
                                    except (TypeError, ValueError):
                                        pass
                                if group.at[i + 1, "Mining.wetTonnes"] <= 0:
                                    group.at[i + 1, "Mining.wetTonnes"] = 0  # Mark as used

                            if delivery_time is not None:
                                delivery_time = (
                                    delivery_time +
                                    timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                        load_time)
                                )
                                mining_start_time = (mining_start_time +
                                timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                            load_time)
                                )
                            else:
                                delivery_time = (
                                    start_time +
                                    timedelta(hours=load_time +
                                            row["HaulageResult.Times.LoadedTravel"] / 60 +
                                            row["HaulageResult.Times.SpotAtDump"] / 60 +
                                            row["HaulageResult.Times.Dumping"] / 60)
                                )
                                mining_start_time = start_time

                        elif delivery_time is not None:
                            delivery_time = (
                                delivery_time +
                                timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                    load_time)
                            )
                            mining_start_time = (
                                mining_start_time +
                                timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                    load_time)
                            )
                        else:
                            delivery_time = (
                                start_time +
                                timedelta(hours=load_time +
                                        row["HaulageResult.Times.LoadedTravel"] / 60 +
                                        row["HaulageResult.Times.SpotAtDump"] / 60 +
                                        row["HaulageResult.Times.Dumping"] / 60)
                            )
                            mining_start_time = start_time

                        # Append the topped-up trip
                        weighted_grade_streams = (
                            reweight_grade_streams_from_properties(
                                weighted_grade_streams,
                                weighted_source_properties,
                            )
                        )
                        self.results.append({
                            "agent": agent,
                            "source": source_name,
                            "start_datetime": mining_start_time,
                            "payload": fractional_tonnes,
                            "source_grade_fe": weighted_grades["Grade_fe"],
                            "source_grade_si": weighted_grades["Grade_si"],
                            "source_grade_al": weighted_grades["Grade_al"],
                            "source_grade_mn": weighted_grades["Grade_mn"],
                            "source_grade_p": weighted_grades["Grade_p"],
                            "grade_streams": weighted_grade_streams,
                            "source_properties": weighted_source_properties,
                            "delivered_datetime": delivery_time,
                            **destination_metadata,
                        })

            return pd.DataFrame(self.results)
    

    def get_latest_block_and_mined_tonnes(self, agent):
        """
        Retrieve the current or last block and mined tonnes for a load agent,
        with the block transformed to the desired naming convention.

        Parameters:
            agent (str): The load equipment identifier (e.g., 'EX8107').

        Returns:
            tuple: A tuple containing the transformed block name and mined tonnes, or (None, None) if no records are found.
        """
        try:
            # Establish connection to Snowflake
            conn = self.connect_snowflake_with_service_account()

            # Define the query
            query = f"""
            WITH LatestTransaction AS (
                SELECT OPERATION, SOURCE_FMS
                FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS
                WHERE CONTAINS(LOAD_EQUIPMENT, %(agent)s) 
                AND CONTAINS(SOURCE_CAT_TO_DEST_CAT, 'Gradeblock')
                ORDER BY TRANSACTION_DATETIME DESC
                LIMIT 1
            ),
            SummedData AS (
                SELECT SOURCE_FMS, OPERATION, SUM(WMT_REPORTING) AS TOTAL_WMT_REPORTING
                FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS
                WHERE CONTAINS(SOURCE_FMS, (
                    SELECT SOURCE_FMS FROM LatestTransaction
                ))
                GROUP BY SOURCE_FMS, OPERATION
            )
            SELECT l.OPERATION AS OPERATION, 
                l.SOURCE_FMS AS BLOCK, 
                s.TOTAL_WMT_REPORTING AS MINED_TONNES
            FROM LatestTransaction l
            JOIN SummedData s
            ON l.SOURCE_FMS = s.SOURCE_FMS;
            """

            # Execute the query
            with conn.cursor() as cursor:
                cursor.execute(query, {'agent': agent})
                result = cursor.fetchone()

            if result:
                source_fms = result[1]  # SOURCE_FMS
                mined_tonnes = result[2]  # TOTAL_WMT_REPORTING

                # Extract the first two parts from self.results' "source" column
                if self.results and 'source' in self.results[0]:
                    source_parts = self.results[0]['source'].split('/')[:2]
                    prefix = '/'.join(source_parts)
                else:
                    raise ValueError("self.results does not contain a valid 'source' format.")

                # Transform the block to the desired naming convention
                parts = source_fms.split('_')
                transformed_block = (
                    f"{prefix}/"
                    f"{parts[0]}/"  # VOQ05
                    f"{parts[1]}/"  # 01
                    f"{int(parts[2]):03}/"  # Drop leading zero, ensure 3 digits
                    f"{parts[3]}/"  # 004
                    f"{int(parts[4]):03}/"  # Drop leading zero, ensure 3 digits
                    f"{parts[5][:2]}_{parts[5][2:]}")

                return transformed_block, mined_tonnes

            else:
                return None, None

        except Exception as e:
            print(f"Error retrieving block and mined tonnes: {e}")
            return None, None

        finally:
            if 'conn' in locals() and conn:
                conn.close()

    def update_transactions(self, expit_payload_transactions, now):
       
        if not self.data.empty:   
            
            updated_transactions = expit_payload_transactions
            
            # Process transactions grouped by `agent`
            grouped = updated_transactions.groupby("agent")
            
            updated_groups = []  # Store updated groups here
            
            for agent, group in grouped:
                # Get latest block info for the agent
                current_block_name, current_block_mined_tonnes = self.get_latest_block_and_mined_tonnes(agent)

                if current_block_mined_tonnes and current_block_name:

                    # Sort transactions for the agent
                    group = group.sort_values(by=["start_datetime"]).reset_index()

                    # Find the first row where `current_block_name` matches
                    filtered_rows = group[group["source"].str.contains(current_block_name, na=False)]
                        
                    if not filtered_rows.empty:
                        block_row = filtered_rows.iloc[0]
                        block_index = block_row.name  # Get index of the matching row

                        # Skip rows until payload sum meets or exceeds `current_block_mined_tonnes`
                        cumulative_payload = 0
                        skip_until_index = None
                        
                        for idx in range(block_index, len(group)):
                            cumulative_payload += group.at[idx, "payload"]
                            if cumulative_payload >= current_block_mined_tonnes:
                                skip_until_index = idx
                                break
                        
                        # Keep only the rows after `skip_until_index`
                        if skip_until_index is not None:
                            group = group.iloc[skip_until_index:]
                            
                            # Calculate time difference and update `delivered_datetime`
                            for idx, row in group.iterrows():
                                if idx == skip_until_index:
                                    # Compute time difference
                                    time_diff = now - row["start_datetime"]

                                # Update `delivered_datetime`
                                if time_diff.total_seconds() > 0:
                                    updated_delivery_time = row["delivered_datetime"] + time_diff
                                    updated_mining_start_time = row["start_datetime"] + time_diff
                                else:
                                    updated_delivery_time = row["delivered_datetime"] - abs(time_diff)
                                    updated_mining_start_time = row["start_datetime"] - abs(time_diff)

                                group.at[idx, "delivered_datetime"] = updated_delivery_time
                                group.at[idx, "start_datetime"] = updated_mining_start_time
            
                        print(fr"Expit payload transactions updated for {agent}.")
                    
                    else:
                        print(fr"Current block not found for {agent}. Original expit payload transactions will be executed for this agent.")
                        block_row = None
                        block_index = None

                    # Append the updated group
                    updated_groups.append(group)

                else: continue

            # Concatenate all updated groups into one DataFrame
            if not updated_groups:
                return updated_transactions.reset_index(drop=True)

            updated_transactions = pd.concat(updated_groups, ignore_index=True)
            
            return updated_transactions

    def connect_snowflake_with_service_account(self):
        try:
            # Connect to Snowflake using service account credentials
            conn = snowflake.connector.connect(
                user='SVC_APS',  
                password='AlastriSnowflake123',  
                account='wn74261.ap-southeast-2',  
                warehouse='WH_EDW_SELFSERVICE', 
                database='AA_OPERATIONS_MANAGEMENT',  
                schema='SELFSERVICE',  
                role='SVC_APS',  
                login_timeout=60,  
                network_timeout=300 
            )

            # Confirm the connection is open
            if conn.is_closed():
                print("Failed to connect to Snowflake.")
                return None

            print("Connection established successfully.")
            return conn

        except snowflake.connector.errors.Error as e:
            print(f"Error connecting to Snowflake: {e}")
            return None
