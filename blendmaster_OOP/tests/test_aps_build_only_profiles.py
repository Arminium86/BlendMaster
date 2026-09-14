import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from copy import deepcopy
import pandas as pd

from execute.Run import Run, StockpileSelectionRunError
from classes.OPFSourceProfiles import prepare_inventory_profiles
from classes.PlanningPrerequisites import PlanningPrerequisiteError
from classes.GradeStreams import legacy_grade_streams
from tests.test_opf_source_profiles import configuration


class APSBuildOnlyProfileTests(unittest.TestCase):
    def fixture(self, balance=45000):
        run = Run.__new__(Run)
        run.gui = SimpleNamespace(stockpile_data={'HAL01_RP01_0304': {'BALANCE': balance, 'BUILD': 'HAL_01'}})
        run.case_bridge = SimpleNamespace(print=Mock())
        calendar = {}
        physical = {'SP1': {'balance': 100, 'build': 'B1'}}
        transactions = pd.DataFrame([{'destination': 'Stockpiles/HAL01_RP01_0304'}])
        rows = run._include_aps_destination_stockpiles(physical, calendar, transactions)
        config = configuration(('OPF1',))
        config['multi_feed_settings'] = dict(tipping_points=[dict(name='CR1', opf='OPF1', rom_area='CR1')],
                                              source_subsets={'SP1': 'CR1', 'HAL01_RP01_0304': 'CR1'}, rehandle_rules=[])
        config['opf_profiles']['OPF1'].update(inventory={'SP1': dict(balance=100, build='B1',
            grade_streams=legacy_grade_streams({'grade_fe': 58}))}, chunks={})
        return run, rows, calendar, config

    def test_added_aps_destinations_need_no_opening_feed_profile_and_keep_their_mass(self):
        for balance in (0, 45000):
            with self.subTest(balance=balance):
                run, rows, calendar, config = self.fixture(balance)
                config['aps_build_only_sources'] = run._validated_build_only_sources(rows, calendar)
                prepare_inventory_profiles(rows, [], config)
                self.assertEqual(rows['HAL01_RP01_0304']['balance'], balance)
                self.assertTrue(all(v == 'Build' for v in calendar['stockpiles_hal01_rp01_0304_state'].values()))
                self.assertNotIn('HAL01_RP01_0304', config['opf_profiles']['OPF1']['inventory'])
                self.assertTrue(rows['SP1']['source_properties'])

    def test_reclaiming_an_unselected_destination_requires_stockpile_submission(self):
        run, rows, calendar, config = self.fixture()
        calendar['stockpiles_hal01_rp01_0304_state']['preplan'] = 'Reclaim'
        with self.assertRaises(StockpileSelectionRunError) as caught:
            run._validated_build_only_sources(rows, calendar)
        self.assertEqual(caught.exception.workflow_page, 'stockpile_inventories')

    def test_a_missing_real_source_profile_still_redirects_instead_of_being_skipped(self):
        run, rows, calendar, config = self.fixture()
        config['aps_build_only_sources'] = run._validated_build_only_sources(rows, calendar)
        del config['opf_profiles']['OPF1']['inventory']['SP1']
        with self.assertRaises(PlanningPrerequisiteError) as caught:
            prepare_inventory_profiles(rows, [], config)
        self.assertEqual(caught.exception.workflow_page, 'stockpile_inventories')

    def test_build_only_marker_without_calendar_validation_cannot_skip_grade_checks(self):
        run, rows, calendar, config = self.fixture()
        with self.assertRaises(PlanningPrerequisiteError):
            prepare_inventory_profiles(rows, [], config)
