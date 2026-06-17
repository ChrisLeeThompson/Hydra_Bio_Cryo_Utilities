
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import "../Config"

// Custom Spin Box

// Extended SpinBox with:
//   - optional arrow buttons (showArrows: bool)
//   - scroll wheel increment/decrement
//   - Enter/Return commits and releases focus
//   - dark-theme-appropriate selection colors
//   - optional float value support via `decimals` property
//
// Integer use (decimals: 0, the default):
//   CustomSpinBox {
//       from: 1; to: 999; value: 120
//   }
//   // Read back via: spinBox.value
//
// Float use (decimals > 0):
//   CustomSpinBox {
//       decimals: 2
//       floatFrom: 0.0; floatTo: 10.0; floatValue: 3.14; floatStep: 0.01
//   }
//   // Read back via: spinBox.realValue
//
// Do not mix integer and float APIs on the same instance — pick one per
// call site based on the `decimals` setting.

SpinBox {

    id: root

    // --- Public additions ---

    // Visual
    property bool showArrows: true

    // Float-mode configuration.  Leave decimals: 0 for integer behavior.
    property int decimals: 0
    property real floatFrom: 0.0
    property real floatTo: 100.0
    property real floatValue: 0.0
    property real floatStep: 1.0

    // Read-back for callers when decimals > 0.  For integer use, read `value`.
    readonly property real realValue: value / _factor

    // --- Internal ---

    // Tick factor derived from decimals (1 when integer, 10^decimals otherwise).
    readonly property int _factor: Math.pow(10, decimals)

    // No self-referential bindings; only override when in float mode.
    Binding on from     { value: Math.round(root.floatFrom  * root._factor); when: root.decimals > 0 }
    Binding on to       { value: Math.round(root.floatTo    * root._factor); when: root.decimals > 0 }
    Binding on value    { value: Math.round(root.floatValue * root._factor); when: root.decimals > 0 }
    Binding on stepSize { value: Math.round(root.floatStep  * root._factor); when: root.decimals > 0 }

    // --- Defaults ---
    editable: true

    // --- Display formatting (ticks → string) ---
    textFromValue: function(val, locale) {
        if (decimals > 0) {
            return Number(val / _factor).toLocaleString(locale, 'f', decimals)
        }
        return Number(val).toLocaleString(locale, 'f', 0)
    }

    // --- Input parsing (string → ticks) ---
    valueFromText: function(text, locale) {
        if (decimals > 0) {
            return Math.round(Number.fromLocaleString(locale, text) * _factor)
        }
        return Number.fromLocaleString(locale, text)
    }

    // --- Validator respects decimals ---
    validator: decimals > 0
               ? doubleValidator
               : intValidator

    DoubleValidator {
        id: doubleValidator
        bottom: root.floatFrom
        top: root.floatTo
        decimals: root.decimals
        notation: DoubleValidator.StandardNotation
        locale: root.locale.name
    }

    IntValidator {
        id: intValidator
        bottom: root.from
        top: root.to
        locale: root.locale.name
    }

    // --- Arrow visibility ---
    // Setting the indicators invisible is not enough — we also need to
    // collapse their width so they don't reserve space in the layout.
    up.indicator.visible: root.showArrows
    down.indicator.visible: root.showArrows
    up.indicator.width: root.showArrows ? undefined : 0
    down.indicator.width: root.showArrows ? undefined : 0

    // --- Scroll wheel support ---
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        onWheel: (wheel) => {
            if (!root.enabled) return
            if (!root.activeFocus) {      // ← new guard
                wheel.accepted = false     // let the event bubble up
                return
            }
            // increase()/decrease() are programmatic and do NOT emit
            // valueModified on their own, which silently desyncs the
            // displayed value from consumers that recalculate in
            // onValueModified. The wheel is user interaction, so emit
            // it ourselves - but only when the value actually changed
            // (no emission when already clamped at from/to).
            var before = root.value
            if (wheel.angleDelta.y > 0) {
                root.increase()
            } else if (wheel.angleDelta.y < 0) {
                root.decrease()
            }
            if (root.value !== before) {
                root.valueModified()
            }
        }
    }

    // --- Enter/Return dismisses focus ---
    Keys.onReturnPressed: root.focus = false
    Keys.onEnterPressed: root.focus = false

    // --- Selection colors (the black-on-dark fix) ---
    contentItem: TextInput {
        text: root.displayText
        font: root.font
        color: root.enabled
               ? AppConfig.universalForeground
               : AppConfig.textDisabledColor
        selectionColor: AppConfig.universalAccent
        selectedTextColor: AppConfig.universalBackground
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Qt.AlignVCenter
        readOnly: !root.editable
        validator: root.validator
        inputMethodHints: root.decimals > 0
                          ? Qt.ImhFormattedNumbersOnly
                          : Qt.ImhDigitsOnly
    }

}

