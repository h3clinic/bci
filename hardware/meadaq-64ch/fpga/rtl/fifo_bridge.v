// fifo_bridge.v — FT2232H Synchronous FIFO Write Bridge
// Rev 0.7 · 2026-03-01 · MEA DAQ 64-Channel
//
// 5-state FSM: S_IDLE → S_FETCH → S_WAIT → S_HOLD → S_WRITE
// Single POP site (S_IDLE only). No prefetch. S_WRITE always → S_IDLE.
// Throughput: 60 MHz / 5 = 12 MB/s. Required: 2.56 MB/s. Margin: 4.7×.
//
// Clock domain: clk_60m (FT_CLKOUT, 60 MHz, sourced by FT2232H)
// All outputs are registered. No combinational paths to FT2232H pins.

`default_nettype none

module fifo_bridge (
    input  wire       clk_60m,       // FT_CLKOUT — 60 MHz from FT2232H
    input  wire       rst_n,         // Active-low reset, synced to clk_60m

    // FT2232H synchronous FIFO interface
    output wire [7:0] ft_data,       // Data bus → FT2232H (always data_reg)
    input  wire       ft_txe_n,      // TX FIFO not full (active low)
    output wire       ft_wr_n,       // Write strobe (active low, pulsed 1 cycle)
    output wire       ft_rd_n,       // Permanently HIGH (not reading)
    output wire       ft_oe_n,       // Permanently HIGH (not reading)
    output wire       ft_siwu_n,     // Permanently HIGH (no flush)

    // Async FIFO read interface
    input  wire [7:0] fifo_rdata,    // Read data from async_fifo
    input  wire       fifo_empty,    // Async FIFO empty flag (read domain)
    output wire       fifo_rd_en,    // Async FIFO read enable

    // Diagnostics
    output wire [31:0] stall_count   // Cumulative TXE_N stall cycles
);

    // ── State encoding ──────────────────────────────────────────────
    localparam [2:0] S_IDLE  = 3'd0,
                     S_FETCH = 3'd1,
                     S_WAIT  = 3'd2,
                     S_HOLD  = 3'd3,
                     S_WRITE = 3'd4;

    // ── Registered signals ──────────────────────────────────────────
    reg [2:0]  state;
    reg [7:0]  data_reg;
    reg        pending_valid;
    reg        txe_n_r;
    reg        wr_n_reg;
    reg        rd_en_reg;
    reg [31:0] stall_cnt;

    // ── Output assignments (all from registers — no comb paths) ─────
    assign ft_data    = data_reg;
    assign ft_wr_n    = wr_n_reg;
    assign ft_rd_n    = 1'b1;
    assign ft_oe_n    = 1'b1;
    assign ft_siwu_n  = 1'b1;
    assign fifo_rd_en = rd_en_reg;
    assign stall_count = stall_cnt;

    // ── TXE registration (1-cycle input synchronizer) ───────────────
    // ft_txe_n is synchronous to clk_60m (FT2232H guarantees this).
    // We register it once for timing closure.
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n)
            txe_n_r <= 1'b1;  // default: assume FTDI busy
        else
            txe_n_r <= ft_txe_n;
    end

    // ── Stall counter ───────────────────────────────────────────────
    // Increments when we have data (pending or in FIFO) but FTDI is busy.
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n)
            stall_cnt <= 32'd0;
        else if (txe_n_r && (pending_valid || !fifo_empty))
            stall_cnt <= stall_cnt + 32'd1;
    end

    // ── 5-state FSM ─────────────────────────────────────────────────
    //
    // Compact spec (from rtl_arch.md §5):
    //
    // S_IDLE:
    //   if (!fifo_empty && !txe_n_r && !pending_valid): rd_en_next=1; next=S_FETCH
    //   else: hold
    //
    // S_FETCH: rd_en_next=0; next=S_WAIT
    //
    // S_WAIT: next=S_HOLD
    //
    // S_HOLD: data_reg_next=fifo_rdata; pending_valid_next=1;
    //   if (!txe_n_r): next=S_WRITE
    //   else:          next=S_HOLD  (stall, data_reg still loaded)
    //
    // S_WRITE:
    //   if (!txe_n_r && pending_valid): wr_n_next=0; pending_valid_next=0; next=S_IDLE
    //   if (txe_n_r):                   wr_n_next=1; next=S_HOLD  (FTDI went busy)

    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) begin
            state         <= S_IDLE;
            data_reg      <= 8'd0;
            pending_valid <= 1'b0;
            wr_n_reg      <= 1'b1;
            rd_en_reg     <= 1'b0;
        end else begin
            // ── Defaults (overridden per-state as needed) ───────────
            wr_n_reg  <= 1'b1;   // WR_N HIGH unless S_WRITE asserts it
            rd_en_reg <= 1'b0;   // rd_en LOW unless S_IDLE asserts it

            case (state)
                // ─────────────────────────────────────────────────────
                // S_IDLE: wait for FIFO data + FTDI ready + no pending
                // THE ONLY POP SITE. rd_en=1 registered here.
                // ─────────────────────────────────────────────────────
                S_IDLE: begin
                    if (!fifo_empty && !txe_n_r && !pending_valid) begin
                        rd_en_reg <= 1'b1;     // POP — FIFO sees next edge
                        state     <= S_FETCH;
                    end
                    // else: stay in S_IDLE (hold all outputs)
                end

                // ─────────────────────────────────────────────────────
                // S_FETCH: FIFO sees rd_en=1 THIS edge (registered
                // pipeline). SPRAM latches address. Read in progress.
                // rdata NOT YET VALID.
                // ─────────────────────────────────────────────────────
                S_FETCH: begin
                    state <= S_WAIT;
                end

                // ─────────────────────────────────────────────────────
                // S_WAIT: SPRAM read complete. rdata valid after K+1
                // tCO. Stable for >1 period. Unconditional → S_HOLD.
                // ─────────────────────────────────────────────────────
                S_WAIT: begin
                    state <= S_HOLD;
                end

                // ─────────────────────────────────────────────────────
                // S_HOLD: Latch fifo_rdata into data_reg. Set pending.
                // If FTDI ready → S_WRITE. If busy → stay S_HOLD.
                //
                // NOTE: data_reg is loaded EVERY cycle in S_HOLD, even
                // during TXE stalls. This is intentional — fifo_rdata
                // is stable (non-FWFT, no new POP issued). Re-latching
                // the same value is harmless. DO NOT optimize this
                // by gating on !pending_valid or first-entry-only.
                // ─────────────────────────────────────────────────────
                S_HOLD: begin
                    data_reg      <= fifo_rdata;
                    pending_valid <= 1'b1;

                    if (!txe_n_r)
                        state <= S_WRITE;
                    // else: stay in S_HOLD (stall)
                end

                // ─────────────────────────────────────────────────────
                // S_WRITE: Pulse WR_N for exactly 1 cycle.
                // ALWAYS → S_IDLE. NO PREFETCH. NO rd_en.
                // data_reg is FROZEN (not assigned in this state).
                // ─────────────────────────────────────────────────────
                S_WRITE: begin
                    if (!txe_n_r && pending_valid) begin
                        // Successful write: pulse WR_N low, clear pending
                        wr_n_reg      <= 1'b0;
                        pending_valid <= 1'b0;
                        state         <= S_IDLE;
                    end else begin
                        // FTDI went busy (txe_n_r=1): do NOT pulse WR_N.
                        // Return to S_HOLD to retry. Byte preserved.
                        wr_n_reg <= 1'b1;
                        state    <= S_HOLD;
                    end
                end

                // ─────────────────────────────────────────────────────
                // Default: unreachable. Reset to S_IDLE as safety net.
                // ─────────────────────────────────────────────────────
                default: begin
                    state         <= S_IDLE;
                    pending_valid <= 1'b0;
                    wr_n_reg      <= 1'b1;
                    rd_en_reg     <= 1'b0;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
