#!/usr/bin/env python3
"""Stage 0 prototype: which window kind behaves correctly on KWin (Plasma 6 Wayland)?

Test harness only. It shows a small diagnostic bar and logs window/focus/screen
events; there is no carousel and no wallpaper backend, and nothing here starts
swww, mpvpaper or any wallpaper tool.

  --mode layer              wlr-layer-shell overlay via org.kde.layershell (as upstream)
  --mode layer-active       same + wantsToBeOnActiveScreen (layer-shell-qt, if available)
  --mode plain              frameless transparent xdg-toplevel, screen-sized
  --mode plain-fullscreen   frameless transparent xdg-toplevel, fullscreen

Run it again while it is open to toggle it closed (upstream single-instance
logic, reused unchanged). A watchdog closes it after --timeout seconds so a
misbehaving overlay can never hold the keyboard indefinitely.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

_T0 = time.monotonic()
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Unchanged upstream module (stdlib only); subclassed only to use its own lock file.
from core.single_instance import SingleInstance  # noqa: E402

_QML = {
    "layer": "LayerWindow.qml",
    "layer-active": "LayerWindowActive.qml",
    "plain": "PlainWindow.qml",
    "plain-fullscreen": "PlainWindow.qml",
}
_APP_ID = "wallfliper-kde-stage0"


class _Lock(SingleInstance):
    def __init__(self) -> None:
        super().__init__()
        runtime = os.environ.get("XDG_RUNTIME_DIR") or str(Path.home() / ".cache")
        self._path = Path(runtime) / f"{_APP_ID}.lock"


_log_file = None


def log(event: str, detail: str = "") -> None:
    """Print to stderr and append to the run log. Never raises: it is called from
    Qt slots and QML handlers, including during teardown after the file is closed."""
    elapsed = (time.monotonic() - _T0) * 1000
    print(f"[{elapsed:9.1f} ms] {event:<22} {detail}", file=sys.stderr, flush=True)
    handle = _log_file
    if handle is None or handle.closed:
        return
    try:
        handle.write(json.dumps({"ms": round(elapsed, 1), "event": event, "detail": detail}) + "\n")
        handle.flush()
    except (OSError, ValueError):
        pass


def _close_log() -> None:
    global _log_file
    handle, _log_file = _log_file, None
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass


def _on_qml_warnings(warnings) -> None:
    """One failing entry must not drop the rest of the batch."""
    for w in warnings:
        try:
            log("qml-warning", w.toString())
        except Exception as exc:  # noqa: BLE001 - must not propagate into Qt
            print(f"stage0: could not log a QML warning: {exc!r}", file=sys.stderr)


def _teardown(engine, guard) -> None:
    """Destroy the QML engine (windows, bindings, handlers) while `probe` and the log
    are still alive. Left to Python's frame cleanup the order is arbitrary: `probe`
    could vanish first, turning bindings like `probe.modeLabel` into TypeErrors."""
    try:
        import shiboken6
    except ImportError:
        return
    if engine is not None:
        shiboken6.delete(engine)
        log("teardown", "engine deleted")
    if guard is not None:
        shiboken6.delete(guard[2])  # the QSocketNotifier
        log("teardown", "signal notifier deleted")


def _install_signal_quit(app):
    """Same self-pipe pattern as upstream main.py: SIGTERM/SIGINT -> clean app.quit()."""
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


def main() -> int:
    global _log_file
    parser = argparse.ArgumentParser(prog="proto.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=sorted(_QML), default="layer")
    parser.add_argument("--screen", choices=("cursor", "primary", "none"), default="cursor",
                        help="plain modes only: which QScreen to ask for (default: under cursor)")
    parser.add_argument("--timeout", type=int, default=90,
                        help="auto-close after N seconds; 0 disables (default 90)")
    args = parser.parse_args()

    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / _APP_ID
    cache.mkdir(parents=True, exist_ok=True)

    lock = _Lock()
    if not lock.acquire():
        print("stage0: another instance was running; asked it to close (toggle).", file=sys.stderr)
        return 0

    engine = guard = None
    try:
        _log_file = open(cache / f"{args.mode}.log", "a", encoding="utf-8")
        log("start", f"mode={args.mode} screen={args.screen} pid={os.getpid()}")

        from PySide6.QtCore import Property, QObject, QTimer, QUrl, Slot, qVersion
        from PySide6.QtGui import QCursor, QGuiApplication, QWindow
        from PySide6.QtQml import QQmlApplicationEngine
        import PySide6

        class Probe(QObject):
            def __init__(self, label: str) -> None:
                super().__init__()
                self._label = label

            @Property(str, constant=True)
            def modeLabel(self) -> str:
                return self._label

            @Slot(str, str)
            def log(self, event: str, detail: str) -> None:  # called from QML
                log(event, detail)

        app = QGuiApplication(sys.argv)
        app.setApplicationName(_APP_ID)
        app.setDesktopFileName(_APP_ID)  # Wayland app_id
        guard = _install_signal_quit(app)
        app.aboutToQuit.connect(lambda: log("about-to-quit"))

        env = {k: os.environ.get(k, "<unset>") for k in (
            "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY",
            "QT_QPA_PLATFORM", "QT_WAYLAND_SHELL_INTEGRATION")}
        log("env", json.dumps({**env, "platform": app.platformName(), "qt": qVersion(),
                               "pyside6": PySide6.__version__, "python": sys.version.split()[0]}))
        for scr in app.screens():
            g = scr.geometry()
            log("screen", f"{scr.name()} {g.width()}x{g.height()}@{g.x()},{g.y()} "
                          f"dpr={scr.devicePixelRatio()} primary={scr is app.primaryScreen()}")
        cursor_screen = app.screenAt(QCursor.pos())
        log("cursor", f"pos={QCursor.pos().x()},{QCursor.pos().y()} "
                      f"screenAt={cursor_screen.name() if cursor_screen else None}")

        engine = QQmlApplicationEngine()
        engine.warnings.connect(_on_qml_warnings)
        probe = Probe(args.mode)
        engine.rootContext().setContextProperty("probe", probe)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "qml" / _QML[args.mode])))
        if not engine.rootObjects():
            log("load-failed", "QML did not load (for layer modes: org.kde.layershell missing, "
                               "or wantsToBeOnActiveScreen unsupported by the installed layer-shell-qt)")
            return 1

        if args.mode.startswith("plain"):
            root = engine.rootObjects()[0]
            if not isinstance(root, QWindow):
                log("load-failed", f"root object is {type(root).__name__}, not a window")
                return 1
            target = None
            if args.screen == "cursor":
                target = cursor_screen or app.primaryScreen()
            elif args.screen == "primary":
                target = app.primaryScreen()
            if target is not None:
                root.setScreen(target)
                log("set-screen", target.name())
            if args.mode == "plain-fullscreen":
                root.showFullScreen()
            else:
                root.show()

        if args.timeout > 0:
            QTimer.singleShot(args.timeout * 1000,
                              lambda: (log("watchdog-quit", f"{args.timeout}s"), app.quit()))
        code = app.exec()
        log("exit", f"code={code}")
        return code
    finally:
        _teardown(engine, guard)
        lock.release()
        _close_log()


if __name__ == "__main__":
    raise SystemExit(main())
