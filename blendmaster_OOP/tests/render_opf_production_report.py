"""Reproducible native Task 15 screenshots with illustrative data, no network.

Run from blendmaster_OOP: python -m tests.render_opf_production_report
"""

from pathlib import Path
import tempfile
from unittest.mock import Mock

from tests.test_opf_production_report import (
    UserInputs, QApplication, QMainWindow, QTabWidget, QWidget, QTest, Qt,
    START, END, observations, targets, ProductAssayHistory, DeferredRunner, prepare_fonts,
)
from PyQt5.QtWidgets import QLabel, QVBoxLayout


def main():
    app = QApplication.instance() or QApplication([])
    prepare_fonts(app)
    host = UserInputs.__new__(UserInputs)
    QMainWindow.__init__(host)
    host.setWindowTitle("BlendMaster — Task 15 UI validation")
    host.scenario_switch_in_progress = True
    host.central_widget = QWidget()
    host.central_widget.setObjectName("mainCentralWidget")
    host.setCentralWidget(host.central_widget)
    layout = QVBoxLayout(host.central_widget)
    notice = QLabel("UI REVIEW · Illustrative product records and targets · Two OPFs / SF")
    notice.setStyleSheet("color: #526474; padding: 5px; font-size: 11px;")
    layout.addWidget(notice)
    host.tabs = QTabWidget()
    layout.addWidget(host.tabs)
    host.setup_navigation()
    host.apply_app_theme()
    host.register_page("amt_stockpiles", host.workspace_tabs, QWidget(), "AMT Stockpiles", position=0)
    host.product_targets = targets()[:2]
    host.byproducts_enabled = False
    host.product_brand_labels_choice = ["SF"]
    host.opf_input_choice = "CBOPF"
    host.start_time_choice = END
    host.active_scenario_id = "a"
    host.site_scenarios = {"b": {"opf_input_choice": "CCOPF01", "product_targets": targets()[2:]}}
    host.calendar_inputs = {}
    host.setup_product_targets_tab()
    host.setup_opf_production_report_tab()
    view = host.opf_production_report
    # Use canonical full-precision rows; the Product Targets table is separately tested.
    host.read_product_targets_from_table = Mock(return_value=targets()[:2])
    host.sync_opf_production_report_context()
    runner = DeferredRunner()
    view.run_async = runner
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshots" / "task15"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        service = ProductAssayHistory(cache_directory=directory, clock=lambda: END)
        service._query = Mock(return_value=observations())
        view.service = service
        host.tabs.setCurrentWidget(host.workspace_navigation_page)
        host.workspace_tabs.setCurrentWidget(view)
        host.resize(1650, 1100)
        host.show()
        view.opf.setCurrentIndex(view.opf.findData("all"))

        def capture(name):
            app.processEvents()
            view.canvas.draw()
            QTest.qWait(100)
            path = output / f"{name}.png"
            assert host.grab().save(str(path))
            print(path)

        QTest.mouseClick(view.refresh, Qt.LeftButton)
        assert view.progress.isVisible() and not view.refresh.isEnabled()
        capture("loading")
        runner.finish()
        assert len(view.points) == 9 and len(view.overlays) == 4
        capture("loaded")
        host.resize(1280, 900)
        capture("compact-loaded")
        host.resize(1650, 1100)
        view.grain.setCurrentIndex(view.grain.findData("observations"))
        capture("raw-observations")
        view.grain.setCurrentIndex(view.grain.findData("day"))
        capture("daily")
        view.grain.setCurrentIndex(view.grain.findData("shift"))
        view.request_refresh()
        runner.finish()
        assert view.snapshot["status"] == "cached"
        capture("cached")
        service._query.side_effect = RuntimeError("Simulated warehouse outage")
        QTest.mouseClick(view.refresh, Qt.LeftButton)
        runner.finish()
        assert view.snapshot["status"] == "offline_cached"
        capture("offline-cached")
        service._query.side_effect = None
        service._query.return_value = []
        QTest.mouseClick(view.refresh, Qt.LeftButton)
        runner.finish()
        assert not view.points and "No product records" in view.status.text()
        capture("no-data")
        host.hide()
        host.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
