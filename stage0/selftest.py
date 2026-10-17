#!/usr/bin/env python3
"""Static / logic checks for the Stage 0 prototype. Stdlib only: needs no Qt, no
Wayland, does not touch KDE. It cannot prove the fix works in a real session.

  python3 stage0/selftest.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(name)


spec = importlib.util.spec_from_file_location("proto", HERE / "proto.py")
proto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proto)  # imports only stdlib + core.single_instance

# --- 1. log() never raises, whatever state the file is in -------------------
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "t.log"
    proto._log_file = open(path, "a", encoding="utf-8")
    proto.log("before-close", "x")
    proto._log_file.close()                     # file closed but global still set (the old failure)
    try:
        proto.log("after-close", "x")
        ok = True
    except Exception as exc:                    # noqa: BLE001
        ok = False
    check("log() after the file was closed does not raise", ok)
    proto._log_file = None
    try:
        proto.log("no-file", "x"); ok = True
    except Exception:                           # noqa: BLE001
        ok = False
    check("log() with no log file does not raise", ok)
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    check("log() wrote only the entry made while open", [l["event"] for l in lines] == ["before-close"])

# --- 2. warning batch: one failure must not drop the rest -------------------
class W:
    def __init__(self, text, boom=False):
        self.text, self.boom = text, boom
    def toString(self):
        if self.boom:
            raise RuntimeError("boom")
        return self.text

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "w.log"
    proto._log_file = open(path, "a", encoding="utf-8")
    try:
        proto._on_qml_warnings([W("a"), W("bad", boom=True), W("c")]); ok = True
    except Exception:                           # noqa: BLE001
        ok = False
    proto._close_log()
    got = [json.loads(l)["detail"] for l in path.read_text().splitlines()]
    check("warning handler survives a failing entry", ok)
    check("warnings after the failing one are still logged", got == ["a", "c"], str(got))
    check("_close_log() leaves no open handle", proto._log_file is None)

# --- 3. _teardown order: engine first, then notifier; log still open --------
order: list[str] = []
fake = types.ModuleType("shiboken6")
fake.delete = lambda obj: order.append(obj)
sys.modules["shiboken6"] = fake
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "t.log"
    proto._log_file = open(path, "a", encoding="utf-8")
    proto._teardown("ENGINE", ("r", "w", "NOTIFIER"))
    still_open = not proto._log_file.closed
    proto._close_log()
check("_teardown deletes engine before the notifier", order == ["ENGINE", "NOTIFIER"], str(order))
check("log file is still open during teardown", still_open)
del sys.modules["shiboken6"]
sys.modules["shiboken6"] = None                 # simulate ImportError
try:
    proto._teardown("E", None); ok = True
except Exception:                               # noqa: BLE001
    ok = False
check("_teardown tolerates a missing shiboken6", ok)
sys.modules.pop("shiboken6", None)

# --- 4. main(): teardown runs before the log is closed ----------------------
tree = ast.parse((HERE / "proto.py").read_text())
main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
tries = [n for n in ast.walk(main) if isinstance(n, ast.Try) and n.finalbody]
calls = [c.value.func.id for c in tries[-1].finalbody
         if isinstance(c, ast.Expr) and isinstance(c.value, ast.Call) and isinstance(c.value.func, ast.Name)]
check("finally: _teardown precedes _close_log",
      "_teardown" in calls and "_close_log" in calls and calls.index("_teardown") < calls.index("_close_log"), str(calls))
src = (HERE / "proto.py").read_text()
check("no lambda-list comprehension warning handler left", "for w in ws" not in src)
check("no bare `_log_file.close()` outside _close_log", src.count("_log_file.close()") == 0)

# --- 5. QML: no unguarded read of `probe` in a binding; braces balance ------
for qml in sorted((HERE / "qml").glob("*.qml")):
    text = qml.read_text()
    check(f"{qml.name}: braces balanced", text.count("{") == text.count("}"))
    bad = [(i, l.strip()) for i, l in enumerate(text.splitlines(), 1)
           if re.search(r"\bprobe\b", l) and "probe.log(" not in l and "probe ?" not in l
           and not l.strip().startswith("//")]
    check(f"{qml.name}: every non-call use of `probe` is null-guarded", not bad, str(bad))

print()
print("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
