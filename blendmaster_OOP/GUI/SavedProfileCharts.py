"""Build/depletion review for either saved result type and every named plan."""
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State
from classes.SavedResultViews import read_reports, plan_names


def balance_points(feed, build):
    rows = []
    if not build.empty and 'stockpile' in build:
        for row in build.to_dict('records'):
            rows.append(dict(source=str(row['stockpile']), time=row.get('time', row.get('delivered_datetime')),
                             balance=row.get('closing_balance'), kind='Build' if row.get('payload') else 'Physical balance'))
    for row in feed.to_dict('records'):
        name = str(row.get('source') or '')
        if not name or row.get('source_type') in ('transport', 'transport_idle'):
            continue
        parent = row.get('parent_stockpile')
        if pd.notna(parent) and parent and parent != name:
            name = str(parent) + ' / ' + name
        rows.extend([dict(source=name, time=row.get('start_datetime'), balance=row.get('source_opening_balance'), kind='Opening'),
                     dict(source=name, time=row.get('end_datetime'), balance=row.get('source_closing_balance'), kind='Closing')])
    frame = pd.DataFrame(rows, columns=['source', 'time', 'balance', 'kind'])
    frame['time'] = pd.to_datetime(frame['time'], errors='coerce')
    frame['balance'] = pd.to_numeric(frame['balance'], errors='coerce')
    return frame.dropna(subset=['time', 'balance']).drop_duplicates(['source', 'time', 'balance']).sort_values('time', kind='stable')


def styled(figure, title, unit):
    figure.update_layout(title=title, template='plotly_white', height=310, margin=dict(l=65,r=25,t=50,b=40),
                         font=dict(family='Segoe UI', size=12), yaxis_title=unit, hovermode='x unified',
                         yaxis=dict(rangemode='tozero'),
                         legend=dict(orientation='h', y=1.1), xaxis=dict(title='AWST', tickformat='%d %b %H:%M'))
    return figure


def overview_figures(feed, product):
    figures = []
    keys = [key for key in ('opf', 'tipping_point') if key in feed]
    groups = feed.groupby(keys, dropna=False, sort=False) if keys else [('Crusher', feed)]
    for point, rows in groups:
        if rows.empty or 'crusher_rate_output' not in rows:
            continue
        keys = [key for key in ('start_datetime', 'end_datetime', 'steady_state_number') if key in rows]
        rows = rows.drop_duplicates(keys).sort_values('start_datetime')
        x, y = [], []
        for row in rows.to_dict('records'):
            x.extend([row['start_datetime'], row['end_datetime'], None])
            y.extend([row.get('crusher_rate_output'), row.get('crusher_rate_output'), None])
        label = ' / '.join(map(str, point)) if isinstance(point, tuple) else str(point)
        figures.append(styled(go.Figure(go.Scatter(x=x, y=y, mode='lines', name='Crusher feed')), label + ' — feed', 'ROM t/h'))
    keys = [key for key in ('opf', 'product_build_lane', 'product_build_id') if key in product]
    if not product.empty and keys:
        for identity, rows in product.groupby(keys, dropna=False, sort=False):
            rows = rows.sort_values('steady_state_end_datetime')
            x, y = [], []
            for row in rows.to_dict('records'):
                x.extend([row.get('steady_state_start_datetime'), row.get('steady_state_end_datetime')])
                y.extend([row.get('build_opening_tonnes'), row.get('build_closing_tonnes')])
            label = str(rows.iloc[0].get('product_build_name') or identity)
            figure = go.Figure(go.Scatter(x=x, y=y, name='Product build', mode='lines+markers'))
            target = pd.to_numeric(rows.get('target_tonnes'), errors='coerce').dropna() if 'target_tonnes' in rows else pd.Series(dtype=float)
            if not target.empty and float(target.iloc[0]) > 0:
                figure.add_hline(y=float(target.iloc[0]), line_dash='dash', annotation_text='Build target')
            figures.append(styled(figure, label + ' — product build', 'Product tonnes'))
    return figures


class SavedProfileCharts:
    def __init__(self, database, port):
        self.db_path, self.port = database, port
        self.app = Dash(__name__)
        self.app.layout = self.layout
        self.app.callback(Output('profile-plan','options'), Output('profile-plan','value'),
                          Input('profile-type','value'), State('profile-plan','value'))(self.plans)
        self.app.callback(Output('profile-graphs','children'), Output('profile-source','options'),
                          Input('profile-type','value'), Input('profile-plan','value'),
                          Input('profile-source','value'), Input('profile-page','value'))(self.charts)

    def layout(self):
        default = 'optimised' if plan_names(self.db_path, 'optimised') else 'manual'
        return html.Div([
            html.H3('Build and Depletion Profiles'),
            dcc.Dropdown(id='profile-type', options=[dict(label='Optimised',value='optimised'), dict(label='Manual',value='manual')],
                         value=default, clearable=False, style={'width':'180px'}),
            dcc.Dropdown(id='profile-plan', options=[], clearable=False, style={'maxWidth':'360px','margin':'10px 0'}),
            html.Div([dcc.Dropdown(id='profile-source', placeholder='All sources — 12 per page', style={'width':'460px'}),
                      html.Label([' Page ', dcc.Input(id='profile-page', type='number', value=1, min=1, step=1, style={'width':'60px'})])],
                     style={'display':'flex','gap':'16px','alignItems':'center'}),
            html.Div(id='profile-graphs'),
        ], style={'fontFamily':'Segoe UI','padding':'12px'})

    def plans(self, kind, selected):
        names = plan_names(self.db_path, kind)
        return [dict(label=n,value=n) for n in names], selected if selected in names else next(iter(names), None)

    def charts(self, kind, plan, source, page):
        if not plan:
            return html.P('No saved ' + kind + ' results.'), []
        frames = read_reports(self.db_path, kind, plan)
        points = balance_points(frames['feed'], frames['build'])
        names = sorted(points['source'].unique())
        options = [dict(label=name,value=name) for name in names]
        selected = [source] if source in names else names[(max(1,int(page or 1))-1)*12:max(1,int(page or 1))*12]
        figures = overview_figures(frames['feed'], frames['product'])
        for name in selected:
            rows = points[points['source'] == name]
            figure = go.Figure(go.Scatter(x=rows['time'], y=rows['balance'], text=rows['kind'], mode='lines+markers',
                                         name='Balance', line=dict(color='#27799a'), hovertemplate='%{text}: %{y:,.2f} WMT<extra></extra>'))
            figures.append(styled(figure, name + ' — balance', 'ROM WMT'))
        graphs = [dcc.Graph(figure=figure, config={'displayModeBar':False, 'responsive':True}) for figure in figures]
        return [html.P(f'{kind.title()} / {plan} · {len(names)} sources. Values are from this saved calculation.'), *graphs], options

    def run_app(self):
        self.app.run_server(port=self.port, debug=False, use_reloader=False)
