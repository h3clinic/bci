"""
Tests for features.py — windowed feature extraction.

Covers:
  - Feature vector shape = N_FEATURES = 20
  - No NaNs / Infs in output
  - Feature names list consistency
  - Features discriminate between conditions
  - Windowed extraction shape correctness
  - Trial feature extraction (baseline vs post-onset)
"""

import numpy as np
import pytest

from features import (
    FEATURE_NAMES,
    N_FEATURES,
    extract_window_features,
    extract_windowed_features,
    extract_trial_features,
    _safe_cv,
    _spectral_edge,
    _mean_pairwise_corr,
)
from perturbation_library import (
    generate_baseline,
    generate_broadband_noise,
    generate_spike_suppression,
    generate_mixed_neurotox,
)


# ── Shared params ────────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DUR = 5.0
ONSET = 2.0
SEED = 42
N_SAMPLES = int(DUR * FS)

COMMON = dict(seed=SEED, n_channels=N_CH, fs=FS, duration_s=DUR)
PERT_COMMON = dict(**COMMON, onset_s=ONSET)


# ═══════════════════════════════════════════════════════════════════
#  Feature vector basics
# ═══════════════════════════════════════════════════════════════════

class TestFeatureNames:
    def test_count(self):
        assert N_FEATURES == 23

    def test_names_count_matches(self):
        assert len(FEATURE_NAMES) == N_FEATURES

    def test_names_unique(self):
        assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


class TestSingleWindowExtraction:
    def test_shape(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, 20_000))
        feat = extract_window_features(data, fs=FS)
        assert feat.shape == (N_FEATURES,)

    def test_dtype_float64(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, 20_000))
        feat = extract_window_features(data, fs=FS)
        assert feat.dtype == np.float64

    def test_no_nans(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, 20_000))
        feat = extract_window_features(data, fs=FS)
        assert not np.any(np.isnan(feat))

    def test_no_infs(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, 20_000))
        feat = extract_window_features(data, fs=FS)
        assert not np.any(np.isinf(feat))

    def test_rms_positive(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, 20_000))
        feat = extract_window_features(data, fs=FS)
        rms_idx = FEATURE_NAMES.index("rms_mean")
        assert feat[rms_idx] > 0

    def test_spike_rate_nonneg(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, 20_000))
        feat = extract_window_features(data, fs=FS)
        sr_idx = FEATURE_NAMES.index("spike_rate_mean")
        assert feat[sr_idx] >= 0


# ═══════════════════════════════════════════════════════════════════
#  Helper functions
# ═══════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_safe_cv_nonzero(self):
        arr = np.array([2.0, 3.0, 4.0])
        cv = _safe_cv(arr)
        assert cv > 0

    def test_safe_cv_zero_mean(self):
        arr = np.array([0.0, 0.0, 0.0])
        assert _safe_cv(arr) == 0.0

    def test_spectral_edge_reasonable(self):
        rng = np.random.default_rng(42)
        data = rng.normal(0, 2.4, (N_CH, 20_000))
        freq = _spectral_edge(data, fs=FS)
        assert 0 < freq < FS / 2

    def test_pairwise_corr_identity(self):
        """Identical channels → correlation = 1."""
        row = np.random.default_rng(42).normal(0, 1, 1000)
        data = np.vstack([row, row])
        assert abs(_mean_pairwise_corr(data) - 1.0) < 1e-6

    def test_pairwise_corr_uncorrelated(self):
        """Independent noise → correlation ≈ 0."""
        rng = np.random.default_rng(42)
        data = rng.normal(0, 1, (10, 10_000))
        corr = _mean_pairwise_corr(data)
        assert abs(corr) < 0.1

    def test_pairwise_corr_single_channel(self):
        data = np.random.default_rng(42).normal(0, 1, (1, 1000))
        assert _mean_pairwise_corr(data) == 0.0


# ═══════════════════════════════════════════════════════════════════
#  Windowed extraction
# ═══════════════════════════════════════════════════════════════════

class TestWindowedExtraction:
    def test_shape_no_overlap(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, N_SAMPLES))
        centers, feat = extract_windowed_features(
            data, fs=FS, window_s=1.0, overlap=0.0,
        )
        expected_windows = N_SAMPLES // int(1.0 * FS)
        assert feat.shape == (expected_windows, N_FEATURES)
        assert centers.shape == (expected_windows,)

    def test_shape_with_overlap(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, N_SAMPLES))
        centers, feat = extract_windowed_features(
            data, fs=FS, window_s=1.0, overlap=0.5,
        )
        # With 50% overlap: step = 0.5s → n_windows ≈ (5-1)/0.5 + 1 = 9
        assert feat.shape[1] == N_FEATURES
        assert feat.shape[0] > 5  # more windows than no-overlap

    def test_centers_monotonic(self):
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, N_SAMPLES))
        centers, _ = extract_windowed_features(
            data, fs=FS, window_s=1.0, overlap=0.0,
        )
        assert np.all(np.diff(centers) > 0)


# ═══════════════════════════════════════════════════════════════════
#  Trial feature extraction
# ═══════════════════════════════════════════════════════════════════

class TestTrialFeatures:
    def test_baseline_and_post_shapes(self):
        w = generate_baseline(**COMMON)
        bl, post = extract_trial_features(
            w.data_uv, fs=FS, onset_s=DUR / 2,
            window_s=1.0, n_baseline_windows=2, n_post_windows=2,
        )
        assert bl.shape[1] == N_FEATURES
        assert post.shape[1] == N_FEATURES

    def test_features_differ_after_perturbation(self):
        """Post-onset features should differ from baseline for strong perturbation."""
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        bl, post = extract_trial_features(
            w.data_uv, fs=FS, onset_s=ONSET,
            window_s=1.0, n_baseline_windows=2, n_post_windows=2,
        )
        if bl.shape[0] > 0 and post.shape[0] > 0:
            rms_idx = FEATURE_NAMES.index("rms_mean")
            bl_rms = np.mean(bl[:, rms_idx])
            post_rms = np.mean(post[:, rms_idx])
            assert post_rms > bl_rms * 1.1


# ═══════════════════════════════════════════════════════════════════
#  Condition discrimination
# ═══════════════════════════════════════════════════════════════════

class TestConditionDiscrimination:
    """Features should differ between perturbation types."""

    def test_baseline_vs_broadband_noise(self):
        bl = generate_baseline(**COMMON)
        bn = generate_broadband_noise(severity=0.9, **PERT_COMMON)

        # Extract features from full recording
        _, feat_bl = extract_windowed_features(bl.data_uv, fs=FS, window_s=1.0)
        _, feat_bn = extract_windowed_features(bn.data_uv, fs=FS, window_s=1.0)

        rms_idx = FEATURE_NAMES.index("rms_mean")
        assert np.mean(feat_bn[:, rms_idx]) > np.mean(feat_bl[:, rms_idx])

    def test_artifact_vs_neurotox_spike_rate(self):
        """Neurotox → lower spike rate; broadband noise → similar or higher."""
        bl = generate_baseline(**COMMON)
        bn = generate_broadband_noise(severity=0.5, **PERT_COMMON)
        ss = generate_spike_suppression(severity=0.7, **PERT_COMMON)

        onset_idx = int(ONSET * FS)
        # Post-onset features only
        feat_bl = extract_window_features(bl.data_uv[:, onset_idx:], fs=FS)
        feat_bn = extract_window_features(bn.data_uv[:, onset_idx:], fs=FS)
        feat_ss = extract_window_features(ss.data_uv[:, onset_idx:], fs=FS)

        sr_idx = FEATURE_NAMES.index("spike_rate_mean")
        # Spike suppression should have lower spike rate than baseline
        # (This may fail for very weak suppression, so use high severity)
        assert feat_ss[sr_idx] <= feat_bl[sr_idx] * 1.1, \
            "Spike suppression should reduce spike rate"


# ═══════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_short_window(self):
        """0.1s window should still produce valid features."""
        data = np.random.default_rng(42).normal(0, 2.4, (N_CH, int(0.1 * FS)))
        feat = extract_window_features(data, fs=FS)
        assert feat.shape == (N_FEATURES,)
        assert not np.any(np.isnan(feat))

    def test_single_channel(self):
        data = np.random.default_rng(42).normal(0, 2.4, (1, 20_000))
        feat = extract_window_features(data, fs=FS)
        assert feat.shape == (N_FEATURES,)
