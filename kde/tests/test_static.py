"""Static consistency checks for the KDE picker. No Qt, no Wayland, no KDE.

They cannot prove the UI works in a real session; they guard against regressions that
are visible in the source: the verified window block drifting, design rules being
broken, Python/QML disagreeing about names, isolation from the old wallpaper tools,
and shutdown order.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KDE = ROOT / "kde"
QML = sorted((KDE / "qml").glob("*.qml"))
PY = [p for p in sorted(KDE.glob("*.py"))]
STAGE0_LAYER = ROOT / "stage0" / "qml" / "LayerWindow.qml"
sys.path.insert(0, str(ROOT))


def layer_lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines()
            if "LayerShell." in l or l.strip().startswith("import org.kde.layershell")]


class WindowBlock(unittest.TestCase):
    def test_layer_shell_block_matches_verified_stage0(self):
        self.assertEqual(layer_lines((KDE / "qml" / "Main.qml").read_text()),
                         layer_lines(STAGE0_LAYER.read_text()))

    def test_only_main_qml_touches_layer_shell(self):
        for q in QML:
            if q.name != "Main.qml":
                self.assertNotIn("layershell", q.read_text().lower(), q.name)


class Design(unittest.TestCase):
    FORBIDDEN = ("radius", "DropShadow", "MultiEffect", "RectangularGlow", "GaussianBlur",
                 "FastBlur", "gradient", "layer.effect", "localStorage", "sessionStorage")

    def test_no_forbidden_visual_features(self):
        for q in QML:
            # Code only: comments may legitimately say "no gradients".
            code = "\n".join(l.split("//", 1)[0] for l in q.read_text().splitlines())
            for word in self.FORBIDDEN:
                self.assertFalse(word in code, f"{q.name} uses {word}")

    def test_braces_balanced(self):
        for q in QML:
            t = q.read_text()
            self.assertEqual(t.count("{"), t.count("}"), q.name)


class PythonQmlContract(unittest.TestCase):
    def setUp(self):
        self.main = (KDE / "main.py").read_text()
        self.controller = (KDE / "controller.py").read_text()
        self.qml = "\n".join(q.read_text() for q in QML)

    def test_controller_calls_exist_as_slots(self):
        used = set(re.findall(r"\bcontroller\.(\w+)", self.qml))
        slots = set(re.findall(r"@Slot\([^)]*\)\s*\n\s*def (\w+)", self.controller))
        self.assertTrue(used, "QML never calls the controller?")
        self.assertLessEqual(used, slots, f"QML uses unknown controller methods: {used - slots}")

    def test_qml_signal_handlers_match_real_signals(self):
        handlers = re.findall(r"function on([A-Z]\w*)\(", self.qml)
        signals = set(re.findall(r"^\s+(\w+) = Signal\(", self.controller, re.MULTILINE))
        self.assertIn("applyFailed", signals)
        for h in handlers:
            self.assertIn(h[0].lower() + h[1:], signals, f"QML handles on{h} but no such signal")

    def test_controller_is_defined_once_and_only_in_controller_py(self):
        self.assertNotIn("class Controller", self.main)
        self.assertIn("class Controller(QObject)", self.controller)

    def test_context_properties_are_all_provided(self):
        provided = set(re.findall(r'setContextProperty\("(\w+)"', self.main))
        for name in ("wallpapers", "emptyDetail", "emptyHint", "controller"):
            self.assertIn(name, provided)
            self.assertRegex(self.qml, rf"\b{name}\b")


class Isolation(unittest.TestCase):
    BANNED_WORDS = ("swww", "awww", "mpvpaper", "shell=True", "os.system", "os.popen",
                    "os.spawn", "os.exec", "hyprctl")

    def test_no_old_wallpaper_tools_or_shell_in_kde_code(self):
        for p in PY:
            text = p.read_text()
            for w in self.BANNED_WORDS:
                self.assertNotIn(w, text, f"{p.name} mentions {w}")

    def test_kde_does_not_import_backends_or_old_ui(self):
        for p in PY:
            for node in ast.walk(ast.parse(p.read_text())):
                mods = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods.append(node.module)
                elif isinstance(node, ast.Import):
                    mods += [a.name for a in node.names]
                for m in mods:
                    self.assertFalse(m.startswith(("core.backends", "ui", "main")), f"{p.name}: import {m}")

    def test_no_hardcoded_home_or_other_users_paths(self):
        for p in PY + QML:
            text = p.read_text()
            self.assertNotRegex(text, r"/home/\w+", p.name)
            self.assertNotIn("xz0", text, p.name)

    def test_subprocess_and_the_wallpaper_tool_live_only_in_applier(self):
        for p in PY:
            if p.name == "applier.py":
                continue
            text = p.read_text()
            self.assertNotIn("subprocess", text, p.name)
            self.assertNotIn("plasma-apply", text, p.name)

    def test_applier_never_uses_a_shell_or_a_string_command(self):
        tree = ast.parse((KDE / "applier.py").read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
                 and n.func.attr in {"Popen", "run", "call", "check_call", "check_output"}]
        self.assertTrue(calls, "expected exactly the Popen call")
        for c in calls:
            for kw in c.keywords:
                if kw.arg == "shell":
                    self.assertIsInstance(kw.value, ast.Constant)
                    self.assertIs(kw.value.value, False)
            first = c.args[0]
            self.assertIsInstance(first, (ast.List, ast.Name), "command must be an argument list")

    def test_dry_run_applier_cannot_start_anything(self):
        tree = ast.parse((KDE / "applier.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DryRunApplier")
        names = {n.id for n in ast.walk(cls) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(cls) if isinstance(n, ast.Attribute)}
        for forbidden in ("subprocess", "PlasmaApplier", "shutil", "os", "Popen", "run"):
            self.assertNotIn(forbidden, names | attrs)

    def test_main_chooses_exactly_one_applier_from_the_flag(self):
        main = (KDE / "main.py").read_text()
        self.assertIn("DryRunApplier() if args.dry_run else PlasmaApplier()", main)
        self.assertEqual(main.count("PlasmaApplier()"), 1)


class Shutdown(unittest.TestCase):
    def test_finally_tears_down_then_stops_the_worker_then_releases_the_lock(self):
        tree = ast.parse((KDE / "main.py").read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        tries = [n for n in ast.walk(main) if isinstance(n, ast.Try) and n.finalbody]
        calls = []
        for stmt in tries[-1].finalbody:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    f = node.func
                    calls.append((node.lineno, f.id if isinstance(f, ast.Name) else f.attr))
        self.assertEqual([name for _, name in sorted(calls)], ["teardown", "shutdown", "release"])

    def test_teardown_deletes_engine_before_notifier(self):
        import types
        from kde import runtime
        order, logs = [], []
        fake = types.ModuleType("shiboken6")
        fake.delete = order.append
        sys.modules["shiboken6"] = fake
        try:
            runtime.teardown("ENGINE", ("r", "w", "NOTIFIER"), lambda e, d: logs.append(e))
        finally:
            del sys.modules["shiboken6"]
        self.assertEqual(order, ["ENGINE", "NOTIFIER"])

    def test_sources_compile(self):
        for p in PY:
            compile(p.read_text(), str(p), "exec")


if __name__ == "__main__":
    unittest.main()
