"""Planner task order and role-aware navigation, independent of calculations."""
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QTabWidget, QTabBar, QStyle,
                             QStyleOptionTab, QStylePainter)
from classes.ProductTargets import product_targets_identifier, migrate_product_target_state

WORKSPACE = ('site_configuration', 'guidance_schedules', 'stockpile_inventories',
             'grade_reconciliation', 'amt_stockpiles', 'product_targets', 'expit_sequence',
             'destination_progress', 'decision_levers', 'calendar', 'optimised_blend_sequence',
             'setup_blends', 'blend_sequence', 'blend_plan', 'material_destination_plan')
VIEWS = ('database_view', 'opf_production_report', 'grade_profiles', 'material_flow_results',
         'build_depletion_profiles', 'closing_rom_stocks_compliance')
SUPPORT = ('site_model', 'guidance_settings', 'define_fields', 'map_fields', 'data_streams',
           'solver_configuration', 'multi_feed_setup', 'material_flow', 'database_reports',
           'site_automation', 'decision_point', 'agent', 'reports')
CAPTIONS = {'setup_blends': 'Manual Blending Dashboard', 'blend_sequence': 'Manual Blend Sequence',
            'material_flow': 'Conveyors & COS', 'decision_point': 'Decision Diagnostics',
            'agent': 'Legacy Agent Bridge'}
DEPENDENT_PAGES = {'data_streams': ('grade_reconciliation',),
    'reports': ('blend_plan', 'material_destination_plan', 'material_flow_results', 'database_reports')}


def page_allowed(host, page_id):
    # Test harnesses/legacy adapters without a session retain their old behavior.
    return vars(host).get('access_role', 'support') != 'planner' or page_id not in SUPPORT


class TaskTabBar(QTabBar):
    """Readable horizontal labels on a vertical list of workflow tabs."""
    def tabSizeHint(self, index):
        return QSize(232, 34)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.CE_TabBarTabShape, option)
            painter.setPen(option.palette.color(option.palette.WindowText)
                           if self.isTabEnabled(index) else option.palette.color(option.palette.Disabled, option.palette.WindowText))
            painter.drawText(option.rect.adjusted(12, 0, -8, 0), Qt.AlignLeft | Qt.AlignVCenter,
                             self.fontMetrics().elidedText(self.tabText(index), Qt.ElideRight, option.rect.width()-20))


class WorkflowNavigation:
    def setup_navigation(self):
        self.page_locations, self.page_widgets, self.navigation_parents = {}, {}, {}
        self.navigation_tab_widgets = [self.tabs]
        self._page_enabled_state = {}
        self.tabs.currentChanged.connect(lambda index: self.handle_navigation_tab_changed(self.tabs, index))
        for attribute, caption in (('workspace', 'Workspace'), ('results', 'Views'), ('setup', 'Support')):
            tabs = self.new_navigation_tabs()
            tabs.setTabBar(TaskTabBar())
            tabs.setTabPosition(QTabWidget.West)
            tabs.setUsesScrollButtons(True)
            page = self.navigation_container(tabs)
            setattr(self, attribute + '_tabs', tabs)
            setattr(self, attribute + '_navigation_page', page)
            self.tabs.addTab(page, caption)
            self.navigation_parents[tabs] = (self.tabs, page)
        self.auto_blend_tabs = self.workspace_tabs
        self.manual_blend_tabs = self.workspace_tabs
        self.grade_profiles_tabs = self.new_navigation_tabs()
        self.grade_profiles_navigation_page = self.navigation_container(self.grade_profiles_tabs)
        self.register_page('grade_profiles', self.results_tabs, self.grade_profiles_navigation_page, 'Grade Profiles')
        self.navigation_parents[self.grade_profiles_tabs] = (self.results_tabs, self.grade_profiles_navigation_page)
        self.legacy_tab_page_ids = ['site_configuration', 'stockpile_inventories', 'amt_stockpiles',
            'solver_configuration', 'product_targets', 'calendar', 'decision_point', 'optimised_blend_sequence',
            'build_depletion_profiles', 'optimised_grade_profiles', 'reports', 'setup_blends',
            'blend_sequence', 'manual_grade_profiles', 'agent']
        self.tabs.setTabVisible(self.tabs.indexOf(self.setup_navigation_page), page_allowed(self, 'site_model'))

    def register_page(self, page_id, tab_widget, page, caption, position=None):
        for order, attribute in ((WORKSPACE, 'workspace_tabs'), (VIEWS, 'results_tabs'), (SUPPORT, 'setup_tabs')):
            tabs = vars(self).get(attribute)
            if page_id in order and tabs is not None and all(k in vars(self) for k in ('workspace_tabs','results_tabs','setup_tabs')):
                tab_widget = tabs
                rank = order.index(page_id)
                position = sum(1 for key, (parent, _) in self.page_locations.items()
                               if parent is tabs and key in order and order.index(key) < rank)
                break
        caption = CAPTIONS.get(page_id, caption)
        index = tab_widget.addTab(page, caption) if position is None else tab_widget.insertTab(position, page, caption)
        self.page_widgets[page_id] = page
        self.page_locations[page_id] = (tab_widget, index)
        for key, (parent, _) in list(self.page_locations.items()):
            self.page_locations[key] = (parent, parent.indexOf(self.page_widgets[key]))
        tab_widget.setTabVisible(index, page_allowed(self, page_id))
        return page_id

    def set_page_enabled(self, page_id, enabled):
        page_id = product_targets_identifier(page_id)
        location = self.page_locations.get(page_id)
        if location is not None:
            vars(self).setdefault('_page_enabled_state', {})[page_id] = bool(enabled)
            tabs, index = location
            tabs.setTabEnabled(index, bool(enabled) and page_allowed(self, page_id))
            tabs.setTabVisible(index, page_id != 'reports' and page_allowed(self, page_id))
        for child in DEPENDENT_PAGES.get(page_id, ()):
            if child in self.page_locations:
                self.set_page_enabled(child, enabled)

    def is_page_enabled(self, page_id):
        location = self.page_locations.get(product_targets_identifier(page_id))
        return bool(location and location[0].isTabEnabled(location[1]))

    def show_page(self, page_id, force=False):
        page_id = product_targets_identifier(page_id)
        controller = vars(self).get('site_workflow_controller')
        if controller and controller.active and page_id != 'calendar':
            return
        if page_id == 'reports':
            page_id = 'blend_plan'
        elif page_id == 'data_streams' and vars(self).get('access_role') == 'planner':
            page_id = 'grade_reconciliation'
        if not page_allowed(self, page_id):
            return
        if (getattr(self, 'project_load_keep_site_configuration_visible', False)
                and page_id != 'site_configuration'):
            return
        location = self.page_locations.get(page_id)
        if not location:
            return
        tabs, index = location
        current = tabs
        while current in self.navigation_parents:
            parent, page = self.navigation_parents[current]
            parent.setCurrentWidget(page)
            current = parent
        tabs.setCurrentIndex(index)
        self.page_widgets[page_id].setFocus(Qt.OtherFocusReason)

    def capture_page_states(self):
        return {key: vars(self).get('_page_enabled_state', {}).get(key, self.is_page_enabled(key))
                for key in self.page_locations}

    def normalized_page_states(self, raw_states):
        if not isinstance(raw_states, dict):
            return {}
        raw_states = migrate_product_target_state({'tab_states': raw_states})['tab_states']
        result = {}
        for key, enabled in raw_states.items():
            if key in self.page_locations:
                result[key] = bool(enabled)
                continue
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(self.legacy_tab_page_ids):
                page_id = product_targets_identifier(self.legacy_tab_page_ids[index])
                if page_id not in raw_states:
                    result[page_id] = bool(enabled)
        return result

    def restore_page_states(self, raw_states):
        states = self.normalized_page_states(raw_states)
        for key, enabled in states.items():
            self.set_page_enabled(key, enabled)
        refresh = vars(self).get('_workflow_context_refresh')
        if refresh:
            refresh()
        return states

    def handle_navigation_tab_changed(self, tab_widget, index):
        page = tab_widget.widget(index)
        for child, (parent, container) in list(self.navigation_parents.items()):
            if parent is tab_widget and container is page:
                self.handle_navigation_tab_changed(child, child.currentIndex())
                return
        for page_id, widget in list(self.page_widgets.items()):
            if page is widget:
                if not page_allowed(self, page_id):
                    return
                enter = vars(self).get('_workflow_page_enter')
                if enter and enter(page_id):
                    return
                self.handle_main_tab_changed(page_id)
                return
