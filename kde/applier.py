"""Applying a chosen image as the wallpaper.

Everything here is Qt-free, so it can be tested anywhere. This is the only module in
`kde/` that starts another program.

  PlasmaApplier   runs Plasma's own wallpaper tool (from plasma-workspace) with an
                  argument list and no shell. The result is never taken from the exit
                  code alone: the tool is known to exit 0 even when it only printed
                  usage text, so success needs a positive confirmation in its output.
  DryRunApplier   prints the path and does nothing else. It cannot start any program.
  ApplyRunner     runs one apply at a time on a worker thread, with cancellation and a
                  bounded shutdown, so the UI never waits on a child process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

TOOL = "plasma-apply-wallpaperimage"
DEFAULT_TIMEOUT = 8.0  # seconds; the tool normally answers in well under one
_TICK = 0.05           # how often a waiting worker checks for cancellation
_MESSAGE_LIMIT = 64    # longest message shown in the panel header

# What the tool prints (observed on Plasma 6: "Successfully set the wallpaper for all
# desktops to the image ..."). The tool is run with an untranslated locale so these
# English markers apply. If a Plasma version words it differently, every apply is
# reported as "no confirmation" rather than falsely as a success; run with --verbose
# to see the raw output.
_SUCCESS = re.compile(r"successfully set the wallpaper", re.IGNORECASE)
_FAILURE = re.compile(
    r"\berror\b|\bfailed\b|\bcould not\b|\bcannot\b|\bunable\b|\bnot connected\b|^\s*usage:",
    re.IGNORECASE | re.MULTILINE,
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    message: str = ""        # short, safe to show in the panel
    cancelled: bool = False  # the caller asked to stop; not an error
    output: str = ""         # raw tool output (trimmed), for --verbose logs only


class WallpaperApplier(Protocol):
    def apply(self, path: Path, cancel: threading.Event) -> ApplyResult: ...


def _one_line(text: str, limit: int = _MESSAGE_LIMIT) -> str:
    text = " ".join(_CONTROL.sub(" ", text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _failure(message: str, output: str = "") -> ApplyResult:
    return ApplyResult(False, _one_line(message), output=output[:2000])


class DryRunApplier:
    """Confirms the choice by printing its path. Starts nothing."""

    def apply(self, path: Path, cancel: threading.Event) -> ApplyResult:
        try:
            sys.stdout.write(f"{path}\n")
            sys.stdout.flush()
        except BrokenPipeError:
            pass
        return ApplyResult(True)


class PlasmaApplier:
    def __init__(self, tool: str = TOOL, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._tool = tool
        self._timeout = timeout

    def apply(self, path: Path, cancel: threading.Event) -> ApplyResult:
        # 1. Arguments are checked before anything is started.
        if cancel.is_set():
            return ApplyResult(False, "cancelled", cancelled=True)
        try:
            usable = path.is_absolute() and path.is_file()
        except (OSError, ValueError):  # e.g. an embedded NUL in the path
            usable = False
        if not usable:
            return _failure("file no longer exists")
        executable = shutil.which(self._tool)
        if executable is None:
            return _failure(f"{Path(self._tool).name} not found")

        # 2. One argument vector, no shell. The path is a single, absolute argument,
        #    so it can never be read as an option or split by spaces or quotes.
        #    An untranslated locale makes the tool's messages predictable; UTF-8 keeps
        #    non-ASCII file names intact.
        env = dict(os.environ)
        env["LC_ALL"] = "C.UTF-8"
        env.pop("LANGUAGE", None)
        try:
            proc = subprocess.Popen(
                [executable, str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                close_fds=True,
                shell=False,
            )
        except (OSError, ValueError):
            return _failure(f"cannot start {Path(self._tool).name}")

        # 3. Wait for it, stoppable and bounded.
        deadline = time.monotonic() + self._timeout
        raw = b""
        while True:
            if cancel.is_set():
                self._stop(proc)
                return ApplyResult(False, "cancelled", cancelled=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop(proc)
                return _failure(f"Plasma did not respond in {self._timeout:g} s")
            try:
                raw, _ = proc.communicate(timeout=min(_TICK, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        return self._interpret(proc.returncode, raw, path)

    @staticmethod
    def _stop(proc: "subprocess.Popen[bytes]") -> None:
        """Terminate, then kill, then reap, so no child or zombie is left behind."""
        try:
            proc.terminate()
            proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        except OSError:
            pass

    @staticmethod
    def _interpret(returncode: int, raw: bytes, path: Path) -> ApplyResult:
        output = raw.decode("utf-8", errors="replace")
        # The success line echoes the file's path; remove it (and the bare name) so a
        # file called "error 404.png" cannot look like an error message.
        cleaned = output
        for needle in (path.as_uri(), str(path), path.name):
            if needle:
                cleaned = cleaned.replace(needle, "")
        lines = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())

        if returncode < 0:
            return _failure(f"tool crashed (signal {-returncode})", output)
        if returncode != 0:
            return _failure(f"failed (exit {returncode}){': ' + lines if lines else ''}", output)
        if _FAILURE.search(cleaned):  # exit 0 is not proof of success
            return _failure(lines or "tool reported an error", output)
        if _SUCCESS.search(cleaned):
            return ApplyResult(True, output=output[:2000])
        return _failure("no confirmation from Plasma", output)


class ApplyRunner:
    """Runs one apply at a time on a worker thread.

    `on_done(result, path)` is called on the worker thread, never after `shutdown()`.
    A new apply is refused while one is running; after a failure a retry is fine.
    """

    def __init__(self, applier: WallpaperApplier,
                 on_done: Callable[[ApplyResult, Path], None]) -> None:
        self._applier = applier
        self._on_done = on_done
        self._lock = threading.Lock()
        self._busy = False
        self._closed = False
        self._cancel: threading.Event | None = None
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def start(self, path: Path) -> bool:
        with self._lock:
            if self._closed or self._busy:
                return False
            token = threading.Event()
            thread = threading.Thread(target=self._run, args=(path, token),
                                      name="wallfliper-apply", daemon=True)
            self._busy, self._cancel, self._thread = True, token, thread
        thread.start()
        return True

    def cancel(self) -> None:
        """Ask the running apply (if any) to stop. Returns immediately."""
        with self._lock:
            token = self._cancel if self._busy else None
        if token is not None:
            token.set()

    def shutdown(self, timeout: float = 3.0) -> bool:
        """Cancel, silence callbacks, and wait (bounded) for the worker to end.
        True if no worker is left running."""
        with self._lock:
            self._closed = True
            token, thread = self._cancel, self._thread
        if token is not None:
            token.set()
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _run(self, path: Path, token: threading.Event) -> None:
        try:
            result = self._applier.apply(path, token)
        except Exception as exc:  # noqa: BLE001 - a worker must never die silently
            result = _failure(f"unexpected error ({type(exc).__name__})")
        with self._lock:
            self._busy = False  # cleared before the callback, so a retry is possible at once
            deliver = not self._closed
        if deliver:
            try:
                self._on_done(result, path)
            except Exception:  # noqa: BLE001
                pass
