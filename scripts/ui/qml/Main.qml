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
    color: "#f6f8f7"
    property bool maximizedByButton: false
    property bool adaptationPage: false

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
                source: Qt.resolvedUrl("../assets/myl-v2-brand.svg")
                width: 10
                height: 10
                sourceSize: Qt.size(10, 10)
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

            Rectangle {
                id: homeButton
                x: 13
                y: 26
                width: 50
                height: 50
                radius: 20
                color: !homeWindow.adaptationPage || homeButtonMouse.containsMouse
                       ? "#eaf4f1" : "transparent"
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
                       ? "#eaf4f1" : "transparent"
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
            color: "#f6f8f7"

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

                    Rectangle {
                        id: shortcutHint
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
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

            Flickable {
                id: adaptationContent
                objectName: "adaptationContent"
                visible: homeWindow.adaptationPage
                width: Math.min(920, parent.width - 48)
                height: parent.height
                anchors.horizontalCenter: parent.horizontalCenter
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentHeight: adaptationColumn.y + adaptationColumn.implicitHeight + 24

                Column {
                    id: adaptationColumn
                    x: 0
                    y: adaptationContent.height < 760 ? 24 : 40
                    width: parent.width
                    spacing: 0

                    Item {
                        width: parent.width
                        height: 55

                        Column {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 5
                            Text {
                                text: "HOME  /  PERSONAL ADAPTATION"
                                color: "#286f70"
                                font.family: "Inter"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                            }
                            Text {
                                text: "个人适配"
                                color: "#26383a"
                                font.family: "Inter"
                                font.pixelSize: 30
                                font.weight: Font.DemiBold
                                lineHeightMode: Text.FixedHeight
                                lineHeight: 38
                            }
                        }

                        Rectangle {
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            width: 188
                            height: 42
                            radius: 12
                            color: "#eaf4f1"
                            Row {
                                anchors.centerIn: parent
                                spacing: 8
                                Text {
                                    text: "24 句"
                                    color: "#286f70"
                                    font.family: "Inter"
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    text: "训练 + 验证"
                                    color: "#64777a"
                                    font.family: "Inter"
                                    font.pixelSize: 13
                                }
                            }
                        }
                    }

                    Item { width: 1; height: adaptationContent.height < 760 ? 16 : 24 }
                    Rectangle { width: parent.width; height: 1; color: "#dee8e5" }
                    Item { width: 1; height: adaptationContent.height < 760 ? 16 : 24 }

                    Rectangle {
                        id: adaptationProgressCard
                        width: parent.width
                        height: 82
                        radius: 16
                        color: "#ffffff"
                        border.color: "#dee8e5"
                        border.width: 1

                        Row {
                            x: 23
                            y: 16
                            width: parent.width - 46
                            height: 20
                            Text {
                                width: parent.width / 2
                                text: "24 句采集进度"
                                color: "#26383a"
                                font.family: "Inter"
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                                verticalAlignment: Text.AlignVCenter
                            }
                            Text {
                                width: parent.width / 2
                                text: ("0" + Math.min(24, Math.max(1, bridge.adaptation.count + 1))).slice(-2) + " / 24"
                                color: "#286f70"
                                font.family: "Inter"
                                font.pixelSize: 13
                                horizontalAlignment: Text.AlignRight
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        Rectangle {
                            id: adaptationProgressTrack
                            x: 23
                            y: 50
                            width: parent.width - 46
                            height: 6
                            radius: 3
                            color: "#eaf4f1"
                            Rectangle {
                                width: parent.width * Math.min(24, Math.max(1, bridge.adaptation.count + 1)) / 24
                                height: parent.height
                                radius: 3
                                color: "#286f70"
                            }
                        }
                    }

                    Item { width: 1; height: adaptationContent.height < 760 ? 16 : 23 }

                    Rectangle {
                        width: parent.width
                        height: 113
                        radius: 16
                        color: "#ffffff"
                        border.color: "#dee8e5"
                        border.width: 1
                        Column {
                            x: 23
                            y: 20
                            width: parent.width - 46
                            spacing: 14
                            Text {
                                text: bridge.adaptation.count >= 24
                                      ? "24 句采集已完成"
                                      : "请按顺序朗读 · 第 " + Math.min(24, bridge.adaptation.count + 1) + " 句"
                                color: "#64777a"
                                font.family: "Inter"
                                font.pixelSize: 12
                            }
                            Text {
                                width: parent.width
                                text: bridge.adaptation.phrase.length > 0
                                      ? bridge.adaptation.phrase
                                      : (bridge.adaptation.count >= 24 ? "可以开始训练并比较效果" : "请先等待识别模型就绪")
                                color: "#26383a"
                                font.family: "Inter"
                                font.pixelSize: 20
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                        }
                    }

                    Item { width: 1; height: adaptationContent.height < 760 ? 16 : 23 }

                    Row {
                        width: parent.width
                        spacing: 14

                        Rectangle {
                            width: (parent.width - 14) / 2
                            height: 144
                            radius: 16
                            color: "#ffffff"
                            border.color: "#dee8e5"
                            border.width: 1
                            Column {
                                x: 23
                                y: 20
                                width: parent.width - 46
                                spacing: 12
                                Text {
                                    text: "录制提示"
                                    color: "#26383a"
                                    font.family: "Inter"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    width: parent.width
                                    text: "保持正面、光线稳定；按右 Ctrl 开始；成功保存后会显示下一句。"
                                    color: "#64777a"
                                    font.family: "Inter"
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    text: "嘴部样本与训练结果保存在本机。"
                                    color: "#286f70"
                                    font.family: "Inter"
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        Rectangle {
                            width: (parent.width - 14) / 2
                            height: 144
                            radius: 16
                            color: "#ffffff"
                            border.color: "#dee8e5"
                            border.width: 1
                            Column {
                                x: 23
                                y: 20
                                width: parent.width - 46
                                spacing: 15
                                Text {
                                    text: "这 24 句如何使用"
                                    color: "#26383a"
                                    font.family: "Inter"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }
                                Row {
                                    width: parent.width
                                    spacing: 12
                                    Rectangle {
                                        width: (parent.width - 12) / 2
                                        height: 42
                                        radius: 10
                                        color: "#eaf4f1"
                                        Text {
                                            anchors.centerIn: parent
                                            text: "18 句用于训练"
                                            color: "#286f70"
                                            font.family: "Inter"
                                            font.pixelSize: 12
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                    Rectangle {
                                        width: (parent.width - 12) / 2
                                        height: 42
                                        radius: 10
                                        color: "#ffffff"
                                        Text {
                                            anchors.centerIn: parent
                                            text: "6 句只做验证"
                                            color: "#286f70"
                                            font.family: "Inter"
                                            font.pixelSize: 12
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Item { width: 1; height: adaptationContent.height < 760 ? 16 : 23 }

                    Rectangle {
                        width: parent.width
                        height: 94
                        radius: 16
                        color: "#ffffff"
                        border.color: "#dee8e5"
                        border.width: 1

                        Button {
                            id: adaptationMainAction
                            x: 23
                            y: 24
                            width: 180
                            height: 46
                            padding: 0
                            enabled: bridge.adaptation.available
                                     && !bridge.adaptation.training
                                     && !bridge.adaptation.processing
                                     && (bridge.adaptation.count < 24 || bridge.adaptation.beforeCer < 0)
                            background: Rectangle {
                                radius: 12
                                color: adaptationMainAction.enabled ? "#eaf4f1" : "#f1f5f4"
                            }
                            contentItem: Text {
                                text: bridge.adaptation.training
                                      ? "正在训练…"
                                      : bridge.adaptation.processing
                                        ? "正在识别…"
                                        : bridge.adaptation.recording
                                          ? "结束当前句"
                                          : bridge.adaptation.count >= 24
                                            ? (bridge.adaptation.beforeCer >= 0 ? "本轮已完成" : "训练并比较")
                                            : "开始录制"
                                color: "#286f70"
                                font.family: "Inter"
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            onClicked: {
                                if (bridge.adaptation.count >= 24)
                                    bridge.trainAdaptation()
                                else
                                    bridge.recordAdaptation()
                            }
                        }

                        Column {
                            x: 217
                            y: 22
                            width: parent.width - 240
                            spacing: 4
                            Text {
                                width: parent.width
                                text: bridge.adaptation.recording
                                      ? "正在录制；再次点击按钮或按右 Ctrl 结束"
                                      : "也可按右 Ctrl 开始 / 结束"
                                color: "#64777a"
                                font.family: "Inter"
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                width: parent.width
                                text: bridge.adaptation.count >= 24
                                      ? (bridge.adaptation.beforeCer >= 0
                                         ? "已用 6 句留出数据完成比较"
                                         : "录完 24 句后训练，再用 6 句留出数据比较效果")
                                      : "录完 24 句后训练，再用 6 句留出数据比较效果"
                                color: "#64777a"
                                font.family: "Inter"
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Text {
                        width: parent.width
                        visible: bridge.adaptation.message.length > 0
                        text: bridge.adaptation.message
                        color: "#286f70"
                        font.family: "Inter"
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }

                    Column {
                        width: parent.width
                        visible: bridge.adaptation.count >= 24
                        spacing: 12

                        Rectangle {
                            width: parent.width
                            height: 136
                            radius: 16
                            color: "#ffffff"
                            border.color: "#dee8e5"
                            border.width: 1
                            Column {
                                x: 23
                                y: 20
                                width: parent.width - 46
                                spacing: 10
                                Text {
                                    text: "留出句比较（6 句）"
                                    color: "#26383a"
                                    font.family: "Inter"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    width: parent.width
                                    text: bridge.adaptation.beforeCer >= 0
                                          ? "原模型字符错误率 " + bridge.adaptation.beforeCer + "%  →  调整后 " + bridge.adaptation.afterCer + "%"
                                          : "训练完成后显示留出句比较结果"
                                    color: "#64777a"
                                    font.family: "Inter"
                                    font.pixelSize: 13
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    width: parent.width
                                    text: bridge.adaptation.candidate.length > 0
                                          ? "留出句指标改善，已生成候选模型；可手动启用。"
                                          : (bridge.adaptation.beforeCer >= 0
                                             ? "留出句没有改善，原模型仍为默认模型。"
                                             : "只有留出句字符错误率下降时才保存候选模型。")
                                    color: "#64777a"
                                    font.family: "Inter"
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        Row {
                            spacing: 12
                            Button {
                                text: "启用个人模型（重启后）"
                                enabled: bridge.adaptation.candidate.length > 0 && !bridge.adaptation.training
                                onClicked: bridge.activateAdaptation()
                            }
                            Button {
                                text: "恢复原模型（重启后）"
                                enabled: bridge.adaptation.active.length > 0
                                onClicked: bridge.resetAdaptationModel()
                            }
                            Button {
                                text: "新建下一轮"
                                enabled: bridge.adaptation.count >= 24 && !bridge.adaptation.training
                                onClicked: bridge.startAdaptation()
                            }
                        }

                        Text {
                            width: parent.width
                            text: "本次已加载：" + bridge.adaptation.loaded + "；下次默认启动："
                                  + (bridge.adaptation.active.length > 0 ? "个人模型" : "原模型")
                            color: "#64777a"
                            font.family: "Inter"
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }
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
            color: "#89beb8"
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
            color: "#89beb8"
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
