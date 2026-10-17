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
    color: "transparent"
    property int contentCornerRadius: 6
    property int windowCornerRadius: visibility === Window.Maximized
                                     || visibility === Window.FullScreen ? 0 : contentCornerRadius
    property bool edgeResizeEnabled: visibility !== Window.Maximized
                                     && visibility !== Window.FullScreen
                                     && !maximizedByButton
    background: Rectangle {
        radius: homeWindow.windowCornerRadius
        color: "#f6f8f7"
    }
    property bool maximizedByButton: false
    property bool adaptationPage: false
    onAdaptationPageChanged: bridge.setAdaptationPageActive(adaptationPage)

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

    Shortcut {
        sequence: "Ctrl+R"
        onActivated: bridge.refreshHistory()
    }

    Shortcut {
        sequence: "Ctrl+O"
        onActivated: bridge.openStorageFolder()
    }

    Rectangle {
        id: titleBar
        x: 0
        y: 0
        width: parent.width
        height: 54
        color: "#ffffff"
        radius: homeWindow.windowCornerRadius

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: parent.height - homeWindow.windowCornerRadius
            color: "#ffffff"
        }

        MouseArea {
            anchors.fill: parent
            z: 0
            acceptedButtons: Qt.LeftButton
            onPressed: homeWindow.startSystemMove()
        }

        Row {
            anchors.left: parent.left
            anchors.leftMargin: 24
            anchors.verticalCenter: parent.verticalCenter
            height: 54
            spacing: 12

            Image {
                anchors.verticalCenter: parent.verticalCenter
                source: Qt.resolvedUrl("../assets/myl-app-icon-16.png")
                width: 16
                height: 16
                sourceSize: Qt.size(16, 16)
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Move Your Lips"
                color: "#26383a"
                font.family: "Inter"
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
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
                    { "source": "../assets/myl-v2-minimize.svg", "name": "Minimize" },
                    { "source": "../assets/myl-v2-maximize.svg", "name": "Maximize" },
                    { "source": "../assets/myl-v2-close.svg", "name": "Close" }
                ]

                delegate: Item {
                    required property var modelData
                    width: 46
                    height: 54

                    Rectangle {
                        anchors.fill: parent
                        color: controlMouse.containsMouse
                               ? (modelData.name === "Close" ? "#fbe9eb" : "#eaf4f1")
                               : "transparent"
                        radius: modelData.name === "Close" ? homeWindow.windowCornerRadius : 0

                        Rectangle {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            width: parent.width - parent.radius
                            height: parent.height
                            color: parent.color
                            visible: parent.radius > 0
                        }

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: parent.height - parent.radius
                            color: parent.color
                            visible: parent.radius > 0
                        }
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
                        width: modelData.name === "Maximize" && homeWindow.maximizedByButton ? 18.667 : 24
                        height: modelData.name === "Maximize"
                                ? (homeWindow.maximizedByButton ? 18.667 : 20)
                                : 24
                        sourceSize: Qt.size(width, height)
                        rotation: modelData.name === "Maximize" && homeWindow.maximizedByButton ? 90 : 0
                        smooth: true
                        antialiasing: true
                    }

                    ColorOverlay {
                        anchors.fill: windowIcon
                        source: windowIcon
                        color: "#b84e5d"
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
            width: 76
            height: parent.height
            color: "#ffffff"
            radius: homeWindow.windowCornerRadius

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: parent.height - homeWindow.windowCornerRadius
                color: "#ffffff"
            }

            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                width: homeWindow.windowCornerRadius
                height: parent.height
                color: "#ffffff"
                visible: width > 0
            }

            Rectangle {
                id: homeButton
                x: 13
                y: 26
                width: 50
                height: 50
                radius: 20
                color: !homeWindow.adaptationPage || homeButtonMouse.containsMouse
                       ? "#eaf4f1" : "#00eaf4f1"
                Behavior on color {
                    ColorAnimation { duration: 180; easing.type: Easing.OutCubic }
                }

                Image {
                    anchors.centerIn: parent
                    source: Qt.resolvedUrl("../assets/myl-v2-home.svg")
                    width: 24.5
                    height: 25.75
                    sourceSize: Qt.size(24.5, 25.75)
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    antialiasing: true
                }

                MouseArea {
                    id: homeButtonMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        homeWindow.adaptationPage = false
                        bridge.showHome()
                    }
                }
            }

            Rectangle {
                id: adaptationNavigationButton
                objectName: "adaptationNavigationButton"
                x: 13
                y: 90
                width: 50
                height: 50
                Accessible.name: "个人适配"
                radius: 20
                color: homeWindow.adaptationPage || adaptationButtonMouse.containsMouse
                       ? "#eaf4f1" : "#00eaf4f1"
                Behavior on color {
                    ColorAnimation { duration: 180; easing.type: Easing.OutCubic }
                }
                Image {
                    anchors.centerIn: parent
                    source: Qt.resolvedUrl("../assets/myl-v2-mic-compact.svg")
                    width: 18
                    height: 27
                    sourceSize: Qt.size(18, 27)
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    antialiasing: true
                }
                MouseArea {
                    id: adaptationButtonMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: homeWindow.adaptationPage = true
                }
            }
        }

        Rectangle {
            id: contentPanel
            width: parent.width - sidebar.width
            height: parent.height
            clip: true
            color: "transparent"

            // The white corner shows through the rounded gray content surface.
            Rectangle {
                width: homeWindow.contentCornerRadius
                height: homeWindow.contentCornerRadius
                color: "#ffffff"
                visible: width > 0
            }

            Rectangle {
                anchors.fill: parent
                radius: homeWindow.contentCornerRadius
                color: "#f6f8f7"
            }

            // Only the content area's upper-left corner is rounded inside the window.
            Rectangle {
                anchors.top: parent.top
                anchors.right: parent.right
                width: homeWindow.contentCornerRadius
                height: homeWindow.contentCornerRadius
                color: "#f6f8f7"
                visible: width > 0
            }

            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                width: homeWindow.contentCornerRadius
                height: homeWindow.contentCornerRadius
                color: "#f6f8f7"
                visible: width > 0
            }

            Item {
                id: historyContent
                visible: !homeWindow.adaptationPage
                width: Math.min(920, parent.width - 48)
                height: parent.height
                anchors.horizontalCenter: parent.horizontalCenter

                Item {
                    id: pageHeading
                    x: 0
                    y: 40
                    width: parent.width
                    // Matches the 55px Page heading component in Figma.
                    height: 55

                    Column {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 5
                        Text {
                            text: "HOME  /  HISTORY"
                            color: "#286f70"
                            font.family: "Inter"
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: "识别记录"
                            color: "#26383a"
                            font.family: "Inter"
                            font.pixelSize: 30
                            font.weight: Font.DemiBold
                            lineHeightMode: Text.FixedHeight
                            lineHeight: 38
                        }
                    }

                    Row {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: shortcutHint.width + spacing + openVideoFolderButton.width
                        height: 42
                        spacing: 12

                        Rectangle {
                            id: shortcutHint
                            width: 188
                            height: 42
                            radius: 12
                            color: "#eaf4f1"
                            Row {
                                anchors.centerIn: parent
                                spacing: 8
                                Text {
                                    text: "右 Ctrl"
                                    color: "#286f70"
                                    font.family: "Inter"
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    text: "开始识别"
                                    color: "#64777a"
                                    font.family: "Inter"
                                    font.pixelSize: 13
                                }
                            }
                        }

                        AdaptationButton {
                            id: openVideoFolderButton
                            objectName: "openVideoFolderButton"
                            width: implicitWidth
                            height: 42
                            tone: "secondary"
                            text: "打开视频文件夹"
                            onClicked: bridge.openStorageFolder()
                        }
                    }
                }

                Rectangle {
                    id: headerDivider
                    anchors.top: pageHeading.bottom
                    anchors.topMargin: 24
                    width: parent.width
                    height: 1
                    color: "#dee8e5"
                }

                ListView {
                    id: historyList
                    objectName: "historyList"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: headerDivider.bottom
                    anchors.topMargin: 24
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 36
                    clip: true
                    spacing: 12
                    model: bridge.historyItems
                    boundsBehavior: Flickable.StopAtBounds
                    reuseItems: true

                    delegate: Column {
                        required property var modelData
                        required property int index
                        width: historyList.width
                        spacing: 12

                        Item {
                            width: 1
                            height: modelData.showDateHeader && index > 0 ? 12 : 0
                            visible: height > 0
                        }

                        Row {
                            width: parent.width
                            height: 26
                            spacing: 10
                            visible: modelData.showDateHeader
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.dateTitle
                                color: "#26383a"
                                font.family: "Inter"
                                font.pixelSize: 16
                                font.weight: Font.DemiBold
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.dateCaption
                                color: "#64777a"
                                font.family: "Inter"
                                font.pixelSize: 13
                                font.weight: Font.Medium
                                visible: text.length > 0
                            }
                        }

                        HistoryCard {
                            width: parent.width
                            recordId: modelData.recordId
                            cardText: modelData.text
                            leadText: modelData.lead
                            previewText: modelData.preview
                            timestamp: modelData.timestamp
                            details: modelData.details
                            mediaAvailable: modelData.mediaAvailable
                            appBridge: bridge
                            onDeleteRequested: function(id) {
                                pendingDeleteId = id
                                deleteDialog.open()
                            }
                            onPlayMediaRequested: function(id) {
                                if (bridge.playMedia(id))
                                    homeWindow.showNotice("已打开素材播放器")
                                else
                                    homeWindow.showNotice("素材不存在或已被删除")
                            }
                            onRetranscribeRequested: function(id) {
                                if (bridge.requestRetranscribe(id))
                                    homeWindow.showNotice("已开始重新转写")
                                else
                                    homeWindow.showNotice("素材不存在或已被删除")
                            }
                            onDetailsRequested: function(id, value) {
                                detailText = value
                                detailsDialog.open()
                            }
                        }
                    }
                }

                Text {
                    anchors.centerIn: historyList
                    visible: bridge.historyItems.length === 0
                    text: "还没有识别记录\n按一下右 Ctrl 创建第一条记录"
                    color: "#64777a"
                    font.family: "Inter"
                    font.pixelSize: 14
                    horizontalAlignment: Text.AlignHCenter
                    lineHeight: 1.7
                }
            }

            PersonalAdaptationPage {
                id: adaptationContent
                objectName: "adaptationContent"
                adaptationBridge: bridge
                visible: homeWindow.adaptationPage
                width: Math.min(920, parent.width - 48)
                height: parent.height
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }
    }

    // Keep the resize zones narrow so buttons and scrollable content remain usable.
    Repeater {
        model: [
            { name: "left", edges: Qt.LeftEdge, cursor: Qt.SizeHorCursor },
            { name: "right", edges: Qt.RightEdge, cursor: Qt.SizeHorCursor },
            { name: "top", edges: Qt.TopEdge, cursor: Qt.SizeVerCursor },
            { name: "bottom", edges: Qt.BottomEdge, cursor: Qt.SizeVerCursor },
            { name: "topLeft", edges: Qt.TopEdge | Qt.LeftEdge, cursor: Qt.SizeFDiagCursor },
            { name: "topRight", edges: Qt.TopEdge | Qt.RightEdge, cursor: Qt.SizeBDiagCursor },
            { name: "bottomLeft", edges: Qt.BottomEdge | Qt.LeftEdge, cursor: Qt.SizeBDiagCursor },
            { name: "bottomRight", edges: Qt.BottomEdge | Qt.RightEdge, cursor: Qt.SizeFDiagCursor }
        ]

        delegate: MouseArea {
            required property var modelData
            readonly property bool horizontalEdge: modelData.name === "top" || modelData.name === "bottom"
            readonly property bool verticalEdge: modelData.name === "left" || modelData.name === "right"
            readonly property bool rightSide: modelData.name === "right" || modelData.name === "topRight"
                                              || modelData.name === "bottomRight"
            readonly property bool bottomSide: modelData.name === "bottom" || modelData.name === "bottomLeft"
                                               || modelData.name === "bottomRight"
            readonly property int edgeWidth: 7
            readonly property int cornerWidth: 14

            x: horizontalEdge ? cornerWidth : (rightSide ? parent.width - width : 0)
            y: verticalEdge ? cornerWidth : (bottomSide ? parent.height - height : 0)
            width: horizontalEdge ? parent.width - 2 * cornerWidth
                                  : (verticalEdge ? edgeWidth : cornerWidth)
            height: verticalEdge ? parent.height - 2 * cornerWidth
                                 : (horizontalEdge ? edgeWidth : cornerWidth)
            visible: homeWindow.edgeResizeEnabled
            enabled: visible
            z: 100
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton
            cursorShape: modelData.cursor
            onPressed: homeWindow.startSystemResize(modelData.edges)
        }
    }

    property string pendingDeleteId: ""
    property string detailText: ""

    function showNotice(message) {
        bridge.setNotice(message)
        noticePopup.open()
        noticeTimer.restart()
    }

    Popup {
        id: detailsDialog
        parent: Overlay.overlay
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        width: Math.min(440, parent.width - 48)
        height: 420
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: 16
            color: "#ffffff"
            border.color: "#dee8e5"
        }

        contentItem: Flickable {
            clip: true
            contentHeight: detailsColumn.implicitHeight + 40
            Column {
                id: detailsColumn
                x: 24
                y: 20
                width: parent.width - 48
                spacing: 12
                Text {
                    text: "记录详情"
                    color: "#26383a"
                    font.family: "Inter"
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: homeWindow.detailText
                    color: "#64777a"
                    font.family: "Inter"
                    font.pixelSize: 13
                    wrapMode: Text.WrapAnywhere
                    lineHeight: 1.5
                }

            }
        }
    }

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
            border.color: "#dee8e5"
            border.width: 1
        }

        contentItem: Item {
            Column {
                anchors.fill: parent
                anchors.margins: 24
                spacing: 12

                Text {
                    text: "删除这条识别记录？"
                    color: "#26383a"
                    font.family: "Segoe UI"
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }

                Text {
                    width: parent.width
                    text: "这会从本地历史记录中移除该条文本，且无法撤销。"
                    color: "#64777a"
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
                        color: cancelMouse.containsMouse ? "#eaf4f1" : "#ffffff"
                        border.color: "#dee8e5"

                        Text {
                            anchors.centerIn: parent
                            text: "取消"
                            color: "#26383a"
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
                        color: deleteMouse.containsMouse ? "#a94353" : "#b84e5d"

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
                                    homeWindow.showNotice("记录已删除")
                                else
                                    homeWindow.showNotice("删除失败，历史文件可能已变化")
                                deleteDialog.close()
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
            color: "#26383a"
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
