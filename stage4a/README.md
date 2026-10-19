# Stage 4A: safe check of upstream's UI on KDE (phase 1: offscreen)

Loads upstream's own `ui/qml/Main.qml` with the real `ui.bridge.Controller`, but with a
backend that only records calls. Phase 1 is **offscreen**: no window appears, no keyboard is
grabbed, no display or D-Bus session is reachable. Nothing applies a wallpaper.

    python3 stage4a/probe.py --src "$HOME/Pictures/wallpapers"            # phase 1
    python3 -m unittest discover -s stage4a/tests -t . -v               # no Qt needed
    python3 stage4a/tests/qt_contract.py                                # needs PySide6, offscreen

What keeps it safe (each is checked by the probe or by a test):

| Mechanism | Detail |
|---|---|
| Injected backend | `Controller(backend=RecordingBackend())`; `get_backend()` is never called, `core.backends.wlroots` and `core.seamless` are never imported |
| Exec guard | a Python audit hook REFUSES every attempt to start a program (any path, `sh`, `os.system`, spawn, fork); attempts are reported |
| Isolated environment | HOME, XDG_CONFIG/CACHE/DATA/STATE/RUNTIME, TMPDIR point into a temp directory; PATH is an empty directory; D-Bus points at nothing; display variables removed; no `.pyc`/`.qmlc` written into the repository |
| Copy of the collection | only image files are copied (size-capped); the original folder is only read, and its fingerprint is compared before and after |
| Probe controller | `apply` goes straight to the recording backend (no `_apply_entry`, no colour tools, no `state.json`); `deleteWallpaper`, folder picking/setting and the shader slots are refused. A test derives the list of such methods from `ui/bridge.py` |
| Real-location fingerprints | real `~/.config/wallfliper`, `~/.cache/wallfliper`, the Trash and the source folder are compared before and after |

Offscreen says nothing about how the UI looks or behaves under KWin; that is phase 2, which
needs a separate go-ahead. This revision contains no code that opens a window on your desktop.
