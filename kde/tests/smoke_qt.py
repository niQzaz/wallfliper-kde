#!/usr/bin/env python3
"""Headless Qt smoke test of the picker UI (Picker.qml). Needs PySide6; does NOT need
layer-shell, a Wayland session or KDE, and never touches the desktop wallpaper.

    QT_QPA_PLATFORM=offscreen python3 kde/tests/smoke_qt.py

Drives the real QML with synthesized keys and mouse clicks against a stub controller
that records what the UI asked for. Offscreen rendering says nothing about how the
panel looks or about KWin behaviour; those stay manual checks.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, QPoint, Qt, QUrl, QtMsgType, Slot, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from kde.collection import load_collection  # noqa: E402

QML = ROOT / "kde" / "qml" / "Picker.qml"
NOMOD = Qt.KeyboardModifier.NoModifier
failures: list[str] = []
messages: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(name)


class Stub(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0
        self.confirmed: list[int] = []
        self.reply = ""

    @Slot(str, str)
    def log(self, event: str, detail: str) -> None:
        pass

    @Slot()
    def close(self) -> None:
        self.closed += 1

    @Slot(int, result=str)
    def confirm(self, index: int) -> str:
        self.confirmed.append(index)
        return self.reply


def make_image(path: Path, w: int, h: int, color: str) -> None:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path)), f"could not write {path}"


def build(collection, stub):
    view = QQuickView()
    ctx = view.rootContext()
    ctx.setContextProperty("controller", stub)
    ctx.setContextProperty("wallpapers", collection.entries)
    ctx.setContextProperty("emptyDetail", collection.detail)
    ctx.setContextProperty("emptyHint", collection.hint)
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(QML)))
    if view.status() != QQuickView.Status.Ready:
        for e in view.errors():
            print("QML ERROR:", e.toString())
        raise SystemExit("Picker.qml failed to load")
    view.resize(1280, 720)
    view.show()
    QTest.qWaitForWindowExposed(view)
    view.requestActivate()
    QTest.qWaitForWindowActive(view)  # may be False on offscreen; keys still reach the focus item
    root = view.rootObject()
    root.forceActiveFocus()
    QTest.qWait(400)  # let asynchronous thumbnails decode
    return view, root


def key(view, k) -> None:
    QTest.keyClick(view, k, NOMOD)


def click(view, x: float, y: float) -> None:
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, NOMOD, QPoint(int(x), int(y)))


def main() -> int:
    def handler(kind, ctx, msg):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            messages.append(msg)
    qInstallMessageHandler(handler)
    app = QGuiApplication(sys.argv[:1])  # noqa: F841

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "my wallpapers é"
        d.mkdir()
        make_image(d / "a wide 21x9.png", 3840, 1080, "#336699")
        make_image(d / "b portrait.png", 600, 1000, "#993366")
        make_image(d / "c accents é.jpg", 1920, 1080, "#669933")
        (d / "d corrupt.png").write_bytes(b"this is not a png")
        collection = load_collection(str(d))
        check("collection sees 4 images", len(collection.entries) == 4, str(len(collection.entries)))

        # ---- populated ----------------------------------------------------
        stub = Stub()
        view, root = build(collection, stub)
        check("count == 4", root.property("count") == 4)
        check("starts on index 0", root.property("currentIndex") == 0)
        key(view, Qt.Key.Key_Right)
        check("Right moves to 1", root.property("currentIndex") == 1)
        for _ in range(10):
            key(view, Qt.Key.Key_Right)
        check("Right clamps at the last card", root.property("currentIndex") == 3)
        for _ in range(10):
            key(view, Qt.Key.Key_Left)
        check("Left clamps at the first card", root.property("currentIndex") == 0)

        key(view, Qt.Key.Key_Right)
        key(view, Qt.Key.Key_Return)
        check("Enter confirms the selected index", stub.confirmed == [1], str(stub.confirmed))
        check("Enter alone does not close from QML", stub.closed == 0)

        stub.reply = "file no longer exists"
        key(view, Qt.Key.Key_Return)
        check("a failed confirm shows its message", root.property("notice") == "file no longer exists",
              str(root.property("notice")))
        key(view, Qt.Key.Key_Left)
        check("the message clears on the next key", root.property("notice") == "")
        stub.reply = ""

        key(view, Qt.Key.Key_Escape)
        check("Esc asks to close", stub.closed == 1, str(stub.closed))

        r = root.property("panelRect")
        pad = root.property("pad")
        cell_h = root.property("cellH")
        before = stub.closed
        click(view, r.x() + 6, r.y() + 6)
        check("click inside the panel keeps the window", stub.closed == before)
        click(view, 4, 4)
        check("click outside the panel closes", stub.closed == before + 1, str(stub.closed))

        idx = root.property("currentIndex")
        cx, cy = r.x() + r.width() / 2, r.y() + r.height() - pad - cell_h / 2
        click(view, cx, cy)
        check("click on a card keeps the window", stub.closed == before + 1)
        QTest.mouseDClick(view, Qt.MouseButton.LeftButton, NOMOD, QPoint(int(cx), int(cy)))
        check("double-click on the centre card confirms it", stub.confirmed[-1] == idx, str(stub.confirmed))
        view.close()
        del view, root

        # ---- empty --------------------------------------------------------
        empty = load_collection(str(Path(tmp) / "does not exist"))
        stub2 = Stub()
        view2, root2 = build(empty, stub2)
        check("empty: count == 0", root2.property("count") == 0)
        key(view2, Qt.Key.Key_Right)
        key(view2, Qt.Key.Key_Return)
        check("empty: arrows/Enter do nothing", stub2.confirmed == [] and stub2.closed == 0)
        key(view2, Qt.Key.Key_Escape)
        check("empty: Esc still closes", stub2.closed == 1)
        click(view2, 4, 4)
        check("empty: click outside still closes", stub2.closed == 2)
        view2.close()

    expected = ("d corrupt",)
    unexpected = [m for m in messages if not any(x in m for x in expected)]
    check("no QML warnings besides the corrupt file", not unexpected, " | ".join(unexpected[:3]))
    print("\nexpected warnings about the corrupt file:", len(messages) - len(unexpected))
    print("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
