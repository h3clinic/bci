# 04 - Layout Rules

## The Core Truth

**For neural-level signals, layout is everything.**

You can have a perfect schematic and destroy it with bad layout. These rules are non-negotiable for sub-10 µVrms noise floors.

---

## A) Board Partitioning

### The Two-Island Model

```
┌────────────────────────────────────────────────────────────────┐
│                         PCB OUTLINE                            │
│                                                                │
│  ┌─────────────────────────┐    ┌─────────────────────────┐   │
│  │     ANALOG ISLAND       │    │     DIGITAL ISLAND      │   │
│  │                         │    │                         │   │
│  │  • Electrode inputs     │    │  • MCU/FPGA             │   │
│  │  • AFE ICs              │    │  • USB/Ethernet PHY     │   │
│  │  • Analog regulators    │    │  • Digital regulators   │   │
│  │  • Reference circuits   │    │  • Clocks (if separate) │   │
│  │                         │    │                         │   │
│  └───────────┬─────────────┘    └───────────┬─────────────┘   │
│              │                              │                  │
│              └──────────┬───────────────────┘                  │
│                         │                                      │
│                   ┌─────┴─────┐                                │
│                   │  BRIDGE   │                                │
│                   │ (single   │                                │
│                   │  point)   │                                │
│                   └───────────┘                                │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### Bridge Rules

1. **Single connection point** between islands (not scattered vias)
2. Bridge carries:
   - Ground connection
   - Digital data signals (with controlled routing)
   - Power (filtered at bridge)
3. **No high-speed clocks** crossing the bridge on outer layers
4. Bridge width: minimum necessary for signals

### Partitioning Checklist

- [ ] Analog island boundary defined
- [ ] Digital island boundary defined
- [ ] Bridge location identified
- [ ] Component placement respects boundaries
- [ ] No analog traces in digital area and vice versa

---

## B) Return Current Discipline

### The Fundamental Rule

**Current flows in loops. Return current takes the path of least impedance.**

For DC and low frequencies: path of least resistance
For high frequencies: path of least inductance (directly under the trace)

### Ground Plane Requirements

| Layer | Requirement |
|-------|-------------|
| Under analog signals | Solid, unbroken AGND |
| Under digital signals | Solid, unbroken DGND |
| At boundaries | Controlled transition, not random |

### Anti-Patterns (Do NOT Do These)

```
BAD: Split ground with digital trace crossing
┌─────────────────────────────────────────┐
│ AGND plane │ gap │ DGND plane          │
│            │     │                      │
│    ════════╪═════╪════════             │
│    Digital trace crossing gap           │
│    (return current has no path!)        │
└─────────────────────────────────────────┘

BAD: Signal via without nearby ground via
┌─────────┐
│ Signal  │
│ via     │ ← Return current has to find
│  ●      │    distant ground via
│         │
└─────────┘

GOOD: Signal via with adjacent ground via
┌─────────┐
│ ● ●     │ ← Signal and return stay together
│ Sig GND │
└─────────┘
```

### Via Stitching

| Location | Via Spacing |
|----------|-------------|
| Around analog island perimeter | Every 3 mm |
| Adjacent to high-speed signals | Every 2 mm |
| At ground plane transitions | At least 2 vias per transition |

---

## C) Input Signal Routing

### Inputs Are Sacred

The first 10 mm of trace from connector to AFE input is the most critical routing on the board.

### Input Routing Rules

| Rule | Specification |
|------|---------------|
| Trace length | As short as possible (<10 mm ideal) |
| Trace width | Consistent (e.g., 0.15–0.2 mm) |
| Symmetry | All channels should have similar length/geometry |
| Layer | Keep on one layer if possible |
| Clearance from clocks | >2 mm, perpendicular crossing only |
| Clearance from power | >1 mm |

### Routing Pattern

```
Good: Direct, short, parallel
┌──────────────────────────────────────┐
│ Connector                   AFE      │
│                                      │
│ ●─────────────────────────────●      │
│ ●─────────────────────────────●      │
│ ●─────────────────────────────●      │
│ ●─────────────────────────────●      │
│                                      │
│ (all traces similar length, parallel)│
└──────────────────────────────────────┘

Bad: Long, meandering, crossed
┌──────────────────────────────────────┐
│ Connector                   AFE      │
│                                      │
│ ●────────────────────╮       ╭──●    │
│ ●───╮   ╭────────────┼───────┤       │
│     │   │   ╭────────┴───╮   │       │
│ ●───┴───┴───┤            ├───╯──●    │
│             ╰────────────╯           │
│ (different lengths, crossings = bad) │
└──────────────────────────────────────┘
```

### Guard Traces/Shielding (Advanced)

If you have experience and need it:

```
       GND guard trace
           │
    ●══════╪══════●  Signal trace
           │
       GND guard trace
```

Guard traces should be:
- Connected to AGND with vias every 5 mm
- On the same layer as the signal
- Only used if you understand the tradeoffs

**If unsure: don't guard, just keep inputs short and isolated.**

---

## D) Decoupling Placement

### The 3-3-3 Rule

1. **3 mm maximum** from cap to pin
2. **3 via maximum** in the path (cap → power plane → pin)
3. **3 different values** for broadband filtering (e.g., 10 µF, 100 nF, 10 nF)

### Placement Example

```
┌─────────────────────────────────────────────┐
│                  IC Pin                     │
│                    │                        │
│                    ●  Via to power plane    │
│                    │                        │
│              ┌─────┴─────┐                  │
│              │           │                  │
│           ──┤├──      ──┤├──                │
│           100nF        10µF                 │
│              │           │                  │
│              ●           ●  Vias to GND     │
│                                             │
│   Distance: pin to cap < 3 mm               │
└─────────────────────────────────────────────┘
```

### Decoupling Strategy by IC Type

| IC | High-Freq Cap | Bulk Cap | Notes |
|----|---------------|----------|-------|
| AFE (analog supply) | 100 nF ceramic | 10 µF ceramic | Multiple pairs for multi-pin |
| AFE (digital supply) | 100 nF ceramic | 10 µF ceramic | Separate from analog |
| ADC reference | 10 µF + 100 nF | 100 µF | Low ESR critical |
| MCU/FPGA core | 100 nF per pin | 10–47 µF | Follow vendor guidance |
| LDO output | Per datasheet | Per datasheet | Often specifies ESR |

### Via Stitching for Decoupling

```
Cap placement:
         Pin (top)
           │
     ┌─────●─────┐
     │     │     │
    ═╪═   Via   ═╪═   ← Caps on bottom, vias to planes
     │           │
     ●           ●    ← GND vias adjacent to caps
```

---

## E) Layer Stack

### 4-Layer Stack (Minimum for Mixed-Signal)

| Layer | Purpose | Notes |
|-------|---------|-------|
| 1 (Top) | Signals + Components | Analog on analog side, digital on digital side |
| 2 | Ground | Solid, partitioned if needed |
| 3 | Power | Split for AVDD, DVDD |
| 4 (Bottom) | Signals + Components | Same partitioning as top |

**4-layer limitations:**
- Must be very disciplined about partitioning
- Limited routing channels
- Ground continuity harder to maintain

### 6-Layer Stack (Recommended)

| Layer | Purpose | Thickness |
|-------|---------|-----------|
| 1 (Top) | Analog signals + AFE components | — |
| 2 | Ground (AGND primary) | Core |
| 3 | Analog power (AVDD) | Prepreg |
| 4 | Digital signals | Core |
| 5 | Ground (DGND primary) | Prepreg |
| 6 (Bottom) | Digital power + signals | — |

**6-layer advantages:**
- Dedicated ground adjacent to each signal layer
- Clear analog/digital separation
- Better EMI performance

### Stack Cross-Section

```
═══════════════════════════════════════════════  L1: Analog signals
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  L2: Ground (AGND)
┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  L3: AVDD
═══════════════════════════════════════════════  L4: Digital signals
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  L5: Ground (DGND)
═══════════════════════════════════════════════  L6: DVDD + signals
```

### Layer Assignment Rules

| Signal Type | Preferred Layer | Reference Layer |
|-------------|-----------------|-----------------|
| Electrode inputs | L1 only | L2 (AGND) |
| Analog AFE routing | L1 | L2 (AGND) |
| SPI clock/data | L4 or L6 | L5 (DGND) |
| USB/Ethernet | L6 | L5 (DGND) |
| Power traces | L3 or L6 | — |

---

## F) Specific Layout Guidance

### AFE IC Placement

```
┌──────────────────────────────────────────────────┐
│                                                  │
│    ┌────────────┐                               │
│    │ CONNECTOR  │  ← Electrode input            │
│    └─────┬──────┘                               │
│          │ <10mm                                │
│    ┌─────┴──────┐                               │
│    │    AFE     │  ← Place AFE close to connector│
│    │    IC      │                               │
│    └─────┬──────┘                               │
│          │                                      │
│    ┌─────┴──────┐                               │
│    │  Digital   │  ← Digital further away       │
│    │   Link     │                               │
│    └────────────┘                               │
│                                                  │
└──────────────────────────────────────────────────┘
```

### Clock Routing

| Rule | Specification |
|------|---------------|
| Trace impedance | 50Ω (single-ended) or per spec |
| Length matching | N/A for low-speed AFE clocks |
| Clearance from analog | Maximum feasible (>2 mm) |
| Layer | Digital layer (L4 or L6) |
| Termination | At destination if >2 inches |

### USB/Ethernet Routing

| Parameter | USB 2.0 HS | Gigabit Ethernet |
|-----------|------------|------------------|
| Differential impedance | 90Ω ± 10% | 100Ω ± 10% |
| Pair-to-pair spacing | 3× trace width | 3× trace width |
| Length matching (pair) | ±5 mm | ±5 mm |
| Layer | Bottom or L4 | Bottom or L4 |
| Reference plane | Solid, unbroken | Solid, unbroken |

---

## G) DFM and Fabrication Notes

### Minimum Specifications (Standard PCB)

| Parameter | Minimum | Recommended |
|-----------|---------|-------------|
| Trace width | 0.1 mm | 0.15 mm |
| Trace spacing | 0.1 mm | 0.15 mm |
| Via drill | 0.2 mm | 0.3 mm |
| Via pad | 0.45 mm | 0.5 mm |
| Annular ring | 0.1 mm | 0.125 mm |

### Stack-up Communication

Send this to your fab house:
```
6-Layer Controlled Impedance Stack
----------------------------------
Total thickness target: 1.6 mm ± 10%

L1: Signal (1 oz copper)
   Prepreg: XX µm (calculate for 50Ω microstrip on L1-L2)
L2: Ground (1 oz copper)
   Core: XX µm
L3: Power (0.5 oz copper)
   Prepreg: XX µm
L4: Signal (1 oz copper)
   Core: XX µm
L5: Ground (1 oz copper)
   Prepreg: XX µm (calculate for 50Ω microstrip on L6-L5)
L6: Signal (1 oz copper)

Required impedances:
- 50Ω single-ended microstrip (L1 ref L2, L6 ref L5)
- 90Ω differential (USB, if used)
- 100Ω differential (Ethernet, if used)
```

### Soldermask and Silkscreen

- **Soldermask:** Standard green or matte black (avoid white near analog—reflections can cause visual inspection issues)
- **Silkscreen:** Mark all test points, polarity indicators, pin 1 markers
- **Exposed pad:** Ensure paste aperture for QFN/BGA thermal pads

---

## H) Layout Review Checklist

### Before Sending to Fab

#### Partitioning
- [ ] Analog and digital islands clearly separated
- [ ] Single bridge connection point
- [ ] No digital traces in analog area

#### Ground Planes
- [ ] AGND solid under all analog signals
- [ ] DGND solid under all digital signals
- [ ] Via stitching at island perimeter
- [ ] No unnecessary ground plane cuts

#### Input Routing
- [ ] Electrode inputs <10 mm length
- [ ] All input traces similar length
- [ ] Inputs clear of clocks and digital signals
- [ ] Inputs on single layer with solid reference

#### Decoupling
- [ ] All caps <3 mm from pins
- [ ] Via to plane for each cap
- [ ] GND via adjacent to each cap

#### High-Speed Signals
- [ ] Impedance controlled per design rules
- [ ] Length matching where required
- [ ] Reference plane unbroken under traces
- [ ] Signal/return vias paired

#### Power
- [ ] Each rail clearly routed
- [ ] Adequate copper for current
- [ ] Thermal relief for test points (not for power delivery)

#### Testability
- [ ] Test points accessible
- [ ] Isolation resistors placeable
- [ ] JTAG/SWD header accessible

#### DFM
- [ ] Design rules pass
- [ ] Impedance calculation provided to fab
- [ ] Stack-up clearly specified
- [ ] Gerbers reviewed visually
