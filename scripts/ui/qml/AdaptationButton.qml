import QtQuick

Rectangle {
    id: control

    property string text: ""
    property string tone: "primary"
    property bool loading: false
    signal clicked()

    implicitWidth: Math.max(44, label.implicitWidth + 28)
    implicitHeight: 46
    radius: 12
    border.width: tone === "secondary" ? 1 : 0
    border.color: "#dee8e5"
    color: !enabled ? "#f1f5f4"
          : tone === "danger"
            ? (buttonMouse.pressed ? "#f3c7c2" : buttonMouse.containsMouse ? "#fbd9d6" : "#fcebe8")
            : tone === "secondary"
              ? (buttonMouse.pressed ? "#d9e9e4" : buttonMouse.containsMouse ? "#eaf4f1" : "#ffffff")
              : (buttonMouse.pressed ? "#d9e9e4" : buttonMouse.containsMouse ? "#d9ede6" : "#eaf4f1")

    Behavior on color {
        ColorAnimation { duration: 180; easing.type: Easing.OutCubic }
    }

    Accessible.role: Accessible.Button
    Accessible.name: text

    Text {
        id: label
        anchors.fill: parent
        text: control.loading ? "处理中…" : control.text
        color: !control.enabled ? "#64777a"
             : control.tone === "danger" ? "#b83838" : "#286f70"
        font.family: "Inter"
        font.pixelSize: 14
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    MouseArea {
        id: buttonMouse
        anchors.fill: parent
        enabled: control.enabled && !control.loading
        hoverEnabled: true
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: control.clicked()
    }
}
