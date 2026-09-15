"""Native, point-scoped schedule shared by optimised and manual result views."""
import hashlib
import html
import pandas as pd
from PyQt5.QtCore import Qt, QRectF, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QBrush, QPen, QPainter, QFont
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSplitter, QTableView, QGraphicsView, QGraphicsScene, QGraphicsRectItem)
from GUI.MaterialFlowResults import FrameModel


def sequence_data(report, points=()):
    """One interval per physical point/state; never combine simultaneous feeds."""
    frame = pd.DataFrame(report).copy().reset_index(drop=True)
    if frame.empty:
        return frame, []
    frame.attrs = {}
    from classes.ReportTiming import restore_report_timing
    frame=restore_report_timing(frame)
    for key, default in [('opf', ''), ('tipping_point', 'Crusher'), ('blend_ID', ''),
                         ('steady_state_number', 0), ('source_type', '')]:
        if key not in frame:
            frame[key] = default
        frame[key] = frame[key].fillna(default)
    for key in ('start_datetime', 'end_datetime'):
        frame[key] = pd.to_datetime(frame[key], errors='coerce')
    frame['source_actual_tonnes'] = pd.to_numeric(frame.source_actual_tonnes, errors='coerce').fillna(0)
    frame = frame.loc[(frame.source_actual_tonnes > 0) & (frame.end_datetime > frame.start_datetime)
                      & frame.source_type.ne('transport')].copy()
    keys = ['opf', 'tipping_point', 'steady_state_number', 'blend_ID', 'start_datetime', 'end_datetime']
    intervals = []
    for identity, rows in frame.groupby(keys, sort=False, dropna=False):
        record = dict(zip(keys, identity))
        tonnes = float(rows.source_actual_tonnes.sum())
        hours = (record['end_datetime']-record['start_datetime']).total_seconds()/3600
        amounts = rows.groupby('source', sort=False).source_actual_tonnes.sum()
        record.update(tonnes=tonnes, rate=tonnes/hours, row_indices=rows.index.tolist(),
                      direct_tip_ratio=float(rows.loc[rows.source_type.eq('grade_block'), 'source_actual_tonnes'].sum())/tonnes,
                      sources='\n'.join(f'{name}: {amount:,.1f} t · {amount/tonnes:.2%}' for name, amount in amounts.items()))
        intervals.append(record)
    return frame, intervals


class IntervalItem(QGraphicsRectItem):
    def __init__(self, rect, index, record, owner):
        super().__init__(rect)
        self.index, self.owner = index, owner
        color = QColor.fromHsv(int(hashlib.sha1(str(record['blend_ID']).encode()).hexdigest()[:4], 16) % 360, 135, 190)
        self.setBrush(QBrush(color)); self.setPen(QPen(color.darker(115)))
        self.setFlag(self.ItemIsSelectable)
        self.setToolTip(html.escape(f"{record['opf']} / {record['tipping_point']}\nBlend {record['blend_ID']} · state {record['steady_state_number']}\n"
            f"{record['start_datetime']} → {record['end_datetime']}\n{record['tonnes']:,.1f} t · {record['rate']:,.1f} t/h\n"
            f"Direct tip: {record['direct_tip_ratio']:.2%}\n{record['sources']}").replace('\n','<br>'))

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.owner.select_interval(self.index)


class BlendSequenceTimeline(QWidget):
    interval_selected = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.report, self.intervals, self.configured_points = pd.DataFrame(), [], []
        self.zoom = 1.0
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        bar = QHBoxLayout(); bar.addWidget(QLabel('Tipping point'))
        self.points = QComboBox(); self.points.addItem('All tipping points', None)
        self.points.currentIndexChanged.connect(self.draw)
        bar.addWidget(self.points)
        for caption, action in [('−', lambda: self.set_zoom(self.zoom/1.6)), ('+', lambda: self.set_zoom(self.zoom*1.6)),
                                ('Fit', lambda: self.set_zoom(1))]:
            button = QPushButton(caption); button.clicked.connect(action); bar.addWidget(button)
        bar.addStretch(); self.status = QLabel('No saved sequence is available.'); self.status.setWordWrap(True)
        bar.addWidget(self.status); layout.addLayout(bar)
        note = QLabel('Bars show new crusher feed, coloured by Blend ID. Opening conveyor/COS discharge is shown in Material Flow and OPF Production.')
        note.setWordWrap(True); layout.addWidget(note)
        splitter = QSplitter(Qt.Vertical)
        self.scene = QGraphicsScene(self); self.view = QGraphicsView(self.scene)
        self.view.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.view.setRenderHint(QPainter.Antialiasing); self.view.setMinimumHeight(230)
        self.view.setBackgroundBrush(QColor('#ffffff'))
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        splitter.addWidget(self.view)
        self.details = QTableView(); self.details.setAlternatingRowColors(True)
        self.details.setModel(FrameModel(pd.DataFrame(), self.details))
        self.details.hide(); splitter.addWidget(self.details)
        splitter.setStretchFactor(0,3); splitter.setStretchFactor(1,1)
        layout.addWidget(splitter,1)

    def set_report(self, report, points=()):
        selected = self.points.currentData()
        self.report, self.intervals = sequence_data(report)
        self.configured_points = [(str(p.get('opf') or ''), str(p['name'])) for p in points]
        lanes = sorted(set(self.configured_points) | {(r['opf'],r['tipping_point']) for r in self.intervals})
        self.points.blockSignals(True); self.points.clear(); self.points.addItem('All tipping points', None)
        for lane in lanes:
            self.points.addItem(' / '.join(v for v in lane if v), lane)
        found = self.points.findData(selected)
        self.points.setCurrentIndex(max(0,found)); self.points.blockSignals(False)
        self.details.hide(); self.draw()

    def set_zoom(self, zoom):
        self.zoom = max(1, min(zoom, 32)); self.draw()

    def draw(self):
        self.scene.clear()
        chosen = self.points.currentData()
        lanes = sorted(set(self.configured_points) | {(r['opf'],r['tipping_point']) for r in self.intervals})
        if chosen:
            lanes = [tuple(chosen)]
        if not self.intervals:
            self.status.setText('No saved sequence is available.'); return
        start = min(r['start_datetime'] for r in self.intervals)
        end = max(r['end_datetime'] for r in self.intervals)
        duration = max(1, (end-start).total_seconds())
        left, width = 205, max(480, self.view.viewport().width()-235)*self.zoom
        bottom = 65+len(lanes)*68
        for tick in range(7):
            at = start + (end-start)*tick/6
            x = left+width*tick/6
            self.scene.addLine(x,45,x,bottom,QPen(QColor('#e2e8f0')))
            item = self.scene.addText(at.strftime('%d %b\n%H:%M'), QFont('Segoe UI',8))
            item.setPos(x-24,2)
        count = 0
        for lane_index, lane in enumerate(lanes):
            y = 64+lane_index*68
            label = self.scene.addText('\n'.join(v for v in lane if v),QFont('Segoe UI',10))
            label.setPos(5,y-4)
            active = False
            for index, record in enumerate(self.intervals):
                if (record['opf'],record['tipping_point']) != lane:
                    continue
                active=True; count+=1
                x = left+(record['start_datetime']-start).total_seconds()/duration*width
                w = (record['end_datetime']-record['start_datetime']).total_seconds()/duration*width
                self.scene.addItem(IntervalItem(QRectF(x,y,max(.8,w),34),index,record,self))
            if not active:
                item = self.scene.addText('No tipping in this saved plan',QFont('Segoe UI',9))
                item.setDefaultTextColor(QColor('#64748b')); item.setPos(left,y)
        self.scene.setSceneRect(0,0,left+width+35,bottom)
        self.status.setText(f'{len(lanes)} tipping points · {count:,} intervals · select a bar for sources')

    def select_interval(self, index):
        record = self.intervals[index]
        columns = [c for c in ['tipping_point','opf','blend_ID','steady_state_number','source','source_type',
            'source_actual_tonnes','crusher_rate_output','source_grade_fe','source_grade_si',
            'source_grade_al','source_grade_p','source_grade_mn'] if c in self.report]
        old = self.details.model()
        self.details.setModel(FrameModel(self.report.loc[record['row_indices'],columns],self.details))
        if old is not None: old.deleteLater()
        self.details.show(); self.details.resizeColumnsToContents()
        self.interval_selected.emit(record)

    def resizeEvent(self,event):
        super().resizeEvent(event); QTimer.singleShot(0,self.draw)
