"""Background workers.

Long jobs run in a QThread; the UI is only ever touched through signals.
Both workers support cancellation and never let an exception escape into Qt.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core import pipeline
from core.models import Profile


class _Base(QThread):
    progress = Signal(int, str)
    message = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel


class ScanWorker(_Base):
    """Read the files and translate the headers - no output written."""

    done = Signal(object)          # pipeline.ScanResult

    def __init__(self, files: list[Path], profile: Profile, parent=None) -> None:
        super().__init__(parent)
        self.files = list(files)
        self.profile = profile

    def run(self) -> None:                       # noqa: D102  (Qt entry point)
        try:
            res = pipeline.scan(
                self.files, self.profile,
                progress=lambda p, m: self.progress.emit(p, m),
                cancelled=self.is_cancelled,
            )
            for m in res.messages:
                self.message.emit(m)
            self.done.emit(res)
        except Exception:
            self.failed.emit(traceback.format_exc())


class JobWorker(_Base):
    """Full run: cleaned workbooks + analysis workbook + HTML + charts."""

    done = Signal(object)          # pipeline.JobResult

    def __init__(self, files: list[Path], profile: Profile,
                 scan_result: Optional[object] = None, parent=None) -> None:
        super().__init__(parent)
        self.files = list(files)
        self.profile = profile
        self.scan_result = scan_result

    def run(self) -> None:                       # noqa: D102
        try:
            res = pipeline.run_job(
                self.files, self.profile,
                progress=lambda p, m: self.progress.emit(p, m),
                cancelled=self.is_cancelled,
                scan_result=self.scan_result,
            )
            for m in res.messages:
                self.message.emit(m)
            self.done.emit(res)
        except Exception:
            self.failed.emit(traceback.format_exc())
