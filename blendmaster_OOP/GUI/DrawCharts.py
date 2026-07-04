import sqlite3
import pandas as pd
import random
import requests, time
import dash
from dash import dcc, html, Input, Output, dash_table, Dash, State, callback_context
import dash_bootstrap_components as dbc
import plotly.express as px
from flask import Flask, jsonify, request
import plotly.graph_objects as go
import numpy as np
import ezdxf
import base64
import io
import re
from collections import defaultdict
from math import sqrt

class DrawStockProfiles:
    def __init__(self, db_path, port):
        """
        Initialize the DrawStockProfiles instance.
        :param db_path: Path to the SQLite database.
        :param port: Port to run the Dash app.
        """
        self.db_path = db_path
        self.port = port
        self.app = Dash(__name__, server=Flask(__name__))  # Flask server for custom routes
        self.setup_layout()  # Set up the initial layout
        self.setup_callbacks()  # Set up the callbacks
        self.setup_routes()  # Set up custom Flask routes

    def fetch_data(self):
        """
        Fetch data from the SQLite database.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT * FROM optimised_stockpile_profile_report"
            data = pd.read_sql(query, conn)
            conn.close()
            return data
        except Exception as e:
            print(f"Error fetching data: {e}")
            return pd.DataFrame()

    def create_charts(self, data):
        """
        Create individual charts for each unique stockpile with random colors.
        :param data: DataFrame containing the data for the charts.
        :return: List of Dash Graph components.
        """
        if data.empty:
            print("No data available to create charts.")
            return [html.Div("No data available.")]

        # Preprocess data: round and add aliases
        data['Balance'] = data['balance'].round(1).fillna('None')
        data['Steady State Number'] = data['steady_state_number'].fillna('None')
        data['Agent'] = data['agent'].fillna('None')
        data['Source or Destination'] = data['source_or_destination'].fillna('None')

        # Format grades: round to 2 decimals and add % suffix
        for col in data.columns:
            if col.startswith('grade_'):
                alias = col.replace('grade_', 'Grade ').capitalize()
                data[alias] = data[col].round(2).astype(str) + '%'

        # Generate a random color for each unique stockpile
        unique_stockpiles = data['stockpile'].unique()
        random_colors = {stockpile: self.generate_random_color() for stockpile in unique_stockpiles}

        charts = []

        for stockpile in unique_stockpiles:
            stockpile_data = data[data['stockpile'] == stockpile]
            stockpile_color = random_colors[stockpile]

            fig = px.area(
                stockpile_data,
                x='time',
                y='Balance',
                color_discrete_sequence=[stockpile_color],
                hover_data={
                    'Balance': True,
                    'Steady State Number': True,
                    'Agent': True,
                    'Source or Destination': True,
                    **{alias: True for alias in stockpile_data.columns if alias.startswith('Grade ')}
                },
                title=f"Stockpile: {stockpile}"
            )
            fig.update_layout(
                title=dict(
                    text=f"Stockpile: {stockpile}",
                    font=dict(color=stockpile_color)
                ),
                xaxis_title='Time',
                yaxis_title='Balance',
                legend_title='Stockpile',
                font=dict(
                family="Segoe UI",  # Set the font
                size=14,            # Font size
                color="black"       # Font color (optional)
                )
            )
            charts.append(dcc.Graph(figure=fig))

        return charts

    def setup_layout(self):
        """
        Set up the initial layout of the app.
        """
        self.app.layout = html.Div(
            children=[
                dcc.Input(id="manual-refresh", type="hidden"),  # Add the hidden input component
                html.Div(id="chart-container", children=[]),
            ]
        )

    def setup_callbacks(self):
        """
        Set up the callbacks for the app.
        """
        @self.app.callback(
            Output("chart-container", "children"),
            Input("manual-refresh", "value")
        )
        def update_charts(_):
            """
            Callback to update charts dynamically when triggered.
            """
            data = self.fetch_data()
            if 'time' in data.columns:
                data['time'] = pd.to_datetime(data['time'], errors='coerce')
            return self.create_charts(data)

    def setup_routes(self):
        """
        Set up custom routes for triggering refresh externally.
        """
        @self.app.server.route("/trigger-refresh", methods=["POST"])
        def trigger_refresh():
            """
            HTTP route to trigger the Dash callback manually.
            """
            # Trigger the callback by setting a value for the "manual-refresh" input
            self.app.callback_map["chart-container.children"]["inputs"][0]["value"] = "refresh"
            return jsonify({"status": "success", "message": "Refresh triggered"})

    def run_app(self):
        """
        Run the Dash app server.
        """
        self.app.run_server(debug=True, port=self.port, use_reloader=False)

    @staticmethod
    def generate_random_color():
        """
        Generate a random hex color, excluding intense magenta-like colors.
        :return: Hex color code as a string.
        """
        while True:
            r, g, b = random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
            if not (r > 200 and b > 200 and g < 100):  # Exclude intense magenta-like colors
                return f"#{r:02x}{g:02x}{b:02x}"

class DrawGanttChart:
    def __init__(self, db_path, port):
        """
        Initialize the Gantt chart generator.
        :param db_path: Path to the SQLite database.
        :param port: Port to run the Dash app.
        """
        self.db_path = db_path
        self.port = port
        self.app = dash.Dash(__name__)
        self.setup_layout()

    def fetch_data(self):
        """
        Fetch data from the optimised_blend_report table in the SQLite database.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT * FROM optimised_blend_report"
            data = pd.read_sql(query, conn)
            conn.close()
            self.debug_blend_ID(data)
            self.push_results_to_database(data)
            return data
        except Exception as e:
            print(f"Error fetching data: {e}")
            return pd.DataFrame()

    def prepare_gantt_data(self, data):
        """
        Prepare data for Gantt chart visualization.
        """
        # Ensure datetime columns are in the correct format
        data['start_datetime'] = pd.to_datetime(data['start_datetime'], errors='coerce')
        data['end_datetime'] = pd.to_datetime(data['end_datetime'], errors='coerce')

        # Aggregate sources and source_blend_ratios by blend_ID and steady_state_number
        aggregated = data.groupby(["blend_ID", "steady_state_number"]).agg({
            "source": lambda x: list(x.dropna()),  # Drop NaN values before aggregating
            "source_blend_ratio": lambda x: list(x.dropna()),
        }).reset_index()
        
        # Merge back with original data
        data = pd.merge(data, aggregated, on=["blend_ID", "steady_state_number"], suffixes=("", "_agg"))
    
        # Convert lists to comma-separated strings for DataTable compatibility
        data['source_agg'] = data['source_agg'].apply(lambda x: ", ".join(map(str, x)) if isinstance(x, list) else "")
        data['source_blend_ratio_agg'] = data['source_blend_ratio_agg'].apply(lambda x: ", ".join(map(str, x)) if isinstance(x, list) else "")

        # Add a new column for lanes (to cascade top to bottom)
        lane_map = {blend_id: idx + 1 for idx, blend_id in enumerate(sorted(data['blend_ID'].unique(), reverse=True))}
        data['lane'] = data['blend_ID'].map(lane_map)

        # Create a tooltip column with formatted sources and ratios
        data['Details'] = data.apply(
            lambda row: f"<br>Steady State: {row['steady_state_number']}<br>"
                        f"Blend Option: {row['blend_option']}<br>"
                        f"Period: {row['period']}<br>"
                        f"Sources and Ratios:<br>" +
                        "".join(
                            f" - {source} @ {float(ratio) * 100:.2f}%<br>"  # Format ratio as percent
                            for source, ratio in zip(
                                (row['source_agg'].split(", ") if isinstance(row['source_agg'], str) else []),
                                (row['source_blend_ratio_agg'].split(", ") if isinstance(row['source_blend_ratio_agg'], str) else [])
                            )
                        ) +
                        f"Crusher Rate Output: {float(row['crusher_rate_output']):.1f}<br>"  # Format to 1 decimal point
                        f"Crusher Actual Tonnes: {float(row['crusher_actual_tonnes']):.1f}<br>"  # Format to 1 decimal point
                        f"Crusher Actual Grade Fe: {float(row['crusher_actual_grade_fe']):.2f}%<br>"  # Format as percent
                        f"Crusher Actual Grade Si: {float(row['crusher_actual_grade_si']):.2f}%<br>"
                        f"Crusher Actual Grade Al: {float(row['crusher_actual_grade_al']):.2f}%<br>"
                        f"Crusher Actual Grade P: {float(row['crusher_actual_grade_p']):.2f}%<br>"
                        f"Crusher Actual Grade Mn: {float(row['crusher_actual_grade_mn']):.2f}%<br>",
            axis=1
        )
        
        # Create a legend column
        data['Legend'] = data.apply(
            lambda row: f"Blend ID: {row['blend_ID']}<br>"
                        f"Sources and Ratios:<br>" +
                        "".join(
                            f" - {source} @ {float(ratio) * 100:.2f}%<br>"  # Format ratio as percent
                            for source, ratio in zip(
                                (row['source_agg'].split(", ") if isinstance(row['source_agg'], str) else []),
                                (row['source_blend_ratio_agg'].split(", ") if isinstance(row['source_blend_ratio_agg'], str) else [])
                            )
                        ),
            axis=1
        )

        return data

    def setup_layout(self):
        """
        Set up the Dash layout.
        """
        # Define column aliases
        column_aliases = {
            "start_datetime": "Start Time",
            "end_datetime": "End Time",
            "blend_ID": "Blend ID",
            "lane": "Lane",
            "steady_state_number": "Steady State",
            "source_agg": "Sources",
            "source_blend_ratio_agg": "Blend Ratios",
            "crusher_actual_tonnes": "Crusher Tonnes",
            "crusher_rate_output": "Crusher Rate",
            "crusher_actual_grade_fe": "Grade Fe (%)",
            "crusher_actual_grade_si": "Grade Si (%)",
            "crusher_actual_grade_al": "Grade Al (%)",
            "crusher_actual_grade_p": "Grade P (%)",
            "crusher_actual_grade_mn": "Grade Mn (%)"
        }
        self.app.layout = html.Div(
            style={
                'display': 'flex',
                'flexDirection': 'column',
                'gap': '10px',
                'padding': '12px',
                'boxSizing': 'border-box',
                'fontFamily': 'Segoe UI'
            },
            children=[
                # Gantt Chart
                html.Div(
                    style={
                        'width': '100%',
                        'paddingBottom': '4px',
                    },
                    children=[
                        html.H3(
                            "Gantt Chart & Blend Details",
                            style={'margin': '0 0 8px 0', 'fontWeight': '600'}
                        ),
                        dcc.Graph(
                            id="gantt-chart",
                            style={
                                'width': '100%',
                                'height': '320px',
                                'border': '1px solid black',
                                'padding': '0',
                                'borderRadius': '4px',
                                'overflow': 'hidden',
                                'boxSizing': 'border-box'
                            }
                        )
                    ]
                ),
                # Property Table
                html.Div(
                    children=[
                        html.H2(""),
                        dash_table.DataTable(
                            id="property-table",
                            columns=[
                                {"name": column_aliases.get(col, col), "id": col}  
                                for col in [
                                    "start_datetime", "end_datetime", "blend_ID", "lane", "steady_state_number",
                                    "source_agg", "source_blend_ratio_agg", "crusher_actual_tonnes",
                                    "crusher_rate_output"
                                ] + [col for col in self.fetch_data().columns if col.startswith("crusher_actual_grade_")]
                            ],
                            data=[],  # Initially empty
                            style_table={
                                'overflowX': 'auto',
                                'overflowY': 'auto',
                                'maxHeight': '360px'
                            },
                            style_cell={
                                'textAlign': 'center',
                                'padding': '5px',
                                'whiteSpace': 'normal',
                                'overflow': 'hidden',
                                'textOverflow': 'ellipsis',
                                'maxWidth': '150px',
                                'fontFamily': 'Segoe UI',  # Set font for table cells
                                'fontSize': '14px'         # Set font size for table cells
                            },
                            style_cell_conditional=[
                                {'if': {'column_id': 'start_datetime'}, 'textAlign': 'center'},
                                {'if': {'column_id': 'end_datetime'}, 'textAlign': 'center'},
                                {'if': {'column_id': 'source_agg'}, 'textAlign': 'center'},
                                {'if': {'column_id': 'source_blend_ratio_agg'}, 'textAlign': 'center'}
                            ],
                            style_header={
                                'fontWeight': 'bold',
                                'textAlign': 'center',
                                'fontFamily': 'Segoe UI',  # Set font for header
                                'fontSize': '14px',        # Set font size for header
                                'whiteSpace': 'normal',
                                'height': 'auto',
                                'lineHeight': '1.2',
                                'padding': '5px',
                                'overflow': 'hidden',
                            },
                            hidden_columns=["lane"],  # Hide the lane column
                        )
                    ]
                )
            ]
        )

        @self.app.callback(
            dash.dependencies.Output("gantt-chart", "figure"),
            dash.dependencies.Output("gantt-chart", "style"),
            dash.dependencies.Input("gantt-chart", "id")
        )
        def update_gantt_chart(_):
            """
            Generate the Gantt chart figure.
            """
            data = self.fetch_data()
            base_chart_style = {
                'width': '100%',
                'height': '320px',
                'border': '1px solid black',
                'padding': '0',
                'borderRadius': '4px',
                'overflow': 'hidden',
                'boxSizing': 'border-box'
            }

            if data.empty:
                return px.scatter(title="No data available"), base_chart_style

            data = self.prepare_gantt_data(data)
            data['hover_name'] = "Blend ID: " + data['blend_ID'].astype(str)

            num_lanes = max(data['lane'].nunique(), 1)
            chart_height = min(max(280, 190 + (num_lanes * 55)), 720)
            chart_style = dict(base_chart_style)
            chart_style['height'] = f'{chart_height + 18}px'

            # Create Gantt chart
            fig = px.timeline(
                data,
                x_start="start_datetime",
                x_end="end_datetime",
                y="lane",  # Cascading lanes (top to bottom)
                color="Legend",  # Different color for each blend_ID
                hover_name="hover_name",
                hover_data={
                'blend_ID': False,
                'start_datetime': True,  # Hide start_datetime
                'end_datetime': True,    # Hide end_datetime
                'lane': False,            # Hide lane
                'Details': True,          # Only display the tooltip explicitly
                'Legend' : False
                },
                title=""
            )

            # Add borders to bars
            fig.update_traces(
                marker=dict(
                    line=dict(
                        width=1,  # Border thickness
                        color="black"  # Border color
                    )
                )
            )

            # Adjust layout
            lane_labels = data[['lane', 'blend_ID']].drop_duplicates().sort_values('lane')
            fig.update_layout(
                xaxis_title="",
                yaxis_title="Blend",
                font=dict(
                    family="Segoe UI",  # Set the font
                    size=14,            # Font size
                    color="black"       # Font color (optional)
                ),
                yaxis=dict(
                    tickmode='array',
                    tickvals=lane_labels['lane'],
                    ticktext=lane_labels['blend_ID']  # Label lanes with blend_ID
                ),
                showlegend=True,
                legend=dict(
                title="Blend Details",
                orientation="v",
                yanchor="top",  # Anchor the legend box at the top
                y=1.0,          # Position the legend vertically (can go beyond plot height)
                xanchor="left", # Anchor the legend box horizontally
                x=1.02,         # Position the legend horizontally
                bgcolor="rgba(255,255,255,0.5)",
                bordercolor="black",
                borderwidth=1
                ),
                autosize=True,
                height=chart_height,
                margin=dict(l=64, r=260, t=20, b=54)
            )

            return fig, chart_style

        @self.app.callback(
            dash.dependencies.Output("property-table", "data"),
            dash.dependencies.Input("gantt-chart", "clickData")
        )
        def update_property_table(click_data):
            """
            Update the property table based on Gantt chart selection.
            """
            data = self.fetch_data()
            data = self.prepare_gantt_data(data)
            
            # Select only the property table columns
            data = data[[
                "start_datetime", "end_datetime", "blend_ID", "lane", "steady_state_number",
                "source_agg", "source_blend_ratio_agg", "crusher_actual_tonnes",
                "crusher_rate_output", "crusher_actual_grade_fe", "crusher_actual_grade_si",
                "crusher_actual_grade_al", "crusher_actual_grade_p", "crusher_actual_grade_mn",
            ]]
            
            # Apply rounding to specific numeric columns
            data["crusher_actual_tonnes"] = data["crusher_actual_tonnes"].round(1)  # Round to 1 decimal point
            data["crusher_rate_output"] = data["crusher_rate_output"].round(1)  # Round to 1 decimal point
            data["crusher_actual_grade_fe"] = data["crusher_actual_grade_fe"].round(2)  # Round to 2 decimal points
            data["crusher_actual_grade_si"] = data["crusher_actual_grade_si"].round(2)
            data["crusher_actual_grade_al"] = data["crusher_actual_grade_al"].round(2)
            data["crusher_actual_grade_p"] = data["crusher_actual_grade_p"].round(2)
            data["crusher_actual_grade_mn"] = data["crusher_actual_grade_mn"].round(2)
            
            data = data.drop_duplicates()

            if click_data and "points" in click_data:
                clicked_lane = click_data['points'][0]['y']  # Match lane (y-axis value) to blend_ID
                clicked_blend_id = data.loc[data['lane'] == clicked_lane, 'blend_ID'].iloc[0]
                filtered_data = data[data['blend_ID'] == clicked_blend_id]
                return filtered_data.to_dict("records")
            
            return data.to_dict("records")

    def run_app(self):
        """
        Run the Dash app.
        """
        self.app.run_server(debug=True, port=self.port, use_reloader=False)
    
    def debug_blend_ID(self, data):

        # Sort data to process sequentially
        data.sort_values(['steady_state_number', 'source'], inplace=True)

        # Initialize variables
        blend_id = 1
        previous_sources = {}  # Tracks sources seen in previous steady states

        # Create a new blend_ID column
        data['blend_ID'] = None

        # Iterate through the DataFrame
        for steady_state in sorted(data['steady_state_number'].unique()):
            # Get rows for the current steady_state_number
            current_data = data[data['steady_state_number'] == steady_state]
            current_sources = tuple(sorted(current_data['source'].unique()))  # Unique and sorted combination of sources

            # Check if this combination has been seen before
            if current_sources in previous_sources.values():
                # Assign the existing blend ID
                existing_blend_id = [k for k, v in previous_sources.items() if v == current_sources][0]
                data.loc[data['steady_state_number'] == steady_state, 'blend_ID'] = existing_blend_id
            else:
                # Assign a new blend ID
                data.loc[data['steady_state_number'] == steady_state, 'blend_ID'] = blend_id
                previous_sources[blend_id] = current_sources
                blend_id += 1

        # Convert blend_ID column to integer
        data['blend_ID'] = data['blend_ID'].astype(int)

    def push_results_to_database(self, dataframe):
        # Connect to the SQLite database or create it
        database_name = 'blendmaster.db'
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table or use it if it already exists
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS optimised_blend_report (
            start_datetime TEXT,
            end_datetime TEXT,
            steady_state_number INTEGER,
            blend_option TEXT,
            blend_ID TEXT,
            steady_state_duration INTEGER,
            period INTEGER,
            source TEXT,
            source_blend_ratio REAL,
            source_opening_balance REAL,
            source_actual_tonnes REAL,
            source_closing_balance REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_p REAL,
            source_grade_mn REAL,
            equipment TEXT,
            equipment_rate_input REAL,
            equipment_rate_output REAL,
            crusher_actual_tonnes REAL,
            crusher_rate_input REAL,
            crusher_rate_output REAL,
            crusher_actual_grade_fe REAL,
            crusher_actual_grade_si REAL,
            crusher_actual_grade_al REAL,
            crusher_actual_grade_p REAL,
            crusher_actual_grade_mn REAL,
            crusher_grade_target_min_fe REAL,
            crusher_grade_target_max_fe REAL,
            crusher_grade_target_min_si REAL,
            crusher_grade_target_max_si REAL,
            crusher_grade_target_min_al REAL,
            crusher_grade_target_max_al REAL,
            crusher_grade_target_min_p REAL,
            crusher_grade_target_max_p REAL,
            crusher_grade_target_min_mn REAL,
            crusher_grade_target_max_mn REAL
        )
        ''')

        # Clear existing data in the table
        cursor.execute('DELETE FROM optimised_blend_report')

        # Insert the data from the DataFrame into the database table
        dataframe.to_sql('optimised_blend_report', conn, if_exists='append', index=False)

        # Commit the transaction and close the connection
        conn.commit()
        conn.close()

class DrawAMTStockpile:
    def __init__(self, db_path, port, hex_sequence_table, chunk_settings=None):
        self.db_path = db_path
        self.port = port
        self.app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
        self.selected_points = hex_sequence_table or []
        self.chunk_settings = chunk_settings or {}
        self.direction_clicks = {}
        self.reclaim_directions = {}
        self.cut_directions = {}
        self.dig_paths = {}
        self.status_message = "Select a footprint, digitize reclaim and cut directions, then generate chunks."
        self.server = self.app.server  # Get Flask server instance
        self.data = self.fetch_data()
        self.unique_footprints = self.data['footprint'].unique()
        self.refresh_call = False
        self.clean_up_hex_sequence_table()
        self.sequence_counter = {}
        self.update_sequence_counter()
        self.init_layout()

        # Add a Flask route for manual refresh
        @self.server.route("/trigger-refresh", methods=["POST"])
        def trigger_refresh():

            # Fetch fresh data
            self.data = self.fetch_data()
            self.unique_footprints = self.data['footprint'].unique()
    
            self.refresh_call = True
            self.clean_up_hex_sequence_table()
            self.update_sequence_counter()
            self.init_layout()
            return ("", 204)

    def clean_up_hex_sequence_table(self):
        """Removes all string entries from self.selected_points."""
        self.selected_points = [entry for entry in self.selected_points if not isinstance(entry, str)]
        for entry in self.selected_points:
            if isinstance(entry, dict) and isinstance(entry.get("member_hexes"), list):
                entry["member_hexes"] = ",".join(str(hex_id) for hex_id in entry["member_hexes"])

    def update_chunk_settings(self, chunk_settings):
        self.chunk_settings = chunk_settings or {}

    def update_sequence_counter(self):
        """
        Populates self.sequence_counter with the highest sequence number for each unique footprint.
        """
        sequence_map = {}

        for entry in self.selected_points:
            if isinstance(entry, dict) and 'footprint' in entry and 'sequence' in entry:
                footprint = entry['footprint']
                sequence = entry['sequence']
                
                if isinstance(sequence, int):  # Ensure sequence is an integer
                    if footprint not in sequence_map or sequence > sequence_map[footprint]:
                        sequence_map[footprint] = sequence

        self.sequence_counter = sequence_map

        self.sequence_counter = {key: value + 1 for key, value in self.sequence_counter.items()}            

    def to_float(self, value, default=0.0):
        try:
            if value in (None, ""):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def positive_tonnes(self, value):
        return max(self.to_float(value), 0.0)

    def get_chunk_setting(self, footprint, key, default=0.0):
        settings = self.chunk_settings.get(footprint, {})
        return self.to_float(settings.get(key), default)

    def get_chunk_size(self, footprint):
        reclaim_rate = self.get_chunk_setting(footprint, "average_reclaim_rate")
        reclaim_hours = self.get_chunk_setting(footprint, "chunk_reclaim_hours")
        return reclaim_rate * reclaim_hours

    def remove_footprint_chunks(self, footprint, table_data=None):
        self.selected_points = [
            entry for entry in self.selected_points
            if not (isinstance(entry, dict) and entry.get("footprint") == footprint)
        ]

        if table_data is None:
            return None

        return [
            entry for entry in table_data
            if not (isinstance(entry, dict) and entry.get("footprint") == footprint)
        ]

    def build_chunk_row(self, footprint, sequence, chunk_rows, chunk_size):
        total_tonnes = sum(row["_positive_balance"] for row in chunk_rows)
        member_hexes = [row["hex"] for row in chunk_rows if row.get("hex") is not None]
        chunk_id = f"{footprint}_CHUNK_{sequence:03d}"
        weighted_grades = {}

        for grade in ["grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn"]:
            if total_tonnes > 0:
                weighted_grades[grade] = (
                    sum(row[grade] * row["_positive_balance"] for row in chunk_rows) / total_tonnes
                )
            else:
                weighted_grades[grade] = 0

        return {
            "footprint": footprint,
            "sequence": sequence,
            "hex": chunk_id,
            "balance": round(total_tonnes, 3),
            **{grade: round(value, 4) for grade, value in weighted_grades.items()},
            "hex_count": len(member_hexes),
            "chunk_size": round(chunk_size, 3),
            "average_reclaim_rate": self.get_chunk_setting(footprint, "average_reclaim_rate"),
            "chunk_reclaim_hours": self.get_chunk_setting(footprint, "chunk_reclaim_hours"),
            "member_hexes": ",".join(str(hex_id) for hex_id in member_hexes)
        }

    def direction_vector(self, start_point, end_point):
        dx = end_point[0] - start_point[0]
        dy = end_point[1] - start_point[1]
        direction_length = sqrt(dx * dx + dy * dy)
        if direction_length == 0:
            return None
        return dx / direction_length, dy / direction_length

    def assign_cut_indices(self, filtered_data):
        ordered = filtered_data.sort_values("_reclaim_axis").copy()
        values = ordered["_reclaim_axis"].to_numpy()

        if len(values) <= 1:
            ordered["_cut_index"] = 0
            return ordered

        gaps = np.diff(values)
        positive_gaps = gaps[gaps > 1e-12]
        if len(positive_gaps) == 0:
            ordered["_cut_index"] = 0
            return ordered

        sorted_gaps = np.sort(positive_gaps)
        threshold = np.percentile(positive_gaps, 75) * 1.5
        if len(sorted_gaps) > 1:
            ratios = sorted_gaps[1:] / np.where(sorted_gaps[:-1] == 0, np.nan, sorted_gaps[:-1])
            ratios = np.nan_to_num(ratios, nan=0.0, posinf=0.0)
            max_ratio_idx = int(np.argmax(ratios))
            if ratios[max_ratio_idx] >= 2:
                threshold = (sorted_gaps[max_ratio_idx] + sorted_gaps[max_ratio_idx + 1]) / 2

        cut_indices = [0]
        current_index = 0
        for gap in gaps:
            if gap > threshold:
                current_index += 1
            cut_indices.append(current_index)

        ordered["_cut_index"] = cut_indices
        return ordered

    def order_hexes_by_dig_path(self, filtered_data):
        ordered = self.assign_cut_indices(filtered_data)
        path_rows = []

        for cut_index, group in ordered.groupby("_cut_index", sort=True):
            ascending = int(cut_index) % 2 == 0
            group = group.sort_values(["_cut_axis", "hex"], ascending=[ascending, True])
            path_rows.extend(group.to_dict("records"))

        return path_rows

    def build_chunks_for_footprint(self, footprint, reclaim_start_point, reclaim_end_point, cut_start_point, cut_end_point):
        chunk_size = self.get_chunk_size(footprint)
        if chunk_size <= 0:
            return [], "Enter positive Average Reclaim Rate and Chunk Reclaim Hours for this stockpile."

        reclaim_vector = self.direction_vector(reclaim_start_point, reclaim_end_point)
        cut_vector = self.direction_vector(cut_start_point, cut_end_point)
        if not reclaim_vector:
            return [], "Digitize two different points to define the reclaim direction."
        if not cut_vector:
            return [], "Digitize two different points to define the cut direction."

        matrix = np.array([
            [reclaim_vector[0], cut_vector[0]],
            [reclaim_vector[1], cut_vector[1]]
        ])
        if abs(np.linalg.det(matrix)) < 1e-6:
            return [], "Reclaim direction and cut direction are too close to parallel."

        filtered_data = self.data[self.data["footprint"] == footprint].copy()
        if filtered_data.empty:
            return [], "No AMT hexagons were found for this footprint."

        numeric_columns = [
            "lat", "long", "balance", "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn"
        ]
        for column in numeric_columns:
            filtered_data[column] = pd.to_numeric(filtered_data[column], errors="coerce")

        filtered_data = filtered_data.dropna(subset=["lat", "long"])
        if filtered_data.empty:
            return [], "No AMT hexagons have valid coordinates for this footprint."

        for grade in ["grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn"]:
            filtered_data[grade] = filtered_data[grade].fillna(0)

        filtered_data["_positive_balance"] = filtered_data["balance"].apply(self.positive_tonnes)
        coordinates = filtered_data[["long", "lat"]].to_numpy() - np.array(reclaim_start_point)
        basis_coordinates = np.linalg.solve(matrix, coordinates.T).T
        filtered_data["_reclaim_axis"] = basis_coordinates[:, 0]
        filtered_data["_cut_axis"] = basis_coordinates[:, 1]
        path_rows = self.order_hexes_by_dig_path(filtered_data)
        self.dig_paths[footprint] = [
            (row["long"], row["lat"], row["hex"]) for row in path_rows
        ]

        if filtered_data["_positive_balance"].sum() <= 0:
            return [], "All hexagons in this footprint have zero or negative balance."

        chunks = []
        current_rows = []
        current_tonnes = 0.0
        pending_zero_rows = []

        def finalise_current():
            nonlocal current_rows, current_tonnes
            if current_tonnes > 0:
                chunks.append(list(current_rows))
            elif current_rows and chunks:
                chunks[-1].extend(current_rows)
            current_rows = []
            current_tonnes = 0.0

        for row_dict in path_rows:
            tonnes = row_dict["_positive_balance"]

            if tonnes <= 0:
                if current_rows:
                    current_rows.append(row_dict)
                else:
                    pending_zero_rows.append(row_dict)
                continue

            if not current_rows:
                current_rows = pending_zero_rows + [row_dict]
                pending_zero_rows = []
                current_tonnes = tonnes
                continue

            before_gap = abs(chunk_size - current_tonnes)
            after_gap = abs(chunk_size - (current_tonnes + tonnes))

            if current_tonnes >= chunk_size or before_gap <= after_gap:
                finalise_current()
                current_rows = pending_zero_rows + [row_dict]
                pending_zero_rows = []
                current_tonnes = tonnes
            else:
                current_rows.append(row_dict)
                current_tonnes += tonnes

        if pending_zero_rows:
            if current_rows:
                current_rows.extend(pending_zero_rows)
            elif chunks:
                chunks[-1].extend(pending_zero_rows)

        finalise_current()

        chunk_rows = [
            self.build_chunk_row(footprint, sequence, rows, chunk_size)
            for sequence, rows in enumerate(chunks, start=1)
        ]
        message = (
            f"Generated {len(chunk_rows)} chunks for {footprint}. "
            f"Target chunk size: {chunk_size:,.0f} tonnes."
        )
        return chunk_rows, message

    def generate_chunks_from_directions(self, footprint, table_data):
        reclaim_direction = self.reclaim_directions.get(footprint)
        cut_direction = self.cut_directions.get(footprint)

        if not reclaim_direction or not cut_direction:
            missing = []
            if not reclaim_direction:
                missing.append("reclaim direction")
            if not cut_direction:
                missing.append("cut direction")
            return table_data, f"Digitize the {' and '.join(missing)} for {footprint} before generating chunks."

        chunk_rows, status_message = self.build_chunks_for_footprint(
            footprint,
            reclaim_direction["start"],
            reclaim_direction["end"],
            cut_direction["start"],
            cut_direction["end"]
        )
        if chunk_rows:
            table_data = self.remove_footprint_chunks(footprint, table_data)
            self.selected_points.extend(chunk_rows)
            table_data.extend(chunk_rows)
            self.update_sequence_counter()

        return table_data, status_message

    def member_hexes_from_entry(self, entry):
        member_hexes = entry.get("member_hexes")
        if isinstance(member_hexes, str):
            return [hex_id.strip() for hex_id in member_hexes.split(",") if hex_id.strip()]
        if isinstance(member_hexes, list):
            return member_hexes
        return []

    def dig_path_from_selected_points(self, footprint):
        selected_chunks = [
            entry for entry in self.selected_points
            if isinstance(entry, dict) and entry.get("footprint") == footprint
        ]
        selected_chunks = sorted(selected_chunks, key=lambda entry: entry.get("sequence", float("inf")))

        path_hexes = []
        for entry in selected_chunks:
            path_hexes.extend(self.member_hexes_from_entry(entry))

        if not path_hexes:
            return []

        filtered_data = self.data[self.data["footprint"] == footprint].copy()
        coordinate_lookup = {
            row["hex"]: (row["long"], row["lat"], row["hex"])
            for _, row in filtered_data.iterrows()
        }
        return [
            coordinate_lookup[hex_id]
            for hex_id in path_hexes
            if hex_id in coordinate_lookup
        ]

    def chunk_lookup_for_footprint(self, footprint):
        chunk_lookup = {}
        for entry in self.selected_points:
            if not isinstance(entry, dict) or entry.get("footprint") != footprint:
                continue

            member_hexes = self.member_hexes_from_entry(entry)

            if member_hexes:
                for hex_id in member_hexes:
                    chunk_lookup[hex_id] = entry.get("sequence")
            elif entry.get("hex"):
                chunk_lookup[entry.get("hex")] = entry.get("sequence")

        return chunk_lookup
    
    def fetch_data(self):
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT * FROM opening_AMT_stockpile_inventories"
            data = pd.read_sql(query, conn)
            conn.close()
            return data
        except Exception as e:
            print(f"Error fetching data: {e}")
            return pd.DataFrame()

    def init_layout(self):
        self.app.layout = dbc.Container([

            html.H5("AMT Stockpile Chunks"),

            # Dropdown for footprint selection
            dbc.Row([
                dbc.Col([
                    dcc.Dropdown(
                        id="footprint-dropdown",
                        options=[{"label": fp, "value": fp} for fp in self.unique_footprints],
                        placeholder="Select a Footprint",
                        style={"marginBottom": "10px"}
                    ),
                    dcc.Upload(
                        id="upload-dxf",
                        children=dbc.Button("Overlay arch_d", color="secondary", size="md"),
                        multiple=False,  # Allow only one file at a time
                        style={"marginBottom": "10px"}
                    )
                ], 
                width=6),
                dbc.Col([
                    html.Div([
                        dbc.Button("Digitize Reclaim Direction", id="digitize-direction-button", color="info", size="sm", style={"marginRight": "8px"}),
                        dbc.Button("Digitize Cut Direction", id="digitize-cut-direction-button", color="info", size="sm", style={"marginRight": "8px"}),
                        dbc.Button("Generate Chunks", id="generate-chunks-button", color="success", size="sm", style={"marginRight": "8px"}),
                        dbc.Button("Clear Footprint Chunks", id="clear-footprint-button", color="warning", size="sm"),
                    ], style={"marginBottom": "8px"}),
                    html.Div(
                        id="chunk-status",
                        children=self.status_message,
                        style={"fontSize": "12px", "fontWeight": "bold", "color": "darkblue"}
                    )
                ], width=6),
            ]),

            # Hex Size Control (Slider)
            dbc.Row([
                dbc.Col([
                    html.Label(
                        "Hexagon Marker Size:",
                        style={
                            "fontSize": "16px",  # Font size
                            "fontWeight": "bold",  # Make text bold
                            "color": "darkblue",  # Change text color
                            "fontFamily": "Arial, sans-serif",  # Set font family
                            "marginBottom": "5px"  # Add space below label
                        }
                    ),
                    dcc.Slider(
                        id="hex-size-slider",
                        min=5,
                        max=50,
                        step=1,
                        value=25,  # Default hex size
                        marks={5: "5", 25: "25", 50: "50"},
                        tooltip={"placement": "bottom", "always_visible": True}
                    )
                ], width=6),
            ], style={"marginBottom": "20px"}),

            # Graph and Table Layout
            dbc.Row([
                # Graph (75% width)
                dbc.Col([
                    dcc.Graph(
                        id="scatter-plot",
                        config={"scrollZoom": True},
                        style={"height": "600px", "width": "100%"}
                    )
                ], width=9),

                # Table (25% width)
                dbc.Col([
                    dash_table.DataTable(
                        id="selected-table",
                        columns=[{"name": col, "id": col} for col in
                                ['footprint', 'sequence', 'hex', 'balance', 'grade_fe', 'grade_si', 'grade_al', 'grade_p',
                                'grade_mn', 'hex_count', 'chunk_size']],
                        data=self.selected_points or [],
                        row_deletable=False,
                        editable=False,
                        style_table={'overflowX': 'auto', 'maxHeight': '600px', 'overflowY': 'auto'},
                        style_cell={'textAlign': 'center', 'fontFamily': 'Segoe UI', 'fontSize': '10.5px'},
                        style_header={'fontWeight': 'bold', 'fontSize': '12px', 'fontFamily': 'Segoe UI', 'textAlign': 'center'}
                    )
                ], width=3),
            ]),

        ], fluid=False)

        if not self.refresh_call:

            self.init_callbacks()

    def init_callbacks(self):

        @self.app.callback(
            [Output("selected-table", "data"),
            Output("scatter-plot", "figure"),
            Output("chunk-status", "children")],
            [Input("scatter-plot", "clickData"),
            Input("footprint-dropdown", "value"),
            Input("hex-size-slider", "value"),  # Hex size slider input
            Input("upload-dxf", "contents"),   # Handle DXF file upload
            Input("digitize-direction-button", "n_clicks"),
            Input("digitize-cut-direction-button", "n_clicks"),
            Input("generate-chunks-button", "n_clicks"),
            Input("clear-footprint-button", "n_clicks")],
            [State("selected-table", "data"),
            State("scatter-plot", "figure"),
            State("scatter-plot", "relayoutData")]
        )
        def update_table_and_plot(click_data, selected_footprint, hex_size, dxf_contents,
                                digitize_clicks, digitize_cut_clicks, generate_clicks, clear_clicks,
                                table_data, current_fig, relayout_data):
            triggered = dash.callback_context.triggered_id
            status_message = self.status_message

            if not selected_footprint:
                status_message = "Select a footprint to digitize reclaim and cut directions."
                self.status_message = status_message
                return table_data, self.generate_scatter_plot(selected_footprint, relayout_data, current_fig, hex_size), status_message

            table_data = table_data or []

            if triggered == "digitize-direction-button":
                self.direction_clicks[selected_footprint] = {"mode": "reclaim", "points": []}
                status_message = f"Click two hexagons on {selected_footprint} to define the reclaim direction."

            if triggered == "digitize-cut-direction-button":
                self.direction_clicks[selected_footprint] = {"mode": "cut", "points": []}
                status_message = f"Click two hexagons on {selected_footprint} to define the cut direction."

            if triggered == "clear-footprint-button":
                table_data = self.remove_footprint_chunks(selected_footprint, table_data)
                self.direction_clicks.pop(selected_footprint, None)
                self.dig_paths.pop(selected_footprint, None)
                self.update_sequence_counter()
                status_message = f"Cleared chunks for {selected_footprint}."

            if triggered == "generate-chunks-button":
                table_data, status_message = self.generate_chunks_from_directions(selected_footprint, table_data)

            # Handle direction digitizing from map clicks.
            if triggered == "scatter-plot" and click_data:
                clicked_point = click_data["points"][0]

                if "customdata" in clicked_point:
                    capture = self.direction_clicks.get(selected_footprint)
                    if not capture:
                        status_message = "Press Digitize Reclaim Direction or Digitize Cut Direction before clicking the map."
                    else:
                        capture["points"].append(
                            (clicked_point["x"], clicked_point["y"])
                        )
                        point_count = len(capture["points"])
                        mode = capture["mode"]
                        mode_label = "reclaim" if mode == "reclaim" else "cut"

                        if point_count == 1:
                            status_message = f"First {mode_label} direction point captured. Click the second point."
                        elif point_count >= 2:
                            start_point, end_point = capture["points"][:2]
                            target = self.reclaim_directions if mode == "reclaim" else self.cut_directions
                            target[selected_footprint] = {
                                "start": start_point,
                                "end": end_point
                            }
                            self.direction_clicks.pop(selected_footprint, None)
                            if self.reclaim_directions.get(selected_footprint) and self.cut_directions.get(selected_footprint):
                                table_data, status_message = self.generate_chunks_from_directions(selected_footprint, table_data)
                            else:
                                status_message = f"{mode_label.capitalize()} direction captured. Digitize the other direction."

            # Handle DXF or ARCHD file upload and overlay lines
            if triggered == "upload-dxf" and dxf_contents:
                try:
                    if not current_fig:
                        current_fig = self.generate_scatter_plot(
                            selected_footprint,
                            relayout_data,
                            current_fig,
                            hex_size
                        ).to_dict()

                    # Decode base64 file
                    content_type, content_string = dxf_contents.split(',')
                    decoded = base64.b64decode(content_string)

                    # Convert bytes to text (for checking if it's ARCHD format)
                    decoded_text = None
                    try:
                        decoded_text = decoded.decode("utf-8")  # Try to decode as text
                    except UnicodeDecodeError:
                        pass  # If it fails, assume it's a binary DXF file

                    # Check if it's an ARCHD file (Starts with "FMT_4")
                    if decoded_text and decoded_text.startswith("FMT_4"):
                        print("Detected ARCHD File, Parsing...")

                        # Extract points using regex
                        points = []
                        for line in decoded_text.split("\n"):
                            match = re.match(r"Point:\s+\d+\s+([\d.]+)\s+([\d.]+)", line)
                            if match:
                                easting, northing = map(float, match.groups())

                                # Convert Easting/Northing to Latitude/Longitude
                                latitude = 0.0000088511 * northing - 88.98862
                                longitude = 0.0000097153 * easting + 112.14090

                                points.append((longitude, latitude))

                        if not points:
                            print("No valid points found in ARCHD file.")
                        else:
                            # Add ARCHD line to the scatter plot
                            longitudes, latitudes = zip(*points)
                            current_fig["data"].append(go.Scatter(
                                x=longitudes,
                                y=latitudes,
                                mode="lines+markers",
                                marker=dict(size=2, color="red"),
                                line=dict(width=3, color="red"),
                                name="Line"
                            ))

                    else:
                        # Otherwise, assume it's a DXF file and process normally
                        print("Detected DXF File, Parsing...")
                        file_stream = io.BytesIO(decoded)

                        # Read DXF file correctly
                        doc = ezdxf.readfile(file_stream)  
                        msp = doc.modelspace()
                        lines = []

                        for entity in msp.query("LINE"):
                            start_x, start_y = entity.dxf.start.x, entity.dxf.start.y
                            end_x, end_y = entity.dxf.end.x, entity.dxf.end.y

                            # Convert Easting/Northing to Latitude/Longitude
                            start_lat = 0.0000088511 * start_y - 88.9692022963
                            start_lng = 0.0000097153 * start_x + 112.1407671837
                            end_lat = 0.0000088511 * end_y - 88.9692022963
                            end_lng = 0.0000097153 * end_x + 112.1407671837

                            lines.append([(start_lng, start_lat), (end_lng, end_lat)])

                        # Add DXF lines to the scatter plot
                        for line in lines:
                            current_fig["data"].append(go.Scatter(
                                x=[line[0][0], line[1][0]],
                                y=[line[0][1], line[1][1]],
                                mode="lines",
                                line=dict(color="black", width=2),
                                name="DXF Line"
                            ))

                except Exception as e:
                    print(f"Error processing file: {e}")
                    status_message = f"Error processing overlay: {e}"

            self.status_message = status_message
            return table_data, self.generate_scatter_plot(selected_footprint, relayout_data, current_fig, hex_size), status_message

    def generate_scatter_plot(self, selected_footprint, relayout_data, existing_fig, hex_size=15):

        # If existing_fig has traces, copy them to fig
        if existing_fig and existing_fig["data"]:
            fig = go.Figure()  # Start with an empty figure
            for trace in existing_fig["data"]:
                if isinstance(trace, dict):
                    trace_name = trace.get("name", "")
                    trace_mode = trace.get("mode", "")
                else:
                    trace_name = getattr(trace, "name", "")
                    trace_mode = getattr(trace, "mode", "")
                if "lines" in trace_mode and trace_name in ["Line", "DXF Line"]:
                    fig.add_trace(trace)
        else:
            fig = go.Figure()  # Create a new figure if no previous traces exist
        
        if not selected_footprint:
            return go.Figure()

        filtered_data = self.data[self.data["footprint"] == selected_footprint]

        if filtered_data.empty:
            return go.Figure()

        # Ensure we're working on a copy to avoid SettingWithCopyWarning
        filtered_data = filtered_data.copy()
        filtered_data.loc[:, "lat"] = pd.to_numeric(filtered_data["lat"], errors="coerce").round(9)
        filtered_data.loc[:, "long"] = pd.to_numeric(filtered_data["long"], errors="coerce").round(9)
        filtered_data = filtered_data.dropna(subset=["lat", "long"])

        # Remove outliers using IQR method
        q1_lat, q3_lat = np.percentile(filtered_data["lat"], [25, 75])
        iqr_lat = q3_lat - q1_lat
        lower_bound_lat = q1_lat - 1.5 * iqr_lat
        upper_bound_lat = q3_lat + 1.5 * iqr_lat

        q1_long, q3_long = np.percentile(filtered_data["long"], [25, 75])
        iqr_long = q3_long - q1_long
        lower_bound_long = q1_long - 1.5 * iqr_long
        upper_bound_long = q3_long + 1.5 * iqr_long

        filtered_data = filtered_data[
            (filtered_data["lat"] >= lower_bound_lat) & (filtered_data["lat"] <= upper_bound_lat) &
            (filtered_data["long"] >= lower_bound_long) & (filtered_data["long"] <= upper_bound_long)
        ]

        # Round latitude, longitude, and grades to 2 decimal places for tooltips
        filtered_data["lat_tooltip"] = filtered_data["lat"].round(2)
        filtered_data["long_tooltip"] = filtered_data["long"].round(2)
        for grade in ["grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn"]:
            filtered_data[f"{grade}_tooltip"] = filtered_data[grade].round(2)

        chunk_lookup = self.chunk_lookup_for_footprint(selected_footprint)
        filtered_data["chunk_sequence"] = filtered_data["hex"].map(chunk_lookup)
        chunk_palette = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
        ]

        chunked_data = filtered_data[filtered_data["chunk_sequence"].notna()]
        for chunk_sequence, group in chunked_data.groupby("chunk_sequence"):
            color = chunk_palette[(int(chunk_sequence) - 1) % len(chunk_palette)]
            fig.add_trace(go.Scatter(
                x=group["long"],
                y=group["lat"],
                mode="markers",
                marker=dict(size=hex_size, symbol="hexagon", color=color),
                name=f"Chunk {int(chunk_sequence)}",
                customdata=group[[
                    "hex", "balance", "grade_fe_tooltip", "grade_si_tooltip",
                    "grade_al_tooltip", "grade_p_tooltip", "grade_mn_tooltip",
                    "lat_tooltip", "long_tooltip", "chunk_sequence"
                ]],
                hovertemplate=(
                    "Hex: %{customdata[0]}<br>" +
                    "Chunk: %{customdata[9]}<br>" +
                    "Latitude: %{customdata[7]:.2f}<br>" +
                    "Longitude: %{customdata[8]:.2f}<br>" +
                    "Balance: %{customdata[1]}t<br>" +
                    "Fe Grade: %{customdata[2]:.2f}%<br>" +
                    "Si Grade: %{customdata[3]:.2f}%<br>" +
                    "Al Grade: %{customdata[4]:.2f}%<br>" +
                    "P Grade: %{customdata[5]:.2f}%<br>" +
                    "Mn Grade: %{customdata[6]:.2f}%<extra></extra>"
                )
            ))

        remaining_data = filtered_data[filtered_data["chunk_sequence"].isna()]
        if not remaining_data.empty:
            colors = remaining_data.apply(
                lambda row:
                "red" if row["hex_updated"] == "True" and row["balance"] <= 0 else
                "purple" if row["hex_updated"] == "True" else
                "green" if row["hex_updated"] == "False" else
                "blue",
                axis=1
            )

            # Add traces for each color group
            for color, group in remaining_data.groupby(colors):
                fig.add_trace(go.Scatter(
                    x=group["long"],
                    y=group["lat"],
                    mode="markers",
                    marker=dict(size=hex_size, symbol="hexagon", color=color),
                    name={
                        "green": "Not Started",
                        "red": "Negative Balance",
                        "purple": "Started",
                        "blue": "Other"
                    }.get(color, "Other"),
                    customdata=group[[
                        "hex", "balance", "grade_fe_tooltip", "grade_si_tooltip",
                        "grade_al_tooltip", "grade_p_tooltip", "grade_mn_tooltip",
                        "lat_tooltip", "long_tooltip"
                    ]],
                    hovertemplate=(
                        "Hex: %{customdata[0]}<br>" +
                        "Latitude: %{customdata[7]:.2f}<br>" +
                        "Longitude: %{customdata[8]:.2f}<br>" +
                        "Balance: %{customdata[1]}t<br>" +
                        "Fe Grade: %{customdata[2]:.2f}%<br>" +
                        "Si Grade: %{customdata[3]:.2f}%<br>" +
                        "Al Grade: %{customdata[4]:.2f}%<br>" +
                        "P Grade: %{customdata[5]:.2f}%<br>" +
                        "Mn Grade: %{customdata[6]:.2f}%<extra></extra>"
                    )
                ))

        direction = self.reclaim_directions.get(selected_footprint)
        if direction:
            fig.add_trace(go.Scatter(
                x=[direction["start"][0], direction["end"][0]],
                y=[direction["start"][1], direction["end"][1]],
                mode="lines+markers",
                marker=dict(size=8, color="black"),
                line=dict(width=3, color="black", dash="dash"),
                name="Reclaim Direction"
            ))

        cut_direction = self.cut_directions.get(selected_footprint)
        if cut_direction:
            fig.add_trace(go.Scatter(
                x=[cut_direction["start"][0], cut_direction["end"][0]],
                y=[cut_direction["start"][1], cut_direction["end"][1]],
                mode="lines+markers",
                marker=dict(size=8, color="#0b5cad"),
                line=dict(width=3, color="#0b5cad", dash="dot"),
                name="Cut Direction"
            ))

        dig_path = self.dig_paths.get(selected_footprint) or self.dig_path_from_selected_points(selected_footprint)
        if dig_path:
            fig.add_trace(go.Scatter(
                x=[point[0] for point in dig_path],
                y=[point[1] for point in dig_path],
                mode="lines+markers",
                marker=dict(size=4, color="#111111"),
                line=dict(width=2, color="#111111"),
                name="Dig Path",
                hoverinfo="skip"
            ))

        fig.update_layout(
            title={
                "text": f"Stockpile AMT Map: {selected_footprint}",
                "font": {
                    "size": 20,  # Title font size
                    "family": "Arial, sans-serif",  # Font family
                    "color": "darkblue"  # Font color
                },
                "x": 0.5,  # Centers the title
            },
            xaxis=dict(
                title="Longitude",
                titlefont=dict(size=16, family="Arial, sans-serif", color="black"),
                tickfont=dict(size=12, family="Arial, sans-serif", color="gray")
            ),
            yaxis=dict(
                title="Latitude",
                titlefont=dict(size=16, family="Arial, sans-serif", color="black"),
                tickfont=dict(size=12, family="Arial, sans-serif", color="gray")
            ),
            xaxis_scaleanchor="y",
            yaxis_scaleanchor="x"
        )

        # Preserve zoom state if relayout data is provided
        if relayout_data and "xaxis.range" in relayout_data and "yaxis.range" in relayout_data:
            fig.update_layout(
                xaxis_range=relayout_data["xaxis.range"],
                yaxis_range=relayout_data["yaxis.range"]
            )

        return fig

    def resequence_table_data(self, table_data):
        """
        Resequences the 'sequence' values in table_data for each unique 'footprint', starting from 1.
        """
        # Group entries by footprint
        grouped_data = defaultdict(list)
        
        for entry in table_data:
            if isinstance(entry, dict) and 'footprint' in entry and 'sequence' in entry:
                grouped_data[entry['footprint']].append(entry)
        
        # Sort and resequence each footprint group
        for footprint, entries in grouped_data.items():
            entries.sort(key=lambda x: x['sequence'])  # Sort by original sequence
            for idx, entry in enumerate(entries, start=1):
                entry['sequence'] = idx  # Assign new sequence starting from 1

        return table_data  # Updated in place
    
    def run_app(self):
        self.app.run_server(port=self.port, debug=True, use_reloader=False)

    def return_hex_sequence(self):
        self.clean_up_hex_sequence_table()
        return self.selected_points
