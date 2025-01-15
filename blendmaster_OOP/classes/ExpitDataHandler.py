import pandas as pd
from datetime import timedelta

class ExpitDataHandler:
    def __init__(self, input_data):
        self.data = pd.read_csv(input_data)
        if not self.data.empty:
            self._preprocess_data()
            self._group_data()

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
        # Filter and sort data (no direct tip yet)
        self.data = self.data[
            (self.data["Source.Type"] == "Reserve") &
            (self.data["Destination.Type"] == "Stockpile")
        ].sort_values(by=["Agent.Name", "Time.StartTime", "Source.FullName", "Destination.FullName"])

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
            results = []
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
                    load_time = payload / row["HaulageResult.LoaderProductionRate.Wtph"]
                    num_trips = tonnes / payload
                    int_trips = int(num_trips)

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
                        results.append({
                            "agent": agent,
                            "source": source_name,
                            "start_datetime": mining_start_time,
                            "payload": payload,
                            "source_grade_fe": row["Mining.grades_fe"],
                            "source_grade_si": row["Mining.grades_si"],
                            "source_grade_al": row["Mining.grades_al"],
                            "source_grade_mn": row["Mining.grades_mn"],
                            "source_grade_p": row["Mining.grades_p"],
                            "destination": destination,
                            "delivered_datetime": delivery_time
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

                            delivery_time = (
                                delivery_time +
                                timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                    load_time)
                            )
                            
                            mining_start_time = (mining_start_time +
                            timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                        load_time)
                            )

                        # Append the topped-up trip
                        results.append({
                            "agent": agent,
                            "source": source_name,
                            "start_datetime": mining_start_time,
                            "payload": fractional_tonnes,
                            "source_grade_fe": weighted_grades["Grade_fe"],
                            "source_grade_si": weighted_grades["Grade_si"],
                            "source_grade_al": weighted_grades["Grade_al"],
                            "source_grade_mn": weighted_grades["Grade_mn"],
                            "source_grade_p": weighted_grades["Grade_p"],
                            "destination": destination,
                            "delivered_datetime": delivery_time
                        })

            return pd.DataFrame(results)
    
    def get_current_block(self, agent, time):
        """Retrieve the current or last block a load agent has interacted with in FMS."""
        if not self.data.empty:
            return "Reserves/EW/WED06/01/475/101/475/BS03_3", 1910

    def update_transactions(self, expit_payload_transactions, now):
       
        if not self.data.empty:   
            
            updated_transactions = expit_payload_transactions
            
            # Process transactions grouped by `agent`
            grouped = updated_transactions.groupby("agent")
            
            updated_groups = []  # Store updated groups here
            
            for agent, group in grouped:
                # Get current block info for the agent
                current_block_name, current_block_mined_tonnes = self.get_current_block(agent, now)

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

            # Concatenate all updated groups into one DataFrame
            updated_transactions = pd.concat(updated_groups, ignore_index=True)
            
            return updated_transactions
