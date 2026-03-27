"""
neural_metrics.py — Core signal-quality and neurotox-detection metrics.

Computes the quantitative metrics needed for the three validation figures:

  Figure 1 — Noise & Stability:
    - noise_rms_uv():        Per-channel RMS noise in µV
    - noise_rms_windowed():  RMS in sliding windows (drift tracking)
    - spectral_density():    Per-channel PSD (µV²/Hz)

  Figure 2 — Crosstalk / Isolation:
    - crosstalk_matrix():    N×N coupling matrix in dB

  Figure 3 — Neurotox Detection:
    - bandpower():           Power in specified frequency band
    - spectral_slope():      1/f slope of PSD (log-log regression)
    - spike_rate():          Threshold-crossing spike rate
    - detect_perturbation(): Changepoint detection on sliding metric

All functions accept data in µV (float64) or ADC codes (int16).
When int16 is passed, automatic conversion to µV is performed.

Design principle: each function is stateless and operates on numpy arrays.
No object state, no side effects, no file I/O. Pure computation.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import signal as sig


# ── Constants ────────────────────────────────────────────────────────
ADC_LSB_UV = 0.195  # µV per LSB (Intan RHD)


def _ensure_uv(data: NDArray, axis_info: str = "data") -> NDArray[np.float64]:
    """Convert int16 ADC codes to µV if needed."""
    if data.dtype == np.int16:
        return data.astype(np.float64) * ADC_LSB_UV
    return data.astype(np.float64)


# ═══════════════════════════════════════════════════════════════════
#  FIGURE 1 — Noise & Stability
# ═══════════════════════════════════════════════════════════════════

def noise_rms_uv(
    data: NDArray,
    fs: float = 20_000.0,
    highpass_hz: float | None = 1.0,
) -> NDArray[np.float64]:
    """Per-channel RMS noise in µV.

    Args:
        data: (n_channels, n_samples) array, int16 or float64
        fs: sample rate in Hz
        highpass_hz: optional high-pass cutoff to remove DC/drift.
            Set to None to skip filtering.

    Returns:
        (n_channels,) array of RMS values in µV
    """
    uv = _ensure_uv(data)

    if highpass_hz is not None and highpass_hz > 0:
        sos = sig.butter(2, highpass_hz, btype="highpass", fs=fs, output="sos")
        uv = sig.sosfiltfilt(sos, uv, axis=-1)

    return np.sqrt(np.mean(uv**2, axis=-1))


def noise_rms_windowed(
    data: NDArray,
    fs: float = 20_000.0,
    window_s: float = 1.0,
    highpass_hz: float | None = 1.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Sliding-window RMS noise for drift tracking.

    Args:
        data: (n_channels, n_samples)
        fs: sample rate
        window_s: window length in seconds
        highpass_hz: optional high-pass cutoff

    Returns:
        (time_centers, rms_windows) where:
            time_centers: (n_windows,) in seconds
            rms_windows: (n_channels, n_windows) in µV
    """
    uv = _ensure_uv(data)

    if highpass_hz is not None and highpass_hz > 0:
        sos = sig.butter(2, highpass_hz, btype="highpass", fs=fs, output="sos")
        uv = sig.sosfiltfilt(sos, uv, axis=-1)

    win_samples = int(window_s * fs)
    n_channels, n_samples = uv.shape
    n_windows = n_samples // win_samples

    rms = np.zeros((n_channels, n_windows))
    centers = np.zeros(n_windows)

    for i in range(n_windows):
        start = i * win_samples
        end = start + win_samples
        chunk = uv[:, start:end]
        rms[:, i] = np.sqrt(np.mean(chunk**2, axis=-1))
        centers[i] = (start + end) / 2 / fs

    return centers, rms


def spectral_density(
    data: NDArray,
    fs: float = 20_000.0,
    nperseg: int = 4096,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Per-channel power spectral density via Welch's method.

    Args:
        data: (n_channels, n_samples)
        fs: sample rate
        nperseg: FFT segment length

    Returns:
        (freqs, psd) where:
            freqs: (n_freq,) in Hz
            psd: (n_channels, n_freq) in µV²/Hz
    """
    uv = _ensure_uv(data)
    freqs, psd = sig.welch(uv, fs=fs, nperseg=nperseg, axis=-1)
    return freqs, psd


# ═══════════════════════════════════════════════════════════════════
#  FIGURE 2 — Crosstalk / Isolation
# ═══════════════════════════════════════════════════════════════════

def crosstalk_matrix(
    data: NDArray,
    source_channel: int,
    fs: float = 20_000.0,
    tone_freq_hz: float = 1000.0,
    tone_bw_hz: float = 50.0,
) -> NDArray[np.float64]:
    """Compute crosstalk isolation relative to a source channel.

    Measures power at the tone frequency on each channel relative to
    the source channel. Returns isolation in dB (negative = good).

    Args:
        data: (n_channels, n_samples) — recording with tone on source_channel
        source_channel: index of the driven channel
        fs: sample rate
        tone_freq_hz: center frequency of injected tone
        tone_bw_hz: bandwidth around tone for power measurement

    Returns:
        (n_channels,) array of isolation in dB.
        source_channel will be 0 dB (reference).
    """
    uv = _ensure_uv(data)
    n_channels = uv.shape[0]

    # Bandpass around tone
    lo = tone_freq_hz - tone_bw_hz / 2
    hi = tone_freq_hz + tone_bw_hz / 2
    sos = sig.butter(4, [lo, hi], btype="bandpass", fs=fs, output="sos")
    filtered = sig.sosfiltfilt(sos, uv, axis=-1)

    # RMS power per channel in the tone band
    power = np.mean(filtered**2, axis=-1)

    # Reference power (source channel)
    ref_power = power[source_channel]

    # Isolation in dB
    with np.errstate(divide="ignore", invalid="ignore"):
        isolation_db = 10 * np.log10(power / ref_power)
        isolation_db = np.where(np.isfinite(isolation_db), isolation_db, -120.0)

    return isolation_db


# ═══════════════════════════════════════════════════════════════════
#  FIGURE 3 — Neurotox Detection Metrics
# ═══════════════════════════════════════════════════════════════════

def bandpower(
    data: NDArray,
    fs: float = 20_000.0,
    band: tuple[float, float] = (300.0, 3000.0),
    nperseg: int = 4096,
) -> NDArray[np.float64]:
    """Total power in a frequency band, per channel.

    Args:
        data: (n_channels, n_samples)
        fs: sample rate
        band: (low_hz, high_hz)
        nperseg: Welch segment length

    Returns:
        (n_channels,) power in µV² within the band
    """
    freqs, psd = spectral_density(data, fs, nperseg)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    df = freqs[1] - freqs[0]
    return np.sum(psd[:, mask] * df, axis=-1)


def bandpower_windowed(
    data: NDArray,
    fs: float = 20_000.0,
    band: tuple[float, float] = (300.0, 3000.0),
    window_s: float = 1.0,
    nperseg: int = 2048,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Sliding-window band power for perturbation tracking.

    Returns:
        (time_centers, bp_windows) where:
            time_centers: (n_windows,) in seconds
            bp_windows: (n_channels, n_windows) in µV²
    """
    uv = _ensure_uv(data)
    win_samples = int(window_s * fs)
    n_channels, n_samples = uv.shape
    n_windows = n_samples // win_samples

    bp = np.zeros((n_channels, n_windows))
    centers = np.zeros(n_windows)

    for i in range(n_windows):
        start = i * win_samples
        end = start + win_samples
        chunk = uv[:, start:end]
        bp[:, i] = bandpower(chunk, fs, band, min(nperseg, win_samples))
        centers[i] = (start + end) / 2 / fs

    return centers, bp


def spectral_slope(
    data: NDArray,
    fs: float = 20_000.0,
    freq_range: tuple[float, float] = (30.0, 300.0),
    nperseg: int = 4096,
) -> NDArray[np.float64]:
    """1/f spectral slope from log-log PSD regression.

    Fits log10(PSD) = slope * log10(freq) + intercept in the specified
    frequency range. Returns slope per channel.

    Steeper negative slope = more 1/f-like (healthy neural tissue).
    Flatter slope = more white noise (degraded / neurotoxic).

    Args:
        data: (n_channels, n_samples)
        fs: sample rate
        freq_range: (low_hz, high_hz) for fitting

    Returns:
        (n_channels,) slope values (typically negative)
    """
    freqs, psd = spectral_density(data, fs, nperseg)
    mask = (freqs >= freq_range[0]) & (freqs <= freq_range[1])

    log_f = np.log10(freqs[mask])
    log_psd = np.log10(psd[:, mask] + 1e-30)  # avoid log(0)

    n_channels = psd.shape[0]
    slopes = np.zeros(n_channels)

    for ch in range(n_channels):
        coeffs = np.polyfit(log_f, log_psd[ch], 1)
        slopes[ch] = coeffs[0]

    return slopes


def spike_rate(
    data: NDArray,
    fs: float = 20_000.0,
    threshold_uv: float = -50.0,
    refractory_ms: float = 1.0,
    highpass_hz: float = 300.0,
) -> NDArray[np.float64]:
    """Threshold-crossing spike rate per channel.

    Counts negative-going threshold crossings with a refractory period.

    Args:
        data: (n_channels, n_samples)
        fs: sample rate
        threshold_uv: detection threshold (negative for neg-going spikes)
        refractory_ms: minimum time between spikes
        highpass_hz: high-pass filter cutoff

    Returns:
        (n_channels,) spike rate in Hz
    """
    uv = _ensure_uv(data)
    n_channels, n_samples = uv.shape
    duration_s = n_samples / fs
    refractory_samples = int(refractory_ms * fs / 1000)

    # High-pass filter to isolate spike band
    sos = sig.butter(2, highpass_hz, btype="highpass", fs=fs, output="sos")
    filtered = sig.sosfiltfilt(sos, uv, axis=-1)

    rates = np.zeros(n_channels)

    for ch in range(n_channels):
        trace = filtered[ch]
        # Find negative threshold crossings
        below = trace < threshold_uv
        crossings = np.where(np.diff(below.astype(int)) == 1)[0]

        # Enforce refractory period
        if len(crossings) == 0:
            continue
        accepted = [crossings[0]]
        for c in crossings[1:]:
            if c - accepted[-1] >= refractory_samples:
                accepted.append(c)
        rates[ch] = len(accepted) / duration_s

    return rates


def spike_rate_windowed(
    data: NDArray,
    fs: float = 20_000.0,
    threshold_uv: float = -50.0,
    refractory_ms: float = 1.0,
    highpass_hz: float = 300.0,
    window_s: float = 1.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Sliding-window spike rate for perturbation tracking.

    Returns:
        (time_centers, rate_windows) where:
            time_centers: (n_windows,) in seconds
            rate_windows: (n_channels, n_windows) in Hz
    """
    uv = _ensure_uv(data)
    win_samples = int(window_s * fs)
    n_channels, n_samples = uv.shape
    n_windows = n_samples // win_samples

    rates = np.zeros((n_channels, n_windows))
    centers = np.zeros(n_windows)

    for i in range(n_windows):
        start = i * win_samples
        end = start + win_samples
        chunk = uv[:, start:end]
        rates[:, i] = spike_rate(
            chunk, fs, threshold_uv, refractory_ms, highpass_hz
        )
        centers[i] = (start + end) / 2 / fs

    return centers, rates


def detect_perturbation(
    metric_timeseries: NDArray[np.float64],
    baseline_end_idx: int,
    sigma_threshold: float = 3.0,
) -> tuple[int | None, float]:
    """Simple changepoint detection: find first sustained excursion.

    Computes mean and std of metric during baseline period, then finds
    the first point where the metric exceeds baseline_mean ± sigma_threshold * std
    for at least 3 consecutive windows.

    Args:
        metric_timeseries: (n_windows,) 1D metric over time
        baseline_end_idx: index marking end of baseline period
        sigma_threshold: number of standard deviations for detection

    Returns:
        (detection_idx, z_score) — index of first detection and its z-score.
        detection_idx is None if no perturbation detected.
    """
    baseline = metric_timeseries[:baseline_end_idx]
    mu = np.mean(baseline)
    std = np.std(baseline)

    if std < 1e-15:
        # No variance in baseline — can't detect anything
        return None, 0.0

    z_scores = (metric_timeseries - mu) / std

    # Look for 3 consecutive windows exceeding threshold
    exceed = np.abs(z_scores) > sigma_threshold
    consecutive = 0
    for i in range(baseline_end_idx, len(exceed)):
        if exceed[i]:
            consecutive += 1
            if consecutive >= 3:
                detection_idx = i - 2  # first of the 3
                return detection_idx, float(z_scores[detection_idx])
        else:
            consecutive = 0

    return None, 0.0
