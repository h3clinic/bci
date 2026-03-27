"""
fig5_perturbation_gallery.py — Figure 5: Neurotoxicity Perturbation Models.

Produces a 4×3 panel figure (4 rows × 3 columns):
  Row 1: Amplitude suppression (synaptic depression)
  Row 2: Noise elevation (ion channel disruption)
  Row 3: Burst fragmentation (Na+ channel block)
  Row 4: Firing rate suppression (GABAergic inhibition)

  Col A: Raw waveform (2 s baseline + 3 s perturbed)
  Col B: Power spectral density (baseline vs perturbed)
  Col C: Feature comparison bar chart (control vs mild vs severe)

This is the "perturbation biology" figure — maps hardware-level signal
manipulations to neurotoxicological mechanisms.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

from signal_gen import SyntheticRecording
from neural_metrics import noise_rms_uv, spectral_density, bandpower, spike_rate


# Perturbation configs: (method_name, kwargs_mild, kwargs_severe, bio_label)
PERTURBATION_GALLERY = [
    (
        "Amplitude Suppression\n(Synaptic Depression)",
        "add_amplitude_suppression",
        {"suppression_frac": 0.2},
        {"suppression_frac": 0.5},
    ),
    (
        "Noise Elevation\n(Ion Channel Disruption)",
        "add_noise_elevation",
        {"noise_increase_uv": 3.0},
        {"noise_increase_uv": 8.0},
    ),
    (
        "Burst Fragmentation\n(Na⁺ Channel Block)",
        "add_burst_fragmentation",
        {"dropout_prob": 0.15},
        {"dropout_prob": 0.45},
    ),
    (
        "Firing Rate Suppression\n(GABAergic Inhibition)",
        "add_firing_rate_suppression",
        {"suppression_frac": 0.3},
        {"suppression_frac": 0.7},
    ),
]


def _make_recording(method_name: str, kwargs: dict, seed: int = 42,
                    onset_s: float = 2.0, duration_s: float = 5.0,
                    fs: float = 20_000.0) -> np.ndarray:
    """Create a single-channel recording with perturbation applied."""
    rec = SyntheticRecording(n_channels=1, fs=fs, duration_s=duration_s, seed=seed)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=8.0, amplitude_uv=-200.0)
    getattr(rec, method_name)(onset_s=onset_s, **kwargs)
    return rec.build_uv()


def make_figure(
    output_path: str | Path | None = None,
    dpi: int = 200,
    seed: int = 42,
    fs: float = 20_000.0,
    duration_s: float = 5.0,
    onset_s: float = 2.0,
) -> plt.Figure:
    """Generate the 4×3 perturbation gallery figure.

    Args:
        output_path: If provided, save figure.
        dpi: Output resolution.
        seed: Random seed.
        fs: Sample rate.
        duration_s: Total recording duration.
        onset_s: Perturbation onset time.

    Returns:
        matplotlib Figure object.
    """
    fig = plt.figure(figsize=(18, 20))
    fig.suptitle(
        "Figure 5 — Neurotoxicity Perturbation Models: Waveforms, Spectra, and Features",
        fontsize=14, fontweight="bold", y=0.98,
    )

    outer_gs = gridspec.GridSpec(4, 1, hspace=0.35,
                                 left=0.06, right=0.96, top=0.94, bottom=0.04)

    for row_idx, (title, method, mild_kw, severe_kw) in enumerate(PERTURBATION_GALLERY):
        inner_gs = gridspec.GridSpecFromSubplotSpec(
            1, 3, subplot_spec=outer_gs[row_idx],
            wspace=0.3,
        )

        # Generate control, mild, severe recordings
        control = SyntheticRecording(n_channels=1, fs=fs, duration_s=duration_s, seed=seed)
        control.add_noise(rms_uv=3.0)
        control.add_spikes(channel=0, rate_hz=8.0, amplitude_uv=-200.0)
        data_control = control.build_uv()

        data_mild = _make_recording(method, mild_kw, seed=seed,
                                     onset_s=onset_s, duration_s=duration_s, fs=fs)
        data_severe = _make_recording(method, severe_kw, seed=seed,
                                       onset_s=onset_s, duration_s=duration_s, fs=fs)

        time_axis = np.arange(int(fs * duration_s)) / fs
        onset_idx = int(onset_s * fs)

        # ── Column A: Raw waveform ───────────────────────────────
        ax_a = fig.add_subplot(inner_gs[0])

        # Show 200ms snippets: baseline vs perturbed
        snippet_len = int(0.2 * fs)  # 200 ms
        base_start = int(1.0 * fs)   # 1s into baseline
        pert_start = int(3.5 * fs)   # 1.5s into perturbed

        t_base = np.arange(snippet_len) / fs * 1000  # ms
        t_pert = t_base + 250  # offset for display

        severe_base = data_severe[0, base_start:base_start+snippet_len]
        severe_pert = data_severe[0, pert_start:pert_start+snippet_len]

        ax_a.plot(t_base, severe_base, color="#2196F3", linewidth=0.5,
                  alpha=0.9, label="Baseline")
        ax_a.plot(t_pert, severe_pert, color="#F44336", linewidth=0.5,
                  alpha=0.9, label="Perturbed (severe)")

        ax_a.axvspan(t_pert[0], t_pert[-1], alpha=0.08, color="red")
        ax_a.set_xlabel("Time (ms)")
        ax_a.set_ylabel("Amplitude (µV)")
        ax_a.set_title(f"{title}", fontsize=10, fontweight="bold")
        ax_a.legend(fontsize=7, loc="upper right")
        ax_a.grid(True, alpha=0.2)

        # ── Column B: PSD comparison ─────────────────────────────
        ax_b = fig.add_subplot(inner_gs[1])

        # Compute PSD for post-onset windows
        post_control = data_control[:, onset_idx:]
        post_mild = data_mild[:, onset_idx:]
        post_severe = data_severe[:, onset_idx:]

        freqs_c, psd_c = spectral_density(post_control, fs=fs, nperseg=2048)
        freqs_m, psd_m = spectral_density(post_mild, fs=fs, nperseg=2048)
        freqs_s, psd_s = spectral_density(post_severe, fs=fs, nperseg=2048)

        mask = (freqs_c >= 10) & (freqs_c <= 5000)
        ax_b.semilogy(freqs_c[mask], psd_c[0, mask], color="#4CAF50",
                       linewidth=1.2, label="Control")
        ax_b.semilogy(freqs_m[mask], psd_m[0, mask], color="#FF9800",
                       linewidth=1.2, label="Mild", alpha=0.8)
        ax_b.semilogy(freqs_s[mask], psd_s[0, mask], color="#F44336",
                       linewidth=1.2, label="Severe", alpha=0.8)

        ax_b.set_xlabel("Frequency (Hz)")
        ax_b.set_ylabel("PSD (µV²/Hz)")
        ax_b.set_title("Power Spectral Density")
        ax_b.legend(fontsize=7)
        ax_b.grid(True, alpha=0.2)

        # ── Column C: Feature bars ───────────────────────────────
        ax_c = fig.add_subplot(inner_gs[2])

        # Compute 3 key features for each condition
        metrics = {}
        for label, data in [("Control", post_control),
                             ("Mild", post_mild),
                             ("Severe", post_severe)]:
            rms = float(np.mean(noise_rms_uv(data, fs=fs, highpass_hz=None)))
            bp = float(np.mean(bandpower(data, fs=fs, band=(300.0, 3000.0))))
            sr = float(np.mean(spike_rate(data, fs=fs, threshold_uv=-50.0)))
            metrics[label] = [rms, bp, sr]

        x = np.arange(3)
        width = 0.25
        colors_bar = ["#4CAF50", "#FF9800", "#F44336"]
        labels_bar = ["Control", "Mild", "Severe"]
        metric_names = ["RMS (µV)", "Spike-band\nPower", "Spike Rate\n(Hz)"]

        for i, (lbl, clr) in enumerate(zip(labels_bar, colors_bar)):
            vals = metrics[lbl]
            # Normalize for display (different scales)
            ax_c.bar(x + i * width, vals, width, label=lbl, color=clr, alpha=0.8)

        ax_c.set_xticks(x + width)
        ax_c.set_xticklabels(metric_names, fontsize=8)
        ax_c.set_title("Key Metrics")
        ax_c.legend(fontsize=7, loc="upper right")
        ax_c.grid(True, alpha=0.2, axis="y")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    return fig
