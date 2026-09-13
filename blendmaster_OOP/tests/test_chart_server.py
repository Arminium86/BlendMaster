import json
import unittest
from types import SimpleNamespace
from urllib.request import urlopen
from dash import Dash, html, Input, Output
from GUI.ChartServer import start


class ChartServerTests(unittest.TestCase):
    def test_sessions_have_distinct_endpoints_and_independent_dash_callbacks(self):
        charts = []
        try:
            for label in ('support', 'planner'):
                app = Dash(__name__)
                app.layout = html.Div([html.Div(label, id='source'), html.Div(id='result')])
                app.callback(Output('result', 'children'), Input('source', 'children'))(lambda value: value)
                chart = SimpleNamespace(app=app, port=8054)
                chart.thread = start(chart)
                charts.append(chart)
            self.assertNotEqual(charts[0].port, charts[1].port)
            for chart, label in zip(charts, ('support', 'planner')):
                base = f'http://127.0.0.1:{chart.port}'
                with urlopen(base + '/_dash-layout', timeout=3) as response:
                    payload = json.load(response)
                self.assertEqual(payload['props']['children'][0]['props']['children'], label)
                from urllib.request import Request
                request = Request(base + '/_dash-update-component', data=json.dumps({
                    'output': 'result.children', 'outputs': {'id': 'result', 'property': 'children'},
                    'inputs': [{'id': 'source', 'property': 'children', 'value': label}],
                    'changedPropIds': ['source.children'], 'state': [],
                }).encode(), headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=3) as response:
                    result = json.load(response)
                self.assertEqual(result['response']['result']['children'], label)
        finally:
            for chart in charts:
                chart._http_server.shutdown()
                chart._http_server.server_close()
                chart.thread.join(timeout=3)
