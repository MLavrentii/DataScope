"""Small reusable Qt widgets."""

from __future__ import annotations

import re

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QWidget,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QColorDialog,
    QSizePolicy,
    QVBoxLayout,
)

CSV_EXT = {".csv", ".txt", ".tsv", ".dat", ".xlsx", ".xlsm", ".xls"}


def argb_to_qcolor(argb: str) -> QColor:
    s = (argb or "").lstrip("#")
    if len(s) == 8:
        return QColor(int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16))
    if len(s) == 6:
        return QColor(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    return QColor("#ffffff")


def qcolor_to_argb(c: QColor) -> str:
    return f"FF{c.red():02X}{c.green():02X}{c.blue():02X}"


class ColorButton(QPushButton):
    """A button that shows and picks one colour (stored as Excel ARGB)."""

    changed = Signal(str)

    def __init__(self, argb: str = "FFFFFFFF", parent=None) -> None:
        super().__init__(parent)
        self._argb = argb
        self.setFixedWidth(64)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)
        self._refresh()

    def argb(self) -> str:
        return self._argb

    def set_argb(self, argb: str) -> None:
        self._argb = argb or "FFFFFFFF"
        self._refresh()

    def _refresh(self) -> None:
        c = argb_to_qcolor(self._argb)
        dark = (c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114) < 140
        self.setStyleSheet(
            f"background:{c.name()};color:{'#fff' if dark else '#111'};"
            "border:1px solid rgba(0,0,0,.25);border-radius:5px;padding:3px"
        )
        self.setText(c.name().upper()[1:])

    def _pick(self) -> None:
        c = QColorDialog.getColor(argb_to_qcolor(self._argb), self, "Colour")
        if c.isValid():
            self._argb = qcolor_to_argb(c)
            self._refresh()
            self.changed.emit(self._argb)


def natural_key(name: str) -> tuple:
    """Sort key that compares digit runs as numbers.

    Plain text sorting puts ``file10`` before ``file2``, and these exports are
    named ``001_PCS監視_2026-08-06.csv`` - so the numbers have to compare as
    numbers or "by name" would not mean "in order".  ``re.split`` on a digit
    group always yields text at even positions and digits at odd ones, so the
    tuples stay comparable position by position.
    """
    parts = re.split(r"(\d+)", str(name))
    return tuple(int(x) if i % 2 else x.lower() for i, x in enumerate(parts))


class DropFileList(QListWidget):
    """File list with drag & drop of files and folders, and a user-set order.

    Two kinds of drop land here and they must not be confused: URLs from
    outside add files, a drag that started inside this list reorders it.  The
    order is what the job runs in, and it is remembered in the session.
    """

    filesDropped = Signal(list)
    orderChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # reorder by dragging a row
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.autosort = False

    # -- drops ------------------------------------------------------------ #
    def _internal(self, e) -> bool:
        return e.source() is self and not e.mimeData().hasUrls()

    def dragEnterEvent(self, e) -> None:                     # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        elif self._internal(e):
            super().dragEnterEvent(e)
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:                      # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        elif self._internal(e):
            super().dragMoveEvent(e)
        else:
            e.ignore()

    def dropEvent(self, e) -> None:                          # noqa: N802
        if self._internal(e):
            super().dropEvent(e)          # Qt moves the rows for us
            self.autosort = False         # a hand-set order is not a sorted one
            self.orderChanged.emit()
            return
        paths: list[Path] = []
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.suffix.lower() in CSV_EXT and not child.name.startswith("~$"):
                        paths.append(child)
            elif p.suffix.lower() in CSV_EXT:
                paths.append(p)
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()

    def paths(self) -> list[Path]:
        return [Path(self.item(i).data(Qt.UserRole)) for i in range(self.count())]

    # -- order ------------------------------------------------------------ #
    def sort_by_name(self) -> None:
        """Reorder by file name (numbers as numbers), folder as the tie-break."""
        rows = [self.item(i) for i in range(self.count())]
        keep = [(Path(it.data(Qt.UserRole)), it.text(), it.toolTip()) for it in rows]
        keep.sort(key=lambda t: (natural_key(t[0].name), natural_key(str(t[0].parent))))
        selected = {str(Path(it.data(Qt.UserRole))) for it in self.selectedItems()}
        self.clear()
        for path, text, tip in keep:
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, str(path))
            it.setToolTip(tip)
            self.addItem(it)
            if str(path) in selected:
                it.setSelected(True)
        self.orderChanged.emit()

    def move_selected(self, delta: int) -> None:
        """Move the selected rows up (-1) or down (+1), keeping them selected."""
        rows = sorted(self.row(it) for it in self.selectedItems())
        if not rows or delta == 0:
            return
        if delta < 0 and rows[0] == 0:
            return
        if delta > 0 and rows[-1] == self.count() - 1:
            return
        for row in (rows if delta < 0 else reversed(rows)):
            it = self.takeItem(row)
            self.insertItem(row + delta, it)
            it.setSelected(True)
        self.autosort = False
        self.setCurrentRow(rows[0] + delta, QItemSelectionModel.NoUpdate)
        self.orderChanged.emit()

    def add_paths(self, paths: Iterable[Path]) -> int:
        have = {str(p) for p in self.paths()}
        added = 0
        for p in paths:
            p = Path(p)
            if str(p) in have:
                continue
            it = QListWidgetItem(f"{p.name}      —  {p.parent}")
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(str(p))
            self.addItem(it)
            have.add(str(p))
            added += 1
        if added and self.autosort:
            self.sort_by_name()
        return added


class MultiTermEdit(QWidget):
    """A comma-separated text field that knows what is actually in your data.

    * type freely - completion suggests real column names as you go
    * press the list button to pick from what the loaded files contain
    * picking a value appends it instead of replacing what you typed
    """

    editingFinished = Signal()

    def __init__(self, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QCompleter, QHBoxLayout, QLineEdit, QMenu, QToolButton

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(self.editingFinished)
        self.button = QToolButton()
        self.button.setText("▾")
        self.button.setToolTip("")
        self.button.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self)
        self.button.setMenu(self._menu)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)

        self._terms: list[tuple[str, str]] = []
        self._completer = QCompleter([], self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.activated.connect(self._complete_last)
        self.edit.setCompleter(self._completer)
        self._empty_text = ""

    # -- text ----------------------------------------------------------- #
    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:                    # noqa: N802
        self.edit.setText(value)

    def setPlaceholderText(self, value: str) -> None:         # noqa: N802
        self.edit.setPlaceholderText(value)

    def setToolTip(self, value: str) -> None:                 # noqa: N802
        super().setToolTip(value)
        self.edit.setToolTip(value)
        self.button.setToolTip(value)

    # -- available values ------------------------------------------------ #
    def set_terms(self, terms: list[tuple[str, str]], empty_text: str = "") -> None:
        """``terms`` = [(what the menu shows, what gets inserted), ...]."""
        self._terms = list(terms)
        self._empty_text = empty_text
        self._completer.model().setStringList([value for _, value in self._terms])
        self._menu.clear()
        if not self._terms:
            act = self._menu.addAction(empty_text or "—")
            act.setEnabled(False)
            return
        for label, value in self._terms[:80]:
            act = self._menu.addAction(label)
            act.triggered.connect(lambda _=False, v=value: self.append_term(v))

    def append_term(self, value: str) -> None:
        current = [t.strip() for t in self.edit.text().split(",") if t.strip()]
        if value in current:
            return
        current.append(value)
        self.edit.setText(", ".join(current))
        self.editingFinished.emit()

    def _complete_last(self, value: str) -> None:
        """Completion replaces only the term being typed, not the whole field."""
        parts = self.edit.text().split(",")
        parts[-1] = f" {value}" if len(parts) > 1 else value
        self.edit.setText(",".join(parts))
        self.edit.setCursorPosition(len(self.edit.text()))


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    pal = lab.palette()
    col = pal.color(QPalette.WindowText)
    col.setAlpha(150)
    pal.setColor(QPalette.WindowText, col)
    lab.setPalette(pal)
    f = lab.font()
    f.setPointSizeF(max(7.5, f.pointSizeF() - 0.5))
    lab.setFont(f)
    return lab


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f


def stack(*widgets) -> QVBoxLayout:
    lay = QVBoxLayout()
    for w in widgets:
        lay.addWidget(w)
    return lay
