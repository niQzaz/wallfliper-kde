# Wallfliper KDE

A minimal keyboard-driven wallpaper picker for KDE Plasma 6 on Wayland.

    python3 kde/main.py [--dir PATH] [--dry-run] [--verbose] [--timeout SECONDS]

Keys: Left/Right move · **Enter applies** the selected image as the desktop wallpaper and closes ·
Esc closes without applying · click outside the panel closes · double-click a card applies it.
If applying fails the panel shows why and stays open, so you can try again.
Run it again while it is open to close it (toggle). Videos are not shown yet.

Applying uses Plasma's own tool, `plasma-apply-wallpaperimage` (package `plasma-workspace`),
started with an argument list, no shell, and a time limit. The tool applies the image to all screens at once; per-screen control is not implemented.
`--dry-run` prints the chosen path instead and never starts any program.
Add `--verbose` to see the tool's raw output (useful if a message looks wrong).

Folder: `--dir`, else `wallpaper_dir` in `~/.config/wallfliper/config.json` (shared with upstream),
else `~/Pictures/Wallpapers`, else `~/Pictures`.

Checks:

    python3 -m unittest discover -s kde/tests -t . -v        # no Qt needed; never touches the wallpaper
    QT_QPA_PLATFORM=offscreen python3 kde/tests/smoke_qt.py   # needs PySide6, headless, fake applier
