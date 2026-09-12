import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import unittest
from copy import deepcopy
from contextlib import closing
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import pandas as pd
from database.DatabaseContext import get_database_path, set_database_path

from GUI.InitialiseGUI import UserInputs
from GUI.MultiFeedSetup import MultiFeedSetup
from PyQt5.QtWidgets import QApplication
from classes.MultiFeedSettings import multi_feed_settings, scoped_builds, route_allowed
from tests.test_multi_lane_optimizer import settings


class MultiFeedUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = MultiFeedSetup()
        self.addCleanup(self.view.deleteLater)

    def test_site_owned_dropdowns_and_stockpile_subset_rules(self):
        cfg = settings(rehandle_rules=[dict(subset='A', tipping_point='B', allowed=True)], route_reclaim_rates={'SP1': {'B': 40}})
        self.view.set_settings(cfg, context={'crushers': ['A', 'B'], 'subsets': ['A', 'B'], 'areas': ['A', 'B']})
        restored = self.view.settings()
        self.assertEqual(restored['tipping_points'][1]['name'], 'B')
        self.assertEqual(restored['route_reclaim_rates'], {})
        self.assertEqual(self.view.rules.horizontalHeaderItem(0).text(), 'Stockpile subset')
        self.assertEqual(self.view.rules.cellWidget(0, 0).count(), 2)
        self.assertTrue(route_allowed(restored, 'SP1', 'B'))
        self.assertFalse(hasattr(self.view, 'targets'))
        self.assertFalse(hasattr(self.view, 'rates'))

    def test_combined_settings_clear_legacy_scenario_selections(self):
        cfg = settings(allow_opf_compensation=True, opf_scenarios={'OPF1': 'one', 'OPF2': 'two'})
        cfg['mode'] = 'combined_opf'
        cfg['tipping_points'][1]['opf'] = 'OPF2'
        self.view.set_settings(cfg)
        saved = self.view.settings()
        self.view.set_settings(json.loads(json.dumps(saved)))
        self.assertEqual(saved, self.view.settings())
        self.assertEqual(saved['opf_scenarios'], {})
        self.assertFalse(hasattr(self.view, 'profiles'))
        self.assertNotIn('OPF reconciliation', [self.view.tabs.tabText(i) for i in range(self.view.tabs.count())])
        self.assertTrue(self.view.compensation.isChecked())

    def test_calendar_owns_defaults_and_existing_target_values(self):
        from classes.MultiFeedCalendar import apply_calendar, calendar_key
        cfg = settings()
        cfg['tipping_points'][0]['targets_by_period']['preplan'] = {'crusher_rate': 10}
        self.view.set_settings(cfg)
        config = apply_calendar(self.view.settings(), {calendar_key('A', 'crusher_rate'): {'Preplan': 20}}, ['Preplan'])
        self.assertEqual(config['tipping_points'][0]['targets_by_period']['preplan']['target_fe_max'], 100)
        self.assertEqual(config['tipping_points'][0]['targets_by_period']['preplan']['crusher_rate'], 20)

    def test_unknown_route_and_bad_ratios_rejected(self):
        with self.assertRaisesRegex(ValueError, 'configured tipping point'):
            multi_feed_settings(settings(route_reclaim_rates={'SP1': {'Missing': 10}}))
        cfg = settings()
        cfg['tipping_points'][0]['targets_by_period']['preplan']['direct_feed_ratio_max'] = 1.1
        with self.assertRaisesRegex(ValueError, 'ratios'):
            multi_feed_settings(cfg)

    def test_shared_requires_explicit_compensation_and_disjoint_groups(self):
        cfg = settings()
        cfg['mode'] = 'combined_opf'
        cfg['tipping_points'][1]['opf'] = 'OPF2'
        cfg = multi_feed_settings(cfg)
        with self.assertRaisesRegex(ValueError, 'Enable'):
            scoped_builds([{'opf': 'OPF1, OPF2'}], cfg)
        cfg['allow_opf_compensation'] = True
        with self.assertRaisesRegex(ValueError, 'overlap'):
            scoped_builds([{'opf': 'OPF1'}, {'opf': 'OPF1, OPF2'}], cfg)

    def test_simultaneous_backups_use_optimiser_evidence_without_manual_conversion(self):
        previous = get_database_path()
        try:
            with TemporaryDirectory() as folder:
                set_database_path(Path(folder) / 'reports.db')
                view = SimpleNamespace(multi_feed_configuration=settings(),
                    selected_site_crushers=['OLD'], stockpile_data={
                        'BACKUP_A': {'nearest_crusher': 'A'}, 'BACKUP_B': {'nearest_crusher': 'B'}},
                    blend_plan_backup_destinations={'A': 'BACKUP_A', 'B': 'BACKUP_B'})
                for method in ('fetch_multi_feed_report', 'current_blend_plan_backup_choices',
                               'validated_blend_plan_backups', 'multi_feed_backup_export_sheets'):
                    setattr(view, method, getattr(UserInputs, method).__get__(view))
                self.assertTrue(view.fetch_multi_feed_report().empty)
                with closing(sqlite3.connect(get_database_path())) as connection:
                    pd.DataFrame([{'tipping_point': 'A', 'source_actual_tonnes': 100}]).to_sql('optimised_blend_report', connection)
                    pd.DataFrame([
                        {'plan_type': 'optimised', 'fallback_1_destination': 'BACKUP_A', 'fallback_2_destination': 'BACKUP_B'},
                        {'plan_type': 'manual', 'fallback_1_destination': 'STALE', 'fallback_2_destination': ''}
                    ]).to_sql('material_destination_plan', connection)
                self.assertEqual(view.current_blend_plan_backup_choices(), {'A': ['BACKUP_A'], 'B': ['BACKUP_B']})
                sheets = view.multi_feed_backup_export_sheets()
                self.assertEqual(set(sheets[0][1]['Backup destination']), {'BACKUP_A', 'BACKUP_B'})
                view.blend_plan_backup_destinations['A'] = 'STALE'
                with self.assertRaisesRegex(ValueError, 'no longer'):
                    view.multi_feed_backup_export_sheets()
        finally:
            set_database_path(previous)
