import pandas as pd
import sqlite3
import dash
from dash import dcc, html, dash_table
import plotly.express as px
import random

class DrawStockProfiles:
    def __init__(self, db_path, port):
        self.db_path = db_path
        self.port = port

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
        """
        if data.empty:
            print("No data available to create charts.")
            return []

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
            stockpile_color = random_colors[stockpile]  # Get the color for this stockpile

            fig = px.area(
                        stockpile_data,
                        x='time',
                        y='Balance',  # Use the alias column
                        color_discrete_sequence=[stockpile_color],  # Apply the stockpile's random color
                        hover_data={
                            'Balance': True,  # Include rounded balance
                            'Steady State Number': True,  # Include alias
                            'Agent': True,  # Include alias
                            'Source or Destination': True,  # Include alias
                            **{alias: True for alias in stockpile_data.columns if alias.startswith('Grade ')}  # Include grades
                        },
                        title=f"Stockpile: {stockpile}"
                    )
            # Set the title color to match the chart's color
            fig.update_layout(
            title=dict(
                text=f"Stockpile: {stockpile}",
                font=dict(color=stockpile_color)  # Set title font color
            ),
            xaxis_title='Time',
            yaxis_title='Balance',
            legend_title='Stockpile'
            )
            charts.append(dcc.Graph(figure=fig))  # Append to the Dash layout

        return charts

    def run_app(self):
        """
        Run a Dash app to display charts in a scrollable column.
        """
        # Fetch data
        data = self.fetch_data()
        if 'time' in data.columns:
            data['time'] = pd.to_datetime(data['time'], errors='coerce')

        # Create charts
        charts = self.create_charts(data)

        # Initialize Dash app
        app = dash.Dash(__name__)
        app.layout = html.Div(
            style={'overflowY': 'scroll', 'height': '100vh'},  # Enable vertical scrolling
            children=[
                html.Div(
                    style={'padding': '10px'},
                    children=charts  # Insert charts
                )
            ]
        )
        # Run the Dash app
        app.run_server(debug=True, port=self.port, use_reloader=False)

    def generate_random_color(self):
        """
        Generate a random hex color, excluding intense magenta-like colors.
        """
        while True:
            # Generate random RGB components
            r, g, b = random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
            
            # Exclude intense magenta-like colors (high red and blue, low green)
            if not (r > 200 and b > 200 and g < 100):
                return f"#{r:02x}{g:02x}{b:02x}"

class DrawGanttChart:
    def __init__(self, db_path, port=8050):
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
            style={'display': 'flex', 'flex-direction': 'column', 'padding': '20px'},
            children=[
                # Gantt Chart
                html.Div(
                    style={
                        'width': '100%',  # Ensure the Gantt chart container spans full width
                        'padding-bottom': '20px',  # Optional padding below the chart
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

 # Example Usage
if __name__ == "__main__":
    db_path = r"C:\BlendMaster\blendmaster_OOP\blendmaster.db"  # SQLite database path
    chart_drawer = DrawGanttChart(db_path, 8051)
    chart_drawer.run_app()