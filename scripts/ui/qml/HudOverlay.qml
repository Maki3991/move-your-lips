import QtQuick
import QtQuick.Window

Window {
    id: hudWindow
    objectName: "hudWindow"
    width: 420
    height: 86
    visible: bridge.hudVisible
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    color: "transparent"
    title: "Move Your Lips Status"
    readonly property bool busy: bridge.hudMode === "loading"
                                 || bridge.hudMode === "starting_camera"
                                 || bridge.hudMode === "processing"
    readonly property bool listening: bridge.hudMode === "listening"

    function centerBottom() {
        x = Math.round((Screen.width - width) / 2)
        y = Math.round(Screen.height - height - 28)
    }

    Component.onCompleted: centerBottom()
    onVisibleChanged: if (visible) centerBottom()

    Rectangle {
        anchors.fill: parent
        color: "#ffffff"
        radius: 8
        border.color: "#d9d9d9"
        border.width: 1
        antialiasing: true

        Row {
            id: standardStatusRow
            x: 19
            y: 17
            height: 16
            spacing: 8
            visible: !hudWindow.listening

            Item {
                width: 12
                height: 12
                anchors.verticalCenter: parent.verticalCenter

                Image {
                    id: spinner
                    anchors.fill: parent
                    source: Qt.resolvedUrl("../assets/hud-spinner.svg")
                    visible: hudWindow.busy
                    smooth: true
                    antialiasing: true

                    RotationAnimation on rotation {
                        from: 0
                        to: 360
                        duration: 850
                        loops: Animation.Infinite
                        running: spinner.visible
                    }
                }

                Image {
                    anchors.centerIn: parent
                    width: 10
                    height: 10
                    source: Qt.resolvedUrl("../assets/hud-result-dot.svg")
                    visible: bridge.hudMode === "result"
                    smooth: true
                    antialiasing: true
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 10
                    height: 10
                    radius: 5
                    color: bridge.hudMode === "error" ? "#c96f79" : "#d9d9d9"
                    visible: !hudWindow.busy && bridge.hudMode !== "result"
                }
            }

            Text {
                text: bridge.hudTitle
                color: "#383838"
                font.family: "Segoe UI"
                font.pixelSize: 12
                font.weight: Font.DemiBold
                verticalAlignment: Text.AlignVCenter
                height: 16
            }
        }

        Text {
            x: 19
            y: 48
            width: 382
            height: 17
            text: bridge.hudBody
            color: "#616161"
            font.family: "Segoe UI"
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            visible: !hudWindow.listening
        }

        Rectangle {
            x: 19
            y: 7
            width: 112
            height: 70
            radius: 6
            color: "#e4f5f7"
            border.color: "#d9d9d9"
            border.width: 1
            clip: true
            visible: hudWindow.listening

            Image {
                anchors.fill: parent
                anchors.margins: 1
                source: bridge.cameraImageSource
                visible: bridge.cameraImageAvailable
                fillMode: Image.PreserveAspectCrop
                cache: false
                smooth: true
                antialiasing: true
            }

            Text {
                anchors.fill: parent
                text: "嘴唇实时预览"
                color: "#777f80"
                font.family: "Segoe UI"
                font.pixelSize: 10
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                visible: !bridge.cameraImageAvailable
            }
        }

        Item {
            x: 147
            y: 9
            width: 252
            height: 68
            visible: hudWindow.listening

            Row {
                x: 0
                y: 0
                height: 16
                spacing: 8

                Image {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 10
                    height: 10
                    source: Qt.resolvedUrl("../assets/hud-listening-dot.svg")
                    smooth: true
                    antialiasing: true
                }

                Text {
                    text: bridge.hudTitle
                    color: "#383838"
                    font.family: "Segoe UI"
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    verticalAlignment: Text.AlignVCenter
                    height: 16
                }
            }

            Text {
                x: 0
                y: 25
                width: parent.width
                height: 16
                text: bridge.hudBody
                color: "#4f595b"
                font.family: "Segoe UI"
                font.pixelSize: 11
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            Text {
                x: 0
                y: 48
                width: parent.width
                height: 15
                text: "松开 Right Ctrl 完成"
                color: "#858585"
                font.family: "Segoe UI"
                font.pixelSize: 10
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
