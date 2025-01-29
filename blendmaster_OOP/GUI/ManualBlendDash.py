import dash
from dash.dependencies import Input, Output
import plotly.express as px
import pandas as pd
import random
import requests
from dash import dcc, html, Input, Output, dash_table, Dash
from flask import Flask, jsonify

class ManualBlendDash:
    def __init__(self, stored_blend_sequence_table_for_gantt, manual_gantt_legend_and_tooltip, port, crusher_rate):
        self.stored_blend_sequence_table_for_gantt = stored_blend_sequence_table_for_gantt
        self.manual_gantt_legend_and_tooltip = manual_gantt_legend_and_tooltip
        self.port = port
        self.crusher_rate = crusher_rate
        self.app = dash.Dash(__name__)
        self.colors = [
            'rgb(77, 148, 204)', 'rgb(50, 200, 50)', 'rgb(255, 255, 51)',
            'rgb(160, 80, 160)', 'rgb(255, 160, 100)'
        ]
        self.color_mapping = {}
        self.create_layout()

    def create_layout(self):
        """
        Create the layout for the Dash app.
        """
        self.app.layout = html.Div([
            html.H1("Gantt Chart"),
            dcc.Graph(id="gantt-chart"),
            dcc.Store(id="chart-data", data=self.stored_blend_sequence_table_for_gantt)  # Store for dynamic updates
        ])

    def create_gantt_chart(self, chart_data):
        """
        Create the Gantt chart using Plotly Express.
        """
        df = pd.DataFrame(chart_data)

        def assign_color_and_border(row):
            """
            Assign unique colors for User Defined Blend IDs
            and make non-User Defined fully transparent.
            """
            if row['Origin'] == 'User Defined':
                if row['Blend ID'] not in self.color_mapping:
                    # Assign a unique random color for each Blend ID
                    available_colors = set(self.colors) - set(self.color_mapping.values())
                    if available_colors:
                        self.color_mapping[row['Blend ID']] = random.choice(list(available_colors))
                    else:
                        self.color_mapping[row['Blend ID']] = random.choice(self.colors)  # Fallback if all colors are used
                return self.color_mapping[row['Blend ID']], "black"  # Visible color and black border
            else:
                return "rgba(0, 0, 0, 0)", "rgba(0, 0, 0, 0)"  # Fully transparent for non-User Defined

        # Apply the color and border assignment
        df[['Color', 'Border Color']] = df.apply(
            lambda row: pd.Series(assign_color_and_border(row)), axis=1
        )

        # Convert datetime strings to datetime objects
        df['Start Datetime'] = pd.to_datetime(df['Start Datetime'])
        df['End Datetime'] = pd.to_datetime(df['End Datetime'])

        # Merge manual_gantt_legend_and_tooltip for tooltips
        tooltip_df = pd.DataFrame(self.manual_gantt_legend_and_tooltip)
        df = pd.merge(df, tooltip_df, on="Blend ID", how="left")

        # Calculate the new column 'Feed Tonnes'
        df['Feed Tonnes'] = df['Duration (hrs)'].astype(float) * self.crusher_rate

        df['Blend ID Name'] = "Blend ID: " + df["Blend ID"]

        # Combine hover data into a categorical column for the legend
        df['Details'] = df.apply(
            lambda row: (
                "<br>Blend ID: " + str(row['Blend ID']) +
                "<br>Grade Fe: " + (str(row['Grade Fe']) if row['Grade Fe'] == "AMT" else f"{row['Grade Fe']}%") +
                "<br>Grade Si: " + (str(row['Grade Si']) if row['Grade Si'] == "AMT" else f"{row['Grade Si']}%") +
                "<br>Grade Al: " + (str(row['Grade Al']) if row['Grade Al'] == "AMT" else f"{row['Grade Al']}%") +
                "<br>Grade P: " + (str(row['Grade P']) if row['Grade P'] == "AMT" else f"{row['Grade P']}%") +
                "<br>Grade Mn: " + (str(row['Grade Mn']) if row['Grade Mn'] == "AMT" else f"{row['Grade Mn']}%") +
                "<br>Feed Tonnes: " + f"{row['Feed Tonnes']:.0f}" +
                "<br>Duration (hrs): " + f"{float(row['Duration (hrs)']):.1f}<br>" +
                (
                    "<br>Sources:" + 
                    "<br>" + 
                    "<br>".join(
                        f"{source} @ {ratio}%" 
                        for source, ratio in zip(row['Sources'].split(','), row['Source Ratios'].split(','))
                    ) 
                    if row['Sources'] and row['Source Ratios'] else ""
                ) +
                "<br>"  # Add a blank line at the end
            ) if row['Origin'] == 'User Defined' else "",
            axis=1
        )


        # Create the Gantt chart
        fig = px.timeline(
            df,
            x_start="Start Datetime",
            x_end="End Datetime",
            y="Blend ID",
            color="Details",
            hover_name="Blend ID Name",
            hover_data={
                "Blend ID": False,
                "Start Datetime": True,
                "End Datetime": True,
                "Details": True
                },  # Only show Hover Info for hover
            color_discrete_map={row['Details']: row['Color'] for _, row in df.iterrows()}
        )

        # Reverse the Y-axis order
        fig.update_yaxes(autorange="reversed")

        # Customize the bars
        for trace in fig.data:
            hover_info = trace.name
            row = df[df['Details'] == hover_info]
            if row.empty or row['Origin'].iloc[0] != 'User Defined':
                trace.marker.opacity = 0  # Fully transparent for non-User Defined
                trace.marker.line.width = 0  # No border
            else:
                trace.marker.line.color = row['Border Color'].iloc[0]  # Add border color
                trace.marker.line.width = 2  # Border width

        # Customize the legend appearance
        fig.update_layout(
            legend_title="Blend Details",
            legend=dict(
                orientation="v",
                x=1.05,
                y=1,
                bgcolor="rgba(255, 255, 255, 0.8)",
                bordercolor="black",
                borderwidth=2
            )
        )

        self.grade_profile_data = df

        return fig

    def run_app(self):
        """
        Run the Dash app.
        """
        @self.app.callback(
            Output("gantt-chart", "figure"),
            Input("chart-data", "data")
        )
        def update_gantt_chart(chart_data):
            """
            Callback to update the Gantt chart when chart-data changes.
            """
            if chart_data:
                return self.create_gantt_chart(chart_data)
            return dash.no_update

        self.app.run_server(port=self.port, debug=True, use_reloader=False)

    def update_data(self, new_data, manual_gantt_legend_and_tooltip):
        """
        Update the stored blend sequence data programmatically.
        """
        self.stored_blend_sequence_table_for_gantt = new_data
        self.manual_gantt_legend_and_tooltip = manual_gantt_legend_and_tooltip
        
        # Update the `dcc.Store` component with the new data
        self.app.layout.children[-1].data = new_data  # Update the data directly
    
    def return_grade_profile_data(self):
        return self.grade_profile_data

class DrawGradeProfiles:
    def __init__(self, data, hex_sequence_table, updated_stockpile_data, port):
        # Initialize Dash app
        self.app = dash.Dash(__name__)
        self.port = port
        self.df = data
        self.hex_sequence_table = hex_sequence_table
        self.updated_stockpile_data = updated_stockpile_data
        
        # Set up the layout
        self.app.layout = html.Div(id='main-container', children=[
            dcc.Store(id='df-store', data=self.df.to_dict('records')),
            html.Div(id='charts-container')
        ])
        
        # Set up callbacks
        self.app.callback(
            Output('charts-container', 'children'),
            Input('df-store', 'data')
        )(self.update_charts)
    

    def transform_data(self, df, grade_columns, hex_sequence_table, updated_stockpile_data):
        """Transform the data to create a 'time' column and expand the rows, modifying records where grade is 'AMT'."""

        grade_key_mapping = {
            "grade_fe": "Grade Fe",
            "grade_si": "Grade Si",
            "grade_al": "Grade Al",
            "grade_p": "Grade P",
            "grade_mn": "Grade Mn",
        }

        # Rename the keys in hex_sequence_table
        hex_sequence_table = [
            {grade_key_mapping.get(k, k): v for k, v in entry.items()} for entry in hex_sequence_table
        ]

        # Rename the keys inside updated_stockpile_data (nested dict)
        updated_stockpile_data = {
            stockpile: {grade_key_mapping.get(k, k): v for k, v in grades.items()}
            for stockpile, grades in updated_stockpile_data.items()
        }

        # Sort by Start Time
        df = df.sort_values(by='Start Datetime').reset_index(drop=True)

        # Identify records that need modification (any grade column has "AMT")
        amt_records = df[df[grade_columns].eq("AMT").any(axis=1)].copy()

        # Remove original records that had "AMT"
        df = df[~df.index.isin(amt_records.index)].reset_index(drop=True)

        new_records = []

        for _, row in amt_records.iterrows():
            blend_id = row["Blend ID"]
            sources = row["Sources"].split(",")  # Sources from df (comma-separated)
            source_ratios = list(map(float, row["Source Ratios"].split(",")))  # Corresponding ratios
            feed_tonnes = row["Feed Tonnes"]
            max_duration = row["Max Duration (hrs)"]
            start_datetime = row["Start Datetime"]

            # Compute Blend ID reclaim rate
            blend_reclaim_rate = float(feed_tonnes) / float(max_duration)

            # Compute reclaim rate for each source
            source_reclaim_rates = {source.strip(): blend_reclaim_rate * ratio for source, ratio in zip(sources, source_ratios)}

            # Process sources found in hex_sequence_table
            source_hex_data = [hex_row for hex_row in hex_sequence_table if hex_row["footprint"].strip() in [s.strip() for s in sources]]

            if not source_hex_data:
                continue  # Skip if no matching hexes found

            source_hex_data.sort(key=lambda x: x["sequence"])  # Ensure order

            current_time = start_datetime
            remaining_duration = row["Duration (hrs)"]

            while float(remaining_duration) > 0 and source_hex_data:
                min_duration = float("inf")
                hex_entries = []

                for hex_row in source_hex_data:
                    footprint = hex_row["footprint"]
                    hex_balance = hex_row["balance"]
                    source_reclaim_rate = source_reclaim_rates.get(footprint, 0)

                    if source_reclaim_rate == 0:
                        continue  # Skip if reclaim rate is 0

                    hex_duration = hex_balance / source_reclaim_rate
                    min_duration = min(min_duration, hex_duration)
                    hex_entries.append((hex_row, hex_duration, footprint))

                # Ensure we do not exceed the total remaining duration
                if float(min_duration) > float(remaining_duration):
                    min_duration = remaining_duration

                # Generate new records
                new_start_time = current_time
                new_end_time = pd.to_datetime(new_start_time) + pd.to_timedelta(min_duration, unit="h")

                total_feed_tonnes = 0
                weighted_grades = {grade: 0 for grade in grade_columns}
                total_weight = 0

                for hex_row, hex_duration, footprint in hex_entries:
                    hex_balance = hex_row["balance"]
                    hex_grades = {grade: hex_row[grade] for grade in grade_columns}

                    # Determine feed tonnes for this segment
                    source_reclaim_rate = source_reclaim_rates.get(footprint, 0)
                    calculated_balance = source_reclaim_rate * min_duration

                    total_feed_tonnes += hex_balance + calculated_balance
                    total_weight += hex_balance + calculated_balance

                    # Compute weighted average for grades
                    for grade in grade_columns:
                        hex_grade = hex_grades[grade]
                        weighted_grades[grade] += hex_grade * (hex_balance + calculated_balance)

                # **Handle sources missing from `hex_sequence_table`**
                for source in sources:
                    if source not in [hex_entry[2] for hex_entry in hex_entries]:  # If source is NOT in hex_sequence_table
                        source_reclaim_rate = source_reclaim_rates.get(source, 0)
                        if source_reclaim_rate > 0:
                            calculated_balance = source_reclaim_rate * min_duration  # Compute missing source balance
                            total_feed_tonnes += calculated_balance
                            total_weight += calculated_balance

                            if source in updated_stockpile_data:
                                source_grades = updated_stockpile_data[source]
                                for grade in grade_columns:
                                    weighted_grades[grade] += source_grades[grade] * calculated_balance

                # Normalize grade values
                for grade in grade_columns:
                    if total_weight > 0:
                        weighted_grades[grade] /= total_weight

                # Create new record
                new_records.append({
                    "Blend ID": blend_id,
                    "Origin": row["Origin"],
                    "Start Datetime": new_start_time,
                    "Duration (hrs)": min_duration,
                    "End Datetime": new_end_time,
                    "Feed Tonnes": total_feed_tonnes,
                    **weighted_grades
                })

                # Update time tracking
                current_time = new_end_time
                remaining_duration = float(remaining_duration) - min_duration

                # Remove fully depleted hexes
                source_hex_data = [hex_row for hex_row, hex_duration, _ in hex_entries if hex_duration > min_duration]

        # Convert new records to DataFrame
        if new_records:
            new_df = pd.DataFrame(new_records)
            df = pd.concat([df, new_df], ignore_index=True)

        # Re-sort the data
        df["Start Datetime"] = pd.to_datetime(df["Start Datetime"], errors="coerce")
        df = df.sort_values(by="Start Datetime").reset_index(drop=True)

        # Generate sequence column
        sequence = []
        current_sequence = 1
        previous_blend_id = None

        for _, row in df.iterrows():
            if row["Blend ID"] != previous_blend_id:
                current_sequence += 1
            sequence.append(current_sequence)
            previous_blend_id = row["Blend ID"]

        df["Sequence"] = sequence

        # Prepare records for plotting
        records = []
        for _, row in df.iterrows():
            for grade in grade_columns:
                records.append({"sequence": row["Sequence"], "time": row["Start Datetime"], "grade": row[grade], "element": grade})
                records.append({"sequence": row["Sequence"], "time": row["End Datetime"], "grade": row[grade], "element": grade})

        transformed_df = pd.DataFrame(records)
        transformed_df["time"] = pd.to_datetime(transformed_df["time"])

        transformed_df = transformed_df.sort_values(by=["sequence", "time"])
        return transformed_df

    def update_charts(self, data):
        """Generate separate charts for each grade dynamically."""
        df = pd.DataFrame(data)
        
        # Filter for 'User Defined' Origin
        df = df[df['Origin'] == 'User Defined']
        if df.empty:
            return [html.Div("No data available for 'User Defined' Origin.")]
        
        # Columns for grades
        grade_columns = ['Grade Fe', 'Grade Si', 'Grade Al', 'Grade P', 'Grade Mn']
        colors = {
            'Grade Fe': 'rgb(77, 148, 204)',
            'Grade Si': 'rgb(50, 200, 50)',
            'Grade Al': 'rgb(255, 255, 51)',
            'Grade P': 'rgb(160, 80, 160)',
            'Grade Mn': 'rgb(255, 160, 100)',
        }
        
        # Transform the data
        transformed_df = self.transform_data(df, grade_columns, self.hex_sequence_table, self.updated_stockpile_data)
        
        # Create separate charts for each grade
        charts = []
        for grade in grade_columns:
            truncated_title = grade.split()[-1]  # Splits the string and takes the last part
            grade_data = transformed_df[transformed_df['element'] == grade]
            fig = px.line(
                grade_data,
                x='time',
                y='grade',
                title=f'{truncated_title} Grade Profile',
                labels={'time': 'Time', 'grade': grade},
                color_discrete_sequence=[colors[grade]]
            )
            fig.update_traces(mode='lines+markers')
            charts.append(html.Div(dcc.Graph(figure=fig), style={'margin-bottom': '20px'}))
        
        return charts

    def update_data(self, new_data):
        """Update the DataFrame and refresh charts."""
        self.df = new_data
        self.app.layout.children[0].data = self.df.to_dict('records')  # Update the stored data
    
    def run_app(self):
        """Run the Dash app."""
        self.app.run_server(port=self.port, debug=True, use_reloader=False)

