import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import HydraBio.Microscope 1.0
import "../Config"

// Sputter Coat activity (Hydra Bio micro sputter coater).
//
// Controls the sputter coater. The activity exposes:
//   • a grid selection ComboBox (Grid 1 / Grid 2 / Grid 1, 2)
//   • an ion species ComboBox, sourced from MicroscopeBounds
//   • a beam current ComboBox whose model + default are swapped when
//     the species changes (beam currents are species-specific)
//   • a read-only high voltage display (fixed by the hardware)
//   • duration and chamber recovery spin boxes
//   • two preset buttons for common sputter durations (bulk / lamella),
//     pulled from SettingsController so the user's last-saved values
//     drive them
//
// Per-instance state model:
//
//   The Cryo page can host multiple Sputter Coat instances. Each
//   instance corresponds to a row in the CryoActivitiesModel and is
//   identified by its `instanceId`. Parameter values are sourced from
//   the model row (via the delegate's `model.*` properties) and pushed
//   back to the controller on every change. The controller's setters
//   are no-ops on equal values, breaking the binding feedback loop.
//
// Beam current storage:
//
//   The selected beam current is stored as an actual current value
//   (amperes), not as a UI-list index. This decouples the saved value
//   from the QML's per-species current lists — switching species
//   doesn't invalidate the saved value, and the activity sends the
//   amperes value directly to AutoScript (which snaps to the nearest
//   available current for the active species).
//
// Species → beam current coupling:
//
//   The species and current ComboBoxes are kept in sync by
//   _syncBeamCurrentToSpecies, which rebinds the current ComboBox's
//   `model` to the new species' beamCurrents list. Calling it
//   imperatively (rather than declaratively) means the model swap and
//   the value-match-to-index lookup happen atomically.
//
//   When the user changes species, the saved current value is
//   discarded and the new species' default current is selected. The
//   pushback to the model uses the new value so the saved current
//   stays consistent with the saved species.
//
//   When the activity is reopened (e.g. on app restart), the stored
//   amperes value is matched against the active species' current list
//   to find the right ComboBox index. Float comparison uses tolerance
//   to absorb sub-femtoampere drift from JSON round-tripping.

GroupBox {

    id: root

    // --- Per-instance binding ---
    //
    // Set by the parent delegate to identify which model row this
    // instance is editing. All setters address the controller using
    // this id. Empty string in the brief startup window before the
    // delegate has bound it; setters guard against this.
    property string instanceId: ""

    // Model-row sourced parameter values. These come in via the
    // delegate (`<param>: model.<role>`) so the binding refreshes
    // when the model row changes (other rows or our own).
    property int gridIndex: 0
    property int ionSpeciesIndex: 0
    property real ionCurrentA: 0      // amperes
    property int duration: 0
    property int chamberRecovery: 0

    // --- Activity header summary ---
    readonly property string parameterSummary:
        gridSelectionComboBox.currentText
        + ", " + ionSpeciesComboBox.currentText
        + ", " + (ionCurrentComboBox.currentText || "—")
        + ", " + duration + "s"

    // --- Species / current coupling ---
    //
    // Tolerance for matching a saved current value (amperes) to an
    // entry in the new species' beamCurrents list. Floating-point
    // round-trip through QSettings can introduce sub-femtoampere
    // drift; a tolerance of 1 nA is well below the smallest list
    // entry and well above any rounding noise.
    readonly property real _currentMatchToleranceA: 1e-9

    function _findCurrentIndexForValue(speciesEntry, valueA) {
        if (!speciesEntry || !speciesEntry.beamCurrents) return -1
        for (let i = 0; i < speciesEntry.beamCurrents.length; ++i) {
            const entry = speciesEntry.beamCurrents[i]
            if (Math.abs(entry.value - valueA) < _currentMatchToleranceA) {
                return i
            }
        }
        return -1
    }

    function _syncBeamCurrentToSpecies(preserveValueA) {
        if (!ionCurrentComboBox) return
        const species = MicroscopeBounds.sputterIonSpecies[ionSpeciesComboBox.currentIndex]
        ionCurrentComboBox.model = species.beamCurrents

        // If the saved value matches an entry in the new species' list,
        // select that entry. Otherwise fall back to the species' default
        // current — the user didn't explicitly pick something for this
        // species, so the species' default is the right fallback.
        let targetIndex = -1
        if (preserveValueA > 0) {
            targetIndex = _findCurrentIndexForValue(species, preserveValueA)
        }
        if (targetIndex < 0) {
            targetIndex = species.defaultCurrentIndex
        }
        ionCurrentComboBox.currentIndex = targetIndex
    }

    // --- Toggle-on auto-sync (public) ---
        //
        // Called by the page delegate when this activity's switch toggles
        // on. Pre-fills the ion species (and beam current) to match the
        // microscope's currently-loaded plasma gas — a UX shortcut for
        // operators who run with a non-default gas (e.g. Oxygen) and would
        // otherwise have to remember to change every fresh sputter
        // activity from the Xenon catalog default.
        //
        // Best-effort: silent no-op when CPWorkflow returns -1 (microscope
        // disconnected, plasma gas outside the catalog, or read failed —
        // see current_microscope_ion_species_index in cp_workflow.py for
        // the failure-mode logging).
        //
        // Order mirrors the species ComboBox's onActivated handler:
        //   1. Drive the species ComboBox's currentIndex (the analog of a
        //      user-click updating the combo before onActivated fires).
        //   2. Push species to the model.
        //   3. _syncBeamCurrentToSpecies(0) — discard the old current,
        //      pick the new species' default (the "user manually picked
        //      a different species" semantic — same as onActivated).
        //   4. Push the new beam current to the model so the saved
        //      current stays consistent with the saved species.
        function syncIonSpeciesToMicroscope() {
            if (!appController.cpWorkflow) return
            if (!root.instanceId) return  // delegate hasn't bound yet

            const idx = appController.cpWorkflow.current_microscope_ion_species_index()
            if (idx < 0) return  // silent no-op — Python logged the why
            if (idx === ionSpeciesComboBox.currentIndex) return  // already matches

            ionSpeciesComboBox.currentIndex = idx
            appController.cryoActivities.set_sputter_ion_species_index(
                root.instanceId, idx
            )

            root._syncBeamCurrentToSpecies(0)

            if (ionCurrentComboBox.currentIndex >= 0) {
                const species = MicroscopeBounds.sputterIonSpecies[idx]
                const newValueA = species.beamCurrents[
                    ionCurrentComboBox.currentIndex
                ].value
                appController.cryoActivities.set_sputter_ion_current_a(
                    root.instanceId, newValueA
                )
            }
        }

    Component.onCompleted: _syncBeamCurrentToSpecies(root.ionCurrentA)

    // When the model row changes its species index from outside (e.g.
    // a different Sputter row was edited and re-bound), re-sync. This
    // is rare but defensive.
    onIonSpeciesIndexChanged: {
        if (ionSpeciesComboBox.currentIndex !== root.ionSpeciesIndex) {
            ionSpeciesComboBox.currentIndex = root.ionSpeciesIndex
        }
    }

    // --- Layout ---
    Layout.fillWidth: true
    focusPolicy: Qt.StrongFocus
    background: Rectangle {
        anchors.fill: parent
        color: "transparent"
    }

    GridLayout {

        id: paramsGrid
        anchors.fill: parent
        columns: 2
        rowSpacing: AppConfig.activityFormRowSpacing
        columnSpacing: AppConfig.activityFormColumnSpacing

        // ---- Row 1: Grid selection ----

        ToolTippedLabel {
            id: gridSelectionLabel
            text: "Grid Selection"
            toolTipText: Strings.gridSelectionLabelTooltip
        }

        ComboBox {
            id: gridSelectionComboBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            Layout.preferredWidth: root.width * 0.35
            model: MicroscopeBounds.sputterGrids
            currentIndex: root.gridIndex
            onActivated: {
                if (root.instanceId) {
                    appController.cryoActivities.set_sputter_grid_index(
                        root.instanceId, currentIndex
                    )
                }
            }
        }

        // ---- Row 2: Ion species ----

        ToolTippedLabel {
            id: ionSpeciesLabel
            text: "Ion Species"
            toolTipText: Strings.ionSpeciesLabelTooltip
        }

        ComboBox {
            id: ionSpeciesComboBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            Layout.preferredWidth: root.width * 0.35
            model: MicroscopeBounds.sputterIonSpecies
            textRole: "name"
            currentIndex: root.ionSpeciesIndex

            onActivated: {
                if (root.instanceId) {
                    appController.cryoActivities.set_sputter_ion_species_index(
                        root.instanceId, currentIndex
                    )
                }
                // Re-sync the current ComboBox's model to the new
                // species' currents. Pass 0 (no value) so we fall
                // back to the new species' default current — the
                // previously-picked current value belonged to the
                // *previous* species' list and isn't meaningful here.
                root._syncBeamCurrentToSpecies(0)

                // The default-current selection just changed; push
                // the new amperes value to the model so the saved
                // current stays consistent with the saved species.
                if (root.instanceId
                    && ionCurrentComboBox.currentIndex >= 0) {
                    const species = MicroscopeBounds.sputterIonSpecies[currentIndex]
                    const newValueA = species.beamCurrents[
                        ionCurrentComboBox.currentIndex
                    ].value
                    appController.cryoActivities.set_sputter_ion_current_a(
                        root.instanceId, newValueA
                    )
                }
            }
        }

        // ---- Row 3: Beam current ----

        ToolTippedLabel {
            id: ionCurrentLabel
            text: "Beam Current"
            toolTipText: Strings.ionCurrentLabelTooltip
        }

        ComboBox {
            id: ionCurrentComboBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            Layout.preferredWidth: root.width * 0.35
            textRole: "display"
            valueRole: "value"
            // model and currentIndex are set imperatively by
            // _syncBeamCurrentToSpecies (on load and on species change).

            onActivated: {
                if (root.instanceId && currentIndex >= 0) {
                    const species = MicroscopeBounds.sputterIonSpecies[ionSpeciesComboBox.currentIndex]
                    const valueA = species.beamCurrents[currentIndex].value
                    appController.cryoActivities.set_sputter_ion_current_a(
                        root.instanceId, valueA
                    )
                }
            }
        }

        // ---- Row 4: High voltage (display only) ----

        ToolTippedLabel {
            id: highVoltageLabel
            text: "High Voltage (kV)"
            toolTipText: Strings.highVoltageLabelTooltip
        }

        Label {
            id: highVoltageValueLabel
            text: MicroscopeBounds.sputterHighVoltageKv
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            Layout.rightMargin: 12
        }

        // ---- Row 5: Duration ----

        ToolTippedLabel {
            id: durationLabel
            text: "Duration (s)"
            toolTipText: Strings.sputterCoatDurationLabelTooltip
        }

        CustomSpinBox {
            id: durationSpinBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            from: appController.cryoActivities.sputterDurationMin
            to: appController.cryoActivities.sputterDurationMax
            value: root.duration
            editable: true
            showArrows: true
            onValueModified: {
                if (root.instanceId) {
                    appController.cryoActivities.set_sputter_duration(
                        root.instanceId, value
                    )
                }
            }
        }

        // ---- Row 6: Chamber recovery ----

        ToolTippedLabel {
            id: chamberRecoveryLabel
            text: "Chamber Recovery (s)"
            toolTipText: Strings.chamberRecoveryLabelTooltip
        }

        CustomSpinBox {
            id: chamberRecoverySpinBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            from: appController.cryoActivities.chamberRecoveryMin
            to: appController.cryoActivities.chamberRecoveryMax
            value: root.chamberRecovery
            editable: true
            showArrows: true
            onValueModified: {
                if (root.instanceId) {
                    appController.cryoActivities.set_sputter_chamber_recovery(
                        root.instanceId, value
                    )
                }
            }
        }

        // ---- Row 7: Preset duration buttons (spans both columns) ----
        //
        // Pull preset values from SettingsController — the user's
        // last-saved bulk/lamella choices drive the buttons.

        RowLayout {

            Layout.columnSpan: 2
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignCenter
            Layout.topMargin: 8
            spacing: 8

            RoundButton {
                id: bulkSputterButton
                text: "Bulk Sputtering"
                radius: AppConfig.buttonRadius

                ToolTip.text: Strings.bulkSputterCoatDurationButtonTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                onClicked: {
                    if (root.instanceId) {
                        appController.cryoActivities.set_sputter_duration(
                            root.instanceId,
                            appController.settings.bulkSputterDuration
                        )
                    }
                }
            }

            RoundButton {
                id: lamellaSputterButton
                text: "Lamella Sputtering"
                radius: AppConfig.buttonRadius

                ToolTip.text: Strings.lamellaSputterCoatDurationButtonTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                onClicked: {
                    if (root.instanceId) {
                        appController.cryoActivities.set_sputter_duration(
                            root.instanceId,
                            appController.settings.lamellaSputterDuration
                        )
                    }
                }
            }
        }
    }
}
