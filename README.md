# CAS_HMI

A small read-only overview of the CAS pen, fed from `GVLMain` over the CODESYS
OPC UA server. Shows pen oxygen, water level inside and outside the pen, and
the control signals going to the inlet pumps and the oxygen valves — each
trended over ten time constants of the loop it belongs to.

One dependency (`asyncua`), no CDN, no build step. Works offline.

---

## Run

```bash
pip install -r requirements.txt
python -m cashmi --url opc.tcp://<plc-host>:4840
```

It opens `http://127.0.0.1:8080/` in your browser. Useful flags:

| Flag | Default | |
|---|---|---|
| `--url` | `opc.tcp://localhost:4840` | CODESYS OPC UA endpoint |
| `--port` | `8080` | HMI port |
| `--host` | `127.0.0.1` | use `0.0.0.0` to reach it from another machine |
| `--period` | `1.0` | PLC poll period, seconds |
| `--user` / `--password` | — | if the server requires a login |
| `--no-browser` | | don't open a browser |

### Docker

The image runs the HMI bound to `0.0.0.0` with `--no-browser`. Everything
after the image name is passed to `cashmi`, so `--url` goes there:

```bash
docker build -t cas-hmi .
docker run -d --name cas-hmi -p 8080:8080 --restart unless-stopped cas-hmi --url opc.tcp://<plc-host>:4840
```

When the container runs on the PLC itself, use the host network and the
default `localhost` URL:

```bash
docker run -d --name cas-hmi --network host --restart unless-stopped cas-hmi
```

#### arm/v7 (32-bit ARM, e.g. WAGO PFC200, Raspberry Pi on a 32-bit OS)

Cross-build with buildx and move the image over as a file:

```bash
docker buildx build --platform linux/arm/v7 -t cas-hmi:armv7 --load .
docker save cas-hmi:armv7 -o cas-hmi-armv7.tar
# copy cas-hmi-armv7.tar to the device, then on the device:
docker load -i cas-hmi-armv7.tar
```

Docker Desktop has the QEMU emulation buildx needs. On a plain Linux build
host, install it once with
`docker run --privileged --rm tonistiigi/binfmt --install arm`.

PyPI has no arm/v7 wheel for `cryptography` (an `asyncua` dependency), so on
32-bit ARM the Dockerfile adds [piwheels](https://www.piwheels.org) as an
extra index and installs its prebuilt `armv7l` wheel. No Rust toolchain is
needed. If piwheels hasn't built the newest `cryptography` yet, pip falls back
to the newest version that has a wheel.

Building natively on the device works with the same Dockerfile
(`docker build -t cas-hmi .`). The exception is a 32-bit userland on a 64-bit
kernel, which reports `armv8l` instead of `armv7l` so the piwheels wheels
don't match. Cross-build with buildx in that case.

### First run: check the tags

```bash
python -m cashmi.browse --url opc.tcp://<plc-host>:4840
```

This prints the node id it found for `GVLMain` and then resolves all 30 tags,
one line each, `OK` or `NOT FOUND` with the reason, followed by the three
oxygen sensors' `BadQuality` flags. Add `--tree` to dump the
published tree. If the PLC project is renamed or restructured, this is the
fastest way to see which paths in `cashmi/tags.py` need updating.

### Without a PLC

```bash
python tools/fake_plc.py --speed 8
python -m cashmi --url opc.tcp://127.0.0.1:4840/freeopcua/server/
```

`fake_plc.py` publishes the same `GVLMain` tree and moves it with simple
curves. It is **not** a plant model — `CasRioSim` is that — it just exists so
the page can be developed and checked without hardware.

---

## PLC prerequisites

1. **Symbol Configuration** in the CODESYS application must include `GVLMain`.
   Add the object if it is missing, tick `GVLMain`, then download.
2. The OPC UA server must be enabled on the PLC. On CODESYS Control Win it is
   on by default at port 4840; on a PFC200 check the runtime's settings.
3. The PLC must be in **RUN**.

Nothing here writes to the PLC. Every node is read-only, and the HMI never
issues a write — it is an overview, not an operator station.

### How the tags are found

Rather than a hard-coded node id like

```
ns=4;s=|var|CODESYS Control Win V3 x64.Application.GVLMain.Pen.Status.OverHeight
```

the client **browses** for a node named `GVLMain` and walks down by browse
name from there. So neither the namespace index nor the device name nor the
application name has to match anything. Array members are tried as both
`Inlet[0]` and `Inlet` → `[0]`, since CODESYS versions differ on that.

---

## Time bases

The two loops are three orders of magnitude apart, so one time base cannot
serve both. Both windows are **10 × the open-loop time constant** of the plant
model in `CasRioSim/casriosim/config.py`:

| Loop | Time constant | Window | Sample |
|---|---|---|---|
| Level | `h_nom·C / Kqo` = (0.06 × 1288.25) / 9.5238 ≈ **8.1 s** | **81 s** | 1 s |
| Oxygen | `V / Kqo` = 20 000 / 9.5238 ≈ **2100 s** (35 min) | **21 000 s** (5.8 h) | 10 s |

Water level, over-height, pump speed and valve position ride the fast base;
pen oxygen rides the slow one. Each axis picks a single unit from its own
window, so one chart never mixes `-88m` with `-5.8h`.

Buffers are in memory only. Restarting the HMI starts the trends over; it
does not log to disk.

---

## What is on the page

**Tiles** — pen oxygen, over-height, and one per inlet showing pump speed,
pump state, valve position and oxygen flow.

**Four fast trends** — water level (inside/outside), over-height, inlet pump
speed setpoint, oxygen control valve position.

**One slow trend** — pen oxygen concentration.

**Show table** gives every value as text, which is also the accessible path
for the series whose colour sits below 3:1 contrast on the light surface.

Colours are the first three categorical slots of the data-viz reference
palette (blue / orange / aqua), which are documented as validating all-pairs
for colour-vision deficiency in both light and dark. Inlet 1/2/3 keep the same
colour on every chart, so identity never moves. Dark mode is a selected set of
steps, not an inverted light mode, and follows the OS unless you use the
**Theme** button.

---

## Two values that will read zero

`Pen.Status.OverHeight` and `Pen.Status.PenOxygenLevel` are the process values
of the two pen-level PID loops — `InletSupervisor` passes both as
`ProcessValue`. A project-wide search finds **no assignment to either**. They
are read and never written, so on a real PLC both read 0 and both loops
regulate on nothing.

So the HMI does not depend on them. It reads the underlying instruments and
computes the same quantities client-side:

| Shown as | Computed from |
|---|---|
| Over-height, "from sensors (avg)" | `Inlet[i].Input.LevelInsidePen.Filtered` − `…LevelOutsidePen.Filtered`, averaged over the three inlets |
| Pen oxygen, "from sensors (avg, good quality)" | `Pen.InputInside.Orbit864Sensor[i].OxygenConcentration.Filtered`, averaged over the sensors whose `…OxygenConcentration.Status.BadQuality` is FALSE |

A sensor is `BadQuality` when its last Modbus poll failed, its data quality word
is not 0, or it is switched off in `PenConfig.Orbit864Config.Enabled`. That is
the same rule `PenInsideSensors` uses for the PLC's own average. Such a sensor
reads as "—" in the table and is left out of the average; the oxygen tile says
how many of the three are in it. If the flag does not resolve (a PLC project
without it), the value is shown unchecked and the log says so.

Both charts also plot the PLC's own value as a second series, so the moment
those get implemented the two lines should converge — and until then the flat
zero line is visible rather than silent.

The functional description §3.2.2 specifies the over-height calculation as
`DeltaH_avg = SUM(DeltaH_active) / count(active)`, with a per-sensor "active"
flag so a faulty sensor drops out of the average. `AnalogSensorConfigDUT` has
no such flag yet, so this HMI averages all three unconditionally. When the
flag arrives, the same rule should be applied here.

---

## Configuration

Tag paths live in `cashmi/tags.py`, one `Tag(key, path, label, unit, convert)`
per value, with `path` dotted relative to `GVLMain`. An optional `bad_path`
names a BOOL that, when TRUE, makes the value read as null. To add a signal, add a
`Tag` and reference its `key` in a chart's series list in
`cashmi/static/index.html`. Unit conversions (m → cm, 12-bit raw → %) are the
`convert` callable, applied once on the server side.

---

## Verification status

Verified against `tools/fake_plc.py` with asyncua 2.0.1 on Python 3.12:

- all 30 tags resolve by browse, including the `Inlet[0]` / `OxySystem[0]`
  array spellings
- the collector reconnects on its own with backoff after the server drops
- fast and slow buffers fill at the right rates and cap at 82 / 2101 points
- both charts, tiles, tooltips, table view and the light/dark toggle render
  correctly; the y-axis tick values were checked numerically, not by eye

**Not verified: a real CODESYS OPC UA server.** No PLC was reachable from this
machine, so the browse-based resolution has only been tested against the
asyncua server in `fake_plc.py`, which was built to mirror the CODESYS layout
but is not CODESYS. The array-member naming (`Inlet[0]` versus an indexed
child) is the most likely thing to differ — `python -m cashmi.browse --tree`
will show it immediately, and the resolver already tries both spellings.
