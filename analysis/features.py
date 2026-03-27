"""
features.py — Windowed feature extraction for perturbation detection.

Turns (n_channels, n_samples) → (n_windows, n_features) feature matrix.

Three feature tiers:
  1. Per-channel metrics (computed per channel, then aggregated)
  2. Cross-channel metrics (relationships between channels)
  3. Aggregation statistics (mean, std, max, min across channels)

Designed for the two-stage detector:
  Stage 1 (change detection): track composite metric over windows
  Stage 2 (cause classification): full feature vector per window

Feature vector per window:
══════════════════════════════════════════
  Per-channel aggregated (mean/std/max):
    rms_mean, rms_std, rms_max                    3
    bp_low_mean, bp_low_std                        2  (1–30 Hz)
    bp_mid_mean, bp_mid_std                        2  (30–300 Hz)
    bp_high_mean, bp_high_std                      2  (300–3000 Hz)
    slope_mean, slope_std                          2
    spike_rate_mean, spike_rate_std, spike_rate_max 3
    kurtosis_mean                                  1
                                                  ──
                                            Total: 15

  Cross-channel:
    mean_pairwise_corr                             1
    spatial_cv_rms                                 1
    spatial_cv_spike_rate                           1
                                                  ──
                                            Total: 3

  Composite / derived:
    spike_to_noise_ratio    (spike_band_power / rms²)  1
    spectral_edge_freq      (95% power frequency)      1
                                                      ──
                                                Total: 2

  Artifact-discriminating (Option B):
    clip_fraction           % samples near ±max        1
    line_power_ratio        (P_60Hz + P_120Hz) / P_wideband  1
    spectral_flatness_hb    geometric/arithmetic mean PSD 300-3000Hz  1
                                                      ──
                                                Total: 3

  Grand total: 23 features per window
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import signal as sig
from scipy.stats import kurtosis as scipy_kurtosis

from neural_metrics import (
    noise_rms_uv,
    bandpower,
    spectral_slope,
    spike_rate,
    spectral_density,
    _ensure_uv,
)


# ── Feature names (stable ordering) ─────────────────────────────────

FEATURE_NAMES: list[str] = [
    # Per-channel aggregated
    "rms_mean",
    "rms_std",
    "rms_max",
    "bp_low_mean",
    "bp_low_std",
    "bp_mid_mean",
    "bp_mid_std",
    "bp_high_mean",
    "bp_high_std",
    "slope_mean",
    "slope_std",
    "spike_rate_mean",
    "spike_rate_std",
    "spike_rate_max",
    "kurtosis_mean",
    # Cross-channel
    "mean_pairwise_corr",
    "spatial_cv_rms",
    "spatial_cv_spike_rate",
    # Composite
    "spike_to_noise_ratio",
    "spectral_edge_freq",
    # Artifact-discriminating (Option B)
    "clip_fraction",
    "line_power_ratio",
    "spectral_flatness_hb",
]

N_FEATURES = len(FEATURE_NAMES)


def _safe_cv(arr: NDArray) -> float:
    """Coefficient of variation, handling zero mean."""
    m = np.mean(arr)
    if abs(m) < 1e-15:
        return 0.0
    return float(np.std(arr) / abs(m))


def _spectral_edge(data_uv: NDArray, fs: float, frac: float = 0.95) -> float:
    """Frequency below which `frac` of total power lies."""
    freqs, psd = spectral_density(data_uv, fs=fs, nperseg=min(2048, data_uv.shape[1]))
    total_psd = np.mean(psd, axis=0)  # average across channels
    cumulative = np.cumsum(total_psd)
    if cumulative[-1] < 1e-30:
        return 0.0
    cumulative /= cumulative[-1]
    idx = np.searchsorted(cumulative, frac)
    return float(freqs[min(idx, len(freqs) - 1)])


def _clip_fraction(
    data_uv: NDArray,
    rail_uv: float = 6400.0,
    threshold: float = 0.95,
) -> float:
    """Fraction of samples near the ADC rail (clipping signature).

    Uses a *fixed* rail reference (default ±6400 µV for Intan RHD2164)
    rather than the data's own max, so biological signals with no
    clipping always return ≈0 while genuinely clipped data returns
    a large fraction.

    Computed per channel, then averaged.
    """
    abs_data = np.abs(data_uv)
    clipped = abs_data > threshold * rail_uv
    return float(np.mean(clipped))


def _line_power_ratio(
    data_uv: NDArray, fs: float,
    line_hz: float = 60.0,
    n_side_bins: int = 1,
) -> float:
    """Ratio of 60 Hz + 120 Hz narrowband power to wideband power.

    Uses nearest-bin lookup with ±n_side_bins around 60 Hz and 120 Hz
    to be robust to coarse frequency resolution (Welch df ≈ 5 Hz).
    EMI / mains coupling concentrates energy at these frequencies;
    biological suppression does not.
    """
    nperseg = min(4096, data_uv.shape[1])
    freqs, psd = spectral_density(data_uv, fs=fs, nperseg=nperseg)
    mean_psd = np.mean(psd, axis=0)  # average across channels
    df = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    def _peak_power_near(target_hz: float) -> float:
        """Sum PSD in ±n_side_bins around the bin closest to target_hz."""
        idx = int(np.argmin(np.abs(freqs - target_hz)))
        lo = max(0, idx - n_side_bins)
        hi = min(len(freqs) - 1, idx + n_side_bins)
        return float(np.sum(mean_psd[lo:hi + 1]) * df)

    p_line = _peak_power_near(line_hz)
    p_harmonic = _peak_power_near(2 * line_hz)

    # Wideband: 5 Hz to min(200, Nyquist)
    wide_mask = (freqs >= 5.0) & (freqs <= min(200.0, fs / 2))
    p_wide = float(np.sum(mean_psd[wide_mask]) * df)

    if p_wide < 1e-30:
        return 0.0
    return float((p_line + p_harmonic) / p_wide)


def _spectral_flatness_highband(
    data_uv: NDArray, fs: float,
    flo: float = 300.0, fhi: float = 3000.0,
) -> float:
    """Spectral flatness (Wiener entropy) in the spike band (300-3000 Hz).

    Ratio of geometric mean to arithmetic mean of PSD values.
    Neural signals with spikes have structured spectra (low flatness ≈ 0.1-0.5)
    due to 1/f roll-off and spike waveforms.  Hardware artifacts that add
    flat-spectrum noise or aliasing push flatness toward 1.0.  Bandpass
    mismatch and sample-rate errors distort the spectral shape.
    """
    nperseg = min(4096, data_uv.shape[1])
    freqs, psd = spectral_density(data_uv, fs=fs, nperseg=nperseg)
    mean_psd = np.mean(psd, axis=0)
    fhi_eff = min(fhi, fs / 2)
    mask = (freqs >= flo) & (freqs <= fhi_eff)
    band = mean_psd[mask]

    if len(band) < 2:
        return 0.0
    # Replace zeros/negatives with floor to avoid log(0)
    band = np.maximum(band, 1e-30)

    log_mean = float(np.mean(np.log(band)))
    arith_mean = float(np.mean(band))
    if arith_mean < 1e-30:
        return 0.0
    return float(np.exp(log_mean) / arith_mean)


def _mean_pairwise_corr(data_uv: NDArray) -> float:
    """Mean pairwise Pearson correlation across channels."""
    n_ch = data_uv.shape[0]
    if n_ch < 2:
        return 0.0
    # Correlation matrix
    cc = np.corrcoef(data_uv)
    # Extract upper triangle (excluding diagonal)
    mask = np.triu(np.ones_like(cc, dtype=bool), k=1)
    vals = cc[mask]
    # Handle NaN from constant channels
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return 0.0
    return float(np.mean(vals))


# ── Single-window feature extraction ────────────────────────────────

def extract_window_features(
    data_uv: NDArray[np.float64],
    fs: float = 20_000.0,
) -> NDArray[np.float64]:
    """Extract feature vector from one (n_channels, n_samples) window.

    Returns:
        (N_FEATURES,) float64 array. Order matches FEATURE_NAMES.
    """
    n_ch, n_samp = data_uv.shape

    # ── Per-channel metrics ──────────────────────────────────────
    rms = noise_rms_uv(data_uv, fs=fs, highpass_hz=1.0)

    bp_low = bandpower(data_uv, fs=fs, band=(1.0, 30.0))
    bp_mid = bandpower(data_uv, fs=fs, band=(30.0, 300.0))
    bp_high = bandpower(data_uv, fs=fs, band=(300.0, 3000.0))

    slopes = spectral_slope(data_uv, fs=fs, freq_range=(30.0, 300.0))

    rates = spike_rate(data_uv, fs=fs, threshold_uv=-50.0, highpass_hz=300.0)

    kurt = scipy_kurtosis(data_uv, axis=1, fisher=True)

    # ── Cross-channel metrics ────────────────────────────────────
    pairwise_corr = _mean_pairwise_corr(data_uv)
    cv_rms = _safe_cv(rms)
    cv_spike_rate = _safe_cv(rates)

    # ── Composite metrics ────────────────────────────────────────
    rms_mean_sq = float(np.mean(rms)) ** 2
    snr = float(np.mean(bp_high)) / rms_mean_sq if rms_mean_sq > 1e-15 else 0.0
    spec_edge = _spectral_edge(data_uv, fs)

    # ── Artifact-discriminating features ─────────────────────────
    clip_frac = _clip_fraction(data_uv)
    line_ratio = _line_power_ratio(data_uv, fs)
    spec_flat = _spectral_flatness_highband(data_uv, fs)

    # ── Assemble feature vector ──────────────────────────────────
    features = np.array([
        float(np.mean(rms)),
        float(np.std(rms)),
        float(np.max(rms)),
        float(np.mean(bp_low)),
        float(np.std(bp_low)),
        float(np.mean(bp_mid)),
        float(np.std(bp_mid)),
        float(np.mean(bp_high)),
        float(np.std(bp_high)),
        float(np.mean(slopes)),
        float(np.std(slopes)),
        float(np.mean(rates)),
        float(np.std(rates)),
        float(np.max(rates)),
        float(np.mean(kurt)),
        pairwise_corr,
        cv_rms,
        cv_spike_rate,
        snr,
        spec_edge,
        clip_frac,
        line_ratio,
        spec_flat,
    ], dtype=np.float64)

    assert features.shape == (N_FEATURES,), f"Expected {N_FEATURES}, got {features.shape}"
    return features


# ── Windowed extraction over full recording ──────────────────────────

def extract_windowed_features(
    data_uv: NDArray[np.float64],
    fs: float = 20_000.0,
    window_s: float = 1.0,
    overlap: float = 0.5,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Extract features in sliding windows over a recording.

    Args:
        data_uv: (n_channels, n_samples) in µV
        fs: sample rate
        window_s: window length in seconds
        overlap: fraction of overlap (0.0 = no overlap, 0.5 = 50%)

    Returns:
        (window_centers, feature_matrix) where:
            window_centers: (n_windows,) time in seconds
            feature_matrix: (n_windows, N_FEATURES) float64
    """
    n_ch, n_samples = data_uv.shape
    win_samples = int(window_s * fs)
    step_samples = int(win_samples * (1.0 - overlap))
    if step_samples < 1:
        step_samples = 1

    n_windows = max(0, (n_samples - win_samples) // step_samples + 1)

    features = np.zeros((n_windows, N_FEATURES), dtype=np.float64)
    centers = np.zeros(n_windows, dtype=np.float64)

    for i in range(n_windows):
        start = i * step_samples
        end = start + win_samples
        chunk = data_uv[:, start:end]
        features[i] = extract_window_features(chunk, fs=fs)
        centers[i] = (start + end) / 2 / fs

    return centers, features


# ── Convenience: extract post-onset features from LabeledWindow ──────

def extract_trial_features(
    data_uv: NDArray[np.float64],
    fs: float,
    onset_s: float,
    window_s: float = 1.0,
    n_baseline_windows: int = 5,
    n_post_windows: int = 10,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Extract baseline and post-onset feature windows.

    Returns:
        (baseline_features, post_features) each (n_windows, N_FEATURES)
    """
    onset_idx = int(onset_s * fs)
    win_samples = int(window_s * fs)

    # Baseline: windows ending before onset
    baseline_start = max(0, onset_idx - n_baseline_windows * win_samples)
    baseline_data = data_uv[:, baseline_start:onset_idx]
    _, baseline_feat = extract_windowed_features(
        baseline_data, fs=fs, window_s=window_s, overlap=0.0,
    )

    # Post-onset: windows starting after onset
    post_start = onset_idx
    post_end = min(data_uv.shape[1], post_start + n_post_windows * win_samples)
    post_data = data_uv[:, post_start:post_end]
    _, post_feat = extract_windowed_features(
        post_data, fs=fs, window_s=window_s, overlap=0.0,
    )

    return baseline_feat, post_feat
