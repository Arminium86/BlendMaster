"""Editable physical tipping points, per-period targets and rehandle routes."""

from copy import deepcopy
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox

from classes.MultiFeedSettings import multi_feed_settings


TARGET_FIELDS = [("crusher_rate", "Rate (t/h)"), ("brand", "Brand")]
TARGET_FIELDS += [(f"target_{a}_{bound}", f"{a.upper()} {bound}") for a in ("fe", "si", "al", "p", "mn") for bound in ("min", "max")]
TARGET_FIELDS += [("direct_feed_ratio_min", "Direct tip min ratio"), ("direct_feed_ratio_max", "Direct tip max ratio")]


class MultiFeedSetup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._saved = multi_feed_settings()
        self.base_targets = {}
        self.default_opf = ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tipping points and Rehandle Movement Rules"))
        self.mode = QComboBox()
        self.mode.addItem("Single tipping point", "single")
        self.mode.addItem("Multiple tipping points → one OPF", "multi_tipping_point")
        self.mode.addItem("Combined OPF", "combined_opf")
        layout.addWidget(self.mode)
        self.compensation = QCheckBox("Allow OPFs to compensate in shared builds")
        self.compensation.setToolTip("Product Targets: enter one OPF for a separate build, or comma-separated OPFs for a shared build. Separate builds never exchange tonnes or grade credit.")
        layout.addWidget(self.compensation)
        note = QLabel("Each stockpile feeds at most one tipping point at a time. Subset defaults to Nearest Crusher in Stockpile Inventories. Allow other routes below; capacity is configured per stockpile and tipping point. Rate 0 schedules a tipping point off.")
        note.setWordWrap(True)
        self.note = note
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.points = self.table("Tipping points", ["Tipping point", "OPF", "ROM area", "Direct tip enabled", "Grade stream", "Min stockpiles", "Max stockpiles"])
        self.points.itemChanged.connect(self.rename_point)
        self.targets = self.table("Tipping-point targets", ["Tipping point", "Period", *[label for _, label in TARGET_FIELDS]])
        self.rules = self.table("Rehandle Movement Rules", ["Subset", "Tipping point", "Allowed"])
        self.rates = self.table("Route reclaim capacity", ["Stockpile", "Tipping point", "Max reclaim rate (t/h)"])
        self.profiles = self.table("OPF reconciliation", ["OPF", "Reconciliation scenario"])
        self.profiles.setToolTip("Prepare Data Streams separately for each OPF at the same mine and scenario start. Reconciliation scenarios supply grades and mapped properties; physical inventory belongs to this combined scenario.")
        self.scenarios = {}
        buttons = QHBoxLayout()
        self.row_buttons = []
        for label, callback in (("Add tipping point", self.add_point), ("Add movement rule", lambda: self.append(self.rules, ["", "", "Yes"])),
                                ("Add route capacity", lambda: self.append(self.rates, ["", "", "0"])), ("Remove selected row", self.remove_row)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            self.row_buttons.append(button)
        layout.addLayout(buttons)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self.setMinimumHeight(350)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()

    def mode_changed(self):
        multiple = self.mode.currentData() != 'single'
        combined = self.mode.currentData() == 'combined_opf'
        self.tabs.setVisible(multiple)
        self.note.setVisible(multiple)
        self.compensation.setVisible(combined)
        self.tabs.setTabEnabled(self.tabs.indexOf(self.profiles), combined)
        for button in self.row_buttons:
            button.setVisible(multiple)
        self.setMinimumHeight(350 if multiple else 80)

    def table(self, title, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        self.tabs.addTab(table, title)
        return table

    @staticmethod
    def append(table, values):
        index = table.rowCount()
        table.insertRow(index)
        for col, value in enumerate(values):
            table.setItem(index, col, QTableWidgetItem('' if value is None else str(value)))

    @staticmethod
    def rows(table):
        return [[table.item(r, c).text().strip() if table.item(r, c) else "" for c in range(table.columnCount())]
                for r in range(table.rowCount())]

    def remove_row(self):
        table = self.tabs.currentWidget()
        if table.currentRow() >= 0:
            if table is self.points:
                name = table.item(table.currentRow(), 0).text()
                for dependent, column in ((self.targets, 0), (self.rules, 1), (self.rates, 1)):
                    for row in reversed(range(dependent.rowCount())):
                        if dependent.item(row, column).text() == name:
                            dependent.removeRow(row)
            table.removeRow(table.currentRow())

    def rename_point(self, item):
        if item.column() == 1 and hasattr(self, 'profiles'):
            self.set_scenarios(self.scenarios)
        if item.column() != 0:
            return
        old, name = item.data(Qt.UserRole), item.text().strip()
        self.points.blockSignals(True)
        item.setData(Qt.UserRole, name)
        self.points.blockSignals(False)
        if old and old != name:
            for table, column in ((self.targets, 0), (self.rules, 1), (self.rates, 1)):
                for row in range(table.rowCount()):
                    if table.item(row, column).text() == old:
                        table.item(row, column).setText(name)
            for point in self._saved["tipping_points"]:
                if point["name"] == old:
                    point["name"] = name

    def add_point(self):
        name = f"Tipping point {self.points.rowCount() + 1}"
        self.append(self.points, [name, self.default_opf, name, "Yes", "adjusted_product", "", ""])
        for period, base in (self.base_targets or {"preplan": {}, "period_1": {}, "period_2": {}}).items():
            values = {**base, "crusher_rate": 0}
            self.append(self.targets, [name, period, *[values.get(key, 1 if key == "direct_feed_ratio_max" else 100 if key.endswith("_max") else "" if key == "brand" else 0) for key, _ in TARGET_FIELDS]])

    def set_settings(self, value=None, *, base_targets=None, opf=""):
        self._saved = multi_feed_settings(value)
        self.base_targets = deepcopy(base_targets or {})
        self.default_opf = opf
        self.mode.setCurrentIndex(self.mode.findData(self._saved["mode"]))
        self.compensation.setChecked(self._saved['allow_opf_compensation'])
        for table in (self.points, self.targets, self.rules, self.rates):
            table.setRowCount(0)
        for point in self._saved["tipping_points"]:
            config = point["solver_config"]
            self.append(self.points, [point["name"], point["opf"], point["rom_area"], "Yes" if point["direct_tip_enabled"] else "No", config.get("selected_data_stream", "adjusted_product"), point.get("min_stockpiles", ""), point.get("max_stockpiles", "")])
            for period in dict.fromkeys([*self.base_targets, *point["targets_by_period"]]):
                target = {**self.base_targets.get(period, {}), **point["targets_by_period"].get(period, {})}
                self.append(self.targets, [point["name"], period, *[target.get(key, 1 if key == "direct_feed_ratio_max" else 100 if key.endswith("_max") else "" if key == "brand" else 0) for key, _ in TARGET_FIELDS]])
        for rule in self._saved["rehandle_rules"]:
            self.append(self.rules, [rule["subset"], rule["tipping_point"], "Yes" if rule["allowed"] else "No"])
        for source, rates in self._saved["route_reclaim_rates"].items():
            for point, rate in rates.items():
                self.append(self.rates, [source, point, rate])
        self.set_scenarios(self.scenarios)

    def set_scenarios(self, scenarios):
        self.scenarios = scenarios
        selected = {row[0]: self.profiles.cellWidget(i, 1).currentData() for i, row in enumerate(self.rows(self.profiles)) if self.profiles.cellWidget(i, 1)}
        self.profiles.setRowCount(0)
        for opf in dict.fromkeys(row[1] for row in self.rows(self.points) if row[1]):
            self.append(self.profiles, [opf, ''])
            row = self.profiles.rowCount() - 1
            self.profiles.item(row, 0).setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
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
        value.update(mode=self.mode.currentData(), tipping_points=[], rehandle_rules=[], route_reclaim_rates={})
        value['allow_opf_compensation'] = self.compensation.isChecked()
        value['opf_scenarios'] = {row[0]: self.profiles.cellWidget(i, 1).currentData() for i, row in enumerate(self.rows(self.profiles)) if self.profiles.cellWidget(i, 1)}
        prior = {r["name"]: r for r in self._saved["tipping_points"]}
        for name, opf, area, direct, stream, minimum, maximum in self.rows(self.points):
            row = deepcopy(prior.get(name, {}))
            row.update(name=name, opf=opf, rom_area=area, direct_tip_enabled=direct.lower() in {"yes", "true", "1"}, targets_by_period={})
            row["solver_config"] = {**row.get("solver_config", {}), "selected_data_stream": stream}
            for key, text in (("min_stockpiles", minimum), ("max_stockpiles", maximum)):
                row[key] = int(text) if text else None
            value["tipping_points"].append(row)
        points = {r["name"]: r for r in value["tipping_points"]}
        for name, period, *cells in self.rows(self.targets):
            if name not in points:
                raise ValueError(f"Target row refers to unknown tipping point {name!r}.")
            if not period or period in points[name]["targets_by_period"]:
                raise ValueError(f"{name}: each period needs exactly one target row.")
            target = {**deepcopy(self.base_targets.get(period, {})), **deepcopy(prior.get(name, {}).get("targets_by_period", {}).get(period, {}))}
            for (key, _), text in zip(TARGET_FIELDS, cells):
                target[key] = text if key == "brand" else float(text)
            points[name]["targets_by_period"][period] = target
        for subset, point, allowed in self.rows(self.rules):
            value["rehandle_rules"].append(dict(subset=subset, tipping_point=point, allowed=allowed.lower() in {"yes", "true", "1"}))
        for source, point, rate in self.rows(self.rates):
            value["route_reclaim_rates"].setdefault(source, {})[point] = float(rate)
        return multi_feed_settings(value)
