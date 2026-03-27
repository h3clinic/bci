# 08 — Board-to-Board Interface Specification
## Headstage ↔ Carrier Interface — FROZEN
### Document Status: INTERFACE FREEZE — Do not modify without formal review

---

## 1. Interface Summary

| Parameter | Value |
|---|---|
| Connector (headstage) | Samtec QSH-030-01-L-D-A |
| Connector (carrier) | Samtec QTH-030-01-L-D-A |
| Pin count | 30 (dual row, 15 per row) |
| Pitch | 0.5 mm |
| Mating height | 5 mm |
| Signal voltage | 3.3V LVCMOS |
| Power voltage | 3.3–5V (TBD — depends on carrier regulator) |
| Protocol | DDR SPI (Intan RHD2164 native) |

---

## 2. Signal List

| # | Signal | Direction | Voltage | Speed | Description |
|---|---|---|---|---|---|
| 1 | SCLK | Carrier → Headstage | 3.3V LVCMOS | 20 MHz | SPI clock (FPGA-generated) |
| 2 | MOSI | Carrier → Headstage | 3.3V LVCMOS | 20 Mbps | SPI master-out / slave-in |
| 3 | CS1 | Carrier → Headstage | 3.3V LVCMOS | <1 MHz | Chip select for U1 (active low) |
| 4 | CS2 | Carrier → Headstage | 3.3V LVCMOS | <1 MHz | Chip select for U2 (active low) |
| 5 | MISO1_A | Headstage → Carrier | 3.3V LVCMOS | 20 Mbps | U1 DDR MISO A (ch 0–31) |
| 6 | MISO1_B | Headstage → Carrier | 3.3V LVCMOS | 20 Mbps | U1 DDR MISO B (ch 32–63) |
| 7 | MISO2_A | Headstage → Carrier | 3.3V LVCMOS | 20 Mbps | U2 DDR MISO A (ch 0–31) |
| 8 | MISO2_B | Headstage → Carrier | 3.3V LVCMOS | 20 Mbps | U2 DDR MISO B (ch 32–63) |
| 9 | TEST_SHORT_EN | Carrier → Headstage | 3.3V LVCMOS | DC/slow | Input shorting control |
| 10 | CAL_EN | Carrier → Headstage | 3.3V LVCMOS | DC/slow | Cal injection enable |
| 11 | SPARE | — | — | — | Reserved for future use |
| 12 | VIN | Carrier → Headstage | 3.3–5V | DC | Power supply (4 pins) |
| 13 | GND | Common | 0V | — | Ground reference (15 pins) |

---

## 3. Connector Pin Map

```
         Headstage side (component view, looking down at board)

  Row A (odd)              Row B (even)
  ──────────               ──────────
   1: GND                   2: GND              ← Ground fence
   3: SCLK ────────►        4: GND              ← Clock (carrier→HS)
   5: MOSI ────────►        6: GND              ← Data  (carrier→HS)
   7: CS1  ────────►        8: CS2  ────────►   ← Chip selects
   9: GND                  10: GND              ← Ground fence
  11: MISO1_A ◄────        12: MISO1_B ◄────   ← U1 DDR pair (HS→carrier)
  13: GND                  14: GND              ← Ground fence
  15: MISO2_A ◄────        16: MISO2_B ◄────   ← U2 DDR pair (HS→carrier)
  17: GND                  18: GND              ← Ground fence
  19: VIN ═══════►         20: VIN ═══════►     ← Power
  21: GND                  22: GND              ← Power return
  23: VIN ═══════►         24: VIN ═══════►     ← Power
  25: GND                  26: GND              ← Power return
  27: TEST_SHORT_EN ►      28: CAL_EN ────►     ← Test control
  29: SPARE                30: GND              ← Spare + ground
```

---

## 4. Pin Allocation Statistics

| Category | Count | Percentage |
|---|---|---|
| Ground (GND) | 15 | 50% |
| High-speed digital (SPI) | 8 | 27% |
| Power (VIN) | 4 | 13% |
| Control (slow) | 2 | 7% |
| Spare | 1 | 3% |
| **Total** | **30** | **100%** |

Ground ratio: 50% — exceeds the 33% minimum for GSG discipline.

---

## 5. Electrical Specifications

### 5.1 Digital Signal Levels — FROM DATASHEET

**Source:** Intan RHD2000 Series Datasheet, Pages 6, 12, 27

**Supply voltage (VDD):**

| Parameter | Min | Typ | Max | Unit | Source |
|---|---|---|---|---|---|
| VDD (supply) | 3.2 | 3.3 | 3.6 | V | Datasheet p6: "3.2–3.6 V" |
| VDD (derated) | 2.9 | 3.0 | 3.1 | V | Datasheet p27: derated operation table |

**CMOS digital logic levels (LVDS_en = 0):**

| Parameter | Symbol | Min | Max | Unit | Source (verbatim) |
|---|---|---|---|---|---|
| Digital input LOW | VinLO | −0.4 | +0.7 | V | Datasheet p6: "−0.4 – +0.7 V" |
| Digital input HIGH | VinHI | +2.4 | +3.6 | V | Datasheet p6: "2.4 – 3.6 V" |
| Digital output LOW | VOL | — | GND | V | Datasheet p12: "driven to ground" |
| Digital output HIGH | VOH | VDD | — | V | Datasheet p12: "driven to VDD" |
| auxout drive current | IOH/IOL | — | ±2 | mA | Datasheet p32 |

**Critical warnings from datasheet (verbatim, p12):**
> "The digital input pins on the RHD2000 interpret any voltage below 0.7V as
> logic 'low' and any voltage above 2.4V as logic 'high', so the chip can be
> interfaced with standard 2.5V, 3.0V, or 3.3V signals. Digital inputs to the
> RHD2000 should not go below −0.4V, and should never exceed 3.6V. Digital
> outputs from the RHD2000 chip are driven to ground for logic 'low' and to
> VDD for logic 'high'."

> "5V signals should never be applied directly to the chips." (p6)

**FPGA I/O bank constraint:**
The FPGA I/O bank driving the RHD2164 must use **3.3V LVCMOS** (VCCO = 3.3V).
2.5V LVCMOS is also compatible (VIH 2.4V ≥ threshold). 1.8V LVCMOS is **NOT**
compatible (VOH ~1.7V < VIH threshold 2.4V). 5V tolerant I/O must **NOT** be
used without level shifter.

### 5.2 Power

| Parameter | Min | Typ | Max | Unit |
|---|---|---|---|---|
| VIN voltage | 3.3 | 5.0 | 5.5 | V |
| VIN current (headstage) | — | 90 | 160 | mA |
| Pin current rating (QSH) | — | — | 500 | mA/pin |
| VIN pins (4 paralleled) | — | — | 2000 | mA total |

### 5.3 Timing — FROM DATASHEET

**Source:** Intan RHD2164 Datasheet, Page 11 — "SPI Bus Timing Specifications"
(TA = 25°C, VDD = 3.3V)

| Symbol | Parameter | Min | Max | Unit | Source |
|---|---|---|---|---|---|
| tSCLK | SCLK Period | 41.6 | — | ns | Max SCLK freq = 24 MHz |
| tSCLKH | SCLK Pulse Width High | 20.8 | — | ns | |
| tSCLKL | SCLK Pulse Width Low | 20.8 | — | ns | |
| tCS1 | CS Low to SCLK High Setup | 20.8 | — | ns | |
| tCS2 | SCLK Low to CS High Setup | 20.8 | — | ns | |
| tCSOFF | CS High Duration | 154 | — | ns | Between commands |
| tMOSI | MOSI Valid to SCLK↑ Setup | 10.4 | — | ns | |
| tMISO | SCLK/CS↓ to MISO Valid | — | 12 | ns | Propagation delay |
| tCYCLE | Min Cycle Time (between samples) | 950 | — | ns | Max 1.05 MS/s per 32-ch module |

**Design operating point:** SCLK = 20 MHz (50 ns period) — well within 24 MHz max.
Round-trip propagation through B2B connector + traces: estimated <5 ns.
Total timing margin at 20 MHz: 50 ns period − 12 ns MISO delay − 5 ns B2B = 33 ns.

✅ Timing specs now sourced from actual datasheet (no longer estimates).

---

## 6. Signal Integrity Rules

### 6.1 Headstage PCB Rules

| Rule | Specification |
|---|---|
| SPI trace impedance | 50Ω ±10% (microstrip on L4 over L5 GND) |
| SPI trace width | 0.15 mm (6-layer stackup) |
| SCLK length match (U1 vs U2) | ±2 mm |
| MISO trace length (each) | <20 mm (U1/U2 to B2B pad) |
| Separation: SPI vs electrode | ≥3 mm |
| Separation: SPI vs analog signal | ≥2 mm |
| Layer: SPI routing | L4 (digital signals) only |
| Layer: SPI reference | L5 (solid DGND) |
| Via count in SPI path | 0 (no layer transitions) |
| Guard vias along SPI | Optional, 0.8 mm spacing |

### 6.2 Series Damping Resistors

| Location | Signal | Resistor | Value | Default |
|---|---|---|---|---|
| Headstage (at U1 MISO_A pin) | MISO1_A | R_D5 | 33Ω | DNI |
| Headstage (at U1 MISO_B pin) | MISO1_B | R_D6 | 33Ω | DNI |
| Headstage (at U2 MISO_A pin) | MISO2_A | R_D7 | 33Ω | DNI |
| Headstage (at U2 MISO_B pin) | MISO2_B | R_D8 | 33Ω | DNI |
| Headstage (at B2B SCLK pad) | SCLK | R_SCLK | 33Ω | DNI |
| **Carrier** (at FPGA SCLK out) | SCLK | R_D1 | 33Ω | Populate |
| **Carrier** (at FPGA MOSI out) | MOSI | R_D2 | 33Ω | Populate |
| **Carrier** (at FPGA CS1 out) | CS1 | R_D3 | 33Ω | DNI |
| **Carrier** (at FPGA CS2 out) | CS2 | R_D4 | 33Ω | DNI |

All resistors are 0402 footprint. DNI = Do Not Install (populate only if
ringing/overshoot is observed during bring-up).

### 6.3 B2B Connector Layout Rules

- Place B2B connector on edge of headstage PCB (shortest path to carrier)
- Ground pads connected to unbroken L2 (AGND) and L5 (DGND) planes
- No routing under B2B connector footprint on L1 (analog signals)
- VIN pins: wide trace (≥0.5 mm) to bulk decoupling cap, then to LDO
- Power entry point: star ground connection between AGND and DGND

---

## 7. DDR SPI Protocol Notes

### 7.1 What "DDR" Means for RHD2164

The RHD2164 is **not** a standard SPI device. It has two MISO output lines:

- **MISO_A**: Outputs data for amplifier channels 0–31
- **MISO_B**: Outputs data for amplifier channels 32–63

Both lines are active **simultaneously** during each SPI command. The data
is clocked out on the **falling edge** of SCLK (standard SPI CPOL=0, CPHA=0
with data valid after falling edge).

This is called "DDR" in Intan's terminology because two data streams are
output per clock cycle (one on each MISO line), effectively doubling the
data throughput compared to a single-MISO SPI device.

### 7.2 Implications for Carrier FPGA

The carrier FPGA must:
1. Generate SCLK, MOSI, CS1, CS2
2. Capture MISO_A and MISO_B simultaneously for each chip
3. That means **4 MISO capture paths** total (2 per chip)
4. Standard SPI peripherals on MCUs typically have only 1 MISO input
5. FPGA fabric can implement arbitrary numbers of capture paths

### 7.3 Bus Timing per Sample

At 30 kS/s (33.33 µs sample period):
- 35 SPI commands per sample (32 CONVERT + 3 AUX)
- 16 SCLK cycles per command
- 560 SCLK cycles per sample
- At 20 MHz SCLK: 28.0 µs active, 5.33 µs idle (16% margin)

With parallel CS (both chips active on separate CS lines):
- Both chips read simultaneously in 28.0 µs ✅
- Total data per sample: 2 × 2 × 32 × 16 = 2048 bits = 256 bytes

---

## 8. Interface Freeze Checklist

- [x] Connector part number defined (QSH-030 / QTH-030)
- [x] Pin map assigned and documented
- [x] Signal directions documented
- [x] Voltage levels specified (3.3V LVCMOS) — **verified from RHD2000 Series Datasheet p6/p12/p27**
- [x] Timing constraints listed — **verified from RHD2164 Datasheet p11**
- [x] VIH/VIL thresholds documented (VIL <0.7V, VIH >2.4V, absolute max 3.6V)
- [x] Ground allocation ≥33% (actual: 50%)
- [x] Power pins allocated with margin (4 pins, 2A capacity vs 160 mA need)
- [x] Spare pin reserved (1)
- [x] Series damping resistor plan documented
- [x] SI routing rules documented
- [ ] Carrier-side connector footprint verified (TODO: when carrier board starts)
- [x] Actual RHD2164 timing verified from datasheet ✅
- [ ] B2B connector mechanical fit verified with 3D model (TODO)

---

## 9. Change Control

| Date | Rev | Change | Author |
|---|---|---|---|
| 2026-02-17 | 2.0 | Complete redesign: LVDS removed, raw DDR SPI passthrough. MCU removed from headstage. New GSG pinout with 50% ground allocation. | BCIInterface |

**This interface is FROZEN.** Any changes require:
1. Documented justification
2. Impact analysis on both headstage and carrier
3. Version increment in this document

---

*Document created: 2026-02-17*
*Sources: Intan RHD2000 Series Datasheet (v2017-12), Intan RHD2164 Datasheet (v2017-12),
Intan-RHX v3.5.0 source code, Intan LVDS adapter board datasheet, Samtec QSH-030 product page*
