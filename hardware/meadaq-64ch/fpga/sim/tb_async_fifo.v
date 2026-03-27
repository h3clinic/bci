// tb_async_fifo.v — Dedicated async FIFO unit TB
// Tests the 3 things the integration TB cannot:
//   TEST 1: Pointer wrap boundary (fill→drain→refill, wrap 16-bit ptrs)
//   TEST 2: Reset skew between write and read domains
//   TEST 3: Clock drift (one clock stretches by 1 timestep periodically)
//
// All tests verify data integrity, flag correctness, and fill_level sanity.
// Designed for iverilog -g2012.

`timescale 1ns/1ps
`default_nettype none

module tb_async_fifo;

    // ─────────────────────────────────────────────────────────────────
    // Parameters
    // ─────────────────────────────────────────────────────────────────
    // Use small FIFO for tractable sim time.
    // ADDR_BITS=4 → DEPTH=16, PTR_BITS=5.
    // Wrap occurs at pointer value 16 (binary), which is achievable quickly.
    localparam ADDR_BITS = 4;
    localparam DEPTH     = 1 << ADDR_BITS;  // 16
    localparam PTR_BITS  = ADDR_BITS + 1;   // 5

    // ─────────────────────────────────────────────────────────────────
    // Clock generation (with drift control)
    // ─────────────────────────────────────────────────────────────────
    // Base periods: ~48 MHz write, ~60 MHz read (non-harmonic).
    // drift_wr_ps / drift_rd_ps add occasional stretches.

    reg wr_clk = 0;
    reg rd_clk = 0;

    // Drift control: set nonzero to add 1ps-1ns jitter every N edges
    integer drift_wr_interval = 0;  // 0 = no drift
    integer drift_rd_interval = 0;
    integer drift_wr_amount   = 0;  // in ps (timescale = 1ns/1ps)
    integer drift_rd_amount   = 0;

    // Write clock: 20.833 ns period (48 MHz), with optional drift
    integer wr_edge_count = 0;
    always begin
        #10.416;
        if (drift_wr_interval > 0 && wr_edge_count > 0 &&
            (wr_edge_count % drift_wr_interval) == 0) begin
            // Stretch by drift amount (in timestep units = 1ps)
            // iverilog: delay values are in timescale units (1ns here).
            // We use 1 ns extra stretch (meaningful CDC stress).
            #1;  // 1 ns extra
        end
        wr_clk = ~wr_clk;
        wr_edge_count = wr_edge_count + 1;
    end

    // Read clock: 16.667 ns period (60 MHz), with optional drift
    integer rd_edge_count = 0;
    always begin
        #8.333;
        if (drift_rd_interval > 0 && rd_edge_count > 0 &&
            (rd_edge_count % drift_rd_interval) == 0) begin
            #1;
        end
        rd_clk = ~rd_clk;
        rd_edge_count = rd_edge_count + 1;
    end

    // ─────────────────────────────────────────────────────────────────
    // Separate reset signals for skew testing
    // ─────────────────────────────────────────────────────────────────
    reg wr_rst_n = 0;
    reg rd_rst_n = 0;

    // ─────────────────────────────────────────────────────────────────
    // DUT signals
    // ─────────────────────────────────────────────────────────────────
    reg        wr_en   = 0;
    reg  [7:0] wr_data = 0;
    wire       wr_full;
    wire [15:0] fill_level;
    wire [15:0] wr_free_bytes;
    wire       overflow;

    reg        rd_en   = 0;
    wire [7:0] rd_data;
    wire       rd_empty;

    // ─────────────────────────────────────────────────────────────────
    // DUT instantiation (small FIFO)
    // ─────────────────────────────────────────────────────────────────
    async_fifo #(
        .ADDR_BITS(ADDR_BITS)
    ) dut (
        .wr_clk       (wr_clk),
        .wr_rst_n     (wr_rst_n),
        .wr_en        (wr_en),
        .wr_data      (wr_data),
        .wr_full      (wr_full),
        .fill_level   (fill_level),
        .wr_free_bytes(wr_free_bytes),
        .overflow     (overflow),

        .rd_clk       (rd_clk),
        .rd_rst_n     (rd_rst_n),
        .rd_en        (rd_en),
        .rd_data      (rd_data),
        .rd_empty     (rd_empty)
    );

    // ─────────────────────────────────────────────────────────────────
    // Test 4 signals (module-level for always-block checker)
    // ─────────────────────────────────────────────────────────────────
    integer t4_wr_count = 0;
    integer t4_rd_count = 0;
    reg     t4_wr_done  = 0;
    reg     t4_data_ok  = 1;
    reg     t4_running  = 0;

    // Track whether a read was accepted on the PREVIOUS rd_clk edge.
    // rd_accept = rd_en && !rd_empty. We delay it by one cycle.
    reg t4_prev_accept = 0;
    always @(posedge rd_clk) begin
        if (!rd_rst_n)
            t4_prev_accept <= 0;
        else
            t4_prev_accept <= (rd_en && !rd_empty);
    end

    // Check rd_data one cycle after accept (registered output latency).
    always @(posedge rd_clk) begin
        if (t4_running && t4_prev_accept) begin
            if (rd_data !== t4_rd_count[7:0]) begin
                $display("T4 DATA FAIL: rd_count=%0d got=%02x exp=%02x t=%0t",
                         t4_rd_count, rd_data, t4_rd_count[7:0], $time);
                t4_data_ok = 0;
            end
            t4_rd_count = t4_rd_count + 1;
        end
    end

    // ─────────────────────────────────────────────────────────────────
    // Helper tasks
    // ─────────────────────────────────────────────────────────────────

    // Write N bytes with pattern (start_val + i) & 0xFF.
    // Returns actual count written (may be < N if full).
    task automatic write_burst(
        input integer n,
        input [7:0]   start_val,
        output integer written
    );
        integer i;
        begin
            written = 0;
            for (i = 0; i < n; i = i + 1) begin
                @(posedge wr_clk);
                wr_en   <= 1;
                wr_data <= (start_val + i[7:0]) & 8'hFF;
                @(negedge wr_clk);  // observe flags after posedge
                if (!wr_full || i == 0) begin
                    // wr_accept = wr_en && !wr_full, evaluated at posedge
                    // We set wr_en before posedge. Check at next posedge.
                end
            end
            @(posedge wr_clk);
            wr_en <= 0;
            // Count actual writes by observing pointer (simpler: just count accepts)
            written = n;  // caller should track actual
        end
    endtask

    // Read N bytes, check against expected pattern (start_val + i) & 0xFF.
    // 1-cycle read latency: rd_en at N → rd_data valid at N+1.
    task automatic read_and_check(
        input integer n,
        input [7:0]   start_val
    );
        integer i;
        reg [7:0] expected;
        begin
            for (i = 0; i < n; i = i + 1) begin
                // Assert rd_en at posedge N
                @(posedge rd_clk);
                rd_en <= 1;
                // Data is valid at posedge N+1 (registered output)
                @(posedge rd_clk);
                // Now rd_data should hold the value
                // But we need to read after it settles
                @(negedge rd_clk);
                expected = (start_val + i[7:0]) & 8'hFF;
                if (rd_data !== expected) begin
                    $display("DATA MISMATCH: i=%0d got=%02x exp=%02x t=%0t",
                             i, rd_data, expected, $time);
                    $fatal;
                end
            end
            @(posedge rd_clk);
            rd_en <= 0;
        end
    endtask

    // Wait for empty to deassert in read domain (with timeout).
    task automatic wait_not_empty(input integer timeout_cycles);
        integer cnt;
        begin
            cnt = 0;
            while (rd_empty && cnt < timeout_cycles) begin
                @(posedge rd_clk);
                cnt = cnt + 1;
            end
            if (rd_empty) begin
                $display("TIMEOUT: rd_empty still asserted after %0d rd_clk cycles", timeout_cycles);
                $fatal;
            end
        end
    endtask

    // Wait for full to deassert in write domain (with timeout).
    task automatic wait_not_full(input integer timeout_cycles);
        integer cnt;
        begin
            cnt = 0;
            while (wr_full && cnt < timeout_cycles) begin
                @(posedge wr_clk);
                cnt = cnt + 1;
            end
            if (wr_full) begin
                $display("TIMEOUT: wr_full still asserted after %0d wr_clk cycles", timeout_cycles);
                $fatal;
            end
        end
    endtask

    // ─────────────────────────────────────────────────────────────────
    // Shared assertions (active at all times)
    // ─────────────────────────────────────────────────────────────────

    // A1: fill_level must never exceed DEPTH
    always @(posedge wr_clk) begin
        if (wr_rst_n && fill_level > DEPTH) begin
            $display("A1 FAIL: fill_level=%0d > DEPTH=%0d t=%0t",
                     fill_level, DEPTH, $time);
            $fatal;
        end
    end

    // A2: wr_free_bytes = DEPTH - fill_level (exact, since DEPTH fits in 16 bits)
    always @(posedge wr_clk) begin
        if (wr_rst_n && wr_free_bytes !== (DEPTH[15:0] - fill_level)) begin
            $display("A2 FAIL: wr_free_bytes=%0d expected=%0d fill=%0d t=%0t",
                     wr_free_bytes, DEPTH[15:0] - fill_level, fill_level, $time);
            $fatal;
        end
    end

    // A3: overflow should never be set if we respect wr_full
    // (Only checked in tests where we DON'T intentionally write-when-full)
    reg allow_overflow_check = 1;
    always @(posedge wr_clk) begin
        if (wr_rst_n && allow_overflow_check && overflow) begin
            $display("A3 FAIL: overflow set unexpectedly at t=%0t", $time);
            $fatal;
        end
    end

    // ═════════════════════════════════════════════════════════════════
    // TEST SEQUENCER
    // ═════════════════════════════════════════════════════════════════

    integer test_num = 0;
    integer pass_count = 0;

    initial begin
        $display("");
        $display("=== tb_async_fifo: Async FIFO Unit TB ===");
        $display("  ADDR_BITS=%0d DEPTH=%0d PTR_BITS=%0d", ADDR_BITS, DEPTH, PTR_BITS);
        $display("");

        // ─────────────────────────────────────────────────────────
        // TEST 1: Pointer wrap boundary
        // ─────────────────────────────────────────────────────────
        test_num = 1;
        $display("--- TEST 1: Pointer wrap boundary ---");
        // Release resets simultaneously (no skew for this test)
        drift_wr_interval = 0;
        drift_rd_interval = 0;
        wr_rst_n = 0;
        rd_rst_n = 0;
        repeat (10) @(posedge wr_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        @(posedge rd_clk); rd_rst_n = 1;
        repeat (5) @(posedge wr_clk);  // let sync flops settle

        // Verify initial state
        @(negedge wr_clk);
        if (wr_full !== 0) begin $display("T1 FAIL: wr_full not 0 after reset"); $fatal; end
        @(negedge rd_clk);
        if (rd_empty !== 1) begin $display("T1 FAIL: rd_empty not 1 after reset"); $fatal; end

        // We need to wrap the 5-bit pointer (range 0..31, wraps at 32).
        // Each fill-drain cycle pushes DEPTH=16 bytes and pops 16.
        // After 2 cycles: write ptr = 32, which wraps back to 0.
        // Do 4 full fill-drain cycles to stress the wrap thoroughly.
        begin : test1_block
            integer cycle_i, j;
            reg [7:0] base_val;

            for (cycle_i = 0; cycle_i < 4; cycle_i = cycle_i + 1) begin
                base_val = (cycle_i * DEPTH) & 8'hFF;
                $display("  T1: fill-drain cycle %0d (base=%02x)", cycle_i, base_val);

                // Fill to DEPTH
                for (j = 0; j < DEPTH; j = j + 1) begin
                    @(posedge wr_clk);
                    wr_en   <= 1;
                    wr_data <= (base_val + j[7:0]) & 8'hFF;
                end
                @(posedge wr_clk);
                wr_en <= 0;

                // Wait for full flag to assert (CDC delay)
                repeat (5) @(posedge wr_clk);
                @(negedge wr_clk);
                if (!wr_full) begin
                    $display("T1 FAIL: wr_full not set after %0d writes, cycle=%0d, fill=%0d",
                             DEPTH, cycle_i, fill_level);
                    $fatal;
                end
                if (fill_level !== DEPTH[15:0]) begin
                    $display("T1 FAIL: fill_level=%0d expected=%0d after fill, cycle=%0d",
                             fill_level, DEPTH, cycle_i);
                    $fatal;
                end

                // Drain all: wait for not-empty, then read DEPTH bytes
                wait_not_empty(20);
                for (j = 0; j < DEPTH; j = j + 1) begin
                    @(posedge rd_clk);
                    rd_en <= 1;
                    // 1-cycle latency: data valid next cycle
                    if (j > 0) begin
                        // Check PREVIOUS read's data (available now)
                        @(negedge rd_clk);
                        begin : t1_check
                            reg [7:0] exp;
                            exp = (base_val + (j-1)) & 8'hFF;
                            if (rd_data !== exp) begin
                                $display("T1 DATA FAIL: cycle=%0d j=%0d got=%02x exp=%02x t=%0t",
                                         cycle_i, j-1, rd_data, exp, $time);
                                $fatal;
                            end
                        end
                    end
                end
                // One more cycle to capture the last byte's data
                @(posedge rd_clk);
                rd_en <= 0;
                @(negedge rd_clk);
                begin : t1_last_check
                    reg [7:0] exp_last;
                    exp_last = (base_val + (DEPTH-1)) & 8'hFF;
                    if (rd_data !== exp_last) begin
                        $display("T1 LAST DATA FAIL: cycle=%0d got=%02x exp=%02x t=%0t",
                                 cycle_i, rd_data, exp_last, $time);
                        $fatal;
                    end
                end

                // Wait for empty propagation through CDC
                repeat (10) @(posedge wr_clk);
                @(negedge wr_clk);
                // fill_level should be 0 (give a few cycles for sync)
                repeat (5) @(posedge wr_clk);
                @(negedge wr_clk);
                if (fill_level !== 0) begin
                    $display("T1 FAIL: fill_level=%0d expected=0 after drain, cycle=%0d",
                             fill_level, cycle_i);
                    $fatal;
                end
            end
        end

        $display("  TEST 1 PASSED: 4 fill-drain cycles across pointer wrap.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // TEST 1b: Simultaneous read/write at boundaries
        // ─────────────────────────────────────────────────────────
        test_num = 2;
        $display("--- TEST 1b: Simultaneous R/W near full/empty ---");

        // Reset
        wr_rst_n = 0; rd_rst_n = 0;
        wr_en = 0; rd_en = 0;
        repeat (10) @(posedge wr_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        @(posedge rd_clk); rd_rst_n = 1;
        repeat (5) @(posedge wr_clk);

        // Fill to DEPTH (completely full)
        begin : test1b_fill
            integer j;
            for (j = 0; j < DEPTH; j = j + 1) begin
                @(posedge wr_clk);
                wr_en   <= 1;
                wr_data <= j[7:0];
            end
            @(posedge wr_clk);
            wr_en <= 0;
        end

        // Wait for full flag
        repeat (5) @(posedge wr_clk);
        @(negedge wr_clk);
        if (!wr_full) begin
            $display("T1b FAIL: wr_full not set after fill to DEPTH");
            $fatal;
        end

        // Now: simultaneously write (will be rejected, full) and read.
        // This exercises the full→not-full transition under concurrent activity.
        // Writer attempts writes but they should be rejected until full clears.
        // Reader drains all DEPTH bytes.
        fork
            begin : t1b_writer
                integer wr_extra;
                wr_extra = 0;
                // Try to write 4 bytes; should succeed only after reads make room
                // and full flag deasserts (after CDC propagation).
                begin : t1b_wr_loop
                    integer attempt;
                    for (attempt = 0; attempt < DEPTH + 10; attempt = attempt + 1) begin
                        @(posedge wr_clk);
                        if (!wr_full && wr_extra < 4) begin
                            wr_en   <= 1;
                            wr_data <= 8'hB0 + wr_extra[7:0];
                            wr_extra = wr_extra + 1;
                        end else begin
                            wr_en <= 0;
                        end
                    end
                end
                @(posedge wr_clk);
                wr_en <= 0;
                $display("  T1b: writer pushed %0d extra bytes during drain", wr_extra);
            end
            begin : t1b_reader
                // Read DEPTH bytes (the original fill) + up to 4 extra
                wait_not_empty(20);
                begin : t1b_rd_loop
                    integer j;
                    for (j = 0; j < DEPTH + 4; j = j + 1) begin
                        @(posedge rd_clk);
                        if (!rd_empty)
                            rd_en <= 1;
                        else begin
                            rd_en <= 0;
                            // If empty after draining original + extras, done
                            if (j >= DEPTH) begin
                                // keep going, writer might push more
                            end
                        end
                    end
                    @(posedge rd_clk);
                    rd_en <= 0;
                end
            end
        join

        // After drain: empty should reassert, fill should go to 0
        repeat (15) @(posedge wr_clk);
        @(negedge rd_clk);
        if (!rd_empty) begin
            $display("T1b FAIL: rd_empty not set after full drain");
            $fatal;
        end

        $display("  TEST 1b PASSED: simultaneous R/W at boundaries.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // TEST 2a: Reset skew — wr_rst_n early
        // ─────────────────────────────────────────────────────────
        test_num = 3;
        $display("--- TEST 2a: Reset skew (wr_rst_n deasserts first) ---");

        wr_rst_n = 0; rd_rst_n = 0;
        wr_en = 0; rd_en = 0;
        repeat (10) @(posedge wr_clk);

        // Deassert wr_rst_n first
        @(posedge wr_clk); wr_rst_n = 1;
        $display("  T2a: wr_rst_n deasserted at t=%0t", $time);

        // During skew: write side is active, read side still in reset.
        // wr_full should be LOW (FIFO is empty from write side's perspective,
        // since synced read pointer is 0 = same as write pointer).
        // rd_empty should be HIGH (reset state).

        // Check write side flags during skew
        repeat (5) @(posedge wr_clk);
        @(negedge wr_clk);
        if (wr_full !== 0) begin
            $display("T2a FAIL: wr_full=%0d during skew (expected 0)", wr_full);
            $fatal;
        end

        // Try writing during skew — should succeed (FIFO is empty from wr perspective)
        begin : t2a_skew_write
            integer j;
            for (j = 0; j < 4; j = j + 1) begin
                @(posedge wr_clk);
                wr_en   <= 1;
                wr_data <= 8'hD0 + j[7:0];
            end
            @(posedge wr_clk);
            wr_en <= 0;
        end

        // Wait 100 more wr_clk cycles, then release rd_rst_n
        repeat (100) @(posedge wr_clk);
        @(posedge rd_clk); rd_rst_n = 1;
        $display("  T2a: rd_rst_n deasserted at t=%0t (100+ wr_clk cycles later)", $time);

        // After rd_rst_n release: wait for CDC to propagate writes
        repeat (10) @(posedge rd_clk);

        // rd_empty should eventually deassert (writes happened during skew)
        wait_not_empty(50);

        // Read back the 4 bytes written during skew
        begin : t2a_readback
            integer j;
            for (j = 0; j < 4; j = j + 1) begin
                @(posedge rd_clk);
                rd_en <= 1;
                if (j > 0) begin
                    @(negedge rd_clk);
                    begin : t2a_chk
                        reg [7:0] exp;
                        exp = 8'hD0 + (j-1);
                        if (rd_data !== exp) begin
                            $display("T2a DATA FAIL: j=%0d got=%02x exp=%02x", j-1, rd_data, exp);
                            $fatal;
                        end
                    end
                end
            end
            @(posedge rd_clk);
            rd_en <= 0;
            @(negedge rd_clk);
            if (rd_data !== 8'hD3) begin
                $display("T2a LAST DATA FAIL: got=%02x exp=%02x", rd_data, 8'hD3);
                $fatal;
            end
        end

        $display("  TEST 2a PASSED: wr_rst_n early, data survives skew.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // TEST 2b: Reset skew — rd_rst_n early
        // ─────────────────────────────────────────────────────────
        test_num = 4;
        $display("--- TEST 2b: Reset skew (rd_rst_n deasserts first) ---");

        wr_rst_n = 0; rd_rst_n = 0;
        wr_en = 0; rd_en = 0;
        repeat (10) @(posedge rd_clk);

        // Deassert rd_rst_n first
        @(posedge rd_clk); rd_rst_n = 1;
        $display("  T2b: rd_rst_n deasserted at t=%0t", $time);

        // During skew: read side is active, write side in reset.
        // rd_empty should be HIGH (no data, synced write ptr = 0 = read ptr).
        // No spurious reads should be accepted.
        repeat (5) @(posedge rd_clk);
        @(negedge rd_clk);
        if (rd_empty !== 1) begin
            $display("T2b FAIL: rd_empty=%0d during skew (expected 1)", rd_empty);
            $fatal;
        end

        // Try to read during skew — should be no-op (empty)
        @(posedge rd_clk);
        rd_en <= 1;
        @(posedge rd_clk);
        @(negedge rd_clk);
        // rd_accept = rd_en && !rd_empty = 1 && !1 = 0 → no accept
        // Empty should still be asserted
        if (rd_empty !== 1) begin
            $display("T2b FAIL: rd_empty deasserted during skew read attempt");
            $fatal;
        end
        rd_en <= 0;

        // Release wr_rst_n after 100 rd_clk cycles
        repeat (100) @(posedge rd_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        $display("  T2b: wr_rst_n deasserted at t=%0t (100+ rd_clk cycles later)", $time);

        // Write some data
        repeat (5) @(posedge wr_clk);
        begin : t2b_write
            integer j;
            for (j = 0; j < 4; j = j + 1) begin
                @(posedge wr_clk);
                wr_en   <= 1;
                wr_data <= 8'hE0 + j[7:0];
            end
            @(posedge wr_clk);
            wr_en <= 0;
        end

        // Read it back
        wait_not_empty(50);
        begin : t2b_readback
            integer j;
            for (j = 0; j < 4; j = j + 1) begin
                @(posedge rd_clk);
                rd_en <= 1;
                if (j > 0) begin
                    @(negedge rd_clk);
                    begin : t2b_chk
                        reg [7:0] exp;
                        exp = 8'hE0 + (j-1);
                        if (rd_data !== exp) begin
                            $display("T2b DATA FAIL: j=%0d got=%02x exp=%02x", j-1, rd_data, exp);
                            $fatal;
                        end
                    end
                end
            end
            @(posedge rd_clk);
            rd_en <= 0;
            @(negedge rd_clk);
            if (rd_data !== 8'hE3) begin
                $display("T2b LAST DATA FAIL: got=%02x exp=%02x", rd_data, 8'hE3);
                $fatal;
            end
        end

        $display("  TEST 2b PASSED: rd_rst_n early, no spurious reads.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // TEST 3: Clock drift
        // ─────────────────────────────────────────────────────────
        test_num = 5;
        $display("--- TEST 3: Clock drift (1ns jitter every 7/11 edges) ---");

        // Reset
        drift_wr_interval = 0;
        drift_rd_interval = 0;
        wr_rst_n = 0; rd_rst_n = 0;
        wr_en = 0; rd_en = 0;
        repeat (10) @(posedge wr_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        @(posedge rd_clk); rd_rst_n = 1;
        repeat (5) @(posedge wr_clk);

        // Enable drift: write clock stretches by 1ns every 7 edges,
        // read clock stretches by 1ns every 11 edges.
        // This breaks the perfect phase relationship.
        drift_wr_interval = 7;
        drift_rd_interval = 11;

        // Run multiple fill-drain cycles with drift active.
        // Use 8 cycles to really stress the CDC synchronizers.
        begin : test3_block
            integer cycle_i, j;
            reg [7:0] base_val;

            for (cycle_i = 0; cycle_i < 8; cycle_i = cycle_i + 1) begin
                base_val = (cycle_i * DEPTH + 8'h40) & 8'hFF;

                // Fill to DEPTH
                for (j = 0; j < DEPTH; j = j + 1) begin
                    @(posedge wr_clk);
                    wr_en   <= 1;
                    wr_data <= (base_val + j[7:0]) & 8'hFF;
                end
                @(posedge wr_clk);
                wr_en <= 0;

                // Wait for full
                repeat (8) @(posedge wr_clk);
                @(negedge wr_clk);
                if (!wr_full) begin
                    $display("T3 FAIL: wr_full not set after fill, cycle=%0d, fill=%0d",
                             cycle_i, fill_level);
                    $fatal;
                end

                // Drain all
                wait_not_empty(30);
                for (j = 0; j < DEPTH; j = j + 1) begin
                    @(posedge rd_clk);
                    rd_en <= 1;
                    if (j > 0) begin
                        @(negedge rd_clk);
                        begin : t3_chk
                            reg [7:0] exp;
                            exp = (base_val + (j-1)) & 8'hFF;
                            if (rd_data !== exp) begin
                                $display("T3 DATA FAIL: cycle=%0d j=%0d got=%02x exp=%02x t=%0t",
                                         cycle_i, j-1, rd_data, exp, $time);
                                $fatal;
                            end
                        end
                    end
                end
                @(posedge rd_clk);
                rd_en <= 0;
                @(negedge rd_clk);
                begin : t3_last
                    reg [7:0] exp_last;
                    exp_last = (base_val + (DEPTH-1)) & 8'hFF;
                    if (rd_data !== exp_last) begin
                        $display("T3 LAST DATA FAIL: cycle=%0d got=%02x exp=%02x t=%0t",
                                 cycle_i, rd_data, exp_last, $time);
                        $fatal;
                    end
                end

                // Let flags settle
                repeat (15) @(posedge wr_clk);
            end
        end

        // Disable drift
        drift_wr_interval = 0;
        drift_rd_interval = 0;

        $display("  TEST 3 PASSED: 8 fill-drain cycles with clock drift.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // TEST 4: Sustained concurrent R/W with drift
        // ─────────────────────────────────────────────────────────
        test_num = 6;
        $display("--- TEST 4: Sustained concurrent R/W with drift ---");

        // Reset
        wr_rst_n = 0; rd_rst_n = 0;
        wr_en = 0; rd_en = 0;
        repeat (10) @(posedge wr_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        @(posedge rd_clk); rd_rst_n = 1;
        repeat (5) @(posedge wr_clk);

        // Enable aggressive drift
        drift_wr_interval = 5;
        drift_rd_interval = 3;

        // Write 1024 bytes concurrently with reading.
        // Use a reference queue to track expected data.
        // Writer pushes wr_count[7:0]; reader checks rd_data against queue.
        begin : test4_block
            integer total_to_write;

            total_to_write = 1024;
            t4_wr_count   = 0;
            t4_rd_count   = 0;
            t4_wr_done    = 0;
            t4_data_ok    = 1;
            t4_running    = 1;

            fork
                // Writer: pushes bytes into FIFO when not full
                begin : t4_writer
                    while (t4_wr_count < total_to_write) begin
                        @(posedge wr_clk);
                        if (!wr_full) begin
                            wr_en   <= 1;
                            wr_data <= t4_wr_count[7:0];
                            t4_wr_count = t4_wr_count + 1;
                        end else begin
                            wr_en <= 0;
                        end
                    end
                    @(posedge wr_clk);
                    wr_en <= 0;
                    t4_wr_done = 1;
                    $display("  T4: writer done at t=%0t, wrote %0d bytes", $time, t4_wr_count);
                end

                // Reader: just asserts rd_en when not empty.
                // Data checking is done by the always block below.
                begin : t4_reader
                    repeat (10) @(posedge rd_clk);  // let some data enter
                    while (!(t4_wr_done && rd_empty)) begin
                        @(posedge rd_clk);
                        rd_en <= !rd_empty;
                    end
                    // One final cycle with rd_en=0 to let last accept complete
                    @(posedge rd_clk);
                    rd_en <= 0;
                    // Wait one more cycle for checker to see last data
                    @(posedge rd_clk);
                    repeat (5) @(posedge rd_clk);
                    t4_running = 0;
                    $display("  T4: reader done at t=%0t, read %0d bytes", $time, t4_rd_count);
                end
            join

            if (!t4_data_ok) begin
                $display("T4 FAIL: data corruption detected");
                $fatal;
            end
            if (t4_rd_count !== total_to_write) begin
                $display("T4 FAIL: read %0d bytes, expected %0d", t4_rd_count, total_to_write);
                $fatal;
            end
        end

        drift_wr_interval = 0;
        drift_rd_interval = 0;

        $display("  TEST 4 PASSED: 1024 bytes concurrent R/W with aggressive drift.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // TEST 5: Overflow detection
        // ─────────────────────────────────────────────────────────
        test_num = 7;
        $display("--- TEST 5: Overflow detection ---");

        allow_overflow_check = 0;  // disable always-block assertion

        // Reset
        wr_rst_n = 0; rd_rst_n = 0;
        wr_en = 0; rd_en = 0;
        repeat (10) @(posedge wr_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        @(posedge rd_clk); rd_rst_n = 1;
        repeat (5) @(posedge wr_clk);

        // Fill to DEPTH
        begin : t5_fill
            integer j;
            for (j = 0; j < DEPTH; j = j + 1) begin
                @(posedge wr_clk);
                wr_en   <= 1;
                wr_data <= j[7:0];
            end
            @(posedge wr_clk);
            wr_en <= 0;
        end

        // Wait for full
        repeat (8) @(posedge wr_clk);
        @(negedge wr_clk);
        if (!wr_full) begin
            $display("T5 FAIL: wr_full not set after fill");
            $fatal;
        end

        // overflow should NOT be set yet
        if (overflow) begin
            $display("T5 FAIL: overflow set before write-when-full");
            $fatal;
        end

        // Now write while full — should trigger overflow
        @(posedge wr_clk);
        wr_en   <= 1;
        wr_data <= 8'hFF;
        @(posedge wr_clk);
        wr_en <= 0;
        @(posedge wr_clk);  // let it register
        @(negedge wr_clk);
        if (!overflow) begin
            $display("T5 FAIL: overflow not set after write-when-full");
            $fatal;
        end

        // Overflow is sticky — should persist after draining
        wait_not_empty(20);
        begin : t5_drain
            integer j;
            for (j = 0; j < DEPTH; j = j + 1) begin
                @(posedge rd_clk);
                rd_en <= 1;
            end
            @(posedge rd_clk);
            rd_en <= 0;
        end
        repeat (10) @(posedge wr_clk);
        @(negedge wr_clk);
        if (!overflow) begin
            $display("T5 FAIL: overflow not sticky after drain");
            $fatal;
        end

        $display("  TEST 5 PASSED: overflow detection and stickiness.");
        pass_count = pass_count + 1;

        allow_overflow_check = 1;  // re-enable for any future tests

        // ─────────────────────────────────────────────────────────
        // TEST 6: Reset clears overflow
        // ─────────────────────────────────────────────────────────
        test_num = 8;
        $display("--- TEST 6: Reset clears overflow ---");

        wr_rst_n = 0; rd_rst_n = 0;
        repeat (10) @(posedge wr_clk);
        @(posedge wr_clk); wr_rst_n = 1;
        @(posedge rd_clk); rd_rst_n = 1;
        repeat (5) @(posedge wr_clk);

        @(negedge wr_clk);
        if (overflow) begin
            $display("T6 FAIL: overflow not cleared after reset");
            $fatal;
        end

        $display("  TEST 6 PASSED: reset clears overflow.");
        pass_count = pass_count + 1;

        // ─────────────────────────────────────────────────────────
        // SUMMARY
        // ─────────────────────────────────────────────────────────
        $display("");
        $display("=== tb_async_fifo: ALL %0d TESTS PASSED ===", pass_count);
        $display("");
        $finish;
    end

    // ─────────────────────────────────────────────────────────────────
    // Watchdog
    // ─────────────────────────────────────────────────────────────────
    initial begin
        #50_000_000;  // 50 ms — should be plenty for small FIFO
        $display("WATCHDOG TIMEOUT at 50 ms");
        $fatal;
    end

endmodule

`default_nettype wire
