"""The one object QML talks to. Everything QML may ask of Python is a slot here.

Confirming runs the injected applier on a worker thread (see applier.ApplyRunner); the
result comes back to the UI thread through a queued Qt signal. Closing never waits for
the worker: it asks it to stop, and `shutdown()` later reaps it with a bounded wait.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImageReader

from kde.applier import ApplyResult, ApplyRunner, WallpaperApplier
from kde.collection import resolve_selection


def _qt_can_read(path: Path) -> bool:
    return QImageReader(str(path)).canRead()


class Controller(QObject):
    # QML-facing: an apply failed; show the message and let the user try again.
    applyFailed = Signal(str)
    # Worker thread -> UI thread: (ok, message, cancelled, output, path).
    _finished = Signal(bool, str, bool, str, str)

    def __init__(
        self,
        entries: list[dict],
        applier: WallpaperApplier,
        *,
        can_read: Callable[[Path], bool] = _qt_can_read,
        quit_app: Callable[[], None] = QGuiApplication.quit,
        log: Callable[[str, str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._entries = entries
        self._can_read = can_read
        self._quit = quit_app
        self._log = log or (lambda event, detail: None)
        self._closing = False
        self._runner = ApplyRunner(applier, self._on_worker_done)
        # Explicit queued connection: the emitter is a plain worker thread.
        self._finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)

    # ---- slots QML may call -------------------------------------------------

    @Slot(str, str)
    def log(self, event: str, detail: str) -> None:
        self._log(event, detail)

    @Slot()
    def close(self) -> None:
        """Esc / click outside. Never starts an apply; stops one that is running."""
        self._log("close", "")
        self._closing = True
        self._runner.cancel()
        self._quit()

    @Slot(int, result=str)
    def confirm(self, index: int) -> str:
        """Start applying entry `index`. Returns "" when an apply was started (the
        outcome arrives later: the app quits on success, applyFailed on failure), or
        a short message to show right away."""
        if self._closing:
            return ""
        if self._runner.busy:
            self._log("confirm-ignored", "an apply is already running")
            return "already applying"
        path, message = resolve_selection(self._entries, index)
        if path is None:
            self._log("confirm-refused", message)
            return message
        if not self._can_read(path):
            self._log("confirm-refused", f"cannot decode {path}")
            return "unsupported or unreadable image"
        if not self._runner.start(path):
            return "already applying"
        self._log("apply-start", str(path))
        return ""

    # ---- lifecycle ----------------------------------------------------------

    def shutdown(self) -> bool:
        """Call after the window is gone, before the process exits: stop and reap the
        worker. True if no worker is left running."""
        self._closing = True
        clean = self._runner.shutdown()
        self._log("shutdown", "worker stopped" if clean else "worker still running (abandoned)")
        return clean

    # ---- results ------------------------------------------------------------

    def _on_worker_done(self, result: ApplyResult, path: Path) -> None:
        # Worker thread: touch nothing but the signal.
        self._finished.emit(result.ok, result.message, result.cancelled, result.output, str(path))

    @Slot(bool, str, bool, str, str)
    def _on_finished(self, ok: bool, message: str, cancelled: bool, output: str, path: str) -> None:
        if output:
            self._log("apply-output", repr(output))
        if self._closing or cancelled:
            self._log("apply-ignored", "window already closing" if self._closing else "cancelled")
            return
        if ok:
            self._log("applied", path)
            self._quit()
        else:
            self._log("apply-failed", message)
            self.applyFailed.emit(message)
