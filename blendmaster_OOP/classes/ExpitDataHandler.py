import math
import pandas as pd
import re
from datetime import timedelta
import snowflake.connector
from datetime import datetime
from pandas import DataFrame
from classes.PeriodManager import PeriodManager

class ExpitDataHandler:
    def __init__(
        self,
        input_data,
        include_crusher_destinations=False,
        selected_crusher_name=None,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
        direct_tip_movement_rules=None,
    ):
        self.include_crusher_destinations = bool(include_crusher_destinations)
        self.selected_crusher_names = self._normalize_selected_crusher_names(selected_crusher_name)
        self.operational_mine = str(operational_mine or "").strip().upper()
        self.operational_crusher = self._normalize_operational_crusher(operational_crusher)
        self.operational_opf = str(operational_opf or "").strip().upper()
        self.direct_tip_movement_rules = self._normalize_movement_rules(
            direct_tip_movement_rules
        )
        self.source_stockpile_fallbacks = {}
        self.data = pd.read_csv(input_data)
        if not self.data.empty:
            self._preprocess_data()
            self._group_data()

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
        """Compare APS crusher names whether supplied as short or full paths."""
        def normalized(value):
            value = str(value or "").strip().upper().replace("\\", "/")
            return value.rsplit("/", 1)[-1]

        return normalized(left) == normalized(right)

    @staticmethod
    def _planning_window(start_time):
        if hasattr(start_time, "toPyDateTime"):
            start_time = start_time.toPyDateTime()
        if not isinstance(start_time, datetime):
            start_time = pd.to_datetime(start_time).to_pydatetime()
        periods = PeriodManager()
        periods.calculate_periods(start_time)
        return start_time, periods.get_periods()["period_2_end"]

    @classmethod
    def _read_planning_window_rows(cls, input_data, columns, start_time=None):
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
        window_start, window_end = cls._planning_window(start_time)
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
    ):
        data = cls._read_planning_window_rows(
            input_data,
            {
                "Destination.Type", "Destination.Name", "Destination.FullName",
                "Mining.wetTonnes",
            },
            start_time,
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

    @classmethod
    def get_stockpile_brand_guidance(
        cls,
        input_data,
        brand_labels,
        operational_mine=None,
        operational_crusher=None,
        operational_opf=None,
    ):
        """Map APS plan rows to ROM stockpile brand proportions.

        Stockpile Inventories is a hub/mine-level view.  Therefore guidance is
        deliberately derived across all qualifying crusher destinations in the
        APS file, rather than being filtered by the active OPF or operating
        crusher.  The optional scenario arguments remain in the signature for
        compatibility with older callers.
        """
        required_columns = {
            "Destination.Type",
            "Destination.Name",
            "Agent.Name",
            "Source.Type",
            "OriginalSource.Name",
            "Mining.wetTonnes",
        }
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in required_columns,
        )
        if data.empty or not required_columns.issubset(set(data.columns)):
            return {}

        filtered = data[
            (data["Destination.Type"].astype("string").str.strip() == "Crusher")
            & (data["Agent.Name"].astype("string").str.strip() == "PlantAgent")
            & (data["Source.Type"].astype("string").str.strip() == "Flow")
        ].copy()
        if filtered.empty:
            return {}

        filtered["stockpile"] = filtered["OriginalSource.Name"].astype("string").str.strip()
        filtered["brand"] = filtered["Destination.Name"].apply(
            lambda value: ExpitDataHandler.normalize_brand_name(value, brand_labels)
        )
        filtered["tonnes"] = pd.to_numeric(filtered["Mining.wetTonnes"], errors="coerce").fillna(0)
        filtered = filtered[
            (filtered["stockpile"].notna())
            & (filtered["stockpile"] != "")
            & (filtered["brand"] != "")
            & (filtered["tonnes"] > 0)
        ]
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

    def _preprocess_data(self):
        if "Destination.Name" not in self.data.columns:
            self.data["Destination.Name"] = self.data.get("Destination.FullName", "")
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
        self.data = self.data[
            (self.data["Source.Type"] == "Reserve")
            & (stockpile_destination_mask | crusher_destination_mask)
        ].sort_values(
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

        # Perform basic aggregation
        aggregated_data = self.data.groupby(
            [
                "Agent.Name", "Source.Type", "Source.FullName", "Destination.Type",
                "Destination.Name", "Destination.FullName",
            ],
            as_index=False
        ).agg({
            "Time.StartTime": "first",  # First row's start time
            "Time.EndTime": "last",    # Last row's end time
            "HaulageResult.Times.Dumping": "mean",
            "HaulageResult.Times.LoadedTravel": "mean",
            "HaulageResult.Times.SpotAtDump": "mean",
            "HaulageResult.Times.SpotAtLoader": "mean",
            "HaulageResult.TruckPayload": "mean",
            "HaulageResult.NumberOfTrips": "sum",  # Sum trips
            "Mining.wetTonnes": "sum",            # Sum wet tonnes
            "Mining.grades_fe": "mean",           # Simple average of grades
            "Mining.grades_si": "mean",
            "Mining.grades_al": "mean",
            "Mining.grades_mn": "mean",
            "Mining.grades_p": "mean",
            "WeightedRate": "sum"  # Sum of weighted rates
        })

        # Calculate weighted average of LoaderProductionRate.Wtph
        aggregated_data["HaulageResult.LoaderProductionRate.Wtph"] = (
            aggregated_data["WeightedRate"] / aggregated_data["Mining.wetTonnes"]
        )

        # Drop the intermediate WeightedRate column
        aggregated_data = aggregated_data.drop(columns=["WeightedRate"])

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

                    if fractional_tonnes > 0:
                        next_row = group.iloc[i + 1] if i + 1 < len(group) else None
                        if next_row is not None:
                            next_start_time = next_row["Time.StartTime"]
                            next_destination = next_row["Destination.FullName"]

                            if next_start_time == row["Time.EndTime"] and next_destination == destination:
                                top_up_tonnes = min(payload - fractional_tonnes, group.at[i + 1, "Mining.wetTonnes"])
                                fractional_tonnes += top_up_tonnes

                                # Weighted average grades for the top-up
                                total_tonnes = group.at[i, "Mining.wetTonnes"] + top_up_tonnes
                                weighted_grades = {
                                    "Grade_fe": (row["Mining.grades_fe"] * group.at[i, "Mining.wetTonnes"] +
                                                next_row["Mining.grades_fe"] * top_up_tonnes) / total_tonnes,
                                    "Grade_si": (row["Mining.grades_si"] * group.at[i, "Mining.wetTonnes"] +
                                                next_row["Mining.grades_si"] * top_up_tonnes) / total_tonnes,
                                    "Grade_al": (row["Mining.grades_al"] * group.at[i, "Mining.wetTonnes"] +
                                                next_row["Mining.grades_al"] * top_up_tonnes) / total_tonnes,
                                    "Grade_mn": (row["Mining.grades_mn"] * group.at[i, "Mining.wetTonnes"] +
                                                next_row["Mining.grades_mn"] * top_up_tonnes) / total_tonnes,
                                    "Grade_p": (row["Mining.grades_p"] * group.at[i, "Mining.wetTonnes"] +
                                                next_row["Mining.grades_p"] * top_up_tonnes) / total_tonnes,
                                }

                                # Update the next row's tonnes
                                group.at[i + 1, "Mining.wetTonnes"] -= top_up_tonnes
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
