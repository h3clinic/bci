# v1 RHD2132 Headstage — Design Notes & Fab Plan

**Rev:** 1.0 · **Date:** 2026-02-26 · **Board:** 30×24mm, 4-layer

---

## 1. Layer Stack & Routing Strategy

### 1.1 Stackup (1.6mm FR4, ENIG)

| Layer     | Purpose             | Cu (µm) | Dielectric |
|-----------|---------------------|---------|------------|
| F.Cu      | Signal + components | 35      | —          |
| Prepreg 1 |                     |         | 0.2mm FR4  |
| In1.Cu    | **GND plane** (full pour) | 35 | —       |
| Core      |                     |         | 1.065mm FR4|
| In2.Cu    | **GND plane** (full pour) | 35 | —       |
| Prepreg 3 |                     |         | 0.2mm FR4  |
| B.Cu      | Signal + routing    | 35      | —          |

Both inner layers are GND. No split power plane — AVDD and +3V3 are routed
as traces on F.Cu/B.Cu, not plane fills. This gives maximum shielding for
the 32 electrode input traces (≤3µV RMS noise target).

### 1.2 Routing Rules

| Net Class   | Track Width | Clearance | Via Drill/OD | Notes |
|-------------|-------------|-----------|--------------|-------|
| ELECTRODE   | 0.15mm      | 0.3mm     | 0.2/0.4mm    | Guard-traced where possible |
| DIGITAL_SPI | 0.2mm       | 0.2mm     | 0.3/0.6mm    | Route away from analog |
| POWER       | 0.4mm       | 0.25mm    | 0.4/0.8mm    | Ferrite bead → AVDD |
| Default     | 0.2mm       | 0.2mm     | 0.3/0.6mm    | — |

### 1.3 Electrode Routing (Critical for Noise)

**Strategy: Top + Bottom, with ground shielding**

- **F.Cu (top):** Route CH0–CH15 from U1 left/top pads to J5 connector.
  These are the v1 active channels — shortest paths, no vias if possible.
- **B.Cu (bottom):** Route CH16–CH31 from U1 right/top pads, via to B.Cu,
  then to J5. These are v2 channels (connector-only in v1).
- **Guard traces:** Where space permits, flank electrode traces with GND
  traces connected to the inner ground planes via stitching vias.
- **Separation:** Maintain ≥0.5mm between any electrode trace and SPI/power
  traces. Route SPI on the opposite side of the board from active analog.

### 1.4 SPI Routing

- SPI signals (CS, SCLK, MOSI, MISO) route from J1 (north) through R2-R4
  (series 100Ω) to U1 bottom-side pads (18-25).
- MISO has no series resistor (output from chip, per Intan reference).
- Route on **B.Cu** where possible to keep F.Cu clean for analog.
- SPI clock rate is 25 MHz max — not impedance-critical at 30mm trace length.

### 1.5 Power Distribution

```
J1 pins 7,8 (+3V3) → C1,C2 bypass → FB1 pin 1
                                    → FB1 pin 2 (AVDD)
                                    → C3,C4 bypass
                                    → U1 VDD1 (pad 13), VDD2 (pad 26), VDD3 (pad 31)
                                    → C5, C6 local bypass at U1
```

- Power enters at J1 (north), travels south to FB1, then to U1.
- AVDD star-point is at FB1 output. All AVDD traces radiate from there.
- +3V3 star-point is at FB1 input (J1 power pins).

---

## 2. Fab & Assembly Method

### 2.1 PCB Fabrication

| Parameter         | Spec                       |
|-------------------|----------------------------|
| Board size        | 30 × 24 mm                 |
| Layers            | 4                          |
| Min trace/space   | 0.15mm / 0.15mm (6/6 mil)  |
| Min via drill     | 0.2mm (8 mil)              |
| Copper weight     | 1 oz (35µm) all layers     |
| Substrate         | FR4 Tg170                  |
| Surface finish    | **ENIG** (required for QFN) |
| Solder mask       | Green LPI both sides       |
| Silkscreen        | White, top side only       |
| Board thickness   | 1.6mm ±10%                 |
| Impedance control | Not required (SPI ≤25MHz)  |

**Recommended fab houses:** JLCPCB (4-layer, ENIG, $20/5pcs), PCBWay,
OSH Park (4-layer, ENIG, $10/sq.in).

### 2.2 Assembly

**Method: Reflow soldering with stencil**

1. **Stencil:** Order framed stainless steel stencil (0.12mm thick) with
   the board. QFN-56 requires paste on all 57 pads including EP.
2. **Paste:** Sn63/Pb37 or SAC305 solder paste (T4 particle size for 0402).
3. **Placement:** Manual with vacuum pen under stereo microscope.
   - Place U1 (QFN-56) first — alignment is critical (0.5mm pitch).
   - Place 0402 passives (C1-C7, FB1, R1-R4).
   - Place J1 and J5 connectors last.
4. **Reflow:** Hot air rework station or desktop reflow oven.
   - Profile: ramp to 150°C (60s), soak 150-200°C (90s), peak 230-245°C (30s).
   - Monitor with thermocouple on board edge.
5. **Inspection:** Check U1 solder joints under microscope.
   - Verify no bridges between QFN pads (0.5mm pitch, 0.25mm gap).
   - Check EP wetting through thermal vias (if added).

**EP thermal relief:** The QFN-56 EP (4.8×4.8mm) should have a 3×3 or 4×4
array of 0.3mm thermal vias to In1.Cu GND plane. These will be added during
interactive routing in KiCad.

### 2.3 Critical Assembly Notes

- **QFN-56 alignment:** Use corner pads and silkscreen pin-1 marker.
  The IC has 0.25mm pad-to-pad gap — any rotation error is visible.
- **Omnetics connectors:** These are fine-pitch (0.635mm). Apply paste
  with stencil, don't hand-solder. The bottom-row pads are only 0.381mm wide.
- **Ferrite bead orientation:** FB1 is unpolarized — no orientation concern.
- **Capacitor values:** C7 (10nF for ADC_ref) is a different value from C1-C6.
  Mark it clearly during placement.

---

## 3. In-Vitro Validation Plan

### 3.1 Bring-Up Sequence

| Step | Test | Pass Criteria | Equipment |
|------|------|---------------|-----------|
| 1 | Visual inspection | No bridges, all pads wetted | Stereo microscope |
| 2 | Continuity: GND | < 1Ω from J1-GND to U1-EP | Multimeter |
| 3 | Continuity: AVDD | < 2Ω from FB1 out to U1 VDD1 | Multimeter |
| 4 | Power-on: +3V3 | 3.3V ±5% at TP1, AVDD at TP2 | Bench supply via cable |
| 5 | Current draw | 10-15mA (RHD2132 typical) | Supply ammeter |
| 6 | SPI comms | Intan GUI detects chip, reads ID register | Intan RHD2000 USB board |
| 7 | Impedance test | Z < 100kΩ on all 16 active channels | Intan built-in impedance check |
| 8 | Noise floor | ≤3µV RMS, 300Hz–7kHz, inputs shorted | Intan recording + offline FFT |
| 9 | ACSF recording | Visible 50/60Hz pickup (normal) | MEA in saline, Faraday cage |
| 10 | Spike injection | 100µV, 1kHz sine → visible on 4 TPs | Signal generator + probe |

### 3.2 Noise Measurement Protocol

1. Short all 16 active electrode inputs to GND via 10kΩ resistors (or
   connect to a shorted MEA).
2. Record 60 seconds at 30 kS/s using Intan GUI.
3. Export raw `.rhd` file.
4. Offline analysis (Python):
   - Bandpass filter 300 Hz – 7 kHz (4th order Butterworth).
   - Compute RMS noise per channel.
   - Target: **≤ 3.0 µV RMS** on all 16 channels.
   - Expected: ~2.4 µV RMS (RHD2132 typical, from datasheet).

### 3.3 ACSF In-Vitro Test

1. Place MEA (e.g., MCS 60-electrode) in ACSF bath at room temperature.
2. Connect MEA to J5 via appropriate adapter.
3. Insert Ag/AgCl reference electrode in bath → connect to J5 pin 33 (REF).
4. Place assembly inside Faraday cage (aluminum mesh).
5. Record 5 minutes baseline.
6. Verify: all 16 channels show similar noise floor, no saturated channels,
   no channel-to-channel crosstalk above -40dB.

### 3.4 Known Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| QFN-56 bridge | SPI failure | Inspect under microscope; rework with flux + hot air |
| Omnetics connector alignment | No SPI comms | Use stencil; verify footprint against physical part |
| Noise above 3µV | Reduced spike detection | Add shield can over U1; improve GND via stitching |
| ACSF moisture ingress | Short circuit | Conformal coat or parylene-C after validation |
| Cable-induced noise (30cm) | Common-mode pickup | Twisted-pair SPI cable; ferrite clamp at headstage end |

---

## 4. Design Decisions Log

| Decision | Rationale | Source |
|----------|-----------|--------|
| QFN-56 (not QFN-40) | RHD2132 is 56-pin per Intan datasheet p.35 | Intan RHD2000 series datasheet |
| CMOS mode (not LVDS) | Intan USB interface board uses CMOS SPI | Intan eval board schematic |
| 100Ω SPI series R (R2-R4) | Cable ringing damping | Intan reference headstage design |
| No MISO series R | Output from chip; per Intan reference | RHD2132_headstage1.sch |
| AUXIN1-3 → VDD | Unused inputs tied high per datasheet | Intan RHD2000 datasheet §3.5 |
| LVDS_EN → GND | CMOS mode selection | Intan RHD2000 datasheet §2.1 |
| VESD → GND | ESD clamp connection | Intan RHD2000 datasheet §3.3 |
| 100nF + 10nF decoupling | Per datasheet recommendation | Intan RHD2000 datasheet §7.1 |
| 4-layer with 2× GND plane | Maximum shielding | Intan RHD2000 datasheet §7.1 |
| ENIG surface finish | Required for QFN + fine-pitch connectors | IPC-7351B |

---

## 5. File Inventory

| File | Description |
|------|-------------|
| `gen_schematic_v1_rhd2132.py` | Schematic generator (KiCad 9) |
| `gen_pcb_v1.py` | PCB layout bootstrapper (KiCad 9) |
| `afe-headstage-v1.kicad_sch` | Generated schematic |
| `afe-headstage-v1.kicad_pcb` | Generated PCB layout (placement + ratsnest) |
| `afe-headstage-v1.kicad_pro` | KiCad project file with net classes |
| `afe_symbols_v1.kicad_sym` | Custom symbol library |
| `footprints_v1.pretty/` | Custom footprint library (QFN-56, PZN-12, A79024) |
| `docs/BOM_v1.csv` | Bill of materials with MPNs |
| `docs/design_notes_v1.md` | This file |
