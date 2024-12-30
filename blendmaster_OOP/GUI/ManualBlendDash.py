import dash
from dash import html, dcc, callback, Input, Output, State, dash_table
import plotly.express as px
import pandas as pd
import sqlite3
from datetime import datetime

class BlendSchedulerApp:
    def __init__(self):
        self.app = dash.Dash(__name__)
        self.database = "blendmaster.db"
        self.default_start_datetime = "2023-12-01 08:00:00"
        self.default_end_datetime = "2023-12-07 18:00:00"
        self.setup_database()

        # Layout
        self.app.layout = html.Div(
            style={'display': 'flex', 'flex-direction': 'column', 'padding': '20px'},
            children=[
                # Gantt Chart
                html.Div(
                    style={'width': '100%', 'padding-bottom': '20px'},
                    children=[
                        html.H2("Gantt Chart & Blend Details"),
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
                html.Div(
                    id="input-container",
                    style={'margin-top': '20px'},
                    children=[
                        html.H3("Blend Details Input"),
                        dash_table.DataTable(
                            id="input-table",
                            columns=[
                                {"name": "Blend ID", "id": "blend_ID", "editable": False},
                                {"name": "Start Time", "id": "start_datetime", "type": "text"},
                                {"name": "End Time", "id": "end_datetime", "type": "text"},
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
                            dropdown={
                                f"source_{i}": {
                                    "options": [{"label": f"Stockpile {j}", "value": f"Stockpile {j}"} for j in range(1, 6)]
                                } for i in range(1, 6)
                            },
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
            CREATE TABLE IF NOT EXISTS schedule (
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
        conn.commit()
        conn.close()

    def fetch_data(self):
        """Fetch data for the Gantt chart."""
        conn = sqlite3.connect(self.database)
        df = pd.read_sql("SELECT * FROM schedule", conn)
        conn.close()

        if df.empty:
            # Return default data if the table is empty
            df = pd.DataFrame({
                "blend_ID": list(range(1, 8)),
                "start_datetime": [self.default_start_datetime] * 7,
                "end_datetime": [self.default_end_datetime] * 7,
                "source_1": [None] * 7,
                "source_2": [None] * 7,
                "source_3": [None] * 7,
                "source_4": [None] * 7,
                "source_5": [None] * 7,
                "ratio_1": [None] * 7,
                "ratio_2": [None] * 7,
                "ratio_3": [None] * 7,
                "ratio_4": [None] * 7,
                "ratio_5": [None] * 7,
                "crusher_rate": [None] * 7,
            })
        return df

    def create_gantt_figure(self, data):
        """Generate the Gantt chart figure."""
        data["lane"] = data["blend_ID"]  # Use blend_ID as lane
        data["Legend"] = "Blend"  # Static legend
        fig = px.timeline(
            data,
            x_start="start_datetime",
            x_end="end_datetime",
            y="lane",
            color="Legend",
            hover_name="blend_ID",
            title="Gantt Chart",
        )
        fig.update_traces(marker=dict(line=dict(width=1, color="black")))
        fig.update_layout(
            yaxis=dict(tickvals=data["lane"], ticktext=data["blend_ID"]),
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
                for row in updated_table_data:
                    if row["blend_ID"] == clicked_blend_id:
                        row["selected"] = True  # Optionally highlight row
                figure = self.create_gantt_figure(data)
                return figure, updated_table_data

            elif triggered_id == "submit-button" and n_clicks:
                # Save updated data
                conn = sqlite3.connect(self.database)
                pd.DataFrame(table_data).to_sql("schedule", conn, if_exists="replace", index=False)
                conn.close()

                # Update Gantt chart
                data = self.fetch_data()
                figure = self.create_gantt_figure(data)
                return figure, table_data

            # Initial load or no updates
            figure = self.create_gantt_figure(data)
            return figure, data.to_dict("records")

    def run(self):
        """Run the Dash app."""
        self.app.run_server(debug=True)


# Run the app
if __name__ == "__main__":
    app = BlendSchedulerApp()
    app.run()
