# Wallfliper KDE — stage 1

A minimal keyboard-driven wallpaper picker on the Stage-0-verified layer-shell window.
Stage 1 does **not** change the desktop wallpaper: Enter prints the chosen path and exits.

    python3 kde/main.py [--dir PATH] [--verbose] [--timeout SECONDS]

Keys: Left/Right move · Enter confirm · Esc close · click outside the panel closes · double-click a card confirms.
Run it again while open to close it (toggle). Videos are not shown in this stage.

Folder: `--dir`, else `wallpaper_dir` in `~/.config/wallfliper/config.json` (shared with upstream),
else `~/Pictures/Wallpapers`, else `~/Pictures`.

Checks:

    python3 -m unittest discover -s kde/tests -t . -v        # no Qt needed
    QT_QPA_PLATFORM=offscreen python3 kde/tests/smoke_qt.py   # needs PySide6, headless
