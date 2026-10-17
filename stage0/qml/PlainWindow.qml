import QtQuick
import QtQuick.Window

// Ordinary xdg-toplevel, no layer-shell. Shown by proto.py (show / showFullScreen)
// after it has asked for a screen. StaysOnTop is only a hint; whether KWin
// honours it for a plain toplevel is one of the things under test.
Window {
    id: win
    property bool firstFrame: true
    visible: false
    width: Screen.width
    height: Screen.height
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint

    Component.onCompleted: probe.log("completed", "plain window created (not yet shown)")

    Connections {
        target: win
        function onActiveChanged() {
            probe.log("window-active", String(win.active))
            if (win.active) scene.forceActiveFocus()
        }
        function onVisibleChanged() { probe.log("window-visible", String(win.visible)) }
        function onVisibilityChanged() { probe.log("window-visibility", String(win.visibility)) }
        function onScreenChanged() { probe.log("window-screen", win.screen ? win.screen.name : "null") }
        function onWidthChanged() { probe.log("window-size", win.width + "x" + win.height) }
        function onFrameSwapped() {
            if (win.firstFrame) { win.firstFrame = false; probe.log("first-frame", "") }
        }
    }

    Probe { id: scene; anchors.fill: parent; host: win; modeName: probe ? probe.modeLabel : "plain" }
}
