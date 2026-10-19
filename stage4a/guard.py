"""Safety rails for the Stage 4A probe. Standard library only.

  - ExecGuard: a process-wide audit hook that REFUSES (not just logs) every attempt to
    start another program from Python, whatever the path or PATH. It covers absolute
    paths and every route Python has for it (subprocess, os.system, os.exec*, spawn,
    posix_spawn, fork, pty). Qt-native process APIs are not covered by it; the static
    tests check that the reachable code does not use them.
  - isolated_env: where every config, cache, data and temp location points during a run.
  - snapshot: read-only fingerprint of a file or tree, to prove nothing outside the
    temp area was touched.
  - copy_images: copies only image files, with a size cap, into the temp area.
"""

from __future__ import annotations

import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Iterable, Mapping


class ProbeError(RuntimeError):
    """A precondition of the probe is not met; nothing has been run."""


class ExternalCommandBlocked(RuntimeError):
    """Raised inside the code that tried to start another program."""


BLOCKED_EVENTS = frozenset({
    "subprocess.Popen", "_posixsubprocess.fork_exec", "os.system", "os.exec",
    "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "pty.spawn",
})

_armed = False
_installed = False
_blocked: list[dict[str, str]] = []


def _audit(event: str, args: tuple) -> None:
    if not _armed or event not in BLOCKED_EVENTS:
        return
    frames = traceback.extract_stack(limit=8)[:-1]
    where = " <- ".join(f"{Path(f.filename).name}:{f.lineno}" for f in reversed(frames))
    _blocked.append({"event": event, "args": repr(args)[:200], "where": where})
    raise ExternalCommandBlocked(f"blocked {event}: {repr(args)[:120]}")


def install_exec_guard() -> None:
    """Install the hook once. Audit hooks cannot be removed; arm()/disarm() switch it."""
    global _installed
    if not _installed:
        sys.addaudithook(_audit)
        _installed = True


def arm() -> None:
    global _armed
    install_exec_guard()
    _armed = True


def disarm() -> None:
    global _armed
    _armed = False


def blocked_attempts() -> list[dict[str, str]]:
    return list(_blocked)


def reset_blocked() -> None:
    _blocked.clear()


def inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def isolated_env(root: Path, base: Mapping[str, str]) -> dict[str, str]:
    """Environment for the probe: everything writable points under `root`.

    HOME is redirected too, so any code that falls back to ~/.config, ~/.cache or
    ~/.local lands in the temp area. PATH is an empty directory, so a lookup of any
    tool finds nothing. The D-Bus session address points at a socket that does not
    exist, and no display variables are kept.
    """
    env = dict(base)
    for key in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "QT_WAYLAND_SHELL_INTEGRATION",
                "QT_QPA_PLATFORMTHEME", "XDG_SESSION_TYPE", "LANGUAGE"):
        env.pop(key, None)
    sub = lambda name: str(root / name)  # noqa: E731
    env.update({
        "HOME": sub("home"),
        "XDG_CONFIG_HOME": sub("config"),
        "XDG_CACHE_HOME": sub("cache"),
        "XDG_DATA_HOME": sub("data"),
        "XDG_STATE_HOME": sub("state"),
        "XDG_RUNTIME_DIR": sub("runtime"),
        "TMPDIR": sub("tmp"),
        "PATH": sub("empty-bin"),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={root}/no-session-bus",
        "DBUS_SYSTEM_BUS_ADDRESS": f"unix:path={root}/no-system-bus",
        "QT_QPA_PLATFORM": "offscreen",
        "QML_DISABLE_DISK_CACHE": "1",     # no .qmlc files written next to the QML sources
        "PYTHONDONTWRITEBYTECODE": "1",    # no __pycache__ written into the repository
    })
    return env


def make_dirs(root: Path) -> None:
    for name in ("home", "config", "cache", "data", "state", "tmp", "empty-bin", "walls"):
        (root / name).mkdir(parents=True, exist_ok=True)
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)  # Qt warns about a runtime dir with looser permissions


def snapshot(path: Path) -> tuple:
    """(exists, files, total_bytes, newest_mtime_ns) of a file or tree. Read-only."""
    if not path.exists():
        return (False, 0, 0, 0)
    if path.is_file():
        st = path.stat()
        return (True, 1, st.st_size, st.st_mtime_ns)
    files = total = newest = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for name in [*dirnames, *filenames]:
            st = os.lstat(os.path.join(dirpath, name))
            files += 1
            total += st.st_size
            newest = max(newest, st.st_mtime_ns)
    return (True, files, total, max(newest, path.stat().st_mtime_ns))


def copy_images(src: Path, dst: Path, extensions: Iterable[str], limit_bytes: int) -> list[Path]:
    """Copy the image files directly inside `src` (not subfolders) into `dst`."""
    # Upstream lists extensions without the dot ("jpg"); accept either form.
    exts = {e.lower().lstrip(".") for e in extensions}
    if not src.is_dir():
        raise ProbeError(f"source folder not found: {src}")
    picked = [p for p in sorted(src.iterdir())
              if p.is_file() and not p.name.startswith(".") and p.suffix.lower().lstrip(".") in exts]
    total = sum(p.stat().st_size for p in picked)
    if total > limit_bytes:
        raise ProbeError(f"{len(picked)} images, {total / 1e6:.0f} MB: over the "
                         f"{limit_bytes / 1e6:.0f} MB limit (raise --max-mb deliberately)")
    dst.mkdir(parents=True, exist_ok=True)
    copies = []
    for p in picked:
        target = dst / p.name
        shutil.copyfile(p, target)
        copies.append(target)
    return copies
