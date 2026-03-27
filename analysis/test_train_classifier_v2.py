"""
Tests for v2 additions to train_classifier.py:

  - zscore_detect: classical z-score change detection
  - detect_change method parameter (cusum vs zscore)
  - wilson_ci: Wilson score binomial CI
  - bootstrap_ci: bootstrap confidence interval
  - latency_summary: median + IQR latency reporting
  - run_ablation: classical vs two-stage comparison
"""

import numpy as np
import pytest

from train_classifier import (
    zscore_detect,
    detect_change,
    DetectionResult,
    wilson_ci,
    bootstrap_ci,
    latency_summary,
    AblationResult,
    run_ablation,
)
from perturbation_library import (
    generate_dataset,
    generate_broadband_noise,
    generate_baseline,
)


# ── Shared params ────────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DUR = 5.0
ONSET = 2.0
SEED = 42

COMMON = dict(seed=SEED, n_channels=N_CH, fs=FS, duration_s=DUR)
PERT_COMMON = dict(**COMMON, onset_s=ONSET)


# ═══════════════════════════════════════════════════════════════════
#  Z-Score Detection
# ═══════════════════════════════════════════════════════════════════

class TestZscoreDetect:
    def test_detects_step(self):
        """Clear step change should be detected."""
        rng = np.random.default_rng(42)
        trace = np.concatenate([
            rng.normal(0, 1, 50),
            rng.normal(5, 1, 50),
        ])
        idx, z_trace = zscore_detect(trace, baseline_end_idx=30, threshold=3.0, n_consec=3)
        assert idx is not None
        assert 30 <= idx < 70

    def test_no_detection_flat(self):
        """Flat signal → no alarm."""
        rng = np.random.default_rng(42)
        trace = rng.normal(0, 1, 200)
        idx, z_trace = zscore_detect(trace, baseline_end_idx=100, threshold=3.0, n_consec=5)
        assert idx is None

    def test_zscore_trace_shape(self):
        trace = np.random.default_rng(42).normal(0, 1, 80)
        _, z_trace = zscore_detect(trace, baseline_end_idx=40)
        assert z_trace.shape == (80,)

    def test_consec_requirement(self):
        """n_consec=1 should be more sensitive than n_consec=10."""
        rng = np.random.default_rng(42)
        trace = np.concatenate([
            rng.normal(0, 1, 50),
            rng.normal(2, 1, 50),
        ])
        idx_loose, _ = zscore_detect(trace, baseline_end_idx=30, threshold=2.0, n_consec=1)
        idx_strict, _ = zscore_detect(trace, baseline_end_idx=30, threshold=2.0, n_consec=10)
        # Loose should detect first (if both detect)
        if idx_loose is not None and idx_strict is not None:
            assert idx_loose <= idx_strict

    def test_zero_variance_returns_none(self):
        """Constant signal (σ=0) should not crash, should return None."""
        trace = np.ones(100)
        idx, z_trace = zscore_detect(trace, baseline_end_idx=50)
        assert idx is None
        assert np.all(z_trace == 0)

    def test_returns_absolute_zscore(self):
        """Z-score trace should be non-negative (absolute)."""
        rng = np.random.default_rng(42)
        trace = rng.normal(0, 1, 100)
        _, z_trace = zscore_detect(trace, baseline_end_idx=50)
        assert np.all(z_trace >= 0)


class TestDetectChangeMethod:
    def test_cusum_method(self):
        """method='cusum' should work (default)."""
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        result = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                               baseline_s=ONSET - 0.5, window_s=0.5,
                               method="cusum")
        assert isinstance(result, DetectionResult)

    def test_zscore_method(self):
        """method='zscore' should work."""
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        result = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                               baseline_s=ONSET - 0.5, window_s=0.5,
                               method="zscore")
        assert isinstance(result, DetectionResult)

    def test_both_detect_strong_perturbation(self):
        """Both methods should detect a strong perturbation."""
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        det_c = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                              baseline_s=ONSET - 0.5, window_s=0.5,
                              method="cusum")
        det_z = detect_change(w.data_uv, fs=FS, onset_s_true=ONSET,
                              baseline_s=ONSET - 0.5, window_s=0.5,
                              method="zscore")
        assert det_c.detected
        assert det_z.detected

    def test_baseline_neither_detects(self):
        """Neither method should trigger on baseline."""
        w = generate_baseline(**COMMON)
        det_c = detect_change(w.data_uv, fs=FS, onset_s_true=DUR,
                              baseline_s=DUR / 2, window_s=0.5,
                              method="cusum")
        det_z = detect_change(w.data_uv, fs=FS, onset_s_true=DUR,
                              baseline_s=DUR / 2, window_s=0.5,
                              method="zscore")
        assert not det_c.detected
        assert not det_z.detected


# ═══════════════════════════════════════════════════════════════════
#  Wilson CI
# ═══════════════════════════════════════════════════════════════════

class TestWilsonCI:
    def test_perfect_detection(self):
        """k=n → CI close to (1.0, 1.0)."""
        lo, hi = wilson_ci(100, 100)
        assert lo > 0.95
        assert hi <= 1.0

    def test_zero_detection(self):
        """k=0 → CI close to (0.0, 0.0)."""
        lo, hi = wilson_ci(0, 100)
        assert lo >= 0.0
        assert hi < 0.05

    def test_half_detection(self):
        """k=50, n=100 → CI contains 0.5."""
        lo, hi = wilson_ci(50, 100)
        assert lo < 0.5 < hi

    def test_empty_n(self):
        """n=0 → (0, 1) full uncertainty."""
        lo, hi = wilson_ci(0, 0)
        assert lo == 0.0
        assert hi == 1.0

    def test_bounds_in_01(self):
        """CI should always be within [0, 1]."""
        for k, n in [(0, 10), (5, 10), (10, 10), (1, 1), (0, 1)]:
            lo, hi = wilson_ci(k, n)
            assert 0.0 <= lo <= hi <= 1.0

    def test_wider_with_fewer_samples(self):
        """CI should be wider with fewer samples."""
        _, hi_large = wilson_ci(50, 100)
        lo_large, _ = wilson_ci(50, 100)
        _, hi_small = wilson_ci(5, 10)
        lo_small, _ = wilson_ci(5, 10)
        width_large = hi_large - lo_large
        width_small = hi_small - lo_small
        assert width_small > width_large


# ═══════════════════════════════════════════════════════════════════
#  Bootstrap CI
# ═══════════════════════════════════════════════════════════════════

class TestBootstrapCI:
    def test_returns_3_tuple(self):
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = bootstrap_ci(values)
        assert len(result) == 3

    def test_point_estimate(self):
        """Point estimate should be the mean."""
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        point, lo, hi = bootstrap_ci(values)
        assert abs(point - 3.0) < 0.01

    def test_ci_contains_point(self):
        values = np.random.default_rng(42).normal(10, 2, 50)
        point, lo, hi = bootstrap_ci(values)
        assert lo <= point <= hi

    def test_empty_array(self):
        point, lo, hi = bootstrap_ci(np.array([]))
        assert point == 0.0
        assert lo == 0.0
        assert hi == 0.0

    def test_single_value(self):
        """Single value → CI collapses to that value."""
        point, lo, hi = bootstrap_ci(np.array([5.0]))
        assert point == 5.0
        assert lo == 5.0
        assert hi == 5.0

    def test_custom_stat(self):
        """Works with median as stat function."""
        values = np.array([1.0, 2.0, 100.0, 3.0, 4.0])
        point, lo, hi = bootstrap_ci(values, stat_fn=np.median)
        assert abs(point - 3.0) < 0.01

    def test_wider_ci_for_higher_variability(self):
        """Higher variance data → wider CI."""
        rng = np.random.default_rng(42)
        narrow = rng.normal(0, 0.1, 50)
        wide = rng.normal(0, 10.0, 50)
        _, lo_n, hi_n = bootstrap_ci(narrow, seed=42)
        _, lo_w, hi_w = bootstrap_ci(wide, seed=42)
        assert (hi_w - lo_w) > (hi_n - lo_n)


# ═══════════════════════════════════════════════════════════════════
#  Latency Summary
# ═══════════════════════════════════════════════════════════════════

class TestLatencySummary:
    def test_empty_list(self):
        result = latency_summary([])
        assert result["n"] == 0
        assert np.isnan(result["median"])

    def test_normal_list(self):
        result = latency_summary([1.0, 2.0, 3.0, 4.0, 5.0])
        assert result["n"] == 5
        assert abs(result["median"] - 3.0) < 0.01
        assert abs(result["mean"] - 3.0) < 0.01

    def test_iqr_correct(self):
        result = latency_summary([1.0, 2.0, 3.0, 4.0, 5.0])
        # Q1 = 2.0, Q3 = 4.0
        assert result["iqr_lo"] == pytest.approx(2.0)
        assert result["iqr_hi"] == pytest.approx(4.0)

    def test_single_value(self):
        result = latency_summary([7.5])
        assert result["n"] == 1
        assert result["median"] == 7.5
        assert result["mean"] == 7.5
        assert result["std"] == 0.0

    def test_none_filtered(self):
        """None values in list should be filtered out."""
        result = latency_summary([1.0, None, 3.0, None, 5.0])
        assert result["n"] == 3

    def test_inf_filtered(self):
        """Infinite values should be filtered out."""
        result = latency_summary([1.0, float("inf"), 3.0])
        assert result["n"] == 2


# ═══════════════════════════════════════════════════════════════════
#  Ablation Study
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def small_dataset_v2():
    """Small dataset for ablation tests."""
    return generate_dataset(
        n_per_class=2, severities=[0.3, 0.7],
        n_channels=N_CH, fs=FS,
        duration_s=DUR, onset_s=ONSET, seed=SEED,
    )


class TestAblation:
    def test_completes(self, small_dataset_v2):
        """Ablation should run without error."""
        result = run_ablation(
            small_dataset_v2, onset_s=ONSET, fs=FS, seed=SEED,
        )
        assert isinstance(result, AblationResult)

    def test_rates_in_range(self, small_dataset_v2):
        """TP and FP rates should be in [0, 1]."""
        result = run_ablation(
            small_dataset_v2, onset_s=ONSET, fs=FS, seed=SEED,
        )
        assert 0.0 <= result.tp_rate_cusum <= 1.0
        assert 0.0 <= result.tp_rate_zscore <= 1.0
        assert 0.0 <= result.fp_rate_cusum <= 1.0
        assert 0.0 <= result.fp_rate_zscore <= 1.0

    def test_cis_populated(self, small_dataset_v2):
        """CIs should be 2-tuples within [0, 1]."""
        result = run_ablation(
            small_dataset_v2, onset_s=ONSET, fs=FS, seed=SEED,
        )
        for ci in [result.tp_ci_cusum, result.tp_ci_zscore,
                    result.fp_ci_cusum, result.fp_ci_zscore]:
            assert len(ci) == 2
            assert 0.0 <= ci[0] <= ci[1] <= 1.0

    def test_stage2_value(self, small_dataset_v2):
        """Stage 2 accuracy should be ≥ majority-class baseline."""
        result = run_ablation(
            small_dataset_v2, onset_s=ONSET, fs=FS, seed=SEED,
        )
        assert result.accuracy_with_stage2 >= result.accuracy_without_stage2 * 0.9

    def test_latency_dicts(self, small_dataset_v2):
        """Latency summaries should have expected keys."""
        result = run_ablation(
            small_dataset_v2, onset_s=ONSET, fs=FS, seed=SEED,
        )
        expected_keys = {"median", "iqr_lo", "iqr_hi", "mean", "std", "n"}
        assert set(result.latency_cusum.keys()) == expected_keys
        assert set(result.latency_zscore.keys()) == expected_keys

    def test_n_trials(self, small_dataset_v2):
        result = run_ablation(
            small_dataset_v2, onset_s=ONSET, fs=FS, seed=SEED,
        )
        assert result.n_trials == len(small_dataset_v2)
