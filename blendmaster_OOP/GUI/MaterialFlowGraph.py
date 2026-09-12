"""Movable native Qt graph shared by setup and saved-result review."""
from copy import deepcopy
import html
import json
import math
from collections import defaultdict
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath, QFont, QPolygonF
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSplitter,
    QPlainTextEdit, QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsPathItem)

COLORS = dict(source='#64748b', tipping_point='#137e9d', conveyor='#c67b15',
              cos='#9466bb', opf='#16856a', product_build_lane='#285b9a')
ORDER = ('source','tipping_point','conveyor','cos','opf','product_build_lane')


class FlowNode(QGraphicsItem):
    def __init__(self, node, owner):
        super().__init__()
        self.node, self.owner = node, owner
        self.metrics = ''
        self.active = False
        self.setFlags(self.ItemIsMovable | self.ItemIsSelectable | self.ItemSendsGeometryChanges)
        self.setToolTip(html.escape(str(node.get('label') or node['node_id'])))
        self.setZValue(1)

    def boundingRect(self):
        return QRectF(0,0,208,100)

    def paint(self, painter, option, widget=None):
        color = QColor(COLORS.get(self.node['node_type'],'#64748b'))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(color,3 if self.active or self.isSelected() else 1))
        painter.setBrush(QColor('#e7f6f2') if self.active else QColor('white'))
        painter.drawRoundedRect(self.boundingRect().adjusted(2,2,-2,-2),9,9)
        painter.fillRect(QRectF(12,14,4,22),color)
        painter.setPen(QColor('#172f46'))
        painter.setFont(QFont('Segoe UI',10,QFont.Bold))
        painter.drawText(QRectF(23,10,174,36),Qt.TextWordWrap|Qt.AlignVCenter,
                         str(self.node.get('label') or '')[:65])
        painter.setFont(QFont('Segoe UI',8))
        painter.setPen(QColor('#556779'))
        painter.drawText(QRectF(14,48,183,43),Qt.TextWordWrap,self.metrics or self.description())

    def description(self):
        kind = self.node['node_type']
        props = self.node['properties']
        if kind in ('conveyor','cos'):
            if props.get('placeholder'):
                return 'Pass-through · no storage'
            value = f"{props.get('capacity_wmt',0):,.0f} ROM WMT"
            return value + (f" · {props.get('chunks',1)} chunks" if kind=='cos' else f" · {props.get('latency_hours',0):g} h at reference rate")
        return kind.replace('_',' ').title()

    def itemChange(self, change, value):
        if change == self.ItemPositionHasChanged:
            self.owner.update_edges()
        if change == self.ItemSelectedHasChanged and value:
            self.owner.show_node(self.node)
        return super().itemChange(change,value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.owner.positions_changed.emit(self.owner.positions())


class FlowEdge(QGraphicsPathItem):
    def __init__(self, edge, source, target):
        super().__init__()
        self.edge,self.source,self.target = edge,source,target
        self.active = False
        self.setZValue(0)
        self.update_path()

    def update_path(self):
        begin = self.source.pos()+QPointF(208,50)
        end = self.target.pos()+QPointF(0,50)
        distance = max(abs(end.x()-begin.x())*.5,50)
        path = QPainterPath(begin)
        path.cubicTo(begin+QPointF(distance,0),end-QPointF(distance,0),end)
        path.moveTo(end-QPointF(9,5)); path.lineTo(end); path.lineTo(end-QPointF(9,-5))
        self.setPath(path)
        self.setPen(QPen(QColor('#159777' if self.active else '#abb8c5'),3 if self.active else 1.4))


class ZoomView(QGraphicsView):
    def wheelEvent(self,event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y()>0 else 1/1.15
            if .12 <= self.transform().m11()*factor <= 4:
                self.scale(factor,factor)
            event.accept()
        else:
            super().wheelEvent(event)


class MaterialFlowGraph(QWidget):
    positions_changed = pyqtSignal(dict)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.graph = {}
        self.nodes,self.edges = {},[]
        self._building = False
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Drag nodes to arrange. Ctrl + wheel to zoom. Select a node for details.'))
        controls.addStretch()
        fit = QPushButton('Fit'); fit.clicked.connect(self.fit_graph); controls.addWidget(fit)
        arrange = QPushButton('Auto arrange'); arrange.clicked.connect(self.auto_arrange); controls.addWidget(arrange)
        layout.addLayout(controls)
        splitter = QSplitter()
        self.scene = QGraphicsScene(self)
        self.view = ZoomView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setBackgroundBrush(QColor('#f3f6fa'))
        self.view.setMinimumHeight(320)
        splitter.addWidget(self.view)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumWidth(210)
        self.details.setMaximumWidth(360)
        self.details.setPlaceholderText('Select a source, tipping point, conveyor, COS, OPF or product lane.')
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0,4)
        splitter.setStretchFactor(1,1)
        layout.addWidget(splitter,1)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.caption.setTextFormat(Qt.PlainText)

    def set_graph(self,graph,positions=None):
        from classes.PhaseSchemas import is_readable, TOPOLOGY_SCHEMA_VERSION
        if not is_readable(graph,TOPOLOGY_SCHEMA_VERSION):
            raise ValueError('Unsupported material-flow topology version.')
        self._building = True
        self.edges = []
        self.nodes = {}
        self.scene.clear()
        self.graph = deepcopy(graph)
        grouped = defaultdict(list)
        for node in self.graph['nodes']:
            item = FlowNode(node,self)
            self.nodes[node['node_id']] = item
            self.scene.addItem(item)
            grouped[node['node_type']].append(item)
        for column,kind in enumerate(ORDER):
            for row,item in enumerate(grouped[kind]):
                pos = (positions or {}).get(item.node['node_id'])
                valid = isinstance(pos,(list,tuple)) and len(pos)==2 and all(isinstance(v,(float,int)) and math.isfinite(v) and abs(v)<1e7 for v in pos)
                item.setPos(*pos if valid else (column*270,row*125))
        for edge in self.graph['edges']:
            if edge['source_node_id'] not in self.nodes or edge['target_node_id'] not in self.nodes:
                raise ValueError('Material-flow edge refers to an unavailable node.')
            item = FlowEdge(edge,self.nodes[edge['source_node_id']],self.nodes[edge['target_node_id']])
            self.edges.append(item); self.scene.addItem(item)
        self._building = False
        self.update_edges()
        self.caption.setText(f"{len(self.nodes):,} nodes · {len(self.edges):,} permitted routes. Layout changes do not change planning rules.")
        self.fit_graph()

    def positions(self):
        return {key:[item.pos().x(),item.pos().y()] for key,item in self.nodes.items()}

    def auto_arrange(self):
        self.set_graph(self.graph)
        self.positions_changed.emit(self.positions())

    def update_edges(self):
        if self._building:
            return
        for edge in self.edges:
            edge.update_path()
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-35,-35,35,35))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        QTimer.singleShot(0,self.fit_graph)

    def showEvent(self,event):
        super().showEvent(event)
        QTimer.singleShot(0,self.fit_graph)

    def fit_graph(self):
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-25,-25,25,25),Qt.KeepAspectRatio)

    def show_node(self,node):
        self.details.setPlainText(str(node.get('label',''))+'\n\n'+json.dumps(node['properties'],indent=2,default=str))
        item = self.nodes.get(node['node_id'])
        if item and item.metrics:
            self.details.appendPlainText('\n'+item.metrics)

    def show_frame(self,annotations=None,active_edges=()):
        active_edges = set(active_edges)
        for key,item in self.nodes.items():
            values = (annotations or {}).get(key,{})
            item.metrics = values.get('text','')
            item.active = bool(values.get('active'))
            item.update()
        for item in self.edges:
            item.active = item.edge['edge_id'] in active_edges
            item.update_path()
