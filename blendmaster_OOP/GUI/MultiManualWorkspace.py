"""Manual allocations and sequence for simultaneous physical tipping points."""
from copy import deepcopy
from pathlib import Path
import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QTableWidget,QTableWidgetItem,QDateTimeEdit,QDoubleSpinBox,QTabWidget)
from GUI.BlendSequenceTimeline import BlendSequenceTimeline
from classes.SavedResultViews import plan_names
from classes.MaterialFlowReview import saved_flow_data
from database.DatabaseContext import get_database_path


class MultiManualWorkspace(QWidget):
    def __init__(self,host,sequence=False):
        super().__init__(host)
        self.host,self.sequence=host,sequence
        self.data={}; self.report=pd.DataFrame(); self.database=None
        self._loaded_token = self._pending_token = None
        self._load_generation = 0
        from GUI.MultiManualAuthoring import MultiManualAuthoring
        outer=QVBoxLayout(self)
        self.workspace_tabs=QTabWidget(); outer.addWidget(self.workspace_tabs)
        self.authoring=MultiManualAuthoring(host,sequence)
        self.workspace_tabs.addTab(self.authoring,'Sequence editor' if sequence else 'Recipes and rates')
        allocation_page=QWidget(); self.workspace_tabs.addTab(allocation_page,'Saved allocation adjustments')
        layout=QVBoxLayout(allocation_page)
        self.workspace_tabs.currentChanged.connect(self.workspace_tab_changed)
        bar=QHBoxLayout(); bar.addWidget(QLabel('Manual plan'))
        self.plans=QComboBox(); self.plans.currentIndexChanged.connect(self.load_plan); bar.addWidget(self.plans)
        self.starting=QComboBox(self); self.starting.hide()
        copy_button=QPushButton('Create manual plan from optimised…'); copy_button.clicked.connect(self.copy_plan); bar.addWidget(copy_button)
        reload=QPushButton('Refresh'); reload.clicked.connect(lambda: self.refresh(force=True)); bar.addWidget(reload)
        bar.addStretch(); layout.addLayout(bar)
        self.status=QLabel(''); self.status.setTextFormat(Qt.PlainText); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.timeline=BlendSequenceTimeline(self); self.timeline.interval_selected.connect(self.choose_interval)
        if sequence: layout.addWidget(self.timeline,2)
        else: self.timeline.hide()
        bar=QHBoxLayout(); bar.addWidget(QLabel('Tipping point / OPF'))
        self.points=QComboBox(); self.points.currentIndexChanged.connect(self.show_point); bar.addWidget(self.points)
        bar.addWidget(QLabel('Blend interval'))
        self.intervals=QComboBox(); self.intervals.currentIndexChanged.connect(self.show_interval); bar.addWidget(self.intervals)
        bar.addStretch(); layout.addLayout(bar)
        bar=QHBoxLayout(); bar.addWidget(QLabel('Start'))
        self.start=QDateTimeEdit(); self.start.setDisplayFormat('dd MMM yyyy HH:mm:ss'); self.start.setCalendarPopup(True); bar.addWidget(self.start)
        bar.addWidget(QLabel('Duration (hours)'))
        self.duration=QDoubleSpinBox(); self.duration.setDecimals(6); self.duration.setRange(.000001,24*90); bar.addWidget(self.duration)
        self.update_button=QPushButton('Apply interval edits'); self.update_button.clicked.connect(self.apply_edits); bar.addWidget(self.update_button)
        bar.addStretch(); layout.addLayout(bar)
        self.sources=QTableWidget(); self.sources.setColumnCount(6)
        self.sources.setHorizontalHeaderLabels(['Source','Type','Tonnes','Ratio (%)','Fe (%)','P (%)'])
        self.sources.setAlternatingRowColors(True); layout.addWidget(self.sources,1)
        self.note=QLabel('Edit tonnes or timing for the selected interval. Shared stock, Calendar limits and Conveyor/COS arrivals are checked across every tipping point before applying.')
        self.note.setWordWrap(True); layout.addWidget(self.note)
        continue_button = QPushButton('Continue to Blend Plan' if sequence else 'Continue to Manual Blend Sequence')
        continue_button.setToolTip('Apply any interval edits before continuing.')
        continue_button.clicked.connect(lambda: host.advance_workspace('blend_sequence' if sequence else 'setup_blends'))
        layout.addWidget(continue_button)

    def workspace_tab_changed(self, index):
        if index == 0:
            self.authoring.refresh()
        elif getattr(self, 'kind', None) == 'manual':
            activate = getattr(self.host, 'activate_manual_plan', None)
            if callable(activate): activate(self.plans.currentText())

    def refresh(self, *, force=False):
        self.authoring.refresh()
        selected=getattr(self.host,'active_manual_plan_id',None) or self.plans.currentText() or 'Primary'
        database=get_database_path()
        try:
            manual,optimised=plan_names(database,'manual'),plan_names(database,'optimised')
        except Exception as exc:
            self.status.setText(str(exc)); return
        self.plans.blockSignals(True); self.plans.clear(); self.plans.addItems(manual or optimised)
        if selected in manual or selected in optimised: self.plans.setCurrentText(selected)
        self.plans.blockSignals(False)
        selected_opt=self.starting.currentText() or self.host.selected_optimisation_plan_id()
        self.starting.clear(); self.starting.addItems(optimised)
        if selected_opt in optimised: self.starting.setCurrentText(selected_opt)
        self.load_plan(force=force)

    def load_plan(self, *_args, force=False):
        name=self.plans.currentText(); database=get_database_path()
        versions = []
        for path in (database, database + '-wal'):
            try:
                info = Path(path).stat()
                versions.append((info.st_size, info.st_mtime_ns))
            except OSError:
                versions.append(None)
        views = vars(self.host).get('_workflow_views')
        token = (database, vars(self.host).get('active_scenario_id'), name,
                 tuple(versions), views.revision if views is not None else 0,
                 repr(vars(self.host).get('multi_feed_configuration')))
        if self._pending_token == token or (not force and self._loaded_token == token):
            return
        self._load_generation += 1
        generation = self._load_generation
        self._pending_token, self._loaded_token = token, None
        selected_point=self.points.currentData()
        self.report=pd.DataFrame(); self.data={}; self.points.clear(); self.intervals.clear(); self.sources.setRowCount(0)
        self.timeline.set_report(pd.DataFrame()); self.update_button.setEnabled(False)
        self.database=database; self.plan_id=name
        if not name:
            self._pending_token = None
            self.status.setText('No saved optimised or manual plan exists for this site.'); return
        self.status.setText('Loading saved allocations…')
        def work():
            kind='manual' if name in plan_names(database,'manual') else 'optimised'
            return kind,saved_flow_data(name,database,plan_type=kind)
        def done(value):
            if generation != self._load_generation:
                return
            self._pending_token = None
            if (database!=get_database_path() or name!=self.plans.currentText()
                    or token[1] != vars(self.host).get('active_scenario_id')): return
            self._loaded_token = token
            self.kind,self.data=value
            activate = getattr(self.host, 'activate_manual_plan', None)
            if self.kind == 'manual' and callable(activate) and self.workspace_tabs.currentIndex() == 1:
                activate(name)
                self.authoring.refresh()
            self.report=self.data['frames']['feed'].copy().reset_index(drop=True)
            self.report.attrs={}
            from classes.ReportTiming import restore_report_timing
            self.report=restore_report_timing(self.report)
            self.authoring.set_projections(self.data['frames'].get('build',pd.DataFrame()))
            if name == getattr(self.host,'active_manual_plan_id','Primary'):
                self.authoring.hydrate_report(self.report,kind=self.kind)
            self.timeline.set_report(self.report,(self.host.multi_feed_configuration or {}).get('tipping_points',[]))
            if self.report.empty:
                self.status.setText(f'{name} has no saved feed allocations.'); return
            points=self.report[['tipping_point','opf']].drop_duplicates()
            for row in points.itertuples(index=False): self.points.addItem(f'{row.tipping_point} / {row.opf}',row.tipping_point)
            previous=self.points.findData(selected_point)
            if previous>=0: self.points.setCurrentIndex(previous)
            self.update_button.setEnabled(self.kind=='manual')
            self.status.setText(f'{name}: {len(self.report):,} saved {self.kind} allocation rows across {len(points)} tipping points.'+
                (' Copy the optimised allocations to start editing a manual plan.' if self.kind=='optimised' else ''))
        def failed(error):
            if generation != self._load_generation:
                return
            self._pending_token = self._loaded_token = None
            self.status.setText(str(error))
            if views is not None:
                views.loaded.pop('blend_sequence' if self.sequence else 'setup_blends', None)
        self.host.run_background_task('Loading manual allocations…',work,done,failed,show_progress=False)

    def show_point(self):
        self.intervals.blockSignals(True); self.intervals.clear()
        if not self.report.empty:
            rows=self.report.loc[self.report.tipping_point.eq(self.points.currentData())]
            for (state,blend,start,end),group in rows.groupby(['steady_state_number','blend_ID','start_datetime','end_datetime'],dropna=False,sort=False):
                self.intervals.addItem(f'Blend {blend} · state {state} · {pd.Timestamp(start):%d %b %H:%M}',group.index.tolist())
        self.intervals.blockSignals(False); self.show_interval()

    def show_interval(self):
        indices=self.intervals.currentData() or []
        self.sources.setRowCount(len(indices))
        if not indices: return
        rows=self.report.loc[indices]; first=rows.iloc[0]
        self.start.setDateTime(pd.Timestamp(first.start_datetime).to_pydatetime())
        self.duration.setValue((pd.Timestamp(first.end_datetime)-pd.Timestamp(first.start_datetime)).total_seconds()/3600)
        self.original_controls=(self.start.dateTime().toPyDateTime(),self.duration.value())
        total=pd.to_numeric(rows.source_actual_tonnes,errors='coerce').fillna(0).sum()
        for index,(_,row) in enumerate(rows.iterrows()):
            amount=float(row.source_actual_tonnes)
            values=[row.source,row.source_type,f'{amount:.6f}',f'{amount/total*100:.2f}' if total else '0',
                    *[f'{float(row[k]):.4f}' if pd.notna(row.get(k)) else '' for k in ('source_grade_fe','source_grade_p')]]
            for column,value in enumerate(values):
                item=QTableWidgetItem('' if value is None or pd.isna(value) else str(value))
                if column!=2 or self.kind!='manual': item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.sources.setItem(index,column,item)
        self.sources.resizeColumnsToContents()

    def choose_interval(self,record):
        index=self.points.findData(record['tipping_point'])
        if index>=0: self.points.setCurrentIndex(index)
        for index in range(self.intervals.count()):
            indices=self.intervals.itemData(index)
            if indices and self.report.loc[indices[0],'steady_state_number']==record['steady_state_number']:
                self.intervals.setCurrentIndex(index); break

    def copy_plan(self):
        self.authoring.copy_plan()
        self.workspace_tabs.setCurrentIndex(0)

    def apply_edits(self):
        indices=self.intervals.currentData() or []
        if not indices or self.kind!='manual': return
        edited=self.report.copy()
        try:
            from GUI.InventoryRefresh import issue
            if issue(self.host, include_workflow=True): raise ValueError(issue(self.host, include_workflow=True))
            from GUI.WorkflowDependencies import input_revision, manual_revision
            if vars(self.host).get('manual_input_revision') != manual_revision(self.host):
                raise ValueError('The saved allocations use an earlier input version. Submit the manual recipes and sequence for the current inputs before adjusting saved allocation rows.')
            for index,row in enumerate(indices): edited.loc[row,'source_actual_tonnes']=float(self.sources.item(index,2).text())
            edited['start_datetime']=pd.to_datetime(edited.start_datetime)
            edited['end_datetime']=pd.to_datetime(edited.end_datetime)
            begin=pd.Timestamp(self.start.dateTime().toPyDateTime())
            if (self.start.dateTime().toPyDateTime(),self.duration.value()) != self.original_controls:
                edited.loc[indices,'start_datetime']=begin
                edited.loc[indices,'end_datetime']=begin+pd.Timedelta(hours=self.duration.value())
            from classes.PeriodManager import PeriodManager
            periods=PeriodManager(self.host.planning_period_count()); periods.calculate_periods(self.host.start_time_choice)
            calendar=deepcopy(self.host.calendar_inputs or {})
            calendar['site_context']=self.host.active_site_context()
            original=self.report.copy(); topology=self.data.get('graph'); database=self.database; name=self.plan_id
            original.attrs['manual_build_report']=self.data['frames'].get('build',pd.DataFrame()).copy()
            targets=deepcopy(self.host.product_targets or [])
            from GUI.WorkflowDependencies import input_revision
            revision=input_revision(self.host)
            from classes.ManualAllocationEdits import recalculate
            def done(report):
                if database!=get_database_path() or revision!=input_revision(self.host):
                    self.status.setText('Inputs changed during calculation. Apply the edits again.'); return
                self.host.active_manual_plan_id=name; self.host.manual_blend_report=report
                from classes.MultiManualPlan import from_report
                self.host.manual_point_drafts=from_report(report,getattr(self.host,'updated_stockpile_data',{}))
                from GUI.WorkflowDependencies import manual_revision
                self.host.manual_input_revision=manual_revision(self.host)
                self.host.write_active_manual_plan_reports(report)
                self.host.capture_active_manual_plan_state()
                self.host.save_active_scenario_state()
                from GUI.WorkflowViews import schedule
                schedule(self.host,results=True,charts=True)
                self.load_plan()
            self.host.run_background_task('Checking manual allocations…',
                lambda:recalculate(original,edited,calendar,periods.get_periods(),targets,topology),done,
                lambda error:self.status.setText('Edits were not applied: '+str(error)))
        except Exception as exc:
            self.status.setText('Edits were not applied: '+str(exc))


def install(host):
    host.multi_manual_dashboard=MultiManualWorkspace(host)
    host.setup_blends_tab_layout.addWidget(host.multi_manual_dashboard)
    host.multi_manual_sequence=MultiManualWorkspace(host,sequence=True)
    host.blend_sequence_tab_layout.addWidget(host.multi_manual_sequence)
    host.multi_manual_dashboard.hide(); host.multi_manual_sequence.hide()


def show_layout(layout, visible, except_widget):
    for index in range(layout.count()):
        item=layout.itemAt(index)
        if item.widget() is not None:
            if item.widget() is not except_widget: item.widget().setVisible(visible)
        elif item.layout() is not None: show_layout(item.layout(),visible,except_widget)


def set_mode(host,multi):
    for layout,widget in [(host.setup_blends_tab_layout,host.multi_manual_dashboard),
                          (host.blend_sequence_tab_layout,host.multi_manual_sequence)]:
        show_layout(layout,not multi,widget); widget.setVisible(multi)


def refresh(host,sequence=False):
    widget=host.multi_manual_sequence if sequence else host.multi_manual_dashboard
    set_mode(host,True); widget.refresh()
