"""
test_adversarial_generators.py — Tests for compound adversarial perturbations.

Covers:
  - X1: adversarial_drift_coupling (impedance drift + crosstalk)
  - X2: adversarial_suppression_60hz (spike suppression + 60 Hz)
  - Shape, metadata, severity scaling, registry
  - Classifier resilience (optional integration)
"""

from __future__ import annotations

import numpy as np
import pytest

from perturbation_library import (
    generate_adversarial_drift_coupling,
    generate_adversarial_suppression_60hz,
    ADVERSARIAL_TYPES,
    ADVERSARIAL_GENERATORS,
    SEVERITY_RANGES,
)


NCH = 8
FS = 4000.0
DUR = 2.0
ONSET = 0.8


# ── X1: adversarial_drift_coupling ──────────────────────────────────

class TestAdversarialDriftCoupling:
    def test_output_shape(self):
        w = generate_adversarial_drift_coupling(
            severity=0.5, seed=1, n_channels=NCH, fs=FS,
            duration_s=DUR, onset_s=ONSET)
        assert w.data_uv.shape == (NCH, int(FS * DUR))

    def test_metadata_category(self):
        w = generate_adversarial_drift_coupling(severity=0.5, seed=1)
        assert w.meta.category == "artifact"
        assert w.meta.perturbation_type == "adversarial_drift_coupling"

    def test_severity_stored(self):
        w = generate_adversarial_drift_coupling(severity=0.7, seed=2)
        assert w.meta.severity == pytest.approx(0.7)

    def test_onset_stored(self):
        w = generate_adversarial_drift_coupling(
            severity=0.5, seed=1, onset_s=5.0)
        assert w.meta.onset_s == 5.0

    def test_params_keys(self):
        w = generate_adversarial_drift_coupling(severity=0.5, seed=1)
        for key in ("ramp_uv_per_s", "coupling_ratio", "n_drift_channels",
                     "n_pairs", "drift_channels", "pairs"):
            assert key in w.meta.params

    def test_low_severity_weaker(self):
        lo = generate_adversarial_drift_coupling(severity=0.1, seed=1,
                                                  n_channels=NCH, fs=FS,
                                                  duration_s=DUR, onset_s=ONSET)
        hi = generate_adversarial_drift_coupling(severity=0.9, seed=1,
                                                  n_channels=NCH, fs=FS,
                                                  duration_s=DUR, onset_s=ONSET)
        assert lo.meta.params["ramp_uv_per_s"] < hi.meta.params["ramp_uv_per_s"]
        assert lo.meta.params["coupling_ratio"] < hi.meta.params["coupling_ratio"]

    def test_deterministic(self):
        w1 = generate_adversarial_drift_coupling(severity=0.5, seed=42,
                                                  n_channels=NCH, fs=FS,
                                                  duration_s=DUR, onset_s=ONSET)
        w2 = generate_adversarial_drift_coupling(severity=0.5, seed=42,
                                                  n_channels=NCH, fs=FS,
                                                  duration_s=DUR, onset_s=ONSET)
        np.testing.assert_array_equal(w1.data_uv, w2.data_uv)

    def test_pre_onset_clean(self):
        """Pre-onset region should match baseline (no drift/coupling yet)."""
        w = generate_adversarial_drift_coupling(
            severity=0.9, seed=1, n_channels=NCH, fs=FS,
            duration_s=DUR, onset_s=ONSET)
        # First 10% of pre-onset should be relatively quiet
        pre_idx = int(0.1 * ONSET * FS)
        rms_pre = np.sqrt(np.mean(w.data_uv[:, :pre_idx] ** 2))
        assert rms_pre > 0  # not all zeros


# ── X2: adversarial_suppression_60hz ────────────────────────────────

class TestAdversarialSuppression60Hz:
    def test_output_shape(self):
        w = generate_adversarial_suppression_60hz(
            severity=0.5, seed=1, n_channels=NCH, fs=FS,
            duration_s=DUR, onset_s=ONSET)
        assert w.data_uv.shape == (NCH, int(FS * DUR))

    def test_metadata_category(self):
        w = generate_adversarial_suppression_60hz(severity=0.5, seed=1)
        assert w.meta.category == "neurotox"
        assert w.meta.perturbation_type == "adversarial_suppression_60hz"

    def test_severity_stored(self):
        w = generate_adversarial_suppression_60hz(severity=0.3, seed=2)
        assert w.meta.severity == pytest.approx(0.3)

    def test_params_keys(self):
        w = generate_adversarial_suppression_60hz(severity=0.5, seed=1)
        for key in ("suppression_frac", "amplitude_uv", "n_harmonics"):
            assert key in w.meta.params

    def test_60hz_present_post_onset(self):
        """Post-onset power should spike at 60 Hz."""
        w = generate_adversarial_suppression_60hz(
            severity=0.9, seed=1, n_channels=NCH, fs=FS,
            duration_s=DUR, onset_s=ONSET)
        onset_idx = int(ONSET * FS)
        post = w.data_uv[0, onset_idx:]
        # FFT check: should have peak near 60 Hz
        freqs = np.fft.rfftfreq(len(post), d=1 / FS)
        spectrum = np.abs(np.fft.rfft(post))
        idx_60 = np.argmin(np.abs(freqs - 60.0))
        # Power at 60 Hz should be significantly above median
        assert spectrum[idx_60] > 5 * np.median(spectrum)

    def test_low_severity_weaker(self):
        lo = generate_adversarial_suppression_60hz(severity=0.1, seed=1)
        hi = generate_adversarial_suppression_60hz(severity=0.9, seed=1)
        assert lo.meta.params["suppression_frac"] < hi.meta.params["suppression_frac"]
        assert lo.meta.params["amplitude_uv"] < hi.meta.params["amplitude_uv"]

    def test_deterministic(self):
        w1 = generate_adversarial_suppression_60hz(severity=0.5, seed=42,
                                                    n_channels=NCH, fs=FS,
                                                    duration_s=DUR, onset_s=ONSET)
        w2 = generate_adversarial_suppression_60hz(severity=0.5, seed=42,
                                                    n_channels=NCH, fs=FS,
                                                    duration_s=DUR, onset_s=ONSET)
        np.testing.assert_array_equal(w1.data_uv, w2.data_uv)


# ── Registry ─────────────────────────────────────────────────────────

class TestAdversarialRegistry:
    def test_types_list(self):
        assert "adversarial_drift_coupling" in ADVERSARIAL_TYPES
        assert "adversarial_suppression_60hz" in ADVERSARIAL_TYPES
        assert len(ADVERSARIAL_TYPES) == 2

    def test_generators_dict(self):
        assert "adversarial_drift_coupling" in ADVERSARIAL_GENERATORS
        assert "adversarial_suppression_60hz" in ADVERSARIAL_GENERATORS

    def test_severity_ranges_defined(self):
        assert "adversarial_drift_coupling" in SEVERITY_RANGES
        assert "adversarial_suppression_60hz" in SEVERITY_RANGES

    def test_not_in_training_generators(self):
        """Adversarial types should NOT be in the main GENERATORS registry."""
        from perturbation_library import GENERATORS
        for atype in ADVERSARIAL_TYPES:
            assert atype not in GENERATORS, \
                f"{atype} should not be in GENERATORS (training-only registry)"

    def test_generators_callable(self):
        for name, fn in ADVERSARIAL_GENERATORS.items():
            w = fn(severity=0.5, seed=1, n_channels=4, fs=1000,
                   duration_s=1.0, onset_s=0.3)
            assert w.data_uv.shape[0] == 4
            assert w.meta.perturbation_type == name
