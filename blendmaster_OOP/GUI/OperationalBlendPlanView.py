"""Separate operational Blend Plan layouts and a complete plan audit workbook."""
import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QPushButton,QLabel,QTabWidget,QTableView,QFileDialog
from GUI.MaterialFlowResults import FrameModel
from GUI.BlendPlanBackupControls import BlendPlanBackupControls
from classes.MaterialFlowReview import saved_flow_plans,saved_flow_data
from classes.OperationalBlendPlans import split_blend_plans,operational_sheets,DETAIL_COLUMNS
from classes.CrossFeatureReports import cross_feature_sheets
from classes.SpreadsheetReportExporter import SpreadsheetReportExporter
from classes.BlendPlanPDF import BlendPlanPDF
from classes.BlendPlanBackups import backup_publication
from database.DatabaseContext import get_database_path


class OperationalBlendPlanView(QWidget):
    def __init__(self,parent=None,run_async=None,backup_context=None,save_backups=None):
        super().__init__(parent)
        self.run_async,self.backup_context,self.save_backups = run_async,backup_context,save_backups
        self.plans_data,self.sheets,self.data = {},[],{}
        self.generation=0
        self.choices,self.selections = {},{}
        layout=QVBoxLayout(self)
        label=QLabel('One operational Blend Plan per tipping point, with shared-plan OPF contributions and audit evidence.')
        label.setWordWrap(True); layout.addWidget(label)
        bar=QHBoxLayout()
        bar.addWidget(QLabel('Plan'))
        self.plans=QComboBox(); self.plans.currentIndexChanged.connect(self.load_plan); bar.addWidget(self.plans)
        bar.addWidget(QLabel('Tipping point'))
        self.points=QComboBox(); self.points.currentIndexChanged.connect(self.show_point); bar.addWidget(self.points)
        refresh=QPushButton('Refresh'); refresh.clicked.connect(self.refresh); bar.addWidget(refresh)
        pdf=QPushButton('Export point PDF...'); pdf.clicked.connect(self.export_pdf); bar.addWidget(pdf)
        xlsx=QPushButton('Export all XLSX...'); xlsx.clicked.connect(self.export_xlsx); bar.addWidget(xlsx)
        bar.addStretch(); layout.addLayout(bar)
        self.backups=BlendPlanBackupControls()
        self.backups.changed.connect(self.backups_changed)
        layout.addWidget(self.backups)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs)
        self.tables={}
        for caption in ('Blend Summary','Sequence','Ratios','Detailed Report'):
            table=QTableView()
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setDefaultSectionSize(160)
            self.tables[caption]=table
            self.tabs.addTab(table,caption)
        audit=QWidget(); audit_layout=QVBoxLayout(audit)
        self.audit_selector=QComboBox(); self.audit_selector.currentIndexChanged.connect(self.show_audit); audit_layout.addWidget(self.audit_selector)
        self.audit_table=QTableView(); self.audit_table.setAlternatingRowColors(True)
        self.audit_table.horizontalHeader().setDefaultSectionSize(180)
        audit_layout.addWidget(self.audit_table)
        self.tabs.addTab(audit,'Plan Audits')
        self.status=QLabel('Run a plan to prepare its tipping-point Blend Plans.')
        self.status.setTextFormat(Qt.PlainText); self.status.setWordWrap(True)
        layout.addWidget(self.status)

    @staticmethod
    def fill(table,frame):
        previous=table.model()
        table.setModel(FrameModel(frame,table))
        if previous:
            previous.deleteLater()

    def refresh(self):
        selected=self.plans.currentText()
        try:
            names=saved_flow_plans()
        except Exception as exc:
            self.status.setText(str(exc)); return
        self.plans.blockSignals(True); self.plans.clear(); self.plans.addItems(names)
        if selected in names:
            self.plans.setCurrentText(selected)
        self.plans.blockSignals(False)
        self.load_plan()

    def load_plan(self):
        self.generation+=1
        generation=self.generation
        name=self.plans.currentText()
        database=get_database_path()
        self.plans_data={}; self.sheets=[]; self.data={}
        self.points.clear(); self.audit_selector.clear()
        self.backups.set_context({}, {},False)
        for table in [*self.tables.values(),self.audit_table]:
            self.fill(table,pd.DataFrame())
        if not name:
            self.status.setText('No saved plan is available.'); return
        self.status.setText('Preparing the saved plan layouts and audit evidence...')
        def work():
            data=saved_flow_data(name,database)
            return data,split_blend_plans(data['frames']['feed']),cross_feature_sheets(name,database,data['frames']['product'])
        def done(result):
            if generation!=self.generation or database!=get_database_path():
                return
            self.set_data(*result)
        def failed(message):
            if generation==self.generation:
                self.status.setText(str(message))
        if self.run_async:
            self.run_async(work,done,failed)
        else:
            try:
                done(work())
            except Exception as exc:
                failed(str(exc))

    def set_data(self,data,plans,sheets):
        self.data,self.plans_data,self.sheets=data,plans,sheets
        self.points.addItems(plans)
        self.audit_selector.addItems([name for name,_ in sheets])
        if self.backup_context:
            self.choices,self.selections=self.backup_context(data,sheets)
            self.backups.set_context(self.choices,self.selections,bool(plans))
        self.show_point()
        self.show_audit()
        self.status.setText(f'{len(plans)} tipping-point layouts. Ratios and timing match the saved plan. '
                            'The workbook includes each point plus the combined OPF, destination, reconciliation, AMT, quality and transport audits.')

    def backups_changed(self,selections):
        self.selections=selections
        if self.save_backups:
            self.save_backups(self.data.get('plan_id','Primary'),selections)
        self.show_point()

    def show_point(self):
        plan=self.plans_data.get(self.points.currentText())
        if not plan:
            return
        summary=plan['summary'].copy()
        summary['Backup destinations']=self.selections.get(self.points.currentText(),'')
        sequence=pd.DataFrame([{key:value for key,value in row.items() if not key.startswith('_')}
            for row in plan['transfer']['sequence_rows']])
        for caption,frame in [('Blend Summary',summary),('Sequence',sequence),('Ratios',plan['ratios']),('Detailed Report',plan['report'])]:
            self.fill(self.tables[caption],frame)

    def show_audit(self):
        index=self.audit_selector.currentIndex()
        self.fill(self.audit_table,self.sheets[index][1] if 0<=index<len(self.sheets) else pd.DataFrame())

    def export_xlsx(self):
        if not self.plans_data:
            return
        try:
            backups=backup_publication(self.choices,self.selections)
            path,_=QFileDialog.getSaveFileName(self,'Export all tipping-point Blend Plans','blend_plans.xlsx','Excel workbooks (*.xlsx)')
            if not path:
                return
            path=path if path.lower().endswith('.xlsx') else path+'.xlsx'
            SpreadsheetReportExporter.export_xlsx(path,operational_sheets(self.plans_data)+
                [('Backup Destinations',pd.DataFrame(backups))]+self.sheets,
                report_title=f"BlendMaster - {self.data.get('plan_id','Primary')} - Tipping-point Blend Plans")
            self.status.setText('Exported '+path)
        except Exception as exc:
            self.status.setText(str(exc))

    def export_pdf(self):
        point=self.points.currentText()
        plan=self.plans_data.get(point)
        if not plan:
            return
        try:
            backups=[r for r in backup_publication(self.choices,self.selections) if r['Tipping point']==point]
            path,_=QFileDialog.getSaveFileName(self,'Export tipping-point Blend Plan',point+'_blend_plan.pdf','PDF documents (*.pdf)')
            if not path:
                return
            path=path if path.lower().endswith('.pdf') else path+'.pdf'
            notes=next((frame.loc[frame.topic.ne('Planning inputs'),'note'].tolist()
                        for name,frame in self.sheets if name=='Plan Notes' and {'topic','note'}.issubset(frame)),[])
            BlendPlanPDF.export(path,plan['transfer']['sequence_rows'],plan['summary'].to_dict('records'),
                plan['report'],[c for c in DETAIL_COLUMNS if c in plan['report']],
                aliases={c:c.replace('_',' ').title() for c in DETAIL_COLUMNS},
                title=f'Blend Plan - {point}',plan_id=self.data.get('plan_id','Primary'),
                backup_destinations=backups,notes=notes,
                rounding_audit=plan['ratios'].loc[pd.to_numeric(plan['ratios'].get('Increment (%)'),errors='coerce').fillna(0)>0]
                    .rename(columns={'Operational ratio (%)':'Rounded ratio (%)'}).to_dict('records'))
            self.status.setText('Exported '+path)
        except Exception as exc:
            self.status.setText(str(exc))
