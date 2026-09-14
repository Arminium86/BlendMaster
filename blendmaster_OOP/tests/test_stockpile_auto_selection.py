"""Inventory defaults follow selected operating crushers and visible 2WP brands."""
import io
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import Mock, patch

import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QCheckBox, QMainWindow, QPushButton, QTableWidget, QVBoxLayout, QWidget

from GUI.InitialiseGUI import UserInputs
from GUI.SelectionComboBox import SelectionComboBox
from classes.HaulCycleDataHandler import HaulCycleDataHandler


class StockpileAutoSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        window = UserInputs.__new__(UserInputs)
        QMainWindow.__init__(window)
        self.addCleanup(window.deleteLater)
        window.mine_input_choice = 'CC'
        window.opf_input_choice = 'CC OPF01'
        window.selected_site_crushers = ['OPF01_PC', 'OPF02_PC']
        window.multi_feed_configuration = dict(mode='combined_opf', tipping_points=[
            dict(name='OPF01_PC', opf='CC OPF01', rom_area='OPF1 Crusher', direct_tip_enabled=False),
            dict(name='OPF02_PC', opf='CC OPF02', rom_area='RCH'),
            dict(name='HAL_PC', opf='CC OPF01', rom_area='HAL Crusher')])
        window.current_haul_cycle_crusher_node = Mock(return_value=['Crushers/HAL Crusher'])
        window.stockpile_data_use_column = {}
        window.stockpile_data_AMT_column = {}
        return window

    def test_all_selected_operating_crushers_match_even_with_old_mapping(self):
        window = self.window()
        rows = {
            'OPF1': dict(nearest_crusher='Crushers/OPF1 Crusher', aps_brand_summary='SF'),
            'OPF2': dict(nearest_crusher='Crushers/RCH:In', aps_brand_summary='CCFB'),
            'HAL': dict(nearest_crusher='HAL Crusher', aps_brand_summary='SF'),
            'EXACT': dict(nearest_crusher='opf02_pc', aps_brand_summary='SF 50%, CCFB 50%'),
            'NO_CRUSHER': dict(nearest_crusher='', aps_brand_summary='SF'),
            'NO_BRAND': dict(nearest_crusher='RCH', aps_brand_summary=''),
        }
        self.assertEqual(window.default_stockpile_use_for_active_crusher(rows),
            dict(OPF1=True, OPF2=True, HAL=False, EXACT=True, NO_CRUSHER=False, NO_BRAND=False))
        window.current_haul_cycle_crusher_node.assert_not_called()

    def test_blank_or_missing_displayed_brands_are_not_selected(self):
        window = self.window()
        for brand in ('', '  ', None, float('nan'), pd.NA, 'None', 'NaN', '<NA>'):
            with self.subTest(brand=repr(brand)):
                rows = {'SP': dict(nearest_crusher='RCH', aps_brand_summary=brand, aps_brand='SF')}
                self.assertEqual(window.default_stockpile_use_for_active_crusher(rows), {'SP': False})
        self.assertEqual(window.default_stockpile_use_for_active_crusher({
            'SP': dict(nearest_crusher='RCH')}), {'SP': False})

    def test_restored_multi_crusher_configuration_uses_each_rom_area(self):
        window = self.window()
        del window.selected_site_crushers
        window.multi_feed_configuration['tipping_points'] = [
            dict(name='OPF01_PC', opf='CC OPF01', rom_area='Primary Pad'),
            dict(name='OPF02_PC', opf='CC OPF02', rom_area='Secondary Pad')]
        self.assertEqual(window.default_stockpile_use_for_active_crusher({
            'SP1': dict(NEAREST_CRUSHER='Primary Pad', APS_BRAND_SUMMARY='SF'),
            'SP2': dict(NEAREST_CRUSHER='Secondary Pad', APS_BRAND_PROPORTIONS={'CCFB': 1})}),
            {'SP1': True, 'SP2': True})

    def test_imported_hi_names_keep_their_identity_and_auto_select_via_mapping(self):
        window = self.window()
        window.multi_feed_configuration['tipping_points'][0]['rom_area'] = 'HI East Pad'
        window.multi_feed_configuration['tipping_points'][1]['rom_area'] = 'HI West Pad'
        routes = HaulCycleDataHandler.build_nearest_crusher_routes(io.StringIO(
            'Source Node,Dest Node,Total Cycle Time (min)\n'
            'Stockpiles/SP1:Out,Crushers/HI East Pad,12\n'
            'Stockpiles/SP2:Out,Crushers/HI West Pad,15\n'
            'Stockpiles/SP3:Out,Crushers/HAL Crusher,10\n'
            'Stockpiles/SP4:Out,Crushers/HI West Pad,15\n'),
            ['HI East Pad', 'HI West Pad', 'HAL Crusher'])
        stockpiles = {name: {**route, 'aps_brand_summary': 'SF' if name != 'SP4' else ''}
                      for name, route in routes.items()}
        expected = {'SP1': True, 'SP2': True, 'SP3': False, 'SP4': False}
        self.assertEqual(window.apply_default_stockpile_preselection(stockpiles), expected)
        self.assertEqual(window.stockpile_data_use_column, expected)
        self.assertEqual(window.stockpile_data_AMT_column, expected)
        self.assertEqual(stockpiles['SP1']['nearest_crusher'], 'HI East Pad')
        self.assertEqual(stockpiles['SP2']['nearest_crusher'], 'HI West Pad')
        self.assertEqual(window.operating_crushers_for_haul_node('HI East Pad'), ['OPF01_PC'])
        self.assertEqual(window.operating_crushers_for_haul_node('HI West Pad'), ['OPF02_PC'])
        self.assertEqual(window.operating_crushers_for_haul_node('HAL Crusher'), [])

    def test_empty_operating_selection_does_not_use_stale_model_or_haul_nodes(self):
        window = self.window()
        window.selected_site_crushers = []
        window.crusher_input_choice = 'OPF02_PC'
        rows = {'SP': dict(nearest_crusher='RCH', aps_brand_summary='SF')}
        self.assertEqual(window.default_stockpile_use_for_active_crusher(rows), {'SP': False})

    def test_legacy_single_crusher_and_total_feed_aliases(self):
        window = self.window()
        del window.selected_site_crushers
        window.multi_feed_configuration = {}
        rows = {name: dict(nearest_crusher=name, aps_brand_summary='SF')
                for name in ('OPF1 Crusher', 'HAL Crusher', 'RCH')}
        window.crusher_input_choice = 'OPF02_PC'
        self.assertEqual(window.default_stockpile_use_for_active_crusher(rows),
                         {'OPF1 Crusher': False, 'HAL Crusher': False, 'RCH': True})
        window.crusher_input_choice = 'TOTAL_FEED_PC'
        self.assertEqual(window.default_stockpile_use_for_active_crusher(rows),
                         {'OPF1 Crusher': True, 'HAL Crusher': True, 'RCH': False})

    def test_site_widget_fallback_uses_checked_crushers_only(self):
        window = self.window()
        del window.selected_site_crushers
        window.multi_feed_configuration = {}
        window.site_crusher_input = SelectionComboBox(window)
        window.site_crusher_input.addItems(['OPF01_PC', 'HAL_PC', 'OPF02_PC'])
        window.site_crusher_input.setMultiple(True)
        window.site_crusher_input.setSelectedTexts(['OPF01_PC', 'OPF02_PC'])
        rows = {name: dict(nearest_crusher=name, aps_brand_summary='SF')
                for name in ('OPF1 Crusher', 'HAL Crusher', 'RCH')}
        self.assertEqual(window.default_stockpile_use_for_active_crusher(rows),
                         {'OPF1 Crusher': True, 'HAL Crusher': False, 'RCH': True})

    def test_auto_select_button_reapplies_defaults_to_sorted_saved_inventory(self):
        window = self.window()
        window.stockpile_data = {
            'B_MATCH': dict(nearest_crusher='RCH'),
            'A_NO_BRAND': dict(nearest_crusher='RCH'),
            'C_NOT_ENABLED': dict(nearest_crusher='HAL Crusher'),
        }
        window.aps_stockpile_brand_map = {
            name: dict(primary_brand='SF', brand_proportions={'SF': 1})
            for name in ('B_MATCH', 'C_NOT_ENABLED')}
        window.aps_stockpile_timing_guidance = {}
        saved = {'B_MATCH': False, 'A_NO_BRAND': True, 'C_NOT_ENABLED': True}
        window.stockpile_data_use_column = dict(saved)
        window.stockpile_data_AMT_column = dict(saved)
        page = QWidget(window)
        window.stockpile_tab_layout = QVBoxLayout(page)
        window.stockpile_table = QTableWidget(page)
        window.stockpile_tab_layout.addWidget(window.stockpile_table)
        window.setup_stockpile_table_first_call = True
        window.submit_calendar_first_call = True
        window.setup_stockpile_table()
        window.stockpile_table.sortItems(2, Qt.DescendingOrder)
        nearest_column = window.stockpile_table_column_index('Nearest Crusher')
        for row in range(window.stockpile_table.rowCount()):
            name = window.stockpile_table.item(row, 2).text()
            item = window.stockpile_table.item(row, nearest_column)
            self.assertEqual(item.text(), window.stockpile_data[name]['nearest_crusher'])
            if name == 'B_MATCH':
                self.assertIn('Operating Crusher: OPF02_PC', item.toolTip())
            elif name == 'C_NOT_ENABLED':
                self.assertIn('No matching Operating Crusher', item.toolTip())

        def checked_rows(column):
            return {window.stockpile_table.item(row, 2).text():
                window.stockpile_table.cellWidget(row, column).findChild(QCheckBox).isChecked()
                for row in range(window.stockpile_table.rowCount())}

        self.assertEqual(checked_rows(0), saved)
        button = next(button for button in page.findChildren(QPushButton)
                      if button.text() == 'Auto Select Stockpiles')
        button.click()
        expected = {'B_MATCH': True, 'A_NO_BRAND': False, 'C_NOT_ENABLED': False}
        for column in (0, 1):
            self.assertEqual(checked_rows(column), expected)
        self.assertEqual(window.stockpile_data_use_column, expected)
        self.assertEqual(window.stockpile_data_AMT_column, expected)
        window.setup_stockpile_table()
        self.assertEqual(checked_rows(0), expected)

    def test_guidance_submit_selects_refreshed_matches_and_saves_visible_checkboxes(self):
        for saved in (
            {'MATCH': False, 'NO_BRAND': False, 'NOT_ENABLED': False},
            {'MATCH': False, 'NO_BRAND': True, 'NOT_ENABLED': True},
        ):
            with self.subTest(saved=saved):
                window = self.window()
                # Before submission, both the guidance and selection are stale.
                window.stockpile_data = {name: dict(nearest_crusher='OLD', aps_brand_summary='')
                                         for name in saved}
                window.stockpile_data_use_column = dict(saved)
                window.stockpile_data_AMT_column = dict(saved)
                window.aps_stockpile_brand_map = {}
                window.aps_stockpile_timing_guidance = {}
                window.stockpile_table = QTableWidget(window)
                window.stockpile_table.setSortingEnabled(True)
                window.setup_stockpile_table_first_call = False
                window.stockpile_tab_index = 'stockpile_inventories'
                window.haul_cycle_file_path_choice = 'cycles.csv'
                window.capture_guidance_schedule_controls = Mock()
                window.validate_guidance_schedule_constraints = Mock(return_value=(True, ''))
                window.validate_form = Mock()
                window.set_page_enabled = Mock()
                window.show_page = Mock()

                def refresh_brands():
                    window.aps_stockpile_brand_map = {
                        name: dict(primary_brand='SF', brand_proportions={'SF': 1})
                        for name in ('MATCH', 'NOT_ENABLED')}

                def refresh_routes(**_):
                    window.haul_cycle_routes = {
                        name: dict(nearest_crusher='HAL Crusher' if name == 'NOT_ENABLED' else 'RCH')
                        for name in saved}
                    window.apply_haul_cycle_routes_to_stockpile_data()
                    return True

                window.refresh_aps_stockpile_brand_map = refresh_brands
                window.refresh_haul_cycle_routes = refresh_routes
                snapshots = []

                def save_selection():
                    window.capture_stockpile_table_choices()
                    snapshots.append((dict(window.stockpile_data_use_column),
                                      dict(window.stockpile_data_AMT_column)))

                window.save_active_scenario_state = save_selection
                with patch('GUI.InitialiseGUI.DatabaseManager'):
                    window.handle_guidance_schedules_submit()

                expected = {'MATCH': True, 'NO_BRAND': False, 'NOT_ENABLED': False}
                self.assertEqual(snapshots, [(expected, expected)])
                for column in (0, 1):
                    visible = {window.stockpile_table.item(row, 2).text():
                        window.stockpile_table.cellWidget(row, column).findChild(QCheckBox).isChecked()
                        for row in range(window.stockpile_table.rowCount())}
                    self.assertEqual(visible, expected)
                window.show_page.assert_called_once_with('stockpile_inventories', force=True)


if __name__ == '__main__':
    unittest.main()
