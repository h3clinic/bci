// frame_packer_stub.v — Sim-only frame packer for integration TB
// Implements §3 spec from rtl_arch.md:
//   - At cycle_done: check wr_free_bytes < 128 → drop or serialize
//   - Serializes 128 bytes from ping-pong bank into fifo_wr_en/fifo_wr_data
//   - Sim test pattern: byte k of frame fid = fid[7:0] ^ k[7:0]
//   - Exports commit_pulse, commit_frame_id, drop_pulse, drop_counter, prod_frame_ctr
//
// The ping-pong bank memory is internal to this stub. The TB drives
// cycle_done + buf_bank; this module fills the bank with the test
// pattern based on the current prod_frame_ctr, then serializes it.
//
// NOT synthesizable. Sim only.

`timescale 1ns/1ps
`default_nettype none

module frame_packer_stub (
    input  wire        clk,           // clk_48m
    input  wire        rst_n,

    // SPI stub interface
    input  wire        cycle_done,    // 1-cycle pulse: new frame ready
    input  wire        buf_bank,      // which bank just completed

    // Async FIFO write interface
    input  wire [15:0] wr_free_bytes, // from async_fifo write domain
    output reg         fifo_wr_en,
    output reg  [7:0]  fifo_wr_data,

    // Telemetry / counters
    output reg  [31:0] frame_counter,   // commit + drop (total produced)

    // SIM exports
    output reg         commit_pulse,
    output reg  [31:0] commit_frame_id,
    output reg         drop_pulse,
    output reg  [31:0] drop_counter,
    output wire [31:0] prod_frame_ctr   // alias for frame_counter
);

    assign prod_frame_ctr = frame_counter;

    // ── Internal state ──────────────────────────────────────────────
    // Serialization FSM
    localparam ST_IDLE      = 2'd0;
    localparam ST_SERIALIZE = 2'd1;
    localparam ST_COMMIT    = 2'd2;

    reg [1:0]  fp_state;
    reg [7:0]  byte_idx;           // 0..127 serialization counter
    reg [31:0] active_frame_id;    // frame ID being serialized
    reg        do_drop;            // latched drop decision

    // ── Sim test pattern ────────────────────────────────────────────
    // byte k of frame fid = fid[7:0] ^ k[7:0]
    // No actual bank memory needed — compute on the fly.

    function automatic [7:0] test_byte(input [31:0] fid, input [7:0] k);
        begin
            test_byte = fid[7:0] ^ k[7:0];
        end
    endfunction

    // ── FSM ─────────────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fp_state        <= ST_IDLE;
            byte_idx        <= 8'd0;
            active_frame_id <= 32'd0;
            do_drop         <= 1'b0;
            fifo_wr_en      <= 1'b0;
            fifo_wr_data    <= 8'd0;
            frame_counter   <= 32'd0;
            commit_pulse    <= 1'b0;
            commit_frame_id <= 32'd0;
            drop_pulse      <= 1'b0;
            drop_counter    <= 32'd0;
        end else begin
            // Default: clear pulses
            commit_pulse <= 1'b0;
            drop_pulse   <= 1'b0;
            fifo_wr_en   <= 1'b0;

            case (fp_state)
                // ─────────────────────────────────────────────────────
                // ST_IDLE: Wait for cycle_done. Make drop decision.
                // ─────────────────────────────────────────────────────
                ST_IDLE: begin
                    if (cycle_done) begin
                        active_frame_id <= frame_counter;
                        frame_counter   <= frame_counter + 1;

                        if (wr_free_bytes < 16'd128) begin
                            // DROP: not enough FIFO space
                            do_drop      <= 1'b1;
                            drop_pulse   <= 1'b1;
                            drop_counter <= drop_counter + 1;
                            // Stay in IDLE — nothing to serialize
                        end else begin
                            // COMMIT path: start serialization
                            do_drop  <= 1'b0;
                            byte_idx <= 8'd0;
                            fp_state <= ST_SERIALIZE;
                        end
                    end
                end

                // ─────────────────────────────────────────────────────
                // ST_SERIALIZE: Push 128 bytes, one per cycle
                // ─────────────────────────────────────────────────────
                ST_SERIALIZE: begin
                    fifo_wr_en   <= 1'b1;
                    fifo_wr_data <= test_byte(active_frame_id, byte_idx);
                    byte_idx     <= byte_idx + 1;

                    if (byte_idx == 8'd127) begin
                        fp_state <= ST_COMMIT;
                    end
                end

                // ─────────────────────────────────────────────────────
                // ST_COMMIT: Fire commit_pulse, return to idle
                // ─────────────────────────────────────────────────────
                ST_COMMIT: begin
                    commit_pulse    <= 1'b1;
                    commit_frame_id <= active_frame_id;
                    fp_state        <= ST_IDLE;
                end

                default: fp_state <= ST_IDLE;
            endcase
        end
    end

endmodule

`default_nettype wire
