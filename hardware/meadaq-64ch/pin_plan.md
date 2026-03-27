# Pin Plan — MEA DAQ 64-Channel (meadaq-64ch)

**Rev 0.1** · 2025-06-28 · **LOCKED — do not change without ECO**

---

## 1. iCE40UP5K (SG48) → RHD2164 SPI Bus

| FPGA Pin | Ball | iCE40 Name | Net        | Dir | RHD2164 Pin | Notes              |
|----------|------|------------|------------|-----|-------------|--------------------|
| 47       | —    | IOB_2      | SPI_SCLK   | →   | 73 (SCLK)   | 24 MHz, CPOL=0     |
| 46       | —    | IOB_3b     | SPI_MOSI   | →   | 75 (MOSI)   | MSB first           |
| 45       | —    | IOB_4a     | SPI_MISO   | ←   | 74 (MISO)   | Directly from AFE   |
| 44       | —    | IOB_5b     | SPI_CS_N   | →   | 72 (CS_N)   | Active low          |
| 48       | —    | IOB_0a     | AFE_INTAN_N| ←   | 65 (INTAN_N)| Chip-present flag   |

**Bank 0**, VCCIO = 3.3V (VDD_A_3V3 or VDD_D_3V3 — both are 3.3V logic)

SPI protocol:
- Mode 0: CPOL=0, CPHA=0
- Max clock: 24 MHz (RHD2164 limit)
- Frame: 16-bit command out, 16-bit result in (full-duplex)
- Conversion cycle: 68 words × 16 bits = 1088 bits = 45.3 µs @ 24 MHz
- Period: 50 µs → 20 kS/s per channel

---

## 2. iCE40UP5K (SG48) → FT2232H Synchronous FIFO Bus

| FPGA Pin | Ball | iCE40 Name | Net        | Dir | FT2232H Pin | Function           |
|----------|------|------------|------------|-----|-------------|--------------------|
| 36       | —    | IOT_36b    | FT_D0      | ↔   | 16 (ADBUS0) | Data bit 0         |
| 37       | —    | IOT_37a    | FT_D1      | ↔   | 17 (ADBUS1) | Data bit 1         |
| 38       | —    | IOT_38b    | FT_D2      | ↔   | 18 (ADBUS2) | Data bit 2         |
| 39       | —    | IOT_39a    | FT_D3      | ↔   | 19 (ADBUS3) | Data bit 3         |
| 40       | —    | IOT_41a    | FT_D4      | ↔   | 21 (ADBUS4) | Data bit 4         |
| 41       | —    | IOT_42b    | FT_D5      | ↔   | 22 (ADBUS5) | Data bit 5         |
| 42       | —    | IOT_43a    | FT_D6      | ↔   | 23 (ADBUS6) | Data bit 6         |
| 43       | —    | IOT_44b    | FT_D7      | ↔   | 24 (ADBUS7) | Data bit 7         |
| 34       | —    | IOT_46b    | FT_RXF_N   | ←   | 26 (ACBUS0) | RX FIFO not empty  |
| 32       | —    | IOT_48b    | FT_TXE_N   | ←   | 27 (ACBUS1) | TX FIFO not full   |
| 31       | —    | IOT_49a    | FT_WR_N    | →   | 29 (ACBUS3) | Write strobe       |
| 28       | —    | IOT_50b    | FT_RD_N    | →   | 28 (ACBUS2) | Read strobe        |
| 27       | —    | IOT_51a    | FT_OE_N    | →   | 33 (ACBUS6) | Output enable      |
| 35       | —    | IOT_45a    | FT_CLKOUT  | ←   | 32 (ACBUS5) | 60 MHz ref clock   |
| 33       | —    | IOT_34b    | FT_SIWU_N  | →   | 30 (ACBUS4) | Send immed/wakeup  |

**Bank 1**, VCCIO = 3.3V (VDD_D_3V3)

Timing (synchronous 245 FIFO mode):
- All signals sampled on CLKOUT rising edge
- Write: assert WR_N low, place data on D[0:7], captured on next CLKOUT↑
- Read: assert OE_N low → RD_N low, data valid on next CLKOUT↑
- FT_SIWU_N: pulse low to flush partial USB packets immediately

---

## 3. iCE40UP5K (SG48) — UART + LEDs + Misc

| FPGA Pin | Ball | iCE40 Name | Net         | Dir | Dest             | Notes              |
|----------|------|------------|-------------|-----|------------------|--------------------|
| 12       | —    | IOB_22a    | UART_TX     | →   | FT2232H pin 39   | FPGA→host debug    |
| 11       | —    | IOB_23b    | UART_RX     | ←   | FT2232H pin 38   | Host→FPGA debug    |
| 10       | —    | IOB_24a    | LED_STATUS  | →   | Green LED + 330Ω | Heartbeat/stream   |
| 9        | —    | IOB_25b    | LED_ERROR   | →   | Red LED + 330Ω   | FIFO overflow/err  |
| 13       | —    | IOB_20a    | DBG_TP1     | →   | Test point pad    | Debug scope probe  |
| 6        | —    | IOB_16a    | OSC_48MHZ   | ←   | 48 MHz MEMS osc  | PLL reference      |

**Bank 2**, VCCIO = 3.3V (VDD_D_3V3)

---

## 4. iCE40UP5K — Configuration Pins

| FPGA Pin | iCE40 Name | Net          | Dir | Dest               | Notes             |
|----------|------------|--------------|-----|--------------------|--------------------|
| 8        | CRESET_B   | PWR_GOOD     | ←   | 1.2V LDO PG output| Active-low reset   |
| 7        | CDONE      | CDONE        | →   | TP + optional LED  | Config complete    |
| 16       | SPI_SS     | CFG_SS       | —   | W25Q32JV CS# + 10kΩ| Config flash select|
| 15       | SPI_SCK    | CFG_SCK      | —   | W25Q32JV CLK      | Config clock       |
| 14       | SPI_SI     | CFG_SI       | —   | W25Q32JV DI       | Config data in     |
| 17       | SPI_SO     | CFG_SO       | —   | W25Q32JV DO       | Config data out    |

---

## 5. FT2232H — Channel B UART

| FT2232H Pin | Function | Net      | Dir | Dest          | Notes            |
|-------------|----------|----------|-----|---------------|------------------|
| 38 (BDBUS0) | TXD      | UART_RX  | →   | FPGA pin 11   | FT sends to FPGA|
| 39 (BDBUS1) | RXD      | UART_TX  | ←   | FPGA pin 12   | FPGA sends to FT|

Note: FT2232H TXD = FPGA's UART_RX (crossover naming convention)

---

## 6. FT2232H — USB PHY + Crystal

| FT2232H Pin | Function | Net        | Dir | Dest            | Notes            |
|-------------|----------|------------|-----|-----------------|------------------|
| 7  (DM)     | USB D-   | USB_DM     | ↔   | USB-C connector | Via 27Ω series R |
| 8  (DP)     | USB D+   | USB_DP     | ↔   | USB-C connector | Via 27Ω series R |
| 1  (OSCI)   | Xtal in  | XTAL_12M_A | ←  | 12 MHz crystal  | 18pF load        |
| 2  (OSCO)   | Xtal out | XTAL_12M_B | →  | 12 MHz crystal  | 27pF load caps   |

---

## 7. FT2232H — EEPROM (93C46)

| FT2232H Pin | Function | Net       | Dir | 93C46 Pin | Notes              |
|-------------|----------|-----------|-----|-----------|--------------------|
| 44 (EECS)   | CS       | EE_CS     | →   | 1 (CS)    | Chip select        |
| 45 (EESK)   | Clock    | EE_CLK    | →   | 2 (CLK)   | Serial clock       |
| 46 (EEDI)   | Data in  | EE_DI     | →   | 3 (DI)    | Data to EEPROM     |
| 47 (EEDO)   | Data out | EE_DO     | ←   | 4 (DO)    | Data from EEPROM   |

---

## 8. RHD2164 — Electrode Channel Mapping

| FPC Pin | Electrode | Net        | RHD2164 Pin | RHD2164 Input | Channel |
|---------|-----------|------------|-------------|---------------|---------|
| 1       | E01       | ELEC_A00   | 2           | IN_A_00       | CH01    |
| 2       | E02       | ELEC_A01   | 3           | IN_A_01       | CH02    |
| ...     | ...       | ...        | ...         | ...           | ...     |
| 16      | E16       | ELEC_A15   | 17          | IN_A_15       | CH16    |
| 17      | E17       | ELEC_A16   | 21          | IN_A_16       | CH17    |
| ...     | ...       | ...        | ...         | ...           | ...     |
| 32      | E32       | ELEC_A31   | 36          | IN_A_31       | CH32    |
| 33      | E33       | ELEC_B00   | 40          | IN_B_00       | CH33    |
| ...     | ...       | ...        | ...         | ...           | ...     |
| 48      | E48       | ELEC_B15   | 55          | IN_B_15       | CH48    |
| 49      | E49       | ELEC_B16   | 59          | IN_B_16       | CH49    |
| ...     | ...       | ...        | ...         | ...           | ...     |
| 64      | E64       | ELEC_B31   | 74          | IN_B_31       | CH64    |
| 65      | REF       | ELEC_REF   | 64          | REF_ELEC      | —       |
| 66      | GND       | GND_A      | —           | —             | —       |
| 67      | SHIELD    | GND_SHIELD | —           | —             | —       |
| 68      | AUX_STIM  | AUX_STIM   | —           | —             | —       |

---

## 9. Power Net Assignments

| Net         | Voltage | Source          | Loads                              | Budget |
|-------------|---------|------------------|------------------------------------|--------|
| VBUS_5V     | 5.0V    | USB-C VBUS      | All LDO inputs                     | 125 mA |
| VDD_A_1V8   | 1.8V    | TPS7A20 (U1)    | RHD2164 VDD (core)                 | 35 mA  |
| VDD_A_3V3   | 3.3V    | TPS7A49 (U2)    | RHD2164 VDD_IO, ESD arrays VCC     | 15 mA  |
| VDD_D_3V3   | 3.3V    | AP2112K (U3)    | FPGA VCCIO, FT2232H VCCIO, flash   | 60 mA  |
| VDD_D_1V2   | 1.2V    | 1.2V LDO (U4)   | FPGA VCC core                      | 20 mA  |
| GND_A       | 0V      | Analog ground    | RHD2164, analog LDOs, ESD, FPC.66  | —      |
| GND_D       | 0V      | Digital ground   | FPGA, FT2232H, digital LDO         | —      |

Star point: under FPGA, single via stitching GND_A ↔ GND_D (v1)

---

## 10. Unused / Reserved Pins

| Component   | Unused Pins                        | Treatment              |
|-------------|------------------------------------|------------------------|
| RHD2164     | auxin1(37), auxin2(71), auxin3(70) | NC, leave floating     |
|             | auxout1-3 (69,68,67)               | NC, leave floating     |
|             | ELEC_TEST(66)                      | NC (or TP for test)    |
| iCE40UP5K   | All remaining IOx pins             | Internal pulldown (RTL)|
| FT2232H     | BDBUS2-7, BCBUS0-7                 | NC, internal pulldown  |
|             | ACBUS7(34)                         | 10kΩ pullup (PWRSAV#)  |

---

## 11. ESD Array Assignment

| Device | U# | Input Channels       | FPC Pins | TPD8S009 Pins |
|--------|----|----------------------|----------|---------------|
| Bank A | U10| ELEC_A[0:7]   CH01-08| 1-8      | IN1-8 → OUT1-8|
| Bank A | U11| ELEC_A[8:15]  CH09-16| 9-16     | IN1-8 → OUT1-8|
| Bank A | U12| ELEC_A[16:23] CH17-24| 17-24    | IN1-8 → OUT1-8|
| Bank A | U13| ELEC_A[24:31] CH25-32| 25-32    | IN1-8 → OUT1-8|
| Bank B | U14| ELEC_B[0:7]   CH33-40| 33-40    | IN1-8 → OUT1-8|
| Bank B | U15| ELEC_B[8:15]  CH41-48| 41-48    | IN1-8 → OUT1-8|
| Bank B | U16| ELEC_B[16:23] CH49-56| 49-56    | IN1-8 → OUT1-8|
| Bank B | U17| ELEC_B[24:31] CH57-64| 57-64    | IN1-8 → OUT1-8|

---

## 12. Reference Designator Summary

| Ref  | Part               | Package     | Sheet | Description                |
|------|--------------------|-------------|-------|----------------------------|
| J1   | USB-C receptacle   | Mid-mount   | 01    | USB 2.0 UFP connector     |
| U1   | TPS7A20 (1.8V)    | SOT-23-5    | 01    | Analog 1.8V LDO           |
| U2   | TPS7A49 (3.3V)    | SOT-23-5    | 01    | Analog 3.3V LDO           |
| U3   | AP2112K-3.3        | SOT-23-5    | 01    | Digital 3.3V LDO          |
| U4   | 1.2V LDO           | SOT-23-5    | 01    | FPGA core 1.2V LDO        |
| U5   | RHD2164            | QFN-76      | 02    | 64-ch biopotential AFE    |
| U6   | iCE40UP5K          | QFN-48 SG48 | 03   | FPGA                       |
| U7   | W25Q32JV           | SOIC-8      | 03    | 32Mbit config flash       |
| U8   | FT2232H            | QFN-64      | 04    | USB 2.0 HS bridge         |
| U9   | 93C46              | SOIC-8      | 04    | FT2232H config EEPROM     |
| U10-17| TPD8S009 ×8       | USON-18     | 05    | 8ch TVS ESD arrays        |
| U18  | Si8661 (DNP)       | SOIC-16W    | 06    | Digital isolator (v2)     |
| J2   | JTAG header        | 2×5 1.27mm  | 03    | FPGA programming          |
| J3   | FPC 68-pin         | 0.5mm ZIF   | 05    | MEA electrode connector   |
| Y1   | 48 MHz MEMS osc    | 2520        | 03    | FPGA PLL reference        |
| Y2   | 12 MHz crystal     | 3215        | 04    | FT2232H oscillator        |
| FB1  | Ferrite bead       | 0402        | 01    | Analog VBUS isolation     |
