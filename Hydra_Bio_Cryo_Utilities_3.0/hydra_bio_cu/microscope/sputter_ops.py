"""Sputter coater hardware ops.

Wraps AutoScript's ``microscope.specimen.sputter_coater`` with a narrow,
intention-revealing surface. Two implementations live side by side:

* :class:`SputterOps` — real, backed by ``SdbMicroscopeClient.specimen.sputter_coater``.
* :class:`SimulatedSputterOps` — in-memory simulation for offline UI development.

This module targets the **MicroSputter** device on Hydra Bio systems.
The ``prepare()`` and ``recover()`` calls supported on other systems
(Aquilos, etc.) are intentionally not exposed — they're unsupported
on Hydra Bio. Saving and restoring ion-beam conditions is handled
separately by :class:`PFIBConditionsRecorder`.

``run`` takes a list of integer grid indices — ``[1]``, ``[2]``, or
``[1, 2]`` — matching the Hydra Bio MicroSputter's two-grid layout.
"""
from __future__ import annotations

import logging
import time
from typing import Any, List

logger = logging.getLogger(__name__)


# Simulated-mode caps so dev mode isn't tedious.
_SIM_SPUTTER_MAX_S = 5.0
_SIM_HOME_DURATION_S = 1.5


class SputterOps:
    """Real sputter ops backed by ``SdbMicroscopeClient.specimen.sputter_coater``."""

    def __init__(self, sdb: Any) -> None:
        self._sdb = sdb

    @property
    def is_installed(self) -> bool:
        return bool(self._sdb.specimen.sputter_coater.is_installed)

    @property
    def is_homed(self) -> bool:
        return bool(self._sdb.specimen.sputter_coater.is_homed)

    def home(self) -> None:
        logger.info("Sputter coater: home")
        self._sdb.specimen.sputter_coater.home()
        logger.info("Sputter coater: home complete")

    @property
    def current(self) -> float:
        return float(self._sdb.specimen.sputter_coater.current.value)

    @current.setter
    def current(self, value: float) -> None:
        logger.info("Sputter coater: setting current to %.3e A", value)
        self._sdb.specimen.sputter_coater.current.value = float(value)

    @property
    def current_available(self) -> List[float]:
        return list(self._sdb.specimen.sputter_coater.current.available_values)

    def run(self, duration_s: int, grids: List[int]) -> None:
        logger.info("Sputter coater: run for %ds on grids %r", duration_s, grids)
        self._sdb.specimen.sputter_coater.run(duration_s, grids)
        logger.info("Sputter coater: run complete")


# --- Simulated implementation -------------------------------------------


# Simulated available currents (amperes). Spans roughly the Xenon range
# from MicroscopeConfig.qml.
_SIM_CURRENT_AVAILABLE: List[float] = [
    1e-9, 3e-9, 10e-9, 30e-9, 0.06e-6, 0.12e-6, 0.30e-6, 0.74e-6,
]


class SimulatedSputterOps:
    """Simulated sputter ops for offline development.

    * ``is_installed`` always True (so the activity doesn't trip the
      "not installed" early exit).
    * ``is_homed`` starts False and flips True after ``home()``.
    * ``home()`` and ``run()`` simulate their durations with
      ``time.sleep`` so the user-visible busy indicator is observable.
      ``run`` is capped so a 120s configured sputter doesn't take
      2 minutes in dev.
    """

    def __init__(self) -> None:
        self._is_homed: bool = False
        self._current: float = _SIM_CURRENT_AVAILABLE[5]  # ~0.12 µA

    @property
    def is_installed(self) -> bool:
        return True

    @property
    def is_homed(self) -> bool:
        return self._is_homed

    def home(self) -> None:
        logger.info("Simulated sputter coater: home (simulated %.1fs)", _SIM_HOME_DURATION_S)
        time.sleep(_SIM_HOME_DURATION_S)
        self._is_homed = True
        logger.info("Simulated sputter coater: home complete")

    @property
    def current(self) -> float:
        return self._current

    @current.setter
    def current(self, value: float) -> None:
        logger.info("Simulated sputter coater: setting current to %.3e A", value)
        self._current = float(value)

    @property
    def current_available(self) -> List[float]:
        return list(_SIM_CURRENT_AVAILABLE)

    def run(self, duration_s: int, grids: List[int]) -> None:
        sim_duration = min(float(duration_s), _SIM_SPUTTER_MAX_S)
        logger.info(
            "Simulated sputter coater: run requested %ds on grids %r — simulating %.1fs",
            duration_s, grids, sim_duration,
        )
        time.sleep(sim_duration)
        logger.info("Simulated sputter coater: run complete")


# Type alias for callers that accept either implementation.
SputterOpsLike = SputterOps | SimulatedSputterOps