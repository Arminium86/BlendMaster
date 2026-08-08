import dash
import sqlite3
from dash.dependencies import Input, Output
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import random
import requests
from dash import dcc, html, Input, Output, dash_table, Dash
from flask import Flask, jsonify, request
import threading
import math
from datetime import date, datetime
from classes.ManualBlendRules import ManualBlendRules


def grade_profile_y_axis_settings(values):
    """Return a readable, one-decimal grade axis with no duplicate labels."""
    numeric_values = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if numeric_values.empty:
        return {"tickformat": ".1f"}

    minimum = float(numeric_values.min())
    maximum = float(numeric_values.max())
    span = max(maximum - minimum, 0.0)
    target_step = max(span / 6.0, 0.01)
    step = next(
        candidate
        for candidate in (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
        if candidate >= target_step
    )
    lower = math.floor(minimum / step) * step
    upper = math.ceil(maximum / step) * step
    if upper <= lower:
        lower -= step
        upper += step

    return {
        "autorange": False,
        "range": [lower, upper],
        "tickmode": "linear",
        "dtick": step,
        "tickformat": ".1f",
    }


class ManualBlendDash:
    def __init__(self, stored_blend_sequence_table_for_gantt, manual_gantt_legend_and_tooltip, port, crusher_rate):
        self.stored_blend_sequence_table_for_gantt = stored_blend_sequence_table_for_gantt
        self.manual_gantt_legend_and_tooltip = manual_gantt_legend_and_tooltip
        self.port = port
        self.crusher_rate = crusher_rate
        self.app = Dash(__name__)
        self.colors = [
            "#A8D5BA", "#F6C28B", "#F7E7A3", "#D9C28F", "#A7C7E7",
            "#BFD8D2", "#CDB4DB", "#F4BFBF", "#BDE0FE", "#C9E4CA"
        ]
        random.shuffle(self.colors)
        self.color_mapping = {}
        self.data_lock = threading.Lock()
        self.pending_table_update = None
        self.data_revision = 0
        self.grade_profile_data = pd.DataFrame()
        self.prepare_grade_profile_data(self.stored_blend_sequence_table_for_gantt)
        self.create_layout()
        self.setup_routes()

    def create_layout(self):
        """
        Create the layout for the Dash app.
        """
        self.app.layout = html.Div([
            html.Iframe(
                src="/manual-timeline",
                style={
                    "width": "100%",
                    "height": "92vh",
                    "border": "0",
                    "display": "block"
                }
            )
        ], style={"margin": "0", "padding": "0"})

    def prepare_grade_profile_data(self, chart_data):
        df = pd.DataFrame(chart_data)
        if df.empty:
            self.grade_profile_data = pd.DataFrame()
            return self.grade_profile_data

        for column in ["Blend ID", "Origin", "Start Datetime", "End Datetime", "Duration (hrs)"]:
            if column not in df.columns:
                df[column] = ""

        df["Start Datetime"] = pd.to_datetime(df["Start Datetime"], errors="coerce")
        df["End Datetime"] = pd.to_datetime(df["End Datetime"], errors="coerce")
        df["Duration (hrs)"] = pd.to_numeric(df["Duration (hrs)"], errors="coerce").fillna(0)

        # Merge manual_gantt_legend_and_tooltip for tooltips
        tooltip_df = pd.DataFrame(self.manual_gantt_legend_and_tooltip or [])
        if not tooltip_df.empty and "Blend ID" in tooltip_df.columns:
            df = pd.merge(df, tooltip_df, on="Blend ID", how="left")

        # Calculate the new column 'Feed Tonnes'
        crusher_rate = float(self.crusher_rate or 0)
        if "_crusher_rate" in df.columns:
            row_rates = pd.to_numeric(
                df["_crusher_rate"], errors="coerce"
            ).fillna(crusher_rate)
        else:
            row_rates = crusher_rate
        df["Feed Tonnes"] = df["Duration (hrs)"] * row_rates

        self.grade_profile_data = df
        return self.grade_profile_data

    def setup_routes(self):
        @self.app.server.route("/manual-timeline")
        def manual_timeline():
            return self.build_timeline_html()

        @self.app.server.route("/manual-gantt-data")
        def manual_gantt_data():
            return jsonify(self.build_chart_payload())

        @self.app.server.route("/manual-gantt-update", methods=["POST"])
        def manual_gantt_update():
            payload = request.get_json(silent=True) or {}
            rows = payload.get("rows", [])
            cleaned_rows = []
            for row in rows:
                cleaned_rows.append({
                    key: value
                    for key, value in row.items()
                    if not str(key).startswith("_")
                })

            conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
                cleaned_rows,
            )
            if conflicts:
                return jsonify({
                    "status": "conflict",
                    "message": ManualBlendRules.conflict_message(
                        conflicts[0]
                    ),
                }), 409

            with self.data_lock:
                self.stored_blend_sequence_table_for_gantt = cleaned_rows
                self.pending_table_update = [row.copy() for row in cleaned_rows]
                self.data_revision += 1
                self.prepare_grade_profile_data(self.stored_blend_sequence_table_for_gantt)

            return jsonify({"status": "ok", "revision": self.data_revision})

    def build_chart_payload(self):
        with self.data_lock:
            rows = [row.copy() for row in (self.stored_blend_sequence_table_for_gantt or [])]
            legend = [row.copy() for row in (self.manual_gantt_legend_and_tooltip or [])]
            revision = self.data_revision

        for index, row in enumerate(rows):
            row["_row_index"] = index

        return self.json_safe_value({
            "rows": rows,
            "legend": legend,
            "crusher_rate": self.crusher_rate,
            "colors": self.colors,
            "revision": revision,
        })

    @classmethod
    def json_safe_value(cls, value):
        if isinstance(value, dict):
            return {
                str(key): cls.json_safe_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls.json_safe_value(item) for item in value]
        if isinstance(value, (datetime, date, pd.Timestamp)):
            return value.isoformat()
        if value is pd.NA:
            return None
        if hasattr(value, "item") and callable(value.item):
            try:
                return cls.json_safe_value(value.item())
            except (TypeError, ValueError):
                pass
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return value

    def consume_pending_table_update(self):
        with self.data_lock:
            if self.pending_table_update is None:
                return None
            update = [row.copy() for row in self.pending_table_update]
            self.pending_table_update = None
            return update

    def build_timeline_html(self):
        return """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
html, body {
    margin: 0;
    padding: 0;
    height: 100%;
    font-family: Segoe UI, Arial, sans-serif;
    color: #1f2933;
    background: #ffffff;
}
.page {
    box-sizing: border-box;
    height: 100vh;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
}
.toolbar select {
    height: 28px;
    border: 1px solid #b8c0cc;
    border-radius: 4px;
    background: white;
}
#status {
    color: #52606d;
}
.timeline-shell {
    flex: 1;
    min-height: 260px;
    display: flex;
    gap: 12px;
    overflow: hidden;
}
.timeline-main {
    flex: 1;
    min-width: 0;
    border: 1px solid #20242a;
    border-radius: 4px;
    overflow: auto;
    background: #edf3fb;
}
.axis-row, .lane {
    display: flex;
    min-width: 980px;
}
.axis-label, .lane-label {
    position: sticky;
    left: 0;
    z-index: 3;
    width: 128px;
    flex: 0 0 128px;
    box-sizing: border-box;
    padding: 8px 8px;
    background: #ffffff;
    border-right: 1px solid rgba(31, 41, 55, 0.2);
    font-size: 12px;
    font-weight: 600;
}
.axis-track, .lane-track {
    position: relative;
    min-width: 920px;
    flex: 1 0 auto;
}
.axis-row {
    height: 48px;
    border-bottom: 1px solid rgba(31, 41, 55, 0.22);
}
.axis-track {
    background: #edf3fb;
}
.tick {
    position: absolute;
    top: 0;
    bottom: 0;
    border-left: 1px solid rgba(255, 255, 255, 0.95);
}
.tick-label {
    position: absolute;
    top: 7px;
    transform: translateX(-50%);
    white-space: nowrap;
    font-size: 11px;
    color: #344054;
}
.lane {
    height: 54px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.9);
}
.lane:nth-child(even) .lane-track {
    background: rgba(255, 255, 255, 0.22);
}
.lane-track {
    background-image: linear-gradient(to right, rgba(255,255,255,0.9) 1px, transparent 1px);
    background-size: 120px 100%;
}
.bar {
    position: absolute;
    top: 10px;
    height: 34px;
    box-sizing: border-box;
    border: 1px solid rgba(17, 24, 39, 0.78);
    border-radius: 4px;
    cursor: grab;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    color: #1f2933;
    user-select: none;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.18);
}
.bar:active {
    cursor: grabbing;
}
.bar.default {
    color: #1f2933;
    border-style: dashed;
    opacity: 1;
}
.handle {
    position: absolute;
    top: 0;
    width: 9px;
    height: 100%;
    background: rgba(255, 255, 255, 0.45);
    cursor: ew-resize;
}
.handle.left {
    left: 0;
    border-right: 1px solid rgba(17, 24, 39, 0.25);
}
.handle.right {
    right: 0;
    border-left: 1px solid rgba(17, 24, 39, 0.25);
}
.legend {
    width: 330px;
    flex: 0 0 330px;
    border: 1px solid #20242a;
    border-radius: 4px;
    overflow: auto;
    padding: 10px;
    font-size: 12px;
    background: #ffffff;
}
.legend-title {
    font-size: 14px;
    font-weight: 700;
    margin-bottom: 8px;
}
.legend-item {
    display: grid;
    grid-template-columns: 14px 1fr;
    gap: 8px;
    margin-bottom: 12px;
}
.swatch {
    width: 12px;
    height: 12px;
    border: 1px solid rgba(17, 24, 39, 0.7);
    margin-top: 2px;
}
.legend-heading {
    font-weight: 700;
    margin-bottom: 3px;
}
.muted {
    color: #667085;
}
.empty {
    padding: 28px;
    color: #52606d;
}
</style>
</head>
<body>
<div class="page">
  <div class="toolbar">
    <strong>Manual Gantt</strong>
    <label for="snap-select">Snap</label>
    <select id="snap-select">
      <option value="5">5 min</option>
      <option value="15" selected>15 min</option>
      <option value="30">30 min</option>
      <option value="60">60 min</option>
    </select>
    <span class="muted">Drag bars to move. Drag either edge to resize.</span>
    <span id="status"></span>
  </div>
  <div class="timeline-shell">
    <div id="timeline-main" class="timeline-main"></div>
    <div id="legend" class="legend"></div>
  </div>
</div>
<script>
const state = {
    rows: [],
    legendRows: [],
    colors: [],
    colorByBlend: {},
    snapMinutes: 15,
    timelineStart: 0,
    timelineEnd: 0,
    trackWidth: 920,
    pxPerMs: 1,
    drag: null
};

function parseDate(value) {
    if (!value) return null;
    if (value instanceof Date) return value;
    const normalised = String(value).trim().replace(" ", "T");
    const date = new Date(normalised);
    return Number.isNaN(date.getTime()) ? null : date;
}

function pad(value) {
    return String(value).padStart(2, "0");
}

function formatDate(date) {
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
        " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

function formatTick(date) {
    return pad(date.getHours()) + ":" + pad(date.getMinutes()) + "<br>" +
        date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, function(match) {
        return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"}[match];
    });
}

function snapMs(ms) {
    const snap = state.snapMinutes * 60 * 1000;
    return Math.round(ms / snap) * snap;
}

function rowStart(row) {
    return parseDate(row["Start Datetime"]);
}

function rowEnd(row) {
    return parseDate(row["End Datetime"]);
}

function setStatus(text) {
    document.getElementById("status").textContent = text || "";
}

function colorForBlend(blendId) {
    const key = String(blendId ?? "");
    if (!state.colorByBlend[key]) {
        const index = Object.keys(state.colorByBlend).length;
        state.colorByBlend[key] = state.colors[index % state.colors.length] || "#A8D5BA";
    }
    return state.colorByBlend[key];
}

function computeTimelineBounds() {
    const validStarts = state.rows.map(rowStart).filter(Boolean).map(d => d.getTime());
    const validEnds = state.rows.map(rowEnd).filter(Boolean).map(d => d.getTime());
    if (!validStarts.length || !validEnds.length) {
        const now = new Date();
        state.timelineStart = now.getTime();
        state.timelineEnd = now.getTime() + 24 * 60 * 60 * 1000;
        return;
    }
    const start = Math.min(...validStarts);
    const end = Math.max(...validEnds);
    const span = Math.max(end - start, 60 * 60 * 1000);
    state.timelineStart = start - span * 0.04;
    state.timelineEnd = end + span * 0.04;
}

function updateScale() {
    const main = document.getElementById("timeline-main");
    const availableWidth = Math.max((main.clientWidth || 1100) - 150, 920);
    state.trackWidth = Math.max(availableWidth, state.rows.length * 130, 920);
    state.pxPerMs = state.trackWidth / Math.max(state.timelineEnd - state.timelineStart, 1);
}

function leftFor(ms) {
    return (ms - state.timelineStart) * state.pxPerMs;
}

function widthFor(startMs, endMs) {
    return Math.max((endMs - startMs) * state.pxPerMs, 3);
}

function buildLegendDetails(row) {
    const grade = name => {
        const value = row[name];
        if (value === undefined || value === null || value === "") return "";
        return "<div>" + name + ": " + escapeHtml(value) + (value === "AMT" ? "" : "%") + "</div>";
    };
    let html = "<div class='legend-heading'>Blend ID: " + escapeHtml(row["Blend ID"]) + "</div>";
    html += grade("Grade Fe") + grade("Grade Si") + grade("Grade Al") + grade("Grade P") + grade("Grade Mn");
    if (row["Sources"] && row["Source Ratios"]) {
        const sources = String(row["Sources"]).split(",");
        const ratios = String(row["Source Ratios"]).split(",");
        html += "<div style='margin-top:5px;'>Sources and Ratios:</div>";
        sources.forEach((source, index) => {
            const rawRatio = String(ratios[index] || "").trim();
            const numericRatio = Number(rawRatio);
            const displayedRatio = Number.isFinite(numericRatio)
                ? (numericRatio * 100).toFixed(2)
                : rawRatio;
            html += "<div>- " + escapeHtml(source.trim()) + " @ " +
                escapeHtml(displayedRatio) + "%</div>";
        });
    }
    if (row["Direct Tip Sources"]) {
        const sources = String(row["Direct Tip Sources"]).split(",");
        const ratios = String(row["Direct Tip Ratios"] || "").split(",");
        const tonnes = String(row["Direct Tip Tonnes"] || "").split(",");
        html += "<div style='margin-top:5px;'>Direct Tip Grade Blocks:</div>";
        sources.forEach((source, index) => {
            const numericRatio = Number(String(ratios[index] || "").trim());
            const numericTonnes = Number(String(tonnes[index] || "").trim());
            const displayedRatio = Number.isFinite(numericRatio)
                ? (numericRatio * 100).toFixed(2) + "%"
                : "";
            const displayedTonnes = Number.isFinite(numericTonnes)
                ? " (" + numericTonnes.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2
                }) + " t)"
                : "";
            html += "<div>- " + escapeHtml(source.trim()) +
                (displayedRatio ? " @ " + escapeHtml(displayedRatio) : "") +
                displayedTonnes + "</div>";
        });
    }
    return html;
}

function renderLegend() {
    const legend = document.getElementById("legend");
    const legendByBlend = {};
    state.legendRows.forEach(row => {
        legendByBlend[String(row["Blend ID"])] = row;
    });
    const shown = new Set();
    let html = "<div class='legend-title'>Blend Details</div>";
    state.rows.forEach(row => {
        const blendId = String(row["Blend ID"] ?? "");
        if (shown.has(blendId)) return;
        shown.add(blendId);
        const detailRow = legendByBlend[blendId] || row;
        html += "<div class='legend-item'><div class='swatch' style='background:" + colorForBlend(blendId) + "'></div><div>" +
            buildLegendDetails(detailRow) + "</div></div>";
    });
    legend.innerHTML = html;
}

function renderAxis(track) {
    track.innerHTML = "";
    const tickCount = 6;
    for (let i = 0; i <= tickCount; i++) {
        const ratio = i / tickCount;
        const tickMs = state.timelineStart + (state.timelineEnd - state.timelineStart) * ratio;
        const left = leftFor(tickMs);
        const tick = document.createElement("div");
        tick.className = "tick";
        tick.style.left = left + "px";
        const label = document.createElement("div");
        label.className = "tick-label";
        label.style.left = left + "px";
        label.innerHTML = formatTick(new Date(tickMs));
        track.appendChild(tick);
        track.appendChild(label);
    }
}

function updateRowFromTimes(rowIndex, startMs, endMs) {
    const minDuration = 5 * 60 * 1000;
    if (endMs - startMs < minDuration) {
        endMs = startMs + minDuration;
    }
    const row = state.rows[rowIndex];
    row["Start Datetime"] = formatDate(new Date(startMs));
    row["End Datetime"] = formatDate(new Date(endMs));
    row["Duration (hrs)"] = ((endMs - startMs) / 3600000).toFixed(1);
}

function positionBar(bar, row) {
    const start = rowStart(row);
    const end = rowEnd(row);
    if (!start || !end) return;
    bar.style.left = leftFor(start.getTime()) + "px";
    bar.style.width = widthFor(start.getTime(), end.getTime()) + "px";
    const duration = Number(row["Duration (hrs)"] || 0);
    bar.querySelector(".bar-label").textContent = "Blend " + row["Blend ID"] + " - " + duration.toFixed(1) + " hrs";
    const durationHours = Number(row["Duration (hrs)"] || 0);
    bar.title = "Start: " + row["Start Datetime"] +
        "\\nEnd: " + row["End Datetime"] +
        "\\nDuration: " + durationHours.toFixed(1) + " hrs";
    if (row["Direct Tip Tonnes"] !== undefined && row["Direct Tip Tonnes"] !== null) {
        const directTipTonnes = Number(row["Direct Tip Tonnes"] || 0);
        const directTipRatio = Number(row["Direct Tip Ratio"] || 0);
        bar.title += "\\nSelected Direct Tip: " + directTipTonnes.toFixed(2) +
            " t (" + (directTipRatio * 100).toFixed(1) + "%)";
    }
}

function beginDrag(event, rowIndex, mode) {
    event.preventDefault();
    event.stopPropagation();
    const row = state.rows[rowIndex];
    const start = rowStart(row);
    const end = rowEnd(row);
    if (!start || !end) return;
    state.drag = {
        rowIndex,
        mode,
        startX: event.clientX,
        originalStart: start.getTime(),
        originalEnd: end.getTime()
    };
    document.addEventListener("mousemove", onDragMove);
    document.addEventListener("mouseup", endDrag);
}

function onDragMove(event) {
    if (!state.drag) return;
    const deltaMs = (event.clientX - state.drag.startX) / state.pxPerMs;
    let newStart = state.drag.originalStart;
    let newEnd = state.drag.originalEnd;
    if (state.drag.mode === "move") {
        const snappedDelta = snapMs(deltaMs);
        newStart = state.drag.originalStart + snappedDelta;
        newEnd = state.drag.originalEnd + snappedDelta;
    } else if (state.drag.mode === "left") {
        newStart = snapMs(state.drag.originalStart + deltaMs);
        newStart = Math.min(newStart, state.drag.originalEnd - 5 * 60 * 1000);
    } else if (state.drag.mode === "right") {
        newEnd = snapMs(state.drag.originalEnd + deltaMs);
        newEnd = Math.max(newEnd, state.drag.originalStart + 5 * 60 * 1000);
    }
    updateRowFromTimes(state.drag.rowIndex, newStart, newEnd);
    const bar = document.querySelector(".bar[data-row-index='" + state.drag.rowIndex + "']");
    if (bar) positionBar(bar, state.rows[state.drag.rowIndex]);
    setStatus("Editing Blend " + state.rows[state.drag.rowIndex]["Blend ID"]);
}

async function endDrag() {
    if (!state.drag) return;
    document.removeEventListener("mousemove", onDragMove);
    document.removeEventListener("mouseup", endDrag);
    state.drag = null;
    computeTimelineBounds();
    updateScale();
    render();
    await postUpdate();
}

async function postUpdate() {
    setStatus("Saving chart edits...");
    try {
        const rows = state.rows.map(row => {
            const clean = {};
            Object.keys(row).forEach(key => {
                if (!key.startsWith("_")) clean[key] = row[key];
            });
            return clean;
        });
        const response = await fetch("/manual-gantt-update", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({rows})
        });
        const result = await response.json();
        if (!response.ok) {
            await loadData();
            throw new Error(result.message || "Update failed");
        }
        setStatus("Chart edits saved to table");
        setTimeout(() => setStatus(""), 1800);
    } catch (error) {
        setStatus(error.message || "Could not save chart edit");
        console.error(error);
    }
}

function render() {
    const main = document.getElementById("timeline-main");
    if (!state.rows.length) {
        main.innerHTML = "<div class='empty'>No blend sequence rows available.</div>";
        renderLegend();
        return;
    }
    computeTimelineBounds();
    updateScale();
    main.innerHTML = "";

    const axisRow = document.createElement("div");
    axisRow.className = "axis-row";
    const axisLabel = document.createElement("div");
    axisLabel.className = "axis-label";
    axisLabel.textContent = "Time";
    const axisTrack = document.createElement("div");
    axisTrack.className = "axis-track";
    axisTrack.style.minWidth = state.trackWidth + "px";
    renderAxis(axisTrack);
    axisRow.appendChild(axisLabel);
    axisRow.appendChild(axisTrack);
    main.appendChild(axisRow);

    state.rows.forEach((row, rowIndex) => {
        const lane = document.createElement("div");
        lane.className = "lane";
        const label = document.createElement("div");
        label.className = "lane-label";
        label.innerHTML = "Blend " + escapeHtml(row["Blend ID"]) + "<br><span class='muted'>" + escapeHtml(row["Origin"] || "") + "</span>";
        const track = document.createElement("div");
        track.className = "lane-track";
        track.style.minWidth = state.trackWidth + "px";

        const bar = document.createElement("div");
        bar.className = "bar" + (row["Origin"] === "User Defined" ? "" : " default");
        bar.dataset.rowIndex = rowIndex;
        bar.style.background = colorForBlend(row["Blend ID"]);
        const leftHandle = document.createElement("div");
        leftHandle.className = "handle left";
        const rightHandle = document.createElement("div");
        rightHandle.className = "handle right";
        const labelSpan = document.createElement("span");
        labelSpan.className = "bar-label";
        bar.appendChild(leftHandle);
        bar.appendChild(labelSpan);
        bar.appendChild(rightHandle);
        bar.addEventListener("mousedown", event => beginDrag(event, rowIndex, "move"));
        leftHandle.addEventListener("mousedown", event => beginDrag(event, rowIndex, "left"));
        rightHandle.addEventListener("mousedown", event => beginDrag(event, rowIndex, "right"));
        positionBar(bar, row);

        track.appendChild(bar);
        lane.appendChild(label);
        lane.appendChild(track);
        main.appendChild(lane);
    });
    renderLegend();
}

async function loadData() {
    try {
        const response = await fetch("/manual-gantt-data?ts=" + Date.now());
        const payload = await response.json();
        state.rows = payload.rows || [];
        state.legendRows = payload.legend || [];
        state.colors = payload.colors || [];
        state.colorByBlend = {};
        render();
    } catch (error) {
        document.getElementById("timeline-main").innerHTML = "<div class='empty'>Could not load manual Gantt data.</div>";
        console.error(error);
    }
}

document.getElementById("snap-select").addEventListener("change", event => {
    state.snapMinutes = Number(event.target.value || 15);
});
window.addEventListener("resize", render);
loadData();
setInterval(async () => {
    if (!state.drag) {
        await loadData();
    }
}, 2500);
</script>
</body>
</html>
"""

    def run_app(self):
        """
        Run the Dash app.
        """
        self.app.run_server(port=self.port, debug=True, use_reloader=False)

    def update_data(self, new_data, manual_gantt_legend_and_tooltip):
        """
        Update the stored blend sequence data programmatically.
        """
        with self.data_lock:
            self.stored_blend_sequence_table_for_gantt = new_data
            self.manual_gantt_legend_and_tooltip = manual_gantt_legend_and_tooltip
            self.data_revision += 1
            self.prepare_grade_profile_data(self.stored_blend_sequence_table_for_gantt)
    
    def return_grade_profile_data(self):
        return self.grade_profile_data

class DrawGradeProfiles:
    def __init__(self, data, hex_sequence_table, updated_stockpile_data, port):
        # Initialize Dash app
        self.app = dash.Dash(__name__)
        self.port = port
        self.df = data
        self.hex_sequence_table = hex_sequence_table
        self.updated_stockpile_data = updated_stockpile_data
        
        # Set up the layout
        self.app.layout = html.Div(id='main-container', children=[
            dcc.Store(id='df-store', data=self.df.to_dict('records')),
            html.Div(id='charts-container')
        ])
        
        # Set up callbacks
        self.app.callback(
            Output('charts-container', 'children'),
            Input('df-store', 'data')
        )(self.update_charts)
    
    def transform_data(self, df, grade_columns, hex_sequence_table, updated_stockpile_data):
        """Transform the data to create a 'time' column and expand the rows, modifying records where grade is 'AMT'."""

        grade_key_mapping = {
            "grade_fe": "Grade Fe",
            "grade_si": "Grade Si",
            "grade_al": "Grade Al",
            "grade_p": "Grade P",
            "grade_mn": "Grade Mn",
        }

        # Rename the keys in hex_sequence_table
        hex_sequence_table = [
            {grade_key_mapping.get(k, k): v for k, v in entry.items()} for entry in hex_sequence_table
        ]

        # Rename the keys inside updated_stockpile_data (nested dict)
        updated_stockpile_data = {
            stockpile: {grade_key_mapping.get(k, k): v for k, v in grades.items()}
            for stockpile, grades in updated_stockpile_data.items()
        }

        # Sort by Start Time
        df["Start Datetime"] = pd.to_datetime(df["Start Datetime"], errors="coerce")
        df["End Datetime"] = pd.to_datetime(df["End Datetime"], errors="coerce")
        df = df.dropna(subset=["Start Datetime", "End Datetime"])
        df = df.dropna(subset=grade_columns, how="all")
        df = df.sort_values(by='Start Datetime').reset_index(drop=True)

        # Identify records that need modification (any grade column has "AMT")
        amt_records = df[df[grade_columns].eq("AMT").any(axis=1)].copy()

        # Remove original records that had "AMT"
        df = df[~df.index.isin(amt_records.index)].reset_index(drop=True)

        new_records = []

        for _, row in amt_records.iterrows():
            blend_id = row["Blend ID"]
            sources = [s.strip() for s in row["Sources"].split(",")]  # Strip spaces from sources
            source_ratios = list(map(float, row["Source Ratios"].split(",")))  # Corresponding ratios
            feed_tonnes = row["Feed Tonnes"]
            scheduled_duration = float(row["Duration (hrs)"])
            start_datetime = row["Start Datetime"]

            if scheduled_duration <= 0:
                continue

            # Use the scheduled row duration, not the blend's total available duration.
            blend_reclaim_rate = float(feed_tonnes) / scheduled_duration

            # Compute reclaim rate for each source
            source_reclaim_rates = {source: blend_reclaim_rate * ratio for source, ratio in zip(sources, source_ratios)}

            # Filter relevant hexes from hex_sequence_table
            source_hex_data = [
                hex_row for hex_row in hex_sequence_table
                if hex_row["footprint"].strip() in sources
                and max(float(hex_row.get("balance", 0) or 0), 0) > 0
            ]
            if not source_hex_data:
                continue  # Skip if no matching hexes found

            source_hex_data.sort(key=lambda x: x["sequence"])  # Ensure order

            # Group hexes by stockpile (footprint)
            stockpile_hex_groups = {}
            for hex_row in source_hex_data:
                footprint = hex_row["footprint"].strip()
                if footprint not in stockpile_hex_groups:
                    stockpile_hex_groups[footprint] = []
                stockpile_hex_groups[footprint].append(hex_row)

            current_time = start_datetime
            remaining_duration = scheduled_duration

            while remaining_duration > 0 and source_hex_data:
                min_duration = float("inf")
                hex_entries = []

                # Determine min duration considering all stockpiles in Blend ID
                for footprint, hex_list in stockpile_hex_groups.items():
                    if not hex_list:
                        continue
                    
                    hex_row = hex_list[0]  # Get first hex in sequence for this stockpile
                    hex_balance = max(float(hex_row.get("balance", 0) or 0), 0)
                    source_reclaim_rate = source_reclaim_rates.get(footprint, 0)

                    if source_reclaim_rate == 0 or hex_balance <= 0:
                        continue

                    hex_duration = hex_balance / source_reclaim_rate
                    hex_entries.append((hex_row, hex_duration, footprint))

                if not hex_entries:
                    break

                min_duration = min(hex_duration for _, hex_duration, _ in hex_entries)

                # Ensure we do not exceed the total remaining duration
                min_duration = min(min_duration, remaining_duration)

                # Generate new records
                new_start_time = current_time
                new_end_time = pd.to_datetime(new_start_time) + pd.to_timedelta(min_duration, unit="h")

                total_feed_tonnes = 0
                weighted_grades = {grade: 0 for grade in grade_columns}
                total_weight = 0

                for hex_row, hex_duration, footprint in hex_entries:
                    hex_balance = max(float(hex_row.get("balance", 0) or 0), 0)
                    hex_grades = {grade: hex_row[grade] for grade in grade_columns}

                    # Determine feed tonnes for this segment
                    source_reclaim_rate = source_reclaim_rates.get(footprint, 0)
                    calculated_balance = source_reclaim_rate * min_duration

                    hex_balance = min(hex_balance, calculated_balance)

                    total_feed_tonnes += hex_balance
                    total_weight += hex_balance

                    # Compute weighted average for grades
                    for grade in grade_columns:
                        hex_grade = hex_grades[grade]
                        weighted_grades[grade] += hex_grade * (hex_balance)

                # Handle sources missing from `hex_sequence_table` (conventional stockpiles)
                for source in sources:
                    if source not in stockpile_hex_groups:  # If source is NOT in hex_sequence_table
                        source_reclaim_rate = source_reclaim_rates.get(source, 0)
                        if source_reclaim_rate > 0:
                            calculated_balance = source_reclaim_rate * min_duration
                            total_feed_tonnes += calculated_balance
                            total_weight += calculated_balance

                            if source in updated_stockpile_data:
                                source_grades = updated_stockpile_data[source]
                                for grade in grade_columns:
                                    weighted_grades[grade] += source_grades[grade] * calculated_balance

                # Normalize grade values
                for grade in grade_columns:
                    if total_weight > 0:
                        weighted_grades[grade] /= total_weight

                # Create new record
                new_records.append({
                    "Blend ID": blend_id,
                    "Origin": row["Origin"],
                    "Start Datetime": new_start_time,
                    "Duration (hrs)": min_duration,
                    "End Datetime": new_end_time,
                    "Feed Tonnes": total_feed_tonnes,
                    **weighted_grades
                })

                # Update time tracking
                current_time = new_end_time
                remaining_duration -= min_duration

                # Remove fully depleted hexes from their stockpile lists
                for footprint, hex_list in stockpile_hex_groups.items():
                    if hex_list:
                        # Get the reclaim rate for this footprint
                        source_reclaim_rate = source_reclaim_rates.get(footprint, 0)
                        depletion_amount = source_reclaim_rate * min_duration  # Calculate depletion for this stockpile
                        
                        current_balance = max(float(hex_list[0].get("balance", 0) or 0), 0)

                        if current_balance > depletion_amount:
                            # If hex is NOT fully depleted, update its balance
                            hex_list[0]["balance"] = current_balance - depletion_amount
                        else:
                            # If fully depleted, remove the hex from the list
                            hex_list.pop(0)

        # Convert new records to DataFrame
        if new_records:
            new_df = pd.DataFrame(new_records)
            if df.empty:
                df = new_df
            else:
                df = pd.concat([df, new_df], ignore_index=True)

        # Re-sort the data
        df = df.sort_values(by="Start Datetime").reset_index(drop=True)

        # Prepare records for plotting
        records = []
        for _, row in df.iterrows():
            for grade in grade_columns:
                value = pd.to_numeric(row[grade], errors="coerce")
                if pd.isna(value):
                    continue
                records.append({"time": row["Start Datetime"], "grade": value, "element": grade})
                records.append({"time": row["End Datetime"], "grade": value, "element": grade})

        transformed_df = pd.DataFrame(records)
        if transformed_df.empty:
            return pd.DataFrame(columns=["time", "grade", "element"])

        transformed_df["time"] = pd.to_datetime(transformed_df["time"])

        return transformed_df


    def update_charts(self, data):
        """Generate separate charts for each grade dynamically."""
        df = pd.DataFrame(data)

        # Columns for grades
        grade_columns = ['Grade Fe', 'Grade Si', 'Grade Al', 'Grade P', 'Grade Mn']
        missing_grade_columns = [column for column in grade_columns if column not in df.columns]
        if df.empty or missing_grade_columns:
            return [html.Div("No manual blend grade data available.")]

        grade_data_presence = df[grade_columns].replace("", pd.NA).notna().any(axis=1)
        df = df[grade_data_presence].copy()
        if df.empty:
            return [html.Div("No manual blend grade data available.")]

        colors = {
            'Grade Fe': 'rgb(77, 148, 204)',
            'Grade Si': 'rgb(50, 200, 50)',
            'Grade Al': 'rgb(255, 255, 51)',
            'Grade P': 'rgb(160, 80, 160)',
            'Grade Mn': 'rgb(255, 160, 100)',
        }
        
        # Transform the data
        transformed_df = self.transform_data(df, grade_columns, self.hex_sequence_table, self.updated_stockpile_data)
        if transformed_df.empty:
            return [html.Div("No manual blend grade data available.")]
        
        # Create separate charts for each grade
        charts = []
        for grade in grade_columns:
            truncated_title = grade.split()[-1]  # Splits the string and takes the last part
            grade_data = transformed_df[transformed_df['element'] == grade]
            fig = px.line(
                grade_data,
                x='time',
                y='grade',
                title=f'{truncated_title} Grade Profile',
                labels={'time': 'Time', 'grade': grade},
                color_discrete_sequence=[colors[grade]]
            )
            fig.update_traces(mode='lines')
            fig.update_layout(hovermode="x unified")
            fig.update_yaxes(type="linear", **grade_profile_y_axis_settings(grade_data["grade"]))
            charts.append(html.Div(dcc.Graph(figure=fig), style={'margin-bottom': '20px'}))

        return charts

    def update_data(self, new_data):
        """Update the DataFrame and refresh charts."""
        self.df = new_data
        self.app.layout.children[0].data = self.df.to_dict('records')  # Update the stored data
    
    def run_app(self):
        """Run the Dash app."""
        self.app.run_server(port=self.port, debug=True, use_reloader=False)

class DrawOptimisedGradeProfiles:
    def __init__(self, db_path, port):
        self.db_path = db_path
        self.port = port
        self.app = dash.Dash(__name__)
        self.df = self.fetch_data()
        self.app.layout = html.Div(id='main-container', children=[
            dcc.Store(id='df-store', data=self.df.to_dict('records')),
            html.Div(id='charts-container')
        ])
        self.app.callback(
            Output('charts-container', 'children'),
            Input('df-store', 'data')
        )(self.update_charts)
        self.setup_routes()

    def setup_routes(self):
        @self.app.server.route("/trigger-refresh", methods=["POST"])
        def trigger_refresh():
            self.df = self.fetch_data()
            self.app.layout.children[0].data = self.df.to_dict('records')
            return ("", 204)

    def fetch_data(self):
        try:
            conn = sqlite3.connect(self.db_path)
            data = pd.read_sql("SELECT * FROM optimised_blend_report", conn)
            conn.close()
            return data
        except Exception as e:
            print(f"Error fetching optimised grade profile data: {e}")
            return pd.DataFrame()

    def fetch_product_build_data(self):
        try:
            conn = sqlite3.connect(self.db_path)
            data = pd.read_sql("SELECT * FROM product_build_report", conn)
            conn.close()
            return data
        except Exception as e:
            print(f"Error fetching product build grade profile data: {e}")
            return pd.DataFrame()

    def transform_data(self, df, grade_columns):
        if df.empty:
            return pd.DataFrame(columns=["time", "grade", "element"])

        required_columns = [
            "steady_state_number", "start_datetime", "end_datetime",
            "crusher_actual_grade_fe", "crusher_actual_grade_si",
            "crusher_actual_grade_al", "crusher_actual_grade_p",
            "crusher_actual_grade_mn"
        ]
        missing_columns = [column for column in required_columns if column not in df.columns]
        if missing_columns:
            print(f"Optimised grade profile data missing columns: {missing_columns}")
            return pd.DataFrame(columns=["time", "grade", "element"])

        df = df.copy()
        df["start_datetime"] = pd.to_datetime(df["start_datetime"], errors="coerce")
        df["end_datetime"] = pd.to_datetime(df["end_datetime"], errors="coerce")
        df = df.dropna(subset=["start_datetime", "end_datetime"])
        df = df.drop_duplicates(subset=["steady_state_number", "start_datetime", "end_datetime"])
        df = df.sort_values("start_datetime")

        grade_mapping = {
            "Grade Fe": "crusher_actual_grade_fe",
            "Grade Si": "crusher_actual_grade_si",
            "Grade Al": "crusher_actual_grade_al",
            "Grade P": "crusher_actual_grade_p",
            "Grade Mn": "crusher_actual_grade_mn",
        }

        records = []
        for _, row in df.iterrows():
            for grade in grade_columns:
                value = pd.to_numeric(row[grade_mapping[grade]], errors="coerce")
                if pd.isna(value):
                    continue
                records.append({
                    "time": row["start_datetime"],
                    "grade": value,
                    "element": grade,
                    "series": "Crusher Feed",
                    "steady_state_number": row.get("steady_state_number"),
                    "target_min": row.get(f"crusher_grade_target_min_{grade.split()[-1].lower()}", 0),
                    "target_max": row.get(f"crusher_grade_target_max_{grade.split()[-1].lower()}", 100),
                    "tonnes": row.get("crusher_actual_tonnes", 0),
                })
                records.append({
                    "time": row["end_datetime"],
                    "grade": value,
                    "element": grade,
                    "series": "Crusher Feed",
                    "steady_state_number": row.get("steady_state_number"),
                    "target_min": row.get(f"crusher_grade_target_min_{grade.split()[-1].lower()}", 0),
                    "target_max": row.get(f"crusher_grade_target_max_{grade.split()[-1].lower()}", 100),
                    "tonnes": row.get("crusher_actual_tonnes", 0),
                })

        transformed_df = pd.DataFrame(records)
        if transformed_df.empty:
            return pd.DataFrame(columns=["time", "grade", "element"])

        transformed_df["time"] = pd.to_datetime(transformed_df["time"])
        return transformed_df

    def transform_product_build_data(self, df, grade_columns, crusher_transformed_df=None):
        output_columns = [
            "time", "grade", "element", "series", "steady_state_number",
            "target_min", "target_max", "tonnes", "target_tonnes",
        ]
        if df.empty:
            return pd.DataFrame(columns=output_columns)

        required_columns = [
            "product_build_id", "product_build_name", "steady_state_number",
            "steady_state_start_datetime", "steady_state_end_datetime",
            "build_closing_tonnes", "target_tonnes",
            "build_grade_fe", "build_grade_si", "build_grade_al", "build_grade_p", "build_grade_mn",
        ]
        missing_columns = [column for column in required_columns if column not in df.columns]
        if missing_columns:
            print(f"Product build grade profile data missing columns: {missing_columns}")
            return pd.DataFrame(columns=output_columns)

        df = df.copy()
        df["steady_state_start_datetime"] = pd.to_datetime(df["steady_state_start_datetime"], errors="coerce")
        df["steady_state_end_datetime"] = pd.to_datetime(df["steady_state_end_datetime"], errors="coerce")
        for column in [
            "build_opening_tonnes", "build_added_tonnes", "build_closing_tonnes", "target_tonnes",
            "build_grade_fe", "build_grade_si", "build_grade_al", "build_grade_p", "build_grade_mn",
            "target_fe_min", "target_fe_max", "target_si_min", "target_si_max",
            "target_al_min", "target_al_max", "target_p_min", "target_p_max",
            "target_mn_min", "target_mn_max",
        ]:
            if column not in df.columns:
                df[column] = 0
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

        event_columns = [
            "product_build_lane", "product_build_id", "product_build_name",
            "steady_state_number",
            "steady_state_start_datetime", "steady_state_end_datetime", "blend_ID",
        ]
        for column in event_columns:
            if column not in df.columns:
                df[column] = (
                    "product" if column == "product_build_lane" else ""
                )
        aggregation = {
            "build_opening_tonnes": "first",
            "build_added_tonnes": "first",
            "build_closing_tonnes": "first",
            "target_tonnes": "first",
        }
        for grade_key in ["fe", "si", "al", "p", "mn"]:
            aggregation[f"build_grade_{grade_key}"] = "first"
            aggregation[f"target_{grade_key}_min"] = "first"
            aggregation[f"target_{grade_key}_max"] = "first"

        events = (
            df.dropna(subset=["steady_state_start_datetime", "steady_state_end_datetime"])
            .groupby(event_columns, dropna=False, as_index=False)
            .agg(aggregation)
            .sort_values([
                "product_build_lane", "steady_state_start_datetime",
                "product_build_id", "steady_state_end_datetime",
            ])
        )
        if "build_added_tonnes" in events.columns:
            events = events[events["build_added_tonnes"] > 0].copy()
        if events.empty:
            return pd.DataFrame(columns=output_columns)

        grade_mapping = {
            "Grade Fe": "fe",
            "Grade Si": "si",
            "Grade Al": "al",
            "Grade P": "p",
            "Grade Mn": "mn",
        }

        crusher_points_by_grade = {}
        if crusher_transformed_df is not None and not crusher_transformed_df.empty:
            crusher_data = crusher_transformed_df.copy()
            crusher_data["time"] = pd.to_datetime(crusher_data["time"], errors="coerce")
            crusher_data = crusher_data.dropna(subset=["time"])
            if not crusher_data.empty:
                for grade in grade_columns:
                    grade_key = grade_mapping[grade]
                    grade_rows = (
                        crusher_data[crusher_data["element"] == grade]
                        .sort_values("time", kind="mergesort")
                        .reset_index(drop=True)
                    )
                    if not grade_rows.empty:
                        crusher_points_by_grade[grade_key] = grade_rows

        def crusher_grade_at(grade_key, when, fallback):
            if grade_key not in crusher_points_by_grade or pd.isna(when):
                return fallback
            grade_rows = crusher_points_by_grade[grade_key]
            exact_rows = grade_rows[grade_rows["time"] == when]
            if not exact_rows.empty:
                return exact_rows.iloc[-1]["grade"]
            prior_rows = grade_rows[grade_rows["time"] <= when]
            if not prior_rows.empty:
                return prior_rows.iloc[-1]["grade"]
            return fallback

        build_order = (
            events.groupby(
                ["product_build_lane", "product_build_id"],
                dropna=False,
            )
            .agg(
                first_start=("steady_state_start_datetime", "min"),
                first_end=("steady_state_end_datetime", "min"),
            )
            .reset_index()
            .sort_values([
                "product_build_lane", "first_start", "first_end",
                "product_build_id",
            ])
        )

        records = []
        previous_end_time_by_lane = {}
        previous_end_grades_by_lane = {}

        for _, build_row in build_order.iterrows():
            build_lane = str(
                build_row.get("product_build_lane") or "product"
            ).strip().lower()
            build_id = build_row["product_build_id"]
            build_events = (
                events[
                    (events["product_build_lane"].astype(str).str.lower()
                     == build_lane)
                    & (events["product_build_id"] == build_id)
                ]
                .sort_values(["steady_state_start_datetime", "steady_state_end_datetime"])
                .reset_index(drop=True)
            )
            if build_events.empty:
                continue

            first_event = build_events.iloc[0]
            last_event = build_events.iloc[-1]
            build_name = str(first_event.get("product_build_name") or f"Build {build_id}")
            lane_label = (
                build_lane.title()
                if build_lane in {"lump", "fines"}
                else "Product"
            )
            series_name = f"{lane_label} Build: {build_name}"
            first_start_time = first_event["steady_state_start_datetime"]
            continuity_tolerance = pd.Timedelta(seconds=1)
            previous_end_time = previous_end_time_by_lane.get(build_lane)
            previous_end_grades = previous_end_grades_by_lane.get(
                build_lane, {}
            )
            use_handoff = False
            if previous_end_time is not None and pd.notna(first_start_time):
                gap = first_start_time - previous_end_time
                use_handoff = abs(gap) <= continuity_tolerance
            segment_start_time = previous_end_time if use_handoff else first_start_time

            for grade in grade_columns:
                grade_key = grade_mapping[grade]
                if use_handoff:
                    start_grade = previous_end_grades.get(grade_key, first_event.get(f"build_grade_{grade_key}", 0))
                else:
                    start_grade = crusher_grade_at(
                        grade_key,
                        segment_start_time,
                        first_event.get(f"build_grade_{grade_key}", 0),
                    )

                records.append({
                    "time": segment_start_time,
                    "grade": start_grade,
                    "element": grade,
                    "series": series_name,
                    "steady_state_number": first_event.get("steady_state_number"),
                    "target_min": first_event.get(f"target_{grade_key}_min", 0),
                    "target_max": first_event.get(f"target_{grade_key}_max", 100),
                    "tonnes": first_event.get("build_opening_tonnes", 0),
                    "target_tonnes": first_event.get("target_tonnes", 0),
                })

                for _, row in build_events.iterrows():
                    records.append({
                        "time": row["steady_state_end_datetime"],
                        "grade": row.get(f"build_grade_{grade_key}", 0),
                        "element": grade,
                        "series": series_name,
                        "steady_state_number": row.get("steady_state_number"),
                        "target_min": row.get(f"target_{grade_key}_min", 0),
                        "target_max": row.get(f"target_{grade_key}_max", 100),
                        "tonnes": row.get("build_closing_tonnes", 0),
                        "target_tonnes": row.get("target_tonnes", 0),
                    })

            previous_end_time_by_lane[build_lane] = (
                last_event["steady_state_end_datetime"]
            )
            previous_end_grades_by_lane[build_lane] = {
                grade_key: last_event.get(f"build_grade_{grade_key}", 0)
                for grade_key in ["fe", "si", "al", "p", "mn"]
            }

        return pd.DataFrame(records, columns=output_columns)

    def update_charts(self, data):
        df = pd.DataFrame(data)
        grade_columns = ['Grade Fe', 'Grade Si', 'Grade Al', 'Grade P', 'Grade Mn']
        colors = {
            'Grade Fe': 'rgb(77, 148, 204)',
            'Grade Si': 'rgb(50, 200, 50)',
            'Grade Al': 'rgb(255, 255, 51)',
            'Grade P': 'rgb(160, 80, 160)',
            'Grade Mn': 'rgb(255, 160, 100)',
        }

        transformed_df = self.transform_data(df, grade_columns)
        product_build_df = self.fetch_product_build_data()
        product_transformed_df = self.transform_product_build_data(product_build_df, grade_columns, transformed_df)
        if transformed_df.empty and product_transformed_df.empty:
            return [html.Div("No optimised grade profile data available.")]

        charts = []
        for grade in grade_columns:
            truncated_title = grade.split()[-1]
            crusher_grade_data = transformed_df[transformed_df['element'] == grade].copy()
            product_grade_data = product_transformed_df[product_transformed_df['element'] == grade].copy()

            fig = go.Figure()
            if not crusher_grade_data.empty:
                fig.add_trace(go.Scatter(
                    x=crusher_grade_data["time"],
                    y=crusher_grade_data["grade"],
                    name="Crusher Feed",
                    mode="lines",
                    line=dict(color=colors[grade], width=2.4, shape="hv"),
                    customdata=crusher_grade_data[[
                        "steady_state_number", "target_min", "target_max", "tonnes"
                    ]].to_numpy(dtype=object),
                    hovertemplate=(
                        "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                        f"{truncated_title}: " + "%{y:.2f}%<br>"
                        "Target: %{customdata[1]:.2f}-%{customdata[2]:.2f}%<br>"
                        "Steady State: %{customdata[0]}<br>"
                        "Crusher Tonnes: %{customdata[3]:,.2f} WMT<extra></extra>"
                    ),
                ))

            build_palette = [
                "#82C4A2", "#7FB3D5", "#F3B56B", "#B59EDB", "#E68A92",
                "#8ECAD1", "#D6B56D", "#A5B4FC",
            ]
            for index, (series, series_data) in enumerate(product_grade_data.groupby("series", sort=False)):
                fig.add_trace(go.Scatter(
                    x=series_data["time"],
                    y=series_data["grade"],
                    name=series,
                    mode="lines",
                    line=dict(color=build_palette[index % len(build_palette)], width=2, dash="dot"),
                    customdata=series_data[[
                        "steady_state_number", "target_min", "target_max", "tonnes", "target_tonnes"
                    ]].to_numpy(dtype=object),
                    hovertemplate=(
                        "<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                        f"{truncated_title}: " + "%{y:.2f}%<br>"
                        "Target: %{customdata[1]:.2f}-%{customdata[2]:.2f}%<br>"
                        "Steady State: %{customdata[0]}<br>"
                        "Build Tonnes: %{customdata[3]:,.2f} / %{customdata[4]:,.2f} WMT<extra></extra>"
                    ),
                ))

            fig.update_layout(
                title=f"{truncated_title} Grade Profile",
                xaxis_title="Time",
                yaxis_title=grade,
                paper_bgcolor="#ffffff",
                plot_bgcolor="#eef4fb",
                font=dict(family="Segoe UI, Arial, sans-serif", size=12, color="#1f2937"),
                hoverlabel=dict(
                    bgcolor="rgba(15, 23, 42, 0.96)",
                    bordercolor="#94a3b8",
                    font=dict(color="#f8fafc", family="Segoe UI", size=12),
                ),
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=60, r=24, t=54, b=46),
            )
            fig.update_xaxes(showgrid=True, gridcolor="rgba(148, 163, 184, 0.30)", zeroline=False)
            fig.update_yaxes(
                type="linear",
                # Keep the Optimised chart on the same basis as Manual:
                # selected-stream Crusher Feed grades.  Product Build is an
                # additional cumulative series and must not alter that scale.
                **grade_profile_y_axis_settings(crusher_grade_data["grade"]),
                showgrid=True,
                gridcolor="rgba(148, 163, 184, 0.35)",
                zeroline=False,
            )
            charts.append(html.Div(
                dcc.Graph(figure=fig, config={"displayModeBar": False, "responsive": True}),
                style={
                    'margin-bottom': '16px',
                    'backgroundColor': '#ffffff',
                    'border': '1px solid #dbe4ee',
                    'borderRadius': '8px',
                    'boxShadow': '0 8px 22px rgba(15, 23, 42, 0.06)',
                    'padding': '8px',
                },
            ))

        return charts

    def run_app(self):
        self.app.run_server(port=self.port, debug=True, use_reloader=False)

