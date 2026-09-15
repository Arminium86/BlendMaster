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
        self.drag_edge = None
        self.setAcceptHoverEvents(True)
        color = QColor.fromHsv(int(hashlib.sha1(str(record['blend_ID']).encode()).hexdigest()[:4], 16) % 360, 135, 190)
        self.setBrush(QBrush(color)); self.setPen(QPen(color.darker(115)))
        self.setFlag(self.ItemIsSelectable)
        self.setToolTip(html.escape(f"{record['opf']} / {record['tipping_point']}\nBlend {record['blend_ID']} · state {record['steady_state_number']}\n"
            f"{record['start_datetime']} → {record['end_datetime']}\n{record['tonnes']:,.1f} t · {record['rate']:,.1f} t/h\n"
            f"Direct tip: {record['direct_tip_ratio']:.2%}\n{record['sources']}").replace('\n','<br>'))
        if owner.editable:
            self.setToolTip(html.escape(f"{record['opf']} / {record['tipping_point']}\nBlend {record['blend_ID']}\n"
                f"{record['start_datetime']} → {record['end_datetime']}\nDrag either edge to resize draft timing.").replace('\n', '<br>'))

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.owner.editable and self.rect().width() >= 12:
            painter.setPen(QPen(QColor('#ffffff'), 2))
            for x in (self.rect().left()+4, self.rect().right()-4):
                painter.drawLine(int(x), int(self.rect().top()+8), int(x), int(self.rect().bottom()-8))

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not self.owner.editable:
            self.owner.select_interval(self.index)
        if self.owner.editable and event.button() == Qt.LeftButton:
            self.drag_edge = self.edge_at(event.pos().x())
            if self.drag_edge:
                self.original_rect = QRectF(self.rect())
                self.press_x = event.scenePos().x()
                event.accept()

    def edge_at(self, x):
        distances = [(abs(x-self.rect().left()), 'start'), (abs(x-self.rect().right()), 'end')]
        distance, edge = min(distances)
        return edge if distance <= 8 else None

    def hoverMoveEvent(self, event):
        self.setCursor(Qt.SizeHorCursor if self.owner.editable and self.edge_at(event.pos().x()) else Qt.ArrowCursor)
        super().hoverMoveEvent(event)

    def mouseMoveEvent(self, event):
        if not self.drag_edge:
            super().mouseMoveEvent(event); return
        rect = QRectF(self.original_rect)
        delta = event.scenePos().x()-self.press_x
        if self.drag_edge == 'start': rect.setLeft(min(rect.right()-1, rect.left()+delta))
        else: rect.setRight(max(rect.left()+1, rect.right()+delta))
        self.setRect(rect); event.accept()

    def mouseReleaseEvent(self, event):
        if not self.drag_edge:
            super().mouseReleaseEvent(event)
            if self.owner.editable:
                owner, index = self.owner, self.index
                QTimer.singleShot(0, lambda: owner.select_interval(index))
            return
        edge, self.drag_edge = self.drag_edge, None
        delta = ((self.rect().left()-self.original_rect.left()) if edge == 'start'
                 else (self.rect().right()-self.original_rect.right()))
        index, seconds = self.index, delta*self.owner.seconds_per_pixel
        event.accept()
        # Redrawing destroys scene items; wait until this mouse handler exits.
        QTimer.singleShot(0, lambda: self.owner.resize_interval(index, edge, seconds))


class BlendSequenceTimeline(QWidget):
    interval_selected = pyqtSignal(object)
    interval_resized = pyqtSignal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.report, self.intervals, self.configured_points = pd.DataFrame(), [], []
        self.zoom = 1.0
        self.editable = False
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
        note = self.note = QLabel('Bars show new crusher feed, coloured by Blend ID. Opening conveyor/COS discharge is shown in Material Flow and OPF Production.')
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
        self.editable = False
        self.note.setText('Bars show new crusher feed, coloured by Blend ID. Opening conveyor/COS discharge is shown in Material Flow and OPF Production.')
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

    def set_drafts(self, drafts, points):
        self.set_report(pd.DataFrame(), points)
        self.editable = True
        self.note.setText('Editable draft timing, coloured by Blend ID. Drag either bar edge to expand or shrink it, then review direct tip and submit to recalculate.')
        opfs = {p['name']: p.get('opf', '') for p in points}
        for point, draft in drafts.items():
            for index, row in enumerate(draft.get('sequence', [])):
                start = pd.to_datetime(row.get('_exact_start') or row.get('Start Datetime'))
                end = pd.to_datetime(row.get('_exact_end') or row.get('End Datetime'))
                if pd.isna(start) or pd.isna(end) or end <= start: continue
                self.intervals.append(dict(opf=opfs.get(point, ''), tipping_point=point,
                    blend_ID=row.get('Blend ID', ''), steady_state_number=index+1,
                    start_datetime=start, end_datetime=end, draft_index=index,
                    tonnes=0, rate=0, direct_tip_ratio=0, sources='Draft timing — submit to calculate tonnes and grades'))
        self.draw()

    def resize_interval(self, index, edge, seconds):
        if not self.editable or not seconds: return
        record = self.intervals[index]
        start, end = record['start_datetime'], record['end_datetime']
        if edge == 'start': start = min(start+pd.Timedelta(seconds=seconds), end-pd.Timedelta(seconds=1))
        else: end = max(end+pd.Timedelta(seconds=seconds), start+pd.Timedelta(seconds=1))
        self.interval_resized.emit(record, start, end)

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
        self.seconds_per_pixel = duration/width
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
        if self.editable:
            self.interval_selected.emit(record)
            return
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
