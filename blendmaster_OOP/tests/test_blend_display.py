import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from GUI.BlendDisplay import blend_color
from GUI.BlendSequenceTimeline import BlendSequenceTimeline, IntervalItem
from GUI.MaterialFlowResults import FrameModel
from tests.test_blend_sequence_workspace import report


class BlendDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_formatting_does_not_round_underlying_data(self):
        frame = pd.DataFrame({'source_actual_tonnes': [1234.56789], 'source_grade_fe': [60.12345]})
        model = FrameModel(frame)
        self.assertEqual(model.data(model.index(0, 0)), '1,235')
        self.assertEqual(model.data(model.index(0, 1)), '60.12')
        self.assertEqual(model.frame.iloc[0, 0], 1234.56789)
        self.assertEqual(model.frame.iloc[0, 1], 60.12345)

    def test_legend_matches_bars_and_uses_solver_grades_without_double_counting(self):
        data = report()
        data['crusher_actual_grade_fe'] = [61.1234, 61.1234, 58.9876]
        data['crusher_actual_grade_p'] = .054321
        view = BlendSequenceTimeline(); self.addCleanup(view.deleteLater); view.set_report(data)
        model = view.legend.model()
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.frame.iloc[0]['Tonnes'], '100')
        self.assertEqual(model.frame.iloc[0]['Fe'], '61.12')
        self.assertEqual(model.frame.iloc[0]['P'], '0.05')
        self.assertEqual(blend_color('1').name(), blend_color(1.0).name())
        bar = next(i for i in view.scene.items() if isinstance(i, IntervalItem))
        self.assertEqual(model.data(model.index(0, 1), Qt.BackgroundRole).color(), bar.brush().color())
        view.points.setCurrentIndex(1)
        self.assertEqual(view.legend.model().rowCount(), 1)

    def test_uncalculated_draft_has_no_fabricated_tonnes_or_grades(self):
        view = BlendSequenceTimeline(); self.addCleanup(view.deleteLater)
        view.set_drafts({'A': {'sequence': [{'Blend ID': 1, 'Start Datetime': '2026-01-01 06:00',
                                            'End Datetime': '2026-01-01 07:00'}]}}, [{'name': 'A', 'opf': 'OPF1'}])
        self.assertEqual(view.legend.model().frame.iloc[0]['Tonnes'], '—')
        self.assertEqual(view.legend.model().frame.iloc[0]['Fe'], '—')


if __name__ == '__main__': unittest.main()
