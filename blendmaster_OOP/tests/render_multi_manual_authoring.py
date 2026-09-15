"""Render the restored manual workflow and its real PDF/XLSX exports."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from PyQt5.QtWidgets import QApplication, QVBoxLayout
from PyQt5.QtGui import QFontDatabase, QFont
from GUI.MultiManualAuthoring import MultiManualAuthoring
from tests.test_multi_manual_delivery import ManualDeliveryTests


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 9))
    case = ManualDeliveryTests(); case.setUp()
    host = case.host
    host.updated_stockpile_data = {'S1': dict(balance=1000, grade_fe=60, grade_si=4, grade_al=2, grade_p=.04, grade_mn=.1, subset='ROM'),
        'S2': dict(balance=1000, grade_fe=55, grade_si=5, grade_al=3, grade_p=.06, grade_mn=.2, subset='ROM'),
        'S3': dict(balance=800, grade_fe=58, grade_si=4, grade_al=2, grade_p=.04, grade_mn=.1, subset='ROM')}
    host.included_stockpile_data = lambda: host.updated_stockpile_data
    host.setWindowTitle('BlendMaster — restored manual workflow')
    layout = QVBoxLayout(host); host.resize(1450, 900)
    dashboard = MultiManualAuthoring(host); layout.addWidget(dashboard); dashboard.refresh()
    dashboard.sources.item(0, 1).setText('3'); dashboard.sources.item(2, 1).setText('1')
    dashboard.set_projections(pd.DataFrame([dict(stockpile='S3', closing_balance=1200, delivered_datetime=host.start_time_choice, grade_fe=59)]))
    host.show(); app.processEvents(); host.grab().save(str(output/'manual-recipes.png'))
    dashboard.hide()
    sequence = MultiManualAuthoring(host, sequence=True); layout.addWidget(sequence); sequence.refresh()
    sequence.add_row(); sequence.hydrate_report(case.report)
    from tests.test_multi_manual_authoring import inputs
    from classes.MultiManualPlan import MultiManualPlanner
    from classes.SavedPlanStore import write_manual_snapshot
    args = list(inputs()); args[0] = host.manual_point_drafts; args[1] = host.updated_stockpile_data
    planner = MultiManualPlanner(*args)
    report = planner.build_report(planner.build_steady_states())
    sequence.hydrate_report(report)
    write_manual_snapshot(case.view.data.get('database') or str(case.path/'state.db'), host.active_manual_plan_id, report)
    from GUI.WorkflowDependencies import manual_revision
    host.manual_input_revision = manual_revision(host)
    case.view.refresh()
    app.processEvents(); host.grab().save(str(output/'manual-sequence.png'))
    sequence.hide(); layout.addWidget(case.view); case.view.show(); app.processEvents()
    host.grab().save(str(output/'manual-blend-plan.png'))
    with patch('GUI.OperationalBlendPlanView.QFileDialog.getSaveFileName', return_value=(str(output/'manual-plan.xlsx'), '')):
        case.view.export_xlsx()
    with patch('GUI.OperationalBlendPlanView.QFileDialog.getSaveFileName', return_value=(str(output/'manual-plan.pdf'), '')):
        case.view.export_pdf()
    print(case.view.status.text())
    try:
        import fitz
        with fitz.open(output/'manual-plan.pdf') as pdf:
            pdf[0].get_pixmap().save(str(output/'manual-plan-pdf.png'))
            print('PDF pages:', len(pdf))
    except ImportError:
        pass
    host.close(); case.doCleanups()


if __name__ == '__main__': main()
