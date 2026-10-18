"""ApplyRunner: one operation at a time, cancellation, bounded shutdown, no late callbacks,
no leaked threads. Uses a scripted fake applier; no processes, no Qt."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kde.applier import ApplyResult, ApplyRunner  # noqa: E402

P = Path("/tmp/example.png")


class FakeApplier:
    """apply() blocks on `gate` (if set), honouring cancellation, then returns `result`."""

    def __init__(self, result: ApplyResult | None = None, gate: threading.Event | None = None):
        self.result = result or ApplyResult(True)
        self.gate = gate
        self.calls: list[Path] = []
        self.entered = threading.Event()
        self.saw_cancel = False
        self.raises: Exception | None = None

    def apply(self, path: Path, cancel: threading.Event) -> ApplyResult:
        self.calls.append(path)
        self.entered.set()
        if self.raises:
            raise self.raises
        if self.gate is not None:
            while not self.gate.wait(0.01):
                if cancel.is_set():
                    self.saw_cancel = True
                    return ApplyResult(False, "cancelled", cancelled=True)
        return self.result


class Collector:
    def __init__(self) -> None:
        self.results: list[tuple[ApplyResult, Path]] = []
        self.threads: list[str] = []
        self.event = threading.Event()

    def __call__(self, result: ApplyResult, path: Path) -> None:
        self.results.append((result, path))
        self.threads.append(threading.current_thread().name)
        self.event.set()


class RunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.before = {t.ident for t in threading.enumerate()}

    def tearDown(self) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if {t.ident for t in threading.enumerate()} <= self.before:
                return
            time.sleep(0.01)
        self.fail(f"leaked threads: {[t.name for t in threading.enumerate() if t.ident not in self.before]}")

    def test_result_is_delivered_off_the_calling_thread(self):
        done = Collector()
        runner = ApplyRunner(FakeApplier(), done)
        self.assertTrue(runner.start(P))
        self.assertTrue(done.event.wait(2))
        self.assertEqual(done.results[0][0], ApplyResult(True))
        self.assertEqual(done.results[0][1], P)
        self.assertNotEqual(done.threads[0], threading.current_thread().name)
        self.assertFalse(runner.busy)

    def test_second_start_while_running_is_refused_and_applier_runs_once(self):
        gate = threading.Event()
        fake = FakeApplier(gate=gate)
        done = Collector()
        runner = ApplyRunner(fake, done)
        self.assertTrue(runner.start(P))
        self.assertTrue(fake.entered.wait(2))
        self.assertTrue(runner.busy)
        for _ in range(5):
            self.assertFalse(runner.start(P))
        gate.set()
        self.assertTrue(done.event.wait(2))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(done.results), 1)

    def test_retry_after_failure_is_allowed(self):
        done = Collector()
        fake = FakeApplier(ApplyResult(False, "boom"))
        runner = ApplyRunner(fake, done)
        self.assertTrue(runner.start(P))
        self.assertTrue(done.event.wait(2))
        done.event.clear()
        fake.result = ApplyResult(True)
        self.assertTrue(runner.start(P), "busy must be cleared before the callback")
        self.assertTrue(done.event.wait(2))
        self.assertEqual([r.ok for r, _ in done.results], [False, True])

    def test_cancel_reaches_the_applier_and_the_result_is_marked_cancelled(self):
        gate = threading.Event()
        fake = FakeApplier(gate=gate)
        done = Collector()
        runner = ApplyRunner(fake, done)
        runner.start(P)
        self.assertTrue(fake.entered.wait(2))
        runner.cancel()
        self.assertTrue(done.event.wait(2))
        self.assertTrue(fake.saw_cancel)
        self.assertTrue(done.results[0][0].cancelled)

    def test_cancel_when_idle_is_harmless(self):
        runner = ApplyRunner(FakeApplier(), Collector())
        runner.cancel()
        self.assertTrue(runner.shutdown())

    def test_shutdown_cancels_joins_and_silences_the_callback(self):
        gate = threading.Event()
        fake = FakeApplier(gate=gate)
        done = Collector()
        runner = ApplyRunner(fake, done)
        runner.start(P)
        self.assertTrue(fake.entered.wait(2))
        t0 = time.monotonic()
        self.assertTrue(runner.shutdown(timeout=3), "worker must be gone")
        self.assertLess(time.monotonic() - t0, 2)
        self.assertTrue(fake.saw_cancel)
        self.assertEqual(done.results, [], "no callback after shutdown")
        self.assertFalse(runner.start(P), "no new work after shutdown")

    def test_shutdown_reports_a_stuck_worker_instead_of_hanging(self):
        release = threading.Event()

        class Stuck:
            def apply(self, path, cancel):
                release.wait(5)  # ignores cancellation on purpose
                return ApplyResult(True)

        runner = ApplyRunner(Stuck(), Collector())
        runner.start(P)
        t0 = time.monotonic()
        self.assertFalse(runner.shutdown(timeout=0.2))
        self.assertLess(time.monotonic() - t0, 1.5)
        release.set()  # let the daemon worker finish so the thread check passes

    def test_an_exception_in_the_applier_becomes_a_failure_result(self):
        fake = FakeApplier()
        fake.raises = RuntimeError("secret internal detail")
        done = Collector()
        runner = ApplyRunner(fake, done)
        runner.start(P)
        self.assertTrue(done.event.wait(2))
        result = done.results[0][0]
        self.assertFalse(result.ok)
        self.assertNotIn("secret", result.message)
        self.assertFalse(runner.busy)

    def test_an_exception_in_the_callback_does_not_wedge_the_runner(self):
        def bad(result, path):
            raise RuntimeError("callback bug")

        runner = ApplyRunner(FakeApplier(), bad)
        runner.start(P)
        deadline = time.monotonic() + 2
        while runner.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(runner.busy)


if __name__ == "__main__":
    unittest.main()
