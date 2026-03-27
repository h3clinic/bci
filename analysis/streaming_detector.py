"""
streaming_detector.py — Real-time two-stage neurotoxicity screening.

Stage 1 (CUSUM):  Continuous change-point detection on RMS envelope.
                  Fires when cumulative deviation exceeds threshold.
Stage 2 (RF):     Classify trigger window as artifact vs neurotox.
                  Only invoked when Stage 1 fires (compute-efficient).

Output per alert:
  - timestamp_s:    when the alert was raised
  - latency_s:      delay from true onset to alert
  - confidence:     Stage 2 P(neurotox)
  - reason_code:    multi-class label (e.g. "spike_suppression")
  - stage1_stat:    CUSUM statistic at trigger

Usage:
  from streaming_detector import StreamingDetector
  det = StreamingDetector.from_training_data(dataset, fs=20_000)
  for alert in det.process_recording(data_uv, onset_s_true=10.0):
      print(alert)

Demo:
  python -m analysis.streaming_detector          # noise sweep demo
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

# ── Path fix for bare imports within analysis/ ──────────────────
_analysis_dir = os.path.dirname(os.path.abspath(__file__))
if _analysis_dir not in sys.path:
    sys.path.insert(0, _analysis_dir)

from features import extract_window_features, N_FEATURES, FEATURE_NAMES
from perturbation_library import (
    LabeledWindow,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
    generate_dataset,
)
from train_classifier import cusum_detect


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Alert:
    """Single alert emitted by the streaming detector."""
    timestamp_s: float          # time of CUSUM alarm
    latency_s: float | None     # delay from true onset (if known)
    binary_label: str           # "artifact" or "neurotox" (RF only)
    confidence: float           # P(neurotox) from RF
    reason_code: str            # multi-class prediction
    reason_probs: dict[str, float]  # all class probabilities
    stage1_stat: float          # CUSUM statistic at trigger
    window_idx: int             # which analysis window triggered
    # 3-state output (with OOD gate)
    triaged_label: str = ""     # "artifact" | "suppression-like" | "uncertain"
    mahal_distance: float = 0.0 # Mahalanobis distance from training manifold
    is_ood: bool = False        # True if feature vector is out-of-distribution


@dataclass
class StreamingSummary:
    """Summary of a streaming run."""
    n_windows: int              # total analysis windows processed
    n_alerts: int               # Stage 1 triggers
    n_neurotox_alerts: int      # Stage 2 classified as neurotox
    n_artifact_alerts: int      # Stage 2 classified as artifact
    elapsed_s: float            # wall-clock processing time
    # Latency stats (only for alerts with known onset)
    latency_median_s: float | None
    latency_p90_s: float | None
    # False alarm rate (alerts before true onset)
    false_alarms: int
    false_alarm_rate_per_min: float
    # OOD gate stats
    n_uncertain_alerts: int = 0 # Stage 2 flagged as OOD/uncertain
    alerts: list[Alert] = field(default_factory=list)
    # Throughput
    recording_duration_s: float = 0.0
    ms_per_s_data: float = 0.0          # processing ms per second of data
    realtime_factor: float = 0.0        # how many × faster than real-time


# ═══════════════════════════════════════════════════════════════════
#  Streaming detector
# ═══════════════════════════════════════════════════════════════════

class StreamingDetector:
    """Two-stage real-time screening pipeline.

    Stage 1: CUSUM on RMS envelope (cheap, runs every window)
    Stage 2: RF on feature vector (expensive, runs only on trigger)
    """

    def __init__(
        self,
        rf_binary: Any,         # trained sklearn classifier
        rf_multi: Any,          # trained multi-class classifier
        scaler: Any,            # fitted StandardScaler
        type_names: list[str],  # multi-class label names
        fs: float = 20_000.0,
        window_s: float = 1.0,
        overlap: float = 0.0,
        cusum_threshold: float = 5.0,
        cusum_drift: float = 0.5,
        baseline_s: float = 8.0,
        neurotox_threshold: float = 0.5,  # P(neurotox) above this → neurotox alert
        # Extreme-outlier / feature-sanity gate (Mahalanobis distance)
        ood_mean: NDArray[np.float64] | None = None,
        ood_cov_inv: NDArray[np.float64] | None = None,
        ood_threshold: float = 15.0,
        # Temporal confirmation: require ≥confirm_k of confirm_n
        # consecutive post-trigger windows to predict neurotox before
        # outputting "suppression-like". Catches transient artifacts.
        confirm_k: int = 3,
        confirm_n: int = 5,
    ):
        self.rf_binary = rf_binary
        self.rf_multi = rf_multi
        self.scaler = scaler
        self.type_names = type_names
        self.fs = fs
        self.window_s = window_s
        self.overlap = overlap
        self.cusum_threshold = cusum_threshold
        self.cusum_drift = cusum_drift
        self.baseline_s = baseline_s
        self.neurotox_threshold = neurotox_threshold
        self.ood_mean = ood_mean
        self.ood_cov_inv = ood_cov_inv
        self.ood_threshold = ood_threshold
        self.confirm_k = confirm_k
        self.confirm_n = confirm_n

    @classmethod
    def from_training_data(
        cls,
        dataset: list[LabeledWindow],
        fs: float = 20_000.0,
        window_s: float = 1.0,
        seed: int = 42,
        **kwargs: Any,
    ) -> StreamingDetector:
        """Build a streaming detector from a labeled training set.

        Trains RF binary + multi-class classifiers and fits scaler.
        """
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler

        # Build feature matrix from training data
        X_list = []
        y_bin_list: list[int] = []
        y_multi_list: list[int] = []

        # Type mapping
        all_types = ["baseline"] + list(ARTIFACT_TYPES) + list(NEUROTOX_TYPES)
        type_to_idx = {t: i for i, t in enumerate(all_types)}

        for w in dataset:
            feat = extract_window_features(w.data_uv, fs=fs)
            X_list.append(feat)

            ptype = w.meta.perturbation_type
            cat = w.meta.category
            y_bin_list.append(1 if cat == "neurotox" else 0)
            y_multi_list.append(type_to_idx.get(ptype, 0))

        X = np.array(X_list)
        y_bin = np.array(y_bin_list)
        y_multi = np.array(y_multi_list)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Binary classifier
        rf_bin = RandomForestClassifier(
            n_estimators=100, max_depth=6, min_samples_leaf=3,
            random_state=seed, class_weight="balanced",
        )
        rf_bin.fit(X_scaled, y_bin)

        # Multi-class classifier
        rf_multi = RandomForestClassifier(
            n_estimators=100, max_depth=8, min_samples_leaf=2,
            random_state=seed, class_weight="balanced",
        )
        rf_multi.fit(X_scaled, y_multi)

        # OOD gate: Mahalanobis distance from training manifold
        # Use global (pooled) covariance for stability with small samples
        ood_mean = X_scaled.mean(axis=0)
        cov = np.cov(X_scaled, rowvar=False)
        # Regularize covariance for numerical stability
        cov += np.eye(cov.shape[0]) * 1e-6
        ood_cov_inv = np.linalg.inv(cov)

        # Threshold: generous multiplier on empirical max to avoid
        # flagging legitimate signals under domain shift. The OOD gate
        # catches only extreme outliers (NaN features, radically novel
        # signal distributions) — not moderate domain shift.
        diffs = X_scaled - ood_mean[np.newaxis, :]
        train_dists = np.sqrt(
            np.sum(diffs @ ood_cov_inv * diffs, axis=1)
        )
        empirical_max = float(np.max(train_dists))
        ood_threshold = empirical_max * 15.0

        return cls(
            rf_binary=rf_bin,
            rf_multi=rf_multi,
            scaler=scaler,
            type_names=all_types,
            fs=fs,
            window_s=window_s,
            ood_mean=ood_mean,
            ood_cov_inv=ood_cov_inv,
            ood_threshold=ood_threshold,
            **kwargs,
        )

    def process_recording(
        self,
        data_uv: NDArray[np.float64],
        onset_s_true: float | None = None,
    ) -> StreamingSummary:
        """Process a full recording in streaming fashion.

        Simulates real-time: slides analysis windows across the recording,
        runs CUSUM on RMS envelope, triggers RF classification on alarm.

        Args:
            data_uv: (n_channels, n_samples) recording
            onset_s_true: ground-truth onset for latency measurement

        Returns:
            StreamingSummary with all alerts and timing stats.
        """
        t0 = time.time()
        n_ch, n_samp = data_uv.shape
        win_samp = int(self.window_s * self.fs)
        step_samp = int(win_samp * (1 - self.overlap))
        baseline_end_idx = max(1, int(self.baseline_s / self.window_s))

        # Step 1: Extract RMS trace (Stage 1 feature)
        rms_trace: list[float] = []
        window_starts: list[int] = []
        idx = 0
        while idx + win_samp <= n_samp:
            chunk = data_uv[:, idx: idx + win_samp]
            rms_val = float(np.sqrt(np.mean(chunk ** 2)))
            rms_trace.append(rms_val)
            window_starts.append(idx)
            idx += step_samp

        rms_arr = np.array(rms_trace)
        n_windows = len(rms_trace)

        if n_windows < baseline_end_idx + 2:
            return StreamingSummary(
                n_windows=n_windows, n_alerts=0,
                n_neurotox_alerts=0, n_artifact_alerts=0,
                alerts=[], elapsed_s=time.time() - t0,
                latency_median_s=None, latency_p90_s=None,
                false_alarms=0, false_alarm_rate_per_min=0.0,
            )

        # Step 2: CUSUM on RMS trace (Stage 1)
        det_idx, cusum_trace = cusum_detect(
            rms_arr, baseline_end_idx,
            threshold=self.cusum_threshold,
            drift=self.cusum_drift,
        )

        alerts: list[Alert] = []
        if det_idx is not None:
            # Find all alarm points (where CUSUM stays above threshold)
            alarm_indices = []
            triggered = False
            for i in range(baseline_end_idx, n_windows):
                if cusum_trace[i] > self.cusum_threshold and not triggered:
                    alarm_indices.append(i)
                    triggered = True
                elif cusum_trace[i] <= self.cusum_threshold * 0.5:
                    triggered = False  # reset after drop

            # Stage 2: classify each alarm with K-of-N confirmation
            for alarm_idx in alarm_indices:
                # Classify up to confirm_n consecutive windows
                # starting from the alarm window.
                confirm_windows = range(
                    alarm_idx,
                    min(alarm_idx + self.confirm_n, n_windows),
                )
                n_neurotox_wins = 0
                n_classified = 0
                last_p = 0.0
                last_feat_scaled = None
                last_mahal = 0.0
                last_is_ood = False
                any_ood = False

                for wi in confirm_windows:
                    wstart = window_starts[wi]
                    wchunk = data_uv[:, wstart: wstart + win_samp]
                    wfeat = extract_window_features(wchunk, fs=self.fs)
                    wfeat_scaled = self.scaler.transform(
                        wfeat.reshape(1, -1)
                    )

                    # Feature-sanity gate (NaN/Inf check)
                    if not np.all(np.isfinite(wfeat_scaled)):
                        any_ood = True
                        last_mahal = 1e6
                        last_is_ood = True
                        continue  # skip degenerate window

                    # Extreme-outlier gate (Mahalanobis)
                    w_mahal = 0.0
                    w_ood = False
                    if (self.ood_mean is not None
                            and self.ood_cov_inv is not None):
                        fv = wfeat_scaled.flatten()
                        diff = fv - self.ood_mean
                        d = float(np.sqrt(diff @ self.ood_cov_inv @ diff))
                        w_mahal = d if np.isfinite(d) else 1e6
                        w_ood = w_mahal > self.ood_threshold
                    if w_ood:
                        any_ood = True
                        last_mahal = w_mahal
                        last_is_ood = True
                        continue  # skip extreme-outlier window

                    wp = float(
                        self.rf_binary.predict_proba(wfeat_scaled)[0, 1]
                    )
                    n_classified += 1
                    if wp >= self.neurotox_threshold:
                        n_neurotox_wins += 1
                    last_p = wp
                    last_feat_scaled = wfeat_scaled
                    last_mahal = w_mahal
                    last_is_ood = False

                # Use the first alarm window for the alert timestamp
                start = window_starts[alarm_idx]
                timestamp_s = (start + win_samp) / self.fs

                # Use last valid classified window for RF outputs;
                # fall back to alarm window if all were OOD/degenerate.
                if last_feat_scaled is None:
                    feat = extract_window_features(
                        data_uv[:, start: start + win_samp], fs=self.fs
                    )
                    last_feat_scaled = self.scaler.transform(
                        feat.reshape(1, -1)
                    )
                    last_p = float(
                        self.rf_binary.predict_proba(
                            last_feat_scaled
                        )[0, 1]
                    )

                p_neurotox = last_p
                binary_label = (
                    "neurotox" if p_neurotox >= self.neurotox_threshold
                    else "artifact"
                )

                # Temporal confirmation → 3-state triage
                if any_ood and n_classified == 0:
                    # All windows were degenerate/extreme-outlier
                    triaged_label = "uncertain"
                    is_ood = True
                    mahal_dist = last_mahal
                elif n_neurotox_wins >= self.confirm_k:
                    # Enough temporal support → confirmed
                    triaged_label = "suppression-like"
                    is_ood = False
                    mahal_dist = last_mahal
                elif p_neurotox < self.neurotox_threshold:
                    # RF says artifact, no need for confirmation
                    triaged_label = "artifact"
                    is_ood = False
                    mahal_dist = last_mahal
                else:
                    # RF says neurotox but not enough temporal support
                    triaged_label = "uncertain"
                    is_ood = any_ood
                    mahal_dist = last_mahal

                # Multi-class prediction (from last valid window)
                multi_probs_raw = self.rf_multi.predict_proba(
                    last_feat_scaled
                )[0]
                multi_pred_idx = int(np.argmax(multi_probs_raw))
                reason_code = self.type_names[
                    self.rf_multi.classes_[multi_pred_idx]
                ]
                reason_probs = {}
                for ci, cls_idx in enumerate(self.rf_multi.classes_):
                    reason_probs[self.type_names[cls_idx]] = float(
                        multi_probs_raw[ci]
                    )

                latency = (
                    timestamp_s - onset_s_true
                    if onset_s_true is not None
                    else None
                )

                alerts.append(Alert(
                    timestamp_s=timestamp_s,
                    latency_s=latency,
                    binary_label=binary_label,
                    confidence=p_neurotox,
                    reason_code=reason_code,
                    reason_probs=reason_probs,
                    stage1_stat=float(cusum_trace[alarm_idx]),
                    window_idx=alarm_idx,
                    triaged_label=triaged_label,
                    mahal_distance=mahal_dist,
                    is_ood=is_ood,
                ))

        # Compute summary stats
        elapsed = time.time() - t0
        n_neurotox = sum(1 for a in alerts
                         if a.triaged_label == "suppression-like")
        n_artifact = sum(1 for a in alerts
                         if a.triaged_label == "artifact")
        n_uncertain = sum(1 for a in alerts
                          if a.triaged_label == "uncertain")

        latencies = [
            a.latency_s for a in alerts
            if a.latency_s is not None and a.latency_s >= 0
        ]
        lat_median = float(np.median(latencies)) if latencies else None
        lat_p90 = float(np.percentile(latencies, 90)) if latencies else None

        # False alarms: alerts that fired before true onset
        false_alarms = 0
        if onset_s_true is not None:
            false_alarms = sum(
                1 for a in alerts if a.timestamp_s < onset_s_true
            )

        duration_s = n_samp / self.fs
        duration_min = duration_s / 60
        fa_rate = false_alarms / duration_min if duration_min > 0 else 0.0

        # Throughput
        ms_per_s = (elapsed * 1000) / duration_s if duration_s > 0 else 0.0
        rt_factor = duration_s / elapsed if elapsed > 0 else float("inf")

        return StreamingSummary(
            n_windows=n_windows,
            n_alerts=len(alerts),
            n_neurotox_alerts=n_neurotox,
            n_artifact_alerts=n_artifact,
            n_uncertain_alerts=n_uncertain,
            alerts=alerts,
            elapsed_s=elapsed,
            latency_median_s=lat_median,
            latency_p90_s=lat_p90,
            false_alarms=false_alarms,
            false_alarm_rate_per_min=fa_rate,
            recording_duration_s=duration_s,
            ms_per_s_data=ms_per_s,
            realtime_factor=rt_factor,
        )


# ═══════════════════════════════════════════════════════════════════
#  Noise sweep demo
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SweepResult:
    """One row in the noise-sweep table."""
    noise_type: str
    severity: float
    n_alerts: int
    n_neurotox: int
    n_artifact: int
    false_alarms: int
    latency_median_s: float | None
    latency_p90_s: float | None
    confidence_mean: float
    processing_time_s: float


def run_noise_sweep(
    det: StreamingDetector,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 30.0,
    onset_s: float = 10.0,
    seed: int = 42,
) -> list[SweepResult]:
    """Run the streaming detector under various noise conditions.

    Generates recordings with known perturbations at different severities,
    processes them through the streaming pipeline, and measures:
    - Detection latency
    - False alarm rate
    - Classification accuracy under noise

    Returns:
        List of SweepResult rows for each condition.
    """
    from signal_gen import generate_neural_signal

    rng = np.random.default_rng(seed)
    results: list[SweepResult] = []

    # Test conditions: (noise_type, severity_values)
    conditions = [
        ("baseline", [0.0]),
        ("sixty_hz", [0.3, 0.6, 0.9]),
        ("dc_drift", [0.3, 0.6, 0.9]),
        ("channel_dropout", [0.3, 0.6, 0.9]),
        ("spike_suppression", [0.3, 0.6, 0.9]),
        ("rate_decrease", [0.3, 0.6, 0.9]),
    ]

    total = sum(len(sevs) for _, sevs in conditions)
    idx = 0
    for noise_type, severities in conditions:
        for sev in severities:
            idx += 1
            print(f"  [{idx}/{total}] {noise_type} sev={sev:.1f}...",
                  end="", flush=True)

            # Generate clean recording
            data_uv = generate_neural_signal(
                n_channels=n_channels, fs=fs, duration_s=duration_s,
                seed=seed + idx * 100,
            )

            # Apply perturbation after onset
            if noise_type != "baseline":
                onset_samp = int(onset_s * fs)
                post = data_uv[:, onset_samp:]

                if noise_type == "sixty_hz":
                    t = np.arange(post.shape[1]) / fs
                    amplitude = sev * 50  # μV
                    post += amplitude * np.sin(2 * np.pi * 60 * t)[np.newaxis, :]
                elif noise_type == "dc_drift":
                    t = np.arange(post.shape[1]) / fs
                    drift = sev * 100 * t / t[-1]  # μV
                    post += drift[np.newaxis, :]
                elif noise_type == "channel_dropout":
                    n_drop = max(1, int(sev * n_channels))
                    drop_ch = rng.choice(n_channels, n_drop, replace=False)
                    post[drop_ch] = 0.0
                elif noise_type == "spike_suppression":
                    post *= (1.0 - sev * 0.8)
                elif noise_type == "rate_decrease":
                    # Add extra smoothing to suppress spikes
                    from scipy.ndimage import uniform_filter1d
                    kernel = int(sev * 20) + 1
                    for ch in range(n_channels):
                        post[ch] = uniform_filter1d(post[ch], size=kernel)

                data_uv[:, onset_samp:] = post

            true_onset = onset_s if noise_type != "baseline" else None
            summary = det.process_recording(data_uv, onset_s_true=true_onset)

            confidences = [a.confidence for a in summary.alerts]
            conf_mean = float(np.mean(confidences)) if confidences else 0.0

            results.append(SweepResult(
                noise_type=noise_type,
                severity=sev,
                n_alerts=summary.n_alerts,
                n_neurotox=summary.n_neurotox_alerts,
                n_artifact=summary.n_artifact_alerts,
                false_alarms=summary.false_alarms,
                latency_median_s=summary.latency_median_s,
                latency_p90_s=summary.latency_p90_s,
                confidence_mean=conf_mean,
                processing_time_s=summary.elapsed_s,
            ))

            status = ("✓" if summary.n_alerts > 0 else "—")
            lat = (f" lat={summary.latency_median_s:.2f}s"
                   if summary.latency_median_s is not None else "")
            print(f" {status} alerts={summary.n_alerts}"
                  f" neurotox={summary.n_neurotox_alerts}"
                  f" fa={summary.false_alarms}{lat}"
                  f" ({summary.elapsed_s:.1f}s)", flush=True)

    return results


def write_sweep_results(
    results: list[SweepResult],
    out_dir: str | Path,
) -> None:
    """Write noise sweep results to CSV and summary figure."""
    import csv
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # CSV
    csv_path = out / "noise_sweep.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "noise_type", "severity", "n_alerts", "n_neurotox",
            "n_artifact", "false_alarms", "latency_median_s",
            "latency_p90_s", "confidence_mean", "processing_time_s",
        ])
        for r in results:
            writer.writerow([
                r.noise_type, f"{r.severity:.1f}", r.n_alerts,
                r.n_neurotox, r.n_artifact, r.false_alarms,
                f"{r.latency_median_s:.3f}" if r.latency_median_s else "",
                f"{r.latency_p90_s:.3f}" if r.latency_p90_s else "",
                f"{r.confidence_mean:.3f}",
                f"{r.processing_time_s:.3f}",
            ])

    # Figure: 2×2 panel
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    fig.suptitle("Streaming Detector — Noise Immunity Sweep", fontsize=14)

    # Panel 1: Detection rate by condition
    ax = axes[0, 0]
    conditions = [r for r in results if r.noise_type != "baseline"]
    labels = [f"{r.noise_type}\n{r.severity:.1f}" for r in conditions]
    detected = [1 if r.n_alerts > 0 else 0 for r in conditions]
    colors = ["#2ecc71" if d else "#e74c3c" for d in detected]
    ax.bar(range(len(labels)), detected, color=colors)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("Detected")
    ax.set_title("Stage 1: Change Detection")
    ax.set_ylim(-0.1, 1.3)

    # Panel 2: Latency distribution
    ax = axes[0, 1]
    latencies = [r.latency_median_s for r in results
                 if r.latency_median_s is not None]
    if latencies:
        ax.hist(latencies, bins=min(10, len(latencies)), color="#3498db",
                edgecolor="white")
        ax.axvline(np.median(latencies), color="red", ls="--",
                   label=f"median={np.median(latencies):.2f}s")
        ax.legend(fontsize=9)
    ax.set_xlabel("Detection Latency (s)")
    ax.set_ylabel("Count")
    ax.set_title("Alert Latency Distribution")

    # Panel 3: Classification confidence by type
    ax = axes[1, 0]
    neurotox_types = ["spike_suppression", "rate_decrease"]
    artifact_types = ["sixty_hz", "dc_drift", "channel_dropout"]
    nt_confs = [r.confidence_mean for r in results
                if r.noise_type in neurotox_types and r.n_alerts > 0]
    art_confs = [r.confidence_mean for r in results
                 if r.noise_type in artifact_types and r.n_alerts > 0]
    data_to_plot = []
    plot_labels = []
    if nt_confs:
        data_to_plot.append(nt_confs)
        plot_labels.append("Neurotox")
    if art_confs:
        data_to_plot.append(art_confs)
        plot_labels.append("Artifact")
    if data_to_plot:
        bp = ax.boxplot(data_to_plot, tick_labels=plot_labels, patch_artist=True)
        colors_bp = ["#e74c3c", "#3498db"]
        for patch, c in zip(bp["boxes"], colors_bp[:len(data_to_plot)]):
            patch.set_facecolor(c)
            patch.set_alpha(0.6)
    ax.set_ylabel("P(neurotox)")
    ax.set_title("Stage 2: Classification Confidence")
    ax.set_ylim(-0.05, 1.05)
    ax.axhline(0.5, color="gray", ls=":", alpha=0.5)

    # Panel 4: False alarm rate
    ax = axes[1, 1]
    baseline_results = [r for r in results if r.noise_type == "baseline"]
    all_fa = [r.false_alarms for r in results]
    ax.bar(["Baseline"] + [f"{r.noise_type}\n{r.severity:.1f}"
            for r in conditions],
           [baseline_results[0].false_alarms if baseline_results else 0]
           + [r.false_alarms for r in conditions],
           color="#e67e22", alpha=0.7)
    ax.set_ylabel("False Alarms")
    ax.set_title("False Alarms (before onset)")
    ax.tick_params(axis="x", rotation=45, labelsize=7)

    plt.tight_layout()
    fig_path = out / "noise_sweep.png"
    plt.savefig(fig_path, dpi=200, bbox_inches="tight")
    plt.close()

    print(f"  ✓ {csv_path.name}")
    print(f"  ✓ {fig_path.name}")


# ═══════════════════════════════════════════════════════════════════
#  CLI demo
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    """Run the streaming detector demo with noise sweep."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Streaming two-stage neurotox screening demo")
    ap.add_argument("--out", default="out/streaming_demo",
                    help="Output directory")
    ap.add_argument("--n-per-class", type=int, default=5,
                    help="Training samples per class per severity")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="Recording duration (seconds)")
    ap.add_argument("--onset", type=float, default=10.0,
                    help="Perturbation onset (seconds)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    FS = 20_000.0
    N_CH = 16

    print("═══ Streaming Detector Demo ═══\n")

    # 1. Generate training data
    print("Generating training dataset...", flush=True)
    t0 = time.time()
    train_data = generate_dataset(
        n_per_class=args.n_per_class,
        severities=[0.3, 0.6, 0.9],
        n_channels=N_CH,
        fs=FS,
        duration_s=args.duration,
        onset_s=args.onset,
        seed=args.seed,
    )
    print(f"  → {len(train_data)} windows ({time.time()-t0:.1f}s)", flush=True)

    # 2. Train streaming detector
    print("Training streaming detector...", flush=True)
    t0 = time.time()
    det = StreamingDetector.from_training_data(
        train_data, fs=FS, window_s=1.0, seed=args.seed,
        baseline_s=args.onset - 2.0,
    )
    print(f"  → Trained in {time.time()-t0:.1f}s", flush=True)

    # 3. Run noise sweep
    print(f"\nNoise immunity sweep (duration={args.duration}s, "
          f"onset={args.onset}s):", flush=True)
    sweep_results = run_noise_sweep(
        det, n_channels=N_CH, fs=FS,
        duration_s=args.duration, onset_s=args.onset,
        seed=args.seed,
    )

    # 4. Write results
    print(f"\nWriting results to {args.out}/...", flush=True)
    write_sweep_results(sweep_results, args.out)

    # 5. Summary
    detected = sum(1 for r in sweep_results
                   if r.noise_type != "baseline" and r.n_alerts > 0)
    total_pert = sum(1 for r in sweep_results if r.noise_type != "baseline")
    latencies = [r.latency_median_s for r in sweep_results
                 if r.latency_median_s is not None]
    total_fa = sum(r.false_alarms for r in sweep_results)

    # Throughput (use last sweep result as representative)
    proc_times = [r.processing_time_s for r in sweep_results if r.processing_time_s > 0]
    avg_proc = float(np.mean(proc_times)) if proc_times else 0.0
    ms_per_s = (avg_proc * 1000) / args.duration if args.duration > 0 else 0.0
    rt_factor = args.duration / avg_proc if avg_proc > 0 else float("inf")

    print(f"\n═══ Summary ═══")
    print(f"  Detection rate: {detected}/{total_pert} "
          f"({100*detected/max(total_pert,1):.0f}%)")
    if latencies:
        print(f"  Median latency: {np.median(latencies):.2f}s")
        print(f"  P90 latency:    {np.percentile(latencies, 90):.2f}s")
    print(f"  Total false alarms: {total_fa}")
    print(f"  Throughput:     {ms_per_s:.2f} ms/s → {rt_factor:.0f}× real-time")
    print(f"  Output: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
