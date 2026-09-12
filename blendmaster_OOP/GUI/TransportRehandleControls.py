"""Per-conveyor payload and service-time controls on Decision Levers."""
from copy import deepcopy
from PyQt5.QtCore import pyqtSignal,Qt
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QDoubleSpinBox,QHeaderView
from classes.TransportSettings import transport_settings

class TransportRehandleControls(QWidget):
    changed=pyqtSignal(dict)
    def __init__(self,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(QLabel('Conveyor rehandle payloads'))
        note=QLabel('Payload and spot/dump times apply to rehandle feed at each enabled conveyor.')
        note.setWordWrap(True); layout.addWidget(note)
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['Tipping point','Payload ROM WMT','Spot seconds','Dump seconds'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        self.set_settings({})

    def set_settings(self,value):
        self.value=transport_settings(value)
        points={name:row for name,row in self.value['tipping_points'].items()
                if row['enabled'] and row['conveyor_capacity_wmt']>0}
        self.table.setRowCount(len(points))
        self.setVisible(bool(points))
        self.table.setMaximumHeight(65+32*len(points))
        for row,(name,settings) in enumerate(points.items()):
            self.table.setItem(row,0,QTableWidgetItem(name))
            self.table.item(row,0).setFlags(self.table.item(row,0).flags() & ~Qt.ItemIsEditable)
            for column,key in enumerate(('rehandle_payload_wmt','spot_seconds','dump_seconds'),1):
                control=QDoubleSpinBox()
                control.setRange(.001 if column==1 else 0,1e9)
                control.setDecimals(3); control.setValue(settings[key])
                self.table.setCellWidget(row,column,control)
                control.valueChanged.connect(lambda value,name=name,key=key:self.update_value(name,key,value))

    def update_value(self,name,key,value):
        self.value['tipping_points'][name][key]=value
        self.changed.emit(deepcopy(self.value))
