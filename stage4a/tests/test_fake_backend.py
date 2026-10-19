"""The recording backend: records, starts nothing, imports nothing real, matches the interface."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from stage4a.fake_backend import RecordingBackend  # noqa: E402

ALLOWED_IMPORTS = {"__future__", "dataclasses", "pathlib", "typing"}


def imports_of(path: Path) -> set[str]:
    mods = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
    return mods


class FakeBackend(unittest.TestCase):
    def test_imports_nothing_that_could_start_a_process_or_reach_a_real_backend(self):
        self.assertLessEqual(imports_of(ROOT / "stage4a" / "fake_backend.py"), ALLOWED_IMPORTS)

    def test_code_names_no_real_tool_or_backend_module(self):
        # Code only: identifiers, imports and string literals. The docstring may explain
        # what the fake is protecting against.
        tree = ast.parse((ROOT / "stage4a" / "fake_backend.py").read_text())
        docstring = tree.body[0].value if isinstance(tree.body[0], ast.Expr) else None
        tokens = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                tokens.add(n.id)
            elif isinstance(n, ast.Attribute):
                tokens.add(n.attr)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                tokens |= {getattr(n, "module", None) or "", *(a.name for a in n.names)}
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not docstring:
                tokens.add(n.value)
        joined = " ".join(tokens)
        for word in ("subprocess", "system", "Popen", "wlroots", "seamless", "swww", "awww", "mpvpaper",
                     "core.backends", "plasma"):
            self.assertNotIn(word, joined)

    def test_matches_the_upstream_interface_by_shape(self):
        base = ast.parse((ROOT / "core" / "backends" / "base.py").read_text())
        iface = next(n for n in base.body if isinstance(n, ast.ClassDef) and n.name == "WallpaperBackend")
        wanted = {m.name: [a.arg for a in m.args.args] for m in iface.body
                  if isinstance(m, ast.FunctionDef) and any("abstractmethod" in ast.unparse(d) for d in m.decorator_list)}
        self.assertTrue(wanted, "found no abstract methods in WallpaperBackend")
        fake = ast.parse((ROOT / "stage4a" / "fake_backend.py").read_text())
        cls = next(n for n in fake.body if isinstance(n, ast.ClassDef) and n.name == "RecordingBackend")
        have = {m.name: [a.arg for a in m.args.args] for m in cls.body if isinstance(m, ast.FunctionDef)}
        for name, params in wanted.items():
            self.assertIn(name, have, f"fake lacks {name}")
            self.assertEqual(have[name], params, f"{name} signature differs")

    def test_records_calls_and_reports_them(self):
        seen = []
        fake = RecordingBackend(sink=lambda e, d: seen.append((e, d)))
        fake.set_image(Path("/x/a b.png"))
        fake.set_video(Path("/x/c.mp4"), "t")
        self.assertEqual([(c.method, c.path, c.transition) for c in fake.calls],
                         [("set_image", "/x/a b.png", None), ("set_video", "/x/c.mp4", "t")])
        self.assertEqual(len(seen), 2)
        self.assertTrue(fake.is_available())


if __name__ == "__main__":
    unittest.main()
