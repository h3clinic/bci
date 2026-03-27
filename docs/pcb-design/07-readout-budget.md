# 07 — Readout Budget
## Neural AFE Headstage — 128 Channel
### Document Status: VALIDATED against Intan-RHX v3.5.0 source code (GPLv3)

---

## 1. Locked Parameters

| Parameter | Value | Source |
|---|---|---|
| fs | 30 kS/s per channel | RHD2164 max (Intan product page, confirmed in `rhxglobals.h` `SampleRate30000Hz = 16`) |
| Channels | 128 (2× RHD2164) | Architecture spec |
| ADC bits | 16 | Intan product page: "16-bit ADC" |
| Egress | LVDS to digital carrier | Architecture spec |

---

## 2. Raw Throughput

```
128 ch × 30,000 S/s × 16 bit = 61,440,000 bit/s
                               = 61.44 Mbps
                               = 7.68 MB/s
```

---

## 3. LVDS Egress Capacity

| Parameter | Value |
|---|---|
| Driver | DS90LV047A (quad) |
| Data lanes | 2 (LVDS0, LVDS1) |
| Per-lane rate | 100 Mbps (claimed in schematic) |
| Total capacity | 200 Mbps |
| **Margin** | **200 / 61.44 = 3.25×** |

With 20% framing overhead: 61.44 × 1.2 = 73.7 Mbps → still 2.7× margin.

⚠️ **UNVERIFIED**: The 100 Mbps per lane is a design target, not a DS90LV047A spec limit.
DS90LV047A supports up to 400 Mbps per channel. The actual rate depends on the
serialization scheme implemented in the MCU/FPGA firmware. This needs to be defined.

---

## 4. RHD2164 SPI Protocol — FROM SOURCE CODE

### 4.1 Critical Architecture Fact

**The RHD2164 uses DDR SPI (Double Data Rate) with two MISO lines.**

From `rhxglobals.h`:
```cpp
enum BoardDataSource {
    PortA1 = 0, PortA2 = 1,   // Standard SPI streams
    ...
    PortA1Ddr = 8, PortA2Ddr = 9,  // DDR SPI streams
    ...
};

enum ChipType {
    RHD2164Chip = 4,
    RHD2164MISOBChip = 1000    // Second MISO line of RHD2164
};
```

**What DDR means for RHD2164:**
- The chip has **two MISO output lines**: MISO A and MISO B
- Each MISO line outputs **32 channels** of data per SPI command cycle
- Together they provide all 64 channels simultaneously
- This is NOT standard SPI. It requires either:
  - An FPGA with two MISO capture paths, OR
  - An MCU with DDR SPI peripheral support (STM32U5, STM32H7 — per Intan firmware)

### 4.2 Commands Per Sample Period

From `rhxdatablock.h` / `rhxdatablock.cpp`:
```cpp
int RHXDataBlock::channelsPerStream(ControllerType type_) {
    case ControllerRecordUSB2:
    case ControllerRecordUSB3:
        return 32;    // 32 channels per MISO line
}
```

Each SPI command is **16 bits** (MOSI sends command, MISO returns data).

Per sample period (33.33 µs at 30 kS/s), the FPGA sends a sequence of commands:
- **32 CONVERT commands** (one per channel on each MISO line → 64 channels total)
- **3 auxiliary commands** (aux ADC reads, register reads, impedance DAC)
- = **35 commands per sample period** (this is the standard Intan Rhythm protocol)

### 4.3 SPI Clock Requirement

```
35 commands × 16 bits/command = 560 SCLK cycles per sample period
Sample period at 30 kS/s = 33.33 µs
Minimum SCLK = 560 / 33.33 µs = 16.8 MHz
```

**At 20 MHz SCLK:**
- Time per command: 16 bits / 20 MHz = 0.8 µs
- Time for 35 commands: 35 × 0.8 µs = 28.0 µs
- Sample period: 33.33 µs
- **Margin: 33.33 - 28.0 = 5.33 µs (16% idle time)** ✅

### 4.4 Two Chips (128 Channels) — The Real Problem

For 2× RHD2164, each chip needs its own SPI bus (separate CS, shared SCLK ok).

**Option A: Two independent SPI buses (parallel)**
- Each bus runs 35 commands at 20 MHz = 28.0 µs
- Both run simultaneously → fits in 33.33 µs ✅
- Requires: 2 SPI peripherals + 4 MISO capture paths (2 per chip for DDR)
- This is what the FPGA does in Intan's reference design

**Option B: Single SPI bus, multiplexed CS (serial)**
- Total: 2 × 35 = 70 commands at 20 MHz = 56.0 µs
- Sample period: 33.33 µs
- **DOES NOT FIT. 56 > 33.33.** ❌
- Would require reducing to ≤15 kS/s, or increasing SCLK to >34 MHz

---

## 5. MCU vs FPGA — Validated Assessment

### 5.1 Intan's Official MCU Firmware (v1.2, June 2025)

From the release notes:
```
- U5 rhd2164_acquisition: DDR SPI to control RHD2164 with STM32U5
- H7 rhd2164_acquisition: DDR SPI to control RHD2164 with STM32H7
```

**Intan provides working STM32 firmware for RHD2164.** This confirms an MCU *can* do it.

However, the release notes do NOT state:
- Maximum sample rate achieved per channel
- Whether 2× RHD2164 is supported simultaneously
- Whether streaming is sustained or offline (batch) mode

The firmware has a `OFFLINE_TRANSFER` toggle, suggesting real-time streaming
to USB may be limited by MCU throughput.

### 5.2 Our Design Uses STM32G431

| Spec | STM32G431 | STM32U5A5 (Intan) | STM32H723 (Intan) |
|---|---|---|---|
| Core | Cortex-M4 @ 170 MHz | Cortex-M33 @ 160 MHz | Cortex-M7 @ 550 MHz |
| SPI peripherals | 3 | 3 | 6 |
| SPI max clock | 42.5 MHz | 40 MHz | 137.5 MHz |
| DMA channels | 12 | 16 | 16 |
| RAM | 32 KB | 2.5 MB | 1 MB |
| DDR SPI | **NO native DDR SPI** | **YES (OCTOSPI in DDR mode)** | **YES (OCTOSPI)** |

### 🔴 CRITICAL FINDING

**The STM32G431 does NOT have DDR SPI capability.**

The RHD2164 requires DDR SPI (dual MISO). The STM32U5 and STM32H7 have OCTOSPI
peripherals that support DDR mode — this is specifically why Intan chose them.

The STM32G431's SPI peripheral is standard single-data-rate with one MISO line.
It **cannot natively capture both MISO A and MISO B simultaneously**.

**Workarounds:**
1. Bit-bang the second MISO with GPIO + DMA timer capture (fragile, unreliable at 20 MHz)
2. Use both SPI peripherals to each capture one MISO line (requires external clock gating logic)
3. **Replace STM32G431 with STM32U5 or STM32H7** (Intan's proven path)
4. **Use an FPGA** (Intan's primary reference design uses Xilinx Artix-7 on Opal Kelly)

---

## 6. Revised Architecture Recommendation

### Option 1: MCU-based (change MCU)
Replace STM32G431CBU6 with **STM32U5A5ZJT6Q** (NUCLEO board available).
- Use OCTOSPI in DDR mode for each RHD2164
- STM32U5 has 2× OCTOSPI peripherals → can drive 2 chips in parallel
- Intan provides working firmware framework
- **Pro**: Proven path, low risk
- **Con**: Larger package (LQFP144 vs UFQFPN-48), higher power

### Option 2: FPGA on carrier (current architecture, but move SPI control to carrier)
Keep headstage as passive analog frontend. Move ALL digital logic to carrier board:
- Headstage: RHD2164 × 2, connectors, power, clock — no MCU
- Carrier: FPGA drives SPI directly over board-to-board connector
- SPI signals (SCLK, MOSI, MISO_A, MISO_B, CS) run over the B2B connector
- **Pro**: Simplest headstage, proven FPGA reference design
- **Con**: More wires over B2B connector, SPI signal integrity at 20 MHz over flex

### Option 3: Small FPGA on headstage
Replace STM32G431 with Lattice iCE40UP5K or similar:
- Handles DDR SPI natively
- Serializes into LVDS
- ~$2 BOM cost for iCE40
- **Pro**: Correct tool for the job
- **Con**: FPGA development learning curve, need toolchain

---

## 7. What Must Be Validated Before Routing

| # | Test | Pass Criteria | Dev Board |
|---|---|---|---|
| 1 | DDR SPI read from one RHD2164 | 32 channels × 30 kS/s, zero dropped samples, 10 min sustained | NUCLEO-U5A5 or Opal Kelly XEM7310 |
| 2 | DDR SPI read from two RHD2164 | 64+64 = 128 ch × 30 kS/s, zero drops, 10 min | Same |
| 3 | LVDS serialization at target rate | Sustained stream, zero framing errors, monotonic sequence counter | Same + LVDS eval board |
| 4 | End-to-end throughput | 7.68 MB/s to host, <70% MCU/FPGA utilization | Full signal chain |

**None of these tests require the custom PCB.**

---

## 8. Decision Required

The STM32G431 in the current schematic **cannot drive the RHD2164** without
external logic to handle DDR SPI.

Choose one:
- [ ] A. Swap MCU to STM32U5A5 (OCTOSPI DDR, Intan firmware available)
- [ ] B. Swap MCU to STM32H723 (OCTOSPI DDR, Intan firmware available)  
- [ ] C. Replace MCU with small FPGA (iCE40UP5K + LVDS serializer)
- [ ] D. Move all digital to carrier FPGA, keep headstage analog-only
- [ ] E. Other: ___

**Do not route until this decision is made and validated on a dev board.**

---

*Document created: 2026-02-17*
*Source validation: Intan-RHX v3.5.0 (GitHub, GPLv3), Intan STM32 Framework v1.2 release notes*
