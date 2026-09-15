"""Manual recipe and sequence authoring for every configured tipping point."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QInputDialog, QMessageBox, QDateTimeEdit, QDoubleSpinBox, QCheckBox)
from GUI.BlendSequenceTimeline import BlendSequenceTimeline
from classes.MultiManualPlan import definitions, from_report, has_drafts, MultiManualPlanner, reset_imported_timing
from classes.MultiFeedSettings import route_allowed, source_routing
from classes.MultiFeedCalendar import apply_calendar
from classes.SavedResultViews import plan_names, read_report
from database.DatabaseContext import get_database_path


def item(value='', editable=False):
    result = QTableWidgetItem(str(value))
    if not editable:
        result.setFlags(result.flags() & ~Qt.ItemIsEditable)
    return result


def clear_cells(table):
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            widget = table.cellWidget(row, col)
            if widget is not None:
                widget.blockSignals(True); widget.hide()
    table.clearContents()


def preview_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (ValueError, TypeError): return 0.0


def payload_reader(host, context, database):
    """Capture the legacy payload loader's inputs before leaving the GUI thread."""
    from types import SimpleNamespace
    fields = ('file_path_24hr_choice', 'file_path_choice', 'reevaluate_aps_direct_tip_choice',
              'aps_direct_tip_crusher_choice', 'selected_24hr_expit_agents', 'expit_mode_choice',
              'start_time_choice', 'expit_completion_tolerance_pct')
    proxy = SimpleNamespace(**{k: deepcopy(getattr(host, k, None)) for k in fields})
    proxy.is_direct_tip_enabled = lambda: any(p.get('direct_tip_enabled', True) for p in context['multi_feed_settings']['tipping_points'])
    proxy.active_site_context = lambda: context
    selected = host.selected_optimisation_plan_id()
    proxy.fetch_optimised_blend_report = lambda: read_report(database, 'optimised', selected)
    implementation = type(host).manual_expit_payload_transactions
    def load():
        from database.DatabaseContext import database_scope
        with database_scope(database):
            return implementation(proxy)
    return load


class MultiManualAuthoring(QWidget):
    def __init__(self, host, sequence=False):
        super().__init__(host)
        self.host, self.sequence = host, sequence
        self._loading = False; self.point = None; self.recipe = None; self.plan_id = None
        self.projections = {}; self.stockpiles = {}; self.feed = {}; self.labels = []
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Manual plan'))
        self.plans = QComboBox(); self.plans.activated.connect(self.change_plan); bar.addWidget(self.plans)
        for label, action in [('New manual plan…', self.new_plan), ('Create manual plan from optimised…', self.copy_plan)]:
            button = QPushButton(label); button.clicked.connect(action); bar.addWidget(button)
        bar.addStretch(); layout.addLayout(bar)
        self.description = QLabel('Choose a tipping point, create a Blend ID, and give its sources positive weights. '
            'Weights are normalised to 100%; zero excludes a source. Each tipping point has its own blends and sequence.')
        self.description.setWordWrap(True); layout.addWidget(self.description)
        if sequence:
            self.description.setText('Add blend rows, choose a Blend ID, and set its start and duration. '
                'Move rows and join times to reorder the sequence. Review steady states and direct tip, then submit to calculate all tipping points together.')
        bar = QHBoxLayout(); bar.addWidget(QLabel('Tipping point / OPF'))
        self.points = QComboBox(); self.points.activated.connect(self.change_point); bar.addWidget(self.points)
        clear = QPushButton('Clear this tipping point'); clear.clicked.connect(self.clear_point); bar.addWidget(clear)
        bar.addStretch(); layout.addLayout(bar)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.PlainText); layout.addWidget(self.status)
        if sequence:
            self.timeline_caption = QLabel('Editable sequence draft'); layout.addWidget(self.timeline_caption)
            self.timeline = BlendSequenceTimeline(self); layout.addWidget(self.timeline, 1)
            self.timeline.interval_resized.connect(self.resize_draft_interval)
            self.timeline.interval_selected.connect(self.select_draft_interval)
            self.rows = QTableWidget(0, 7)
            self.rows.setHorizontalHeaderLabels(['Blend ID', 'Origin', 'Start datetime', 'Duration (hours)',
                'End datetime', 'Early start', 'Remaining hours'])
            self.rows.setSelectionBehavior(QTableWidget.SelectRows); layout.addWidget(self.rows, 2)
            bar = QHBoxLayout()
            for label, action in [('Add blend', self.add_row), ('Remove selected blends', self.remove_rows),
                                  ('Move up', lambda: self.move_row(-1)), ('Move down', lambda: self.move_row(1)),
                                  ('Join times in row order', self.join_times)]:
                button = QPushButton(label); button.clicked.connect(action); bar.addWidget(button)
            layout.addLayout(bar)
            self.review_button = QPushButton('Steady States & Direct Tip…'); self.review_button.clicked.connect(lambda: self.calculate(True)); layout.addWidget(self.review_button)
            self.submit_button = QPushButton('Submit manual sequence'); self.submit_button.clicked.connect(lambda: self.calculate(False)); layout.addWidget(self.submit_button)
        else:
            bar = QHBoxLayout(); bar.addWidget(QLabel('Blend ID'))
            self.recipes = QComboBox(); self.recipes.activated.connect(self.change_recipe); bar.addWidget(self.recipes)
            for label, action in [('New recipe…', self.new_recipe), ('Delete blend', self.delete_recipe)]:
                button = QPushButton(label); button.clicked.connect(action); bar.addWidget(button)
            bar.addStretch(); bar.addWidget(QLabel('Round blend ratios to'))
            self.rounding = QComboBox(); self.rounding.addItems(['1%', '2%', '5%', '10%', '20%', '25%', '50%']); self.rounding.setCurrentText('5%'); bar.addWidget(self.rounding)
            rounding = QPushButton('Apply rounding'); rounding.clicked.connect(self.round_recipe); bar.addWidget(rounding)
            layout.addLayout(bar)
            self.sources = QTableWidget(0, 13)
            self.sources.setHorizontalHeaderLabels(['Source', 'Weight', 'Ratio (%)', 'Reclaim rate (t/h)',
                'Opening (WMT)', 'Projected (WMT)', 'Available after', 'Use projected', 'Fe', 'Si', 'Al', 'P', 'Mn'])
            self.sources.cellChanged.connect(self.recipe_changed); layout.addWidget(self.sources, 3)
            self.preview = QLabel(); self.preview.setWordWrap(True); layout.addWidget(self.preview)
            self.recipe_preview = QTableWidget(0, 9)
            self.recipe_preview.setHorizontalHeaderLabels(['Blend ID', 'Sources and ratios', 'Fe', 'Si', 'Al', 'P', 'Mn', 'Available after', 'Max hours (estimate)'])
            self.recipe_preview.setMaximumHeight(140); layout.addWidget(self.recipe_preview)
            layout.addWidget(QLabel('Manual crusher rates by period (t/h) — capped by this tipping point’s Calendar limits'))
            self.rates = QTableWidget(0, 3); self.rates.setHorizontalHeaderLabels(['Period', 'Manual rate', 'Calendar limit'])
            self.rates.setMaximumHeight(180); self.rates.cellChanged.connect(lambda *_: self.save_point()); layout.addWidget(self.rates)
            self.submit_button = QPushButton('Submit blends'); self.submit_button.clicked.connect(self.submit_recipes); layout.addWidget(self.submit_button)

    def drafts(self):
        self.host.manual_point_drafts = getattr(self.host, 'manual_point_drafts', None) or {}
        return self.host.manual_point_drafts

    def draft(self):
        return self.drafts().setdefault(self.point, dict(recipes=[], sequence=[], rates={}, allocations={}))

    def refresh(self):
        active = getattr(self.host, 'active_manual_plan_id', 'Primary') or 'Primary'
        context = (get_database_path(), getattr(self.host, 'active_scenario_id', None), active)
        if getattr(self, '_context', None) != context:
            self._context = context
            self.projections = {}
            self._legend_report = pd.DataFrame()
            if self.sequence: self.timeline.set_report(pd.DataFrame())
        # Controls persist directly into the active draft; refreshing never
        # replaces entered recipes with saved result rows.
        self.plan_id = active
        self._loading = True
        try:
            names = list(dict.fromkeys([active, *(getattr(self.host, 'manual_plan_states', {}) or {}), *plan_names(get_database_path(), 'manual')]))
            self.plans.clear(); self.plans.addItems(names); self.plans.setCurrentText(active)
            self.feed = source_routing(vars(self.host))
            self.stockpiles = dict(self.host.included_stockpile_data() if hasattr(self.host, 'included_stockpile_data') else getattr(self.host, 'updated_stockpile_data', {}) or {})
            self.profiles = self.host.current_opf_profiles() if hasattr(self.host, 'current_opf_profiles') else {}
            from classes.PeriodManager import PeriodManager
            periods = PeriodManager(self.host.planning_period_count() if hasattr(self.host, 'planning_period_count') else 3)
            periods.calculate_periods(getattr(self.host, 'start_time_choice', None) or datetime.now())
            self.periods = {k: v for k, v in periods.get_periods().items() if k.endswith(('_start', '_end'))}
            self.labels = ['Preplan' if key == 'preplan_start' else key[:-6].capitalize() for key in self.periods if key.endswith('_start')]
            self.feed = apply_calendar(self.feed, getattr(self.host, 'calendar_inputs', {}) or {}, self.labels)
            old = self.point
            self.points.clear()
            for p in self.feed['tipping_points']:
                self.points.addItem(p['name']+' / '+p['opf'], p['name'])
            index = self.points.findData(old)
            if index >= 0: self.points.setCurrentIndex(index)
            self.point = self.points.currentData(); self.recipe = None
            self.show_point()
        finally:
            self._loading = False

    def hydrate_report(self, report, *, kind='manual'):
        self._legend_report = report.copy()
        if kind == 'manual' and not has_drafts(self.drafts()) and not report.empty:
            from GUI.WorkflowDependencies import manual_revision
            was_current = getattr(self.host, 'manual_input_revision', None) == manual_revision(self.host)
            self.host.manual_point_drafts = from_report(report, self.stockpiles)
            if was_current: self.host.manual_input_revision = manual_revision(self.host)
            self.host.capture_active_manual_plan_state() if hasattr(self.host, 'capture_active_manual_plan_state') else None
            self.show_point()
        if self.sequence:
            self.refresh_draft_timeline()

    def set_projections(self, frame):
        self.projections = {}
        if frame is None or frame.empty or not {'stockpile', 'closing_balance', 'delivered_datetime'}.issubset(frame):
            return
        data = frame.copy()
        data['_at'] = pd.to_datetime(data.delivered_datetime, errors='coerce')
        for name, rows in data.dropna(subset=['_at']).groupby('stockpile', sort=False):
            row = rows.sort_values('_at').iloc[-1]
            # Use the projected record's material evidence when supplied. A
            # projected quantity alone cannot establish the inbound chemistry.
            material = {k: row[k] for k in ('grade_streams', 'source_properties', 'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn')
                        if k in row and row[k] is not None}
            for key in ('grade_streams', 'source_properties'):
                if isinstance(material.get(key), str):
                    try: material[key] = json.loads(material[key])
                    except (ValueError, TypeError): material.pop(key, None)
            self.projections[str(name)] = dict(balance=float(row.closing_balance), available=row['_at'].to_pydatetime(), material=material)
        if not self.sequence and self.point and self.sources.state() != self.sources.EditingState: self.show_recipe()

    def change_plan(self):
        name = self.plans.currentText()
        self.host.activate_manual_plan(name)
        self.point = self.recipe = None
        self.refresh()
        report = read_report(get_database_path(), 'manual', name)
        self.hydrate_report(report)

    def new_plan(self):
        name, ok = QInputDialog.getText(self, 'New manual plan', 'Manual plan name:')
        if not ok or not name.strip(): return
        name = name.strip()
        if name in [self.plans.itemText(i) for i in range(self.plans.count())]:
            self.status.setText('That manual plan already exists. Select it to edit.'); return
        self.host.activate_manual_plan(name)
        self.host.manual_point_drafts = {}
        self.host.capture_active_manual_plan_state(); self.host.save_active_scenario_state(); self.refresh()

    def copy_plan(self):
        names = plan_names(get_database_path(), 'optimised')
        if not names:
            self.status.setText('There is no saved optimised plan to use as a starting point. You can create a manual recipe directly.'); return
        source, ok = QInputDialog.getItem(self, 'Start from an optimised plan', 'Optimised starting plan:', names, 0, False)
        if not ok: return
        name, ok = QInputDialog.getText(self, 'Create editable manual plan', 'New manual plan name:', text=source+' manual')
        if not ok or not name.strip(): return
        if name.strip() in [self.plans.itemText(i) for i in range(self.plans.count())]:
            self.status.setText('Choose a new manual plan name to preserve your existing manual edits.'); return
        self.host.activate_manual_plan(name.strip())
        if self.host.prepopulate_manual_from_optimised_result(source_plan_id=source):
            self.refresh()
            self.status.setText(f'Created {name.strip()} from {source}. Edit blends and timing, then submit the manual sequence to recalculate.')

    def change_point(self):
        self.point = self.points.currentData(); self.recipe = None; self.show_point()

    def show_point(self):
        if not self.point: return
        previous = self._loading; self._loading = True
        try:
            draft = self.draft()
            if self.sequence:
                self.populate_rows()
            else:
                selected = self.recipe
                self.recipes.clear(); self.recipes.addItems([r['id'] for r in draft['recipes']])
                if selected in [r['id'] for r in draft['recipes']]: self.recipes.setCurrentText(selected)
                self.recipe = self.recipes.currentText()
                self.show_recipe()
                point = next(p for p in self.feed['tipping_points'] if p['name'] == self.point)
                self.rates.setRowCount(len(self.labels))
                for i, label in enumerate(self.labels):
                    key = 'preplan' if i == 0 else f'period_{i}'
                    limit = point['targets_by_period'][key]['crusher_rate']
                    for col, value in enumerate([label, draft['rates'].get(label, draft['rates'].get(key, limit)), limit]):
                        self.rates.setItem(i, col, item(value, col == 1))
                self.rates.resizeColumnsToContents()
                self.update_recipe_preview()
            self.status.setText(f'{self.point}: {len(draft["recipes"])} blends · {len(draft["sequence"])} scheduled blends. '
                                'Edits are drafts until the manual sequence is calculated.')
        finally: self._loading = previous

    def change_recipe(self):
        self.recipe = self.recipes.currentText(); self.show_recipe()

    def new_recipe(self):
        name, ok = QInputDialog.getText(self, 'New blend', 'Blend ID:')
        if not ok or not name.strip(): return
        if any(r['id'] == name.strip() for r in self.draft()['recipes']):
            self.status.setText('This tipping point already has that Blend ID.'); return
        self.draft()['recipes'].append(dict(id=name.strip(), sources=[]))
        self.recipe = name.strip(); self.changed(); self.show_point()

    def delete_recipe(self):
        if any(str(r.get('Blend ID')) == self.recipe for r in self.draft()['sequence']):
            self.status.setText('Remove this recipe’s sequence rows before deleting it.'); return
        self.draft()['recipes'] = [r for r in self.draft()['recipes'] if r['id'] != self.recipe]
        self.recipe = None; self.changed(); self.show_point()

    def clear_point(self):
        if QMessageBox.question(self, 'Clear tipping point', f'Clear all manual blends and sequence rows for {self.point}?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        self.drafts()[self.point] = dict(recipes=[], sequence=[], rates={}, allocations={})
        self.recipe = None; self.changed(); self.show_point()

    def show_recipe(self):
        if self.sequence: return
        previous = self._loading; self._loading = True
        try:
            recipe = next((r for r in self.draft()['recipes'] if r['id'] == self.recipe), {})
            selected = {s['source']: s for s in recipe.get('sources', [])}
            available = [s for s in self.stockpiles if route_allowed(self.feed, s, self.point)]
            available += [s for s in selected if s not in available]
            clear_cells(self.sources)
            self.sources.setRowCount(len(available)); self.sources.setEnabled(bool(recipe))
            opf = next(p['opf'] for p in self.feed['tipping_points'] if p['name'] == self.point)
            point_inventory = (self.profiles.get(opf) or {}).get('inventory') or self.stockpiles
            for i, name in enumerate(available):
                source = point_inventory.get(name, {}); saved = selected.get(name, {})
                projected = saved.get('projection') or self.projections.get(name) or {}
                values = [name, saved.get('weight', 0), '', saved.get('reclaim_rate') if saved.get('reclaim_rate') is not None else '',
                    source.get('balance', 0), projected.get('balance', ''), projected.get('available', ''), '',
                    *[source.get('grade_'+g, 'AMT' if source.get('amt') else '') for g in ('fe', 'si', 'al', 'p', 'mn')]]
                for col, value in enumerate(values):
                    from GUI.BlendDisplay import number
                    shown = number(value, 0) if col in (4, 5) else number(value, 2) if col >= 8 else value
                    self.sources.setItem(i, col, item(shown, col in (1, 3)))
                self.sources.item(i, 0).setData(Qt.UserRole, deepcopy(projected))
                check = QCheckBox(); check.setChecked(bool(saved.get('projected')))
                check.setEnabled(bool(projected) and not source.get('amt')); check.toggled.connect(lambda *_: self.recipe_changed())
                self.sources.setCellWidget(i, 7, check)
            self.sources.resizeColumnsToContents(); self.update_ratios()
        finally: self._loading = previous

    def update_ratios(self):
        previous = self._loading; self._loading = True
        try:
            weights = [max(0, float(self.sources.item(i, 1).text() or 0)) for i in range(self.sources.rowCount())]
            total = sum(weights)
            for i, weight in enumerate(weights): self.sources.item(i, 2).setText(f'{100*weight/total:.3f}' if total else '0')
            self.preview.setText(f'{sum(w > 0 for w in weights)} selected sources · weights total {total:g}. '
                                 'Blank reclaim rate uses the configured equipment / route limit.')
        except ValueError: self.preview.setText('Enter numeric non-negative weights and reclaim rates.')
        finally: self._loading = previous

    def recipe_changed(self, *_):
        if self._loading: return
        self.update_ratios(); self.save_point(recipe_edit=True)

    def save_point(self, *_args, recipe_edit=False):
        if self._loading or not self.point or self.plan_id != getattr(self.host, 'active_manual_plan_id', 'Primary'): return
        draft = self.draft()
        before = deepcopy(draft)
        if not self.sequence:
            recipe = next((r for r in draft['recipes'] if r['id'] == self.recipe), None)
            if recipe:
                recipe['sources'] = [dict(source=self.sources.item(i, 0).text(), weight=self.sources.item(i, 1).text() or '0',
                    reclaim_rate=self.sources.item(i, 3).text() or None, projected=self.sources.cellWidget(i, 7).isChecked(),
                    projection=deepcopy(self.sources.item(i, 0).data(Qt.UserRole) or {})) for i in range(self.sources.rowCount())]
                if recipe_edit:
                    for row in draft['sequence']:
                        if str(row.get('Blend ID')) == self.recipe:
                            for key in list(row):
                                if key.startswith('_'): row.pop(key)
                            row['Origin'] = 'Manual'
            rates = {self.rates.item(i, 0).text(): self.rates.item(i, 1).text() for i in range(self.rates.rowCount())}
            if any(preview_number(v) != preview_number(draft['rates'].get(k, draft['rates'].get(k.lower())))
                   for k, v in rates.items() if k in draft['rates'] or k.lower() in draft['rates']):
                for row in draft['sequence']:
                    for key in ('_fixed_steady_state', '_crusher_rate', '_physical_feed_tonnes', '_stockpile_source_tonnes', '_product_build_actual_tonnes'):
                        row.pop(key, None)
                    row['Origin'] = 'Manual'
            draft['rates'] = rates
        else:
            draft['sequence'] = self.sequence_values()
        if draft != before:
            had_selections = reset_imported_timing(draft)
            if had_selections:
                self.status.setText(f'{self.point}: timing or recipe changed. Previous direct-tip selections were reset; review Steady States & Direct Tip for the new schedule.')
            if self.sequence:
                for i, row in enumerate(draft['sequence']):
                    self.rows.item(i, 1).setData(Qt.UserRole, deepcopy(row))
                    self.rows.item(i, 1).setData(Qt.UserRole+1, (self.rows.cellWidget(i, 0).currentText(),
                        self.rows.cellWidget(i, 2).dateTime().toPyDateTime(), self.rows.cellWidget(i, 3).value()))
        self.changed()
        if not self.sequence: self.update_recipe_preview()

    def update_recipe_preview(self):
        point = next(p for p in self.feed['tipping_points'] if p['name'] == self.point)
        inventory = (self.profiles.get(point['opf']) or {}).get('inventory') or self.stockpiles
        rate = preview_number(self.draft()['rates'].get('Preplan', self.draft()['rates'].get('preplan', point['targets_by_period']['preplan']['crusher_rate'])))
        self.recipe_preview.setRowCount(len(self.draft()['recipes']))
        for i, recipe in enumerate(self.draft()['recipes']):
            sources = [s for s in recipe.get('sources', []) if preview_number(s.get('weight')) > 0]
            total = sum(preview_number(s['weight']) for s in sources)
            shares = [(s, preview_number(s['weight'])/total) for s in sources] if total else []
            materials = [(s, ratio, (s.get('projection') or {}).get('material') if s.get('projected') else inventory.get(s['source'], {})) for s, ratio in shares]
            grades = []
            for grade in ('fe', 'si', 'al', 'p', 'mn'):
                if materials and all(m and m.get('grade_'+grade) is not None and not m.get('amt') for _, _, m in materials):
                    grades.append(f'{sum(ratio*preview_number(m["grade_"+grade]) for _, ratio, m in materials):.2f}')
                else: grades.append('AMT / calculate' if materials else '')
            available = [str(s['projection']['available']) for s, _ in shares if s.get('projected') and s.get('projection')]
            durations = []
            for s, ratio in shares:
                balance = preview_number((s.get('projection') or {}).get('balance') if s.get('projected') else inventory.get(s['source'], {}).get('balance'))
                if rate*ratio > 0: durations.append(balance/(rate*ratio))
            values = [recipe['id'], ', '.join(f'{s["source"]}: {ratio:.1%}' for s, ratio in shares), *grades,
                      max(available) if available else 'Opening stock', f'{min(durations):.3f}' if durations else '']
            for col, value in enumerate(values): self.recipe_preview.setItem(i, col, item(value))
        self.recipe_preview.resizeColumnsToContents()
        self.recipe_preview.setToolTip('Opening-stock estimates at the first period’s manual rate. Calculate the sequence for mapped grades, AMT progression and direct tip.')

    def changed(self):
        self.draft()['authoring_initialized'] = True
        self.host.manual_input_revision = None

    def round_recipe(self):
        from classes.ManualRatioRounding import round_feed_ratios
        try:
            values = round_feed_ratios([float(self.sources.item(i, 1).text()) for i in range(self.sources.rowCount())], int(self.rounding.currentText()[:-1]))
            self._loading = True
            for i, value in enumerate(values): self.sources.item(i, 1).setText(str(value*100))
        except ValueError as exc: self.status.setText(str(exc))
        finally: self._loading = False
        self.recipe_changed()

    def submit_recipes(self):
        try:
            from GUI.WorkflowSubmissions import can_submit
            if not can_submit(self.host, 'setup_blends'): return
            self.save_point()
            for draft in self.drafts().values(): definitions(draft)
            self.host.save_active_scenario_state()
            self.host.set_page_enabled('blend_sequence', True)
            from GUI.WorkflowSubmissions import submitted
            submitted(self.host, 'setup_blends')
            self.host.show_page('blend_sequence')
        except ValueError as exc: self.status.setText(str(exc))

    def populate_rows(self):
        values = self.draft()['sequence']; clear_cells(self.rows); self.rows.setRowCount(len(values))
        for i, row in enumerate(values):
            combo = QComboBox(); combo.addItems([r['id'] for r in self.draft()['recipes']]); combo.setCurrentText(str(row.get('Blend ID', '')))
            self.rows.setCellWidget(i, 0, combo)
            self.rows.setItem(i, 1, item(row.get('Origin', 'Manual')))
            self.rows.item(i, 1).setData(Qt.UserRole, deepcopy(row))
            start = QDateTimeEdit(); start.setDisplayFormat('dd MMM yyyy HH:mm:ss'); start.setCalendarPopup(True)
            start.setDateTime(pd.Timestamp(row.get('_exact_start') or row.get('Start Datetime') or min(self.periods.values())).to_pydatetime()); self.rows.setCellWidget(i, 2, start)
            duration = QDoubleSpinBox(); duration.setDecimals(6); duration.setRange(.000001, 24*90)
            end = pd.Timestamp(row.get('_exact_end') or row.get('End Datetime')) if row.get('_exact_end') or row.get('End Datetime') else None
            duration.setValue((end-pd.Timestamp(start.dateTime().toPyDateTime())).total_seconds()/3600 if end is not None else float(row.get('Duration (hrs)', 1)))
            self.rows.setCellWidget(i, 3, duration); self.rows.setItem(i, 4, item())
            self.rows.item(i, 1).setData(Qt.UserRole+1, (combo.currentText(), start.dateTime().toPyDateTime(), duration.value()))
            early = item(row.get('Early Start Flag', '')); self.rows.setItem(i, 5, early)
            self.rows.setItem(i, 6, item())
            for signal in (combo.activated, start.dateTimeChanged, duration.valueChanged): signal.connect(lambda *_: self.sequence_changed())
        self.update_sequence_preview(); self.rows.resizeColumnsToContents()
        self.refresh_draft_timeline()

    def refresh_draft_timeline(self):
        self.timeline.set_drafts(self.drafts(), self.feed.get('tipping_points', []), getattr(self, '_legend_report', None))

    def select_draft_interval(self, record):
        point = record['tipping_point']
        if point != self.point:
            self.points.setCurrentIndex(self.points.findData(point))
            self.point = point
            previous = self._loading; self._loading = True
            try: self.populate_rows()
            finally: self._loading = previous
        self.rows.selectRow(record['draft_index'])

    def resize_draft_interval(self, record, start, end):
        point, index = record['tipping_point'], record['draft_index']
        # Keep edits inside the planning horizon and neighbouring sequence rows.
        rows = self.drafts()[point]['sequence']
        lower, upper = pd.Timestamp(min(self.periods.values())), pd.Timestamp(max(self.periods.values()))
        if index: lower = max(lower, pd.Timestamp(rows[index-1].get('_exact_end') or rows[index-1]['End Datetime']))
        if index+1 < len(rows): upper = min(upper, pd.Timestamp(rows[index+1].get('_exact_start') or rows[index+1]['Start Datetime']))
        if record.get('edit_mode') == 'move':
            duration = end-start
            start = max(lower, min(start, upper-duration)); end = start+duration
            if end > upper:
                self.status.setText('There is not enough room between the neighbouring bars to move this interval.')
                self.refresh_draft_timeline(); return
        else: start, end = max(start, lower), min(end, upper)
        if end <= start:
            self.status.setText('The bar must have positive duration inside the planning horizon and neighbouring rows.')
            self.refresh_draft_timeline(); return
        if start == record['start_datetime'] and end == record['end_datetime']:
            self.status.setText('The bar has reached its neighbouring row or planning boundary.')
            self.refresh_draft_timeline(); return
        reset_imported_timing(self.drafts()[point])
        rows[index].update({'Start Datetime': start.to_pydatetime(), 'End Datetime': end.to_pydatetime(),
                            'Duration (hrs)': (end-start).total_seconds()/3600})
        self.points.setCurrentIndex(self.points.findData(point)); self.point = point
        self.changed(); self.show_point(); self.rows.selectRow(index)
        self.status.setText(f'{point}: draft timing updated. Review direct-tip selections and submit to recalculate.')

    def sequence_values(self):
        rows = []
        for i in range(self.rows.rowCount()):
            old = self.rows.item(i, 1).data(Qt.UserRole) or {}
            start = self.rows.cellWidget(i, 2).dateTime().toPyDateTime(); duration = self.rows.cellWidget(i, 3).value()
            name = self.rows.cellWidget(i, 0).currentText(); end = start+timedelta(hours=duration)
            changed = (name, start, duration) != self.rows.item(i, 1).data(Qt.UserRole+1)
            if not changed:
                rows.append(deepcopy(old)); continue
            row = {}
            row.update({'Blend ID': name, 'Origin': 'Manual' if changed else old.get('Origin', 'Manual'),
                'Start Datetime': start, 'Duration (hrs)': duration, 'End Datetime': end,
                'Early Start Flag': self.rows.item(i, 5).text(), 'Remaining Hrs': self.rows.item(i, 6).text()})
            rows.append(row)
        return rows

    def update_sequence_preview(self):
        remaining = {s: float(r.get('balance') or 0) for s, r in self.stockpiles.items()}
        for i in range(self.rows.rowCount()):
            start = self.rows.cellWidget(i, 2).dateTime().toPyDateTime(); duration = self.rows.cellWidget(i, 3).value()
            self.rows.item(i, 4).setText(str(start+timedelta(hours=duration)))
            recipe = next((r for r in self.draft()['recipes'] if r['id'] == self.rows.cellWidget(i, 0).currentText()), {})
            available = [pd.Timestamp(s['projection']['available']) for s in recipe.get('sources', []) if s.get('projected') and s.get('projection') and preview_number(s.get('weight'))>0]
            self.rows.item(i, 5).setText('Yes — before projected arrival' if available and pd.Timestamp(start)<max(available) else 'No')
            sources = [s for s in recipe.get('sources', []) if preview_number(s.get('weight')) > 0]
            total = sum(preview_number(s['weight']) for s in sources)
            period = next((k[:-6] for k, v in self.periods.items() if k.endswith('_start') and v <= start < self.periods[k[:-6]+'_end']), 'preplan')
            label = 'Preplan' if period == 'preplan' else period.capitalize()
            point = next(p for p in self.feed['tipping_points'] if p['name'] == self.point)
            rate = preview_number(self.draft()['rates'].get(label, self.draft()['rates'].get(period, point['targets_by_period'].get(period, {}).get('crusher_rate', 0))))
            hours = []
            for source in sources:
                name = source['source']; share = float(source['weight'])/total
                supply = float(source['projection']['balance']) if source.get('projected') and source.get('projection') else remaining.get(name, 0)
                left = supply-duration*rate*share
                remaining[name] = left
                if rate*share > 0: hours.append(left/(rate*share))
            self.rows.item(i, 6).setText(f'{min(hours):.3f}' if hours else 'Direct tip only')
            self.rows.item(i, 6).setToolTip('Estimated stockpile feed hours left after this row at the manual rate. The shared calculation uses mapped quantities, AMT chunks and direct tip for the exact depletion boundary.')

    def sequence_changed(self):
        if self._loading: return
        self.update_sequence_preview(); self.save_point()
        self.refresh_draft_timeline()

    def add_row(self):
        if not self.draft()['recipes']:
            self.status.setText('Create and submit a blend on the Manual Blending Dashboard first.'); return
        rows = self.draft()['sequence']
        begin = pd.Timestamp(rows[-1]['End Datetime']).to_pydatetime() if rows else min(self.periods.values())
        rows.append({'Blend ID': self.draft()['recipes'][0]['id'], 'Start Datetime': begin, 'Duration (hrs)': 1,
                     'End Datetime': begin+timedelta(hours=1), 'Origin': 'Manual'})
        reset_imported_timing(self.draft())
        self.changed(); self.show_point()

    def remove_rows(self):
        selected = {i.row() for i in self.rows.selectedIndexes()}
        self.draft()['sequence'] = [r for i, r in enumerate(self.draft()['sequence']) if i not in selected]
        if selected: reset_imported_timing(self.draft())
        self.changed(); self.show_point()

    def move_row(self, delta):
        index = self.rows.currentRow(); rows = self.draft()['sequence']
        if not 0 <= index+delta < len(rows): return
        rows[index], rows[index+delta] = rows[index+delta], rows[index]
        reset_imported_timing(self.draft())
        self.changed(); self.show_point(); self.rows.selectRow(index+delta)

    def join_times(self):
        rows = self.draft()['sequence']
        reset_imported_timing(self.draft())
        for previous, row in zip(rows, rows[1:]):
            start = pd.Timestamp(previous['End Datetime']).to_pydatetime()
            hours = float(row.get('Duration (hrs)') or (pd.Timestamp(row.get('_exact_end') or row['End Datetime'])-
                pd.Timestamp(row.get('_exact_start') or row['Start Datetime'])).total_seconds()/3600)
            row.update({'Start Datetime': start, 'End Datetime': start+timedelta(hours=hours), 'Duration (hrs)': hours, 'Origin': 'Manual'})
            for key in list(row):
                if key.startswith('_'): row.pop(key)
        self.changed(); self.show_point()

    def calculate(self, review=False):
        self.save_point()
        try:
            from GUI.WorkflowSubmissions import can_submit, actuals_required
            if not can_submit(self.host, 'blend_sequence'): return
            if actuals_required(self.host, 'blend_sequence'): return
            from classes.ApprovedReconciliation import missing_sources, required_message
            missing = missing_sources(vars(self.host), planning=True)
            if missing: raise ValueError(required_message(missing))
            from GUI.InventoryRefresh import issue
            message = issue(self.host, include_workflow=True)
            if message: raise ValueError(message)
            from GUI.OPFProfileLoading import ensure
            if ensure(self.host, lambda: self.calculate(review)):
                self.status.setText('Preparing OPF source grades…'); return
            from GUI.WorkflowDependencies import manual_revision
            revision = manual_revision(self.host); database = get_database_path(); plan_id = self.plan_id
            calendar = deepcopy({k: v for k, v in (self.host.calendar_inputs or {}).items() if k != 'site_context'})
            calendar['site_context'] = self.host.active_site_context()
            from GUI.MaterialFlowIntegration import topology_for_gui
            topology = topology_for_gui(self.host)
            payloads = payload_reader(self.host, calendar['site_context'], database)
            args = (deepcopy(self.drafts()), deepcopy(self.stockpiles), deepcopy(self.host.hex_sequence_table),
                deepcopy(self.periods), deepcopy(self.host.product_targets), calendar, topology)
            def work():
                planner = MultiManualPlanner(*args[:3], payloads(), *args[3:])
                states = planner.build_steady_states()
                allocations = planner.imported_allocations(states)
                return planner, states, allocations, None if review else planner.build_report(states, allocations)
            def done(value):
                if database != get_database_path() or plan_id != self.host.active_manual_plan_id or revision != manual_revision(self.host):
                    self.status.setText('Inputs changed during calculation. Submit the manual sequence again.'); return
                planner, states, allocations, report = value
                if review:
                    from GUI.ManualSteadyStateDialog import ManualSteadyStateDialog
                    from PyQt5.QtWidgets import QDialog
                    dialog = ManualSteadyStateDialog(planner, states, allocations, self)
                    if dialog.exec_() != QDialog.Accepted: return
                    allocations, report = dialog.allocations, dialog.report
                for point in planner.reset_direct_tip_points:
                    self.drafts()[point] = deepcopy(planner.drafts[point])
                for point, draft in self.drafts().items():
                    keys = {s['state_key'] for s in states if s['tipping_point'] == point}
                    draft['allocations'] = {k: v for k, v in allocations.items() if k in keys}
                    draft['direct_tip_rows'] = []
                self.host.manual_steady_states = states; self.host.manual_blend_report = report
                self._legend_report = report.copy()
                self.host.manual_input_revision = manual_revision(self.host)
                self.host.write_active_manual_plan_reports(report)
                self.host.capture_active_manual_plan_state(); self.host.save_active_scenario_state()
                from GUI.WorkflowSubmissions import submitted
                submitted(self.host, 'blend_sequence')
                self.show_point()
                self.status.setText(f'{plan_id}: calculated {len(states)} steady states across {len(planner.planners)} tipping points. Blend Plan is ready to review.')
                self.host.set_page_enabled('blend_plan', True)
            def failed(error):
                self.status.setText('Manual plan was not generated: '+str(error.get('message', error) if isinstance(error, dict) else error))
            self.host.run_background_task('Calculating manual blends and sequence…', work, done, failed)
        except ValueError as exc: self.status.setText(str(exc))
