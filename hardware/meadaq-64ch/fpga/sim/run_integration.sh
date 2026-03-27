#!/bin/bash
# run_integration.sh — Compile and run the integration TB
# Usage: ./run_integration.sh
#
# Tests: frame_packer_stub → async_fifo_beh → fifo_bridge (real RTL)
# with 3 checkers and deterministic TXE stall schedule.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RTL_DIR="$SCRIPT_DIR/../rtl"
SIM_DIR="$SCRIPT_DIR"

echo "=== Integration TB: compile ==="
iverilog -g2012 \
    -o "$SIM_DIR/tb_stream_top" \
    -I "$RTL_DIR" \
    "$RTL_DIR/async_fifo.v" \
    "$SIM_DIR/frame_packer_stub.v" \
    "$RTL_DIR/fifo_bridge.v" \
    "$SIM_DIR/tb_stream_top.v"

echo "=== Integration TB: run ==="
"$SIM_DIR/tb_stream_top" | tee "$SIM_DIR/tb_stream_top.log"

echo ""
echo "=== Done ==="
