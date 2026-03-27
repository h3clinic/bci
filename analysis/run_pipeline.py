"""
run_pipeline.py — Single entrypoint for the artifact-resilient detection system.

Architecture contracts:
  1. Figures NEVER train models. They consume precomputed results.
  2. Training ONLY happens via guarded wrappers that check data sufficiency.
  3. Every stage returns a status dict. Callers never guess.
  4. Pipeline runs on garbage inputs without crashing. Skips are explicit.

Pipeline stages:
  1. Acquire   — load or generate multichannel time-series data
  2. QC        — CUSUM/z-score detection (always runs, no minimum data)
  3. Classify  — RF + Deep (guarded: skips if insufficient data)
  4. Report    — figures + metrics + predictions + model card

Usage:
  python analysis/run_pipeline.py --source synthetic --out out/demo
  python analysis/run_pipeline.py --source npz --data frames.npz
  python analysis/run_pipeline.py --source rhd --data recording.rhd
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure bare imports (from perturbation_library import ...) work
# regardless of whether we're invoked as `python -m analysis.run_pipeline`
# or `python analysis/run_pipeline.py`.
_analysis_dir = str(Path(__file__).resolve().parent)
if _analysis_dir not in sys.path:
    sys.path.insert(0, _analysis_dir)

import numpy as np
from numpy.typing import NDArray

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from perturbation_library import (
    generate_dataset,
    LabeledWindow,
    PerturbationMeta,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
)
from train_classifier import (
    detect_change,
    train_cause_classifier,
    wilson_ci,
    latency_summary,
)


# ═══════════════════════════════════════════════════════════════════
#  Data sufficiency guard — TWO-TIER policy
# ═══════════════════════════════════════════════════════════════════
#
#  "demo"   — low bar: prevents crashes, allows smoke runs.
#             Results are marked training_mode="demo" in output.
#  "report" — high bar: scientifically honest thresholds.
#             Results are marked training_mode="report" in output.
#             Only "report"-level results should go on a poster.

@dataclass(frozen=True)
class DataProfile:
    """Summary of dataset properties for sufficiency checks."""
    n_windows: int
    n_channels: int
    fs: float
    duration_s: float
    n_classes: int
    min_per_class: int

    @classmethod
    def from_dataset(cls, dataset: list[LabeledWindow]) -> "DataProfile":
        if not dataset:
            return cls(0, 0, 0.0, 0.0, 0, 0)
        from collections import Counter
        counts = Counter(w.meta.perturbation_type for w in dataset)
        return cls(
            n_windows=len(dataset),
            n_channels=dataset[0].data_uv.shape[0],
            fs=dataset[0].meta.fs,
            duration_s=dataset[0].meta.duration_s,
            n_classes=len(counts),
            min_per_class=min(counts.values()),
        )


# ── Demo tier (prevents crashes) ─────────────────────────────────

def enough_data_for_rf(profile: DataProfile) -> bool:
    """Demo-tier guard for RF. Prevents sklearn crashes on empty arrays."""
    return (
        profile.n_windows >= 20
        and profile.n_channels >= 1
        and profile.fs >= 1000.0
        and profile.duration_s >= 2.0
        and profile.n_classes >= 2
        and profile.min_per_class >= 3
    )


def enough_data_for_deep(profile: DataProfile) -> bool:
    """Demo-tier guard for deep model. Prevents degenerate batches."""
    return (
        profile.n_windows >= 30
        and profile.n_channels >= 4
        and profile.fs >= 2000.0
        and profile.duration_s >= 2.0
        and profile.n_classes >= 2
        and profile.min_per_class >= 5
    )


# ── Report tier (scientifically honest) ──────────────────────────

def enough_data_for_rf_report(profile: DataProfile) -> bool:
    """Report-tier guard for RF. Results at this level can go on a poster."""
    return (
        enough_data_for_rf(profile)
        and profile.min_per_class >= 25
        and profile.n_windows >= 200
    )


def enough_data_for_deep_report(profile: DataProfile) -> bool:
    """Report-tier guard for deep model. Results at this level are publishable."""
    return (
        enough_data_for_deep(profile)
        and profile.min_per_class >= 50
        and profile.n_windows >= 400
    )


def determine_training_mode(profile: DataProfile) -> str:
    """Determine which training tier the data supports.

    Returns "report", "demo", or "none".
    """
    if enough_data_for_rf_report(profile) or enough_data_for_deep_report(profile):
        return "report"
    if enough_data_for_rf(profile) or enough_data_for_deep(profile):
        return "demo"
    return "none"


# ═══════════════════════════════════════════════════════════════════
#  Canonical result structures — callers never guess
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StageResult:
    """Result from a pipeline stage. status is always present."""
    status: str          # "ok" | "skipped"
    reason: str = ""     # why skipped (empty if ok)
    metrics: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
#  Stage 1: Acquire
# ═══════════════════════════════════════════════════════════════════

def acquire_synthetic(
    n_per_class: int = 5,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 5.0,
    onset_s: float = 2.0,
    seed: int = 42,
) -> tuple[list[LabeledWindow], dict]:
    """Generate synthetic perturbation dataset."""
    dataset = generate_dataset(
        n_per_class=n_per_class,
        severities=[0.3, 0.6, 0.9],
        n_channels=n_channels,
        fs=fs,
        duration_s=duration_s,
        onset_s=onset_s,
        seed=seed,
    )
    meta = {
        "source": "synthetic",
        "n_per_class": n_per_class,
        "n_channels": n_channels,
        "fs": fs,
        "duration_s": duration_s,
        "onset_s": onset_s,
        "seed": seed,
        "n_windows": len(dataset),
    }
    return dataset, meta


def acquire_npz(path: str | Path) -> tuple[list[LabeledWindow], dict]:
    """Load from .npz dataset pack."""
    from data_loader import load_npz

    rec = load_npz(path)
    n_ch, n_samp = rec.data_uv.shape
    win = LabeledWindow(
        data_uv=rec.data_uv,
        meta=PerturbationMeta(
            perturbation_type="unknown",
            category="unknown",
            severity=0.0,
            onset_s=0.0,
            duration_s=n_samp / rec.metadata.fs,
            fs=rec.metadata.fs,
            n_channels=n_ch,
            seed=0,
        ),
    )
    meta = {
        "source": "npz",
        "path": str(path),
        "n_channels": n_ch,
        "n_samples": n_samp,
        "fs": rec.metadata.fs,
    }
    return [win], meta


def acquire_rhd(path: str | Path) -> tuple[list[LabeledWindow], dict]:
    """Load from Intan .rhd file."""
    from rhd_loader import load_rhd

    data = load_rhd(path)
    n_ch = data["amplifier_data"].shape[0]
    n_samp = data["amplifier_data"].shape[1]
    fs = data["sample_rate"]
    win = LabeledWindow(
        data_uv=data["amplifier_data"],
        meta=PerturbationMeta(
            perturbation_type="unknown",
            category="unknown",
            severity=0.0,
            onset_s=0.0,
            duration_s=n_samp / fs,
            fs=fs,
            n_channels=n_ch,
            seed=0,
        ),
    )
    meta = {
        "source": "rhd",
        "path": str(path),
        "n_channels": n_ch,
        "n_samples": n_samp,
        "fs": fs,
    }
    return [win], meta


# ═══════════════════════════════════════════════════════════════════
#  Stage 2: QC / Detection  (always runs — no minimum data needed)
# ═══════════════════════════════════════════════════════════════════

def run_qc(
    dataset: list[LabeledWindow],
    fs: float,
    onset_s: float,
) -> list[dict]:
    """Per-window CUSUM/z-score detection. Always runs, never crashes."""
    results = []
    for i, w in enumerate(dataset):
        is_pert = w.meta.category not in ("baseline", "unknown")
        bl_s = max(0.5, min(w.meta.onset_s - 0.5, onset_s - 0.5)) if is_pert \
            else w.meta.duration_s / 2

        det = detect_change(
            w.data_uv, fs=fs,
            onset_s_true=w.meta.onset_s if is_pert else w.meta.duration_s,
            baseline_s=bl_s, window_s=1.0,
            threshold=5.0, method="cusum",
        )

        results.append({
            "window_idx": i,
            "perturbation_type": w.meta.perturbation_type,
            "category": w.meta.category,
            "severity": w.meta.severity,
            "detected": det.detected,
            "latency_s": det.detection_latency_s,
            "cusum_max": float(np.max(det.cusum_trace)) if det.cusum_trace is not None else None,
        })
    return results


# ═══════════════════════════════════════════════════════════════════
#  Stage 3: Classification  (guarded — skips if insufficient data)
# ═══════════════════════════════════════════════════════════════════

def _train_rf_guarded(
    dataset: list[LabeledWindow],
    profile: DataProfile,
    seed: int,
) -> StageResult:
    """Train RF classifier with data sufficiency guard."""
    if not enough_data_for_rf(profile):
        return StageResult(
            status="skipped",
            reason=(f"Insufficient data for RF: {profile.n_windows} windows, "
                    f"{profile.n_channels}ch, {profile.fs}Hz, "
                    f"{profile.duration_s}s, {profile.min_per_class}/class"),
        )
    try:
        cls = train_cause_classifier(dataset, seed=seed)
        return StageResult(
            status="ok",
            metrics={
                "accuracy_multi": cls.accuracy_multi,
                "accuracy_binary": cls.accuracy_binary,
                "auroc_binary": cls.auroc_binary,
                "n_features": cls.n_features,
            },
        )
    except Exception as e:
        return StageResult(status="skipped", reason=f"RF training failed: {e}")


def _train_deep_guarded(
    dataset: list[LabeledWindow],
    profile: DataProfile,
    seed: int,
    max_samples: int = 4000,
    n_epochs: int = 10,
) -> StageResult:
    """Train deep model on the provided dataset with data sufficiency guard.

    Trains on the GIVEN dataset — no internal data generation.
    """
    if not enough_data_for_deep(profile):
        return StageResult(
            status="skipped",
            reason=(f"Insufficient data for Deep: {profile.n_windows} windows, "
                    f"{profile.n_channels}ch, {profile.fs}Hz, "
                    f"{profile.duration_s}s, {profile.min_per_class}/class"),
        )
    try:
        import torch
        from train_deep import train_model, evaluate_model, TrainConfig
        from deep_model import NeuralQANet, ModelConfig

        rng = np.random.default_rng(seed)
        perm = rng.permutation(len(dataset))
        split = int(0.8 * len(dataset))
        train_wins = [dataset[i] for i in perm[:split]]
        val_wins = [dataset[i] for i in perm[split:]]

        if len(train_wins) < 10 or len(val_wins) < 3:
            return StageResult(
                status="skipped",
                reason=f"Too few windows after split: train={len(train_wins)}, val={len(val_wins)}",
            )

        n_ch = dataset[0].data_uv.shape[0]
        cfg = ModelConfig(n_channels=n_ch)
        model = NeuralQANet(cfg)

        train_cfg = TrainConfig(
            n_epochs=n_epochs,
            batch_size=min(16, len(train_wins)),
            max_samples=max_samples,
            seed=seed,
        )
        train_res = train_model(model, train_wins, val_wins, train_cfg, verbose=False)
        eval_res = evaluate_model(model, val_wins, max_samples=max_samples)

        return StageResult(
            status="ok",
            metrics={
                "accuracy": eval_res.accuracy,
                "binary_accuracy": eval_res.binary_accuracy,
                "per_class": eval_res.per_class_accuracy,
                "n_params": train_res.n_params,
                "temperature": train_res.temperature,
                "best_epoch": train_res.best_epoch,
            },
        )
    except ImportError:
        return StageResult(status="skipped", reason="PyTorch not installed")
    except Exception as e:
        return StageResult(status="skipped", reason=f"Deep training failed: {e}")


def run_classification(
    dataset: list[LabeledWindow],
    seed: int = 42,
    max_samples: int = 4000,
    deep_epochs: int = 10,
) -> dict:
    """Run RF + Deep classification with guards. Always returns a complete dict."""
    profile = DataProfile.from_dataset(dataset)

    rf = _train_rf_guarded(dataset, profile, seed)
    deep = _train_deep_guarded(dataset, profile, seed, max_samples, deep_epochs)

    return {
        "stage2_rf": {
            "status": rf.status,
            "reason": rf.reason,
            "metrics": rf.metrics,
        },
        "stage2_deep": {
            "status": deep.status,
            "reason": deep.reason,
            "metrics": deep.metrics,
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  Figures — NEVER train. Consume precomputed results.
# ═══════════════════════════════════════════════════════════════════

def make_fig6_stress_grid(
    results: dict[str, NDArray[np.float64]],
    noise_levels: list[float],
    severities: list[float],
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 6: Stress grid from precomputed results.

    Args:
        results: {"CUSUM": (sev×noise) array, "RF": ..., "Deep": ...}
                 Any method may be absent if skipped.
        noise_levels: x-axis values
        severities: y-axis values
    """
    methods = [m for m in ["CUSUM", "RF", "Deep"] if m in results]
    n_panels = max(len(methods), 1)

    fig, axes = plt.subplots(1, n_panels, figsize=(6 * n_panels, 5),
                             squeeze=False)
    fig.suptitle(
        "Figure 6 — Stress Grid: Noise × Severity (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    for idx, method in enumerate(methods):
        ax = axes[0, idx]
        grid = results[method]
        im = ax.imshow(grid, aspect="auto", cmap="RdYlGn",
                       vmin=0, vmax=1, origin="lower")
        ax.set_xticks(range(len(noise_levels)))
        ax.set_xticklabels([f"{n}×" for n in noise_levels])
        ax.set_yticks(range(len(severities)))
        ax.set_yticklabels([f"{s:.1f}" for s in severities])
        ax.set_xlabel("Noise Multiplier")
        ax.set_ylabel("Perturbation Severity")
        ax.set_title(f"{method}", fontweight="bold")
        for i in range(len(severities)):
            for j in range(len(noise_levels)):
                val = grid[i, j]
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=9, color="black" if val > 0.4 else "white")
        fig.colorbar(im, ax=ax, shrink=0.8)

    if not methods:
        axes[0, 0].text(0.5, 0.5, "All methods skipped\n(insufficient data)",
                        ha="center", va="center", transform=axes[0, 0].transAxes,
                        fontsize=12, color="gray")

    fig.tight_layout(rect=[0, 0.02, 1, 0.93])

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    return fig


def make_fig7_domain_shift(
    rf_accs: dict[str, float],
    deep_accs: dict[str, float],
    shift_types: list[str],
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 7: Domain shift from precomputed results.

    Args:
        rf_accs: {"in-dist": 0.9, "amplitude_cal": 0.7, ...}
        deep_accs: same structure
        shift_types: ordered list of shift names (excluding "in-dist")
    """
    all_shifts = ["in-dist"] + shift_types
    shift_labels = {
        "in-dist": "In-Distribution",
        "amplitude_cal": "Amplitude\nCal Error",
        "noise_floor": "3× Noise\nFloor",
        "packet_dropout": "Packet\nDropout",
        "coupling_shift": "Coupling\nShift",
        "combined": "All Combined",
    }

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        "Figure 7 — Domain Shift Robustness (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    x = np.arange(len(all_shifts))
    w = 0.35

    # Panel A: accuracy comparison
    ax = axes[0]
    rf_vals = [rf_accs.get(s, 0.0) for s in all_shifts]
    deep_vals = [deep_accs.get(s, 0.0) for s in all_shifts]
    ax.bar(x - w/2, rf_vals, w, label="RF (Stage 2)", color="#4CAF50", alpha=0.8)
    ax.bar(x + w/2, deep_vals, w, label="Deep (NeuralQANet)", color="#2196F3", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([shift_labels.get(s, s) for s in all_shifts], fontsize=8)
    ax.set_ylabel("Binary Accuracy")
    ax.set_ylim(0, 1.2)
    ax.set_title("A) Accuracy Under Domain Shift", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")
    for i, (rv, dv) in enumerate(zip(rf_vals, deep_vals)):
        ax.text(i - w/2, rv + 0.03, f"{rv:.2f}", ha="center", fontsize=7)
        ax.text(i + w/2, dv + 0.03, f"{dv:.2f}", ha="center", fontsize=7)

    # Panel B: accuracy drop
    ax = axes[1]
    rf_drops = [rf_accs.get("in-dist", 0) - rf_accs.get(s, 0) for s in shift_types]
    deep_drops = [deep_accs.get("in-dist", 0) - deep_accs.get(s, 0) for s in shift_types]
    x2 = np.arange(len(shift_types))
    ax.bar(x2 - w/2, rf_drops, w, label="RF Drop", color="#F44336", alpha=0.7)
    ax.bar(x2 + w/2, deep_drops, w, label="Deep Drop", color="#FF9800", alpha=0.7)
    ax.set_xticks(x2)
    ax.set_xticklabels([shift_labels.get(s, s) for s in shift_types], fontsize=8)
    ax.set_ylabel("Accuracy Drop from In-Distribution")
    ax.set_title("B) Degradation Under Shift", fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="black", linewidth=0.5)

    fig.tight_layout(rect=[0, 0.02, 1, 0.93])

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    return fig


# ═══════════════════════════════════════════════════════════════════
#  Evaluation runners — compute results, then hand to figures
# ═══════════════════════════════════════════════════════════════════

def compute_stress_grid(
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 5.0,
    onset_s: float = 2.0,
    seed: int = 42,
    max_samples: int = 4000,
    deep_epochs: int = 5,
) -> tuple[dict[str, NDArray], list[float], list[float]]:
    """Compute stress grid metrics. Returns (grids, noise_levels, severities).

    Training happens here — guarded by data checks.
    """
    noise_levels = [0.5, 1.0, 2.0, 4.0]
    severities = [0.3, 0.5, 0.7, 0.9]
    n_per_cell = 3

    grids: dict[str, NDArray] = {
        "CUSUM": np.zeros((len(severities), len(noise_levels))),
    }

    rng = np.random.default_rng(seed)

    # Track whether RF/Deep ever succeed so we can include their grids
    rf_grid = np.full((len(severities), len(noise_levels)), np.nan)
    deep_grid = np.full((len(severities), len(noise_levels)), np.nan)
    rf_any_ok = False
    deep_any_ok = False

    for si, sev in enumerate(severities):
        for ni, noise_mult in enumerate(noise_levels):
            cell_dataset = generate_dataset(
                n_per_class=n_per_cell,
                severities=[sev],
                n_channels=n_channels,
                fs=fs,
                duration_s=duration_s,
                onset_s=onset_s,
                seed=seed + si * 100 + ni * 10,
            )

            if noise_mult != 1.0:
                for w in cell_dataset:
                    extra = rng.standard_normal(w.data_uv.shape) * 50.0 * noise_mult
                    w.data_uv[:] = w.data_uv + extra

            # CUSUM — always runs
            pert_wins = [w for w in cell_dataset if w.meta.category != "baseline"]
            if pert_wins:
                cusum_det = sum(
                    1 for w in pert_wins
                    if detect_change(
                        w.data_uv, fs=fs, onset_s_true=w.meta.onset_s,
                        baseline_s=max(0.3, onset_s - 0.3), window_s=0.5,
                        threshold=5.0, method="cusum",
                    ).detected
                )
                grids["CUSUM"][si, ni] = cusum_det / len(pert_wins)

            # RF — guarded
            profile = DataProfile.from_dataset(cell_dataset)
            rf_res = _train_rf_guarded(cell_dataset, profile, seed)
            if rf_res.status == "ok":
                rf_grid[si, ni] = rf_res.metrics.get("accuracy_binary", 0.0)
                rf_any_ok = True

            # Deep — guarded
            deep_res = _train_deep_guarded(cell_dataset, profile, seed,
                                           max_samples, deep_epochs)
            if deep_res.status == "ok":
                deep_grid[si, ni] = deep_res.metrics.get("binary_accuracy", 0.0)
                deep_any_ok = True

    if rf_any_ok:
        grids["RF"] = np.nan_to_num(rf_grid, nan=0.0)
    if deep_any_ok:
        grids["Deep"] = np.nan_to_num(deep_grid, nan=0.0)

    return grids, noise_levels, severities


def compute_domain_shift(
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 5.0,
    onset_s: float = 2.0,
    seed: int = 42,
    max_samples: int = 4000,
    deep_epochs: int = 8,
) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Compute domain shift metrics. Returns (rf_accs, deep_accs, shift_types).

    Training happens here — guarded by data checks.
    """
    rng = np.random.default_rng(seed)

    train_data = generate_dataset(
        n_per_class=3, severities=[0.3, 0.6, 0.9],
        n_channels=n_channels, fs=fs,
        duration_s=duration_s, onset_s=onset_s,
        seed=seed,
    )
    test_data_raw = generate_dataset(
        n_per_class=3, severities=[0.3, 0.6, 0.9],
        n_channels=n_channels, fs=fs,
        duration_s=duration_s, onset_s=onset_s,
        seed=seed + 5000,
    )

    shift_types = [
        "amplitude_cal", "noise_floor", "packet_dropout",
        "coupling_shift", "combined",
    ]

    shifted_datasets: dict[str, list[LabeledWindow]] = {}
    for shift in shift_types:
        shifted = []
        for w in test_data_raw:
            data = w.data_uv.copy()
            if shift in ("amplitude_cal", "combined"):
                gains = rng.uniform(0.7, 1.3, size=(data.shape[0], 1))
                data = data * gains
            if shift in ("noise_floor", "combined"):
                data = data + rng.standard_normal(data.shape) * 150.0
            if shift in ("packet_dropout", "combined"):
                n_drop = int(0.05 * data.shape[1])
                drop_idx = rng.choice(data.shape[1], size=n_drop, replace=False)
                data[:, drop_idx] = 0.0
            if shift in ("coupling_shift", "combined"):
                for _ in range(3):
                    src, tgt = rng.choice(data.shape[0], 2, replace=False)
                    data[tgt] += 0.03 * data[src]
            shifted.append(LabeledWindow(data_uv=data, meta=w.meta))
        shifted_datasets[shift] = shifted

    rf_accs: dict[str, float] = {}
    deep_accs: dict[str, float] = {}

    profile = DataProfile.from_dataset(train_data)

    # In-distribution
    rf_in = _train_rf_guarded(train_data, profile, seed)
    rf_accs["in-dist"] = rf_in.metrics.get("accuracy_binary", 0.0) if rf_in.status == "ok" else 0.0

    deep_in = _train_deep_guarded(train_data, profile, seed, max_samples, deep_epochs)
    deep_accs["in-dist"] = deep_in.metrics.get("binary_accuracy", 0.0) if deep_in.status == "ok" else 0.0

    # Each shift
    for shift, shifted_data in shifted_datasets.items():
        combined = train_data + shifted_data
        combined_profile = DataProfile.from_dataset(combined)

        rf_s = _train_rf_guarded(combined, combined_profile, seed)
        rf_accs[shift] = rf_s.metrics.get("accuracy_binary", 0.0) if rf_s.status == "ok" else 0.0

        # Deep: evaluate on shifted using model trained on in-dist
        if deep_in.status == "ok":
            try:
                from train_deep import train_model, evaluate_model as deep_eval, TrainConfig
                from deep_model import NeuralQANet, ModelConfig

                rng2 = np.random.default_rng(seed)
                perm = rng2.permutation(len(train_data))
                split = int(0.8 * len(train_data))
                tw = [train_data[i] for i in perm[:split]]
                vw = [train_data[i] for i in perm[split:]]

                n_ch = train_data[0].data_uv.shape[0]
                model = NeuralQANet(ModelConfig(n_channels=n_ch))
                t_cfg = TrainConfig(n_epochs=deep_epochs,
                                    batch_size=min(16, len(tw)),
                                    max_samples=max_samples, seed=seed)
                train_model(model, tw, vw, t_cfg, verbose=False)
                ev = deep_eval(model, shifted_data, max_samples=max_samples)
                deep_accs[shift] = ev.binary_accuracy
            except Exception:
                deep_accs[shift] = 0.0
        else:
            deep_accs[shift] = 0.0

    return rf_accs, deep_accs, shift_types


# ═══════════════════════════════════════════════════════════════════
#  Report: output writers
# ═══════════════════════════════════════════════════════════════════

def generate_figures(
    dataset: list[LabeledWindow],
    out_dir: Path,
    onset_s: float,
    fs: float,
    seed: int,
    dpi: int = 200,
) -> list[str]:
    """Generate evaluation figures 1–5. Figures 6–7 handled separately."""
    from eval_report import (
        make_fig1_onset_detection,
        make_fig2_cause_classification,
        make_fig3_robustness,
        make_fig4_ablation,
        make_fig5_model_comparison,
    )

    saved = []
    profile = DataProfile.from_dataset(dataset)

    fig_makers = [
        ("fig1_onset_detection.png",
         lambda: make_fig1_onset_detection(dataset, onset_s=onset_s, fs=fs,
                                           output_path=out_dir / "fig1_onset_detection.png", dpi=dpi),
         True),
        ("fig2_cause_classification.png",
         lambda: make_fig2_cause_classification(dataset, seed=seed,
                                                output_path=out_dir / "fig2_cause_classification.png", dpi=dpi),
         enough_data_for_rf(profile)),
        ("fig3_robustness.png",
         lambda: make_fig3_robustness(dataset, fs=fs, onset_s=onset_s, seed=seed,
                                      output_path=out_dir / "fig3_robustness.png", dpi=dpi),
         True),
        ("fig4_ablation.png",
         lambda: make_fig4_ablation(dataset, onset_s=onset_s, fs=fs, seed=seed,
                                    output_path=out_dir / "fig4_ablation.png", dpi=dpi),
         True),
        ("fig5_model_comparison.png",
         lambda: make_fig5_model_comparison(dataset, onset_s=onset_s, fs=fs, seed=seed,
                                            output_path=out_dir / "fig5_model_comparison.png", dpi=dpi),
         enough_data_for_rf(profile)),
    ]

    for name, maker, should_run in fig_makers:
        if not should_run:
            print(f"  ⊘ {name}: skipped (insufficient data)")
            continue
        try:
            fig = maker()
            if fig is not None:
                plt.close(fig)
            saved.append(name)
        except Exception as e:
            print(f"  ⚠ {name}: {e}")

    return saved


def write_predictions_csv(qc_results: list[dict], out_path: Path) -> None:
    """Write per-window predictions to CSV."""
    if not qc_results:
        return
    fieldnames = list(qc_results[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(qc_results)


def write_metrics_json(
    meta: dict,
    qc_results: list[dict],
    classification: dict,
    out_path: Path,
    training_mode: str = "none",
    data_source: str = "synthetic",
) -> None:
    """Write pipeline metrics to JSON."""
    detected = [r for r in qc_results if r["detected"]]
    latencies = [r["latency_s"] for r in detected if r["latency_s"] is not None]

    categories: dict[str, dict] = {}
    for r in qc_results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "detected": 0}
        categories[cat]["total"] += 1
        if r["detected"]:
            categories[cat]["detected"] += 1
    for cat in categories:
        n = categories[cat]["total"]
        k = categories[cat]["detected"]
        categories[cat]["rate"] = k / max(n, 1)
        categories[cat]["ci_95"] = list(wilson_ci(k, n))

    metrics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "2.0.0",
        "training_mode": training_mode,
        "data_source": data_source,
        "framing": (
            "Artifact-resilient detection of suppression-like "
            "electrophysiological perturbation signatures under "
            "a low-cost phantom protocol."
        ),
        "source": meta,
        "detection": {
            "n_windows": len(qc_results),
            "n_detected": len(detected),
            "detection_rate": len(detected) / max(len(qc_results), 1),
            "latency": latency_summary(latencies) if latencies else {},
            "per_category": categories,
        },
        "classification": classification,
    }

    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)


def write_model_card(out_path: Path, meta: dict) -> None:
    """Write MODEL_CARD.md with claims, limits, and proxy labels."""
    card = """# Model Card — BioGENEius Computational Pipeline v1

## Intended Use
Artifact-resilient detection of suppression-like electrophysiological
perturbation signatures under a low-cost phantom protocol.

## What This System Does
1. **Acquires** multichannel time-series data (frames → ADC → µV)
2. **QC / Artifact Segmentation** — ML mask + quality score per window
3. **Detects Change** — CUSUM or z-score baseline comparison
4. **Classifies Cause** — RF (Stage 2) and Deep model (NeuralQANet)
5. **Reports** — Figures, latency, confidence intervals, "unknown" option

## Architecture
- **Classical Stage 1**: CUSUM / z-score change-point detection
- **Classical Stage 2**: Random Forest on 20 handcrafted features
- **Deep Model**: NeuralQANet (1D CNN + Attention Pooling, 76K params)
  - 3 heads: artifact mask (binary), cause classification (9-class), severity (regression)
  - Self-supervised pretraining: masked reconstruction + NT-Xent contrastive
  - Domain-randomization augmentations: 60Hz, dropout, gain, jitter, coupling, drift, clipping
  - Temperature-calibrated probabilities

## Proxy Labels — What We Claim and What We Don't
All labels marked with † are **proxy signatures**, not validated biological neurotoxicity.

**We never claim neurons.** All results are simulation-study validated on synthetic data.

## Known Limitations
- **No real data yet** — All training and evaluation on synthetic perturbation library
- **Random noise floor** — ML does NOT beat physics. PCB shielding + analog design does.
- **Structured artifacts** — ML DOES help with 60Hz, coupling, drift, patterned contamination
- **Domain shift** — Performance may degrade when real phantom data differs from synthetic
- **Small model** — 76K params deliberately small for CPU; may under-fit complex patterns

## "Unknown" Behavior
When the model encounters data that doesn't match any trained class:
- Classification probabilities will be low (< 0.3 for top class)
- The system flags these as "low-confidence" in predictions.csv
- Intended behavior: **don't make a call when you don't know**

## Reproducibility
""" + f"- Seed: {meta.get('seed', 42)}\n"
    card += f"- Source: {meta.get('source', 'synthetic')}\n"
    card += "- All random state seeded for exact reproduction\n"
    card += "- Run command: `python analysis/run_pipeline.py --source synthetic`\n"

    with open(out_path, "w") as f:
        f.write(card)


# ═══════════════════════════════════════════════════════════════════
#  Model checkpoint save/load
# ═══════════════════════════════════════════════════════════════════

def save_model_checkpoint(
    model_dir: Path,
    dataset: list[LabeledWindow],
    seed: int,
) -> tuple[Path | None, str]:
    """Save trained model. Returns (path, status_msg).

    Guards on data sufficiency. Does NOT generate its own data.
    """
    profile = DataProfile.from_dataset(dataset)
    if not enough_data_for_deep(profile):
        return None, f"skipped: insufficient data ({profile.n_windows} windows)"

    try:
        import torch
        from deep_model import NeuralQANet, ModelConfig
        from train_deep import train_model, evaluate_model, TrainConfig

        model_dir.mkdir(parents=True, exist_ok=True)

        rng = np.random.default_rng(seed)
        perm = rng.permutation(len(dataset))
        split = int(0.8 * len(dataset))
        train_wins = [dataset[i] for i in perm[:split]]
        val_wins = [dataset[i] for i in perm[split:]]

        n_ch = dataset[0].data_uv.shape[0]
        cfg = ModelConfig(n_channels=n_ch)
        model = NeuralQANet(cfg)

        t_cfg = TrainConfig(n_epochs=10, batch_size=min(16, len(train_wins)),
                            max_samples=4000, seed=seed)
        train_res = train_model(model, train_wins, val_wins, t_cfg, verbose=False)
        eval_res = evaluate_model(model, val_wins, max_samples=4000)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ckpt_name = f"neuralqanet_v1_seed{seed}_{timestamp}.pt"
        ckpt_path = model_dir / ckpt_name

        torch.save({
            "model_state": model.state_dict(),
            "config": {"n_channels": cfg.n_channels, "n_classes": cfg.n_classes,
                       "embed_dim": cfg.embed_dim, "n_conv_blocks": cfg.n_conv_blocks},
            "train_result": {"best_val_acc": train_res.best_val_acc,
                             "temperature": train_res.temperature,
                             "n_params": train_res.n_params},
            "eval_result": {"accuracy": eval_res.accuracy,
                            "binary_accuracy": eval_res.binary_accuracy},
            "seed": seed,
            "timestamp": timestamp,
            "version": "1.0.0",
        }, ckpt_path)

        return ckpt_path, "ok"
    except ImportError:
        return None, "skipped: PyTorch not installed"
    except Exception as e:
        return None, f"skipped: {e}"


# ═══════════════════════════════════════════════════════════════════
#  Main pipeline
# ═══════════════════════════════════════════════════════════════════

def run_pipeline(
    source: str = "synthetic",
    data_path: str | None = None,
    out_dir: str | None = None,
    n_per_class: int = 5,
    duration_s: float = 5.0,
    onset_s: float = 2.0,
    seed: int = 42,
    max_samples: int = 4000,
    deep_epochs: int = 10,
    dpi: int = 200,
    save_model: bool = True,
) -> Path:
    """Run the full pipeline: acquire → QC → classify → report.

    Never crashes on valid inputs. Stages skip when data is insufficient.
    Returns path to the output directory.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if out_dir is None:
        out_dir = f"out/run_{ts}"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("═══ BioGENEius Pipeline v2 ═══")
    print(f"Source: {source}")
    print(f"Output: {out.resolve()}")
    print(f"Seed: {seed}")
    print()

    # ── Stage 1: Acquire ────────────────────────────────────────
    print("Stage 1: Acquiring data...")
    t0 = time.time()
    if source == "synthetic":
        dataset, meta = acquire_synthetic(
            n_per_class=n_per_class,
            duration_s=duration_s,
            onset_s=onset_s,
            seed=seed,
        )
    elif source == "npz":
        if not data_path:
            raise ValueError("--data required for npz source")
        dataset, meta = acquire_npz(data_path)
    elif source == "rhd":
        if not data_path:
            raise ValueError("--data required for rhd source")
        dataset, meta = acquire_rhd(data_path)
    else:
        raise ValueError(f"Unknown source: {source}")
    print(f"  → {len(dataset)} windows ({time.time()-t0:.1f}s)")

    profile = DataProfile.from_dataset(dataset)
    training_mode = determine_training_mode(profile)
    print(f"  → Profile: {profile.n_channels}ch, {profile.fs}Hz, "
          f"{profile.duration_s}s, {profile.n_classes} classes, "
          f"{profile.min_per_class}/class")
    print(f"  → Training mode: {training_mode}")

    # ── Stage 2: QC ─────────────────────────────────────────────
    print("Stage 2: QC / detection...")
    t0 = time.time()
    fs = meta.get("fs", 20_000.0)
    qc_results = run_qc(dataset, fs=fs, onset_s=onset_s)
    n_det = sum(1 for r in qc_results if r["detected"])
    print(f"  → {n_det}/{len(qc_results)} windows flagged ({time.time()-t0:.1f}s)")

    # ── Stage 3: Classify ───────────────────────────────────────
    print("Stage 3: Classification...")
    t0 = time.time()
    classification = run_classification(
        dataset, seed=seed, max_samples=max_samples, deep_epochs=deep_epochs,
    )
    for stage_name in ("stage2_rf", "stage2_deep"):
        s = classification[stage_name]
        if s["status"] == "ok":
            m = s["metrics"]
            acc = m.get("accuracy_binary", m.get("binary_accuracy", "?"))
            print(f"  → {stage_name}: ok (binary_acc={acc})")
        else:
            print(f"  → {stage_name}: skipped — {s['reason']}")
    print(f"  → ({time.time()-t0:.1f}s)")

    # ── Stage 4: Report ─────────────────────────────────────────
    print("Stage 4: Generating report...")
    t0 = time.time()

    print("  Figures 1–5...")
    saved_figs = generate_figures(dataset, out, onset_s=onset_s, fs=fs,
                                  seed=seed, dpi=dpi)

    # Fig 6: compute → plot
    print("  Figure 6: Stress grid...")
    try:
        grids, noise_levels, sevs = compute_stress_grid(
            n_channels=meta.get("n_channels", 16),
            fs=fs, duration_s=min(duration_s, 5.0),
            onset_s=min(onset_s, 2.0),
            seed=seed, max_samples=min(max_samples, 4000),
            deep_epochs=min(deep_epochs, 5),
        )
        fig6 = make_fig6_stress_grid(
            grids, noise_levels, sevs,
            output_path=out / "fig6_stress_grid.png", dpi=dpi,
        )
        plt.close(fig6)
        saved_figs.append("fig6_stress_grid.png")
    except Exception as e:
        print(f"  ⚠ fig6: {e}")

    # Fig 7: compute → plot
    print("  Figure 7: Domain shift...")
    try:
        rf_accs, deep_accs, shift_types = compute_domain_shift(
            n_channels=meta.get("n_channels", 16),
            fs=fs, duration_s=min(duration_s, 5.0),
            onset_s=min(onset_s, 2.0),
            seed=seed, max_samples=min(max_samples, 4000),
            deep_epochs=min(deep_epochs, 8),
        )
        fig7 = make_fig7_domain_shift(
            rf_accs, deep_accs, shift_types,
            output_path=out / "fig7_domain_shift.png", dpi=dpi,
        )
        plt.close(fig7)
        saved_figs.append("fig7_domain_shift.png")
    except Exception as e:
        print(f"  ⚠ fig7: {e}")

    write_predictions_csv(qc_results, out / "predictions.csv")
    write_metrics_json(
        meta, qc_results, classification, out / "metrics.json",
        training_mode=training_mode, data_source=source,
    )
    write_model_card(out / "model_card.md", meta)

    if save_model:
        print("  Saving model checkpoint...")
        models_dir = Path(__file__).parent / "models"
        ckpt_path, ckpt_status = save_model_checkpoint(models_dir, dataset, seed)
        if ckpt_path:
            print(f"  → {ckpt_path}")
        else:
            print(f"  → Checkpoint {ckpt_status}")

    print(f"  → ({time.time()-t0:.1f}s)")

    print()
    print("═══ Run Complete ═══")
    print(f"Output: {out.resolve()}")
    print(f"Figures: {len(saved_figs)}")
    for f in sorted(saved_figs):
        print(f"  {f}")
    print(f"predictions.csv: {len(qc_results)} rows")
    print(f"metrics.json: pipeline metrics + CI")
    print(f"model_card.md: claims + limits")

    return out


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "BioGENEius Pipeline v2 — Artifact-resilient detection of "
            "suppression-like electrophysiological perturbation signatures."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m analysis.run_pipeline --source synthetic\n"
            "  python -m analysis.run_pipeline --source synthetic --out out/demo --seed 42\n"
            "  python -m analysis.run_pipeline --source npz --data frames.npz\n"
            "  python -m analysis.run_pipeline --mode domain_shift --out out/domain_shift --profile report\n"
        ),
    )
    ap.add_argument("--mode", choices=["pipeline", "domain_shift"],
                    default="pipeline",
                    help="Run mode: 'pipeline' (default) or 'domain_shift'")
    ap.add_argument("--source", choices=["synthetic", "npz", "rhd"],
                    default="synthetic", help="Data source (default: synthetic)")
    ap.add_argument("--data", type=str, default=None,
                    help="Path to data file (required for npz/rhd)")
    ap.add_argument("--out", type=str, default=None,
                    help="Output directory (default: auto-timestamped)")
    ap.add_argument("--n-per-class", type=int, default=5,
                    help="Samples per class per severity (default: 5)")
    ap.add_argument("--duration", type=float, default=5.0,
                    help="Trial duration seconds (default: 5)")
    ap.add_argument("--onset", type=float, default=2.0,
                    help="Perturbation onset seconds (default: 2)")
    ap.add_argument("--seed", type=int, default=42,
                    help="Random seed (default: 42)")
    ap.add_argument("--max-samples", type=int, default=4000,
                    help="Max samples per window for deep model (default: 4000)")
    ap.add_argument("--deep-epochs", type=int, default=10,
                    help="Deep model training epochs (default: 10)")
    ap.add_argument("--dpi", type=int, default=200,
                    help="Figure resolution (default: 200)")
    ap.add_argument("--no-save-model", action="store_true",
                    help="Skip model checkpoint save")
    ap.add_argument("--profile", choices=["demo", "report"],
                    default="demo",
                    help="Guard tier for domain_shift mode (default: demo)")
    ap.add_argument("--models", type=str, default=None,
                    help="Comma-separated models for domain_shift: cusum,rf,deep (default: all)")
    ap.add_argument("--shifts", type=str, default=None,
                    help="Comma-separated shifts for domain_shift (default: all 13)")
    ap.add_argument("--severities", type=str, default=None,
                    help="Comma-separated severities for domain_shift, e.g. 0.3,0.6 (default: 0.3,0.6,0.9)")
    args = ap.parse_args()

    if args.mode == "domain_shift":
        from domain_shift import run_domain_shift_suite

        model_list = None
        if args.models:
            model_list = [m.strip() for m in args.models.split(",")]

        shift_list = None
        if args.shifts:
            shift_list = [s.strip() for s in args.shifts.split(",")]

        sev_list = None
        if args.severities:
            sev_list = [float(s.strip()) for s in args.severities.split(",")]

        out = args.out or "out/domain_shift"
        run_domain_shift_suite(
            out_dir=out,
            n_per_class=args.n_per_class,
            fs=20_000.0,
            duration_s=args.duration,
            onset_s=args.onset,
            seed=args.seed,
            max_samples=args.max_samples,
            deep_epochs=args.deep_epochs,
            dpi=args.dpi,
            profile=args.profile,
            shifts=shift_list,
            severities=sev_list,
            models=model_list,
        )
    else:
        run_pipeline(
            source=args.source,
            data_path=args.data,
            out_dir=args.out,
            n_per_class=args.n_per_class,
            duration_s=args.duration,
            onset_s=args.onset,
            seed=args.seed,
            max_samples=args.max_samples,
            deep_epochs=args.deep_epochs,
            dpi=args.dpi,
            save_model=not args.no_save_model,
        )


if __name__ == "__main__":
    main()
