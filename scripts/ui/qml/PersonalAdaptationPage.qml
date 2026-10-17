import QtQuick
import QtQuick.Controls

Flickable {
    id: page

    required property var adaptationBridge
    readonly property var adaptation: adaptationBridge.adaptation
    readonly property int completed: Math.min(24, Math.max(0, Number(adaptation.count) || 0))
    readonly property bool training: Boolean(adaptation.training)
    readonly property bool switching: Boolean(adaptation.switching)
    readonly property bool recording: Boolean(adaptation.recording)
    readonly property bool processing: Boolean(adaptation.processing)
    readonly property bool hasResult: adaptation.beforeCer >= 0 && adaptation.afterCer >= 0
    readonly property bool trainingFailed: completed === 24 && !training && !switching
                                           && !hasResult
                                           && /失败|未找到|无法/.test(String(adaptation.message || ""))
    readonly property bool collecting: completed < 24
    readonly property bool resuming: collecting && completed > 0
                                     && !adaptation.captureArmed && !recording && !processing
    readonly property bool actionError: /失败|未找到|无法|错误/.test(String(adaptation.message || ""))

    clip: true
    boundsBehavior: Flickable.StopAtBounds
    contentHeight: content.y + content.implicitHeight + 24

    Column {
        id: content
        x: 0
        y: 40
        width: page.width
        spacing: 24

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

        }

        Rectangle { width: parent.width; height: 1; color: "#dee8e5" }

        Rectangle {
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
                    text: ("0" + page.completed).slice(-2) + " / 24"
                    color: "#286f70"
                    font.family: "Inter"
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignRight
                    verticalAlignment: Text.AlignVCenter
                }
            }

            Rectangle {
                x: 23
                y: 50
                width: parent.width - 46
                height: 6
                radius: 3
                color: "#eaf4f1"
                Rectangle {
                    visible: page.completed > 0
                    width: parent.width * page.completed / 24
                    height: parent.height
                    radius: 3
                    color: "#286f70"
                }
            }
        }

        Rectangle {
            width: parent.width
            height: 113
            radius: 16
            color: "#ffffff"
            border.color: "#dee8e5"
            border.width: 1

            Column {
                x: 23
                y: 23
                width: parent.width - 46
                spacing: 13
                Text {
                    width: parent.width
                    text: page.hasResult
                          ? (adaptation.afterCer < adaptation.beforeCer
                             ? "留出句比较：本次有所改善 · 字符错误率越低越好"
                             : "留出句比较：本次未提升 · 字符错误率越低越好")
                          : page.training ? "24 句采集已完成"
                          : page.trainingFailed ? "训练未完成 · 24 句样本已保留"
                          : page.completed === 24 ? "24 句采集已完成"
                          : page.processing ? "正在识别 · 第 " + (page.completed + 1) + " 句"
                          : page.recording ? "正在录制 · 第 " + (page.completed + 1) + " 句"
                          : page.resuming ? "继续朗读 · 第 " + (page.completed + 1) + " 句"
                          : "请按顺序朗读 · 第 " + (page.completed + 1) + " 句"
                    color: "#64777a"
                    font.family: "Inter"
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    text: page.hasResult
                          ? "原模型 " + adaptation.beforeCer + "%   →   个人模型 "
                            + adaptation.afterCer + "%"
                          : page.training ? "正在训练个人模型…"
                          : page.trainingFailed ? "训练失败，可以重试"
                          : page.completed === 24 ? "正在准备训练…"
                          : adaptation.phrase || "请先等待识别模型就绪"
                    color: "#26383a"
                    font.family: "Inter"
                    font.pixelSize: page.width < 830 ? 20 : 24
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
            }
        }

        Rectangle {
            id: actionCard
            width: parent.width
            height: page.width < 830 && page.hasResult ? 112 : 94
            visible: page.collecting || page.hasResult || page.trainingFailed
            radius: 16
            color: "#ffffff"
            border.color: "#dee8e5"
            border.width: 1

            Row {
                id: collectionActions
                visible: page.collecting
                x: 23
                anchors.verticalCenter: parent.verticalCenter
                spacing: 14

                AdaptationButton {
                    id: mainButton
                    width: implicitWidth
                    height: 46
                    text: page.processing ? "正在识别…"
                          : page.recording ? "结束录制"
                          : page.resuming ? "继续采集" : "开始录制"
                    enabled: adaptation.available && !page.processing
                    onClicked: adaptationBridge.recordAdaptation()
                }

                AdaptationButton {
                    id: redoButton
                    width: implicitWidth
                    height: 46
                    tone: "secondary"
                    text: "重录上一句"
                    visible: page.completed > 0 && !page.recording && !page.processing
                    enabled: !page.switching
                    onClicked: adaptationBridge.redoPreviousAdaptation()
                }

                Column {
                    width: Math.max(0, actionCard.width - 46 - mainButton.width
                                    - (redoButton.visible ? redoButton.width + 14 : 0) - 14)
                    spacing: 4
                    Text {
                        width: parent.width
                        text: page.processing ? "正在保存这一句的采集结果"
                              : page.recording ? "正在采集嘴部画面；点击结束录制或按右 Ctrl"
                              : page.resuming ? "点击继续后，右 Ctrl 才会用于个人适配"
                              : adaptation.captureArmed ? "本页已进入适配采集，右 Ctrl 可开始 / 结束"
                              : "点击开始录制，进入个人适配采集"
                        color: "#64777a"
                        font.family: "Inter"
                        font.pixelSize: page.width < 830 ? 12 : 13
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        width: parent.width
                        text: page.actionError ? adaptation.message
                              : !adaptation.available && !page.recording
                                ? (adaptation.message || "请先等待识别模型就绪")
                              : page.processing ? "完成后自动显示下一句"
                              : page.resuming ? "离开本页时，右 Ctrl 仍是普通识别"
                              : page.completed === 0 ? "未开始时，右 Ctrl 仍是普通识别"
                              : "重录会覆盖上一句样本"
                        color: page.actionError ? "#a94442" : "#64777a"
                        font.family: "Inter"
                        font.pixelSize: page.width < 830 ? 12 : 13
                        wrapMode: Text.WordWrap
                    }
                }
            }

            Row {
                visible: page.trainingFailed
                x: 23
                anchors.verticalCenter: parent.verticalCenter
                spacing: 14

                AdaptationButton {
                    id: retryButton
                    width: implicitWidth
                    height: 46
                    text: "重试训练"
                    enabled: !page.switching
                    onClicked: adaptationBridge.trainAdaptation()
                }
                Column {
                    width: actionCard.width - 46 - retryButton.width - 14
                    spacing: 4
                    Text {
                        text: "不会清空已录制的 24 句"
                        color: "#64777a"
                        font.family: "Inter"
                        font.pixelSize: page.width < 830 ? 12 : 13
                    }
                    Text {
                        width: parent.width
                        text: adaptation.message || "如果仍失败，可稍后返回本页重试"
                        color: "#a94442"
                        font.family: "Inter"
                        font.pixelSize: page.width < 830 ? 12 : 13
                        elide: Text.ElideRight
                    }
                }
            }

            Row {
                visible: page.hasResult
                x: 23
                anchors.verticalCenter: parent.verticalCenter
                spacing: 14

                AdaptationModelSelector {
                    width: Math.min(406, Math.max(330, actionCard.width * 0.46))
                    height: 46
                    usingPersonal: Boolean(adaptation.usingPersonal)
                    hasPersonalModel: Boolean(adaptation.candidate)
                    switching: page.switching
                    onOriginalRequested: adaptationBridge.resetAdaptationModel()
                    onPersonalRequested: adaptationBridge.activateAdaptation()
                }

                AdaptationButton {
                    id: retrainButton
                    width: implicitWidth
                    height: 46
                    tone: "danger"
                    text: "重新训练"
                    enabled: !page.switching
                    onClicked: restartTrainingDialog.open()
                }

                Column {
                    width: Math.max(0, actionCard.width - 46
                                    - Math.min(406, Math.max(330, actionCard.width * 0.46))
                                    - retrainButton.width - 28)
                    spacing: 4
                    Text {
                        width: parent.width
                        text: page.actionError ? adaptation.message
                              : adaptation.afterCer < adaptation.beforeCer
                                ? "本次错误率有所下降" : "本次未提升，建议切回原模型"
                        color: page.actionError ? "#a94442" : "#64777a"
                        font.family: "Inter"
                        font.pixelSize: page.width < 830 ? 12 : 13
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        width: parent.width
                        text: "当前使用：" + (adaptation.usingPersonal ? "个人模型" : "原模型")
                        color: "#64777a"
                        font.family: "Inter"
                        font.pixelSize: page.width < 830 ? 12 : 13
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }

    Dialog {
        id: restartTrainingDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(500, page.width - 32)
        height: 180
        modal: true
        padding: 0
        header: null
        footer: null
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "#47000000" }

        background: Rectangle {
            radius: 18
            color: "#ffffff"
            border.color: "#dee8e5"
            border.width: 1
        }

        contentItem: Item {
            Column {
                x: 28
                y: 26
                width: parent.width - 56
                spacing: 18

                Text {
                    text: "重新开始个人适配？"
                    color: "#26383a"
                    font.family: "Inter"
                    font.pixelSize: 22
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: "这会清空本轮 24 句采集进度和个人模型。历史识别记录会保留。"
                    color: "#64777a"
                    font.family: "Inter"
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                Item {
                    width: parent.width
                    height: 40
                    Row {
                        anchors.right: parent.right
                        spacing: 12
                        AdaptationButton {
                            width: implicitWidth
                            height: 40
                            tone: "secondary"
                            text: "取消"
                            onClicked: restartTrainingDialog.close()
                        }
                        AdaptationButton {
                            width: implicitWidth
                            height: 40
                            tone: "danger"
                            text: "删除并重新训练"
                            onClicked: {
                                restartTrainingDialog.close()
                                adaptationBridge.restartAdaptation()
                            }
                        }
                    }
                }
            }
        }
    }
}
