#!/bin/bash
# run_fifo_unit.sh — Compile and run the async FIFO unit TB
# Tests: pointer wrap, reset skew, clock drift, concurrent R/W, overflow
#
# Uses ADDR_BITS=4 (DEPTH=16) for fast sim with thorough wrap testing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RTL_DIR="$SCRIPT_DIR/../rtl"
SIM_DIR="$SCRIPT_DIR"

echo "=== Async FIFO Unit TB: compile ==="
iverilog -g2012 \
    -o "$SIM_DIR/tb_async_fifo" \
    -I "$RTL_DIR" \
    "$RTL_DIR/async_fifo.v" \
    "$SIM_DIR/tb_async_fifo.v"

echo "=== Async FIFO Unit TB: run ==="
"$SIM_DIR/tb_async_fifo" | tee "$SIM_DIR/tb_async_fifo.log"

echo ""
echo "=== Done ==="
