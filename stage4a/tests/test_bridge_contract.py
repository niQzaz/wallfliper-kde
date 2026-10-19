"""The contract of the one change made to upstream ui/bridge.py: an optional, keyword-only
`backend`; calling Controller() with no argument behaves exactly as before. Static (AST)
checks run anywhere; the behavioural twin is stage4a/tests/qt_contract.py (needs PySide6)."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def controller_init() -> ast.FunctionDef:
    tree = ast.parse((ROOT / "ui" / "bridge.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Controller")
    return next(m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == "__init__")


class BridgeContract(unittest.TestCase):
    def test_signature_keeps_parent_positional_and_adds_keyword_only_backend(self):
        a = controller_init().args
        self.assertEqual([x.arg for x in a.args], ["self", "parent"])
        self.assertEqual(len(a.defaults), 1)
        self.assertIsNone(a.defaults[0].value)          # parent=None, as before
        self.assertEqual([x.arg for x in a.kwonlyargs], ["backend"])
        self.assertIsNone(a.kw_defaults[0].value)       # backend=None
        self.assertIsNone(a.vararg)
        self.assertIsNone(a.kwarg)

    def test_backend_defaults_to_get_backend_and_an_explicit_one_skips_it(self):
        assigns = [n for n in ast.walk(controller_init()) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Attribute) and t.attr == "_backend" for t in n.targets)]
        self.assertEqual(len(assigns), 1)
        self.assertEqual(ast.unparse(assigns[0].value), "backend if backend is not None else get_backend()")

    def test_get_backend_is_called_in_exactly_one_place_in_the_whole_module(self):
        tree = ast.parse((ROOT / "ui" / "bridge.py").read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "get_backend"]
        self.assertEqual(len(calls), 1)

    def test_nothing_else_in_the_constructor_names_a_concrete_backend(self):
        text = ast.unparse(controller_init())
        for word in ("Wlroots", "wlroots", "swww", "mpvpaper"):
            self.assertNotIn(word, text)

    def test_upstream_entry_point_still_constructs_controller_without_arguments(self):
        tree = ast.parse((ROOT / "main.py").read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "Controller"]
        self.assertTrue(calls)
        for c in calls:
            self.assertEqual((c.args, c.keywords), ([], []), "upstream main.py must keep Controller()")

    def test_backend_type_is_imported_from_the_public_package(self):
        tree = ast.parse((ROOT / "ui" / "bridge.py").read_text())
        names = {a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "core.backends"
                 for a in n.names}
        self.assertLessEqual({"WallpaperBackend", "get_backend"}, names)


if __name__ == "__main__":
    unittest.main()
