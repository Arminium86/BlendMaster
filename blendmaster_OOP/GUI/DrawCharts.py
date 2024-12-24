import pandas as pd
import sqlite3
import dash
from dash import dcc, html
import plotly.express as px

class DrawCharts:
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

# Example Usage
if __name__ == "__main__":
    db_path = r"C:\BlendMaster\blendmaster_OOP\blendmaster.db"  # SQLite database path
    chart_drawer = DrawCharts(db_path)
    chart_drawer.run_app()
