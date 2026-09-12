"""COS chunk fill/depletion profile, driven by the saved-result time slider."""
import pandas as pd
from PyQt5.QtWidgets import QTableView,QStyledItemDelegate,QStyleOptionProgressBar,QStyle
from PyQt5.QtCore import Qt

class ChunkFillDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        frame=index.model().frame
        row=frame.iloc[index.row()]
        wmt=float(row['physical_rom_wmt'] or 0)
        capacity=float(row['chunk_capacity_wmt'] or 0)
        bar=QStyleOptionProgressBar()
        bar.rect=option.rect.adjusted(4,4,-4,-4)
        bar.minimum,bar.maximum=0,1000
        bar.progress=int(min(1,wmt/capacity)*1000) if capacity>0 else 0
        bar.text=f'{wmt:,.1f} / {capacity:,.1f} WMT'
        bar.textVisible=True
        bar.textAlignment=Qt.AlignCenter
        option.widget.style().drawControl(QStyle.CE_ProgressBar,bar,painter,option.widget)

class COSProfile(QTableView):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setDefaultSectionSize(135)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.fill_delegate=ChunkFillDelegate(self)

    def set_frame(self,contents,graph):
        from GUI.MaterialFlowResults import FrameModel
        rows=contents[contents.stage.eq('cos')].copy() if 'stage' in contents else pd.DataFrame()
        capacities={n['properties'].get('tipping_point'):n['properties'].get('capacity_wmt',0)/max(n['properties'].get('chunks',1),1)
                    for n in graph.get('nodes',[]) if n['node_type']=='cos' and not n['properties'].get('placeholder')}
        if not rows.empty:
            rows=rows[rows.tipping_point.isin(capacities)]
            rows['chunk_capacity_wmt']=rows.tipping_point.map(capacities)
            columns=['tipping_point','chunk_id','physical_rom_wmt','chunk_capacity_wmt','chunk_status',
                     'grade_fe','grade_si','grade_al','grade_p','grade_mn','composition']
            rows=rows.reindex(columns=columns)
        old=self.model()
        self.setModel(FrameModel(rows,self))
        if old is not None:
            old.deleteLater()
        if not rows.empty:
            self.setItemDelegateForColumn(2,self.fill_delegate)
            self.setColumnWidth(2,240)
            self.setColumnWidth(len(rows.columns)-1,650)
