"""
test_domain_shift.py — Tier 1 tests for Domain Shift Suite v1.

Tests structure:
  TestShiftApplicators     — each of 10 shifts produces valid output
  TestMetricFunctions      — ECE, FPR@TPR, helper functions
  TestComputeSmoke         — smoke test on 2 shifts × 1 severity (fast)
  TestPlotFromMock         — plot_domain_shift renders from mock data
  TestWriters              — JSON/CSV/model_card write correctly
  TestResultDataclass      — DomainShiftResults aggregation
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from domain_shift import (
    # Shift applicators
    _apply_shift,
    SHIFT_NAMES,
    SHIFT_DISPLAY,
    SEVERITIES,
    DOMAIN_A,
    DOMAIN_B,
    # Metrics
    expected_calibration_error,
    fpr_at_tpr,
    # Recording-level split (v2)
    RecordingProfile,
    generate_recording_dataset,
    split_by_recording,
    check_leakage,
    # Compute budget (v2)
    ComputeBudget,
    compute_budget,
    # Model selection (v2)
    ALL_MODELS,
    # Worst-case summary (v2)
    WorstCaseRow,
    compute_worst_case_summary,
    write_poster_table,
    plot_poster_figure,
    write_poster_pack,
    # Compute
    compute_domain_shift_suite,
    # Plot
    plot_domain_shift,
    # Writers
    write_domain_shift_json,
    write_domain_shift_csv,
    write_domain_shift_model_card,
    # Dataclasses
    ShiftMetrics,
    DomainShiftResults,
    # Entrypoint
    run_domain_shift_suite,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def sample_data():
    """(16, 100000) array ~ 5 seconds @ 20 kHz."""
    rng = np.random.default_rng(42)
    return rng.normal(0, 3.0, (16, 100_000))


@pytest.fixture
def mock_results() -> DomainShiftResults:
    """Minimal DomainShiftResults with realistic structure."""
    metrics = []
    rng = np.random.default_rng(99)
    for model in ["CUSUM", "RF", "Deep"]:
        for shift in ["sixty_hz", "dc_drift"]:
            for sev in [0.3, 0.6, 0.9]:
                for domain in ["A", "B"]:
                    auroc = rng.uniform(0.5, 1.0)
                    metrics.append(ShiftMetrics(
                        model=model, shift=shift, severity=sev, domain=domain,
                        auroc=auroc,
                        binary_accuracy=rng.uniform(0.4, 1.0),
                        fpr_at_tpr90=rng.uniform(0, 0.5),
                        ece=rng.uniform(0, 0.3),
                        latency_median_s=rng.uniform(0.5, 3.0),
                        latency_iqr_lo=rng.uniform(0.2, 1.0),
                        latency_iqr_hi=rng.uniform(2.0, 5.0),
                        detection_rate=rng.uniform(0.5, 1.0),
                        holdout_accuracy=rng.uniform(0.3, 0.8),
                        n_train=50, n_test=20,
                    ))
    return DomainShiftResults(
        config_hash="abc123def456",
        timestamp="2026-03-03T00:00:00Z",
        training_mode="demo",
        data_source="synthetic",
        metrics=metrics,
        domain_a_mean_auroc={"CUSUM": 0.85, "RF": 0.90, "Deep": 0.88},
        domain_b_mean_auroc={"CUSUM": 0.70, "RF": 0.75, "Deep": 0.72},
        domain_drop={"CUSUM": 0.15, "RF": 0.15, "Deep": 0.16},
    )


# ═══════════════════════════════════════════════════════════════════
#  TestShiftApplicators
# ═══════════════════════════════════════════════════════════════════

class TestShiftApplicators:
    """Each of the 10 shifts produces valid output without crashing."""

    @pytest.mark.parametrize("shift_name", SHIFT_NAMES)
    def test_shift_returns_valid_array(self, shift_name, sample_data, rng):
        result = _apply_shift(sample_data, shift_name, 0.5, rng, fs=20_000.0)
        assert result.shape == sample_data.shape
        assert result.dtype == np.float64
        assert np.all(np.isfinite(result))

    @pytest.mark.parametrize("shift_name", SHIFT_NAMES)
    def test_shift_does_not_modify_input(self, shift_name, rng):
        data = np.random.default_rng(10).normal(0, 3.0, (4, 20_000))
        original = data.copy()
        _apply_shift(data, shift_name, 0.5, rng, fs=20_000.0)
        np.testing.assert_array_equal(data, original)

    @pytest.mark.parametrize("severity", [0.0, 0.3, 0.6, 0.9, 1.0])
    def test_severity_range(self, sample_data, rng, severity):
        result = _apply_shift(sample_data, "sixty_hz", severity, rng)
        assert result.shape == sample_data.shape

    def test_high_severity_changes_data_more(self, sample_data, rng):
        low = _apply_shift(sample_data, "sixty_hz", 0.1, rng)
        rng2 = np.random.default_rng(42)  # reset
        high = _apply_shift(sample_data, "sixty_hz", 0.9, rng2)
        diff_low = np.mean(np.abs(low - sample_data))
        diff_high = np.mean(np.abs(high - sample_data))
        assert diff_high > diff_low

    def test_all_shifts_have_display_names(self):
        for shift in SHIFT_NAMES:
            assert shift in SHIFT_DISPLAY


# ═══════════════════════════════════════════════════════════════════
#  TestMetricFunctions
# ═══════════════════════════════════════════════════════════════════

class TestMetricFunctions:
    """ECE, FPR@TPR helper functions."""

    def test_ece_perfect_calibration(self):
        # Perfect calibration: predicted prob matches true rate
        y_true = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 0], dtype=np.int64)
        y_prob = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0])
        ece = expected_calibration_error(y_true, y_prob)
        assert 0.0 <= ece <= 1.0

    def test_ece_empty(self):
        ece = expected_calibration_error(np.array([], dtype=np.int64),
                                         np.array([], dtype=np.float64))
        assert ece == 0.0

    def test_ece_overconfident(self):
        # All predict 0.99 but only half are positive
        y_true = np.array([1, 1, 0, 0, 1, 0], dtype=np.int64)
        y_prob = np.array([0.99, 0.99, 0.99, 0.99, 0.99, 0.99])
        ece = expected_calibration_error(y_true, y_prob)
        assert ece > 0.3  # should be badly calibrated

    def test_fpr_at_tpr_perfect(self):
        y_true = np.array([1, 1, 1, 0, 0, 0], dtype=np.int64)
        y_scores = np.array([0.9, 0.8, 0.7, 0.1, 0.2, 0.15])
        fpr = fpr_at_tpr(y_true, y_scores, target_tpr=0.9)
        # Should achieve 0.9 TPR at low FPR
        assert 0.0 <= fpr <= 1.0

    def test_fpr_at_tpr_empty(self):
        fpr = fpr_at_tpr(np.array([], dtype=np.int64),
                         np.array([], dtype=np.float64))
        assert fpr == 0.0

    def test_fpr_at_tpr_random(self):
        rng = np.random.default_rng(42)
        y_true = rng.integers(0, 2, size=100).astype(np.int64)
        y_scores = rng.random(100)
        fpr = fpr_at_tpr(y_true, y_scores, target_tpr=0.9)
        assert 0.0 <= fpr <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  TestComputeSmoke — fast subset
# ═══════════════════════════════════════════════════════════════════

class TestComputeSmoke:
    """Smoke test compute on 2 shifts (not all 10) to stay fast."""

    def test_compute_returns_results(self):
        results = compute_domain_shift_suite(
            n_per_class=2,
            n_channels=4,
            fs=4000.0,
            duration_s=2.0,
            onset_s=0.8,
            seed=42,
            max_samples=1000,
            deep_epochs=2,
            shifts=["sixty_hz", "dc_drift"],
            verbose=False,
        )
        assert isinstance(results, DomainShiftResults)
        assert results.config_hash != ""
        assert len(results.metrics) > 0

    def test_compute_has_both_domains(self):
        results = compute_domain_shift_suite(
            n_per_class=2, n_channels=4, fs=4000.0,
            duration_s=2.0, onset_s=0.8, seed=42,
            max_samples=1000, deep_epochs=2,
            shifts=["sixty_hz"],
            verbose=False,
        )
        domains_a = [m for m in results.metrics if m.domain == "A"]
        domains_b = [m for m in results.metrics if m.domain == "B"]
        assert len(domains_a) > 0
        assert len(domains_b) > 0

    def test_compute_has_all_models(self):
        results = compute_domain_shift_suite(
            n_per_class=2, n_channels=4, fs=4000.0,
            duration_s=2.0, onset_s=0.8, seed=42,
            max_samples=1000, deep_epochs=2,
            shifts=["sixty_hz"],
            verbose=False,
        )
        models = set(m.model for m in results.metrics)
        assert "CUSUM" in models
        assert "RF" in models
        assert "Deep" in models

    def test_compute_populates_aggregates(self):
        results = compute_domain_shift_suite(
            n_per_class=2, n_channels=4, fs=4000.0,
            duration_s=2.0, onset_s=0.8, seed=42,
            max_samples=1000, deep_epochs=2,
            shifts=["sixty_hz"],
            verbose=False,
        )
        assert "CUSUM" in results.domain_a_mean_auroc
        assert "RF" in results.domain_b_mean_auroc
        assert "Deep" in results.domain_drop

    def test_compute_with_models_filter(self):
        """models=['CUSUM', 'RF'] skips Deep entirely (fast)."""
        results = compute_domain_shift_suite(
            n_per_class=2, n_channels=4, fs=4000.0,
            duration_s=2.0, onset_s=0.8, seed=42,
            max_samples=1000, deep_epochs=2,
            shifts=["sixty_hz"],
            models=["CUSUM", "RF"],
            verbose=False,
        )
        model_set = set(m.model for m in results.metrics)
        assert "CUSUM" in model_set
        assert "RF" in model_set
        assert "Deep" not in model_set

    def test_all_models_constant(self):
        assert ALL_MODELS == ["CUSUM", "RF", "Deep"]


# ═══════════════════════════════════════════════════════════════════
#  TestPlotFromMock — pure rendering, no training
# ═══════════════════════════════════════════════════════════════════

class TestPlotFromMock:
    """plot_domain_shift renders from mock data — zero training."""

    def test_renders_figure(self, mock_results):
        fig = plot_domain_shift(mock_results)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_saves_to_file(self, mock_results, tmp_path):
        out = tmp_path / "fig.png"
        fig = plot_domain_shift(mock_results, output_path=out)
        assert out.exists()
        assert out.stat().st_size > 1000
        plt.close(fig)

    def test_empty_results_no_crash(self):
        empty = DomainShiftResults()
        fig = plot_domain_shift(empty)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════
#  TestWriters — JSON/CSV/model_card
# ═══════════════════════════════════════════════════════════════════

class TestWriters:
    """Output files are written correctly."""

    def test_json_valid(self, mock_results, tmp_path):
        out = tmp_path / "results.json"
        write_domain_shift_json(mock_results, out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["config_hash"] == "abc123def456"
        assert data["training_mode"] == "demo"
        assert len(data["metrics"]) == len(mock_results.metrics)

    def test_json_metrics_have_required_fields(self, mock_results, tmp_path):
        out = tmp_path / "results.json"
        write_domain_shift_json(mock_results, out)
        data = json.loads(out.read_text())
        required = {"model", "shift", "severity", "domain", "auroc",
                    "binary_accuracy", "fpr_at_tpr90", "ece",
                    "latency_median_s", "status"}
        for entry in data["metrics"]:
            assert required.issubset(entry.keys())

    def test_csv_rows(self, mock_results, tmp_path):
        out = tmp_path / "table.csv"
        write_domain_shift_csv(mock_results, out)
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == len(mock_results.metrics) + 1  # header + rows
        header = lines[0].split(",")
        assert "model" in header
        assert "auroc" in header
        assert "ece" in header

    def test_model_card_content(self, mock_results, tmp_path):
        out = tmp_path / "card.md"
        write_domain_shift_model_card(mock_results, out)
        assert out.exists()
        text = out.read_text()
        assert "Domain Shift Suite v2" in text
        assert "abc123def456" in text
        assert "CUSUM" in text
        assert "Known Limitations" in text
        assert "Proxy Language" in text

    def test_model_card_has_all_shifts(self, mock_results, tmp_path):
        out = tmp_path / "card.md"
        write_domain_shift_model_card(mock_results, out)
        text = out.read_text()
        for shift in SHIFT_NAMES:
            assert shift in text


# ═══════════════════════════════════════════════════════════════════
#  TestResultDataclass
# ═══════════════════════════════════════════════════════════════════

class TestResultDataclass:
    """DomainShiftResults aggregation and structure."""

    def test_shift_metrics_defaults(self):
        m = ShiftMetrics(model="CUSUM", shift="sixty_hz",
                         severity=0.5, domain="A")
        assert m.status == "ok"
        assert m.auroc == 0.0
        assert m.ece == 0.0

    def test_results_defaults(self):
        r = DomainShiftResults()
        assert r.config_hash == ""
        assert r.metrics == []
        assert r.domain_drop == {}

    def test_n_shifts_constant(self):
        assert len(SHIFT_NAMES) == 13

    def test_severities_constant(self):
        assert SEVERITIES == [0.3, 0.6, 0.9]

    def test_domain_configs(self):
        assert DOMAIN_A["noise_rms_uv"] < DOMAIN_B["noise_rms_uv"]
        assert DOMAIN_A["tag"] == "A"
        assert DOMAIN_B["tag"] == "B"


# ═══════════════════════════════════════════════════════════════════
#  v2: TestRecordingLevelSplit
# ═══════════════════════════════════════════════════════════════════

class TestRecordingLevelSplit:
    """Recording-level split enforcement prevents window leakage."""

    def test_generate_recording_dataset_returns_correct_types(self):
        windows, rec_ids = generate_recording_dataset(
            n_recordings=3, windows_per_recording=2,
            domain=DOMAIN_A, n_channels=4, fs=4000.0,
            duration_s=5.0, onset_s=2.0, base_seed=42,
        )
        assert isinstance(windows, list)
        assert isinstance(rec_ids, list)
        assert len(windows) == len(rec_ids)
        assert len(windows) > 0

    def test_recording_ids_are_consistent(self):
        windows, rec_ids = generate_recording_dataset(
            n_recordings=4, windows_per_recording=2,
            domain=DOMAIN_A, n_channels=4, fs=4000.0,
            duration_s=5.0, onset_s=2.0, base_seed=42,
        )
        unique_ids = set(rec_ids)
        assert len(unique_ids) == 4

    def test_split_produces_disjoint_sets(self):
        windows, rec_ids = generate_recording_dataset(
            n_recordings=6, windows_per_recording=2,
            domain=DOMAIN_A, n_channels=4, fs=4000.0,
            duration_s=5.0, onset_s=2.0, base_seed=42,
        )
        train_w, test_w, train_ids, test_ids = split_by_recording(
            windows, rec_ids, train_frac=0.7, seed=42,
        )
        assert len(train_ids & test_ids) == 0
        assert len(train_w) > 0
        assert len(test_w) > 0

    def test_no_leakage(self):
        windows, rec_ids = generate_recording_dataset(
            n_recordings=6, windows_per_recording=2,
            domain=DOMAIN_A, n_channels=4, fs=4000.0,
            duration_s=5.0, onset_s=2.0, base_seed=42,
        )
        _, _, train_ids, test_ids = split_by_recording(
            windows, rec_ids, train_frac=0.7, seed=42,
        )
        result = check_leakage(train_ids, test_ids)
        assert result["leaked"] is False
        assert result["overlap"] == set()
        assert result["n_train"] + result["n_test"] == 6

    def test_recording_profile_random(self):
        rng = np.random.default_rng(42)
        p = RecordingProfile.random(0, rng, DOMAIN_A)
        assert p.recording_id == 0
        assert p.noise_rms_uv > 0
        assert p.coupling_ratio > 0
        assert p.amplitude_scale > 0

    def test_different_recordings_have_different_profiles(self):
        rng = np.random.default_rng(42)
        p1 = RecordingProfile.random(0, rng, DOMAIN_A)
        p2 = RecordingProfile.random(1, rng, DOMAIN_A)
        # At least one field should differ
        assert (
            p1.noise_rms_uv != p2.noise_rms_uv
            or p1.coupling_ratio != p2.coupling_ratio
            or p1.spike_rate_offset != p2.spike_rate_offset
            or p1.amplitude_scale != p2.amplitude_scale
        )

    def test_leakage_detected_when_ids_overlap(self):
        result = check_leakage({0, 1, 2}, {2, 3, 4})
        assert result["leaked"] is True
        assert 2 in result["overlap"]

    def test_split_with_minimum_recordings(self):
        windows, rec_ids = generate_recording_dataset(
            n_recordings=2, windows_per_recording=2,
            domain=DOMAIN_A, n_channels=4, fs=4000.0,
            duration_s=5.0, onset_s=2.0, base_seed=42,
        )
        train_w, test_w, train_ids, test_ids = split_by_recording(
            windows, rec_ids, train_frac=0.5, seed=42,
        )
        assert len(train_ids) >= 1
        assert len(test_ids) >= 1
        assert len(train_ids & test_ids) == 0


# ═══════════════════════════════════════════════════════════════════
#  v2: TestHardShifts
# ═══════════════════════════════════════════════════════════════════

class TestHardShifts:
    """3 hard / instrumentation-realistic shifts produce valid output."""

    @pytest.mark.parametrize("shift", [
        "bandpass_mismatch", "sample_rate_mismatch", "quantization_change",
    ])
    def test_hard_shift_valid_output(self, shift, sample_data, rng):
        result = _apply_shift(sample_data, shift, 0.5, rng, fs=20_000.0)
        assert result.shape == sample_data.shape
        assert result.dtype == np.float64
        assert np.all(np.isfinite(result))

    @pytest.mark.parametrize("shift", [
        "bandpass_mismatch", "sample_rate_mismatch", "quantization_change",
    ])
    def test_hard_shift_does_not_modify_input(self, shift, rng):
        data = np.random.default_rng(10).normal(0, 3.0, (4, 20_000))
        original = data.copy()
        _apply_shift(data, shift, 0.5, rng, fs=20_000.0)
        np.testing.assert_array_equal(data, original)

    @pytest.mark.parametrize("severity", [0.3, 0.6, 0.9])
    def test_bandpass_severity_range(self, sample_data, rng, severity):
        result = _apply_shift(sample_data, "bandpass_mismatch",
                              severity, rng, fs=20_000.0)
        assert result.shape == sample_data.shape

    @pytest.mark.parametrize("severity", [0.3, 0.6, 0.9])
    def test_sample_rate_severity_range(self, sample_data, rng, severity):
        result = _apply_shift(sample_data, "sample_rate_mismatch",
                              severity, rng, fs=20_000.0)
        assert result.shape == sample_data.shape

    @pytest.mark.parametrize("severity", [0.3, 0.6, 0.9])
    def test_quantization_severity_range(self, sample_data, rng, severity):
        result = _apply_shift(sample_data, "quantization_change",
                              severity, rng, fs=20_000.0)
        assert result.shape == sample_data.shape

    def test_quantization_reduces_precision(self, sample_data, rng):
        """High severity should produce fewer unique values."""
        low_sev = _apply_shift(sample_data, "quantization_change",
                               0.1, rng, fs=20_000.0)
        rng2 = np.random.default_rng(42)
        high_sev = _apply_shift(sample_data, "quantization_change",
                                0.9, rng2, fs=20_000.0)
        # More quantization → fewer unique values
        assert len(np.unique(high_sev)) < len(np.unique(low_sev))

    def test_hard_shifts_in_shift_names(self):
        assert "bandpass_mismatch" in SHIFT_NAMES
        assert "sample_rate_mismatch" in SHIFT_NAMES
        assert "quantization_change" in SHIFT_NAMES

    def test_hard_shifts_in_display(self):
        assert "bandpass_mismatch" in SHIFT_DISPLAY
        assert "sample_rate_mismatch" in SHIFT_DISPLAY
        assert "quantization_change" in SHIFT_DISPLAY


# ═══════════════════════════════════════════════════════════════════
#  v2: TestComputeBudget
# ═══════════════════════════════════════════════════════════════════

class TestComputeBudget:
    """Compute budget returns valid timing/memory for all models."""

    def test_returns_list_of_budgets(self):
        budgets = compute_budget(
            n_channels=4, fs=4000.0, window_s=0.5,
            n_warmup=1, n_trials=2, seed=42,
        )
        assert isinstance(budgets, list)
        assert len(budgets) == 3  # CUSUM, RF, Deep
        for b in budgets:
            assert isinstance(b, ComputeBudget)

    def test_inference_ms_positive(self):
        budgets = compute_budget(
            n_channels=4, fs=4000.0, window_s=0.5,
            n_warmup=1, n_trials=2, seed=42,
        )
        for b in budgets:
            assert b.inference_ms >= 0

    def test_model_names(self):
        budgets = compute_budget(
            n_channels=4, fs=4000.0, window_s=0.5,
            n_warmup=1, n_trials=2, seed=42,
        )
        names = {b.model for b in budgets}
        assert names == {"CUSUM", "RF", "Deep"}

    def test_deep_has_params(self):
        budgets = compute_budget(
            n_channels=4, fs=4000.0, window_s=0.5,
            n_warmup=1, n_trials=2, seed=42,
        )
        deep = [b for b in budgets if b.model == "Deep"][0]
        assert deep.model_size_params > 0

    def test_cusum_has_zero_params(self):
        budgets = compute_budget(
            n_channels=4, fs=4000.0, window_s=0.5,
            n_warmup=1, n_trials=2, seed=42,
        )
        cusum = [b for b in budgets if b.model == "CUSUM"][0]
        assert cusum.model_size_params == 0

    def test_real_time_capability_positive(self):
        budgets = compute_budget(
            n_channels=4, fs=4000.0, window_s=0.5,
            n_warmup=1, n_trials=2, seed=42,
        )
        for b in budgets:
            assert b.real_time_capable_at_khz >= 0


# ═══════════════════════════════════════════════════════════════════
#  v2: TestWorstCaseSummary
# ═══════════════════════════════════════════════════════════════════

class TestWorstCaseSummary:
    """Worst-case summary table for poster."""

    def test_returns_rows(self, mock_results):
        rows = compute_worst_case_summary(mock_results)
        assert len(rows) == 3  # CUSUM, RF, Deep
        for r in rows:
            assert isinstance(r, WorstCaseRow)

    def test_worst_auroc_is_min(self, mock_results):
        rows = compute_worst_case_summary(mock_results)
        for row in rows:
            b_aurocs = [m.auroc for m in mock_results.metrics
                        if m.model == row.model and m.domain == "B"]
            if b_aurocs:
                assert row.worst_auroc == min(b_aurocs)

    def test_worst_fpr_is_max(self, mock_results):
        rows = compute_worst_case_summary(mock_results)
        for row in rows:
            b_fprs = [m.fpr_at_tpr90 for m in mock_results.metrics
                      if m.model == row.model and m.domain == "B"]
            if b_fprs:
                assert row.worst_fpr_at_tpr90 == max(b_fprs)

    def test_empty_results(self):
        empty = DomainShiftResults()
        rows = compute_worst_case_summary(empty)
        assert len(rows) == 3
        for r in rows:
            assert r.worst_auroc == 0.0


# ═══════════════════════════════════════════════════════════════════
#  v2: TestPosterPack
# ═══════════════════════════════════════════════════════════════════

class TestPosterPack:
    """Poster pack produces all expected files."""

    @pytest.fixture
    def mock_budgets(self):
        return [
            ComputeBudget("CUSUM", 1.2, 0.5, 0, 0.0, 20000.0),
            ComputeBudget("RF", 3.5, 2.1, 5000, 45.0, 8000.0),
            ComputeBudget("Deep", 15.0, 50.0, 76045, 310.0, 1500.0),
        ]

    def test_write_poster_table(self, mock_results, mock_budgets, tmp_path):
        rows = compute_worst_case_summary(mock_results)
        out = tmp_path / "poster_table.csv"
        write_poster_table(rows, mock_budgets, out)
        assert out.exists()
        text = out.read_text()
        lines = text.strip().split("\n")
        assert len(lines) == 4  # header + 3 models
        assert "worst_auroc" in lines[0]
        assert "inference_ms" in lines[0]

    def test_plot_poster_figure(self, mock_results, mock_budgets, tmp_path):
        rows = compute_worst_case_summary(mock_results)
        out = tmp_path / "poster_fig.png"
        fig = plot_poster_figure(mock_results, rows, mock_budgets,
                                 output_path=out)
        assert out.exists()
        assert out.stat().st_size > 1000
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_write_poster_pack(self, mock_results, mock_budgets, tmp_path):
        pack = write_poster_pack(mock_results, mock_budgets,
                                 tmp_path / "poster_pack")
        assert "poster_table" in pack
        assert "poster_fig" in pack
        assert "model_card" in pack
        for path in pack.values():
            assert path.exists()

    def test_poster_table_has_all_models(self, mock_results, mock_budgets,
                                         tmp_path):
        rows = compute_worst_case_summary(mock_results)
        out = tmp_path / "poster_table.csv"
        write_poster_table(rows, mock_budgets, out)
        text = out.read_text()
        assert "CUSUM" in text
        assert "RF" in text
        assert "Deep" in text

    def test_poster_figure_empty_results(self, mock_budgets, tmp_path):
        empty = DomainShiftResults()
        rows = compute_worst_case_summary(empty)
        fig = plot_poster_figure(empty, rows, mock_budgets)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)
