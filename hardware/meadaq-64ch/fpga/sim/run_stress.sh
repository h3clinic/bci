#!/usr/bin/env zsh
# run_stress.sh — Multi-seed stress runner for fifo_bridge testbench
# Usage: ./run_stress.sh [num_bytes]
#
# Runs 1 fixed seed (42) + 10 random seeds.
# Each run pushes NUM_BYTES through the bridge with bursty stalls.

set -euo pipefail

cd "$(dirname "$0")"

NUM_BYTES=${1:-100000}
SEEDS=(42 12345 99999 7 65536 314159 271828 1 999999999 55555 8675309 \
       100 200 300 400 500 600 700 800 900 \
       1000 2000 3000 4000 5000 6000 7000 8000 9000 10000 \
       11111 22222 33333 44444 66666 77777 88888 \
       123456789 987654321 111111111 222222222 333333333 \
       444444444 555555555 666666666 777777777 888888888 \
       13 17 31)

echo "═══════════════════════════════════════════════════════"
echo "  fifo_bridge stress suite"
echo "  ${#SEEDS[@]} seeds × ${NUM_BYTES} bytes = $((${#SEEDS[@]} * NUM_BYTES)) total bytes"
echo "═══════════════════════════════════════════════════════"
echo ""

# Compile once (no VCD for stress runs — too large)
echo "Compiling..."
iverilog -g2012 \
    -DNUM_BYTES=${NUM_BYTES} \
    -o tb_fifo_bridge_stress \
    tb_fifo_bridge.v ../rtl/fifo_bridge.v

if [[ $? -ne 0 ]]; then
    echo "COMPILE FAILED"
    exit 1
fi
echo "Compile OK"
echo ""

PASS=0
FAIL=0

for s in "${SEEDS[@]}"; do
    echo "── Seed ${s} ──────────────────────────────────────────"
    # Run without VCD (performance). Capture output.
    OUTPUT=$(vvp tb_fifo_bridge_stress +SEED=${s} +NO_VCD=1 2>&1)
    echo "$OUTPUT" | tail -20

    if echo "$OUTPUT" | grep -q "ALL PASS"; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "*** SEED ${s} FAILED ***"
    fi
    echo ""
done

echo "═══════════════════════════════════════════════════════"
echo "  SUITE RESULTS: ${PASS} passed, ${FAIL} failed out of ${#SEEDS[@]} seeds"
echo "═══════════════════════════════════════════════════════"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
