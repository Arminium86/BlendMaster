"""Named Support presets with a single Planner selection control."""
from copy import deepcopy
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
from classes.SiteWorkflow import require_action
from classes.SolverPresets import (COMPOSITION_FIELDS, make_preset, matching_preset,
                                  preset_library, save_preset)


class SolverPresetControls:
    def __init__(self, host):
        self.host = host
        self.name = QLineEdit()
        self.name.setPlaceholderText('Preset name')
        self.name.setMaxLength(80)
        self.name.setMinimumWidth(220)
        self.save_button = QPushButton('Save preset')
        self.save_button.setToolTip('Save the current solver settings and blend composition under this name.')
        self.status = QLabel('')
        support = QHBoxLayout()
        support.addWidget(QLabel('Named preset'))
        support.addWidget(self.name)
        support.addWidget(self.save_button)
        support.addWidget(self.status, 1)
        host.solver_config_layout.insertLayout(0, support)
        self.choice = QComboBox()
        self.choice.setMinimumWidth(240)
        self.choice.setToolTip('Apply a Support-defined preset. Calendar equipment limits still apply.')
        planner = QHBoxLayout()
        planner.addWidget(QLabel('Solver preset'))
        planner.addWidget(self.choice, 1)
        host.decision_levers_layout.insertLayout(2, planner)
        self.save_button.clicked.connect(self.save)
        self.choice.activated.connect(self.apply)
        self.refresh()

    def refresh(self):
        h = self.host
        library = preset_library(vars(h).get('solver_presets'))
        selected = matching_preset(library, vars(h).get('solver_config'), vars(h))
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItem('Custom settings', '')
        for row in library:
            self.choice.addItem(row['name'], row['name'])
        self.choice.setCurrentIndex(max(0, self.choice.findData(selected)))
        self.choice.blockSignals(False)
        h.selected_solver_preset = selected
        self.status.clear()

    def save(self):
        h = self.host
        require_action(h.access_role, 'solver_configuration')
        if not h.store_solver_config_inputs():
            return
        try:
            record = make_preset(self.name.text(), h.solver_config, vars(h))
            h.solver_presets = save_preset(vars(h).get('solver_presets'), record)
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))
            return
        self.refresh()
        h.save_active_scenario_state()
        self.status.setText('Preset saved.')

    def apply(self, index):
        h = self.host
        require_action(h.access_role, 'decision_levers')
        name = self.choice.itemData(index)
        if not name:
            return
        record = next((r for r in preset_library(vars(h).get('solver_presets')) if r['name'] == name), None)
        if record is None:
            self.refresh()
            return
        previous = {k: deepcopy(vars(h).get(k)) for k in ('solver_config', 'calendar_inputs', *COMPOSITION_FIELDS)}
        try:
            h.solver_config = h.normalized_solver_config(record['solver_config'])
            h.calendar_inputs = deepcopy(vars(h).get('calendar_inputs') or {})
            h.calendar_inputs['solver_config'] = deepcopy(h.solver_config)
            for key, value in record['composition'].items():
                setattr(h, key, value)
                h.calendar_inputs[key] = value
            h.load_solver_config_inputs()
            if not h.store_solver_config_inputs():
                raise ValueError('The preset is incompatible with this site. Previous settings were retained.')
        except (ValueError, TypeError) as exc:
            for key, value in previous.items():
                setattr(h, key, value)
            h.load_solver_config_inputs()
            self.refresh()
            QMessageBox.warning(h, 'Solver preset', str(exc))
            return
        self.refresh()
        h.save_active_scenario_state()


def install(host):
    host.solver_preset_controls = SolverPresetControls(host)
