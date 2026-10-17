#!/usr/bin/env bash
# Stage 0: read-only environment report. Changes nothing, needs no sudo.
# Optional: --with-dnf also asks dnf what is installed/available (may fetch repo metadata).
set -u
sec() { printf '\n== %s ==\n' "$1"; }

sec "session"
for v in XDG_SESSION_TYPE XDG_CURRENT_DESKTOP XDG_SESSION_DESKTOP WAYLAND_DISPLAY \
         QT_QPA_PLATFORM QT_WAYLAND_SHELL_INTEGRATION; do
  printf '%-30s %s\n' "$v" "${!v:-<unset>}"
done

sec "versions (rpm -q, read-only)"
rpm -q fedora-release-common kwin plasma-workspace 2>&1
command -v plasmashell >/dev/null && plasmashell --version 2>&1 | head -1

sec "packages Stage 0 needs"
rpm -q python3-pyside6 layer-shell-qt wayland-utils 2>&1
sec "packages needed later (not for Stage 0)"
rpm -q xdg-desktop-portal-kde ffmpeg-free ffmpeg 2>&1

sec "python used by 'python3'"
command -v python3; python3 --version
python3 - <<'PY'
try:
    import PySide6
    from PySide6.QtCore import QLibraryInfo, qVersion
    import os
    print("PySide6", PySide6.__version__, "| Qt", qVersion(), "| from", os.path.dirname(PySide6.__file__))
    qml = QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath)
    print("Qt QML import path:", qml)
    print("org.kde.layershell on that path:",
          os.path.exists(os.path.join(qml, "org", "kde", "layershell", "qmldir")))
except Exception as e:
    print("PySide6 not importable:", e)
PY
for d in /usr/lib64/qt6/qml /usr/lib/qt6/qml; do
  [ -f "$d/org/kde/layershell/qmldir" ] && echo "layershell qmldir found: $d"
done

sec "compositor protocols (needs wayland-utils)"
if command -v wayland-info >/dev/null; then
  wayland-info 2>/dev/null | grep -E "zwlr_layer_shell_v1|org_kde_kwin_blur_manager|ext_background_effect|xdg_wm_base|org_kde_plasma_shell" \
    || echo "(no matching globals listed)"
else
  echo "wayland-info not installed (package wayland-utils) - skipped"
fi

if [ "${1:-}" = "--with-dnf" ]; then
  sec "dnf (non-root)"
  dnf list python3-pyside6 layer-shell-qt wayland-utils 2>&1 | tail -n +1
fi
echo
