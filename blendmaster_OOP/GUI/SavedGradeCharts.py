"""Shared manual/optimised grade charts backed by immutable saved report rows."""
import threading
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output
from classes.ProductQualityLimits import quality_label
from classes.ProductBuildLanes import lane_kind
from classes.SavedResultViews import read_report, plan_names

ANALYTES = ('fe', 'si', 'al', 'p', 'mn')
COLORS = ('#1769aa', '#008577', '#8752a3', '#cf6b27', '#ac3f64', '#4f667a')


def number(value):
    value = pd.to_numeric(value, errors='coerce')
    return None if pd.isna(value) else float(value)


def grade_figures(feed, product):
    figures = []
    for a in ANALYTES:
        fig, axis_values = go.Figure(), []
        if not feed.empty and {'start_datetime', 'end_datetime'}.issubset(feed):
            frame = feed.copy()
            for key in ('opf', 'tipping_point'):
                if key not in frame:
                    frame[key] = ''
            for i, ((opf, point), rows) in enumerate(frame.groupby(['opf', 'tipping_point'], dropna=False, sort=False)):
                rows = rows.drop_duplicates(['steady_state_number', 'start_datetime', 'end_datetime']).sort_values('start_datetime')
                x, y = [], []
                for row in rows.to_dict('records'):
                    value = number(row.get('crusher_actual_grade_' + a))
                    if value is not None:
                        x.extend([row['start_datetime'], row['end_datetime'], None])
                        y.extend([value, value, None])
                        axis_values.append(value)
                label = ' / '.join(str(v) for v in (opf, point) if pd.notna(v) and str(v).strip()) or 'Crusher feed'
                fig.add_trace(go.Scatter(x=x, y=y, name=label, mode='lines',
                    line=dict(color=COLORS[i % len(COLORS)], width=2.2),
                    hovertemplate='%{x}<br>%{y:.4f}%<extra>%{fullData.name}</extra>'))
        if not product.empty:
            frame = product.copy()
            for key in ('opf', 'product_build_lane'):
                if key not in frame:
                    frame[key] = '' if key == 'opf' else 'product'
            keys = ['opf', 'product_build_lane', 'product_build_id']
            for i, (identity, rows) in enumerate(frame.groupby(keys, dropna=False, sort=False)):
                rows = rows.sort_values('steady_state_end_datetime').drop_duplicates(['steady_state_start_datetime', 'steady_state_end_datetime'])
                first = rows.iloc[0]
                lane = lane_kind(identity[1])
                lane_label = lane.title() if lane in ('lump', 'fines') else ''
                label = ' / '.join(str(v) for v in (identity[0], lane_label, first.get('product_build_name') or str(identity[2])) if pd.notna(v) and str(v).strip())
                values = pd.to_numeric(rows.get('build_grade_' + a, pd.Series(dtype=float)), errors='coerce')
                axis_values.extend(values.dropna().tolist())
                fig.add_trace(go.Scatter(x=rows['steady_state_end_datetime'], y=values,
                    name='Build: ' + label, mode='lines+markers', line=dict(width=2, dash='dot', color=COLORS[i % len(COLORS)]),
                    hovertemplate='%{x}<br>%{y:.4f}%<extra>%{fullData.name}</extra>'))
                for part, color, dash in (('lql', '#a33b16', 'dash'), ('target', '#334155', 'dot'), ('hql', '#15803d', 'dash')):
                    x, y = [], []
                    for row in rows.to_dict('records'):
                        value = number(row.get(f'target_{a}_{part}'))
                        if value is not None:
                            x.extend([row['steady_state_start_datetime'], row['steady_state_end_datetime'], None])
                            y.extend([value, value, None])
                            axis_values.append(value)
                    if x:
                        fig.add_trace(go.Scatter(x=x, y=y, name=f'{quality_label(a, part)} · {label}',
                            mode='lines', line=dict(color=color, dash=dash, width=1.5),
                            hovertemplate='%{x}<br>%{y:.4f}%<extra>%{fullData.name}</extra>'))
        title = {'fe': 'Fe', 'si': 'SiO₂', 'al': 'Al₂O₃', 'p': 'P', 'mn': 'Mn'}[a]
        fig.update_layout(title=title + ' grade', xaxis_title='Time (AWST)', yaxis_title='Grade (%)',
            template='plotly_white', hovermode='x unified', height=480,
            legend=dict(orientation='h', yanchor='top', y=-.35, font=dict(size=11)),
            margin=dict(l=60, r=25, t=50, b=180))
        fig.update_xaxes(title_standoff=12)
        fig.update_yaxes(tickformat='.4f' if a in ('p', 'mn') else '.2f')
        if axis_values:
            low, high = min(axis_values), max(axis_values)
            margin = max((high - low) * .08, .0001 if a in ('p', 'mn') else .02)
            fig.update_yaxes(range=[max(0, low-margin), high+margin])
        figures.append(fig)
    return figures


class SavedGradeCharts:
    def __init__(self, database, port, plan_type='optimised', plan_id='Primary'):
        self.db_path, self.port, self.plan_type, self.plan_id = database, port, plan_type, plan_id
        self.app = Dash(__name__)
        self.app.layout = self.layout
        self.app.callback(Output('saved-grade-charts', 'children'),
                          Input('saved-grade-plan', 'value'))(self.charts)

    def layout(self):
        names = plan_names(self.db_path, self.plan_type)
        selected = self.plan_id if self.plan_id in names else next(iter(names), None)
        return html.Div([
            html.Div(self.plan_type.title() + ' grade profiles', style={'fontSize': '20px', 'fontWeight': '600'}),
            dcc.Dropdown(id='saved-grade-plan', options=[dict(label=n, value=n) for n in names],
                         value=selected, clearable=False, style={'maxWidth': '360px', 'margin': '12px 0'}),
            html.Div(id='saved-grade-charts'),
        ], style={'fontFamily': 'Segoe UI', 'padding': '12px'})

    def charts(self, plan):
        if not plan:
            return html.Div('No saved ' + self.plan_type + ' results.')
        database = self.db_path
        from classes.SavedResultViews import read_reports
        frames = read_reports(database, self.plan_type, plan, ('feed', 'product'))
        feed, product = frames['feed'], frames['product']
        if feed.empty and product.empty:
            return html.Div('No saved ' + self.plan_type + ' results.')
        return [dcc.Graph(figure=fig, config={'displayModeBar': False, 'responsive': True})
                for fig in grade_figures(feed, product)]

    def update_data(self, _data=None):
        # The next page load reads the selected database/plan; data from a different
        # manual Gantt callback cannot replace the saved product-build evidence.
        pass

    def run_app(self):
        self.app.run_server(port=self.port, debug=False, use_reloader=False)
