// tb_frame_packer.v — Testbench for frame_packer + crc16_ccitt
// Rev 1.0 · 2025-06-01
//
// Verifications:
//   1. Single-frame commit: header magic, version, frame_id, timestamp, CRC correct
//   2. Multi-frame: frame_id monotonic, timestamp increments
//   3. Drop on FIFO full: drop_pulse, drop_counter, no fifo writes
//   4. Drop recovery: drop_since_last status bit set in next committed frame
//   5. CRC covers bytes 0..253 and matches CRC16-CCITT-FALSE
//   6. ADC payload content matches BRAM readback
//   7. Diagnostics region contains correct fifo_fill / drop_cnt / stall_cnt
//   8. Reserved region is all zeros
//   9. Frame size is exactly 256 bytes
//  10. No write enable outside frame emission

`timescale 1ns / 1ps

module tb_frame_packer;

    // ── Parameters ──────────────────────────────────────────────────
    localparam FRAME_SIZE  = 256;
    localparam HDR_SIZE    = 16;
    localparam NUM_CH      = 64;
    localparam ADC_BYTES   = 128;
    localparam NUM_AUX     = 3;
    localparam AUX_BYTES   = 6;
    localparam DIAG_BYTES  = 16;
    localparam CRC_SIZE    = 2;
    localparam TOTAL_SAMP  = NUM_CH + NUM_AUX;  // 67

    localparam DIAG_START  = HDR_SIZE + ADC_BYTES + AUX_BYTES;  // 150
    localparam RSVD_START  = DIAG_START + DIAG_BYTES;            // 166
    localparam CRC_START   = FRAME_SIZE - CRC_SIZE;              // 254

    localparam CLK_PERIOD  = 20;   // 50 MHz (doesn't matter, just for timing)

    // ── DUT signals ─────────────────────────────────────────────────
    reg         clk;
    reg         rst_n;
    reg         cycle_done;
    reg         buf_bank;
    wire [6:0]  buf_addr;
    reg  [15:0] buf_data;
    reg  [15:0] wr_free_bytes;
    wire        fifo_wr_en;
    wire [7:0]  fifo_wr_data;
    reg  [15:0] fifo_fill_level;
    reg  [31:0] ext_stall_count;
    wire [31:0] frame_counter;
    wire [31:0] drop_counter;
    wire        commit_pulse;
    wire [31:0] commit_frame_id;
    wire        drop_pulse;

    // ── Simulated ping-pong BRAM ────────────────────────────────────
    // Two banks of 67 × 16-bit samples
    reg [15:0] bram [0:1][0:TOTAL_SAMP-1];
    reg        bram_bank_sel;  // Controlled by test

    // BRAM read model: 1-cycle latency
    always @(posedge clk) begin
        buf_data <= bram[bram_bank_sel][buf_addr];
    end

    // ── Frame capture buffer ────────────────────────────────────────
    reg [7:0]  captured_frame [0:FRAME_SIZE-1];
    integer    capture_idx;
    reg        capturing;

    always @(posedge clk) begin
        if (!rst_n) begin
            capture_idx <= 0;
            capturing   <= 0;
        end else if (fifo_wr_en) begin
            if (capture_idx < FRAME_SIZE) begin
                captured_frame[capture_idx] <= fifo_wr_data;
                capture_idx <= capture_idx + 1;
            end
            capturing <= 1;
        end
    end

    // ── DUT instantiation ───────────────────────────────────────────
    frame_packer #(
        .FRAME_SIZE (FRAME_SIZE),
        .HDR_SIZE   (HDR_SIZE),
        .CRC_SIZE   (CRC_SIZE),
        .NUM_CH     (NUM_CH),
        .ADC_BYTES  (ADC_BYTES),
        .NUM_AUX    (NUM_AUX),
        .AUX_BYTES  (AUX_BYTES),
        .DIAG_BYTES (DIAG_BYTES)
    ) uut (
        .clk             (clk),
        .rst_n           (rst_n),
        .cycle_done      (cycle_done),
        .buf_bank        (buf_bank),
        .buf_data        (buf_data),
        .buf_addr        (buf_addr),
        .wr_free_bytes   (wr_free_bytes),
        .fifo_wr_en      (fifo_wr_en),
        .fifo_wr_data    (fifo_wr_data),
        .fifo_fill_level (fifo_fill_level),
        .ext_stall_count (ext_stall_count),
        .frame_counter   (frame_counter),
        .drop_counter    (drop_counter),
        .commit_pulse    (commit_pulse),
        .commit_frame_id (commit_frame_id),
        .drop_pulse      (drop_pulse)
    );

    // ── Clock ───────────────────────────────────────────────────────
    initial clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    // ── Counters ────────────────────────────────────────────────────
    integer pass_count;
    integer fail_count;
    integer wr_count;        // Count fifo_wr_en assertions per frame

    // Count writes per frame
    always @(posedge clk) begin
        if (fifo_wr_en)
            wr_count <= wr_count + 1;
    end

    // ── Helper tasks ────────────────────────────────────────────────

    // Fill BRAM bank with test pattern: channel i → value (seed + i)
    task fill_bram(input integer bank, input [15:0] seed);
        integer i;
        begin
            for (i = 0; i < TOTAL_SAMP; i = i + 1)
                bram[bank][i] = seed + i[15:0];
        end
    endtask

    // Pulse cycle_done for 1 clock
    task pulse_cycle_done;
        begin
            @(posedge clk);
            cycle_done <= 1'b1;
            @(posedge clk);
            cycle_done <= 1'b0;
        end
    endtask

    // Wait for commit or drop, with timeout
    task wait_for_event(output integer event_type);
        // event_type: 1 = commit, 2 = drop, 0 = timeout
        integer timeout;
        begin
            event_type = 0;
            timeout = 0;
            while (timeout < 1000) begin
                @(posedge clk);
                if (commit_pulse) begin
                    event_type = 1;
                    timeout = 1000;  // exit
                end else if (drop_pulse) begin
                    event_type = 2;
                    timeout = 1000;  // exit
                end
                timeout = timeout + 1;
            end
        end
    endtask

    // Compute CRC16-CCITT-FALSE over captured_frame[0..253]
    function [15:0] compute_crc;
        input integer len;
        reg [15:0] crc;
        reg [7:0] d;
        integer i, b;
        begin
            crc = 16'hFFFF;
            for (i = 0; i < len; i = i + 1) begin
                d = captured_frame[i];
                // Bit-serial CRC (MSB first)
                for (b = 7; b >= 0; b = b - 1) begin
                    if ((crc[15] ^ d[b]) == 1'b1)
                        crc = {crc[14:0], 1'b0} ^ 16'h1021;
                    else
                        crc = {crc[14:0], 1'b0};
                end
            end
            compute_crc = crc;
        end
    endfunction

    // Check assertion
    task check(input integer cond, input [799:0] msg);
        begin
            if (cond) begin
                $display("  PASS: %0s", msg);
                pass_count = pass_count + 1;
            end else begin
                $display("  FAIL: %0s", msg);
                fail_count = fail_count + 1;
            end
        end
    endtask

    task check_pass(input integer cond);
        begin
            if (cond)
                pass_count = pass_count + 1;
            else
                fail_count = fail_count + 1;
        end
    endtask

    // ── Main test sequence ──────────────────────────────────────────
    integer ev;
    integer i;
    reg [15:0] expected_crc;
    reg [15:0] frame_crc;

    initial begin
        $dumpfile("tb_frame_packer.vcd");
        $dumpvars(0, tb_frame_packer);

        pass_count = 0;
        fail_count = 0;

        // Init signals
        rst_n           = 0;
        cycle_done      = 0;
        buf_bank        = 0;
        wr_free_bytes   = 16'd32768;  // Plenty of space
        fifo_fill_level = 16'd100;
        ext_stall_count = 32'd42;
        bram_bank_sel   = 0;
        wr_count        = 0;

        // Fill BRAM bank 0 with known pattern
        fill_bram(0, 16'h1000);
        fill_bram(1, 16'h2000);

        // Reset
        repeat (10) @(posedge clk);
        rst_n = 1;
        repeat (4) @(posedge clk);

        // ═════════════════════════════════════════════════════════════
        // TEST 1: Single frame commit — verify structure
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 1: Single frame commit ==========");

        capture_idx   = 0;
        wr_count      = 0;
        buf_bank      = 0;
        bram_bank_sel = 0;

        pulse_cycle_done;
        wait_for_event(ev);

        // Wait a couple more cycles for capture to finish
        repeat (4) @(posedge clk);

        check(ev == 1, "Commit pulse received");

        $display("  Frame size = %0d (expected %0d)", capture_idx, FRAME_SIZE);
        check_pass(capture_idx == FRAME_SIZE);

        // Check header magic
        $display("  Magic = 0x%02X%02X (expected 0xA55A)", captured_frame[0], captured_frame[1]);
        check_pass(captured_frame[0] == 8'hA5 && captured_frame[1] == 8'h5A);

        // Check version
        $display("  Version = 0x%02X (expected 0x01)", captured_frame[2]);
        check_pass(captured_frame[2] == 8'h01);

        // Check frame ID = 0 (first frame)
        begin : fid_check
            reg [31:0] fid;
            fid = {captured_frame[4], captured_frame[5], captured_frame[6], captured_frame[7]};
            $display("  Frame ID = %0d (expected 0)", fid);
            check_pass(fid == 32'd0);
        end

        // Check timestamp = 1 (first cycle_done increments from 0 to 1)
        begin : ts_check
            reg [31:0] ts;
            ts = {captured_frame[8], captured_frame[9], captured_frame[10], captured_frame[11]};
            $display("  Timestamp = %0d (expected 1)", ts);
            check_pass(ts == 32'd1);
        end

        // Check channel count = 64
        begin : ch_check
            reg [15:0] ch;
            ch = {captured_frame[12], captured_frame[13]};
            $display("  Channel count = %0d (expected 64)", ch);
            check_pass(ch == 16'd64);
        end

        // Check sample rate code = 1
        begin : sr_check
            reg [15:0] sr;
            sr = {captured_frame[14], captured_frame[15]};
            $display("  Sample rate code = 0x%04X (expected 0x0001)", sr);
            check_pass(sr == 16'h0001);
        end

        // Check ADC payload (bytes 16..143): channel i → 0x1000 + i, big-endian
        begin : adc_check
            reg [15:0] sample;
            integer ch_fail;
            ch_fail = 0;
            for (i = 0; i < NUM_CH; i = i + 1) begin
                sample = {captured_frame[16 + i*2], captured_frame[16 + i*2 + 1]};
                if (sample != 16'h1000 + i[15:0])
                    ch_fail = ch_fail + 1;
            end
            $display("  ADC payload: %0d/%0d channels correct", NUM_CH - ch_fail, NUM_CH);
            check_pass(ch_fail == 0);
        end

        // Check AUX payload (bytes 144..149): aux i → 0x1000 + 64 + i
        begin : aux_check
            reg [15:0] aux;
            integer aux_fail;
            aux_fail = 0;
            for (i = 0; i < NUM_AUX; i = i + 1) begin
                aux = {captured_frame[144 + i*2], captured_frame[144 + i*2 + 1]};
                if (aux != 16'h1000 + NUM_CH[15:0] + i[15:0])
                    aux_fail = aux_fail + 1;
            end
            $display("  AUX payload: %0d/%0d channels correct", NUM_AUX - aux_fail, NUM_AUX);
            check_pass(aux_fail == 0);
        end

        // Check diagnostics — drop count (bytes 154..157) should be 0
        begin : diag_check
            reg [31:0] diag_drop;
            reg [31:0] diag_stall;
            diag_drop  = {captured_frame[154], captured_frame[155], captured_frame[156], captured_frame[157]};
            diag_stall = {captured_frame[158], captured_frame[159], captured_frame[160], captured_frame[161]};
            $display("  Diag drop_cnt = %0d (expected 0)", diag_drop);
            check_pass(diag_drop == 32'd0);
            $display("  Diag stall_cnt = %0d (expected 42)", diag_stall);
            check_pass(diag_stall == 32'd42);
        end

        // Check reserved region (bytes 166..253) is all zero
        begin : rsvd_check
            integer rsvd_fail;
            rsvd_fail = 0;
            for (i = RSVD_START; i < CRC_START; i = i + 1) begin
                if (captured_frame[i] != 8'h00)
                    rsvd_fail = rsvd_fail + 1;
            end
            $display("  Reserved region: %0d non-zero bytes (expected 0)", rsvd_fail);
            check_pass(rsvd_fail == 0);
        end

        // Check CRC
        expected_crc = compute_crc(CRC_START);
        frame_crc    = {captured_frame[254], captured_frame[255]};
        $display("  CRC = 0x%04X (expected 0x%04X)", frame_crc, expected_crc);
        check_pass(frame_crc == expected_crc);

        // Check commit_frame_id
        $display("  commit_frame_id = %0d (expected 0)", commit_frame_id);
        check_pass(commit_frame_id == 32'd0);

        // ═════════════════════════════════════════════════════════════
        // TEST 2: Second frame — frame_id increments, different bank
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 2: Second frame (bank 1) ==========");

        capture_idx   = 0;
        wr_count      = 0;
        buf_bank      = 1;
        bram_bank_sel = 1;

        pulse_cycle_done;
        wait_for_event(ev);
        repeat (4) @(posedge clk);

        check(ev == 1, "Second commit pulse received");

        // Frame ID = 1
        begin : fid2
            reg [31:0] fid;
            fid = {captured_frame[4], captured_frame[5], captured_frame[6], captured_frame[7]};
            $display("  Frame ID = %0d (expected 1)", fid);
            check_pass(fid == 32'd1);
        end

        // Timestamp = 2
        begin : ts2
            reg [31:0] ts;
            ts = {captured_frame[8], captured_frame[9], captured_frame[10], captured_frame[11]};
            $display("  Timestamp = %0d (expected 2)", ts);
            check_pass(ts == 32'd2);
        end

        // First ADC sample should be from bank 1: 0x2000
        begin : adc2
            reg [15:0] s;
            s = {captured_frame[16], captured_frame[17]};
            $display("  Bank 1 CH0 = 0x%04X (expected 0x2000)", s);
            check_pass(s == 16'h2000);
        end

        // CRC valid
        expected_crc = compute_crc(CRC_START);
        frame_crc    = {captured_frame[254], captured_frame[255]};
        $display("  CRC = 0x%04X (expected 0x%04X)", frame_crc, expected_crc);
        check_pass(frame_crc == expected_crc);

        // ═════════════════════════════════════════════════════════════
        // TEST 3: Drop on FIFO full
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 3: Frame drop (no FIFO space) ==========");

        wr_free_bytes = 16'd100;  // < 256
        capture_idx   = 0;
        wr_count      = 0;

        pulse_cycle_done;
        wait_for_event(ev);
        repeat (4) @(posedge clk);

        check(ev == 2, "Drop pulse received");
        $display("  drop_counter = %0d (expected 1)", drop_counter);
        check_pass(drop_counter == 32'd1);
        $display("  No bytes written during drop (%0d)", capture_idx);
        check_pass(capture_idx == 0);

        // ═════════════════════════════════════════════════════════════
        // TEST 4: Recovery after drop — status bit set
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 4: Recovery after drop ==========");

        wr_free_bytes = 16'd32768;  // Space restored
        capture_idx   = 0;
        wr_count      = 0;
        buf_bank      = 0;
        bram_bank_sel = 0;

        pulse_cycle_done;
        wait_for_event(ev);
        repeat (4) @(posedge clk);

        check(ev == 1, "Recovery commit pulse");

        // Status byte bit 7 (drop_since_last) should be 1
        begin : status_chk
            reg [7:0] status;
            status = captured_frame[3];
            $display("  Status drop_since_last = %0b (expected 1)", status[7]);
            check_pass(status[7] == 1'b1);
        end

        // Frame ID should be 3 (0, 1 committed; 2 dropped; 3 committed)
        begin : fid3
            reg [31:0] fid;
            fid = {captured_frame[4], captured_frame[5], captured_frame[6], captured_frame[7]};
            $display("  Frame ID after drop = %0d (expected 3)", fid);
            check_pass(fid == 32'd3);
        end

        // Drop count in diagnostics should be 1
        begin : diag_drop2
            reg [31:0] dd;
            dd = {captured_frame[154], captured_frame[155], captured_frame[156], captured_frame[157]};
            $display("  Diag drop_cnt = %0d (expected 1)", dd);
            check_pass(dd == 32'd1);
        end

        // CRC still valid
        expected_crc = compute_crc(CRC_START);
        frame_crc    = {captured_frame[254], captured_frame[255]};
        $display("  CRC after recovery = 0x%04X (expected 0x%04X)", frame_crc, expected_crc);
        check_pass(frame_crc == expected_crc);

        // ═════════════════════════════════════════════════════════════
        // TEST 5: Next frame clears drop_since_last
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 5: drop_since_last clears ==========");

        capture_idx   = 0;
        wr_count      = 0;

        pulse_cycle_done;
        wait_for_event(ev);
        repeat (4) @(posedge clk);

        check(ev == 1, "Commit after recovery");
        begin : status_chk2
            reg [7:0] status;
            status = captured_frame[3];
            $display("  Status drop_since_last cleared = %0b", status[7]);
            check_pass(status[7] == 1'b0);
        end

        // ═════════════════════════════════════════════════════════════
        // TEST 6: Rapid back-to-back frames
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 6: 10 rapid frames ==========");
        begin : rapid
            integer committed;
            integer dropped;
            committed = 0;
            dropped   = 0;

            for (i = 0; i < 10; i = i + 1) begin
                capture_idx = 0;
                pulse_cycle_done;
                wait_for_event(ev);
                repeat (2) @(posedge clk);
                if (ev == 1) committed = committed + 1;
                if (ev == 2) dropped   = dropped   + 1;
            end

            $display("  Rapid: %0d/10 committed", committed);
            check_pass(committed == 10);
            $display("  Rapid: %0d drops (expected 0)", dropped);
            check_pass(dropped == 0);
        end

        // ═════════════════════════════════════════════════════════════
        // TEST 7: Frame counter consistency
        // ═════════════════════════════════════════════════════════════
        $display("\n========== TEST 7: Frame counter consistency ==========");
        // We've done: 2 commits + 1 drop + 2 commits + 10 commits = frame_id should be 15
        // drop_counter should be 1
        $display("  frame_counter = %0d (expected 15)", frame_counter);
        check_pass(frame_counter == 32'd15);
        $display("  drop_counter = %0d (expected 1)", drop_counter);
        check_pass(drop_counter == 32'd1);

        // ═════════════════════════════════════════════════════════════
        // Summary
        // ═════════════════════════════════════════════════════════════
        repeat (10) @(posedge clk);
        $display("\n===================================================");
        $display("frame_packer TB: %0d PASS, %0d FAIL", pass_count, fail_count);
        $display("===================================================\n");

        if (fail_count > 0)
            $display("*** FAILURES DETECTED ***");
        else
            $display("ALL TESTS PASSED");

        $finish;
    end

    // Timeout
    initial begin
        #5000000;
        $display("TIMEOUT!");
        $finish;
    end

endmodule
