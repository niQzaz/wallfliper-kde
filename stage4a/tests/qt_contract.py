#!/usr/bin/env python3
"""Behavioural contract of the Controller change and of the probe's refusals. Needs PySide6.

    python3 stage4a/tests/qt_contract.py

Runs offscreen in the same isolated environment as the probe (temp HOME/XDG/PATH, exec
guard armed). Not named test_*.py on purpose: it arms a process-wide guard, so it must
not share a process with the unit tests. Shows nothing on screen, applies nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from stage4a import guard  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="wallfliper-contract-"))
    guard.make_dirs(root)
    env = guard.isolated_env(root, os.environ)
    os.environ.clear()
    os.environ.update(env)
    guard.arm()
    try:
        from core.state import config_dir

        from PySide6.QtCore import QObject
        from PySide6.QtGui import QColor, QGuiApplication, QImage
        from PySide6.QtTest import QTest

        import ui.bridge as bridge
        from stage4a.fake_backend import RecordingBackend
        from stage4a.probe_controller import ProbeController
        from unittest import mock

        walls = root / "walls"
        for i, color in enumerate(("#336699", "#993366", "#669933")):
            img = QImage(160, 90, QImage.Format.Format_RGB32)
            img.fill(QColor(color))
            assert img.save(str(walls / f"img{i}.png"))
        cfg = config_dir()
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.json").write_text(json.dumps({
            "wallpaper_dir": str(walls), "color_hook": "", "transition": "none", "card_layout": "push"}))

        app = QGuiApplication(["wallfliper-contract"])  # noqa: F841
        made = []

        # ---- the contract of ui/bridge.py ---------------------------------
        sentinel = RecordingBackend()
        with mock.patch.object(bridge, "get_backend", return_value=sentinel) as gb:
            c = bridge.Controller()
            made.append(c)
            check("Controller() with no arguments still calls get_backend() once", gb.call_count == 1, str(gb.call_count))
            check("...and uses what it returned", c._backend is sentinel)

            parent = QObject()
            c_parent = bridge.Controller(parent)
            made.append(c_parent)
            check("Controller(parent) keeps parent positional", c_parent.parent() is parent)
            check("...and still falls back to get_backend()", c_parent._backend is sentinel and gb.call_count == 2)

        explicit = RecordingBackend()
        with mock.patch.object(bridge, "get_backend", side_effect=AssertionError("must not be called")) as gb:
            c2 = bridge.Controller(backend=explicit)
            made.append(c2)
            check("Controller(backend=...) never calls get_backend()", gb.call_count == 0)
            check("...and uses the injected backend", c2._backend is explicit)
            c3 = bridge.Controller(parent, backend=explicit)
            made.append(c3)
            check("parent and backend can be combined", c3.parent() is parent and c3._backend is explicit)
            try:
                bridge.Controller(explicit)  # backend must not be accepted positionally
                check("backend is keyword-only", False, "a positional backend was accepted")
            except TypeError:
                check("backend is keyword-only", True)

        # ---- the probe's refusals, on real objects -------------------------
        events: list[tuple[str, str]] = []
        fake = RecordingBackend()
        p = ProbeController(fake, sink=lambda e, d: events.append((e, d)))
        made.append(p)
        QTest.qWait(400)  # let the model load
        rows = p.model.rowCount()
        check("probe controller sees the 3 temp images", rows == 3, str(rows))

        returned = p.apply(0)
        check("apply() returns False (no shader surface in flight)", returned is False)
        check("apply() reaches the recording backend exactly once", len(fake.calls) == 1, str(fake.calls))
        check("apply() went through the probe path, not _apply_entry", p.blocked == [], str(p.blocked))
        check("apply() wrote no state.json", not (cfg / "state.json").exists())

        p.deleteWallpaper(0)
        check("deleteWallpaper is refused", any(b.startswith("deleteWallpaper") for b in p.blocked))
        check("...and no file was removed", sorted(f.name for f in walls.iterdir()) == ["img0.png", "img1.png", "img2.png"])
        check("...and nothing reached a trash", not (root / "data" / "Trash").exists())

        closed = []
        p.folderPickerClosed.connect(lambda: closed.append(1))
        p.pickFolder()
        check("pickFolder is refused but still releases the UI", closed == [1] and "pickFolder" in p.blocked)
        check("setFolderFromText is refused", p.setFolderFromText("/") == "disabled in the probe")
        check("the portal is reported unavailable", p.folderPortalAvailable() is False)

        p.setCardLayout("overlay")  # allowed: it only writes the temp config
        saved = json.loads((cfg / "config.json").read_text())
        check("setCardLayout writes only the temp config.json",
              saved.get("card_layout") == "overlay" and guard.inside(root, cfg / "config.json"))

        # ---- isolation evidence --------------------------------------------
        check("core.backends.wlroots was never imported", "core.backends.wlroots" not in sys.modules)
        check("core.seamless was never imported", "core.seamless" not in sys.modules)
        check("no external command was attempted", not guard.blocked_attempts(),
              json.dumps(guard.blocked_attempts())[:300])

        import shiboken6
        for obj in reversed(made):
            shiboken6.delete(obj)
        shiboken6.delete(parent)
    except Exception as exc:  # noqa: BLE001
        check("contract script ran to the end", False, f"{type(exc).__name__}: {exc}")
    finally:
        guard.disarm()
        shutil.rmtree(root, ignore_errors=True)

    print("\n=== Controller contract (offscreen) ===")
    for name, ok, detail in results:
        print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    failed = [n for n, ok, _ in results if not ok]
    print("\n" + ("ALL CHECKS PASSED" if not failed else f"{len(failed)} CHECK(S) FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
