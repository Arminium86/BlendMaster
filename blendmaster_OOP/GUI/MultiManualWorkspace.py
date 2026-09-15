"""Manual authoring with cached saved-plan hydration."""
from pathlib import Path
import pandas as pd
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QLabel
from classes.SavedResultViews import plan_names
from classes.MaterialFlowReview import saved_flow_data
from database.DatabaseContext import get_database_path


class MultiManualWorkspace(QWidget):
    def __init__(self, host, sequence=False):
        super().__init__(host)
        self.host, self.sequence = host, sequence
        self.data = {}; self.report = pd.DataFrame(); self.database = None
        self._loaded_token = self._pending_token = None
        self._load_generation = 0
        from GUI.MultiManualAuthoring import MultiManualAuthoring
        outer = QVBoxLayout(self)
        self.workspace_tabs = QTabWidget(); outer.addWidget(self.workspace_tabs)
        self.authoring = MultiManualAuthoring(host, sequence)
        self.workspace_tabs.addTab(self.authoring, 'Sequence editor' if sequence else 'Blend Configuration')
        self.plans = self.authoring.plans
        self.plans.activated.connect(self.load_plan)
        self.status = QLabel(self); self.status.hide()

    def refresh(self, *, force=False):
        self.authoring.refresh()
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
        self.report=pd.DataFrame(); self.data={}
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
            self.report=self.data['frames']['feed'].copy().reset_index(drop=True)
            self.report.attrs={}
            from classes.ReportTiming import restore_report_timing
            self.report=restore_report_timing(self.report)
            self.authoring.set_projections(self.data['frames'].get('build',pd.DataFrame()))
            if name == getattr(self.host,'active_manual_plan_id','Primary'):
                self.authoring.hydrate_report(self.report,kind=self.kind)
            self.status.setText(f'{name}: saved {self.kind} plan loaded.')
        def failed(error):
            if generation != self._load_generation:
                return
            self._pending_token = self._loaded_token = None
            self.status.setText(str(error))
            self.authoring.status.setText('Could not load the saved plan: '+str(error))
            if views is not None:
                views.loaded.pop('blend_sequence' if self.sequence else 'setup_blends', None)
        self.host.run_background_task('Loading manual allocations…',work,done,failed,show_progress=False)


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
