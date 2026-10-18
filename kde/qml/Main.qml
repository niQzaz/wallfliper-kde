import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// Window host for the picker. The window block is identical to the one verified in
// Stage 0 (stage0/qml/LayerWindow.qml): layer-shell Overlay, Exclusive keyboard,
// anchored to all edges, a Screen-sized transparent Window. Everything visible lives
// in Picker.qml.
Window {
    id: win
    visible: true
    width: Screen.width
    height: Screen.height
    color: "transparent"
    title: "wallfliper"

    LayerShell.Window.scope: "wallfliper"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight

    onActiveChanged: if (active) picker.forceActiveFocus()

    Component.onCompleted: {
        win.requestActivate()
        controller.log("ready", picker.count + " wallpapers, dpr " + Screen.devicePixelRatio)
    }

    Picker {
        id: picker
        anchors.fill: parent
        dpr: Screen.devicePixelRatio
    }
}
