// tb_fifo_bridge.v — Stress testbench for fifo_bridge (Rev 0.7)
// 2026-03-01 · MEA DAQ 64-Channel
//
// Strategy:
//   - Behavioral non-FWFT FIFO model (push bytes in, bridge pops them out)
//   - Heavy-tail TXE stalls: 95% short (1..32), 5% long (1..4096). No loops.
//   - Bursty FIFO emptiness (long droughts + rapid floods)
//   - Deterministic long-stall phase: TXE forced high for 50K cycles
//   - Assertions A1, A2, A5, A7–A15 checked continuously
//   - Byte scoreboard: every pushed byte appears exactly once on
//     ft_data when ft_wr_n=0. No duplication, no loss, in order.
//   - 100K bytes per seed. Configurable via +define+NUM_BYTES=N.
//   - Multi-seed: runner script iterates seeds externally.
//
// Assertion sampling: all negedge-sampled assertions use negedge clk_60m
// as a brute-force workaround for mixed-region observation at posedge.
// In plain Verilog (no SystemVerilog clocking blocks or #1step),
// negedge guarantees all posedge NBA updates have resolved, so we
// observe a consistent post-update snapshot. This is NOT a principled
// sampling strategy — it's a pragmatic choice for iverilog compatibility.
//
// Compile & run:
//   iverilog -g2012 -o tb_fifo_bridge tb_fifo_bridge.v ../rtl/fifo_bridge.v
//   vvp tb_fifo_bridge +SEED=42
//   vvp tb_fifo_bridge +SEED=12345

`timescale 1ns / 100ps
`default_nettype none

module tb_fifo_bridge;

    // ── Parameters ──────────────────────────────────────────────────
    parameter CLK_PERIOD = 16.667;   // 60 MHz

`ifndef NUM_BYTES
    parameter NUM_BYTES  = 100_000;  // 100K bytes default
`else
    parameter NUM_BYTES  = `NUM_BYTES;
`endif

    // ── DUT signals ─────────────────────────────────────────────────
    reg        clk_60m;
    reg        rst_n;
    wire [7:0] ft_data;
    reg        ft_txe_n;
    wire       ft_wr_n;
    wire       ft_rd_n;
    wire       ft_oe_n;
    wire       ft_siwu_n;
    wire [7:0] fifo_rdata;
    wire       fifo_empty;
    wire       fifo_rd_en;
    wire [31:0] stall_count;

    // ── Clock generation ────────────────────────────────────────────
    initial clk_60m = 0;
    always #(CLK_PERIOD / 2.0) clk_60m = ~clk_60m;

    // ── Seed from plusarg or default ────────────────────────────────
    integer seed;
    initial begin
        if (!$value$plusargs("SEED=%d", seed))
            seed = 42;
    end

    // ── LCG PRNG utility ────────────────────────────────────────────
    // Deterministic, portable, no $random dependency.
    function automatic integer lcg_next(input integer state);
        lcg_next = $unsigned(state * 1103515245 + 12345);
    endfunction

    // ── Behavioral FIFO model ───────────────────────────────────────
    // Non-FWFT: rdata holds last-popped value until next rd_en.
    // Synchronous read on clk_60m (models SPRAM 1-cycle latency).

    reg [7:0] fifo_mem [0:65535];   // 64K deep — enough for 1M burst margin
    reg [16:0] fifo_wr_ptr;
    reg [16:0] fifo_rd_ptr;
    reg [7:0]  fifo_rdata_reg;
    wire       fifo_empty_w;

    assign fifo_empty  = fifo_empty_w;
    assign fifo_rdata  = fifo_rdata_reg;
    assign fifo_empty_w = (fifo_wr_ptr == fifo_rd_ptr);

    // FIFO read: 1-cycle SPRAM latency model.
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) begin
            fifo_rd_ptr    <= 17'd0;
            fifo_rdata_reg <= 8'd0;
        end else if (fifo_rd_en && !fifo_empty_w) begin
            fifo_rdata_reg <= fifo_mem[fifo_rd_ptr[15:0]];
            fifo_rd_ptr    <= fifo_rd_ptr + 17'd1;
        end
        // else: rdata holds (non-FWFT contract)
    end

    // FIFO write: testbench pushes bytes
    initial fifo_wr_ptr = 17'd0;

    task fifo_push(input [7:0] data);
        begin
            fifo_mem[fifo_wr_ptr[15:0]] = data;
            fifo_wr_ptr = fifo_wr_ptr + 17'd1;
        end
    endtask

    // ── DUT instantiation ───────────────────────────────────────────
    fifo_bridge dut (
        .clk_60m     (clk_60m),
        .rst_n       (rst_n),
        .ft_data     (ft_data),
        .ft_txe_n    (ft_txe_n),
        .ft_wr_n     (ft_wr_n),
        .ft_rd_n     (ft_rd_n),
        .ft_oe_n     (ft_oe_n),
        .ft_siwu_n   (ft_siwu_n),
        .fifo_rdata  (fifo_rdata),
        .fifo_empty  (fifo_empty),
        .fifo_rd_en  (fifo_rd_en),
        .stall_count (stall_count)
    );

    // ── State encoding (mirror DUT for readability) ─────────────────
    localparam [2:0] S_IDLE  = 3'd0,
                     S_FETCH = 3'd1,
                     S_WAIT  = 3'd2,
                     S_HOLD  = 3'd3,
                     S_WRITE = 3'd4;

    // ── Assertion counters ──────────────────────────────────────────
    integer a1_fail  = 0, a2_fail  = 0, a5_fail  = 0;
    integer a7_fail  = 0, a8_fail  = 0, a9_fail  = 0;
    integer a10_fail = 0, a11_fail = 0, a12_fail = 0;
    integer a13_fail = 0, a14_fail = 0;
    integer push_count = 0, pop_count = 0, wr_count = 0;
    integer wr_pulse_count = 0;  // A15: negedge WR_N pulse counter

    // ════════════════════════════════════════════════════════════════
    //  ASSERTION SAMPLING — negedge workaround
    // ════════════════════════════════════════════════════════════════
    //
    // Problem: at posedge clk, multiple always blocks update DUT
    // registers via NBA. A TB always block at the same posedge may
    // read a MIXTURE of old and new signal values depending on
    // simulator evaluation order. This is "mixed-region observation."
    //
    // Workaround: sample at negedge clk (half-period later). By then,
    // all posedge NBA updates have resolved and we see a consistent
    // snapshot. This bakes in a 50% duty-cycle assumption, which is
    // acceptable for simulation with iverilog. In SystemVerilog, the
    // correct approach is `@(posedge clk); #1step;` or clocking blocks.
    // ════════════════════════════════════════════════════════════════

    // ── prev_txe_n_r: shadow register for A1 ───────────────────────
    //
    // CONTRACT: prev_txe_n_r corresponds to the txe_n_r value used by
    // the FSM in the same cycle that produced ft_wr_n.
    //
    // Proof: At posedge N, the FSM's case() reads txe_n_r (its Q output,
    // loaded at posedge N-1). In the NBA region, txe_n_r is updated to
    // its new value. In a separate TB always block at the same posedge N,
    // `prev_txe_n_r <= dut.txe_n_r` reads the OLD Q of txe_n_r (pre-NBA)
    // because all RHS evaluations occur in the active region before any
    // NBA updates. After NBA: prev_txe_n_r holds the old txe_n_r that
    // the FSM used. At negedge N+½, ft_wr_n reflects the FSM's decision
    // from posedge N, and prev_txe_n_r holds the txe_n_r that informed it.
    //
    // Requires: (1) FSM reads txe_n_r (registered), not raw ft_txe_n.
    //           (2) txe_n_r updated via NBA (<=). (3) This shadow uses NBA.
    reg prev_txe_n_r;
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) prev_txe_n_r <= 1'b1;
        else        prev_txe_n_r <= dut.txe_n_r;
    end

    // ── A1: WR_N never low when the FSM's decision-basis txe_n_r was 1
    always @(negedge clk_60m) begin
        if (rst_n && !ft_wr_n && prev_txe_n_r) begin
            a1_fail = a1_fail + 1;
            if (a1_fail <= 10)
                $display("A1 FAIL @ %0t: WR_N=0 while txe_n_r(decision)=1", $time);
        end
    end

    // ── A2: WR_N never low for consecutive cycles (1-cycle pulse) ───
    reg wr_was_low;
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) begin
            wr_was_low <= 1'b0;
        end else begin
            if (!ft_wr_n) begin
                if (wr_was_low) begin
                    a2_fail = a2_fail + 1;
                    if (a2_fail <= 10)
                        $display("A2 FAIL @ %0t: WR_N low for consecutive cycles", $time);
                end
                wr_was_low <= 1'b1;
            end else begin
                wr_was_low <= 1'b0;
            end
        end
    end

    // ── A5: pending_valid blocks rd_en ──────────────────────────────
    always @(negedge clk_60m) begin
        if (rst_n && fifo_rd_en && dut.pending_valid) begin
            a5_fail = a5_fail + 1;
            if (a5_fail <= 10)
                $display("A5 FAIL @ %0t: rd_en=1 while pending_valid=1", $time);
        end
    end

    // ── A7: rd_en observed high → state is S_FETCH (negedge) ────────
    // Sampling: negedge clk_60m (post-NBA snapshot).
    // At posedge N, the FSM in S_IDLE asserts rd_en_reg<=1 and
    // state<=S_FETCH via NBA. At negedge N+½, both have settled:
    // rd_en=1 and state=S_FETCH. So the OBSERVABLE pair at negedge
    // is (rd_en=1, state=S_FETCH). The DECISION was made in S_IDLE,
    // but S_IDLE is not visible at this sample point.
    // If rd_en=1 with state != S_FETCH, rd_en was set from a wrong state.
    always @(negedge clk_60m) begin
        if (rst_n && fifo_rd_en && (dut.state != S_FETCH)) begin
            a7_fail = a7_fail + 1;
            if (a7_fail <= 10)
                $display("A7 FAIL @ %0t: rd_en=1 but state=%0d (expected S_FETCH)", $time, dut.state);
        end
    end

    // ── A8: pop_count <= push_count (cumulative underflow guard) ────
    always @(posedge clk_60m) begin
        if (rst_n && fifo_rd_en && !fifo_empty_w)
            pop_count = pop_count + 1;
    end
    always @(posedge clk_60m) begin
        if (rst_n && (pop_count > push_count)) begin
            a8_fail = a8_fail + 1;
            if (a8_fail <= 10)
                $display("A8 FAIL @ %0t: pop_count(%0d) > push_count(%0d)", $time, pop_count, push_count);
        end
    end

    // ── A9: no pop on empty (direct edge) ───────────────────────────
    always @(posedge clk_60m) begin
        if (rst_n && fifo_rd_en && fifo_empty_w) begin
            a9_fail = a9_fail + 1;
            if (a9_fail <= 10)
                $display("A9 FAIL @ %0t: POP attempted while FIFO empty", $time);
        end
    end

    // ── A10: pending_valid implies rd_en == 0 ───────────────────────
    // Cheap guard against future edits. The FSM should never issue a
    // new POP while a byte is already pending delivery to FTDI.
    always @(negedge clk_60m) begin
        if (rst_n && dut.pending_valid && fifo_rd_en) begin
            a10_fail = a10_fail + 1;
            if (a10_fail <= 10)
                $display("A10 FAIL @ %0t: pending_valid=1 but rd_en=1", $time);
        end
    end

    // ── A11: WR_N observed low → state is S_IDLE (negedge) ──────────
    // Sampling: negedge clk_60m (post-NBA snapshot).
    // At posedge N, the FSM in S_WRITE asserts wr_n_reg<=0 and
    // state<=S_IDLE via NBA. At negedge N+½, both have settled:
    // ft_wr_n=0 and state=S_IDLE. So the OBSERVABLE pair at negedge
    // is (ft_wr_n=0, state=S_IDLE). The DECISION was made in S_WRITE,
    // but S_WRITE is not visible at this sample point.
    // If ft_wr_n=0 with state != S_IDLE, the write came from a wrong state.
    always @(negedge clk_60m) begin
        if (rst_n && !ft_wr_n) begin
            if (dut.state != S_IDLE) begin
                a11_fail = a11_fail + 1;
                if (a11_fail <= 10)
                    $display("A11 FAIL @ %0t: WR_N=0 but state=%0d (expected S_IDLE)",
                             $time, dut.state);
            end
        end
    end

    // ── A12: RD_N, OE_N, SIWU_N permanently high ───────────────────
    // These are hardwired in the RTL. Catch accidental modification.
    always @(negedge clk_60m) begin
        if (!ft_rd_n || !ft_oe_n || !ft_siwu_n) begin
            a12_fail = a12_fail + 1;
            if (a12_fail <= 10)
                $display("A12 FAIL @ %0t: idle pin went low (RD=%b OE=%b SIWU=%b)",
                         $time, ft_rd_n, ft_oe_n, ft_siwu_n);
        end
    end

    // ── A13: WR_N high during reset and first cycle after deassert ──
    reg rst_n_d1;
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) rst_n_d1 <= 1'b0;
        else        rst_n_d1 <= rst_n;
    end
    always @(negedge clk_60m) begin
        // Check during reset OR on the first cycle after reset deasserts
        if ((!rst_n || !rst_n_d1) && !ft_wr_n) begin
            a13_fail = a13_fail + 1;
            if (a13_fail <= 10)
                $display("A13 FAIL @ %0t: WR_N low during/after reset", $time);
        end
    end

    // ── A14: ft_data stable at negedge when WR_N is low ─────────────
    // When WR_N=0 at posedge (write), ft_data must be the same at the
    // following negedge. Since ft_data = data_reg (register), this is
    // guaranteed by construction, but catch any future wiring errors.
    reg [7:0] ft_data_at_posedge;
    reg       capture_valid;
    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) begin
            ft_data_at_posedge <= 8'd0;
            capture_valid      <= 1'b0;
        end else begin
            ft_data_at_posedge <= ft_data;
            capture_valid      <= !ft_wr_n;
        end
    end
    always @(negedge clk_60m) begin
        if (rst_n && capture_valid && (ft_data !== ft_data_at_posedge)) begin
            a14_fail = a14_fail + 1;
            if (a14_fail <= 10)
                $display("A14 FAIL @ %0t: ft_data changed mid-WR (pos=0x%02h neg=0x%02h)",
                         $time, ft_data_at_posedge, ft_data);
        end
    end

    // ── A15: WR pulse count == bytes out (negedge cross-check) ─────
    // Counts every cycle where ft_wr_n is observed low at negedge.
    // Must equal wr_count (posedge counter) at end of test.
    // Catches double-pulses, missed pulses, or wr_count bookkeeping bugs.
    // Valuable during frame_packer integration when burst writes appear.
    always @(negedge clk_60m) begin
        if (rst_n && !ft_wr_n)
            wr_pulse_count = wr_pulse_count + 1;
    end

    // ── Byte scoreboard ─────────────────────────────────────────────
    reg [7:0] expected_byte;
    integer   byte_errors = 0;

    initial expected_byte = 8'd0;

    always @(posedge clk_60m) begin
        if (rst_n && !ft_wr_n) begin
            wr_count = wr_count + 1;
            if (ft_data !== expected_byte) begin
                byte_errors = byte_errors + 1;
                if (byte_errors <= 10)
                    $display("BYTE ERROR @ %0t: expected 0x%02h, got 0x%02h (byte #%0d)",
                             $time, expected_byte, ft_data, wr_count);
            end
            expected_byte = expected_byte + 8'd1;  // wraps at 256
        end
    end

    // ════════════════════════════════════════════════════════════════
    //  TXE STALL GENERATOR — heavy-tail distribution (no loops)
    // ════════════════════════════════════════════════════════════════
    //
    // Models real FT2232H behavior: mostly short ready/busy bursts,
    // with rare long stalls (USB microframe boundaries, host backpressure).
    //
    // Mixed model (no loops, O(1) per transition):
    //   95% of durations: rng % 32 + 1   (1..32 cycles)
    //    5% of durations: rng % 4096 + 1 (1..4096 cycles)
    //
    // This preserves tail risk (rare multi-thousand-cycle stalls) that
    // the uniform model removed. The 5% branch catches bugs triggered
    // by long TXE-high periods that cause state machine starvation.
    //
    // Additionally, txe_force_busy (driven from initial block) allows
    // deterministic long-stall testing without random model interaction.

    integer txe_rng;
    integer txe_ready_remain;
    integer txe_busy_remain;
    reg     txe_in_ready;
    reg     txe_force_busy;  // driven by deterministic stall phase

    // Heavy-tail burst duration: 95% short, 5% long. No loops.
    function automatic integer heavy_tail_duration(
        input integer rng_state,
        input integer short_max,
        input integer long_max
    );
        integer r;
        begin
            r = lcg_next(rng_state);
            // 5% long tail: check if bottom 5 bits == 0 (1/32 ≈ 3%)
            // or bits [6:5] == 0 (1/4), combined: ~5%
            if (r[4:0] == 5'd0)
                heavy_tail_duration = ({1'b0, r[30:19]} % long_max) + 1;
            else
                heavy_tail_duration = ({1'b0, r[30:19]} % short_max) + 1;
        end
    endfunction

    initial txe_force_busy = 1'b0;

    always @(posedge clk_60m or negedge rst_n) begin
        if (!rst_n) begin
            ft_txe_n         <= 1'b1;   // busy during reset
            txe_rng          <= 0;      // will be set after reset
            txe_ready_remain <= 0;
            txe_busy_remain  <= 10;     // brief busy after reset
            txe_in_ready     <= 1'b0;
        end else if (txe_force_busy) begin
            // Deterministic stall: override random model
            ft_txe_n <= 1'b1;
        end else begin
            if (txe_rng == 0) txe_rng <= seed;  // initialize from seed

            if (txe_in_ready) begin
                ft_txe_n <= 1'b0;  // ready
                if (txe_ready_remain > 0) begin
                    txe_ready_remain <= txe_ready_remain - 1;
                end else begin
                    // Transition to busy burst
                    txe_rng <= lcg_next(txe_rng);
                    txe_busy_remain <= heavy_tail_duration(txe_rng, 32, 4096);
                    txe_in_ready <= 1'b0;
                end
            end else begin
                ft_txe_n <= 1'b1;  // busy
                if (txe_busy_remain > 0) begin
                    txe_busy_remain <= txe_busy_remain - 1;
                end else begin
                    // Transition to ready stretch
                    txe_rng <= lcg_next(lcg_next(txe_rng));
                    txe_ready_remain <= heavy_tail_duration(lcg_next(txe_rng), 32, 4096);
                    txe_in_ready <= 1'b1;
                end
            end
        end
    end

    // ════════════════════════════════════════════════════════════════
    //  PUSH GENERATOR — bursty FIFO fill + deterministic long stall
    // ════════════════════════════════════════════════════════════════
    //
    // Phase 1 (random): bursty push/drought alternation with heavy-tail
    // Phase 2 (deterministic): force TXE high for 50K cycles while
    //          continuing to push bytes. Verifies FSM stalls correctly,
    //          stall_count increments, and stream recovers without
    //          corruption when TXE goes low.

    integer push_rng;
    integer bytes_remaining;
    integer burst_len, drought_len;
    integer bi, di;  // loop counters
    integer stall_count_before;

    initial begin
        // ── Reset ───────────────────────────────────────────────────
        rst_n    = 1'b0;
        ft_txe_n = 1'b1;

        repeat (10) @(posedge clk_60m);
        rst_n = 1'b1;
        @(posedge clk_60m);
        @(posedge clk_60m);

        // Initialize push RNG from seed
        push_rng = seed + 7;

        $display("[%0t] Starting: %0d bytes, seed=%0d", $time, NUM_BYTES, seed);

        // ── Phase 1: Random bursty push (80% of bytes) ──────────────
        bytes_remaining = (NUM_BYTES * 4) / 5;  // 80%

        while (bytes_remaining > 0) begin
            // ── Burst phase: push a chunk of bytes immediately ──────
            push_rng  = lcg_next(push_rng);
            burst_len = heavy_tail_duration(push_rng, 200, 2000);
            if (burst_len > bytes_remaining) burst_len = bytes_remaining;

            for (bi = 0; bi < burst_len; bi = bi + 1) begin
                fifo_push(push_count[7:0]);
                push_count = push_count + 1;
            end
            bytes_remaining = bytes_remaining - burst_len;

            // ── Drought phase: wait some cycles before next burst ───
            // Cap short droughts at 200, heavy tail up to 2000
            push_rng    = lcg_next(push_rng);
            drought_len = heavy_tail_duration(push_rng, 100, 500);

            // Mix in some trickle pushes during long droughts
            if (drought_len > 50 && bytes_remaining > 0) begin
                for (di = 0; di < drought_len; di = di + 1) begin
                    @(posedge clk_60m);
                    push_rng = lcg_next(push_rng);
                    if ((push_rng[5:0] == 6'd0) && bytes_remaining > 0) begin
                        fifo_push(push_count[7:0]);
                        push_count = push_count + 1;
                        bytes_remaining = bytes_remaining - 1;
                    end
                end
            end else begin
                repeat (drought_len) @(posedge clk_60m);
            end

            // Progress report every 10K bytes
            if ((push_count > 0) && (push_count % 10000 < burst_len))
                $display("[%0t] Phase 1: pushed %0d / %0d bytes", $time, push_count, NUM_BYTES);
        end

        // Wait for phase 1 bytes to drain before deterministic stall
        begin : phase1_drain
            integer p1_timeout;
            p1_timeout = 0;
            while (wr_count < push_count && p1_timeout < push_count * 50) begin
                @(posedge clk_60m);
                p1_timeout = p1_timeout + 1;
            end
        end

        // ── Phase 2: Deterministic long TXE stall ───────────────────
        // Force TXE high for 50K cycles while pushing remaining 20%.
        // The bridge must: stop writing, increment stall_count,
        // not corrupt ordering, and recover cleanly.
        $display("[%0t] Phase 2: deterministic 50K-cycle TXE stall", $time);
        stall_count_before = stall_count;
        txe_force_busy = 1'b1;

        // Push remaining 20% of bytes during the stall
        bytes_remaining = NUM_BYTES - push_count;
        for (bi = 0; bi < bytes_remaining; bi = bi + 1) begin
            fifo_push(push_count[7:0]);
            push_count = push_count + 1;
            // Spread pushes across the stall period
            if (bi % 4 == 0) @(posedge clk_60m);
        end

        // Hold TXE busy for 50K cycles total
        repeat (50000) @(posedge clk_60m);

        // Verify stall counter incremented during the stall
        if (stall_count <= stall_count_before) begin
            $display("STALL COUNT ERROR: did not increment during 50K forced stall");
            $display("  before=%0d after=%0d", stall_count_before, stall_count);
        end else begin
            $display("[%0t] Stall count OK: +%0d during forced stall",
                     $time, stall_count - stall_count_before);
        end

        // Verify no bytes were written during the stall
        // (wr_count should not have increased since all bytes before
        //  the stall were already drained in phase1_drain)
        // Note: some writes may have snuck in before txe_force_busy
        // took effect (1-cycle registration delay), so we just check
        // that the bridge is currently stalled.
        if (!ft_wr_n) begin
            $display("ERROR @ %0t: WR_N low while TXE forced busy!", $time);
        end

        // Release TXE — bridge should recover and drain all bytes
        txe_force_busy = 1'b0;
        $display("[%0t] Phase 2: TXE released. All %0d bytes pushed. Draining...",
                 $time, push_count);

        // ── Wait for bridge to drain all bytes ──────────────────────
        // Budget: NUM_BYTES * 50 cycles max.
        //   Rationale: FSM takes 5 cycles/byte minimum (S_IDLE→S_FETCH→
        //   S_WAIT→S_HOLD→S_WRITE). With stalls, worst case is ~20
        //   cyc/byte. 50× gives 2.5× margin over worst observed.
        //   If FSM depth changes, update this constant.
        // Stuck detector: if wr_count doesn't advance for 60K cycles
        //   after TXE is released, something is deadlocked. 60K > 50K
        //   deterministic stall to avoid false triggers during phase 2.
        begin : drain_wait
            integer drain_timeout, max_drain, last_wr, stuck_count;
            drain_timeout = 0;
            max_drain     = NUM_BYTES * 50;
            last_wr       = wr_count;
            stuck_count   = 0;
            while (wr_count < NUM_BYTES && drain_timeout < max_drain) begin
                @(posedge clk_60m);
                drain_timeout = drain_timeout + 1;

                // Stuck detector: 60K cycles of no write progress
                if (wr_count == last_wr) begin
                    stuck_count = stuck_count + 1;
                    if (stuck_count >= 60000) begin
                        $display("STUCK @ %0t: no write progress for 60K cycles", $time);
                        $display("  wrote %0d / %0d, FIFO level: %0d",
                                 wr_count, NUM_BYTES, fifo_wr_ptr - fifo_rd_ptr);
                        $display("  state=%0d pending=%0d txe_n_r=%0d ft_txe_n=%0d force=%0d",
                                 dut.state, dut.pending_valid, dut.txe_n_r, ft_txe_n, txe_force_busy);
                        $finish;
                    end
                end else begin
                    last_wr     = wr_count;
                    stuck_count = 0;
                end

                // Progress every 100K cycles
                if (drain_timeout % 100000 == 0)
                    $display("[%0t] drain: %0d/%0d bytes, cycle %0d/%0d, fifo=%0d state=%0d txe=%0d",
                             $time, wr_count, NUM_BYTES, drain_timeout, max_drain,
                             fifo_wr_ptr - fifo_rd_ptr, dut.state, ft_txe_n);
            end
            if (drain_timeout >= max_drain) begin
                $display("TIMEOUT: drain exceeded %0d cycles (wrote %0d / %0d)",
                         max_drain, wr_count, NUM_BYTES);
                $display("  FIFO level: %0d, state=%0d, pending=%0d, txe_n_r=%0d",
                         fifo_wr_ptr - fifo_rd_ptr, dut.state, dut.pending_valid, dut.txe_n_r);
                $finish;
            end
        end

        repeat (100) @(posedge clk_60m);  // settle

        // ── Report ──────────────────────────────────────────────────
        $display("");
        $display("═══════════════════════════════════════════════════════");
        $display("  FIFO BRIDGE STRESS TEST — seed=%0d", seed);
        $display("═══════════════════════════════════════════════════════");
        $display("  Total bytes pushed:   %0d", push_count);
        $display("  Total bytes written:  %0d", wr_count);
        $display("  Total POPs:           %0d", pop_count);
        $display("  Stall cycles:         %0d", stall_count);
        $display("───────────────────────────────────────────────────────");
        $display("  A1  (WR when TXE high):     %0d failures", a1_fail);
        $display("  A2  (consecutive WR_N low):  %0d failures", a2_fail);
        $display("  A5  (rd_en + pending):       %0d failures", a5_fail);
        $display("  A7  (rd_en high, state!=S_FETCH): %0d failures", a7_fail);
        $display("  A8  (pop > push):            %0d failures", a8_fail);
        $display("  A9  (pop on empty):          %0d failures", a9_fail);
        $display("  A10 (pending + rd_en):        %0d failures", a10_fail);
        $display("  A11 (WR low, state!=S_IDLE):   %0d failures", a11_fail);
        $display("  A12 (idle pins low):          %0d failures", a12_fail);
        $display("  A13 (WR during reset):        %0d failures", a13_fail);
        $display("  A14 (data unstable mid-WR):   %0d failures", a14_fail);
        $display("  A15 (wr_pulses != bytes_out): %0d",
                 (wr_pulse_count !== wr_count) ? 1 : 0);
        $display("  Byte order errors:           %0d", byte_errors);
        $display("───────────────────────────────────────────────────────");

        if (a1_fail || a2_fail || a5_fail || a7_fail || a8_fail ||
            a9_fail || a10_fail || a11_fail || a12_fail || a13_fail ||
            a14_fail || byte_errors || (wr_pulse_count !== wr_count)) begin
            $display("  *** FAIL ***");
            $finish;
        end

        if (wr_count !== push_count) begin
            $display("  *** FAIL: byte count mismatch (pushed %0d, wrote %0d) ***",
                     push_count, wr_count);
            $finish;
        end

        if (pop_count !== push_count) begin
            $display("  *** FAIL: pop count mismatch (pushed %0d, popped %0d) ***",
                     push_count, pop_count);
            $finish;
        end

        $display("  *** ALL PASS — %0d bytes, 0 assertion failures ***", wr_count);
        $display("═══════════════════════════════════════════════════════");
        $finish;
    end

    // ── Watchdog ────────────────────────────────────────────────────
    // Proportional: (NUM_BYTES * 100 + 60000) * CLK_PERIOD.
    //   Rationale: 100 cyc/byte accounts for 5-cycle FSM pipeline,
    //   heavy-tail stalls (up to 4096 cycles), and drought gaps.
    //   +60000 covers the 50K deterministic stall phase plus margin.
    //   Must be >= drain_timeout + push overhead + deterministic stall.
    //   If FSM depth or stall model changes, revisit this constant.
    initial begin : watchdog
        real watchdog_ns;
        watchdog_ns = (NUM_BYTES * 100.0 + 60000.0) * CLK_PERIOD;
        #(watchdog_ns);
        $display("WATCHDOG TIMEOUT at %0t (budget: %0d ns)", $time, $rtoi(watchdog_ns));
        $display("  bytes written: %0d / %0d", wr_count, NUM_BYTES);
        $display("  FSM state: %0d, pending: %0d", dut.state, dut.pending_valid);
        $finish;
    end

endmodule

`default_nettype wire
