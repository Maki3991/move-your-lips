import QtQuick

Rectangle {
    id: selector

    property bool usingPersonal: false
    property bool hasPersonalModel: false
    property bool switching: false
    signal originalRequested()
    signal personalRequested()

    implicitWidth: 406
    implicitHeight: 46
    radius: 12
    color: "#ffffff"
    border.color: "#dee8e5"
    border.width: 1

    Row {
        x: 3
        y: 3
        width: parent.width - 6
        height: parent.height - 6
        spacing: 3

        Repeater {
            model: 2
            delegate: Rectangle {
                required property int index
                readonly property bool selected: index === (selector.usingPersonal ? 1 : 0)
                readonly property bool optionEnabled: !selector.switching
                                                       && (index === 0 || selector.hasPersonalModel)

                width: (selector.width - 9) / 2
                height: 40
                radius: 9
                color: !optionEnabled ? "#f1f5f4" : selected ? "#eaf4f1" : "#ffffff"

                Text {
                    anchors.centerIn: parent
                    text: selector.switching && selected ? "切换中…"
                          : index === 0 ? "原模型" : "个人模型"
                    color: !parent.optionEnabled ? "#64777a"
                          : parent.selected ? "#286f70" : "#64777a"
                    font.family: "Inter"
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }

                MouseArea {
                    anchors.fill: parent
                    enabled: parent.optionEnabled && !parent.selected
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: {
                        if (index === 0)
                            selector.originalRequested()
                        else
                            selector.personalRequested()
                    }
                }
            }
        }
    }
}
