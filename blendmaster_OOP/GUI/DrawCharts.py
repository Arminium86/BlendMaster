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
            style={'display': 'flex', 'flexDirection': 'column', 'padding': '20px'},
            children=[
                # Gantt Chart
                html.Div(
                    style={
                        'width': '100%',  # Ensure the Gantt chart container spans full width
                        'paddingBottom': '20px',  # Optional padding below the chart
                    },
                    children=[
                        html.H2("Gantt Chart & Blend Details"),
                        dcc.Graph(
                            id="gantt-chart",
                            style={
                                'width': '100%',  # Ensure the chart spans full width
                                'height': '100vh',  # Adjust height as needed
                                'border': '2px solid black',  # Add a black border
                                'padding': '0',  # Remove padding
                                'borderRadius': '5px',  # Optional: Rounded corners
                                'overflow': 'auto',  # Ensure content stays inside the border
                                'boxSizing': 'border-box'  # Include padding in total size calculations
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
                            style_table={'overflowX': 'auto'},
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
            dash.dependencies.Input("gantt-chart", "id")
        )
        def update_gantt_chart(_):
            """
            Generate the Gantt chart figure.
            """
            data = self.fetch_data()

            if data.empty:
                return px.scatter(title="No data available")

            data = self.prepare_gantt_data(data)
            data['hover_name'] = "Blend ID: " + data['blend_ID'].astype(str)

            # Calculate the number of unique lanes
            num_lanes = max(data['lane'].nunique(),3)

            # Set the height in pixels based on the number of lanes
            chart_height = num_lanes * 100

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
                    tickvals=data['lane'],
                    ticktext=data['blend_ID']  # Label lanes with blend_ID
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
                width=1750,
                height=chart_height + 200
            )

            return fig

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
    def __init__(self, db_path, port, hex_sequence_table):
        self.db_path = db_path
        self.port = port
        self.app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
        self.selected_points = hex_sequence_table
        self.sequence_counter = {}
        self.server = self.app.server  # Get Flask server instance
        self.data = self.fetch_data()
        self.unique_footprints = self.data['footprint'].unique()
        self.refresh_call = False
        self.hex_sequence_table = hex_sequence_table
        self.init_layout()

        # Add a Flask route for manual refresh
        @self.server.route("/trigger-refresh", methods=["POST"])
        def trigger_refresh():

            # Fetch fresh data
            self.data = self.fetch_data()
            self.unique_footprints = self.data['footprint'].unique()
    
            self.refresh_call = True
            self.init_layout()

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

            html.H5("AMT Stockpile Depletion Sequence"),

            # Dropdown for footprint selection
            dbc.Row([
                dbc.Col([
                    dcc.Dropdown(
                        id="footprint-dropdown",
                        options=[{"label": fp, "value": fp} for fp in self.unique_footprints],
                        placeholder="Select a Footprint",
                        style={"marginBottom": "10px"}
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
                    html.H5("Sequence Table", style={
                            "textAlign": "center",
                            "fontSize": "18px",
                            "fontWeight": "bold",
                            "marginBottom": "10px"
                        }),
                    dash_table.DataTable(
                        id="selected-table",
                        columns=[{"name": col, "id": col} for col in
                                ['footprint', 'sequence', 'hex', 'balance', 'grade_fe', 'grade_si', 'grade_al', 'grade_p',
                                'grade_mn']],
                        data=self.hex_sequence_table or [],
                        row_deletable=True,
                        editable=False,
                        style_table={'overflowX': 'auto'},
                        style_cell={'textAlign': 'center', 'fontFamily': 'Segoe UI', 'fontSize': '10.5px'},
                        style_header={'fontWeight': 'bold', 'fontSize': '12px', 'fontFamily': 'Segoe UI', 'textAlign': 'center'}
                    ),

                    # Buttons Below the Table
                    html.Div([
                        dbc.Button("Reset Table", id="reset-button", color="danger", size="sm", style={"margin": "10px"}),
                        dbc.Button("Store Table", id="store-button", color="primary", size="sm", style={"margin": "10px"}),
                    ], style={"textAlign": "center", "marginTop": "10px"})
                ], width=3),
            ]),

        ], fluid=False)

        if not self.refresh_call:

            self.init_callbacks()

    def init_callbacks(self):
        @self.app.callback(
            [Output("selected-table", "data"),
            Output("scatter-plot", "figure")],  # Single callback handling both
            [Input("scatter-plot", "clickData"),
            Input("footprint-dropdown", "value"),
            Input("reset-button", "n_clicks"),
            Input("hex-size-slider", "value")],  # Add hex size slider input
            [State("selected-table", "data"),
            State("scatter-plot", "relayoutData")]
        )
        def update_table_and_plot(click_data, selected_footprint, reset_clicks, hex_size, table_data, relayout_data):
            triggered = dash.callback_context.triggered_id

            # Reset table and scatter plot
            if triggered == "reset-button":
                self.selected_points = []
                return [], self.generate_scatter_plot(selected_footprint, None, hex_size)  # Pass hex_size

            if not selected_footprint:
                return table_data, self.generate_scatter_plot(selected_footprint, relayout_data, hex_size)

            table_data = table_data or []

            # Handle point selection/unselection
            if triggered == "scatter-plot" and click_data:
                clicked_point = click_data["points"][0]
                clicked_hex = clicked_point["customdata"][0]  # Use customdata[0] for hex value

                if clicked_hex in self.selected_points:
                    # Unselect point
                    self.selected_points.remove(clicked_hex)
                    table_data = [row for row in table_data if row["hex"] != clicked_hex]
                else:
                    # Select point
                    self.selected_points.append(clicked_hex)
                    sequence = self.sequence_counter.get(selected_footprint, 1)
                    new_row = {
                        "footprint": selected_footprint,
                        "sequence": sequence,
                        "hex": clicked_hex,
                        "balance": clicked_point["customdata"][1],
                        "grade_fe": clicked_point["customdata"][2],
                        "grade_si": clicked_point["customdata"][3],
                        "grade_al": clicked_point["customdata"][4],
                        "grade_p": clicked_point["customdata"][5],
                        "grade_mn": clicked_point["customdata"][6]
                    }
                    table_data.append(new_row)
                    self.sequence_counter[selected_footprint] = sequence + 1

            return table_data, self.generate_scatter_plot(selected_footprint, relayout_data, hex_size)

        @self.app.callback(
            Output("store-button", "n_clicks"),
            Input("store-button", "n_clicks"),
            State("selected-table", "data")
        )
        def store_table(n_clicks, table_data):
            if n_clicks:
                self.selected_points = table_data
                
            return n_clicks

    def generate_scatter_plot(self, selected_footprint, relayout_data, hex_size=15):
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

        colors = filtered_data.apply(
            lambda row: "black" if (
                row["hex"] in self.selected_points or  # Case: direct string match
                any(row["hex"] in d.values() for d in self.selected_points if isinstance(d, dict))  # Case: inside dict values
            ) else
            "red" if row["hex_updated"] == "True" and row["balance"] <= 0 else
            "purple" if row["hex_updated"] == "True" else
            "green" if row["hex_updated"] == "False" else
            "blue",
            axis=1
        )

        fig = go.Figure()
        
        # Add traces for each color group
        for color, group in filtered_data.groupby(colors):
            fig.add_trace(go.Scatter(
                x=group["long"],
                y=group["lat"],
                mode="markers",
                marker=dict(size=hex_size, symbol="hexagon", color=color),
                name={
                    "green": "Not Started",
                    "red": "Negative Balance",
                    "purple": "Started",
                    "black": "Selected",
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

    def run_app(self):
        self.app.run_server(port=self.port, debug=True, use_reloader=False)

    def return_hex_sequence(self):
        return self.selected_points