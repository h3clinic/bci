# Bill of Materials - Neural AFE Headstage (Rev 2.0)
# 128-Channel, 30kS/s, DDR SPI Passthrough to Carrier FPGA
# Updated: 2026-02-17
# Architecture: AFE-only headstage (no MCU, no LVDS)

# ═══════════════════════════════════════════════════════════════════════════════
#                              CRITICAL COMPONENTS
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Part Number | Manufacturer | Description | Notes |
|-----|-----|-------|---------|-------------|--------------|-------------|-------|
| U1, U2 | 2 | RHD2164 | BGA-100 | RHD2164 | Intan Technologies | 64-ch Neural AFE + ADC | CRITICAL - long lead time |
| U_LDO1 | 1 | ADP151 | WLCSP-4 | ADP151AUJZ-3.3-R7 | Analog Devices | Low-noise LDO 3.3V | PSRR >70dB, AVDD rail |

# DELETED from Rev 1.0:
# U3 (STM32G431CBU6) — MCU removed, no digital processing on headstage
# U4 (DS90LV047A) — LVDS driver removed, no serialization on headstage
# U6 (AP2112K-3.3) — Digital LDO removed, DVDD_AFE uses ferrite filter
# Y1 (DSC1001DI2-020) — TCXO removed, SCLK comes from carrier FPGA
# U_CLK (NC7SZ125) — Clock buffer removed with oscillator

# ═══════════════════════════════════════════════════════════════════════════════
#                              CONNECTORS
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Part Number | Manufacturer | Description | Notes |
|-----|-----|-------|---------|-------------|--------------|-------------|-------|
| J1-J4 | 4 | NPD-36 | Through-hole | NPD-36-AA-GS | Omnetics | 36-pin electrode connector | Mating: NSD-36 |
| J5 | 1 | QSH-030 | SMD | QSH-030-01-L-D-A | Samtec | 30-pin B2B connector | DDR SPI + power passthrough |

# DELETED from Rev 1.0:
# J6 (TC2030-IDC) — SWD debug connector removed (no MCU)

# ═══════════════════════════════════════════════════════════════════════════════
#                              ESD PROTECTION
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Part Number | Manufacturer | Description | Notes |
|-----|-----|-------|---------|-------------|--------------|-------------|-------|
| D1-D128 | 128 | TVS | SOT-23 | PESD5V0S1BL | Nexperia | ESD diode <3pF | One per electrode channel |
| D129 | 1 | TVS | SOT-23 | PESD5V0S1BL | Nexperia | VIN protection | Input supply |

# DELETED from Rev 1.0:
# D129-D132 (PESD5V0S2BT) — LVDS pair ESD removed (no LVDS)
# D133 moved to D129

# ═══════════════════════════════════════════════════════════════════════════════
#                              TEST INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Part Number | Manufacturer | Description | Notes |
|-----|-----|-------|---------|-------------|--------------|-------------|-------|
| U3-U18 | 16 | ADG1414 | TSSOP-16 | ADG1414BRUZ | Analog Devices | 8-ch SPST switch | Input shorting (128ch total) |
| R_CAL1 | 1 | 100kΩ | 0402 | - | - | Cal attenuator high | 1% tolerance |
| R_CAL2 | 1 | 100Ω | 0402 | - | - | Cal attenuator low | 1% tolerance |
| R_ISO1 | 1 | 0Ω | 0402 | - | - | Power isolation | Remove for debug |

# DELETED from Rev 1.0:
# R_ISO2 — second power isolation (no separate DVDD LDO)

# ═══════════════════════════════════════════════════════════════════════════════
#                              PASSIVES - DECOUPLING
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Part Number | Manufacturer | Description | Notes |
|-----|-----|-------|---------|-------------|--------------|-------------|-------|
| C1-C6 | 6 | 100nF | 0402 | GRM155R71H104KE14D | Murata | RHD2164 VDD decoupling | 3x per IC, ×2 ICs |
| C7-C10 | 4 | 100nF | 0402 | GRM155R71H104KE14D | Murata | RHD2164 VDDD decoupling | 2x per IC, ×2 ICs |
| C11-C12 | 2 | 10µF | 0603 | GRM188R61A106ME69D | Murata | RHD2164 VDD bulk | Per IC |
| C13-C14 | 2 | 10µF | 0603 | GRM188R61A106ME69D | Murata | RHD2164 VDDD bulk | Per IC |
| C15-C16 | 2 | 10µF | 0603 | GRM188R61A106ME69D | Murata | VREF caps | Per IC |
| C17 | 1 | 100nF | 0402 | GRM155R71H104KE14D | Murata | VIN HF decoupling | Near B2B |
| C18 | 1 | 10µF | 0603 | GRM188R61A106ME69D | Murata | VIN bulk | Near B2B |
| C19 | 1 | 1µF | 0402 | GRM155R61A105KE15D | Murata | AVDD LDO input | |
| C20 | 1 | 1µF | 0402 | GRM155R61A105KE15D | Murata | AVDD LDO output | |
| C21 | 1 | 100nF | 0402 | GRM155R71H104KE14D | Murata | DVDD_AFE HF | After FB2 |
| C22 | 1 | 10µF | 0603 | GRM188R61A106ME69D | Murata | DVDD_AFE bulk | After FB2 |

# DELETED from Rev 1.0:
# C15-C18 (MCU decoupling) — no MCU
# C20-C21 (LVDS driver decoupling) — no LVDS
# C27-C28, C29 (oscillator decoupling) — no oscillator

# ═══════════════════════════════════════════════════════════════════════════════
#                              PASSIVES - BIAS/TERMINATION/FILTER
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Part Number | Manufacturer | Description | Notes |
|-----|-----|-------|---------|-------------|--------------|-------------|-------|
| R1-R128 | 128 | 10MΩ | 0402 | - | - | Input bias resistors | High value, low noise |
| R_D5 | 1 | 33Ω | 0402 | - | - | MISO1_A damping | DNI, at U1 output |
| R_D6 | 1 | 33Ω | 0402 | - | - | MISO1_B damping | DNI, at U1 output |
| R_D7 | 1 | 33Ω | 0402 | - | - | MISO2_A damping | DNI, at U2 output |
| R_D8 | 1 | 33Ω | 0402 | - | - | MISO2_B damping | DNI, at U2 output |
| R_SCLK | 1 | 33Ω | 0402 | - | - | SCLK damping (optional) | DNI, at B2B input |
| FB1 | 1 | 600Ω@100MHz | 0402 | BLM15AG601SN1D | Murata | Analog/digital bridge | |
| FB2 | 1 | 600Ω@100MHz | 0402 | BLM15AG601SN1D | Murata | DVDD_AFE filter | |

# DELETED from Rev 1.0:
# R129 (22Ω clock termination) — no local oscillator

# ═══════════════════════════════════════════════════════════════════════════════
#                              TEST POINTS
# ═══════════════════════════════════════════════════════════════════════════════

| Ref | Qty | Value | Package | Description |
|-----|-----|-------|---------|-------------|
| TP1 | 1 | TP_VIN | 1mm pad | Input voltage from carrier |
| TP2 | 1 | TP_AVDD | 1mm pad | Analog rail (noise measurement) |
| TP3 | 1 | TP_DVDD_AFE | 1mm pad | Filtered digital rail |
| TP4 | 1 | TP_VREF | 1mm pad | Reference voltage |
| TP5 | 1 | TP_GND | 1mm pad | Ground reference |
| TP6 | 1 | TP_SCLK | 1mm pad | SPI clock from carrier |
| TP7 | 1 | TP_MISO1A | 1mm pad | DDR MISO A from U1 |
| TP8 | 1 | TP_MISO1B | 1mm pad | DDR MISO B from U1 |
| TP9 | 1 | TP_MISO2A | 1mm pad | DDR MISO A from U2 |
| TP10 | 1 | TP_CAL | 1mm pad | Cal injection signal |

# DELETED from Rev 1.0:
# TP_DVDD — replaced by TP_DVDD_AFE
# TP_LVDS — no LVDS on headstage

# ═══════════════════════════════════════════════════════════════════════════════
#                              SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

Total unique part numbers: ~25 (was ~35)
Total component count: ~310 (was ~350)

REMOVED COMPONENTS (Rev 1.0 → 2.0):
  - STM32G431CBU6 (MCU)
  - DS90LV047A (LVDS quad driver)
  - AP2112K-3.3 (digital LDO)
  - DSC1001DI2-020 (20MHz TCXO)
  - NC7SZ125 (clock buffer)
  - TC2030-IDC (SWD debug connector)
  - 4× PESD5V0S2BT (LVDS ESD)
  - ~15 passive components (MCU/LVDS/oscillator decoupling)

ADDED COMPONENTS (Rev 2.0):
  - FB2 (DVDD_AFE ferrite filter)
  - R_D5-R_D8 (MISO series damping, DNI)
  - R_SCLK (SCLK damping, DNI)
  - C_DVDD1, C_DVDD2 (DVDD_AFE decoupling)

Critical lead time items:
  - RHD2164: 8-12 weeks (order early, contact Intan directly)
  - Omnetics NPD-36: 4-6 weeks
  - Samtec QSH-030: 2-4 weeks

Estimated board cost (prototype qty 5):
  - PCB (6-layer, controlled impedance): $150-300
  - Assembly: $400-600 (fewer components)
  - Components: $300-450 per board (no MCU/LVDS/oscillator)
  - Total per board: ~$350-450 (at qty 5)

# ═══════════════════════════════════════════════════════════════════════════════
#                              PROCUREMENT NOTES
# ═══════════════════════════════════════════════════════════════════════════════

1. RHD2164: Contact Intan Technologies directly (intantech.com)
   - Requires NDA for detailed datasheet
   - Minimum order quantity may apply

2. Omnetics connectors: Long lead time, order with PCB
   - Get mating connectors (NSD-36) at same time

3. ESD diodes: Verify leakage spec (<1nA) - critical for neural signals

4. Capacitors: Use X5R or X7R dielectric, not Y5V

5. 10MΩ resistors: May need to parallel 2× 5MΩ if 10MΩ not available in 0402

6. Samtec QSH-030: Verify mating connector (QTH-030) is compatible
   with carrier board design
