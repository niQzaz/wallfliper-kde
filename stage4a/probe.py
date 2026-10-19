#!/usr/bin/env python3
"""Stage 4A, phase 1: load upstream's QML with a recording backend, OFFSCREEN.

No window appears on your desktop and no keyboard is grabbed: the Qt platform is
forced to `offscreen`, display variables are removed, and D-Bus points at nothing.
Nothing here applies a wallpaper. See stage4a/README.md.

    python3 stage4a/probe.py --src "$HOME/Pictures/wallpapers"
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # the probe must not write __pycache__ into the repository
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Standard-library-only modules: safe to import before the environment is isolated.
from stage4a import guard  # noqa: E402
from stage4a.fake_backend import RecordingBackend  # noqa: E402

_T0 = time.monotonic()


def _xdg(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or Path.home() / default)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage4a/probe.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", required=True, metavar="DIR",
                   help="your wallpaper folder; its images are COPIED, the folder is only read")
    p.add_argument("--max-mb", type=int, default=500, help="refuse to copy more than this (default 500)")
    p.add_argument("--timeout", type=int, default=90, help="hard stop after N seconds (default 90)")
    p.add_argument("--keep-temp", action="store_true", help="keep the temporary directory for inspection")
    p.add_argument("--verbose", action="store_true", help="print every probe event as it happens")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.src).expanduser()

    # ---- 1. fingerprints of everything outside the temp area that must stay untouched
    real_config = _xdg("XDG_CONFIG_HOME", ".config") / "wallfliper"
    real_cache = _xdg("XDG_CACHE_HOME", ".cache") / "wallfliper"
    real_trash = _xdg("XDG_DATA_HOME", ".local/share") / "Trash"
    watched = {"real config dir": real_config, "real cache dir": real_cache,
               "real trash": real_trash, "source folder": src}
    before = {k: guard.snapshot(v) for k, v in watched.items()}
    stale_before = {p for pat in ("*.pyc", "*.qmlc") for p in ROOT.rglob(pat) if ".git" not in p.parts}

    # ---- 2. temp area, isolated environment, exec guard: BEFORE any core/ui/Qt import
    root = Path(tempfile.mkdtemp(prefix="wallfliper-probe-"))
    guard.make_dirs(root)
    isolated = guard.isolated_env(root, os.environ)
    os.environ.clear()
    os.environ.update(isolated)
    guard.arm()
    faulthandler.dump_traceback_later(args.timeout, exit=True)

    results: list[tuple[str, bool, str]] = []
    events: list[str] = []
    qt_messages: list[tuple[str, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    def log(event: str, detail: str = "") -> None:
        line = f"[{(time.monotonic() - _T0) * 1000:8.0f} ms] {event:<16} {detail}"
        events.append(line)
        if args.verbose:
            print(line, file=sys.stderr, flush=True)

    code = 1
    try:
        log("temp-root", str(root))
        # ---- 3. now the imports (cache paths in upstream are computed at import time)
        from core import library
        from core.state import cache_dir, config_dir

        copies = guard.copy_images(src, root / "walls", library.IMAGE_EXTENSIONS, args.max_mb * 1_000_000)
        cfg_dir = config_dir()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.json").write_text(json.dumps({
            "wallpaper_dir": str(root / "walls"), "color_hook": "", "transition": "none",
            "transition_duration": 1.0, "card_layout": "push"}, indent=2))
        log("copied", f"{len(copies)} images from {src}")

        from PySide6.QtCore import Qt, QUrl, QtMsgType, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtTest import QTest

        from stage4a.probe_controller import ProbeController
        from ui.model import PATH_ROLE

        def on_message(kind, ctx, msg):
            label = {QtMsgType.QtWarningMsg: "warning", QtMsgType.QtCriticalMsg: "critical",
                     QtMsgType.QtFatalMsg: "fatal"}.get(kind)
            if label:
                qt_messages.append((label, msg))
                log("qt-" + label, msg)
        qInstallMessageHandler(on_message)

        app = QGuiApplication(["wallfliper-probe"])
        backend = RecordingBackend(sink=log)
        controller = ProbeController(backend, sink=log)
        engine = QQmlApplicationEngine()
        quits: list[int] = []
        engine.quit.connect(lambda: quits.append(1))
        engine.rootContext().setContextProperty("controller", controller)
        engine.load(QUrl.fromLocalFile(str(ROOT / "ui" / "qml" / "Main.qml")))

        loaded = bool(engine.rootObjects())
        check("upstream Main.qml loaded", loaded,
              "" if loaded else "rootObjects() is empty: see the Qt messages below")

        # ---- 4. state that must hold whether or not the QML loaded
        check("controller uses the injected recording backend", controller._backend is backend)
        wall_dir = Path(controller.wallpaperDir)
        check("wallpaper folder is the temp copy", wall_dir == root / "walls", str(wall_dir))
        model = controller.model
        QTest.qWait(300)
        check("model rows == copied images", model.rowCount() == len(copies),
              f"{model.rowCount()} vs {len(copies)}")

        if loaded:
            win = engine.rootObjects()[0]
            QTest.qWaitForWindowExposed(win)
            win.requestActivate()
            QTest.qWaitForWindowActive(win)
            QTest.qWait(700)  # thumbnails, first layout
            focus_item = win.activeFocusItem() if hasattr(win, "activeFocusItem") else None
            check("a QML item holds keyboard focus", focus_item is not None,
                  "root object is not a QQuickWindow" if not hasattr(win, "activeFocusItem") else "")

            def current() -> int:
                return int(win.property("currentIndex"))

            def path_of(row: int) -> str:
                return str(model.data(model.index(row, 0), PATH_ROLE))

            def press(key, mods=Qt.KeyboardModifier.NoModifier, wait=450):
                QTest.keyClick(win, key, mods)
                QTest.qWait(wait)

            if len(copies) >= 2:
                i0 = current()
                press(Qt.Key.Key_Right)
                i1 = current()
                press("l")
                i2 = current()
                press("h")
                i3 = current()
                check("Right moves the selection", i1 != i0, f"{i0} -> {i1}")
                check("l moves it on", i2 != i1, f"{i1} -> {i2}")
                check("h moves it back", i3 == i1, f"{i2} -> {i3}, expected {i1}")
            else:
                check("navigation needs 2+ images", False, f"only {len(copies)} copied")

            seen = []
            for _ in range(4):  # push -> overlay -> flow -> honeycomb -> push
                press("t", wait=900)
                seen.append(controller.cardLayout)
            check("t cycles all four layouts", seen == ["overlay", "flow", "honeycomb", "push"], str(seen))

            n_before = len(backend.calls)
            expected = path_of(current())
            press(Qt.Key.Key_Space)
            check("Space calls the recording backend once",
                  len(backend.calls) == n_before + 1 and backend.calls[-1].path == expected,
                  f"calls={len(backend.calls)} last={backend.calls[-1].path if backend.calls else None}")
            check("Space keeps the window open", not quits)

            expected = path_of(current())
            press(Qt.Key.Key_Return)
            check("Enter calls the recording backend with the selected image",
                  backend.calls and backend.calls[-1].path == expected,
                  f"calls={len(backend.calls)}")
            check("Enter asks the app to quit", len(quits) == 1, f"quit requests: {len(quits)}")

            before_files = sorted(p.name for p in (root / "walls").iterdir())
            press("D", Qt.KeyboardModifier.ShiftModifier)
            check("Shift+D reaches the refusal, not the trash",
                  any(b.startswith("deleteWallpaper") for b in controller.blocked), str(controller.blocked))
            check("Shift+D removed nothing",
                  sorted(p.name for p in (root / "walls").iterdir()) == before_files
                  and not (root / "data" / "Trash").exists())

            press(Qt.Key.Key_Escape)
            check("Esc asks the app to quit", len(quits) == 2, f"quit requests: {len(quits)}")

        # ---- 5. isolation evidence
        for mod in ("core.backends.wlroots", "core.seamless"):
            check(f"{mod} was never imported", mod not in sys.modules)
        check("no external command was attempted (exec guard)", not guard.blocked_attempts(),
              json.dumps(guard.blocked_attempts())[:300])
        check("Path.home() is inside the temp area", guard.inside(root, Path.home()), str(Path.home()))
        check("config dir is inside the temp area", guard.inside(root, config_dir()), str(config_dir()))
        check("cache dir is inside the temp area", guard.inside(root, cache_dir()), str(cache_dir()))
        for modname, attr in (("core.thumbnails", "_THUMB_DIR"), ("core.previews", "_PREVIEW_DIR"),
                              ("core.colors", "_INDEX_PATH")):
            value = getattr(sys.modules.get(modname), attr, None)
            check(f"{modname}.{attr} is inside the temp area",
                  isinstance(value, Path) and guard.inside(root, value), str(value))

        # ---- 6. orderly teardown: engine first, then the controller (waits for its pools)
        import shiboken6
        shiboken6.delete(engine)
        shiboken6.delete(controller)
        del app
    except Exception as exc:  # noqa: BLE001 - report everything, never hide a failure
        check("probe ran to the end", False, f"{type(exc).__name__}: {exc}")
    finally:
        faulthandler.cancel_dump_traceback_later()
        guard.disarm()
        after = {k: guard.snapshot(v) for k, v in watched.items()}
        for name in watched:
            check(f"{name} unchanged", before[name] == after[name], f"{before[name]} -> {after[name]}")
        stale_after = {p for pat in ("*.pyc", "*.qmlc") for p in ROOT.rglob(pat) if ".git" not in p.parts}
        check("no .pyc/.qmlc written into the repository", stale_after == stale_before,
              ", ".join(sorted(str(p) for p in stale_after - stale_before)[:5]))

        qml_loaded = any(n == "upstream Main.qml loaded" and ok for n, ok, _ in results)
        qml_problems = [m for m in qt_messages if ".qml" in m[1] or "QML" in m[1]]
        if qml_loaded:
            check("no QML warnings or errors", not qml_problems, "; ".join(m[1] for m in qml_problems[:3]))
        else:  # a pass here would be vacuous: nothing was loaded to produce warnings
            check("no QML warnings or errors", False, "NOT VERIFIED: the QML did not load")

        print("\n=== Stage 4A probe, phase 1 (offscreen) ===")
        for name, ok, detail in results:
            print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
        print("\n-- recording backend calls:")
        print("   " + "\n   ".join(l for l in events if "fake-backend" in l or "probe-" in l) or "   (none)")
        print("\n-- all Qt warnings/criticals seen (%d):" % len(qt_messages))
        for kind, msg in qt_messages:
            print(f"   {kind}: {msg}")
        print("\n-- temp area:", root)
        for p in sorted(root.rglob("*")):
            if p.is_file() and "walls" not in p.parts:
                print("   ", p.relative_to(root), f"({p.stat().st_size} B)")
        failed = [n for n, ok, _ in results if not ok]
        print(f"\n{'ALL CHECKS PASSED' if not failed else str(len(failed)) + ' CHECK(S) FAILED'}"
              "  (offscreen only: says nothing about how it looks or behaves under KWin)")
        code = 0 if not failed else 1
        if args.keep_temp:
            print("temp kept:", root)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
