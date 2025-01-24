import dash
from dash import dcc, html, Input, Output, State, dash_table, callback_context
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
import sqlite3
import numpy as np


class DrawAMTStockpile:
    def __init__(self, db_path, port):
        self.db_path = db_path
        self.port = port
        self.app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
        self.selected_points = []
        self.sequence_counter = {}

        self.data = self.fetch_data()
        self.unique_footprints = self.data['footprint'].unique()
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
            html.H1("Draw AMT Stockpile"),
            dbc.Row([
                dbc.Col([
                    dcc.Dropdown(
                        id="footprint-dropdown",
                        options=[{"label": fp, "value": fp} for fp in self.unique_footprints],
                        placeholder="Select a Footprint",
                        style={"margin-bottom": "10px"}
                    )
                ], width=6),
            ]),
            dbc.Row([
                dcc.Graph(
                    id="scatter-plot",
                    config={"scrollZoom": True},
                    style={"height": "800px", "width": "800px"}
                )
            ])
            ,
            dbc.Row([
                dash_table.DataTable(
                    id="selected-table",
                    columns=[
                        {"name": col, "id": col} for col in
                        ['footprint', 'sequence', 'hex', 'balance', 'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn']
                    ],
                    data=[],
                    row_deletable=True,
                    editable=False,
                    style_table={'overflowX': 'auto'},
                    style_cell={'textAlign': 'center'}
                )
            ]),
            dbc.Row([
                dbc.Button("Reset Table", id="reset-button", color="danger", style={"margin-right": "10px"}),
                dbc.Button("Store Table", id="store-button", color="primary")
            ]),
            ], fluid=False)

        self.init_callbacks()
    
    def init_callbacks(self):
        @self.app.callback(
            [Output("selected-table", "data"),
            Output("scatter-plot", "figure")],
            [Input("scatter-plot", "clickData"),
            Input("footprint-dropdown", "value"),
            Input("reset-button", "n_clicks")],
            [State("selected-table", "data"),
            State("scatter-plot", "relayoutData")]
        )
        def update_table_and_plot(click_data, selected_footprint, reset_clicks, table_data, relayout_data):
            triggered = callback_context.triggered_id

            # Reset table and scatter plot
            if triggered == "reset-button":
                self.selected_points = []
                return [], self.generate_scatter_plot(selected_footprint, None)

            if not selected_footprint:
                return table_data, self.generate_scatter_plot(selected_footprint, relayout_data)

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

            return table_data, self.generate_scatter_plot(selected_footprint, relayout_data)

        @self.app.callback(
            Output("store-button", "n_clicks"),
            Input("store-button", "n_clicks"),
            State("selected-table", "data")
        )
        def store_table(n_clicks, table_data):
            if n_clicks:
                self.selected_points = table_data
                print("Stored Table:", self.selected_points)
            return n_clicks

    def generate_scatter_plot(self, selected_footprint, relayout_data):
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

        colors = filtered_data.apply(
            lambda row: "black" if row["hex"] in self.selected_points else
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
                marker=dict(size=30, symbol="hexagon", color=color),
                name={
                    "green": "Not Started",
                    "red": "Negative Balance",
                    "purple": "Started",
                    "black": "Selected",
                    "blue": "Other"
                }.get(color, "Other"),
                customdata=group[["hex", "balance", "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn"]],
                hovertemplate=(
                    "Hex: %{customdata[0]}<br>" +
                    "Latitude: %{x:.9f}<br>" +
                    "Longitude: %{y:.9f}<br>" +
                    "Balance: %{customdata[1]}<br>" +
                    "Fe Grade: %{customdata[2]}<br>" +
                    "Si Grade: %{customdata[3]}<br>" +
                    "Al Grade: %{customdata[4]}<br>" +
                    "P Grade: %{customdata[5]}<br>" +
                    "Mn Grade: %{customdata[6]}<extra></extra>"
                )
            ))

        fig.update_layout(
            title=f"Scatter Plot for Footprint: {selected_footprint}",
            xaxis_title="Longitude",
            yaxis_title="Latitude",
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

    def run(self):
        self.app.run_server(port=self.port, debug=True)


if __name__ == "__main__":
    app = DrawAMTStockpile(db_path=r"C:\BlendMaster\blendmaster_OOP\blendmaster.db", port=8050)
    app.run()