"""A wallpaper backend that only records what it was asked to do.

It imports nothing from `core.backends` (not even the interface) and starts no
process, so it cannot reach swww, mpvpaper or any other real tool. It matches the
interface by shape: the same method names and parameters as
`core.backends.base.WallpaperBackend`, which a test checks against that file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    transition: object | None = None


class RecordingBackend:
    def __init__(self, sink: Callable[[str, str], None] | None = None) -> None:
        self.calls: list[Call] = []
        self._sink = sink or (lambda event, detail: None)

    def set_image(self, path: Path, transition: object | None = None) -> None:
        self._record("set_image", path, transition)

    def set_video(self, path: Path, transition: object | None = None) -> None:
        self._record("set_video", path, transition)

    def is_available(self) -> bool:
        return True

    def _record(self, method: str, path: Path, transition: object | None) -> None:
        self.calls.append(Call(method, str(path), transition))
        self._sink("fake-backend", f"{method} {path}")
