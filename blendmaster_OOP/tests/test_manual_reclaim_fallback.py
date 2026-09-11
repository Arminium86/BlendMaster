"""Reclaim-only retries and partial rounded plans use isolated opening inventory."""
import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch
import pickle
import pandas as pd
from classes.ManualBlendPlanner import ManualBlendPlanner, ManualBlendPlanningError
from classes.ManualRatioRounding import recalculate_rounded_plan, rounding_audit_rows
from tests import test_manual_ratio_rounding as fixture


class ReclaimFallbackTests(unittest.TestCase):
    def scenario(self):
        original, planner = fixture.scenario(True)
        original['source_actual_tonnes'] = [50, 30, 20]
        planner.payload_transactions = pd.DataFrame()
        return original, planner

    def test_even_percentage_points_and_recalculated_grade(self):
        original, planner = self.scenario()
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(set(report.source), {'SP1', 'SP2'})
        amounts = report.groupby('source').source_actual_tonnes.sum()
        self.assertAlmostEqual(amounts['SP1'], 60)
        self.assertAlmostEqual(amounts['SP2'], 40)
        self.assertAlmostEqual(report.iloc[0].crusher_actual_grade_fe, 59.2)
        self.assertEqual(transfer['blend_definitions'][0]['Source Ratios'], '0.600000, 0.400000')
        audit = rounding_audit_rows(transfer['sequence_rows'])
        grade_block = next(row for row in audit if row['Source'] == 'GB1')
        self.assertEqual(grade_block['Rounded ratio (%)'], 0)
        self.assertEqual(grade_block['Original ratio (%)'], 20)
        self.assertIn('Reclaim-only fallback', grade_block['Timing adjustment'])
        self.assertTrue(all(not values for values in allocations.values()))
        self.assertEqual(transfer['rounding_stop_reason'], '')
        restored = pickle.loads(pickle.dumps(transfer))
        replay = ManualBlendPlanner(restored['sequence_rows'], restored['blend_definitions'], planner.stockpile_data, [], None, {}, [], 100)
        replayed = replay.build_report(replay.build_steady_states(), {})
        self.assertEqual(replayed.source_actual_tonnes.tolist(), report.source_actual_tonnes.tolist())
        self.assertEqual(rounding_audit_rows(restored['sequence_rows']), audit)

    def test_added_share_recalculates_depletion_and_following_recipe(self):
        original, planner = self.scenario()
        original['source_closing_balance'] = [0, 970, 0]
        planner._inventory_template['SP1'][0]['balance'] = 30
        planner.stockpile_data['SP1']['balance'] = 30
        f = fixture.fixture.OptimisedManualPrepopulationTests()
        original = pd.concat([original, pd.DataFrame([f.report_row(2, 'SP2', 100)])], ignore_index=True)
        _, states, _, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(states[0]['end_datetime'], datetime(2025, 1, 1, 6, 30))
        self.assertEqual(states[1]['start_datetime'], datetime(2025, 1, 1, 6, 30))
        self.assertEqual(states[-1]['end_datetime'], datetime(2025, 1, 1, 8))
        self.assertAlmostEqual(report.loc[report.source == 'SP1', 'source_actual_tonnes'].sum(), 30)
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 200)

    def test_equal_split_is_rounded_again_before_recalculation(self):
        original, planner = self.scenario()
        original['source_actual_tonnes'] = [40, 30, 10]
        f = fixture.fixture.OptimisedManualPrepopulationTests()
        original = pd.concat([original, pd.DataFrame([f.report_row(1, 'SP3', 20)])], ignore_index=True)
        planner.stockpile_data['SP3'] = deepcopy(planner.stockpile_data['SP2'])
        planner._inventory_template['SP3'] = deepcopy(planner._inventory_template['SP2'])
        transfer, _, _, report = recalculate_rounded_plan(original, planner)
        amounts = report.groupby('source').source_actual_tonnes.sum()
        for source, expected in [('SP1', 45), ('SP2', 35), ('SP3', 20)]:
            self.assertAlmostEqual(amounts[source], expected)
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 100)
        self.assertIn('split evenly', rounding_audit_rows(transfer['sequence_rows'])[0]['Timing adjustment'])

    def test_52_5_47_5_becomes_55_45_and_controls_depletion_and_saved_report(self):
        original, planner = self.scenario()
        # The report's source order is retained: DT 15%, SP1 42.5%, SP2 42.5%.
        original['source_actual_tonnes'] = [42.5, 42.5, 15]
        original = original.iloc[[2, 0, 1]].reset_index(drop=True)
        planner.stockpile_data['SP1']['balance'] = 27.5
        planner._inventory_template['SP1'][0]['balance'] = 27.5
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner, 5)
        self.assertEqual(transfer['blend_definitions'][0]['Source Ratios'], '0.550000, 0.450000')
        self.assertEqual(states[-1]['end_datetime'], datetime(2025, 1, 1, 6, 30))
        amounts = report.groupby('source').source_actual_tonnes.sum()
        self.assertAlmostEqual(amounts['SP1'], 27.5)
        self.assertAlmostEqual(amounts['SP2'], 22.5)
        self.assertAlmostEqual(report.iloc[0].crusher_actual_grade_fe, 59.1)
        self.assertTrue(all(not values for values in allocations.values()))
        audit = rounding_audit_rows(transfer['sequence_rows'])
        self.assertEqual({row['Source']: round(row['Rounded ratio (%)'], 6) for row in audit},
                         {'SP1': 55, 'SP2': 45, 'GB1': 0})
        self.assertIn('rounded again to 5%', audit[0]['Timing adjustment'])
        restored = pickle.loads(pickle.dumps(transfer))
        replay = ManualBlendPlanner(restored['sequence_rows'], restored['blend_definitions'],
                                    planner.stockpile_data, [], None, {}, [], 100)
        replayed = replay.build_report(replay.build_steady_states(), {})
        self.assertEqual(replayed.source_actual_tonnes.tolist(), report.source_actual_tonnes.tolist())

    def test_reclaim_fallback_respects_each_supported_increment(self):
        for increment in (1, 2, 5, 10, 20, 25, 50):
            original, planner = self.scenario()
            original['source_actual_tonnes'] = [35, 30, 35]
            transfer, _, _, report = recalculate_rounded_plan(original, planner, increment)
            shares = [row['Rounded ratio (%)'] for row in rounding_audit_rows(transfer['sequence_rows'])]
            self.assertAlmostEqual(sum(shares), 100)
            for share in shares:
                self.assertAlmostEqual(share / increment, round(share / increment))
            self.assertNotIn('GB1', set(report.source))

    def test_failed_next_recipe_preserves_prefix_and_omits_later_recipes(self):
        original, planner = self.scenario()
        original = original[original.source.eq('SP1')].copy()
        original['source_actual_tonnes'] = 100
        f = fixture.fixture.OptimisedManualPrepopulationTests()
        original = pd.concat([original, pd.DataFrame([
            f.report_row(2, 'SP2', 80), f.report_row(2, 'GB1', 20, 'grade_block'),
            f.report_row(3, 'SP1', 100)])], ignore_index=True)
        planner._inventory_template['SP2'][0]['balance'] = 0
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]['end_datetime'], datetime(2025, 1, 1, 7))
        self.assertEqual(set(report.source), {'SP1'})
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 100)
        self.assertIn('Rounded Blend 2', transfer['rounding_stop_reason'])
        self.assertIn('Reclaim-only fallback failed', transfer['rounding_stop_reason'])
        self.assertEqual(transfer['blend_count'], 1)
        self.assertEqual(transfer['rounding_unfilled_hours'], 2)
        audit = rounding_audit_rows(transfer['sequence_rows'])
        self.assertIn('Partial sequence', audit[0]['Sequence status'])
        self.assertIn('Partial sequence', report.iloc[0].rounding_stop_reason)

    def test_no_stockpile_sources_returns_previous_valid_blend(self):
        original, planner = self.scenario()
        original = original[original.source.eq('SP1')].copy()
        f = fixture.fixture.OptimisedManualPrepopulationTests()
        original = pd.concat([original, pd.DataFrame([f.report_row(2, 'GB1', 100, 'grade_block')])], ignore_index=True)
        transfer, states, _, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(len(states), 1)
        self.assertIn('no stockpile sources', transfer['rounding_stop_reason'])
        self.assertEqual(set(report.source), {'SP1'})

    def test_no_valid_first_blend_does_not_invent_a_sequence(self):
        original, planner = self.scenario()
        planner._inventory_template['SP1'][0]['balance'] = 0
        with self.assertRaisesRegex(ManualBlendPlanningError, 'No rounded states could be generated'):
            recalculate_rounded_plan(original, planner)

    def test_report_failure_also_retains_a_valid_prefix(self):
        original, planner = self.scenario()
        original = original[original.source.eq('SP1')].copy()
        f = fixture.fixture.OptimisedManualPrepopulationTests()
        original = pd.concat([original, pd.DataFrame([f.report_row(2, 'SP1', 100)])], ignore_index=True)
        build_report = planner.build_report
        def validate_prefix(states, allocations):
            if len(states) > 1:
                raise ManualBlendPlanningError('Invalid mapped report data in Blend 2')
            return build_report(states, allocations)
        with patch.object(planner, 'build_report', side_effect=validate_prefix):
            transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(len(states), 1)
        self.assertEqual(len(allocations), 1)
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 50)
        self.assertIn('Invalid mapped report data', transfer['rounding_stop_reason'])

    def test_retry_discards_partial_direct_tip_attempt_before_reclaiming(self):
        original, planner = fixture.scenario(True)
        planner.payload_transactions['payload'] = 5
        planner.product_build_settings = [{'target_tonnes': 25, 'brand': 'FB'}]
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        # The failed direct-tip attempt made 25 t before retrying. It must not
        # consume stock or advance product progress in the reclaim-only retry.
        self.assertEqual(states[0]['end_datetime'], datetime(2025, 1, 1, 6, 15))
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 100)
        self.assertEqual(set(report.source), {'SP1', 'SP2'})
        self.assertTrue(all(not values for values in allocations.values()))
        self.assertEqual([s['steady_state_number'] for s in states], [1, 2])
        self.assertEqual(transfer['rounding_stop_reason'], '')

    def test_last_fallback_retains_valid_states_within_failed_reclaim_recipe(self):
        original, planner = self.scenario()
        original['source_actual_tonnes'] = [40, 40, 20]
        first = deepcopy(planner._inventory_template['SP1'][0])
        first['balance'] = 25
        bad = deepcopy(first); bad['balance'] = 975; bad['bad_mapping'] = True
        planner._inventory_template['SP1'] = [first, bad]
        coefficient = planner._chunk_quantity_coefficient
        def read_coefficient(chunk, field, source):
            if chunk.get('bad_mapping'):
                raise ManualBlendPlanningError('Missing mapped quantity in next chunk')
            return coefficient(chunk, field, source)
        with patch.object(planner, '_chunk_quantity_coefficient', side_effect=read_coefficient):
            transfer, states, _, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(states[-1]['end_datetime'], datetime(2025, 1, 1, 6, 30))
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 50)
        self.assertEqual(set(report.source), {'SP1', 'SP2'})
        self.assertIn('Partial sequence', transfer['rounding_stop_reason'])
        self.assertIn('Missing mapped quantity', transfer['rounding_stop_reason'])


if __name__ == '__main__':
    unittest.main()
