import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.express as px
import pandas as pd
import random

class ManualBlendDash:
    def __init__(self, stored_blend_sequence_table_for_gantt, manual_gantt_legend_and_tooltip, port=8050):
        """
        Constructor for the ManualBlendDash class.
        :param stored_blend_sequence_table_for_gantt: List of dictionaries containing blend sequence details.
        :param manual_gantt_legend_and_tooltip: List of dictionaries for legend and tooltip data.
        :param port: Port number for the Dash app.
        """
        self.stored_blend_sequence_table_for_gantt = stored_blend_sequence_table_for_gantt
        self.manual_gantt_legend_and_tooltip = manual_gantt_legend_and_tooltip
        self.port = port
        self.app = dash.Dash(__name__)
        self.colors = [
            'rgb(77, 148, 204)',  # Sharper Pale Blue
            'rgb(50, 200, 50)',   # Sharper Pale Green
            'rgb(255, 255, 51)',  # Sharper Pale Yellow
            'rgb(160, 80, 160)',  # Sharper Pale Purple
            'rgb(255, 160, 100)'  # Sharper Pale Orange
        ]
        self.color_mapping = {}
        self.layout = self.create_layout()

    def create_layout(self):
        """
        Create the layout for the Dash app.
        """
        return html.Div([
            html.H1("Gantt Chart"),
            dcc.Graph(id="gantt-chart"),
            html.Div(id="legend-placeholder"),
            html.Div(id="tooltip-placeholder")
        ])

    def create_gantt_chart(self):
        """
        Create the Gantt chart using Plotly Express.
        """
        # Convert stored_blend_sequence_table_for_gantt to a DataFrame
        df = pd.DataFrame(self.stored_blend_sequence_table_for_gantt)

        # Assign random colors for 'User Defined' blends
        def assign_color_and_border(row):
            if row['Origin'] == 'User Defined':
                if row['Blend ID'] not in self.color_mapping:
                    self.color_mapping[row['Blend ID']] = random.choice(self.colors)
                return self.color_mapping[row['Blend ID']], "black"  # Color and black border
            else:
                return "rgba(0, 0, 0, 0)", "rgba(0, 0, 0, 0)"  # Fully invisible (no color, transparent border)

        df[['Color', 'Border Color']] = df.apply(
            lambda row: pd.Series(assign_color_and_border(row)),
            axis=1
        )

        # Convert datetime strings to datetime objects
        df['Start Datetime'] = pd.to_datetime(df['Start Datetime'])
        df['End Datetime'] = pd.to_datetime(df['End Datetime'])

        # Merge manual_gantt_legend_and_tooltip for tooltips
        tooltip_df = pd.DataFrame(self.manual_gantt_legend_and_tooltip)
        df = pd.merge(df, tooltip_df, on="Blend ID", how="left")

        # Create the Gantt chart
        fig = px.timeline(
            df,
            x_start="Start Datetime",
            x_end="End Datetime",
            y="Blend ID",
            hover_data={
                "Blend ID": True,
                "Start Datetime": True,
                "End Datetime": True,
                "Tooltip Info": True,  # Show custom tooltip info
                "Color": False,  # Hide color in tooltips
                "Border Color": False  # Hide border color in tooltips
            }
        )

        # Update color and add conditional borders
        fig.update_traces(
            marker=dict(
                color=df['Color'],
                line=dict(
                    color=df['Border Color'],  # Use transparent for invisible bars
                    width=df['Border Color'].apply(lambda x: 1 if x != "rgba(0, 0, 0, 0)" else 0)  # Width only for visible borders
                )
            )
        )

        # Reverse the Y-axis order if needed
        fig.update_layout(
            title="Gantt Chart",
            xaxis_title="Datetime",
            yaxis_title="Blend IDs",
            yaxis=dict(categoryorder="category descending"),  # Reverse Y-axis
            showlegend=False
        )

        return fig


    def run(self):
        """
        Run the Dash app.
        """
        self.app.layout = self.layout

        @self.app.callback(
            Output("gantt-chart", "figure"),
            Input("gantt-chart", "id")
        )
        def update_gantt_chart(_):
            return self.create_gantt_chart()

        self.app.run_server(port=self.port)

# Example Usage
if __name__ == "__main__":
    # Example data
    stored_blend_sequence_table_for_gantt = [
        {"Blend ID": "B1", "Origin": "User Defined", "Start Datetime": "2025-01-01T08:00:00", "Duration (hrs)": "4", "End Datetime": "2025-01-01T12:00:00", "Early Start Flag": "False"},
        {"Blend ID": "B2", "Origin": "System Generated", "Start Datetime": "2025-01-01T12:00:00", "Duration (hrs)": "3", "End Datetime": "2025-01-01T15:00:00", "Early Start Flag": "True"},
        {"Blend ID": "B3", "Origin": "User Defined", "Start Datetime": "2025-01-01T15:00:00", "Duration (hrs)": "2", "End Datetime": "2025-01-01T17:00:00", "Early Start Flag": "False"}
    ]

    manual_gantt_legend_and_tooltip = [
        {"Blend ID": "B1", "Tooltip Info": "B1 details: High Priority"},
        {"Blend ID": "B3", "Tooltip Info": "B3 details: Medium Priority"}
    ]

    app = ManualBlendDash(stored_blend_sequence_table_for_gantt, manual_gantt_legend_and_tooltip)
    app.run()
