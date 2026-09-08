"""DataScope main window.

Five steps, left to right: input -> columns -> colour rules -> analysis ->
output.  Long work happens in a worker thread; the UI stays responsive and
every run can be cancelled.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import pipeline
from core.models import (
    APP_NAME,
    APP_VERSION,
    ChartSpec,
    ColumnSpec,
    ContentScope,
    ANALYSIS_FEATURES,
    FEATURE_LABELS,
    FEATURE_OUTPUTS,
    HTML_SECTION_LABELS,
    HTML_SECTIONS,
    Profile,
    Rule,
    RuleKind,
    ScopeKind,
    default_rules,
    user_data_dir,
)
from core.profile_io import list_profiles, load_profile, new_profile, save_profile
from core.session import (
    last_dir,
    load_session,
    profile_from_session,
    remember_dir,
    save_session,
)
from workers.job_runner import JobWorker, ScanWorker

from .i18n import LANGUAGES, language, set_language, tr
from .tips import tip
from .widgets import ColorButton, DropFileList, MultiTermEdit, hint, hline

RESAMPLE_CHOICES = ["", "1min", "5min", "10min", "15min", "30min", "1h"]
DEV_MODES = ["mean", "median", "max", "best"]
OPERATORS = ["<", "<=", ">", ">=", "==", "!="]


def rule_kind_name(kind: RuleKind) -> str:
    """Translated name of a rule type."""
    return tr(f"rule.{kind.value}")


def rule_title(rule: Rule) -> str:
    """What the rule list shows: the user's own label, else the type name."""
    return rule.label or rule_kind_name(rule.kind)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("DataScope", "DataScope")
        self.session = load_session()
        set_language(self.settings.value("lang", "ja", str))
        self.profile: Profile = new_profile("PCS")
        self.scan: Optional[pipeline.ScanResult] = None
        self.specs: dict[str, ColumnSpec] = {}
        self.worker = None
        self._last_out: Optional[Path] = None

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(900, 560)
        self.resize(self._fit_to_screen(1180, 860))
        self._build()
        self._load_settings()
        self._load_profile_list()
        self._restore_session()
        self.retranslate()
        self._rules_reload()
        self._charts_reload()
        self._filter_toggled()
        self._update_col_count()

    # ================================================================== #
    # construction
    # ================================================================== #
    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """Put a tab in a scroll area so it survives a small screen.

        The Output and Analysis tabs have grown past 1000 px tall; on a laptop
        or at 150 % display scaling the bottom rows - including Run - could end
        up off screen with no way to reach them.
        """
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)          # the page still stretches sideways
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return area

    def _build(self) -> None:
        self.tabs = QTabWidget()
        # the log tab scrolls on its own inside its text view
        self.tabs.addTab(self._scrollable(self._tab_input()), "")
        self.tabs.addTab(self._scrollable(self._tab_columns()), "")
        self.tabs.addTab(self._scrollable(self._tab_rules()), "")
        self.tabs.addTab(self._scrollable(self._tab_analysis()), "")
        self.tabs.addTab(self._scrollable(self._tab_output()), "")
        self.tabs.addTab(self._tab_log(), "")

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.addWidget(self.tabs, 1)

        bar = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(20)
        self.lbl_status = QLabel("")
        self.btn_run = QPushButton()
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self.on_run)
        self.btn_cancel = QPushButton()
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_open = QPushButton()
        self.btn_open.clicked.connect(self.on_open_folder)
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.lbl_status)
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_cancel)
        bar.addWidget(self.btn_run)
        lay.addLayout(bar)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

    # ---------------- tab 1: input ------------------------------------- #
    def _tab_input(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        self.grp_profile = QGroupBox()
        pl = QHBoxLayout(self.grp_profile)
        self.lbl_profile = QLabel()
        self.cmb_profile = QComboBox()
        self.cmb_profile.setMinimumWidth(220)
        self.cmb_profile.activated.connect(self.on_profile_selected)
        self.txt_profile_name = QLineEdit()
        self.txt_profile_name.setMinimumWidth(150)
        self.btn_profile_new = QPushButton()
        self.btn_profile_new.clicked.connect(self.on_profile_new)
        self.btn_profile_save = QPushButton()
        self.btn_profile_save.clicked.connect(self.on_profile_save)
        self.btn_profile_saveas = QPushButton()
        self.btn_profile_saveas.clicked.connect(self.on_profile_saveas)
        self.cmb_lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.cmb_lang.addItem(name, code)
        self.cmb_lang.setCurrentIndex(max(0, list(LANGUAGES).index(language())))
        self.cmb_lang.activated.connect(self.on_lang)
        self.lbl_lang = QLabel()
        pl.addWidget(self.lbl_profile)
        pl.addWidget(self.cmb_profile)
        pl.addWidget(self.txt_profile_name)
        pl.addWidget(self.btn_profile_new)
        pl.addWidget(self.btn_profile_save)
        pl.addWidget(self.btn_profile_saveas)
        pl.addStretch(1)
        pl.addWidget(self.lbl_lang)
        pl.addWidget(self.cmb_lang)
        lay.addWidget(self.grp_profile)

        self.grp_dict = QGroupBox()
        dl = QGridLayout(self.grp_dict)
        self.lbl_dict = QLabel()
        self.txt_dict = QLineEdit()
        self.btn_dict = QPushButton()
        self.btn_dict.clicked.connect(self.on_pick_dict)
        self.chk_keepraw = QCheckBox()
        self.chk_keepraw.setChecked(True)
        self.lbl_dict_hint = hint("")
        dl.addWidget(self.lbl_dict, 0, 0)
        dl.addWidget(self.txt_dict, 0, 1)
        dl.addWidget(self.btn_dict, 0, 2)
        dl.addWidget(self.chk_keepraw, 1, 1)
        dl.addWidget(self.lbl_dict_hint, 2, 1, 1, 2)
        dl.setColumnStretch(1, 1)
        lay.addWidget(self.grp_dict)

        self.grp_files = QGroupBox()
        fl = QVBoxLayout(self.grp_files)
        self.lst_files = DropFileList()
        self.lst_files.filesDropped.connect(self.on_files_dropped)
        self.lst_files.orderChanged.connect(self.on_order_changed)
        row = QHBoxLayout()
        self.btn_add = QPushButton()
        self.btn_add.clicked.connect(self.on_add_files)
        self.btn_add_folder = QPushButton()
        self.btn_add_folder.clicked.connect(self.on_add_folder)
        self.btn_remove = QPushButton()
        self.btn_remove.clicked.connect(self.on_remove_files)
        self.btn_clear = QPushButton()
        self.btn_clear.clicked.connect(self.lst_files.clear)
        self.btn_scan = QPushButton()
        self.btn_scan.clicked.connect(self.on_scan)
        for b in (self.btn_add, self.btn_add_folder, self.btn_remove, self.btn_clear):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_scan)
        # --- the order the files are processed in --------------------------- #
        order = QHBoxLayout()
        self.btn_up = QPushButton("\u25b2")
        self.btn_up.setFixedWidth(34)
        self.btn_up.clicked.connect(lambda: self.lst_files.move_selected(-1))
        self.btn_down = QPushButton("\u25bc")
        self.btn_down.setFixedWidth(34)
        self.btn_down.clicked.connect(lambda: self.lst_files.move_selected(1))
        self.btn_sortname = QPushButton()
        self.btn_sortname.clicked.connect(self.lst_files.sort_by_name)
        self.chk_autosort = QCheckBox()
        self.chk_autosort.toggled.connect(self.on_autosort_toggled)
        order.addWidget(self.btn_up)
        order.addWidget(self.btn_down)
        order.addWidget(self.btn_sortname)
        order.addWidget(self.chk_autosort)
        order.addStretch(1)
        self.lbl_order_hint = hint("")
        order.addWidget(self.lbl_order_hint)
        self.lbl_files_hint = hint("")
        sess = QHBoxLayout()
        self.chk_autoload = QCheckBox()
        self.chk_autoload.setChecked(True)
        self.chk_autoscan = QCheckBox()
        self.btn_forget = QPushButton()
        self.btn_forget.clicked.connect(self.on_forget_session)
        sess.addWidget(self.chk_autoload)
        sess.addWidget(self.chk_autoscan)
        sess.addStretch(1)
        sess.addWidget(self.btn_forget)
        fl.addWidget(self.lst_files, 1)
        fl.addLayout(order)
        fl.addLayout(row)
        fl.addLayout(sess)
        fl.addWidget(self.lbl_files_hint)
        lay.addWidget(self.grp_files, 1)
        return w

    # ---------------- tab 2: columns ----------------------------------- #
    def _tab_columns(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tbl_cols = QTableWidget(0, 8)
        lay.addWidget(self.tbl_cols, 1)
        self.tbl_cols.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_cols.setAlternatingRowColors(True)
        self.tbl_cols.verticalHeader().setVisible(False)
        self.tbl_cols.itemChanged.connect(self.on_col_item_changed)
        filt = QGridLayout()
        self.chk_filter_on = QCheckBox()
        self.chk_filter_on.stateChanged.connect(self._filter_toggled)
        self.f_col_filter = QLabel()
        self.txt_col_filter = MultiTermEdit("Data10, 交流電力, PCS01")
        self.txt_col_filter.editingFinished.connect(self.on_apply_filter)
        self.f_col_exclude = QLabel()
        self.txt_col_exclude = MultiTermEdit("ANN_, 通信異常")
        self.txt_col_exclude.editingFinished.connect(self.on_apply_filter)
        self.chk_keep_first = QCheckBox()
        self.chk_keep_first.setChecked(True)
        self.btn_apply_filter = QPushButton()
        self.btn_apply_filter.clicked.connect(self.on_apply_filter)
        self.btn_show_all = QPushButton()
        self.btn_show_all.clicked.connect(self.on_show_all)
        filt.addWidget(self.chk_filter_on, 0, 0, 1, 3)
        filt.addWidget(self.f_col_filter, 1, 0)
        filt.addWidget(self.txt_col_filter, 1, 1)
        filt.addWidget(self.btn_apply_filter, 1, 2)
        filt.addWidget(self.f_col_exclude, 2, 0)
        filt.addWidget(self.txt_col_exclude, 2, 1)
        filt.addWidget(self.btn_show_all, 2, 2)
        filt.addWidget(self.chk_keep_first, 3, 1)
        filt.setColumnStretch(1, 1)
        lay.addLayout(filt)

        self.lbl_col_empty = QLabel()
        self.lbl_col_empty.setAlignment(Qt.AlignCenter)
        self.lbl_col_empty.setWordWrap(True)
        lay.addWidget(self.lbl_col_empty)

        row = QHBoxLayout()
        self.btn_col_all = QPushButton()
        self.btn_col_all.clicked.connect(lambda: self._set_all_cols(True))
        self.btn_col_none = QPushButton()
        self.btn_col_none.clicked.connect(lambda: self._set_all_cols(False))
        self.btn_col_numeric = QPushButton()
        self.btn_col_numeric.clicked.connect(self._only_numeric)
        self.btn_col_invert = QPushButton()
        self.btn_col_invert.clicked.connect(self.on_invert_cols)
        self.btn_col_keep = QPushButton()
        self.btn_col_keep.clicked.connect(self.on_keep_ticked)
        self.btn_col_first = QPushButton()
        self.btn_col_first.clicked.connect(self.on_first_only)
        for b in (self.btn_col_all, self.btn_col_none, self.btn_col_numeric,
                  self.btn_col_invert, self.btn_col_keep, self.btn_col_first):
            row.addWidget(b)
        row.addStretch(1)
        self.lbl_col_hint = hint("")
        self.lbl_col_count = hint("")
        lay.addLayout(row)
        lay.addWidget(self.lbl_col_count)
        lay.addWidget(self.lbl_col_hint)
        return w

    # ---------------- tab 3: rules ------------------------------------- #
    def _tab_rules(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        self.lbl_rules = QLabel()
        self.lst_rules = QListWidget()
        self.lst_rules.currentRowChanged.connect(self.on_rule_selected)
        self.lst_rules.itemChanged.connect(self.on_rule_toggled)
        rb = QHBoxLayout()
        self.btn_rule_add = QPushButton()
        self.btn_rule_add.clicked.connect(self.on_rule_add)
        self.btn_rule_dup = QPushButton()
        self.btn_rule_dup.clicked.connect(self.on_rule_dup)
        self.btn_rule_del = QPushButton()
        self.btn_rule_del.clicked.connect(self.on_rule_del)
        for b in (self.btn_rule_add, self.btn_rule_dup, self.btn_rule_del):
            rb.addWidget(b)
        ll.addWidget(self.lbl_rules)
        ll.addWidget(self.lst_rules, 1)
        ll.addLayout(rb)
        split.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self.cmb_kind = QComboBox()
        for k in RuleKind:
            self.cmb_kind.addItem(rule_kind_name(k), k.value)
        self.cmb_kind.activated.connect(self.on_rule_edit)
        self.txt_rule_label = QLineEdit()
        self.txt_rule_label.editingFinished.connect(self.on_rule_edit)
        self.cmb_scope = QComboBox()
        for s in ScopeKind:
            self.cmb_scope.addItem(s.value, s.value)
        self.cmb_scope.activated.connect(self.on_rule_edit)
        self.btn_scope_pick = QPushButton()
        self.btn_scope_pick.clicked.connect(self.on_scope_pick)
        self.lbl_scope_cols = QLabel("—")
        self.lbl_scope_cols.setWordWrap(True)
        self.txt_pattern = MultiTermEdit("通信異常  |  Data1[034]")
        self.txt_pattern.editingFinished.connect(self.on_rule_edit)
        self.cmb_operator = QComboBox()
        self.cmb_operator.addItems(OPERATORS)
        self.cmb_operator.activated.connect(self.on_rule_edit)
        self.spn_value = QDoubleSpinBox()
        self.spn_value.setRange(-1e12, 1e12)
        self.spn_value.setDecimals(4)
        self.spn_value.valueChanged.connect(self.on_rule_edit)
        self.spn_value2 = QDoubleSpinBox()
        self.spn_value2.setRange(-1e12, 1e12)
        self.spn_value2.setDecimals(4)
        self.spn_value2.valueChanged.connect(self.on_rule_edit)
        self.spn_tol = QDoubleSpinBox()
        self.spn_tol.setRange(0, 1e9)
        self.spn_tol.setDecimals(6)
        self.spn_tol.valueChanged.connect(self.on_rule_edit)
        self.txt_text = QLineEdit()
        self.txt_text.editingFinished.connect(self.on_rule_edit)
        self.spn_rank = QSpinBox()
        self.spn_rank.setRange(1, 1000)
        self.spn_rank.setValue(10)
        self.spn_rank.valueChanged.connect(self.on_rule_edit)
        self.chk_percent = QCheckBox()
        self.chk_percent.stateChanged.connect(self.on_rule_edit)
        self.chk_bottom = QCheckBox()
        self.chk_bottom.stateChanged.connect(self.on_rule_edit)
        self.btn_fill = ColorButton()
        self.btn_fill.changed.connect(lambda _: self.on_rule_edit())
        self.btn_font = ColorButton()
        self.btn_font.changed.connect(lambda _: self.on_rule_edit())
        self.chk_bold = QCheckBox()
        self.chk_bold.stateChanged.connect(self.on_rule_edit)
        self.cmb_scale_mode = QComboBox()
        for m in ("2color", "3color", "5color"):
            self.cmb_scale_mode.addItem(m, m)
        self.cmb_scale_mode.activated.connect(self.on_rule_edit)
        self.cmb_scale_bounds = QComboBox()
        for b in ("auto", "percent", "numbers"):
            self.cmb_scale_bounds.addItem(b, b)
        self.cmb_scale_bounds.activated.connect(self.on_rule_edit)
        self.txt_scale_values = QLineEdit()
        self.txt_scale_values.setPlaceholderText("0, 25, 50, 75, 90")
        self.txt_scale_values.editingFinished.connect(self.on_rule_edit)
        self.btn_scale1 = ColorButton()
        self.btn_scale2 = ColorButton()
        self.btn_scale3 = ColorButton()
        self.btn_scale4 = ColorButton()
        self.btn_scale5 = ColorButton()
        self.scale_buttons = [self.btn_scale1, self.btn_scale2, self.btn_scale3,
                              self.btn_scale4, self.btn_scale5]
        for b in self.scale_buttons:
            b.changed.connect(lambda _: self.on_rule_edit())
        scale_row = QWidget()
        sr = QHBoxLayout(scale_row)
        sr.setContentsMargins(0, 0, 0, 0)
        for b in self.scale_buttons:
            sr.addWidget(b)
        sr.addStretch(1)

        self.f_kind = QLabel(); self.f_label = QLabel(); self.f_scope = QLabel()
        self.f_cols = QLabel(); self.f_pattern = QLabel(); self.f_op = QLabel()
        self.f_val = QLabel(); self.f_val2 = QLabel(); self.f_tol = QLabel()
        self.f_text = QLabel(); self.f_rank = QLabel(); self.f_fill = QLabel()
        self.f_font = QLabel(); self.f_scale = QLabel()
        form.addRow(self.f_kind, self.cmb_kind)
        form.addRow(self.f_label, self.txt_rule_label)
        form.addRow(self.f_scope, self.cmb_scope)
        form.addRow(self.f_cols, self.btn_scope_pick)
        form.addRow("", self.lbl_scope_cols)
        form.addRow(self.f_pattern, self.txt_pattern)
        form.addRow(self.f_op, self.cmb_operator)
        form.addRow(self.f_val, self.spn_value)
        form.addRow(self.f_val2, self.spn_value2)
        form.addRow(self.f_tol, self.spn_tol)
        form.addRow(self.f_text, self.txt_text)
        form.addRow(self.f_rank, self.spn_rank)
        form.addRow("", self.chk_percent)
        form.addRow("", self.chk_bottom)
        form.addRow(hline())
        form.addRow(self.f_fill, self.btn_fill)
        form.addRow(self.f_font, self.btn_font)
        form.addRow("", self.chk_bold)
        self.f_mode = QLabel(); self.f_bounds = QLabel(); self.f_stops = QLabel()
        form.addRow(self.f_mode, self.cmb_scale_mode)
        form.addRow(self.f_bounds, self.cmb_scale_bounds)
        form.addRow(self.f_stops, self.txt_scale_values)
        form.addRow(self.f_scale, scale_row)
        self.lbl_rules_hint = hint("")
        form.addRow(self.lbl_rules_hint)
        split.addWidget(right)
        split.setSizes([320, 640])
        outer.addWidget(split)
        return w

    # ---------------- tab 4: analysis ---------------------------------- #
    def _tab_analysis(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_metrics = QLabel()
        self.lst_metrics = QListWidget()
        self.lst_metrics.setSelectionMode(QAbstractItemView.NoSelection)
        form = QFormLayout()
        self.chk_matrix = QCheckBox(); self.chk_matrix.setChecked(True)
        self.chk_dev = QCheckBox(); self.chk_dev.setChecked(True)
        self.cmb_devmode = QComboBox(); self.cmb_devmode.addItems(DEV_MODES)
        self.chk_devpct = QCheckBox(); self.chk_devpct.setChecked(True)
        self.spn_floor = QDoubleSpinBox(); self.spn_floor.setRange(0, 100)
        self.spn_floor.setDecimals(1); self.spn_floor.setValue(2.0)
        self.spn_floor.setSuffix(" %")
        self.chk_skipshared = QCheckBox(); self.chk_skipshared.setChecked(True)
        self.chk_daily = QCheckBox(); self.chk_daily.setChecked(True)
        self.chk_zeros = QCheckBox(); self.chk_zeros.setChecked(True)
        self.chk_flags = QCheckBox(); self.chk_flags.setChecked(True)
        self.cmb_resample = QComboBox(); self.cmb_resample.addItems(RESAMPLE_CHOICES)
        self.spn_round = QSpinBox(); self.spn_round.setRange(0, 6); self.spn_round.setValue(3)
        self.f_devmode = QLabel(); self.f_resample = QLabel(); self.f_round = QLabel()

        # ---- period: date from a calendar, time from a clock ------------- #
        self.chk_period = QCheckBox()
        self.dt_from = QDateTimeEdit()
        self.dt_to = QDateTimeEdit()
        from PySide6.QtCore import QDate, QDateTime, QTime
        today = QDate.currentDate()
        for dt, when in ((self.dt_from, QTime(0, 0)), (self.dt_to, QTime(23, 59))):
            dt.setCalendarPopup(True)            # the arrow opens a month calendar
            dt.setDisplayFormat("yyyy-MM-dd HH:mm")
            dt.setDateTime(QDateTime(today, when))   # never show a 1900/2000 default
            dt.setEnabled(False)
        self._period_touched = False
        self.btn_period_all = QPushButton()
        self.btn_period_all.setEnabled(False)
        self.btn_period_all.clicked.connect(self.on_period_full_range)
        self.f_period_from = QLabel()
        self.chk_period.toggled.connect(self._period_toggled)
        self.dt_from.dateTimeChanged.connect(self._period_edited)
        self.dt_to.dateTimeChanged.connect(self._period_edited)
        prow = QWidget(); pl = QHBoxLayout(prow); pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.dt_from, 1); pl.addWidget(self.dt_to, 1)
        pl.addWidget(self.btn_period_all)
        self.row_period = prow
        form.addRow(self.chk_matrix)
        form.addRow(self.chk_dev)
        form.addRow(self.f_devmode, self.cmb_devmode)
        form.addRow(self.chk_devpct)
        self.f_floor = QLabel()
        form.addRow(self.f_floor, self.spn_floor)
        form.addRow(self.chk_skipshared)
        form.addRow(self.chk_daily)
        form.addRow(self.chk_zeros)
        form.addRow(self.chk_flags)
        form.addRow(self.f_resample, self.cmb_resample)
        form.addRow(self.f_round, self.spn_round)
        form.addRow(self.chk_period)
        form.addRow(self.f_period_from, self.row_period)

        self.chk_stable = QCheckBox(); self.chk_stable.setChecked(True)
        self.spn_minirr = QDoubleSpinBox(); self.spn_minirr.setRange(0, 2)
        self.spn_minirr.setDecimals(2); self.spn_minirr.setSingleStep(0.05)
        self.spn_minirr.setValue(0.4)
        self.spn_stabletol = QDoubleSpinBox(); self.spn_stabletol.setRange(0, 100)
        self.spn_stabletol.setDecimals(1); self.spn_stabletol.setValue(5.0)
        self.spn_stabletol.setSuffix(" %")
        self.spn_capacity = QDoubleSpinBox(); self.spn_capacity.setRange(0, 100000)
        self.spn_capacity.setDecimals(1); self.spn_capacity.setSingleStep(10)
        self.spn_capacity.setSuffix(" kW")
        self.f_minirr = QLabel(); self.f_stabletol = QLabel(); self.f_capacity = QLabel()
        form.addRow(self.chk_stable)
        form.addRow(self.f_minirr, self.spn_minirr)
        form.addRow(self.f_stabletol, self.spn_stabletol)
        form.addRow(self.f_capacity, self.spn_capacity)
        self.lbl_an_hint = hint("")

        # ---- charts -------------------------------------------------- #
        charts = QGroupBox()
        self.grp_charts = charts
        cl = QHBoxLayout(charts)
        left = QVBoxLayout()
        self.lst_charts = QListWidget()
        self.lst_charts.currentRowChanged.connect(self.on_chart_selected)
        self.lst_charts.itemChanged.connect(self.on_chart_toggled)
        cb = QHBoxLayout()
        self.btn_chart_add = QPushButton(); self.btn_chart_add.clicked.connect(self.on_chart_add)
        self.btn_chart_dup = QPushButton(); self.btn_chart_dup.clicked.connect(self.on_chart_dup)
        self.btn_chart_del = QPushButton(); self.btn_chart_del.clicked.connect(self.on_chart_del)
        for b in (self.btn_chart_add, self.btn_chart_dup, self.btn_chart_del):
            cb.addWidget(b)
        left.addWidget(self.lst_charts, 1)
        left.addLayout(cb)
        cl.addLayout(left, 1)

        cf = QFormLayout()
        self.txt_chart_title = QLineEdit()
        self.txt_chart_title.editingFinished.connect(self.on_chart_edit)
        self.cmb_chart_x = QComboBox()
        self.cmb_chart_x.activated.connect(self.on_chart_edit)
        self.btn_chart_y = QPushButton()
        self.btn_chart_y.clicked.connect(self.on_chart_pick_y)
        self.lbl_chart_y = QLabel("—")
        self.lbl_chart_y.setWordWrap(True)
        self.cmb_chart_group = QComboBox()
        for g in ("single", "per_column", "per_entity", "pattern"):
            self.cmb_chart_group.addItem(g, g)
        self.cmb_chart_group.activated.connect(self.on_chart_edit)
        self.txt_chart_patterns = MultiTermEdit("Data10, Data06")
        self.txt_chart_patterns.editingFinished.connect(self.on_chart_edit)
        self.cmb_chart_type = QComboBox()
        for t in ("line", "scatter", "bar"):
            self.cmb_chart_type.addItem(t, t)
        self.cmb_chart_type.activated.connect(self.on_chart_edit)
        self.chk_chart_norm = QCheckBox()
        self.chk_chart_norm.stateChanged.connect(self.on_chart_edit)
        self.f_chart_title = QLabel(); self.f_chart_x = QLabel()
        self.f_chart_y = QLabel(); self.f_chart_group = QLabel()
        self.f_chart_patterns = QLabel(); self.f_chart_type = QLabel()
        cf.addRow(self.f_chart_title, self.txt_chart_title)
        cf.addRow(self.f_chart_x, self.cmb_chart_x)
        cf.addRow(self.f_chart_y, self.btn_chart_y)
        cf.addRow("", self.lbl_chart_y)
        cf.addRow(self.f_chart_group, self.cmb_chart_group)
        cf.addRow(self.f_chart_patterns, self.txt_chart_patterns)
        cf.addRow(self.f_chart_type, self.cmb_chart_type)
        cf.addRow("", self.chk_chart_norm)
        self.lbl_chart_hint = hint("")
        cf.addRow(self.lbl_chart_hint)
        cl.addLayout(cf, 2)

        # The settings and the chart builder are two separate jobs; keeping them
        # in one column made this tab 1000 px tall.
        settings = QWidget()
        sl = QVBoxLayout(settings)
        sl.setContentsMargins(0, 8, 0, 0)
        sl.addWidget(self.lbl_metrics)
        sl.addWidget(self.lst_metrics, 1)
        sl.addLayout(form)
        sl.addWidget(self.lbl_an_hint)

        chart_page = QWidget()
        clay = QVBoxLayout(chart_page)
        clay.setContentsMargins(0, 8, 0, 0)
        clay.addWidget(charts, 1)

        self.tabs_analysis = QTabWidget()
        self.tabs_analysis.addTab(settings, "")
        self.tabs_analysis.addTab(chart_page, "")
        lay.addWidget(self.tabs_analysis)
        return w

    # ---------------- tab 5: output ------------------------------------ #
    def _scope_combo(self) -> QComboBox:
        """Selected metrics / all visible columns / everything."""
        c = QComboBox()
        for s in ContentScope:
            c.addItem(s.value, s.value)      # text filled in by _retranslate
        return c

    def _period_edited(self) -> None:
        self._period_touched = True

    def _period_toggled(self, on: bool) -> None:
        self.dt_from.setEnabled(on)
        self.dt_to.setEnabled(on)
        self.btn_period_all.setEnabled(on)
        if on and self.dt_from.dateTime() == self.dt_to.dateTime():
            lo, _ = self._data_range()
            if lo is not None:
                self.on_period_full_range()

    def _data_range(self):
        """Earliest and latest timestamp across the scanned files."""
        import pandas as pd
        from PySide6.QtCore import QDateTime
        if self.scan is None or not self.scan.tables:
            return (None, None)
        lo = hi = None
        for t in self.scan.tables:
            if not t.time_column or t.time_column not in t.df.columns:
                continue
            ts = pd.to_datetime(t.df[t.time_column], errors="coerce").dropna()
            if ts.empty:
                continue
            a, b = ts.min(), ts.max()
            lo = a if lo is None or a < lo else lo
            hi = b if hi is None or b > hi else hi
        if lo is None:
            return (None, None)
        fmt = "yyyy-MM-dd HH:mm:ss"
        return (QDateTime.fromString(lo.strftime("%Y-%m-%d %H:%M:%S"), fmt),
                QDateTime.fromString(hi.strftime("%Y-%m-%d %H:%M:%S"), fmt))

    def on_period_full_range(self) -> None:
        """Set both pickers to everything the loaded files cover."""
        lo, hi = self._data_range()
        if lo is None:
            QMessageBox.information(self, APP_NAME, tr("an.period.noscan"))
            return
        for dt in (self.dt_from, self.dt_to):
            dt.setDateTimeRange(lo, hi)          # the calendar cannot leave the data
        for dt, value in ((self.dt_from, lo), (self.dt_to, hi)):
            dt.blockSignals(True)
            dt.setDateTime(value)
            dt.blockSignals(False)

    def _tab_output(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # --- where ------------------------------------------------------- #
        self.grp_where = QGroupBox()
        where = QFormLayout(self.grp_where)
        self.txt_out = QLineEdit()
        self.btn_out = QPushButton()
        self.btn_out.clicked.connect(self.on_pick_out)
        outrow = QWidget()
        orl = QHBoxLayout(outrow)
        orl.setContentsMargins(0, 0, 0, 0)
        orl.addWidget(self.txt_out, 1)
        orl.addWidget(self.btn_out)
        self.txt_suffix = QLineEdit("_cleaned")
        self.f_out = QLabel(); self.f_suffix = QLabel()
        where.addRow(self.f_out, outrow)
        where.addRow(self.f_suffix, self.txt_suffix)

        # --- which files ------------------------------------------------- #
        self.grp_which = QGroupBox()
        which = QVBoxLayout(self.grp_which)
        self.chk_excel = QCheckBox(); self.chk_excel.setChecked(True)
        self.chk_combined = QCheckBox(); self.chk_combined.setChecked(True)
        self.chk_html = QCheckBox(); self.chk_html.setChecked(True)
        self.chk_png = QCheckBox()
        for c in (self.chk_excel, self.chk_combined, self.chk_html, self.chk_png):
            which.addWidget(c)
        presets = QHBoxLayout()
        self.btn_preset_all = QPushButton()
        self.btn_preset_html = QPushButton()
        self.btn_preset_excel = QPushButton()
        self.btn_preset_clean = QPushButton()
        self.btn_preset_all.clicked.connect(lambda: self._preset_outputs(True, True, True, True))
        self.btn_preset_html.clicked.connect(lambda: self._preset_outputs(False, False, True, False))
        self.btn_preset_excel.clicked.connect(lambda: self._preset_outputs(False, True, False, False))
        self.btn_preset_clean.clicked.connect(lambda: self._preset_outputs(True, False, False, False))
        for b in (self.btn_preset_all, self.btn_preset_html,
                  self.btn_preset_excel, self.btn_preset_clean):
            presets.addWidget(b)
        presets.addStretch(1)
        self.lbl_preset = QLabel()
        which.addWidget(self.lbl_preset)
        which.addLayout(presets)

        # --- what goes into each file ------------------------------------ #
        self.grp_scope = QGroupBox()
        scope = QFormLayout(self.grp_scope)
        self.cmb_scope_cleaned = self._scope_combo()
        self.cmb_scope_analysis = self._scope_combo()
        self.cmb_scope_html = self._scope_combo()
        self.f_scope_cleaned = QLabel()
        self.f_scope_analysis = QLabel()
        self.f_scope_html = QLabel()
        scope.addRow(self.f_scope_cleaned, self.cmb_scope_cleaned)
        scope.addRow(self.f_scope_analysis, self.cmb_scope_analysis)
        scope.addRow(self.f_scope_html, self.cmb_scope_html)
        self.spn_anmax = QSpinBox(); self.spn_anmax.setRange(0, 400)
        self.spn_anmax.setValue(12)
        self.f_anmax = QLabel()
        scope.addRow(self.f_anmax, self.spn_anmax)
        self.spn_htmlextra = QSpinBox(); self.spn_htmlextra.setRange(0, 500)
        self.spn_htmlextra.setValue(40)
        self.f_htmlextra = QLabel()
        scope.addRow(self.f_htmlextra, self.spn_htmlextra)
        # which blocks the report contains
        self.grp_sections = QGroupBox()
        sl = QVBoxLayout(self.grp_sections)
        self.chk_sections: dict[str, QCheckBox] = {}
        for key in HTML_SECTIONS:
            c = QCheckBox(); c.setChecked(True)
            self.chk_sections[key] = c
            sl.addWidget(c)
        self.lbl_sections_hint = QLabel(); self.lbl_sections_hint.setWordWrap(True)
        sl.addWidget(self.lbl_sections_hint)

        self.lbl_scope_hint = QLabel()
        self.lbl_scope_hint.setWordWrap(True)
        scope.addRow(self.lbl_scope_hint)

        # --- diagnostics: which extra analysis goes into which file -------- #
        self.grp_features = QGroupBox()
        fg = QGridLayout(self.grp_features)
        self.lbl_feat_name = QLabel(); self.lbl_feat_excel = QLabel()
        self.lbl_feat_html = QLabel()
        for col, w2 in ((0, self.lbl_feat_name), (1, self.lbl_feat_excel),
                        (2, self.lbl_feat_html)):
            w2.setStyleSheet("color:#6b6b66;font-size:11px")
            fg.addWidget(w2, 0, col)
        self.feature_rows: dict[str, tuple[QLabel, dict[str, QCheckBox]]] = {}
        for i, key in enumerate(ANALYSIS_FEATURES, start=1):
            name = QLabel()
            fg.addWidget(name, i, 0)
            boxes: dict[str, QCheckBox] = {}
            for col, out in ((1, "excel"), (2, "html")):
                c = QCheckBox()
                if out not in FEATURE_OUTPUTS[key]:
                    c.setEnabled(False)          # e.g. a scatter plot in Excel
                fg.addWidget(c, i, col)
                boxes[out] = c
            self.feature_rows[key] = (name, boxes)
        fg.setColumnStretch(0, 1)
        self.lbl_feat_hint = QLabel(); self.lbl_feat_hint.setWordWrap(True)
        fg.addWidget(self.lbl_feat_hint, len(ANALYSIS_FEATURES) + 1, 0, 1, 3)

        # --- details ----------------------------------------------------- #
        self.grp_detail = QGroupBox()
        form = QFormLayout(self.grp_detail)
        self.chk_xlcharts = QCheckBox(); self.chk_xlcharts.setChecked(True)
        self.chk_freeze = QCheckBox(); self.chk_freeze.setChecked(True)
        self.chk_filter = QCheckBox(); self.chk_filter.setChecked(True)
        self.chk_split = QCheckBox(); self.chk_split.setChecked(True)
        self.chk_htmlcolor = QCheckBox(); self.chk_htmlcolor.setChecked(True)
        self.spn_points = QSpinBox(); self.spn_points.setRange(200, 200000)
        self.spn_points.setSingleStep(500); self.spn_points.setValue(4000)
        self.spn_maxcf = QSpinBox(); self.spn_maxcf.setRange(0, 5000)
        self.spn_maxcf.setValue(120)
        self.spn_worst = QSpinBox(); self.spn_worst.setRange(1, 200)
        self.spn_worst.setValue(6)
        self.cmb_htmllang = QComboBox()
        for code in ("auto", "ja", "en", "both"):
            self.cmb_htmllang.addItem(code, code)
        self.f_htmllang = QLabel()
        self.f_points = QLabel(); self.f_maxcf = QLabel(); self.f_worst = QLabel()
        for c in (self.chk_xlcharts, self.chk_freeze, self.chk_filter,
                  self.chk_split, self.chk_htmlcolor):
            form.addRow(c)
        form.addRow(self.f_points, self.spn_points)
        form.addRow(self.f_worst, self.spn_worst)
        form.addRow(self.f_htmllang, self.cmb_htmllang)
        form.addRow(self.f_maxcf, self.spn_maxcf)

        # Four short pages instead of one 1300 px column - the tab scrolls too,
        # but nobody wants to scroll past four group boxes to reach the fifth.
        self.tabs_output = QTabWidget()
        self.tabs_output.addTab(self._group_page(self.grp_where, self.grp_which), "")
        self.tabs_output.addTab(self._group_page(self.grp_scope, self.grp_sections), "")
        self.tabs_output.addTab(self._group_page(self.grp_features), "")
        self.tabs_output.addTab(self._group_page(self.grp_detail), "")
        lay.addWidget(self.tabs_output)
        return w

    @staticmethod
    def _group_page(*groups) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        for g in groups:
            pl.addWidget(g)
        pl.addStretch(1)
        return page

    def _preset_outputs(self, cleaned: bool, analysis: bool,
                        html: bool, png: bool) -> None:
        self.chk_excel.setChecked(cleaned)
        self.chk_combined.setChecked(analysis)
        self.chk_html.setChecked(html)
        self.chk_png.setChecked(png)

    # ---------------- tab 6: log --------------------------------------- #
    def _tab_log(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        f = QFont("Consolas" if sys.platform.startswith("win") else "monospace")
        f.setPointSizeF(9.5)
        self.txt_log.setFont(f)
        lay.addWidget(self.txt_log)
        return w

    # ================================================================== #
    # translation
    # ================================================================== #
    def retranslate(self) -> None:
        self.setWindowTitle(f"{tr('app.title')}  —  {APP_VERSION}")
        for i, key in enumerate(("tab.input", "tab.columns", "tab.rules",
                                 "tab.analysis", "tab.output", "tab.log")):
            self.tabs.setTabText(i, tr(key))
        self.grp_profile.setTitle(tr("profile.group"))
        self.lbl_profile.setText(tr("profile.label"))
        self.btn_profile_new.setText(tr("profile.new"))
        self.btn_profile_save.setText(tr("profile.save"))
        self.btn_profile_saveas.setText(tr("profile.saveas"))
        self.lbl_lang.setText(tr("lang"))
        self.grp_dict.setTitle(tr("dict.group"))
        self.lbl_dict.setText(tr("dict.file"))
        self.btn_dict.setText(tr("dict.browse"))
        self.chk_keepraw.setText(tr("dict.keepraw"))
        self.lbl_dict_hint.setText(tr("dict.hint"))
        self.grp_files.setTitle(tr("files.group"))
        self.btn_add.setText(tr("files.add"))
        self.btn_add_folder.setText(tr("files.addfolder"))
        self.btn_remove.setText(tr("files.remove"))
        self.btn_clear.setText(tr("files.clear"))
        self.btn_sortname.setText(tr("files.sortname"))
        self.chk_autosort.setText(tr("files.autosort"))
        self.lbl_order_hint.setText(tr("files.orderhint"))
        self.btn_up.setToolTip(tr("files.up"))
        self.btn_down.setToolTip(tr("files.down"))
        self.btn_scan.setText(tr("files.scan"))
        self.lbl_files_hint.setText(tr("files.hint"))
        self.chk_autoload.setText(tr("files.autoload"))
        self.chk_autoscan.setText(tr("files.autoscan"))
        self.btn_forget.setText(tr("files.forget"))
        self.tbl_cols.setHorizontalHeaderLabels([
            tr("col.include"), tr("col.raw"), tr("col.name"), tr("col.unit"),
            tr("col.type"), tr("col.kind"), tr("col.decimals"), tr("col.note"),
        ])
        self.chk_filter_on.setText(tr("col.filteron"))
        self.f_col_filter.setText(tr("col.filter"))
        self.f_col_exclude.setText(tr("col.exclude"))
        self.chk_keep_first.setText(tr("col.keepfirst"))
        self.btn_apply_filter.setText(tr("col.apply"))
        self.btn_show_all.setText(tr("col.showall"))
        self.lbl_col_empty.setText(tr("col.empty"))
        self.btn_col_all.setText(tr("col.all"))
        self.btn_col_none.setText(tr("col.none"))
        self.btn_col_numeric.setText(tr("col.numeric"))
        self.btn_col_invert.setText(tr("col.invert"))
        self.btn_col_keep.setText(tr("col.keep"))
        self.btn_col_first.setText(tr("col.first"))
        self.lbl_col_hint.setText(tr("col.hint"))
        self.lbl_rules.setText(tr("rules.list"))
        self.btn_rule_add.setText(tr("rules.add"))
        self.btn_rule_dup.setText(tr("rules.dup"))
        self.btn_rule_del.setText(tr("rules.del"))
        self.f_kind.setText(tr("rules.kind"))
        self.f_label.setText(tr("rules.label"))
        self.f_scope.setText(tr("rules.scope"))
        self.f_cols.setText(tr("rules.columns"))
        self.btn_scope_pick.setText(tr("rules.pick"))
        self.f_pattern.setText(tr("rules.pattern"))
        self.f_op.setText(tr("rules.operator"))
        self.f_val.setText(tr("rules.value"))
        self.f_val2.setText(tr("rules.value2"))
        self.f_tol.setText(tr("rules.tolerance"))
        self.f_text.setText(tr("rules.text"))
        self.f_rank.setText(tr("rules.rank"))
        self.chk_percent.setText(tr("rules.percent"))
        self.chk_bottom.setText(tr("rules.bottom"))
        self.f_fill.setText(tr("rules.fill"))
        self.f_font.setText(tr("rules.font"))
        self.chk_bold.setText(tr("rules.bold"))
        self.f_scale.setText(tr("rules.scale"))
        self.f_mode.setText(tr("rules.mode"))
        self.f_bounds.setText(tr("rules.bounds"))
        self.f_stops.setText(tr("rules.stops"))
        for i, m in enumerate(("2color", "3color", "5color")):
            self.cmb_scale_mode.setItemText(i, tr(f"mode.{m}"))
        for i, b in enumerate(("auto", "percent", "numbers")):
            self.cmb_scale_bounds.setItemText(i, tr(f"bounds.{b}"))
        self.lbl_rules_hint.setText(tr("rules.hint"))
        for i, s in enumerate(ScopeKind):
            self.cmb_scope.setItemText(i, tr(f"scope.{s.value}"))
        for i, k in enumerate(RuleKind):
            self.cmb_kind.setItemText(i, rule_kind_name(k))
        self.lbl_metrics.setText(tr("an.metrics"))
        self.chk_matrix.setText(tr("an.matrix"))
        self.chk_dev.setText(tr("an.dev"))
        self.f_devmode.setText(tr("an.devmode"))
        self.chk_devpct.setText(tr("an.devpercent"))
        self.chk_daily.setText(tr("an.daily"))
        self.chk_zeros.setText(tr("an.zeros"))
        self.chk_flags.setText(tr("an.flags"))
        self.f_floor.setText(tr("an.floor"))
        self.chk_skipshared.setText(tr("an.skipshared"))
        self.f_resample.setText(tr("an.resample"))
        self.f_round.setText(tr("an.round"))
        for i, key in enumerate(("an.tab.settings", "an.tab.charts")):
            self.tabs_analysis.setTabText(i, tr(key))
        self.chk_period.setText(tr("an.period"))
        self.f_period_from.setText(tr("an.period.range"))
        self.btn_period_all.setText(tr("an.period.all"))
        self.lbl_an_hint.setText(tr("an.hint"))
        self.grp_charts.setTitle(tr("ch.list"))
        self.btn_chart_add.setText(tr("ch.add"))
        self.btn_chart_dup.setText(tr("ch.dup"))
        self.btn_chart_del.setText(tr("ch.del"))
        self.f_chart_title.setText(tr("ch.title"))
        self.f_chart_x.setText(tr("ch.x"))
        self.f_chart_y.setText(tr("ch.y"))
        self.btn_chart_y.setText(tr("ch.ypick"))
        self.f_chart_group.setText(tr("ch.group"))
        self.f_chart_patterns.setText(tr("ch.patterns"))
        self.f_chart_type.setText(tr("ch.type"))
        self.chk_chart_norm.setText(tr("ch.norm"))
        self.lbl_chart_hint.setText(tr("ch.hint"))
        for i, g in enumerate(("single", "per_column", "per_entity", "pattern")):
            self.cmb_chart_group.setItemText(i, tr(f"group.{g}"))
        for i, t in enumerate(("line", "scatter", "bar")):
            self.cmb_chart_type.setItemText(i, tr(f"type.{t}"))
        self.grp_where.setTitle(tr("out.grp.where"))
        self.grp_which.setTitle(tr("out.grp.which"))
        self.grp_scope.setTitle(tr("out.grp.scope"))
        self.grp_detail.setTitle(tr("out.grp.detail"))
        self.f_out.setText(tr("out.dir"))
        self.btn_out.setText(tr("dict.browse"))
        self.txt_out.setPlaceholderText(tr("out.same"))
        self.f_suffix.setText(tr("out.suffix"))
        self.chk_excel.setText(tr("out.excel"))
        self.chk_combined.setText(tr("out.combined"))
        self.chk_html.setText(tr("out.html"))
        self.chk_png.setText(tr("out.png"))
        self.lbl_preset.setText(tr("out.preset"))
        self.btn_preset_all.setText(tr("out.preset.all"))
        self.btn_preset_html.setText(tr("out.preset.html"))
        self.btn_preset_excel.setText(tr("out.preset.excel"))
        self.btn_preset_clean.setText(tr("out.preset.clean"))
        self.f_scope_cleaned.setText(tr("out.scope.cleaned"))
        self.f_scope_analysis.setText(tr("out.scope.analysis"))
        self.f_scope_html.setText(tr("out.scope.html"))
        self.f_anmax.setText(tr("out.anmax"))
        self.f_htmlextra.setText(tr("out.htmlextra"))
        self.lbl_scope_hint.setText(tr("out.scope.hint"))
        for i, key in enumerate(("out.tab.files", "out.tab.content",
                                 "out.tab.diag", "out.tab.format")):
            self.tabs_output.setTabText(i, tr(key))
        self.grp_features.setTitle(tr("out.grp.features"))
        self.lbl_feat_name.setText(tr("feat.col.name"))
        self.lbl_feat_excel.setText(tr("feat.col.excel"))
        self.lbl_feat_html.setText(tr("feat.col.html"))
        self.lbl_feat_hint.setText(tr("feat.hint"))
        for key, (name, _boxes) in self.feature_rows.items():
            en, ja = FEATURE_LABELS[key]
            name.setText(ja if language() == "ja" else en)
        self.chk_stable.setText(tr("an.stable"))
        self.f_minirr.setText(tr("an.minirr"))
        self.f_stabletol.setText(tr("an.stabletol"))
        self.f_capacity.setText(tr("an.capacity"))
        self.grp_sections.setTitle(tr("out.grp.sections"))
        self.lbl_sections_hint.setText(tr("out.sections.hint"))
        for key, box in self.chk_sections.items():
            en, ja = HTML_SECTION_LABELS[key]
            box.setText(ja if language() == "ja" else en)
        for cmb in (self.cmb_scope_cleaned, self.cmb_scope_analysis, self.cmb_scope_html):
            for i, sc in enumerate(ContentScope):
                cmb.setItemText(i, tr(f"scope.{sc.value}"))
        self.chk_xlcharts.setText(tr("out.charts"))
        self.chk_freeze.setText(tr("out.freeze"))
        self.chk_filter.setText(tr("out.filter"))
        self.f_points.setText(tr("out.points"))
        self.chk_split.setText(tr("out.split"))
        self.f_maxcf.setText(tr("out.maxcf"))
        self.f_worst.setText(tr("out.worst"))
        self.f_htmllang.setText(tr("out.htmllang"))
        for i, code in enumerate(("auto", "ja", "en", "both")):
            self.cmb_htmllang.setItemText(i, tr(f"lang.{code}"))
        self.chk_htmlcolor.setText(tr("out.htmlcolor"))
        self.btn_run.setText(tr("run.start"))
        self.btn_cancel.setText(tr("run.cancel"))
        self.btn_open.setText(tr("out.open"))
        self._apply_tooltips()
        if not self.lbl_status.text():
            self.lbl_status.setText(tr("run.ready"))

    def _apply_tooltips(self) -> None:
        """Hover help on every field, in the current language."""
        pairs = [
            ((self.cmb_profile, self.txt_profile_name, self.btn_profile_save,
              self.btn_profile_saveas, self.btn_profile_new, self.lbl_profile), "profile"),
            ((self.txt_dict, self.btn_dict, self.lbl_dict), "dict"),
            ((self.chk_keepraw,), "keepraw"),
            ((self.lst_files, self.btn_add, self.btn_add_folder), "files"),
            ((self.btn_scan,), "scan"),
            ((self.chk_autoload,), "autoload"),
            ((self.chk_autoscan,), "autoscan"),
            ((self.btn_forget,), "forget"),
            ((self.txt_col_filter, self.f_col_filter, self.chk_filter_on,
              self.btn_apply_filter), "col.filter"),
            ((self.txt_col_exclude, self.f_col_exclude), "col.exclude"),
            ((self.chk_keep_first,), "col.keepfirst"),
            ((self.btn_col_keep,), "col.keep"),
            ((self.btn_col_invert,), "col.invert"),
            ((self.btn_col_first,), "col.first"),
            ((self.btn_col_all, self.btn_col_none, self.btn_col_numeric), "col.buttons"),
            ((self.cmb_kind, self.f_kind), "rules.kind"),
            ((self.cmb_scope, self.f_scope), "rules.scope"),
            ((self.btn_scope_pick,), "rules.pick"),
            ((self.txt_pattern, self.f_pattern), "rules.pattern"),
            ((self.spn_value, self.f_val, self.cmb_operator, self.f_op), "rules.value"),
            ((self.spn_value2, self.f_val2), "rules.value2"),
            ((self.spn_tol, self.f_tol), "rules.tolerance"),
            ((self.txt_text, self.f_text), "rules.text"),
            ((self.spn_rank, self.f_rank, self.chk_percent, self.chk_bottom), "rules.rank"),
            ((self.btn_fill, self.f_fill), "rules.fill"),
            ((self.btn_font, self.f_font, self.chk_bold), "rules.font"),
            ((self.cmb_scale_mode, self.f_mode), "rules.mode"),
            ((self.cmb_scale_bounds, self.f_bounds), "rules.bounds"),
            ((self.txt_scale_values, self.f_stops) + tuple(self.scale_buttons)
             + (self.f_scale,), "rules.stops"),
            ((self.lst_metrics, self.lbl_metrics), "an.metrics"),
            ((self.cmb_devmode, self.f_devmode, self.chk_dev, self.chk_devpct), "an.devmode"),
            ((self.spn_floor, self.f_floor), "an.floor"),
            ((self.cmb_resample, self.f_resample), "an.resample"),
            ((self.chk_skipshared,), "an.skipshared"),
            ((self.chk_period,), "an.period"),
            ((self.dt_from, self.dt_to, self.f_period_from), "an.period.range"),
            ((self.btn_period_all,), "an.period.all"),
            ((self.txt_chart_title, self.f_chart_title), "ch.title"),
            ((self.cmb_chart_x, self.f_chart_x), "ch.x"),
            ((self.btn_chart_y, self.f_chart_y, self.lbl_chart_y), "ch.y"),
            ((self.cmb_chart_group, self.f_chart_group), "ch.group"),
            ((self.txt_chart_patterns, self.f_chart_patterns), "ch.patterns"),
            ((self.cmb_chart_type, self.f_chart_type), "ch.type"),
            ((self.chk_chart_norm,), "ch.norm"),
            ((self.txt_out, self.btn_out, self.f_out), "out.dir"),
            ((self.txt_suffix, self.f_suffix), "out.suffix"),
            ((self.chk_excel,), "out.excel"),
            ((self.chk_combined,), "out.combined"),
            ((self.chk_html,), "out.html"),
            ((self.chk_png,), "out.png"),
            ((self.chk_xlcharts,), "out.charts"),
            ((self.chk_split,), "out.split"),
            ((self.spn_points, self.f_points), "out.points"),
            ((self.spn_maxcf, self.f_maxcf), "out.maxcf"),
            ((self.chk_htmlcolor,), "out.htmlcolor"),
            ((self.spn_htmlextra, self.f_htmlextra), "out.htmlextra"),
            ((self.cmb_scope_cleaned, self.f_scope_cleaned), "out.scope.cleaned"),
            ((self.cmb_scope_analysis, self.f_scope_analysis), "out.scope.analysis"),
            ((self.cmb_scope_html, self.f_scope_html), "out.scope.html"),
            ((self.spn_anmax, self.f_anmax), "out.anmax"),
            ((self.spn_worst, self.f_worst), "out.worst"),
            ((self.cmb_htmllang, self.f_htmllang), "out.htmllang"),
            (tuple(self.chk_sections.values()) + (self.grp_sections,), "out.sections"),
            ((self.btn_sortname, self.chk_autosort, self.lbl_order_hint),
             "files.order"),
            ((self.grp_features, self.lbl_feat_hint), "out.features"),
            ((self.chk_stable,), "an.stable"),
            ((self.spn_minirr, self.f_minirr), "an.minirr"),
            ((self.spn_stabletol, self.f_stabletol), "an.stabletol"),
            ((self.spn_capacity, self.f_capacity), "an.capacity"),
            ((self.btn_preset_all, self.btn_preset_html, self.btn_preset_excel,
              self.btn_preset_clean, self.lbl_preset), "out.preset"),
            ((self.btn_run,), "run"),
        ]
        for widgets, key in pairs:
            text = tip(key)
            if not text:
                continue
            for widget in widgets:
                widget.setToolTip(text)
        # the Columns table explains itself per column
        self.tbl_cols.setToolTip(tip("col.include"))

    def on_lang(self) -> None:
        set_language(self.cmb_lang.currentData())
        self.settings.setValue("lang", language())
        self.retranslate()
        self._rules_reload(keep_row=self.lst_rules.currentRow())

    # ================================================================== #
    # logging
    # ================================================================== #
    def log(self, text: str) -> None:
        self.txt_log.appendPlainText(text)

    # ================================================================== #
    # profile handling
    # ================================================================== #
    def _load_profile_list(self) -> None:
        self.cmb_profile.blockSignals(True)
        self.cmb_profile.clear()
        for p in list_profiles():
            self.cmb_profile.addItem(p.stem, str(p))
        self.cmb_profile.blockSignals(False)
        last = self.settings.value("profile", "", str)
        if last:
            idx = self.cmb_profile.findData(last)
            if idx >= 0:
                self.cmb_profile.setCurrentIndex(idx)
                self._apply_profile(load_profile(Path(last)))

    def on_profile_selected(self) -> None:
        path = self.cmb_profile.currentData()
        if not path:
            return
        try:
            self._apply_profile(load_profile(Path(path)))
            self.settings.setValue("profile", path)
            self.log(f"Profile loaded: {path}")
        except Exception as exc:
            QMessageBox.warning(self, tr("err.title"), str(exc))

    def on_profile_new(self) -> None:
        self._apply_profile(new_profile(self.txt_profile_name.text() or "new project"))

    def on_profile_save(self) -> None:
        path = self.cmb_profile.currentData()
        if not path:
            return self.on_profile_saveas()
        self._collect_profile()
        save_profile(self.profile, Path(path))
        self.log(f"Profile saved: {path}")
        self.statusBar().showMessage(f"{tr('profile.save')}: {Path(path).name}", 4000)

    def on_profile_saveas(self) -> None:
        self._collect_profile()
        start = user_data_dir() / "profiles" / f"{self.profile.name or 'profile'}.json"
        saved_dir = self._dialog_dir("profile", str(start.parent))
        path, _ = QFileDialog.getSaveFileName(
            self, tr("profile.saveas"), str(Path(saved_dir) / start.name), "JSON (*.json)")
        if not path:
            return
        self._remember("profile", path)
        save_profile(self.profile, Path(path))
        self._load_profile_list()
        idx = self.cmb_profile.findData(path)
        if idx < 0:
            self.cmb_profile.addItem(Path(path).stem, path)
            idx = self.cmb_profile.count() - 1
        self.cmb_profile.setCurrentIndex(idx)
        self.settings.setValue("profile", path)
        self.log(f"Profile saved: {path}")

    def _apply_profile(self, prof: Profile) -> None:
        self.profile = prof
        self.txt_profile_name.setText(prof.name)
        self.txt_dict.setText(prof.dictionary.path)
        self.chk_keepraw.setChecked(prof.dictionary.keep_raw_row)
        a = prof.analysis
        self.chk_matrix.setChecked(a.make_matrix)
        self.chk_dev.setChecked(a.make_deviation)
        self.cmb_devmode.setCurrentText(a.deviation_mode)
        self.chk_devpct.setChecked(a.deviation_percent)
        self.spn_floor.setValue(a.deviation_floor_pct)
        self.chk_skipshared.setChecked(a.skip_shared_metrics)
        self.chk_daily.setChecked(a.make_daily_summary)
        self.chk_zeros.setChecked(a.zero_counts)
        self.chk_flags.setChecked(a.make_flags)
        self.spn_round.setValue(a.round_to)
        from PySide6.QtCore import QDateTime
        fmt = "yyyy-MM-dd HH:mm:ss"
        self.chk_period.blockSignals(True)
        self.chk_period.setChecked(a.time_filter)
        self.chk_period.blockSignals(False)
        for widget, value in ((self.dt_from, a.time_from), (self.dt_to, a.time_to)):
            if value:
                qd = QDateTime.fromString(str(value)[:19], fmt)
                if qd.isValid():
                    widget.blockSignals(True)
                    widget.setDateTimeRange(QDateTime.fromString("2000-01-01 00:00:00", fmt),
                                            QDateTime.fromString("2100-01-01 00:00:00", fmt))
                    widget.setDateTime(qd)
                    widget.blockSignals(False)
                    self._period_touched = True
        self._period_toggled(a.time_filter)
        self.chk_stable.setChecked(a.stable_only)
        self.spn_minirr.setValue(a.stable_min_irradiance)
        self.spn_stabletol.setValue(a.stable_tolerance_pct)
        self.spn_capacity.setValue(a.capacity_kw)
        targets = a.feature_targets or {}
        for key, (_n, boxes) in self.feature_rows.items():
            want = set(targets.get(key, []))
            for out, box in boxes.items():
                box.setChecked(box.isEnabled() and out in want)
        self.cmb_resample.setCurrentText(prof.identity.resample)
        o = prof.output
        self.txt_out.setText(o.out_dir)
        self.txt_suffix.setText(o.suffix)
        self.chk_excel.setChecked(o.write_excel)
        self.chk_combined.setChecked(o.combined_workbook)
        self.chk_html.setChecked(o.write_html)
        self.chk_png.setChecked(o.write_png)
        self.chk_xlcharts.setChecked(o.excel_charts)
        self.chk_freeze.setChecked(o.freeze_panes)
        self.chk_filter.setChecked(o.autofilter)
        self.spn_points.setValue(o.max_html_points)
        self.chk_split.setChecked(o.split_sheets_by_entity)
        self.spn_maxcf.setValue(o.max_cf_columns)
        self.chk_htmlcolor.setChecked(o.html_color_controls)
        self.spn_htmlextra.setValue(o.html_extra_channels)
        self.spn_anmax.setValue(o.analysis_max_metrics)
        chosen = set(o.html_sections or HTML_SECTIONS)
        for key, box in self.chk_sections.items():
            box.setChecked(key in chosen)
        self.spn_worst.setValue(o.html_worst_n)
        i = self.cmb_htmllang.findData(o.html_language)
        self.cmb_htmllang.setCurrentIndex(i if i >= 0 else 0)
        for cmb, value in ((self.cmb_scope_cleaned, o.cleaned_scope),
                           (self.cmb_scope_analysis, o.analysis_scope),
                           (self.cmb_scope_html, o.html_scope)):
            i = cmb.findData(value)
            cmb.setCurrentIndex(i if i >= 0 else 0)
        # Fill the text first: ticking the box fires _filter_toggled, which reads
        # these fields back into the profile - if they were still empty it would
        # wipe the very filter we are restoring.
        flt = prof.column_filter
        contains, exclude = list(flt.contains), list(flt.exclude)
        keep_first, enabled = flt.keep_first_column, flt.enabled
        self.txt_col_filter.setText(", ".join(contains))
        self.txt_col_exclude.setText(", ".join(exclude))
        self.chk_keep_first.blockSignals(True)
        self.chk_keep_first.setChecked(keep_first)
        self.chk_keep_first.blockSignals(False)
        self.chk_filter_on.blockSignals(True)
        self.chk_filter_on.setChecked(enabled)
        self.chk_filter_on.blockSignals(False)
        flt.contains, flt.exclude = contains, exclude
        flt.keep_first_column, flt.enabled = keep_first, enabled
        self._filter_toggled()
        self._rules_reload()
        self._metrics_reload()
        self._charts_reload()

    def _collect_profile(self) -> None:
        p = self.profile
        p.name = self.txt_profile_name.text() or p.name
        p.dictionary.path = self.txt_dict.text().strip()
        p.dictionary.keep_raw_row = self.chk_keepraw.isChecked()
        p.identity.resample = self.cmb_resample.currentText()
        a = p.analysis
        a.make_matrix = self.chk_matrix.isChecked()
        a.make_deviation = self.chk_dev.isChecked()
        a.deviation_mode = self.cmb_devmode.currentText()
        a.deviation_percent = self.chk_devpct.isChecked()
        a.deviation_floor_pct = self.spn_floor.value()
        a.skip_shared_metrics = self.chk_skipshared.isChecked()
        a.make_daily_summary = self.chk_daily.isChecked()
        a.zero_counts = self.chk_zeros.isChecked()
        a.make_flags = self.chk_flags.isChecked()
        a.round_to = self.spn_round.value()
        a.stable_only = self.chk_stable.isChecked()
        a.stable_min_irradiance = self.spn_minirr.value()
        a.stable_tolerance_pct = self.spn_stabletol.value()
        a.capacity_kw = self.spn_capacity.value()
        a.feature_targets = {
            key: [out for out, box in boxes.items() if box.isChecked()]
            for key, (_n, boxes) in self.feature_rows.items()
        }
        a.time_filter = self.chk_period.isChecked()
        if a.time_filter:
            a.time_from = self.dt_from.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            a.time_to = self.dt_to.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        else:
            a.time_from = a.time_to = ""      # nothing to remember while it is off
        a.metrics = self._checked_metrics()
        flt = p.column_filter
        flt.enabled = self.chk_filter_on.isChecked()
        flt.contains = self._split_terms(self.txt_col_filter.text())
        flt.exclude = self._split_terms(self.txt_col_exclude.text())
        flt.keep_first_column = self.chk_keep_first.isChecked()
        o = p.output
        o.out_dir = self.txt_out.text().strip()
        o.suffix = self.txt_suffix.text().strip() or "_cleaned"
        o.write_excel = self.chk_excel.isChecked()
        o.combined_workbook = self.chk_combined.isChecked()
        o.write_html = self.chk_html.isChecked()
        o.write_png = self.chk_png.isChecked()
        o.excel_charts = self.chk_xlcharts.isChecked()
        o.freeze_panes = self.chk_freeze.isChecked()
        o.autofilter = self.chk_filter.isChecked()
        o.max_html_points = self.spn_points.value()
        o.split_sheets_by_entity = self.chk_split.isChecked()
        o.max_cf_columns = self.spn_maxcf.value()
        o.html_color_controls = self.chk_htmlcolor.isChecked()
        o.html_extra_channels = self.spn_htmlextra.value()
        o.analysis_max_metrics = self.spn_anmax.value()
        picked = [k for k in HTML_SECTIONS if self.chk_sections[k].isChecked()]
        o.html_sections = picked or list(HTML_SECTIONS)
        o.html_worst_n = self.spn_worst.value()
        # "auto" is stored as-is so the profile stays portable; it is resolved
        # to the app's current language when a job actually starts (on_run).
        o.html_language = str(self.cmb_htmllang.currentData() or "auto")
        o.cleaned_scope = str(self.cmb_scope_cleaned.currentData()
                              or ContentScope.VISIBLE.value)
        o.analysis_scope = str(self.cmb_scope_analysis.currentData()
                               or ContentScope.SELECTED.value)
        o.html_scope = str(self.cmb_scope_html.currentData() or ContentScope.ALL.value)
        o.html_all_channels = None
        # column overrides
        p.columns = {}
        for key, spec in self.specs.items():
            p.columns[key] = {
                "name": spec.name, "unit": spec.unit, "include": spec.include,
                "decimals": spec.decimals,
            }

    # ================================================================== #
    # files
    # ================================================================== #
    def on_files_dropped(self, paths: list) -> None:
        n = self.lst_files.add_paths(paths)
        self.log(f"{n} file(s) added")

    def on_add_files(self) -> None:
        start = self._dialog_dir("files")
        files, _ = QFileDialog.getOpenFileNames(
            self, tr("files.add"), start,
            "Data (*.csv *.CSV *.txt *.tsv *.xlsx *.xlsm *.xls);;All files (*.*)")
        if files:
            self.lst_files.add_paths([Path(f) for f in files])
            self._remember("files", files[0])

    def on_add_folder(self) -> None:
        start = self._dialog_dir("folder")
        folder = QFileDialog.getExistingDirectory(self, tr("files.addfolder"), start)
        if not folder:
            return
        from core.loader import discover_files

        found = discover_files(Path(folder))
        self.lst_files.add_paths(found)
        self._remember("folder", folder)
        self.log(f"{len(found)} file(s) found in {folder}")

    def on_order_changed(self) -> None:
        """The list was reordered by hand, so it is no longer 'always by name'."""
        if self.chk_autosort.isChecked() and not self.lst_files.autosort:
            self.chk_autosort.blockSignals(True)
            self.chk_autosort.setChecked(False)
            self.chk_autosort.blockSignals(False)

    def on_autosort_toggled(self, on: bool) -> None:
        """Keep the list in name order, now and for anything added later."""
        self.lst_files.autosort = bool(on)
        if on:
            self.lst_files.sort_by_name()

    def on_forget_session(self) -> None:
        """Start clean next time: forget files, paths and the working profile."""
        from core.session import _empty

        keep_lang = self.session.get("autoload", True)
        self.session = _empty()
        self.session["autoload"] = keep_lang
        save_session(self.session)
        self.log("Session cleared - the next start will be empty.")
        self.statusBar().showMessage(tr("files.forget"), 4000)

    def on_remove_files(self) -> None:
        for item in self.lst_files.selectedItems():
            self.lst_files.takeItem(self.lst_files.row(item))

    def on_pick_dict(self) -> None:
        start = self.txt_dict.text() or self._dialog_dir("dict")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dict.file"), start,
            "Excel / CSV (*.xlsx *.xlsm *.xls *.csv);;All files (*.*)")
        if path:
            self.txt_dict.setText(path)
            self._remember("dict", path)

    def on_pick_out(self) -> None:
        start = self.txt_out.text() or self._dialog_dir("out")
        folder = QFileDialog.getExistingDirectory(self, tr("out.dir"), start)
        if folder:
            self.txt_out.setText(folder)
            self._remember("out", folder)

    def on_open_folder(self) -> None:
        target = self._last_out or (Path(self.txt_out.text()) if self.txt_out.text() else None)
        if target is None:
            paths = self.lst_files.paths()
            target = paths[0].parent if paths else None
        if target is None or not Path(target).exists():
            return
        target = str(target)
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)                                  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except OSError as exc:
            QMessageBox.warning(self, tr("err.title"), str(exc))

    # ================================================================== #
    # scanning / columns
    # ================================================================== #
    def on_scan(self) -> None:
        files = self.lst_files.paths()
        if not files:
            QMessageBox.information(self, APP_NAME, tr("run.nofiles"))
            return
        self._collect_profile()
        self._start_worker(ScanWorker(files, self.profile, self), self._scan_done)

    def _scan_done(self, res) -> None:
        self.scan = res
        self.specs = dict(res.display_specs)
        if self.profile.column_filter.enabled:
            self.on_apply_filter()
        self._columns_reload()
        self._metrics_reload()
        self._refresh_term_pickers()
        # the calendar should offer the period the files actually cover
        if not self._period_touched and self._data_range()[0] is not None:
            self.on_period_full_range()
        if not self.profile.charts:
            from core.custom_charts import default_charts

            metrics = self.profile.analysis.metrics or self._checked_metrics()
            self.profile.charts = default_charts(metrics, self.specs)
        self._charts_reload()
        self.statusBar().showMessage(
            f"{len(res.tables)} file(s), {len(self.specs)} column(s)", 6000)
        self.tabs.setCurrentIndex(1)

    def _columns_reload(self) -> None:
        self.tbl_cols.blockSignals(True)
        self.tbl_cols.setRowCount(0)
        for key, spec in self.specs.items():
            r = self.tbl_cols.rowCount()
            self.tbl_cols.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if spec.include else Qt.Unchecked)
            chk.setData(Qt.UserRole, key)
            self.tbl_cols.setItem(r, 0, chk)
            raw = QTableWidgetItem(spec.raw)
            raw.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_cols.setItem(r, 1, raw)
            self.tbl_cols.setItem(r, 2, QTableWidgetItem(spec.name))
            self.tbl_cols.setItem(r, 3, QTableWidgetItem(spec.unit))
            kind = QTableWidgetItem("number" if spec.is_numeric else "text")
            kind.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_cols.setItem(r, 4, kind)
            vkind = QTableWidgetItem(spec.kind)
            vkind.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_cols.setItem(r, 5, vkind)
            dec = QTableWidgetItem("" if spec.decimals is None else str(spec.decimals))
            self.tbl_cols.setItem(r, 6, dec)
            note_text = spec.note if spec.matched else "— " + tr("col.matched")
            if spec.shared:
                note_text = f"{note_text}  ({tr('col.shared')})".strip()
            note = QTableWidgetItem(note_text)
            note.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.tbl_cols.setItem(r, 7, note)
        self.tbl_cols.blockSignals(False)
        h = self.tbl_cols.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.setSectionResizeMode(7, QHeaderView.Stretch)
        self.tbl_cols.setColumnWidth(1, 220)
        self.tbl_cols.setColumnWidth(2, 220)
        self._update_col_count()

    def on_col_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        key_item = self.tbl_cols.item(row, 0)
        if key_item is None:
            return
        key = key_item.data(Qt.UserRole)
        spec = self.specs.get(key)
        if spec is None:
            return
        col = item.column()
        if col == 0:
            spec.include = item.checkState() == Qt.Checked
            self._update_col_count()
        elif col == 2:
            spec.name = item.text().strip()
        elif col == 3:
            spec.unit = item.text().strip()
        elif col == 6:
            text = item.text().strip()
            spec.decimals = int(text) if text.isdigit() else None

    def _filter_toggled(self) -> None:
        on = self.chk_filter_on.isChecked()
        for w in (self.txt_col_filter, self.txt_col_exclude, self.chk_keep_first,
                  self.btn_apply_filter):
            w.setEnabled(on)
        if on:
            self.on_apply_filter()

    @staticmethod
    def _split_terms(text: str) -> list[str]:
        parts = [t.strip() for t in text.replace(";", ",").replace("、", ",").split(",")]
        return [t for t in parts if t]

    def on_apply_filter(self) -> None:
        """Tick / untick rows by what the column name contains."""
        flt = self.profile.column_filter
        flt.enabled = self.chk_filter_on.isChecked()
        flt.contains = self._split_terms(self.txt_col_filter.text())
        flt.exclude = self._split_terms(self.txt_col_exclude.text())
        flt.keep_first_column = self.chk_keep_first.isChecked()
        if not self.specs:
            return
        tables = self.scan.tables if self.scan else []
        protected: set[str] = set()
        for t in tables:
            if t.time_column:
                protected.add(str(t.time_column))
            if flt.keep_first_column and len(t.df.columns):
                protected.add(str(t.df.columns[0]))
        if self.scan is not None and self.scan.is_wide and flt.keep_first_column:
            protected |= {k for k, sp in self.specs.items() if sp.raw == sp.metric == ""}
        for key, spec in self.specs.items():
            if key in protected:
                spec.include = True
                continue
            verdict = flt.matches(spec.raw, spec.name)
            if verdict is not None:
                spec.include = bool(verdict)
        self._columns_reload()
        self._metrics_reload()

    def on_show_all(self) -> None:
        self.chk_filter_on.setChecked(False)
        self.profile.column_filter.enabled = False
        self._set_all_cols(True)
        self._metrics_reload()

    def _term_sources(self) -> list[tuple[str, str]]:
        """What the ▾ pickers offer: the channels actually present in the files."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key, spec in self.specs.items():
            name = spec.name or key
            unit = f" [{spec.unit}]" if spec.unit else ""
            if key not in seen:
                seen.add(key)
                out.append((f"{key}   →   {name}{unit}", key))
            if spec.name and spec.name != key and spec.name not in seen:
                seen.add(spec.name)
                out.append((f"{name}{unit}   ({key})", spec.name))
        if self.scan is not None:
            for entity in self.scan.entities:
                if entity and entity not in seen:
                    seen.add(entity)
                    out.append((f"{entity}   (unit)", entity))
        return out

    def _refresh_term_pickers(self) -> None:
        terms = self._term_sources()
        empty = tr("col.empty")
        for widget in (self.txt_col_filter, self.txt_col_exclude,
                       self.txt_pattern, self.txt_chart_patterns):
            widget.set_terms(terms, empty)
        if terms:
            sample = [v for _, v in terms[:3]]
            self.txt_chart_patterns.setPlaceholderText(", ".join(sample[:2]))
            self.txt_col_filter.setPlaceholderText(", ".join(sample))

    def _protected_keys(self) -> set[str]:
        """Columns that must stay visible: the timestamp, and the first column
        of each file when 'always keep the first column' is on."""
        keep_first = self.chk_keep_first.isChecked()
        out: set[str] = set()
        tables = (self.scan.tables if self.scan else [])
        for t in tables:
            if t.time_column:
                out.add(str(t.time_column))
            if keep_first and len(t.df.columns):
                out.add(str(t.df.columns[0]))
        if self.scan is not None and self.scan.is_wide:
            # the per-unit view has no raw time column; keep nothing extra
            out |= {k for k in self.specs if k in out}
        return out

    def _set_visibility(self, decide) -> None:
        """Apply ``decide(key, spec) -> bool`` to every column, then refresh."""
        protected = self._protected_keys()
        for key, spec in self.specs.items():
            spec.include = True if key in protected else bool(decide(key, spec))
        self._columns_reload()
        self._metrics_reload()

    def on_invert_cols(self) -> None:
        self._set_visibility(lambda k, s: not s.include)

    def on_keep_ticked(self) -> None:
        """Hide everything that is not ticked right now - the manual selection."""
        ticked = set(self._selected_column_keys())
        if not ticked:
            QMessageBox.information(self, APP_NAME, tr("col.noselection"))
            return
        self._set_visibility(lambda k, s: k in ticked)
        self.log(f"{len(ticked)} column(s) kept, the rest hidden")

    def on_first_only(self) -> None:
        """Only the label / time column stays - a clean slate to build from."""
        self._set_visibility(lambda k, s: False)

    def _update_col_count(self) -> None:
        total = len(self.specs)
        hidden = sum(1 for s in self.specs.values() if not s.include)
        self.lbl_col_count.setText(tr("col.count") % (total, hidden))
        empty = total == 0
        self.lbl_col_empty.setVisible(empty)
        self.tbl_cols.setVisible(not empty)

    def _set_all_cols(self, on: bool) -> None:
        self.tbl_cols.blockSignals(True)
        for r in range(self.tbl_cols.rowCount()):
            it = self.tbl_cols.item(r, 0)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.specs[it.data(Qt.UserRole)].include = on
        self.tbl_cols.blockSignals(False)
        self._update_col_count()

    def _only_numeric(self) -> None:
        self.tbl_cols.blockSignals(True)
        for r in range(self.tbl_cols.rowCount()):
            it = self.tbl_cols.item(r, 0)
            spec = self.specs[it.data(Qt.UserRole)]
            keep = spec.is_numeric
            it.setCheckState(Qt.Checked if keep else Qt.Unchecked)
            spec.include = keep
        self.tbl_cols.blockSignals(False)
        self._update_col_count()

    def _selected_column_keys(self) -> list[str]:
        keys = []
        for r in range(self.tbl_cols.rowCount()):
            it = self.tbl_cols.item(r, 0)
            if it is not None and it.checkState() == Qt.Checked:
                keys.append(it.data(Qt.UserRole))
        return keys

    # ================================================================== #
    # metrics
    # ================================================================== #
    def _metrics_reload(self) -> None:
        chosen = set(self.profile.analysis.metrics)
        self.lst_metrics.clear()
        keys = (self.scan.numeric_keys if self.scan else
                [k for k, s in self.specs.items() if s.is_numeric])
        if not keys:
            keys = list(chosen)
        keys = [k for k in keys if self.specs.get(k) is None or self.specs[k].include]
        for k in keys:
            spec = self.specs.get(k)
            label = f"{spec.name} [{spec.unit}]" if spec and spec.unit else (
                spec.name if spec else k)
            it = QListWidgetItem(f"{label}      ({k})")
            it.setData(Qt.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if k in chosen else Qt.Unchecked)
            self.lst_metrics.addItem(it)

    def _checked_metrics(self) -> list[str]:
        out = []
        for i in range(self.lst_metrics.count()):
            it = self.lst_metrics.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    # ================================================================== #
    # charts
    # ================================================================== #
    def _charts_reload(self, keep_row: int = 0) -> None:
        self.lst_charts.blockSignals(True)
        self.lst_charts.clear()
        for c in self.profile.charts:
            it = QListWidgetItem(c.title or tr("ch.title"))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c.enabled else Qt.Unchecked)
            self.lst_charts.addItem(it)
        self.lst_charts.blockSignals(False)
        if self.lst_charts.count():
            row = min(max(0, keep_row), self.lst_charts.count() - 1)
            self.lst_charts.setCurrentRow(row)
            self.on_chart_selected(row)
        self._chart_axis_reload()

    def _chart_axis_reload(self) -> None:
        """X-axis choices: the time column first, then every other column."""
        current = self.cmb_chart_x.currentData()
        self.cmb_chart_x.blockSignals(True)
        self.cmb_chart_x.clear()
        time_col = ""
        if self.scan is not None and self.scan.analysis_tables:
            time_col = self.scan.analysis_tables[0].time_column or ""
        self.cmb_chart_x.addItem(f"{time_col or 'time'}  (time)", "")
        for key, spec in self.specs.items():
            label = f"{spec.name} ({key})" if spec.name and spec.name != key else key
            self.cmb_chart_x.addItem(label, key)
        idx = self.cmb_chart_x.findData(current)
        self.cmb_chart_x.setCurrentIndex(max(0, idx))
        self.cmb_chart_x.blockSignals(False)

    def _current_chart(self) -> Optional[ChartSpec]:
        r = self.lst_charts.currentRow()
        if 0 <= r < len(self.profile.charts):
            return self.profile.charts[r]
        return None

    def on_chart_toggled(self, item: QListWidgetItem) -> None:
        r = self.lst_charts.row(item)
        if 0 <= r < len(self.profile.charts):
            self.profile.charts[r].enabled = item.checkState() == Qt.Checked

    def on_chart_selected(self, row: int) -> None:
        chart = self._current_chart()
        if chart is None:
            return
        self._loading_chart = True
        self.txt_chart_title.setText(chart.title)
        idx = self.cmb_chart_x.findData(chart.x_column)
        self.cmb_chart_x.setCurrentIndex(max(0, idx))
        self._show_chart_y(chart)
        gi = ["single", "per_column", "per_entity", "pattern"]
        self.cmb_chart_group.setCurrentIndex(
            gi.index(chart.group_mode) if chart.group_mode in gi else 0)
        self.txt_chart_patterns.setText(", ".join(chart.patterns))
        ti = ["line", "scatter", "bar"]
        self.cmb_chart_type.setCurrentIndex(
            ti.index(chart.chart_type) if chart.chart_type in ti else 0)
        self.chk_chart_norm.setChecked(chart.normalize)
        self._loading_chart = False
        self._update_chart_fields(chart)

    def _show_chart_y(self, chart: ChartSpec) -> None:
        names = []
        for key in chart.y_columns:
            spec = self.specs.get(key)
            names.append(spec.name if spec and spec.name else key)
        self.lbl_chart_y.setText(", ".join(names[:12]) +
                                 ("…" if len(names) > 12 else "") or "—")

    def _update_chart_fields(self, chart: ChartSpec) -> None:
        pattern = chart.group_mode == "pattern"
        self.txt_chart_patterns.setVisible(pattern)
        self.f_chart_patterns.setVisible(pattern)

    def on_chart_edit(self) -> None:
        if getattr(self, "_loading_chart", False):
            return
        chart = self._current_chart()
        if chart is None:
            return
        chart.title = self.txt_chart_title.text().strip()
        chart.x_column = self.cmb_chart_x.currentData() or ""
        chart.group_mode = self.cmb_chart_group.currentData() or "single"
        chart.patterns = self._split_terms(self.txt_chart_patterns.text())
        chart.chart_type = self.cmb_chart_type.currentData() or "line"
        chart.normalize = self.chk_chart_norm.isChecked()
        item = self.lst_charts.item(self.lst_charts.currentRow())
        if item is not None:
            item.setText(chart.title or tr("ch.title"))
        self._update_chart_fields(chart)

    def on_chart_pick_y(self) -> None:
        chart = self._current_chart()
        if chart is None:
            return
        chart.y_columns = self._checked_metrics()
        self._show_chart_y(chart)
        self.log(f"chart '{chart.title or '?'}': {len(chart.y_columns)} channel(s)")

    def on_chart_add(self) -> None:
        chart = ChartSpec(title=f"chart {len(self.profile.charts) + 1}",
                          y_columns=self._checked_metrics())
        self.profile.charts.append(chart)
        self._charts_reload(keep_row=len(self.profile.charts) - 1)

    def on_chart_dup(self) -> None:
        chart = self._current_chart()
        if chart is None:
            return
        copy = ChartSpec.from_dict(chart.to_dict())
        copy.title = f"{copy.title} (2)"
        self.profile.charts.append(copy)
        self._charts_reload(keep_row=len(self.profile.charts) - 1)

    def on_chart_del(self) -> None:
        row = self.lst_charts.currentRow()
        if 0 <= row < len(self.profile.charts):
            del self.profile.charts[row]
            self._charts_reload(keep_row=row - 1)

    # ================================================================== #
    # rules
    # ================================================================== #
    def _rules_reload(self, keep_row: int = 0) -> None:
        if not self.profile.rules:
            self.profile.rules = default_rules()
        self.lst_rules.blockSignals(True)
        self.lst_rules.clear()
        for rule in self.profile.rules:
            it = QListWidgetItem(rule_title(rule))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if rule.enabled else Qt.Unchecked)
            self.lst_rules.addItem(it)
        self.lst_rules.blockSignals(False)
        row = min(max(0, keep_row), self.lst_rules.count() - 1)
        if self.lst_rules.count():
            self.lst_rules.setCurrentRow(row)
            self.on_rule_selected(row)

    def _current_rule(self) -> Optional[Rule]:
        r = self.lst_rules.currentRow()
        if 0 <= r < len(self.profile.rules):
            return self.profile.rules[r]
        return None

    def on_rule_toggled(self, item: QListWidgetItem) -> None:
        r = self.lst_rules.row(item)
        if 0 <= r < len(self.profile.rules):
            self.profile.rules[r].enabled = item.checkState() == Qt.Checked

    def on_rule_selected(self, row: int) -> None:
        rule = self._current_rule()
        if rule is None:
            return
        self._loading_rule = True
        self.cmb_kind.setCurrentIndex(list(RuleKind).index(rule.kind))
        self.txt_rule_label.setText(rule.label)
        self.cmb_scope.setCurrentIndex(list(ScopeKind).index(rule.scope))
        self.txt_pattern.setText(rule.pattern)
        self.cmb_operator.setCurrentText(rule.operator)
        self.spn_value.setValue(rule.value)
        self.spn_value2.setValue(rule.value2)
        self.spn_tol.setValue(rule.tolerance)
        self.txt_text.setText(rule.text)
        self.spn_rank.setValue(rule.rank)
        self.chk_percent.setChecked(rule.percent)
        self.chk_bottom.setChecked(rule.bottom)
        self.btn_fill.set_argb(rule.fill)
        self.btn_font.set_argb(rule.font)
        self.chk_bold.setChecked(rule.bold)
        from core.rules import band_colors

        self.cmb_scale_mode.setCurrentIndex(
            max(0, ["2color", "3color", "5color"].index(rule.scale_mode)
                if rule.scale_mode in ("2color", "3color", "5color") else 1))
        self.cmb_scale_bounds.setCurrentIndex(
            max(0, ["auto", "percent", "numbers"].index(rule.scale_bounds)
                if rule.scale_bounds in ("auto", "percent", "numbers") else 0))
        self.txt_scale_values.setText(
            ", ".join(str(v) for v in rule.scale_values))
        cols = band_colors(rule) + ["FFFFFFFF"] * 5
        for i, btn in enumerate(self.scale_buttons):
            btn.set_argb(cols[i])
        self.lbl_scope_cols.setText(
            ", ".join(rule.columns[:14]) + ("…" if len(rule.columns) > 14 else "")
            or "—")
        self._loading_rule = False
        self._update_rule_fields(rule)

    def _update_rule_fields(self, rule: Rule) -> None:
        """Show only the parameters this rule kind actually uses."""
        k = rule.kind
        show = {
            "op": k is RuleKind.THRESHOLD,
            "val": k in (RuleKind.THRESHOLD, RuleKind.BETWEEN, RuleKind.JUMP),
            "val2": k in (RuleKind.BETWEEN, RuleKind.OUTLIER_SIGMA, RuleKind.STUCK,
                          RuleKind.JUMP),
            "tol": k is RuleKind.ZERO,
            "text": k is RuleKind.TEXT_CONTAINS,
            "rank": k is RuleKind.TOP_BOTTOM,
            "colour": k not in (RuleKind.COLOR_SCALE, RuleKind.DATA_BAR),
            "scale": k in (RuleKind.COLOR_SCALE, RuleKind.DATA_BAR),
            "pattern": rule.scope is ScopeKind.PATTERN,
            "cols": rule.scope is ScopeKind.SELECTED,
        }
        for widget, label, key in (
            (self.cmb_operator, self.f_op, "op"),
            (self.spn_value, self.f_val, "val"),
            (self.spn_value2, self.f_val2, "val2"),
            (self.spn_tol, self.f_tol, "tol"),
            (self.txt_text, self.f_text, "text"),
            (self.spn_rank, self.f_rank, "rank"),
            (self.btn_fill, self.f_fill, "colour"),
            (self.btn_font, self.f_font, "colour"),
            (self.txt_pattern, self.f_pattern, "pattern"),
            (self.btn_scope_pick, self.f_cols, "cols"),
        ):
            widget.setVisible(show[key])
            label.setVisible(show[key])
        self.chk_percent.setVisible(show["rank"])
        self.chk_bottom.setVisible(show["rank"])
        self.chk_bold.setVisible(show["colour"])
        self.lbl_scope_cols.setVisible(show["cols"])
        gradient = k is RuleKind.COLOR_SCALE
        n_colors = {"2color": 2, "3color": 3, "5color": 5}.get(rule.scale_mode, 3)
        for i, b in enumerate(self.scale_buttons):
            b.setVisible(show["scale"] and (i < n_colors if gradient else i < 3))
        self.f_scale.setVisible(show["scale"])
        for widget, label in ((self.cmb_scale_mode, self.f_mode),
                              (self.cmb_scale_bounds, self.f_bounds)):
            widget.setVisible(gradient)
            label.setVisible(gradient)
        wants_numbers = gradient and rule.scale_bounds == "numbers"
        self.txt_scale_values.setVisible(wants_numbers)
        self.f_stops.setVisible(wants_numbers)

    def on_rule_edit(self) -> None:
        if getattr(self, "_loading_rule", False):
            return
        rule = self._current_rule()
        if rule is None:
            return
        rule.kind = RuleKind(self.cmb_kind.currentData())
        rule.label = self.txt_rule_label.text().strip()
        rule.scope = ScopeKind(self.cmb_scope.currentData())
        rule.pattern = self.txt_pattern.text().strip()
        rule.operator = self.cmb_operator.currentText()
        rule.value = self.spn_value.value()
        rule.value2 = self.spn_value2.value()
        rule.tolerance = self.spn_tol.value()
        rule.text = self.txt_text.text()
        rule.rank = self.spn_rank.value()
        rule.percent = self.chk_percent.isChecked()
        rule.bottom = self.chk_bottom.isChecked()
        rule.fill = self.btn_fill.argb()
        rule.font = self.btn_font.argb()
        rule.bold = self.chk_bold.isChecked()
        rule.scale_mode = self.cmb_scale_mode.currentData() or "3color"
        rule.scale_bounds = self.cmb_scale_bounds.currentData() or "auto"
        n_colors = {"2color": 2, "3color": 3, "5color": 5}[rule.scale_mode]
        rule.scale_colors = [b.argb() for b in self.scale_buttons[:n_colors]]
        stops = []
        for part in self.txt_scale_values.text().replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                stops.append(float(part))
            except ValueError:
                pass
        rule.scale_values = stops
        row = self.lst_rules.currentRow()
        item = self.lst_rules.item(row)
        if item is not None:
            item.setText(rule_title(rule))
        self._update_rule_fields(rule)

    def on_scope_pick(self) -> None:
        rule = self._current_rule()
        if rule is None:
            return
        rule.columns = self._selected_column_keys()
        rule.scope = ScopeKind.SELECTED
        self.cmb_scope.setCurrentIndex(list(ScopeKind).index(ScopeKind.SELECTED))
        self.lbl_scope_cols.setText(
            ", ".join(rule.columns[:14]) + ("…" if len(rule.columns) > 14 else "") or "—")
        self.log(f"{rule_title(rule)}: {len(rule.columns)} column(s) assigned")

    def on_rule_add(self) -> None:
        self.profile.rules.append(Rule())
        self._rules_reload(keep_row=len(self.profile.rules) - 1)

    def on_rule_dup(self) -> None:
        rule = self._current_rule()
        if rule is None:
            return
        self.profile.rules.append(Rule.from_dict(rule.to_dict()))
        self._rules_reload(keep_row=len(self.profile.rules) - 1)

    def on_rule_del(self) -> None:
        row = self.lst_rules.currentRow()
        if 0 <= row < len(self.profile.rules):
            del self.profile.rules[row]
            self._rules_reload(keep_row=row - 1)

    # ================================================================== #
    # running
    # ================================================================== #
    def _start_worker(self, worker, on_done) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = worker
        worker.progress.connect(self._on_progress)
        worker.message.connect(self.log)
        worker.failed.connect(self._on_failed)
        worker.done.connect(on_done)
        worker.finished.connect(self._on_worker_finished)
        self.btn_run.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_status.setText(tr("run.running"))
        worker.start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress.setValue(max(0, min(100, pct)))
        self.lbl_status.setText(msg)

    def _on_failed(self, text: str) -> None:
        self.log(text)
        QMessageBox.critical(self, tr("err.title"), text.strip().splitlines()[-1])

    def _on_worker_finished(self) -> None:
        self.btn_run.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        cancelled = self.worker is not None and self.worker.is_cancelled()
        self.lbl_status.setText(tr("run.cancelled") if cancelled else tr("run.done"))
        self.worker = None

    def on_run(self) -> None:
        files = self.lst_files.paths()
        if not files:
            QMessageBox.information(self, APP_NAME, tr("run.nofiles"))
            return
        self._collect_profile()
        if self.profile.output.html_language == "auto":
            # the report speaks whatever language the app is set to
            self.profile.output.html_language = language()
            self._html_lang_auto = True
        reuse = self.scan if (self.scan and
                              {str(t.path) for t in self.scan.tables} ==
                              {str(f) for f in files}) else None
        if reuse is not None and not reuse.is_wide:
            reuse.specs = self.specs        # wide mode edits the same objects already
        self._start_worker(JobWorker(files, self.profile, reuse, self), self._job_done)

    def _job_done(self, res) -> None:
        if getattr(self, "_html_lang_auto", False):
            self.profile.output.html_language = "auto"    # put the setting back
            self._html_lang_auto = False
        # a run also reads the files, so the Columns tab is never left empty
        if getattr(res, "scan", None) is not None and not self.specs:
            self._scan_done(res.scan)
        if res.outputs:
            self._last_out = Path(res.outputs[0]).parent
            self.log("")
            self.log("Output:")
            for p in res.outputs:
                self.log(f"  {p}")
            self.statusBar().showMessage(f"{len(res.outputs)} file(s) written", 8000)
        self.tabs.setCurrentIndex(5)

    def on_cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.lbl_status.setText(tr("run.cancelled"))

    # ================================================================== #
    # session: files, paths and the profile you were editing
    # ================================================================== #
    def _restore_session(self) -> None:
        data = self.session
        prof = profile_from_session(data)
        if prof is not None:
            # the working copy, including edits that were never saved
            self._apply_profile(prof)
            path = data.get("profile_path") or ""
            idx = self.cmb_profile.findData(path) if path else -1
            if idx >= 0:
                self.cmb_profile.blockSignals(True)
                self.cmb_profile.setCurrentIndex(idx)
                self.cmb_profile.blockSignals(False)

        if data.get("dictionary"):
            self.txt_dict.setText(data["dictionary"])
        if data.get("out_dir"):
            self.txt_out.setText(data["out_dir"])

        files = [Path(f) for f in data.get("files", []) if Path(f).exists()]
        if files and data.get("autoload", True):
            self.lst_files.add_paths(files)
            self.log(f"Session restored: {len(files)} file(s), "
                     f"profile '{self.profile.name}'")
        self.chk_autoload.setChecked(bool(data.get("autoload", True)))
        self.chk_autosort.setChecked(bool(data.get("autosort", False)))
        self.chk_autoscan.setChecked(bool(data.get("autoscan", False)))

        tab = int(data.get("tab", 0) or 0)
        if 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

        if files and self.chk_autoload.isChecked() and self.chk_autoscan.isChecked():
            QTimer.singleShot(400, self.on_scan)

    def _store_session(self) -> None:
        self._collect_profile()
        data = self.session
        data["files"] = [str(p) for p in self.lst_files.paths()]
        data["autosort"] = bool(self.chk_autosort.isChecked())
        data["dictionary"] = self.txt_dict.text().strip()
        data["out_dir"] = self.txt_out.text().strip()
        data["profile_path"] = self.cmb_profile.currentData() or ""
        data["profile"] = self.profile.to_dict()
        data["tab"] = self.tabs.currentIndex()
        data["autoload"] = self.chk_autoload.isChecked()
        data["autoscan"] = self.chk_autoscan.isChecked()
        save_session(data)

    def _dialog_dir(self, key: str, fallback: str = "") -> str:
        """Where a file dialog should open: this dialog's last folder."""
        return last_dir(self.session, key, fallback)

    def _remember(self, key: str, path: Any) -> None:
        remember_dir(self.session, key, path)

    # ================================================================== #
    # settings
    # ================================================================== #
    def _fit_to_screen(self, w: int, h: int):
        """Clamp a window size to what the display can actually show."""
        from PySide6.QtCore import QSize
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return QSize(w, h)
        avail = screen.availableGeometry()
        return QSize(min(w, avail.width() - 40), min(h, avail.height() - 60))

    def _load_settings(self) -> None:
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
            # a geometry saved on a bigger monitor must not strand the window
            # off the edge of this one
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                size = self._fit_to_screen(self.width(), self.height())
                if size != self.size():
                    self.resize(size)
                pos = self.pos()
                if not avail.contains(self.frameGeometry()):
                    self.move(max(avail.left() + 10, min(pos.x(), avail.right() - 200)),
                              max(avail.top() + 10, min(pos.y(), avail.bottom() - 200)))

    def closeEvent(self, e) -> None:                       # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("lang", language())
        try:
            self._store_session()
        except Exception as exc:            # never block closing over a session file
            self.log(f"Session not saved: {type(exc).__name__}: {exc}")
        super().closeEvent(e)
