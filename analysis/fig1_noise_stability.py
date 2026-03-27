"""
fig1_noise_stability.py — Figure 1: Noise Floor & Temporal Stability.

Produces a 3-panel figure:
  A) Box plot: per-channel RMS noise (µVrms)
  B) Line plot: windowed RMS over time, all channels (drift tracking)
  C) Bar chart: coefficient of variation across channels

This is the credibility figure. If it's messy, nothing else matters.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

from data_loader import Recording
from neural_metrics import noise_rms_uv, noise_rms_windowed


def make_figure(
    rec: Recording,
    output_path: str | Path | None = None,
    window_s: float = 1.0,
    highpass_hz: float = 1.0,
    dpi: int = 200,
) -> plt.Figure:
    """Generate the 3-panel noise & stability figure.

    Args:
        rec: Recording object (synthetic or real).
        output_path: If provided, save figure to this path.
        window_s: Window length for RMS tracking.
        highpass_hz: High-pass cutoff for DC removal.
        dpi: Output resolution.

    Returns:
        matplotlib Figure object.
    """
    # ── Compute metrics ──────────────────────────────────────────
    rms_per_channel = noise_rms_uv(rec.data_uv, fs=rec.fs, highpass_hz=highpass_hz)
    centers, rms_windowed = noise_rms_windowed(
        rec.data_uv, fs=rec.fs, window_s=window_s, highpass_hz=highpass_hz,
    )

    # Cross-channel CV
    mean_rms = np.mean(rms_per_channel)
    std_rms = np.std(rms_per_channel)
    cv = std_rms / mean_rms if mean_rms > 0 else 0

    # ── Layout ───────────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 10))
    fig.suptitle(
        "Figure 1 — Noise Floor & Temporal Stability",
        fontsize=14, fontweight="bold", y=0.98,
    )

    gs = gridspec.GridSpec(2, 2, hspace=0.35, wspace=0.3,
                           left=0.08, right=0.95, top=0.92, bottom=0.08)

    # ── Panel A: Box plot of per-channel RMS ─────────────────────
    ax_a = fig.add_subplot(gs[0, 0])
    channel_labels = [f"CH{i}" for i in range(rec.n_channels)]

    bp = ax_a.boxplot(
        [rec.data_uv[ch] * 0 + rms_per_channel[ch] for ch in range(rec.n_channels)],
        positions=range(rec.n_channels),
        widths=0.6,
        showfliers=False,
        patch_artist=True,
    )
    # Actually plot as bar chart since we have one RMS value per channel
    ax_a.clear()
    colors = plt.cm.viridis(np.linspace(0.2, 0.8, rec.n_channels))
    bars = ax_a.bar(range(rec.n_channels), rms_per_channel, color=colors, width=0.7)
    ax_a.axhline(mean_rms, color="red", linestyle="--", linewidth=1,
                 label=f"Mean: {mean_rms:.2f} µVrms")
    ax_a.set_xlabel("Channel")
    ax_a.set_ylabel("RMS Noise (µVrms)")
    ax_a.set_title("A) Per-Channel Noise Floor")
    ax_a.set_xticks(range(rec.n_channels))
    ax_a.set_xticklabels([str(i) for i in range(rec.n_channels)],
                         fontsize=7, rotation=45)
    ax_a.legend(fontsize=9)
    ax_a.set_ylim(bottom=0)

    # ── Panel B: Windowed RMS over time ──────────────────────────
    ax_b = fig.add_subplot(gs[0, 1])
    for ch in range(rec.n_channels):
        alpha = 0.6 if rec.n_channels > 4 else 0.9
        ax_b.plot(centers, rms_windowed[ch], linewidth=0.8, alpha=alpha,
                  label=f"CH{ch}" if rec.n_channels <= 8 else None)

    # Plot mean across channels
    mean_trace = np.mean(rms_windowed, axis=0)
    ax_b.plot(centers, mean_trace, color="black", linewidth=2, label="Mean")

    ax_b.set_xlabel("Time (s)")
    ax_b.set_ylabel("RMS Noise (µVrms)")
    ax_b.set_title(f"B) Noise Stability ({window_s:.0f}s windows)")
    ax_b.legend(fontsize=7, loc="upper right", ncol=2)
    ax_b.set_ylim(bottom=0)

    # ── Panel C: Summary statistics ──────────────────────────────
    ax_c = fig.add_subplot(gs[1, :])

    # Time-varying mean ± std band
    std_trace = np.std(rms_windowed, axis=0)
    ax_c.fill_between(centers, mean_trace - std_trace, mean_trace + std_trace,
                      alpha=0.3, color="steelblue", label="±1σ across channels")
    ax_c.plot(centers, mean_trace, color="steelblue", linewidth=2,
              label=f"Mean RMS (CV = {cv:.3f})")

    # Annotate overall statistics
    stats_text = (
        f"Channels: {rec.n_channels}\n"
        f"Mean noise: {mean_rms:.2f} µVrms\n"
        f"Std across channels: {std_rms:.2f} µVrms\n"
        f"CV: {cv:.3f}\n"
        f"Duration: {rec.duration_s:.1f} s\n"
        f"Sample rate: {rec.fs/1000:.0f} kS/s"
    )
    ax_c.text(0.02, 0.95, stats_text, transform=ax_c.transAxes,
              fontsize=9, verticalalignment="top",
              bbox=dict(boxstyle="round,pad=0.5", facecolor="lightyellow",
                        edgecolor="gray", alpha=0.9))

    ax_c.set_xlabel("Time (s)")
    ax_c.set_ylabel("RMS Noise (µVrms)")
    ax_c.set_title("C) Cross-Channel Consistency & Drift")
    ax_c.legend(fontsize=9, loc="upper right")
    ax_c.set_ylim(bottom=0)

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
    rec = load_synthetic("baseline", duration_s=30.0)
    make_figure(rec, output_path="out/figures/fig1_noise_stability.png")
    print("Figure 1 saved to out/figures/fig1_noise_stability.png")
