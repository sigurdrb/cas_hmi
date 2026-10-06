"""The GVLMain variables the HMI reads, and the trend time bases.

Paths are dotted, relative to the GVLMain global variable list, exactly as they
read in the CODESYS project. `opc.Resolver` turns them into OPC UA nodes by
browsing, so nothing here depends on the namespace index or on the device name.

Everything is read-only. This HMI displays; it does not command.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

N_INLETS = 3
N_OXYSENSORS_INSIDE = 3


@dataclass(frozen=True)
class Tag:
    """One value shown on the HMI."""

    key: str  #: stable id used in the JSON API and the page
    path: str  #: dotted path below GVLMain
    label: str  #: what a person calls it
    unit: str
    #: Optional conversion from the PLC's engineering units to display units.
    convert: Optional[Callable[[float], float]] = None
    #: Optional dotted path to a BOOL that is TRUE when the value must not be
    #: trusted. The value then reads as null, so averages and trends skip it.
    bad_path: Optional[str] = None


def _m_to_cm(value: float) -> float:
    return value * 100.0


def _raw_to_pct(value: float) -> float:
    """AnalogValveOutputDUT.RawPosition is a 12-bit word, 0..4095 -> 0..100 %."""
    return value / 4095.0 * 100.0


# --------------------------------------------------------------------------
# Pen-level values
# --------------------------------------------------------------------------

PEN_TAGS: List[Tag] = [
    # Both of these are the process values the two pen-level PID loops read.
    # As of this writing neither is written anywhere in the project (they are
    # read in InletSupervisor and assigned nowhere), so on a real PLC they
    # will read 0. The HMI therefore also reads the underlying sensors and
    # shows both, so the gap is visible rather than silent. See README.
    Tag("pen_o2", "Pen.Status.PenOxygenLevel", "Pen oxygen (PLC)", "g/m³"),
    Tag("pen_overheight", "Pen.Status.OverHeight", "Over-height (PLC)", "cm", _m_to_cm),

    Tag("level_sp", "Pen.Command.LevelSetpoint", "Level setpoint", "cm", _m_to_cm),
    Tag("o2_sp", "OxyController.Input.Setpoint", "Oxygen setpoint", "g/m³"),
    Tag("level_out", "LevelController.Output.OUT", "Level PID output", "rpm"),
    Tag("o2_out", "OxyController.Output.OUT", "Oxygen PID output", "%"),
] + [
    # BadQuality is TRUE when the sensor's last Modbus poll failed, its data
    # quality word is not 0, or it is switched off in
    # PenConfig.Orbit864Config.Enabled -- the same rule PenInsideSensors uses
    # to leave a sensor out of the PLC's own average.
    Tag(
        f"o2_sensor_{i + 1}",
        f"Pen.InputInside.Orbit864Sensor[{i}].OxygenConcentration.Filtered",
        f"O₂ sensor {i + 1} inside",
        "g/m³",
        bad_path=f"Pen.InputInside.Orbit864Sensor[{i}].OxygenConcentration.Status.BadQuality",
    )
    for i in range(N_OXYSENSORS_INSIDE)
]


# --------------------------------------------------------------------------
# Per-inlet values
# --------------------------------------------------------------------------

def _inlet_tags(i: int) -> List[Tag]:
    """Tags for inlet `i` (0-based), as a pump paired with its oxygenator."""
    n = i + 1
    return [
        Tag(
            f"level_inside_{n}",
            f"Inlet[{i}].Input.LevelInsidePen.Filtered",
            f"Inside {n}",
            "m",
        ),
        Tag(
            f"level_outside_{n}",
            f"Inlet[{i}].Input.LevelOutsidePen.Filtered",
            f"Outside {n}",
            "m",
        ),
        Tag(
            f"pump_sp_{n}",
            f"Inlet[{i}].InletPump.Output.VelocitySetpoint",
            f"Pump {n} setpoint",
            "rpm",
        ),
        Tag(
            f"pump_actual_{n}",
            f"Inlet[{i}].InletPump.Status.ActualSpeed",
            f"Pump {n} actual",
            "rpm",
        ),
        Tag(
            f"pump_state_{n}",
            f"Inlet[{i}].InletPump.State",
            f"Pump {n} state",
            "",
        ),
        Tag(
            f"valve_{n}",
            f"OxySystem[{i}].OxyValve.Output.RawPosition",
            f"O₂ valve {n}",
            "%",
            _raw_to_pct,
        ),
        Tag(
            f"oxyflow_{n}",
            f"OxySystem[{i}].Input.OxygenFlow.Filtered",
            f"O₂ flow {n}",
            "kg/h",
        ),
    ]


ALL_TAGS: List[Tag] = PEN_TAGS + [t for i in range(N_INLETS) for t in _inlet_tags(i)]

BY_KEY = {t.key: t for t in ALL_TAGS}


#: InletPumpState enum, for turning the numeric state into words.
PUMP_STATE_NAMES = [
    "Unavailable",
    "Available",
    "Starting",
    "Remote",
    "Hold",
    "Stopping",
    "Lockdown",
]


# --------------------------------------------------------------------------
# Trend time bases
# --------------------------------------------------------------------------
#
# The two loops are three orders of magnitude apart, so one time base cannot
# serve both. Both windows are 10 x the open-loop time constant of the plant
# model (see CasRioSim/casriosim/config.py):
#
#   level   tau = (h_nom * C) / Kqo = (0.06 * 1288.25) / 9.5238  ~=    8.1 s
#   oxygen  tau = V / Kqo           = 20000 / 9.5238             ~= 2100   s
#
TAU_LEVEL_S = 0.06 * 1288.25 / 9.5238  # ~= 8.1 s
TAU_OXYGEN_S = 20_000.0 / 9.5238  # ~= 2100 s

#: Fast trend: levels, over-height, pump speeds, valve positions.
FAST_WINDOW_S = round(10 * TAU_LEVEL_S)  # ~= 81 s
FAST_PERIOD_S = 1.0

#: Slow trend: pen oxygen concentration.
SLOW_WINDOW_S = round(10 * TAU_OXYGEN_S)  # ~= 21 000 s, about 5 h 50 min
SLOW_PERIOD_S = 10.0

FAST_POINTS = int(FAST_WINDOW_S / FAST_PERIOD_S) + 1
SLOW_POINTS = int(SLOW_WINDOW_S / SLOW_PERIOD_S) + 1

#: Which tags go on the slow trend; everything else rides the fast one.
SLOW_KEYS = {"pen_o2", "o2_sp"} | {
    f"o2_sensor_{i + 1}" for i in range(N_OXYSENSORS_INSIDE)
}
