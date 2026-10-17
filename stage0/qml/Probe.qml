import QtQuick

// Stage 0 diagnostic scene: test harness, not the product UI. It borrows the
// upstream chrome language (2px white frame, near-black 0.95 fill) so alpha and
// transparency behave as the real bar will. Everything outside the bar is
// transparent and acts as click-away, like upstream.
FocusScope {
    id: root
    required property var host          // the hosting Window
    required property string modeName

    property string lastKey: "-"
    property int keyCount: 0
    property int barClicks: 0

    focus: true
    onActiveFocusChanged: probe.log("scope-activeFocus", String(activeFocus))

    Keys.onPressed: (event) => {
        root.keyCount += 1
        root.lastKey = event.text !== "" ? event.text : "key#" + event.key
        probe.log("key", "code=" + event.key + " text=" + JSON.stringify(event.text)
                  + " repeat=" + event.isAutoRepeat)
        if (event.key === Qt.Key_Escape) {
            probe.log("esc-quit", "")
            Qt.quit()
        }
        event.accepted = true
    }

    MouseArea {
        anchors.fill: parent
        onClicked: {
            probe.log("click-away", "")
            Qt.quit()
        }
    }

    // Window extent marker (faint), so a windowed surface's real size/position is visible.
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: "#66ffffff"
        border.width: 1
    }

    Rectangle {
        id: bar
        anchors.centerIn: parent
        width: Math.max(460, info.implicitWidth + 40)
        height: info.implicitHeight + 28
        color: Qt.rgba(5 / 255, 5 / 255, 5 / 255, 0.95)
        border.color: "#ffffff"
        border.width: 2

        MouseArea {
            anchors.fill: parent
            onClicked: {
                root.barClicks += 1
                probe.log("bar-click", String(root.barClicks))
            }
        }

        Column {
            id: info
            anchors.centerIn: parent
            spacing: 4
            readonly property string fam: "monospace"

            Text {
                text: "wallfliper · stage 0 · " + root.modeName
                color: "#ffffff"; font.family: info.fam; font.bold: true; font.pixelSize: 15
            }
            Text {
                text: "screen   " + (root.host.screen ? root.host.screen.name + "  "
                      + root.host.screen.width + "x" + root.host.screen.height
                      + "  dpr " + root.host.screen.devicePixelRatio : "?")
                color: "#8a8a8a"; font.family: info.fam; font.pixelSize: 13
            }
            Text {
                text: "window   " + root.host.width + "x" + root.host.height
                      + " @ " + root.host.x + "," + root.host.y
                color: "#8a8a8a"; font.family: info.fam; font.pixelSize: 13
            }
            Text {
                text: "active " + root.host.active + "   focus " + root.activeFocus
                      + "   visibility " + root.host.visibility
                color: "#8a8a8a"; font.family: info.fam; font.pixelSize: 13
            }
            Text {
                text: "last key  " + root.lastKey + "  (" + root.keyCount + ")"
                color: "#ffffff"; font.family: info.fam; font.pixelSize: 13
            }
            Text {
                text: "Esc or click outside = close"
                color: "#4a4a4a"; font.family: info.fam; font.pixelSize: 13
            }
        }
    }
}
