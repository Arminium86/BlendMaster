import unittest
from datetime import datetime, timedelta
import pandas as pd
from classes.EquipmentLimits import equipment_violations
from classes.MultiFeedCalendar import calendar_key


class EquipmentLimitsTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 12, 17)
        self.periods = dict(preplan_start=self.start, preplan_end=self.start + timedelta(hours=1),
                            period_1_start=self.start + timedelta(hours=1), period_1_end=self.start + timedelta(hours=13))
        self.calendar = {'crusher_rate': {'Preplan': 6000, 'Period_1': 4000},
                         'reclaim_equipment_max_reclaim_rate': {'Preplan': 2000, 'Period_1': 1000}}

    def report(self, rates, hours=1):
        return pd.DataFrame([dict(start_datetime=self.start, end_datetime=self.start + timedelta(hours=hours),
                                 steady_state_duration=hours, steady_state_number=0, blend_ID='1',
                                 source=name, source_type='stockpile', source_actual_tonnes=rate * hours)
                             for name, rate in rates.items()])

    def test_lower_manual_rates_and_numeric_tolerance_are_allowed(self):
        self.assertEqual(equipment_violations(self.report({'A': 2000.000079, 'B': 1900}), self.calendar, self.periods), [])

    def test_rounded_source_rate_is_blocked_even_when_crusher_total_fits(self):
        errors = equipment_violations(self.report({'A': 2259.4, 'B': 1900}), self.calendar, self.periods)
        self.assertEqual(len(errors), 1)
        self.assertIn('A', errors[0])
        self.assertIn('2,000.0000', errors[0])

    def test_period_crossing_checks_each_limit(self):
        errors = equipment_violations(self.report({'A': 1500}, hours=2), self.calendar, self.periods)
        self.assertIn('period_1', errors[0])

    def test_multi_point_reclaim_is_aggregate(self):
        report = self.report({'A': 1500, 'B': 1500}); report['tipping_point'] = 'PC2'
        calendar = {calendar_key('PC2', 'crusher_rate'): {'Preplan': 6000},
                    calendar_key('PC2', 'max_reclaim_rate'): {'Preplan': 2000}}
        errors = equipment_violations(report, calendar, self.periods, mode='multi_tipping_point')
        self.assertIn('aggregate reclaim', errors[0])

    def test_missing_calendar_is_not_publication_ready(self):
        errors = equipment_violations(self.report({'A': 1000}), {}, self.periods, require_limits=True)
        self.assertTrue(errors)

    def test_chunk_rows_for_one_reclaimer_are_combined(self):
        report = self.report({'chunk1': 1200, 'chunk2': 1200}); report['parent_stockpile'] = 'A'
        self.assertTrue(equipment_violations(report, self.calendar, self.periods))
