// crc16_ccitt.v — CRC16-CCITT byte-serial calculator
// Rev 1.0 · 2026-03-01 · MEA DAQ 64-Channel
//
// Polynomial: x^16 + x^12 + x^5 + 1  (0x1021)
// Init:       0xFFFF
// Input:      byte-at-a-time, MSB first
// No final XOR.
//
// Usage:
//   Assert `init` for 1 cycle to reset CRC to 0xFFFF.
//   Then assert `valid` with `data_in[7:0]` for each byte.
//   After all bytes, `crc_out[15:0]` holds the CRC.
//
// Latency: 0 cycles (combinational update, registered output).
// The CRC is available on the cycle AFTER the last `valid` assertion.

`default_nettype none

module crc16_ccitt (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        init,       // 1-cycle: reset CRC to 0xFFFF
    input  wire        valid,      // 1-cycle: process data_in byte
    input  wire [7:0]  data_in,    // Input byte (MSB first)
    output wire [15:0] crc_out     // Current CRC value
);

    reg [15:0] crc_reg;
    assign crc_out = crc_reg;

    // Byte-serial CRC16-CCITT: process 8 bits per clock.
    // Unrolled from the bit-serial recurrence:
    //   crc[i+1] = crc[i] XOR (bit << 15), then shift+XOR with poly.
    //
    // For efficiency, compute the next CRC from current CRC and 8 input
    // bits in one combinational step.

    function [15:0] crc_next;
        input [15:0] crc;
        input [7:0]  din;
        reg [15:0] c;
        reg [7:0]  d;
        begin
            c = crc;
            d = din;
            // Unrolled 8-bit CRC16-CCITT (poly 0x1021)
            // Generated from standard CRC XOR matrix for byte-at-a-time.
            crc_next[0]  = c[8]  ^ c[12] ^ d[0] ^ d[4];
            crc_next[1]  = c[9]  ^ c[13] ^ d[1] ^ d[5];
            crc_next[2]  = c[10] ^ c[14] ^ d[2] ^ d[6];
            crc_next[3]  = c[11] ^ c[15] ^ d[3] ^ d[7];
            crc_next[4]  = c[12] ^ d[4];
            crc_next[5]  = c[8]  ^ c[12] ^ c[13] ^ d[0] ^ d[4] ^ d[5];
            crc_next[6]  = c[9]  ^ c[13] ^ c[14] ^ d[1] ^ d[5] ^ d[6];
            crc_next[7]  = c[10] ^ c[14] ^ c[15] ^ d[2] ^ d[6] ^ d[7];
            crc_next[8]  = c[0]  ^ c[11] ^ c[15] ^ d[3] ^ d[7];
            crc_next[9]  = c[1]  ^ c[12] ^ d[4];
            crc_next[10] = c[2]  ^ c[13] ^ d[5];
            crc_next[11] = c[3]  ^ c[14] ^ d[6];
            crc_next[12] = c[4]  ^ c[8]  ^ c[12] ^ c[15] ^ d[0] ^ d[4] ^ d[7];
            crc_next[13] = c[5]  ^ c[9]  ^ c[13] ^ d[1] ^ d[5];
            crc_next[14] = c[6]  ^ c[10] ^ c[14] ^ d[2] ^ d[6];
            crc_next[15] = c[7]  ^ c[11] ^ c[15] ^ d[3] ^ d[7];
        end
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            crc_reg <= 16'hFFFF;
        else if (init)
            crc_reg <= 16'hFFFF;
        else if (valid)
            crc_reg <= crc_next(crc_reg, data_in);
    end

endmodule

`default_nettype wire
