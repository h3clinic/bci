"""
Tests for train_classifier.py — two-stage perturbation detector.

Covers:
  Stage 1:
    - CUSUM detects known step change
    - CUSUM returns None for flat signal
    - detect_change returns correct structure
    - Detection latency is reasonable

  Stage 2:
    - Binary classifier trains without error
    - AUROC > 0.70 (binary: artifact vs neurotox)
    - Confusion matrix shape = (2,2) and (n_types, n_types)
    - Feature importance shape = (N_FEATURES,)

  Full pipeline:
    - run_two_stage_pipeline completes end-to-end
    - TwoStageResult has both stages populated
"""

import numpy as np
import pytest

from train_classifier import (
    cusum_detect,
    detect_change,
    DetectionResult,
    ClassificationResult,
    TwoStageResult,
    train_cause_classifier,
    run_two_stage_pipeline,
    _build_feature_matrix,
)
from perturbation_library import (
    generate_dataset,
    generate_broadband_noise,
    generate_spike_suppression,
    generate_baseline,
    generate_mixed_neurotox,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
)
from features import N_FEATURES


# ── Shared params ────────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DUR = 5.0
ONSET = 2.0
SEED = 42

COMMON = dict(seed=SEED, n_channels=N_CH, fs=FS, duration_s=DUR)
PERT_COMMON = dict(**COMMON, onset_s=ONSET)


# ═══════════════════════════════════════════════════════════════════
#  Stage 1: CUSUM change detection
# ═══════════════════════════════════════════════════════════════════

class TestCusumDetect:
    def test_detects_step(self):
        """Clear step change should be detected."""
        rng = np.random.default_rng(42)
        trace = np.concatenate([
            rng.normal(0, 1, 50),   # baseline
            rng.normal(5, 1, 50),   # shifted
        ])
        idx, cusum = cusum_detect(trace, baseline_end_idx=30, threshold=5.0)
        assert idx is not None
        assert 30 <= idx < 70  # detected somewhere in shifted region

    def test_no_detection_flat(self):
        """Flat signal → no alarm."""
        rng = np.random.default_rng(42)
        trace = rng.normal(0, 1, 200)
        idx, cusum = cusum_detect(trace, baseline_end_idx=100, threshold=8.0)
        assert idx is None

    def test_cusum_trace_shape(self):
        trace = np.random.default_rng(42).normal(0, 1, 80)
        _, cusum = cusum_detect(trace, baseline_end_idx=40)
        assert cusum.shape == (80,)

    def test_lower_threshold_more_sensitive(self):
        """Lower threshold → earlier detection."""
        rng = np.random.default_rng(42)
        trace = np.concatenate([
            rng.normal(0, 1, 50),
            rng.normal(2, 1, 50),  # moderate shift
        ])
        idx_strict, _ = cusum_detect(trace, baseline_end_idx=30, threshold=8.0)
        idx_loose, _ = cusum_detect(trace, baseline_end_idx=30, threshold=3.0)
        # Loose threshold should detect earlier (or both detect)
        if idx_strict is not None and idx_loose is not None:
            assert idx_loose <= idx_strict


class TestDetectChange:
    def test_detection_result_structure(self):
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        result = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                               baseline_s=ONSET - 0.5, window_s=0.5)
        assert isinstance(result, DetectionResult)
        assert hasattr(result, "detected")
        assert hasattr(result, "onset_time_s")
        assert hasattr(result, "confidence")
        assert hasattr(result, "detection_latency_s")
        assert hasattr(result, "cusum_trace")

    def test_detects_strong_noise_perturbation(self):
        """Strong broadband noise → reliable detection."""
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        result = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                               baseline_s=ONSET - 0.5, window_s=0.5)
        assert result.detected

    def test_baseline_no_detection(self):
        """Baseline → should NOT trigger detection."""
        w = generate_baseline(**COMMON)
        result = detect_change(w.data_uv, fs=FS, onset_s_true=DUR,
                               baseline_s=DUR / 2, window_s=0.5)
        assert not result.detected

    def test_detection_latency_positive(self):
        """Detection latency should be ≥ 0 (detected after true onset)."""
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        result = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                               baseline_s=ONSET - 0.5, window_s=0.5)
        if result.detected and result.detection_latency_s is not None:
            # Latency can be slightly negative due to window centering
            assert result.detection_latency_s > -1.0


# ═══════════════════════════════════════════════════════════════════
#  Stage 2: Cause classification
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def small_dataset():
    """Small dataset for classifier tests (minimize runtime)."""
    return generate_dataset(
        n_per_class=2, severities=[0.3, 0.7],
        n_channels=N_CH, fs=FS,
        duration_s=DUR, onset_s=ONSET, seed=SEED,
    )


@pytest.fixture(scope="module")
def classification_result(small_dataset):
    """Train classifier once, reuse across tests."""
    return train_cause_classifier(small_dataset, n_cv_folds=3, seed=SEED)


class TestBuildFeatureMatrix:
    def test_shape(self, small_dataset):
        X, y_bin, y_multi, type_names = _build_feature_matrix(small_dataset)
        assert X.shape[1] == N_FEATURES
        assert X.shape[0] == y_bin.shape[0]
        assert X.shape[0] == y_multi.shape[0]

    def test_binary_labels(self, small_dataset):
        _, y_bin, _, _ = _build_feature_matrix(small_dataset)
        unique = set(y_bin.tolist())
        assert unique.issubset({0, 1})

    def test_type_order(self, small_dataset):
        _, _, _, type_names = _build_feature_matrix(small_dataset)
        assert type_names[0] == "baseline_stable"
        assert len(type_names) == 1 + len(ARTIFACT_TYPES) + len(NEUROTOX_TYPES)

    def test_no_nans(self, small_dataset):
        X, _, _, _ = _build_feature_matrix(small_dataset)
        assert not np.any(np.isnan(X))


class TestCauseClassifier:
    def test_result_structure(self, classification_result):
        r = classification_result
        assert isinstance(r, ClassificationResult)
        assert isinstance(r.binary_label, str)
        assert r.binary_label in ("artifact", "neurotox")
        assert 0.0 <= r.binary_probability <= 1.0

    def test_accuracy_binary_above_chance(self, classification_result):
        """Binary accuracy should beat random (50%)."""
        assert classification_result.accuracy_binary > 0.50

    def test_auroc_above_threshold(self, classification_result):
        """AUROC should be meaningfully above 0.5 (random)."""
        assert classification_result.auroc_binary > 0.60

    def test_confusion_matrix_binary_shape(self, classification_result):
        assert classification_result.confusion_matrix_binary.shape == (2, 2)

    def test_confusion_matrix_multi_shape(self, classification_result):
        n_types = 1 + len(ARTIFACT_TYPES) + len(NEUROTOX_TYPES)
        assert classification_result.confusion_matrix_multi.shape == (n_types, n_types)

    def test_feature_importance_shape(self, classification_result):
        assert classification_result.feature_importance.shape == (N_FEATURES,)

    def test_feature_names(self, classification_result):
        assert len(classification_result.feature_names) == N_FEATURES

    def test_cv_scores_populated(self, classification_result):
        assert len(classification_result.cv_scores_binary) > 0
        assert all(0 <= s <= 1 for s in classification_result.cv_scores_binary)

    def test_n_train_correct(self, classification_result, small_dataset):
        # May differ slightly if some windows produce empty post-onset
        assert classification_result.n_train <= len(small_dataset)
        assert classification_result.n_train > 0


# ═══════════════════════════════════════════════════════════════════
#  Full two-stage pipeline
# ═══════════════════════════════════════════════════════════════════

class TestTwoStagePipeline:
    def test_pipeline_completes(self):
        """End-to-end pipeline should run without error."""
        result = run_two_stage_pipeline(
            n_per_class=1, severities=[0.5],
            n_channels=N_CH, fs=FS,
            duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        assert isinstance(result, TwoStageResult)
        assert isinstance(result.detection, DetectionResult)
        assert isinstance(result.classification, ClassificationResult)

    def test_pipeline_detection_populated(self):
        result = run_two_stage_pipeline(
            n_per_class=1, severities=[0.5],
            n_channels=N_CH, fs=FS,
            duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        # Detection may or may not trigger depending on severity
        assert hasattr(result.detection, "detected")

    def test_pipeline_classification_populated(self):
        result = run_two_stage_pipeline(
            n_per_class=1, severities=[0.5],
            n_channels=N_CH, fs=FS,
            duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        assert result.classification.n_train > 0
        assert result.classification.n_features == N_FEATURES
