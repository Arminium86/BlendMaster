import pandas as pd
from datetime import timedelta

class ExpitDataHandler:
    def __init__(self, input_data, aps_transactions):
        self.data = pd.read_excel(input_data, sheet_name=aps_transactions)
        self._preprocess_data()
        self._group_data()

    def _preprocess_data(self):
        # Explicit datetime parsing with the correct format
        self.data["Time.StartTime"] = pd.to_datetime(
            self.data["Time.StartTime"], 
            format="%d/%m/%Y %I:%M:%S %p", 
            errors="coerce"
        )
        
        self.data["Time.EndTime"] = pd.to_datetime(
            self.data["Time.EndTime"], 
            format="%d/%m/%Y %I:%M:%S %p", 
            errors="coerce"
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
                    else:
                        delivery_time = (
                            delivery_time +
                            timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                    load_time)
                        )
                    results.append({
                        "agent": agent,
                        "source": source_name,
                        "start_datetime": start_time,
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

                    # Append the topped-up trip
                    results.append({
                        "agent": agent,
                        "source": source_name,
                        "start_datetime": start_time,
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