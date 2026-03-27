#!/usr/bin/env python3
"""Pre-order preflight check — run before uploading to JLC/PCBWay.

Usage:
    python preflight.py              # check PCB source
    python preflight.py --package    # also run packager and check output

Takes < 30 seconds.  All checks are pass/fail with no ambiguity.
Exit code 0 = safe to order.  Non-zero = DO NOT ORDER.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

HW_DIR = Path(__file__).resolve().parent
PCB_PATH = HW_DIR / "afe-headstage-v1.kicad_pcb"
FAB_NOTES = HW_DIR / "fab_notes.txt"
FAB_PROFILE = HW_DIR / "fab_profile.yaml"
ASSEMBLY_CONSTRAINTS = HW_DIR / "assembly_constraints.txt"
VIP_CSV = HW_DIR / "vip_vias.csv"

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"

fail_count = 0
warn_count = 0


def check(condition: bool, label: str, *, warn_only: bool = False) -> bool:
    global fail_count, warn_count
    if condition:
        print(f"  {PASS}  {label}")
    elif warn_only:
        warn_count += 1
        print(f"  {WARN}  {label}")
    else:
        fail_count += 1
        print(f"  {FAIL}  {label}")
    return condition


def main() -> int:
    global fail_count, warn_count

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", action="store_true",
                        help="Also generate fab package and verify it")
    args = parser.parse_args()

    print("=" * 70)
    print(" AFE Headstage v1 — Pre-Order Preflight")
    print("=" * 70)

    # ── 1. File existence ────────────────────────────────────────────
    print("\n1. Required files")
    for f in [PCB_PATH, FAB_NOTES, FAB_PROFILE, ASSEMBLY_CONSTRAINTS]:
        check(f.exists(), f"{f.name} exists")

    # ── 2. PCB source checks ────────────────────────────────────────
    print("\n2. PCB source integrity")
    board_text = PCB_PATH.read_text()

    # 2a. J5 VIP pad mask openings
    j5_match = re.search(
        r'footprint "afe_footprints:Omnetics_A79024_36pin".*?'
        r'(?=\n    \(footprint|\n  \(embedded_fonts)',
        board_text, re.DOTALL,
    )
    if j5_match:
        j5_block = j5_match.group(0)
        vip_pads = re.findall(
            r'\(pad "\d+" smd.*?\(layers ([^)]+)\).*?\(net \d+ "(CH\d+)"\)',
            j5_block, re.DOTALL,
        )
        ch9_22 = [(layers, net) for layers, net in vip_pads
                   if net.startswith("CH") and 9 <= int(net[2:]) <= 22]
        all_mask_open = all("F.Mask" in layers for layers, _ in ch9_22)
        all_paste = all("F.Paste" in layers for layers, _ in ch9_22)
        check(len(ch9_22) == 14, f"J5 has 14 CH9–CH22 pads (found {len(ch9_22)})")
        check(all_mask_open, "All 14 J5 VIP pads have F.Mask (mask open)")
        check(all_paste, "All 14 J5 VIP pads have F.Paste (paste applied)")
    else:
        check(False, "J5 footprint found in PCB")

    # 2b. U1 thermal via tenting
    th_via_pat = re.compile(
        r'\(pad "EP" thru_hole circle\s+'
        r'\(at [^)]+\)\s+'
        r'\(size [^)]+\)\s+'
        r'\(drill ([\d.]+)\)\s+'
        r'\(layers "[^"]+"\)\s+'
        r'(?:\(remove_unused_layers[^)]*\)\s+)?'
        r'(?:\(tenting ([^)]*)\)\s+)?',
        re.DOTALL,
    )
    th_vias = th_via_pat.findall(board_text)
    th_03 = [(drill, tent) for drill, tent in th_vias if drill == "0.3"]
    check(len(th_03) == 9, f"U1 has 9 thermal vias (found {len(th_03)})")
    all_tented = all("front" in tent for _, tent in th_03)
    check(all_tented, "All U1 thermal vias tented on component side")

    # 2c. Drill tool count
    all_drills = re.findall(r'\(drill ([\d.]+)\)', board_text)
    unique_drills = sorted(set(all_drills))
    check(unique_drills == ["0.2", "0.3"],
          f"Exactly 2 drill sizes (got {unique_drills})")
    check(all_drills.count("0.2") == 28,
          f"28 × 0.20mm drills (got {all_drills.count('0.2')})")
    check(all_drills.count("0.3") == 9,
          f"9 × 0.30mm drills (got {all_drills.count('0.3')})")

    # 2d. Soldermask slivers at J5
    mask_exp_m = re.search(r'\(pad_to_mask_clearance\s+([\d.]+)\)', board_text)
    mask_exp = float(mask_exp_m.group(1)) if mask_exp_m else 0.0
    J5_PITCH, J5_PAD_W = 0.635, 0.381
    mask_web = J5_PITCH - (J5_PAD_W + 2 * mask_exp)
    check(mask_web >= 0.075,
          f"J5 inter-pad mask web = {mask_web:.3f}mm ≥ 0.075mm min")

    # ── 3. Fab notes content ────────────────────────────────────────
    print("\n3. Fab notes content gates")
    fab_text = FAB_NOTES.read_text()
    for kw in ["CLAUSE VIP-1", "CLAUSE SM-1", "CLAUSE SM-2",
               "CLAUSE SUB-1", "CLAUSE FA-1",
               "IPC-4761 Type VII", "vip_vias.csv",
               "do not fabricate"]:
        check(kw in fab_text, f"fab_notes.txt contains '{kw}'")

    # ── 4. Fab profile sanity ───────────────────────────────────────
    print("\n4. Fab profile consistency")
    fp_text = FAB_PROFILE.read_text()
    check("Standard" not in fp_text.split("name:")[1].split("\n")[0]
          if "name:" in fp_text else False,
          "Fab profile name does not say 'Standard'")
    check("VIPPO" in fp_text, "Fab profile mentions VIPPO")
    check("vendor_capability_gate" in fp_text,
          "Fab profile has vendor capability gate")

    # ── 5. Assembly constraints alignment ───────────────────────────
    print("\n5. Assembly constraints alignment")
    asm_text = ASSEMBLY_CONSTRAINTS.read_text()
    check("CLAUSE SM-2" in asm_text,
          "Assembly doc references CLAUSE SM-2 (U1 tenting)")
    check("5" in asm_text and "15" in asm_text and "bridg" in asm_text.lower(),
          "Assembly doc has paste reduction guidance for J5")

    # ── 6. VIP vias CSV ────────────────────────────────────────────
    print("\n6. VIP vias reference CSV")
    if VIP_CSV.exists():
        with open(VIP_CSV) as f:
            # Skip comment lines
            lines = [l for l in f if not l.startswith("#") and l.strip()]
        reader = csv.DictReader(lines)
        rows = list(reader)
        check(len(rows) == 14, f"vip_vias.csv has 14 entries (got {len(rows)})")
        nets = {r.get("Net", r.get("net", "")) for r in rows}
        expected_nets = {f"CH{i}" for i in range(9, 23)}
        check(nets == expected_nets,
              f"vip_vias.csv covers CH9–CH22 ({len(nets)} nets)")
    else:
        check(False, "vip_vias.csv exists")

    # ── 7. Optional: run packager and check output ──────────────────
    if args.package:
        print("\n7. Fab package generation")
        packager = HW_DIR / "make_fab_package.py"
        check(packager.exists(), "make_fab_package.py exists")
        if packager.exists():
            result = subprocess.run(
                [sys.executable, str(packager), "--skip-drc"],
                capture_output=True, text=True, cwd=str(HW_DIR), timeout=120,
            )
            check(result.returncode == 0,
                  f"Packager exited cleanly (rc={result.returncode})")
            if result.returncode == 0:
                out_base = HW_DIR / "out" / "fab"
                if out_base.exists():
                    folders = sorted(
                        [d for d in out_base.iterdir() if d.is_dir()],
                        key=lambda d: d.stat().st_mtime, reverse=True,
                    )
                    if folders:
                        out = folders[0]
                        check((out / "vip_vias.csv").exists(),
                              "Package contains vip_vias.csv")
                        check((out / "README_FAB.md").exists(),
                              "Package contains README_FAB.md")
                        gerbers = out / "gerbers"
                        if gerbers.exists():
                            gfiles = list(gerbers.iterdir())
                            check(len(gfiles) >= 8,
                                  f"Gerber directory has {len(gfiles)} files (≥8 expected)")
                        drill_dir = out / "drill"
                        if drill_dir.exists():
                            dfiles = list(drill_dir.iterdir())
                            check(len(dfiles) >= 1,
                                  f"Drill directory has {len(dfiles)} file(s)")

    # ── Summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    if fail_count == 0:
        print(f" {PASS}  ALL CHECKS PASSED ({warn_count} warnings)")
        print(" → Safe to upload to JLC / PCBWay")
        print(" → Order qty 7–10, VIPPO option, ENIG finish")
    else:
        print(f" {FAIL}  {fail_count} CHECK(S) FAILED ({warn_count} warnings)")
        print(" → DO NOT ORDER until failures are resolved")
    print("=" * 70)

    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
