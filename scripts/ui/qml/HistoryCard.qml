import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects

Rectangle {
    id: card
    objectName: "historyCard"
    property string recordId: ""
    property string cardText: ""
    property string leadText: ""
    property string previewText: ""
    property string timestamp: "时间未知"
    property string details: ""
    property bool mediaAvailable: false
    property var appBridge
    signal deleteRequested(string recordId)
    signal playMediaRequested(string recordId)
    signal retranscribeRequested(string recordId)
    signal detailsRequested(string recordId, string details)

    onRecordIdChanged: {
        copyTimer.stop()
        copyAction.copied = false
    }

    width: 920
    height: 22 + leadItem.implicitHeight
            + (previewItem.visible ? 8 + previewItem.implicitHeight : 0)
            + 8 + 32 + 22
    radius: 16
    color: "#ffffff"
    border.width: 1
    border.color: cardHover.hovered ? "#89beb8" : "#dee8e5"
    antialiasing: true

    Behavior on border.color {
        ColorAnimation {
            duration: 180
            easing.type: Easing.InOutQuad
        }
    }

    HoverHandler {
        id: cardHover
    }

    layer.enabled: cardHover.hovered
    layer.effect: DropShadow {
        transparentBorder: true
        color: "#141f4240"
        horizontalOffset: 0
        verticalOffset: 8
        radius: 10
        samples: 21
    }

    Text {
        id: leadItem
        objectName: "historyCardText"
        x: 22
        y: 22
        width: parent.width - 44
        text: card.leadText
        color: "#26383a"
        font.family: "Inter"
        font.pixelSize: 17
        font.weight: Font.DemiBold
        wrapMode: Text.WrapAnywhere
        textFormat: Text.PlainText
        lineHeightMode: Text.FixedHeight
        lineHeight: 26
        renderType: Text.QtRendering
    }

    Text {
        id: previewItem
        x: 22
        y: leadItem.y + leadItem.implicitHeight + 8
        width: parent.width - 44
        text: card.previewText
        visible: text.length > 0
        color: "#64777a"
        font.family: "Inter"
        font.pixelSize: 14
        wrapMode: Text.WrapAnywhere
        textFormat: Text.PlainText
        lineHeightMode: Text.FixedHeight
        lineHeight: 22
        renderType: Text.QtRendering
    }

    Item {
        id: footer
        objectName: "historyCardFooter"
        x: 22
        y: previewItem.visible
           ? previewItem.y + previewItem.implicitHeight + 8
           : leadItem.y + leadItem.implicitHeight + 8
        width: parent.width - 44
        height: 32

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: card.timestamp
            color: "#64777a"
            font.family: "Inter"
            font.pixelSize: 13
            font.weight: Font.Medium
            verticalAlignment: Text.AlignVCenter
        }

        Row {
            id: actionGroup
            objectName: "historyCardActions"
            anchors.right: parent.right
            anchors.top: parent.top
            width: 96
            height: 32
            opacity: cardHover.hovered || moreMenu.visible ? 1 : 0
            visible: opacity > 0.01 || moreMenu.visible

            Behavior on opacity {
                NumberAnimation {
                    duration: 180
                    easing.type: Easing.InOutQuad
                }
            }

            spacing: 0
            Item {
                    id: copyAction
                    width: 32
                    height: 32
                    property bool copied: false

                    Rectangle {
                        anchors.fill: parent
                        radius: 10
                        color: copyAction.copied ? "#286f70"
                                                 : (copyMouse.containsMouse ? "#eaf4f1" : "transparent")
                        Behavior on color {
                            ColorAnimation {
                                duration: 150
                                easing.type: Easing.OutQuad
                            }
                        }
                    }
                    Image {
                        id: copyIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl("../assets/myl-v2-copy.svg")
                        width: 15.333
                        height: 18.667
                        sourceSize: Qt.size(width, height)
                        smooth: true
                        antialiasing: true
                        visible: !copyAction.copied
                    }
                    Image {
                        anchors.centerIn: parent
                        width: 24
                        height: 24
                        source: Qt.resolvedUrl("../assets/action-copied.svg")
                        smooth: true
                        antialiasing: true
                        visible: copyAction.copied
                    }
                    MouseArea {
                        id: copyMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: card.cardText.length > 0
                        onClicked: {
                            if (card.appBridge.copyText(card.cardText)) {
                                copyAction.copied = true
                                copyTimer.restart()
                            } else {
                                copyAction.copied = false
                                copyTimer.stop()
                            }
                        }
                        onExited: {
                            copyAction.copied = false
                            copyTimer.stop()
                        }
                    }
                    Timer {
                        id: copyTimer
                        interval: 500
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
                        color: deleteMouse.containsMouse ? "#fbe9eb" : "transparent"
                    }
                    Image {
                        id: deleteIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl("../assets/myl-v2-delete.svg")
                        width: 21
                        height: 20.667
                        sourceSize: Qt.size(width, height)
                        smooth: true
                        antialiasing: true
                    }
                    ColorOverlay {
                        anchors.fill: deleteIcon
                        source: deleteIcon
                        color: "#b84e5d"
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
                        color: moreMouse.containsMouse ? "#eaf4f1" : "transparent"
                    }
                    Image {
                        id: moreIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl("../assets/myl-v2-more.svg")
                        width: 18.5
                        height: 18
                        sourceSize: Qt.size(width, height)
                        smooth: true
                        antialiasing: true
                    }
                    MouseArea {
                        id: moreMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: moreMenu.popup()
                    }
                    Menu {
                        id: moreMenu
                        y: moreAction.height
                        MenuItem {
                            text: "重新播放素材"
                            enabled: card.mediaAvailable
                            onTriggered: card.playMediaRequested(card.recordId)
                        }
                        MenuItem {
                            text: "重新转写"
                            enabled: card.mediaAvailable
                            onTriggered: card.retranscribeRequested(card.recordId)
                        }
                        MenuItem {
                            text: "查看记录详情"
                            onTriggered: card.detailsRequested(card.recordId, card.details)
                        }
                    }
                }
        }
    }
}
