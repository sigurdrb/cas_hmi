"""A stand-in OPC UA server publishing a GVLMain tree, for developing the HMI.

This is NOT a model of the plant -- CasRioSim does that. It publishes the same
variable names the CODESYS application does and moves them with simple curves,
so the page can be built and checked without a PLC.

    python tools/fake_plc.py            # opc.tcp://localhost:4840
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time

from asyncua import Server, ua

N_INLETS = 3


async def build(server: Server):
    idx = await server.register_namespace("CasFakePlc")
    objects = server.nodes.objects

    # Mirror the CODESYS shape: <Device>/Application/GVLMain/...
    device = await objects.add_object(idx, "ControlWinSim")
    application = await device.add_object(idx, "Application")
    gvl = await application.add_object(idx, "GVLMain")

    handles = {}

    async def folder(parent, name):
        return await parent.add_object(idx, name)

    async def var(parent, name, value, key):
        node = await parent.add_variable(idx, name, value)
        handles[key] = node
        return node

    # -- Pen -------------------------------------------------------------
    pen = await folder(gvl, "Pen")
    pen_status = await folder(pen, "Status")
    await var(pen_status, "PenOxygenLevel", 7.0, "pen_o2")
    await var(pen_status, "OverHeight", 0.0, "pen_overheight")
    pen_cmd = await folder(pen, "Command")
    await var(pen_cmd, "LevelSetpoint", 0.06, "level_sp")

    pen_inside = await folder(pen, "InputInside")
    for i in range(3):
        sensor = await folder(pen_inside, f"Orbit864Sensor[{i}]")
        conc = await folder(sensor, "OxygenConcentration")
        await var(conc, "Filtered", 7.0, f"o2_sensor_{i + 1}")

    # -- controllers -----------------------------------------------------
    for name, out_key, sp_key in (("LevelController", "level_out", None),
                                  ("OxyController", "o2_out", "o2_sp")):
        ctrl = await folder(gvl, name)
        out = await folder(ctrl, "Output")
        await var(out, "OUT", 0.0, out_key)
        inp = await folder(ctrl, "Input")
        if sp_key:
            await var(inp, "Setpoint", 9.0, sp_key)
        else:
            await var(inp, "Setpoint", 0.06, "unused_level_sp")

    # -- inlets ----------------------------------------------------------
    for i in range(N_INLETS):
        n = i + 1
        inlet = await folder(gvl, f"Inlet[{i}]")
        inp = await folder(inlet, "Input")
        for sensor, key in (("LevelInsidePen", f"level_inside_{n}"),
                            ("LevelOutsidePen", f"level_outside_{n}")):
            node = await folder(inp, sensor)
            await var(node, "Filtered", 1.0, key)

        pump = await folder(inlet, "InletPump")
        pump_out = await folder(pump, "Output")
        await var(pump_out, "VelocitySetpoint", 0.0, f"pump_sp_{n}")
        pump_status = await folder(pump, "Status")
        await var(pump_status, "ActualSpeed", 0.0, f"pump_actual_{n}")
        await var(pump, "State", 1, f"pump_state_{n}")

        oxy = await folder(gvl, f"OxySystem[{i}]")
        valve = await folder(oxy, "OxyValve")
        valve_out = await folder(valve, "Output")
        await var(valve_out, "RawPosition", ua.Variant(0, ua.VariantType.UInt16),
                  f"valve_{n}")
        oxy_in = await folder(oxy, "Input")
        flow = await folder(oxy_in, "OxygenFlow")
        await var(flow, "Filtered", 0.0, f"oxyflow_{n}")

    for node in handles.values():
        await node.set_writable()
    return handles


async def drive(handles, speed: float):
    """Move the values so the trends have something to show."""
    t0 = time.time()
    while True:
        t = (time.time() - t0) * speed

        # pumps ramp up over the first 30 s and then breathe slightly
        base = min(1.0, t / 30.0) * 75.0
        for n in (1, 2, 3):
            rpm = base + 6.0 * math.sin(t / 12.0 + n)
            if n == 3 and t > 120:  # inlet 3 stops after two minutes
                rpm = 0.0
            await handles[f"pump_sp_{n}"].write_value(float(rpm))
            await handles[f"pump_actual_{n}"].write_value(float(rpm * 0.98))
            await handles[f"pump_state_{n}"].write_value(3 if rpm > 0 else 1)

            pct = 0.0 if rpm <= 0 else 45.0 + 25.0 * math.sin(t / 40.0 + n)
            await handles[f"valve_{n}"].write_value(
                ua.Variant(int(pct / 100.0 * 4095), ua.VariantType.UInt16))
            await handles[f"oxyflow_{n}"].write_value(float(pct / 100.0 * 104.2))

        # level responds to pump speed with the level time constant
        over = 0.06 * (base / 75.0) + 0.004 * math.sin(t / 9.0)
        outside = 1.0 + 0.01 * math.sin(t / 25.0)
        for n in (1, 2, 3):
            await handles[f"level_outside_{n}"].write_value(float(outside))
            await handles[f"level_inside_{n}"].write_value(float(outside + over))
        await handles["pen_overheight"].write_value(float(over))

        # oxygen drifts on its own, much slower time constant
        o2 = 7.0 + 2.0 * (1.0 - math.exp(-t / 2100.0)) + 0.05 * math.sin(t / 300.0)
        await handles["pen_o2"].write_value(float(o2))
        for i in range(3):
            # three sensors, slightly apart, as real probes in one pen would be
            await handles[f"o2_sensor_{i + 1}"].write_value(
                float(o2 + 0.06 * math.sin(t / 180.0 + i * 2.1)))
        await handles["level_out"].write_value(float(base))
        await handles["o2_out"].write_value(float(45.0))

        await asyncio.sleep(0.5)


async def amain(args) -> int:
    server = Server()
    await server.init()
    server.set_endpoint(args.endpoint)
    server.set_server_name("CAS fake PLC")
    handles = await build(server)
    async with server:
        print(f"fake PLC on {args.endpoint} -- {len(handles)} variables under GVLMain")
        await drive(handles, args.speed)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--endpoint", default="opc.tcp://0.0.0.0:4840/freeopcua/server/")
    p.add_argument("--speed", type=float, default=1.0)
    args = p.parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
