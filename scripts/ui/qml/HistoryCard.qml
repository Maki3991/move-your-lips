import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects

Rectangle {
    id: card
    objectName: "historyCard"
    property string recordId: ""
    property string cardText: ""
    property string timestamp: "--:--"
    property var appBridge
    signal deleteRequested(string recordId)

    width: 1203
    height: cardTextItem.implicitHeight + 94
    radius: 20
    color: cardHover.hovered ? "#ffffff" : Qt.rgba(1, 1, 1, 0.2)
    antialiasing: true

    Behavior on color {
        ColorAnimation {
            duration: 160
            easing.type: Easing.InOutQuad
        }
    }

    HoverHandler {
        id: cardHover
    }

    Text {
        id: cardTextItem
        objectName: "historyCardText"
        x: 20
        y: 20
        width: parent.width - 40
        text: card.cardText
        color: "#2f3a3d"
        font.family: "Segoe UI"
        font.pixelSize: 22
        font.weight: Font.Normal
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        lineHeightMode: Text.FixedHeight
        lineHeight: 27
        renderType: Text.QtRendering
    }

    Item {
        id: footer
        objectName: "historyCardFooter"
        x: 20
        y: cardTextItem.y + cardTextItem.implicitHeight + 22
        width: parent.width - 40
        height: 32

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: card.timestamp
            color: "#718487"
            font.family: "Segoe UI"
            font.pixelSize: 16
            verticalAlignment: Text.AlignVCenter
        }

        Rectangle {
            id: actionGroup
            objectName: "historyCardActions"
            anchors.right: parent.right
            anchors.top: parent.top
            width: 96
            height: 32
            radius: 10
            // Figma's Actions container is transparent; only the hovered
            // icon button receives its own background.
            color: "transparent"
            opacity: cardHover.hovered ? 1 : 0
            visible: opacity > 0.01

            Behavior on opacity {
                NumberAnimation {
                    duration: 140
                    easing.type: Easing.InOutQuad
                }
            }

            Row {
                anchors.fill: parent
                spacing: 0

                Item {
                    id: copyAction
                    width: 32
                    height: 32
                    property bool copied: false

                    Rectangle {
                        anchors.fill: parent
                        radius: 10
                        color: copyMouse.containsMouse ? "#d8f1f5" : "transparent"
                    }
                    Image {
                        id: copyIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl("../assets/action-copy.svg")
                        smooth: true
                        antialiasing: true
                        visible: !copyAction.copied
                    }
                    Text {
                        anchors.centerIn: parent
                        text: "✓"
                        color: "#4f9a82"
                        font.family: "Segoe UI"
                        font.pixelSize: 18
                        font.weight: Font.DemiBold
                        visible: copyAction.copied
                    }
                    MouseArea {
                        id: copyMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: {
                            copyAction.copied = card.appBridge.copyText(card.cardText)
                            copyTimer.restart()
                        }
                    }
                    Timer {
                        id: copyTimer
                        interval: 1000
                        onTriggered: copyAction.copied = false
                    }
                }

                Item {
                    id: deleteAction
                    width: 32
                    height: 32
                    Rectangle {
                        anchors.fill: parent
                        radius: 10
                        color: deleteMouse.containsMouse ? "#33c96f79" : "transparent"
                    }
                    Image {
                        id: deleteIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl("../assets/action-delete.svg")
                        smooth: true
                        antialiasing: true
                    }
                    ColorOverlay {
                        anchors.fill: deleteIcon
                        source: deleteIcon
                        color: "#c96f79"
                        visible: deleteMouse.containsMouse
                    }
                    MouseArea {
                        id: deleteMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: card.deleteRequested(card.recordId)
                    }
                }

                Item {
                    id: moreAction
                    width: 32
                    height: 32
                    Rectangle {
                        anchors.fill: parent
                        radius: 10
                        color: moreMouse.containsMouse ? "#d8f1f5" : "transparent"
                    }
                    Image {
                        id: moreIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl("../assets/action-more.svg")
                        smooth: true
                        antialiasing: true
                    }
                    MouseArea {
                        id: moreMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: card.appBridge.setNotice("更多操作暂未开放")
                        ToolTip.visible: containsMouse
                        ToolTip.text: "更多操作暂未开放"
                        ToolTip.delay: 450
                    }
                }
            }
        }
    }
}
