"""
fig3_detection.py — Figure 3: Neurotox Proxy Perturbation Detection.

Produces a 4-panel figure:
  A) Windowed RMS noise over time with perturbation onset marked
  B) Windowed bandpower (300–3000 Hz) over time
  C) Spectral slope comparison: baseline vs perturbed epochs
  D) Detection statistic with threshold and lead-time annotation

This is the "novel claim" figure — the scientific result.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

from data_loader import Recording
from neural_metrics import (
    noise_rms_windowed, bandpower_windowed, spectral_slope,
    spectral_density, detect_perturbation,
)


def make_figure(
    rec: Recording,
    perturbation_onset_s: float | None = None,
    baseline_end_s: float | None = None,
    window_s: float = 1.0,
    sigma_threshold: float = 3.0,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Generate the 4-panel perturbation detection figure.

    Args:
        rec: Recording with perturbation (synthetic or real).
        perturbation_onset_s: Known onset time (for ground-truth line).
            If None, defaults to midpoint.
        baseline_end_s: End of baseline epoch for statistics.
            If None, defaults to onset - 1s.
        window_s: Analysis window length.
        sigma_threshold: Detection threshold in standard deviations.
        output_path: If provided, save figure.
        dpi: Output resolution.

    Returns:
        matplotlib Figure object.
    """
    if perturbation_onset_s is None:
        perturbation_onset_s = rec.duration_s / 2
    if baseline_end_s is None:
        baseline_end_s = perturbation_onset_s - 1.0

    baseline_end_idx = max(1, int(baseline_end_s / window_s))

    # ── Compute metrics ──────────────────────────────────────────
    # Metric 1: Windowed RMS
    rms_centers, rms_win = noise_rms_windowed(
        rec.data_uv, fs=rec.fs, window_s=window_s, highpass_hz=1.0,
    )
    rms_mean = np.mean(rms_win, axis=0)

    # Metric 2: Windowed bandpower (300–3000 Hz = "spike band")
    bp_centers, bp_win = bandpower_windowed(
        rec.data_uv, fs=rec.fs, band=(300.0, 3000.0), window_s=window_s,
    )
    bp_mean = np.mean(bp_win, axis=0)

    # Metric 3: Spectral slope — baseline vs perturbed epochs
    baseline_samples = int(baseline_end_s * rec.fs)
    perturbed_start = int((perturbation_onset_s + 2.0) * rec.fs)
    baseline_data = rec.data_uv[:, :baseline_samples]
    perturbed_data = rec.data_uv[:, perturbed_start:]

    slope_baseline = spectral_slope(baseline_data, fs=rec.fs)
    slope_perturbed = spectral_slope(perturbed_data, fs=rec.fs)

    # Detection on RMS metric
    det_idx_rms, z_rms = detect_perturbation(
        rms_mean, baseline_end_idx, sigma_threshold,
    )
    det_time_rms = rms_centers[det_idx_rms] if det_idx_rms is not None else None

    # Detection on bandpower metric
    det_idx_bp, z_bp = detect_perturbation(
        bp_mean, baseline_end_idx, sigma_threshold,
    )
    det_time_bp = bp_centers[det_idx_bp] if det_idx_bp is not None else None

    # ── Layout ───────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 14))
    fig.suptitle(
        "Figure 3 — Neurotox Proxy Perturbation Detection",
        fontsize=14, fontweight="bold", y=0.98,
    )

    gs = gridspec.GridSpec(2, 2, hspace=0.35, wspace=0.3,
                           left=0.08, right=0.95, top=0.93, bottom=0.06)

    onset_color = "red"
    det_color = "green"

    # ── Panel A: Windowed RMS ────────────────────────────────────
    ax_a = fig.add_subplot(gs[0, 0])
    for ch in range(rec.n_channels):
        ax_a.plot(rms_centers, rms_win[ch], linewidth=0.5, alpha=0.4)
    ax_a.plot(rms_centers, rms_mean, color="black", linewidth=2, label="Mean RMS")

    ax_a.axvline(perturbation_onset_s, color=onset_color, linestyle="--",
                 linewidth=1.5, label=f"Onset ({perturbation_onset_s:.0f}s)")
    if det_time_rms is not None:
        ax_a.axvline(det_time_rms, color=det_color, linestyle="-.",
                     linewidth=1.5,
                     label=f"Detected ({det_time_rms:.1f}s)")
        lead_time = det_time_rms - perturbation_onset_s
        ax_a.annotate(
            f"Lead: {lead_time:+.1f}s",
            xy=(det_time_rms, np.max(rms_mean) * 0.9),
            fontsize=9, color=det_color, fontweight="bold",
        )

    ax_a.set_xlabel("Time (s)")
    ax_a.set_ylabel("RMS Noise (µVrms)")
    ax_a.set_title("A) Windowed RMS — Noise Floor Shift")
    ax_a.legend(fontsize=8, loc="upper left")
    ax_a.set_ylim(bottom=0)

    # ── Panel B: Windowed Bandpower ──────────────────────────────
    ax_b = fig.add_subplot(gs[0, 1])
    for ch in range(rec.n_channels):
        ax_b.plot(bp_centers, bp_win[ch], linewidth=0.5, alpha=0.4)
    ax_b.plot(bp_centers, bp_mean, color="black", linewidth=2, label="Mean BP")

    ax_b.axvline(perturbation_onset_s, color=onset_color, linestyle="--",
                 linewidth=1.5, label=f"Onset ({perturbation_onset_s:.0f}s)")
    if det_time_bp is not None:
        ax_b.axvline(det_time_bp, color=det_color, linestyle="-.",
                     linewidth=1.5,
                     label=f"Detected ({det_time_bp:.1f}s)")

    ax_b.set_xlabel("Time (s)")
    ax_b.set_ylabel("Band Power (µV²)")
    ax_b.set_title("B) 300–3000 Hz Band Power — Spectral Energy Shift")
    ax_b.legend(fontsize=8, loc="upper left")
    ax_b.set_ylim(bottom=0)

    # ── Panel C: Spectral Slope Comparison ───────────────────────
    ax_c = fig.add_subplot(gs[1, 0])

    x = np.arange(rec.n_channels)
    width = 0.35
    ax_c.bar(x - width/2, slope_baseline, width, color="steelblue",
             label="Baseline", alpha=0.8)
    ax_c.bar(x + width/2, slope_perturbed, width, color="coral",
             label="Perturbed", alpha=0.8)

    ax_c.set_xlabel("Channel")
    ax_c.set_ylabel("Spectral Slope (log-log)")
    ax_c.set_title("C) 1/f Spectral Slope — Baseline vs Perturbed")
    ax_c.set_xticks(x)
    ax_c.set_xticklabels([str(i) for i in range(rec.n_channels)], fontsize=7)
    ax_c.legend(fontsize=9)
    ax_c.axhline(0, color="gray", linewidth=0.5)

    # Mean shift annotation
    mean_shift = np.mean(slope_perturbed) - np.mean(slope_baseline)
    ax_c.text(0.98, 0.05, f"Mean shift: {mean_shift:+.2f}",
              transform=ax_c.transAxes, fontsize=10, ha="right",
              fontweight="bold", color="coral")

    # ── Panel D: Detection Statistic ─────────────────────────────
    ax_d = fig.add_subplot(gs[1, 1])

    # Z-score of RMS metric
    baseline_rms = rms_mean[:baseline_end_idx]
    mu_rms = np.mean(baseline_rms)
    std_rms = np.std(baseline_rms)
    if std_rms > 0:
        z_rms_trace = (rms_mean - mu_rms) / std_rms
    else:
        z_rms_trace = np.zeros_like(rms_mean)

    # Z-score of bandpower metric
    baseline_bp = bp_mean[:baseline_end_idx]
    mu_bp = np.mean(baseline_bp)
    std_bp = np.std(baseline_bp)
    if std_bp > 0:
        z_bp_trace = (bp_mean - mu_bp) / std_bp
    else:
        z_bp_trace = np.zeros_like(bp_mean)

    ax_d.plot(rms_centers, z_rms_trace, color="steelblue", linewidth=1.5,
              label="Z(RMS)")
    ax_d.plot(bp_centers, z_bp_trace, color="coral", linewidth=1.5,
              label="Z(Bandpower)")

    ax_d.axhline(sigma_threshold, color="gray", linestyle="--",
                 label=f"Threshold ({sigma_threshold}σ)")
    ax_d.axhline(-sigma_threshold, color="gray", linestyle="--")
    ax_d.axvline(perturbation_onset_s, color=onset_color, linestyle="--",
                 linewidth=1.5, label="True onset")

    if det_time_rms is not None:
        ax_d.axvline(det_time_rms, color="steelblue", linestyle="-.",
                     linewidth=1, alpha=0.7)
    if det_time_bp is not None:
        ax_d.axvline(det_time_bp, color="coral", linestyle="-.",
                     linewidth=1, alpha=0.7)

    ax_d.set_xlabel("Time (s)")
    ax_d.set_ylabel("Z-Score (σ)")
    ax_d.set_title("D) Detection Statistic — Multi-Biomarker")
    ax_d.legend(fontsize=8, loc="upper left")

    # Summary box
    summary_lines = [
        f"True onset: {perturbation_onset_s:.0f}s",
    ]
    if det_time_rms is not None:
        summary_lines.append(
            f"RMS detection: {det_time_rms:.1f}s "
            f"(lead {det_time_rms - perturbation_onset_s:+.1f}s, "
            f"z={z_rms:.1f}σ)")
    else:
        summary_lines.append("RMS detection: NOT DETECTED")
    if det_time_bp is not None:
        summary_lines.append(
            f"BP detection: {det_time_bp:.1f}s "
            f"(lead {det_time_bp - perturbation_onset_s:+.1f}s, "
            f"z={z_bp:.1f}σ)")
    else:
        summary_lines.append("BP detection: NOT DETECTED")

    ax_d.text(0.98, 0.95, "\n".join(summary_lines),
              transform=ax_d.transAxes, fontsize=9,
              verticalalignment="top", horizontalalignment="right",
              bbox=dict(boxstyle="round,pad=0.5", facecolor="lightyellow",
                        edgecolor="gray", alpha=0.9))

    # Source annotation
    fig.text(0.5, 0.01,
             f"Source: {rec.metadata.source} | {rec.metadata.notes}",
             ha="center", fontsize=8, color="gray")

    # ── Save ─────────────────────────────────────────────────────
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")

    return fig


if __name__ == "__main__":
    from data_loader import load_synthetic
    rec = load_synthetic("neurotox", duration_s=30.0)
    make_figure(rec, perturbation_onset_s=15.0,
                output_path="out/figures/fig3_detection.png")
    print("Figure 3 saved to out/figures/fig3_detection.png")
