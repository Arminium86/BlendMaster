import pandas as pd
from datetime import timedelta

class ExpitDataHandler:
    def __init__(self, filepath, sheet_name):
        self.data = pd.read_excel(filepath, sheet_name=sheet_name)
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
            "HaulageResult.Times.Loading": "float64",
            "HaulageResult.Times.SpotAtDump": "float64",
            "HaulageResult.Times.SpotAtLoader": "float64",
            "HaulageResult.TruckPayload": "float64",
            "HaulageResult.NumberOfTrips": "float64"
        })
        # Filter and sort data
        self.data = self.data[
            (self.data["Source.Type"] == "Reserve") &
            (self.data["Destination.Type"] == "Stockpile")
        ].sort_values(by=["Agent.Name", "Time.StartTime", "Source.FullName", "Destination.FullName"])


    def _group_data(self):
        def calculate_weighted_averages(df, group_cols, weight_col, value_cols):
            """Calculate weighted averages for multiple columns."""
            weighted_sums = df[group_cols + [weight_col] + value_cols].copy()
            for col in value_cols:
                weighted_sums[f"{col}_weighted"] = weighted_sums[weight_col] * weighted_sums[col]
            
            grouped = weighted_sums.groupby(group_cols, as_index=False)
            total_weights = grouped[weight_col].sum()
            weighted_sums = grouped[[f"{col}_weighted" for col in value_cols]].sum()

            # Compute weighted averages
            for col in value_cols:
                weighted_sums[col] = weighted_sums[f"{col}_weighted"] / total_weights[weight_col]
            
            return weighted_sums[group_cols + value_cols]

        # Perform basic aggregation
        aggregated_data = self.data.groupby(
            ["Agent.Name", "Source.Type", "Source.FullName", "Destination.Type", "Destination.FullName"],
            as_index=False
        ).agg({
            "Time.StartTime": "first",  # First row's start time
            "Time.EndTime": "last",    # Last row's end time
            "HaulageResult.Times.Dumping": "mean",
            "HaulageResult.Times.LoadedTravel": "mean",
            "HaulageResult.Times.Loading": "mean",
            "HaulageResult.Times.SpotAtDump": "mean",
            "HaulageResult.Times.SpotAtLoader": "mean",
            "HaulageResult.TruckPayload": "mean",
            "HaulageResult.NumberOfTrips": "sum",  # Sum trips
            "Mining.wetTonnes": "sum"             # Sum wet tonnes
        })

        # Compute weighted averages for grades
        grade_cols = ["Mining.grades_fe", "Mining.grades_si", "Mining.grades_al", "Mining.grades_mn", "Mining.grades_p"]
        weighted_avg_data = calculate_weighted_averages(
            self.data,
            group_cols=["Agent.Name", "Source.Type", "Source.FullName", "Destination.Type", "Destination.FullName"],
            weight_col="Mining.wetTonnes",
            value_cols=grade_cols
        )

        # Merge weighted averages back into the aggregated data
        aggregated_data = aggregated_data.merge(weighted_avg_data, on=["Agent.Name", "Source.Type", "Source.FullName", "Destination.Type", "Destination.FullName"])

        self.data = aggregated_data.sort_values(by=["Agent.Name", "Time.StartTime", "Source.FullName", "Destination.FullName"])


    def process_transactions(self):
    
        results = []
        for agent, group in self.data.groupby("Agent.Name"):
            group = group.reset_index(drop=True)
            for i, row in group.iterrows():
                tonnes = row["Mining.wetTonnes"]
                payload = row["HaulageResult.TruckPayload"]
                start_time = row["Time.StartTime"]
                end_time = row["Time.EndTime"]
                destination = row["Destination.FullName"]
                source_name = row["Source.FullName"]
                num_trips = tonnes / payload
                int_trips = int(num_trips)
                
                for trip in range(int_trips):
                    if trip == 0:
                        delivery_time = (
                            start_time +
                            timedelta(hours=row["HaulageResult.Times.Loading"] / 60 +
                                    row["HaulageResult.Times.LoadedTravel"] / 60 +
                                    row["HaulageResult.Times.SpotAtDump"] / 60 +
                                    row["HaulageResult.Times.Dumping"] / 60)
                        )
                    else:
                        delivery_time = (
                            delivery_time +
                            timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                    row["HaulageResult.Times.Loading"] / 60
                                    )
                        )
                    results.append({
                        "Agent": agent,
                        "Source": source_name,
                        "Time.StartTime": start_time,
                        "Time.EndTime": end_time,
                        "Tonnes": payload,
                        "Grade_fe": row["Mining.grades_fe"],
                        "Grade_si": row["Mining.grades_si"],
                        "Grade_al": row["Mining.grades_al"],
                        "Grade_mn": row["Mining.grades_mn"],
                        "Grade_p": row["Mining.grades_p"],
                        "Destination": destination,
                        "DeliveredTime": delivery_time
                    })
                
                
                # this is not adding up - it does not update the next record correctly. Also need to weight average the grades when topping up a payload from the next record
                fractional_tonnes = tonnes % payload
                if fractional_tonnes > 0:
                    next_row = group.iloc[i + 1] if i + 1 < len(group) else None
                    if next_row is not None:
                        next_start_time = next_row["Time.StartTime"]
                        next_destination = next_row["Destination.FullName"]
                        
                        if (next_start_time == row["Time.EndTime"] and
                            next_destination == destination):
                            top_up_tonnes = min(payload - fractional_tonnes, next_row["Mining.wetTonnes"])
                            fractional_tonnes += top_up_tonnes
                            group.at[i + 1, "Mining.wetTonnes"] -= top_up_tonnes
                            if group.at[i + 1, "Mining.wetTonnes"] <= 0:
                                group.drop(index=i + 1, inplace=True)
                        
                    delivery_time = (
                        delivery_time +
                        timedelta(hours=row["HaulageResult.Times.SpotAtLoader"] / 60 +
                                row["HaulageResult.Times.Loading"] / 60
                                )
                    )
                    
                    results.append({
                        "Agent": agent,
                        "Source": source_name,
                        "Time.StartTime": start_time,
                        "Time.EndTime": end_time,
                        "Tonnes": fractional_tonnes,
                        "Grade_fe": row["Mining.grades_fe"],
                        "Grade_si": row["Mining.grades_si"],
                        "Grade_al": row["Mining.grades_al"],
                        "Grade_mn": row["Mining.grades_mn"],
                        "Grade_p": row["Mining.grades_p"],
                        "Destination": destination,
                        "DeliveredTime": delivery_time
                    })
                    
        return pd.DataFrame(results)



# Usage example
processor = ExpitDataHandler(filepath=r"C:\BlendMaster\blendmaster_backend_OOP\input\data.xlsx", sheet_name="aps_transactions")
result = processor.process_transactions()

# Save or display results
result.to_excel(fr"C:\BlendMaster\blendmaster_backend_OOP\output\processed_aps_transactions.xlsx")