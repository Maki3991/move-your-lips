import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects
import QtQuick.Window

ApplicationWindow {
    id: homeWindow
    objectName: "homeWindow"
    title: "Move Your Lips"
    visible: bridge.homeVisible
    // Keep the first Home window compact, close to the reference app shown by
    // the user, while still leaving enough room for the history cards.
    property int defaultWidth: 1000
    property int defaultHeight: 650
    property int minimumHomeWidth: 900
    property int minimumHomeHeight: 580
    width: Math.min(defaultWidth, Screen.width - 80)
    height: Math.min(defaultHeight, Screen.height - 120)
    minimumWidth: Math.min(minimumHomeWidth, Screen.width - 80)
    minimumHeight: Math.min(minimumHomeHeight, Screen.height - 120)
    flags: Qt.Window | Qt.FramelessWindowHint
    color: "#ffffff"
    property bool maximizedByButton: false

    function clampWindowPosition() {
        x = Math.max(0, Math.min(x, Math.max(0, Screen.width - width)))
        y = Math.max(0, Math.min(y, Math.max(0, Screen.height - height)))
    }

    function setHomeSize(targetWidth, targetHeight, centerWindow) {
        if (maximizedByButton) {
            homeWindow.visibility = Window.Windowed
            maximizedByButton = false
        }

        var maxWidth = Math.max(minimumWidth, Screen.width - 80)
        var maxHeight = Math.max(minimumHeight, Screen.height - 120)
        width = Math.max(minimumWidth, Math.min(maxWidth, targetWidth))
        height = Math.max(minimumHeight, Math.min(maxHeight, targetHeight))

        if (centerWindow) {
            x = Math.round((Screen.width - width) / 2)
            y = Math.round((Screen.height - height) / 2)
        } else {
            clampWindowPosition()
        }
    }

    function adjustHomeSize(deltaWidth, deltaHeight) {
        setHomeSize(width + deltaWidth, height + deltaHeight, false)
    }

    onClosing: function(close) {
        close.accepted = false
        bridge.hideHome()
    }

    Component.onCompleted: {
        x = Math.round((Screen.width - width) / 2)
        y = Math.round((Screen.height - height) / 2)
    }

    // These use the platform's standard zoom-in/zoom-out shortcuts, which
    // map to Ctrl + / Ctrl - on Windows. Ctrl+0 restores the compact default.
    Shortcut {
        sequence: StandardKey.ZoomIn
        onActivated: homeWindow.adjustHomeSize(80, 50)
    }

    Shortcut {
        sequence: StandardKey.ZoomOut
        onActivated: homeWindow.adjustHomeSize(-80, -50)
    }

    Shortcut {
        sequence: "Ctrl+0"
        onActivated: homeWindow.setHomeSize(homeWindow.defaultWidth,
                                            homeWindow.defaultHeight,
                                            true)
    }

    Rectangle {
        id: titleBar
        x: 0
        y: 0
        width: parent.width
        height: 52
        color: "#ffffff"

        MouseArea {
            anchors.fill: parent
            z: 0
            acceptedButtons: Qt.LeftButton
            onPressed: homeWindow.startSystemMove()
        }

        Row {
            id: windowControls
            anchors.right: parent.right
            anchors.top: parent.top
            height: parent.height
            spacing: 0
            z: 1

            Repeater {
                model: [
                    { "source": "../assets/window-minimize.svg", "name": "Minimize" },
                    { "source": "../assets/window-maximize.svg", "name": "Maximize" },
                    { "source": "../assets/window-close.svg", "name": "Close" }
                ]

                delegate: Item {
                    required property var modelData
                    width: 46
                    height: 52

                    Rectangle {
                        anchors.fill: parent
                        color: controlMouse.containsMouse
                               ? (modelData.name === "Close" ? "#33c96f79" : "#f1f4f5")
                               : "transparent"
                    }

                    Image {
                        id: windowIcon
                        anchors.centerIn: parent
                        source: Qt.resolvedUrl(
                            modelData.name === "Maximize" && homeWindow.maximizedByButton
                                ? (controlMouse.containsMouse
                                   ? "../assets/window-restore-hover.svg"
                                   : "../assets/window-restore.svg")
                                : modelData.source
                        )
                        rotation: modelData.name === "Maximize" && homeWindow.maximizedByButton ? 90 : 0
                        smooth: true
                        antialiasing: true
                    }

                    ColorOverlay {
                        anchors.fill: windowIcon
                        source: windowIcon
                        color: "#c96f79"
                        visible: modelData.name === "Close" && controlMouse.containsMouse
                    }

                    MouseArea {
                        id: controlMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: {
                            if (modelData.name === "Minimize") {
                                // Match the previous resident app behavior: hide Home,
                                // keep the process alive, and restore it after a result.
                                bridge.hideHome()
                            } else if (modelData.name === "Maximize") {
                                if (homeWindow.maximizedByButton) {
                                    homeWindow.visibility = Window.Windowed
                                    homeWindow.maximizedByButton = false
                                } else {
                                    homeWindow.visibility = Window.Maximized
                                    homeWindow.maximizedByButton = true
                                }
                            } else {
                                bridge.hideHome()
                            }
                        }
                    }
                }
            }
        }
    }

    Row {
        id: workspace
        anchors.top: titleBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        spacing: 0

        Rectangle {
            id: sidebar
            // Figma: 10 px outer left padding + a 50 px navigation button
            // + an 11 px gap before the main panel.
            width: 71
            height: parent.height
            color: "#ffffff"

            Rectangle {
                id: homeButton
                x: 10
                y: 0
                width: 50
                height: 50
                radius: 20
                color: Qt.rgba(190 / 255, 231 / 255, 239 / 255, 0.6)

                Image {
                    anchors.centerIn: parent
                    source: Qt.resolvedUrl("../assets/home-active.svg")
                    width: 30
                    height: 30
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    antialiasing: true
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: bridge.showHome()
                }
            }
        }

        Item {
            id: contentPanel
            width: parent.width - sidebar.width - 11
            height: parent.height - 8
            clip: true

            Rectangle {
                id: contentSurface
                anchors.fill: parent
                radius: 20
                color: "#d8f1f5"
            }

            Text {
                id: historyTitle
                x: 69
                y: 16
                text: "History"
                color: "#2f3a3d"
                font.family: "Segoe UI"
                font.pixelSize: 22
                font.weight: Font.DemiBold
                verticalAlignment: Text.AlignVCenter
            }

            ListView {
                id: historyList
                objectName: "historyList"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.leftMargin: 69
                anchors.rightMargin: 86
                anchors.topMargin: 60
                clip: true
                spacing: 20
                model: bridge.historyItems
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: true

                delegate: HistoryCard {
                    required property var modelData
                    width: historyList.width
                    recordId: modelData.recordId
                    cardText: modelData.text
                    timestamp: modelData.timestamp
                    appBridge: bridge
                    onDeleteRequested: function(id) {
                        pendingDeleteId = id
                        deleteDialog.open()
                    }
                }

            }

            Text {
                anchors.centerIn: historyList
                visible: bridge.historyItems.length === 0
                text: "还没有识别记录\n按住 Right Ctrl 创建第一条记录"
                color: "#718487"
                font.family: "Segoe UI"
                font.pixelSize: 16
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                lineHeight: 24
            }
        }
    }

    // The window is frameless, so provide an explicit resize target at the
    // bottom-right corner instead of relying on the hidden native frame.
    Item {
        id: resizeGrip
        width: 18
        height: 18
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        z: 100

        Rectangle {
            width: 2
            height: 9
            radius: 1
            color: "#9fcdd3"
            rotation: 45
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: 4
            anchors.bottomMargin: 4
        }

        Rectangle {
            width: 2
            height: 6
            radius: 1
            color: "#9fcdd3"
            rotation: 45
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: 4
            anchors.bottomMargin: 9
        }

        MouseArea {
            id: resizeMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.SizeFDiagCursor
            ToolTip.visible: containsMouse
            ToolTip.text: "拖动调整窗口大小"
            ToolTip.delay: 650
            onPressed: homeWindow.startSystemResize(Qt.RightEdge | Qt.BottomEdge)
        }
    }

    property string pendingDeleteId: ""

    Popup {
        id: deleteDialog
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        width: 390
        height: 190
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 16
            color: "#ffffff"
            border.color: "#d9e3e5"
            border.width: 1
        }

        contentItem: Item {
            Column {
                anchors.fill: parent
                anchors.margins: 24
                spacing: 12

                Text {
                    text: "删除这条识别记录？"
                    color: "#2f3a3d"
                    font.family: "Segoe UI"
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }

                Text {
                    width: parent.width
                    text: "这会从本地历史记录中移除该条文本，且无法撤销。"
                    color: "#66777b"
                    font.family: "Segoe UI"
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }

                Item { width: 1; height: 2 }

                Row {
                    width: parent.width
                    height: 36
                    spacing: 10

                    Item { width: parent.width - 186; height: 1 }

                    Rectangle {
                        width: 88
                        height: 36
                        radius: 10
                        color: cancelMouse.containsMouse ? "#eef7f8" : "#ffffff"
                        border.color: "#cfdddf"

                        Text {
                            anchors.centerIn: parent
                            text: "取消"
                            color: "#405054"
                            font.family: "Segoe UI"
                            font.pixelSize: 14
                        }
                        MouseArea {
                            id: cancelMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: deleteDialog.close()
                        }
                    }

                    Rectangle {
                        width: 88
                        height: 36
                        radius: 10
                        color: deleteMouse.containsMouse ? "#b85d68" : "#c96f79"

                        Text {
                            anchors.centerIn: parent
                            text: "删除"
                            color: "#ffffff"
                            font.family: "Segoe UI"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                        }
                        MouseArea {
                            id: deleteMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: {
                                if (bridge.deleteRecord(pendingDeleteId))
                                    bridge.setNotice("记录已删除")
                                else
                                    bridge.setNotice("删除失败，历史文件可能已变化")
                                deleteDialog.close()
                                noticePopup.open()
                                noticeTimer.restart()
                            }
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: noticePopup
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: parent.height - height - 28
        width: 250
        height: 42
        padding: 0
        modal: false
        closePolicy: Popup.NoAutoClose

        background: Rectangle {
            radius: 12
            color: "#2f3a3d"
            opacity: 0.94
        }

        contentItem: Text {
            text: bridge.notice
            color: "#ffffff"
            font.family: "Segoe UI"
            font.pixelSize: 14
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    Timer {
        id: noticeTimer
        interval: 1800
        onTriggered: noticePopup.close()
    }
}
