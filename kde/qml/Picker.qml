import QtQuick

// Wallfliper KDE, stage 1 picker. Independent of the window kind: hosted by the
// layer-shell Window in Main.qml, or by a plain view in the headless smoke test.
//
// Inputs (context properties set from Python):
//   wallpapers   list of { name, path, url }
//   emptyDetail  why the list is empty ("" when it is not)
//   emptyHint    what the user can do about it
//   controller   slots: log(event, detail), close(), confirm(index) -> "" or a message
//
// Style follows the upstream DESIGN.md: flat, monochrome, square corners, 2px white
// frame as the selection language, no shadows, no gradients.
FocusScope {
    id: root
    focus: true

    // Set by the host: device pixel ratio of the screen, so thumbnails decode at
    // the pixel size they are shown at (and no larger).
    property real dpr: 1.0

    readonly property int count: wallpapers.length
    property alias currentIndex: list.currentIndex
    readonly property rect panelRect: Qt.rect(panel.x, panel.y, panel.width, panel.height)
    property string notice: ""

    // Tokens (same values as upstream Theme.qml).
    readonly property string fontFamily:
        Qt.fontFamilies().indexOf("JetBrainsMono Nerd Font") !== -1 ? "JetBrainsMono Nerd Font" : "monospace"
    readonly property color bg: Qt.rgba(5 / 255, 5 / 255, 5 / 255, 0.95)
    readonly property color cellFill: "#0d0d0d"
    readonly property color frame: "#ffffff"
    readonly property color text: "#ffffff"
    readonly property color muted: "#8a8a8a"
    readonly property color faint: "#4a4a4a"
    readonly property int frameWidth: 2

    // Geometry: thumbnails are fitted (never cropped) into fixed 16:10 cells, so the
    // strip does not reflow as images finish loading.
    readonly property int cellW: 224
    readonly property int cellH: 140
    readonly property int gap: 12
    readonly property int pad: 16

    function move(delta) {
        if (root.count === 0)
            return
        list.currentIndex = Math.max(0, Math.min(root.count - 1, list.currentIndex + delta))
    }

    function confirmIndex(index) {
        if (root.count === 0)
            return
        const message = controller.confirm(index)
        if (message !== "")
            root.notice = message
    }

    Keys.onPressed: (event) => {
        root.notice = ""
        if (event.key === Qt.Key_Escape) {
            controller.close()
        } else if (event.key === Qt.Key_Left) {
            root.move(-1)
        } else if (event.key === Qt.Key_Right) {
            root.move(1)
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (!event.isAutoRepeat)
                root.confirmIndex(list.currentIndex)
        } else {
            return
        }
        event.accepted = true
    }

    // Click anywhere outside the panel closes (the panel's own MouseArea swallows clicks inside).
    MouseArea {
        anchors.fill: parent
        onClicked: {
            controller.log("click-away", "")
            controller.close()
        }
    }

    Rectangle {
        id: panel
        anchors.centerIn: parent
        width: Math.min(root.width - 96, 5 * root.cellW + 4 * root.gap + 2 * root.pad)
        height: root.pad + brand.implicitHeight + 12 + root.cellH + root.pad
        color: root.bg
        border.color: root.frame
        border.width: root.frameWidth

        MouseArea { anchors.fill: parent }

        Text {
            id: brand
            anchors.top: parent.top
            anchors.topMargin: root.pad
            anchors.left: parent.left
            anchors.leftMargin: root.pad
            text: "wallfliper"
            color: root.text
            font.family: root.fontFamily
            font.pixelSize: 15
            font.bold: true
        }

        Text {
            anchors.verticalCenter: brand.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: root.pad
            text: root.notice
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: 13
        }

        ListView {
            id: list
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: root.frameWidth
            anchors.rightMargin: root.frameWidth
            anchors.bottomMargin: root.pad
            height: root.cellH
            visible: root.count > 0
            clip: true
            orientation: ListView.Horizontal
            spacing: root.gap
            interactive: false
            model: wallpapers
            currentIndex: 0
            cacheBuffer: root.cellW * 3

            // Keep the current card centred; margins let the first and last card reach the centre too.
            leftMargin: (width - root.cellW) / 2
            rightMargin: (width - root.cellW) / 2
            highlightRangeMode: ListView.StrictlyEnforceRange
            preferredHighlightBegin: (width - root.cellW) / 2
            preferredHighlightEnd: (width + root.cellW) / 2
            highlightMoveDuration: 180

            onCurrentIndexChanged: controller.log("select", String(currentIndex))

            delegate: Item {
                id: cell
                required property int index
                required property var modelData
                readonly property bool current: ListView.isCurrentItem

                width: root.cellW
                height: root.cellH
                opacity: cell.current ? 1.0 : 0.55
                Behavior on opacity { NumberAnimation { duration: 120 } }

                Rectangle {
                    anchors.fill: parent
                    color: root.cellFill
                }

                Image {
                    id: img
                    anchors.fill: parent
                    source: cell.modelData.url
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    autoTransform: true
                    sourceSize: Qt.size(root.cellW * root.dpr, root.cellH * root.dpr)
                }

                // Unreadable / unsupported file: say so quietly instead of an empty box.
                Text {
                    anchors.centerIn: parent
                    visible: img.status === Image.Error
                    text: "×"
                    color: root.muted
                    font.family: root.fontFamily
                    font.pixelSize: 22
                }

                // Selection language: the white frame.
                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.color: cell.current ? root.frame : "transparent"
                    border.width: root.frameWidth
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        list.currentIndex = cell.index
                        root.forceActiveFocus()
                    }
                    onDoubleClicked: root.confirmIndex(cell.index)
                }
            }
        }

        Column {
            visible: root.count === 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: root.pad
            anchors.rightMargin: root.pad
            anchors.bottomMargin: root.pad
            height: root.cellH
            spacing: 8

            Item { width: 1; height: Math.max(0, (root.cellH - emptyTitle.height - emptyDetailText.height - emptyHintText.height - 16) / 2) }

            Text {
                id: emptyTitle
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: "no wallpapers to show"
                color: root.text
                font.family: root.fontFamily
                font.pixelSize: 15
                font.bold: true
            }
            Text {
                id: emptyDetailText
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WrapAnywhere
                text: emptyDetail
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: 13
            }
            Text {
                id: emptyHintText
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WrapAnywhere
                text: emptyHint
                color: root.faint
                font.family: root.fontFamily
                font.pixelSize: 13
            }
        }
    }
}
