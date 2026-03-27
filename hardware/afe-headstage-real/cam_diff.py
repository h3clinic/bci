#!/usr/bin/env python3
"""CAM-diff guard — detect changes to mask/drill layers between revisions.

Usage:
    python cam_diff.py baseline                  # save current hashes
    python cam_diff.py check                     # compare against baseline
    python cam_diff.py check --ack-mask-change   # acknowledge mask changes

The baseline is stored in:
    out/fab/.cam_baseline.yaml

If mask layers (F.Mask, B.Mask) or drill files change between revisions,
this script exits non-zero unless the operator explicitly acknowledges
the change with --ack-mask-change.

Copper layer changes always require review but don't block.
Mask and drill changes BLOCK because they affect VIPPO processing.

Baseline management policy:
    1. Generate baseline on a tagged release (e.g. fab_v1.0).
    2. Commit .cam_baseline.yaml to version control.
    3. Any mask/drill delta requires:
       a) Explicit acknowledgment via --ack-mask-change
       b) A new baseline saved after review
       c) Re-confirmation with vendor that VIPPO processing is unaffected
    4. validate_release.py checks the baseline automatically.
    5. Never delete .cam_baseline.yaml without tagging a new release.

Exit codes:
    0 — no blocking changes (or acknowledged)
    1 — blocking changes detected, must acknowledge
    2 — baseline not found or usage error
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

HW_DIR = Path(__file__).resolve().parent
FAB_OUT = HW_DIR / "out" / "fab"
BASELINE_FILE = FAB_OUT / ".cam_baseline.yaml"

# ── Layer classification ─────────────────────────────────────────────────
# Blocking layers: mask and drill changes directly affect VIPPO/soldermask
# Non-blocking: copper and silk changes are always reviewed but don't block
#
# Drill extensions cover:
#   .drl          — standard Excellon (KiCad default)
#   -PTH.drl      — plated through-hole (when separated)
#   -NPTH.drl     — non-plated through-hole (when separated)
#   .xln          — Excellon variant (some CAM tools)
#   .exc          — Excellon variant
BLOCKING_EXTENSIONS = {
    ".gts", ".gbs",         # F.Mask, B.Mask
    ".drl",                 # Excellon drill (covers PTH/NPTH when in same ext)
    ".xln", ".exc",         # Excellon variants
}
REVIEW_EXTENSIONS = {".gtl", ".g1", ".g2", ".gbl", ".gto", ".gbo", ".gm1"}

# ── Semantic role grouping ───────────────────────────────────────────────
# Files are classified by role, not just extension.
# This prevents vendor/exporter filename changes from sneaking a drill
# delta past blocking rules.
import re as _re

FILE_ROLES: dict[str, tuple[str, ...]] = {
    # role_name  →  (regex_pattern, ...)
    # NOTE: drill_npth MUST come before drill_pth so "NPTH.drl" isn't
    # swallowed by the generic .drl pattern.
    "mask_top":  (r"[Ff][._-]?[Mm]ask|F_Mask|\.gts$",),
    "mask_bot":  (r"[Bb][._-]?[Mm]ask|B_Mask|\.gbs$",),
    "drill_npth":(r"[Nn][Pp][Tt][Hh]",),
    "drill_pth": (r"[Pp][Tt][Hh]|\.drl$|\.xln$|\.exc$",),
    "copper_top":(r"F[._-]?Cu|\.gtl$",),
    "copper_in1":(r"In1[._-]?Cu|\.g1$",),
    "copper_in2":(r"In2[._-]?Cu|\.g2$",),
    "copper_bot":(r"B[._-]?Cu|\.gbl$",),
    "silk_top":  (r"F[._-]?Silk|\.gto$",),
    "silk_bot":  (r"B[._-]?Silk|\.gbo$",),
    "edge_cuts": (r"Edge[._-]?Cuts|\.gm1$",),
}

BLOCKING_ROLES = {"mask_top", "mask_bot", "drill_pth", "drill_npth"}


def classify_role(filename: str) -> str | None:
    """Classify a gerber/drill filename into a semantic role.

    Returns role name or None if unrecognized.
    """
    for role, patterns in FILE_ROLES.items():
        for pat in patterns:
            if _re.search(pat, filename):
                return role
    return None


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_latest_output() -> Path | None:
    """Find the most recent output folder in out/fab/."""
    if not FAB_OUT.exists():
        return None
    dirs = sorted(
        [d for d in FAB_OUT.iterdir() if d.is_dir() and d.name.startswith("afe-headstage")],
        key=lambda p: p.stat().st_mtime,
    )
    return dirs[-1] if dirs else None


def _collect_hashes(out_dir: Path) -> dict[str, str]:
    """Collect SHA-256 hashes of all gerber and drill files."""
    hashes: dict[str, str] = {}

    gerber_dir = out_dir / "gerbers"
    if gerber_dir.exists():
        for f in sorted(gerber_dir.iterdir()):
            if f.is_file():
                hashes[f"gerbers/{f.name}"] = _hash_file(f)

    drill_dir = out_dir / "drill"
    if drill_dir.exists():
        for f in sorted(drill_dir.iterdir()):
            if f.is_file() and f.suffix in (".drl", ".xln", ".exc", ".pdf"):
                hashes[f"drill/{f.name}"] = _hash_file(f)

    return hashes


def save_baseline() -> None:
    """Save current file hashes as the baseline."""
    out_dir = _find_latest_output()
    if out_dir is None:
        print("ERROR: No fab output found in out/fab/.")
        print("  Run: python make_fab_package.py")
        sys.exit(2)

    hashes = _collect_hashes(out_dir)
    if not hashes:
        print(f"ERROR: No gerber/drill files found in {out_dir}")
        sys.exit(2)

    baseline = {
        "source_dir": str(out_dir.name),
        "files": hashes,
    }

    FAB_OUT.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(yaml.dump(baseline, default_flow_style=False))
    print(f"Baseline saved: {BASELINE_FILE}")
    print(f"  Source: {out_dir.name}")
    print(f"  Files:  {len(hashes)}")


def check_baseline(ack_mask: bool = False) -> None:
    """Compare current output against saved baseline."""
    if not BASELINE_FILE.exists():
        print("ERROR: No baseline found.  Save one first:")
        print("  python cam_diff.py baseline")
        sys.exit(2)

    baseline = yaml.safe_load(BASELINE_FILE.read_text())
    old_hashes: dict[str, str] = baseline.get("files", {})
    old_source = baseline.get("source_dir", "unknown")

    out_dir = _find_latest_output()
    if out_dir is None:
        print("ERROR: No fab output found in out/fab/.")
        sys.exit(2)

    new_hashes = _collect_hashes(out_dir)

    print(f"Comparing:")
    print(f"  Baseline:  {old_source}")
    print(f"  Current:   {out_dir.name}")
    print(f"{'='*60}")

    changed_blocking: list[str] = []
    changed_review: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    all_keys = sorted(set(old_hashes.keys()) | set(new_hashes.keys()))

    for key in all_keys:
        old_h = old_hashes.get(key)
        new_h = new_hashes.get(key)

        if old_h is None:
            added.append(key)
        elif new_h is None:
            removed.append(key)
        elif old_h != new_h:
            # Dual check: extension-based AND role-based blocking.
            # A file is blocking if EITHER its extension OR its semantic
            # role is in the blocking set.  This prevents vendor filename
            # changes from sneaking deltas past the guard.
            ext = Path(key).suffix
            fname = Path(key).name
            role = classify_role(fname)
            is_blocking = (
                ext in BLOCKING_EXTENSIONS
                or (role is not None and role in BLOCKING_ROLES)
            )
            if is_blocking:
                changed_blocking.append(key)
            else:
                changed_review.append(key)

    # ── Report ───────────────────────────────────────────────────────
    if not (changed_blocking or changed_review or added or removed):
        print("\n✓ No changes detected.  Package matches baseline.")
        sys.exit(0)

    if changed_review:
        print(f"\n⚠ Changed (review required, non-blocking):")
        for f in changed_review:
            print(f"    {f}")

    if changed_blocking:
        print(f"\n✗ Changed (BLOCKING — mask/drill):")
        for f in changed_blocking:
            print(f"    {f}")

    if added:
        print(f"\n+ Added files:")
        for f in added:
            print(f"    {f}")

    if removed:
        print(f"\n- Removed files:")
        for f in removed:
            print(f"    {f}")

    # ── Decision ─────────────────────────────────────────────────────
    if changed_blocking:
        if ack_mask:
            print(f"\n⚠ Mask/drill changes ACKNOWLEDGED by operator.")
            print(f"  {len(changed_blocking)} blocking change(s) accepted.")
            print(f"  Ensure VIPPO processing is re-validated with vendor.")
        else:
            print(f"\n{'='*60}")
            print(f"RELEASE BLOCKED — {len(changed_blocking)} mask/drill file(s) changed.")
            print(f"These changes affect VIPPO processing and soldermask.")
            print(f"")
            print(f"To acknowledge and proceed:")
            print(f"  python cam_diff.py check --ack-mask-change")
            print(f"")
            print(f"To save new baseline after review:")
            print(f"  python cam_diff.py baseline")
            sys.exit(1)
    else:
        print(f"\n✓ No blocking changes.  {len(changed_review)} non-blocking change(s).")
        print(f"  Review the above before submission.")


def main():
    parser = argparse.ArgumentParser(description="CAM-diff guard for fab package revisions")
    parser.add_argument("action", choices=["baseline", "check"],
                        help="'baseline' to save, 'check' to compare")
    parser.add_argument("--ack-mask-change", action="store_true",
                        help="Acknowledge mask/drill layer changes (unblocks)")
    args = parser.parse_args()

    if args.action == "baseline":
        save_baseline()
    elif args.action == "check":
        check_baseline(ack_mask=args.ack_mask_change)


if __name__ == "__main__":
    main()
