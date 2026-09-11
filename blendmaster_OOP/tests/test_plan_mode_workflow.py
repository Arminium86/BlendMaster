import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QComboBox
from PyQt5.QtTest import QTest
from GUI.SelectionComboBox import SelectionComboBox
from GUI.InitialiseGUI import UserInputs
from classes.MultiFeedSettings import multi_feed_settings
from classes.MultiFeedCalendar import apply_calendar, calendar_key, calendar_rows, legacy_aggregate_calendar
from tests.test_multi_lane_optimizer import settings


class PlanModeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_cardinality_and_opf_coverage(self):
        mode, opfs, points, mine = QComboBox(), SelectionComboBox(), SelectionComboBox(), QComboBox()
        for widget in (mode, opfs, points, mine):
            self.addCleanup(widget.deleteLater)
        for value in ['single', 'multi_tipping_point', 'combined_opf']:
            mode.addItem(value, value)
        mine.addItem('CC')
        opfs.addItems(['CC OPF01', 'CC OPF02'])
        points.addItems(['OPF01_PC', 'HAL_PC', 'OPF02_PC'])
        view = SimpleNamespace(plan_mode_input=mode, opf_input=opfs, site_crusher_input=points, mine_input=mine)
        for name in ['site_plan_mode', 'selected_site_crusher_names', 'site_plan_selection_valid']:
            setattr(view, name, getattr(UserInputs, name).__get__(view))
        self.assertTrue(view.site_plan_selection_valid())
        mode.setCurrentIndex(1)
        points.setMultiple(True)
        points.setSelectedTexts(['OPF01_PC', 'HAL_PC'])
        self.assertTrue(view.site_plan_selection_valid())
        mode.setCurrentIndex(2)
        opfs.setMultiple(True)
        opfs.setSelectedTexts(['CC OPF01', 'CC OPF02'])
        self.assertFalse(view.site_plan_selection_valid())
        points.setSelectedTexts(['OPF01_PC', 'OPF02_PC'])
        self.assertTrue(view.site_plan_selection_valid())
        points.setMultiple(False)
        self.assertEqual(points.selectedTexts(), ['OPF01_PC'])

    def test_mouse_selection_keeps_multiple_choices_and_summary(self):
        combo = SelectionComboBox()
        self.addCleanup(combo.deleteLater)
        combo.addItems(['OPF1', 'OPF2', 'OPF3'])
        combo.setMultiple(True)
        combo.show()
        combo.showPopup()
        self.app.processEvents()
        rect = combo.view().visualRect(combo.model().index(1, 0))
        QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=rect.center())
        self.assertEqual(combo.selectedTexts(), ['OPF1', 'OPF2'])
        self.assertEqual(combo.lineEdit().text(), 'OPF1, OPF2')
        QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=rect.center())
        self.assertEqual(combo.selectedTexts(), ['OPF1'])
        combo.view().hide()

    def test_calendar_settings_round_trip_and_brand_ownership(self):
        config = multi_feed_settings(settings())
        labels = ['Preplan', 'Period 1']
        calendar = {calendar_key('A', 'crusher_rate'): dict(zip(labels, [40, 80])),
                    calendar_key('A', 'max_reclaim_rate'): dict(zip(labels, [30, 70]))}
        config = apply_calendar(config, calendar, labels)
        self.assertEqual(config['tipping_points'][0]['targets_by_period']['period_1']['max_reclaim_rate'], 70)
        rows = calendar_rows(config, labels, [{'opf': 'OPF1', 'brand': 'FB'}])
        brands = [r for r in rows if isinstance(r, dict) and calendar_key('A', 'brand') in r]
        self.assertFalse(any(brands[0][calendar_key('A', 'brand')][1]))
        self.assertIn('FB', brands[0][calendar_key('A', 'brand')][3][0])
        saved = legacy_aggregate_calendar(config, calendar, labels)
        self.assertEqual(saved['crusher_rate']['Preplan'], 140)
        self.assertEqual(apply_calendar(config, saved, labels), config)

    def test_reclaim_capacity_is_total_per_point_not_per_source(self):
        from tests.test_multi_lane_optimizer import MultiLaneOptimizerTests
        from tests.test_decision_levers import DecisionLeverOptimizerTests
        cfg = settings(rehandle_rules=[dict(subset='B', tipping_point='A', allowed=True)])
        cfg['tipping_points'][0]['targets_by_period']['preplan']['max_reclaim_rate'] = 50
        cfg['tipping_points'][1]['targets_by_period']['preplan']['crusher_rate'] = 0
        result = MultiLaneOptimizerTests().solve(cfg, [DecisionLeverOptimizerTests.event('SP1'), DecisionLeverOptimizerTests.event('SP2')])
        self.assertAlmostEqual(result['crusher_actual_tonnes'], 50)

    def test_calendar_reaches_loader_solver_and_shared_inventory(self):
        import io
        import pandas as pd
        from contextlib import redirect_stdout
        from classes.DataLoader import DataLoader
        from classes.CaseModeller import CaseModeller
        from tests.test_material_flow_topology import planning_inputs, SITE
        _, periods, calendar, stockpiles, _ = planning_inputs()
        labels = periods.period_labels()
        calendar[calendar_key('A', 'crusher_rate')] = dict.fromkeys(labels, 250)
        calendar[calendar_key('A', 'max_reclaim_rate')] = dict.fromkeys(labels, 180)
        calendar[calendar_key('B', 'crusher_rate')] = dict.fromkeys(labels, 100)
        calendar[calendar_key('B', 'max_reclaim_rate')] = dict.fromkeys(labels, 100)
        for point in ('A', 'B'):
            calendar[calendar_key(point, 'target_fe_min')] = dict.fromkeys(labels, 0)
            calendar[calendar_key(point, 'target_fe_max')] = dict.fromkeys(labels, 100)
        config = apply_calendar(multi_feed_settings(settings()), calendar, labels)
        calendar = legacy_aggregate_calendar(config, calendar, labels)
        calendar['solver_config']['multi_feed_settings'] = config
        piles, blocks, equipment, targets = DataLoader(stockpiles, calendar, pd.DataFrame(), [], periods).load_data()
        with redirect_stdout(io.StringIO()):
            case = CaseModeller(piles, blocks, equipment, targets, pd.DataFrame(), periods, 1, [],
                               solver_config=calendar['solver_config'], site_context=SITE)
            case.run()
        feed = case.results[case.results.source_actual_tonnes > 0]
        self.assertEqual(feed.groupby('tipping_point').source_actual_tonnes.sum().to_dict(), {'A': 540, 'B': 300})
        self.assertTrue((feed.source_closing_balance >= 0).all())

    def test_direct_tip_aliases_match_any_selected_physical_point(self):
        mine, opf = QComboBox(), QComboBox()
        self.addCleanup(mine.deleteLater)
        self.addCleanup(opf.deleteLater)
        mine.addItem('CC')
        opf.addItem('CC OPF01')
        view = SimpleNamespace(mine_input=mine, opf_input=opf,
            multi_feed_configuration={'tipping_points': [{'name': 'OPF01_PC', 'opf': 'CC OPF01'}, {'name': 'OPF02_PC', 'opf': 'CC OPF02'}]},
            selected_site_crusher_names=lambda: ['OPF01_PC', 'OPF02_PC'],
            selected_aps_crusher_names=lambda: ['OPF1 CRUSHER', 'RCH'])
        self.assertTrue(UserInputs.selected_aps_crusher_matches_operating_crusher(view))
        view.selected_aps_crusher_names = lambda: ['HAL CRUSHER']
        self.assertFalse(UserInputs.selected_aps_crusher_matches_operating_crusher(view))
