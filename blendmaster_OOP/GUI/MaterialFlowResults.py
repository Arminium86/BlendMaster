"""Time-slider review of the immutable topology and output of a saved plan."""
import json
import pandas as pd
from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, pyqtSignal
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QPushButton,QLabel,QSlider,QTabWidget,QTableView,QSplitter,QHeaderView
from GUI.MaterialFlowGraph import MaterialFlowGraph
from classes.MaterialFlowReview import FlowTimeline,saved_flow_data,saved_flow_plans
from database.DatabaseContext import get_database_path


class FrameModel(QAbstractTableModel):
    def __init__(self,frame,parent=None):
        super().__init__(parent)
        self.frame = frame.reset_index(drop=True)
    def rowCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame)
    def columnCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame.columns)
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole,Qt.ToolTipRole):
            return None
        if not (0 <= index.row() < self.rowCount() and 0 <= index.column() < self.columnCount()):
            return None
        value = self.frame.iat[index.row(),index.column()]
        if isinstance(value,(dict,list,tuple)):
            return json.dumps(value,default=str)
        if pd.isna(value):
            return ''
        if isinstance(value,float):
            return f'{value:,.4f}'.rstrip('0').rstrip('.')
        return str(value)
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role in (Qt.DisplayRole,Qt.ToolTipRole):
            if orientation == Qt.Vertical:
                return str(section+1) if 0 <= section < self.rowCount() else None
            # Qt can request headers while an empty/loading model replaces
            # the previous table. Never index outside the current schema.
            if orientation != Qt.Horizontal or not 0 <= section < self.columnCount():
                return None
            column=str(self.frame.columns[section])
            labels={'steady_state_number':'State','steady_state_duration':'Duration (h)',
                    'Steady State Duration (hrs)':'Duration (h)','Optimiser Grade Stream':'Grade stream',
                    'chunk_capacity_wmt':'Chunk WMT capacity'}
            return labels.get(column,column.replace('_',' ').title()) if role==Qt.DisplayRole else column


class MaterialFlowResults(QWidget):
    positions_changed = pyqtSignal(dict)

    def __init__(self,parent=None,run_async=None,positions=None):
        super().__init__(parent)
        self.run_async,self.get_positions = run_async,positions or (lambda:{})
        self.timeline = None
        self._request = 0
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Saved plan'))
        self.plans = QComboBox()
        self.plans.currentIndexChanged.connect(self.load_plan)
        bar.addWidget(self.plans)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_button)
        bar.addStretch()
        layout.addLayout(bar)
        self.time_label = QLabel('Run a plan to review material flow.')
        self.time_label.setStyleSheet('font-size:16px;font-weight:600;color:#17324d')
        layout.addWidget(self.time_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self.show_time)
        self.slider.setEnabled(False)
        layout.addWidget(self.slider)
        splitter = QSplitter(Qt.Vertical)
        self.graph = MaterialFlowGraph()
        self.graph.positions_changed.connect(self.positions_changed)
        splitter.addWidget(self.graph)
        self.tabs = QTabWidget()
        self.tables = {}
        for caption in ('Tipping transactions','OPF arrivals','Conveyor / COS contents','Transport movements','Product contributions'):
            table = QTableView()
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            table.horizontalHeader().setDefaultSectionSize(155)
            table.setSelectionBehavior(QTableView.SelectRows)
            self.tabs.addTab(table,caption)
            self.tables[caption] = table
        from GUI.COSProfile import COSProfile
        self.cos_profile=COSProfile()
        self.tabs.addTab(self.cos_profile,'COS profile')
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0,3)
        splitter.setStretchFactor(1,2)
        layout.addWidget(splitter)
        self.note = QLabel()
        self.note.setTextFormat(Qt.PlainText)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def refresh(self):
        selected = self.plans.currentText()
        try:
            plans = saved_flow_plans()
        except Exception as exc:
            self.failed(str(exc)); return
        self.plans.blockSignals(True)
        self.plans.clear(); self.plans.addItems(plans)
        if selected in plans:
            self.plans.setCurrentText(selected)
        self.plans.blockSignals(False)
        self.load_plan()

    def load_plan(self):
        self._request += 1
        request = self._request
        plan = self.plans.currentText()
        database = get_database_path()
        self.timeline = None
        self.slider.setEnabled(False)
        self.graph.scene.clear(); self.graph.nodes={}; self.graph.edges=[]
        self.cos_profile.set_frame(pd.DataFrame(),{})
        for table in self.tables.values():
            old=table.model()
            table.setModel(FrameModel(pd.DataFrame(),table))
            if old is not None:
                old.deleteLater()
        if not plan:
            self.time_label.setText('No saved plan is available.')
            self.note.clear(); return
        self.time_label.setText('Loading saved material flow...')
        def finish(data):
            if request != self._request or database != get_database_path():
                return
            self.set_data(data)
        def failed(message):
            if request == self._request:
                self.failed(message)
        if self.run_async:
            self.run_async(lambda:saved_flow_data(plan,database),finish,failed)
        else:
            try:
                finish(saved_flow_data(plan,database))
            except Exception as exc:
                failed(str(exc))

    def failed(self,message):
        self.time_label.setText('Material flow is unavailable.')
        self.note.setText(str(message))
        self.slider.setEnabled(False)

    def set_data(self,data):
        if not data.get('graph'):
            self.failed('This saved result predates material-flow snapshots. Rerun the plan to create its diagram.')
            return
        self.timeline = FlowTimeline(data['graph'],data['frames'],data.get('warnings',[]))
        self.graph.set_graph(data['graph'],self.get_positions())
        self.slider.blockSignals(True)
        self.slider.setRange(0,max(0,len(self.timeline.times)-1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.slider.setEnabled(bool(self.timeline.times))
        enabled = data['graph'].get('properties',{}).get('latency_enabled')
        caveat = ('Arrivals drive Product Targets. Source depletion is recorded at tipping. Closing contents are retained at the plan stop; no drain extension. '
                  'Actual movement tonnes use modelled, reconciled chemistry. Inferred opening feed: 0 WMT.'
                  if enabled else 'Transport is disabled. Tipping and OPF arrival occur in the same steady state.')
        self.note.setText(caveat+'\n'+'\n'.join(data.get('warnings',[])))
        self.show_time(0)

    def show_time(self,index):
        if self.timeline is None:
            return
        frame = self.timeline.frame(index)
        self.time_label.setText(frame['caption'])
        self.graph.show_frame(frame['annotations'],frame['active_edges'])
        self.cos_profile.set_frame(frame['tables'].get('Conveyor / COS contents',pd.DataFrame()),self.timeline.graph)
        for name,table in self.tables.items():
            old = table.model()
            table.setModel(FrameModel(frame['tables'].get(name,pd.DataFrame()),table))
            if old is not None:
                old.deleteLater()
