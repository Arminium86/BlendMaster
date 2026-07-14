import sqlite3
import pandas as pd
import random
import requests, time, traceback
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
from database.DatabaseContext import get_database_path

class DrawStockProfiles:
    def __init__(self, db_path, port):
        """
        Initialize the DrawStockProfiles instance.
        :param db_path: Path to the SQLite database.
        :param port: Port to run the Dash app.
        """
        self.db_path = db_path
        self.port = port
        self.depletion_palette = [
            "#7FB3D5",
            "#82C4A2",
            "#F3B56B",
            "#D6B56D",
            "#B59EDB",
            "#E68A92",
            "#79B8A9",
            "#A5B4FC",
            "#F2C94C",
            "#8ECAD1",
        ]
        self.destination_pastel_palette = [
            ("#B7D8F3", "#F6C28B"),
            ("#C8E6C9", "#F4B6C2"),
            ("#C9B8EA", "#F7E7A3"),
            ("#BFD8D2", "#F3C7A6"),
            ("#A5B4FC", "#D9C28F"),
        ]
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

    def fetch_crusher_data(self):
        """
        Fetch one row per optimised steady state for the actual crusher feed chart.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            query = """
                SELECT
                    steady_state_number,
                    start_datetime,
                    end_datetime,
                    blend_ID,
                    steady_state_duration,
                    crusher_actual_tonnes,
                    crusher_rate_input,
                    crusher_rate_output,
                    actual_direct_tip_ratio,
                    crusher_actual_grade_fe,
                    crusher_actual_grade_si,
                    crusher_actual_grade_al,
                    crusher_actual_grade_p,
                    crusher_actual_grade_mn,
                    crusher_grade_target_min_fe,
                    crusher_grade_target_max_fe,
                    crusher_grade_target_min_si,
                    crusher_grade_target_max_si,
                    crusher_grade_target_min_al,
                    crusher_grade_target_max_al,
                    crusher_grade_target_min_p,
                    crusher_grade_target_max_p,
                    crusher_grade_target_min_mn,
                    crusher_grade_target_max_mn
                FROM optimised_blend_report
            """
            data = pd.read_sql(query, conn)
            conn.close()
        except Exception as e:
            print(f"Error fetching crusher profile data: {e}")
            return pd.DataFrame()

        if data.empty:
            return data

        data = data.drop_duplicates(
            subset=["steady_state_number", "start_datetime", "end_datetime", "blend_ID"]
        ).copy()
        data["start_datetime"] = pd.to_datetime(data["start_datetime"], errors="coerce")
        data["end_datetime"] = pd.to_datetime(data["end_datetime"], errors="coerce")
        numeric_columns = [
            "crusher_actual_tonnes", "crusher_rate_input", "crusher_rate_output",
            "actual_direct_tip_ratio", "crusher_actual_grade_fe", "crusher_actual_grade_si",
            "crusher_actual_grade_al", "crusher_actual_grade_p", "crusher_actual_grade_mn",
            "crusher_grade_target_min_fe", "crusher_grade_target_max_fe",
            "crusher_grade_target_min_si", "crusher_grade_target_max_si",
            "crusher_grade_target_min_al", "crusher_grade_target_max_al",
            "crusher_grade_target_min_p", "crusher_grade_target_max_p",
            "crusher_grade_target_min_mn", "crusher_grade_target_max_mn",
        ]
        for column in numeric_columns:
            data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
        data["direct_tip_tonnes"] = data["crusher_actual_tonnes"] * data["actual_direct_tip_ratio"]
        data["stockpile_feed_tonnes"] = (
            data["crusher_actual_tonnes"] - data["direct_tip_tonnes"]
        ).clip(lower=0)
        data["direct_tip_rate_output"] = data["crusher_rate_output"] * data["actual_direct_tip_ratio"]
        data["stockpile_feed_rate_output"] = (
            data["crusher_rate_output"] - data["direct_tip_rate_output"]
        ).clip(lower=0)
        data["end_datetime_display"] = data["end_datetime"].dt.strftime("%Y-%m-%d %H:%M")
        return data.sort_values("start_datetime")

    def fetch_product_build_data(self):
        try:
            conn = sqlite3.connect(self.db_path)
            data = pd.read_sql("SELECT * FROM product_build_report", conn)
            conn.close()
        except Exception as e:
            print(f"Error fetching product build profile data: {e}")
            return pd.DataFrame()

        if data.empty:
            return data

        data = data.copy()
        for column in ["steady_state_start_datetime", "steady_state_end_datetime"]:
            if column in data.columns:
                data[column] = pd.to_datetime(data[column], errors="coerce")
        numeric_columns = [
            "target_tonnes", "build_opening_tonnes", "build_added_tonnes",
            "build_closing_tonnes", "build_grade_fe", "build_grade_si",
            "build_grade_al", "build_grade_p", "build_grade_mn",
            "target_fe_min", "target_fe_max", "target_si_min", "target_si_max",
            "target_al_min", "target_al_max", "target_p_min", "target_p_max",
            "target_mn_min", "target_mn_max", "steady_state_duration",
        ]
        for column in numeric_columns:
            if column not in data.columns:
                data[column] = 0
            data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
        return data.sort_values(["product_build_id", "steady_state_start_datetime"])

    @staticmethod
    def common_time_range(*dataframes):
        times = []
        candidate_columns = [
            "time",
            "start_datetime",
            "end_datetime",
            "steady_state_start_datetime",
            "steady_state_end_datetime",
        ]
        for data in dataframes:
            if data is None or data.empty:
                continue
            for column in candidate_columns:
                if column not in data.columns:
                    continue
                series = pd.to_datetime(data[column], errors="coerce").dropna()
                if not series.empty:
                    times.extend(series.tolist())

        if not times:
            return None
        return [min(times), max(times)]

    @staticmethod
    def readable_hoverlabel():
        return dict(
            bgcolor="rgba(15, 23, 42, 0.96)",
            bordercolor="#94a3b8",
            font=dict(color="#f8fafc", family="Segoe UI", size=12),
        )

    @staticmethod
    def extend_completed_product_build_profile(profile, target_tonnes, x_axis_range=None):
        if profile is None or profile.empty or not x_axis_range:
            return profile
        try:
            target_tonnes = float(target_tonnes or 0)
        except (TypeError, ValueError):
            target_tonnes = 0
        if target_tonnes <= 0:
            return profile

        axis_end = pd.to_datetime(x_axis_range[1], errors="coerce")
        if pd.isna(axis_end):
            return profile

        profile = profile.copy().sort_values("time")
        last_row = profile.iloc[-1].copy()
        last_time = pd.to_datetime(last_row.get("time"), errors="coerce")
        if pd.isna(last_time) or last_time >= axis_end:
            return profile

        try:
            last_tonnes = float(last_row.get("tonnes") or 0)
        except (TypeError, ValueError):
            last_tonnes = 0
        if last_tonnes < target_tonnes - 0.1:
            return profile

        last_row["time"] = axis_end
        last_row["tonnes"] = target_tonnes
        last_row["point_type"] = "Complete"
        last_row["duration"] = 0.0
        last_row["added_tonnes"] = 0.0
        return pd.concat([profile, pd.DataFrame([last_row])], ignore_index=True)

    def create_charts(self, data, crusher_data=None, product_build_data=None):
        """
        Create individual charts for each unique stockpile with random colors.
        :param data: DataFrame containing the data for the charts.
        :return: List of Dash Graph components.
        """
        charts = []
        x_axis_range = self.common_time_range(data, crusher_data, product_build_data)
        crusher_chart = self.create_actual_crusher_feed_chart(crusher_data, x_axis_range)
        if crusher_chart is not None:
            charts.append(crusher_chart)

        charts.extend(self.create_product_build_charts(product_build_data, x_axis_range))

        if data is None or data.empty:
            print("No data available to create charts.")
            return charts or [html.Div("No data available.")]

        data = data.copy()
        data['Balance'] = pd.to_numeric(data['balance'], errors='coerce').round(1)
        for column in [
            "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile",
            "movement_tonnes"
        ]:
            if column not in data.columns:
                data[column] = 0
            data[column] = pd.to_numeric(data[column], errors='coerce').fillna(0)
        if "movement_destination" not in data.columns:
            data["movement_destination"] = ""
        data['Steady State Number'] = data['steady_state_number'].fillna('None')
        data['Agent'] = data['agent'].fillna('None')
        data['Source or Destination'] = data['source_or_destination'].fillna('None')
        data['profile_type'] = data.apply(self.profile_type_for_row, axis=1)

        # Format grades: round to 2 decimals and add % suffix
        for col in data.columns:
            if col.startswith('grade_'):
                alias = col.replace('grade_', 'Grade ').capitalize()
                data[alias] = pd.to_numeric(data[col], errors='coerce').round(2).astype(str) + '%'

        unique_sources = sorted(data['stockpile'].dropna().unique())
        source_colors = {
            stockpile: self.depletion_palette[index % len(self.depletion_palette)]
            for index, stockpile in enumerate(unique_sources)
        }

        for stockpile in unique_sources:
            stockpile_full_data = data[data['stockpile'] == stockpile].copy()
            if stockpile_full_data.empty:
                continue
            movement_summary = self.grade_block_movement_summary(stockpile_full_data)
            stockpile_data = stockpile_full_data.copy()
            stockpile_data = self.prepare_profile_for_chart(stockpile_data)
            if stockpile_data.empty:
                continue
            stockpile_color = source_colors[stockpile]
            profile_type = stockpile_data['profile_type'].dropna().iloc[0]
            profile_badge_color = "#2563eb" if profile_type == "Stockpile" else "#7c3aed"

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
            )
            fig.update_layout(
                height=260,
                margin=dict(l=54, r=26, t=14, b=48),
                paper_bgcolor="#ffffff",
                plot_bgcolor="#eef4fb",
                xaxis_title='Time',
                yaxis_title='Balance (WMT)',
                showlegend=False,
                font=dict(
                    family="Segoe UI",
                    size=12,
                    color="#1f2937"
                ),
                hoverlabel=self.readable_hoverlabel(),
                hovermode="x unified",
            )
            fig.update_traces(
                line=dict(color=stockpile_color, width=2.4),
                fillcolor=self.hex_to_rgba(stockpile_color, 0.42),
                hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Balance: %{y:,.1f} WMT<extra></extra>",
            )
            fig.update_xaxes(
                showgrid=True,
                gridcolor="rgba(148, 163, 184, 0.30)",
                zeroline=False,
                range=x_axis_range,
            )
            fig.update_yaxes(
                showgrid=True,
                gridcolor="rgba(148, 163, 184, 0.35)",
                zeroline=False,
            )

            header_children = [
                html.Span(
                    profile_type,
                    style={
                        "backgroundColor": profile_badge_color,
                        "color": "#ffffff",
                        "fontSize": "12px",
                        "fontWeight": "700",
                        "padding": "4px 10px",
                        "borderRadius": "999px",
                        "marginRight": "10px",
                    },
                ),
                html.Span(
                    stockpile,
                    style={
                        "fontSize": "16px",
                        "fontWeight": "650",
                        "color": "#1f2937",
                    },
                ),
            ]

            card_children = [
                html.Div(
                    children=header_children,
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "padding": "12px 16px 4px 16px",
                    },
                )
            ]
            if profile_type == "Grade Block":
                card_children.append(self.grade_block_summary_row(movement_summary))
                destination_chart = self.create_grade_block_destination_chart(
                    stockpile_full_data,
                    stockpile,
                    x_axis_range,
                )
                if destination_chart is not None:
                    card_children.append(destination_chart)
                else:
                    card_children.append(html.Div(
                        "No destination movements available for this grade block.",
                        style={
                            "padding": "18px 16px 22px 16px",
                            "color": "#64748b",
                            "fontSize": "13px",
                        },
                    ))
            else:
                card_children.append(
                    dcc.Graph(
                        figure=fig,
                        config={"displayModeBar": False, "responsive": True},
                        style={"height": "270px"},
                    )
                )

            charts.append(html.Div(
                children=card_children,
                style={
                    "backgroundColor": "#ffffff",
                    "border": "1px solid #dbe4ee",
                    "borderRadius": "8px",
                    "boxShadow": "0 8px 22px rgba(15, 23, 42, 0.06)",
                    "margin": "0 0 16px 0",
                    "overflow": "hidden",
                },
            ))

        return charts

    def create_product_build_charts(self, product_build_data, x_axis_range=None):
        if product_build_data is None or product_build_data.empty:
            return []

        data = product_build_data.copy()
        required_columns = {
            "product_build_id", "product_build_name", "brand", "target_tonnes",
            "build_opening_tonnes", "build_added_tonnes", "build_closing_tonnes",
            "steady_state_number", "steady_state_start_datetime", "steady_state_end_datetime",
        }
        if not required_columns.issubset(data.columns):
            return []

        event_group_columns = [
            "product_build_id", "product_build_name", "brand", "steady_state_number",
            "blend_ID", "blend_option", "steady_state_start_datetime", "steady_state_end_datetime",
        ]
        for column in event_group_columns:
            if column not in data.columns:
                data[column] = ""
        aggregation = {
            "target_tonnes": "first",
            "build_opening_tonnes": "first",
            "build_added_tonnes": "first",
            "build_closing_tonnes": "first",
            "steady_state_duration": "first",
            "build_complete": "first",
            "build_on_spec": "first",
        }
        for grade in ["fe", "si", "al", "p", "mn"]:
            aggregation[f"build_grade_{grade}"] = "first"
            aggregation[f"target_{grade}_min"] = "first"
            aggregation[f"target_{grade}_max"] = "first"
        for column in aggregation:
            if column not in data.columns:
                data[column] = 0

        events = (
            data.groupby(event_group_columns, dropna=False, as_index=False)
            .agg(aggregation)
            .sort_values(["product_build_id", "steady_state_start_datetime", "steady_state_end_datetime"])
        )
        events = events.dropna(subset=["steady_state_start_datetime", "steady_state_end_datetime"])
        if events.empty:
            return []

        cards = []
        colors = ["#82C4A2", "#7FB3D5", "#F3B56B", "#B59EDB", "#E68A92", "#8ECAD1"]
        for build_index, (build_id, build_events) in enumerate(events.groupby("product_build_id", sort=False)):
            build_events = build_events.sort_values("steady_state_start_datetime")
            if build_events.empty:
                continue

            build_name = str(build_events["product_build_name"].dropna().iloc[0] or f"Build {build_id}")
            brand = str(build_events["brand"].dropna().iloc[0] or "")
            target_tonnes = float(build_events["target_tonnes"].max() or 0)
            current_tonnes = float(build_events["build_closing_tonnes"].max() or 0)
            remaining_tonnes = max(target_tonnes - current_tonnes, 0)
            build_color = colors[build_index % len(colors)]

            profile_rows = []
            previous_grades = {grade: 0.0 for grade in ["fe", "si", "al", "p", "mn"]}
            for _, row in build_events.iterrows():
                common = {
                    "steady_state_number": row.get("steady_state_number"),
                    "blend_ID": row.get("blend_ID"),
                    "blend_option": row.get("blend_option"),
                    "duration": float(row.get("steady_state_duration") or 0),
                    "added_tonnes": float(row.get("build_added_tonnes") or 0),
                    "target_tonnes": target_tonnes,
                    "build_complete": row.get("build_complete"),
                    "build_on_spec": row.get("build_on_spec"),
                }
                closing_grades = {}
                for grade in ["fe", "si", "al", "p", "mn"]:
                    closing_grades[grade] = float(row.get(f"build_grade_{grade}") or 0)
                    common[f"target_{grade}_min"] = float(row.get(f"target_{grade}_min") or 0)
                    common[f"target_{grade}_max"] = float(row.get(f"target_{grade}_max") or 0)

                opening_common = common.copy()
                closing_common = common.copy()
                for grade in ["fe", "si", "al", "p", "mn"]:
                    opening_common[f"grade_{grade}"] = previous_grades[grade]
                    closing_common[f"grade_{grade}"] = closing_grades[grade]

                profile_rows.append({
                    **opening_common,
                    "time": row["steady_state_start_datetime"],
                    "tonnes": float(row.get("build_opening_tonnes") or 0),
                    "point_type": "Opening",
                })
                profile_rows.append({
                    **closing_common,
                    "time": row["steady_state_end_datetime"],
                    "tonnes": float(row.get("build_closing_tonnes") or 0),
                    "point_type": "Closing",
                })
                previous_grades = closing_grades

            profile = pd.DataFrame(profile_rows).dropna(subset=["time"]).sort_values("time")
            profile = self.extend_completed_product_build_profile(profile, target_tonnes, x_axis_range)
            if profile.empty:
                continue

            custom_columns = [
                "point_type", "steady_state_number", "blend_ID", "duration", "added_tonnes",
                "target_tonnes",
                "grade_fe", "target_fe_min", "target_fe_max",
                "grade_si", "target_si_min", "target_si_max",
                "grade_al", "target_al_min", "target_al_max",
                "grade_p", "target_p_min", "target_p_max",
                "grade_mn", "target_mn_min", "target_mn_max",
            ]
            customdata = profile[custom_columns].to_numpy(dtype=object)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=profile["time"],
                y=profile["tonnes"],
                name="Actual Build Tonnes",
                mode="lines",
                line=dict(color=build_color, width=2.4),
                fill="tozeroy",
                fillcolor=self.hex_to_rgba(build_color, 0.35),
                customdata=customdata,
                hovertemplate=(
                    "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                    "%{customdata[0]} tonnes: %{y:,.1f} WMT<br>"
                    "Target tonnes: %{customdata[5]:,.1f} WMT<br>"
                    "Steady State: %{customdata[1]}<br>"
                    "Blend ID: %{customdata[2]}<br>"
                    "Duration: %{customdata[3]:.2f} hrs<br>"
                    "Added in state: %{customdata[4]:,.1f} WMT<br>"
                    "Fe: %{customdata[6]:.2f}% (target %{customdata[7]:.2f}-%{customdata[8]:.2f}%)<br>"
                    "Si: %{customdata[9]:.2f}% (target %{customdata[10]:.2f}-%{customdata[11]:.2f}%)<br>"
                    "Al: %{customdata[12]:.2f}% (target %{customdata[13]:.2f}-%{customdata[14]:.2f}%)<br>"
                    "P: %{customdata[15]:.2f}% (target %{customdata[16]:.2f}-%{customdata[17]:.2f}%)<br>"
                    "Mn: %{customdata[18]:.2f}% (target %{customdata[19]:.2f}-%{customdata[20]:.2f}%)"
                    "<extra></extra>"
                ),
            ))
            target_x_range = x_axis_range or [profile["time"].min(), profile["time"].max()]
            fig.add_trace(go.Scatter(
                x=target_x_range,
                y=[target_tonnes, target_tonnes],
                name="Target Tonnes",
                mode="lines",
                line=dict(color="#dc2626", width=2.5),
                hovertemplate="Target tonnes: %{y:,.1f} WMT<extra></extra>",
            ))
            fig.update_layout(
                height=260,
                margin=dict(l=58, r=26, t=10, b=48),
                paper_bgcolor="#ffffff",
                plot_bgcolor="#eef4fb",
                xaxis_title="Time",
                yaxis_title="Build Tonnes (WMT)",
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1,
                    bgcolor="rgba(255,255,255,0.72)",
                ),
                font=dict(family="Segoe UI", size=12, color="#1f2937"),
                hoverlabel=self.readable_hoverlabel(),
                hovermode="x unified",
            )
            fig.update_xaxes(
                showgrid=True,
                gridcolor="rgba(148, 163, 184, 0.30)",
                zeroline=False,
                range=x_axis_range,
            )
            fig.update_yaxes(showgrid=True, gridcolor="rgba(148, 163, 184, 0.35)", zeroline=False)

            cards.append(html.Div(
                children=[
                    html.Div(
                        children=[
                            html.Span(
                                "Product Build",
                                style={
                                    "backgroundColor": "#0369a1",
                                    "color": "#ffffff",
                                    "fontSize": "12px",
                                    "fontWeight": "700",
                                    "padding": "4px 10px",
                                    "borderRadius": "999px",
                                    "marginRight": "10px",
                                },
                            ),
                            html.Span(
                                f"{build_name}" + (f" ({brand})" if brand else ""),
                                style={
                                    "fontSize": "16px",
                                    "fontWeight": "650",
                                    "color": "#1f2937",
                                },
                            ),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "padding": "12px 16px 4px 16px",
                        },
                    ),
                    html.Div(
                        children=[
                            self.summary_chip("Target", target_tonnes, "#fee2e2", "#991b1b"),
                            self.summary_chip("Actual", current_tonnes, "#dbeafe", "#1e40af"),
                            self.summary_chip("Remaining", remaining_tonnes, "#f1f5f9", "#334155"),
                        ],
                        style={
                            "display": "flex",
                            "gap": "8px",
                            "flexWrap": "wrap",
                            "padding": "2px 16px 8px 16px",
                        },
                    ),
                    dcc.Graph(
                        figure=fig,
                        config={"displayModeBar": False, "responsive": True},
                        style={"height": "270px"},
                    ),
                ],
                style={
                    "backgroundColor": "#ffffff",
                    "border": "1px solid #dbe4ee",
                    "borderRadius": "8px",
                    "boxShadow": "0 8px 22px rgba(15, 23, 42, 0.06)",
                    "margin": "0 0 16px 0",
                    "overflow": "hidden",
                },
            ))

        return cards

    def create_actual_crusher_feed_chart(self, crusher_data, x_axis_range=None):
        if crusher_data is None or crusher_data.empty:
            return None

        data = crusher_data.copy()
        data = data.dropna(subset=["start_datetime"])
        if data.empty:
            return None

        crusher_hover_columns = [
            "end_datetime_display",
            "steady_state_duration",
            None,
            "crusher_actual_tonnes",
            "crusher_rate_output",
            "crusher_actual_grade_fe",
            "crusher_grade_target_min_fe",
            "crusher_grade_target_max_fe",
            "crusher_actual_grade_si",
            "crusher_grade_target_min_si",
            "crusher_grade_target_max_si",
            "crusher_actual_grade_al",
            "crusher_grade_target_min_al",
            "crusher_grade_target_max_al",
            "crusher_actual_grade_p",
            "crusher_grade_target_min_p",
            "crusher_grade_target_max_p",
            "crusher_actual_grade_mn",
            "crusher_grade_target_min_mn",
            "crusher_grade_target_max_mn",
        ]

        stockpile_series = self.expand_interval_series(
            data,
            "stockpile_feed_rate_output",
            [
                crusher_hover_columns[0],
                crusher_hover_columns[1],
                "stockpile_feed_tonnes",
                *crusher_hover_columns[3:],
            ],
        )
        direct_tip_series = self.expand_interval_series(
            data,
            "direct_tip_rate_output",
            [
                crusher_hover_columns[0],
                crusher_hover_columns[1],
                "direct_tip_tonnes",
                *crusher_hover_columns[3:],
            ],
        )
        target_series = self.expand_interval_series(
            data,
            "crusher_rate_input",
            [
                crusher_hover_columns[0],
                crusher_hover_columns[1],
                "crusher_actual_tonnes",
                *crusher_hover_columns[4:],
            ],
        )

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=stockpile_series["x"],
            y=stockpile_series["y"],
            name="Stockpile Feed",
            mode="lines",
            stackgroup="crusher_feed",
            line=dict(color="#5DAF83", width=1.4, shape="hv"),
            fillcolor="rgba(130, 196, 162, 0.58)",
            customdata=stockpile_series["customdata"],
            hovertemplate=(
                "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                "End: %{customdata[0]}<br>"
                "Duration: %{customdata[1]:.2f} hrs<br>"
                "Stockpile Feed Rate: %{y:,.1f} t/h<br>"
                "Stockpile Feed Tonnes: %{customdata[2]:,.1f} WMT<br>"
                "Total Crusher Tonnes: %{customdata[3]:,.1f} WMT<br>"
                "Actual Crusher Rate: %{customdata[4]:,.1f} t/h<br>"
                "Fe: %{customdata[5]:.2f}% (target %{customdata[6]:.2f}-%{customdata[7]:.2f}%)<br>"
                "Si: %{customdata[8]:.2f}% (target %{customdata[9]:.2f}-%{customdata[10]:.2f}%)<br>"
                "Al: %{customdata[11]:.2f}% (target %{customdata[12]:.2f}-%{customdata[13]:.2f}%)<br>"
                "P: %{customdata[14]:.2f}% (target %{customdata[15]:.2f}-%{customdata[16]:.2f}%)<br>"
                "Mn: %{customdata[17]:.2f}% (target %{customdata[18]:.2f}-%{customdata[19]:.2f}%)"
                "<extra></extra>"
            ),
        ))
        fig.add_trace(go.Scatter(
            x=direct_tip_series["x"],
            y=direct_tip_series["y"],
            name="Direct Tip",
            mode="lines",
            stackgroup="crusher_feed",
            line=dict(color="#D9933F", width=1.4, shape="hv"),
            fillcolor="rgba(243, 181, 107, 0.68)",
            customdata=direct_tip_series["customdata"],
            hovertemplate=(
                "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                "End: %{customdata[0]}<br>"
                "Duration: %{customdata[1]:.2f} hrs<br>"
                "Direct Tip Rate: %{y:,.1f} t/h<br>"
                "Direct Tip Tonnes: %{customdata[2]:,.1f} WMT<br>"
                "Total Crusher Tonnes: %{customdata[3]:,.1f} WMT<br>"
                "Actual Crusher Rate: %{customdata[4]:,.1f} t/h<br>"
                "Fe: %{customdata[5]:.2f}% (target %{customdata[6]:.2f}-%{customdata[7]:.2f}%)<br>"
                "Si: %{customdata[8]:.2f}% (target %{customdata[9]:.2f}-%{customdata[10]:.2f}%)<br>"
                "Al: %{customdata[11]:.2f}% (target %{customdata[12]:.2f}-%{customdata[13]:.2f}%)<br>"
                "P: %{customdata[14]:.2f}% (target %{customdata[15]:.2f}-%{customdata[16]:.2f}%)<br>"
                "Mn: %{customdata[17]:.2f}% (target %{customdata[18]:.2f}-%{customdata[19]:.2f}%)"
                "<extra></extra>"
            ),
        ))
        fig.add_trace(go.Scatter(
            x=target_series["x"],
            y=target_series["y"],
            mode="lines",
            name="Crusher Rate Input",
            line=dict(color="#dc2626", width=3, shape="hv"),
            customdata=target_series["customdata"],
            hovertemplate=(
                "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                "End: %{customdata[0]}<br>"
                "Duration: %{customdata[1]:.2f} hrs<br>"
                "Crusher Rate Input: %{y:,.1f} t/h<br>"
                "Total Crusher Tonnes: %{customdata[2]:,.1f} WMT<br>"
                "Actual Crusher Rate: %{customdata[3]:,.1f} t/h<br>"
                "Fe: %{customdata[4]:.2f}% (target %{customdata[5]:.2f}-%{customdata[6]:.2f}%)<br>"
                "Si: %{customdata[7]:.2f}% (target %{customdata[8]:.2f}-%{customdata[9]:.2f}%)<br>"
                "Al: %{customdata[10]:.2f}% (target %{customdata[11]:.2f}-%{customdata[12]:.2f}%)<br>"
                "P: %{customdata[13]:.2f}% (target %{customdata[14]:.2f}-%{customdata[15]:.2f}%)<br>"
                "Mn: %{customdata[16]:.2f}% (target %{customdata[17]:.2f}-%{customdata[18]:.2f}%)"
                "<extra></extra>"
            ),
        ))
        fig.update_layout(
            height=255,
            margin=dict(l=58, r=24, t=12, b=50),
            paper_bgcolor="#ffffff",
            plot_bgcolor="#eef4fb",
            xaxis_title="Time",
            yaxis_title="Crusher Rate Output (t/h)",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
            ),
            font=dict(family="Segoe UI", size=12, color="#1f2937"),
            hoverlabel=self.readable_hoverlabel(),
            hovermode="x unified",
        )
        fig.update_xaxes(
            showgrid=True,
            gridcolor="rgba(148, 163, 184, 0.30)",
            zeroline=False,
            range=x_axis_range,
        )
        fig.update_yaxes(showgrid=True, gridcolor="rgba(148, 163, 184, 0.35)", zeroline=False)

        return html.Div(
            children=[
                html.Div(
                    children=[
                        html.Span(
                            "Crusher",
                            style={
                                "backgroundColor": "#0f766e",
                                "color": "#ffffff",
                                "fontSize": "12px",
                                "fontWeight": "700",
                                "padding": "4px 10px",
                                "borderRadius": "999px",
                                "marginRight": "10px",
                            },
                        ),
                        html.Span(
                            "Actual Crusher Feed",
                            style={
                                "fontSize": "16px",
                                "fontWeight": "650",
                                "color": "#1f2937",
                            },
                        ),
                    ],
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "padding": "12px 16px 4px 16px",
                    },
                ),
                dcc.Graph(
                    figure=fig,
                    config={"displayModeBar": False, "responsive": True},
                    style={"height": "260px"},
                ),
            ],
            style={
                "backgroundColor": "#ffffff",
                "border": "1px solid #dbe4ee",
                "borderRadius": "8px",
                "boxShadow": "0 8px 22px rgba(15, 23, 42, 0.06)",
                "margin": "0 0 16px 0",
                "overflow": "hidden",
            },
        )

    def create_grade_block_destination_chart(self, stockpile_data, source_name, x_axis_range=None):
        destination_series = self.grade_block_destination_series(stockpile_data)
        if destination_series.empty:
            return None

        stockpile_color, crusher_color = self.destination_color_pair(source_name)
        custom_columns = [
            "event_stockpile_tonnes",
            "event_crusher_tonnes",
            "stockpile_cumulative_tonnes",
            "crusher_cumulative_tonnes",
            "total_destination_tonnes",
        ]
        customdata = destination_series[custom_columns].to_numpy(dtype=object)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=destination_series["time"],
            y=destination_series["stockpile_cumulative_tonnes"],
            name="Sent to Stockpile",
            mode="lines",
            stackgroup="destination_split",
            line=dict(color=stockpile_color, width=1.5, shape="hv"),
            fillcolor=self.hex_to_rgba(stockpile_color, 0.66),
            customdata=customdata,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                "Sent to Stockpile at this time: %{customdata[0]:,.1f} WMT<br>"
                "Cumulative Sent to Stockpile: %{customdata[2]:,.1f} WMT<br>"
                "Total Sent: %{customdata[4]:,.1f} WMT<extra></extra>"
            ),
        ))
        fig.add_trace(go.Scatter(
            x=destination_series["time"],
            y=destination_series["crusher_cumulative_tonnes"],
            name="Sent to Crusher",
            mode="lines",
            stackgroup="destination_split",
            line=dict(color=crusher_color, width=1.5, shape="hv"),
            fillcolor=self.hex_to_rgba(crusher_color, 0.68),
            customdata=customdata,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                "Sent to Crusher at this time: %{customdata[1]:,.1f} WMT<br>"
                "Cumulative Sent to Crusher: %{customdata[3]:,.1f} WMT<br>"
                "Total Sent: %{customdata[4]:,.1f} WMT<extra></extra>"
            ),
        ))
        fig.update_layout(
            height=260,
            margin=dict(l=60, r=26, t=10, b=48),
            paper_bgcolor="#ffffff",
            plot_bgcolor="#f4f8fc",
            xaxis_title="Time",
            yaxis_title="Cumulative Destination Tonnes (WMT)",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                bgcolor="rgba(255,255,255,0.72)",
            ),
            font=dict(family="Segoe UI", size=11, color="#1f2937"),
            hoverlabel=self.readable_hoverlabel(),
            hovermode="x unified",
        )
        fig.update_xaxes(
            showgrid=True,
            gridcolor="rgba(148, 163, 184, 0.28)",
            zeroline=False,
            range=x_axis_range,
        )
        fig.update_yaxes(showgrid=True, gridcolor="rgba(148, 163, 184, 0.32)", zeroline=False)

        return html.Div(
            children=[
                html.Div(
                    "Destination Split",
                    style={
                        "fontSize": "12px",
                        "fontWeight": "700",
                        "color": "#475569",
                        "padding": "0 0 2px 2px",
                    },
                ),
                dcc.Graph(
                    figure=fig,
                    config={"displayModeBar": False, "responsive": True},
                    style={"height": "270px"},
                ),
            ],
            style={"padding": "0 16px 14px 16px"},
        )

    @staticmethod
    def grade_block_destination_series(stockpile_data):
        if stockpile_data is None or stockpile_data.empty:
            return pd.DataFrame()

        data = stockpile_data.copy()
        data["time"] = pd.to_datetime(data.get("time"), errors="coerce")
        for column in ["tonnes_to_stockpile", "tonnes_to_crusher"]:
            if column not in data:
                data[column] = 0
            data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
        data = data.dropna(subset=["time"])
        if data.empty:
            return pd.DataFrame()
        for column in ["steady_state_start_datetime", "steady_state_end_datetime"]:
            if column not in data:
                data[column] = pd.NaT
            data[column] = pd.to_datetime(data[column], errors="coerce")

        movement_destination = data.get(
            "movement_destination",
            pd.Series("", index=data.index),
        ).fillna("").astype(str)
        source_or_destination = data.get(
            "source_or_destination",
            pd.Series("", index=data.index),
        ).fillna("").astype(str)
        data["_is_arrival"] = (
            pd.to_numeric(data.get("arrival_tonnes", 0), errors="coerce").fillna(0) > 0
        ) | movement_destination.str.contains("arrived", case=False, na=False) | source_or_destination.str.contains(
            "arrived", case=False, na=False
        )
        data["_is_movement"] = (
            (data["tonnes_to_stockpile"] > 0) | (data["tonnes_to_crusher"] > 0)
        )

        movement_records = []
        for _, row in data.sort_values("time").iterrows():
            if not bool(row.get("_is_movement")):
                continue

            movement_time = row["time"]
            movement_start = row.get("steady_state_start_datetime")
            movement_end = row.get("steady_state_end_datetime")
            if pd.isna(movement_start):
                movement_start = movement_time
            if pd.isna(movement_end) or movement_end <= movement_start:
                movement_end = movement_start + pd.Timedelta(minutes=1)
            movement_records.append({
                "time": movement_start,
                "end_time": movement_end,
                "tonnes_to_stockpile": row["tonnes_to_stockpile"],
                "tonnes_to_crusher": row["tonnes_to_crusher"],
            })

        if not movement_records:
            return pd.DataFrame()

        movements = (
            pd.DataFrame(movement_records)
            .groupby("time", as_index=False)
            .agg({
                "end_time": "max",
                "tonnes_to_stockpile": "sum",
                "tonnes_to_crusher": "sum",
            })
            .sort_values("time")
        )

        start_time = movements["time"].min()
        end_time = movements["end_time"].max()
        if pd.isna(start_time):
            start_time = movements["time"].min()
        if pd.isna(end_time) or end_time <= start_time:
            end_time = start_time + pd.Timedelta(minutes=1)

        rows = [{
            "time": start_time,
            "event_stockpile_tonnes": 0.0,
            "event_crusher_tonnes": 0.0,
            "stockpile_cumulative_tonnes": 0.0,
            "crusher_cumulative_tonnes": 0.0,
            "total_destination_tonnes": 0.0,
        }]

        stockpile_cumulative = 0.0
        crusher_cumulative = 0.0
        for _, row in movements.iterrows():
            event_stockpile = float(row["tonnes_to_stockpile"] or 0)
            event_crusher = float(row["tonnes_to_crusher"] or 0)
            stockpile_cumulative += event_stockpile
            crusher_cumulative += event_crusher
            rows.append({
                "time": row["time"],
                "event_stockpile_tonnes": event_stockpile,
                "event_crusher_tonnes": event_crusher,
                "stockpile_cumulative_tonnes": stockpile_cumulative,
                "crusher_cumulative_tonnes": crusher_cumulative,
                "total_destination_tonnes": stockpile_cumulative + crusher_cumulative,
            })
            rows.append({
                "time": row["end_time"],
                "event_stockpile_tonnes": 0.0,
                "event_crusher_tonnes": 0.0,
                "stockpile_cumulative_tonnes": stockpile_cumulative,
                "crusher_cumulative_tonnes": crusher_cumulative,
                "total_destination_tonnes": stockpile_cumulative + crusher_cumulative,
            })

        return pd.DataFrame(rows).dropna(subset=["time"]).sort_values("time")

    def destination_color_pair(self, source_name):
        palette = getattr(self, "destination_pastel_palette", None) or [
            ("#B7D8F3", "#F6C28B"),
            ("#C8E6C9", "#F4B6C2"),
            ("#C9B8EA", "#F7E7A3"),
        ]
        seed = sum((index + 1) * ord(char) for index, char in enumerate(str(source_name)))
        return palette[seed % len(palette)]

    @staticmethod
    def expand_interval_series(data, value_column, custom_columns):
        x_values = []
        y_values = []
        custom_values = []

        for _, row in data.sort_values("start_datetime").iterrows():
            start_time = row.get("start_datetime")
            end_time = row.get("end_datetime")
            if pd.isna(start_time) or pd.isna(end_time):
                continue

            y_value = row.get(value_column, 0)
            try:
                y_value = float(y_value)
            except (TypeError, ValueError):
                y_value = 0

            custom_row = [row.get(column, "") for column in custom_columns]
            x_values.extend([start_time, end_time])
            y_values.extend([y_value, y_value])
            custom_values.extend([custom_row, custom_row])

        return {
            "x": x_values,
            "y": y_values,
            "customdata": np.array(custom_values, dtype=object),
        }

    @staticmethod
    def grade_block_movement_summary(stockpile_data):
        data = stockpile_data.copy()
        movement_destination = data.get("movement_destination", pd.Series("", index=data.index)).fillna("").astype(str)
        source_or_destination = data.get("source_or_destination", pd.Series("", index=data.index)).fillna("").astype(str)
        arrival_mask = (
            movement_destination.str.contains("arrived", case=False, na=False)
            | source_or_destination.str.contains("arrived", case=False, na=False)
        )
        arrival_source = data.loc[arrival_mask, "arrival_tonnes"] if arrival_mask.any() else data.get("arrival_tonnes", 0)
        arrival_tonnes = float(pd.to_numeric(arrival_source, errors="coerce").fillna(0).sum())
        tonnes_to_crusher = float(pd.to_numeric(
            data.get("tonnes_to_crusher", 0), errors="coerce"
        ).fillna(0).sum())
        tonnes_to_stockpile = float(pd.to_numeric(
            data.get("tonnes_to_stockpile", 0), errors="coerce"
        ).fillna(0).sum())
        remaining_tonnes = arrival_tonnes - tonnes_to_crusher - tonnes_to_stockpile
        if abs(remaining_tonnes) < 0.5:
            remaining_tonnes = 0

        return {
            "arrival_tonnes": arrival_tonnes,
            "tonnes_to_crusher": tonnes_to_crusher,
            "tonnes_to_stockpile": tonnes_to_stockpile,
            "remaining_tonnes": max(remaining_tonnes, 0),
        }

    def grade_block_summary_row(self, summary):
        return html.Div(
            children=[
                self.summary_chip("Arrived at ROM / Crusher Area", summary["arrival_tonnes"], "#e0f2fe", "#075985"),
                self.summary_chip("To Crusher", summary["tonnes_to_crusher"], "#fee2e2", "#991b1b"),
                self.summary_chip("To Stockpile", summary["tonnes_to_stockpile"], "#dbeafe", "#1e40af"),
                self.summary_chip("Remaining", summary["remaining_tonnes"], "#f1f5f9", "#334155"),
            ],
            style={
                "display": "flex",
                "gap": "8px",
                "flexWrap": "wrap",
                "padding": "2px 16px 8px 16px",
            },
        )

    @staticmethod
    def summary_chip(label, value, background_color, text_color):
        return html.Span(
            f"{label}: {value:,.1f} WMT",
            style={
                "backgroundColor": background_color,
                "color": text_color,
                "fontSize": "12px",
                "fontWeight": "650",
                "padding": "5px 9px",
                "borderRadius": "999px",
            },
        )

    @staticmethod
    def prepare_profile_for_chart(stockpile_data, max_points=900):
        stockpile_data = stockpile_data.copy()
        stockpile_data['time'] = pd.to_datetime(stockpile_data['time'], errors='coerce')
        stockpile_data['Balance'] = pd.to_numeric(stockpile_data['Balance'], errors='coerce')
        stockpile_data = stockpile_data.dropna(subset=['time', 'Balance']).sort_values('time')
        if stockpile_data.empty:
            return stockpile_data

        balance_changed = stockpile_data['Balance'].ne(stockpile_data['Balance'].shift())
        keep_mask = balance_changed | balance_changed.shift(-1, fill_value=False)
        keep_mask.iloc[0] = True
        keep_mask.iloc[-1] = True
        stockpile_data = stockpile_data.loc[keep_mask].copy()

        if len(stockpile_data) > max_points:
            sampled_positions = np.linspace(0, len(stockpile_data) - 1, max_points).round().astype(int)
            stockpile_data = stockpile_data.iloc[np.unique(sampled_positions)].copy()

        return stockpile_data

    @staticmethod
    def profile_type_for_row(row):
        source_type = str(row.get("source_type", "") or "").strip().lower()
        source_name = str(row.get("stockpile", "") or "").strip()
        if source_type in {"grade_block", "grade block", "gradeblock"}:
            return "Grade Block"
        if source_type == "stockpile":
            return "Stockpile"
        if source_name.startswith("Reserves/"):
            return "Grade Block"
        return "Stockpile"

    @staticmethod
    def hex_to_rgba(hex_color, alpha):
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6:
            return f"rgba(127, 179, 213, {alpha})"
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"

    def setup_layout(self):
        """
        Set up the initial layout of the app.
        """
        self.app.layout = html.Div(
            children=[
                dcc.Input(id="manual-refresh", type="hidden"),  # Add the hidden input component
                html.Div(
                    children=[
                        html.Div(
                            children=[
                                html.Div(
                                    "Build and Depletion Profiles",
                                    style={
                                        "fontSize": "22px",
                                        "fontWeight": "750",
                                        "color": "#172033",
                                    },
                                ),
                                html.Div(
                                    "Product builds, crusher feed, stockpile balances and grade-block destinations through time",
                                    style={
                                        "fontSize": "13px",
                                        "color": "#64748b",
                                        "marginTop": "2px",
                                    },
                                ),
                            ],
                            style={"padding": "14px 4px 12px 4px"},
                        ),
                        html.Div(id="chart-container", children=[]),
                    ],
                    style={
                        "backgroundColor": "#f8fafc",
                        "padding": "12px 18px 18px 18px",
                        "minHeight": "100vh",
                        "fontFamily": "Segoe UI, Arial, sans-serif",
                    },
                ),
            ],
            style={"margin": "0", "backgroundColor": "#f8fafc"},
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
            try:
                data = self.fetch_data()
                if 'time' in data.columns:
                    data = data.copy()
                    data['time'] = pd.to_datetime(data['time'], errors='coerce')
                crusher_data = self.fetch_crusher_data()
                product_build_data = self.fetch_product_build_data()
                return self.create_charts(data, crusher_data, product_build_data)
            except Exception:
                print(traceback.format_exc())
                return html.Div(
                    "Unable to load depletion profiles. Check the console for details.",
                    style={
                        "padding": "16px",
                        "backgroundColor": "#fff7ed",
                        "border": "1px solid #fdba74",
                        "borderRadius": "8px",
                        "color": "#9a3412",
                        "fontWeight": "600",
                    },
                )

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
        self.stockpile_pastel_palette = [
            "#A8D5BA",  # pale green
            "#F6C28B",  # pale orange
            "#F7E7A3",  # pale yellow
            "#D9C28F",  # ochre
            "#A7C7E7",  # pale blue
            "#BFD8D2",  # mint
            "#CDB4DB",  # lavender
            "#F4BFBF",  # soft coral
            "#BDE0FE",  # light sky blue
            "#C9E4CA",  # sage
            "#F2D0A4",  # apricot
            "#E2D4B7",  # sand
        ]
        random.shuffle(self.stockpile_pastel_palette)
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
        required_columns = {
            "start_datetime",
            "end_datetime",
            "blend_ID",
            "steady_state_number",
            "source",
            "source_blend_ratio",
        }
        if data.empty or not required_columns.issubset(data.columns):
            return pd.DataFrame()

        if "actual_direct_tip_ratio" not in data.columns:
            data["actual_direct_tip_ratio"] = 0
        if "source_type" not in data.columns:
            data["source_type"] = "stockpile"

        # Ensure datetime columns are in the correct format
        data['start_datetime'] = pd.to_datetime(data['start_datetime'], errors='coerce')
        data['end_datetime'] = pd.to_datetime(data['end_datetime'], errors='coerce')
        if "steady_state_duration" not in data.columns:
            data["steady_state_duration"] = (
                data["end_datetime"] - data["start_datetime"]
            ).dt.total_seconds() / 3600
        data["source_blend_ratio_numeric"] = pd.to_numeric(
            data["source_blend_ratio"], errors="coerce"
        ).fillna(0)

        def build_stockpile_signature(group):
            stockpile_rows = group[
                (group["source_type"] == "stockpile")
                & (group["source_blend_ratio_numeric"] > 0)
            ].copy()
            if stockpile_rows.empty:
                return "No stockpile component"

            stockpile_sources = sorted(
                str(source)
                for source in stockpile_rows["source"].dropna().unique()
                if str(source).strip()
            )
            return " | ".join(stockpile_sources) if stockpile_sources else "No stockpile component"

        def format_stockpile_component(signature):
            if signature == "No stockpile component":
                return signature
            return "; ".join(signature.split(" | "))

        stockpile_signatures = (
            data.groupby(["blend_ID", "steady_state_number"], dropna=False)
            .apply(build_stockpile_signature)
            .reset_index(name="stockpile_component_signature")
        )
        unique_signatures = sorted(stockpile_signatures["stockpile_component_signature"].unique())
        stockpile_mix_map = {
            signature: f"Stockpile Mix {index + 1}"
            for index, signature in enumerate(unique_signatures)
        }
        stockpile_signatures["stockpile_component"] = stockpile_signatures[
            "stockpile_component_signature"
        ].map(stockpile_mix_map)
        stockpile_signatures["stockpile_component_details"] = stockpile_signatures[
            "stockpile_component_signature"
        ].apply(format_stockpile_component)

        # Aggregate sources and source_blend_ratios by blend_ID and steady_state_number
        aggregated = data.groupby(["blend_ID", "steady_state_number"]).agg({
            "source": lambda x: list(x.dropna()),  # Drop NaN values before aggregating
            "source_blend_ratio": lambda x: list(x.dropna()),
        }).reset_index()
        
        # Merge back with original data
        data = pd.merge(data, aggregated, on=["blend_ID", "steady_state_number"], suffixes=("", "_agg"))
        data = pd.merge(data, stockpile_signatures, on=["blend_ID", "steady_state_number"], how="left")
    
        def format_ratio_list(values):
            if not isinstance(values, list):
                return ""
            formatted_values = []
            for value in values:
                try:
                    formatted_values.append(f"{float(value):.2f}")
                except (TypeError, ValueError):
                    formatted_values.append(str(value))
            return ", ".join(formatted_values)

        # Convert lists to comma-separated strings for DataTable compatibility
        data['source_agg'] = data['source_agg'].apply(lambda x: ", ".join(map(str, x)) if isinstance(x, list) else "")
        data['source_blend_ratio_agg'] = data['source_blend_ratio_agg'].apply(format_ratio_list)

        # Add a new column for lanes (to cascade top to bottom)
        lane_map = {blend_id: idx + 1 for idx, blend_id in enumerate(sorted(data['blend_ID'].unique(), reverse=True))}
        data['lane'] = data['blend_ID'].map(lane_map)

        # Create a tooltip column with formatted sources and ratios
        data['Details'] = data.apply(
            lambda row: f"<br>Steady State: {row['steady_state_number']}<br>"
                        f"Duration: {float(row['steady_state_duration']):.2f} hrs<br>"
                        f"Blend Option: {row['blend_option']}<br>"
                        f"Period: {row['period']}<br>"
                        f"Stockpile Component: {row['stockpile_component']}<br>"
                        f"{row['stockpile_component_details']}<br>"
                        f"Sources and Ratios:<br>" +
                        "".join(
                            f" - {source} @ {float(ratio) * 100:.2f}%<br>"  # Format ratio as percent
                            for source, ratio in zip(
                                (row['source_agg'].split(", ") if isinstance(row['source_agg'], str) else []),
                                (row['source_blend_ratio_agg'].split(", ") if isinstance(row['source_blend_ratio_agg'], str) else [])
                            )
                        ) +
                        f"Actual Direct Tip Ratio: {float(row['actual_direct_tip_ratio']):.2f}<br>"
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
            "steady_state_duration": "Duration (hrs)",
            "stockpile_component": "Stockpile Mix",
            "source_agg": "Sources",
            "source_blend_ratio_agg": "Blend Ratios",
            "actual_direct_tip_ratio": "Actual Direct Tip Ratio",
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
                'gap': '12px',
                'padding': '12px',
                'boxSizing': 'border-box',
                'fontFamily': 'Segoe UI, Arial, sans-serif',
                'backgroundColor': '#f8fafc',
                'minHeight': '100vh',
            },
            children=[
                # Gantt Chart
                html.Div(
                    style={
                        'width': '100%',
                        'backgroundColor': '#ffffff',
                        'border': '1px solid #dbe4ee',
                        'borderRadius': '8px',
                        'boxShadow': '0 8px 22px rgba(15, 23, 42, 0.06)',
                        'padding': '12px 14px 14px 14px',
                        'boxSizing': 'border-box',
                    },
                    children=[
                        html.Div(
                            children=[
                                html.Div(
                                    "Gantt Chart & Blend Details",
                                    style={
                                        'fontSize': '18px',
                                        'fontWeight': '750',
                                        'color': '#172033',
                                    },
                                ),
                                html.Div(
                                    "Click a bar to filter the steady-state table below.",
                                    style={
                                        'fontSize': '12px',
                                        'color': '#64748b',
                                        'marginTop': '2px',
                                    },
                                ),
                            ],
                            style={'margin': '0 0 10px 0'},
                        ),
                        dcc.Graph(
                            id="gantt-chart",
                            style={
                                'width': '100%',
                                'height': '320px',
                                'border': '0',
                                'padding': '0',
                                'borderRadius': '6px',
                                'overflow': 'hidden',
                                'boxSizing': 'border-box'
                            }
                        )
                    ]
                ),
                # Property Table
                html.Div(
                    style={
                        'backgroundColor': '#ffffff',
                        'border': '1px solid #dbe4ee',
                        'borderRadius': '8px',
                        'boxShadow': '0 8px 22px rgba(15, 23, 42, 0.05)',
                        'padding': '10px 12px 12px 12px',
                        'boxSizing': 'border-box',
                    },
                    children=[
                        html.Div(
                            "Steady-State Details",
                            style={
                                'fontSize': '15px',
                                'fontWeight': '750',
                                'color': '#172033',
                                'margin': '0 0 8px 0',
                            },
                        ),
                        dash_table.DataTable(
                            id="property-table",
                            columns=[
                                {"name": column_aliases.get(col, col), "id": col}  
                                for col in [
                                    "start_datetime", "end_datetime", "blend_ID", "lane", "steady_state_number",
                                    "steady_state_duration", "stockpile_component", "source_agg",
                                    "source_blend_ratio_agg", "actual_direct_tip_ratio", "crusher_actual_tonnes",
                                    "crusher_rate_output"
                                ] + [col for col in self.fetch_data().columns if col.startswith("crusher_actual_grade_")]
                            ],
                            data=[],  # Initially empty
                            style_table={
                                'overflowX': 'auto',
                                'overflowY': 'auto',
                                'maxHeight': '360px',
                                'border': '1px solid #e2e8f0',
                                'borderRadius': '6px',
                            },
                            style_cell={
                                'textAlign': 'center',
                                'padding': '7px 8px',
                                'whiteSpace': 'normal',
                                'overflow': 'hidden',
                                'textOverflow': 'ellipsis',
                                'maxWidth': '150px',
                                'height': 'auto',
                                'lineHeight': '1.2',
                                'fontFamily': 'Segoe UI, Arial, sans-serif',
                                'fontSize': '12px',
                                'color': '#1f2937',
                                'border': '1px solid #e5e7eb',
                            },
                            style_cell_conditional=[
                                {'if': {'column_id': 'start_datetime'}, 'textAlign': 'center'},
                                {'if': {'column_id': 'end_datetime'}, 'textAlign': 'center'},
                                {
                                    'if': {'column_id': 'source_agg'},
                                    'textAlign': 'left',
                                    'minWidth': '420px',
                                    'width': '520px',
                                    'maxWidth': '760px',
                                    'whiteSpace': 'normal',
                                    'overflow': 'visible',
                                    'textOverflow': 'clip',
                                },
                                {
                                    'if': {'column_id': 'source_blend_ratio_agg'},
                                    'textAlign': 'center',
                                    'minWidth': '160px',
                                    'width': '180px',
                                    'maxWidth': '220px',
                                    'whiteSpace': 'normal',
                                    'overflow': 'visible',
                                    'textOverflow': 'clip',
                                }
                            ],
                            style_header={
                                'fontWeight': 'bold',
                                'textAlign': 'center',
                                'fontFamily': 'Segoe UI, Arial, sans-serif',
                                'fontSize': '12px',
                                'whiteSpace': 'normal',
                                'height': 'auto',
                                'lineHeight': '1.2',
                                'padding': '8px',
                                'overflow': 'hidden',
                                'backgroundColor': '#f1f5f9',
                                'color': '#0f172a',
                                'border': '1px solid #dbe4ee',
                            },
                            style_data_conditional=[
                                {
                                    'if': {'row_index': 'odd'},
                                    'backgroundColor': '#f8fafc',
                                },
                                {
                                    'if': {'state': 'selected'},
                                    'backgroundColor': '#e0f2fe',
                                    'border': '1px solid #38bdf8',
                                },
                            ],
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
                'border': '0',
                'padding': '0',
                'borderRadius': '6px',
                'overflow': 'hidden',
                'boxSizing': 'border-box'
            }

            if data.empty:
                return px.scatter(title="No data available"), base_chart_style

            data = self.prepare_gantt_data(data)
            if data.empty:
                return px.scatter(title="No data available"), base_chart_style
            data['hover_name'] = "Blend ID: " + data['blend_ID'].astype(str)

            chart_data = data.drop_duplicates(
                subset=["blend_ID", "steady_state_number", "start_datetime", "end_datetime", "lane", "Legend"]
            ).copy()

            stockpile_palette = getattr(self, "stockpile_pastel_palette", [
                "#A8D5BA", "#F6C28B", "#F7E7A3", "#D9C28F", "#A7C7E7", "#BFD8D2"
            ])
            component_color_map = {}
            legend_color_map = {}
            for _, row in chart_data.drop_duplicates("Legend").iterrows():
                component_signature = row.get("stockpile_component_signature", row.get("Legend", ""))
                if component_signature not in component_color_map:
                    component_color_map[component_signature] = stockpile_palette[
                        len(component_color_map) % len(stockpile_palette)
                    ]
                legend_color_map[row["Legend"]] = component_color_map[component_signature]

            num_lanes = max(chart_data['lane'].nunique(), 1)
            chart_height = min(max(280, 190 + (num_lanes * 55)), 720)
            chart_style = dict(base_chart_style)
            chart_style['height'] = f'{chart_height + 18}px'

            # Create Gantt chart
            fig = px.timeline(
                chart_data,
                x_start="start_datetime",
                x_end="end_datetime",
                y="lane",  # Cascading lanes (top to bottom)
                color="Legend",
                color_discrete_map=legend_color_map,
                hover_name="hover_name",
                hover_data={
                'blend_ID': False,
                'start_datetime': True,  # Hide start_datetime
                'end_datetime': True,    # Hide end_datetime
                'lane': False,            # Hide lane
                'Details': True,          # Only display the tooltip explicitly
                'Legend' : False,
                'stockpile_component': False,
                'stockpile_component_details': False
                },
                title=""
            )

            # Add borders to bars
            fig.update_traces(
                marker=dict(
                    line=dict(
                        width=1,  # Border thickness
                        color="#475569"  # Border color
                    )
                )
            )

            # Adjust layout
            lane_labels = chart_data[['lane', 'blend_ID']].drop_duplicates().sort_values('lane')
            fig.update_layout(
                xaxis_title="",
                yaxis_title="Blend",
                paper_bgcolor="#ffffff",
                plot_bgcolor="#eef4fb",
                font=dict(
                    family="Segoe UI, Arial, sans-serif",
                    size=12,
                    color="#1f2937"
                ),
                yaxis=dict(
                    tickmode='array',
                    tickvals=lane_labels['lane'],
                    ticktext=lane_labels['blend_ID']  # Label lanes with blend_ID
                ),
                xaxis=dict(
                    showgrid=True,
                    gridcolor="rgba(148, 163, 184, 0.30)",
                    zeroline=False,
                ),
                showlegend=True,
                legend=dict(
                title="Blend Details",
                orientation="v",
                yanchor="top",  # Anchor the legend box at the top
                y=1.0,          # Position the legend vertically (can go beyond plot height)
                xanchor="left", # Anchor the legend box horizontally
                x=1.02,         # Position the legend horizontally
                bgcolor="rgba(255,255,255,0.82)",
                bordercolor="#dbe4ee",
                borderwidth=1
                ),
                autosize=True,
                height=chart_height,
                margin=dict(l=64, r=260, t=20, b=54)
            )
            fig.update_yaxes(
                showgrid=False,
                zeroline=False,
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
            if data.empty:
                return []
            data = self.prepare_gantt_data(data)
            if data.empty:
                return []
            
            # Select only the property table columns
            data = data[[
                "start_datetime", "end_datetime", "blend_ID", "lane", "steady_state_number",
                "steady_state_duration", "stockpile_component", "source_agg",
                "source_blend_ratio_agg", "actual_direct_tip_ratio", "crusher_actual_tonnes",
                "crusher_rate_output", "crusher_actual_grade_fe", "crusher_actual_grade_si",
                "crusher_actual_grade_al", "crusher_actual_grade_p", "crusher_actual_grade_mn",
            ]].copy()
            
            # Apply rounding to specific numeric columns
            data["steady_state_duration"] = data["steady_state_duration"].round(2)
            data["actual_direct_tip_ratio"] = data["actual_direct_tip_ratio"].round(2)
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
                filtered_data = data[data['blend_ID'] == clicked_blend_id].copy()
                return filtered_data.to_dict("records")
            
            return data.to_dict("records")

    def run_app(self):
        """
        Run the Dash app.
        """
        self.app.run_server(debug=True, port=self.port, use_reloader=False)
    
    def debug_blend_ID(self, data):
        if data.empty or not {"steady_state_number", "source"}.issubset(data.columns):
            return

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
        database_name = get_database_path()
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
            actual_direct_tip_ratio REAL,
            source TEXT,
            source_id TEXT,
            source_type TEXT,
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

        cursor.execute("PRAGMA table_info(optimised_blend_report)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "source_id" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN source_id TEXT")
        if "source_type" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN source_type TEXT")
        if "actual_direct_tip_ratio" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN actual_direct_tip_ratio REAL")

        # Clear existing data in the table
        cursor.execute('DELETE FROM optimised_blend_report')

        # Insert the data from the DataFrame into the database table
        dataframe.to_sql('optimised_blend_report', conn, if_exists='append', index=False)

        # Commit the transaction and close the connection
        conn.commit()
        conn.close()

class DrawAMTStockpile:
    AMT_COLUMNS = [
        "footprint", "hex", "balance", "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn",
        "lat", "long", "northing", "easting", "last_update", "hex_updated"
    ]

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
        self.unique_footprints = self.get_unique_footprints()
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
            self.unique_footprints = self.get_unique_footprints()
    
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

    def empty_amt_dataframe(self):
        return pd.DataFrame(columns=self.AMT_COLUMNS)

    def get_unique_footprints(self):
        if self.data is None or self.data.empty or "footprint" not in self.data.columns:
            return []
        return self.data["footprint"].dropna().unique()

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
        filtered_data = filtered_data[filtered_data["_positive_balance"] > 0].copy()
        if filtered_data.empty:
            return [], "All hexagons in this footprint have zero or negative balance."

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
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT * FROM opening_AMT_stockpile_inventories"
            data = pd.read_sql(query, conn)
            if data.empty:
                return self.empty_amt_dataframe()
            return data
        except Exception as e:
            if "opening_AMT_stockpile_inventories" not in str(e):
                print(f"Error fetching data: {e}")
            return self.empty_amt_dataframe()
        finally:
            if conn is not None:
                conn.close()

    def init_layout(self):
        page_style = {
            "backgroundColor": "#f8fafc",
            "fontFamily": "Segoe UI, Arial, sans-serif",
            "padding": "14px 16px 18px",
            "color": "#172033",
        }
        card_style = {
            "backgroundColor": "#ffffff",
            "border": "1px solid #d8e0ea",
            "borderRadius": "8px",
            "boxShadow": "0 8px 20px rgba(15, 23, 42, 0.05)",
            "padding": "12px",
        }
        label_style = {
            "fontSize": "12px",
            "fontWeight": "700",
            "color": "#334155",
            "marginBottom": "6px",
        }
        button_style = {
            "borderRadius": "5px",
            "fontWeight": "650",
            "fontSize": "12px",
            "padding": "7px 10px",
            "marginRight": "8px",
            "marginBottom": "8px",
        }

        self.app.layout = dbc.Container([
            html.Div([
                html.Div([
                    html.H4(
                        "AMT Stockpile Chunks",
                        style={"fontWeight": "750", "margin": "0", "letterSpacing": "0", "color": "#172033"}
                    ),
                    html.Div(
                        "Digitize reclaim and cut directions, then generate practical chunks for AMT stockpiles.",
                        style={"fontSize": "12px", "color": "#64748b", "marginTop": "3px"}
                    ),
                ]),
            ], style={**card_style, "marginBottom": "10px"}),

            dbc.Row([
                dbc.Col([
                    html.Div([
                        html.Label("Selected Footprint", style=label_style),
                        dcc.Dropdown(
                            id="footprint-dropdown",
                            options=[{"label": fp, "value": fp} for fp in self.unique_footprints],
                            placeholder="Select a footprint",
                            style={"fontSize": "13px"}
                        ),
                        html.Div(style={"height": "8px"}),
                        dcc.Upload(
                            id="upload-dxf",
                            children=dbc.Button(
                                "Overlay arch_d",
                                size="sm",
                                style={
                                    **button_style,
                                    "backgroundColor": "#475569",
                                    "borderColor": "#475569",
                                    "color": "#ffffff",
                                    "marginBottom": "0",
                                }
                            ),
                            multiple=False,
                        ),
                    ], style=card_style)
                ], md=5),
                dbc.Col([
                    html.Div([
                        html.Div([
                            dbc.Button(
                                "Digitize Reclaim Direction",
                                id="digitize-direction-button",
                                size="sm",
                                style={**button_style, "backgroundColor": "#0e7490", "borderColor": "#0e7490", "color": "#ffffff"}
                            ),
                            dbc.Button(
                                "Digitize Cut Direction",
                                id="digitize-cut-direction-button",
                                size="sm",
                                style={**button_style, "backgroundColor": "#2563eb", "borderColor": "#2563eb", "color": "#ffffff"}
                            ),
                            dbc.Button(
                                "Generate Chunks",
                                id="generate-chunks-button",
                                size="sm",
                                style={**button_style, "backgroundColor": "#15803d", "borderColor": "#15803d", "color": "#ffffff"}
                            ),
                            dbc.Button(
                                "Clear Footprint Chunks",
                                id="clear-footprint-button",
                                size="sm",
                                style={**button_style, "backgroundColor": "#f59e0b", "borderColor": "#f59e0b", "color": "#172033"}
                            ),
                        ]),
                        html.Div(
                            id="chunk-status",
                            children=self.status_message,
                            style={
                                "display": "inline-block",
                                "fontSize": "12px",
                                "fontWeight": "700",
                                "color": "#1e3a8a",
                                "backgroundColor": "#eff6ff",
                                "border": "1px solid #bfdbfe",
                                "borderRadius": "999px",
                                "padding": "6px 10px",
                            }
                        )
                    ], style={**card_style, "minHeight": "100%"})
                ], md=7),
            ], style={"marginBottom": "10px"}),

            html.Div([
                html.Label("Hexagon Marker Size", style=label_style),
                dcc.Slider(
                    id="hex-size-slider",
                    min=5,
                    max=50,
                    step=1,
                    value=25,
                    marks={5: "5", 25: "25", 50: "50"},
                    tooltip={"placement": "bottom", "always_visible": True}
                )
            ], style={**card_style, "marginBottom": "10px"}),

            dbc.Row([
                dbc.Col([
                    html.Div([
                        dcc.Graph(
                            id="scatter-plot",
                            config={"scrollZoom": True},
                            style={"height": "600px", "width": "100%"}
                        )
                    ], style={**card_style, "padding": "8px"})
                ], md=9),
                dbc.Col([
                    html.Div([
                        html.Div(
                            "Chunk Sequence",
                            style={"fontWeight": "750", "fontSize": "14px", "marginBottom": "8px", "color": "#172033"}
                        ),
                        dash_table.DataTable(
                            id="selected-table",
                            columns=[{"name": col, "id": col} for col in
                                    ['footprint', 'sequence', 'hex', 'balance', 'grade_fe', 'grade_si', 'grade_al', 'grade_p',
                                    'grade_mn', 'hex_count', 'chunk_size']],
                            data=self.selected_points or [],
                            row_deletable=False,
                            editable=False,
                            style_table={'overflowX': 'auto', 'maxHeight': '580px', 'overflowY': 'auto'},
                            style_cell={
                                'textAlign': 'center',
                                'fontFamily': 'Segoe UI',
                                'fontSize': '10.5px',
                                'padding': '6px',
                                'border': '1px solid #e5e7eb',
                                'color': '#172033',
                            },
                            style_header={
                                'fontWeight': 'bold',
                                'fontSize': '11px',
                                'fontFamily': 'Segoe UI',
                                'textAlign': 'center',
                                'backgroundColor': '#f1f5f9',
                                'border': '1px solid #dbe4ee',
                                'color': '#172033',
                            },
                            style_data_conditional=[
                                {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8fbff'},
                                {'if': {'state': 'selected'}, 'backgroundColor': '#dbeafe', 'border': '1px solid #93c5fd'},
                            ],
                        )
                    ], style={**card_style, "height": "100%"})
                ], md=3),
            ]),

        ], fluid=True, style=page_style)

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
        filtered_data.loc[:, "balance"] = pd.to_numeric(filtered_data["balance"], errors="coerce")
        filtered_data = filtered_data.dropna(subset=["lat", "long"])
        filtered_data = filtered_data[filtered_data["balance"] > 0]
        if filtered_data.empty:
            return fig

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
            filtered_data[grade] = pd.to_numeric(filtered_data[grade], errors="coerce").fillna(0)
            filtered_data[f"{grade}_tooltip"] = filtered_data[grade].round(2)

        chunk_lookup = self.chunk_lookup_for_footprint(selected_footprint)
        filtered_data["chunk_sequence"] = filtered_data["hex"].map(chunk_lookup)
        chunk_palette = [
            "#A8D5BA", "#F6C28B", "#F7E7A3", "#D9C28F", "#A7C7E7",
            "#BFD8D2", "#C9B8EA", "#F4B6C2", "#8ECAD1", "#D7E8BA",
            "#F3C0A8", "#B8D8F0", "#E7D3A0", "#C6E2C6", "#D8C8E8"
        ]

        chunked_data = filtered_data[filtered_data["chunk_sequence"].notna()]
        for chunk_sequence, group in chunked_data.groupby("chunk_sequence"):
            color = chunk_palette[(int(chunk_sequence) - 1) % len(chunk_palette)]
            fig.add_trace(go.Scatter(
                x=group["long"],
                y=group["lat"],
                mode="markers",
                marker=dict(
                    size=hex_size,
                    symbol="hexagon",
                    color=color,
                    line=dict(color="#334155", width=1),
                    opacity=0.95
                ),
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
            fig.add_trace(go.Scatter(
                x=remaining_data["long"],
                y=remaining_data["lat"],
                mode="markers",
                marker=dict(
                    size=hex_size,
                    symbol="hexagon",
                    color="#94a3b8",
                    line=dict(color="#475569", width=1),
                    opacity=0.72
                ),
                name="Available Hexagons",
                customdata=remaining_data[[
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
                    "size": 17,
                    "family": "Segoe UI, Arial, sans-serif",
                    "color": "#172033"
                },
                "x": 0.5,
            },
            xaxis=dict(
                title="Longitude",
                titlefont=dict(size=13, family="Segoe UI, Arial, sans-serif", color="#172033"),
                tickfont=dict(size=11, family="Segoe UI, Arial, sans-serif", color="#475569"),
                gridcolor="#d7e2ee",
                zeroline=False
            ),
            yaxis=dict(
                title="Latitude",
                titlefont=dict(size=13, family="Segoe UI, Arial, sans-serif", color="#172033"),
                tickfont=dict(size=11, family="Segoe UI, Arial, sans-serif", color="#475569"),
                gridcolor="#d7e2ee",
                zeroline=False
            ),
            xaxis_scaleanchor="y",
            yaxis_scaleanchor="x",
            paper_bgcolor="#ffffff",
            plot_bgcolor="#eef4fb",
            margin=dict(l=58, r=20, t=58, b=56),
            legend=dict(
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#d8e0ea",
                borderwidth=1,
                font=dict(size=11, color="#334155"),
                orientation="v"
            ),
            hoverlabel=dict(
                bgcolor="#172033",
                bordercolor="#172033",
                font=dict(color="#ffffff", family="Segoe UI, Arial, sans-serif", size=12)
            )
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
