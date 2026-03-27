#!/usr/bin/env bash
# assert_repo_integrity.sh — fail-fast if any deliverable file is missing.
# Run this BEFORE pytest in every regression cycle.
set -euo pipefail
cd "$(dirname "$0")/.."

req=(
  # Phantom experiment protocol + trial generation
  "experiment/phantom_protocol_v1.md"
  "experiment/generate_trial_order.py"
  "experiment/session_log.csv"
  "experiment/trial_orders.txt"

  # Analysis pipeline
  "analysis/rhd_loader.py"
  "analysis/session_analyzer.py"
  "analysis/test_session_analyzer.py"
  "analysis/data_loader.py"
  "analysis/neural_metrics.py"
  "analysis/test_neural_metrics.py"
  "analysis/signal_gen.py"
  "analysis/fig1_noise_stability.py"
  "analysis/fig2_crosstalk.py"
  "analysis/fig3_detection.py"
  "analysis/make_all_figures.py"

  # 64ch digital pipeline
  "hardware/meadaq-64ch/fpga/rtl/crc16_ccitt.v"
  "hardware/meadaq-64ch/fpga/rtl/frame_packer.v"
  "hardware/meadaq-64ch/fpga/rtl/fifo_bridge.v"
  "hardware/meadaq-64ch/fpga/rtl/async_fifo.v"
  "hardware/meadaq-64ch/host/frame_parser.py"
  "hardware/meadaq-64ch/host/test_frame_parser.py"
  "hardware/meadaq-64ch/frame_format.md"
)

fail=0
for f in "${req[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "MISSING: $f"
    fail=1
  fi
done

if [[ $fail -ne 0 ]]; then
  echo ""
  echo "REPO INTEGRITY FAILED — deliverable files missing from disk."
  echo "Check git status, .gitignore, and file creation logs."
  exit 1
fi

echo "Repo integrity OK (${#req[@]} files verified)"
