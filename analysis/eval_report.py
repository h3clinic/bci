"""
eval_report.py — Computational evaluation figures (Simulation Study).

Three required BioGENEius figures + ablation panel:

  Fig 1 (Onset Detection): CUSUM vs z-score detection, latency + FPR
  Fig 2 (Cause Classification): confusion matrix across severities + ROC
  Fig 3 (Robustness): domain shift / noise stress test + holdout generalization

  Fig 4 (Ablation): Classical-only vs two-stage — why ML adds value

All figures labeled "Simulation Study" — honest proxy language.
User-facing labels use "suppression-like" not "neurotox."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
from numpy.typing import NDArray

from perturbation_library import (
    generate_dataset,
    generate_mixed_neurotox,
    generate_baseline,
    generate_broadband_noise,
    generate_partial_suppression,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
    LabeledWindow,
)
from features import extract_windowed_features, FEATURE_NAMES, N_FEATURES
from train_classifier import (
    detect_change,
    train_cause_classifier,
    run_ablation,
    DetectionResult,
    ClassificationResult,
    AblationResult,
    wilson_ci,
    bootstrap_ci,
    latency_summary,
)


# ── Display-safe labels (no bare "neurotox") ────────────────────────

DISPLAY_LABELS = {
    "baseline_stable": "Baseline",
    "impedance_drift": "Impedance Drift",
    "broadband_noise": "Broadband Noise",
    "line_interference": "Line Interference",
    "crosstalk_coupling": "Crosstalk",
    "spike_suppression": "Spike Suppression†",
    "burst_collapse": "Burst Collapse†",
    "spectral_shift": "Spectral Shift†",
    "mixed_neurotox": "Mixed Suppression†",
    "partial_suppression_holdout": "Holdout†",
}

BINARY_LABELS = ["Artifact /\nBaseline", "Suppression-\nLike†"]


def _safe_label(ptype: str) -> str:
    return DISPLAY_LABELS.get(ptype, ptype)


# ═══════════════════════════════════════════════════════════════════
#  Figure 1: Onset Detection — Latency + FPR
# ═══════════════════════════════════════════════════════════════════

def make_fig1_onset_detection(
    dataset: list[LabeledWindow],
    onset_s: float,
    fs: float,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 1: Detection latency distribution + false positive rate.

    Shows CUSUM detects perturbation fast with low false alarms.
    Compares CUSUM vs classical z-score.
    """
    pert_windows = [w for w in dataset if w.meta.category != "baseline"]
    base_windows = [w for w in dataset if w.meta.category == "baseline"]

    # Collect detection results for both methods
    methods = {"CUSUM (Stage 1)": "cusum", "Z-Score (Classical)": "zscore"}
    thresholds = {"cusum": 5.0, "zscore": 3.0}

    results: dict[str, dict] = {}
    for label, method in methods.items():
        lat_list: list[float] = []
        tp = 0
        fp = 0
        for w in pert_windows:
            bl_s = max(1.0, min(w.meta.onset_s - 1.0, onset_s - 1.0))
            det = detect_change(w.data_uv, fs=fs, onset_s_true=w.meta.onset_s,
                                baseline_s=bl_s, window_s=1.0,
                                threshold=thresholds[method], method=method)
            if det.detected:
                tp += 1
                if det.detection_latency_s is not None:
                    lat_list.append(det.detection_latency_s)
        for w in base_windows:
            bl_s = w.meta.duration_s / 2
            det = detect_change(w.data_uv, fs=fs, onset_s_true=w.meta.duration_s,
                                baseline_s=bl_s, window_s=1.0,
                                threshold=thresholds[method], method=method)
            if det.detected:
                fp += 1

        n_pert = max(len(pert_windows), 1)
        n_base = max(len(base_windows), 1)
        results[label] = {
            "tp_rate": tp / n_pert,
            "fp_rate": fp / n_base,
            "latencies": lat_list,
            "tp_ci": wilson_ci(tp, len(pert_windows)),
            "fp_ci": wilson_ci(fp, len(base_windows)),
            "summary": latency_summary(lat_list),
        }

    # ── Build figure ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle(
        "Figure 1 — Onset Detection Performance (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    # Panel A: Latency histogram comparison
    ax = axes[0]
    colors = ["#2196F3", "#FF9800"]
    for (label, res), color in zip(results.items(), colors):
        if res["latencies"]:
            ax.hist(res["latencies"], bins=12, alpha=0.6, color=color,
                    label=f"{label}\nmed={res['summary']['median']:.1f}s",
                    edgecolor="black", linewidth=0.5)
    ax.set_xlabel("Detection Latency (s)")
    ax.set_ylabel("Count")
    ax.set_title("A) Latency Distribution", fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # Panel B: TP rate + CI comparison
    ax = axes[1]
    names = list(results.keys())
    tp_rates = [results[n]["tp_rate"] for n in names]
    tp_ci_lo = [results[n]["tp_ci"][0] for n in names]
    tp_ci_hi = [results[n]["tp_ci"][1] for n in names]
    yerr_lo = [tp_rates[i] - tp_ci_lo[i] for i in range(len(names))]
    yerr_hi = [tp_ci_hi[i] - tp_rates[i] for i in range(len(names))]
    bars = ax.bar(names, tp_rates, color=colors, alpha=0.8,
                  yerr=[yerr_lo, yerr_hi], capsize=8, edgecolor="black")
    ax.set_ylabel("True Positive Rate")
    ax.set_ylim(0, 1.15)
    ax.set_title("B) Detection Rate (95% Wilson CI)", fontweight="bold")
    for bar, rate in zip(bars, tp_rates):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.03,
                f"{rate:.2f}", ha="center", fontsize=10, fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    # Panel C: FP rate comparison
    ax = axes[2]
    fp_rates = [results[n]["fp_rate"] for n in names]
    fp_ci_lo = [results[n]["fp_ci"][0] for n in names]
    fp_ci_hi = [results[n]["fp_ci"][1] for n in names]
    yerr_lo = [fp_rates[i] - fp_ci_lo[i] for i in range(len(names))]
    yerr_hi = [fp_ci_hi[i] - fp_rates[i] for i in range(len(names))]
    bars = ax.bar(names, fp_rates, color=colors, alpha=0.8,
                  yerr=[yerr_lo, yerr_hi], capsize=8, edgecolor="black")
    ax.set_ylabel("False Positive Rate")
    ax.set_ylim(0, max(0.3, max(fp_rates) * 2 + 0.05))
    ax.set_title("C) False Alarm Rate (95% Wilson CI)", fontweight="bold")
    for bar, rate in zip(bars, fp_rates):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                f"{rate:.2f}", ha="center", fontsize=10, fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout(rect=[0, 0.02, 1, 0.93])
    _add_watermark(fig, len(dataset))

    if output_path:
        _save(fig, output_path, dpi)
    return fig


# ═══════════════════════════════════════════════════════════════════
#  Figure 2: Cause Classification
# ═══════════════════════════════════════════════════════════════════

def make_fig2_cause_classification(
    dataset: list[LabeledWindow],
    seed: int = 42,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 2: Confusion matrix (artifact vs suppression-like) + CV + feature importance."""
    clf = train_cause_classifier(dataset, seed=seed)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.suptitle(
        "Figure 2 — Cause Classification: Artifact vs Suppression-Like† (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    # Panel A: Binary confusion matrix
    ax = axes[0]
    cm = clf.confusion_matrix_binary
    im = ax.imshow(cm, cmap="Blues", aspect="auto")
    for i in range(2):
        for j in range(2):
            color = "white" if cm[i, j] > cm.max() * 0.5 else "black"
            ax.text(j, i, str(cm[i, j]), ha="center", va="center",
                    fontsize=18, fontweight="bold", color=color)
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(BINARY_LABELS, fontsize=9)
    ax.set_yticklabels(BINARY_LABELS, fontsize=9)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(f"A) Binary Confusion Matrix\n"
                 f"Acc={clf.accuracy_binary:.3f}, AUROC={clf.auroc_binary:.3f}",
                 fontweight="bold", fontsize=10)

    # Panel B: CV scores
    ax = axes[1]
    cv_bin = clf.cv_scores_binary
    ax.bar(range(len(cv_bin)), cv_bin, color="#4CAF50", alpha=0.8, edgecolor="black")
    mean_cv, lo_cv, hi_cv = bootstrap_ci(cv_bin)
    ax.axhline(mean_cv, color="red", linewidth=2, linestyle="--",
               label=f"Mean: {mean_cv:.3f} [{lo_cv:.3f}, {hi_cv:.3f}]")
    ax.set_xlabel("CV Fold")
    ax.set_ylabel("Accuracy")
    ax.set_title("B) Cross-Validation (Binary)", fontweight="bold")
    ax.set_ylim(0, 1.1)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    # Panel C: Feature importance (top 10)
    ax = axes[2]
    imp = clf.feature_importance
    top_idx = np.argsort(imp)[-10:][::-1]
    top_names = [clf.feature_names[i] for i in top_idx]
    top_vals = imp[top_idx]
    ax.barh(range(len(top_names)), top_vals, color="#FF5722", alpha=0.8)
    ax.set_yticks(range(len(top_names)))
    ax.set_yticklabels(top_names, fontsize=8)
    ax.set_xlabel("Importance (Gini)")
    ax.set_title("C) Feature Importance (Top 10)", fontweight="bold")
    ax.invert_yaxis()
    ax.grid(True, alpha=0.3, axis="x")

    fig.tight_layout(rect=[0, 0.02, 1, 0.91])
    _add_footnote(fig)
    _add_watermark(fig, len(dataset))

    if output_path:
        _save(fig, output_path, dpi)
    return fig


# ═══════════════════════════════════════════════════════════════════
#  Figure 3: Robustness — Domain Shift + Holdout Generalization
# ═══════════════════════════════════════════════════════════════════

def make_fig3_robustness(
    dataset: list[LabeledWindow],
    n_holdout: int = 5,
    fs: float = 20_000.0,
    onset_s: float = 10.0,
    seed: int = 42,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 3: Robustness + holdout generalization.

    Panel A: Detection rate by severity level (stress test)
    Panel B: Holdout generalization (unseen perturbation type)
    Panel C: Detection rate by perturbation type (per-class breakdown)
    """
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.suptitle(
        "Figure 3 — Robustness & Generalization (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    # ── Panel A: Detection rate by severity ──────────────────────
    ax = axes[0]
    severity_groups: dict[float, list[bool]] = {}
    for w in dataset:
        if w.meta.category == "baseline":
            continue
        sev = w.meta.severity
        bl_s = max(1.0, min(w.meta.onset_s - 1.0, onset_s - 1.0))
        det = detect_change(w.data_uv, fs=fs, onset_s_true=w.meta.onset_s,
                            baseline_s=bl_s, window_s=1.0,
                            threshold=5.0, method="cusum")
        severity_groups.setdefault(sev, []).append(det.detected)

    sevs_sorted = sorted(severity_groups.keys())
    rates = [np.mean(severity_groups[s]) for s in sevs_sorted]
    cis = [wilson_ci(sum(severity_groups[s]), len(severity_groups[s])) for s in sevs_sorted]
    yerr_lo = [rates[i] - cis[i][0] for i in range(len(sevs_sorted))]
    yerr_hi = [cis[i][1] - rates[i] for i in range(len(sevs_sorted))]

    ax.bar([f"{s:.1f}" for s in sevs_sorted], rates,
           yerr=[yerr_lo, yerr_hi], capsize=6,
           color="#3F51B5", alpha=0.8, edgecolor="black")
    ax.set_xlabel("Severity Level")
    ax.set_ylabel("Detection Rate")
    ax.set_ylim(0, 1.15)
    ax.set_title("A) Detection Rate by Severity", fontweight="bold")
    for i, (s, r) in enumerate(zip(sevs_sorted, rates)):
        ax.text(i, r + 0.04, f"{r:.2f}", ha="center", fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    # ── Panel B: Holdout generalization ──────────────────────────
    ax = axes[1]
    # Generate holdout windows (never in training)
    holdout_windows = []
    for i in range(n_holdout):
        for sev in [0.3, 0.6, 0.9]:
            hw = generate_partial_suppression(
                severity=sev, seed=seed + 80000 + i * 10 + int(sev * 100),
                onset_s=onset_s, n_channels=16, fs=fs, duration_s=30.0,
            )
            holdout_windows.append(hw)

    # Train classifier on original dataset (no holdout)
    clf = train_cause_classifier(dataset, seed=seed)

    # Test holdout: extract features, predict binary label
    from features import extract_trial_features
    holdout_correct = 0
    holdout_total = len(holdout_windows)
    from sklearn.preprocessing import StandardScaler
    from train_classifier import _build_feature_matrix
    X_train, y_train, _, _ = _build_feature_matrix(dataset)
    scaler = StandardScaler()
    scaler.fit(X_train)

    # Build holdout features
    from sklearn.ensemble import RandomForestClassifier
    clf_bin = RandomForestClassifier(
        n_estimators=100, max_depth=6, min_samples_leaf=3,
        random_state=seed, class_weight="balanced",
    )
    X_scaled = scaler.transform(X_train)
    clf_bin.fit(X_scaled, y_train)

    for hw in holdout_windows:
        _, post = extract_trial_features(
            hw.data_uv, fs=fs, onset_s=hw.meta.onset_s,
            window_s=1.0, n_baseline_windows=3, n_post_windows=5,
        )
        if post.shape[0] > 0:
            feat = np.mean(post, axis=0).reshape(1, -1)
            feat_scaled = scaler.transform(feat)
            pred = clf_bin.predict(feat_scaled)[0]
            # True label is 1 (neurotox-like)
            if pred == 1:
                holdout_correct += 1

    holdout_acc = holdout_correct / max(holdout_total, 1)
    holdout_ci = wilson_ci(holdout_correct, holdout_total)

    bars_holdout = ax.bar(
        ["Training Set\n(Known Types)", "Holdout Set\n(Unseen Type)"],
        [clf.accuracy_binary, holdout_acc],
        yerr=[[0, holdout_acc - holdout_ci[0]],
              [0, holdout_ci[1] - holdout_acc]],
        capsize=6, color=["#4CAF50", "#FF9800"], alpha=0.8, edgecolor="black",
    )
    ax.set_ylabel("Binary Accuracy")
    ax.set_ylim(0, 1.15)
    ax.set_title("B) Holdout Generalization", fontweight="bold")
    for bar, val in zip(bars_holdout, [clf.accuracy_binary, holdout_acc]):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.03,
                f"{val:.2f}", ha="center", fontsize=11, fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    # ── Panel C: Per-type detection rate ─────────────────────────
    ax = axes[2]
    type_groups: dict[str, list[bool]] = {}
    for w in dataset:
        if w.meta.category == "baseline":
            continue
        ptype = w.meta.perturbation_type
        bl_s = max(1.0, min(w.meta.onset_s - 1.0, onset_s - 1.0))
        det = detect_change(w.data_uv, fs=fs, onset_s_true=w.meta.onset_s,
                            baseline_s=bl_s, window_s=1.0,
                            threshold=5.0, method="cusum")
        type_groups.setdefault(ptype, []).append(det.detected)

    type_names = sorted(type_groups.keys())
    type_rates = [np.mean(type_groups[t]) for t in type_names]
    type_labels = [_safe_label(t) for t in type_names]
    type_colors = ["#F44336" if t in NEUROTOX_TYPES else "#2196F3" for t in type_names]

    ax.barh(range(len(type_names)), type_rates, color=type_colors, alpha=0.8)
    ax.set_yticks(range(len(type_names)))
    ax.set_yticklabels(type_labels, fontsize=8)
    ax.set_xlabel("Detection Rate (CUSUM)")
    ax.set_xlim(0, 1.15)
    ax.set_title("C) Per-Type Detection Rate", fontweight="bold")
    for i, r in enumerate(type_rates):
        ax.text(r + 0.02, i, f"{r:.2f}", va="center", fontsize=9)
    ax.invert_yaxis()
    ax.grid(True, alpha=0.3, axis="x")

    fig.tight_layout(rect=[0, 0.02, 1, 0.91])
    _add_footnote(fig)
    _add_watermark(fig, len(dataset))

    if output_path:
        _save(fig, output_path, dpi)
    return fig


# ═══════════════════════════════════════════════════════════════════
#  Figure 4: Ablation — Classical vs Two-Stage
# ═══════════════════════════════════════════════════════════════════

def make_fig4_ablation(
    dataset: list[LabeledWindow],
    onset_s: float = 10.0,
    fs: float = 20_000.0,
    seed: int = 42,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 4: Ablation study — why two-stage matters."""
    abl = run_ablation(dataset, onset_s=onset_s, fs=fs, seed=seed)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle(
        "Figure 4 — Ablation: Classical-Only vs Two-Stage (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    # Panel A: Detection comparison
    ax = axes[0]
    names = ["Z-Score\n(Classical)", "CUSUM\n(Stage 1)"]
    tp = [abl.tp_rate_zscore, abl.tp_rate_cusum]
    fp = [abl.fp_rate_zscore, abl.fp_rate_cusum]
    x = np.arange(len(names))
    w = 0.35
    ax.bar(x - w/2, tp, w, label="TP Rate", color="#4CAF50", alpha=0.8)
    ax.bar(x + w/2, fp, w, label="FP Rate", color="#F44336", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("Rate")
    ax.set_ylim(0, 1.15)
    ax.set_title("A) Detection: TP vs FP", fontweight="bold")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    # Panel B: Latency comparison
    ax = axes[1]
    lat_data = []
    lat_labels = []
    if abl.latency_zscore["n"] > 0:
        lat_data.append(abl.latency_zscore)
        lat_labels.append("Z-Score")
    if abl.latency_cusum["n"] > 0:
        lat_data.append(abl.latency_cusum)
        lat_labels.append("CUSUM")

    if lat_data:
        medians = [d["median"] for d in lat_data]
        iqr_lo = [d["median"] - d["iqr_lo"] for d in lat_data]
        iqr_hi = [d["iqr_hi"] - d["median"] for d in lat_data]
        ax.bar(lat_labels, medians, yerr=[iqr_lo, iqr_hi], capsize=8,
               color=["#FF9800", "#2196F3"][:len(lat_data)], alpha=0.8,
               edgecolor="black")
        for i, m in enumerate(medians):
            ax.text(i, m + max(iqr_hi) * 0.1 + 0.2,
                    f"{m:.1f}s", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylabel("Detection Latency (s)")
    ax.set_title("B) Latency (Median ± IQR)", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    # Panel C: Stage 2 adds value
    ax = axes[2]
    bars = ax.bar(
        ["Majority-Class\nBaseline", "Stage 2\n(RF Classifier)"],
        [abl.accuracy_without_stage2, abl.accuracy_with_stage2],
        color=["#9E9E9E", "#4CAF50"], alpha=0.8, edgecolor="black",
    )
    ax.set_ylabel("Binary Accuracy")
    ax.set_ylim(0, 1.15)
    ax.set_title("C) Classification Value\n(Stage 2 vs No Classification)",
                 fontweight="bold")
    for bar, val in zip(bars, [abl.accuracy_without_stage2, abl.accuracy_with_stage2]):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.03,
                f"{val:.2f}", ha="center", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout(rect=[0, 0.02, 1, 0.93])
    _add_watermark(fig, abl.n_trials)

    if output_path:
        _save(fig, output_path, dpi)
    return fig


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _add_watermark(fig: plt.Figure, n_trials: int) -> None:
    fig.text(0.5, 0.005,
             f"SIMULATION STUDY — Synthetic perturbation data  |  "
             f"N={n_trials} trials  |  † = proxy class (not biological neurotox)",
             ha="center", fontsize=8, style="italic", color="gray")


def _add_footnote(fig: plt.Figure) -> None:
    fig.text(0.01, 0.005,
             "† Suppression-like: electrophysiological proxy signatures, "
             "not validated biological neurotoxicity",
             fontsize=7, style="italic", color="gray")


def _save(fig: plt.Figure, path: str | Path, dpi: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════
#  Figure 5: 3-Way Model Comparison — Classical vs RF vs Deep
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ModelComparisonResult:
    """Precomputed metrics for 3-way model comparison."""
    # Classical CUSUM
    classical_acc: float = 0.0
    classical_bin_acc: float = 0.0
    classical_per_class: dict[str, float] = field(default_factory=dict)
    # RF (Stage 2)
    rf_acc: float = 0.0
    rf_binary_acc: float = 0.0
    rf_per_class: dict[str, float] = field(default_factory=dict)
    # Deep (NeuralQANet)
    deep_acc: float = 0.0
    deep_bin_acc: float = 0.0
    deep_per_class: dict[str, float] = field(default_factory=dict)
    # Meta
    n_val: int = 0
    n_total: int = 0


def compute_model_comparison(
    dataset: list[LabeledWindow],
    onset_s: float = 10.0,
    fs: float = 20_000.0,
    seed: int = 42,
    max_samples: int = 4000,
    deep_epochs: int = 10,
) -> ModelComparisonResult:
    """Compute 3-way comparison metrics: Classical (CUSUM) vs RF vs Deep.

    Training happens here — NOT in the plot function.
    """
    try:
        import torch
        from train_deep import (
            quick_train_and_eval,
            evaluate_model as deep_evaluate,
            TrainConfig,
        )
        from deep_model import NeuralQANet, ModelConfig, CLASS_NAMES as DEEP_CLASSES
        HAS_TORCH = True
    except ImportError:
        HAS_TORCH = False

    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(dataset))
    split = int(0.8 * len(dataset))
    train_wins = [dataset[i] for i in perm[:split]]
    val_wins = [dataset[i] for i in perm[split:]]

    res = ModelComparisonResult(n_val=len(val_wins), n_total=len(dataset))

    # ── Model 1: Classical CUSUM — uses detect_change for binary ──
    classical_correct = 0
    classical_binary_correct = 0
    classical_total = 0
    classical_binary_total = 0
    classical_per_class: dict[str, list[bool]] = {}

    for w in val_wins:
        is_pert = w.meta.category != "baseline"
        bl_s = max(1.0, min(w.meta.onset_s - 1.0, onset_s - 1.0)) if is_pert \
            else w.meta.duration_s / 2
        det = detect_change(
            w.data_uv, fs=fs,
            onset_s_true=w.meta.onset_s if is_pert else w.meta.duration_s,
            baseline_s=bl_s, window_s=1.0, threshold=5.0, method="cusum",
        )
        pred_pert = det.detected
        correct = pred_pert == is_pert
        classical_correct += int(correct)
        classical_total += 1

        pt = w.meta.perturbation_type
        if pt not in classical_per_class:
            classical_per_class[pt] = []
        classical_per_class[pt].append(correct)

        is_neurotox = w.meta.category in ("suppression-like", "neurotox")
        pred_neurotox = False  # CUSUM can't classify cause
        if is_pert or is_neurotox:
            classical_binary_correct += int(pred_neurotox == is_neurotox)
            classical_binary_total += 1

    res.classical_acc = classical_correct / max(classical_total, 1)
    res.classical_bin_acc = classical_binary_correct / max(classical_binary_total, 1)
    res.classical_per_class = {
        k: float(np.mean(v)) for k, v in classical_per_class.items()
    }

    # ── Model 2: RF classifier (Stage 2) ──
    cls_result = train_cause_classifier(dataset, seed=seed)
    res.rf_acc = cls_result.accuracy_multi
    res.rf_binary_acc = cls_result.accuracy_binary
    cm = cls_result.confusion_matrix_multi
    all_types = sorted(set(w.meta.perturbation_type for w in dataset))
    for i, pt in enumerate(all_types):
        if i < cm.shape[0]:
            row_sum = cm[i].sum()
            if row_sum > 0:
                res.rf_per_class[pt] = float(cm[i, i]) / float(row_sum)

    # ── Model 3: Deep (NeuralQANet) ──
    if HAS_TORCH:
        n_ch = dataset[0].data_uv.shape[0] if dataset else 16
        model, train_res, eval_res = quick_train_and_eval(
            dataset=dataset,
            max_samples=max_samples,
            n_epochs=deep_epochs,
            seed=seed,
            verbose=False,
        )
        res.deep_acc = eval_res.accuracy
        res.deep_bin_acc = eval_res.binary_accuracy
        res.deep_per_class = eval_res.per_class_accuracy

    return res


def plot_model_comparison(
    comp: ModelComparisonResult,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 5: Plot 3-way comparison from precomputed ModelComparisonResult.

    NEVER trains. Consumes precomputed results only.
    """
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle(
        "Figure 5 — Model Comparison: Classical vs RF vs Deep (Simulation Study)",
        fontsize=13, fontweight="bold",
    )

    model_names = ["CUSUM\n(Classical)", "RF\n(Stage 2)", "NeuralQANet\n(Deep)"]
    colors_3 = ["#FF9800", "#4CAF50", "#2196F3"]

    # Panel A: Overall accuracy
    ax = axes[0]
    accs = [comp.classical_acc, comp.rf_acc, comp.deep_acc]
    n_val = max(comp.n_val, 1)
    cis = [wilson_ci(int(a * n_val), n_val) for a in accs]

    bars = ax.bar(model_names, accs, color=colors_3, alpha=0.8, edgecolor="black")
    for i, (bar, acc) in enumerate(zip(bars, accs)):
        yerr_lo = acc - cis[i][0]
        yerr_hi = cis[i][1] - acc
        ax.errorbar(bar.get_x() + bar.get_width()/2, acc,
                    yerr=[[yerr_lo], [yerr_hi]], fmt="none", color="black", capsize=8)
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.04,
                f"{acc:.2f}", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylabel("Accuracy")
    ax.set_ylim(0, 1.2)
    ax.set_title("A) Overall Accuracy (95% Wilson CI)", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    # Panel B: Per-class accuracy heatmap
    ax = axes[1]
    all_classes = sorted(set(
        list(comp.classical_per_class.keys())
        + list(comp.rf_per_class.keys())
        + list(comp.deep_per_class.keys())
    ))
    if not all_classes:
        all_classes = ["(no classes)"]

    heat = np.zeros((3, len(all_classes)))
    for j, cls in enumerate(all_classes):
        heat[0, j] = comp.classical_per_class.get(cls, 0.0)
        heat[1, j] = comp.rf_per_class.get(cls, 0.0)
        heat[2, j] = comp.deep_per_class.get(cls, 0.0)

    im = ax.imshow(heat, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
    ax.set_xticks(range(len(all_classes)))
    ax.set_xticklabels([_safe_label(c) for c in all_classes], fontsize=7,
                       rotation=45, ha="right")
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(["CUSUM", "RF", "Deep"])
    ax.set_title("B) Per-Class Accuracy", fontweight="bold")
    for i in range(3):
        for j in range(len(all_classes)):
            ax.text(j, i, f"{heat[i, j]:.2f}", ha="center", va="center",
                    fontsize=7, color="black" if heat[i, j] > 0.4 else "white")
    fig.colorbar(im, ax=ax, shrink=0.8)

    # Panel C: Binary accuracy
    ax = axes[2]
    bin_accs = [comp.classical_bin_acc, comp.rf_binary_acc, comp.deep_bin_acc]
    bars = ax.bar(model_names, bin_accs, color=colors_3, alpha=0.8, edgecolor="black")
    for bar, acc in zip(bars, bin_accs):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.03,
                f"{acc:.2f}", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylabel("Binary Accuracy")
    ax.set_ylim(0, 1.2)
    ax.set_title("C) Artifact vs Suppression-Like†", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout(rect=[0, 0.02, 1, 0.93])
    _add_watermark(fig, comp.n_total)

    if output_path:
        _save(fig, output_path, dpi)
    return fig


def make_fig5_model_comparison(
    dataset: list[LabeledWindow],
    onset_s: float = 10.0,
    fs: float = 20_000.0,
    seed: int = 42,
    max_samples: int = 4000,
    deep_epochs: int = 10,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Fig 5: 3-way comparison — backward-compatible wrapper.

    Computes then plots. For new code, prefer calling
    compute_model_comparison() + plot_model_comparison() separately
    so figures never train.
    """
    comp = compute_model_comparison(
        dataset, onset_s=onset_s, fs=fs, seed=seed,
        max_samples=max_samples, deep_epochs=deep_epochs,
    )
    return plot_model_comparison(comp, output_path=output_path, dpi=dpi)


# ═══════════════════════════════════════════════════════════════════
#  Master report function
# ═══════════════════════════════════════════════════════════════════

def make_eval_report(
    n_per_class: int = 5,
    duration_s: float = 30.0,
    onset_s: float = 10.0,
    seed: int = 42,
    output_dir: str | Path | None = None,
    dpi: int = 200,
) -> dict[str, plt.Figure]:
    """Generate all evaluation figures.

    Returns dict of {name: Figure}. Saves to output_dir if provided.
    """
    n_channels = 16
    fs = 20_000.0

    dataset = generate_dataset(
        n_per_class=n_per_class,
        severities=[0.3, 0.6, 0.9],
        n_channels=n_channels, fs=fs,
        duration_s=duration_s, onset_s=onset_s, seed=seed,
    )

    out = Path(output_dir) if output_dir else None
    figures = {}

    figures["fig1_onset_detection"] = make_fig1_onset_detection(
        dataset, onset_s=onset_s, fs=fs,
        output_path=out / "fig1_onset_detection.png" if out else None, dpi=dpi,
    )

    figures["fig2_cause_classification"] = make_fig2_cause_classification(
        dataset, seed=seed,
        output_path=out / "fig2_cause_classification.png" if out else None, dpi=dpi,
    )

    figures["fig3_robustness"] = make_fig3_robustness(
        dataset, fs=fs, onset_s=onset_s, seed=seed,
        output_path=out / "fig3_robustness.png" if out else None, dpi=dpi,
    )

    figures["fig4_ablation"] = make_fig4_ablation(
        dataset, onset_s=onset_s, fs=fs, seed=seed,
        output_path=out / "fig4_ablation.png" if out else None, dpi=dpi,
    )

    figures["fig5_model_comparison"] = make_fig5_model_comparison(
        dataset, onset_s=onset_s, fs=fs, seed=seed,
        output_path=out / "fig5_model_comparison.png" if out else None, dpi=dpi,
    )

    return figures


# ═══════════════════════════════════════════════════════════════════
#  CLI — One command to generate all poster panels
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Generate BioGENEius evaluation figures (Simulation Study).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python analysis/eval_report.py --out out/figures/ --seed 42\n\n"
            "Produces 5 PNG panels in the output directory:\n"
            "  fig1_onset_detection.png\n"
            "  fig2_cause_classification.png\n"
            "  fig3_robustness.png\n"
            "  fig4_ablation.png\n"
            "  fig5_model_comparison.png\n"
        ),
    )
    ap.add_argument("--out", type=str, required=True,
                    help="Output directory for PNG figures")
    ap.add_argument("--seed", type=int, default=42,
                    help="Random seed for reproducibility (default: 42)")
    ap.add_argument("--n-per-class", type=int, default=5,
                    help="Samples per perturbation class per severity (default: 5)")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="Trial duration in seconds (default: 30)")
    ap.add_argument("--onset", type=float, default=10.0,
                    help="Perturbation onset in seconds (default: 10)")
    ap.add_argument("--dpi", type=int, default=200,
                    help="Figure resolution (default: 200)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating evaluation figures (seed={args.seed}, "
          f"n_per_class={args.n_per_class}, duration={args.duration}s)")
    print(f"Output directory: {out_dir.resolve()}")

    figures = make_eval_report(
        n_per_class=args.n_per_class,
        duration_s=args.duration,
        onset_s=args.onset,
        seed=args.seed,
        output_dir=str(out_dir),
        dpi=args.dpi,
    )

    print(f"\n✓ Generated {len(figures)} figures:")
    for name in sorted(figures.keys()):
        print(f"  {out_dir / (name + '.png')}")

    # Close all figures to free memory
    for fig in figures.values():
        plt.close(fig)

    print(f"\nDone. Seed={args.seed} — results are deterministic.")


if __name__ == "__main__":
    main()
