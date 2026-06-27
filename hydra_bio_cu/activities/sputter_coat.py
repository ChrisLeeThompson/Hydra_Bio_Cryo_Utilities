"""Sputter Coat activity service.

Hardware-side implementation of the Sputter Coat activity used in
Cryo Prep workflows. Mirrors v2.1's proven sequence on Hydra Bio
systems (the only platform this app targets — ``prepare()`` /
``recover()`` are not supported).

Sequence
--------

1. Verify the sputter coater is installed (else fail early with
   :class:`ActivityResult.EXCEPTION`).
2. Home the sputter coater if not already homed (uninterruptible).
3. Set ion species (plasma gas).
4. Turn on the ion beam.
5. Set ion beam high voltage to the sputter coat HV (12 kV).
6. Set ion beam current and sputter coater current to matching values.
7. Run the sputter (uninterruptible; capped to simulated duration in
   simulation mode).
8. Run the chamber recovery (interruptible per-second loop).

Cancellation
------------

Stop checks are issued before every AutoScript call. The two
operations that can't be interrupted mid-call are:

* ``sputter_coater.home()`` — runs to completion.
* ``sputter_coater.run()`` — runs to completion.

For both, we check the stop event immediately after the call
returns, and return :class:`ActivityResult.STOP` (skipping
subsequent steps and chamber recovery) if set.

The chamber recovery loop is interruptible per second, matching
the GIS Purge pattern.

PFIB conditions
---------------

Sputter Coat mutates plasma gas, high voltage, beam current, and
beam-on state. Capture and restore of those values is handled at
the workflow level — see :meth:`CPWorkflow._before_run` and
:meth:`CPWorkflow._after_run`. The activity itself does not try
to undo its mutations: in a multi-coat sequence, restoring between
consecutive Sputter Coats would revert state we're about to mutate
again.

Beam current
------------

The activity stores beam current as an actual amperes value rather
than a UI-list index. This decouples the storage from the QML's
species-specific current lists: AutoScript snaps the value to the
nearest available current for the active species, so the value is
portable across species changes.

Progress reporting
------------------

The activity emits one ``report_indeterminate`` at the top of
:meth:`_run_inner`, then lets the StatusBar progress bar ride in
indeterminate mode through every step until chamber recovery. The
intermediate steps (set species, beam on, set HV, set current,
sputter run) are all opaque from our side — AutoScript doesn't
expose per-step or mid-call progress hooks — so a unified
"indeterminate for the whole hardware sequence" mode is the most
honest visual signal. Chamber recovery is the one phase we *can*
report determinate progress for, because it's our own
``time.sleep`` loop with a known total; it emits per-second
``on_progress(i, total)`` calls that flip the bar back to
determinate mode automatically.

Status emits at each step keep the user informed of what's
happening even while the bar is indeterminate.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

from .. import defaults
from ..microscope.ion_beam_ops import IonBeamOpsLike, resolve_plasma_gas_enum
from ..microscope.sputter_ops import SputterOpsLike
from ..pre_start_checks import PreStartCheck
from ..pre_start_checks.checks import StagePositionWithinSafeRangeCheck
from .base import (
    ActivityResult,
    ActivityService,
    ProgressCallback,
    StatusCallback,
    report_activity_exception,
    report_indeterminate,
)

logger = logging.getLogger(__name__)


# Grid index → AutoScript grid list. Matches MicroscopeConfig.qml's sputterGrids ordering:
#   0 → Grid 1
#   1 → Grid 2
#   2 → Grid 1 and 2
_GRID_LISTS: List[List[int]] = [
    [1],
    [2],
    [1, 2],
]
_GRID_LABELS: List[str] = ["Grid 1", "Grid 2", "Grids 1 and 2"]


def _format_ion_current(value_a: float) -> str:
    """Format an ion beam current value for user-facing display.

    Picks a magnitude-appropriate unit prefix so the rendered string
    matches microscopy convention (the catalog's curated display
    strings in :data:`defaults.SPUTTER_ION_SPECIES` follow the same
    rules — though with bespoke decimal counts per entry rather than
    the uniform per-unit rule used here).

    * ``value >= 1 µA``            → ``"0.12 µA"`` etc., two decimal places.
    * ``1 nA <= value <  1 µA``    → ``"70.0 nA"`` etc., one decimal place.
    * ``value < 1 nA``             → ``"1.00e-10 A"`` (scientific) as a
                                     defensive fallback for unexpected
                                     values; not reached with any
                                     catalog-driven value today.

    Used by the user-facing status emit just before the activity sets
    the beam current. Log lines and JSONL records continue to use the
    raw amperes value for precision.
    """
    if value_a >= 1e-6:
        return f"{value_a * 1e6:.2f} µA"
    if value_a >= 1e-9:
        return f"{value_a * 1e9:.1f} nA"
    return f"{value_a:.2e} A"


class SputterCoatService(ActivityService):
    """Activity that sputter-coats one or both grids on a Hydra Bio system."""

    activity_id = "sputter_coat"

    def __init__(
        self,
        ion_beam_ops: IonBeamOpsLike,
        sputter_ops: SputterOpsLike,
        grid_index: int,
        ion_species_index: int,
        ion_current_a: float,
        duration_s: int,
        chamber_recovery_s: int,
        instance_id: str,
    ) -> None:
        """Construct the activity.

        Parameters mirror the :class:`SputterCoatRecord` fields. The
        ``instance_id`` is forwarded so log messages can disambiguate
        multiple Sputter Coat instances in the same workflow.
        """
        super().__init__()
        self._ion_beam = ion_beam_ops
        self._sputter = sputter_ops

        self._grid_index = int(grid_index)
        self._ion_species_index = int(ion_species_index)
        self._ion_current_a = float(ion_current_a)
        self._duration_s = int(duration_s)
        self._chamber_recovery_s = int(chamber_recovery_s)
        # Override the class default with this instance's id. Read by
        # the workflow runner for per-instance status routing.
        self.instance_id = str(instance_id)

    def run(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
    ) -> ActivityResult:
        log_prefix = f"Sputter Coat [{self.instance_id}]"
        # Distinct from log_prefix: omits the instance_id (which the
        # user can't see and doesn't need) and uses the user-facing
        # capitalization. Threaded through report_activity_exception
        # to compose tooltip-ready status messages on failure.
        status_prefix = "Sputter coat"

        # ---------------------------------------------------------------
        # Validate parameters and resolve translations.
        # ---------------------------------------------------------------
        if not (0 <= self._grid_index < len(_GRID_LISTS)):
            logger.error(
                "%s: grid_index %d out of range; aborting",
                log_prefix, self._grid_index,
            )
            on_status("Sputter coat: invalid grid selection")
            return ActivityResult.EXCEPTION

        if not (0 <= self._ion_species_index
                < len(defaults.PLASMA_GAS_SPECIES_NAMES)):
            logger.error(
                "%s: ion_species_index %d out of range; aborting",
                log_prefix, self._ion_species_index,
            )
            on_status("Sputter coat: invalid ion species selection")
            return ActivityResult.EXCEPTION

        species_name = defaults.PLASMA_GAS_SPECIES_NAMES[self._ion_species_index]
        species_value = resolve_plasma_gas_enum(species_name)
        grids = _GRID_LISTS[self._grid_index]
        grid_label = _GRID_LABELS[self._grid_index]

        logger.info(
            "%s: starting (grid=%s, species=%s, current=%.3e A, "
            "duration=%ds, recovery=%ds)",
            log_prefix, grid_label, species_name, self._ion_current_a,
            self._duration_s, self._chamber_recovery_s,
        )

        # ---------------------------------------------------------------
        # Verify hardware is present.
        # ---------------------------------------------------------------
        if not self._sputter.is_installed:
            logger.error("%s: sputter coater not installed", log_prefix)
            on_status("Sputter coat: sputter coater not installed")
            return ActivityResult.EXCEPTION

        return self._run_inner(
            stop_event, on_progress, on_status,
            log_prefix, status_prefix, species_name, species_value,
            grids, grid_label,
        )

    def _run_inner(
        self,
        stop_event: threading.Event,
        on_progress: ProgressCallback,
        on_status: StatusCallback,
        log_prefix: str,
        status_prefix: str,
        species_name: str,
        species_value: Any,
        grids: List[int],
        grid_label: str,
    ) -> ActivityResult:
        # AutoScript's sputter_coater.run() is opaque from our side —
        # we hand it a duration and grid list and wait for it to
        # return; there's no per-second hook to report progress
        # against. Rather than scattering indeterminate emits before
        # each opaque step (homing, sputter run), emit it once here
        # at activity start and let the bar ride in indeterminate
        # mode until chamber recovery's per-second loop takes over
        # (which is the one phase we *can* report determinate
        # progress for, because it's our own time.sleep loop).
        report_indeterminate(on_progress)

        # ---------------------------------------------------------------
        # Home the sputter coater if not already homed.
        # ---------------------------------------------------------------
        if stop_event.is_set():
            return ActivityResult.STOP

        if not self._sputter.is_homed:
            on_status("Homing sputter coater...")
            try:
                self._sputter.home()
            except Exception as exc:
                report_activity_exception(
                    on_status, log_prefix, status_prefix, "homing", exc,
                )
                return ActivityResult.EXCEPTION

        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Set ion species.
        # ---------------------------------------------------------------
        on_status(f"Setting PFIB species to {species_name}...")
        try:
            self._ion_beam.plasma_gas = species_value
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "setting ion species", exc,
            )
            return ActivityResult.EXCEPTION

        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Turn on ion beam.
        # ---------------------------------------------------------------
        on_status("Turning on ion beam...")
        try:
            self._ion_beam.turn_on()
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "turning on ion beam", exc,
            )
            return ActivityResult.EXCEPTION

        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Set ion beam high voltage.
        #
        # On Hydra Bio with the MicroSputter, the sputter HV is
        # effectively fixed at 12 kV — but we set it explicitly in
        # case something else changed it earlier in the user's
        # session, matching v2.1's defensive write.
        # ---------------------------------------------------------------
        on_status(f"Setting PFIB high voltage to "
                  f"{defaults.SPUTTER_HIGH_VOLTAGE_V / 1000:.0f} kV...")
        try:
            self._ion_beam.high_voltage = defaults.SPUTTER_HIGH_VOLTAGE_V
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "setting high voltage", exc,
            )
            return ActivityResult.EXCEPTION

        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Set beam current — both ion_beam and sputter_coater receive
        # the same value. AutoScript snaps to the nearest available
        # current for the active species, so we don't need to look up
        # the species-specific available list ourselves.
        # ---------------------------------------------------------------
        on_status(f"Setting beam current to {_format_ion_current(self._ion_current_a)}...")
        try:
            self._ion_beam.beam_current = self._ion_current_a
            self._sputter.current = self._ion_current_a
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix,
                "setting beam current", exc,
            )
            return ActivityResult.EXCEPTION

        if stop_event.is_set():
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Run the sputter.
        #
        # Uninterruptible — we call run() and wait for it to return.
        # In simulation mode, this sleeps for up to _SIM_SPUTTER_MAX_S
        # seconds; on real hardware, the full duration.
        # ---------------------------------------------------------------
        on_status(
            f"Sputtering {grid_label} for {self._duration_s} second"
            f"{'' if self._duration_s == 1 else 's'}..."
        )
        try:
            self._sputter.run(self._duration_s, grids)
        except Exception as exc:
            report_activity_exception(
                on_status, log_prefix, status_prefix, "sputter run", exc,
            )
            return ActivityResult.EXCEPTION

        if stop_event.is_set():
            logger.info(
                "%s: cancelled after sputter run; skipping recovery",
                log_prefix,
            )
            return ActivityResult.STOP

        # ---------------------------------------------------------------
        # Chamber recovery.
        # ---------------------------------------------------------------
        if self._chamber_recovery_s > 0:
            on_status(
                f"Chamber recovery for {self._chamber_recovery_s} second"
                f"{'' if self._chamber_recovery_s == 1 else 's'}..."
            )
            on_progress(0, self._chamber_recovery_s)
            for i in range(1, self._chamber_recovery_s + 1):
                if stop_event.is_set():
                    logger.info(
                        "%s: cancelled during recovery", log_prefix,
                    )
                    return ActivityResult.STOP
                time.sleep(1)
                on_progress(i, self._chamber_recovery_s)

        on_status(f"Sputter coat ({grid_label}) complete")
        logger.info("%s: complete", log_prefix)
        return ActivityResult.COMPLETE
    
    @classmethod
    def pre_start_checks(cls) -> List[PreStartCheck]:
        """Sputter Coat verifies the stage is in a safe position
        before any of the activity's hardware steps.

        Advisory (ASK_CONFIRM on failure) — Sputter Coat doesn't
        itself drive the stage, but it runs in a workflow where
        adjacent activities (GIS Deposition, Home Stage) do, and
        the stage's starting position is the operationally
        meaningful one to surface.
        """
        return [StagePositionWithinSafeRangeCheck()]

    def parameter_summary(self) -> Dict[str, Any]:
        """Provenance summary — labels for enum choices, raw SI for numerics.

        Defensive on the index lookups: an out-of-range ``grid_index``
        or ``ion_species_index`` returns a tagged sentinel string rather
        than raising, so a bad parameter that :meth:`run` would catch
        and report as an EXCEPTION result doesn't also crash the log
        capture. The validation inside :meth:`run` remains the
        load-bearing check; this is just resilience for the log.

        Hardcoded invariants (the 12 kV sputter HV) are intentionally
        excluded — the log records the user's choices, not constants
        applied by the activity. If HV ever becomes user-tunable,
        adding the key here is purely additive thanks to the tolerant
        JSONL loader.
        """
        grids = (
            _GRID_LABELS[self._grid_index]
            if 0 <= self._grid_index < len(_GRID_LABELS)
            else f"<invalid grid_index {self._grid_index}>"
        )
        species = (
            defaults.PLASMA_GAS_SPECIES_NAMES[self._ion_species_index]
            if 0 <= self._ion_species_index < len(defaults.PLASMA_GAS_SPECIES_NAMES)
            else f"<invalid ion_species_index {self._ion_species_index}>"
        )
        return {
            "grids": grids,
            "ion_species": species,
            "ion_current_a": self._ion_current_a,
            "duration_s": self._duration_s,
            "chamber_recovery_s": self._chamber_recovery_s,
        }