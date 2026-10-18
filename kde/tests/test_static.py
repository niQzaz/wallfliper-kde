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
        self.qml = "\n".join(q.read_text() for q in QML)

    def test_controller_calls_exist_as_slots(self):
        used = set(re.findall(r"\bcontroller\.(\w+)", self.qml))
        slots = set(re.findall(r"@Slot\([^)]*\)\s*\n\s*def (\w+)", self.main))
        self.assertTrue(used, "QML never calls the controller?")
        self.assertLessEqual(used, slots, f"QML uses unknown controller methods: {used - slots}")

    def test_context_properties_are_all_provided(self):
        provided = set(re.findall(r'setContextProperty\("(\w+)"', self.main))
        for name in ("wallpapers", "emptyDetail", "emptyHint", "controller"):
            self.assertIn(name, provided)
            self.assertRegex(self.qml, rf"\b{name}\b")


class Isolation(unittest.TestCase):
    BANNED_WORDS = ("swww", "awww", "mpvpaper", "shell=True", "os.system", "hyprctl")

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

    def test_stage1_never_changes_the_desktop(self):
        for p in PY:
            self.assertNotIn("subprocess", p.read_text(), p.name)
            self.assertNotIn("plasma-apply", p.read_text(), p.name)


class Shutdown(unittest.TestCase):
    def test_finally_tears_down_before_releasing_the_lock(self):
        tree = ast.parse((KDE / "main.py").read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        tries = [n for n in ast.walk(main) if isinstance(n, ast.Try) and n.finalbody]
        calls = []
        for stmt in tries[-1].finalbody:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                f = stmt.value.func
                calls.append(f.id if isinstance(f, ast.Name) else f.attr)
        self.assertEqual(calls, ["teardown", "release"])

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
