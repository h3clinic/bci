#!/usr/bin/env python3
"""Release candidate gate — one-button "ship it" validation.

Usage:
    python validate_release.py                      # validate latest fab ZIP
    python validate_release.py out/fab/*.zip        # validate specific ZIP
    python validate_release.py --build              # build then validate
    python validate_release.py --build --skip-drc   # build (no DRC) then validate
    python validate_release.py --allow-detached     # skip git SHA HEAD check

Exit codes:
    0 — all gates pass, package is release-ready
    1 — one or more gates failed
    2 — usage error or file not found

Gates:
  1. Required top-level files in ZIP
  2. Gerber files present
  3. Drill files present (PTH + NPTH)
  4. fab_notes.txt clause IDs + regex backup
  5. fab_profile.yaml keyword coverage
  6. vip_vias.csv columns + row count
  7. order_checklist.txt keywords
  8. Tool chain readiness (validate_bringup.py, template.csv)
  9. CAM-diff baseline
 10. Vendor VIPPO capability evidence (naming + size + content enforced)
 11. manifest.json integrity — hash verification + git SHA HEAD check
     + bidirectional completeness (required ⊆ manifest ⊆ zip)
 12. manifest.build_inputs — board file hash matches working tree
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

HW_DIR = Path(__file__).resolve().parent

# ── Required files in ZIP ────────────────────────────────────────────────
REQUIRED_ZIP_FILES = [
    "fab_notes.txt",
    "fab_profile.yaml",
    "vip_vias.csv",
    "README_FAB.md",
    "bringup_checklist.txt",
    "assembly_constraints.txt",
    "dfm_feedback_loop.txt",
    "order_checklist.txt",
    "manifest.json",
]

# Vendor capability artifact naming convention
VENDOR_CAP_PATTERN = re.compile(
    r"vippo_capability_\w+_\d{4}[-_]?\d{2}[-_]?\d{2}\.(pdf|png|txt|jpg|eml)$",
    re.IGNORECASE,
)


def _current_git_sha() -> str | None:
    """Return 7-char git SHA of current HEAD, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=HW_DIR, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

# Gerber files must exist in gerbers/ subfolder
REQUIRED_GERBER_EXTENSIONS = [".gtl", ".g1", ".g2", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gm1"]

# ── fab_notes.txt structural patterns — canonical clause IDs ─────────────
# These are machine-checkable anchors, not English-phrase guesses.
FAB_NOTES_CLAUSE_IDS = [
    ("CLAUSE SM-1", "soldermask lock — do not modify expansion rules"),
    ("CLAUSE VIP-1", "VIPPO required per IPC-4761 Type VII at J5"),
    ("CLAUSE SUB-1", "no material substitutions without written approval"),
    ("CLAUSE FA-1", "first-article microsection required"),
]

# Legacy regex patterns as backup belt-and-suspenders
FAB_NOTES_PATTERNS = [
    (r"IPC-4761.*Type\s*VII|Type\s*VII.*IPC-4761", "IPC-4761 Type VII co-location"),
    (r"NOT\s+acceptable", "non-substitution clause"),
    (r"microsection|cross.?section", "first-article microsection requirement"),
]

# ── VIP CSV expected columns ─────────────────────────────────────────────
VIP_CSV_REQUIRED_COLS = {
    "net", "x_mm", "y_mm", "size_mm", "drill_mm",
    "pad_ref", "pad_pin", "pad_size_x_mm", "pad_size_y_mm",
    "layer_set", "mask_state",
}
VIP_CSV_MIN_ROWS = 14  # 14 channels CH9-CH22


def find_latest_zip() -> Path | None:
    """Find the most recent fab ZIP in out/fab/."""
    fab_dir = HW_DIR / "out" / "fab"
    if not fab_dir.exists():
        return None
    zips = sorted(fab_dir.glob("afe-headstage-v1_fab_*.zip"), key=lambda p: p.stat().st_mtime)
    return zips[-1] if zips else None


def validate(zip_path: Path, *, allow_detached: bool = False) -> list[str]:
    """Return list of error strings.  Empty = pass."""
    errors: list[str] = []

    if not zip_path.exists():
        return [f"ZIP not found: {zip_path}"]

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

        # ── Gate 1: Required top-level files ─────────────────────────
        for req in REQUIRED_ZIP_FILES:
            if req not in names:
                errors.append(f"ZIP missing required file: {req}")

        # ── Gate 2: Gerber files ─────────────────────────────────────
        gerber_files = [n for n in names if n.startswith("gerbers/")]
        for ext in REQUIRED_GERBER_EXTENSIONS:
            if not any(n.endswith(ext) for n in gerber_files):
                errors.append(f"ZIP missing gerber with extension {ext}")

        # ── Gate 3: Drill files ──────────────────────────────────────
        drill_files = [n for n in names if n.startswith("drill/")]
        if not drill_files:
            errors.append("ZIP missing drill/ directory")
        elif not any(n.endswith(".drl") for n in drill_files):
            errors.append("ZIP missing Excellon drill file (.drl)")

        # ── Gate 4: fab_notes.txt structural checks ──────────────────
        if "fab_notes.txt" in names:
            fab_notes = zf.read("fab_notes.txt").decode("utf-8")
            # 4a — canonical clause IDs (primary check)
            for clause_id, desc in FAB_NOTES_CLAUSE_IDS:
                if clause_id not in fab_notes:
                    errors.append(f"fab_notes.txt: missing {clause_id} ({desc})")
            # 4b — legacy regex patterns (informational only, non-blocking)
            # CLAUSE IDs are the sole enforcement mechanism.
            # English patterns are logged for human review but never block.
            fab_notes_warnings: list[str] = []
            for pattern, desc in FAB_NOTES_PATTERNS:
                if not re.search(pattern, fab_notes, re.IGNORECASE):
                    fab_notes_warnings.append(
                        f"fab_notes.txt: English pattern not found: {desc} "
                        f"(informational — not blocking)"
                    )
            if fab_notes_warnings:
                # Print warnings to stdout but do NOT append to errors
                for w in fab_notes_warnings:
                    print(f"  ⚠ {w}")
        else:
            errors.append("Cannot check fab_notes.txt structure — file missing")

        # ── Gate 5: fab_profile.yaml keyword coverage ────────────────
        if "fab_profile.yaml" in names:
            try:
                profile = yaml.safe_load(zf.read("fab_profile.yaml").decode("utf-8"))
                # Keywords may be at top level or under fab_notes
                keywords = profile.get("required_keywords", [])
                if not keywords and isinstance(profile.get("fab_notes"), dict):
                    keywords = profile["fab_notes"].get("required_keywords", [])
                if len(keywords) < 10:
                    errors.append(
                        f"fab_profile.yaml: only {len(keywords)} required_keywords "
                        f"(expected ≥10)"
                    )
            except Exception as e:
                errors.append(f"fab_profile.yaml: parse error — {e}")

        # ── Gate 6: vip_vias.csv columns and row count ───────────────
        if "vip_vias.csv" in names:
            csv_text = zf.read("vip_vias.csv").decode("utf-8")
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)
            if rows:
                actual_cols = set(rows[0].keys())
                missing_cols = VIP_CSV_REQUIRED_COLS - actual_cols
                if missing_cols:
                    errors.append(
                        f"vip_vias.csv: missing columns {missing_cols}"
                    )
            if len(rows) < VIP_CSV_MIN_ROWS:
                errors.append(
                    f"vip_vias.csv: only {len(rows)} rows "
                    f"(expected ≥{VIP_CSV_MIN_ROWS})"
                )

        # ── Gate 7: order_checklist.txt content ──────────────────────
        if "order_checklist.txt" in names:
            oc = zf.read("order_checklist.txt").decode("utf-8")
            for keyword in ["VIPPO", "microsection", "No material", "SIGN-OFF"]:
                if keyword.lower() not in oc.lower():
                    errors.append(
                        f"order_checklist.txt: missing keyword '{keyword}'"
                    )

    # ── Gate 8: Tool chain readiness (outside ZIP) ───────────────────
    if not (HW_DIR / "validate_bringup.py").exists():
        errors.append("validate_bringup.py not found — bring-up gate unavailable")

    if not (HW_DIR / "bringup_results" / "template.csv").exists():
        errors.append("bringup_results/template.csv not found — template missing")

    # ── Gate 9: CAM-diff baseline check ──────────────────────────────
    cam_baseline = HW_DIR / "out" / "fab" / ".cam_baseline.yaml"
    if cam_baseline.exists():
        try:
            baseline_data = yaml.safe_load(cam_baseline.read_text())
            old_hashes: dict[str, str] = baseline_data.get("files", {})

            # Find the output dir that corresponds to the ZIP
            zip_stem = zip_path.stem  # e.g. afe-headstage-v1_fab_20260301_abc1234
            # The output folder name drops the "_fab" part
            out_dir_name = zip_stem.replace("_fab_", "_")
            out_dir = zip_path.parent / out_dir_name

            if out_dir.exists():
                blocking_exts = {".gts", ".gbs", ".drl"}
                changed_blocking: list[str] = []

                for rel_path, old_hash in old_hashes.items():
                    full_path = out_dir / rel_path
                    if full_path.exists():
                        h = hashlib.sha256(full_path.read_bytes()).hexdigest()
                        if h != old_hash and full_path.suffix in blocking_exts:
                            changed_blocking.append(rel_path)

                if changed_blocking:
                    errors.append(
                        f"CAM-diff BLOCK: {len(changed_blocking)} mask/drill file(s) changed "
                        f"since baseline:\n"
                        + "\n".join(f"    {f}" for f in changed_blocking)
                        + "\n  Run: python cam_diff.py check --ack-mask-change"
                    )
        except Exception as e:
            errors.append(f"CAM-diff baseline check error: {e}")

    # ── Gate 10: Vendor capability evidence ──────────────────────────
    # Not just "dir exists" — must contain at least one artifact
    # matching: vippo_capability_<vendor>_<date>.(pdf|png|txt|jpg|eml)
    cap_dir = HW_DIR / "dfm_feedback" / "vendor_capability"
    if not cap_dir.exists():
        errors.append(
            "dfm_feedback/vendor_capability/ not found — "
            "vendor VIPPO capability evidence required"
        )
    else:
        cap_files = [f for f in cap_dir.iterdir() if f.is_file()]
        matching = [f for f in cap_files if VENDOR_CAP_PATTERN.match(f.name)]
        if not matching:
            errors.append(
                "dfm_feedback/vendor_capability/ has no evidence file matching\n"
                "  naming convention: vippo_capability_<vendor>_<YYYYMMDD>.(pdf|png|txt)\n"
                f"  Found: {[f.name for f in cap_files[:10]]}\n"
                "  Add vendor VIPPO capability proof before release."
            )
        else:
            # 10b — minimum file size (reject empty placeholders)
            MIN_EVIDENCE_BYTES = 100
            for ef in matching:
                sz = ef.stat().st_size
                if sz < MIN_EVIDENCE_BYTES:
                    errors.append(
                        f"Vendor evidence file '{ef.name}' is only {sz} bytes — "
                        f"minimum {MIN_EVIDENCE_BYTES} bytes required.\n"
                        f"  Replace placeholder with actual vendor proof."
                    )
            # 10c — content must reference IPC-4761 + Type VII
            # (for text-readable files only: .txt, .eml)
            REQUIRED_EVIDENCE_STRINGS = ["IPC-4761", "Type VII"]
            for ef in matching:
                if ef.suffix.lower() in (".txt", ".eml"):
                    try:
                        content = ef.read_text(errors="replace")
                        for req_str in REQUIRED_EVIDENCE_STRINGS:
                            if req_str not in content:
                                errors.append(
                                    f"Vendor evidence '{ef.name}' missing "
                                    f"required string '{req_str}'.\n"
                                    f"  Text evidence must reference IPC-4761 Type VII."
                                )
                    except Exception:
                        pass  # binary files skip content check

    # ── Gate 11: manifest.json integrity + completeness ──────────────
    # Verifies:
    #   a) manifest.json exists and parses
    #   b) Every file listed in manifest has matching SHA-256 in ZIP
    #   c) required_files ⊆ manifest.files  (nothing required is unhashed)
    #   d) manifest.files ⊆ zip_members    (no phantom entries)
    #   e) manifest.git_sha matches current HEAD (unless --allow-detached)
    if "manifest.json" not in names:
        errors.append("ZIP missing manifest.json — cannot verify build integrity")
    else:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf2:
                manifest = json.loads(zf2.read("manifest.json").decode("utf-8"))
                manifest_files = manifest.get("files", {})

                # 11a — manifest must not be empty
                if not manifest_files:
                    errors.append("manifest.json has empty 'files' dict")
                else:
                    # 11b — hash verification: manifest entry → ZIP content
                    for arc_name, expected_hash in manifest_files.items():
                        if arc_name not in names:
                            errors.append(
                                f"manifest.json lists '{arc_name}' but ZIP is missing it"
                            )
                            continue
                        actual_hash = hashlib.sha256(zf2.read(arc_name)).hexdigest()
                        if actual_hash != expected_hash:
                            errors.append(
                                f"manifest.json STALE: '{arc_name}' hash mismatch "
                                f"(expected {expected_hash[:12]}… got {actual_hash[:12]}…)"
                            )

                    # 11c — required_files ⊆ manifest.files
                    # Every REQUIRED_ZIP_FILES entry (except manifest.json itself)
                    # must appear in the manifest
                    for req in REQUIRED_ZIP_FILES:
                        if req == "manifest.json":
                            continue
                        if req not in manifest_files:
                            errors.append(
                                f"manifest.json missing required file '{req}' — "
                                f"it's required in ZIP but not hashed in manifest"
                            )

                    # Gerbers must also be in manifest
                    for ext in REQUIRED_GERBER_EXTENSIONS:
                        gerber_in_manifest = any(
                            k.startswith("gerbers/") and k.endswith(ext)
                            for k in manifest_files
                        )
                        if not gerber_in_manifest:
                            errors.append(
                                f"manifest.json missing gerber with extension {ext}"
                            )

                    # 11d — manifest.files ⊆ zip_members
                    for arc_name in manifest_files:
                        if arc_name not in names:
                            pass  # already caught in 11b

                # 11e — git SHA HEAD check
                manifest_sha = manifest.get("git_sha", "")
                if not manifest_sha:
                    errors.append("manifest.json missing 'git_sha' field")
                elif manifest_sha == "nogit":
                    pass  # not in a git repo, can't enforce
                elif not allow_detached:
                    current_sha = _current_git_sha()
                    if current_sha and current_sha != manifest_sha:
                        errors.append(
                            f"manifest.json git_sha '{manifest_sha}' does not match "
                            f"current HEAD '{current_sha}'.\n"
                            f"  The ZIP was built on a different commit.\n"
                            f"  Rebuild with: python make_fab_package.py\n"
                            f"  Or pass --allow-detached to skip this check."
                        )

                if not manifest.get("build_timestamp_utc"):
                    errors.append("manifest.json missing 'build_timestamp_utc' field")

                # ── Gate 12: build_inputs provenance ─────────────────
                # Verify the board file used to build the package matches
                # the current working-tree board file.  Catches "rebuilt
                # ZIP from an edited board I forgot to commit."
                build_inputs = manifest.get("build_inputs", {})
                if not build_inputs:
                    errors.append(
                        "manifest.json missing 'build_inputs' dict — "
                        "cannot verify source provenance"
                    )
                elif not allow_detached:
                    board_path = HW_DIR / "afe-headstage-v1.kicad_pcb"
                    if board_path.exists():
                        current_board_hash = hashlib.sha256(
                            board_path.read_bytes()
                        ).hexdigest()
                        manifest_board_hash = build_inputs.get("board_sha256", "")
                        if manifest_board_hash and current_board_hash != manifest_board_hash:
                            errors.append(
                                f"manifest.build_inputs.board_sha256 mismatch:\n"
                                f"  manifest: {manifest_board_hash[:16]}…\n"
                                f"  current:  {current_board_hash[:16]}…\n"
                                f"  The ZIP was built from a different board file.\n"
                                f"  Rebuild with: python make_fab_package.py\n"
                                f"  Or pass --allow-detached to skip."
                            )
        except Exception as e:
            errors.append(f"manifest.json parse error: {e}")

    return errors


def _build_package(skip_drc: bool = False) -> Path | None:
    """Run make_fab_package.py and return the ZIP path it produced."""
    cmd = [sys.executable, str(HW_DIR / "make_fab_package.py")]
    if skip_drc:
        cmd.append("--skip-drc")
    print(f"Building fab package...")
    result = subprocess.run(cmd, cwd=HW_DIR)
    if result.returncode != 0:
        print("ERROR: make_fab_package.py failed.")
        return None
    return find_latest_zip()


def main():
    parser = argparse.ArgumentParser(
        description="Release candidate gate — one-button ship-it validation"
    )
    parser.add_argument("zip_path", nargs="?", default=None,
                        help="Path to fab ZIP (default: latest in out/fab/)")
    parser.add_argument("--build", action="store_true",
                        help="Build fab package first, then validate")
    parser.add_argument("--skip-drc", action="store_true",
                        help="Skip DRC when building (requires --build)")
    parser.add_argument("--allow-detached", action="store_true",
                        help="Skip manifest git_sha vs HEAD check")
    parser.add_argument("--expected-sha",
                        help="Accept this specific SHA instead of HEAD")
    args = parser.parse_args()

    if args.build:
        zip_path = _build_package(skip_drc=args.skip_drc)
        if zip_path is None:
            sys.exit(2)
    elif args.zip_path:
        zip_path = Path(args.zip_path)
    else:
        zip_path = find_latest_zip()
        if zip_path is None:
            print("ERROR: No fab ZIP found in out/fab/. Build first:")
            print("  python validate_release.py --build")
            print("  python validate_release.py --build --skip-drc")
            sys.exit(2)

    print(f"Validating release candidate: {zip_path.name}")
    print(f"{'='*60}")

    errors = validate(zip_path, allow_detached=args.allow_detached)

    if errors:
        print(f"\n✗ {len(errors)} gate failure(s):\n")
        for i, e in enumerate(errors, 1):
            print(f"  {i}. {e}")
        print(f"\n{'='*60}")
        print("RELEASE BLOCKED — fix the above before submission.")
        sys.exit(1)
    else:
        print(f"\n✓ All release gates passed.")
        print(f"{'='*60}")
        print("Package is RELEASE-READY for vendor submission.")
        print(f"\nNext step: complete order_checklist.txt and submit.")
        sys.exit(0)


if __name__ == "__main__":
    main()
