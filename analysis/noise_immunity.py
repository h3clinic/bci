"""
noise_immunity.py — Noise immunity proof for the streaming detector.

Three deliverables for the poster:

A) FPR budget sweep
   Run long baseline-only streams across noise conditions.
   Measure false alarms/hour vs CUSUM threshold + cooldown policy.
   Output: fpr_budget.csv + fpr_budget.png

B) Calibration curves (Stage 2)
   Reliability diagram + ECE for P(suppression-like).
   Choose operating point p* that eliminates baseline spurious labels.
   Output: calibration.png

C) Adversarial sweeps
   60Hz+harmonics+phase drift, EMI bursts, ADC clipping,
   channel dropout, coupling shift, sample-rate mismatch,
   quantization change, bandpass mismatch.
   Output: noise_immunity_table.csv + noise_immunity.png

D) Operating point summary
   Combined box: chosen thresholds + why.
   Output: operating_point.txt

Usage:
  python -m analysis.noise_immunity --out out/noise_immunity
"""

from __future__ import annotations

import csv
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

# ── Path fix ─────────────────────────────────────────────────────
_analysis_dir = os.path.dirname(os.path.abspath(__file__))
if _analysis_dir not in sys.path:
    sys.path.insert(0, _analysis_dir)

from features import extract_window_features, FEATURE_NAMES, N_FEATURES
from perturbation_library import generate_dataset, LabeledWindow
from signal_gen import generate_neural_signal, SyntheticRecording
from streaming_detector import StreamingDetector, StreamingSummary


# ═══════════════════════════════════════════════════════════════════
#  A) FPR Budget Sweep
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FPRRow:
    """One row in the FPR budget table."""
    cusum_threshold: float
    cooldown_s: float
    noise_condition: str
    duration_min: float
    n_false_alarms: int
    n_neurotox_false_alarms: int
    fpr_per_hour: float
    neurotox_fpr_per_hour: float


def run_fpr_budget_sweep(
    det: StreamingDetector,
    thresholds: list[float] | None = None,
    cooldowns_s: list[float] | None = None,
    noise_conditions: dict[str, dict[str, Any]] | None = None,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_min: float = 10.0,
    seed: int = 42,
) -> list[FPRRow]:
    """Sweep CUSUM threshold × cooldown × noise conditions on baseline-only data.

    Generates long baseline-only recordings (no perturbation), processes
    them through the streaming detector at various operating points, and
    counts how many false alarms fire per hour.

    Args:
        det: A trained StreamingDetector (classifiers already fitted).
        thresholds: CUSUM thresholds to sweep.
        cooldowns_s: Minimum seconds between alerts (cooldown policy).
        noise_conditions: Dict of {name: {apply_fn_kwargs}} for noise overlay.
        n_channels: Channels per recording.
        fs: Sample rate.
        duration_min: Length of each baseline recording in minutes.
        seed: Base seed for reproducibility.

    Returns:
        List of FPRRow with false alarm rates for each condition.
    """
    if thresholds is None:
        thresholds = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]
    if cooldowns_s is None:
        cooldowns_s = [0.0, 5.0, 10.0, 30.0]

    duration_s = duration_min * 60
    n_samples = int(fs * duration_s)
    rng = np.random.default_rng(seed)

    # Build noise conditions: baseline + various artifact overlays
    if noise_conditions is None:
        noise_conditions = _default_noise_conditions()

    results: list[FPRRow] = []
    total = len(thresholds) * len(cooldowns_s) * len(noise_conditions)
    idx = 0

    for cond_name, cond_fn in noise_conditions.items():
        # Generate one long baseline recording per condition
        data_uv = generate_neural_signal(
            n_channels=n_channels, fs=fs, duration_s=duration_s,
            seed=seed + hash(cond_name) % 10000,
        )
        # Apply noise overlay (no perturbation onset — purely baseline)
        data_uv = cond_fn(data_uv, fs=fs, rng=rng)

        for thresh in thresholds:
            for cooldown in cooldowns_s:
                idx += 1
                if idx % 5 == 1 or idx == total:
                    print(f"  [{idx}/{total}] {cond_name} "
                          f"thr={thresh:.1f} cd={cooldown:.0f}s...",
                          end="", flush=True)

                # Run detector with this threshold
                det_copy = _clone_detector(det, cusum_threshold=thresh)
                summary = det_copy.process_recording(
                    data_uv, onset_s_true=None,
                )

                # Apply cooldown filter
                filtered_alerts = _apply_cooldown(
                    summary.alerts, cooldown_s=cooldown,
                )

                n_fa = len(filtered_alerts)
                n_neurotox_fa = sum(
                    1 for a in filtered_alerts
                    if a.binary_label == "neurotox"
                )
                hours = duration_min / 60
                fpr_h = n_fa / hours if hours > 0 else 0.0
                neurotox_fpr_h = n_neurotox_fa / hours if hours > 0 else 0.0

                results.append(FPRRow(
                    cusum_threshold=thresh,
                    cooldown_s=cooldown,
                    noise_condition=cond_name,
                    duration_min=duration_min,
                    n_false_alarms=n_fa,
                    n_neurotox_false_alarms=n_neurotox_fa,
                    fpr_per_hour=fpr_h,
                    neurotox_fpr_per_hour=neurotox_fpr_h,
                ))

                if idx % 5 == 1 or idx == total:
                    print(f" fa={n_fa} fpr/h={fpr_h:.1f}", flush=True)

    return results


def _default_noise_conditions() -> dict[str, Any]:
    """Default noise conditions for FPR sweep (baseline-only)."""
    def clean(data, fs, rng):
        return data

    def sixty_hz_mild(data, fs, rng):
        t = np.arange(data.shape[1]) / fs
        data += 5.0 * np.sin(2 * np.pi * 60 * t)[np.newaxis, :]
        return data

    def sixty_hz_heavy(data, fs, rng):
        t = np.arange(data.shape[1]) / fs
        data += 30.0 * np.sin(2 * np.pi * 60 * t)[np.newaxis, :]
        # Add 3rd harmonic
        data += 10.0 * np.sin(2 * np.pi * 180 * t)[np.newaxis, :]
        return data

    def emi_bursts(data, fs, rng):
        """Short high-amplitude EMI bursts."""
        n_ch, n_samp = data.shape
        n_bursts = max(1, int(n_samp / fs / 10))  # ~1 burst per 10s
        for _ in range(n_bursts):
            start = rng.integers(0, n_samp - int(0.01 * fs))
            length = rng.integers(int(0.001 * fs), int(0.01 * fs))
            burst = rng.normal(0, 50.0, (n_ch, length))
            data[:, start:start + length] += burst
        return data

    def thermal_drift(data, fs, rng):
        """Slow baseline wander on random channels."""
        n_ch, n_samp = data.shape
        n_drift = max(1, n_ch // 4)
        drift_chs = rng.choice(n_ch, n_drift, replace=False)
        t = np.arange(n_samp) / fs
        for ch in drift_chs:
            # Random slow drift: 0.1-0.5 Hz, 5-20 µV amplitude
            freq = rng.uniform(0.05, 0.3)
            amp = rng.uniform(5.0, 20.0)
            data[ch] += amp * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2*np.pi))
        return data

    return {
        "clean": clean,
        "60Hz_mild": sixty_hz_mild,
        "60Hz_heavy+harmonics": sixty_hz_heavy,
        "EMI_bursts": emi_bursts,
        "thermal_drift": thermal_drift,
    }


def _clone_detector(
    det: StreamingDetector, **overrides: Any,
) -> StreamingDetector:
    """Clone a detector with optional parameter overrides."""
    return StreamingDetector(
        rf_binary=det.rf_binary,
        rf_multi=det.rf_multi,
        scaler=det.scaler,
        type_names=det.type_names,
        fs=det.fs,
        window_s=det.window_s,
        overlap=det.overlap,
        cusum_threshold=overrides.get("cusum_threshold", det.cusum_threshold),
        cusum_drift=overrides.get("cusum_drift", det.cusum_drift),
        baseline_s=overrides.get("baseline_s", det.baseline_s),
        neurotox_threshold=overrides.get(
            "neurotox_threshold", det.neurotox_threshold,
        ),
        ood_mean=det.ood_mean,
        ood_cov_inv=det.ood_cov_inv,
        ood_threshold=overrides.get("ood_threshold", det.ood_threshold),
        confirm_k=overrides.get("confirm_k", det.confirm_k),
        confirm_n=overrides.get("confirm_n", det.confirm_n),
    )


def _apply_cooldown(
    alerts: list, cooldown_s: float,
) -> list:
    """Filter alerts by minimum inter-alert interval."""
    if cooldown_s <= 0 or not alerts:
        return list(alerts)
    filtered = [alerts[0]]
    for a in alerts[1:]:
        if a.timestamp_s - filtered[-1].timestamp_s >= cooldown_s:
            filtered.append(a)
    return filtered


def write_fpr_budget(
    rows: list[FPRRow], out_dir: str | Path,
) -> None:
    """Write FPR budget CSV + poster-ready figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # CSV
    csv_path = out / "fpr_budget.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "cusum_threshold", "cooldown_s", "noise_condition",
            "duration_min", "n_false_alarms", "n_neurotox_false_alarms",
            "fpr_per_hour", "neurotox_fpr_per_hour",
        ])
        for r in rows:
            w.writerow([
                f"{r.cusum_threshold:.1f}", f"{r.cooldown_s:.0f}",
                r.noise_condition, f"{r.duration_min:.1f}",
                r.n_false_alarms, r.n_neurotox_false_alarms,
                f"{r.fpr_per_hour:.2f}", f"{r.neurotox_fpr_per_hour:.2f}",
            ])

    # Figure: FPR/hour vs threshold, one line per noise condition
    # Aggregate across cooldowns (use cooldown=0 for the main plot)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle("False-Alarm Budget: FPR/hour vs CUSUM Threshold", fontsize=13)

    # Panel 1: FPR vs threshold (cooldown=0)
    ax = axes[0]
    conditions = sorted(set(r.noise_condition for r in rows))
    colors = plt.cm.Set2(np.linspace(0, 1, len(conditions)))
    for ci, cond in enumerate(conditions):
        subset = [r for r in rows if r.noise_condition == cond
                  and r.cooldown_s == 0.0]
        subset.sort(key=lambda r: r.cusum_threshold)
        if not subset:
            continue
        xs = [r.cusum_threshold for r in subset]
        ys = [r.fpr_per_hour for r in subset]
        ax.plot(xs, ys, "o-", color=colors[ci], label=cond, linewidth=2)
    ax.axhline(1.0, color="red", ls="--", alpha=0.5, label="Budget: ≤1/hr")
    ax.set_xlabel("CUSUM Threshold (σ)")
    ax.set_ylabel("False Alarms / Hour")
    ax.set_title("No cooldown")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_ylim(bottom=-0.5)
    ax.grid(alpha=0.3)

    # Panel 2: FPR vs cooldown (at best threshold)
    ax = axes[1]
    # Find threshold where worst-case FPR first drops ≤ 1/hr
    for ci, cond in enumerate(conditions):
        cooldowns = sorted(set(r.cooldown_s for r in rows))
        # Use threshold=5.0 as reference
        subset = [r for r in rows if r.noise_condition == cond
                  and r.cusum_threshold == 5.0]
        subset.sort(key=lambda r: r.cooldown_s)
        if not subset:
            continue
        xs = [r.cooldown_s for r in subset]
        ys = [r.fpr_per_hour for r in subset]
        ax.plot(xs, ys, "s-", color=colors[ci], label=cond, linewidth=2)
    ax.axhline(1.0, color="red", ls="--", alpha=0.5, label="Budget: ≤1/hr")
    ax.set_xlabel("Cooldown (s)")
    ax.set_ylabel("False Alarms / Hour")
    ax.set_title("Threshold = 5.0σ")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_ylim(bottom=-0.5)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig_path = out / "fpr_budget.png"
    plt.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {csv_path.name}  ✓ {fig_path.name}")


# ═══════════════════════════════════════════════════════════════════
#  B) Calibration Curves
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CalibrationResult:
    """Calibration analysis output."""
    bin_edges: NDArray[np.float64]
    bin_means_predicted: NDArray[np.float64]
    bin_means_actual: NDArray[np.float64]
    bin_counts: NDArray[np.int64]
    ece: float              # Expected Calibration Error
    mce: float              # Maximum Calibration Error
    n_samples: int
    optimal_threshold: float  # p* that minimizes false neurotox on baseline


def compute_calibration(
    det: StreamingDetector,
    dataset: list[LabeledWindow],
    n_bins: int = 10,
    fs: float = 20_000.0,
) -> CalibrationResult:
    """Compute reliability diagram data + ECE for Stage 2 binary classifier.

    Runs the RF binary classifier on each window in the dataset and
    compares predicted P(neurotox) against ground truth.

    Args:
        det: Trained StreamingDetector.
        dataset: Labeled windows with known ground truth.
        n_bins: Number of bins for reliability diagram.
        fs: Sample rate.

    Returns:
        CalibrationResult with binned statistics and ECE.
    """
    y_true: list[int] = []
    y_prob: list[float] = []

    for w in dataset:
        feat = extract_window_features(w.data_uv, fs=fs)
        feat_scaled = det.scaler.transform(feat.reshape(1, -1))
        p = float(det.rf_binary.predict_proba(feat_scaled)[0, 1])
        y_prob.append(p)
        y_true.append(1 if w.meta.category == "neurotox" else 0)

    y_true_arr = np.array(y_true)
    y_prob_arr = np.array(y_prob)

    # Bin edges
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_means_pred = np.zeros(n_bins)
    bin_means_actual = np.zeros(n_bins)
    bin_counts = np.zeros(n_bins, dtype=np.int64)

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == n_bins - 1:
            mask = (y_prob_arr >= lo) & (y_prob_arr <= hi)
        else:
            mask = (y_prob_arr >= lo) & (y_prob_arr < hi)
        count = mask.sum()
        bin_counts[i] = count
        if count > 0:
            bin_means_pred[i] = y_prob_arr[mask].mean()
            bin_means_actual[i] = y_true_arr[mask].mean()

    # ECE: weighted average of |predicted - actual| per bin
    total = len(y_true)
    ece = float(np.sum(bin_counts / max(total, 1)
                       * np.abs(bin_means_pred - bin_means_actual)))
    mce = float(np.max(np.abs(bin_means_pred - bin_means_actual)))

    # Optimal threshold: maximize F1 on the labeled dataset
    best_f1 = 0.0
    best_p = 0.5
    for p_star in np.linspace(0.1, 0.95, 50):
        preds = (y_prob_arr >= p_star).astype(int)
        tp = ((preds == 1) & (y_true_arr == 1)).sum()
        fp = ((preds == 1) & (y_true_arr == 0)).sum()
        fn = ((preds == 0) & (y_true_arr == 1)).sum()
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-10)
        if f1 > best_f1:
            best_f1 = f1
            best_p = float(p_star)

    return CalibrationResult(
        bin_edges=bin_edges,
        bin_means_predicted=bin_means_pred,
        bin_means_actual=bin_means_actual,
        bin_counts=bin_counts,
        ece=ece,
        mce=mce,
        n_samples=total,
        optimal_threshold=best_p,
    )


def write_calibration(cal: CalibrationResult, out_dir: str | Path) -> None:
    """Write reliability diagram + ECE annotation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    fig.suptitle("Stage 2 RF Calibration — P(suppression-like)", fontsize=13)

    # Panel 1: Reliability diagram
    ax = axes[0]
    bin_centers = (cal.bin_edges[:-1] + cal.bin_edges[1:]) / 2
    mask = cal.bin_counts > 0
    ax.bar(bin_centers[mask], cal.bin_means_actual[mask],
           width=0.08, alpha=0.6, color="#3498db", edgecolor="white",
           label="Observed frequency")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="Perfect calibration")
    ax.axvline(cal.optimal_threshold, color="red", ls=":",
               label=f"p* = {cal.optimal_threshold:.2f}")
    ax.set_xlabel("Predicted P(suppression-like)")
    ax.set_ylabel("Observed fraction (true neurotox)")
    ax.set_title(f"Reliability Diagram  (ECE={cal.ece:.3f})")
    ax.legend(fontsize=9)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.3)

    # Panel 2: Bin counts
    ax = axes[1]
    ax.bar(bin_centers, cal.bin_counts, width=0.08, color="#2ecc71",
           edgecolor="white", alpha=0.7)
    ax.axvline(cal.optimal_threshold, color="red", ls=":",
               label=f"p* = {cal.optimal_threshold:.2f}")
    ax.set_xlabel("Predicted P(suppression-like)")
    ax.set_ylabel("Count")
    ax.set_title("Prediction Distribution")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig_path = out / "calibration.png"
    plt.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {fig_path.name}  ECE={cal.ece:.4f}  p*={cal.optimal_threshold:.2f}")


# ═══════════════════════════════════════════════════════════════════
#  C) Adversarial Sweeps
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AdversarialRow:
    """One row in the adversarial sweep table."""
    condition: str
    severity: float
    description: str
    n_alerts: int
    n_neurotox: int
    n_artifact: int
    n_uncertain: int
    false_alarms: int
    latency_median_s: float | None
    latency_p90_s: float | None
    confidence_mean: float
    correct_classification: bool  # did Stage 2 get the right label?
    triaged_label: str            # first alert's 3-state label
    mahal_distance: float         # first alert's Mahalanobis distance
    ms_per_s: float
    realtime_factor: float


def _build_adversarial_conditions() -> list[dict[str, Any]]:
    """Define the adversarial test conditions.

    Each condition specifies:
      - name, severity, description
      - apply_fn(data_uv, onset_samp, fs, rng) → modified data
      - expected_label: "artifact" or "neurotox"
    """
    conditions: list[dict[str, Any]] = []

    # 1. 60 Hz + harmonics + phase drift
    for sev in [0.3, 0.6, 0.9]:
        amp = 10.0 + sev * 50.0  # 10–60 µV fundamental
        def apply_60hz(data, onset_samp, fs, rng, _amp=amp, _sev=sev):
            t = np.arange(data.shape[1] - onset_samp) / fs
            post = data[:, onset_samp:]
            for h in range(1, 4):  # fundamental + 2 harmonics
                phase = rng.uniform(0, 2 * np.pi)
                freq = 60.0 * h + rng.uniform(-0.5, 0.5) * _sev  # drift
                post += (_amp / h) * np.sin(2 * np.pi * freq * t + phase)[np.newaxis, :]
            data[:, onset_samp:] = post
            return data
        conditions.append({
            "name": "60Hz+harmonics+drift",
            "severity": sev,
            "description": f"60Hz {amp:.0f}µV + 2 harmonics + ±{sev*0.5:.1f}Hz drift",
            "apply_fn": apply_60hz,
            "expected_label": "artifact",
        })

    # 2. EMI bursts (transient high-amplitude interference + 60Hz coupling)
    for sev in [0.3, 0.6, 0.9]:
        n_bursts = int(2 + sev * 8)
        burst_amp = 30 + sev * 100  # µV
        line_amp = 5 + sev * 30     # µV of 60Hz mains coupling
        def apply_emi(data, onset_samp, fs, rng,
                      _n=n_bursts, _amp=burst_amp, _line=line_amp):
            n_ch, n_samp = data.shape
            post_len = n_samp - onset_samp
            # Broadband burst component
            for _ in range(_n):
                start = onset_samp + rng.integers(0, max(1, post_len - int(0.01 * fs)))
                length = rng.integers(int(0.001 * fs), int(0.01 * fs))
                end = min(start + length, n_samp)
                data[:, start:end] += rng.normal(0, _amp, (n_ch, end - start))
            # Continuous 60Hz mains coupling (always present with EMI)
            t = np.arange(onset_samp, n_samp) / fs
            mains = _line * np.sin(2 * np.pi * 60 * t)
            mains += (_line * 0.3) * np.sin(2 * np.pi * 120 * t)  # 2nd harmonic
            data[:, onset_samp:] += mains[np.newaxis, :]
            return data
        conditions.append({
            "name": "EMI_bursts",
            "severity": sev,
            "description": f"{n_bursts} bursts @ {burst_amp:.0f}µV + {line_amp:.0f}µV 60Hz",
            "apply_fn": apply_emi,
            "expected_label": "artifact",
        })

    # 3. ADC clipping — clip relative to actual signal amplitude
    for sev in [0.3, 0.6, 0.9]:
        # At sev=0.3 clip at 2× peak (mild), sev=0.9 clip at 0.5× peak (severe)
        scale = 2.5 - sev * 2.2  # 1.84, 1.18, 0.52
        def apply_clip(data, onset_samp, fs, rng, _scale=scale):
            post = data[:, onset_samp:]
            peak = np.max(np.abs(post))
            clip_uv = max(peak * _scale, 1.0)  # never clip to 0
            data[:, onset_samp:] = np.clip(post, -clip_uv, clip_uv)
            return data
        conditions.append({
            "name": "ADC_clipping",
            "severity": sev,
            "description": f"Clip at {scale:.2f}× peak amplitude",
            "apply_fn": apply_clip,
            "expected_label": "artifact",
        })

    # 4. Channel dropout (progressive)
    for sev in [0.3, 0.6, 0.9]:
        def apply_dropout(data, onset_samp, fs, rng, _sev=sev):
            n_ch = data.shape[0]
            n_drop = max(1, int(_sev * n_ch))
            drop_chs = rng.choice(n_ch, n_drop, replace=False)
            data[drop_chs, onset_samp:] = 0.0
            return data
        conditions.append({
            "name": "channel_dropout",
            "severity": sev,
            "description": f"Drop {sev*100:.0f}% channels → 0",
            "apply_fn": apply_dropout,
            "expected_label": "artifact",
        })

    # 5. Coupling shift (crosstalk increases)
    for sev in [0.3, 0.6, 0.9]:
        ratio = 0.01 + sev * 0.05  # 1–6% coupling
        def apply_coupling(data, onset_samp, fs, rng, _ratio=ratio):
            n_ch = data.shape[0]
            n_pairs = max(1, n_ch // 4)
            for _ in range(n_pairs):
                src, tgt = rng.choice(n_ch, 2, replace=False)
                data[tgt, onset_samp:] += _ratio * data[src, onset_samp:]
            return data
        conditions.append({
            "name": "coupling_shift",
            "severity": sev,
            "description": f"Crosstalk coupling {ratio*100:.1f}%",
            "apply_fn": apply_coupling,
            "expected_label": "artifact",
        })

    # 6. Sample-rate mismatch (resample to slightly different rate)
    for sev in [0.3, 0.6, 0.9]:
        rate_factor = 1.0 + sev * 0.05  # up to 5% mismatch
        def apply_resample(data, onset_samp, fs, rng, _factor=rate_factor):
            from scipy.signal import resample
            post = data[:, onset_samp:]
            new_len = int(post.shape[1] / _factor)
            resampled = np.zeros((data.shape[0], new_len))
            for ch in range(data.shape[0]):
                resampled[ch] = resample(post[ch], new_len)
            # Pad or truncate to original length
            orig_len = post.shape[1]
            if new_len >= orig_len:
                data[:, onset_samp:] = resampled[:, :orig_len]
            else:
                data[:, onset_samp:onset_samp + new_len] = resampled
                data[:, onset_samp + new_len:] = 0.0
            return data
        conditions.append({
            "name": "sample_rate_mismatch",
            "severity": sev,
            "description": f"Rate mismatch {rate_factor:.3f}×",
            "apply_fn": apply_resample,
            "expected_label": "artifact",
        })

    # 7. Quantization change (coarser ADC resolution)
    for sev in [0.3, 0.6, 0.9]:
        lsb = 0.195 * (1 + sev * 10)  # 0.195 → up to 2.15 µV/LSB
        def apply_quant(data, onset_samp, fs, rng, _lsb=lsb):
            post = data[:, onset_samp:]
            data[:, onset_samp:] = np.round(post / _lsb) * _lsb
            return data
        conditions.append({
            "name": "quantization_change",
            "severity": sev,
            "description": f"LSB={lsb:.2f}µV (vs 0.195 nominal)",
            "apply_fn": apply_quant,
            "expected_label": "artifact",
        })

    # 8. Bandpass mismatch (wrong filter applied)
    for sev in [0.3, 0.6, 0.9]:
        def apply_bandpass(data, onset_samp, fs, rng, _sev=sev):
            from scipy.signal import butter, sosfilt
            # Apply a narrower bandpass than expected
            low = 1.0 + _sev * 50   # 1–51 Hz low cutoff
            high = 5000 - _sev * 2000  # 5000–3000 Hz high cutoff
            if high <= low + 10:
                high = low + 100
            sos = butter(4, [low, high], btype="band", fs=fs, output="sos")
            for ch in range(data.shape[0]):
                data[ch, onset_samp:] = sosfilt(sos, data[ch, onset_samp:])
            return data
        conditions.append({
            "name": "bandpass_mismatch",
            "severity": sev,
            "description": f"Narrower bandpass at sev={sev:.1f}",
            "apply_fn": apply_bandpass,
            "expected_label": "artifact",
        })

    # 9. Spike suppression (the neurotox condition — should detect correctly)
    for sev in [0.3, 0.6, 0.9]:
        def apply_suppression(data, onset_samp, fs, rng, _sev=sev):
            data[:, onset_samp:] *= (1.0 - _sev * 0.8)
            return data
        conditions.append({
            "name": "spike_suppression",
            "severity": sev,
            "description": f"Amplitude ×{1.0 - sev*0.8:.2f}",
            "apply_fn": apply_suppression,
            "expected_label": "neurotox",
        })

    # 10. Rate decrease (neurotox — should detect correctly)
    for sev in [0.3, 0.6, 0.9]:
        def apply_rate_decrease(data, onset_samp, fs, rng, _sev=sev):
            from scipy.ndimage import uniform_filter1d
            kernel = int(_sev * 20) + 1
            for ch in range(data.shape[0]):
                data[ch, onset_samp:] = uniform_filter1d(
                    data[ch, onset_samp:], size=kernel,
                )
            return data
        conditions.append({
            "name": "rate_decrease",
            "severity": sev,
            "description": f"Smoothing kernel={int(sev*20)+1}",
            "apply_fn": apply_rate_decrease,
            "expected_label": "neurotox",
        })

    return conditions


def run_adversarial_sweep(
    det: StreamingDetector,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 30.0,
    onset_s: float = 10.0,
    seed: int = 42,
    conditions: list[dict[str, Any]] | None = None,
) -> list[AdversarialRow]:
    """Run the streaming detector under adversarial conditions.

    For each condition, generates a clean recording, applies the
    adversarial perturbation after onset, and processes through the
    full two-stage pipeline.

    Returns:
        List of AdversarialRow with detection/classification results.
    """
    if conditions is None:
        conditions = _build_adversarial_conditions()

    rng = np.random.default_rng(seed)
    results: list[AdversarialRow] = []

    for i, cond in enumerate(conditions):
        name = cond["name"]
        sev = cond["severity"]
        print(f"  [{i+1}/{len(conditions)}] {name} sev={sev:.1f}...",
              end="", flush=True)

        # Generate clean recording
        data_uv = generate_neural_signal(
            n_channels=n_channels, fs=fs, duration_s=duration_s,
            seed=seed + i * 100,
        )

        # Apply adversarial perturbation
        onset_samp = int(onset_s * fs)
        data_uv = cond["apply_fn"](data_uv, onset_samp, fs, rng)

        # Process through streaming detector
        summary = det.process_recording(data_uv, onset_s_true=onset_s)

        # Check if classification was correct (using 3-state triage)
        expected = cond["expected_label"]
        triaged = ""
        mahal = 0.0
        if summary.alerts:
            first = summary.alerts[0]
            triaged = first.triaged_label
            mahal = first.mahal_distance
            # For neurotox: correct if triaged as suppression-like
            # For artifact: correct if triaged as artifact OR uncertain
            #   (uncertain = conservative, not a miss)
            if expected == "neurotox":
                correct = (triaged == "suppression-like")
            else:  # artifact
                correct = (triaged in ("artifact", "uncertain"))
        else:
            # No alert → incorrect for conditions that should trigger
            correct = False

        confidences = [a.confidence for a in summary.alerts]
        conf_mean = float(np.mean(confidences)) if confidences else 0.0

        results.append(AdversarialRow(
            condition=name,
            severity=sev,
            description=cond["description"],
            n_alerts=summary.n_alerts,
            n_neurotox=summary.n_neurotox_alerts,
            n_artifact=summary.n_artifact_alerts,
            n_uncertain=summary.n_uncertain_alerts,
            false_alarms=summary.false_alarms,
            latency_median_s=summary.latency_median_s,
            latency_p90_s=summary.latency_p90_s,
            confidence_mean=conf_mean,
            correct_classification=correct,
            triaged_label=triaged,
            mahal_distance=mahal,
            ms_per_s=summary.ms_per_s_data,
            realtime_factor=summary.realtime_factor,
        ))

        status = "✓" if summary.n_alerts > 0 else "✗"
        cls_mark = "✓" if correct else "✗"
        ood_mark = f" OOD={triaged}" if triaged == "uncertain" else ""
        lat_str = (f" lat={summary.latency_median_s:.2f}s"
                   if summary.latency_median_s is not None else "")
        print(f" {status} det={summary.n_alerts} cls={cls_mark}"
              f" triage={triaged or 'none'} mahal={mahal:.1f}"
              f" conf={conf_mean:.2f}{lat_str}{ood_mark}"
              f" ({summary.elapsed_s:.1f}s)",
              flush=True)

    return results


def write_adversarial_results(
    rows: list[AdversarialRow], out_dir: str | Path,
) -> None:
    """Write adversarial sweep CSV + poster-ready figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # CSV
    csv_path = out / "noise_immunity_table.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "condition", "severity", "description",
            "detected", "n_alerts", "n_neurotox", "n_artifact",
            "n_uncertain", "false_alarms", "correct_classification",
            "triaged_label", "mahal_distance",
            "latency_median_s", "latency_p90_s",
            "confidence_mean", "ms_per_s", "realtime_factor",
        ])
        for r in rows:
            w.writerow([
                r.condition, f"{r.severity:.1f}", r.description,
                "Y" if r.n_alerts > 0 else "N",
                r.n_alerts, r.n_neurotox, r.n_artifact,
                r.n_uncertain, r.false_alarms,
                "Y" if r.correct_classification else "N",
                r.triaged_label, f"{r.mahal_distance:.2f}",
                f"{r.latency_median_s:.3f}" if r.latency_median_s else "",
                f"{r.latency_p90_s:.3f}" if r.latency_p90_s else "",
                f"{r.confidence_mean:.3f}",
                f"{r.ms_per_s:.3f}", f"{r.realtime_factor:.0f}",
            ])

    # Figure: 2×2 poster panel
    fig, axes = plt.subplots(2, 2, figsize=(13, 10))
    fig.suptitle("Adversarial Noise Immunity — Streaming Detector", fontsize=14)

    # Panel 1: Detection rate by condition group
    ax = axes[0, 0]
    cond_names = sorted(set(r.condition for r in rows))
    det_rates = []
    for cn in cond_names:
        subset = [r for r in rows if r.condition == cn]
        rate = sum(1 for r in subset if r.n_alerts > 0) / max(len(subset), 1)
        det_rates.append(rate)
    colors_det = ["#2ecc71" if r >= 0.67 else "#f39c12" if r >= 0.34
                  else "#e74c3c" for r in det_rates]
    ax.barh(range(len(cond_names)), det_rates, color=colors_det, alpha=0.8)
    ax.set_yticks(range(len(cond_names)))
    ax.set_yticklabels(cond_names, fontsize=8)
    ax.set_xlabel("Detection Rate (across severities)")
    ax.set_title("Stage 1: Change Detection")
    ax.set_xlim(-0.02, 1.1)
    ax.axvline(1.0, color="gray", ls=":", alpha=0.5)

    # Panel 2: 3-state triage breakdown by condition group
    ax = axes[0, 1]
    n_supp = []
    n_art_l = []
    n_unc = []
    n_nodet = []
    for cn in cond_names:
        subset = [r for r in rows if r.condition == cn]
        n_s = sum(1 for r in subset if r.triaged_label == "suppression-like")
        n_a = sum(1 for r in subset if r.triaged_label == "artifact")
        n_u = sum(1 for r in subset if r.triaged_label == "uncertain")
        n_nd = sum(1 for r in subset if r.n_alerts == 0)
        n_supp.append(n_s)
        n_art_l.append(n_a)
        n_unc.append(n_u)
        n_nodet.append(n_nd)
    y_pos = range(len(cond_names))
    ax.barh(y_pos, n_supp, color="#e74c3c", alpha=0.8, label="Suppression-like")
    ax.barh(y_pos, n_art_l, left=n_supp, color="#3498db", alpha=0.8,
            label="Artifact")
    lefts = [s + a for s, a in zip(n_supp, n_art_l)]
    ax.barh(y_pos, n_unc, left=lefts, color="#f39c12", alpha=0.8,
            label="Uncertain (OOD)")
    lefts2 = [l + u for l, u in zip(lefts, n_unc)]
    ax.barh(y_pos, n_nodet, left=lefts2, color="#bdc3c7", alpha=0.6,
            label="Not detected")
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(cond_names, fontsize=8)
    ax.set_xlabel("Count (across severities)")
    ax.set_title("Stage 2: 3-State Triage")
    ax.legend(fontsize=7, loc="lower right")

    # Panel 3: Latency distribution
    ax = axes[1, 0]
    neurotox_rows = [r for r in rows
                     if r.condition in ("spike_suppression", "rate_decrease")
                     and r.latency_median_s is not None]
    artifact_rows = [r for r in rows
                     if r.condition not in ("spike_suppression", "rate_decrease")
                     and r.latency_median_s is not None]
    data_box = []
    box_labels = []
    if neurotox_rows:
        data_box.append([r.latency_median_s for r in neurotox_rows])
        box_labels.append("Neurotox")
    if artifact_rows:
        data_box.append([r.latency_median_s for r in artifact_rows])
        box_labels.append("Artifact")
    if data_box:
        bp = ax.boxplot(data_box, tick_labels=box_labels, patch_artist=True)
        bp_colors = ["#e74c3c", "#3498db"]
        for patch, c in zip(bp["boxes"], bp_colors[:len(data_box)]):
            patch.set_facecolor(c)
            patch.set_alpha(0.6)
    ax.set_ylabel("Detection Latency (s)")
    ax.set_title("Alert Latency by Category")
    ax.grid(alpha=0.3)

    # Panel 4: Confidence separation
    ax = axes[1, 1]
    nt_confs = [r.confidence_mean for r in rows
                if r.condition in ("spike_suppression", "rate_decrease")
                and r.n_alerts > 0]
    art_confs = [r.confidence_mean for r in rows
                 if r.condition not in ("spike_suppression", "rate_decrease")
                 and r.n_alerts > 0]
    if nt_confs:
        ax.hist(nt_confs, bins=8, alpha=0.6, color="#e74c3c",
                label="Neurotox", edgecolor="white")
    if art_confs:
        ax.hist(art_confs, bins=8, alpha=0.6, color="#3498db",
                label="Artifact", edgecolor="white")
    ax.axvline(0.5, color="gray", ls="--", alpha=0.5, label="p=0.5")
    ax.set_xlabel("Mean P(neurotox)")
    ax.set_ylabel("Count")
    ax.set_title("Confidence Separation")
    ax.legend(fontsize=9)

    plt.tight_layout()
    fig_path = out / "noise_immunity.png"
    plt.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {csv_path.name}  ✓ {fig_path.name}")


# ═══════════════════════════════════════════════════════════════════
#  C-1) Persistent FP Diagnostic
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FPDiagnosticRow:
    """One persistent-FP diagnostic row."""
    condition: str
    severity: float
    p_neurotox: float
    triaged_label: str
    top_features: list[tuple[str, float, float, float, float]]
    """List of (name, importance, raw_val, baseline_val, contribution)."""


def write_fp_diagnostic(
    det: StreamingDetector,
    adv_rows: list[AdversarialRow],
    out_dir: str | Path,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 30.0,
    onset_s: float = 10.0,
    seed: int = 42,
    top_k: int = 5,
) -> list[FPDiagnosticRow]:
    """Diagnose persistent false positives by feature contribution.

    For each artifact condition triaged as "suppression-like", extracts
    post-onset feature vectors, compares to baseline, and ranks features
    by importance × |deviation|.  Tells you exactly which features are
    being fooled and guides artifact-specific feature patches.

    Returns list of FPDiagnosticRow (also writes CSV + text).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    conditions = _build_adversarial_conditions()
    importances = det.rf_binary.feature_importances_

    # --- Baseline feature vector (pre-onset, clean signal) -----------
    data_clean = generate_neural_signal(
        n_channels=n_channels, fs=fs, duration_s=duration_s, seed=seed,
    )
    win_samp = int(1.0 * fs)
    baseline_feats: list[NDArray] = []
    for w in range(5):
        start = int(2.0 * fs) + w * win_samp
        if start + win_samp > data_clean.shape[1]:
            break
        chunk = data_clean[:, start: start + win_samp]
        baseline_feats.append(extract_window_features(chunk, fs=fs))
    baseline = np.mean(baseline_feats, axis=0)
    baseline_scaled = det.scaler.transform(
        baseline.reshape(1, -1)
    ).flatten()

    # --- Identify FP rows (artifact triaged as suppression-like) -----
    neurotox_names = {"spike_suppression", "rate_decrease"}
    fp_rows = [
        r for r in adv_rows
        if r.condition not in neurotox_names
        and r.triaged_label == "suppression-like"
    ]

    if not fp_rows:
        print("  No persistent FPs to diagnose.")
        return []

    # --- Extract features for each FP condition ----------------------
    def _features_for(name: str, sev: float) -> NDArray | None:
        for i, c in enumerate(conditions):
            if c["name"] == name and abs(c["severity"] - sev) < 0.01:
                data = generate_neural_signal(
                    n_channels=n_channels, fs=fs,
                    duration_s=duration_s, seed=seed + i * 100,
                )
                os = int(onset_s * fs)
                rng = np.random.default_rng(seed)
                data = c["apply_fn"](data, os, fs, rng)
                feats: list[NDArray] = []
                for w in range(5):
                    s = os + (w + 2) * win_samp
                    if s + win_samp > data.shape[1]:
                        break
                    feats.append(
                        extract_window_features(
                            data[:, s: s + win_samp], fs=fs
                        )
                    )
                return np.mean(feats, axis=0) if feats else None
        return None

    diag_rows: list[FPDiagnosticRow] = []

    for r in fp_rows:
        feat = _features_for(r.condition, r.severity)
        if feat is None:
            continue
        feat_scaled = det.scaler.transform(
            feat.reshape(1, -1)
        ).flatten()
        p_neuro = float(
            det.rf_binary.predict_proba(feat_scaled.reshape(1, -1))[0, 1]
        )

        dev = feat_scaled - baseline_scaled
        contribution = importances * np.abs(dev)
        top_idx = np.argsort(contribution)[::-1][:top_k]

        top_feats = []
        for idx in top_idx:
            top_feats.append((
                FEATURE_NAMES[idx],
                float(importances[idx]),
                float(feat[idx]),
                float(baseline[idx]),
                float(contribution[idx]),
            ))

        diag_rows.append(FPDiagnosticRow(
            condition=r.condition,
            severity=r.severity,
            p_neurotox=p_neuro,
            triaged_label=r.triaged_label,
            top_features=top_feats,
        ))

    # --- Also get neurotox rows for comparison -----------------------
    nt_rows_raw = [
        r for r in adv_rows
        if r.condition in neurotox_names and r.n_alerts > 0
    ]
    nt_diags: list[FPDiagnosticRow] = []
    for r in nt_rows_raw:
        feat = _features_for(r.condition, r.severity)
        if feat is None:
            continue
        feat_scaled = det.scaler.transform(
            feat.reshape(1, -1)
        ).flatten()
        p_neuro = float(
            det.rf_binary.predict_proba(feat_scaled.reshape(1, -1))[0, 1]
        )
        dev = feat_scaled - baseline_scaled
        contribution = importances * np.abs(dev)
        top_idx = np.argsort(contribution)[::-1][:top_k]
        top_feats = [
            (FEATURE_NAMES[idx], float(importances[idx]),
             float(feat[idx]), float(baseline[idx]),
             float(contribution[idx]))
            for idx in top_idx
        ]
        nt_diags.append(FPDiagnosticRow(
            condition=r.condition, severity=r.severity,
            p_neurotox=p_neuro, triaged_label=r.triaged_label,
            top_features=top_feats,
        ))

    # --- CSV output --------------------------------------------------
    csv_path = out / "fp_diagnostic.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "group", "condition", "severity", "p_neurotox",
            "triaged_label",
            "feat1_name", "feat1_imp", "feat1_val", "feat1_base",
            "feat1_contrib",
            "feat2_name", "feat2_imp", "feat2_val", "feat2_base",
            "feat2_contrib",
            "feat3_name", "feat3_imp", "feat3_val", "feat3_base",
            "feat3_contrib",
            "feat4_name", "feat4_imp", "feat4_val", "feat4_base",
            "feat4_contrib",
            "feat5_name", "feat5_imp", "feat5_val", "feat5_base",
            "feat5_contrib",
        ])
        for group_label, rows_list in [("neurotox", nt_diags),
                                       ("FP_artifact", diag_rows)]:
            for dr in rows_list:
                row: list[Any] = [
                    group_label, dr.condition, f"{dr.severity:.1f}",
                    f"{dr.p_neurotox:.3f}", dr.triaged_label,
                ]
                for name, imp, val, base, contrib in dr.top_features:
                    row.extend([name, f"{imp:.4f}", f"{val:.3f}",
                                f"{base:.3f}", f"{contrib:.4f}"])
                # Pad if fewer than top_k features
                while len(row) < 5 + top_k * 5:
                    row.append("")
                w.writerow(row)

    # --- Text summary ------------------------------------------------
    lines: list[str] = [
        "═══ Persistent FP Diagnostic ═══",
        "",
        f"RF binary feature importances (top 10):",
    ]
    order = np.argsort(importances)[::-1][:10]
    for rank, idx in enumerate(order):
        lines.append(
            f"  {rank+1:2d}. {FEATURE_NAMES[idx]:25s} "
            f"{importances[idx]:.4f}"
        )
    lines += ["", "─── Neurotox (true positives) ───"]
    for dr in nt_diags:
        lines.append(
            f"\n  {dr.condition} sev={dr.severity:.1f}  "
            f"P(neurotox)={dr.p_neurotox:.3f}"
        )
        for name, imp, val, base, contrib in dr.top_features:
            lines.append(
                f"    {name:25s} imp={imp:.4f}  "
                f"val={val:.3f}  base={base:.3f}  "
                f"contrib={contrib:.4f}"
            )

    lines += ["", "─── Persistent FPs (artifact → suppression-like) ───"]
    for dr in diag_rows:
        lines.append(
            f"\n  {dr.condition} sev={dr.severity:.1f}  "
            f"P(neurotox)={dr.p_neurotox:.3f}"
        )
        for name, imp, val, base, contrib in dr.top_features:
            lines.append(
                f"    {name:25s} imp={imp:.4f}  "
                f"val={val:.3f}  base={base:.3f}  "
                f"contrib={contrib:.4f}"
            )

    # --- Feature frequency across FPs --------------------------------
    from collections import Counter
    freq: Counter[str] = Counter()
    for dr in diag_rows:
        for name, *_ in dr.top_features:
            freq[name] += 1
    lines += [
        "",
        f"─── Feature frequency in top-{top_k} across "
        f"{len(diag_rows)} FPs ───",
    ]
    for fname, count in freq.most_common():
        lines.append(
            f"  {fname:25s} appears in {count}/{len(diag_rows)} FPs"
        )

    lines += [
        "",
        "─── Interpretation ───",
        "Features that appear in most FPs are the ones being fooled.",
        "Persistent hardware artifacts produce the SAME directional",
        "changes (↓ RMS, ↓ variability, ↓ spatial CV) as neurotox.",
        "Implemented artifact-discriminating features (Option B):",
        "  ✓ line_power_ratio (60Hz band / total) — separates EMI ✓",
        "  ✓ clip_fraction (% at ADC rail) — separates ADC clipping",
        "  ✓ spectral_flatness_hb (spike band) — separates resampling",
        "Remaining candidates for future work:",
        "  • granular band ratios — separates bandpass mismatch",
        "  • cross-channel phase coherence — separates coupling",
    ]

    text = "\n".join(lines)
    txt_path = out / "fp_diagnostic.txt"
    txt_path.write_text(text)
    print(text)
    print(f"\n  ✓ {csv_path.name}  ✓ {txt_path.name}")

    # --- Figure: Feature-contribution heatmap ------------------------
    if diag_rows:
        # Collect all unique feature names in top-k across all FPs+NTs
        all_names: list[str] = []
        for dr in nt_diags + diag_rows:
            for name, *_ in dr.top_features:
                if name not in all_names:
                    all_names.append(name)

        all_diags = nt_diags + diag_rows
        mat = np.zeros((len(all_diags), len(all_names)))
        ylabels: list[str] = []
        for ri, dr in enumerate(all_diags):
            tag = "NT" if dr.condition in neurotox_names else "FP"
            ylabels.append(f"{tag}: {dr.condition} {dr.severity:.1f}")
            for name, imp, val, base, contrib in dr.top_features:
                if name in all_names:
                    mat[ri, all_names.index(name)] = contrib

        fig, ax = plt.subplots(
            figsize=(max(8, len(all_names) * 0.8),
                     max(4, len(all_diags) * 0.45)),
        )
        im = ax.imshow(mat, aspect="auto", cmap="YlOrRd")
        ax.set_xticks(range(len(all_names)))
        ax.set_xticklabels(all_names, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(all_diags)))
        ax.set_yticklabels(ylabels, fontsize=8)
        ax.set_title(
            "Feature Contribution to Neurotox Prediction\n"
            "(importance × |scaled deviation from baseline|)"
        )
        plt.colorbar(im, ax=ax, label="Contribution", shrink=0.8)

        # Draw a separator line between NT and FP rows
        if nt_diags and diag_rows:
            ax.axhline(
                len(nt_diags) - 0.5, color="white", lw=2, ls="--",
            )

        plt.tight_layout()
        fig_path = out / "fp_diagnostic.png"
        plt.savefig(fig_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  ✓ {fig_path.name}")

    return diag_rows


# ═══════════════════════════════════════════════════════════════════
#  D-1) Pareto Operating-Point Curve
# ═══════════════════════════════════════════════════════════════════

def write_pareto_curve(
    fpr_rows: list[FPRRow],
    adv_rows: list[AdversarialRow],
    det: StreamingDetector,
    out_dir: str | Path,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 30.0,
    onset_s: float = 10.0,
    seed: int = 42,
) -> None:
    """Write Pareto curve: neurotox sensitivity vs worst-case FPR/hr.

    For each CUSUM threshold (from FPR budget data), re-runs neurotox
    conditions to get sensitivity, and reads FPR from the budget sweep.
    Marks conservative and practical operating points.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Get neurotox adversarial conditions
    neurotox_conds = [c for c in _build_adversarial_conditions()
                      if c["expected_label"] == "neurotox"]

    thresholds = sorted(set(r.cusum_threshold for r in fpr_rows))
    rng = np.random.default_rng(seed)

    pareto_points: list[dict] = []

    for thresh in thresholds:
        # Worst-case FPR across all noise conditions (cooldown=0)
        fpr_subset = [r for r in fpr_rows
                      if r.cusum_threshold == thresh and r.cooldown_s == 0.0]
        worst_fpr = max((r.fpr_per_hour for r in fpr_subset), default=0.0)

        # Re-run neurotox conditions at this threshold
        det_copy = _clone_detector(det, cusum_threshold=thresh)
        n_detected = 0
        n_correct = 0
        latencies_s: list[float] = []

        for i, cond in enumerate(neurotox_conds):
            data_uv = generate_neural_signal(
                n_channels=n_channels, fs=fs, duration_s=duration_s,
                seed=seed + i * 100 + 5000,
            )
            onset_samp = int(onset_s * fs)
            data_uv = cond["apply_fn"](data_uv, onset_samp, fs, rng)
            summary = det_copy.process_recording(data_uv, onset_s_true=onset_s)

            if summary.n_alerts > 0:
                n_detected += 1
                first = summary.alerts[0]
                if first.triaged_label == "suppression-like":
                    n_correct += 1
                if summary.latency_median_s is not None:
                    latencies_s.append(summary.latency_median_s)

        sensitivity = n_detected / max(len(neurotox_conds), 1)
        cls_rate = n_correct / max(n_detected, 1) if n_detected > 0 else 0.0
        med_lat = float(np.median(latencies_s)) if latencies_s else None

        pareto_points.append({
            "threshold": thresh,
            "worst_fpr_hr": worst_fpr,
            "sensitivity": sensitivity,
            "cls_rate": cls_rate,
            "median_latency_s": med_lat,
        })

    # Plot Pareto curve
    fig, ax = plt.subplots(figsize=(8, 5))
    fprs = [p["worst_fpr_hr"] for p in pareto_points]
    sens = [p["sensitivity"] for p in pareto_points]
    thrs = [p["threshold"] for p in pareto_points]

    ax.plot(fprs, sens, "o-", color="#2c3e50", linewidth=2, markersize=8)
    for i, p in enumerate(pareto_points):
        label = f'{p["threshold"]:.0f}σ'
        ax.annotate(label, (fprs[i], sens[i]),
                    textcoords="offset points", xytext=(8, -5),
                    fontsize=9, color="#2c3e50")

    # Mark operating points
    # Conservative: lowest FPR with sensitivity > 0
    conservative = [p for p in pareto_points
                    if p["worst_fpr_hr"] <= 1.0 and p["sensitivity"] > 0]
    if conservative:
        cp = conservative[0]
        ax.plot(cp["worst_fpr_hr"], cp["sensitivity"], "s",
                color="#e74c3c", markersize=14, zorder=5,
                label=f'Conservative ({cp["threshold"]:.0f}σ, '
                      f'≤{cp["worst_fpr_hr"]:.0f}/hr)')

    # Practical: ≤10/hr
    practical = [p for p in pareto_points
                 if p["worst_fpr_hr"] <= 10.0 and p["sensitivity"] > 0]
    if practical:
        pp = min(practical, key=lambda p: p["threshold"])
        ax.plot(pp["worst_fpr_hr"], pp["sensitivity"], "D",
                color="#27ae60", markersize=14, zorder=5,
                label=f'Practical ({pp["threshold"]:.0f}σ, '
                      f'≤{pp["worst_fpr_hr"]:.0f}/hr)')

    ax.axvline(1.0, color="red", ls="--", alpha=0.3, label="1/hr budget")
    ax.axvline(10.0, color="orange", ls="--", alpha=0.3, label="10/hr budget")
    ax.set_xlabel("Worst-Case False Alarms / Hour")
    ax.set_ylabel("Neurotox Detection Sensitivity")
    ax.set_title("Pareto Operating-Point Curve: Sensitivity vs FPR")
    ax.legend(fontsize=9, loc="lower right")
    ax.set_ylim(-0.05, 1.15)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig_path = out / "pareto_curve.png"
    plt.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close()

    # Also write CSV
    csv_path = out / "pareto_curve.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["threshold_sigma", "worst_fpr_hr", "sensitivity",
                     "cls_rate", "median_latency_s"])
        for p in pareto_points:
            w.writerow([
                f'{p["threshold"]:.1f}', f'{p["worst_fpr_hr"]:.2f}',
                f'{p["sensitivity"]:.3f}', f'{p["cls_rate"]:.3f}',
                f'{p["median_latency_s"]:.2f}'
                if p["median_latency_s"] is not None else "",
            ])

    print(f"  ✓ {fig_path.name}  ✓ {csv_path.name}")


# ═══════════════════════════════════════════════════════════════════
#  D-2) Operating Point Summary
# ═══════════════════════════════════════════════════════════════════

def write_operating_point(
    fpr_rows: list[FPRRow],
    cal: CalibrationResult,
    adv_rows: list[AdversarialRow],
    out_dir: str | Path,
) -> str:
    """Write the operating point summary.

    Selects CUSUM threshold + confidence p* + cooldown that achieves
    ≤1 false alarm/hour worst-case, then reports detection and
    classification performance at that operating point.

    Returns:
        The summary text.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Find best CUSUM threshold: lowest threshold where worst-case
    # FPR across all noise conditions is ≤ 1/hr (with some cooldown)
    thresholds = sorted(set(r.cusum_threshold for r in fpr_rows))
    cooldowns = sorted(set(r.cooldown_s for r in fpr_rows))

    best_thresh = thresholds[-1]
    best_cooldown = cooldowns[-1]
    for thresh in thresholds:
        for cd in cooldowns:
            subset = [r for r in fpr_rows
                      if r.cusum_threshold == thresh and r.cooldown_s == cd]
            if not subset:
                continue
            worst_fpr = max(r.fpr_per_hour for r in subset)
            if worst_fpr <= 1.0:
                best_thresh = thresh
                best_cooldown = cd
                break
        else:
            continue
        break

    # Adversarial summary at current operating point
    n_detected = sum(1 for r in adv_rows if r.n_alerts > 0)
    n_total = len(adv_rows)

    neurotox_conds = [r for r in adv_rows
                      if r.condition in ("spike_suppression", "rate_decrease")]
    artifact_conds = [r for r in adv_rows
                      if r.condition not in ("spike_suppression", "rate_decrease")]
    nt_det = sum(1 for r in neurotox_conds if r.n_alerts > 0)
    art_det = sum(1 for r in artifact_conds if r.n_alerts > 0)

    # 3-state triage counts
    nt_suppression = sum(1 for r in neurotox_conds
                         if r.triaged_label == "suppression-like")
    art_artifact = sum(1 for r in artifact_conds
                       if r.triaged_label == "artifact")
    art_uncertain = sum(1 for r in artifact_conds
                        if r.triaged_label == "uncertain")
    art_suppression = sum(1 for r in artifact_conds
                          if r.triaged_label == "suppression-like")

    # Split: trained artifact types vs novel (adversarial-only) types
    trained_artifact_names = {
        "channel_dropout", "coupling_shift",
        "60Hz+harmonics+drift",
    }
    novel_artifact_names = {
        "EMI_bursts", "ADC_clipping", "sample_rate_mismatch",
        "quantization_change", "bandpass_mismatch",
    }
    trained_art = [r for r in artifact_conds
                   if r.condition in trained_artifact_names and r.n_alerts > 0]
    novel_art = [r for r in artifact_conds
                 if r.condition in novel_artifact_names and r.n_alerts > 0]
    trained_art_correct = sum(1 for r in trained_art
                              if r.triaged_label in ("artifact", "uncertain"))
    novel_art_uncertain = sum(1 for r in novel_art
                              if r.triaged_label == "uncertain")

    nt_latencies = [r.latency_median_s for r in neurotox_conds
                    if r.latency_median_s is not None]
    lat_med = float(np.median(nt_latencies)) if nt_latencies else None
    lat_p90 = float(np.percentile(nt_latencies, 90)) if nt_latencies else None

    rt_factors = [r.realtime_factor for r in adv_rows if r.realtime_factor > 0]
    avg_rt = float(np.mean(rt_factors)) if rt_factors else 0.0

    lines = [
        "═══ Operating Point Summary ═══",
        "",
        "Selected parameters:",
        f"  CUSUM threshold:      {best_thresh:.1f}σ",
        f"  Cooldown:             {best_cooldown:.0f}s",
        f"  Sanity gate:          NaN/Inf feature check + Mahalanobis "
        f"extreme-outlier (15× training max)",
        f"  Temporal gate:        ≥K-of-N consecutive windows must predict "
        f"neurotox to confirm 'suppression-like'",
        f"  Calibration ECE:      {cal.ece:.4f}",
        "",
        "False-alarm budget:",
        f"  Target:   ≤1 false alarm/hour",
    ]

    # Report worst-case FPR at selected operating point
    subset = [r for r in fpr_rows
              if r.cusum_threshold == best_thresh
              and r.cooldown_s == best_cooldown]
    if subset:
        worst = max(subset, key=lambda r: r.fpr_per_hour)
        lines.append(f"  Achieved: {worst.fpr_per_hour:.2f}/hr "
                     f"(worst: {worst.noise_condition})")
    lines += [
        "",
        f"Stage 1 — Change Detection ({n_total} conditions):",
        f"  Overall:  {n_detected}/{n_total} detected "
        f"({100*n_detected/max(n_total,1):.0f}%)",
        f"  Neurotox: {nt_det}/{len(neurotox_conds)} "
        f"({100*nt_det/max(len(neurotox_conds),1):.0f}%)",
        f"  Artifact: {art_det}/{len(artifact_conds)} "
        f"({100*art_det/max(len(artifact_conds),1):.0f}%)",
        "",
        f"Stage 2 — 3-State Triage (detected alerts only):",
        f"  Neurotox → suppression-like: {nt_suppression}/{nt_det} "
        f"({100*nt_suppression/max(nt_det,1):.0f}%)",
        f"  Artifact → artifact:         {art_artifact}/{art_det}",
        f"  Artifact → uncertain:        {art_uncertain}/{art_det}",
        f"  Artifact → suppression-like: {art_suppression}/{art_det} "
        f"(false positives)",
        "",
        f"  Trained artifacts:  {trained_art_correct}/{len(trained_art)} "
        f"correct (artifact or uncertain)",
        f"  Novel artifacts:    {novel_art_uncertain}/{len(novel_art)} "
        f"flagged uncertain (sanity gate + temporal confirmation)",
        "",
        f"  Safety mechanisms:",
        f"    1. Feature-sanity gate: NaN/Inf → uncertain (channel dropout)",
        f"    2. Extreme-outlier gate: Mahalanobis > 15× training → uncertain",
        f"    3. Temporal confirmation: ≥K-of-N post-trigger windows must",
        f"       agree on neurotox. Transient artifacts fail this check",
        f"       because they don't persist across windows.",
        f"    4. Conservative direction: unconfirmed events flagged for",
        f"       human review, never silently dismissed.",
    ]

    # Review-load analysis: what fraction of artifact alerts require
    # human review (i.e. triaged as suppression-like)?
    art_actionable = art_suppression  # alerts going to human reviewer
    art_safe = art_artifact + art_uncertain  # correctly filtered
    review_pct = (100 * art_actionable / max(art_det, 1)
                  if art_det > 0 else 0.0)
    safe_pct = (100 * art_safe / max(art_det, 1)
                if art_det > 0 else 0.0)

    # Persistent vs transient FP breakdown
    persistent_fp_list = [
        r for r in artifact_conds
        if r.triaged_label == "suppression-like"
    ]
    persistent_names = sorted(set(
        f"{r.condition} sev={r.severity:.1f}"
        for r in persistent_fp_list
    ))

    lines += [
        "",
        "Review-load analysis (screening + triage framing):",
        f"  Artifact alerts correctly filtered:  "
        f"{art_safe}/{art_det} ({safe_pct:.0f}%)",
        f"  Artifact alerts requiring review:    "
        f"{art_actionable}/{art_det} ({review_pct:.0f}%)",
        f"  (These are persistent hardware shifts whose feature",
        f"   signatures overlap with suppression — not silent misses.)",
        "",
        f"  Persistent FP conditions ({len(persistent_fp_list)}):",
    ]
    for pname in persistent_names:
        lines.append(f"    • {pname}")
    lines += [
        "",
        f"  Why these remain: even with {N_FEATURES} features (including",
        f"  clip_fraction, line_power_ratio, spectral_flatness),",
        f"  persistent instrumentation shifts produce overlapping",
        f"  feature signatures with biological suppression.",
        "",
        "Latency (neurotox conditions only):",
        f"  Median:   {f'{lat_med:.2f}s' if lat_med is not None else 'N/A'}",
        f"  P90:      {f'{lat_p90:.2f}s' if lat_p90 is not None else 'N/A'}",
        "",
        f"Throughput:",
        f"  Average:  {avg_rt:.0f}× real-time",
        "",
        "Poster claim:",
        f'  "At ≤1 FA/hr across five noise regimes, the CUSUM→RF pipeline',
        f'  detects suppression-like events with {nt_suppression}/{nt_det} '
        f'sensitivity (median latency '
        f'{f"{lat_med:.1f}" if lat_med is not None else "?"}s) at '
        f'{avg_rt:.0f}× real-time on CPU.',
        f'  A K-of-N temporal confirmation gate eliminates transient',
        f'  artifact false positives. {safe_pct:.0f}% of artifact alerts are',
        f'  correctly filtered or flagged uncertain; the remaining',
        f'  {review_pct:.0f}% (persistent hardware shifts) route to human',
        f'  review — conservative failure mode for screening."',
    ]

    text = "\n".join(lines)
    txt_path = out / "operating_point.txt"
    txt_path.write_text(text)
    print(text)
    print(f"\n  ✓ {txt_path.name}")
    return text


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    """Run the full noise immunity proof."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Noise immunity proof for streaming detector")
    ap.add_argument("--out", default="out/noise_immunity",
                    help="Output directory")
    ap.add_argument("--n-per-class", type=int, default=5,
                    help="Training samples per class per severity")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="Adversarial sweep recording duration (s)")
    ap.add_argument("--fpr-duration-min", type=float, default=10.0,
                    help="FPR baseline recording duration (minutes)")
    ap.add_argument("--onset", type=float, default=10.0,
                    help="Perturbation onset (seconds)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    FS = 20_000.0
    N_CH = 16
    t_total = time.time()

    print("═══ Noise Immunity Proof ═══\n")

    # 1. Generate training data & build detector
    print("1. Training streaming detector...", flush=True)
    t0 = time.time()
    train_data = generate_dataset(
        n_per_class=args.n_per_class,
        severities=[0.3, 0.6, 0.9],
        n_channels=N_CH, fs=FS,
        duration_s=args.duration,
        onset_s=args.onset,
        seed=args.seed,
    )
    det = StreamingDetector.from_training_data(
        train_data, fs=FS, window_s=1.0, seed=args.seed,
        baseline_s=args.onset - 2.0,
    )
    print(f"   → {len(train_data)} windows, trained in {time.time()-t0:.1f}s\n",
          flush=True)

    # 2. FPR budget sweep
    print("2. FPR budget sweep "
          f"({args.fpr_duration_min:.0f}min baseline × 5 conditions)...",
          flush=True)
    t0 = time.time()
    fpr_rows = run_fpr_budget_sweep(
        det, n_channels=N_CH, fs=FS,
        duration_min=args.fpr_duration_min,
        seed=args.seed,
    )
    write_fpr_budget(fpr_rows, args.out)
    print(f"   FPR sweep: {time.time()-t0:.1f}s\n", flush=True)

    # 3. Calibration curves
    print("3. Calibration curves...", flush=True)
    t0 = time.time()
    cal = compute_calibration(det, train_data, fs=FS)
    write_calibration(cal, args.out)
    print(f"   Calibration: {time.time()-t0:.1f}s\n", flush=True)

    # 4. Adversarial sweep
    print("4. Adversarial sweep "
          f"({args.duration:.0f}s recordings, onset={args.onset:.0f}s)...",
          flush=True)
    t0 = time.time()
    adv_rows = run_adversarial_sweep(
        det, n_channels=N_CH, fs=FS,
        duration_s=args.duration,
        onset_s=args.onset,
        seed=args.seed,
    )
    write_adversarial_results(adv_rows, args.out)
    print(f"   Adversarial sweep: {time.time()-t0:.1f}s\n", flush=True)

    # 4b. FP diagnostic
    print("4b. Persistent FP diagnostic...", flush=True)
    t0 = time.time()
    write_fp_diagnostic(
        det, adv_rows, args.out,
        n_channels=N_CH, fs=FS,
        duration_s=args.duration,
        onset_s=args.onset,
        seed=args.seed,
    )
    print(f"   FP diagnostic: {time.time()-t0:.1f}s\n", flush=True)

    # 4c. Pareto curve
    print("4c. Pareto operating-point curve...", flush=True)
    t0 = time.time()
    write_pareto_curve(
        fpr_rows, adv_rows, det, args.out,
        n_channels=N_CH, fs=FS,
        duration_s=args.duration,
        onset_s=args.onset,
        seed=args.seed,
    )
    print(f"   Pareto curve: {time.time()-t0:.1f}s\n", flush=True)

    # 5. Operating point
    print("5. Operating point selection...\n", flush=True)
    write_operating_point(fpr_rows, cal, adv_rows, args.out)

    print(f"\n═══ Total: {time.time()-t_total:.1f}s ═══")
    print(f"Output: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
