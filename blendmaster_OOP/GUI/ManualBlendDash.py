import dash
from dash import html, dcc, dash_table, Input, Output, State
import plotly.express as px
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
#from classes.PeriodManager import PeriodManager

class ManualBlendDash:
    
    def __init__(self, periods, db_path, port):
        self.app = dash.Dash(__name__)
        self.database = db_path
        self.default_start_datetime = periods.get_periods()["preplan_start"].strftime("%Y-%m-%dT%H:%M")
        self.default_end_datetime = periods.get_periods()["period_2_end"].strftime("%Y-%m-%dT%H:%M")
        self.db_path = db_path
        self.port = port
        self.setup_database()

        # Layout
        self.app.layout = html.Div(
            style={'display': 'flex', 'flex-direction': 'column', 'padding': '20px', 'font-family': 'Segoe UI', 'font-size': '10px'},
            children=[
                # Gantt Chart
                html.Div(
                    style={'width': '100%', 'padding-bottom': '20px'},
                    children=[
                        html.H2("Gantt Chart & Blend Details", style={'font-weight': 'bold'}),
                        dcc.Graph(
                            id="gantt-chart",
                            style={
                                'width': '100%',
                                'height': '60vh',
                                'border': '2px solid black',
                                'padding': '0',
                                'borderRadius': '5px',
                                'overflow': 'auto',
                                'boxSizing': 'border-box',
                            }
                        ),
                    ]
                ),
                # Input Table
                # Define the calendar and table layout
                html.Div(
                    id="input-container",
                    style={'margin-top': '20px'},
                    children=[
                        html.H3("Blend Details Input", style={'font-weight': 'bold'}),
                        dash_table.DataTable(
                            id="input-table",
                            columns=[
                                {"name": "Blend ID", "id": "blend_ID", "editable": False},
                                {"name": "Start Date", "id": "start_date", "presentation": "input"},
                                {"name": "Start Time", "id": "start_time", "presentation": "dropdown"},
                                {"name": "End Date", "id": "end_date", "presentation": "input"},
                                {"name": "End Time", "id": "end_time", "presentation": "dropdown"},
                                {"name": "Source 1", "id": "source_1", "presentation": "dropdown"},
                                {"name": "Source 2", "id": "source_2", "presentation": "dropdown"},
                                {"name": "Source 3", "id": "source_3", "presentation": "dropdown"},
                                {"name": "Source 4", "id": "source_4", "presentation": "dropdown"},
                                {"name": "Source 5", "id": "source_5", "presentation": "dropdown"},
                                {"name": "Source Ratio 1", "id": "ratio_1", "type": "numeric"},
                                {"name": "Source Ratio 2", "id": "ratio_2", "type": "numeric"},
                                {"name": "Source Ratio 3", "id": "ratio_3", "type": "numeric"},
                                {"name": "Source Ratio 4", "id": "ratio_4", "type": "numeric"},
                                {"name": "Source Ratio 5", "id": "ratio_5", "type": "numeric"},
                                {"name": "Crusher Rate", "id": "crusher_rate", "type": "numeric"},
                            ],
                            editable=True,
                            style_table={'overflowX': 'auto'},
                            style_header={
                                'backgroundColor': '#f4f4f4',
                                'fontWeight': 'bold',
                                'textAlign': 'center',
                            },
                            style_data={
                                'textAlign': 'center',
                            },

                             style_data_conditional=[
                                {
                                    "if": {"column_id": col},
                                    "color": "black",  # Set dropdown font color to black
                                }
                                for col in ["start_time", "end_time", "source_1", "source_2", "source_3", "source_4", "source_5"]
                            ],
                            dropdown={
                                "start_time": {
                                    "options": [
                                        {"label": f"{hour:02d}:{minute:02d}", "value": f"{hour:02d}:{minute:02d}"}
                                        for hour in range(24) for minute in range(60)
                                    ]
                                },
                                "end_time": {
                                    "options": [
                                        {"label": f"{hour:02d}:{minute:02d}", "value": f"{hour:02d}:{minute:02d}"}
                                        for hour in range(24) for minute in range(60)
                                    ]
                                },
                                **{
                                    f"source_{i}": {
                                        "options": [{"label": f"Stockpile {j}", "value": f"Stockpile {j}"} for j in range(1, 6)]
                                    } for i in range(1, 6)
                                },
                            },
                        ),
                        html.Div(
                            id="calendar-picker",
                            children=[
                                html.Div(
                                    style={"display": "flex", "justifyContent": "space-between", "margin-top": "20px"},
                                    children=[
                                        dcc.DatePickerSingle(
                                            id="date-picker",
                                            date=datetime.today().strftime("%Y-%m-%d"),
                                            placeholder="Date",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Button("Submit", id="submit-button", style={'margin-top': '10px'}),
                    ]
                )
            ]
        )

        # Register Callbacks
        self.register_callbacks()

    def setup_database(self):
        """Setup SQLite database."""
        conn = sqlite3.connect(self.database)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS manual_blend_dash_input (
                blend_ID INTEGER,
                start_datetime TEXT,
                end_datetime TEXT,
                source_1 TEXT,
                source_2 TEXT,
                source_3 TEXT,
                source_4 TEXT,
                source_5 TEXT,
                ratio_1 REAL,
                ratio_2 REAL,
                ratio_3 REAL,
                ratio_4 REAL,
                ratio_5 REAL,
                crusher_rate REAL
            )
        """)
        # Clear the table if it exists
        cursor.execute("DELETE FROM manual_blend_dash_input")
       
        conn.commit()
        conn.close()

    def fetch_data(self):
        """Fetch data for the Gantt chart."""
        conn = sqlite3.connect(self.database)
        df = pd.read_sql("SELECT * FROM manual_blend_dash_input", conn)
        conn.close()

        if df.empty:
            # Default data if table is empty
            df = pd.DataFrame({
                "blend_ID": list(range(1, 8)),
                "start_datetime": [self.default_start_datetime] * 7,
                "end_datetime": [self.default_end_datetime] * 7,
                "lane": list(range(1, 8)),
                "Legend": ["Blend"] * 7,
            })
        return df

    def create_gantt_figure(self, data):
        """Generate the Gantt chart figure with custom colors and borders."""
        # Define 50%-sharper pale RGB colors for each blend_ID
        colors = [
            'rgb(77, 148, 204)',  # Sharper Pale Blue
            'rgb(50, 200, 50)',   # Sharper Pale Green
            'rgb(255, 255, 51)',  # Sharper Pale Yellow
            'rgb(255, 60, 90)',   # Sharper Pale Red
            'rgb(160, 80, 160)',  # Sharper Pale Purple
            'rgb(255, 160, 100)', # Sharper Pale Orange
            'rgb(77, 200, 200)'   # Sharper Pale Cyan
        ]
        blend_ids = sorted(data["blend_ID"].unique())  # Ensure blend_ID is unique and sorted
        color_map = {str(blend_id): colors[i % len(colors)] for i, blend_id in enumerate(blend_ids)}

        # Convert blend_ID to string for discrete mapping
        data["blend_ID"] = data["blend_ID"].astype(str)

        # Create the Gantt chart
        fig = px.timeline(
            data,
            x_start="start_datetime",
            x_end="end_datetime",
            y="blend_ID",
            title="Gantt Chart",
            color="blend_ID",  # Use blend_ID for discrete color mapping
            color_discrete_map=color_map,  # Apply the custom 50%-sharper pale colors
        )

        # Add a border to each bar
        for trace in fig.data:
            trace.update(marker=dict(line=dict(width=1, color='black')))  # 1px black border

        # Update layout for correct display
        fig.update_layout(
            legend_title="Blend ID",
            yaxis=dict(tickvals=data["blend_ID"].unique(), ticktext=data["blend_ID"].unique()),
            font=dict(family="Segoe UI", size=10),
            height=700,
        )
        return fig

    def register_callbacks(self):
        """Register Dash callbacks."""
        @self.app.callback(
            [Output("gantt-chart", "figure"),
             Output("input-table", "data")],
            [Input("gantt-chart", "clickData"),
             Input("submit-button", "n_clicks")],
            [State("input-table", "data")]
        )
        def handle_gantt_and_table(click_data, n_clicks, table_data):
            """Handle updates to the Gantt chart and input table."""
                        
            ctx = dash.callback_context
            triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]

            data = self.fetch_data()
            if triggered_id == "gantt-chart" and click_data:
                clicked_blend_id = click_data["points"][0]["y"]
                updated_table_data = data.to_dict("records")
                return self.create_gantt_figure(data), updated_table_data

            elif triggered_id == "submit-button" and n_clicks:
                 for row in table_data:

                    # Check if values are not None
                    if row['start_date'] and row['start_time'] and row['end_date'] and row['end_time']:

                        # Merge date and time into ISO format
                        start_datetime = f"{row['start_date']} {row['start_time']}"
                        end_datetime = f"{row['end_date']} {row['end_time']}"
                        
                        # Convert to ISO 8601 format
                        row['start_datetime'] = datetime.strptime(start_datetime, "%m/%d/%Y %H:%M").strftime("%Y-%m-%dT%H:%M")
                        row['end_datetime'] = datetime.strptime(end_datetime, "%m/%d/%Y %H:%M").strftime("%Y-%m-%dT%H:%M")
        
                        # Save updated data
                        conn = sqlite3.connect(self.database)
                        pd.DataFrame(table_data).to_sql("manual_blend_dash_input", conn, if_exists="replace", index=False)
                        conn.close()
                        return self.create_gantt_figure(self.fetch_data()), table_data
                        
                    else:
                        # Save updated data
                        conn = sqlite3.connect(self.database)
                        pd.DataFrame(table_data).to_sql("manual_blend_dash_input", conn, if_exists="replace", index=False)
                        conn.close()
                        return self.create_gantt_figure(self.fetch_data()), table_data

            # Initial load
            return self.create_gantt_figure(data), data.to_dict("records")

    def run(self):
        """Run the Dash app."""
        self.app.run_server(debug=True)

class PeriodManager:
    def __init__(self):
        self.periods = {}

    def calculate_periods(self, start_time):
        now = start_time

        # Define next 6AM and 6PM
        if now.hour >= 6 and now.hour < 18: 
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
            next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

        elif now.hour >= 18:
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
            next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)

        else:  
            next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
            next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

        # Preplan ends at the closest 6AM or 6PM
        preplan_end = min(next_6am, next_6pm)

        # Period 1 and Period 2 follow the preplan period
        period_1_start = preplan_end
        period_1_end = period_1_start + timedelta(hours=12)
        period_2_start = period_1_end
        period_2_end = period_2_start + timedelta(hours=12)

        self.periods = {
            "preplan_start": now,
            "preplan_end": preplan_end,
            "preplan_duration": (preplan_end - now).total_seconds() / 3600,
            "period_1_start": period_1_start,
            "period_1_end": period_1_end,
            "period_1_duration": (period_1_end - period_1_start).total_seconds() / 3600,
            "period_2_start": period_2_start,
            "period_2_end": period_2_end,
            "period_2_duration": (period_2_end - period_2_start).total_seconds() / 3600,
        }

    def get_periods(self):
        return self.periods

# Run the app
if __name__ == "__main__":
    db_path = r"C:\BlendMaster\blendmaster_OOP\blendmaster.db"  # SQLite database path
    periods = PeriodManager()
    periods.calculate_periods(datetime.now())
    app = ManualBlendDash(periods, db_path, 8050)
    app.run()