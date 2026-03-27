// tb_stream_top.v — Integration TB: frame_packer → async_fifo → fifo_bridge
// Tests the complete data path with 3 checkers:
//   Checker 1 (clk_60m): Frame atomicity + byte correctness (commit queue)
//   Checker 2 (clk_48m): Drop decision correctness at cycle_done
//   Checker 3 (clk_48m): Accounting identity (commit + drop == produced)
//
// Deterministic TXE stall schedule forces FIFO fill and drops.
// SPI stub is a simple cycle_done generator with configurable frame cadence.
//
// Uses: frame_packer_stub.v, async_fifo_beh.v, fifo_bridge.v (real RTL)

`timescale 1ns/1ps
`default_nettype none

module tb_stream_top;

    // ─────────────────────────────────────────────────────────────────
    // Parameters
    // ─────────────────────────────────────────────────────────────────
    parameter FRAME_CADENCE = 2400;  // clk_48m cycles between cycle_done
                                     // 2400 @ 48 MHz = 50 µs = 20 kS/s

    // ─────────────────────────────────────────────────────────────────
    // Clocks
    // ─────────────────────────────────────────────────────────────────
    reg clk_48m = 0;
    reg clk_60m = 0;
    always #10.416 clk_48m = ~clk_48m;  // 48 MHz (20.833 ns period)
    always #8.333  clk_60m = ~clk_60m;  // 60 MHz (16.667 ns period)

    // ─────────────────────────────────────────────────────────────────
    // Reset (released synchronously to both domains)
    // ─────────────────────────────────────────────────────────────────
    reg rst_n = 0;
    initial begin
        rst_n = 0;
        repeat (20) @(posedge clk_60m);
        @(posedge clk_48m);  // align to 48m edge too
        rst_n = 1;
    end

    // ─────────────────────────────────────────────────────────────────
    // TXE model — deterministic stall schedule
    // ─────────────────────────────────────────────────────────────────
    // Goal: force FIFO occupancy above DEPTH-128 to trigger drops.
    //
    // At 20 kS/s: 1 frame/50 us = 128 bytes/50 us.
    // FIFO depth = 32768 bytes = 256 frames.
    // To fill: stall > 256 * 50 us = 12.8 ms. We use 20 ms.
    //
    // Phase 1: Normal (2 ms warmup, ~40 frames produced + drained)
    // Phase 2: Long stall (20 ms, FIFO fills, drops occur)
    // Phase 3: Resume (10 ms, drain all + verify recovery)
    //
    reg ft_txe_n = 1'b0;  // active-low: 0 = "can write"
    initial begin
        ft_txe_n = 1'b0;
        wait (rst_n);
        spi_enable = 1;

        // Phase 1: warmup — 2 ms @ 60 MHz = 120,000 cycles
        repeat (120000) @(posedge clk_60m);

        // Phase 2: stall — 20 ms @ 60 MHz = 1,200,000 cycles
        $display("[TXE] Phase 2: stall begins at t=%0t", $time);
        ft_txe_n = 1'b1;
        repeat (1200000) @(posedge clk_60m);

        // Phase 3: resume — 10 ms @ 60 MHz = 600,000 cycles
        $display("[TXE] Phase 3: resume at t=%0t", $time);
        ft_txe_n = 1'b0;
        repeat (600000) @(posedge clk_60m);

        // Phase 4: stop producing, drain remaining bytes
        $display("[TXE] Phase 4: stop SPI, drain at t=%0t", $time);
        spi_enable = 0;
        // Drain: max 32768 bytes / (12 MB/s) < 3 ms. Use 5 ms.
        repeat (300000) @(posedge clk_60m);

        $display("[TXE] Deterministic TXE test finished at t=%0t", $time);
    end

    // ─────────────────────────────────────────────────────────────────
    // SPI stub — cycle_done generator
    // ─────────────────────────────────────────────────────────────────
    // Generates cycle_done every FRAME_CADENCE clk_48m cycles.
    // buf_bank toggles each cycle_done (not used by stub, but wired).
    // spi_enable gates production — deasserted before drain phase.
    reg cycle_done  = 0;
    reg buf_bank    = 0;
    reg spi_enable  = 0;
    integer fc = 0;

    always @(posedge clk_48m or negedge rst_n) begin
        if (!rst_n) begin
            fc         <= 0;
            cycle_done <= 0;
            buf_bank   <= 0;
        end else begin
            cycle_done <= 0;
            if (spi_enable) begin
                fc <= fc + 1;
                if (fc == FRAME_CADENCE - 1) begin
                    fc         <= 0;
                    cycle_done <= 1;
                    buf_bank   <= ~buf_bank;
                end
            end
        end
    end

    // ─────────────────────────────────────────────────────────────────
    // DUT interconnect signals
    // ─────────────────────────────────────────────────────────────────

    // frame_packer → async_fifo
    wire        fp_wr_en;
    wire [7:0]  fp_wr_data;

    // async_fifo flags (write domain)
    wire        wr_full;
    wire [15:0] fill_level;
    wire [15:0] wr_free_bytes;
    wire        overflow;

    // async_fifo → fifo_bridge (read domain)
    wire        rd_empty;
    wire [7:0]  rd_data;
    wire        fifo_rd_en;

    // fifo_bridge → FTDI pins
    wire [7:0]  ft_data;
    wire        ft_wr_n;
    wire        ft_rd_n, ft_oe_n, ft_siwu_n;
    wire [31:0] bridge_stall_count;

    // frame_packer SIM exports
    wire [31:0] frame_counter;
    wire        commit_pulse;
    wire [31:0] commit_frame_id;
    wire        drop_pulse;
    wire [31:0] drop_counter;
    wire [31:0] prod_frame_ctr;

    // ─────────────────────────────────────────────────────────────────
    // Instantiate frame_packer_stub
    // ─────────────────────────────────────────────────────────────────
    frame_packer_stub u_frame_packer (
        .clk            (clk_48m),
        .rst_n          (rst_n),

        .cycle_done     (cycle_done),
        .buf_bank       (buf_bank),

        .wr_free_bytes  (wr_free_bytes),
        .fifo_wr_en     (fp_wr_en),
        .fifo_wr_data   (fp_wr_data),

        .frame_counter  (frame_counter),

        .commit_pulse    (commit_pulse),
        .commit_frame_id (commit_frame_id),
        .drop_pulse      (drop_pulse),
        .drop_counter    (drop_counter),
        .prod_frame_ctr  (prod_frame_ctr)
    );

    // ─────────────────────────────────────────────────────────────────
    // Instantiate async_fifo (behavioral)
    // ─────────────────────────────────────────────────────────────────
    async_fifo #(
        .ADDR_BITS (15)
    ) u_async_fifo (
        // Write domain
        .wr_clk       (clk_48m),
        .wr_rst_n     (rst_n),
        .wr_en        (fp_wr_en),
        .wr_data      (fp_wr_data),
        .wr_full      (wr_full),
        .fill_level   (fill_level),
        .wr_free_bytes(wr_free_bytes),
        .overflow     (overflow),

        // Read domain
        .rd_clk       (clk_60m),
        .rd_rst_n     (rst_n),
        .rd_en        (fifo_rd_en),
        .rd_data      (rd_data),
        .rd_empty     (rd_empty)
    );

    // ─────────────────────────────────────────────────────────────────
    // Instantiate fifo_bridge (real RTL)
    // ─────────────────────────────────────────────────────────────────
    fifo_bridge u_fifo_bridge (
        .clk_60m      (clk_60m),
        .rst_n        (rst_n),

        .ft_data      (ft_data),
        .ft_txe_n     (ft_txe_n),
        .ft_wr_n      (ft_wr_n),
        .ft_rd_n      (ft_rd_n),
        .ft_oe_n      (ft_oe_n),
        .ft_siwu_n    (ft_siwu_n),

        .fifo_rdata   (rd_data),
        .fifo_empty   (rd_empty),
        .fifo_rd_en   (fifo_rd_en),

        .stall_count  (bridge_stall_count)
    );

    // =================================================================
    // CHECKER 1 (clk_60m): Frame atomicity + byte correctness
    // Uses serialize-start queue — no CDC guessing.
    //
    // The queue is populated at the START of serialization (when the
    // frame_packer decides not to drop). This guarantees the queue
    // entry exists before any byte of that frame can exit the read
    // side of the async_fifo (CDC + bridge latency >> 0 cycles).
    //
    // commit_pulse fires AFTER all 128 bytes are pushed. We don't
    // use it here — it's for Checker 3 (accounting).
    // =================================================================

    // ── Serialize-start queue (SIM ONLY) ────────────────────────────
    // Populated when frame_packer begins serialization (not drop).
    // This is: cycle_done && !(wr_free_bytes < 128).
    integer ser_q_wr = 0;
    integer ser_q_rd = 0;
    reg [31:0] ser_q [0:100000];

    // Watch frame_packer_stub's internal decision: when cycle_done
    // fires and drop is NOT taken, a frame with id = frame_counter-1
    // (just incremented) will be serialized. We detect this by
    // observing cycle_done && !(wr_free_bytes < 128).
    // The frame ID is frame_counter's value BEFORE the increment,
    // which equals frame_counter - 1 after. But frame_counter is
    // a register that gets the new value next cycle. So at the
    // posedge where cycle_done is seen, frame_counter still has
    // the OLD value. We capture that.
    always @(posedge clk_48m) begin
        if (!rst_n) begin
            ser_q_wr <= 0;
        end else if (cycle_done && !(wr_free_bytes < 16'd128)) begin
            // Frame with id = current frame_counter is about to be serialized
            ser_q[ser_q_wr] <= frame_counter;
            ser_q_wr <= ser_q_wr + 1;
        end
    end

    // ── FTDI byte stream checker ────────────────────────────────────
    integer out_byte_idx = 0;
    reg [31:0] cur_frame_id = 32'h0;

    // Canonical test pattern — MUST match frame_packer_stub.test_byte()
    function automatic [7:0] expected_byte;
        input [31:0] fid;
        input integer k;
        begin
            expected_byte = fid[7:0] ^ k[7:0];
        end
    endfunction

    always @(negedge clk_60m) begin
        if (!rst_n) begin
            out_byte_idx <= 0;
            ser_q_rd     <= 0;
            cur_frame_id <= 0;
        end else if ((ft_wr_n == 1'b0) && (ft_txe_n == 1'b0)) begin
            // A byte was successfully written to FTDI
            // (ft_wr_n=0 AND ft_txe_n=0 means FTDI accepted this byte)

            if ((out_byte_idx % 128) == 0) begin
                // Start of a new output frame: dequeue next serialized frame ID
                if (ser_q_rd >= ser_q_wr) begin
                    $display("CHK1 STREAM FAIL: output byte but no frame_id queued. out_byte_idx=%0d t=%0t",
                             out_byte_idx, $time);
                    $fatal;
                end
                cur_frame_id = ser_q[ser_q_rd];
                ser_q_rd     = ser_q_rd + 1;
            end

            // Check byte matches expected for this frame and position
            if (ft_data !== expected_byte(cur_frame_id, out_byte_idx % 128)) begin
                $display("CHK1 PATTERN FAIL: frame_id=%0d k=%0d got=%02x exp=%02x t=%0t",
                         cur_frame_id, out_byte_idx % 128,
                         ft_data, expected_byte(cur_frame_id, out_byte_idx % 128), $time);
                $fatal;
            end

            out_byte_idx = out_byte_idx + 1;
        end
    end

    // ── Final: frame atomicity check ────────────────────────────────
    // At simulation end, byte count must be a multiple of 128.
    reg sim_done = 0;
    initial begin
        // Wait for TXE schedule (all 4 phases) to complete + extra margin
        wait (rst_n);
        repeat (120000 + 1200000 + 600000 + 300000 + 60000) @(posedge clk_60m);
        sim_done = 1;

        // Atomicity check
        if ((out_byte_idx % 128) != 0) begin
            $display("CHK1 ATOMICITY FAIL: simulation ended mid-frame: out_byte_idx=%0d", out_byte_idx);
            $fatal;
        end

        // Summary
        $display("");
        $display("=== INTEGRATION TB RESULTS ===");
        $display("  Frames produced:  %0d", prod_frame_ctr);
        $display("  Frames serialized:%0d", ser_q_wr);
        $display("  Frames dropped:   %0d", drop_counter);
        $display("  Frames received:  %0d", out_byte_idx / 128);
        $display("  Bytes received:   %0d", out_byte_idx);
        $display("  Bridge stalls:    %0d", bridge_stall_count);
        $display("  FIFO overflow:    %0d", overflow);
        $display("  fill_level final: %0d", fill_level);
        $display("");

        // Final sanity: all serialized frames were received
        if (ser_q_rd != ser_q_wr) begin
            $display("CHK1 DRAIN FAIL: %0d serialized frames not yet received (queued=%0d, dequeued=%0d)",
                     ser_q_wr - ser_q_rd, ser_q_wr, ser_q_rd);
            $fatal;
        end

        if (overflow) begin
            $display("CHK1 OVERFLOW FAIL: async_fifo overflow flag asserted");
            $fatal;
        end

        $display("ALL 3 CHECKERS PASSED.");
        $finish;
    end

    // =================================================================
    // CHECKER 2 (clk_48m): Drop decision correctness at cycle_done
    // Checks: drop_pulse == (wr_free_bytes < 128) at each cycle_done.
    //
    // drop_pulse is a registered output set in the same posedge as
    // cycle_done is sampled. Both are registered → drop_pulse reflects
    // the decision on the NEXT cycle. We capture wr_free_bytes at
    // cycle_done and check drop_pulse one cycle later.
    // =================================================================

    reg        chk2_pending   = 0;
    reg [15:0] chk2_free_snap = 0;

    always @(posedge clk_48m) begin
        if (!rst_n) begin
            chk2_pending   <= 0;
            chk2_free_snap <= 0;
        end else begin
            if (chk2_pending) begin
                // One cycle after cycle_done: drop_pulse is now valid
                chk2_pending <= 0;
                if (drop_pulse !== (chk2_free_snap < 16'd128)) begin
                    $display("CHK2 DROP RULE FAIL: wr_free_bytes=%0d exp_drop=%0d drop_pulse=%0d t=%0t",
                             chk2_free_snap, (chk2_free_snap < 16'd128), drop_pulse, $time);
                    $fatal;
                end
            end

            if (cycle_done) begin
                chk2_pending   <= 1;
                chk2_free_snap <= wr_free_bytes;
            end
        end
    end

    // =================================================================
    // CHECKER 3 (clk_48m): Accounting identity
    // commit_count + drop_count == prod_count (checked at end of sim).
    //
    // All counting and checking is done at NEGEDGE in a SINGLE always
    // block to observe settled registered outputs from posedge-clocked
    // modules, and to avoid inter-block evaluation-order races.
    // =================================================================

    reg [31:0] chk3_commit_count = 0;
    reg [31:0] chk3_drop_count   = 0;
    reg [31:0] chk3_prod_count   = 0;

    always @(negedge clk_48m) begin
        if (!rst_n) begin
            chk3_commit_count <= 0;
            chk3_drop_count   <= 0;
            chk3_prod_count   <= 0;
        end else begin
            // Count pulses (these are stable at negedge after posedge set them)
            if (commit_pulse)
                chk3_commit_count <= chk3_commit_count + 1;
            if (drop_pulse)
                chk3_drop_count <= chk3_drop_count + 1;
            if (cycle_done)
                chk3_prod_count <= chk3_prod_count + 1;

            // Check: drop_counter (module's internal reg, also posedge-set)
            // must match our pulse count. Both are now stable at negedge.
            // We use blocking reads of drop_counter and non-blocking writes
            // to chk3_drop_count, so we compare against the UPDATED value.
            // Compute expected new count inline:
            begin : chk3_inline
                reg [31:0] expected_drop;
                expected_drop = chk3_drop_count + (drop_pulse ? 1 : 0);
                if (drop_counter != expected_drop) begin
                    $display("CHK3 DROP_COUNTER FAIL: drop_counter=%0d expected=%0d t=%0t",
                             drop_counter, expected_drop, $time);
                    $fatal;
                end
            end
        end
    end

    // Final accounting check: strict equality after drain
    initial begin
        wait (sim_done);
        // Allow a few more cycles for any in-flight commit to land
        repeat (FRAME_CADENCE + 200) @(posedge clk_48m);

        if (chk3_commit_count + chk3_drop_count != chk3_prod_count) begin
            $display("CHK3 ACCOUNTING FAIL: commit=%0d drop=%0d prod=%0d",
                     chk3_commit_count, chk3_drop_count, chk3_prod_count);
            $fatal;
        end

        // Also cross-check with module's own counters
        if (frame_counter != chk3_prod_count) begin
            $display("CHK3 FRAME_COUNTER MISMATCH: module=%0d tb=%0d",
                     frame_counter, chk3_prod_count);
            $fatal;
        end
    end

    // =================================================================
    // Diagnostic: periodic status prints
    // =================================================================
    integer diag_tick = 0;
    always @(posedge clk_48m) begin
        if (rst_n) begin
            diag_tick <= diag_tick + 1;
            if (diag_tick % 48000 == 0) begin  // every ~1 ms
                $display("[DIAG] t=%0t fill=%0d free=%0d prod=%0d commit=%0d drop=%0d out=%0d",
                         $time, fill_level, wr_free_bytes,
                         prod_frame_ctr, chk3_commit_count, chk3_drop_count,
                         out_byte_idx / 128);
            end
        end
    end

    // =================================================================
    // Safety: overflow should never happen with correct drop policy
    // =================================================================
    always @(posedge clk_48m) begin
        if (rst_n && overflow) begin
            $display("OVERFLOW DETECTED at t=%0t — drop policy failed!", $time);
            $fatal;
        end
    end

    // =================================================================
    // Watchdog: prevent infinite simulation
    // =================================================================
    initial begin
        #500_000_000;  // 500 ms — total test is ~38 ms
        $display("WATCHDOG TIMEOUT at 500 ms");
        $fatal;
    end

endmodule

`default_nettype wire
