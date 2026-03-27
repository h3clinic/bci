#!/usr/bin/env python3
"""Validate a completed bring-up results CSV.

Usage:
    python validate_bringup.py bringup_results/lot001.csv

Policy: No assembly shall proceed until this script exits 0 on the
bare-board phases (1-4).  No functional test until phases 5-6 pass.

Exit codes:
    0 — all rows filled, all results within bounds
    1 — validation errors found (printed to stdout)
    2 — file not found or parse error

Measurement methods (MUST be recorded in CSV 'method' column):
    Continuity (step 5.1):
        method=4W-Kelvin  4-wire Kelvin measurement, probe on J5 pad + U1 pad
        method=2W-probe   2-wire DMM, subtract probe resistance (0.3-0.5 Ω)
        Probe points: J5 SMD pad (top, connector not mated) → U1 QFN pad
        Do NOT probe through mated Omnetics connector (adds ~0.5 Ω contact R)
    Isolation (step 5.2):
        method=100V-megger  Insulation resistance tester at 100V DC
        method=IR-DMM       DMM insulation resistance mode (voltage varies)
        Measure: each channel pad to nearest GND pad on J5 side
        1-minute soak time minimum before reading
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

# ── Acceptance bounds ────────────────────────────────────────────────────

# Phase 5 step 5.1: continuity (Ω)
# Expected: 0.15mm trace (~50mm typical length) + 2× via (0.2mm drill)
# + pad contact ≈ 0.5-2.5 Ω.  With 2-wire probe overhead: up to 3.5 Ω.
# 4-wire Kelvin: up to 2.5 Ω.  Threshold is 2-wire safe.
MAX_CONTINUITY_OHM = 3.5
MIN_CONTINUITY_OHM = 0.05  # below this = probe short or measurement error

# Phase 5 step 5.2: isolation (MΩ) at 100V DC
# Channel to GND and channel to adjacent channel.
# Clean FR4 at room temp: >100 MΩ typical.
# Threshold set conservatively; <50 MΩ suggests contamination or defect.
MIN_ISOLATION_MOHM = 50.0
ISOLATION_TEST_VOLTAGE = "100V DC"

# Channels that must have per-channel results
REQUIRED_CHANNELS = [f"CH{n}" for n in range(9, 23)]  # CH9..CH22

# ── Evidence requirements ────────────────────────────────────────────────

EVIDENCE_DIR_NAME = "evidence"
REQUIRED_EVIDENCE_PATTERNS = [
    # At least one microsection image for J5 VIP
    "microsection_J5",
]


def validate(csv_path: Path) -> list[str]:
    """Return list of error strings.  Empty list = pass."""
    errors: list[str] = []
    results_dir = csv_path.parent

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return ["CSV file is empty or has no data rows."]

    # ── Check header required fields ─────────────────────────────────
    required_cols = {"vendor", "order_id", "lot_id", "panel_id",
                     "board_serial", "inspector", "date",
                     "phase", "step", "channel", "result", "value", "unit",
                     "method", "probe_from", "probe_to"}
    actual_cols = set(rows[0].keys())
    missing_cols = required_cols - actual_cols
    if missing_cols:
        errors.append(f"Missing columns: {missing_cols}")
        return errors  # can't continue without structure

    # ── Check lot/inspector/vendor metadata on at least one row ──────
    meta_filled = any(
        row["lot_id"].strip() and row["inspector"].strip() and row["date"].strip()
        for row in rows
    )
    if not meta_filled:
        errors.append("No row has lot_id, inspector, AND date filled in.")

    vendor_filled = any(row.get("vendor", "").strip() for row in rows)
    if not vendor_filled:
        errors.append("No row has 'vendor' filled in — traceability requires vendor name.")

    order_filled = any(row.get("order_id", "").strip() for row in rows)
    if not order_filled:
        errors.append("No row has 'order_id' filled in — traceability requires order ID.")

    # ── Check lot_id / order_id consistency ──────────────────────────
    # All non-blank lot_id values should be the same (single-lot run)
    lot_ids = {row["lot_id"].strip() for row in rows if row.get("lot_id", "").strip()}
    if len(lot_ids) > 1:
        errors.append(
            f"Multiple lot_ids found: {sorted(lot_ids)}.\n"
            f"  Single-lot expected. If multi-lot is intentional, note in 'notes' column."
        )
    order_ids = {row["order_id"].strip() for row in rows if row.get("order_id", "").strip()}
    if len(order_ids) > 1:
        errors.append(
            f"Multiple order_ids found: {sorted(order_ids)}.\n"
            f"  Single-order expected. If multi-order is intentional, note in 'notes' column."
        )

    # ── Check every row has a result ─────────────────────────────────
    blank_results = []
    for i, row in enumerate(rows, start=2):  # row 2 = first data row (header is 1)
        result = row.get("result", "").strip()
        if not result:
            step = row.get("step", "?")
            ch = row.get("channel", "")
            label = f"phase {row.get('phase','?')} step {step}"
            if ch:
                label += f" {ch}"
            blank_results.append(f"  row {i}: {label}")

    if blank_results:
        errors.append(
            f"{len(blank_results)} row(s) have blank 'result' field:\n"
            + "\n".join(blank_results[:20])
            + ("\n  ... (truncated)" if len(blank_results) > 20 else "")
        )

    # ── Check PASS/FAIL values ───────────────────────────────────────
    for i, row in enumerate(rows, start=2):
        result = row.get("result", "").strip().upper()
        if result and result not in ("PASS", "FAIL", "N/A"):
            errors.append(
                f"  row {i}: result '{row['result']}' is not PASS/FAIL/N/A"
            )

    # ── Check any FAIL → error ───────────────────────────────────────
    fails = [
        (i, row) for i, row in enumerate(rows, start=2)
        if row.get("result", "").strip().upper() == "FAIL"
    ]
    if fails:
        for i, row in fails:
            step = row.get("step", "?")
            ch = row.get("channel", "")
            errors.append(
                f"  row {i}: FAIL on phase {row.get('phase','?')} step {step}"
                + (f" {ch}" if ch else "")
            )

    # ── Check VIPPO microsection row (Phase 3, step 3.2) ────────────
    microsection_rows = [
        row for row in rows
        if row.get("step", "").strip() == "3.2"
    ]
    if not microsection_rows:
        errors.append(
            "Missing Phase 3 step 3.2 row (VIPPO microsection reviewed).\n"
            "  First article requires destructive cross-section with result=PASS."
        )
    elif all(r.get("result", "").strip().upper() != "PASS" for r in microsection_rows):
        errors.append(
            "Phase 3 step 3.2 (VIPPO microsection) does not have result=PASS.\n"
            "  Assembly cannot proceed without microsection acceptance."
        )

    # ── Check per-channel continuity (Phase 5, step 5.1) ────────────
    VALID_METHODS_51 = {"4W-Kelvin", "2W-probe"}
    VALID_UNITS_51 = {"Ω", "ohm", "Ohm", "OHM"}
    continuity_rows = [
        (i, row) for i, row in enumerate(rows, start=2)
        if row.get("step", "").strip() == "5.1" and row.get("channel", "").strip()
    ]
    seen_channels_cont = set()
    for i, row in continuity_rows:
        ch = row["channel"].strip()
        seen_channels_cont.add(ch)
        # Enforce measurement method
        method = row.get("method", "").strip()
        if not method:
            errors.append(
                f"  row {i}: {ch} continuity (step 5.1) missing 'method' — "
                f"must be one of {VALID_METHODS_51}"
            )
        elif method not in VALID_METHODS_51:
            errors.append(
                f"  row {i}: {ch} method '{method}' not recognized — "
                f"must be one of {VALID_METHODS_51}"
            )
        # Enforce unit column — must be Ω/ohm
        unit = row.get("unit", "").strip()
        if not unit:
            errors.append(
                f"  row {i}: {ch} continuity (step 5.1) missing 'unit' — "
                f"must be one of {VALID_UNITS_51}"
            )
        elif unit not in VALID_UNITS_51:
            errors.append(
                f"  row {i}: {ch} unit '{unit}' not valid for continuity — "
                f"must be one of {VALID_UNITS_51}"
            )
        # Enforce probe points — must specify J5.pad → U1.pad
        probe_from = row.get("probe_from", "").strip()
        probe_to = row.get("probe_to", "").strip()
        if not probe_from or not probe_to:
            errors.append(
                f"  row {i}: {ch} continuity (step 5.1) missing probe_from/probe_to — "
                f"must specify J5.pad and U1.pad endpoints"
            )
        else:
            if not (probe_from.startswith("J5.") or probe_from.startswith("U1.")):
                errors.append(
                    f"  row {i}: {ch} probe_from '{probe_from}' must start with J5. or U1."
                )
            if not (probe_to.startswith("U1.") or probe_to.startswith("J5.")):
                errors.append(
                    f"  row {i}: {ch} probe_to '{probe_to}' must start with U1. or J5."
                )
        # Enforce numeric measurement value — not optional
        val = row.get("value", "").strip()
        if not val:
            errors.append(
                f"  row {i}: {ch} continuity (step 5.1) missing 'value' — "
                f"must record numeric resistance in Ω"
            )
        else:
            try:
                ohm = float(val)
                if ohm > MAX_CONTINUITY_OHM:
                    errors.append(
                        f"  row {i}: {ch} continuity {ohm} Ω > {MAX_CONTINUITY_OHM} Ω limit"
                    )
                elif ohm < MIN_CONTINUITY_OHM:
                    errors.append(
                        f"  row {i}: {ch} continuity {ohm} Ω suspiciously low "
                        f"(< {MIN_CONTINUITY_OHM} Ω — probe short?)"
                    )
            except ValueError:
                errors.append(f"  row {i}: {ch} continuity value '{val}' is not numeric")

    missing_ch_cont = set(REQUIRED_CHANNELS) - seen_channels_cont
    if missing_ch_cont:
        errors.append(
            f"Missing continuity (step 5.1) rows for channels: "
            + ", ".join(sorted(missing_ch_cont))
        )

    # ── Check per-channel isolation (Phase 5, step 5.2) ──────────────
    VALID_METHODS_52 = {"100V-megger", "IR-DMM"}
    VALID_UNITS_52 = {"MΩ", "Mohm", "MOhm", "MOHM", "GΩ", "Gohm"}
    isolation_rows = [
        (i, row) for i, row in enumerate(rows, start=2)
        if row.get("step", "").strip() == "5.2" and row.get("channel", "").strip()
    ]
    seen_channels_iso = set()
    for i, row in isolation_rows:
        ch = row["channel"].strip()
        seen_channels_iso.add(ch)
        # Enforce measurement method
        method = row.get("method", "").strip()
        if not method:
            errors.append(
                f"  row {i}: {ch} isolation (step 5.2) missing 'method' — "
                f"must be one of {VALID_METHODS_52}"
            )
        elif method not in VALID_METHODS_52:
            errors.append(
                f"  row {i}: {ch} method '{method}' not recognized — "
                f"must be one of {VALID_METHODS_52}"
            )
        # Enforce unit column — must be MΩ/GΩ
        unit = row.get("unit", "").strip()
        if not unit:
            errors.append(
                f"  row {i}: {ch} isolation (step 5.2) missing 'unit' — "
                f"must be one of {VALID_UNITS_52}"
            )
        elif unit not in VALID_UNITS_52:
            errors.append(
                f"  row {i}: {ch} unit '{unit}' not valid for isolation — "
                f"must be one of {VALID_UNITS_52}"
            )
        # Enforce probe points — must specify channel pad → GND
        probe_from = row.get("probe_from", "").strip()
        probe_to = row.get("probe_to", "").strip()
        if not probe_from or not probe_to:
            errors.append(
                f"  row {i}: {ch} isolation (step 5.2) missing probe_from/probe_to — "
                f"must specify channel pad and GND reference"
            )
        else:
            if not probe_from.startswith("J5."):
                errors.append(
                    f"  row {i}: {ch} isolation probe_from '{probe_from}' "
                    f"must start with J5. (channel pad)"
                )
            if "GND" not in probe_to.upper():
                errors.append(
                    f"  row {i}: {ch} isolation probe_to '{probe_to}' "
                    f"must reference GND (e.g. J5.GND)"
                )
        # Enforce numeric measurement value — not optional
        val = row.get("value", "").strip()
        if not val:
            errors.append(
                f"  row {i}: {ch} isolation (step 5.2) missing 'value' — "
                f"must record numeric insulation resistance in MΩ"
            )
        else:
            try:
                mohm = float(val)
                if mohm < MIN_ISOLATION_MOHM:
                    errors.append(
                        f"  row {i}: {ch} isolation {mohm} MΩ < {MIN_ISOLATION_MOHM} MΩ "
                        f"limit (at {ISOLATION_TEST_VOLTAGE})"
                    )
            except ValueError:
                errors.append(f"  row {i}: {ch} isolation value '{val}' is not numeric")

    missing_ch_iso = set(REQUIRED_CHANNELS) - seen_channels_iso
    if missing_ch_iso:
        errors.append(
            f"Missing isolation (step 5.2) rows for channels: "
            + ", ".join(sorted(missing_ch_iso))
        )

    # ── Channel completeness: each CH must have BOTH continuity AND isolation ─
    for ch in REQUIRED_CHANNELS:
        has_cont = ch in seen_channels_cont
        has_iso = ch in seen_channels_iso
        if has_cont and not has_iso:
            errors.append(
                f"{ch}: has continuity (5.1) but MISSING isolation (5.2) — "
                f"both measurements required per channel"
            )
        elif has_iso and not has_cont:
            errors.append(
                f"{ch}: has isolation (5.2) but MISSING continuity (5.1) — "
                f"both measurements required per channel"
            )

    # ── Check microsection evidence file reference (Phase 3, step 3.2) ───
    for i, row in enumerate(rows, start=2):
        if row.get("step", "").strip() == "3.2":
            notes = row.get("notes", "").strip()
            # microsection rows should reference an evidence file or contain
            # a filename-like string (e.g., microsection_J5_lot001.jpg)
            if not notes or not any(
                kw in notes.lower()
                for kw in ("microsection", ".jpg", ".png", ".pdf", "evidence")
            ):
                errors.append(
                    f"  row {i}: step 3.2 (microsection) 'notes' should reference "
                    f"evidence file (e.g. microsection_J5_<lot_id>.jpg)"
                )

    # ── Check first-article evidence files ───────────────────────────
    evidence_dir = results_dir / EVIDENCE_DIR_NAME
    if not evidence_dir.exists():
        errors.append(
            f"Evidence directory not found: {evidence_dir}\n"
            f"  First article requires microsection photo in {EVIDENCE_DIR_NAME}/"
        )
    else:
        evidence_files = [f.name for f in evidence_dir.iterdir() if f.is_file()]
        for pattern in REQUIRED_EVIDENCE_PATTERNS:
            if not any(pattern in fn for fn in evidence_files):
                errors.append(
                    f"Missing evidence file matching '{pattern}' in {EVIDENCE_DIR_NAME}/\n"
                    f"  e.g. microsection_J5_pin10.jpg\n"
                    f"  Found: {evidence_files[:10]}"
                )

        # Cross-reference: microsection file must include lot_id from CSV
        lot_ids = {row["lot_id"].strip() for row in rows if row.get("lot_id", "").strip()}
        if lot_ids and evidence_files:
            lot_in_filename = any(
                any(lid in fn for lid in lot_ids)
                for fn in evidence_files if "microsection" in fn
            )
            if not lot_in_filename:
                errors.append(
                    f"Microsection evidence filename must include lot_id "
                    f"(one of: {lot_ids}).\n"
                    f"  Expected: microsection_J5_<lot_id>_<board_serial>.jpg\n"
                    f"  Found: {[f for f in evidence_files if 'microsection' in f]}"
                )

    return errors


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <bringup_results.csv>")
        print("Validates a completed bring-up results CSV.")
        print("\nPolicy: no assembly until this script exits 0.")
        sys.exit(2)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(2)

    errors = validate(csv_path)
    if errors:
        print(f"BRING-UP VALIDATION FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(f"  ✗ {e}")
        print(f"\nPolicy: do not proceed to assembly until all errors are resolved.")
        sys.exit(1)
    else:
        print("✓ Bring-up validation PASSED — all rows filled, all results within bounds.")
        sys.exit(0)


if __name__ == "__main__":
    main()
