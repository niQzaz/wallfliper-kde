"""Exec guard, environment isolation, snapshots and image copying. No Qt."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage4a import guard  # noqa: E402
from core.library import IMAGE_EXTENSIONS  # noqa: E402


class ExecGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        guard.reset_blocked()
        guard.arm()
        self.addCleanup(guard.disarm)  # the audit hook itself cannot be removed
        self.addCleanup(guard.reset_blocked)

    def test_subprocess_is_refused_by_name_and_by_absolute_path(self):
        marker = self.dir / "ran"
        for argv in (["touch", str(marker)], ["/usr/bin/touch", str(marker)], ["/bin/sh", "-c", f"touch {marker}"]):
            with self.subTest(argv=argv), self.assertRaises(guard.ExternalCommandBlocked):
                subprocess.run(argv)
        self.assertFalse(marker.exists())
        self.assertGreaterEqual(len(guard.blocked_attempts()), 3)

    def test_popen_and_shell_variants_are_refused(self):
        marker = self.dir / "ran2"
        with self.assertRaises(guard.ExternalCommandBlocked):
            subprocess.Popen(f"touch {marker}", shell=True)
        with self.assertRaises(guard.ExternalCommandBlocked):
            os.system(f"touch {marker}")
        self.assertFalse(marker.exists())

    def test_posix_spawn_is_refused(self):
        marker = self.dir / "ran3"
        with self.assertRaises(guard.ExternalCommandBlocked):
            os.posix_spawn("/bin/sh", ["sh", "-c", f"touch {marker}"], dict(os.environ))
        self.assertFalse(marker.exists())

    def test_every_route_python_has_is_in_the_blocked_set(self):
        for event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
                      "os.fork", "os.forkpty", "pty.spawn", "_posixsubprocess.fork_exec"):
            self.assertIn(event, guard.BLOCKED_EVENTS)

    def test_attempts_are_recorded_with_where_they_came_from(self):
        with self.assertRaises(guard.ExternalCommandBlocked):
            subprocess.run(["definitely-anything"])
        rec = guard.blocked_attempts()[0]
        self.assertEqual(rec["event"], "subprocess.Popen")
        self.assertIn("test_guard.py", rec["where"])

    def test_disarmed_guard_lets_ordinary_code_run(self):
        guard.disarm()
        self.assertEqual(subprocess.run([sys.executable, "-c", "pass"]).returncode, 0)


class IsolatedEnv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.env = guard.isolated_env(self.root, {
            "HOME": "/home/someone", "XDG_CONFIG_HOME": "/real/config", "PATH": "/usr/bin:/bin",
            "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "QT_QPA_PLATFORMTHEME": "kde",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "LANG": "ru_RU.UTF-8"})

    def test_every_writable_location_is_inside_the_temp_root(self):
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                    "XDG_RUNTIME_DIR", "TMPDIR"):
            self.assertTrue(guard.inside(self.root, Path(self.env[key])), f"{key}={self.env[key]}")

    def test_path_is_an_empty_temp_directory_only(self):
        self.assertEqual(self.env["PATH"], str(self.root / "empty-bin"))

    def test_no_display_or_real_bus_survives(self):
        for key in ("WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORMTHEME"):
            self.assertNotIn(key, self.env)
        self.assertTrue(self.env["DBUS_SESSION_BUS_ADDRESS"].startswith(f"unix:path={self.root}"))
        self.assertEqual(self.env["QT_QPA_PLATFORM"], "offscreen")

    def test_no_bytecode_or_qml_cache_writes_into_the_repository(self):
        self.assertEqual(self.env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(self.env["QML_DISABLE_DISK_CACHE"], "1")

    def test_unrelated_variables_are_kept(self):
        self.assertEqual(self.env["LANG"], "ru_RU.UTF-8")

    def test_make_dirs_creates_a_private_runtime_dir(self):
        guard.make_dirs(self.root)
        self.assertEqual((self.root / "runtime").stat().st_mode & 0o777, 0o700)
        self.assertEqual(list((self.root / "empty-bin").iterdir()), [])


class Snapshot(unittest.TestCase):
    def test_detects_create_change_and_delete(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "watched"
            self.assertEqual(guard.snapshot(d), (False, 0, 0, 0))
            d.mkdir()
            s0 = guard.snapshot(d)
            (d / "a").write_text("x")
            s1 = guard.snapshot(d)
            self.assertNotEqual(s0, s1)
            (d / "a").write_text("xyz")
            s2 = guard.snapshot(d)
            self.assertNotEqual(s1, s2)
            (d / "a").unlink()
            self.assertNotEqual(s2, guard.snapshot(d))

    def test_unchanged_tree_has_a_stable_fingerprint(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a").write_text("x")
            self.assertEqual(guard.snapshot(Path(t)), guard.snapshot(Path(t)))


class CopyImages(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "src"
        self.dst = Path(self._tmp.name) / "dst"
        self.src.mkdir()

    def put(self, name, size=10):
        (self.src / name).write_bytes(b"x" * size)

    def test_copies_only_images_using_upstreams_dotless_extensions(self):
        for n in ("a.jpg", "B.PNG", "c.webp", "d.mp4", "e.txt", ".hidden.png"):
            self.put(n)
        (self.src / "sub").mkdir()
        (self.src / "sub" / "nested.png").write_bytes(b"x")
        got = guard.copy_images(self.src, self.dst, IMAGE_EXTENSIONS, 10**6)
        self.assertEqual(sorted(p.name for p in got), ["B.PNG", "a.jpg", "c.webp"])
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["B.PNG", "a.jpg", "c.webp"])

    def test_names_with_spaces_and_unicode(self):
        for n in ("my wall é.jpg", "Закат.png"):
            self.put(n)
        self.assertEqual(len(guard.copy_images(self.src, self.dst, IMAGE_EXTENSIONS, 10**6)), 2)

    def test_the_source_is_not_modified(self):
        self.put("a.jpg")
        before = guard.snapshot(self.src)
        guard.copy_images(self.src, self.dst, IMAGE_EXTENSIONS, 10**6)
        self.assertEqual(before, guard.snapshot(self.src))

    def test_size_limit_refuses_before_copying_anything(self):
        self.put("a.jpg", 600); self.put("b.jpg", 600)
        with self.assertRaises(guard.ProbeError):
            guard.copy_images(self.src, self.dst, IMAGE_EXTENSIONS, 1000)
        self.assertFalse(self.dst.exists())

    def test_missing_source_is_refused(self):
        with self.assertRaises(guard.ProbeError):
            guard.copy_images(self.src / "nope", self.dst, IMAGE_EXTENSIONS, 10**6)


if __name__ == "__main__":
    unittest.main()
