#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"

KICAD_PY="/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9"
KICAD_CLI="/opt/homebrew/bin/kicad-cli"
PY="$(cd "$ROOT/../.." && pwd)/.venv/bin/python3"

# Homebrew kicad-cli resolves ${KICAD9_SYMBOL_DIR} relative to its own path;
# default to the KiCad.app bundle on macOS, but allow env override for Linux/CI.
: "${KICAD9_SYMBOL_DIR:=/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols}"
export KICAD9_SYMBOL_DIR

# EEG escape_y clamp mode: "fix" for dev, "fail" for CI/release, "off" for guard testing.
# In CI ($CI=true), default to "fail" so design regressions are never silently fixed.
# Outside CI, default to "fix" for interactive dev.  Explicit override always wins.
if [ -z "${EEG_CLAMP_MODE:-}" ]; then
    if [ "${CI:-}" = "true" ]; then
        EEG_CLAMP_MODE="fail"
    else
        EEG_CLAMP_MODE="fix"
    fi
fi
export EEG_CLAMP_MODE

# Clamp policy: if EEG_CLAMP_POLICY=deny, gen_pcb.py will hard-fail when any
# clamp event occurs even in "fix" mode.  Use for release builds.
: "${EEG_CLAMP_POLICY:=allow}"
export EEG_CLAMP_POLICY

# ── Loud warning: CI + off is dangerous ──────────────────────────────────
if [ "${CI:-}" = "true" ] && [ "$EEG_CLAMP_MODE" = "off" ]; then
    echo "══════════════════════════════════════════════════════════════"
    echo "⚠  CI_OVERRIDE  EEG_CLAMP_MODE=off  (guards only, no gen-time clamp)"
    echo "   This disables gen-time safety checks.  Only use for proving guards."
    echo "══════════════════════════════════════════════════════════════"
fi

# ── Pipeline state tracking ──────────────────────────────────────────────
# These variables are updated as the pipeline progresses.  The trap handler
# prints a consolidated status line on EVERY exit — success or failure.
PIPELINE_STEP="init"
CLAMP_EVENTS="?"
GUARDS_PASS="?"
PIPELINE_RC=0

_pipeline_exit() {
    local rc=$?
    rm -rf "$TMP"
    # Always emit a machine-parseable status line, regardless of how we exited.
    echo "PIPELINE_STATUS step=${PIPELINE_STEP} CLAMP_EVENTS=${CLAMP_EVENTS} GUARDS_PASS=${GUARDS_PASS} EXIT=${rc}"
    exit $rc
}
trap '_pipeline_exit' EXIT

SCH="$ROOT/afe-headstage-real.kicad_sch"
PCB="$ROOT/afe-headstage-real.kicad_pcb"
NETXML="$TMP/netlist.xml"
FILLED="$TMP/filled.kicad_pcb"
DRC="$ROOT/drc-latest.rpt"
ERC="$ROOT/erc-latest.json"

echo "[1/7] gen_schematic_v3.py"
PIPELINE_STEP="gen_schematic"
cd "$ROOT"
"$PY" gen_schematic_v3.py

echo "[2/7] export netlist"
PIPELINE_STEP="export_netlist"
"$KICAD_CLI" sch export netlist --format kicadxml --output "$NETXML" "$SCH"

echo "[3/7] verify_schematic.py"
PIPELINE_STEP="verify_schematic"
"$PY" verify_schematic.py "$NETXML"

echo "[4/7] gen_pcb.py"
PIPELINE_STEP="gen_pcb"
# Capture output but allow failure to be handled gracefully.
# The CLAMP_SUMMARY line is always printed by gen_pcb.py (even on deny),
# so we can extract it regardless of exit code.
GEN_RC=0
GEN_OUT=$( "$PY" gen_pcb.py 2>&1 ) || GEN_RC=$?
echo "$GEN_OUT"
# Extract clamp event count from the always-printed CLAMP_SUMMARY line
CLAMP_EVENTS=$(echo "$GEN_OUT" | sed -n 's/.*clamp_events=\([0-9]*\).*/\1/p' | tail -1)
CLAMP_EVENTS="${CLAMP_EVENTS:-?}"
if [ "$GEN_RC" -ne 0 ]; then
    echo "FAILED: gen_pcb.py exited with code $GEN_RC"
    exit "$GEN_RC"
fi

# ── Proactive clearance sanity check ──────────────────────────────────
# Verify the gen_pcb.py output declares Default netclass clearance 0.15mm.
# pcbnew.SaveBoard() strips net_class blocks from filled PCBs, so we check
# the UNFILLED source PCB here, immediately after gen_pcb.py.
# Catches gen_pcb.py regressions before they propagate to DRC.
if ! grep -q '(clearance 0.15)' "$PCB"; then
    echo "FATAL: source PCB does not contain '(clearance 0.15)' in any netclass."
    echo "       Board Default clearance may have been changed in gen_pcb.py."
    echo "       Check gen_pcb.py get_net_class_assignments()."
    exit 1
fi

echo "[5/7] fill_zones.py"
PIPELINE_STEP="fill_zones"
"$KICAD_PY" fill_zones.py "$PCB" "$FILLED"

# Copy custom DRC rules so kicad-cli finds them next to the filled PCB
DRU="$ROOT/afe-headstage-real.kicad_dru"
if [ -f "$DRU" ]; then
    cp "$DRU" "$TMP/filled.kicad_dru"
fi

# Copy library tables so kicad-cli can resolve footprint/symbol libraries.
# Replace ${KIPRJMOD} with the real project root since the filled PCB
# lives in a temp directory.
FP_LIB="$ROOT/fp-lib-table"
if [ -f "$FP_LIB" ]; then
    sed "s|\${KIPRJMOD}|${ROOT}|g" "$FP_LIB" > "$TMP/fp-lib-table"
fi
SYM_LIB="$ROOT/sym-lib-table"
if [ -f "$SYM_LIB" ]; then
    sed "s|\${KIPRJMOD}|${ROOT}|g" "$SYM_LIB" > "$TMP/sym-lib-table"
fi

# Copy minimal DRC project file so kicad-cli picks up fp-lib-table.
# This file sets Default netclass clearance to 0.1mm (below board's own
# 0.15mm) so it never overrides the board's real constraints.
# See drc.kicad_pro for rationale — NEVER copy the real kicad_pro.
DRC_PRO="$ROOT/drc.kicad_pro"
if [ -f "$DRC_PRO" ]; then
    cp "$DRC_PRO" "$TMP/filled.kicad_pro"
fi

echo "[5.5/7] verify_pcb_guards.py"
PIPELINE_STEP="verify_guards"
if "$KICAD_PY" verify_pcb_guards.py "$FILLED"; then
    GUARDS_PASS=1
else
    GUARDS_PASS=0
fi
if [ "$GUARDS_PASS" -eq 0 ]; then
    echo "FAILED: guards did not pass"
    exit 1
fi

echo "[6/7] kicad-cli pcb drc"
PIPELINE_STEP="kicad_drc"
"$KICAD_CLI" pcb drc --output "$DRC" --exit-code-violations "$FILLED" || true

echo "[6.5/7] parse_drc.py (structured regression check)"
PIPELINE_STEP="parse_drc"
"$PY" parse_drc.py "$DRC"

echo "[7/7] ERC artifact"
PIPELINE_STEP="erc"
"$KICAD_CLI" sch erc --format json --severity-all --output "$ERC" "$SCH" || true

PIPELINE_STEP="complete"
