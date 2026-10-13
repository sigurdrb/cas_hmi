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
    """AnalogValveOutputDUT.RawPosition: the PLC writes 0..65535 to the 750-563
    (factory setting: 4..20 mA over the full 16-bit word) -> 0..100 % opening."""
    return value / 65535.0 * 100.0


# --------------------------------------------------------------------------
# Pen-level values
# --------------------------------------------------------------------------

PEN_TAGS: List[Tag] = [
    # Pen averages are computed in the PLC (PenInsideSensors / PenOutsideSensors,
    # from the sensors that are enabled and of good quality); the HMI shows them
    # as they are and does no averaging of its own. Each comes with the PLC's
    # BadQuality flag for the average (no valid sensor left).
    #
    # Pen.Status.PenOxygenLevel is the oxygen controller's process value: the
    # mean O2 concentration [mg/L] of the inside sensors. Saturation [%] is
    # averaged too, for display only; the controller does not use it.
    Tag("pen_o2", "Pen.Status.PenOxygenLevel", "Pen O₂ (mean, PLC)", "mg/L",
        bad_path="Pen.InputInside.AverageOxygenInside.Status.BadQuality"),
    Tag("pen_o2_sat", "Pen.InputInside.AverageOxygenSaturationInside.Filtered",
        "Pen O₂ saturation (mean, PLC)", "%",
        bad_path="Pen.InputInside.AverageOxygenSaturationInside.Status.BadQuality"),
    # Pen.Status.OverHeight is the level controller's process value:
    # AverageLevelInside - AverageLevelOutside.
    Tag("pen_overheight", "Pen.Status.OverHeight", "Over-height (PLC)", "cm", _m_to_cm,
        bad_path="Pen.InputInside.AverageOverHeight.Status.BadQuality"),
    Tag("level_inside_avg", "Pen.InputInside.AverageLevelInside.Filtered",
        "Level inside (mean, PLC)", "m",
        bad_path="Pen.InputInside.AverageLevelInside.Status.BadQuality"),
    Tag("level_outside_avg", "Pen.InputOutside.AverageLevelOutside.Filtered",
        "Level outside (mean, PLC)", "m",
        bad_path="Pen.InputOutside.AverageLevelOutside.Status.BadQuality"),

    Tag("level_sp", "Pen.Command.LevelSetpoint", "Level setpoint", "cm", _m_to_cm),
    Tag("o2_sp", "OxyController.Input.Setpoint", "Oxygen setpoint", "mg/L"),
    Tag("level_out", "LevelController.Output.OUT", "Level PID output", "rpm"),
    Tag("o2_out", "OxyController.Output.OUT", "Oxygen PID output", "%"),
] + [
    # Each sensor on its own, for the table and the sensor count. BadQuality is
    # TRUE when its last Modbus poll failed, its data quality word is not 0, or
    # it is switched off in PenConfig.Orbit864Config.Enabled -- the sensors
    # PenInsideSensors leaves out of the PLC's average.
    Tag(
        f"o2_sensor_{i + 1}",
        f"Pen.InputInside.Orbit864Sensor[{i}].OxygenConcentration.Filtered",
        f"O₂ sensor {i + 1} inside",
        "mg/L",
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


#: InletPumpState enum (ENUM.InletPumpState in the PLC), value -> name.
#: The values are not consecutive: Unavailable is 9.
PUMP_STATE_NAMES = {
    0: "Available",
    1: "Starting",
    2: "Remote",
    3: "Stopping",
    4: "Hold",
    5: "Lockdown",
    9: "Unavailable",
}


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
SLOW_KEYS = {"pen_o2", "pen_o2_sat", "o2_sp"} | {
    f"o2_sensor_{i + 1}" for i in range(N_OXYSENSORS_INSIDE)
}
