// frame_packer.v — Data Framing with Header + CRC
// Rev 1.0 · 2025-06-01 · MEA DAQ 64-Channel
//
// Reads 64 × 16-bit ADC samples + 3 AUX from ping-pong BRAM bank,
// constructs a FRAME_SIZE-byte frame with:
//   [0..15]                 Header (magic, version, status, frame_id, timestamp, etc.)
//   [16..143]               ADC payload (64 ch × 16-bit, big-endian)
//   [144..149]              AUX payload (3 ch × 16-bit, big-endian)
//   [150..165]              Diagnostics (fifo_fill, drop_cnt, stall_cnt, spi_err)
//   [166..253]              Reserved (zero-padded)
//   [254..255]              CRC16-CCITT over bytes 0..253
//
// Writes one byte per clock into async_fifo.
// Atomic commit: checks wr_free_bytes >= FRAME_SIZE before starting.
// If insufficient space: entire frame dropped, counters updated.
//
// BRAM addressing: buf_addr outputs a channel index (0..66).
// The bank select is driven externally; buf_bank is latched for reference only.
//
// Clock domain: clk (system clock, typically 48 MHz)
// Throughput: 256 clocks per frame + 2 overhead = 258 cycles (~5.4 µs @ 48 MHz)
//             Well within 50 µs sample period (20 kSPS)

`default_nettype none

module frame_packer #(
    parameter FRAME_SIZE   = 256,   // Total frame bytes
    parameter HDR_SIZE     = 16,    // Header bytes
    parameter CRC_SIZE     = 2,     // CRC16 trailer bytes
    parameter NUM_CH       = 64,    // ADC channels per frame
    parameter ADC_BYTES    = 128,   // NUM_CH * 2
    parameter NUM_AUX      = 3,     // Auxiliary ADC channels
    parameter AUX_BYTES    = 6,     // NUM_AUX * 2
    parameter DIAG_BYTES   = 16     // Diagnostic field bytes
) (
    input  wire        clk,
    input  wire        rst_n,

    // From spi_master (ping-pong buffer interface)
    input  wire        cycle_done,      // Pulse: new sample set ready
    input  wire        buf_bank,        // Which ping-pong bank to read (active bank)
    input  wire [15:0] buf_data,        // Read data from ping-pong BRAM (1-cycle latency)
    output reg  [6:0]  buf_addr,        // Channel index into bank (0..NUM_CH+NUM_AUX-1)

    // To async_fifo
    input  wire [15:0] wr_free_bytes,   // Free space in async FIFO (bytes)
    output reg         fifo_wr_en,      // Write enable (1 byte per assert)
    output reg  [7:0]  fifo_wr_data,    // Write data byte

    // Diagnostic inputs (from other modules)
    input  wire [15:0] fifo_fill_level, // Current FIFO fill level (bytes)
    input  wire [31:0] ext_stall_count, // Stall counter from fifo_bridge

    // Outputs
    output wire [31:0] frame_counter,   // Total frame_id (committed + dropped)
    output wire [31:0] drop_counter,    // Total frames dropped
    output reg         commit_pulse,    // 1-cycle pulse on successful frame commit
    output reg  [31:0] commit_frame_id, // Frame ID of last committed frame
    output reg         drop_pulse       // 1-cycle pulse on frame drop
);

    // ── Derived constants ───────────────────────────────────────────
    localparam PAYLOAD_START = HDR_SIZE;                    // 16
    localparam AUX_START     = HDR_SIZE + ADC_BYTES;        // 144
    localparam DIAG_START    = AUX_START + AUX_BYTES;       // 150
    localparam RSVD_START    = DIAG_START + DIAG_BYTES;     // 166
    localparam CRC_START     = FRAME_SIZE - CRC_SIZE;       // 254
    localparam RSVD_BYTES    = CRC_START - RSVD_START;      // 88
    localparam TOTAL_SAMPLES = NUM_CH + NUM_AUX;            // 67

    // ── State machine ───────────────────────────────────────────────
    localparam [2:0] S_IDLE    = 3'd0,  // Wait for cycle_done
                     S_CHECK   = 3'd1,  // Check FIFO space
                     S_HEADER  = 3'd2,  // Emit header bytes
                     S_FETCH   = 3'd3,  // Wait 1 cycle for BRAM read latency
                     S_ADC_HI  = 3'd4,  // Emit high byte of ADC/AUX sample
                     S_ADC_LO  = 3'd5,  // Emit low byte, pre-fetch next
                     S_TAIL    = 3'd6,  // Emit diagnostics + reserved
                     S_CRC     = 3'd7;  // Wait 1 cycle then emit CRC bytes

    reg [2:0]  state;
    reg [8:0]  byte_idx;     // Current byte position in frame (0..255)
    reg [31:0] frame_id;     // Monotonic frame counter
    reg [31:0] drop_cnt;     // Monotonic drop counter
    reg [31:0] timestamp;    // Sample timestamp (increments every cycle_done)
    reg        pending;      // cycle_done captured, waiting to process
    reg        bank_latch;   // Latched buf_bank at cycle_done

    // Status flags
    reg        drop_since_last;  // Cleared on commit, set on drop
    reg        overflow_sticky;  // Set if FIFO overflow detected, never cleared

    // BRAM read pipeline
    reg [15:0] sample_latch;  // Latched 16-bit sample from buf_data
    reg [6:0]  ch_idx;        // Channel counter (0..TOTAL_SAMPLES-1)

    // Header buffer (built in S_CHECK, consumed in S_HEADER)
    reg [7:0]  hdr [0:15];

    // Diagnostic buffer (built in S_CHECK, consumed in S_TAIL)
    reg [7:0]  diag_buf [0:15];

    // Tail-region indexing
    reg [7:0]  tail_offset;  // byte_idx - DIAG_START (used during S_TAIL)

    // CRC interface
    wire [15:0] crc_value;
    reg         crc_init;
    reg         crc_valid;
    reg  [7:0]  crc_data;

    assign frame_counter = frame_id;
    assign drop_counter  = drop_cnt;

    // ── CRC16 instance ──────────────────────────────────────────────
    crc16_ccitt u_crc (
        .clk      (clk),
        .rst_n    (rst_n),
        .init     (crc_init),
        .valid    (crc_valid),
        .data_in  (crc_data),
        .crc_out  (crc_value)
    );

    // ── Capture cycle_done ──────────────────────────────────────────
    // cycle_done is a 1-cycle pulse from spi_master.
    // We latch it as 'pending' and the bank selection.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pending    <= 1'b0;
            bank_latch <= 1'b0;
            timestamp  <= 32'd0;
        end else begin
            if (cycle_done) begin
                pending    <= 1'b1;
                bank_latch <= buf_bank;
                timestamp  <= timestamp + 32'd1;
            end else if (state == S_CHECK) begin
                pending <= 1'b0;  // Consumed
            end
        end
    end

    // ── Overflow detection ──────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            overflow_sticky <= 1'b0;
        else if (wr_free_bytes == 16'd0 && fifo_wr_en)
            overflow_sticky <= 1'b1;
    end

    // ── Helper: emit one byte to FIFO + CRC ─────────────────────────
    // (Used procedurally inside the FSM always block)
    // We drive fifo_wr_en, fifo_wr_data, crc_valid, crc_data from
    // the FSM states directly. This comment documents the pattern:
    //   fifo_wr_en   = 1
    //   fifo_wr_data = byte_value
    //   crc_valid    = 1  (except during CRC emission itself)
    //   crc_data     = byte_value

    // ── Main FSM ────────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state           <= S_IDLE;
            byte_idx        <= 9'd0;
            frame_id        <= 32'd0;
            drop_cnt        <= 32'd0;
            drop_since_last <= 1'b0;
            fifo_wr_en      <= 1'b0;
            fifo_wr_data    <= 8'd0;
            buf_addr        <= 7'd0;
            commit_pulse    <= 1'b0;
            commit_frame_id <= 32'd0;
            drop_pulse      <= 1'b0;
            crc_init        <= 1'b0;
            crc_valid       <= 1'b0;
            crc_data        <= 8'd0;
            ch_idx          <= 7'd0;
            sample_latch    <= 16'd0;
            tail_offset     <= 8'd0;
        end else begin
            // Default: deassert pulses each cycle
            fifo_wr_en   <= 1'b0;
            commit_pulse <= 1'b0;
            drop_pulse   <= 1'b0;
            crc_init     <= 1'b0;
            crc_valid    <= 1'b0;

            case (state)
                // ─────────────────────────────────────────────────────
                // S_IDLE: Wait for pending cycle_done
                // ─────────────────────────────────────────────────────
                S_IDLE: begin
                    if (pending)
                        state <= S_CHECK;
                end

                // ─────────────────────────────────────────────────────
                // S_CHECK: Atomic commit gate — check FIFO has room
                //          Also build header + diagnostic buffers
                // ─────────────────────────────────────────────────────
                S_CHECK: begin
                    if (wr_free_bytes >= FRAME_SIZE[15:0]) begin
                        // ── Build header buffer ─────────────────────
                        begin : build_hdr
                            reg [7:0] status_byte;
                            status_byte = {drop_since_last,
                                           (fifo_fill_level > 16'd16384),  // bit 6: > 50% full
                                           (fifo_fill_level > 16'd24576),  // bit 5: > 75% full
                                           1'b0,                           // bit 4: spi_crc_err (reserved)
                                           1'b0,                           // bit 3: spi_timeout (reserved)
                                           overflow_sticky,                // bit 2: overflow
                                           2'b00};                         // bits 1:0: reserved
                            hdr[0]  <= 8'hA5;                // Magic high
                            hdr[1]  <= 8'h5A;                // Magic low
                            hdr[2]  <= 8'h01;                // Version
                            hdr[3]  <= status_byte;          // Status
                            hdr[4]  <= frame_id[31:24];      // Frame ID [31:24]
                            hdr[5]  <= frame_id[23:16];      // Frame ID [23:16]
                            hdr[6]  <= frame_id[15:8];       // Frame ID [15:8]
                            hdr[7]  <= frame_id[7:0];        // Frame ID [7:0]
                            hdr[8]  <= timestamp[31:24];     // Timestamp [31:24]
                            hdr[9]  <= timestamp[23:16];     // Timestamp [23:16]
                            hdr[10] <= timestamp[15:8];      // Timestamp [15:8]
                            hdr[11] <= timestamp[7:0];       // Timestamp [7:0]
                            hdr[12] <= 8'd0;                 // Channel count high (64 = 0x0040)
                            hdr[13] <= NUM_CH[7:0];          // Channel count low
                            hdr[14] <= 8'h00;                // Sample rate code high
                            hdr[15] <= 8'h01;                // Sample rate code low (0x0001 = 20kSPS)
                        end

                        // ── Build diagnostic buffer ─────────────────
                        begin : build_diag
                            // FIFO fill level (32-bit, big-endian, upper 16 = 0)
                            diag_buf[0]  <= 8'd0;
                            diag_buf[1]  <= 8'd0;
                            diag_buf[2]  <= fifo_fill_level[15:8];
                            diag_buf[3]  <= fifo_fill_level[7:0];
                            // Drop count (32-bit, big-endian)
                            diag_buf[4]  <= drop_cnt[31:24];
                            diag_buf[5]  <= drop_cnt[23:16];
                            diag_buf[6]  <= drop_cnt[15:8];
                            diag_buf[7]  <= drop_cnt[7:0];
                            // Stall count (32-bit, big-endian)
                            diag_buf[8]  <= ext_stall_count[31:24];
                            diag_buf[9]  <= ext_stall_count[23:16];
                            diag_buf[10] <= ext_stall_count[15:8];
                            diag_buf[11] <= ext_stall_count[7:0];
                            // SPI error count (32-bit, zero placeholder)
                            diag_buf[12] <= 8'd0;
                            diag_buf[13] <= 8'd0;
                            diag_buf[14] <= 8'd0;
                            diag_buf[15] <= 8'd0;
                        end

                        crc_init <= 1'b1;       // Reset CRC to 0xFFFF
                        byte_idx <= 9'd0;
                        ch_idx   <= 7'd0;
                        state    <= S_HEADER;
                    end else begin
                        // DROP: insufficient FIFO space
                        frame_id        <= frame_id + 32'd1;
                        drop_cnt        <= drop_cnt + 32'd1;
                        drop_since_last <= 1'b1;
                        drop_pulse      <= 1'b1;
                        state           <= S_IDLE;
                    end
                end

                // ─────────────────────────────────────────────────────
                // S_HEADER: Emit header bytes [0..15]
                //           One byte per clock cycle
                // ─────────────────────────────────────────────────────
                S_HEADER: begin
                    fifo_wr_en   <= 1'b1;
                    fifo_wr_data <= hdr[byte_idx[3:0]];
                    crc_valid    <= 1'b1;
                    crc_data     <= hdr[byte_idx[3:0]];
                    byte_idx     <= byte_idx + 9'd1;

                    if (byte_idx == (HDR_SIZE - 1)) begin
                        // Last header byte being emitted this cycle.
                        // Pre-fetch channel 0 from BRAM (address appears now,
                        // data valid after S_FETCH latency cycle).
                        buf_addr <= 7'd0;
                        state    <= S_FETCH;
                    end
                end

                // ─────────────────────────────────────────────────────
                // S_FETCH: 1-cycle BRAM read latency
                //          buf_addr was set last cycle; buf_data valid next.
                // ─────────────────────────────────────────────────────
                S_FETCH: begin
                    state <= S_ADC_HI;
                end

                // ─────────────────────────────────────────────────────
                // S_ADC_HI: Emit high byte of current ADC/AUX sample
                //           Latch full 16-bit word from BRAM
                // ─────────────────────────────────────────────────────
                S_ADC_HI: begin
                    sample_latch <= buf_data;
                    fifo_wr_en   <= 1'b1;
                    fifo_wr_data <= buf_data[15:8];
                    crc_valid    <= 1'b1;
                    crc_data     <= buf_data[15:8];
                    byte_idx     <= byte_idx + 9'd1;
                    state        <= S_ADC_LO;
                end

                // ─────────────────────────────────────────────────────
                // S_ADC_LO: Emit low byte; advance channel; pre-fetch or transition
                // ─────────────────────────────────────────────────────
                S_ADC_LO: begin
                    fifo_wr_en   <= 1'b1;
                    fifo_wr_data <= sample_latch[7:0];
                    crc_valid    <= 1'b1;
                    crc_data     <= sample_latch[7:0];
                    byte_idx     <= byte_idx + 9'd1;
                    ch_idx       <= ch_idx + 7'd1;

                    if (ch_idx + 7'd1 < TOTAL_SAMPLES[6:0]) begin
                        // More samples: pre-fetch next channel, wait 1 cycle
                        buf_addr <= ch_idx + 7'd1;
                        state    <= S_FETCH;
                    end else begin
                        // All ADC + AUX samples emitted → move to tail
                        tail_offset <= 8'd0;
                        state       <= S_TAIL;
                    end
                end

                // ─────────────────────────────────────────────────────
                // S_TAIL: Emit diagnostics [150..165] + reserved [166..253]
                //         Uses tail_offset as local counter.
                //         When byte_idx reaches CRC_START, transition to
                //         S_CRC to let the CRC register settle (1-cycle
                //         latency) before reading crc_value.
                // ─────────────────────────────────────────────────────
                S_TAIL: begin
                    if (byte_idx < (DIAG_START + DIAG_BYTES)) begin
                        // Diagnostics region: byte_idx 150..165
                        fifo_wr_en   <= 1'b1;
                        fifo_wr_data <= diag_buf[tail_offset[3:0]];
                        crc_valid    <= 1'b1;
                        crc_data     <= diag_buf[tail_offset[3:0]];
                        byte_idx     <= byte_idx + 9'd1;
                        tail_offset  <= tail_offset + 8'd1;
                    end else if (byte_idx < CRC_START) begin
                        // Reserved region: zero-pad (byte_idx 166..253)
                        fifo_wr_en   <= 1'b1;
                        fifo_wr_data <= 8'h00;
                        crc_valid    <= 1'b1;
                        crc_data     <= 8'h00;
                        byte_idx     <= byte_idx + 9'd1;
                        tail_offset  <= tail_offset + 8'd1;
                    end else begin
                        // byte_idx == CRC_START: last payload byte's
                        // crc_valid was asserted previous cycle; the CRC
                        // register latches it on THIS edge.  We must wait
                        // one cycle for crc_value to be valid.
                        state <= S_CRC;
                    end
                end

                // ─────────────────────────────────────────────────────
                // S_CRC: Emit CRC bytes [254..255]
                //        Entered after a 1-cycle gap so crc_value reflects
                //        all 254 payload bytes (0..253).
                // ─────────────────────────────────────────────────────
                S_CRC: begin
                    fifo_wr_en <= 1'b1;

                    if (byte_idx == CRC_START) begin
                        // CRC high byte (byte 254)
                        fifo_wr_data <= crc_value[15:8];
                        byte_idx     <= byte_idx + 9'd1;
                    end else begin
                        // CRC low byte (byte 255) — frame complete
                        fifo_wr_data    <= crc_value[7:0];
                        byte_idx        <= byte_idx + 9'd1;
                        commit_pulse    <= 1'b1;
                        commit_frame_id <= frame_id;
                        frame_id        <= frame_id + 32'd1;
                        drop_since_last <= 1'b0;
                        state           <= S_IDLE;
                    end
                end

                default: begin
                    state      <= S_IDLE;
                    fifo_wr_en <= 1'b0;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
