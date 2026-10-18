#!/usr/bin/env python3
"""Wallfliper KDE, stage 1: a minimal keyboard-driven wallpaper picker.

  Left / Right  move the selection      Enter  confirm (prints the chosen path, exits)
  Esc           close                   click outside the panel closes; inside keeps it

Stage 1 does NOT change the desktop wallpaper: confirming only prints the chosen
file's path to stdout. Applying it is a later stage.

  python3 kde/main.py [--dir PATH] [--verbose] [--timeout SECONDS]
Run it again while it is open to close it (toggle).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_T0 = time.monotonic()
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from kde.collection import load_collection, resolve_selection  # noqa: E402
from kde.runtime import APP_ID, AppLock, install_signal_quit, teardown  # noqa: E402

_QML = Path(__file__).resolve().parent / "qml" / "Main.qml"
_verbose = False


def log(event: str, detail: str = "") -> None:
    if _verbose:
        print(f"[{(time.monotonic() - _T0) * 1000:9.1f} ms] {event:<14} {detail}",
              file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    global _verbose
    parser = argparse.ArgumentParser(prog="wallfliper-kde", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", metavar="PATH", help="wallpaper folder (overrides config.json)")
    parser.add_argument("--verbose", action="store_true", help="log events to stderr")
    parser.add_argument("--timeout", type=int, default=0, metavar="SECONDS",
                        help="auto-close after N seconds (safety net for first runs; default off)")
    args = parser.parse_args(argv)
    _verbose = args.verbose

    lock = AppLock()
    if not lock.acquire():
        print("wallfliper-kde: asked the running instance to close (toggle).", file=sys.stderr)
        return 0

    engine = guard = None
    try:
        collection = load_collection(args.dir)
        log("collection", f"dir={collection.directory} images={len(collection.entries)} "
                          f"detail={collection.detail!r}")

        from PySide6.QtCore import QObject, QTimer, QUrl, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine

        entries = collection.entries

        class Controller(QObject):
            """Everything QML may ask of Python; nothing else is exposed."""

            @Slot(str, str)
            def log(self, event: str, detail: str) -> None:
                log(event, detail)

            @Slot()
            def close(self) -> None:
                log("close", "")
                QGuiApplication.quit()

            @Slot(int, result=str)
            def confirm(self, index: int) -> str:
                """Empty string on success (the app quits); otherwise a message for the panel."""
                path, message = resolve_selection(entries, index)
                if path is None:
                    log("confirm-failed", message)
                    return message
                log("confirm", str(path))
                try:
                    sys.stdout.write(f"{path}\n")
                    sys.stdout.flush()
                except BrokenPipeError:
                    pass
                QGuiApplication.quit()
                return ""

        app = QGuiApplication(sys.argv[:1])
        app.setApplicationName(APP_ID)
        app.setDesktopFileName(APP_ID)  # Wayland app_id
        guard = install_signal_quit(app, log)
        app.aboutToQuit.connect(lambda: log("about-to-quit"))

        controller = Controller()
        engine = QQmlApplicationEngine()
        ctx = engine.rootContext()
        ctx.setContextProperty("controller", controller)
        ctx.setContextProperty("wallpapers", entries)
        ctx.setContextProperty("emptyDetail", collection.detail)
        ctx.setContextProperty("emptyHint", collection.hint)
        engine.load(QUrl.fromLocalFile(str(_QML)))
        if not engine.rootObjects():
            print("wallfliper-kde: failed to load the QML UI (is layer-shell-qt installed, "
                  "and is this a Wayland session?)", file=sys.stderr)
            return 1

        if args.timeout > 0:
            QTimer.singleShot(args.timeout * 1000,
                              lambda: (log("timeout", f"{args.timeout}s"), app.quit()))
        code = app.exec()
        log("exit", f"code={code}")
        return code
    finally:
        teardown(engine, guard, log)
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
