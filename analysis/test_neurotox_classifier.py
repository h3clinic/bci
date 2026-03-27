"""
test_neurotox_classifier.py — Tests for the neurotoxicity classification pipeline.

Test categories:
  1. Perturbation model sanity (8 tests)
  2. Feature extraction (5 tests)
  3. Dataset generation (3 tests)
  4. Classification pipeline (4 tests)

Total: 20 tests
"""

import numpy as np
import pytest

from signal_gen import SyntheticRecording, adc_to_uv
from neural_metrics import noise_rms_uv, bandpower, spike_rate
from neurotox_classifier import (
    extract_features,
    generate_dataset,
    train_and_evaluate,
    run_full_pipeline,
    TrialFeatures,
    FEATURE_NAMES,
    PERTURBATION_PARAMS,
)


FS = 20_000.0


# ═══════════════════════════════════════════════════════════════════
#  1. Perturbation Model Sanity
# ═══════════════════════════════════════════════════════════════════

def test_amplitude_suppression_reduces_power():
    """50% amplitude suppression → >40% power reduction post-onset."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=10.0, seed=200)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
    rec.add_amplitude_suppression(onset_s=5.0, suppression_frac=0.5)
    data = rec.build_uv()

    rms_pre = np.mean(noise_rms_uv(data[:, :int(4*FS)], fs=FS, highpass_hz=None))
    rms_post = np.mean(noise_rms_uv(data[:, int(6*FS):], fs=FS, highpass_hz=None))
    # 50% amplitude → 50% RMS reduction
    assert rms_post < rms_pre * 0.7, f"Pre={rms_pre:.2f}, Post={rms_post:.2f}"


def test_amplitude_suppression_preserves_baseline():
    """Pre-onset data is unchanged by amplitude suppression."""
    rec1 = SyntheticRecording(n_channels=2, fs=FS, duration_s=6.0, seed=201)
    rec1.add_noise(rms_uv=3.0)
    data_clean = rec1.build_uv()

    rec2 = SyntheticRecording(n_channels=2, fs=FS, duration_s=6.0, seed=201)
    rec2.add_noise(rms_uv=3.0)
    rec2.add_amplitude_suppression(onset_s=3.0, suppression_frac=0.5)
    data_perturbed = rec2.build_uv()

    # Pre-onset should be identical
    pre_onset = int(3.0 * FS)
    np.testing.assert_array_equal(data_clean[:, :pre_onset], data_perturbed[:, :pre_onset])


def test_noise_elevation_increases_rms():
    """8 µV noise elevation → measurable RMS increase post-onset."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=10.0, seed=210)
    rec.add_noise(rms_uv=3.0)
    rec.add_noise_elevation(onset_s=5.0, noise_increase_uv=8.0)
    data = rec.build_uv()

    rms_pre = np.mean(noise_rms_uv(data[:, :int(4*FS)], fs=FS, highpass_hz=None))
    rms_post = np.mean(noise_rms_uv(data[:, int(6*FS):], fs=FS, highpass_hz=None))
    assert rms_post > rms_pre * 2.0, f"Pre={rms_pre:.2f}, Post={rms_post:.2f}"


def test_noise_elevation_preserves_baseline():
    """Pre-onset data is unchanged by noise elevation."""
    rec1 = SyntheticRecording(n_channels=2, fs=FS, duration_s=6.0, seed=211)
    rec1.add_noise(rms_uv=3.0)
    data_clean = rec1.build_uv()

    rec2 = SyntheticRecording(n_channels=2, fs=FS, duration_s=6.0, seed=211)
    rec2.add_noise(rms_uv=3.0)
    rec2.add_noise_elevation(onset_s=3.0, noise_increase_uv=8.0)
    data_perturbed = rec2.build_uv()

    pre_onset = int(3.0 * FS)
    np.testing.assert_array_equal(data_clean[:, :pre_onset], data_perturbed[:, :pre_onset])


def test_burst_fragmentation_reduces_power():
    """45% dropout probability → measurable power reduction."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=10.0, seed=220)
    rec.add_noise(rms_uv=5.0)
    rec.add_burst_fragmentation(onset_s=5.0, dropout_prob=0.45)
    data = rec.build_uv()

    rms_pre = np.mean(noise_rms_uv(data[:, :int(4*FS)], fs=FS, highpass_hz=None))
    rms_post = np.mean(noise_rms_uv(data[:, int(6*FS):], fs=FS, highpass_hz=None))
    # Dropout reduces RMS (zeroed windows → lower power)
    assert rms_post < rms_pre * 0.9, f"Pre={rms_pre:.2f}, Post={rms_post:.2f}"


def test_burst_fragmentation_creates_zero_segments():
    """Burst fragmentation produces zero-valued segments post-onset."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=6.0, seed=221)
    rec.add_noise(rms_uv=5.0)
    rec.add_burst_fragmentation(onset_s=3.0, dropout_prob=0.5)
    data = rec.build_uv()

    post = data[0, int(3.0*FS):]
    # With 50% dropout, there should be many zeros
    zero_frac = np.mean(np.abs(post) < 1e-10)
    assert zero_frac > 0.1, f"Expected many zeros, got {zero_frac:.2%}"


def test_firing_rate_suppression_reduces_spike_count():
    """70% spike suppression → fewer detected spikes post-onset."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=30.0, seed=230)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=10.0, amplitude_uv=-200.0)
    rec.add_firing_rate_suppression(onset_s=15.0, suppression_frac=0.7)
    data = rec.build_uv()

    # Spike rate pre vs post (use longer windows for statistical stability)
    rate_pre = spike_rate(data[:, :int(14*FS)], fs=FS, threshold_uv=-50.0)
    rate_post = spike_rate(data[:, int(16*FS):], fs=FS, threshold_uv=-50.0)
    # Some reduction expected (may not be exactly 70% due to stochastic process)
    assert rate_post[0] < rate_pre[0], (
        f"Pre={rate_pre[0]:.1f} Hz, Post={rate_post[0]:.1f} Hz — expected reduction"
    )


def test_firing_rate_suppression_preserves_noise_floor():
    """Firing rate suppression doesn't significantly change RMS of noise-only band."""
    rec = SyntheticRecording(n_channels=1, fs=FS, duration_s=10.0, seed=231)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
    rec.add_firing_rate_suppression(onset_s=5.0, suppression_frac=0.7)
    data = rec.build_uv()

    # Use RMS on high-pass filtered data (captures background noise level)
    # Spike suppression attenuates spikes but shouldn't increase noise floor
    rms_pre = noise_rms_uv(data[:, :int(4*FS)], fs=FS, highpass_hz=300.0)
    rms_post = noise_rms_uv(data[:, int(6*FS):], fs=FS, highpass_hz=300.0)
    # Post-onset RMS should decrease or stay similar (not increase drastically)
    assert rms_post[0] <= rms_pre[0] * 1.5, (
        f"Pre RMS={rms_pre[0]:.2f}, Post RMS={rms_post[0]:.2f} — "
        f"suppression shouldn't increase noise"
    )


# ═══════════════════════════════════════════════════════════════════
#  2. Feature Extraction
# ═══════════════════════════════════════════════════════════════════

def test_feature_extraction_returns_correct_shape():
    """extract_features returns TrialFeatures with 7-element array."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=5.0, seed=300)
    rec.add_noise(rms_uv=3.0)
    data = rec.build_uv()

    feat = extract_features(data, fs=FS, label="test", perturbation_type="none")
    arr = feat.to_array()
    assert arr.shape == (7,), f"Expected (7,), got {arr.shape}"
    assert feat.label == "test"
    assert feat.perturbation_type == "none"


def test_feature_extraction_no_nans():
    """No NaN values in feature vector for valid input."""
    rec = SyntheticRecording(n_channels=8, fs=FS, duration_s=5.0, seed=301)
    rec.add_noise(rms_uv=3.0)
    rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
    data = rec.build_uv()

    feat = extract_features(data, fs=FS)
    arr = feat.to_array()
    assert not np.any(np.isnan(arr)), f"NaN found in features: {arr}"


def test_feature_rms_matches_metric():
    """RMS feature matches noise_rms_uv metric."""
    rec = SyntheticRecording(n_channels=4, fs=FS, duration_s=5.0, seed=302)
    rec.add_noise(rms_uv=5.0)
    data = rec.build_uv()

    feat = extract_features(data, fs=FS)
    rms_direct = noise_rms_uv(data, fs=FS, highpass_hz=1.0)
    assert abs(feat.rms_mean - np.mean(rms_direct)) < 0.01


def test_features_differ_between_conditions():
    """Control and severely perturbed trials produce different features."""
    # Control
    rec1 = SyntheticRecording(n_channels=4, fs=FS, duration_s=5.0, seed=303)
    rec1.add_noise(rms_uv=3.0)
    rec1.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
    data1 = rec1.build_uv()

    # Severe noise elevation
    rec2 = SyntheticRecording(n_channels=4, fs=FS, duration_s=5.0, seed=303)
    rec2.add_noise(rms_uv=3.0)
    rec2.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
    rec2.add_noise_elevation(onset_s=0.0, noise_increase_uv=10.0)
    data2 = rec2.build_uv()

    feat1 = extract_features(data1, fs=FS)
    feat2 = extract_features(data2, fs=FS)

    # RMS should be clearly different
    assert feat2.rms_mean > feat1.rms_mean * 1.5, (
        f"Control RMS={feat1.rms_mean:.2f}, Perturbed RMS={feat2.rms_mean:.2f}"
    )


def test_feature_names_match_array_length():
    """FEATURE_NAMES list has same length as feature array."""
    assert len(FEATURE_NAMES) == 7


# ═══════════════════════════════════════════════════════════════════
#  3. Dataset Generation
# ═══════════════════════════════════════════════════════════════════

def test_dataset_shape():
    """generate_dataset produces correct shapes: 3 classes × 4 perturbations + control."""
    # n_trials_per_class=5 → 5 control + 5×4×2 perturbed = 5+40 = 45
    X, y, labels, ptypes = generate_dataset(n_trials_per_class=5, seed=400)
    assert X.shape[0] == 45, f"Expected 45 trials, got {X.shape[0]}"
    assert X.shape[1] == 7, f"Expected 7 features, got {X.shape[1]}"
    assert len(y) == 45
    assert len(labels) == 45
    assert len(ptypes) == 45


def test_dataset_class_balance():
    """Dataset has expected class distribution."""
    X, y, labels, ptypes = generate_dataset(n_trials_per_class=5, seed=401)
    n_control = np.sum(y == 0)
    n_mild = np.sum(y == 1)
    n_severe = np.sum(y == 2)
    assert n_control == 5
    assert n_mild == 20  # 5 per type × 4 types
    assert n_severe == 20


def test_dataset_no_nans():
    """No NaN values in generated dataset."""
    X, y, _, _ = generate_dataset(n_trials_per_class=5, seed=402)
    assert not np.any(np.isnan(X)), "NaN found in feature matrix"
    assert not np.any(np.isnan(y)), "NaN found in labels"


# ═══════════════════════════════════════════════════════════════════
#  4. Classification Pipeline
# ═══════════════════════════════════════════════════════════════════

def test_classifier_runs_without_error():
    """Full pipeline runs and returns ClassificationResult."""
    result = run_full_pipeline(n_trials_per_class=10, seed=500)
    assert result.n_trials > 0
    assert result.n_features == 7
    assert 0 <= result.accuracy <= 1.0
    assert 0 <= result.auroc_macro <= 1.0


def test_classifier_auroc_above_chance():
    """AUROC significantly above chance (0.5) with distinguishable classes."""
    result = run_full_pipeline(n_trials_per_class=15, seed=501)
    assert result.auroc_macro > 0.7, (
        f"AUROC {result.auroc_macro:.3f} not above chance+margin"
    )


def test_confusion_matrix_shape():
    """Confusion matrix is 3×3 (control, mild, severe)."""
    result = run_full_pipeline(n_trials_per_class=10, seed=502)
    assert result.confusion_matrix.shape == (3, 3)
    # Total should equal number of trials
    assert result.confusion_matrix.sum() == result.n_trials


def test_feature_importance_sums_to_one():
    """RandomForest feature importances sum to ~1.0."""
    result = run_full_pipeline(n_trials_per_class=10, seed=503)
    assert result.feature_importance.shape == (7,)
    assert abs(np.sum(result.feature_importance) - 1.0) < 0.01


# ═══════════════════════════════════════════════════════════════════
#  5. Integration: End-to-end neurotox classification
# ═══════════════════════════════════════════════════════════════════

def test_full_pipeline_high_auroc():
    """Full pipeline with adequate data → AUROC > 0.90.

    This is the key result for the paper: the synthetic perturbation
    models produce separable classes that a simple classifier can
    distinguish, validating the platform's detection capability.
    """
    result = run_full_pipeline(n_trials_per_class=30, seed=999)
    assert result.auroc_macro > 0.90, (
        f"Full pipeline AUROC {result.auroc_macro:.3f} — expected >0.90"
    )
    print(f"\n  Pipeline AUROC: {result.auroc_macro:.3f}")
    print(f"  Accuracy:       {result.accuracy:.3f}")
    print(f"  CV scores:      {result.cv_scores.mean():.3f} ± {result.cv_scores.std():.3f}")
    print(f"  Per-class AUROC: {result.auroc_per_class}")


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
    print(f"neurotox_classifier tests: {passed} PASS, {failed} FAIL")
    print(f"{'='*60}")
    sys.exit(1 if failed else 0)
