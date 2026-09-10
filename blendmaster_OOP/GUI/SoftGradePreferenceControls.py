"""Editable soft product-grade objective settings shared by all Soft builds."""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QComboBox, QCheckBox, QSizePolicy
from classes.SoftProductGrades import objective_config
from classes.ProductQualityLimits import QUALITY_DIRECTION_NOTE


class SoftGradePreferenceControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        title = QLabel("Soft Product Grades")
        title.setStyleSheet("font-weight: bold; color: #334155;")
        layout.addWidget(title)
        description = QLabel("Applies to Soft builds in Product Targets. Weights trade against throughput, cost and the other incentives in Solver Configuration. Hard LQL/HQL remain constraints even when a penalty is disabled.")
        description.setWordWrap(True)
        description.setToolTip(QUALITY_DIRECTION_NOTE)
        layout.addWidget(description)
        top = QHBoxLayout()
        self.target_weight = QLineEdit()
        self.target_weight.setMaximumWidth(110)
        self.limit_multiplier = QLineEdit()
        self.limit_multiplier.setMaximumWidth(110)
        self.shape = QComboBox()
        self.shape.addItem("Increasing piecewise-linear", "piecewise_linear")
        self.shape.addItem("Linear absolute deviation", "linear")
        self.shape.setMinimumWidth(max(self.shape.fontMetrics().horizontalAdvance(self.shape.itemText(i)) for i in range(self.shape.count())) + 45)
        for label, widget in (("Target weight", self.target_weight), ("LQL/HQL breach multiplier", self.limit_multiplier), ("Penalty shape", self.shape)):
            caption = QLabel(label)
            caption.setContentsMargins(0, 0, 14, 0)
            caption.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
            top.addWidget(caption)
            top.addWidget(widget)
        top.addStretch()
        layout.addLayout(top)
        source_row = QHBoxLayout()
        self.similarity_mode = QComboBox()
        for label, value in (("Off", "off"), ("Closeness reward", "closeness"), ("Dispersion penalty", "dispersion"), ("Both", "both")):
            self.similarity_mode.addItem(label, value)
        self.closeness_weight = QLineEdit()
        self.dispersion_weight = QLineEdit()
        self.include_direct_tip = QCheckBox("Include direct-tip grade blocks")
        for label, field in (("Source similarity", self.similarity_mode), ("Closeness weight", self.closeness_weight), ("Dispersion weight", self.dispersion_weight)):
            field.setMaximumWidth(160)
            source_row.addWidget(QLabel(label))
            source_row.addWidget(field)
        source_row.addWidget(self.include_direct_tip)
        source_row.addStretch()
        layout.addLayout(source_row)
        grid = QGridLayout()
        for column, label in enumerate(("Analyte", "Penalty enabled", "Scale (percentage points)", "Penalty weight", "Similarity enabled", "Similarity weight")):
            grid.addWidget(QLabel(label), 0, column)
        self.analytes = {}
        for row, a in enumerate(("fe", "si", "al", "p", "mn"), 1):
            fields = dict(enabled=QCheckBox(), scale=QLineEdit(), weight=QLineEdit(), similarity_enabled=QCheckBox(), similarity_weight=QLineEdit())
            grid.addWidget(QLabel(a.title()), row, 0)
            for column, field in enumerate(fields.values(), 1):
                grid.addWidget(field, row, column)
            self.analytes[a] = fields
        grid.setColumnStretch(6, 1)
        layout.addLayout(grid)
        self.explanation = QLabel("Penalty starts at 100 × Target weight × analyte weight × grade-weight tonnes × normalized deviation. Scale normalizes grade distance; 1 Fe point and 0.01 P points have equal default distance. The increasing shape has slopes 1, 3 and 5 at 0, 1 and 2 scales. Soft LQL/HQL breaches add the configured multiplier. Cumulative mode charges the change from the opening build penalty.")
        self.explanation.setWordWrap(True)
        self.explanation.setStyleSheet("color: #64748b;")
        layout.addWidget(self.explanation)
        similarity_note = QLabel("Source similarity uses inventory and AMT stockpiles. Closeness rewards 1/(1 + normalized distance); dispersion penalizes squared normalized distance from each source to Target. Both use source product grades and declared grade-weight tonnes. Lump/fines use their mapped product grade fields. A product grade stream is required; a missing Target disables that analyte's similarity.")
        similarity_note.setWordWrap(True)
        similarity_note.setStyleSheet("color: #64748b;")
        layout.addWidget(similarity_note)
        self.set_config(None)

    def set_config(self, value):
        config = objective_config(value)
        for key in ("target_weight", "limit_multiplier", "closeness_weight", "dispersion_weight"):
            getattr(self, key).setText(str(config[key]))
        self.shape.setCurrentIndex(self.shape.findData(config["shape"]))
        self.similarity_mode.setCurrentIndex(self.similarity_mode.findData(config["similarity_mode"]))
        self.include_direct_tip.setChecked(config["include_direct_tip"])
        for a, fields in self.analytes.items():
            fields["enabled"].setChecked(config["analytes"][a]["enabled"])
            fields["similarity_enabled"].setChecked(config["analytes"][a]["similarity_enabled"])
            for key in ("scale", "weight", "similarity_weight"):
                fields[key].setText(str(config["analytes"][a][key]))

    def config(self):
        return objective_config(dict(target_weight=self.target_weight.text(), limit_multiplier=self.limit_multiplier.text(),
                                     shape=self.shape.currentData(), similarity_mode=self.similarity_mode.currentData(),
                                     closeness_weight=self.closeness_weight.text(), dispersion_weight=self.dispersion_weight.text(),
                                     include_direct_tip=self.include_direct_tip.isChecked(),
                                     analytes={a: dict(enabled=f["enabled"].isChecked(), similarity_enabled=f["similarity_enabled"].isChecked(),
                                     scale=f["scale"].text(), weight=f["weight"].text(), similarity_weight=f["similarity_weight"].text())
                                     for a, f in self.analytes.items()}))
