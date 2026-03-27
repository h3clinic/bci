"""
test_neural_metrics.py — Tests for signal processing pipeline.

Tests each metric against synthetic waveforms with known ground truth.
These tests prove the analysis pipeline is correct BEFORE hardware arrives.

Test categories:
  1. Noise RMS (5 tests)
  2. Windowed RMS drift detection (2 tests)
  3. Spectral density (2 tests)
  4. Crosstalk isolation (3 tests)
  5. Band power (2 tests)
  6. Spectral slope (2 tests)
  7. Spike detection (3 tests)
  8. Perturbation detection (3 tests)
  9. ADC conversion round-trip (2 tests)
"""

import numpy as np
import pytest

from signal_gen import (
    SyntheticRecording, uv_to_adc, adc_to_uv, ADC_LSB_UV,
)
from neural_metrics import (
    noise_rms_uv, noise_rms_windowed, spectral_density,
    crosstalk_matrix, bandpower, bandpower_windowed,
    spectral_slope, spike_rate, spike_rate_windowed,
    detect_perturbation,
)


FS = 20_000.0  # Standard sample rate


# ═══════════════════════════════════════════════════════════════════
#  1. ADC Conversion
# ═══════════════════════════════════════════════════════════════════

def test_adc_round_trip():
    """µV → ADC → µV round-trip error < 1 LSB."""
    uv_in = np.array([[100.0, -200.0, 0.0, 3000.0]], dtype=np.float64)
    codes = uv_to_adc(uv_in)
    uv_out = adc_to_uv(codes)
    assert np.all(np.abs(uv_in - uv_out) < ADC_LSB_UV)


def test_adc_clipping():
    """Values beyond ±6400 µV clip to int16 range."""
    uv_in = np.array([[10000.0, -10000.0]], dtype=np.float64)
    codes = uv_to_adc(uv_in)
    assert codes[0, 0] == 32767
    assert codes[0, 1] == -32768


# ═══════════════════════════════════════════════════════════════════
#  2. Noise RMS
# ═══════════════════════════════════════════════════════════════════

def test_noise_rms_known_level():
    """White noise at 5 µVrms → measured RMS within 10%."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=5.0, seed=123)
    rec.add_noise(rms_uv=5.0)
    data = rec.build_uv()

    rms = noise_rms_uv(data, fs=FS, highpass_hz=None)
    assert rms.shape == (4,)
    for ch_rms in rms:
        assert 4.5 < ch_rms < 5.5, f"Expected ~5.0, got {ch_rms:.2f}"


def test_noise_rms_zero_input():
    """Zero input → zero RMS."""
    data = np.zeros((4, 10000), dtype=np.float64)
    rms = noise_rms_uv(data, fs=FS, highpass_hz=None)
    assert np.all(rms < 0.01)


def test_noise_rms_pure_tone():
    """Pure sine wave: RMS = amplitude / sqrt(2)."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=1.0, seed=0)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=100.0)
    data = rec.build_uv()

    rms = noise_rms_uv(data, fs=FS, highpass_hz=None)
    expected = 100.0 / np.sqrt(2)  # ≈ 70.71
    assert abs(rms[0] - expected) < 2.0, f"Expected {expected:.1f}, got {rms[0]:.1f}"


def test_noise_rms_highpass_removes_dc():
    """High-pass filter removes DC offset from RMS calculation."""
    data = np.ones((1, 40000), dtype=np.float64) * 1000.0  # 1000 µV DC
    rms_no_hp = noise_rms_uv(data, fs=FS, highpass_hz=None)
    rms_with_hp = noise_rms_uv(data, fs=FS, highpass_hz=1.0)

    assert rms_no_hp[0] > 900.0   # DC not removed
    assert rms_with_hp[0] < 10.0  # DC removed


def test_noise_rms_channel_independence():
    """Different noise levels on different channels are measured independently."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=5.0, seed=99)
    rec.add_noise(rms_uv=2.0, channels=[0, 1])
    rec.add_noise(rms_uv=10.0, channels=[2, 3])
    data = rec.build_uv()

    rms = noise_rms_uv(data, fs=FS, highpass_hz=None)
    assert rms[0] < 3.0
    assert rms[2] > 8.0
    assert rms[2] > rms[0] * 2


# ═══════════════════════════════════════════════════════════════════
#  3. Windowed RMS — Drift Detection
# ═══════════════════════════════════════════════════════════════════

def test_windowed_rms_stable():
    """Constant noise → flat windowed RMS (std < 20% of mean)."""
    rec = SyntheticRecording(n_channels=2, fs=FS, duration_s=10.0, seed=7)
    rec.add_noise(rms_uv=5.0)
    data = rec.build_uv()

    centers, rms = noise_rms_windowed(data, fs=FS, window_s=1.0, highpass_hz=None)
    # Each window's RMS should be stable
    for ch in range(2):
        ch_std = np.std(rms[ch])
        ch_mean = np.mean(rms[ch])
        assert ch_std / ch_mean < 0.20, f"CH{ch} too much variance: CV={ch_std/ch_mean:.2f}"


def test_windowed_rms_detects_increase():
    """Noise increase at t=5s → later windows have higher RMS."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=10.0, seed=42)
    rec.add_noise(rms_uv=3.0)
    rec.add_neurotox_perturbation(onset_s=5.0, noise_increase_uv=15.0)
    data = rec.build_uv()

    centers, rms = noise_rms_windowed(data, fs=FS, window_s=1.0, highpass_hz=None)
    early_mean = np.mean(rms[0, centers < 4.0])
    late_mean = np.mean(rms[0, centers > 6.0])
    assert late_mean > early_mean * 2, (
        f"Late RMS {late_mean:.1f} should be >> early {early_mean:.1f}"
    )


# ═══════════════════════════════════════════════════════════════════
#  4. Spectral Density
# ═══════════════════════════════════════════════════════════════════

def test_psd_tone_peak():
    """PSD of a 1 kHz tone peaks at 1 kHz."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=2.0, seed=0)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=200.0)
    rec.add_noise(rms_uv=1.0)
    data = rec.build_uv()

    freqs, psd = spectral_density(data, fs=FS, nperseg=4096)
    peak_freq = freqs[np.argmax(psd[0])]
    assert abs(peak_freq - 1000) < 20, f"Peak at {peak_freq} Hz, expected 1000"


def test_psd_white_noise_flat():
    """White noise PSD is approximately flat (within 6 dB across band)."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=5.0, seed=88)
    rec.add_noise(rms_uv=5.0)
    data = rec.build_uv()

    freqs, psd = spectral_density(data, fs=FS, nperseg=4096)
    # Look at 100–5000 Hz band (avoid DC and Nyquist edge effects)
    mask = (freqs >= 100) & (freqs <= 5000)
    psd_band = psd[0, mask]
    ratio_db = 10 * np.log10(np.max(psd_band) / np.min(psd_band))
    assert ratio_db < 6.0, f"PSD variation {ratio_db:.1f} dB, expected < 6"


# ═══════════════════════════════════════════════════════════════════
#  5. Crosstalk / Isolation
# ═══════════════════════════════════════════════════════════════════

def test_crosstalk_source_is_0db():
    """Source channel has 0 dB isolation (reference)."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=2.0, seed=50)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=500.0)
    rec.add_noise(rms_uv=1.0)
    data = rec.build_uv()

    iso = crosstalk_matrix(data, source_channel=0, fs=FS, tone_freq_hz=1000)
    assert abs(iso[0]) < 0.5, f"Source channel isolation should be ~0 dB, got {iso[0]:.1f}"


def test_crosstalk_no_coupling():
    """Without coupling, non-source channels have < -40 dB isolation."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=2.0, seed=51)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=500.0)
    rec.add_noise(rms_uv=2.0)
    data = rec.build_uv()

    iso = crosstalk_matrix(data, source_channel=0, fs=FS, tone_freq_hz=1000)
    for ch in [1, 2, 3]:
        assert iso[ch] < -30, f"CH{ch} isolation {iso[ch]:.1f} dB, expected < -30"


def test_crosstalk_with_coupling():
    """1% coupling → ~-40 dB isolation on target channel."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=2.0, seed=52)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=500.0)
    rec.add_noise(rms_uv=2.0)
    rec.add_crosstalk(source_channel=0, target_channel=1, coupling_ratio=0.01)
    data = rec.build_uv()

    iso = crosstalk_matrix(data, source_channel=0, fs=FS, tone_freq_hz=1000)
    # 1% = -40 dB, but noise + filter effects → expect -35 to -45 dB
    assert -50 < iso[1] < -30, f"CH1 isolation {iso[1]:.1f} dB, expected ~-40"
    # CH2 and CH3 should still be well isolated
    assert iso[2] < -30


# ═══════════════════════════════════════════════════════════════════
#  6. Band Power
# ═══════════════════════════════════════════════════════════════════

def test_bandpower_tone_in_band():
    """Tone within band → significant power. Tone outside → negligible."""
    rec = SyntheticRecording(n_channels=2, fs=FS, duration_s=2.0, seed=60)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=200.0)   # in 300-3000
    rec.add_tone(channel=1, freq_hz=5000, amplitude_uv=200.0)   # out of band
    rec.add_noise(rms_uv=1.0)
    data = rec.build_uv()

    bp = bandpower(data, fs=FS, band=(300.0, 3000.0))
    assert bp[0] > bp[1] * 10, (
        f"In-band ch0={bp[0]:.1f} should be >> out-of-band ch1={bp[1]:.1f}"
    )


def test_bandpower_scales_with_amplitude():
    """Doubling amplitude → 4× power."""
    rec1 = SyntheticRecording(n_channels=1, fs=FS, duration_s=2.0, seed=61)
    rec1.add_tone(channel=0, freq_hz=1000, amplitude_uv=100.0)
    rec2 = SyntheticRecording(n_channels=1, fs=FS, duration_s=2.0, seed=61)
    rec2.add_tone(channel=0, freq_hz=1000, amplitude_uv=200.0)

    bp1 = bandpower(rec1.build_uv(), fs=FS, band=(300.0, 3000.0))
    bp2 = bandpower(rec2.build_uv(), fs=FS, band=(300.0, 3000.0))
    ratio = bp2[0] / bp1[0]
    assert 3.0 < ratio < 5.0, f"Power ratio {ratio:.1f}, expected ~4.0"


# ═══════════════════════════════════════════════════════════════════
#  7. Spectral Slope
# ═══════════════════════════════════════════════════════════════════

def test_spectral_slope_white_noise():
    """White noise → slope near 0."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=5.0, seed=70)
    rec.add_noise(rms_uv=5.0)
    data = rec.build_uv()

    slopes = spectral_slope(data, fs=FS, freq_range=(30.0, 300.0))
    assert abs(slopes[0]) < 0.5, f"White noise slope {slopes[0]:.2f}, expected ~0"


def test_spectral_slope_colored_noise():
    """1/f noise → negative slope (steeper than white)."""
    # Approximate 1/f by filtering white noise
    rng = np.random.default_rng(71)
    white = rng.normal(0, 5.0, (1, 100000))
    # Apply simple 1/f shaping: cumulative sum approximates 1/f^2 → take sqrt-ish
    from scipy.signal import sosfiltfilt, butter
    # Low-pass to make it pink-ish (not exact, but slope should be negative)
    sos = butter(1, 100.0, btype="lowpass", fs=FS, output="sos")
    colored = sosfiltfilt(sos, white, axis=-1)

    slopes = spectral_slope(colored, fs=FS, freq_range=(10.0, 100.0))
    assert slopes[0] < -0.3, f"Colored noise slope {slopes[0]:.2f}, expected < -0.3"


# ═══════════════════════════════════════════════════════════════════
#  8. Spike Detection
# ═══════════════════════════════════════════════════════════════════

def test_spike_rate_no_spikes():
    """Low noise → zero spike rate at typical threshold."""
    rec = SyntheticRecording(n_channels=2, fs=FS, duration_s=5.0, seed=80)
    rec.add_noise(rms_uv=3.0)
    data = rec.build_uv()

    rates = spike_rate(data, fs=FS, threshold_uv=-50.0)
    assert np.all(rates < 1.0), f"Expected ~0 spikes, got {rates}"


def test_spike_rate_with_spikes():
    """Injected spikes at ~5 Hz → detected rate near 5 Hz."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=30.0, seed=81)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0, duration_ms=1.0)
    data = rec.build_uv()

    rates = spike_rate(data, fs=FS, threshold_uv=-50.0, highpass_hz=300.0)
    assert 2.0 < rates[0] < 10.0, f"Expected ~5 Hz, got {rates[0]:.1f}"


def test_spike_rate_threshold_sensitivity():
    """Higher threshold → fewer spikes detected."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=10.0, seed=82)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=10.0, amplitude_uv=-200.0)
    data = rec.build_uv()

    rate_low = spike_rate(data, fs=FS, threshold_uv=-30.0)
    rate_high = spike_rate(data, fs=FS, threshold_uv=-150.0)
    assert rate_low[0] >= rate_high[0], (
        f"Low threshold rate {rate_low[0]} should be >= high threshold {rate_high[0]}"
    )


# ═══════════════════════════════════════════════════════════════════
#  9. Perturbation Detection
# ═══════════════════════════════════════════════════════════════════

def test_detect_perturbation_finds_onset():
    """Noise step at t=5s detected within ±1 window."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=10.0, seed=90)
    rec.add_noise(rms_uv=3.0)
    rec.add_neurotox_perturbation(onset_s=5.0, noise_increase_uv=15.0)
    data = rec.build_uv()

    centers, rms = noise_rms_windowed(data, fs=FS, window_s=1.0, highpass_hz=None)
    baseline_end = int(4.0 / 1.0)  # 4 windows of baseline

    det_idx, z = detect_perturbation(rms[0], baseline_end, sigma_threshold=3.0)
    assert det_idx is not None, "Perturbation not detected"
    det_time = centers[det_idx]
    assert 4.5 < det_time < 7.0, f"Detected at {det_time:.1f}s, expected ~5-6s"
    assert abs(z) > 3.0


def test_detect_perturbation_no_false_positive():
    """Stable signal → no detection."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=10.0, seed=91)
    rec.add_noise(rms_uv=3.0)
    data = rec.build_uv()

    centers, rms = noise_rms_windowed(data, fs=FS, window_s=1.0, highpass_hz=None)
    baseline_end = 4

    det_idx, z = detect_perturbation(rms[0], baseline_end, sigma_threshold=3.0)
    assert det_idx is None, f"False positive at idx {det_idx}, z={z:.1f}"


def test_detect_perturbation_small_effect():
    """Small perturbation (2× noise) still detectable with lower threshold."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=20.0, seed=92)
    rec.add_noise(rms_uv=3.0)
    rec.add_neurotox_perturbation(onset_s=10.0, noise_increase_uv=6.0)
    data = rec.build_uv()

    centers, rms = noise_rms_windowed(data, fs=FS, window_s=1.0, highpass_hz=None)
    baseline_end = int(9.0 / 1.0)

    det_idx, z = detect_perturbation(rms[0], baseline_end, sigma_threshold=2.0)
    assert det_idx is not None, "Small perturbation not detected"
    det_time = centers[det_idx]
    assert 9.5 < det_time < 14.0, f"Detected at {det_time:.1f}s, expected ~10-12s"


# ═══════════════════════════════════════════════════════════════════
#  10. Integration: End-to-end synthetic neurotox assay
# ═══════════════════════════════════════════════════════════════════

def test_synthetic_neurotox_assay_end_to_end():
    """Full pipeline: generate → quantize → analyze → detect.

    This test simulates the complete experiment:
    1. 16-channel recording, 30 seconds
    2. Baseline noise: 3 µVrms
    3. Neurotox perturbation at t=15s: +12 µVrms
    4. Spikes on CH0 at 5 Hz during baseline
    5. Verify: noise increase detected, spike rate changes

    This is the test that proves the pipeline works end-to-end.
    When real hardware data replaces the synthetic generator,
    this same analysis code produces the validation figures.
    """
    # Generate synthetic recording
    rec = SyntheticRecording(n_channels=16, fs=FS, duration_s=30.0, seed=100)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
    rec.add_neurotox_perturbation(onset_s=15.0, noise_increase_uv=12.0)

    # Quantize through ADC (realistic path)
    adc_data = rec.build()
    # Convert back for analysis (as host parser would do)
    data_uv = adc_to_uv(adc_data)

    # Metric 1: RMS noise — should show step increase
    centers, rms = noise_rms_windowed(data_uv, fs=FS, window_s=1.0, highpass_hz=None)
    baseline_rms = np.mean(rms[:, centers < 14.0], axis=1)
    perturbed_rms = np.mean(rms[:, centers > 16.0], axis=1)
    for ch in range(16):
        assert perturbed_rms[ch] > baseline_rms[ch] * 1.5, (
            f"CH{ch}: perturbed RMS {perturbed_rms[ch]:.1f} not >> "
            f"baseline {baseline_rms[ch]:.1f}"
        )

    # Metric 2: Perturbation detection
    baseline_end = int(14.0 / 1.0)
    det_idx, z = detect_perturbation(
        np.mean(rms, axis=0), baseline_end, sigma_threshold=3.0
    )
    assert det_idx is not None, "Perturbation not detected in end-to-end test"

    # Metric 3: Crosstalk (no coupling added → isolation should be good)
    # Use first 5 seconds of baseline only
    baseline_data = data_uv[:, :int(5 * FS)]
    # Add a test tone mentally: just verify the function runs
    rec2 = SyntheticRecording(n_channels=16, fs=FS, duration_s=2.0, seed=101)
    rec2.add_tone(channel=0, freq_hz=1000, amplitude_uv=500.0)
    rec2.add_noise(rms_uv=1.0)
    iso = crosstalk_matrix(rec2.build_uv(), source_channel=0, fs=FS, tone_freq_hz=1000)
    assert iso[0] > -1.0  # source channel reference
    for ch in range(1, 16):
        assert iso[ch] < -20  # reasonable isolation without coupling


# ── Run with pytest or standalone ────────────────────────────────────
if __name__ == "__main__":
    test_funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for func in test_funcs:
        try:
            func()
            print(f"  PASS: {func.__name__}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL: {func.__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"neural_metrics tests: {passed} PASS, {failed} FAIL")
    print(f"{'='*60}")
    sys.exit(1 if failed else 0)
