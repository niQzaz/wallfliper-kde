#!/usr/bin/env python3
"""Wallfliper KDE: a minimal keyboard-driven wallpaper picker for Plasma on Wayland.

  Left / Right  move the selection
  Enter         apply the selected image as the desktop wallpaper, then close
                (double-click a card does the same)
  Esc           close without applying      click outside the panel closes; inside keeps it

If applying fails, the panel says why and stays open so you can try again.

  python3 kde/main.py [--dir PATH] [--dry-run] [--verbose] [--timeout SECONDS]

--dry-run confirms the choice by printing its path and exits; it never starts
anything and never touches the wallpaper. Run the program again while it is open to
close it (toggle).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_T0 = time.monotonic()
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from kde.applier import DryRunApplier, PlasmaApplier  # noqa: E402
from kde.collection import load_collection  # noqa: E402
from kde.runtime import APP_ID, AppLock, install_signal_quit, teardown  # noqa: E402

_QML = Path(__file__).resolve().parent / "qml" / "Main.qml"
_verbose = False


def log(event: str, detail: str = "") -> None:
    if _verbose:
        print(f"[{(time.monotonic() - _T0) * 1000:9.1f} ms] {event:<14} {detail}",
              file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallfliper-kde", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", metavar="PATH", help="wallpaper folder (overrides config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the chosen path instead of applying it; never changes the wallpaper")
    parser.add_argument("--verbose", action="store_true", help="log events to stderr")
    parser.add_argument("--timeout", type=int, default=0, metavar="SECONDS",
                        help="auto-close after N seconds (safety net for first runs; default off)")
    return parser


def main(argv: list[str] | None = None) -> int:
    global _verbose
    args = build_parser().parse_args(argv)
    _verbose = args.verbose

    lock = AppLock()
    if not lock.acquire():
        print("wallfliper-kde: asked the running instance to close (toggle).", file=sys.stderr)
        return 0

    engine = guard = controller = None
    try:
        collection = load_collection(args.dir)
        log("collection", f"dir={collection.directory} images={len(collection.entries)} "
                          f"detail={collection.detail!r}")

        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine

        from kde.controller import Controller

        entries = collection.entries

        app = QGuiApplication(sys.argv[:1])
        app.setApplicationName(APP_ID)
        app.setDesktopFileName(APP_ID)  # Wayland app_id
        guard = install_signal_quit(app, log)
        app.aboutToQuit.connect(lambda: log("about-to-quit"))

        applier = DryRunApplier() if args.dry_run else PlasmaApplier()
        log("applier", type(applier).__name__)
        controller = Controller(entries, applier, log=log)
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
        if controller is not None:
            controller.shutdown()
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
