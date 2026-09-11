"""Site-selected tipping points and stockpile rehandle routes."""
from copy import deepcopy
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox
from classes.MultiFeedSettings import multi_feed_settings


class MultiFeedSetup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._saved = multi_feed_settings()
        self.context, self.scenarios = {}, {}
        layout = QVBoxLayout(self)
        note = QLabel('Plan Mode, OPFs and operating crushers come from Site Configuration. Rates, grade targets and Max Reclaim Rate are in Calendar. Brand follows Product Targets. Each stockpile feeds at most one tipping point at a time.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.compensation = QCheckBox('Allow OPFs to compensate in shared builds')
        self.compensation.setToolTip('Only explicitly shared Product Targets combine OPF contributions. Tipping-point grade limits still apply.')
        layout.addWidget(self.compensation)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.points = self.table('Tipping points', ['Tipping point', 'OPF', 'ROM area', 'Direct tip enabled', 'Grade stream', 'Min stockpiles', 'Max stockpiles'])
        self.rules = self.table('Rehandle Movement Rules', ['Stockpile subset', 'Tipping point', 'Allowed'])
        self.profiles_page = QWidget()
        profile_layout = QVBoxLayout(self.profiles_page)
        note = QLabel('Each OPF needs its own reconciled source grades before outputs can be combined. Select the prepared Data Streams scenario for that OPF, at the same mine and scenario start. This selects existing reconciliation data; it does not change factors or inventory. A unique matching scenario is selected automatically.')
        note.setWordWrap(True)
        profile_layout.addWidget(note)
        self.profiles = QTableWidget(0, 2)
        self.profiles.setHorizontalHeaderLabels(['OPF', 'Prepared reconciliation data'])
        self.profiles.horizontalHeader().setStretchLastSection(True)
        profile_layout.addWidget(self.profiles)
        self.tabs.addTab(self.profiles_page, 'OPF reconciliation')
        buttons = QHBoxLayout()
        add, remove = QPushButton('Add movement rule'), QPushButton('Remove selected movement rule')
        add.clicked.connect(self.add_rule)
        remove.clicked.connect(lambda: self.rules.removeRow(self.rules.currentRow()) if self.tabs.currentWidget() is self.rules else None)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)

    def table(self, title, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        self.tabs.addTab(table, title)
        return table

    @staticmethod
    def rows(table):
        return [[table.cellWidget(r, c).currentData() if isinstance(table.cellWidget(r, c), QComboBox)
                 else table.item(r, c).text().strip() if table.item(r, c) else '' for c in range(table.columnCount())]
                for r in range(table.rowCount())]

    @staticmethod
    def text(table, row, col, value, editable=True):
        item = QTableWidgetItem('' if value is None else str(value))
        if not editable:
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        table.setItem(row, col, item)

    @staticmethod
    def choice(table, row, col, options, value):
        combo = QComboBox()
        for option in dict.fromkeys(options):
            combo.addItem(str(option), option)
        if value not in options and value not in (None, ''):
            combo.addItem(f'Unavailable: {value}', value)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        table.setCellWidget(row, col, combo)
        return combo

    def set_settings(self, value=None, *, context=None, **_):
        self._saved = multi_feed_settings(value)
        self.context = context or self.context
        self.compensation.setChecked(self._saved['allow_opf_compensation'])
        combined = self._saved['mode'] == 'combined_opf'
        self.compensation.setVisible(combined)
        self.tabs.setTabVisible(self.tabs.indexOf(self.profiles_page), combined)
        names = self.context.get('crushers', [p['name'] for p in self._saved['tipping_points']])
        self.points.setRowCount(len(self._saved['tipping_points']))
        for row, point in enumerate(self._saved['tipping_points']):
            combo = self.choice(self.points, row, 0, names, point['name'])
            combo.currentIndexChanged.connect(lambda _index, r=row: self.point_changed(r))
            self.text(self.points, row, 1, point['opf'], False)
            self.choice(self.points, row, 2, self.context.get('areas', [point['rom_area']]), point['rom_area'])
            self.choice(self.points, row, 3, ['Yes', 'No'], 'Yes' if point['direct_tip_enabled'] else 'No')
            self.choice(self.points, row, 4, ['insitu', 'modelled_rom', 'adjusted_rom', 'modelled_product', 'adjusted_product'], point['solver_config'].get('selected_data_stream', 'adjusted_product'))
            for col, key in [(5, 'min_stockpiles'), (6, 'max_stockpiles')]:
                self.text(self.points, row, col, point.get(key))
        self.rules.setRowCount(0)
        for rule in self._saved['rehandle_rules']:
            self.add_rule(rule)
        self.set_scenarios(self.scenarios)
        self.points.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate([185, 150, 210, 160, 210, 160, 160]):
            self.points.setColumnWidth(col, width)

    def point_changed(self, row):
        name = self.points.cellWidget(row, 0).currentData()
        opf = self.context.get('point_opfs', {}).get(name)
        if opf:
            self.text(self.points, row, 1, opf, False)

    def add_rule(self, rule=None):
        rule = rule if isinstance(rule, dict) else {}
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        self.choice(self.rules, row, 0, self.context.get('subsets', sorted(set(self._saved['source_subsets'].values()))), rule.get('subset'))
        self.choice(self.rules, row, 1, self.context.get('crushers', [p['name'] for p in self._saved['tipping_points']]), rule.get('tipping_point'))
        self.choice(self.rules, row, 2, ['Yes', 'No'], 'Yes' if rule.get('allowed', True) else 'No')

    def set_scenarios(self, scenarios):
        self.scenarios = scenarios
        selected = {row[0]: row[1] for row in self.rows(self.profiles)}
        opfs = list(dict.fromkeys(p['opf'] for p in self._saved['tipping_points']))
        self.profiles.setRowCount(len(opfs))
        for row, opf in enumerate(opfs):
            self.text(self.profiles, row, 0, opf, False)
            combo = QComboBox()
            combo.addItem('Choose prepared scenario…', '')
            for identity, (label, scenario_opf) in scenarios.items():
                if scenario_opf == opf:
                    combo.addItem(label, identity)
            saved = self._saved['opf_scenarios'].get(opf) or selected.get(opf)
            index = combo.findData(saved)
            if saved and index < 0:
                combo.addItem(f'Unavailable scenario: {saved}', saved)
                index = combo.count() - 1
            combo.setCurrentIndex(index if index >= 0 else 1 if combo.count() == 2 else 0)
            self.profiles.setCellWidget(row, 1, combo)

    def settings(self):
        value = deepcopy(self._saved)
        value.update(tipping_points=[], rehandle_rules=[], route_reclaim_rates={})
        value['allow_opf_compensation'] = self.compensation.isChecked()
        value['opf_scenarios'] = {row[0]: row[1] for row in self.rows(self.profiles)}
        prior = {p['name']: p for p in self._saved['tipping_points']}
        for name, opf, area, direct, stream, minimum, maximum in self.rows(self.points):
            if name not in prior:
                raise ValueError('Choose operating crushers in Site Configuration first.')
            point = deepcopy(prior[name])
            point.update(name=name, opf=opf, rom_area=area, direct_tip_enabled=direct == 'Yes')
            point['solver_config']['selected_data_stream'] = stream
            point.update(min_stockpiles=int(minimum) if minimum else None, max_stockpiles=int(maximum) if maximum else None)
            value['tipping_points'].append(point)
        for subset, point, allowed in self.rows(self.rules):
            if subset not in self.context.get('subsets', [subset]):
                raise ValueError('Choose an existing stockpile subset for each movement rule.')
            value['rehandle_rules'].append(dict(subset=subset or '', tipping_point=point, allowed=allowed == 'Yes'))
        return multi_feed_settings(value)
