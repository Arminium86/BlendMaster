import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.express as px
import pandas as pd
import random

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

        # Combine hover data into a categorical column for the legend
        df['Hover Info'] = df.apply(
            lambda row: (
                "Blend ID: " + row['Blend ID'] +
                "<br>Start: " + row['Start Datetime'].strftime('%Y-%m-%d %H:%M') +
                "<br>End: " + row['End Datetime'].strftime('%Y-%m-%d %H:%M') +
                "<br>Grade Fe: " + str(row['Grade Fe']) +
                "<br>Grade Si: " + str(row['Grade Si']) +
                "<br>Grade Al: " + str(row['Grade Al']) +
                "<br>Grade P: " + str(row['Grade P']) +
                "<br>Grade Mn: " + str(row['Grade Mn']) +
                "<br>Feed Tonnes: " + f"{row['Feed Tonnes']:.0f}" +
                "<br>Duration (hrs): " + f"{float(row['Duration (hrs)']):.1f}" +
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
            color="Hover Info",
            hover_data={"Hover Info": False},  # Only show Hover Info for hover
            color_discrete_map={row['Hover Info']: row['Color'] for _, row in df.iterrows()}
        )

        # Reverse the Y-axis order
        fig.update_yaxes(autorange="reversed")

        # Customize the bars
        for trace in fig.data:
            hover_info = trace.name
            row = df[df['Hover Info'] == hover_info]
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

    def update_data(self, new_data):
        """
        Update the stored blend sequence data programmatically.
        """
        self.stored_blend_sequence_table_for_gantt = new_data
        # Update the `dcc.Store` component with the new data
        self.app.layout.children[-1].data = new_data  # Update the data directly
