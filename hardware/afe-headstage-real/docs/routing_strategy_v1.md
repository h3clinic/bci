# v1 RHD2132 Headstage — Routing Strategy

**Rev:** 3.10 · **Date:** 2026-02-28 · **Status:** REF_ELEC routed & hardened; Bundle C nested-L topology locked (Phases 1–4); bottom fan deferred

This document defines the routing strategy before any track is drawn.
It is the binding reference for layer assignments, via policy, noise
separation constraints, and routing order. Every rule here is a
constraint, not a suggestion.

---

## 0. Non-Negotiables

These three rules override everything else. If a routing decision
conflicts with any of them, the routing decision is wrong.

1. **Inner layers are sacred ground planes.**
   In1.Cu and In2.Cu carry no routed traces. No "just this one signal."
   No power stubs. No escape routes. Continuous return path beats
   convenience. Via antipads are the only allowed discontinuities.

2. **Electrode nets never share corridors with SPI or power.**
   If an electrode trace and a SPI/power trace would occupy the same
   routing channel on the same layer, you don't "add spacing." You
   **change topology** — move the non-electrode signal to a different
   layer, a different path, or a different region of the board.

3. **No long parallelism between SCLK and any electrode run.**
   SCLK is the aggressor. Treat it like RF. It must be short,
   contained, and referenced to ground. If SCLK runs alongside the
   electrode corridor for any meaningful length (>1mm), the routing
   is rejected.

---

## 1. Physical Context

### 1.1 Board and placement

```
Board: 30 × 24 mm, 4-layer, 1.6mm FR4, ENIG

          NORTH (cable exit)
    ┌──────────────────────────────┐  y=0
    │     TP1   TP4                │
    │     TP2  J1 (PZN-12) TP5    │  y=3.5  SPI connector
    │     TP3  R2 R3 R4    TP6    │  y=6.0  SPI series R
    │                              │
    │  C2 C1 FB1 C3 C4             │  y=8.5  Power domain
    │                              │
    │ TP7  R1                      │  y=14-15 REF bias
    │ TP8  C5  ┌────────┐  C6     │
    │ TP9      │   U1   │  C7     │  y=14  RHD2132 (8×8mm)
    │ TP10     └────────┘         │         pads at ±4.05mm
    │                              │
    │       J5 (A79024-001)        │  y=21.5 Electrode connector
    └──────────────────────────────┘  y=24
          SOUTH (MEA connection)
```

### 1.2 U1 (RHD2132 QFN-56) pad geography

Pin 1 is IN8, CCW numbering. 14 pads per side, 0.5mm pitch.

| Side   | Pads    | Signals | Offset from center |
|--------|---------|---------|-------------------|
| Left   | 1–14    | IN8..IN0, REF, GND×2, VDD1, AUX×3 | x = −4.05mm |
| Bottom | 15–28   | GND, SPI (CS±,SCLK±,MOSI±,MISO±), VDD2, AUXOUT, ADC_REF | y = +4.05mm |
| Right  | 29–42   | GND, LVDS_EN, VDD3, VESD, ELEC_TEST, IN31..IN23 | x = +4.05mm |
| Top    | 43–56   | IN22..IN9 | y = −4.05mm |
| EP     | center  | GND | 4.8×4.8mm |

Electrode inputs exit U1 on **three sides** (left, right, top).
SPI exits only on the **bottom** side. This is the key geometric fact
that makes clean separation possible.

### 1.3 Connectors

**J5** (electrode): 36-pin nano-strip, ~13.2mm wide, centered at y=21.5.
Dual-row: **T row = north** (y=19.34, odd pins), **B row = south** (y=20.48, even pins).
Pins 1–32 = CH0–CH31, pin 33 = REF_ELEC (T row, x≈19.8), pin 34/36 = GND,
pin 35 = ELEC_TEST (T row, x≈20.4).
Distance U1→J5: 7.5mm center-to-center, ~3.5mm pad-to-pad.

**J1** (SPI/power): PZN-12, ~4.4mm wide, centered at y=3.5.
Pins 1–4: MISO, MOSI, SCLK, CS. Pins 5–6/12: GND. Pins 7–10: power.
Distance U1→J1: 10.5mm center-to-center.

---

## 2. Layer Stack Commitment

| Layer  | Function | Copper | Dielectric below |
|--------|----------|--------|------------------|
| F.Cu   | Electrodes (primary) + local power stubs + passives | 35µm | 0.2mm prepreg |
| In1.Cu | **Solid GND plane** — no splits, no traces, no exceptions | 35µm | 1.065mm core |
| In2.Cu | **Solid GND plane** — no splits, no traces, no exceptions | 35µm | 0.2mm prepreg |
| B.Cu   | SPI (primary) + non-sensitive overflow | 35µm | — |

### 2.1 Why GND+GND (not GND+power)

The question was asked and answered:

> Are you committing to GND+GND inner layers, or reserving an inner
> layer for power?

**GND+GND. Committed.**

Justification:
- Board is 30×24mm. Total current draw ≤15mA. +3V3 and AVDD route
  as F.Cu traces without voltage drop or IR concerns.
- Electrode signals need low-inductance return paths through In1.Cu.
  A continuous GND plane 0.2mm below F.Cu provides this.
- SPI signals on B.Cu need their own return path through In2.Cu.
  A second GND plane provides this.
- Dual-GND stack creates a shielding sandwich: F.Cu electrodes are
  isolated from B.Cu SPI by 1.465mm of dielectric + two copper
  planes. Coupling attenuation >60dB at 25MHz.
- A power plane would require splits (AVDD vs +3V3 vs ADC_ref),
  creating return-path discontinuities exactly where you need
  continuous ground. Not worth it at this current level.

### 2.2 Plane integrity rules

1. In1.Cu and In2.Cu are unbroken GND pours covering the full board.
2. Via antipads are the only discontinuities. No via cluster may
   create an antipad gap >1mm across in the electrode corridor.
3. No power pours on any layer. +3V3 and AVDD route as traces.

---

## 3. Board Zones

The board is partitioned into three zones. The boundaries are enforced
by routing discipline and GND stitching.

```
    y=0  ┌─────────────────────────────┐
         │                             │
         │     ZONE B — Digital        │  J1, R2-R4 (SPI domain)
         │     (SPI on B.Cu)           │
    y~8  ├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┤
         │     ZONE C — Power island   │  FB1, C1-C4 (power domain)
         │     (+3V3/AVDD on F.Cu)     │
    y~10 ├─────────────────────────────┤  ← GND stitching barrier
         │                             │
         │     ZONE A — Electrode      │  U1, C5-C7, R1, J5
         │     corridor (F.Cu)         │  (quiet highway)
         │                             │
    y=24 └─────────────────────────────┘
```

### 3.1 Zone A — Electrode corridor (U1 ↔ J5)

The quiet highway. All 34 electrode-class nets route here on F.Cu.
- Keep traces **direct**. No meanders.
- Keep spacing **wide** (0.3mm trace-to-trace minimum).
- No SPI trace enters this zone on any layer.
- No power trace enters this zone except U1 VDD pad connections
  and their immediately adjacent bypass cap stubs.

### 3.2 Zone B — Digital corridor (J1 ↔ U1 bottom pads)

SPI signals route here, primarily on B.Cu.
- SPI bundle stays tight and short.
- Does not snake through or under the electrode corridor.
- SCLK is treated as the dominant aggressor.

### 3.3 Zone C — Power island (FB1 and decoupling)

+3V3 enters from J1, filters through FB1 to AVDD, feeds local
decoupling caps. All on F.Cu with short trunk + short branches.
- Keep tight and local. Don't loop AVDD around the chip.
- Bypass cap GND pads get direct vias to In1.Cu.

### 3.4 Zone boundary enforcement

The barrier between Zone A and Zones B/C is enforced by:
- GND stitching vias across the full board width at y ≈ 10mm
- No SPI or power trace crossing that boundary on any layer
- SPI traces on B.Cu terminate at U1 bottom-side pads and do not
  extend south into Zone A

---

## 4. Electrode Routing (CH0–CH31 + REF_ELEC + ELEC_TEST)

### 4.1 REF_ELEC — dedicated crossing trunk (not a lane)

REF_ELEC is the reference electrode bias path. It sets the DC
operating point for all 32 input amplifiers. Noise on REF_ELEC
appears as common-mode interference on every channel.

**REF_ELEC is NOT part of Bundle A.** It is not embedded in the
CH lane ordering. It is its own class of net, routed as a dedicated
shielded trunk that crosses the electrode corridor once.

**Physical reality:** U1 pad 10 is on the **left edge, north half**
(x = 10.95, y = 12.75). This is *above* all nine Bundle A electrode
pads (CH8 at y=17.25 down to CH0 at y=13.25). R1 (10 kΩ REF bias,
pin 2 = REF_ELEC) is at (9.5, 15.0) — on the same net, west of U1.
J5 pin 33 is on the east side (x ≈ 19.8, **T row / north**, y = 19.34).
REF_ELEC must traverse ~8.8 mm laterally and ~6.6 mm vertically.
"Sacred" does not mean "short." It means quiet, shielded, and not
sharing coupling geometry with CH traces or SPI. With two solid GND
inner planes, an F.Cu trace that crosses at ~90° and avoids parallel
adjacency is absolutely fine.

**Route:**
1. Exit U1 pad 10 (x = 10.95, y = 12.75) heading west.
2. Route to R1 pin 2 at (9.5, 15.0) — short south-west stub.
   **R1 must be connected first**, before anything else blocks it.
3. From R1, go **west** to x ≈ 8.3 (clearing C5 pad 1 west edge
   at x = 8.77 with ≥0.3mm margin), then **south** past C5 (y = 17.0).
4. Once south of C5 copper (below y ≈ 17.30), turn **east** and
   cross the electrode corridor above the U1↔J5 compression band.
5. At the east side (x ≈ 19.8), turn south and drop down to
   J5 pin 33 (T row, y = 19.34) from the north.

**Crossing band target: y ≈ 17.0–17.6.** Goal: cross above the
U1↔J5 compression band (bottom pad tips at y = 17.60, J5 T row
at y = 19.34) and below any corridor settle zone. Acceptance
criteria: one crossing, no >3mm parallel adjacency to CH lanes,
no vias.

**Obstacle map in the crossing band** (hard-checked from generator):

| Obstacle | Copper extent (x) | Copper extent (y) | Constraint |
|----------|-------------------|-------------------|------------|
| C5 (0402 AVDD bypass) | [8.77, 10.23] | [16.70, 17.30] | West approach must pass west of x=8.77 or south of y=17.30 |
| Left pad 1 / CH8 | [10.50, 11.40] | [17.125, 17.375] | Crossing must clear by ≥0.3mm in y |
| Left pad 2 / CH7 | [10.50, 11.40] | [16.625, 16.875] | Below crossing band — not blocking |
| Bottom pads 15–28 tips | [11.625, 18.375] | [17.60, 18.50] | Central zone ceiling: y ≤ 17.525 for 0.3mm clearance |
| Right pad 42 / CH23 | [18.60, 19.50] | [17.125, 17.375] | Mirror of pad 1 constraint |
| Right pad 41 / CH24 | [18.60, 19.50] | [16.625, 16.875] | Below crossing band — not blocking |
| EP GND pad | [12.60, 17.40] | [11.60, 16.40] | Ends at y=16.40 — clear of crossing band |

**Shaped crossing — not a band crossing.** There is no single
constant-Y REF_ELEC crossing that meets ELECTRODE clearance against
both the U1 bottom pad tips and the left/right edge pads. The
numbers are incompatible: clearing pad 1/42 requires y ≥ 17.75,
while clearing bottom pad tips requires y ≤ 17.525. The x-zone
gaps between them (~0.225 mm) are physically unusable for a
class-compliant jog (need ~0.75 mm for trace + clearances).

**Therefore REF_ELEC must cross using a diagonal or multi-segment
path that changes Y as X changes.** One conceptual crossing of the
corridor, but geometrically a shaped traverse — entering the
central x-zone at a Y that clears bottom pad tips, and reaching
the flanks at a Y that clears pad 1 / pad 42. The exact shape is
resolved during interactive routing with DRC live feedback.

**"One crossing" definition:** REF_ELEC goes from the west
region to the east region exactly once. A shaped path (diagonal,
multi-segment) still counts as one crossing provided the trace
never doubles back west after entering the central corridor.
Allowed: one conceptual traverse, even if composed of angled
segments. Not allowed: weaving back and forth across the lane
bundle (multiple east-west passes), or running parallel inside
the lane field.

**Route acceptance criteria** (DRC-checkable pass/fail):

| # | Criterion | Pass condition |
|---|-----------|----------------|
| A1 | Max 2 vias | REF_ELEC net has ≤ 2 vias (single B.Cu hop) |
| A2 | No parallel adjacency | No segment > 3 mm parallel to any CH* trace within the corridor (x = 11–19) |
| A3 | ELECTRODE clearance | ≥ 0.30 mm edge-to-edge everywhere; target ≥ 0.50 mm to CH lanes where space exists (do not invent space) |
| A4 | R1 first-class | U1.10 → R1.2 routed before the trunk turns into the crossing |
| A5 | No bottom-pad intrusion | No REF_ELEC copper inside the U1 bottom-pad tip envelope: x = [11.625, 18.375], y = [17.60, 18.50] |
| A6 | One crossing | West-to-east traverse of the corridor exactly once (via B.Cu) |
| A7 | B.Cu hop only | B.Cu used only for the single REF_ELEC hop; no other signals on B.Cu |

- Keep away from SPI, power, and the EP via array.
- If REF_ELEC's crossing corridor conflicts with CH lane
  placement, the CH lanes shift. REF_ELEC does not bend to
  accommodate them.

**Anti-pattern (reject):** REF_ELEC routed inside the electrode
lane bundle for >3mm of parallel run alongside CH traces.

**Anti-pattern (reject):** REF_ELEC attempts a constant-Y east-run
across x = 11.6–18.4 in the y = 17.1–17.9 band. This is where
people will be tempted, but no constant Y in that range
simultaneously clears the bottom pad tips (y ≤ 17.525 needed) and
the flank pads (y ≥ 17.75 needed). The crossing **must** be shaped.

### 4.2 Channel grouping and escape strategy

**Group A — Left-side inputs (9 channels, F.Cu, 0 vias):**

| U1 pad | Signal | Escape direction |
|--------|--------|-----------------|
| 1 | CH8 | South along west half of board |
| 2 | CH7 | ↓ |
| 3 | CH6 | ↓ |
| 4 | CH5 | ↓ |
| 5 | CH4 | ↓ |
| 6 | CH3 | ↓ |
| 7 | CH2 | ↓ |
| 8 | CH1 | ↓ |
| 9 | CH0 | ↓ |

These pads face west and have a clear path south to J5. Route as
a radial fanout on F.Cu. No vias. No exceptions.

**Group B — Right-side inputs (9 channels, F.Cu, 0 vias):**

| U1 pad | Signal | Escape direction |
|--------|--------|-----------------|
| 34 | CH31 | South along east half of board |
| 35 | CH30 | ↓ |
| 36 | CH29 | ↓ |
| 37 | CH28 | ↓ |
| 38 | CH27 | ↓ |
| 39 | CH26 | ↓ |
| 40 | CH25 | ↓ |
| 41 | CH24 | ↓ |
| 42 | CH23 | ↓ |

Mirror of Group A on the east side. No vias. No exceptions.

**Group C — Top-side inputs (14 channels, F.Cu preferred, max 1 via pair):**

> **Pin-1 orientation assumption:** Pin 1 (IN8) is at the NW corner
> of the QFN-56 body. Numbering is counter-clockwise: west side 1–14,
> south side 15–28, east side 29–42, north side 43–56. All pad
> coordinates below derive from this orientation and
> `QFN56_PAD_CX = 4.05 mm` (frozen in `ref_elec_clearance_check.py`).

| U1 pad | Signal | pad_x (brd) | Escape side |
|--------|--------|-------------|------------|
| 43 | CH22 | 18.250 | East (route around U1 right side) |
| 44 | CH21 | 17.750 | East |
| 45 | CH20 | 17.250 | East |
| 46 | CH19 | 16.750 | East |
| 47 | CH18 | 16.250 | East |
| 48 | CH17 | 15.750 | East |
| 49 | CH16 | 15.250 | East |
| 50 | CH15 | 14.750 | West (route around U1 left side) |
| 51 | CH14 | 14.250 | West |
| 52 | CH13 | 13.750 | West |
| 53 | CH12 | 13.250 | West |
| 54 | CH11 | 12.750 | West |
| 55 | CH10 | 12.250 | West |
| 56 | CH9  | 11.750 | West |

These 14 pads exit U1's north side and must route **around** U1 to
reach J5 to the south. Split into two subgroups:

- **C-East (CH22–CH16, pads 43–49):** East-half pads (x > 15.0).
  Route east from pad, turn south along U1's right side, merge into
  the Group B corridor heading to J5.
- **C-West (CH15–CH9, pads 50–56):** West-half pads (x < 15.0).
  Route west from pad, turn south along U1's left side, merge into
  the Group A corridor heading to J5.

> **Rationale:** Each subgroup escapes toward its nearest board edge.
> Pin 43 (x = 18.250) is 3.250 mm east of center — routing it east is
> the natural shortest path. Routing it west (crossing center) would
> add ≥ 6.5 mm of header run and create avoidable lane crossings with
> the other subgroup. The standard assignment produces 0 lane crossings
> in the fan-out zone.

**Prefer F.Cu for the entire path.** If F.Cu is physically blocked
(by Group A/B traces or power stubs), the single allowed escape is:

> Via down to B.Cu → route south **within Zone A only** (not entering
> the SPI/power zones) → via back to F.Cu near J5.

The B.Cu segment is shielded above by In2.Cu (solid GND) and must
not run under or near any SPI trace. Max 1 via pair (down + back)
per electrode net.

#### 4.2.1 Bundle C perimeter geometry (LOCKED — Rev 3.10)

This subsection defines the physical geometry that Bundle C traces
must follow. All coordinates are brd-space (KiCad-origin offset:
brd_ox = 100.0, brd_oy = 68.58). Every number derives from frozen
constants in `ref_elec_clearance_check.py` and `gen_pcb_v1.py`.

##### 4.2.1a Assumptions block

These assumptions must be true for all geometry in this section.
If any assumption is violated, every number below is invalid.

1. **Pin-1 orientation:** IN8 at NW corner, CCW numbering
   (west 1–14, south 15–28, east 29–42, north 43–56).
2. **In1.Cu solid GND plane** is continuous under the full U1 region
   with no splits, traces, or power stubs (Non-Negotiable §0.1).
3. **Frozen constants** (from `ref_elec_clearance_check.py`):
   - `TRACE_W = 0.150 mm`, `CLR = 0.300 mm`
   - `KEEP = CLR + TRACE_W/2 = 0.375 mm` (pad edge → trace center)
   - `VIA_KEEP = VIA_R + CLR + TRACE_W/2 = 0.675 mm` (via center → trace center)
   - `LANE_PITCH = CLR + TRACE_W = 0.450 mm`
   - `VIA: 0.3 mm drill, 0.6 mm pad (radius 0.3 mm)`
   - `FP0402: PAD_SX=0.500, PAD_SY=0.600, PAD_CX=0.480`
   - `QFN56_PAD_LONG=0.900, QFN56_PAD_CX=4.050`
4. **Placement** (frozen in `gen_pcb_v1.py`):
   - U1: (15.000, 14.000), EP 4.8×4.8 mm
   - R1: (9.500, 15.000), C5: (9.500, 17.000) — 0402
   - C6: (21.000, 15.500), C7: (21.000, 17.000) — 0402
   - VIA1: (9.980, 12.750), VIA2: (19.7625, 18.200)
   - J5: (15.000, 21.500), 36-pin, pitch 0.635 mm

##### 4.2.1b Bundle definition

- **Channels:** CH9–CH22 (14 total)
- **U1 pins:** 43–56 (north side, QFN-56)
- **Pad row:** y = 9.950 (pad center), pad outer edge y = 9.500
- **Subgroups:**
  - C-East: CH22–CH16 (pins 43–49, x ∈ [15.250, 18.250]) — 7 lanes
  - C-West: CH15–CH9 (pins 50–56, x ∈ [11.750, 14.750]) — 7 lanes

**Why the corridor is blocked — EP proof:**

```
Corridor x-range:  [11.775, 18.225]  (width 6.450 mm)
EP KEEP x-range:   [12.225, 17.775]  (width 5.550 mm)
West strip:        [11.775, 12.225]  =  0.450 mm  →  1 lane, 0 mm margin
East strip:        [17.775, 18.225]  =  0.450 mm  →  1 lane, 0 mm margin
Max lanes at EP:   2 (vs 14 needed)  →  BLOCKED
```

Bundle C **cannot** descend through the U1 corridor. All 14 traces
must route around U1 via the west and east perimeter arcs.

##### 4.2.1c Escape topology — 4 phases (nested-L)

Each Bundle C trace follows this path from U1 to the bottom fan
boundary (y = BOTTOM_FAN_Y = 18.875):

```
Phase 1: PAD ESCAPE     — North from pad tip, through KEEP boundary
Phase 2: HEADER FAN     — Horizontal run (E or W) to align with descent lane
Phase 3: CORNER TURN    — 90° turn from header to descent corridor
Phase 4: SIDE DESCENT   — Vertical run south alongside U1 (uniform pack)
```

Phase 5 (BOTTOM FAN — descent endpoint to J5 pad) is **deferred**.
Polylines terminate at y = BOTTOM_FAN_Y. See §4.2.1i for the
geometric impossibility proof and deferral rationale.

**Nested-L topology (crossing-free guarantee):**

The header fan (Phase 2) and descent corridor (Phase 4) assignments
are **reversed** relative to the natural channel order:

```
Inner lanes (lane_idx 0, nearest U1 center):
  → LOWEST  header_y  (6.425 — furthest north in header zone)
  → LARGEST descent_x (24.805 east / 5.695 west — furthest from U1)

Outer lanes (lane_idx 6, nearest U1 corner):
  → HIGHEST header_y  (9.125 — U1 KEEP boundary)
  → SMALLEST descent_x (22.105 east / 8.395 west — nearest to U1)
```

This creates **nested L-shapes**: each lane's horizontal header
segment is shorter AND lower (further north) than the next outer
lane. No horizontal segment can cross any other lane's vertical
descent. The topology is proven to produce **zero crossings** across
all 14 channels.

> **E1 jog eliminated:** In the previous Rev 3.9 topology, E1 (CH16)
> descended at x = 19.875 (U1 KEEP right) and required a 45° jog to
> x = 22.105 before the C6 KEEP zone. Under the nested-L topology,
> E7 (CH22, lane_idx 0) is now the innermost lane with descent_x =
> 24.805 (furthest east). E1 (lane_idx 6) descends at the KEEP
> boundary x = 22.105 directly from its header_y = 9.125 — no jog
> needed because the lane ordering reversal places E1 at the
> innermost descent position (nearest to C6/C7 KEEP east edge).

**Phase 1 — Pad escape:**

Traces exit pads northward (y decreasing). The pad outer edge (north
tip) is at y = 9.500. The KEEP boundary is at y = 9.125. The escape
segment is 0.375 mm long, purely vertical.

**Phase 2 — Header fan zone:**

```
Header zone:  y ∈ [6.425, 9.125]
Lane pitch:   0.450 mm (CLR 0.30 + TRACE_W 0.15)
Lanes:        7 per subgroup
```

Under nested-L assignment, the **outermost** lane (lane_idx 6,
nearest U1 corner) runs at y = 9.125 (the KEEP boundary). Each
successive inner lane runs 0.450 mm further north. The **innermost**
lane (lane_idx 0, nearest U1 center) runs at y = 6.425.

C-West lanes run westward, C-East lanes run eastward, each to its
respective descent corridor x-position.

**Phase 3 — Corner turn:**

Each lane makes a 90° vertex where its horizontal header run meets
its vertical descent column. The nested-L topology guarantees that
corners never share x or y coordinates between lanes.

**Phase 4 — Side descent (with obstacle analysis):**

Vertical lanes running south alongside U1 from the corner turn to
y = BOTTOM_FAN_Y (18.875). Each side uses a **uniform pack** — no
mid-descent jog zones.

> **Critical requirement:** all lane positions in this section have
> been verified against every obstacle. No lane may be added or moved
> without re-running the full obstacle clearance analysis.

##### 4.2.1d Lane pack definitions

Both sides use uniform packs for the entire descent. This avoids
mid-descent jog zones and the lane-ordering inversions they create.

**C-West: uniform pack (7 lanes)**

```
Governing constraint: R1/C5 (0402, both at x = 9.500)
  R1 pad 1:  x ∈ [8.770, 9.270]  (center 9.020 = 9.500 − PAD_CX)
  R1.1 KEEP west = 8.770 − 0.375 = 8.395
  R1/C5 zone: y ∈ [14.325, 17.675]  (pad ± KEEP in y)
  W1–W3 nominal (9.305, 8.855, 8.405) ALL inside R1.1 KEEP [8.395, 9.645]

Decision: uniform pack for entire descent, W1 = R1.1 KEEP west = 8.395
  Also clears VIA1: W1 dx to VIA1 = 9.980 − 8.395 = 1.585 >> 0.675 ✓

Lane  descent_x  Derivation
W1    8.395      R1.1 KEEP west (= 8.770 − 0.375)
W2    7.945      W1 − LANE_PITCH
W3    7.495      W2 − LANE_PITCH
W4    7.045      W3 − LANE_PITCH
W5    6.595      W4 − LANE_PITCH
W6    6.145      W5 − LANE_PITCH
W7    5.695      W6 − LANE_PITCH

Board margin: W7 = 5.695 mm from west edge (0.000) → 5.695 mm ✓
```

**C-East: uniform pack (7 lanes)**

```
Governing constraint: C6/C7 (0402, both at x = 21.000) + VIA2 (19.7625, 18.200)

  C6.2/C7.2 KEEP east = 21.730 + 0.375 = 22.105
  C6/C7 zone: y ∈ [14.825, 17.675]

Decision: ALL 7 east lanes in uniform pack east of C6.2/C7.2 KEEP east.
  Under nested-L topology, E7 (lane_idx 6, outermost) descends at
  x = 22.105 directly — no jog needed (see §4.2.1c).

Lane  descent_x  Derivation
E1    22.105     C6.2 KEEP east (= 21.730 + 0.375)
E2    22.555     E1 + LANE_PITCH
E3    23.005     E2 + LANE_PITCH
E4    23.455     E3 + LANE_PITCH
E5    23.905     E4 + LANE_PITCH
E6    24.355     E5 + LANE_PITCH
E7    24.805     E6 + LANE_PITCH

Board margin: E7 = 24.805, east edge = 30.000 → 5.195 mm ✓
VIA2 clearance: E1 dx to VIA2 = 22.105 − 19.7625 = 2.343 >> 0.675 ✓
```

##### 4.2.1e Obstacle zone inventory

Three obstacle zones exist alongside U1. The uniform pack decisions
above ensure no lane passes through any of them.

**OBS-W: R1/C5 west zone**

```
Components: R1 at (9.500, 15.000), C5 at (9.500, 17.000)
Zone y-range: [14.325, 17.675]  (pad edge ± KEEP)
KEEP boundaries: west = 8.395, east = 10.605
Affected lanes (nominal): W1 (9.305), W2 (8.855), W3 (8.405)
Resolution: uniform pack at W1 = 8.395 clears entire zone
VIA1 note: VIA1 at (9.980, 12.750) is also cleared by the pack
  (W1 at 8.395 is 1.585 mm west of VIA1 center)
```

**OBS-E1: C6/C7 east zone**

```
Components: C6 at (21.000, 15.500), C7 at (21.000, 17.000)
Zone y-range: [14.825, 17.675]
KEEP boundaries: west = 19.895, east = 22.105
Between-pad gap: 0.460 mm → KEEP overlap (BLOCKED, no lane can pass between pads)
Affected lanes (nominal): ALL E1–E7 inside KEEP zone
Resolution: uniform pack at E1 = 22.105 routes ALL lanes east of zone
(See §4.2.1j for the pinch impossibility proof that motivates this.)
```

**OBS-E2: VIA2 bottom zone**

```
Via: VIA2 at (19.7625, 18.200), KEEP = 0.675 mm
Resolution: uniform east pack at x = 22.105 clears VIA2 by 2.343 mm
  No lane ever enters VIA2 KEEP zone.
(See §4.2.1j for the VIA2 overlap proof.)
```

##### 4.2.1f Lane mapping table (complete — nested-L topology)

Under the nested-L topology, header_y and descent_x are **reversed**
relative to the natural channel order: innermost lanes (nearest U1
center) get the lowest header_y and the largest descent_x. This
guarantees zero crossings in both the header fan and the descent
corridors.

J5 pin assignments are the **unique** zero-crossing permutations,
verified by brute-force search over all 5040 permutations per side.

**C-West (7 lanes):**

| Lane | CH  | U1 pin | pad_x  | header_y | descent_x | J5 pin |
|------|-----|--------|--------|----------|-----------|--------|
| W1   | CH15| 50     | 14.750 | 6.425    | 5.695     | 10     |
| W2   | CH14| 51     | 14.250 | 6.875    | 6.145     | 12     |
| W3   | CH13| 52     | 13.750 | 7.325    | 6.595     | 14     |
| W4   | CH12| 53     | 13.250 | 7.775    | 7.045     | 16     |
| W5   | CH11| 54     | 12.750 | 8.225    | 7.495     | 11     |
| W6   | CH10| 55     | 12.250 | 8.675    | 7.945     | 13     |
| W7   | CH9 | 56     | 11.750 | 9.125    | 8.395     | 15     |

Nested-L assignment: W1 (lane_idx 0, nearest center) gets the lowest
header_y (6.425) and the outermost descent_x (5.695). W7 (lane_idx 6,
nearest NW corner) gets the highest header_y (9.125) and the innermost
descent_x (8.395 = R1.1 KEEP west). Zero crossings.

**C-East (7 lanes):**

| Lane | CH  | U1 pin | pad_x  | header_y | descent_x | J5 pin |
|------|-----|--------|--------|----------|-----------|--------|
| E1   | CH16| 49     | 15.250 | 6.425    | 24.805    | 22     |
| E2   | CH17| 48     | 15.750 | 6.875    | 24.355    | 20     |
| E3   | CH18| 47     | 16.250 | 7.325    | 23.905    | 18     |
| E4   | CH19| 46     | 16.750 | 7.775    | 23.455    | 23     |
| E5   | CH20| 45     | 17.250 | 8.225    | 23.005    | 21     |
| E6   | CH21| 44     | 17.750 | 8.675    | 22.555    | 19     |
| E7   | CH22| 43     | 18.250 | 9.125    | 22.105    | 17     |

Nested-L assignment mirrors C-West: E1 (lane_idx 0, nearest center)
gets the lowest header_y (6.425) and the outermost descent_x (24.805).
E7 (lane_idx 6, nearest NE corner) gets the highest header_y (9.125)
and the innermost descent_x (22.105 = C6.2 KEEP east). Zero crossings.

> **E7 at C6/C7 boundary:** Under nested-L, E7 (not E1) is the lane
> at descent_x = 22.105 (C6.2 KEEP east). E7's header_y = 9.125
> means it turns south immediately at the KEEP boundary and descends
> through the C6/C7 zone at the KEEP-derived clearance. No jog needed.

**J5 pin mapping (bottom fan, deferred — see §4.2.1i):**

J5 pin assignments are frozen here for use by the future bottom-fan
generator. The assignments produce zero crossings when connecting
descent endpoints to J5 pads.

```
J5 center:    (15.000, 21.500)
J5 pitch:     0.635 mm, 18 columns, 2 rows
T row (north): y = 19.341 (odd pins)
B row (south): y = 20.484 (even pins)
Bundle C at J5: columns 5–12

  CH    J5 pin   J5 col   J5 col_x     J5 row
  CH9     10       5      12.1425      B (even)
  CH10    12       6      12.7775      B (even)
  CH11    11       6      12.7775      T (odd)
  CH12    16       8      14.0475      B (even)
  CH13    14       7      13.4125      B (even)
  CH14    15       8      14.0475      T (odd)
  CH15    13       7      13.4125      T (odd)
  CH16    22      11      15.9525      B (even)
  CH17    20      10      15.3175      B (even)
  CH18    18       9      14.6825      B (even)
  CH19    23      12      16.5875      T (odd)
  CH20    21      11      15.9525      T (odd)
  CH21    19      10      15.3175      T (odd)
  CH22    17       9      14.6825      T (odd)
```

Column x-values are authoritative, computed from `gen_pcb_v1.py`
`x_positions` array (connector center 15.000, pitch 0.635, 18 cols).

##### 4.2.1g Minimum radial distance

At every point along its path, each Bundle C trace center must
maintain ≥ KEEP (0.375 mm) from:

- All U1 pad edges (north, west, east sides)
- EP KEEP envelope
- VIA1 / VIA2 KEEP circles (0.675 mm radius)
- R1, C5, C6, C7 pad edges
- All other Bundle C traces (lane pitch ≥ 0.450 mm)
- Group A / Group B traces (same clearance rules)

The tightest constraints are:
- R1/C5 zone: W1 at exactly KEEP from R1.1 west edge (0.000 mm margin)
- C6/C7 zone: E1 at exactly KEEP from C6.2 east edge (0.000 mm margin)
- VIA2: nearest lane (E1 at 22.105) clears by 2.343 mm (massive margin)

##### 4.2.1h "Lanes established" target

All 14 Bundle C traces are considered "established" (ready for
Phase 4 descent) when they reach their descent x-position at:

```
y = 9.125  (= U1 KEEP top = U1_PAD_TOP − KEEP = 9.500 − 0.375)
```

At this y-coordinate, all lanes are in their vertical descent
columns with proper KEEP clearance from U1 north-side pads.
Everything above this line is header fan geometry; everything
below is side descent.

Under nested-L topology, all 14 lanes are established at y = 9.125
without exception. No lane requires a mid-descent jog.

Polylines terminate at y = BOTTOM_FAN_Y = 18.875 (U1 KEEP bottom).
Below this line is the bottom fan connector-entry zone (§4.2.1i).

##### 4.2.1i Bottom fan connector-entry zone (DEFERRED)

The bottom fan zone (y ∈ [18.875, 21.500]) connects the 14 descent
lane endpoints to their assigned J5 connector pads. This zone is
**not emitted** by `gen_bundle_c_route.py` because single-layer F.Cu
routing is **geometrically impossible** at LANE_PITCH spacing.

**Geometric impossibility proof:**

The 7 east descent lanes are spaced at LANE_PITCH = 0.450 mm
(x ∈ [22.105, 24.805]) and must converge to J5 columns 9–12
(x ∈ [14.6825, 16.5875]). The convergence creates diagonal
fan segments whose perpendicular centerline spacing is:

```
Perpendicular spacing = LANE_PITCH × sin(θ)
where θ = angle of diagonal from horizontal

T-row diagonals (y-span ≈ 0.466 mm over dx ≈ 7–10 mm):
  θ ≈ arctan(0.466/8) ≈ 3.3°
  Perpendicular spacing ≈ 0.450 × sin(3.3°) ≈ 0.026 mm
  Required copper gap = 0.026 − 0.150 = −0.124 mm (OVERLAP)

B-row diagonals (y-span ≈ 1.609 mm over dx ≈ 7–10 mm):
  θ ≈ arctan(1.609/8) ≈ 11.4°
  Perpendicular spacing ≈ 0.450 × sin(11.4°) ≈ 0.089 mm
  Required copper gap = 0.089 − 0.150 = −0.061 mm (OVERLAP)

Both cases: perpendicular spacing < TRACE_W (0.150 mm)
  → traces physically overlap → DRC violation on any single layer
```

**Manhattan fan also fails:**

A Manhattan (orthogonal) bottom fan would require stacking 4 T-row
lanes (0.466 mm y-span) at LANE_PITCH = 0.450 mm, needing:
  3 × 0.450 = 1.350 mm (but only 0.466 mm available)

**Resolution:**

Bottom fan requires either:
  (a) **Interactive routing locked by constraints** — KiCad interactive
      router with B.Cu via transitions at staggered y-positions, each
      lane routed to its J5 column on B.Cu and viaed back at the pad; or
  (b) **A separate solver** — monotone Manhattan with bounded angles +
      spacing constraints, managing multi-layer transitions with proper
      via keep-out analysis.

Current generator intentionally terminates at BOTTOM_FAN_Y. This is
not optional future work — the bottom fan cannot ship without one of
the above solutions.

J5 pin assignments (§4.2.1f) are frozen and verified crossing-free
for the eventual bottom fan implementation.

##### 4.2.1j Rationale — rejected options

This section documents design alternatives that were analyzed and
rejected. It is historical context only; the current design does NOT
depend on any of these rejected paths.

**Rejected: E1 descent at x = 19.875 (U1 KEEP right)**

Prior to Rev 3.10, E1 (CH16) was planned to descend at x = 19.875
(U1 KEEP boundary) and jog east to 22.105 before C6 KEEP. This fails:

```
E1 pinch analysis:
  C6 pad 1:  x ∈ [20.270, 20.770]  (center 20.520 = 21.000 − PAD_CX)
  C6 pad 2:  x ∈ [21.230, 21.730]  (center 21.480 = 21.000 + PAD_CX)
  Between-pad gap: 21.230 − 20.770 = 0.460 mm → KEEP overlap → BLOCKED

  U1 KEEP right = 19.875
  C6.1 KEEP west = 20.270 − 0.375 = 19.895
  Gap = 19.895 − 19.875 = 0.020 mm (sub-manufacturing tolerance, 20 µm)
  Edge-to-edge: 0.320 mm ≥ 0.300 mm (technically DRC-legal but NOT relied upon)

  VIA2 overlap zone y ∈ [17.538, 17.675]: E1 at 19.875 violates VIA2
  KEEP (dist = 0.537 mm < 0.675 mm) and cannot jog east (C7 blocks
  at x = 19.895). F.Cu routing at x = 19.875 through this zone is
  IMPOSSIBLE.
```

Resolution: nested-L topology (Rev 3.10) eliminates the E1 jog entirely.
E7 (outermost, lane_idx 6) now occupies x = 22.105, descending directly
from header_y = 9.125. No lane uses x = 19.875.

**Rejected: VIA2 overlap at x = 19.875**

```
VIA2 at (19.7625, 18.200), KEEP = 0.675 mm
Zone y-range (for x = 19.875): [17.534, 18.866]
Overlap with OBS-E1: y ∈ [17.534, 17.675] — both zones active
  At y = 17.538, VIA2 arc requires x > 19.895 but C7 blocks → IMPOSSIBLE
```

Resolution: uniform east pack at x ≥ 22.105 clears VIA2 by 2.343 mm.

**ELEC_TEST:** U1 pad 33 (right side) → south on F.Cu → J5 pin 35.
Zero vias.

### 4.3 Electrode routing rules

1. **Primary layer:** F.Cu.
2. **Overflow layer:** B.Cu, for Group C only, within Zone A only.
3. **Track width:** 0.15mm (ELECTRODE net class).
4. **Trace-to-trace clearance:** 0.3mm minimum.
5. **Via budget:** 0 for Groups A, B, REF_ELEC, ELEC_TEST.
   Max 1 via pair for Group C if physically necessary.
   Via drill 0.2mm, pad 0.4mm.
6. **No crossing:** No SPI or power trace may cross an electrode
   trace on the same layer. If they must cross on different layers,
   the crossing is perpendicular (minimizing coupling length).
7. **Separation from SPI:** ≥0.5mm edge-to-edge on any layer.
8. **Separation from power:** ≥0.5mm edge-to-edge, except for U1
   VDD pad connections and immediately adjacent bypass caps.
9. **Geometry:** Radial fanout from U1 into clean corridors to J5.
   No meanders. No serpentine. No length matching needed.

### 4.4 Guard trace policy (conservative)

Do **not** blanket-guard every electrode trace. Over-guarding creates
capacitive loading and couples noise into the guard network.

Guard traces are permitted only:
- On the **outermost** electrode traces (CH0 on west edge, CH31 on
  east edge) if they run near the board edge for >5mm.
- At the **electrode-digital boundary** (y ≈ 10mm) if electrode
  traces approach SPI territory.

Guard rules when used:
- GND guard trace, 0.2mm width, on F.Cu.
- Connected to In1.Cu via stitching vias every 3mm.
- Guard trace does not enter the space between electrode traces.

### 4.5 Return path model

```
Electrode → J5 pin → CH trace (F.Cu) → U1 input amplifier
                                        → internal diff pair
                                        → referenced to REF (pad 10)
                                        → R1 → GND
                                        → In1.Cu (solid GND plane)
                                        → J5 pins 34/36 (GND)
                                        → MEA ground reference
```

The critical return path segment is **In1.Cu directly beneath the
F.Cu electrode traces**. This is why In1.Cu must be unbroken in the
electrode corridor.

If an electrode trace vias to B.Cu (Group C overflow), the return
current shifts to In2.Cu (also solid GND). Return path continuity
is maintained.

---

## 5. SPI Routing (CS, SCLK, MOSI, MISO and *_J variants)

### 5.1 Signal path

```
J1.4 (CS_J)   → R2 → CS   → U1.19 (CS+)
J1.3 (SCLK_J) → R3 → SCLK → U1.21 (SCLK+)
J1.2 (MOSI_J) → R4 → MOSI → U1.23 (MOSI+)
J1.1 (MISO)   → ────────── → U1.25 (MISO+)   [no series R]
```

### 5.2 Layer and corridor

- **Primary layer:** B.Cu.
- **Corridor:** Center of board (x ≈ 12–18mm), between J1 (y=3.5)
  and U1 bottom pads (y≈18). Entirely in Zones B and C.
- SPI signals via from F.Cu (where R2–R4 are soldered) to B.Cu,
  route south on B.Cu, via back to F.Cu at U1 bottom-side pads.
- **SPI traces do not extend south of U1 bottom pads into Zone A.**

### 5.3 Bundle order

Route the SPI bundle with **SCLK in the middle**, surrounded by
MOSI and MISO on either side, with CS on the outside:

```
(west)  CS — MOSI — SCLK — MISO  (east)
```

or any permutation that keeps SCLK **interior** to the bundle, not
on the edge nearest the electrode corridor. SCLK is the aggressor;
burying it inside the bundle minimizes its radiation to the outside.

### 5.4 SCLK containment

- No SCLK run parallel to any electrode trace for >1mm on any layer.
- SCLK on B.Cu must not run directly beneath electrode traces on F.Cu
  in the corridor between U1 and J5.
- Keep SCLK trace as short as practical (R3 → B.Cu via → U1.21).

### 5.5 LVDS GND ties

U1 pads 18, 20, 22, 24 (CS−, SCLK−, MOSI−, MISO−) are tied to GND
in CMOS mode. Route short stubs (<1mm) from these pads to the nearest
GND connection — either the EP GND or a dedicated via to In1.Cu.

### 5.6 Return path

SPI return current flows through In2.Cu (solid GND directly beneath
B.Cu). Since In2.Cu has no splits, the return path is continuous for
all SPI signals. Avoid routing SPI traces over voids that could be
created by aggressive local copper pours on B.Cu.

---

## 6. Power Routing (+3V3, AVDD, ADC_ref)

### 6.1 +3V3

Short trunk + short branches. Not a long snake.

```
J1 pins 7,8 → short run → C1 (100nF) → C2 (1µF) → FB1 pin 1
```

All on F.Cu in Zone C. Track width ≥0.4mm.

### 6.2 AVDD

Tight distribution. Do not loop around the chip.

```
FB1 pin 2 (AVDD) → C3 (100nF)  [immediately adjacent]
                 → C4 (1µF)    [immediately adjacent]
                 → C5 (100nF)  [near U1 pad 13/VDD1, west]
                 → C6 (100nF)  [near U1 pad 26/VDD2, south-east]
                 → U1 pad 31/VDD3 [east, short trace]
```

Star topology: traces radiate from FB1 pin 2. Each branch is a
direct shot — no daisy-chaining AVDD through multiple caps in series.
Track width ≥0.4mm. All on F.Cu.

### 6.3 ADC_ref

ADC_ref is a sensitive analog reference. Route like an analog signal:

- C7 (10nF) connects U1 pad 28 (bottom side) to GND.
- Keep trace short: ≤3mm pad-to-pad.
- Route on F.Cu, isolated from SPI traces.
- If ADC_ref must cross anything, it crosses a GND region, not
  a digital signal.
- C7's GND pad gets a direct via to In1.Cu immediately adjacent
  to the pad.

### 6.4 Bypass capacitor loop area rule

Every bypass cap (C1–C7) must have **minimal loop area** between its
power pin and its GND pin:

- GND via placed directly at the cap's GND pad (≤1mm from pad center).
- Power trace as short as possible from the source.
- No routing the power side of a cap through a long detour.

This is the most important power layout rule. Low loop area = low
parasitic inductance = effective decoupling.

### 6.5 Power separation from electrodes

Power traces must maintain ≥0.5mm edge-to-edge clearance from any
electrode trace. Route power through Zone C (y < 10mm) and along
board edges. The only power traces in Zone A are the short stubs
connecting C5/C6 to U1 VDD pads.

---

## 7. Via Policy

### 7.1 Via budget per domain

| Domain | Drill | Pad | Max per net | Layer transition |
|--------|-------|-----|-------------|------------------|
| Electrode Groups A, B | — | — | **0** | F.Cu only |
| Electrode Group C (overflow) | 0.2mm | 0.4mm | **1 pair** | F.Cu→B.Cu→F.Cu |
| REF_ELEC | — | — | **0** | F.Cu only |
| ELEC_TEST | — | — | **0** | F.Cu only |
| SPI signals | 0.3mm | 0.6mm | 2 | F.Cu→B.Cu→F.Cu |
| Power (cap GND) | 0.3mm | 0.6mm | 1 per cap | F.Cu→In1.Cu |
| GND stitching | 0.3mm | 0.6mm | N/A | F.Cu→In1.Cu |
| EP thermal | 0.3mm | 0.6mm | 9 (locked) | F.Cu→B.Cu (tented) |

### 7.2 Electrode via rule (committed)

> **Max 1 via pair per electrode net.** 0 is the target. 1 is the
> exception for Group C nets that physically cannot escape on F.Cu.
> This is enforced during routing, not after.

When an electrode via is used:
- The B.Cu segment stays within Zone A (south of y≈10mm).
- The B.Cu segment does not run under or near any SPI trace.
- Via placement avoids creating antipad clusters that fragment In1.Cu.

### 7.3 GND stitching plan

Place GND stitching vias (0.3mm drill, 0.6mm pad) at:

1. **Zone boundary (y ≈ 10mm):** Row of vias across board width,
   ~3mm spacing. Primary barrier between digital and analog zones.
2. **Electrode corridor edges:** 3–4 vias along west and east
   board edges, between U1 and J5.
3. **Around U1:** Ring of GND stitching vias around the EP thermal
   via array perimeter (supplement the 9 thermal vias).
4. **Near J1 and J5 GND pads:** 2–3 vias adjacent to each connector's
   GND pins.
5. **At every bypass cap GND pad:** 1 via directly at each cap's GND
   connection.

---

## 8. Routing Order

Follow this sequence. Do not improvise.

| Step | Action | Layer | Notes |
|------|--------|-------|-------|
| 0 | Lock zones: mark Zone A/B/C boundaries on Cmts.User layer | — | Visual reference |
| 1 | Confirm decoupling cap orientation for shortest traces | F.Cu | Check C5, C6, C7 pad-to-pin |
| 2 | Define GND pours on In1.Cu and In2.Cu (full board) | In1, In2 | Don't finalize yet |
| 3 | **Route REF_ELEC** (U1.10 → R1 first → west of C5 → south → east crossing at y≈17–17.6 → drop to J5.33) | F.Cu | R1 connection first. Crossing above compression band. See §4.1. |
| 4 | Route Bundle C (CH9–CH22, U1 top → clockwise arc → corridor) | F.Cu (+B.Cu) | Densest group. Second. Prevents blocking. |
| 5 | Route Bundle A (CH0–CH8, U1 left → south → J5) | F.Cu | West corridor, lanes 1–9 |
| 6 | Route Bundle B (CH23–CH31, U1 right → south → J5) | F.Cu | East corridor, lanes 24–32 |
| 7 | Route ELEC_TEST (U1.33 → J5.35) | F.Cu | Outermost east lane 33 |
| 8 | GND stitching vias at zone boundary (y ≈ 10mm) | F.Cu→In1 | Full-width row |
| 9 | GND stitching vias along electrode corridor edges | F.Cu→In1 | West + east board edges |
| 10 | Power: +3V3 → FB1 → AVDD star → C5/C6 → U1 VDD pads | F.Cu | Short trunk + branches |
| 11 | ADC_ref: U1.28 → C7, short and isolated | F.Cu | ≤3mm |
| 12 | Bypass cap GND vias (1 per cap, ≤1mm from pad) | F.Cu→In1 | Minimal loop area |
| 13 | SPI on B.Cu (J1→R2-R4→B.Cu→U1 bottom pads) | B.Cu | SCLK in center of bundle |
| 14 | LVDS GND ties (U1 pads 18,20,22,24 → GND) | F.Cu | Short stubs to EP/via |
| 15 | Selective guard traces (outermost electrodes only) | F.Cu | Only per §4.4 rules |
| 16 | Finalize pours, check for neck-downs / plane fragments | All | Visual + DRC |
| 17 | Full DRC + parity + post-routing checks (see §10) | — | 97 pre-route tests + 0 new |

---

## 9. What NOT To Do

These are common failure patterns. If you catch yourself doing any
of them, stop and reconsider the topology.

1. **Don't route electrodes on B.Cu "because it's open."**
   B.Cu is the SPI domain. Putting electrodes there puts them near
   digital signals and kills noise performance. The only exception
   is Group C overflow within Zone A, far from SPI.

2. **Don't cut the ground plane to fix a routing jam.**
   If a trace won't fit, re-route the trace. Splitting In1.Cu or
   In2.Cu breaks the return path for every signal above/below the
   split. This is a false win that creates a real problem.

3. **Don't over-guard with GND traces.**
   Guard traces create capacitive loading and can couple noise into
   the guard network if it's not truly quiet. Use guards only on the
   outermost runs or near the digital boundary, per §4.4.

4. **Don't let SCLK run alongside the electrode corridor.**
   Any parallel run >1mm is rejected. Re-route SCLK to stay contained
   in Zone B on B.Cu.

5. **Don't route AVDD in a loop around the chip.**
   Use star topology from FB1. Each AVDD branch is a direct shot.

6. **Don't place power vias in the electrode corridor.**
   Power vias in Zone A create antipad disruptions in In1.Cu exactly
   where you need continuous return path coverage.

7. **Don't route REF_ELEC inside the CH lane bundle.**
   REF_ELEC is not a lane — it's a crossing trunk. Any parallel run
   alongside CH traces for >3mm is rejected. Cross the corridor once
   at ~90°. If you're fitting REF_ELEC between CH lanes, you've
   already failed.

8. **Don't route Bundle A and B before Bundle C.**
   If you route the easy left/right groups first, you block the clean
   perimeter escape path for CH9–CH22. Bundle C is routed second
   (after REF_ELEC) specifically to prevent this.

---

## 10. Post-Routing Verification

| Check | Method | Pass criteria |
|-------|--------|---------------|
| DRC clean | `kicad-cli pcb drc` | 0 electrical violations |
| Parity | `kicad-cli pcb drc --schematic-parity` | 0 parity issues |
| Unconnected | DRC report | 0 unconnected pads |
| Plane continuity | Visual: In1.Cu, In2.Cu solo | No splits, no islands, no traces |
| Electrode clearance | DRC + manual | ≥0.3mm all electrode pairs |
| SPI-electrode separation | Manual measurement | ≥0.5mm on all layers |
| SCLK parallelism | Manual inspection | No parallel run >1mm near electrodes |
| REF_ELEC spacing | Manual measurement | ≥0.5mm from nearest CH trace |
| Bypass cap loop | Manual measurement | GND via ≤1mm from each cap GND pad |
| Power width | DRC net class | All POWER nets ≥0.4mm |
| Electrode via count | Manual count | 0 for A/B/REF/ETEST; ≤1 pair for C |
| Via integrity | DRC | 0 dangling vias |
| Zone compliance | Visual | No SPI/power in Zone A; no electrode in Zone B |

---

## 11. Commitments and Limitations

### Committed decisions

1. **GND+GND inner layers.** No power plane. Justified by return-path
   continuity requirements and ≤15mA total current draw.
2. **Max 1 via pair per electrode net.** 0 is the target for Groups A,
   B, REF_ELEC, ELEC_TEST. 1 pair allowed for Group C overflow only.
3. **SCLK contained in SPI bundle interior.** Not on bundle edge near
   electrode corridor.
4. **REF_ELEC is a shaped crossing trunk, not a lane.** Exits U1
   pad 10 at y=12.75, connects R1 first, routes west of C5 to
   avoid the C5/pad-1 choke, then crosses the corridor as a
   diagonal or multi-segment path (no single constant-Y works).
   Zero vias, one conceptual crossing at ~90°, no parallel run
   >3 mm with CH traces. Shape resolved during interactive routing.
5. **Bundle C before A and B.** Top-edge nets use clockwise perimeter
   arc escape. Routing them second (after REF_ELEC) prevents blocking.
6. **34-lane corridor ordering is deterministic.** West→east:
   CH8..CH0 | CH9..CH22 | CH23..CH31, ELEC_TEST. No freelancing.

### Known limitations

1. **No impedance control.** SPI at ≤25MHz over ≤30mm traces does not
   require controlled impedance. Revisit if SPI rate increases in v2.
2. **No differential pair routing.** CMOS SPI mode. LVDS pairs GND-tied.
3. **No length matching.** All signals DC–25MHz, traces ≤30mm.
   Delay mismatch <0.1ns — irrelevant.
4. **EP thermal vias locked.** 9 vias, 3×3, 0.3mm drill, tented, GND.
   Enforced by `TestEPFootprintInvariants`.
5. **v1 routes all 32 channels** to J5 for v2 compatibility, but only
   CH0–CH15 are electrically active in v1.

---

## A. REF_ELEC Route — Option A (2 vias, B.Cu hop)

**Added in Rev 3.5.** This section documents the implemented REF_ELEC
route. The F.Cu-only path was proven geometrically impossible (see
impossibility proof in prior revisions) due to three impenetrable
barriers: left pad column, right pad column, and bottom pad tips. All
inter-barrier gaps are 0.225–0.333 mm, below the 0.75 mm minimum
required for a 0.15 mm trace with 0.30 mm clearance.

### A.1 Route topology

T-junction at board coordinate **(9.98, 12.75)**:

| Branch | Layer | From → To | Length |
|--------|-------|-----------|--------|
| 1 (stub) | F.Cu | Junction (9.98, 12.75) → U1.10 (10.95, 12.75) | 0.97 mm |
| 2 (stub) | F.Cu | Junction (9.98, 12.75) → R1.2 (9.98, 15.00) | 2.25 mm |
| 3a | B.Cu | VIA1 (9.98, 12.75) → VIA2 (19.7625, 18.20) | 11.20 mm |
| 3b | F.Cu | VIA2 (19.7625, 18.20) → J5.33 (19.7625, 19.341) | 1.14 mm |

**Total: 15.56 mm** (F.Cu: 4.36 mm, B.Cu: 11.20 mm).

### A.2 Via specifications

| Parameter | Value |
|-----------|-------|
| Via count | 2 |
| Drill | 0.3 mm |
| Annular ring | 0.15 mm |
| Total diameter | 0.6 mm |
| VIA1 location (board) | (9.98, 12.75) — west of U1, at pad-10 Y-level |
| VIA2 location (board) | (19.7625, 18.20) — north of J5 T-row, at J5.33 X |
| VIA1 KiCad | (109.98, 81.33) |
| VIA2 KiCad | (119.7625, 86.78) |

### A.3 Clearance summary

All clearances verified by deterministic Minkowski-sum checker
(`ref_elec_clearance_check.py`), locked by 58 pytest tests
(`tests/test_ref_elec_clearance.py`).

| Segment | Min clearance | Nearest obstacle |
|---------|--------------|------------------|
| S1: Jct→U1.10 | 0.300 mm | U1.9 (CH0) |
| S2: Jct→R1.2 | 0.445 mm | U1.6 (CH3) |
| S3: B.Cu hop | — (ground pour auto-clear) | — |
| S4: VIA2→J5.33 | 0.369 mm | J5.31 (CH15) |
| VIA1 | 0.341 mm | U1.9 (CH0) |
| VIA2 | 0.473 mm | J5.31 (CH15) |

**S1 clearance is fundamental.** The 0.300 mm clearance on S1 equals
the ELECTRODE net class requirement exactly and is the theoretical
maximum achievable for this QFN-56 geometry. Proof: pad pitch = 0.5 mm,
pad height = 0.25 mm → gap between pads = 0.25 mm each side.
Passage width = gap + half_pad = 0.25 + 0.125 = 0.375 mm = KEEP.
Edge clearance = KEEP − HW = 0.375 − 0.075 = 0.300 mm = CLR. The
junction y-coordinate (12.75) is the symmetric optimum between U1
pads 9 and 11; any shift worsens one side. The footprint geometry is
frozen by regression tests (`TestFootprintFreeze`, 18 tests).

### A.3a Digital/SPI isolation

The B.Cu hop (11.20 mm diagonal) passes under the IC at 29.1° from
horizontal, crossing the electrode corridor x-range at y ∈ [13.75, 17.34].
All SPI copper is on F.Cu only:

| SPI component | Position | Distance to B.Cu trace |
|---------------|----------|----------------------|
| R4 (MOSI) | brd(12.0, 6.0) | ≥ 6.75 mm |
| R3 (SCLK) | brd(15.0, 6.0) | ≥ 6.75 mm |
| R2 (CS) | brd(18.0, 6.0) | ≥ 6.75 mm |
| U1.25 (MISO) | brd(19.05, 18.05) | 0.88 mm (F.Cu pad, different layer) |
| U1.23 (MOSI) | brd(19.05, 18.05) | ~1.4 mm (F.Cu pad, different layer) |

No SPI signals exist on B.Cu. The In1.Cu continuous GND plane provides
full shielding between the F.Cu SPI pads and the B.Cu REF_ELEC trace.
The B.Cu diagonal creates a 0.75 mm antipad slot in the B.Cu ground
pour; In1.Cu GND plane remains unbroken. Verified by
`TestDigitalIsolation` (6 tests).

### A.3b Corridor intrusion analysis

The 34-lane electrode corridor (x ∈ [11.775, 18.225]) on F.Cu is
**not obstructed** by this route:

```
  Board-space X (mm), not to scale:

  9.98  10.95 11.40 11.775              18.225 18.60 19.05 19.76
  VIA1  |←pad→|KEEP|←─── corridor ───→|KEEP|←pad→|       VIA2
  (B.Cu) left        (6.45 mm, 34 CH)          right      (F.Cu)
```

Derivation (frozen — `test_corridor_derivation_from_frozen_constants`):

```
CORRIDOR_X_MIN = U1_CX − QFN56_PAD_CX + QFN56_PAD_LONG/2 + KEEP
               = 15.0   − 4.05          + 0.45              + 0.375 = 11.775
CORRIDOR_X_MAX = U1_CX + QFN56_PAD_CX − QFN56_PAD_LONG/2 − KEEP
               = 15.0   + 4.05          − 0.45              − 0.375 = 18.225
```

- VIA1 at x = 9.98 is west of corridor (x < 11.775)
- VIA2 at x = 19.7625 is east of corridor (x > 18.225), aligned with
  J5 pin 33 (REF_ELEC’s own connector column)
- All F.Cu segments (S1, S2, S4) are entirely outside the corridor x-range
- The B.Cu diagonal crosses under the corridor — this is on B.Cu, not F.Cu

Verified by `TestCorridorIntrusion` (5 tests). Bundle C routing may
proceed without corridor conflicts.

### A.3c Analog coupling analysis

The B.Cu diagonal passes directly under U1 CH input pads on its way
from VIA1 to VIA2. Although on different layers (B.Cu vs F.Cu), the
In1.Cu GND plane provides shielding.

> **Metric definitions:**
> - **Distance to B.Cu trace:** minimum edge-to-edge distance from the
>   CH pad copper to the B.Cu REF_ELEC trace copper, measured in the
>   xy-plane (ignoring z-axis layer separation).
> - **Expanded pad:** the CH pad rectangle inflated by **expansion buffer
>   = CLR = 0.300 mm** on each edge. This is the ELECTRODE net-class
>   clearance (from `ref_elec_clearance_check.py`), representing the
>   keep-out envelope around each CH pad.
> - **Path inside expanded pad:** length of the B.Cu trace segment that
>   falls inside the expanded pad rectangle. "—" means the trace does
>   not enter the expanded pad at all.

| CH pad | Net  | Distance to B.Cu trace | Path inside expanded pad | Coupling risk |
|--------|------|----------------------|-------------------------|---------------|
| U1.9   | CH0  | 0.000 mm (at VIA1)   | 1.649 mm                | VIA1 junction; GND-shielded |
| U1.8   | CH1  | 0.073 mm             | 0.788 mm                | In1.Cu GND, DC bias |
| U1.42  | CH23 | 0.155 mm             | 0.595 mm                | In1.Cu GND, DC bias |
| U1.7   | CH2  | 0.510 mm             | —                       | In1.Cu GND |
| U1.41  | CH24 | 0.592 mm             | —                       | In1.Cu GND |
| U1.6   | CH3  | 0.947 mm             | —                       | In1.Cu GND |

Frozen distances (±0.02 mm tolerance) verified by
`test_bcu_worst_case_distance_to_named_ch_pads`.

Mitigations:

1. **In1.Cu GND plane** is continuous between F.Cu and B.Cu
   (0.200 mm F.Cu→In1.Cu prepreg, per §2 stackup table). This is
   the physical dielectric separation; do not confuse with the
   full-stack 1.465 mm F.Cu→B.Cu distance.
2. **REF_ELEC is a DC bias** (1.225 V mid-supply), not a switching signal
3. **29.1° crossing angle** limits parallel overlap to **0.334 mm per lane**
   (well under the 3.0 mm A2 acceptance criterion)
4. **Worst path-inside-expanded-pad:** U1.9 at 1.649 mm — this is the VIA1
   junction point where the B.Cu segment starts within CH0’s expanded pad
   projection. Acceptable: different layers with GND shield between them.
5. Total CH pads within 1.0 mm: **6** (frozen in `test_bcu_coupling_boundary_at_1mm`)

Verified by `TestAnalogCoupling` (4 invariant-based tests).

### A.4 KiCad s-expression

Generated by `gen_ref_elec_route.py`. Append inside `(kicad_pcb ...)`:

```lisp
(segment (start 109.98 81.33) (end 110.95 81.33) (width 0.15) (layer "F.Cu") (net 36))
(segment (start 109.98 81.33) (end 109.98 83.58) (width 0.15) (layer "F.Cu") (net 36))
(via (at 109.98 81.33) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 36))
(segment (start 109.98 81.33) (end 119.7625 86.78) (width 0.15) (layer "B.Cu") (net 36))
(via (at 119.7625 86.78) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 36))
(segment (start 119.7625 86.78) (end 119.7625 87.921) (width 0.15) (layer "F.Cu") (net 36))
```

### A.5 TP6 connection

TP6 (REF_ELEC test point) at brd(28.0, 10.0) is far NE of the main
route. It will be connected via a separate F.Cu trace from the VIA1
junction or from U1.10, routed through the open area north of U1.
Not part of this route scope.
