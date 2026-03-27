// async_fifo_beh.v — Behavioral Async FIFO for Integration Simulation
// Implements the §4 spec from rtl_arch.md:
//   - Non-FWFT, 1-cycle read latency (rd_en at edge N => rd_data at N+1)
//   - Gray-code CDC modeled as 2-cycle synchronizer delay
//   - fill_level in write domain (pessimistic-high)
//   - wr_free_bytes = DEPTH - fill_level (pessimistic-low, no saturation)
//   - rd_empty: pessimistic-safe (may lag writes by 2-3 rd_clk cycles)
//   - wr_full: pessimistic-safe
//   - overflow: sticky, asserts if wr_en while truly full
//
// NOT synthesizable. Uses reg arrays and integer math.
// Clock domain crossing is modeled with shift-register synchronizers.

`timescale 1ns/1ps
`default_nettype none

module async_fifo #(
    parameter ADDR_BITS = 15       // 2^15 = 32768 entries
) (
    // Write domain (clk_48m)
    input  wire        wr_clk,
    input  wire        wr_rst_n,
    input  wire        wr_en,
    input  wire [7:0]  wr_data,
    output reg         wr_full,
    output reg  [15:0] fill_level,
    output wire [15:0] wr_free_bytes,
    output reg         overflow,

    // Read domain (clk_60m)
    input  wire        rd_clk,
    input  wire        rd_rst_n,
    input  wire        rd_en,
    output reg  [7:0]  rd_data,
    output reg         rd_empty
);

    localparam DEPTH    = 1 << ADDR_BITS;   // 32768
    localparam PTR_BITS = ADDR_BITS + 1;    // 16 (15 addr + 1 wrap bit)

    // ── Storage ─────────────────────────────────────────────────────
    reg [7:0] mem [0:DEPTH-1];

    // ── Binary pointers ─────────────────────────────────────────────
    // PTR_BITS wide: [PTR_BITS-1] = wrap bit, [ADDR_BITS-1:0] = address
    reg [PTR_BITS-1:0] wr_ptr;
    reg [PTR_BITS-1:0] rd_ptr;

    // ── Gray-code pointers ──────────────────────────────────────────
    wire [PTR_BITS-1:0] wr_ptr_gray = wr_ptr ^ (wr_ptr >> 1);
    wire [PTR_BITS-1:0] rd_ptr_gray = rd_ptr ^ (rd_ptr >> 1);

    // ── CDC synchronizers (2-stage shift registers) ─────────────────
    // Write → Read: wr_ptr_gray synchronized into rd_clk domain
    reg [PTR_BITS-1:0] wr_gray_sync1, wr_gray_sync2;
    // Read → Write: rd_ptr_gray synchronized into wr_clk domain
    reg [PTR_BITS-1:0] rd_gray_sync1, rd_gray_sync2;

    // ── Gray-to-binary conversion ───────────────────────────────────
    function automatic [PTR_BITS-1:0] gray2bin(input [PTR_BITS-1:0] g);
        integer i;
        begin
            gray2bin[PTR_BITS-1] = g[PTR_BITS-1];
            for (i = PTR_BITS-2; i >= 0; i = i - 1)
                gray2bin[i] = gray2bin[i+1] ^ g[i];
        end
    endfunction

    wire [PTR_BITS-1:0] rd_ptr_bin_sync = gray2bin(rd_gray_sync2);

    // ── wr_free_bytes: pessimistic-low, no saturation ───────────────
    // Per §4: wr_free_bytes = 16'd32768 - fill_level[15:0]
    // If fill_level > DEPTH (invariant violation), this wraps — detectable.
    assign wr_free_bytes = 16'd32768 - fill_level;

    // ═════════════════════════════════════════════════════════════════
    // WRITE DOMAIN (wr_clk)
    // ═════════════════════════════════════════════════════════════════

    // CDC: rd_ptr_gray → wr_clk (2-FF synchronizer)
    always @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) begin
            rd_gray_sync1 <= {PTR_BITS{1'b0}};
            rd_gray_sync2 <= {PTR_BITS{1'b0}};
        end else begin
            rd_gray_sync1 <= rd_ptr_gray;
            rd_gray_sync2 <= rd_gray_sync1;
        end
    end

    // Write pointer + full detection + fill_level
    always @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) begin
            wr_ptr     <= {PTR_BITS{1'b0}};
            wr_full    <= 1'b0;
            fill_level <= 16'd0;
            overflow   <= 1'b0;
        end else begin
            // Update fill_level: wr_ptr_bin - rd_ptr_bin_sync
            // This is the pessimistic-high upper bound on occupancy.
            fill_level <= wr_ptr[PTR_BITS-1:0] - rd_ptr_bin_sync[PTR_BITS-1:0];

            // Full detection: Cummings wrap-bit inversion compare on Gray codes
            // Full when top 2 bits of wr_gray are inverted vs rd_gray_sync,
            // and remaining bits are equal.
            wr_full <= (wr_ptr_gray[PTR_BITS-1] != rd_gray_sync2[PTR_BITS-1]) &&
                       (wr_ptr_gray[PTR_BITS-2] != rd_gray_sync2[PTR_BITS-2]) &&
                       (wr_ptr_gray[PTR_BITS-3:0] == rd_gray_sync2[PTR_BITS-3:0]);

            if (wr_en) begin
                // Check for true overflow (write to actually full FIFO)
                if ((wr_ptr[ADDR_BITS-1:0] == rd_ptr[ADDR_BITS-1:0]) &&
                    (wr_ptr[PTR_BITS-1] != rd_ptr[PTR_BITS-1])) begin
                    overflow <= 1'b1;  // sticky
                end else begin
                    mem[wr_ptr[ADDR_BITS-1:0]] <= wr_data;
                    wr_ptr <= wr_ptr + 1;
                end
            end
        end
    end

    // ═════════════════════════════════════════════════════════════════
    // READ DOMAIN (rd_clk)
    // ═════════════════════════════════════════════════════════════════

    // CDC: wr_ptr_gray → rd_clk (2-FF synchronizer)
    always @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) begin
            wr_gray_sync1 <= {PTR_BITS{1'b0}};
            wr_gray_sync2 <= {PTR_BITS{1'b0}};
        end else begin
            wr_gray_sync1 <= wr_ptr_gray;
            wr_gray_sync2 <= wr_gray_sync1;
        end
    end

    // Read pointer + empty detection + 1-cycle read latency
    //
    // Read contract (§4): rd_en sampled high at edge N ⇒ rd_data valid at N+1.
    //
    // Implementation: on edge N, if rd_en && !rd_empty, latch mem[rd_ptr]
    // into rd_data (available at N+1 via register output) and advance rd_ptr.
    //
    // rd_en when rd_empty is a NO-OP (enforced here, not consumer convention).

    always @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) begin
            rd_ptr   <= {PTR_BITS{1'b0}};
            rd_empty <= 1'b1;
            rd_data  <= 8'd0;
        end else begin
            // Empty detection: Gray pointer equality (pessimistic-safe)
            rd_empty <= (rd_ptr_gray == wr_gray_sync2) ||
                        // After a pop, check if we just consumed the last entry
                        // by comparing next rd_ptr gray with synced wr_ptr gray
                        ((rd_en && !rd_empty) &&
                         (((rd_ptr + 1) ^ ((rd_ptr + 1) >> 1)) == wr_gray_sync2));

            if (rd_en && !rd_empty) begin
                // 1-cycle read: mem access registered → rd_data valid at N+1
                rd_data <= mem[rd_ptr[ADDR_BITS-1:0]];
                rd_ptr  <= rd_ptr + 1;
            end
            // else: rd_data holds previous value (non-FWFT)
        end
    end

endmodule

`default_nettype wire
