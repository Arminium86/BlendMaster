import os
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QComboBox, QTableWidget  # noqa: E402

from GUI.InitialiseGUI import UserInputs  # noqa: E402


class CalendarWidgetRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def host(rows):
        window = UserInputs.__new__(UserInputs)
        window.main_table = QTableWidget()
        window.calendar_headers = ["", "Preplan"]
        window.calendar_rows = rows
        return window

    def test_only_stockpile_state_keys_are_combo_rows(self):
        self.assertTrue(
            UserInputs.is_calendar_stockpile_state_row("stockpiles_sp01_state")
        )
        self.assertFalse(
            UserInputs.is_calendar_stockpile_state_row(
                "crusher_custom_constraint_feed_state"
            )
        )
        self.assertFalse(
            UserInputs.is_calendar_stockpile_state_row("crusher_operating_state")
        )

    def test_rebuild_removes_stockpile_combo_from_custom_max_cell(self):
        window = self.host([
            {
                "stockpiles_sp01_state": (
                    "    State", [True], "red", ["Auto"]
                )
            }
        ])
        UserInputs.populate_calendar(window)
        self.assertIsInstance(window.main_table.cellWidget(0, 1), QComboBox)

        window.calendar_rows = [{
            "crusher_custom_constraint_feed_state_max": (
                "      Max", [True], "blue", ["25"]
            )
        }]
        UserInputs.populate_calendar(window)

        self.assertIsNone(window.main_table.cellWidget(0, 1))
        self.assertEqual(window.main_table.item(0, 1).text(), "25.00")

    def test_non_stockpile_state_key_uses_numeric_cell(self):
        window = self.host([{
            "crusher_operating_state": (
                "  Operating State", [True], "blue", ["1"]
            )
        }])

        UserInputs.populate_calendar(window)

        self.assertIsNone(window.main_table.cellWidget(0, 1))
        self.assertEqual(window.main_table.item(0, 1).text(), "1")


if __name__ == "__main__":
    unittest.main()
