"""Wallpaper collection for the KDE picker: which folder, which images, what to say
when there is nothing to show. Pure Python (no Qt) so it can be tested anywhere.

Reuses upstream `core.library.scan` and `core.state.load_config` unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.library import scan
from core.state import config_dir, load_config


@dataclass(frozen=True)
class Collection:
    # One dict per image, shaped for QML: {"name", "path", "url"}.
    entries: list[dict] = field(default_factory=list)
    directory: Path | None = None
    detail: str = ""  # why the picker is empty ("" when there are images)
    hint: str = ""    # what the user can do about it


def _tilde(path: Path) -> str:
    """Show paths relative to $HOME so the UI never prints a full home path."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _config_hint() -> str:
    return f"set wallpaper_dir in {_tilde(config_dir() / 'config.json')} or run with --dir PATH"


def load_collection(override: str | None = None) -> Collection:
    """Resolve the folder (CLI override > config.json > upstream's default guess) and scan it."""
    if override:
        directory = Path(override).expanduser()
    else:
        configured = load_config().wallpaper_path
        directory = configured.expanduser() if configured else None

    if directory is None:
        return Collection(detail="no wallpaper folder is configured", hint=_config_hint())
    directory = directory.absolute()

    if not directory.exists():
        return Collection(directory=directory, detail=f"folder not found: {_tilde(directory)}",
                          hint=_config_hint())
    if not directory.is_dir():
        return Collection(directory=directory, detail=f"not a folder: {_tilde(directory)}",
                          hint=_config_hint())
    if not os.access(directory, os.R_OK | os.X_OK):
        return Collection(directory=directory, detail=f"cannot read folder: {_tilde(directory)}",
                          hint="check the folder's permissions")
    try:
        found = scan(directory)
    except OSError as exc:
        return Collection(directory=directory, detail=f"cannot read folder: {_tilde(directory)}",
                          hint=exc.strerror or "check the folder's permissions")

    images = [e for e in found if e.kind == "image"]
    videos = len(found) - len(images)
    if not images:
        detail = f"no supported images in {_tilde(directory)}"
        if videos:
            detail += f" ({videos} video file{'s' if videos != 1 else ''} not shown in this version)"
        return Collection(directory=directory, detail=detail,
                          hint="add images there, or pick another folder with --dir PATH")

    entries = [{"name": e.name, "path": str(e.path.absolute()), "url": e.path.absolute().as_uri()}
               for e in images]
    return Collection(entries=entries, directory=directory)


def resolve_selection(entries: list[dict], index: int) -> tuple[Path | None, str]:
    """Validate a confirmed selection. Returns (path, "") or (None, user-facing message)."""
    if not 0 <= index < len(entries):
        return None, "nothing selected"
    path = Path(entries[index]["path"])
    if not path.is_file():
        return None, "file no longer exists"
    return path, ""
