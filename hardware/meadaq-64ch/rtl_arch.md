# FPGA RTL Architecture — MEA DAQ 64-Channel

**Rev 0.8** · 2025-06-01 · **CDR-4 R4.0 — 256B frame format with header + CRC16 + diagnostics**

Target: Lattice iCE40UP5K (SG48) · Toolchain: Yosys + nextpnr-ice40 + icestorm

> **Changes from Rev 0.6 (CDR-3 R3.2 — prefetch removal + safety invariants):**
> - §5 S_WRITE→S_FETCH prefetch **removed entirely**. S_WRITE always → S_IDLE.
>   POP occurs in exactly ONE place: S_IDLE→S_FETCH. No special-case gating.
>   Throughput drops from 60/4=15 MB/s to 60/5=12 MB/s. Margin still 4.7×.
> - §4 added **rd_empty safety invariant**: `rd_empty` in the read domain
>   may be pessimistic (stall) but NEVER optimistic (false not-empty).
>   Gray-code CDC proof included.
> - §5 S_HOLD: **explicit documentation** that data_reg is loaded even when
>   TXE is high (pre-load with pending_valid=1). Flagged DO NOT OPTIMIZE.
> - §5 added **A7** (rd_en only in S_IDLE), **A8** (pop≤push underflow guard),
>   **A9** (direct pop-on-empty edge detector). 9 assertions total (A1–A9).
> - §5 added **compact FSM output spec** — 3 lines per state, RTL contract.
> - §5 invariant #6 simplified: rd_en ONLY in S_IDLE. Period.
> - §5 invariant #7 simplified: pending_valid==0 is now trivially true
>   at the only POP site (S_IDLE entry requires pending_valid==0).
>
> **Changes from Rev 0.5 (CDR-3 R3.1b — registered rd_en pipeline):**
> - §4 FIFO read contract: **simplified to 1-cycle latency.** The FIFO's
>   own contract is: `rd_en` sampled at edge N ⇒ `rd_data` valid at N+1.
>   The registered `rd_en` pipeline (FIFO sees rd_en 1 edge after FSM
>   registers it) is the bridge's concern, documented in §5.
> - §4 added **SB_SPRAM256KA implementation note** (CS must stay high during
>   and after read until rdata captured).
> - §5 K-timing table: **corrected** — FIFO sees rd_en at K+1 (its "edge N"),
>   rdata valid at K+2 (its "edge N+1"), data_reg latched at K+3 in S_HOLD.
> - §5 "Why S_WAIT exists" **rewritten**: clearly separates the FIFO's 1-cycle
>   contract from the bridge's registered-output pipeline stage.
>
> **Changes from Rev 0.4 (CDR-3 R3.1 — addresses 5-point review):**
> - §4 FIFO read contract: **rewritten with edge-based timing** (rising edges
>   of rd_clk). Now defines: `rd_en` at edge N ⇒ `rd_data` valid at edge N+1.
> - §4 non-FWFT rationale: **removed false "SPRAM→non-FWFT" proof**. Non-FWFT
>   is now declared as an architectural choice with 4 explicit behavioral
>   requirements the FIFO RTL must enforce.
> - §4 empty-domain: **rd_en gating moved into FIFO read-domain logic** itself,
>   not just consumer convention. `rd_ptr_next` gated by `!rd_empty`.
> - §4 added **empty→notempty simulation test** requirement (A6).
> - §5 S_WAIT: **rewritten with edge-timing table** showing exactly when rdata
>   becomes valid relative to rd_en assertion.
> - §5 added **invariant #7**: `fifo_rd_en` NEVER asserted when `pending_valid=1`.
>   Verified in truth table row-by-row.
> - §5 truth table: **rewritten** with explicit pend_valid column values (not
>   "(hold)"), annotated rd_en POP markers, and invariant #7 verification note.
> - §5 cycle trace: **rewritten** with per-edge FIFO/FT action annotations and
>   explicit edge-timing verification block for byte[0].
> - Assertions: 3 → **6** (A1–A3 in tb_fifo_bridge, A4+A6 in tb_async_fifo,
>   A5 in tb_fifo_bridge). All 8 are gate-to-fab.
>
> **Changes from Rev 0.3 (CDR-3 R3):**
> - async_fifo §4: added FIFO read contract — non-FWFT, 1-cycle latency
> - fifo_bridge §5: 4-state → 5-state FSM (IDLE/FETCH/WAIT/HOLD/WRITE)
> - Added write timing contract, byte ownership rule, 3 assertions (A1–A3)
> - All FTDI QFN-64 pin numbers verified against pin_plan.md + schematic
>
> **Changes from Rev 0.2 (CDR-3 R2):**
> - fifo_bridge: 3-state pipeline → 4-state FSM (IDLE/FETCH/HOLD/WRITE)
> - WR_N: continuous low → pulsed for exactly 1 CLKOUT cycle per byte
> - data_reg: loaded in FETCH+WRITE → loaded ONLY in S_HOLD, frozen in S_WRITE
> - TXE_N: "full after this write" assumption → strict gating, WR never asserted when txe_n_r=1
> - Stall counter: IDLE-only → counts in all states where pending + txe_n_r=1
> - Unused FTDI pins: "floating acceptable" → 10kΩ pulldowns on all unused BDBUS/BCBUS
> - Pin 34 error: ft_rxf_n was listed as pin 34 → corrected to pin 26 (ACBUS0)
> - pending_valid flag added to track data_reg valid state across stalls
>
> **Changes from Rev 0.1 (CDR-1):**
> - Async FIFO depth: 512B (BRAM) → 32,768B (SPRAM) — 12 ms buffering
> - Frame format: 136B (sync+hdr+data+CRC) → 128B pure data + sideband metadata
> - STATUS register: 3 bits → 8 bits, added FIFO_LEVEL/DROP_CNT/STALL_CNT regs
> - SPI timing: confirmed 68 words @ 24 MHz = 45.33 µs, 4.67 µs margin, LOCKED
> - CDC boundary: confirmed correct (async_fifo gray-code, fifo_bridge in clk_60m)
> - FT2232H CLKOUT: confirmed 60 MHz fixed (12 MHz × 5 internal PLL)

---

## Block Diagram

```
                    ┌─────────────────────────────────────────────────┐
                    │              iCE40UP5K  (top.v)                 │
                    │                                                 │
  48 MHz OSC ──────►│ PLL ──► clk_48m ──► clk_24m (÷2)              │
                    │         clk_60m ◄── FT_CLKOUT (external)       │
                    │                                                 │
                    │  ┌──────────┐    ┌──────────────┐              │
  SPI_MOSI ◄────────│──│          │    │              │              │
  SPI_MISO ─────────│─►│ spi_     │───►│ frame_       │              │
  SPI_SCLK ◄────────│──│ master   │    │ packer       │              │
  SPI_CS_N ◄────────│──│          │    │              │              │
                    │  └──────────┘    └──────┬───────┘              │
                    │       clk_24m           │ clk_48m              │
                    │                         │                      │
                    │                   ┌─────▼───────┐              │
                    │                   │  async_fifo  │              │
                    │                   │  32KB SPRAM  │              │
                    │                   │  (48→60 MHz  │              │
                    │                   │   CDC)       │              │
                    │                   └─────┬───────┘              │
                    │                         │ clk_60m              │
                    │                   ┌─────▼───────┐              │
  FT_D[0:7] ◄──────│───────────────────│ fifo_bridge  │              │
  FT_RXF_N ────────│──────────────────►│ (sync FIFO   │              │
  FT_TXE_N ────────│──────────────────►│  protocol)   │              │
  FT_WR_N  ◄───────│───────────────────│              │              │
  FT_RD_N  ◄───────│───────────────────│              │              │
  FT_OE_N  ◄───────│───────────────────│              │              │
  FT_CLKOUT────────│──────────────────►│              │              │
  FT_SIWU_N◄───────│───────────────────│              │              │
                    │                   └──────────────┘              │
                    │                                                 │
                    │  ┌──────────┐    ┌──────────────┐              │
  UART_TX ◄─────────│──│ uart_tx  │    │              │              │
  UART_RX ──────────│─►│ uart_rx  │◄──►│ ctrl_regs    │              │
                    │  └──────────┘    └──────────────┘              │
                    │                                                 │
  LED_STATUS ◄──────│── heartbeat counter (1 Hz toggle)              │
  LED_ERROR  ◄──────│── fifo_overflow | spi_timeout                  │
  AFE_INTAN_N──────│── chip-present check                           │
  DBG_TP1   ◄──────│── frame_sync pulse (50 µs period)              │
                    │                                                 │
                    └─────────────────────────────────────────────────┘
```

---

## Module Inventory

### 1. `pll_cfg` — Clock Generation
- **Input**: `clk_48m_ext` (48 MHz MEMS oscillator, pin 6)
- **Output**: `clk_48m` (system clock), `clk_24m` (SPI clock), `pll_locked`
- **Implementation**: iCE40 SB_PLL40_CORE hard macro
- **Note**: `clk_60m` comes directly from FT_CLKOUT (pin 35), not from PLL

### 2. `spi_master` — RHD2164 SPI Controller
- **Clock domain**: `clk_24m`
- **Priority**: **HIGHEST. This module is king. Nothing may stall it.**
- **Ports**:

| Port         | Width | Dir | Description                     |
|--------------|-------|-----|---------------------------------|
| clk_24m      | 1     | in  | 24 MHz SPI clock               |
| rst_n        | 1     | in  | Active-low reset               |
| spi_mosi     | 1     | out | To RHD2164 MOSI               |
| spi_miso     | 1     | in  | From RHD2164 MISO             |
| spi_sclk     | 1     | out | To RHD2164 SCLK               |
| spi_cs_n     | 1     | out | To RHD2164 CS_N               |
| sample_data  | 16    | out | Latest 16-bit response word    |
| sample_valid | 1     | out | Pulses for each valid response |
| sample_index | 7     | out | Word index 0–67 in cycle       |
| cycle_done   | 1     | out | Full 68-word cycle complete    |
| buf_bank     | 1     | out | Which ping/pong bank is filling|

- **Behavior**:
  - Sequences 68 SPI transactions per conversion cycle
  - Words 0-63: CONVERT(ch) commands — read amplifier channels 0-63
  - Words 64-66: READ(40,41,42) — read auxiliary ADC channels
  - Word 67: DUMMY (0xFFFF) — flush 2-command pipeline
  - CS_N stays asserted (low) for entire 68-word burst, no inter-word gaps
  - `cycle_done` pulses high at end of 68th word
  - Auto-restarts next cycle after 50 µs period (20 kS/s)
  - **Timing**: 68 × 16 bits ÷ 24 MHz = 45.33 µs, margin = 4.67 µs (9.3%)
  - SPI mode 0: CPOL=0, CPHA=0, MSB first, 16-bit full-duplex
  - Response pipeline: result for command N arrives at command N+2

- **Non-blocking guarantee**:
  - SPI master writes responses into a **2-deep ping-pong sample buffer**
    (2 × 64 words × 16 bits = 256 bytes, fits in 1 BRAM block).
  - Bank A fills while frame_packer reads bank B (and vice versa).
  - `buf_bank` toggles on `cycle_done`. Frame_packer has the entire next
    50 µs period to drain the completed bank.
  - SPI master has **zero downstream dependencies**. It never checks
    whether frame_packer is done. It never waits on FIFO space.
  - If frame_packer hasn't finished draining the old bank by the time
    SPI overwrites it, that's a **frame drop** (see §Frame Drop Policy).

### 3. `frame_packer` — Data Framing with Header + CRC
- **Clock domain**: `clk_48m`
- **Priority**: medium — must not stall SPI, may stall on FIFO backpressure
- **Ports**:

| Port             | Width | Dir | Description                                  |
|------------------|-------|-----|----------------------------------------------|
| clk              | 1     | in  | 48 MHz system clock                          |
| rst_n            | 1     | in  | Reset                                        |
| buf_bank         | 1     | in  | From spi_master: which bank done             |
| buf_data         | 16    | in  | Read port from ping-pong BRAM                |
| buf_addr         | 7     | out | Channel index into bank (0..66)              |
| cycle_done       | 1     | in  | From spi_master                              |
| wr_free_bytes    | 16    | in  | From async_fifo: free space (§4)             |
| fifo_wr_en       | 1     | out | Write enable to async_fifo                   |
| fifo_wr_data     | 8     | out | Write data byte to async_fifo                |
| fifo_fill_level  | 16    | in  | From async_fifo: current fill level          |
| ext_stall_count  | 32    | in  | From fifo_bridge: stall counter              |
| frame_counter    | 32    | out | Running frame count (commit + drop)          |
| drop_counter     | 32    | out | Running count of dropped frames              |
| commit_pulse     | 1     | out | 1-cycle pulse when frame fully committed     |
| commit_frame_id  | 32    | out | Frame ID of committed frame (valid at pulse) |
| drop_pulse       | 1     | out | 1-cycle pulse on whole-frame drop            |

- **Non-blocking contract**:
  - Frame_packer reads from the ping-pong bank that SPI just finished.
  - It serializes 67 samples (64 ADC + 3 AUX) into a 256-byte frame
    with header, diagnostics, zero-padded reserved, and CRC16 trailer.
  - Writes one byte per clock into async_fifo.
  - At 48 MHz, serializing 256 bytes + 3 overhead cycles = **~5.4 µs**.
    This is well within the 50 µs sample period (10.8% utilization).
  - Frame_packer never signals anything back to SPI master.

- **CRC16 submodule** (`crc16_ccitt`):
  - Byte-serial CRC16-CCITT-FALSE calculator.
  - Polynomial: 0x1021, init: 0xFFFF, no final XOR.
  - Combinational XOR matrix with registered output (1-cycle latency).
  - Interface: `init`, `valid`, `data_in[7:0]` → `crc_out[15:0]`.
  - Verified: 8/8 TB tests pass (known vector "123456789" → 0x29B1,
    self-check property, re-init, single-byte, multi-byte).

- **Frame Drop Policy**:

  A **whole-frame drop** occurs when this condition is true at the
  moment `cycle_done` fires:

  ```
  drop = (wr_free_bytes < 256)    // canonical inequality, §4
  ```

  where `wr_free_bytes` is the async_fifo write-domain free-space
  signal (pessimistic-low, see §4 fill_level invariant).

  That is the **only** trigger. On drop:
  - The entire frame is discarded. No partial writes enter the FIFO.
  - `drop_counter` increments by 1.
  - `drop_pulse` fires high for 1 cycle.
  - `frame_id` still increments — host detects gap via frame_id
    discontinuity in the received stream.
  - `drop_since_last` status bit is set (cleared on next commit).

  **Partial frame writes are forbidden.** Frame_packer pre-checks
  `wr_free_bytes >= 256` before starting serialization.

  **`commit_frame_id` definition (FROZEN):**
  `commit_frame_id[31:0]` is the frame ID of the frame whose 256 bytes
  have just been **fully pushed** into async_fifo. Valid when
  `commit_pulse=1`. Specifically:
  - `commit_pulse` fires on the cycle the CRC low byte is written.
  - `commit_frame_id` equals `frame_id` at `cycle_done` trigger time.

- **Data Frame format** (256 bytes per frame):

  ```
  Offset  Size  Field               Description
  ──────  ────  ──────────────────  ─────────────────────────────────
  0       2     Magic               0xA55A (frame sync marker)
  2       1     Version             0x01 (format version)
  3       1     Status              Bit field (see below)
  4       4     Frame ID            32-bit monotonic counter, big-endian
  8       4     Timestamp           32-bit sample counter, big-endian
  12      2     Channel Count       Number of ADC channels (64), big-endian
  14      2     Sample Rate Code    0x0001 = 20 kSPS, big-endian
  16      128   ADC Payload         64 × 16-bit samples, big-endian
  144     6     AUX Payload         3 × 16-bit aux samples, big-endian
  150     16    Diagnostics         4 × 32-bit counters (see below)
  166     88    Reserved            Zero-padded, future expansion
  254     2     CRC16               CRC16-CCITT-FALSE over bytes 0..253
  ──────  ────  ──────────────────  ─────────────────────────────────
  Total: 256 bytes.  2 frames = 512 bytes = 1 USB HS bulk packet.
  ```

  **Status byte (offset 3):**
  ```
  Bit 7: drop_since_last  — 1 if any frame was dropped since last commit
  Bit 6: fifo_half_full   — 1 if FIFO fill > 50% at frame build time
  Bit 5: fifo_near_full   — 1 if FIFO fill > 75% at frame build time
  Bit 4: spi_crc_err      — Reserved (RHD2164 SPI CRC error)
  Bit 3: spi_timeout      — Reserved (SPI timeout)
  Bit 2: overflow_sticky  — 1 if FIFO overflow ever detected (sticky)
  Bit 1:0: reserved       — 0
  ```

  **Diagnostics block (offset 150, 16 bytes):**
  ```
  Offset  Size  Field           Description
  150     4     FIFO Fill       Current FIFO fill level, big-endian
  154     4     Drop Count      Total frames dropped, big-endian
  158     4     Stall Count     USB bridge stall cycles, big-endian
  162     4     SPI Error Count Reserved (zero), big-endian
  ```

  **CRC16-CCITT-FALSE specification:**
  - Polynomial: x¹⁶ + x¹² + x⁵ + 1 (0x1021)
  - Initial value: 0xFFFF
  - Input: bytes 0..253, MSB first
  - No final XOR
  - Stored big-endian at bytes 254..255
  - Self-check: append CRC to payload, recompute → 0x0000
  - Standard check value: "123456789" → 0x29B1

  **ADC serialization mapping (byte k within ADC payload, offset 16+k):**
  - k even: `sample[k/2][15:8]` (high byte of channel k/2 + 1)
  - k odd:  `sample[k/2][7:0]`  (low byte of channel k/2 + 1)

  **Simulation test pattern (for integration TB — frame_packer_stub):**
  ```
  byte k of frame with id F = F[7:0] ^ k[7:0]
  ```
  Note: integration TB still uses the old 128B stub for async_fifo/
  fifo_bridge testing. The real frame_packer produces 256B frames.

  **Host-side frame recovery:**
  1. Scan USB bulk stream for magic 0xA55A at 256-byte boundaries.
  2. Validate CRC16 over bytes 0..253 against bytes 254..255.
  3. Detect drops via frame_id discontinuity (gap > 1).
  4. Extract telemetry from status byte and diagnostics block.
  5. Parse ADC payload (64 channels, big-endian 16-bit signed).

  **FIFO buffer budget (256B frames in 32KB SPRAM):**
  - 32768 / 256 = 128 frames = **6.4 ms** of buffering at 20 kSPS.
  - At 20 kSPS × 256 B/frame = 5.12 MB/s sustained write.
  - USB 2.0 HS bulk: ~40 MB/s → ~12.8% bandwidth utilization.

### 4. `async_fifo` — Clock Domain Crossing
- **Write clock**: `clk_48m` (from frame_packer)
- **Read clock**: `clk_60m` (from FT_CLKOUT)
- **Width**: 8 bits
- **Depth**: 32,768 entries (`ADDR_BITS=15`, pointers are `PTR_BITS=16` — 15 address + 1 wrap bit for full/empty disambiguation)
- **Implementation**: iCE40UP5K **SPRAM** block (SB_SPRAM256KA, 256 Kbit = 32 KB)
- **Flags**: `wr_full`, `rd_empty`, `overflow` (sticky), `fill_level[15:0]`, `wr_free_bytes[15:0]`
- **Purpose**: bridge between internal 48 MHz domain and FT2232H 60 MHz domain
- **Buffering**: 32,768 ÷ 128 = **256 frames = 12.8 ms** @ 20 kS/s
- **Rationale**: USB bulk NAK stalls are typically 1–3 ms; OS scheduling
  delays can reach 5–8 ms on macOS. 12.8 ms provides ≥2× safety margin.
- **Note**: SPRAM is single-port per block. True dual-port async FIFO uses
  2× SPRAM blocks (one for write, one for read via address mirroring) OR
  use 1× SPRAM + ping-pong buffer scheme with 2× 4Kb BRAM for CDC staging.

- **`fill_level` domain + safety invariant (LOCKED)**:

  **Domain:** `fill_level[15:0]` is computed in the **write clock domain**
  (`clk_48m`). It is NOT available in the read domain.

  **Definition (write domain, `clk_48m`):**

  Let `ADDR_BITS = 15`, `DEPTH = 2^ADDR_BITS = 32768`.
  Pointers are `PTR_BITS = ADDR_BITS + 1 = 16` bits wide. The extra
  MSB (bit 15) is the **wrap bit** — it distinguishes full from empty.
  Without it, `wr_ptr == rd_ptr` is ambiguous: it occurs both when
  the FIFO is empty (0 entries) and when it is full (DEPTH entries).

  ```
  wr_ptr_bin[15:0]       — advances on each accepted write (write domain)
  rd_ptr_bin[15:0]       — advances on each accepted read (read domain),
                           then Gray-synchronized into clk_48m as
                           rd_ptr_bin_sync[15:0]

  fill_level[15:0] = wr_ptr_bin[15:0] - rd_ptr_bin_sync[15:0]
                                        // 16-bit unsigned subtraction
  ```
  Expected operating range: `0..DEPTH` (0..32768). Values outside this
  range indicate a FIFO protocol violation (writes while full or reads
  while empty). The range is enforced by the FIFO control logic
  (`wr_full` gates writes, `rd_empty` gates reads), not by the
  subtraction itself.

  The 16-bit subtraction naturally handles pointer wrap: if `wr_ptr_bin`
  has wrapped past bit 15 but `rd_ptr_bin_sync` has not, the unsigned
  difference correctly yields the occupancy. This is the standard
  Cummings dual-clock FIFO technique (SNUG 2002).

  At empty: `wr_ptr_bin == rd_ptr_bin_sync` → `fill_level == 0`.
  At full:  pointers differ by exactly DEPTH → `fill_level == DEPTH`.
  These are distinct states because the wrap bit participates in the
  subtraction.

  **Capacity:** This FIFO uses **true DEPTH** capacity (32768 entries),
  NOT DEPTH−1. Full and empty are disambiguated by the wrap bit, not
  by sacrificing one entry.

  **Full detection (write domain, Cummings wrap-bit inversion):**
  ```
  wr_full = (next_wr_ptr_gray[PTR_BITS-1:PTR_BITS-2]
              == ~rd_ptr_gray_sync[PTR_BITS-1:PTR_BITS-2])
         && (next_wr_ptr_gray[PTR_BITS-3:0]
              ==  rd_ptr_gray_sync[PTR_BITS-3:0])
  ```
  The top two Gray bits are inverted-equal; the remaining bits are
  equal. This is the standard SNUG 2002 full detection. It must use
  the **Gray-code** pointers directly, not the binary conversion.

  **Empty detection (read domain):**
  ```
  rd_empty = (rd_ptr_gray == wr_ptr_gray_sync)
  ```

  **Safety property — pessimistic-high (NEVER optimistic-low):**

  > `fill_level` is an **upper bound** on occupancy as observed from
  > the write domain.

  The synchronizer lag means `rd_ptr_bin_sync` lags behind the real
  `rd_ptr_bin` by 2–3 `clk_48m` cycles. This makes the subtraction
  overestimate occupancy (pessimistic-high). Overestimation may
  trigger early frame drops — a throughput cost, not a correctness bug.

  The critical invariant is that `fill_level` is **never** optimistic-low
  (never underestimates occupancy), because `rd_ptr_bin` can only advance
  forward, and the synchronized copy lags behind — the write domain always
  sees an equal-or-older read pointer, never a newer one.

  **If `fill_level` is ever optimistic-low, `frame_packer` may begin
  a 128-byte serialization into a FIFO that cannot hold it, causing
  overflow and partial frame corruption. This is the one failure mode
  that the entire drop policy exists to prevent.**

  **`wr_free_bytes` (derived, write domain):**
  ```
  wr_free_bytes[15:0] = 16'd32768 - fill_level[15:0]   // explicit constant
  ```
  `wr_free_bytes` is a **lower bound** on free space as observed from
  the write domain (pessimistic-low: may undercount free space due to
  sync lag). This is the signal `frame_packer` actually checks. It
  reads like the requirement and eliminates off-by-one risk from manual
  `DEPTH-128` arithmetic scattered across the codebase.

  At empty: `wr_free_bytes == DEPTH` (32768).
  At full:  `wr_free_bytes == 0`.

  **No saturation.** If FIFO invariants are violated (writes while full
  or reads while empty), `fill_level` may exceed DEPTH, causing
  `wr_free_bytes` to wrap to a large value. This is detectable and must
  be treated as an error, not masked by clamping.

  **Canonical drop inequality (define ONCE, use everywhere):**
  ```
  drop = (wr_free_bytes < 128)          // strict less-than
  ```
  Equivalently: `drop = (fill_level > DEPTH - 128)`. Both forms mean
  "fewer than 128 bytes free." Use `wr_free_bytes < 128` in RTL and TB.
  Do NOT use `<=`, `>=`, or `DEPTH-128-1` variants anywhere.

> **FIFO read contract (non-FWFT, 1-cycle read latency):**
>
> This FIFO is **non-FWFT** (non-First-Word-Fall-Through). This is an
> **architectural choice**, not an automatic consequence of using SPRAM.
> (FWFT can be built on top of registered memory with a prefetch output
> register — we deliberately chose NOT to do that, to keep the FIFO
> implementation minimal and its timing contract unambiguous.)
>
> **The read contract is one sentence:**
>
> `rd_en` sampled high at `rd_clk` rising edge N ⇒ `rd_data` valid at
> rising edge N+1, and stable for the full N+1 → N+2 period.
>
> That is **the entire FIFO read-side timing contract.** Everything
> about registered `rd_en` pipeline stages or "K+2" is the *consumer's*
> concern (see fifo_bridge §5), not the FIFO's.
>
> **Implementation note (iCE40 SB_SPRAM256KA):** SPRAM has a synchronous
> read interface with registered output. Address is captured at the
> rising edge; data output is valid after that edge's clock-to-Q delay
> (sampleable at the next rising edge). This 1-cycle latency directly
> satisfies the read contract above. SPRAM output is undefined when
> CHIPSELECT is deasserted — the FIFO must keep CS=1 during and after
> the read until `rd_data` is captured.
>
> `rd_data` remains stable from edge N+1 until the next successful
> `rd_en` pop. It does NOT auto-advance.
>
> **Behavioral requirements the FIFO RTL must enforce:**
>
> 1. **No auto-present on empty→notempty.** When the FIFO transitions
>    from empty to notempty (writer pushes first byte), `rdata` does
>    NOT change and does NOT present the new byte. `rdata` only changes
>    as a consequence of a `rd_en` pop. This is the defining property
>    of non-FWFT.
>
> 2. **`rd_en` when `rd_empty=1` is a no-op — enforced in read-domain
>    logic.** The FIFO's own read-side RTL must gate the pointer advance:
>    `rd_ptr_next = (rd_en && !rd_empty) ? rd_ptr + 1 : rd_ptr;`
>    This is NOT just a consumer-side convention. The FIFO itself must
>    refuse to advance. If only the consumer checks empty and the FIFO
>    blindly advances on rd_en, a CDC race on rd_empty can cause
>    underflow.
>
> 3. **`rdata` is undefined until the first successful `rd_en` pop.**
>    SPRAM initial contents are indeterminate. Do not rely on rdata
>    value before the first pop completes.
>
> 4. **`rd_empty` is a CDC-delayed flag.** It may remain high for 2–3
>    `clk_60m` cycles after a write completes on `clk_48m`. This causes
>    a harmless stall (fifo_bridge waits in S_IDLE), never data loss —
>    but only because requirement #2 above prevents pointer advance
>    when `rd_empty=1` in the read domain.
>
> 5. **`rd_empty` safety invariant: pessimistic-safe, NEVER optimistic.**
>    In a standard gray-code async FIFO, `rd_empty` in the read domain
>    is computed by comparing `rd_ptr` against the synchronized copy of
>    `wr_ptr_gray`. The synchronizer adds 2–3 cycle latency to seeing
>    new writes, so `rd_empty` may remain asserted after the write side
>    has pushed data (pessimistic = safe stall). Critically, `rd_empty`
>    can **never** falsely deassert (claim "not empty" when the FIFO is
>    truly empty), because the write pointer can only advance forward,
>    and the synchronized copy lags behind — the read domain always sees
>    an equal-or-older write pointer, never a newer one.
>
>    **This is the difference between "stall" and "duplicate bytes."**
>    If `rd_empty` were ever optimistic (false not-empty), fifo_bridge
>    would issue a POP on an empty FIFO, the POP would be a no-op
>    (requirement #2), `rdata` would hold the previous byte, and
>    fifo_bridge would latch and re-transmit that stale byte — silent
>    data duplication. The pessimistic-safe invariant prevents this.
>
>    The FIFO RTL must implement `rd_empty` using the standard gray-code
>    comparison: `rd_empty = (rd_ptr_gray == wr_ptr_gray_sync)`. Any
>    other implementation must be proven to satisfy this invariant.
>
> **Simulation requirement:** The testbench must exercise the
> empty→notempty transition without asserting `rd_en` and confirm
> that `rdata` does NOT change. This validates the non-FWFT contract
> independently of the consumer FSM.

### 5. `fifo_bridge` — FT2232H Synchronous FIFO Protocol

> **FT2232H write timing contract (DS_FT2232H Rev 2.10, Table 7.10, Figure 4.4):**
> In synchronous 245 FIFO mode, the FT2232H samples the data bus and WR_N
> on each **rising edge of CLKOUT** (60 MHz). A byte is accepted into the
> TX FIFO when WR_N is sampled low AND TXE_N was sampled low on the
> **previous** edge. The datasheet specifies data setup time t₁₁ = 5 ns
> and hold time t₁₂ = 5 ns relative to CLKOUT rising edge.
>
> **Design decision**: We cannot verify from publicly-fetchable web sources
> whether continuous WR_N=0 across multiple CLKOUT edges captures one byte
> per edge (level-based) or only on the falling transition (edge-based).
> Therefore we adopt the **safest assumption: WR_N is pulsed low for
> exactly one CLKOUT cycle per byte**. This is unambiguously correct
> regardless of the chip's internal implementation.
> Throughput at 60 MHz / 4 cycles = 15 MB/s. We need 2.56 MB/s.
> Margin: **5.9×**. Still boring. Still good.

- **Clock domain**: `clk_60m` (FT_CLKOUT — INPUT from FT2232H, 60 MHz fixed)
- **Mode**: write-only (FPGA→host acquisition stream).
- **Ports**:

| Port           | Width | Dir | Description                     |
|----------------|-------|-----|---------------------------------|
| clk_60m        | 1     | in  | From FT_CLKOUT                 |
| rst_n          | 1     | in  | Reset (synced to clk_60m)      |
| ft_data        | 8     | out | Data bus (always from data_reg) |
| ft_txe_n       | 1     | in  | TX FIFO not full (raw pin)     |
| ft_wr_n        | 1     | out | Write strobe (pulsed, 1 cycle) |
| ft_rd_n        | 1     | out | Permanently HIGH (not reading)  |
| ft_oe_n        | 1     | out | Permanently HIGH (not reading)  |
| ft_siwu_n      | 1     | out | Permanently HIGH (no flush)     |
| fifo_rdata     | 8     | in  | Read data from async_fifo      |
| fifo_empty     | 1     | in  | Async FIFO empty flag          |
| fifo_rd_en     | 1     | out | Async FIFO read enable         |
| stall_count    | 32    | out | Cumulative TXE_N stall cycles  |

- **Permanently tied outputs**:
  - `ft_rd_n  = 1` (never reading from host)
  - `ft_oe_n  = 1` (never enabling FT2232H output drivers)
  - `ft_siwu_n = 1` (no flush control — USB engine handles packetization)

- **Unused pin handling** (FT2232H QFN-64 package):
  - `ft_rxf_n` — **ACBUS0, FT2232H pin 26** (not pin 34): FPGA input,
    ignored in fifo_bridge logic (we never read from host). PCF
    constraint includes it; FPGA pad has internal pullup enabled.
  - **ACBUS7, FT2232H pin 34** (PWRSAV#): 10kΩ external pullup to
    VCCIO. Must be high to prevent power-save mode. Not connected to
    FPGA — hardwired on PCB.
  - **RESET#, FT2232H pin 52**: 10kΩ external pullup to VCCIO + 100nF
    decoupling cap. Not connected to FPGA — hardwired on PCB.
  - **BDBUS[2:7], BCBUS[0:3]**: configured as GPIO inputs in FT2232H
    EEPROM (not used — Channel B is UART, only BDBUS0/1 active).
    **Each unused pin has a 10kΩ pulldown to GND on PCB.** Internal
    pulls vary by EEPROM configuration and are not guaranteed. We do
    not leave pins floating near a USB PHY.
  - **ACBUS4 (SIWU#, pin 30)**: connected to FPGA (driven high permanently).
    If board-level pullup is preferred, the FPGA pin can be freed.

- **Registered signals** (all clocked on CLKOUT rising edge):
  ```
  reg [7:0] data_reg;      // holding register — ONLY source of ft_data
  reg       pending_valid;  // 1 = data_reg holds a valid byte to write
  reg       txe_n_r;        // registered copy of raw ft_txe_n
  reg       wr_n_reg;       // registered WR_N output
  reg [2:0] state;          // FSM state (3 bits → 5 states)

  assign ft_data   = data_reg;  // NEVER combinational from fifo_rdata
  assign ft_wr_n   = wr_n_reg;
  assign ft_rd_n   = 1'b1;
  assign ft_oe_n   = 1'b1;
  assign ft_siwu_n = 1'b1;
  ```

- **5-state FSM** (S_IDLE → S_FETCH → S_WAIT → S_HOLD → S_WRITE):

  **Why S_WAIT exists — registered `rd_en` adds a bridge-side pipeline stage:**

  The async_fifo read contract (§4) is simple: **1-cycle latency.**
  `rd_en` sampled high at edge N ⇒ `rd_data` valid at edge N+1.

  But `fifo_rd_en` is a **registered FSM output** — a flip-flop clocked
  by `clk_60m`, the same clock that drives the FIFO's read-side logic.
  In a synchronous system, all flip-flops sample D-inputs at the same
  rising edge. When the FSM registers `rd_en=1` at edge K, the FIFO
  still sees the OLD `rd_en=0` at edge K. The FIFO samples the NEW
  `rd_en=1` at edge K+1.

  This registered-output delay is **the bridge's concern, not the
  FIFO's.** The FIFO's contract is satisfied: it sees `rd_en=1` at
  K+1 and delivers valid `rd_data` at K+2. The bridge must account
  for the extra edge it introduced.

  Concretely, let edge K be the physical rising edge where the FSM
  registers rd_en=1 and state transitions to S_FETCH.

  Convention: "State after edge" = value captured INTO the state register
  at this edge. The state register's Q output (used by case statements)
  reflects this value starting at the NEXT edge.

  | Rising edge | State captured | FIFO sees rd_en | rdata status              |
  |-------------|----------------|-----------------|---------------------------|
  | K           | → S_FETCH      | 0 (old value)   | (no SPRAM activity yet)   |
  | K+1         | → S_WAIT       | 1 (from edge K) | FIFO's edge N. SPRAM reads.|
  | K+2         | → S_HOLD       | 0               | FIFO's edge N+1. **rdata valid.** |
  | K+3         | → S_WRITE      | 0               | rdata stable, data_reg captured ✓ |

  **Latency decomposition (bridge perspective):**
  - K: FSM registers rd_en=1. FIFO sees old rd_en=0. (bridge pipeline)
  - K+1: FIFO sees rd_en=1. This is the FIFO's "edge N." SPRAM address
    latched, internal read begins.
  - K+1 + tCO: SPRAM `DATAOUT` valid (~3-5 ns after K+1 rising edge).
    rdata valid and stable during K+1→K+2 period.
  - K+2: FIFO's "edge N+1." rdata valid per §4 read contract.
    State case-match is S_WAIT (captured at K+1). No latch yet.
  - K+3: State case-match is S_HOLD (captured at K+2). `data_reg <= fifo_rdata`.
    rdata has been stable for >1 full period (16.67 ns). **Margin: >14 ns.** ✓

  Total: 1 edge (registered rd_en) + 1 edge (FIFO 1-cycle contract) =
  2 edges from "FSM decides" to "rdata valid." But the FIFO only
  promises 1-cycle latency. The extra edge is the bridge's doing.

  S_WAIT absorbs the FIFO's 1-cycle read latency. S_HOLD latches with
  1 full cycle of rdata stability. No same-edge race.

  If the FIFO's internal latency ever increases to 2 cycles (e.g.,
  output register added), data_reg at K+3 would capture marginally
  stable data. **In that case, add S_WAIT2.** For now, the FIFO's
  1-cycle contract is locked.

  ```
  ┌────────┐  !fifo_empty && !txe_n_r   ┌─────────┐
  │ S_IDLE │────────────────────────────►│ S_FETCH │  rd_en registered here
  │        │  && pending_valid==0        │         │  (FIFO sees it next edge)
  └────────┘                             └────┬────┘
       ▲                                      │ (unconditional)
       │                                      ▼
       │                                ┌─────────┐
       │                                │ S_WAIT  │  FIFO reads SPRAM here.
       │                                │         │  rdata valid after this edge.
       │                                └────┬────┘
       │                                     │ (unconditional)
       │                                     ▼
       │                                ┌─────────┐
       │                                │ S_HOLD  │ ← data_reg loaded HERE
       │                                │         │   (rdata stable >1 period)
       │                                │         │   pending_valid = 1
       │                                └────┬────┘
       │                                     │ !txe_n_r
       │                                     ▼
       │                                ┌─────────┐
       │          (always)              │ S_WRITE │ WR_N=0 for ONE cycle
       └────────────────────────────────│         │ data_reg FROZEN
                                        │         │ pending_valid ← 0
                                        └─────────┘

  NO prefetch. NO S_WRITE→S_FETCH shortcut. S_WRITE always → S_IDLE.
  One POP site. One rule. Boring. Correct.
  ```

  **Design invariants:**
  1. `data_reg` is loaded **only in S_HOLD**. Never in any other state.
  2. `wr_n_reg` goes low **only in S_WRITE**. For **exactly one cycle**.
  3. WR_N is **never asserted when txe_n_r=1**. No exceptions.
  4. `pending_valid` tracks whether `data_reg` holds an unsent byte.
  5. `stall_count` increments **in any state** where `txe_n_r=1` AND
     we have data pending (either `pending_valid=1` or `!fifo_empty`).
  6. **`fifo_rd_en` is asserted ONLY in S_IDLE→S_FETCH.** One site.
     Not in S_FETCH. Not in S_WAIT. Not in S_HOLD. Not in S_WRITE.
     One `rd_en` pulse = one byte popped. No exceptions.
  7. **`fifo_rd_en` is NEVER asserted when `pending_valid=1`.**
     This is trivially true given invariant #6: the only POP site is
     S_IDLE, and S_IDLE is only entered from reset or from S_WRITE
     with `pending_valid` just cleared to 0. `pending_valid` is 0
     throughout S_IDLE. No special gating logic required.

  **Compact FSM output spec (RTL must match exactly):**

  ```
  S_IDLE:
    if (!fifo_empty && !txe_n_r && !pending_valid): rd_en_next=1; next=S_FETCH
    else: hold

  S_FETCH:
    rd_en_next=0; next=S_WAIT

  S_WAIT:
    next=S_HOLD

  S_HOLD:
    data_reg_next=fifo_rdata; pending_valid_next=1;
    if (!txe_n_r): next=S_WRITE
    else:          next=S_HOLD  // stall, data_reg still loaded

  S_WRITE:
    if (!txe_n_r && pending_valid): wr_n_next=0; pending_valid_next=0; next=S_IDLE
    if (txe_n_r):                   wr_n_next=1; next=S_HOLD  // FTDI went busy
  ```

  **Cycle-by-cycle truth table:**

  ```
  All signals are registered outputs, updated at CLKOUT rising edges.
  "State" = state register Q output at this edge (loaded at previous edge).
  "rd_en reg" = value captured into fifo_rd_en register at THIS edge.
     The FIFO sees this value at the NEXT edge (registered pipeline delay).
  "FIFO sees" = what the FIFO's read-side samples at THIS edge (= PREVIOUS edge's rd_en).
  pending_valid: 1 = data_reg holds a byte not yet written.

  ┌─────────┬───────────────────────────┬──────────┬──────┬──────────┬───────────┬────────────┬───────────┐
  │ State   │ Condition                 │ data_reg │ WR_N │ rd_en    │ FIFO sees │ pend_valid │ Next      │
  │         │                           │          │      │ reg      │ rd_en     │            │           │
  ├─────────┼───────────────────────────┼──────────┼──────┼──────────┼───────────┼────────────┼───────────┤
  │ S_IDLE  │ fifo_empty                │ (hold)   │  1   │  ← 0    │    0      │    0       │ S_IDLE    │
  │ S_IDLE  │ txe_n_r=1                 │ (hold)   │  1   │  ← 0    │    0      │    0       │ S_IDLE    │
  │         │                           │          │      │          │           │            │ +stall++  │
  │ S_IDLE  │ !fifo_empty & !txe_n_r   │ (hold)   │  1   │  ← 1    │    0      │    0       │ S_FETCH   │
  │         │ THE ONLY POP SITE.        │          │      │  POP     │ (old=0)   │            │           │
  │         │ rd_en registered here.    │          │      │          │           │            │           │
  │         │ FIFO sees it next edge.   │          │      │          │           │            │           │
  ├─────────┼───────────────────────────┼──────────┼──────┼──────────┼───────────┼────────────┼───────────┤
  │ S_FETCH │ (unconditional)           │ (hold)   │  1   │  ← 0    │  ← 1     │    0       │ S_WAIT    │
  │         │ FIFO sees rd_en=1 NOW.    │          │      │          │ (from K)  │            │           │
  │         │ SPRAM addr latched. Read  │          │      │          │           │            │           │
  │         │ in progress. rdata NOT    │          │      │          │           │            │           │
  │         │ YET VALID.                │          │      │          │           │            │           │
  ├─────────┼───────────────────────────┼──────────┼──────┼──────────┼───────────┼────────────┼───────────┤
  │ S_WAIT  │ (unconditional)           │ (hold)   │  1   │  ← 0    │    0      │    0       │ S_HOLD    │
  │         │ rdata NOW VALID (SPRAM    │          │      │          │           │            │           │
  │         │ tCO after K+1). Stable    │          │      │          │           │            │           │
  │         │ for >1 period already.    │          │      │          │           │            │           │
  ├─────────┼───────────────────────────┼──────────┼──────┼──────────┼───────────┼────────────┼───────────┤
  │ S_HOLD  │ !txe_n_r                  │ ← fifo_ │  1   │  ← 0    │    0      │    ← 1     │ S_WRITE   │
  │         │                           │   rdata  │      │          │           │            │           │
  │ S_HOLD  │ txe_n_r=1                 │ ← fifo_ │  1   │  ← 0    │    0      │    ← 1     │ S_HOLD    │
  │         │ (stall: TXE high.         │   rdata  │      │          │           │ (pre-load: │ +stall++  │
  │         │  Byte held. DO NOT        │          │      │          │           │  see note  │           │
  │         │  optimize away this       │          │      │          │           │  below)    │           │
  │         │  latch — see note below.) │          │      │          │           │            │           │
  ├─────────┼───────────────────────────┼──────────┼──────┼──────────┼───────────┼────────────┼───────────┤
  │ S_WRITE │ !txe_n_r                  │ (FROZEN) │  0   │  ← 0    │    0      │    ← 0     │ S_IDLE    │
  │         │ WR_N pulsed. Byte sent.   │          │      │          │           │            │           │
  │         │ pend_valid cleared.       │          │      │          │           │            │           │
  │         │ ALWAYS → S_IDLE.          │          │      │          │           │            │           │
  │         │ NO PREFETCH. NO rd_en.    │          │      │          │           │            │           │
  │ S_WRITE │ txe_n_r=1                 │ (FROZEN) │  1   │  ← 0    │    0      │   (hold=1) │ S_HOLD    │
  │         │ (FTDI went busy: do NOT   │          │      │          │           │ (still     │ +stall++  │
  │         │  strobe WR. Return to     │          │      │          │           │  pending)  │           │
  │         │  S_HOLD and wait.)        │          │      │          │           │            │           │
  └─────────┴───────────────────────────┴──────────┴──────┴──────────┴───────────┴────────────┴───────────┘

  Verify invariant #6: rd_en reg ← 1 appears ONLY in S_IDLE→S_FETCH.
  Every other row: rd_en reg ← 0. One POP site. No exceptions. ✓

  Verify invariant #7: at the only POP site (S_IDLE), pend_valid is
  already 0. No gating logic needed. Trivially satisfied. ✓

  "FIFO sees" column: FIFO sees rd_en=1 only at S_FETCH (registered
  pipeline delay from S_IDLE). All other states: FIFO sees 0. ✓
  ```

  **Critical row: S_WRITE when txe_n_r=1.** We do NOT pulse WR_N.
  Instead, WR_N stays high, we keep `pending_valid=1`, and return to
  S_HOLD to retry. We **never** write when FTDI says "full". Period.

  **Critical simplification: S_WRITE ALWAYS → S_IDLE.** No prefetch.
  No conditional branching in S_WRITE based on fifo_empty. The only
  decision is: did TXE allow the write? If yes → S_IDLE. If no → S_HOLD.
  This eliminates the entire class of "pop/write overlap" bugs where
  `pending_valid` is being simultaneously cleared and used as a POP gate.

  **Note on S_HOLD latch behavior (DO NOT OPTIMIZE):**
  `data_reg <= fifo_rdata` executes on **every** cycle spent in S_HOLD,
  including when `txe_n_r=1` (TXE stall). This is intentional:
  - We pre-load `data_reg` even if TXE is high.
  - The byte is held with `pending_valid=1` until TXE drops.
  - Since no new POP has occurred (invariant #6: rd_en only in S_IDLE),
    `fifo_rdata` holds the same stable value from the original pop
    (non-FWFT: output holds until next `rd_en`).
  - On first entry from S_WAIT: captures the freshly-valid byte.
  - On stall loops (S_HOLD→S_HOLD): re-latches the same value. Harmless.
  - On re-entry from S_WRITE (txe stall): `data_reg` already holds the
    byte (FROZEN in S_WRITE), and `fifo_rdata` hasn't changed. Safe.

  **DO NOT "optimize" this by gating the latch on `!pending_valid` or
  on first-entry-only.** Re-latching is simpler and has identical
  behavior. The unconditional latch is correct by construction as long
  as `fifo_rdata` is stable, which it is by the non-FWFT contract.

  **Detailed cycle trace (steady-state burst, annotated with edge timing):**

  Convention: "State" = the state that the FSM is IN at this edge
  (loaded into the register at the previous edge). "rd_en" = value
  captured into the rd_en register at this edge. FIFO sees the
  PREVIOUS edge's rd_en value (registered pipeline delay).
  rd_en registered at edge K → FIFO sees at K+1 → rdata valid
  after K+1 tCO → data_reg latches at K+3 (state=S_HOLD).

  ```
  Edge  State    txe_n_r  empty  rd_en  FIFO sees  data_reg   pend  WR_N  What happens
  ────  ───────  ───────  ─────  ─────  ─────────  ─────────  ────  ────  ────────────────────────
  0     S_IDLE   0        0      1      0          (old)      0     1     rd_en reg←1. FIFO sees old=0.
  1     S_FETCH  0        0      0      1          (old)      0     1     FIFO sees rd_en=1 → SPRAM addr latched
  2     S_WAIT   0        0      0      0          (old)      0     1     rdata=byte[0] VALID (stable since 1+tCO)
  3     S_HOLD   0        0      0      0          ←byte[0]   1     1     data_reg ← rdata. Stable >1 period. ✓
  4     S_WRITE  0        0      0      0          (frozen)   0     0     FT captures byte[0]✓. Always → S_IDLE.
  5     S_IDLE   0        0      1      0          (hold)     0     1     rd_en reg←1 (byte[1]). FIFO sees old=0.
  6     S_FETCH  0        0      0      1          (hold)     0     1     FIFO sees rd_en=1 → SPRAM reads byte[1]
  7     S_WAIT   0        0      0      0          (hold)     0     1     rdata=byte[1] VALID
  8     S_HOLD   0        0      0      0          ←byte[1]   1     1     data_reg ← rdata
  9     S_WRITE  0        0      0      0          (frozen)   0     0     FT captures byte[1]✓. Always → S_IDLE.
  10    S_IDLE   0        0      1      0          (hold)     0     1     rd_en reg←1 (byte[2]).
  11    S_FETCH  0        0      0      1          (hold)     0     1     FIFO sees rd_en=1 → SPRAM reads byte[2]
  12    S_WAIT   0        0      0      0          (hold)     0     1     rdata=byte[2] VALID
  13    S_HOLD   0        0      0      0          ←byte[2]   1     1     data_reg ← rdata
  14    S_WRITE  0        1      0      0          (frozen)   0     0     FT captures byte[2]✓. Always → S_IDLE.
  15    S_IDLE   0        1      0      0          (hold)     0     1     empty → stay in S_IDLE. drain complete.
  ```

  **Edge-timing verification for byte[0]:**
  - Edge 0: FSM registers rd_en=1. State captures S_FETCH. FIFO sees old rd_en=0.
  - Edge 1: FIFO sees rd_en=1. SPRAM latches address. Read begins.
  - Edge 1 + tCO (~3 ns): SPRAM DATAOUT valid. rdata = byte[0].
  - Edge 2: State = S_WAIT. rdata has been valid since edge 1 + tCO.
  - Edge 3: State = S_HOLD. data_reg ← rdata. Stable for >16 ns. ✓
  - Edge 4: State = S_WRITE. WR_N=0. FT2232H captures byte[0]. ✓
  - Edge 5: State = S_IDLE. Ready for next byte. No prefetch. Clean.

  **Throughput**: 1 byte per 5 CLKOUT cycles (IDLE→FETCH→WAIT→HOLD→WRITE).
  - Peak: 60 MHz / 5 = **12 MB/s**.
  - Required: 128 bytes × 20,000 frames/s = **2.56 MB/s**.
  - Margin: **4.7×**. Entirely adequate.
  - 128 bytes at 5 cycles each = 640 cycles = 10.67 µs per frame.
    Frame period is 50 µs. FTDI bus utilization: **21.3%**.
  - No prefetch optimization. No shortcuts. Boring. Correct.

  **Key safety properties:**
  1. **data_reg is frozen during WR_N=0.** Only S_HOLD loads data_reg.
     S_WRITE never touches it. The byte on ft_data is rock-stable
     for the entire CLKOUT period when WR_N is low. Setup margin:
     16.67 ns - 2.5 ns routing = **14 ns** vs 5 ns requirement.

  2. **WR_N is pulsed for exactly one cycle.** No continuous WR_N=0
     across multiple edges. No ambiguity about edge-vs-level behavior.

  3. **WR_N is NEVER asserted when txe_n_r=1.** If FTDI indicates full,
     we do not write. Full stop. No "full after this write" assumption.

  4. **No combinational path from fifo_rdata to ft_data.** `ft_data`
     is wired to `data_reg`, which is a flip-flop output. `fifo_rdata`
     is only captured into `data_reg` in S_HOLD, gated by a registered
     state value.

  5. **pending_valid tracks data_reg state.** Set in S_HOLD, cleared
     in S_WRITE (on successful write). If TXE stalls us, pending_valid
     stays high and we retry.

  6. **S_WAIT absorbs SPRAM read latency; S_HOLD latches with full-cycle margin.**
     `rd_en` registered at edge K (entering S_FETCH). FIFO sees it at K+1
     (state=S_FETCH). SPRAM DATAOUT valid at K+1 + tCO (during S_WAIT
     period). At K+3, state=S_HOLD and `data_reg <= fifo_rdata` — rdata
     has been stable for >1 full 60 MHz period (~16.67 ns). Margin: >14 ns.
     S_WAIT is the cycle during which SPRAM processes the read. Without it,
     data_reg would latch before rdata is valid. If the FIFO is later
     changed to FWFT, rdata would be valid 1 edge earlier and S_WAIT
     would provide additional slack.

  7. **`fifo_rd_en` is NEVER asserted when `pending_valid=1`.**
     Trivially true: the only POP site is S_IDLE, and `pending_valid`
     is always 0 in S_IDLE (cleared in S_WRITE before returning to
     S_IDLE). No special gating logic required. No corner cases.

> **Write timing contract (formal):**
>
> For every byte written to the FT2232H, the following timing sequence
> is guaranteed by the FSM structure:
>
> | Condition                     | Guarantee                                       |
> |-------------------------------|-------------------------------------------------|
> | `data_reg` stable before WR   | Loaded in S_HOLD (≥1 full CLKOUT cycle before S_WRITE) |
> | `data_reg` stable during WR   | FROZEN in S_WRITE — `data_reg` is not assigned   |
> | `data_reg` stable after WR    | Held until next S_HOLD entry (≥3 cycles later)   |
> | WR_N pulse width              | Exactly 1 CLKOUT cycle (16.67 ns)               |
> | WR_N deasserted between bytes | ≥4 CLKOUT cycles high (IDLE+FETCH+WAIT+HOLD)     |
> | TXE_N checked before WR       | `txe_n_r` sampled and checked in S_HOLD→S_WRITE transition |
> | TXE_N re-checked in S_WRITE   | If `txe_n_r=1` in S_WRITE, WR_N stays HIGH, goto S_HOLD |
> | Data setup time               | ≥14 ns (16.67 - 2.5 routing) vs 5 ns required   |
> | Data hold time                | ≥14 ns (full CLKOUT period) vs 5 ns required     |

> **Byte ownership rule:**
>
> Once `fifo_rd_en` pops a byte from async_fifo, **fifo_bridge owns that
> byte and MUST eventually deliver it to the FT2232H**. No byte may be
> silently discarded, reordered, or duplicated. Specifically:
>
> 1. `fifo_rd_en` is asserted exactly once per byte, **only** in
>    S_IDLE→S_FETCH. One POP site. No prefetch. The rd_en register
>    is loaded in S_IDLE; the FIFO sees it during S_FETCH (registered
>    pipeline delay).
> 2. The popped byte appears on `fifo_rdata` in S_WAIT (2 edges after
>    FSM decides: 1 for rd_en pipeline + 1 for SPRAM read).
> 3. It is captured into `data_reg` in S_HOLD.
> 4. It is presented to FT2232H with WR_N=0 in S_WRITE.
> 5. If TXE_N stalls us, we loop S_HOLD→S_HOLD (or S_WRITE→S_HOLD)
>    indefinitely until TXE clears. The byte is never dropped.
> 6. The only way to lose data is **upstream**: frame_packer drops an
>    entire frame before pushing it into async_fifo. Once a byte enters
>    the FIFO, it WILL reach the host.
>
> **Corollary**: byte count into async_fifo (write side) minus byte
> count out of fifo_bridge (WR_N assertions) must equal the current
> FIFO fill level, at all times. Any discrepancy is a bug.

  **Stall handling:**
  - `stall_count` increments on **every CLKOUT cycle** where ALL of:
    (a) we have data pending (`pending_valid=1` OR `!fifo_empty`), AND
    (b) `txe_n_r=1` (FTDI cannot accept data).
  - This covers stalls in S_IDLE (have data, can't send), S_HOLD
    (byte loaded, waiting for TXE), and S_WRITE (TXE went high before
    we could strobe — returned to S_HOLD without writing).
  - Saturates at 0xFFFFFFFF. Read via UART telemetry or register 0x11–0x14.

  **Required simulation assertions (tb_fifo_bridge + tb_async_fifo):**

  The following properties MUST pass in simulation before PCB
  fabrication. These are not optional. They are the minimum conditions
  that prove the bridge cannot corrupt the data stream.

  1. **A1: No WR_N when TXE_N is high.**
     ```
     // SVA-style (translate to iverilog $display+$finish for cocotb)
     assert property (@(posedge clk_60m)
       (ft_wr_n == 0) |-> (txe_n_r == 0)
     ) else $fatal("A1 FAIL: WR_N asserted while txe_n_r=1");
     ```
     Verifies invariant #3. If this fires, the FTDI may NAK or corrupt.

  2. **A2: data_reg stable during WR_N=0.**
     ```
     assert property (@(posedge clk_60m)
       (ft_wr_n == 0) |-> (data_reg == $past(data_reg))
     ) else $fatal("A2 FAIL: data_reg changed during WR_N=0");
     ```
     Verifies invariant #1. If this fires, the FT2232H may sample a
     glitch or wrong byte value.

  3. **A3: Every popped byte is eventually written.**
     ```
     // Liveness check: count rd_en assertions vs wr_n assertions.
     // At end of simulation: pop_count == write_count + pending_valid.
     integer pop_count = 0, write_count = 0;
     always @(posedge clk_60m) begin
       if (fifo_rd_en && !fifo_empty) pop_count <= pop_count + 1;
       if (!ft_wr_n)                  write_count <= write_count + 1;
     end
     // At $finish: assert (pop_count == write_count + pending_valid)
     ```
     Verifies the byte ownership rule. If `pop_count > write_count + 1`
     at end of test, a byte was lost. If `write_count > pop_count`, a
     byte was fabricated from nowhere.

  4. **A4: rd_en when rd_empty does not advance pointer.** (tb_async_fifo)
     ```
     // Inside async_fifo testbench, read-domain logic:
     assert property (@(posedge clk_60m)
       (rd_en && rd_empty) |=> (rd_ptr == $past(rd_ptr))
     ) else $fatal("A4 FAIL: rd_ptr advanced while rd_empty=1");
     ```
     Verifies §4 requirement #2: the FIFO itself refuses to advance
     on rd_en when empty, regardless of what the consumer does. This
     prevents CDC races on rd_empty from causing underflow.

  5. **A5: fifo_rd_en never asserted when pending_valid=1.**
     ```
     assert property (@(posedge clk_60m)
       (fifo_rd_en == 1) |-> (pending_valid == 0)
     ) else $fatal("A5 FAIL: rd_en while pending_valid=1 — re-pop risk");
     ```
     Verifies invariant #7. If this fires, data_reg may be overwritten
     before the current byte is written to FT2232H.

  6. **A6: non-FWFT contract — rdata stable on empty→notempty without rd_en.**
     (tb_async_fifo)
     ```
     // Stimulus: push 1 byte into empty FIFO. Wait 5 read-domain cycles
     // without asserting rd_en. Sample rdata before and after.
     // Assert: rdata_before == rdata_after (rdata did not change).
     ```
     Verifies §4 requirement #1: the FIFO does not auto-present the
     first word. This is the defining test of non-FWFT behavior.

  7. **A7: when rd_en is observed high at negedge, state is S_FETCH.** (tb_fifo_bridge)
     ```
     // Negedge sampling (iverilog workaround for mixed-region observation).
     // At posedge N: FSM in S_IDLE asserts rd_en_reg<=1, state<=S_FETCH (NBA).
     // At negedge N+½: rd_en=1, state=S_FETCH (both settled).
     // Decision was made in S_IDLE, but S_IDLE is NOT observable at negedge.
     always @(negedge clk_60m)
       if (rst_n && fifo_rd_en && (state != S_FETCH))
         $fatal("A7 FAIL: rd_en=1 but state=%0d (expected S_FETCH)", state);
     ```
     Verifies invariant #6: one POP site, no exceptions. If this fires,
     someone added a prefetch path or other POP site.

  8. **A8: pop_count never exceeds FIFO write_count (underflow guard).**
     (tb_fifo_bridge + tb_async_fifo integration)
     ```
     // Track cumulative pops (read side) vs pushes (write side).
     // At ALL times: pop_count <= push_count.
     integer push_count = 0, pop_count = 0;
     always @(posedge clk_48m)
       if (wr_en && !wr_full) push_count <= push_count + 1;
     always @(posedge clk_60m)
       if (fifo_rd_en && !fifo_empty) pop_count <= pop_count + 1;
     // Continuous assertion (check every clk_60m edge):
     // assert (pop_count <= push_count)
     // else $fatal("A8 FAIL: more pops than pushes — underflow");
     ```
     Catches the scenario where a stale (optimistic) `rd_empty` allows
     a POP on a truly-empty FIFO. Combined with §4 requirement #5
     (pessimistic-safe `rd_empty`), this should never fire. If it does,
     the CDC gray-code synchronizer or empty logic is broken.

  9. **A9: no pop on empty (direct edge detector).**
     (tb_fifo_bridge — fires on the exact violating edge)
     ```
     // A9: rd_en must NEVER be asserted when fifo_empty is asserted.
     // Unlike A8 (cumulative counter), this catches the exact edge.
     always @(posedge clk_60m)
       if (fifo_rd_en && fifo_empty)
         $fatal("A9 FAIL: POP attempted while FIFO empty");
     ```
     With the single-POP-site architecture (S_IDLE gates on `!fifo_empty`),
     A9 should never fire. If it does, either the empty flag is optimistic
     (violates §4 req #5) or the FSM has a bug in its S_IDLE guard.

### 6. `uart_tx` / `uart_rx` — Debug UART + Telemetry
- **Clock domain**: `clk_48m`
- 115200 baud, 8N1
- `uart_tx`: simple shift register, byte-at-a-time
- `uart_rx`: oversample at 16×, majority vote
- Connected to `ctrl_regs` for register read/write
- **Also carries all diagnostic telemetry** (see §8)

### 7. `ctrl_regs` — Configuration Registers
- **Clock domain**: `clk_48m`
- Accessible via UART (host sends: `W <addr> <data>\n`, `R <addr>\n`)
- Registers:

| Addr | Name          | R/W | Default | Description                          |
|------|---------------|-----|---------|--------------------------------------|
| 0x00 | CTRL          | RW  | 0x00    | [0]=stream_en, [1]=test_mode         |
| 0x01 | STATUS        | R   | —       | See STATUS bit map below             |
| 0x02 | FRAME_CNT_H   | R   | —       | Frame counter [31:16]                |
| 0x03 | FRAME_CNT_L   | R   | —       | Frame counter [15:0]                 |
| 0x04 | SPI_CMD       | RW  | 0xFFFF  | Manual SPI command (debug)           |
| 0x05 | SPI_RSP       | R   | —       | Last SPI response (debug)            |
| 0x06 | VERSION       | R   | 0x02    | RTL version (bumped for CDR-1)       |
| 0x07 | FIFO_LEVEL_H  | R   | —       | Async FIFO fill level [14:8]         |
| 0x08 | FIFO_LEVEL_L  | R   | —       | Async FIFO fill level [7:0]          |
| 0x09 | DROP_CNT_3    | R   | —       | Dropped frame counter [31:24]        |
| 0x0A | DROP_CNT_2    | R   | —       | Dropped frame counter [23:16]        |
| 0x0B | DROP_CNT_1    | R   | —       | Dropped frame counter [15:8]         |
| 0x0C | DROP_CNT_0    | R   | —       | Dropped frame counter [7:0]          |
| 0x0D | OVFL_CNT_3    | R   | —       | FIFO overflow counter [31:24]        |
| 0x0E | OVFL_CNT_2    | R   | —       | FIFO overflow counter [23:16]        |
| 0x0F | OVFL_CNT_1    | R   | —       | FIFO overflow counter [15:8]         |
| 0x10 | OVFL_CNT_0    | R   | —       | FIFO overflow counter [7:0]          |
| 0x11 | STALL_CNT_3   | R   | —       | TXE stall cycle counter [31:24]      |
| 0x12 | STALL_CNT_2   | R   | —       | TXE stall cycle counter [23:16]      |
| 0x13 | STALL_CNT_1   | R   | —       | TXE stall cycle counter [15:8]       |
| 0x14 | STALL_CNT_0   | R   | —       | TXE stall cycle counter [7:0]        |
| 0x15 | TELEM_DIV     | RW  | 0x14    | UART telemetry divider: send every   |
|      |               |     |         | N×50ms. Default 0x14=20 → 1 Hz.      |
|      |               |     |         | Set 0x01 for 20 Hz (every 50 ms).    |

**STATUS register (0x01) bit map:**

| Bit | Name             | Description                              |
|-----|------------------|------------------------------------------|
| [0] | pll_locked       | SB_PLL40 lock indicator                  |
| [1] | afe_present      | AFE_INTAN_N low = chip detected          |
| [2] | fifo_overflow    | Sticky: async FIFO wrote when full       |
| [3] | spi_busy         | SPI master currently in conversion cycle |
| [4] | ftdi_stall       | TXE_N high while pending_valid or !empty |
| [5] | frame_sync_lost  | Expected 50 µs frame timing violated     |
| [6] | stream_active    | CTRL.stream_en AND data actually flowing |
| [7] | reserved         | Reads as 0                               |

Sticky bits (fifo_overflow, frame_sync_lost) clear on read.

---

### 8. UART Telemetry Protocol (out-of-band diagnostics)

All diagnostic/metadata is sent over **FT2232H Channel B UART** (115200 8N1),
not in the USB data stream. The USB stream on Channel A is 100% pure 128-byte
ADC data frames — no mixed types, no sync markers, no resync ambiguity.

**Telemetry packet** (ASCII, newline-terminated, human-readable for debug):
```
$TEL,<frame_counter>,<dropped>,<overflows>,<stalls>,<fifo_fill>,<temp>,<vcc>\n
```

| Field         | Format  | Description                              |
|---------------|---------|------------------------------------------|
| frame_counter | decimal | 32-bit running frame count               |
| dropped       | decimal | 32-bit cumulative dropped frames         |
| overflows     | decimal | 32-bit cumulative FIFO overflow events   |
| stalls        | decimal | 32-bit cumulative TXE stall cycles       |
| fifo_fill     | decimal | Current async FIFO fill level (0–32767)  |
| temp          | hex     | RHD2164 temperature sensor raw (16-bit)  |
| vcc           | hex     | RHD2164 supply voltage raw (16-bit)      |

- Rate: configurable via `TELEM_DIV` register (0x15). Default 1 Hz.
  Set to 0x01 for 20 Hz (one packet per 50 ms). Maximum useful rate
  limited by 115200 baud: ~80 byte packet → ~7 ms per packet → ~140 Hz
  theoretical max, but 10–50 Hz is practical.
- Packet is ~60–80 bytes at typical counter values. At 115200 baud,
  one packet takes ~5–7 ms to transmit. At 20 Hz, UART is ~10–14%
  utilized. No contention risk.
- Host reads Channel B (typically `/dev/ttyUSB1` on macOS) in a
  separate thread. Parse by splitting on `$TEL,` prefix.
- **Frame counter in telemetry + frame counter gaps in the data stream
  together provide full lossless accounting.** Host knows its own read
  position in the USB stream (128B × frames_read). If `dropped > 0`,
  host knows exactly how many sample periods were lost.

**Register read/write** (also on UART, shared channel):
```
Host→FPGA: W <hex_addr> <hex_data>\n    (write register)
Host→FPGA: R <hex_addr>\n               (read register)
FPGA→Host: =<hex_data>\n                (read response)
```
Telemetry packets and register responses are interleaved. Host
distinguishes by prefix: `$TEL,` vs `=`.

---

## Resource Estimate

| Resource     | Used (est.) | Available | Utilization |
|--------------|-------------|-----------|-------------|
| LUT4         | ~2,600      | 5,280     | 49%         |
| DFF          | ~2,200      | 5,280     | 42%         |
| BRAM (4Kb)   | 3           | 30        | 10%         |
| SPRAM (256Kb)| 2           | 4         | 50%         |
| PLL          | 1           | 1         | 100%        |
| SPI hard IP  | 0 (soft)    | 2         | 0%          |
| I/O pins     | 28          | 39 (usable)| 72%        |

Notes:
- BRAM usage: 1× ping-pong sample buffer (2×64×16 = 256B, fits 1 BRAM),
  1× frame_packer staging, 1× UART TX buffer
- SPRAM usage: 2 blocks for async FIFO (true dual-clock operation)
- fifo_bridge is now a simple 5-state FSM + data_reg + pending_valid
  + wr_n_reg + txe_n_r + 32-bit stall counter. No pipelining, no CRC,
  no SIWU counter. Estimated ~65 LUTs + ~55 FFs. Trivially small.
- ASCII telemetry formatter for UART Channel B: ~100 LUTs (hex/decimal
  conversion + packet framing).

---

## Clock Domains

| Domain   | Frequency | Source         | Modules                          |
|----------|-----------|----------------|----------------------------------|
| clk_24m  | 24 MHz    | PLL ÷2         | spi_master                       |
| clk_48m  | 48 MHz    | PLL (buffered) | frame_packer, uart, ctrl_regs    |
| clk_60m  | 60 MHz    | FT_CLKOUT      | fifo_bridge                      |

CDC crossings:
- `clk_24m → clk_48m`: frame_packer input (synchronous — 24 is ÷2 of 48, no CDC needed)
- `clk_48m → clk_60m`: **async_fifo** (gray-code pointer crossing, 15-bit)
  - Write side: `frame_packer` outputs byte + valid in `clk_48m`
  - Read side: `fifo_bridge` consumes bytes in `clk_60m` (FT_CLKOUT)
  - This is the ONLY asynchronous boundary in the design
  - All FT2232H bus signals (D[0:7], WR#, RD#, OE#, SIWU#) are driven
    exclusively from `fifo_bridge` in `clk_60m` domain ← CRITICAL
  - FT_CLKOUT (60 MHz) is an INPUT from FT2232H, not PLL-generated

---

## Build Flow

```bash
# Synthesize
yosys -p "synth_ice40 -top top -json top.json" \
    src/top.v src/pll_cfg.v src/spi_master.v src/frame_packer.v \
    src/async_fifo.v src/fifo_bridge.v src/uart_tx.v src/uart_rx.v \
    src/ctrl_regs.v

# Place & Route
nextpnr-ice40 --up5k --package sg48 --json top.json \
    --pcf pins.pcf --asc top.asc

# Pack bitstream
icepack top.asc top.bin

# Program flash
iceprog top.bin
```

---

## Pin Constraint File (`pins.pcf`)

```
# SPI to RHD2164
set_io spi_sclk   47
set_io spi_mosi   46
set_io spi_miso   45
set_io spi_cs_n   44
set_io afe_intan_n 48

# Sync FIFO to FT2232H
set_io ft_data[0] 36
set_io ft_data[1] 37
set_io ft_data[2] 38
set_io ft_data[3] 39
set_io ft_data[4] 40
set_io ft_data[5] 41
set_io ft_data[6] 42
set_io ft_data[7] 43
set_io ft_rxf_n   34
set_io ft_txe_n   32
set_io ft_wr_n    31
set_io ft_rd_n    28
set_io ft_oe_n    27
set_io ft_clkout  35
set_io ft_siwu_n  33

# UART debug
set_io uart_tx    12
set_io uart_rx    11

# LEDs + misc
set_io led_status 10
set_io led_error   9
set_io dbg_tp1    13
set_io osc_48mhz   6
```

---

## Next Steps (after schematic freeze)

1. Write `top.v` — instantiate all modules, wire per pin plan
2. Write `spi_master.v` — 68-word conversion cycle state machine
3. Write `frame_packer.v` — serialize 64 channels into 128B frames
4. Write `async_fifo.v` — gray-code CDC FIFO (non-FWFT, SPRAM-based)
5. Write `fifo_bridge.v` — 5-state FSM per §5 spec
6. Write testbenches:
   - `tb_spi_master.v`
   - `tb_frame_packer.v`
   - `tb_async_fifo.v` — MUST include A4 (no underflow) + A6 (non-FWFT) + A8 (pop≤push)
   - `tb_fifo_bridge.v` — MUST include A1/A2/A3/A5/A7/A9 + A8 (integration)
7. All 9 assertions (A1–A9) are a **gate to PCB fabrication**
8. Simulate with iverilog/cocotb
9. Synthesize + P&R, check timing at 60 MHz
