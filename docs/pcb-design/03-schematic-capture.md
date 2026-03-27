# 03 - Schematic Capture Directions

## Workflow Order (Do This Sequence)

```
1. Power Tree          ──► Define all rails first
2. Connector Strategy  ──► How do 128 channels enter?
3. Reference/CM        ──► Ground and reference plan
4. AFE/ADC            ──► Core acquisition circuit
5. Clocking           ──► Clean clock distribution
6. Digital Interface  ──► Egress path
7. Test/Debug         ──► Injection, isolation, test points
```

**Do not start with the AFE.** Start with power.

---

## Hierarchical Sheet Structure

Create these sheets in your CAD tool (KiCad/Altium/OrCAD):

```
Top Level
├── Power
│   ├── Input Protection
│   ├── Analog Regulators
│   ├── Digital Regulators
│   └── Power Sequencing (if needed)
│
├── Inputs (repeated or arrayed)
│   ├── Channels 1-32
│   ├── Channels 33-64
│   ├── Channels 65-96
│   └── Channels 97-128
│
├── Reference_CM
│   ├── Reference Electrode Interface
│   └── Common-Mode Handling
│
├── AFE_ADC
│   ├── AFE_IC_1 (channels 1-64)
│   └── AFE_IC_2 (channels 65-128)
│
├── Clocking
│   ├── Clock Source
│   └── Distribution
│
├── Digital_Interface
│   ├── MCU/FPGA (if on board)
│   └── Egress (USB/Ethernet/Connector)
│
└── Test_Debug
    ├── Input Shorting
    ├── Cal Injection
    └── Test Points
```

---

## Sheet 1: Power

### 1.1 Define Every Rail

Create a table in your schematic notes:

| Rail Name | Voltage | Tolerance | Max Current | Source | Load |
|-----------|---------|-----------|-------------|--------|------|
| VIN | 5.0V | ±5% | 500 mA | USB/External | All regulators |
| AVDD | 3.3V | ±2% | 100 mA | LDO_A | AFE analog |
| DVDD_AFE | 3.3V | ±5% | 50 mA | LDO_D | AFE digital |
| VIO | 3.3V | ±5% | 30 mA | LDO_D or separate | Level shifters |
| VCORE | 1.2V | ±3% | 200 mA | Buck | MCU/FPGA core |

### 1.2 Power Tree Diagram

Draw the actual power flow:

```
USB 5V ──┬──[Ferrite]──► VIN_PROT
         │
         └──[TVS]──► GND

VIN_PROT ──┬──► [LDO: ADP151-3.3] ──► AVDD
           │       │
           │       └── Cin: 1µF, Cout: 1µF (per datasheet)
           │
           ├──► [LDO: AP2112K-3.3] ──► DVDD
           │       │
           │       └── Cin: 1µF, Cout: 2.2µF
           │
           └──► [Buck: TPS62840] ──► VCORE (if needed)
```

### 1.3 Ground Hierarchy

Define how grounds connect:

```
USB_GND ──► CHASSIS_GND (optional, via capacitor)
        │
        └──► BOARD_GND ──┬──► AGND (analog section)
                         │
                         └──► DGND (digital section)
                              │
                              └── Connect at single point near power entry
```

### 1.4 Power Schematic Checklist

- [ ] Input protection (TVS, polarity protection if external DC)
- [ ] Input bulk capacitance (10–100 µF depending on source)
- [ ] Each regulator with correct input/output caps
- [ ] Enable pins defined (directly tied or controlled)
- [ ] Power-good indicators (optional but useful)
- [ ] Test points on every rail

---

## Sheet 2: Connectors

### 2.1 Electrode Connector Strategy

For 128 channels, common approaches:

| Connector Type | Channels/Connector | Quantity | Notes |
|----------------|-------------------|----------|-------|
| Omnetics NPD-36 | 32 | 4 | Standard neural |
| Samtec QSH/QTH | 50+ | 2-3 | High density |
| Hirose FX23 | 100+ | 1-2 | Very high density |
| Custom flex | 128 | 1 | Requires flex PCB |

### 2.2 Pinout Strategy

**Do not randomly assign pins.** Follow this logic:

```
Connector Pinout (example for 36-pin Omnetics):
- Pins 1-32: Signal channels (sequential)
- Pins 33-34: Reference electrodes
- Pins 35-36: Ground/Shield

Arrange so:
- Adjacent channels are adjacent pins (reduces crosstalk issues)
- Reference pins are at end (easy routing)
- Ground pins at corners (shield routing)
```

### 2.3 Reference/Shield Pinout

| Pin | Function | Connection |
|-----|----------|------------|
| REF1 | Reference electrode 1 | To reference buffer input |
| REF2 | Reference electrode 2 (optional) | Selectable or differential |
| GND | Electrode ground | AGND (star point) |
| SHIELD | Cable shield (if used) | AGND via 0Ω or capacitor |

### 2.4 Connector Schematic Checklist

- [ ] All 128 channel pins mapped
- [ ] Reference pins clearly labeled
- [ ] Ground/shield pins defined
- [ ] Mechanical footprint verified
- [ ] ESD protection immediately after connector

---

## Sheet 3: Inputs

### 3.1 Per-Channel Circuit

```
            ESD              Input Network           To AFE
Connector ──┬──►[TVS]──┬────[Rbias]────┬──────────► CH_IN
            │          │       │        │
            │          │       ▼        │
            │          │     VBIAS      │
            │          │               [Cin] (if AC-coupled)
            │          │                │
            │          └────────────────┴──► AGND
            │
            └─► Shield (if used)
```

### 3.2 Component Selection

| Component | Value | Rationale |
|-----------|-------|-----------|
| ESD TVS | PESD5V0S1BL or similar | <1 nA leakage, <5 pF |
| Rbias | 10–100 MΩ | High to minimize noise, but must sink input bias current |
| Cin | 100 nF–1 µF | Only if AC-coupling needed; sets HPF with Rbias |
| VBIAS | Mid-supply or dedicated | Stable, low-noise reference |

### 3.3 Array vs. Individual

For 128 channels, use **schematic repeat** or **hierarchical sheets**:

```
Sheet: Inputs_1_32
  - 32 identical sub-circuits
  - Bus naming: CH[1..32]

Sheet: Inputs_33_64
  - 32 identical sub-circuits
  - Bus naming: CH[33..64]
```

### 3.4 Input Schematic Checklist

- [ ] ESD on every channel
- [ ] Bias path defined for every channel
- [ ] AC-coupling capacitor (if used) with justified value
- [ ] Schematic note: expected electrode impedance range
- [ ] Net names match connector pinout

---

## Sheet 4: Reference and Common-Mode

### 4.1 Reference Electrode Interface

```
REF_ELECTRODE ──► [ESD] ──► [Buffer] ──► REF_SIGNAL (to AFE)
                              │
                              └──► AGND (return)
```

If using driven reference:
```
REF_ELECTRODE ──► [ESD] ──► [Unity-gain buffer] ──► REF_DRIVE
                                    ▲
                                    │
                    Feedback from common-mode signal
```

### 4.2 Schematic Checklist

- [ ] Reference electrode input with ESD
- [ ] Buffer amplifier (if needed)
- [ ] Clear connection to AFE reference input
- [ ] Ground return path explicitly shown

---

## Sheet 5: AFE/ADC

### 5.1 IC Placement

For each AFE IC, include:

```
┌─────────────────────────────────────────┐
│                AFE IC                    │
│                                         │
│  AVDD ──┬── 100nF ──► AGND              │
│         └── 10µF ──► AGND               │
│                                         │
│  DVDD ──┬── 100nF ──► DGND              │
│         └── 10µF ──► DGND               │
│                                         │
│  REFP ──── 10µF ──► AGND                │
│  REFN ──── 10µF ──► AGND (or AVSS)      │
│                                         │
│  MCLK ◄── Clock distribution            │
│  SCLK ──► SPI bus                       │
│  MOSI ◄── SPI bus                       │
│  MISO ──► SPI bus                       │
│  CS ◄── Chip select                     │
│                                         │
│  IN[0..63] ◄── Input channels           │
└─────────────────────────────────────────┘
```

### 5.2 Decoupling Rules

Follow **exactly** what the datasheet says. For Intan RHD series:

| Pin | Capacitor | Notes |
|-----|-----------|-------|
| VDD (each) | 100 nF + 10 µF | Ceramic, close to pin |
| VREF | 10 µF | Low ESR |
| ELEC_TEST | 100 nF | If used |

### 5.3 AFE Schematic Checklist

- [ ] Every power pin decoupled per datasheet
- [ ] Reference voltage generation/connection
- [ ] All input channels connected with correct net names
- [ ] SPI/digital interface signals with correct polarity
- [ ] Chip select directly controlled (no floating)
- [ ] Unused inputs tied appropriately

---

## Sheet 6: Clocking

### 6.1 Clock Source

```
┌────────────────┐        ┌───────────────┐
│ Crystal/TCXO   │───────►│ Clock Buffer  │──┬──► AFE_MCLK
│ (e.g., 2.048MHz)│       │ (e.g., LMK1C1104)│  │
└────────────────┘        └───────────────┘  └──► MCU_CLK
```

### 6.2 Clock Specification

| Parameter | Requirement | Notes |
|-----------|-------------|-------|
| Frequency | Per AFE datasheet | e.g., 2.048 MHz for Intan |
| Accuracy | ±50 ppm | TCXO if critical |
| Jitter | <50 ps RMS | Typically not critical for neural |
| Output type | LVCMOS/LVDS | Match AFE input |

### 6.3 Clock Schematic Checklist

- [ ] Crystal/oscillator with required load capacitors
- [ ] Buffer with power decoupling
- [ ] Termination at AFE input (if required)
- [ ] Test point for clock verification

---

## Sheet 7: Digital Interface

### 7.1 MCU/FPGA (if on headstage)

Minimal microcontroller for headstage:
- SPI master to AFE
- Clock generation/distribution
- FIFO buffering
- Interface to carrier

### 7.2 Egress Options

**Option A: Direct to carrier (LVDS)**
```
MCU SPI ──► LVDS Driver ──► Flex connector ──► Carrier
```

**Option B: USB on headstage**
```
MCU ──► USB PHY ──► USB connector ──► Host
```

### 7.3 High-Speed Layout Considerations (capture in schematic)

Add schematic notes:
```
NOTE: LVDS pairs require:
- 100Ω differential impedance
- Length matching within 5 mm
- Guard traces or spacing from other signals
```

### 7.4 Digital Interface Schematic Checklist

- [ ] MCU/FPGA with all required support circuits
- [ ] Interface PHY with correct reference resistors
- [ ] ESD protection on external signals
- [ ] Decoupling per IC requirements
- [ ] Connectors with defined pinout
- [ ] Termination resistors placed correctly

---

## Sheet 8: Test and Debug

### 8.1 Input Shorting

```
                    ┌─────────────────────────────────┐
                    │     CMOS Switch Array          │
                    │     (e.g., ADG1414)            │
CH[1..8] from ──────┤IN                          COM├──► VREF
electrodes          │                               │
                    │                           CTRL├◄── TEST_SHORT_EN
                    └─────────────────────────────────┘

TEST_SHORT_EN: GPIO from MCU or jumper
When enabled: all inputs shorted to VREF for noise measurement
```

### 8.2 Calibration Injection

```
                    ┌─────────────────┐
CAL_DAC_OUT ───────►│  R1 (100kΩ)     │
                    ├─────────────────┤
                    │  R2 (100Ω)      ├──► CAL_INJECT
                    └────────┬────────┘
                             │
                           AGND

Attenuation: 1000:1
1V DAC output → 1 mV at injection point

CAL_INJECT routed to selectable input or summed into all channels
```

### 8.3 Power Isolation

```
AVDD_MAIN ──[R_ISO 0Ω]── AVDD_AFE
                │
              [TP]  ← Test point

DVDD_MAIN ──[R_ISO 0Ω]── DVDD_AFE
                │
              [TP]

R_ISO: 0Ω resistor, can be removed for isolation
Alternative: Use load switches for controlled isolation
```

### 8.4 Test Points

Create a test point table:

| TP# | Net | Purpose |
|-----|-----|---------|
| TP1 | AVDD | Analog supply |
| TP2 | DVDD | Digital supply |
| TP3 | VREF | Reference voltage |
| TP4 | MCLK | Clock verification |
| TP5 | SPI_MISO | Digital data |
| TP6 | AGND | Ground reference |
| TP7 | CAL_INJECT | Cal signal verify |

### 8.5 Test/Debug Schematic Checklist

- [ ] Input shorting switches with control signal
- [ ] Cal injection circuit with defined attenuation
- [ ] Power isolation resistors
- [ ] Test points for all critical nets
- [ ] JTAG/SWD header for MCU/FPGA
- [ ] Debug LEDs (optional)

---

## Final Schematic Review Checklist

Before proceeding to layout:

### Power
- [ ] All rails defined with source and load
- [ ] Every regulator has correct capacitors
- [ ] Ground connections explicit

### Analog
- [ ] All 128 inputs have ESD and bias
- [ ] Reference electrode path complete
- [ ] AFE decoupled per datasheet

### Digital
- [ ] Clock source and distribution complete
- [ ] SPI/data bus connections verified
- [ ] Egress interface complete

### Test
- [ ] Shorting circuit functional
- [ ] Cal injection path defined
- [ ] Test points placed

### General
- [ ] No unconnected pins (all intentional NC marked)
- [ ] Net names consistent across sheets
- [ ] Schematic notes for layout-critical items
- [ ] BOM review complete
