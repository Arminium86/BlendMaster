import pandas as pd
import sqlite3
import dash
from dash import dcc, html, dash_table
import plotly.express as px

class DrawStockProfiles:
    def __init__(self, db_path):
        self.db_path = db_path

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
        Create individual charts for each unique stockpile.
        """
        if data.empty:
            print("No data available to create charts.")
            return []

        unique_stockpiles = data['stockpile'].unique()
        charts = []
        
        for stockpile in unique_stockpiles:
            stockpile_data = data[data['stockpile'] == stockpile]
            fig = px.area(
                stockpile_data,
                x='time',
                y='balance',
                color='stockpile',  # Distinguish by stockpile
                hover_data=['steady_state_number', 'agent', 'source_or_destination'] +
                           [col for col in data.columns if col.startswith('grade_')],
                title=f"Stockpile: {stockpile}"
            )
            fig.update_layout(
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
        app.run_server(debug=True)


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


        return data

    def setup_layout(self):
        """
        Set up the Dash layout.
        """
        self.app.layout = html.Div(
            style={'display': 'flex', 'flex-direction': 'row', 'padding': '20px'},
            children=[
                # Gantt Chart
                html.Div(
                    style={'flex': '2', 'padding-right': '10px'},
                    children=[
                        html.H1("Gantt Chart for Optimised Blend Report"),
                        dcc.Graph(
                            id="gantt-chart",
                            style={'width': '100%', 'height': '100vh'}  # Adjust chart width and height
                        )
                    ]
                ),
                # Property Table
                html.Div(
                    style={'flex': '1'},
                    children=[
                        html.H2("Details"),
                        dash_table.DataTable(
                            id="property-table",
                            columns=[
                                {"name": col, "id": col} for col in [
                                    "start_datetime", "end_datetime", "blend_ID", "lane", "steady_state_number",
                                    "source_agg", "source_blend_ratio_agg", "crusher_actual_tonnes",
                                    "crusher_rate_output"
                                ] + [col for col in self.fetch_data().columns if col.startswith("crusher_actual_grade_")]
                            ],
                            data=[],  # Initially empty
                            style_table={'overflowX': 'auto'},
                            style_cell={'textAlign': 'left', 'padding': '5px'},
                            style_header={'fontWeight': 'bold'},
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
            num_lanes = data['lane'].nunique()

            # Set the height in pixels based on the number of lanes
            chart_height = num_lanes * 120

            # Create Gantt chart
            fig = px.timeline(
                data,
                x_start="start_datetime",
                x_end="end_datetime",
                y="lane",  # Cascading lanes (top to bottom)
                color="blend_ID",  # Different color for each blend_ID
                hover_name="hover_name",
                hover_data={
                'blend_ID': False,
                'start_datetime': True,  # Hide start_datetime
                'end_datetime': True,    # Hide end_datetime
                'lane': False,            # Hide lane
                'Details': True           # Only display the tooltip explicitly
                },
                title=""
            )

            # Adjust layout
            fig.update_layout(
                xaxis_title="Time",
                yaxis_title="Blend",
                yaxis=dict(
                    tickmode='array',
                    tickvals=data['lane'],
                    ticktext=data['blend_ID'] # Label lanes with blend_ID
                ),
                showlegend=False,
                width=1000, # Increase chart width
                height=chart_height  # Set dynamic height in pixels
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
        self.app.run_server(debug=True, port=self.port)

# Example Usage
if __name__ == "__main__":
    db_path = r"C:\BlendMaster\blendmaster_OOP\blendmaster.db"  # SQLite database path
    chart_drawer = DrawStockProfiles(db_path)
    chart_drawer.run_app()
