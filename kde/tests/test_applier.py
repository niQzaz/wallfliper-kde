"""PlasmaApplier / DryRunApplier against a FAKE tool. The real wallpaper tool is never
started: every test points the applier at a script in a temp directory that records what
it was given. Real subprocess machinery (argument passing, exit codes, timeouts,
cancellation) is exercised; the desktop is untouched. No Qt needed.
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kde.applier import DryRunApplier, PlasmaApplier  # noqa: E402

FAKE = f"""#!{sys.executable}
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
if os.environ.get("FAKE_PID"):
    open(os.environ["FAKE_PID"], "w").write(str(os.getpid()))
if os.environ.get("FAKE_OUT"):
    json.dump({{"argv": sys.argv[1:], "LC_ALL": os.environ.get("LC_ALL"),
               "LANGUAGE": os.environ.get("LANGUAGE")}}, open(os.environ["FAKE_OUT"], "w"))
arg = sys.argv[1] if len(sys.argv) > 1 else ""
if mode == "ok":
    print("Successfully set the wallpaper for all desktops to the image", arg)
elif mode == "ok_stderr":
    print("Successfully set the wallpaper for all desktops to the image", arg, file=sys.stderr)
elif mode == "usage_rc0":
    print("Usage: plasma-apply-wallpaperimage [options] imagefile\\nOptions:\\n  -h, --help")
elif mode == "error_rc0":
    print("An error occurred while attempting to set the Plasma wallpaper:\\nNot connected to D-Bus server")
elif mode == "silent_rc0":
    pass
elif mode == "rc2":
    print("something broke"); sys.exit(2)
elif mode == "crash":
    os.kill(os.getpid(), 9)
elif mode == "noisy":
    print("Successfully set the wallpaper \\x1b[31m" + "x" * 500 + "\\nerror")
elif mode == "sleep":
    time.sleep(30)
"""

NASTY_NAMES = [
    "plain.png",
    "with space.png",
    "Закат над морем é.png",
    "it's \"quoted\".png",
    "$(touch PWNED).png",
    "`touch PWNED2`.png",
    "; touch PWNED3 ;.png",
    "-rf.png",
    "--help.png",
    "100% #1 & more.png",
]


class FakeToolCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.tool = self.dir / "fake-tool"
        self.tool.write_text(FAKE)
        self.tool.chmod(self.tool.stat().st_mode | stat.S_IXUSR)
        self.out = self.dir / "record.json"
        self.pidfile = self.dir / "pid"
        self.cancel = threading.Event()
        self.mode("ok")

    def mode(self, name: str) -> None:
        patcher = mock.patch.dict(os.environ, {"FAKE_MODE": name, "FAKE_OUT": str(self.out),
                                               "FAKE_PID": str(self.pidfile), "LANGUAGE": "ru"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def image(self, name: str = "a.png") -> Path:
        p = self.dir / "imgs" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x")
        return p

    def applier(self, **kw) -> PlasmaApplier:
        return PlasmaApplier(tool=str(self.tool), **kw)

    def recorded(self) -> dict:
        return json.loads(self.out.read_text())


class Success(FakeToolCase):
    def test_success_passes_the_path_as_one_argument(self):
        img = self.image("my wall.png")
        r = self.applier().apply(img, self.cancel)
        self.assertTrue(r.ok, r)
        self.assertEqual(self.recorded()["argv"], [str(img)])

    def test_success_text_on_stderr_counts_too(self):
        self.mode("ok_stderr")
        self.assertTrue(self.applier().apply(self.image(), self.cancel).ok)

    def test_tricky_names_arrive_untouched_and_nothing_is_executed(self):
        for name in NASTY_NAMES:
            with self.subTest(name=name):
                img = self.image(name)
                r = self.applier().apply(img, self.cancel)
                self.assertTrue(r.ok, f"{name}: {r}")
                self.assertEqual(self.recorded()["argv"], [str(img)])
        for marker in ("PWNED", "PWNED2", "PWNED3"):
            self.assertFalse((Path.cwd() / marker).exists(), marker)
            self.assertFalse((self.dir / marker).exists(), marker)
            self.assertFalse((self.dir / "imgs" / marker).exists(), marker)

    def test_locale_is_forced_so_messages_are_predictable(self):
        self.applier().apply(self.image(), self.cancel)
        rec = self.recorded()
        self.assertEqual(rec["LC_ALL"], "C.UTF-8")
        self.assertIsNone(rec["LANGUAGE"])

    def test_a_file_named_like_an_error_is_still_a_success(self):
        img = self.image("error failed usage: 404.png")
        self.assertTrue(self.applier().apply(img, self.cancel).ok)


class Failures(FakeToolCase):
    def test_tool_not_found(self):
        r = PlasmaApplier(tool="definitely-not-installed-xyz").apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("not found", r.message)

    def test_tool_that_cannot_be_started(self):
        broken = self.dir / "broken"
        broken.write_text("this is not a program\n")
        broken.chmod(0o755)  # executable, but no interpreter line: exec fails
        r = PlasmaApplier(tool=str(broken)).apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("cannot start", r.message)

    def test_nonzero_exit(self):
        self.mode("rc2")
        r = self.applier().apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("exit 2", r.message)

    def test_killed_by_signal(self):
        self.mode("crash")
        r = self.applier().apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("signal 9", r.message)

    def test_exit_zero_with_usage_text_is_not_success(self):
        self.mode("usage_rc0")  # the observed behaviour of the real tool with no argument
        self.assertFalse(self.applier().apply(self.image(), self.cancel).ok)

    def test_exit_zero_with_an_error_message_is_not_success(self):
        self.mode("error_rc0")
        r = self.applier().apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("error", r.message.lower())

    def test_exit_zero_without_confirmation_is_not_success(self):
        self.mode("silent_rc0")
        r = self.applier().apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("no confirmation", r.message)

    def test_messages_are_one_short_clean_line(self):
        self.mode("noisy")
        r = self.applier().apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertLessEqual(len(r.message), 64)
        self.assertNotIn("\n", r.message)
        self.assertNotIn("\x1b", r.message)

    def test_missing_file_is_refused_without_starting_the_tool(self):
        for bad in (self.dir / "nope.png", self.dir / "imgs", Path("relative.png")):
            with self.subTest(path=str(bad)):
                self.out.unlink(missing_ok=True)
                r = self.applier().apply(bad, self.cancel)
                self.assertFalse(r.ok)
                self.assertFalse(self.out.exists(), "the tool must not be started")

    def test_embedded_nul_in_path_is_refused(self):
        r = self.applier().apply(Path(str(self.dir / "a\0b.png")), self.cancel)
        self.assertFalse(r.ok)
        self.assertFalse(self.out.exists())


class TimeoutAndCancel(FakeToolCase):
    def assert_child_gone(self):
        pid = int(self.pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"child {pid} still alive")

    def test_timeout_stops_the_child(self):
        self.mode("sleep")
        t0 = time.monotonic()
        r = self.applier(timeout=0.4).apply(self.image(), self.cancel)
        self.assertFalse(r.ok)
        self.assertIn("did not respond", r.message)
        self.assertLess(time.monotonic() - t0, 5)
        self.assert_child_gone()

    def test_cancel_stops_the_child_quickly_and_is_not_an_error(self):
        self.mode("sleep")
        threading.Timer(0.3, self.cancel.set).start()
        t0 = time.monotonic()
        r = self.applier(timeout=20).apply(self.image(), self.cancel)
        self.assertTrue(r.cancelled)
        self.assertFalse(r.ok)
        self.assertLess(time.monotonic() - t0, 5)
        self.assert_child_gone()

    def test_already_cancelled_never_starts_the_tool(self):
        self.cancel.set()
        r = self.applier().apply(self.image(), self.cancel)
        self.assertTrue(r.cancelled)
        self.assertFalse(self.out.exists())


class DryRun(FakeToolCase):
    def test_prints_the_path_and_never_starts_any_program(self):
        # Put a recording fake on PATH under the real tool's name; dry-run must not run it.
        bindir = self.dir / "bin"
        bindir.mkdir()
        fake_on_path = bindir / "plasma-apply-wallpaperimage"
        fake_on_path.write_text(FAKE)
        fake_on_path.chmod(0o755)
        img = self.image("dry run é.png")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}), \
                redirect_stdout(buf):
            r = DryRunApplier().apply(img, self.cancel)
        self.assertTrue(r.ok)
        self.assertEqual(buf.getvalue(), f"{img}\n")
        self.assertFalse(self.out.exists(), "dry-run started a program")


if __name__ == "__main__":
    unittest.main()
