"""Process plumbing: single-instance toggle, signal -> clean quit, ordered teardown.

Same behaviour as the Stage 0 prototype, which was verified on KDE: a second launch
closes the running one (toggle), SIGTERM/SIGINT quit cleanly, and Qt objects are
destroyed engine-first while everything they reference is still alive.
"""

from __future__ import annotations

import os
import signal
import socket
from pathlib import Path
from typing import Callable

from core.single_instance import SingleInstance

APP_ID = "wallfliper-kde"


class AppLock(SingleInstance):
    """Upstream's flock+SIGTERM toggle, on its own lock file so it never collides
    with the original app or the Stage 0 prototype."""

    def __init__(self) -> None:
        super().__init__()
        runtime = os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".cache")
        self._path = Path(runtime) / f"{APP_ID}.lock"


def install_signal_quit(app, log: Callable[[str, str], None]):
    """SIGTERM/SIGINT -> app.quit() via a self-pipe (no timers, no polling)."""
    from PySide6.QtCore import QSocketNotifier

    reader, writer = socket.socketpair()
    reader.setblocking(False)
    writer.setblocking(False)
    signal.set_wakeup_fd(writer.fileno())
    notifier = QSocketNotifier(reader.fileno(), QSocketNotifier.Type.Read)

    def _drain_and_quit() -> None:
        try:
            reader.recv(64)
        except OSError:
            pass
        log("signal-quit", "SIGTERM/SIGINT (toggle or Ctrl+C)")
        app.quit()

    notifier.activated.connect(_drain_and_quit)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: None)
    return reader, writer, notifier


def teardown(engine, guard, log: Callable[[str, str], None]) -> None:
    """Delete the QML engine (windows, bindings, handlers) first, then the signal
    notifier. Left to Python's frame cleanup the order is arbitrary."""
    try:
        import shiboken6
    except ImportError:
        return
    if engine is not None:
        shiboken6.delete(engine)
        log("teardown", "engine deleted")
    if guard is not None:
        shiboken6.delete(guard[2])
        log("teardown", "signal notifier deleted")
