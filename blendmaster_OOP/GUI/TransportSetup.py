"""Native per-crusher conveyor/COS setup and opening-movement review."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QDoubleSpinBox, QSpinBox, QPushButton, QTabWidget
from classes.TransportSettings import transport_settings


class TransportSetup(QWidget):
    submit_requested = pyqtSignal()
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        title = QLabel('Conveyors & COS')
        title.setStyleSheet('font-size:20px;font-weight:700;color:#17324d')
        layout.addWidget(title)
        note = QLabel('Optional FIFO conveyor and coarse ore stockpile (COS) modelling. Capacities are physical ROM WMT. '
            'Crusher targets apply at tipping; Product Targets apply when material reaches the OPF. '
            'Opening contents come from actual movements before the scenario start, using the first operating Calendar rate. '
            'Conveyors and COS pause when that crusher is stopped.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.table = QTableWidget(0,9)
        self.table.setHorizontalHeaderLabels(['Tipping point','Enable','Conveyor WMT','COS WMT','COS chunks',
                                             'Rehandle payload WMT','Spot seconds','Dump seconds','Reference rate t/h'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tabs.addTab(self.table,'Conveyor & COS')
        self.history = QTableWidget()
        self.history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabs.addTab(self.history,'Opening actual movements')
        buttons = QHBoxLayout()
        self.submit = QPushButton('Submit')
        self.submit.clicked.connect(self.submit_requested)
        self.refresh = QPushButton('Refresh opening actual movements')
        self.refresh.clicked.connect(self.refresh_requested)
        buttons.addWidget(self.submit)
        buttons.addWidget(self.refresh)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel('Transport is disabled by default. Submit Site Configuration and Calendar before loading opening contents.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)

    def set_context(self, points, settings, history=None):
        values = transport_settings(settings)['tipping_points']
        self.table.setRowCount(len(points))
        self.points = points
        for row, point in enumerate(points):
            cfg = values.get(point['name'], {})
            for column, value in [(0,point['name']),(8,point.get('opening_rate',0))]:
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row,column,item)
            enabled = QCheckBox()
            enabled.setChecked(cfg.get('enabled',False))
            self.table.setCellWidget(row,1,enabled)
            controls = []
            for column,key,default in [(2,'conveyor_capacity_wmt',0),(3,'cos_capacity_wmt',0),(4,'cos_chunks',10),
                                     (5,'rehandle_payload_wmt',200),(6,'spot_seconds',30),(7,'dump_seconds',30)]:
                control = QSpinBox() if column == 4 else QDoubleSpinBox()
                control.setRange(1 if column in (4,5) else 0,1000 if column == 4 else 1e9)
                if column != 4:
                    control.setDecimals(3)
                control.setValue(cfg.get(key,default))
                self.table.setCellWidget(row,column,control)
                controls.append(control)
            def sync(_=None, enabled=enabled, controls=controls):
                for control in controls[:3]:
                    control.setEnabled(enabled.isChecked())
                for control in controls[3:]:
                    control.setEnabled(enabled.isChecked() and controls[0].value()>0)
            enabled.toggled.connect(sync)
            controls[0].valueChanged.connect(sync)
            sync()
        self.show_history(history or {})

    def settings(self):
        fields = ['conveyor_capacity_wmt','cos_capacity_wmt','cos_chunks','rehandle_payload_wmt','spot_seconds','dump_seconds']
        points = {}
        for row in range(self.table.rowCount()):
            cfg = dict(enabled=self.table.cellWidget(row,1).isChecked())
            cfg.update({key:self.table.cellWidget(row,column).value() for column,key in enumerate(fields,2)})
            points[self.table.item(row,0).text()] = cfg
        return transport_settings(dict(tipping_points=points))

    def show_history(self, bundle):
        columns = [('tipping_point','Tipping point'),('opf','OPF'),('time','Actual delivery'),
                   ('SOURCE_FMS','Source'),('wmt','ROM WMT'),('INTERNAL_ID','Movement ID')]
        self.history.setColumnCount(len(columns))
        self.history.setHorizontalHeaderLabels([label for _,label in columns])
        records = bundle.get('records',[])
        self.history.setRowCount(len(records))
        for r,row in enumerate(records):
            for c,(key,_) in enumerate(columns):
                self.history.setItem(r,c,QTableWidgetItem(str(row.get(key,''))))
        self.history.resizeColumnsToContents()
        if bundle:
            self.status.setText(f'{len(records):,} actual movements loaded. Grades use the configured source fields and OPF reconciliation. '
                'Unobserved opening capacity stays empty; no missing grades or tonnes are invented.')
