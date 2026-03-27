# Frame Format Specification — MEA DAQ Stream Protocol
# Rev 1.0 · 2026-03-01
# Scope: meadaq-64ch FPGA → USB host stream

## Overview

Every USB byte stream consists of back-to-back fixed-size **frames**.
Each frame is self-describing, integrity-checked, and loss-detectable.

**Key properties:**
- Fixed frame size: configurable per build (default 256B)
- CRC16-CCITT integrity check over entire frame (excluding CRC field)
- Monotonic 32-bit frame ID for gap detection (dropped frames)
- Status byte reports FIFO pressure, SPI errors, drop history
- No partial frames ever enter the FIFO (atomic commit contract preserved)

## Frame Layout (256 bytes, default configuration)

```
Offset  Size  Field               Description
──────  ────  ──────────────────  ─────────────────────────────────────
  0       2   magic               0xA5, 0x5A — sync word
  2       1   version             Protocol version (0x01)
  3       1   status              Status flags (see below)
  4       4   frame_id            Monotonic 32-bit counter (big-endian)
  8       4   timestamp           Sample timestamp, 32-bit (big-endian)
                                   Units: sample periods since reset
                                   At 20 kSPS: wraps every ~59.6 hours
 12       2   channel_count       Number of ADC channels in payload (big-endian)
 14       2   sample_rate_code    Encoded sample rate (0x0001 = 20 kSPS,
                                   0x0002 = 30 kSPS, etc.)
 ─── HEADER: 16 bytes ────────────────────────────────────────────────

 16     128   adc_payload         64 channels × 16-bit, big-endian
                                   Byte 16-17: CH01 [15:8], CH01 [7:0]
                                   Byte 18-19: CH02 [15:8], CH02 [7:0]
                                   ...
                                   Byte 142-143: CH64 [15:8], CH64 [7:0]

144       6   aux_payload         3 auxiliary ADC channels × 16-bit
                                   Byte 144-145: AUX_IN1
                                   Byte 146-147: AUX_IN2
                                   Byte 148-149: AUX_IN3

150       4   diag_fifo_fill      FIFO fill level at frame commit (32-bit)
154       4   diag_drop_count     Cumulative dropped frames (32-bit)
158       4   diag_stall_count    Cumulative FT2232H stall cycles (32-bit)
162       4   diag_spi_err_count  Cumulative SPI errors (32-bit)

 ─── DIAGNOSTICS: 16 bytes ───────────────────────────────────────────

166      88   reserved            Zero-padded (future: impedance, stats)

 ─── RESERVED: 88 bytes ──────────────────────────────────────────────

254       2   crc16               CRC16-CCITT over bytes 0–253 (big-endian)

 ─── TOTAL: 256 bytes ────────────────────────────────────────────────
```

## Status Byte (offset 3)

```
Bit  Name                 Description
───  ───────────────────  ──────────────────────────────────────────
 7   drop_since_last      1 = at least one frame was dropped between
                           this frame and the previous committed frame
 6   fifo_half_full       1 = FIFO fill > 50% at commit time
 5   fifo_near_full       1 = FIFO fill > 75% at commit time
 4   spi_crc_err          1 = SPI CRC error detected this cycle
                           (RHD2164 returns CRC in aux word)
 3   spi_timeout          1 = SPI cycle did not complete in expected
                           time (stuck MISO or clock failure)
 2   overflow_sticky      1 = FIFO overflow has occurred since reset
                           (latched, never clears until reset)
 1   reserved             0
 0   reserved             0
```

## CRC16-CCITT Specification

- Polynomial: 0x1021 (x^16 + x^12 + x^5 + 1)
- Initial value: 0xFFFF
- Input: bytes 0–253 (254 bytes), MSB first
- No final XOR
- Result stored big-endian at bytes 254–255

## Frame ID Contract

- `frame_id` increments by 1 for every frame **produced** (committed OR dropped)
- Host detects drops by checking: `frame_id[n+1] - frame_id[n] > 1`
- The gap size equals the number of dropped frames
- `frame_id` wraps at 2^32 (handled by unsigned arithmetic)
- On FPGA reset, `frame_id` starts at 0

## Atomic Commit Contract (unchanged)

- frame_packer checks `wr_free_bytes >= FRAME_SIZE` before serialization
- If insufficient space: entire frame dropped, `drop_counter++`, `frame_id++`
- No partial frame writes enter the FIFO, ever

## Backward Compatibility

The magic word `0xA5 0x5A` and version byte allow the host parser to:
1. Scan for frame sync (find magic in byte stream)
2. Verify protocol version before parsing
3. Resynchronize after USB glitches or partial reads

If the host receives raw 128B frames (old format, no magic), it can
detect this by the absence of the magic word and fall back to legacy
parsing. This is a safety net, not a primary path.

## FIFO Impact

- Frame size: 256 bytes (vs 128 bytes previously)
- FIFO depth: 32,768 bytes
- Frames buffered: 32768 / 256 = **128 frames = 6.4 ms** @ 20 kSPS
- USB HS bulk packet: 512 bytes = **2 frames** (clean alignment)
- Margin: USB NAK stalls typically 1–3 ms, OS scheduling 5–8 ms
- 6.4 ms > typical stall but < worst-case OS scheduling
- **Mitigation**: if 6.4ms is too tight, reduce reserved to 0 and
  use 144B frame (32768/144 = 227 frames = 11.3ms). Or go to 192B.

## Build-Time Configuration

The frame size is a Verilog parameter:
```verilog
parameter FRAME_SIZE = 256;   // bytes
parameter HDR_SIZE   = 16;    // bytes
parameter CRC_SIZE   = 2;     // bytes
parameter ADC_BYTES  = 128;   // 64 channels × 2
```

For the 14-channel headstage variant:
```verilog
parameter FRAME_SIZE = 64;    // bytes
parameter HDR_SIZE   = 16;    // bytes
parameter CRC_SIZE   = 2;     // bytes
parameter ADC_BYTES  = 28;    // 14 channels × 2
// Remaining: 64 - 16 - 28 - 2 = 18 bytes aux/diag/reserved
```
