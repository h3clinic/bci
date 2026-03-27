"""
fig2_crosstalk.py — Figure 2: Channel Isolation & Crosstalk.

Produces a 3-panel figure:
  A) N×N heatmap: isolation matrix in dB
  B) Bar chart: per-channel isolation relative to source
  C) Time-domain overlay: source channel vs worst-case leakage channel

This is the "professional instrument builder" figure.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

from data_loader import Recording
from neural_metrics import crosstalk_matrix, spectral_density


def make_figure(
    rec: Recording,
    source_channel: int = 0,
    tone_freq_hz: float = 1000.0,
    tone_bw_hz: float = 50.0,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Generate the 3-panel crosstalk figure.

    Args:
        rec: Recording with a tone injected on source_channel.
        source_channel: Which channel carries the test tone.
        tone_freq_hz: Frequency of injected tone.
        tone_bw_hz: Bandwidth for tone power measurement.
        output_path: If provided, save figure.
        dpi: Output resolution.

    Returns:
        matplotlib Figure object.
    """
    n_ch = rec.n_channels

    # ── Compute isolation vector ─────────────────────────────────
    iso_db = crosstalk_matrix(
        rec.data_uv, source_channel=source_channel,
        fs=rec.fs, tone_freq_hz=tone_freq_hz, tone_bw_hz=tone_bw_hz,
    )

    # ── Build full N×N matrix (drive each channel conceptually) ──
    # For now, we only have one driven channel.
    # Full matrix: row = source, col = measured.
    # We fill the source_channel row and leave others as -inf.
    full_matrix = np.full((n_ch, n_ch), -80.0)
    full_matrix[source_channel, :] = iso_db
    np.fill_diagonal(full_matrix, 0.0)

    # Worst-case coupling (excluding source)
    non_source = [ch for ch in range(n_ch) if ch != source_channel]
    worst_ch = non_source[np.argmax(iso_db[non_source])]
    worst_db = iso_db[worst_ch]

    # ── Layout ───────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(
        "Figure 2 — Channel Isolation & Crosstalk",
        fontsize=14, fontweight="bold", y=0.98,
    )

    gs = gridspec.GridSpec(2, 2, hspace=0.35, wspace=0.3,
                           left=0.08, right=0.95, top=0.92, bottom=0.08)

    # ── Panel A: Heatmap ─────────────────────────────────────────
    ax_a = fig.add_subplot(gs[0, 0])
    im = ax_a.imshow(
        full_matrix, cmap="RdYlGn_r", vmin=-80, vmax=0,
        aspect="auto", interpolation="nearest",
    )
    ax_a.set_xlabel("Measured Channel")
    ax_a.set_ylabel("Source Channel")
    ax_a.set_title(f"A) Isolation Matrix (dB) — Tone at {tone_freq_hz:.0f} Hz")
    ax_a.set_xticks(range(n_ch))
    ax_a.set_yticks(range(n_ch))
    ax_a.set_xticklabels(range(n_ch), fontsize=7)
    ax_a.set_yticklabels(range(n_ch), fontsize=7)

    cbar = fig.colorbar(im, ax=ax_a, shrink=0.8)
    cbar.set_label("Isolation (dB)")

    # Highlight source row
    ax_a.axhline(source_channel - 0.5, color="white", linewidth=2)
    ax_a.axhline(source_channel + 0.5, color="white", linewidth=2)

    # ── Panel B: Bar chart of isolation ──────────────────────────
    ax_b = fig.add_subplot(gs[0, 1])
    channels = list(range(n_ch))
    colors = ["red" if ch == source_channel else
              "orange" if ch == worst_ch else
              "steelblue" for ch in channels]

    ax_b.bar(channels, iso_db, color=colors, width=0.7)
    ax_b.axhline(-40, color="green", linestyle="--", linewidth=1,
                 label="–40 dB target")
    ax_b.axhline(-60, color="gray", linestyle=":", linewidth=1,
                 label="–60 dB ideal")

    ax_b.set_xlabel("Channel")
    ax_b.set_ylabel("Isolation (dB)")
    ax_b.set_title(f"B) Per-Channel Isolation (Source: CH{source_channel})")
    ax_b.set_xticks(channels)
    ax_b.set_xticklabels([str(c) for c in channels], fontsize=7)
    ax_b.legend(fontsize=8)

    # Annotate worst case
    ax_b.annotate(
        f"Worst: CH{worst_ch}\n{worst_db:.1f} dB",
        xy=(worst_ch, worst_db), xytext=(worst_ch + 2, worst_db + 10),
        fontsize=8, color="orange",
        arrowprops=dict(arrowstyle="->", color="orange"),
    )

    # ── Panel C: Time-domain overlay ─────────────────────────────
    ax_c = fig.add_subplot(gs[1, :])

    # Show 10 ms of data at tone frequency
    show_samples = int(0.01 * rec.fs)  # 10 ms
    t_ms = np.arange(show_samples) / rec.fs * 1000

    # Normalize for overlay visibility
    source_trace = rec.data_uv[source_channel, :show_samples]
    worst_trace = rec.data_uv[worst_ch, :show_samples]

    ax_c.plot(t_ms, source_trace, color="red", linewidth=1.5,
              label=f"CH{source_channel} (source)", alpha=0.9)

    # Scale worst-case to show coupling (amplify for visibility)
    scale = np.max(np.abs(source_trace)) / max(np.max(np.abs(worst_trace)), 1e-10)
    ax_c.plot(t_ms, worst_trace * min(scale * 0.5, 100), color="orange",
              linewidth=1, label=f"CH{worst_ch} (×{min(scale*0.5, 100):.0f})",
              alpha=0.8)

    ax_c.set_xlabel("Time (ms)")
    ax_c.set_ylabel("Amplitude (µV)")
    ax_c.set_title(
        f"C) Source (CH{source_channel}) vs Worst Leakage "
        f"(CH{worst_ch}, {worst_db:.1f} dB)"
    )
    ax_c.legend(fontsize=9)

    # Summary box
    stats_text = (
        f"Test tone: {tone_freq_hz:.0f} Hz, "
        f"Source: CH{source_channel}\n"
        f"Worst coupling: CH{worst_ch} = {worst_db:.1f} dB\n"
        f"Median isolation: {np.median(iso_db[non_source]):.1f} dB\n"
        f"Sample rate: {rec.fs/1000:.0f} kS/s, "
        f"Channels: {n_ch}"
    )
    ax_c.text(0.98, 0.95, stats_text, transform=ax_c.transAxes,
              fontsize=9, verticalalignment="top", horizontalalignment="right",
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
    rec = load_synthetic("crosstalk", duration_s=5.0)
    make_figure(rec, source_channel=0, tone_freq_hz=1000.0,
                output_path="out/figures/fig2_crosstalk.png")
    print("Figure 2 saved to out/figures/fig2_crosstalk.png")
