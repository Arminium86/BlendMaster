import json
import unittest
from datetime import datetime, timedelta
import pandas as pd
from classes.PlanReadiness import evaluate, physical_violations
from classes.BlendSnapshot import selected_state
from GUI.MaterialFlowGraph import compact_sources


class PlanReadinessTests(unittest.TestCase):
    def report(self):
        return pd.DataFrame([dict(source='A', source_actual_tonnes=100, source_closing_balance=50,
            start_datetime='2026-09-12 06:00', end_datetime='2026-09-12 07:00',
            product_build_name='Build 1', product_build_closing_tonnes=80, steady_state_number=1)])

    def test_complete_run_does_not_hide_target_shortfall_or_missing_audits(self):
        result = evaluate(self.report(), [{'build_name':'Build 1', 'target_tonnes':100}], equipment_errors=[])
        self.assertEqual(result['status'], 'requires_review')
        checks = {r['check']: r for r in result['checks']}
        self.assertEqual(checks['Calculation']['status'], 'pass')
        self.assertIn('20.0', checks['Product targets']['detail'])
        self.assertEqual(checks['Product quality']['status'], 'not_checked')

    def test_unproduced_target_is_not_omitted(self):
        result = evaluate(self.report(), [{'build_name':'Missing build', 'target_tonnes':100}], equipment_errors=[])
        self.assertIn('100.0', next(r['detail'] for r in result['checks'] if r['check']=='Product targets'))

    def test_negative_inventory_and_nonfinite_tonnes_are_blocked(self):
        report = self.report(); report.loc[0, 'source_closing_balance']=-1
        self.assertTrue(physical_violations(report))
        report = self.report(); report['source_actual_tonnes'] = float('inf')
        self.assertTrue(physical_violations(report))

    def test_cumulative_policy_uses_final_state_but_steady_state_policy_keeps_breaches(self):
        report = pd.concat([self.report(), self.report()], ignore_index=True)
        report['start_datetime']=['2026-09-12 06:00','2026-09-12 07:00']
        report['end_datetime']=['2026-09-12 07:00','2026-09-12 08:00']
        report['steady_state_number']=[1,2]
        for grain, expected in [('cumulative_build','pass'), ('steady_state','review')]:
            report['product_build_quality_audit']=[json.dumps(dict(schema_version=1, rows=[dict(
                grain=grain, evaluation_basis=grain, analyte='p', build_name='Build 1',
                quality_status=status)])) for status in ('Hard limit breached','Within limits')]
            result=evaluate(report, equipment_errors=[])
            self.assertEqual(next(r['status'] for r in result['checks'] if r['check']=='Product quality'),expected)

    def test_click_selects_one_occurrence_when_the_same_blend_lane_repeats(self):
        data=pd.DataFrame([dict(steady_state_number=i, start_datetime=f'2026-09-12 0{i}:00',
            end_datetime=f'2026-09-12 0{i+1}:00', lane='Blend 4') for i in (1,2)])
        clicked={'points':[{'y':'Blend 4','customdata':[2,'2026-09-12 02:00','2026-09-12 03:00','Blend 4']}]}
        self.assertEqual(selected_state(data,clicked).steady_state_number.tolist(),[2])

    def test_browser_integer_identity_matches_saved_float_occurrence(self):
        data = pd.DataFrame([dict(steady_state_number=0.0, start_datetime='2026-08-18 19:51:49',
            end_datetime='2026-08-19 06:00', lane=1.0, source=name) for name in ('A', 'B')])
        click = {'points': [{'customdata': [0, '2026-08-18T19:51:49', '2026-08-19T06:00:00', 1]}]}
        self.assertEqual(selected_state(data, click).source.tolist(), ['A', 'B'])

    def test_compact_flow_preserves_routes_and_does_not_change_saved_topology(self):
        graph=dict(nodes=[dict(node_id=f'S{i}',node_type='source',label=f'Block {i}',properties={'source_type':'grade_block'}) for i in range(30)] +
                    [dict(node_id='PC',node_type='tipping_point',label='PC',properties={})],
                   edges=[dict(edge_id=str(i),source_node_id=f'S{i}',target_node_id='PC') for i in range(30)])
        compact=compact_sources(graph)
        self.assertEqual(len(compact['nodes']),2)
        self.assertEqual(len(compact['edges']),1)
        self.assertEqual(len(graph['nodes']),31)
        self.assertEqual(len(compact['nodes'][1]['properties']['members']),30)
