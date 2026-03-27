#!/usr/bin/env python3
"""
generate_dataset_pack.py — Reproducible competition dataset bundle.

Generates and saves:
  - Training split (.npz)
  - Holdout split (.npz)
  - Metadata JSON (parameters, label counts, hashes)
  - Reproduction instructions

Usage:
  python analysis/generate_dataset_pack.py --out out/dataset_pack/ --seed 42

Judges can verify:
  python analysis/generate_dataset_pack.py --verify out/dataset_pack/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from perturbation_library import (
    generate_dataset,
    generate_partial_suppression,
    dataset_summary,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
    HOLDOUT_TYPES,
    LabeledWindow,
)
from features import extract_trial_features, N_FEATURES, FEATURE_NAMES


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _windows_to_npz(
    windows: list[LabeledWindow],
    output_path: Path,
) -> None:
    """Save list of LabeledWindows as .npz."""
    n = len(windows)
    if n == 0:
        return

    n_ch = windows[0].data_uv.shape[0]
    n_samp = windows[0].data_uv.shape[1]

    # Stack all data
    data = np.stack([w.data_uv for w in windows])  # (n, n_ch, n_samp)

    # Metadata arrays
    categories = [w.meta.category for w in windows]
    types = [w.meta.perturbation_type for w in windows]
    severities = np.array([w.meta.severity for w in windows])
    onsets = np.array([w.meta.onset_s for w in windows])
    seeds = np.array([w.meta.seed for w in windows])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        data_uv=data,
        categories=np.array(categories),
        perturbation_types=np.array(types),
        severities=severities,
        onsets=onsets,
        seeds=seeds,
        n_channels=np.array(n_ch),
        n_samples=np.array(n_samp),
        n_windows=np.array(n),
    )


def generate_pack(
    output_dir: Path,
    seed: int = 42,
    n_per_class: int = 5,
    n_holdout: int = 5,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 30.0,
    onset_s: float = 10.0,
) -> dict:
    """Generate full dataset pack with train + holdout + metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Training dataset ────────────────────────────────────────
    print(f"Generating training dataset (n_per_class={n_per_class}, "
          f"seed={seed})...")
    train_dataset = generate_dataset(
        n_per_class=n_per_class,
        severities=[0.3, 0.6, 0.9],
        n_channels=n_channels,
        fs=fs,
        duration_s=duration_s,
        onset_s=onset_s,
        seed=seed,
    )
    train_path = output_dir / "train.npz"
    _windows_to_npz(train_dataset, train_path)
    print(f"  Training: {len(train_dataset)} windows → {train_path}")

    # ── Holdout dataset ─────────────────────────────────────────
    print(f"Generating holdout dataset (n={n_holdout})...")
    holdout_windows: list[LabeledWindow] = []
    for i in range(n_holdout):
        sev = 0.3 + 0.3 * (i % 3)  # cycle through 0.3, 0.6, 0.9
        w = generate_partial_suppression(
            severity=sev,
            seed=seed + 90000 + i,
            onset_s=onset_s,
            n_channels=n_channels,
            fs=fs,
            duration_s=duration_s,
        )
        holdout_windows.append(w)
    holdout_path = output_dir / "holdout.npz"
    _windows_to_npz(holdout_windows, holdout_path)
    print(f"  Holdout: {len(holdout_windows)} windows → {holdout_path}")

    # ── Compute hashes ──────────────────────────────────────────
    train_hash = _sha256(train_path)
    holdout_hash = _sha256(holdout_path)

    # ── Dataset summary ─────────────────────────────────────────
    train_summary = dataset_summary(train_dataset)
    holdout_summary = dataset_summary(holdout_windows)

    # ── Metadata ────────────────────────────────────────────────
    metadata = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "analysis/generate_dataset_pack.py",
        "parameters": {
            "seed": seed,
            "n_per_class": n_per_class,
            "n_holdout": n_holdout,
            "severities": [0.3, 0.6, 0.9],
            "n_channels": n_channels,
            "fs": fs,
            "duration_s": duration_s,
            "onset_s": onset_s,
        },
        "training": {
            "file": "train.npz",
            "sha256": train_hash,
            "n_windows": len(train_dataset),
            "label_counts": train_summary,
            "perturbation_types": ["baseline_stable"] + ARTIFACT_TYPES + NEUROTOX_TYPES,
        },
        "holdout": {
            "file": "holdout.npz",
            "sha256": holdout_hash,
            "n_windows": len(holdout_windows),
            "label_counts": holdout_summary,
            "perturbation_types": HOLDOUT_TYPES,
        },
        "feature_spec": {
            "n_features": N_FEATURES,
            "feature_names": FEATURE_NAMES,
        },
        "reproduction": {
            "command": (
                f"python analysis/generate_dataset_pack.py "
                f"--out {output_dir} --seed {seed} "
                f"--n-per-class {n_per_class} --n-holdout {n_holdout}"
            ),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
    }

    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"  Metadata → {meta_path}")

    print(f"\n✓ Dataset pack generated at {output_dir.resolve()}")
    print(f"  Train: {len(train_dataset)} windows (SHA-256: {train_hash[:16]}...)")
    print(f"  Holdout: {len(holdout_windows)} windows (SHA-256: {holdout_hash[:16]}...)")

    return metadata


def verify_pack(pack_dir: Path) -> bool:
    """Verify dataset pack integrity from hashes."""
    meta_path = pack_dir / "metadata.json"
    if not meta_path.exists():
        print(f"ERROR: {meta_path} not found")
        return False

    metadata = json.loads(meta_path.read_text())

    ok = True
    for split in ("training", "holdout"):
        fname = metadata[split]["file"]
        expected_hash = metadata[split]["sha256"]
        fpath = pack_dir / fname

        if not fpath.exists():
            print(f"FAIL: {fpath} missing")
            ok = False
            continue

        actual_hash = _sha256(fpath)
        if actual_hash == expected_hash:
            print(f"  ✓ {fname}: SHA-256 matches")
        else:
            print(f"  ✗ {fname}: SHA-256 MISMATCH")
            print(f"    expected: {expected_hash}")
            print(f"    actual:   {actual_hash}")
            ok = False

    if ok:
        print("✓ All hashes verified — dataset pack is intact.")
    else:
        print("✗ VERIFICATION FAILED")

    return ok


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate or verify a reproducible competition dataset pack.",
    )
    ap.add_argument("--out", type=str, default="out/dataset_pack/",
                    help="Output directory (default: out/dataset_pack/)")
    ap.add_argument("--seed", type=int, default=42,
                    help="Random seed (default: 42)")
    ap.add_argument("--n-per-class", type=int, default=5,
                    help="Samples per class per severity (default: 5)")
    ap.add_argument("--n-holdout", type=int, default=5,
                    help="Holdout samples (default: 5)")
    ap.add_argument("--verify", type=str, default=None,
                    help="Verify existing pack at this path (skip generation)")
    args = ap.parse_args()

    if args.verify:
        success = verify_pack(Path(args.verify))
        sys.exit(0 if success else 1)

    generate_pack(
        output_dir=Path(args.out),
        seed=args.seed,
        n_per_class=args.n_per_class,
        n_holdout=args.n_holdout,
    )


if __name__ == "__main__":
    main()
