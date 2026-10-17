import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// Mirrors upstream Main.qml's window block exactly (scope, Overlay layer,
// Exclusive keyboard, all four anchors, Screen-sized transparent Window).
Window {
    id: win
    property bool firstFrame: true
    visible: true
    width: Screen.width
    height: Screen.height
    color: "transparent"

    LayerShell.Window.scope: "wallfliper"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight

    Component.onCompleted: {
        probe.log("completed", "layer-shell window created")
        win.requestActivate()
    }

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

    Probe { id: scene; anchors.fill: parent; host: win; modeName: "layer" }
}
