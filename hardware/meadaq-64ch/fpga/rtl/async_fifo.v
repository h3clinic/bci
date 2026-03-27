// async_fifo.v — Synthesizable Async FIFO for iCE40UP5K
// Implements §4 spec from rtl_arch.md (Rev 0.7):
//
//   - Non-FWFT, 1-cycle read latency
//   - Gray-code CDC (Cummings SNUG 2002)
//   - 16-bit pointers (15 addr + 1 wrap bit)
//   - Full detection: next_wr_ptr_gray top-2-bits inverted vs rd_gray_sync
//   - Empty detection: next_rd_ptr_gray == wr_gray_sync (after pop)
//                      rd_ptr_gray == wr_gray_sync (no pop)
//   - fill_level / wr_free_bytes in write domain (pessimistic-high / low)
//   - overflow: sticky
//
// Storage: reg array for simulation portability.
// For iCE40 synthesis, wrap with SB_SPRAM256KA behind `ifdef ICE40.
//
// True dual-port behavior: write port in wr_clk, read port in rd_clk.
// ADDR_BITS=15 → DEPTH=32768 → 32 KB.

`default_nettype none

module async_fifo #(
    parameter ADDR_BITS = 15
) (
    // Write domain
    input  wire        wr_clk,
    input  wire        wr_rst_n,
    input  wire        wr_en,
    input  wire [7:0]  wr_data,
    output reg         wr_full,
    output reg  [15:0] fill_level,
    output wire [15:0] wr_free_bytes,
    output reg         overflow,

    // Read domain
    input  wire        rd_clk,
    input  wire        rd_rst_n,
    input  wire        rd_en,
    output reg  [7:0]  rd_data,
    output reg         rd_empty
);

    localparam DEPTH    = 1 << ADDR_BITS;   // 32768
    localparam PTR_BITS = ADDR_BITS + 1;    // 16

    // ═════════════════════════════════════════════════════════════════
    // Storage
    // ═════════════════════════════════════════════════════════════════
    // Reg array — infers dual-port RAM in synthesis, works in simulation.
    // For iCE40: replace with 2× SB_SPRAM256KA behind `ifdef ICE40.
    reg [7:0] mem [0:DEPTH-1];

    // ═════════════════════════════════════════════════════════════════
    // Binary-to-Gray / Gray-to-Binary
    // ═════════════════════════════════════════════════════════════════
    function automatic [PTR_BITS-1:0] bin2gray(input [PTR_BITS-1:0] b);
        begin
            bin2gray = b ^ (b >> 1);
        end
    endfunction

    function automatic [PTR_BITS-1:0] gray2bin(input [PTR_BITS-1:0] g);
        integer i;
        begin
            gray2bin[PTR_BITS-1] = g[PTR_BITS-1];
            for (i = PTR_BITS-2; i >= 0; i = i - 1)
                gray2bin[i] = gray2bin[i+1] ^ g[i];
        end
    endfunction

    // ═════════════════════════════════════════════════════════════════
    // WRITE DOMAIN
    // ═════════════════════════════════════════════════════════════════

    // Write pointer (binary + gray)
    reg [PTR_BITS-1:0] wr_ptr_bin;
    reg [PTR_BITS-1:0] wr_ptr_gray;

    // Accepted write = wr_en AND not full
    wire wr_accept = wr_en && !wr_full;

    // Next write pointer
    wire [PTR_BITS-1:0] next_wr_ptr_bin  = wr_ptr_bin + 1;
    wire [PTR_BITS-1:0] next_wr_ptr_gray = bin2gray(next_wr_ptr_bin);

    // CDC: rd_ptr_gray → wr_clk (2-FF synchronizer)
    reg [PTR_BITS-1:0] rd_gray_sync1, rd_gray_sync2;

    always @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) begin
            rd_gray_sync1 <= {PTR_BITS{1'b0}};
            rd_gray_sync2 <= {PTR_BITS{1'b0}};
        end else begin
            rd_gray_sync1 <= rd_ptr_gray_out;  // from read domain
            rd_gray_sync2 <= rd_gray_sync1;
        end
    end

    // Binary version of synchronized read pointer (for fill_level)
    wire [PTR_BITS-1:0] rd_ptr_bin_sync = gray2bin(rd_gray_sync2);

    // fill_level intermediate: PTR_BITS-wide subtraction, then zero-extended.
    // This ensures correct wrap-around arithmetic for any parameterization.
    // For PTR_BITS==16 (production), fill_diff IS fill_level (no extension).
    wire [PTR_BITS-1:0] fill_diff_cur  = wr_ptr_bin - rd_ptr_bin_sync;
    wire [PTR_BITS-1:0] fill_diff_next = (wr_ptr_bin + 1'b1) - rd_ptr_bin_sync;

    // wr_free_bytes: pessimistic-low, no saturation (§4)
    assign wr_free_bytes = DEPTH[15:0] - fill_level;

    // Full detection: Cummings wrap-bit inversion compare
    // Uses NEXT write pointer gray vs synchronized read pointer gray.
    // Full = top 2 gray bits inverted-equal, rest equal.
    wire next_wr_full =
        (next_wr_ptr_gray[PTR_BITS-1]   != rd_gray_sync2[PTR_BITS-1])   &&
        (next_wr_ptr_gray[PTR_BITS-2]   != rd_gray_sync2[PTR_BITS-2])   &&
        (next_wr_ptr_gray[PTR_BITS-3:0] == rd_gray_sync2[PTR_BITS-3:0]);

    // Write pointer, wr_full, fill_level, overflow, memory write
    always @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) begin
            wr_ptr_bin  <= {PTR_BITS{1'b0}};
            wr_ptr_gray <= {PTR_BITS{1'b0}};
            wr_full     <= 1'b0;
            fill_level  <= 16'd0;
            overflow    <= 1'b0;
        end else begin
            // fill_level: PTR_BITS-wide difference, auto zero-extended to [15:0].
            // Correct for all ADDR_BITS parameterizations including wrap.
            if (wr_accept)
                fill_level <= fill_diff_next;
            else
                fill_level <= fill_diff_cur;

            if (wr_accept) begin
                // Write to memory
                mem[wr_ptr_bin[ADDR_BITS-1:0]] <= wr_data;
                // Advance pointer
                wr_ptr_bin  <= next_wr_ptr_bin;
                wr_ptr_gray <= next_wr_ptr_gray;
                // Full check uses the pointer AFTER this write
                wr_full <= next_wr_full;
            end else begin
                // No write: re-evaluate full in case read side advanced
                wr_full <= (wr_ptr_gray[PTR_BITS-1]   != rd_gray_sync2[PTR_BITS-1])   &&
                           (wr_ptr_gray[PTR_BITS-2]   != rd_gray_sync2[PTR_BITS-2])   &&
                           (wr_ptr_gray[PTR_BITS-3:0] == rd_gray_sync2[PTR_BITS-3:0]);
            end

            // Overflow detection: wr_en asserted while full (sticky)
            if (wr_en && wr_full)
                overflow <= 1'b1;
        end
    end

    // ═════════════════════════════════════════════════════════════════
    // READ DOMAIN
    // ═════════════════════════════════════════════════════════════════

    // Read pointer (binary + gray)
    reg [PTR_BITS-1:0] rd_ptr_bin;
    reg [PTR_BITS-1:0] rd_ptr_gray;
    // Wire alias for CDC source (read domain drives this)
    wire [PTR_BITS-1:0] rd_ptr_gray_out = rd_ptr_gray;

    // Accepted read = rd_en AND not empty (§4 requirement #2)
    wire rd_accept = rd_en && !rd_empty;

    // Next read pointer
    wire [PTR_BITS-1:0] next_rd_ptr_bin  = rd_ptr_bin + 1;
    wire [PTR_BITS-1:0] next_rd_ptr_gray = bin2gray(next_rd_ptr_bin);

    // CDC: wr_ptr_gray → rd_clk (2-FF synchronizer)
    reg [PTR_BITS-1:0] wr_gray_sync1, wr_gray_sync2;

    always @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) begin
            wr_gray_sync1 <= {PTR_BITS{1'b0}};
            wr_gray_sync2 <= {PTR_BITS{1'b0}};
        end else begin
            wr_gray_sync1 <= wr_ptr_gray;  // from write domain
            wr_gray_sync2 <= wr_gray_sync1;
        end
    end

    // Empty detection: Cummings gray-code equality
    // If accepting a read, use NEXT rd pointer gray.
    // Otherwise use CURRENT rd pointer gray.
    wire next_rd_empty = (next_rd_ptr_gray == wr_gray_sync2);

    // Read pointer, rd_empty, rd_data (1-cycle read latency)
    //
    // Read contract (§4): rd_en at edge N → rd_data valid at N+1.
    // Implementation: at edge N, if rd_accept, read mem[rd_ptr] into
    // rd_data register. rd_data output = register Q → valid at N+1.

    always @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) begin
            rd_ptr_bin  <= {PTR_BITS{1'b0}};
            rd_ptr_gray <= {PTR_BITS{1'b0}};
            rd_empty    <= 1'b1;
            rd_data     <= 8'd0;
        end else begin
            if (rd_accept) begin
                // Read from memory — registered output = 1-cycle latency
                rd_data     <= mem[rd_ptr_bin[ADDR_BITS-1:0]];
                // Advance pointer
                rd_ptr_bin  <= next_rd_ptr_bin;
                rd_ptr_gray <= next_rd_ptr_gray;
                // Empty check after this pop
                rd_empty    <= next_rd_empty;
            end else begin
                // No read: re-evaluate empty in case write side advanced
                rd_empty <= (rd_ptr_gray == wr_gray_sync2);
            end
        end
    end

endmodule

`default_nettype wire
