# This loads the data from external sources (currently an Excel file with multiple tabs which represents the combined user input and opening inventories)
import json
import math
from copy import deepcopy
import pandas as pd
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from classes.HaulCycleDataHandler import HaulCycleDataHandler
from classes.PeriodManager import PeriodManager
from classes.CustomConstraints import (
    constraint_key,
    custom_constraint_property_keys,
    expand_required_property_keys,
    filter_source_properties,
    normalize_custom_constraints,
    source_properties_from_mapping,
)
from classes.GradeStreams import ANALYTES, STREAMS, inventory_product_property_aliases
from classes.ProductBuildLanes import (
    normalize_byproduct_grade_fields,
    normalize_byproduct_quantity_fields,
)
from pandas import DataFrame

class DataLoader:
    def __init__(self, stockpile_data: dict, calendar_inputs: dict, expit_payload_transactions: DataFrame, hex_sequence_table: list, periods=None):
        # AMT chunk selection replaces solver balances. Keep the selected
        # inventory snapshot intact for audit and later scenario refreshes.
        self.stockpile_data = deepcopy(stockpile_data)
        self.calendar_inputs = calendar_inputs
        self.expit_payload_transactions = expit_payload_transactions
        self.hex_sequence_table = hex_sequence_table
        self.periods = periods
        self.solver_config = (calendar_inputs or {}).get("solver_config", {})
        self.direct_tip_enabled = bool(self.solver_config.get("direct_tip_enabled", True))
        self.required_source_property_keys = custom_constraint_property_keys(
            self.solver_config.get("custom_constraints")
        )
        self.required_source_property_keys.update(
            self.solver_config.get("optimisation_source_property_fields") or []
        )
        selected_stream = str(
            self.solver_config.get("selected_data_stream") or ""
        ).strip().lower()
        self.required_source_property_keys.update(
            str(self.solver_config.get(key) or default)
            for key, default in {
                "crusher_tonnes_stream": "modelled_rom_wmt",
                "reclaimer_tonnes_stream": "modelled_rom_wmt",
                "product_build_tonnes_stream": "modelled_product_wmt",
            }.items()
        )
        if self.solver_config.get("byproducts_enabled", False):
            quantities = normalize_byproduct_quantity_fields(
                self.solver_config.get("byproduct_quantity_fields")
            )
            grades = normalize_byproduct_grade_fields(
                self.solver_config.get("byproduct_grade_fields")
            )
            self.required_source_property_keys.update(quantities.values())
            self.required_source_property_keys.update(
                field for lane_fields in grades.values()
                for field in lane_fields.values()
            )
        if selected_stream in STREAMS:
            self.required_source_property_keys.update(
                f"{selected_stream}_{analyte}" for analyte in ANALYTES
            )
        self.required_source_property_keys = expand_required_property_keys(
            self.required_source_property_keys,
            self.solver_config.get("source_property_weights"),
        )

    def solver_source_properties(self, record, *, inventory=False):
        if inventory:
            record = {
                **dict(record or {}),
                **inventory_product_property_aliases(
                    record,
                    ((self.calendar_inputs or {}).get(
                        "site_context", {}
                    ) or {}).get("opf"),
                ),
            }
        # Only canonical mapping output may reach the solver. Raw inventory,
        # lineage and APS columns remain visible for audit but cannot bypass an
        # empty Map Fields cell merely because their names happen to match.
        has_canonical_contract = (
            "defined_fields" in record or "source_properties" in record
        )
        canonical = dict(record.get("defined_fields") or {})
        canonical.update(dict(record.get("source_properties") or {}))
        if not has_canonical_contract:
            # Read-only compatibility for legacy/project records that predate
            # Define/Map Fields. Current setup paths always carry the explicit
            # canonical markers, including when every mapping is blank.
            canonical = source_properties_from_mapping(record)
        return filter_source_properties(
            source_properties_from_mapping({"source_properties": canonical}),
            self.required_source_property_keys,
        )

    def period_keys(self):
        if self.periods is not None:
            return self.periods.period_keys()
        return PeriodManager.period_keys_for_count(
            (self.calendar_inputs or {}).get("planning_period_count", 3)
        )

    def period_labels(self):
        if self.periods is not None:
            return self.periods.period_labels()
        return PeriodManager.period_labels_for_count(
            (self.calendar_inputs or {}).get("planning_period_count", 3)
        )

    @staticmethod
    def coerce_grade_streams(value):
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                decoded = json.loads(value)
                return decoded if isinstance(decoded, dict) else None
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def normalized_stockpile_name(value):
        text = str(value or "").strip().replace("\\", "/")
        if text.lower().startswith("stockpiles/"):
            text = text[len("stockpiles/"):]
        return text.strip(" /").upper()

    def load_data(self):
        """Loads data from GUI and returns it in structured format."""

        # Convert GUI and APS payload data into model objects.
        self.set_first_hex_tonnes_and_grades_to_AMT_stockpile()
        stockpile_data = self.process_stockpile_data()
        calendar_inputs = self.calendar_inputs
        stockpile_data_objects = self.create_stockpile_data_objects(stockpile_data, calendar_inputs)
        grade_block_data_objects = self.create_grade_block_data_objects(self.expit_payload_transactions)

        if self.uses_stockpile_max_reclaim_rates():
            equipment_data = {
                period: 0.0 for period in self.period_labels()
            }
        else:
            equipment_data = self.calendar_inputs['reclaim_equipment_max_reclaim_rate']
        crusher_rate_data = self.calendar_inputs['crusher_rate']
        equipment_data_objects = self.create_equipment_data_objects(equipment_data, crusher_rate_data)

        # Structure crusher target data as a nested dictionary
        crusher_target_data = {}
        for period_key, period_label in zip(
            self.period_keys(), self.period_labels()
        ):
            target = {
                "direct_feed_ratio_min": self.get_direct_feed_ratio(
                    "crusher_direct_feed_ratio_min", period_label, 0
                ),
                "direct_feed_ratio_max": self.get_direct_feed_ratio(
                    "crusher_direct_feed_ratio_max", period_label, 1
                ),
                "crusher_rate": self.calendar_inputs["crusher_rate"][period_label],
                "brand": (self.calendar_inputs.get("crusher_brand", {}) or {}).get(period_label, ""),
            }
            for grade in ("fe", "si", "al", "p", "mn"):
                target[f"target_{grade}_min"] = self.calendar_inputs[
                    f"crusher_target_{grade}_min"
                ][period_label]
                target[f"target_{grade}_max"] = self.calendar_inputs[
                    f"crusher_target_{grade}_max"
                ][period_label]
            target["custom_constraints"] = []
            for definition in normalize_custom_constraints(
                self.solver_config.get("custom_constraints")
            ):
                if not definition.get("enabled", True):
                    continue
                key = constraint_key(
                    definition.get("key") or definition.get("name")
                )
                minimum = (self.calendar_inputs.get(
                    f"crusher_custom_constraint_{key}_min", {}
                ) or {}).get(period_label)
                maximum = (self.calendar_inputs.get(
                    f"crusher_custom_constraint_{key}_max", {}
                ) or {}).get(period_label)

                def optional_number(value, bound_name):
                    if value in (None, ""):
                        return None
                    try:
                        number = float(value)
                    except (TypeError, ValueError) as error:
                        raise ValueError(
                            f"{definition['name']} {bound_name} must be numeric or blank."
                        ) from error
                    if not math.isfinite(number):
                        raise ValueError(
                            f"{definition['name']} {bound_name} must be finite."
                        )
                    return number

                item = dict(definition)
                item["minimum"] = optional_number(minimum, "Min")
                item["maximum"] = optional_number(maximum, "Max")
                if (
                    item["minimum"] is not None
                    and item["maximum"] is not None
                    and item["minimum"] > item["maximum"]
                ):
                    raise ValueError(
                        f"{definition['name']} Min cannot be greater than Max."
                    )
                target["custom_constraints"].append(item)
            target["expit_material_brand_incentives"] = []
            for key, pair in (
                (self.calendar_inputs.get(
                    "expit_material_brand_pairs", {}
                ) or {}).items()
            ):
                if not isinstance(pair, dict):
                    continue
                raw_value = (self.calendar_inputs.get(key, {}) or {}).get(
                    period_label, 0
                )
                try:
                    incentive = float(raw_value or 0)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"{pair.get('material_type')} → {pair.get('brand')} "
                        "incentive must be a signed numeric $/t value."
                    ) from error
                if not math.isfinite(incentive):
                    raise ValueError(
                        f"{pair.get('material_type')} → {pair.get('brand')} "
                        "incentive must be finite."
                    )
                target["expit_material_brand_incentives"].append({
                    "material_type": str(
                        pair.get("material_type") or ""
                    ).strip().upper(),
                    "brand": str(pair.get("brand") or "").strip().upper(),
                    "incentive_per_tonne": incentive,
                })
            crusher_target_data[period_key] = target

        for target in crusher_target_data.values():
            if target["direct_feed_ratio_min"] > target["direct_feed_ratio_max"]:
                raise ValueError("Direct Tip Ratio Min cannot be greater than Direct Tip Ratio Max.")

        return stockpile_data_objects, grade_block_data_objects, equipment_data_objects, crusher_target_data

    def uses_stockpile_max_reclaim_rates(self):
        site_context = (self.calendar_inputs or {}).get("site_context", {}) or {}
        crusher = str(site_context.get("crusher") or "").strip().upper()
        return crusher.replace("-", "_") == "TOTAL_FEED_PC"

    def get_direct_feed_ratio(self, key, period, default):
        if not self.direct_tip_enabled:
            return 0

        direct_tip_key = key.replace("direct_feed", "direct_tip")
        ratio_values = self.calendar_inputs.get(direct_tip_key, self.calendar_inputs.get(key, {}))
        ratio = float(ratio_values.get(period, default))
        if not 0 <= ratio <= 1:
            raise ValueError("Direct Tip Ratio Min/Max values must be between 0 and 1.")
        return ratio
    
    def process_stockpile_data(self):
             
        # Load stockpile data

        stockpile_data = self.stockpile_data

        for record, nested_record in stockpile_data.items():
            # Initialize auto_turnover_datetime to None
            nested_record['auto_turnover_datetime'] = None
            nested_record['is_ready'] = None

        for record, nested_record in stockpile_data.items():

            # Filter transactions related to the current stockpile
            stockpile_id = record
            
            if not self.expit_payload_transactions.empty:
                
                normalized_stockpile_id = self.normalized_stockpile_name(
                    stockpile_id
                )
                stockpile_transactions = self.expit_payload_transactions[
                    self.expit_payload_transactions["destination"].map(
                        self.normalized_stockpile_name
                    ) == normalized_stockpile_id
                ].to_dict(orient='records')
            
            else:
                stockpile_transactions = None


            if not stockpile_transactions:
                continue  # Skip if no transactions for this stockpile

            # Find the latest transaction by delivered_datetime
            latest_transaction = max(
                stockpile_transactions,
                key=lambda txn: txn['delivered_datetime']
            )
            latest_datetime = latest_transaction['delivered_datetime']

            # Sum all payloads for the stockpile
            total_payload = sum(txn['payload'] for txn in stockpile_transactions)

            # Determine availability
            total_balance = nested_record['balance'] + total_payload
            nested_record['is_ready'] = total_balance >= nested_record['reclaim_threshold']

            # If ready, update auto_turnover_datetime
            if nested_record['is_ready']:
                nested_record['auto_turnover_datetime'] = latest_datetime
        
        return stockpile_data
    
    def create_equipment_data_objects(self, equipment_data_dicts: dict, crusher_rate_dicts: dict):
        labels = self.period_labels()

        def dynamic_values(prefix, values):
            return {
                f"{prefix}_{key}": values[label]
                for key, label in zip(self.period_keys(), labels)
            }

        return [
            EquipmentData(
                name="RC",
                priority_preplan=0, 
                priority_period_1=0,
                priority_period_2=0,
                rate_preplan=equipment_data_dicts['Preplan'],
                rate_period_1=equipment_data_dicts['Period_1'],
                rate_period_2=equipment_data_dicts['Period_2'],
                period_values={
                    **{f"priority_{key}": 0 for key in self.period_keys()},
                    **dynamic_values("rate", equipment_data_dicts),
                },
            ),
            EquipmentData(
                name="EX",
                priority_preplan=0,
                priority_period_1=0,
                priority_period_2=0,
                rate_preplan=crusher_rate_dicts['Preplan'],
                rate_period_1=crusher_rate_dicts['Period_1'],
                rate_period_2=crusher_rate_dicts['Period_2'],
                period_values={
                    **{f"priority_{key}": 0 for key in self.period_keys()},
                    **dynamic_values("rate", crusher_rate_dicts),
                },
            )
        ]
    
    def create_stockpile_data_objects(self, stockpile_data:dict, calendar_inputs:dict):
        """Create a list of StockpileData objects from a list of dictionaries."""
        stockpiles = []
        use_stockpile_rates = self.uses_stockpile_max_reclaim_rates()
        rehandle_penalty_enabled = bool(
            self.solver_config.get("rehandle_cycle_time_penalty_enabled", False)
        )
        haulage_cost_per_hour = self.solver_config.get(
            "haulage_cost_per_hour",
            5.0,
        )
        for record, nested_record in stockpile_data.items():
            calendar_name = nested_record["name"]
            states_by_period = calendar_inputs[
                f"stockpiles_{calendar_name}_state"
            ]
            states = [
                states_by_period[period] for period in self.period_labels()
            ]
            max_quantities = calendar_inputs[
                f"stockpiles_{calendar_name}_maximum_quantity"
            ]
            max_reclaim_rate = nested_record.get("max_reclaim_rate")
            if use_stockpile_rates:
                reclaimable = any(
                    str(state or "").strip().lower() != "build"
                    for state in states
                )
                if max_reclaim_rate is not None:
                    try:
                        max_reclaim_rate = float(max_reclaim_rate)
                    except (TypeError, ValueError):
                        max_reclaim_rate = 0.0
                if reclaimable and (
                    max_reclaim_rate is None or max_reclaim_rate <= 0
                ):
                    raise ValueError(
                        f"Max Reclaim Rate must be greater than 0 t/h for "
                        f"Total_Feed stockpile '{record}'."
                    )
            else:
                max_reclaim_rate = None

            reclaimable = any(
                str(state or "").strip().lower() != "build"
                for state in states
            )
            cycle_time_minutes = nested_record.get(
                "rehandle_cycle_time_minutes"
            )
            if rehandle_penalty_enabled and reclaimable:
                try:
                    cycle_time_minutes = float(cycle_time_minutes)
                except (TypeError, ValueError):
                    cycle_time_minutes = 0.0
                if cycle_time_minutes <= 0:
                    raise ValueError(
                        f"No selected-crusher haul cycle was found for "
                        f"stockpile '{record}'."
                    )
            derived_cost_per_tonne = (
                HaulCycleDataHandler.cost_per_tonne(
                    cycle_time_minutes,
                    haulage_cost_per_hour,
                )
                if rehandle_penalty_enabled
                else 0.0
            )

            stockpiles.append(StockpileData(
                name=record,
                balance=nested_record["balance"],
                state_preplan=states[0],
                state_period_1=states[1],
                state_period_2=states[2],
                max_quantity_preplan=calendar_inputs[f"stockpiles_{calendar_name}_maximum_quantity"]['Preplan'],
                max_quantity_period_1=calendar_inputs[f"stockpiles_{calendar_name}_maximum_quantity"]['Period_1'],
                max_quantity_period_2=calendar_inputs[f"stockpiles_{calendar_name}_maximum_quantity"]['Period_2'],
                cost_preplan=derived_cost_per_tonne,
                cost_period_1=derived_cost_per_tonne,
                cost_period_2=derived_cost_per_tonne,
                # Retained on StockpileData for project compatibility. Calendar
                # Cash is no longer an active optimisation input.
                cash_preplan=0.0,
                cash_period_1=0.0,
                cash_period_2=0.0,
                equipment="RC",
                reclaim_threshold=nested_record["reclaim_threshold"],
                grade_fe=nested_record["grade_fe"],
                grade_si=nested_record["grade_si"],
                grade_al=nested_record["grade_al"],
                grade_p=nested_record["grade_p"],
                grade_mn=nested_record["grade_mn"],
                auto_turnover_datetime=nested_record["auto_turnover_datetime"],
                is_ready=nested_record["is_ready"],
                is_AMT=nested_record["amt"],
                aps_brand=nested_record.get("aps_brand", ""),
                aps_brand_proportions=nested_record.get("aps_brand_proportions", {}),
                aps_brand_tonnes=nested_record.get("aps_brand_tonnes", {}),
                max_reclaim_rate=max_reclaim_rate,
                grade_streams=self.coerce_grade_streams(nested_record.get("grade_streams")),
                source_properties=self.solver_source_properties(
                    nested_record,
                    inventory=not bool(nested_record.get("amt", False)),
                ),
                period_values={
                    **{
                        f"state_{key}": states_by_period[label]
                        for key, label in zip(
                            self.period_keys(), self.period_labels()
                        )
                    },
                    **{
                        f"max_quantity_{key}": max_quantities[label]
                        for key, label in zip(
                            self.period_keys(), self.period_labels()
                        )
                    },
                    **{
                        f"cost_{key}": derived_cost_per_tonne
                        for key in self.period_keys()
                    },
                    **{
                        f"cash_{key}": 0.0 for key in self.period_keys()
                    },
                },

            ))
        return stockpiles

    def create_grade_block_data_objects(self, expit_payload_transactions):
        """Create a list of GradeBlockData objects from a list of dictionaries."""
        if not self.direct_tip_enabled:
            return []

        if expit_payload_transactions is None or expit_payload_transactions.empty:
            return []

        payload_transactions = expit_payload_transactions.copy()
        if "direct_tip_eligible" in payload_transactions.columns:
            payload_transactions = payload_transactions[
                payload_transactions["direct_tip_eligible"].map(
                    lambda value: str(value).strip().lower() in {"true", "1", "yes"}
                )
            ].copy()
        else:
            # New runs require an explicit source-to-crusher movement rule.
            return []
        if payload_transactions.empty:
            return []
        if "direct_tip_id" not in payload_transactions.columns:
            payload_transactions["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(payload_transactions))
            ]

        return [
            GradeBlockData(
                name=record["direct_tip_id"],
                balance=record["payload"],
                max_quantity_preplan=self.payload_quantity_for_period(record, "preplan"),
                max_quantity_period_1=self.payload_quantity_for_period(record, "period_1"),
                max_quantity_period_2=self.payload_quantity_for_period(record, "period_2"),
                cost_preplan=0,
                cost_period_1=0,
                cost_period_2=0,
                cash_preplan=0,
                cash_period_1=0,
                cash_period_2=0,
                equipment="EX",
                grade_fe=record["source_grade_fe"],
                grade_si=record["source_grade_si"],
                grade_al=record["source_grade_al"],
                grade_p=record["source_grade_p"],
                grade_mn=record["source_grade_mn"],
                delivered_datetime=record.get("delivered_datetime"),
                destination=record.get("destination"),
                agent=record.get("agent"),
                start_datetime=record.get("start_datetime"),
                source=record.get("source"),
                grade_streams=self.coerce_grade_streams(record.get("grade_streams")),
                source_properties=self.solver_source_properties(record),
                two_wp_planned_stockpile_destination=record.get(
                    "planned_destination"
                ),
                two_wp_first_reclaim_datetime=record.get(
                    "two_wp_first_reclaim_datetime"
                ),
                two_wp_destination_turnover_priority=record.get(
                    "two_wp_destination_turnover_priority"
                ),
                two_wp_turnover_guidance_applicable=record.get(
                    "two_wp_turnover_guidance_applicable", False
                ),
                period_values={
                    **{
                        f"max_quantity_{period_key}":
                            self.payload_quantity_for_period(
                                record, period_key
                            )
                        for period_key in self.period_keys()
                    },
                    **{
                        f"cost_{period_key}": 0
                        for period_key in self.period_keys()
                    },
                    **{
                        f"cash_{period_key}": 0
                        for period_key in self.period_keys()
                    },
                },
            )
            for record in payload_transactions.to_dict(orient="records")
        ]

    def payload_quantity_for_period(self, payload_record, period_name):
        delivered_datetime = pd.to_datetime(
            payload_record.get("delivered_datetime"),
            errors="coerce",
        )
        if pd.isna(delivered_datetime):
            return 0

        if self.periods is None:
            return payload_record["payload"]

        period_data = self.periods.get_periods()
        period_start = period_data[f"{period_name}_start"]
        period_end = period_data[f"{period_name}_end"]
        if period_start <= delivered_datetime < period_end:
            return payload_record["payload"]
        return 0

    def set_first_hex_tonnes_and_grades_to_AMT_stockpile(self):
        for stockpile_name, stockpile_data in self.stockpile_data.items():
            # Check if the stockpile has 'amt' set to True
            if stockpile_data.get('amt'):
                corresponding_hexes = sorted(
                    (
                        hex_entry for hex_entry in self.hex_sequence_table
                        if hex_entry['footprint'] == stockpile_name
                    ),
                    key=lambda hex_entry: hex_entry.get('sequence', float('inf'))
                )
                corresponding_hex = next(
                    (
                        hex_entry for hex_entry in corresponding_hexes
                        if max(float(hex_entry.get('balance', 0) or 0), 0) > 0
                    ),
                    None
                )
                if corresponding_hex:
                    # Update the balance and grades in stockpile_data
                    stockpile_data['balance'] = max(float(corresponding_hex.get('balance', 0) or 0), 0)
                    for key in corresponding_hex:
                        if key.startswith('grade_'):
                            stockpile_data[key] = corresponding_hex[key]
                    if corresponding_hex.get("grade_streams") is not None:
                        stockpile_data["grade_streams"] = corresponding_hex.get("grade_streams")
                    stockpile_data["source_properties"] = (
                        self.solver_source_properties(corresponding_hex)
                    )
                else:
                    # An AMT source without positive chunks has no opening
                    # material. Its inventory balance cannot act as a fallback.
                    stockpile_data['balance'] = 0.0
                    stockpile_data['defined_fields'] = {}
                    stockpile_data['source_properties'] = {}


