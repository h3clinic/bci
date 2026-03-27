#!/usr/bin/env python3
"""Step D — Deterministic fab package generator for afe-headstage-v1.

Usage:
    python make_fab_package.py              # build package in out/fab/...
    python make_fab_package.py --dry-run    # print what would happen
    python make_fab_package.py --skip-drc   # skip DRC (use existing report)

One command.  Zero ambiguity.  Ships a zip containing:
  gerbers/          — all copper + mask + silk + edge + inner layers
  drill/            — PTH Excellon + drill map
  fab_notes.txt     — mandatory VIPPO callout (IPC-4761 Type VII)
  fab_profile.yaml  — pinned fab capability constants
  vip_vias.csv      — enumerated VIP coordinates for CAM verification
  bringup_checklist.txt    — first-article inspection & bring-up procedure
  assembly_constraints.txt — assembly process constraints
  dfm_feedback_loop.txt    — DFM vendor feedback process
  order_checklist.txt      — vendor submission pre-flight checklist
  README_FAB.md     — human-readable package index (generated, never hand-edited)

The output folder is named:
  afe-headstage-v1_<YYYYMMDD>_<gitsha7>/
and zipped to:
  afe-headstage-v1_fab_<YYYYMMDD>_<gitsha7>.zip
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

# ── Paths ────────────────────────────────────────────────────────────────
HW_DIR = Path(__file__).resolve().parent
BOARD_PCB = HW_DIR / "afe-headstage-v1.kicad_pcb"
FAB_PROFILE = HW_DIR / "fab_profile.yaml"
FAB_NOTES = HW_DIR / "fab_notes.txt"
BRINGUP_CHECKLIST = HW_DIR / "bringup_checklist.txt"
ASSEMBLY_CONSTRAINTS = HW_DIR / "assembly_constraints.txt"
DFM_FEEDBACK_LOOP = HW_DIR / "dfm_feedback_loop.txt"
ORDER_CHECKLIST = HW_DIR / "order_checklist.txt"
OUT_BASE = HW_DIR / "out" / "fab"

# ── Board identity ───────────────────────────────────────────────────────
BOARD_NAME = "afe-headstage-v1"
BOARD_REV = "v1"

# ── Required Gerber layers (KiCad layer names) ──────────────────────────
REQUIRED_GERBER_LAYERS = [
    "F.Cu", "In1.Cu", "In2.Cu", "B.Cu",
    "F.Mask", "B.Mask",
    "F.Silkscreen", "B.Silkscreen",
    "Edge.Cuts",
]

# ── Layer → expected Protel extension mapping ────────────────────────────
LAYER_EXT = {
    "F.Cu": "-F_Cu.gtl",
    "In1.Cu": "-In1_Cu.g1",
    "In2.Cu": "-In2_Cu.g2",
    "B.Cu": "-B_Cu.gbl",
    "F.Mask": "-F_Mask.gts",
    "B.Mask": "-B_Mask.gbs",
    "F.Silkscreen": "-F_Silkscreen.gto",
    "B.Silkscreen": "-B_Silkscreen.gbo",
    "Edge.Cuts": "-Edge_Cuts.gm1",
}

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _git_sha() -> str:
    """Return 7-char git SHA or 'nogit'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=HW_DIR, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "nogit"


def _kicad_cli_path() -> str | None:
    """Find kicad-cli."""
    return shutil.which("kicad-cli")


def _kicad_cli_version(cli: str) -> str:
    """Return kicad-cli version string."""
    result = subprocess.run([cli, "version"], capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def _sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], label: str) -> subprocess.CompletedProcess:
    """Run a subprocess, print status, abort on failure."""
    print(f"  → {label}")
    print(f"    $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"    FAILED (exit {result.returncode})")
        print(result.stderr[:2000])
        sys.exit(1)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# VIP VIA EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

def _extract_pad_size(board_text: str, ref: str, pin: str) -> tuple[float, float]:
    """Extract pad size (W, H) from the board file for a given ref+pin."""
    # Find the footprint block for this ref, then the pad block for this pin
    # Look for (pad "<pin>" smd ... (size W H)
    import re as _re
    # Find all footprint blocks with this ref
    fp_pattern = _re.compile(
        r'\(footprint\s.*?\(property\s+"Reference"\s+"' + _re.escape(ref) + r'"',
        _re.DOTALL,
    )
    for fp_m in fp_pattern.finditer(board_text):
        # Search forward from this match for the pad
        search_start = fp_m.start()
        # Find the pad within a reasonable window (10KB should cover any footprint)
        window = board_text[search_start:search_start + 10000]
        pad_pattern = _re.compile(
            r'\(pad\s+"' + _re.escape(pin) + r'"\s+smd\s+\w+\s*\n'
            r'\s+\(at\s+[\d.Ee+-]+\s+[\d.Ee+-]+\)\s*\n'
            r'\s+\(size\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\)',
            _re.MULTILINE,
        )
        pad_m = pad_pattern.search(window)
        if pad_m:
            return round(float(pad_m.group(1)), 4), round(float(pad_m.group(2)), 4)
    return 0.0, 0.0  # fallback if not found

def extract_vip_vias(board_text: str, fab: dict) -> list[dict]:
    """Extract via-in-pad vias from the board file.

    A VIP is any via whose center coincides (within 0.05mm) with an SMD
    pad center on the same net, for nets CH9–CH22.

    Returns list of dicts: {net, x, y, size, drill, pad_ref, pad_pin}
    """
    # Import parser from connectivity_proof
    sys.path.insert(0, str(HW_DIR))
    from connectivity_proof import (
        parse_board,
        NET_CODE_FOR_CH,
        CH_FOR_NET_CODE,
    )

    parsed = parse_board(board_text)
    ch_net_codes = set(NET_CODE_FOR_CH.values())

    EPS = 0.05  # mm snap tolerance
    vip_list = []

    for via in parsed["vias"]:
        if via["net"] not in ch_net_codes:
            continue
        vx, vy = via["at"]
        v_net = via["net"]

        for pad in parsed["pads"].get(v_net, []):
            if pad.get("pad_type") != "smd":
                continue
            px, py = pad["abs_xy"]
            if abs(vx - px) < EPS and abs(vy - py) < EPS:
                ch_num = CH_FOR_NET_CODE.get(v_net, v_net)
                ch_name = f"CH{ch_num}"
                pad_layers = pad.get("layers", [])
                mask_state = "mask_open" if "F.Mask" in pad_layers else "tented"
                # Extract pad size from board text (search near the pad's at coords)
                pad_sx, pad_sy = _extract_pad_size(board_text, pad["ref"], pad["pin"])
                vip_list.append({
                    "net": ch_name,
                    "x_mm": round(vx, 4),
                    "y_mm": round(vy, 4),
                    "size_mm": round(via["size"], 3),
                    "drill_mm": round(via["drill"], 3),
                    "pad_ref": pad["ref"],
                    "pad_pin": pad["pin"],
                    "pad_size_x_mm": pad_sx,
                    "pad_size_y_mm": pad_sy,
                    "layer_set": "|".join(pad_layers),
                    "mask_state": mask_state,
                })
                break  # one pad match per via is enough

    # Sort by net name for deterministic output
    vip_list.sort(key=lambda v: v["net"])
    return vip_list


def write_vip_csv(vip_list: list[dict], dest: Path) -> None:
    """Write vip_vias.csv."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["net", "x_mm", "y_mm", "size_mm", "drill_mm",
                        "pad_ref", "pad_pin",
                        "pad_size_x_mm", "pad_size_y_mm",
                        "layer_set", "mask_state"],
        )
        writer.writeheader()
        writer.writerows(vip_list)
    print(f"  → Wrote {dest.name}: {len(vip_list)} VIP vias")


# ═══════════════════════════════════════════════════════════════════════════
# README_FAB.md GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_readme_fab(fab: dict, vip_count: int, date_str: str, git_sha: str) -> str:
    """Generate README_FAB.md from fab_profile.yaml — no hand edits."""
    lim = fab["limits"]
    vfp = fab["via_fill_policy"]
    vn = vfp.get("vendor_notes", {})

    return f"""# Fabrication Package — {BOARD_NAME}

**Generated:** {date_str} | **Git SHA:** `{git_sha}` | **Do not hand-edit this file.**

---

## 1. Board Identity

| Field | Value |
|-------|-------|
| Board name | `{BOARD_NAME}` |
| Revision | `{BOARD_REV}` |
| Fab profile | `{fab["fab"]["name"]}` |
| Stackup | 4-layer, 1.6 mm, 1 oz Cu (0.035 mm), FR4 (Tg ≥ 150°C) |
| Surface finish | ENIG |
| Board size | 30.0 × 24.0 mm |
| Via tenting | All non-VIP vias shall be tented both sides |
| Soldermask | Do not modify mask expansion rules. Openings per Gerbers. |
| Substitutions | No material substitutions without written approval |

## 2. Stackup

| Layer | Name | Copper Weight |
|-------|------|---------------|
| L1 | F.Cu | 1 oz (0.035 mm) |
| L2 | In1.Cu | 1 oz (0.035 mm) |
| L3 | In2.Cu | 1 oz (0.035 mm) |
| L4 | B.Cu | 1 oz (0.035 mm) |

Inner layers (In1.Cu, In2.Cu): solid GND copper pour, no signal routing.

## 3. Drill Table

| Feature | Drill (mm) | Pad (mm) | Count | Notes |
|---------|-----------|----------|-------|-------|
| Signal vias | {lim["min_drill_mm"]:.2f} | 0.40 | 28 | 14 are VIP at J5 (see §4) |
| QFN thermal vias | 0.30 | 0.60 | 9 | U1 exposed pad, 3×3 grid |

- Minimum finished PTH drill: **{lim["min_drill_mm"]:.2f} mm**
- Maximum via aspect ratio: **{lim.get("max_aspect_ratio", 8.0):.0f}:1**
- Minimum annular ring: **{lim["min_annular_ring_mm"]:.3f} mm**
- All vias are through-hole (no blind/buried)
- NPTH file: empty (no mechanical holes)
- VIP vias enumerated in `vip_vias.csv` — treat as Type VII VIPPO minimum

## 4. ⚠️ VIPPO — See `fab_notes.txt` §4 (MANDATORY) ⚠️

**{vip_count} via-in-pad (VIP) vias at J5 require IPC-4761 Type VII (VIPPO).**

All process requirements, acceptance criteria, and the non-substitution clause
are specified in `fab_notes.txt` Section 4.  **Do not rely on this README for
VIPPO spec — `fab_notes.txt` is the authoritative document.**

| Parameter | Value |
|-----------|-------|
| VIP via count | {vip_count} |
| Coordinates | `vip_vias.csv` |
| Full spec | `fab_notes.txt` §4 |
| Reject clause | `fab_notes.txt` §9 |

## 5. Order Form Summary

| Parameter | Value |
|-----------|-------|
| Board thickness | 1.6 mm |
| Copper weight | 1 oz (35 µm) all layers |
| Surface finish | ENIG |
| Soldermask | Both sides, green |
| Silkscreen | Front, white |
| Min drill | {lim["min_drill_mm"]:.2f} mm |
| Min trace / space | {lim["min_trace_width_mm"]:.2f} mm / {lim["min_clearance_mm"]:.2f} mm |
| Via fill | **VIPPO required** (IPC-4761 Type VII) at J5 — 14 vias |
| If VIPPO unavailable | **REJECT ORDER** and contact us |

## 6. Package Contents

| File/Folder | Description |
|-------------|-------------|
| `gerbers/` | All copper, mask, silk, and edge Gerber files |
| `drill/` | Excellon drill file + drill map (PDF) |
| `fab_notes.txt` | Complete fabrication notes (MANDATORY reading) |
| `fab_profile.yaml` | Machine-readable fab capability constants |
| `vip_vias.csv` | Enumerated VIP coordinates: net, x, y, size, drill |
| `bringup_checklist.txt` | First-article inspection & bring-up procedure |
| `assembly_constraints.txt` | Assembly process constraints (stencil, reflow, ESD) |
| `dfm_feedback_loop.txt` | DFM vendor feedback process & FAQ |
| `order_checklist.txt` | Vendor submission pre-flight checklist |
| `README_FAB.md` | This file (package index) |

## 7. VIP Via Coordinates

See `vip_vias.csv` ({vip_count} entries).

## 8. Quality & Inspection

See `fab_notes.txt` §10 for inspection requirements.
See `bringup_checklist.txt` for first-article inspection procedure.

---

*Generated by `make_fab_package.py` — do not hand-edit.*
"""


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PACKAGER
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate fab package for afe-headstage-v1")
    parser.add_argument("--dry-run", action="store_true", help="Print plan, don't execute")
    parser.add_argument("--skip-drc", action="store_true", help="Skip DRC run")
    args = parser.parse_args()

    # ── Prerequisites ────────────────────────────────────────────────────
    cli = _kicad_cli_path()
    if not cli:
        print("ERROR: kicad-cli not found on PATH.")
        print("Install KiCad 9.x or add kicad-cli to PATH.")
        sys.exit(1)

    cli_ver = _kicad_cli_version(cli)
    print(f"kicad-cli version: {cli_ver}")
    print(f"Board: {BOARD_PCB}")

    if not BOARD_PCB.exists():
        print(f"ERROR: Board file not found: {BOARD_PCB}")
        sys.exit(1)

    if not FAB_PROFILE.exists():
        print(f"ERROR: fab_profile.yaml not found: {FAB_PROFILE}")
        sys.exit(1)

    if not FAB_NOTES.exists():
        print(f"ERROR: fab_notes.txt not found: {FAB_NOTES}")
        sys.exit(1)

    fab = yaml.safe_load(FAB_PROFILE.read_text())

    # ── Build output folder name ─────────────────────────────────────────
    date_str = datetime.date.today().strftime("%Y%m%d")
    git_sha = _git_sha()
    folder_name = f"{BOARD_NAME}_{date_str}_{git_sha}"
    out_dir = OUT_BASE / folder_name
    gerber_dir = out_dir / "gerbers"
    drill_dir = out_dir / "drill"
    zip_name = f"{BOARD_NAME}_fab_{date_str}_{git_sha}.zip"
    zip_path = OUT_BASE / zip_name

    print(f"\nOutput folder: {out_dir}")
    print(f"Zip file:      {zip_path}")

    if args.dry_run:
        print("\n[DRY RUN] Would execute the following steps:")
        print(f"  1. Create {out_dir}")
        print(f"  2. Export Gerbers to {gerber_dir}")
        print(f"  3. Export drills to {drill_dir}")
        print(f"  4. Extract VIP vias → vip_vias.csv")
        print(f"  5. Copy fab_notes.txt + fab_profile.yaml")
        print(f"  6. Generate README_FAB.md")
        print(f"  7. Zip → {zip_path}")
        return

    # ── Clean + create output dirs ───────────────────────────────────────
    if out_dir.exists():
        shutil.rmtree(out_dir)
    gerber_dir.mkdir(parents=True)
    drill_dir.mkdir(parents=True)

    # ── Step 1: Export Gerbers ───────────────────────────────────────────
    layers_csv = ",".join(REQUIRED_GERBER_LAYERS)
    _run(
        [cli, "pcb", "export", "gerbers",
         "--output", str(gerber_dir) + "/",
         "--layers", layers_csv,
         "--subtract-soldermask",
         str(BOARD_PCB)],
        f"Export Gerbers ({len(REQUIRED_GERBER_LAYERS)} layers)",
    )

    # ── Step 2: Export Drill files ───────────────────────────────────────
    _run(
        [cli, "pcb", "export", "drill",
         "--output", str(drill_dir) + "/",
         "--format", "excellon",
         "--excellon-units", "mm",
         "--excellon-zeros-format", "decimal",
         "--excellon-separate-th",
         "--generate-map",
         "--map-format", "pdf",
         str(BOARD_PCB)],
        "Export Excellon drill + map (PTH/NPTH separated)",
    )

    # ── Step 3: Extract VIP vias ─────────────────────────────────────────
    board_text = BOARD_PCB.read_text()
    vip_list = extract_vip_vias(board_text, fab)
    vip_csv_path = out_dir / "vip_vias.csv"
    write_vip_csv(vip_list, vip_csv_path)

    if len(vip_list) == 0:
        print("  WARNING: No VIP vias detected.  Is the board routed?")
    else:
        print(f"  VIP vias: {len(vip_list)} (nets: {', '.join(v['net'] for v in vip_list)})")

    # ── Step 4: Copy fab_notes.txt + fab_profile.yaml + Step E docs ────
    shutil.copy2(FAB_NOTES, out_dir / "fab_notes.txt")
    print(f"  → Copied fab_notes.txt")
    shutil.copy2(FAB_PROFILE, out_dir / "fab_profile.yaml")
    print(f"  → Copied fab_profile.yaml")

    # Step E manufacturing docs — these SHIP with the package
    for src, label in [
        (BRINGUP_CHECKLIST, "bringup_checklist.txt"),
        (ASSEMBLY_CONSTRAINTS, "assembly_constraints.txt"),
        (DFM_FEEDBACK_LOOP, "dfm_feedback_loop.txt"),
        (ORDER_CHECKLIST, "order_checklist.txt"),
    ]:
        if src.exists():
            shutil.copy2(src, out_dir / label)
            print(f"  → Copied {label}")
        else:
            print(f"  WARNING: {label} not found at {src} — skipping")

    # ── Step 5: Generate README_FAB.md ───────────────────────────────────
    readme_text = generate_readme_fab(fab, len(vip_list), date_str, git_sha)
    readme_path = out_dir / "README_FAB.md"
    readme_path.write_text(readme_text)
    print(f"  → Generated README_FAB.md")

    # ── Step 6: Optional DRC gate ────────────────────────────────────────
    if not args.skip_drc:
        drc_json = out_dir / "drc_report.json"
        try:
            _run(
                [cli, "pcb", "drc",
                 "--output", str(drc_json),
                 "--severity-all",
                 str(BOARD_PCB)],
                "Run DRC",
            )
        except SystemExit:
            # kicad-cli drc returns non-zero if violations exist;
            # we still capture the JSON for review
            print("  WARNING: DRC exited non-zero (violations found?). JSON saved.")
        if drc_json.exists():
            print(f"  → DRC report: {drc_json.name}")
    else:
        print("  → DRC skipped (--skip-drc)")

    # ── Step 7: Build manifest.json ──────────────────────────────────────
    # The manifest is the anti-stale-ZIP lock.  It records:
    #   - git SHA at build time
    #   - UTC build timestamp (ISO 8601)
    #   - kicad-cli version used to generate gerbers
    #   - SHA-256 of every shipped document
    # validate_release.py Gate 11 re-hashes ZIP members against this manifest.
    manifest_files: dict[str, str] = {}
    for root, dirs, files_in_dir in os.walk(out_dir):
        for fname in sorted(files_in_dir):
            full = Path(root) / fname
            arcname = str(full.relative_to(out_dir))
            # Skip manifest itself (it won't exist yet on first pass)
            if arcname == "manifest.json":
                continue
            manifest_files[arcname] = _sha256_file(full)

    # Build-inputs section: hash the sources that determine outputs.
    # validate_release.py Gate 12 re-checks board_sha256 against the
    # working tree to catch "rebuilt ZIP from an edited board I forgot
    # to commit."
    build_inputs: dict[str, str] = {}
    for input_path, input_key in [
        (BOARD_PCB, "board_sha256"),
        (Path(__file__).resolve(), "make_fab_package_sha256"),
        (FAB_PROFILE, "fab_profile_sha256"),
        (FAB_NOTES, "fab_notes_sha256"),
    ]:
        if input_path.exists():
            build_inputs[input_key] = _sha256_file(input_path)
    # Also capture gen_bundle_c_route.py if it exists (it defines routing)
    gen_route = HW_DIR / "gen_bundle_c_route.py"
    if gen_route.exists():
        build_inputs["gen_bundle_c_route_sha256"] = _sha256_file(gen_route)

    manifest = {
        "git_sha": git_sha,
        "build_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kicad_cli_version": cli_ver if cli else "n/a",
        "build_inputs": build_inputs,
        "files": manifest_files,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  → manifest.json ({len(manifest_files)} files hashed)")

    # ── Step 8: Zip everything ───────────────────────────────────────────
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(out_dir):
            for fname in sorted(files):
                full = Path(root) / fname
                arcname = str(full.relative_to(out_dir))
                zf.write(full, arcname)
    zip_size_kb = zip_path.stat().st_size / 1024
    print(f"\n  → Zip: {zip_path.name} ({zip_size_kb:.1f} KB)")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  FAB PACKAGE COMPLETE")
    print(f"  Folder: {out_dir}")
    print(f"  Zip:    {zip_path}")
    print(f"  VIP:    {len(vip_list)} via-in-pad (VIPPO required)")
    print(f"  Layers: {len(REQUIRED_GERBER_LAYERS)}")
    print(f"  SHA:    {git_sha}")
    print(f"{'='*60}")

    # Validate required files exist
    required_in_package = [
        gerber_dir / f"{BOARD_NAME}{LAYER_EXT[ly]}" for ly in REQUIRED_GERBER_LAYERS
    ] + [
        out_dir / "fab_notes.txt",
        out_dir / "fab_profile.yaml",
        out_dir / "vip_vias.csv",
        out_dir / "README_FAB.md",
        out_dir / "bringup_checklist.txt",
        out_dir / "assembly_constraints.txt",
        out_dir / "dfm_feedback_loop.txt",
        out_dir / "order_checklist.txt",
        out_dir / "manifest.json",
    ]

    missing = [f for f in required_in_package if not f.exists()]
    if missing:
        print(f"\n  ⚠ MISSING FILES ({len(missing)}):")
        for f in missing:
            print(f"    - {f.relative_to(out_dir)}")
        sys.exit(1)
    else:
        print(f"\n  ✓ All {len(required_in_package)} required files present.")

    # Validate zip contains fab_notes.txt with VIPPO keywords
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if "fab_notes.txt" not in names:
            print("  ⚠ ZIP missing fab_notes.txt!")
            sys.exit(1)
        content = zf.read("fab_notes.txt").decode("utf-8")
        for kw in ["IPC-4761", "Type VII", "VIPPO"]:
            if kw not in content:
                print(f"  ⚠ fab_notes.txt in ZIP missing keyword: {kw}")
                sys.exit(1)
    print("  ✓ ZIP validated: fab_notes.txt contains VIPPO callout.")


if __name__ == "__main__":
    main()
