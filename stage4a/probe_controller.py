"""Upstream's Controller with every side-effecting entry point cut off, for the probe.

Importing this module imports `ui.bridge` (and so PySide6): the probe does it only
after the environment is isolated. Which methods must be overridden is not a guess:
a static test derives them from `ui/bridge.py` and fails if one is missing here.

What the probe still exercises: the models, the filter proxy, thumbnail loading and
the QML views. What it replaces:

  apply / _apply_entry     -> a direct call on the injected backend. The real path
                              (_apply_entry) also writes state.json and runs the colour
                              notification, which starts `sh`; none of that is wanted.
  shader* slots            -> no-ops (the shader surface is never requested)
  deleteWallpaper          -> refused (the real one moves the file to the trash)
  folder picking / setting -> refused (portal dialog, config changes)
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Slot

from ui.bridge import Controller


class ProbeController(Controller):
    def __init__(self, backend, sink: Callable[[str, str], None], parent: QObject | None = None) -> None:
        super().__init__(parent, backend=backend)
        self._sink = sink
        self.blocked: list[str] = []

    def _refuse(self, what: str) -> None:
        self.blocked.append(what)
        self._sink("probe-blocked", what)

    # -- application: straight to the injected backend, nothing else -------------

    @Slot(int, result=bool)
    def apply(self, proxy_row: int) -> bool:
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        entry = self._model.entry_at(source)
        if entry is None:
            self._sink("probe-apply", f"row {proxy_row}: no entry")
            return False
        if entry.kind == "video":
            self._backend.set_video(entry.path, None)
        else:
            self._backend.set_image(entry.path, None)
        self._sink("probe-apply", f"row {proxy_row} -> {entry.path}")
        return False  # no shader surface is in flight: the caller may quit

    def _apply_entry(self, entry, transition) -> None:
        self._refuse("_apply_entry")

    @Slot()
    def shaderSurfaceReady(self) -> None:
        self._refuse("shaderSurfaceReady")

    @Slot()
    def shaderTransitionFailed(self) -> None:
        self._refuse("shaderTransitionFailed")

    # -- destructive or environment-touching features ---------------------------

    @Slot(int)
    def deleteWallpaper(self, proxy_row: int) -> None:
        self._refuse(f"deleteWallpaper({proxy_row})")

    @Slot(result=bool)
    def folderPortalAvailable(self) -> bool:
        return False

    @Slot()
    def pickFolder(self) -> None:
        self._refuse("pickFolder")
        self.folderPickerClosed.emit()  # the QML waits for this to show itself again

    def _on_folder_picked(self, path: str) -> None:
        self._refuse(f"folder picked: {path}")
        self.folderPickerClosed.emit()

    @Slot(str, result=str)
    def setFolderFromText(self, text: str) -> str:
        self._refuse("setFolderFromText")
        return "disabled in the probe"

    @Slot(str)
    def setFolder(self, folder: str) -> None:
        self._refuse(f"setFolder({folder})")
