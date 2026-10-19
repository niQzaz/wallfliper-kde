"""Static audit of the probe and of the upstream code it drives. No Qt.

The point of deriving things from the sources: if upstream grows a new side effect, or
someone edits the probe unsafely, these fail instead of the probe silently doing more.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE_FILES = [p for p in sorted((ROOT / "stage4a").glob("*.py")) if p.name != "__init__.py"]

# Names that mean "touches the outside world" when a Controller method reaches them.
SIDE = {"_apply_entry", "notify_color_tools", "save_state", "moveToTrash", "_backend",
        "_folder_chooser", "first_frame", "expanduser", "save_config"}
# Reaches a side-effect name but is acceptable unchanged, with the reason.
ACCEPTED = {
    "__init__": "construction; everything it reaches is checked separately",
    "setCardLayout": "writes config.json under XDG_CONFIG_HOME, which the probe points into the temp area",
}


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def controller_methods() -> dict[str, ast.FunctionDef]:
    cls = next(n for n in parse(ROOT / "ui" / "bridge.py").body
               if isinstance(n, ast.ClassDef) and n.name == "Controller")
    return {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}


def reaches_side_effects() -> set[str]:
    methods = controller_methods()
    direct: dict[str, tuple[set[str], set[str]]] = {}
    for name, m in methods.items():
        names, calls = set(), set()
        for n in ast.walk(m):
            if isinstance(n, ast.Name):
                names.add(n.id)
            if isinstance(n, ast.Attribute):
                names.add(n.attr)
                if isinstance(n.value, ast.Name) and n.value.id == "self" and n.attr in methods:
                    calls.add(n.attr)
        direct[name] = (names, calls)

    def reach(name: str, seen: set[str]) -> set[str]:
        if name in seen:
            return set()
        seen.add(name)
        names, calls = direct[name]
        out = names & SIDE
        for c in calls:
            out |= reach(c, seen)
        return out

    return {name for name in methods if reach(name, set())}


def code_tokens(path: Path) -> str:
    tree = parse(path)
    doc = tree.body[0].value if tree.body and isinstance(tree.body[0], ast.Expr) else None
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out += [getattr(n, "module", None) or "", *(a.name for a in n.names)]
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc:
            out.append(n.value)
    return " ".join(out)


class ProbeControllerCoversEverySideEffect(unittest.TestCase):
    def setUp(self):
        cls = next(n for n in parse(ROOT / "stage4a" / "probe_controller.py").body
                   if isinstance(n, ast.ClassDef) and n.name == "ProbeController")
        self.overrides = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}

    def test_every_method_reaching_a_side_effect_is_overridden_or_explicitly_accepted(self):
        required = reaches_side_effects() - set(ACCEPTED)
        self.assertTrue(required, "analysis found nothing: the audit itself is broken")
        missing = required - set(self.overrides)
        self.assertFalse(missing, f"upstream methods with side effects the probe does not override: {sorted(missing)}")

    def test_the_known_dangerous_ones_are_in_the_derived_set(self):
        derived = reaches_side_effects()
        for name in ("apply", "_apply_entry", "deleteWallpaper", "pickFolder", "setFolder",
                     "setFolderFromText", "shaderSurfaceReady", "shaderTransitionFailed"):
            self.assertIn(name, derived)

    def test_overrides_match_a_real_upstream_method_and_its_arity(self):
        upstream = controller_methods()
        for name, m in self.overrides.items():
            if name == "__init__" or name == "_refuse":
                continue
            self.assertIn(name, upstream, f"{name} overrides nothing upstream (typo?)")
            self.assertEqual(len(m.args.args), len(upstream[name].args.args), f"{name}: parameter count differs")

    def test_overriding_slots_are_declared_as_slots_like_upstream(self):
        upstream = controller_methods()
        slot = lambda m: any(ast.unparse(d).startswith("Slot") for d in m.decorator_list)  # noqa: E731
        for name, m in self.overrides.items():
            if name in upstream and slot(upstream[name]):
                self.assertTrue(slot(m), f"{name} is a Slot upstream but not in the probe")

    def test_apply_in_the_probe_goes_straight_to_the_backend(self):
        apply = ast.unparse(self.overrides["apply"])
        self.assertIn("self._backend.set_image", apply)
        for forbidden in ("_apply_entry", "notify_color_tools", "save_state", "save_config", "shaderTransitionRequested"):
            self.assertNotIn(forbidden, apply)

    def test_private_attributes_the_probe_reaches_into_still_exist_upstream(self):
        init = ast.unparse(controller_methods()["__init__"])
        for attr in ("self._proxy =", "self._model =", "self._backend ="):
            self.assertIn(attr, init)


class ProbeOrderAndIsolation(unittest.TestCase):
    def setUp(self):
        self.tree = parse(ROOT / "stage4a" / "probe.py")
        self.main = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")

    def first_line(self, pred) -> int:
        lines = [n.lineno for n in ast.walk(self.main) if pred(n)]
        self.assertTrue(lines)
        return min(lines)

    def test_no_upstream_or_qt_import_at_module_level(self):
        for n in self.tree.body:
            mods = []
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.append(n.module)
            elif isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            for m in mods:
                self.assertFalse(m.startswith(("core", "ui", "PySide6", "shiboken6", "stage4a.probe_controller")), m)

    def test_environment_is_isolated_and_the_guard_armed_before_any_core_ui_or_qt_import(self):
        env_update = self.first_line(lambda n: isinstance(n, ast.Call) and ast.unparse(n.func) == "os.environ.update")
        arm = self.first_line(lambda n: isinstance(n, ast.Call) and ast.unparse(n.func) == "guard.arm")
        heavy = self.first_line(lambda n: isinstance(n, (ast.Import, ast.ImportFrom)) and (
            (getattr(n, "module", None) or "").startswith(("core", "ui", "PySide6", "stage4a.probe_controller"))
            or any(a.name.startswith(("core", "ui", "PySide6", "shiboken6")) for a in n.names)))
        self.assertLess(env_update, heavy)
        self.assertLess(arm, heavy)

    def test_the_real_environment_is_cleared_not_just_overlaid(self):
        calls = [ast.unparse(n.func) for n in ast.walk(self.main) if isinstance(n, ast.Call)]
        self.assertIn("os.environ.clear", calls)

    def test_probe_never_chooses_a_real_display_platform(self):
        text = (ROOT / "stage4a" / "probe.py").read_text()
        self.assertNotIn("QT_QPA_PLATFORM", text)           # only guard.isolated_env sets it
        self.assertIn('"QT_QPA_PLATFORM": "offscreen"', (ROOT / "stage4a" / "guard.py").read_text())
        for word in ("wayland", "WAYLAND", "showFullScreen", "--live"):
            self.assertNotIn(word, code_tokens(ROOT / "stage4a" / "probe.py"))

    def test_probe_loads_upstreams_own_unmodified_qml_entry_file(self):
        text = (ROOT / "stage4a" / "probe.py").read_text()
        self.assertIn('ROOT / "ui" / "qml" / "Main.qml"', text)

    def test_probe_asks_for_the_isolated_temp_config_not_the_users(self):
        text = (ROOT / "stage4a" / "probe.py").read_text()
        self.assertIn('"transition": "none"', text)          # keeps the shader path off
        self.assertIn('"color_hook": ""', text)
        self.assertIn("config_dir()", text)                   # resolved after isolation


class ProbeFilesAreSafe(unittest.TestCase):
    def test_probe_code_starts_no_process_and_names_no_real_backend(self):
        for path in PROBE_FILES:
            tree = parse(path)
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    self.assertNotIn("subprocess", [a.name for a in n.names], path.name)
                if isinstance(n, ast.ImportFrom):
                    self.assertNotEqual(n.module, "subprocess", path.name)
                    self.assertFalse((n.module or "").startswith("core.backends"), path.name)
                if isinstance(n, ast.Call) and ast.unparse(n.func) in {"os.system", "os.popen", "os.execv", "os.fork"}:
                    self.fail(f"{path.name}: {ast.unparse(n.func)}")
            # Identifiers and imports may never name a real backend or tool.
            idents = " ".join(
                [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
                + [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
                + [m for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                   for m in [getattr(n, "module", None) or "", *(a.name for a in n.names)]])
            for word in ("wlroots", "Wlroots", "seamless", "swww", "awww", "mpvpaper", "hyprctl"):
                self.assertNotIn(word, idents, f"{path.name} uses {word}")
            # String literals: the probe may NAME the two upstream modules it checks were
            # never imported; nothing else here may mention a real tool.
            checked_names = {"core.backends.wlroots", "core.seamless"}
            doc = tree.body[0].value if tree.body and isinstance(tree.body[0], ast.Expr) else None
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc \
                        and n.value not in checked_names and path.name != "guard.py":
                    for word in ("wlroots", "seamless", "swww", "awww", "mpvpaper", "hyprctl", "shell=True"):
                        self.assertNotIn(word, n.value, f"{path.name}: string {n.value!r}")

    def test_guard_names_no_real_tool(self):
        tokens = code_tokens(ROOT / "stage4a" / "guard.py")
        for word in ("wlroots", "swww", "mpvpaper", "hyprctl", "plasma-apply"):
            self.assertNotIn(word, tokens)


class UpstreamReachableCodeHasNotGrownNewReach(unittest.TestCase):
    """If upstream starts launching programs from a new place, this fails and the probe's
    guarantees must be re-examined before the probe is run again."""

    SPAWNERS = {"core/colors.py", "core/firstframe.py", "core/integrations.py",
                "core/previews.py", "core/thumbnails.py"}
    FFMPEG_ONLY = SPAWNERS - {"core/integrations.py"}

    def reachable(self):
        skip = {"core/seamless.py", "core/deps.py", "core/single_instance.py"}
        files = [p for p in sorted((ROOT / "core").glob("*.py")) if f"core/{p.name}" not in skip]
        return files + sorted((ROOT / "ui").glob("*.py")) + [ROOT / "core" / "backends" / "base.py",
                                                             ROOT / "core" / "backends" / "transitions.py"]

    def test_only_the_known_modules_import_subprocess(self):
        found = set()
        for p in self.reachable():
            for n in ast.walk(parse(p)):
                if (isinstance(n, ast.Import) and "subprocess" in [a.name for a in n.names]) or \
                        (isinstance(n, ast.ImportFrom) and n.module == "subprocess"):
                    found.add(str(p.relative_to(ROOT)))
        self.assertEqual(found, self.SPAWNERS)

    def test_no_qt_process_apis_anywhere_reachable(self):
        for p in self.reachable():
            text = p.read_text()
            for word in ("QProcess", "startDetached", "QDesktopServices", "openUrlExternally"):
                self.assertNotIn(word, text, f"{p.name}: {word}")
        for q in sorted((ROOT / "ui" / "qml").glob("*.qml")):
            for word in ("Qt.openUrlExternally", "Process", "FileDialog", "FolderDialog", "XMLHttpRequest", "WebSocket"):
                self.assertNotIn(word, q.read_text(), f"{q.name}: {word}")

    def test_ffmpeg_modules_check_for_the_tool_before_running_it(self):
        for rel in sorted(self.FFMPEG_ONLY):
            text = (ROOT / rel).read_text()
            self.assertIn('shutil.which("ffmpeg")', text, rel)

    def test_no_reachable_code_hard_codes_an_absolute_command_path(self):
        pattern = re.compile(r"""['"]/(usr|bin|sbin|opt|run|var|etc|home|tmp)/""")
        for p in self.reachable():
            for lineno, line in enumerate(p.read_text().splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                self.assertIsNone(pattern.search(line), f"{p.name}:{lineno}: {line.strip()}")

    def test_trash_is_only_touched_in_the_delete_slot(self):
        users = [str(p.relative_to(ROOT)) for p in self.reachable() if "moveToTrash" in p.read_text()]
        self.assertEqual(users, ["ui/bridge.py"])
        methods = controller_methods()
        touching = [n for n, m in methods.items() if "moveToTrash" in ast.unparse(m)]
        self.assertEqual(touching, ["deleteWallpaper"])


if __name__ == "__main__":
    unittest.main()
