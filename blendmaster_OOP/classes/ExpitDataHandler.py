import pandas as pd
from datetime import timedelta
import snowflake.connector
from datetime import datetime
from pandas import DataFrame

class ExpitDataHandler:
    def __init__(self, input_data, include_crusher_destinations=False, selected_crusher_name=None):
        self.include_crusher_destinations = bool(include_crusher_destinations)
        self.selected_crusher_names = self._normalize_selected_crusher_names(selected_crusher_name)
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
    def get_distinct_crusher_destinations(input_data):
        data = pd.read_csv(
            input_data,
            usecols=lambda column: column in {"Destination.Type", "Destination.FullName"}
        )
        if data.empty or "Destination.Type" not in data or "Destination.FullName" not in data:
            return []

        destination_type = data["Destination.Type"].astype("string").str.strip()
        crusher_names = (
            data.loc[destination_type == "Crusher", "Destination.FullName"]
            .astype("string")
            .str.strip()
            .dropna()
        )
        return sorted(name for name in crusher_names.unique() if name)

    def _preprocess_data(self):
        # Explicit datetime parsing with the correct format
        self.data["Time.StartTime"] = pd.to_datetime(
            self.data["Time.StartTime"])
        
        
        self.data["Time.EndTime"] = pd.to_datetime(
            self.data["Time.EndTime"])
        
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
        destination_name = self.data["Destination.FullName"].str.strip()
        stockpile_destination_mask = destination_type == "Stockpile"
        crusher_destination_mask = pd.Series(False, index=self.data.index)
        if self.include_crusher_destinations and self.selected_crusher_names:
            crusher_destination_mask = (
                (destination_type == "Crusher")
                & destination_name.isin(self.selected_crusher_names)
            )

        # Filter and sort data. Stockpile destinations remain the planned APS builds.
        # Selected crusher destinations are added as re-evaluable direct-tip candidates.
        self.data = self.data[
            (self.data["Source.Type"] == "Reserve") &
            (stockpile_destination_mask | crusher_destination_mask)
        ].sort_values(by=["Agent.Name", "Time.StartTime", "Source.FullName", "Destination.FullName"])

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
            "aps_direct_tip_candidate": bool(is_crusher_destination),
        }

    def _group_data(self):
        # Create WeightedRate column without directly inserting into the fragmented DataFrame
        weighted_rate = self.data["HaulageResult.LoaderProductionRate.Wtph"] * self.data["Mining.wetTonnes"]

        # Concatenate the new column with the existing DataFrame
        self.data = pd.concat([self.data, weighted_rate.rename("WeightedRate")], axis=1)

        # Perform basic aggregation
        aggregated_data = self.data.groupby(
            ["Agent.Name", "Source.Type", "Source.FullName", "Destination.Type", "Destination.FullName"],
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
                    
                    payload = row["HaulageResult.TruckPayload"]
                    start_time = row["Time.StartTime"]
                    destination = row["Destination.FullName"]
                    source_name = row["Source.FullName"]
                    destination_metadata = self._payload_destination_metadata(row)
                    load_time = payload / row["HaulageResult.LoaderProductionRate.Wtph"]
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
