# 02 - Block Architecture

## System Block Diagram

```
                                    ┌─────────────────────────────────────────────────┐
                                    │              AFE HEADSTAGE BOARD                │
┌──────────┐                        │                                                 │
│          │    ┌───────────────────┼──────────────────┐                              │
│Electrodes│───►│ A) ELECTRODE      │                  │                              │
│  (128)   │    │    INPUTS         │                  │                              │
│          │    │  • ESD protection │                  │                              │
└──────────┘    │  • Bias network   │     ┌────────────┴────────────┐                 │
                │  • Input RC       │     │                         │                 │
   ┌────────┐   └───────────────────┼────►│  C) AFE + ADC BLOCK     │                 │
   │Reference│                      │     │   • LNA chain           │                 │
   │Electrode│──────────────────────┼────►│   • Filtering           │                 │
   └────────┘   ┌───────────────────┼─────│   • ADC (often integrated)                │
                │ B) REFERENCE &    │     │   • Anti-alias          │                 │
                │    COMMON-MODE    │     └────────────┬────────────┘                 │
                │  • Ref routing    │                  │                              │
                │  • Ground return  │                  │ Digital data                 │
                └───────────────────┘                  ▼                              │
                                          ┌────────────────────────┐                  │
          ┌──────────────────┐            │ D) CLOCKING/TIMING     │                  │
          │ E) POWER         │            │  • Clean clock source  │                  │
          │  • Analog rails  │───────────►│  • Deterministic frame │                  │
          │  • Digital rails │            │  • Timestamp/sequence  │                  │
          │  • Filtering     │            └────────────┬───────────┘                  │
          │  • Ground plan   │                         │                              │
          └──────────────────┘                         ▼                              │
                                          ┌────────────────────────┐                  │
          ┌──────────────────┐            │ F) DIGITAL EGRESS      │◄─────────────────┤
          │ G) TESTABILITY   │            │  • High-speed link     │    Interconnect  │
          │  • Short inputs  │───────────►│  • Packet framing      │    to Carrier    │
          │  • Cal injection │            │  • Sequence IDs        │                  │
          │  • Test points   │            └────────────────────────┘                  │
          │  • Isolation     │                                                        │
          └──────────────────┘                                                        │
                                    └─────────────────────────────────────────────────┘
```

---

## A) Electrode Inputs (Per Channel)

### Requirements
- Handle DC offsets from electrode-electrolyte interface
- Protect AFE from ESD events
- Define a DC bias path (electrodes must not float)

### Circuit Elements

```
Electrode ──┬── ESD ──┬── [Optional RC] ──► AFE Input
            │         │
            │         └── Bias resistor to VREF (if AC-coupled)
            │
            └── Shield/guard (if used)
```

### Design Rules

| Element | Guideline |
|---------|-----------|
| ESD diodes | Low leakage (<1 nA), low capacitance (<5 pF) |
| Input capacitor | Only if AC-coupling required; specify based on low-frequency cutoff |
| Bias resistor | High value (10–100 MΩ) to minimize noise contribution |
| Input RC | If used, cutoff >> signal bandwidth (anti-alias is elsewhere) |

### ⚠️ Common Mistakes
- Adding random 100 nF caps at inputs (destroys electrode impedance matching)
- Forgetting bias path (inputs saturate from leakage currents)
- Using TVS diodes with high leakage

---

## B) Reference & Common-Mode Strategy

### Options

| Strategy | Pros | Cons |
|----------|------|------|
| Dedicated reference electrode | Simple, low noise | Requires extra electrode |
| Average reference (computed) | No extra electrode | Increases digital complexity |
| Driven reference | Cancels common-mode | Stability concerns |

### Ground Return Rules

1. Reference electrode connects to **analog ground** near ADC reference
2. This is the **only** intentional connection between electrode system and board ground
3. Do not connect electrode shield to chassis ground (creates ground loops)

### Schematic Checklist
- [ ] Reference electrode input clearly marked
- [ ] Return path to analog ground defined
- [ ] Reference buffer (if driven reference)
- [ ] Reference selection jumpers (if multiple modes)

---

## C) AFE + ADC Block

### Typical Integrated AFE Options

| Part Family | Channels | Integrated ADC | Notes |
|-------------|----------|----------------|-------|
| Intan RHD2164 | 64 | Yes (16-bit) | Neural-specific, SPI |
| Intan RHD2132 | 32 | Yes (16-bit) | Lower channel count |
| TI ADS1299 | 8 | Yes (24-bit) | EEG-focused, daisy-chain |
| Analog Devices AD7124 | 8 | Yes (24-bit) | Precision, lower channel |

For 128 channels:
- 2× RHD2164, or
- 4× RHD2132, or
- 16× ADS1299 (daisy-chained)

### Anti-Alias Strategy

```
Signal BW: 6 kHz (spikes)
Sample rate: 30 kS/s
Nyquist: 15 kHz

Anti-alias filter cutoff: ≤ 10 kHz (with margin)
Filter order: Depends on ADC's input bandwidth and oversampling
```

Many integrated AFEs have built-in anti-alias—**verify in datasheet**.

### Schematic Checklist
- [ ] AFE IC placed with all required decoupling
- [ ] Reference voltage generation (internal or external)
- [ ] Gain configuration (resistors or register settings)
- [ ] Filter configuration (if programmable)
- [ ] Digital interface signals (SPI/parallel)

---

## D) Clocking/Timing

### Requirements
- Low-jitter clock for ADC
- Deterministic sample timing
- Ability to correlate with external events

### Clock Architecture

```
┌──────────────┐     ┌─────────────┐     ┌─────────┐
│ Crystal/TCXO │────►│ Clock buffer│────►│ AFE/ADC │
│   (master)   │     │  (fan-out)  │     │ (MCLK)  │
└──────────────┘     └──────┬──────┘     └─────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  MCU/FPGA   │
                     │ (sync clock)│
                     └─────────────┘
```

### Specifications

| Parameter | Target | Notes |
|-----------|--------|-------|
| Clock frequency | Per AFE requirements | e.g., 2.048 MHz for Intan |
| Jitter (RMS) | < 10 ps | For neural, less critical than RF |
| Clock type | Crystal oscillator or TCXO | Avoid RC oscillators |

### Timestamping Options
1. **Hardware counter** in FPGA/MCU, latched with each sample
2. **Sequence number** per packet (detect drops, not absolute time)
3. **External sync input** for multi-board or stimulus sync

---

## E) Power Architecture

### Rail Definitions

| Rail | Voltage | Purpose | Current Est. |
|------|---------|---------|--------------|
| AVDD | 3.3V or 2.5V | AFE analog supply | ~50 mA |
| AVSS | GND or -2.5V | AFE analog return | — |
| DVDD | 3.3V or 1.8V | AFE digital supply | ~20 mA |
| DVSS | GND | AFE digital return | — |
| VIO | 3.3V or 1.8V | Logic level for interface | ~10 mA |
| VCORE | 1.0–1.2V | MCU/FPGA core (if on board) | ~100 mA |

### Power Tree Example

```
External 5V ─┬─► [LDO 3.3V Analog] ──► AVDD
             │
             ├─► [LDO 3.3V Digital] ──► DVDD
             │
             └─► [Buck 1.2V] ──► VCORE (if FPGA)
```

### Filtering Strategy
- **Pi filter** between analog and digital grounds (if single ground)
- **Ferrite bead** + capacitors at each power domain entry
- **Separate regulators** for analog vs. digital (not just ferrites)

### Ground Strategy

| Approach | When to Use |
|----------|-------------|
| Single ground, partitioned | 4-layer boards, careful layout |
| Split ground, single-point connect | 6-layer, high noise sensitivity |
| Separate ground planes per domain | Complex, rarely needed |

**Rule:** Digital return currents must never flow under analog inputs.

---

## F) Digital Egress

### Bandwidth Requirements

| Channels | Sample Rate | Bits | Raw BW | With Overhead |
|----------|-------------|------|--------|---------------|
| 128 | 20 kS/s | 16 | 5.1 MB/s | 6.2 MB/s |
| 128 | 30 kS/s | 16 | 7.7 MB/s | 9.2 MB/s |
| 128 | 40 kS/s | 16 | 10.2 MB/s | 12.3 MB/s |

### Interface Comparison

| Interface | Practical BW | Latency | Complexity |
|-----------|--------------|---------|------------|
| USB 2.0 HS | ~40 MB/s | Variable | Low |
| USB 3.0 SS | ~400 MB/s | Low | Medium |
| Gigabit Ethernet | ~100 MB/s | Configurable | Medium |
| LVDS to carrier | >100 MB/s | Very low | Low |

### Packet Format Requirements
- **Sequence ID** (32-bit counter minimum)
- **Timestamp** (optional, 32–64 bit)
- **Channel data** (all 128 channels per packet, or chunked)
- **Checksum/CRC** (detect corruption)

### Schematic Checklist
- [ ] Interface PHY/transceiver
- [ ] ESD protection on external lines
- [ ] Proper termination for high-speed signals
- [ ] Connector with defined pinout

---

## G) Testability

### Non-Negotiable Test Features

| Feature | Purpose | Implementation |
|---------|---------|----------------|
| Input short | Measure noise floor | CMOS switches or jumpers |
| Cal injection | Verify gain, BW, channel mapping | DAC or resistor divider |
| Rail test points | Debug power issues | Via or pad per rail |
| Isolation jumpers | Separate analog/digital debug | 0Ω resistors |

### Input Shorting Circuit

```
              ┌─── Normal: electrode connected
Electrode ───┤
              └─── Test: all inputs shorted to reference
                   (via CMOS switch, e.g., ADG1414)
```

### Calibration Injection

```
                    ┌──────────────┐
Cal DAC/PWM ───────►│ Attenuator   │───► Injection point
                    │ (1000:1)     │     (before or after input RC)
                    └──────────────┘
```

Injection amplitude: ~100 µVpp to ~1 mVpp (realistic spike amplitude)

### Debug Isolation

```
Analog section ───[0Ω or jumper]─── Digital section

With jumper removed:
- Analog can be powered/tested independently
- Verify analog noise floor without digital switching
```
