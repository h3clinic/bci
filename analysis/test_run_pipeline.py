"""
test_run_pipeline.py — Tiered tests for the pipeline.

Tier 1 (default):  Fast unit tests — guards, profiles, results, figure
                    functions with mock data, IO writers, QC on tiny data.
                    Run with: pytest test_run_pipeline.py

Tier 2 (@pytest.mark.slow):  Integration tests requiring realistic data and
                              actual training. Run with:
                              pytest test_run_pipeline.py -m slow
"""

from __future__ import annotations

import csv
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perturbation_library import (
    generate_dataset,
    LabeledWindow,
    PerturbationMeta,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
)
from run_pipeline import (
    DataProfile,
    StageResult,
    enough_data_for_rf,
    enough_data_for_deep,
    enough_data_for_rf_report,
    enough_data_for_deep_report,
    determine_training_mode,
    acquire_synthetic,
    run_qc,
    run_classification,
    _train_rf_guarded,
    _train_deep_guarded,
    make_fig6_stress_grid,
    make_fig7_domain_shift,
    generate_figures,
    write_predictions_csv,
    write_metrics_json,
    write_model_card,
    run_pipeline,
    save_model_checkpoint,
)


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _make_toy_window(
    perturbation_type: str = "baseline_stable",
    category: str = "baseline",
    severity: float = 0.0,
    n_channels: int = 4,
    fs: float = 1000.0,
    duration_s: float = 1.0,
    onset_s: float = 0.5,
    seed: int = 0,
) -> LabeledWindow:
    """Create a minimal toy window for fast tests."""
    rng = np.random.default_rng(seed)
    n_samples = int(fs * duration_s)
    data = rng.standard_normal((n_channels, n_samples)) * 50.0
    meta = PerturbationMeta(
        perturbation_type=perturbation_type,
        category=category,
        severity=severity,
        onset_s=onset_s,
        duration_s=duration_s,
        fs=fs,
        n_channels=n_channels,
        seed=seed,
    )
    return LabeledWindow(data_uv=data, meta=meta)


def _make_toy_dataset(n: int = 6, **kwargs) -> list[LabeledWindow]:
    """Create a small mixed dataset (half baseline, half perturbation)."""
    wins = []
    for i in range(n):
        if i % 2 == 0:
            wins.append(_make_toy_window(seed=i, **kwargs))
        else:
            wins.append(_make_toy_window(
                perturbation_type="spike_suppression",
                category="neurotox",
                severity=0.5,
                seed=i,
                **kwargs,
            ))
    return wins


# Realistic dataset fixture — expensive, only for Tier 2
@pytest.fixture(scope="module")
def realistic_dataset():
    """16ch, 20kHz, 5s — suitable for actual training."""
    return generate_dataset(
        n_per_class=5,
        severities=[0.3, 0.6, 0.9],
        n_channels=16,
        fs=20_000.0,
        duration_s=5.0,
        onset_s=2.0,
        seed=42,
    )


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — DataProfile
# ═══════════════════════════════════════════════════════════════════

class TestDataProfile:
    """Unit tests for the DataProfile guard structure."""

    def test_empty_dataset(self):
        p = DataProfile.from_dataset([])
        assert p.n_windows == 0
        assert p.n_channels == 0
        assert p.n_classes == 0
        assert p.min_per_class == 0

    def test_single_window(self):
        w = _make_toy_window()
        p = DataProfile.from_dataset([w])
        assert p.n_windows == 1
        assert p.n_channels == 4
        assert p.fs == 1000.0
        assert p.duration_s == 1.0
        assert p.n_classes == 1
        assert p.min_per_class == 1

    def test_mixed_dataset(self):
        wins = _make_toy_dataset(10)
        p = DataProfile.from_dataset(wins)
        assert p.n_windows == 10
        assert p.n_classes == 2  # baseline + neurotox

    def test_frozen(self):
        p = DataProfile(10, 4, 1000.0, 1.0, 2, 5)
        with pytest.raises(Exception):
            p.n_windows = 20  # type: ignore[misc]

    def test_from_dataset_uses_first_window_metadata(self):
        w1 = _make_toy_window(n_channels=8, fs=2000.0, duration_s=3.0)
        w2 = _make_toy_window(perturbation_type="spike_suppression",
                               category="neurotox", severity=0.5,
                               n_channels=8, fs=2000.0, duration_s=3.0, seed=1)
        p = DataProfile.from_dataset([w1, w2])
        assert p.n_channels == 8
        assert p.fs == 2000.0
        assert p.duration_s == 3.0


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — Guard functions
# ═══════════════════════════════════════════════════════════════════

class TestGuards:
    """Unit tests for enough_data_for_rf / enough_data_for_deep."""

    def test_rf_happy_path(self):
        p = DataProfile(50, 4, 2000.0, 5.0, 3, 10)
        assert enough_data_for_rf(p)

    def test_rf_too_few_windows(self):
        p = DataProfile(5, 4, 2000.0, 5.0, 3, 2)
        assert not enough_data_for_rf(p)

    def test_rf_too_few_channels(self):
        p = DataProfile(50, 0, 2000.0, 5.0, 3, 10)
        assert not enough_data_for_rf(p)

    def test_rf_low_fs(self):
        p = DataProfile(50, 4, 500.0, 5.0, 3, 10)
        assert not enough_data_for_rf(p)

    def test_rf_short_duration(self):
        p = DataProfile(50, 4, 2000.0, 0.5, 3, 10)
        assert not enough_data_for_rf(p)

    def test_rf_one_class(self):
        p = DataProfile(50, 4, 2000.0, 5.0, 1, 50)
        assert not enough_data_for_rf(p)

    def test_rf_too_few_per_class(self):
        p = DataProfile(50, 4, 2000.0, 5.0, 3, 2)
        assert not enough_data_for_rf(p)

    def test_rf_boundary(self):
        """Exact minimum should pass."""
        p = DataProfile(20, 1, 1000.0, 2.0, 2, 3)
        assert enough_data_for_rf(p)

    def test_deep_happy_path(self):
        p = DataProfile(100, 16, 20000.0, 5.0, 3, 10)
        assert enough_data_for_deep(p)

    def test_deep_too_few_windows(self):
        p = DataProfile(10, 16, 20000.0, 5.0, 3, 5)
        assert not enough_data_for_deep(p)

    def test_deep_too_few_channels(self):
        p = DataProfile(100, 2, 20000.0, 5.0, 3, 10)
        assert not enough_data_for_deep(p)

    def test_deep_low_fs(self):
        p = DataProfile(100, 16, 1000.0, 5.0, 3, 10)
        assert not enough_data_for_deep(p)

    def test_deep_short_duration(self):
        p = DataProfile(100, 16, 20000.0, 1.0, 3, 10)
        assert not enough_data_for_deep(p)

    def test_deep_one_class(self):
        p = DataProfile(100, 16, 20000.0, 5.0, 1, 100)
        assert not enough_data_for_deep(p)

    def test_deep_too_few_per_class(self):
        p = DataProfile(100, 16, 20000.0, 5.0, 3, 3)
        assert not enough_data_for_deep(p)

    def test_deep_boundary(self):
        """Exact minimum should pass."""
        p = DataProfile(30, 4, 2000.0, 2.0, 2, 5)
        assert enough_data_for_deep(p)

    def test_empty_profile_fails_both(self):
        p = DataProfile(0, 0, 0.0, 0.0, 0, 0)
        assert not enough_data_for_rf(p)
        assert not enough_data_for_deep(p)


# ═════════════════════════════════════════════════════════════════
#  TIER 1 — Report-tier guards (high bar)
# ═════════════════════════════════════════════════════════════════

class TestReportTierGuards:
    """Report-tier guards enforce scientifically honest thresholds."""

    def test_rf_report_needs_25_per_class(self):
        # Demo passes, report fails
        p = DataProfile(100, 4, 2000.0, 5.0, 3, 10)
        assert enough_data_for_rf(p)
        assert not enough_data_for_rf_report(p)

    def test_rf_report_needs_200_windows(self):
        p = DataProfile(150, 4, 2000.0, 5.0, 3, 30)
        assert enough_data_for_rf(p)
        assert not enough_data_for_rf_report(p)

    def test_rf_report_passes(self):
        p = DataProfile(500, 16, 20000.0, 5.0, 3, 50)
        assert enough_data_for_rf_report(p)

    def test_deep_report_needs_50_per_class(self):
        p = DataProfile(200, 16, 20000.0, 5.0, 3, 20)
        assert enough_data_for_deep(p)
        assert not enough_data_for_deep_report(p)

    def test_deep_report_needs_400_windows(self):
        p = DataProfile(300, 16, 20000.0, 5.0, 3, 55)
        assert enough_data_for_deep(p)
        assert not enough_data_for_deep_report(p)

    def test_deep_report_passes(self):
        p = DataProfile(600, 16, 20000.0, 5.0, 3, 60)
        assert enough_data_for_deep_report(p)

    def test_empty_fails_report(self):
        p = DataProfile(0, 0, 0.0, 0.0, 0, 0)
        assert not enough_data_for_rf_report(p)
        assert not enough_data_for_deep_report(p)


# ═════════════════════════════════════════════════════════════════
#  TIER 1 — determine_training_mode
# ═════════════════════════════════════════════════════════════════

class TestTrainingMode:
    """Training mode reflects data quality."""

    def test_none_on_garbage(self):
        p = DataProfile(5, 2, 500.0, 0.5, 1, 5)
        assert determine_training_mode(p) == "none"

    def test_demo_on_small(self):
        p = DataProfile(50, 4, 2000.0, 5.0, 3, 10)
        assert determine_training_mode(p) == "demo"

    def test_report_on_large(self):
        p = DataProfile(600, 16, 20000.0, 5.0, 3, 60)
        assert determine_training_mode(p) == "report"

    def test_empty_is_none(self):
        p = DataProfile(0, 0, 0.0, 0.0, 0, 0)
        assert determine_training_mode(p) == "none"


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — StageResult
# ═══════════════════════════════════════════════════════════════════

class TestStageResult:
    """StageResult construction and semantics."""

    def test_ok_result(self):
        r = StageResult(status="ok", metrics={"acc": 0.9})
        assert r.status == "ok"
        assert r.reason == ""
        assert r.metrics["acc"] == 0.9

    def test_skipped_result(self):
        r = StageResult(status="skipped", reason="too few windows")
        assert r.status == "skipped"
        assert "too few" in r.reason
        assert r.metrics == {}

    def test_default_metrics_empty(self):
        r = StageResult(status="ok")
        assert r.metrics == {}


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — Acquire
# ═══════════════════════════════════════════════════════════════════

class TestAcquire:
    """Fast tests for acquire_synthetic."""

    def test_synthetic_returns_dataset_and_meta(self):
        dataset, meta = acquire_synthetic(n_per_class=1, seed=99)
        assert isinstance(dataset, list)
        assert len(dataset) > 0
        assert meta["source"] == "synthetic"
        assert meta["n_per_class"] == 1
        assert meta["seed"] == 99

    def test_synthetic_window_has_correct_shape(self):
        dataset, meta = acquire_synthetic(n_per_class=1, n_channels=8, fs=5000.0)
        w = dataset[0]
        assert w.data_uv.shape[0] == 8
        expected_samples = int(5.0 * 5000.0)
        assert w.data_uv.shape[1] == expected_samples

    def test_synthetic_deterministic(self):
        d1, _ = acquire_synthetic(n_per_class=1, seed=7)
        d2, _ = acquire_synthetic(n_per_class=1, seed=7)
        np.testing.assert_array_equal(d1[0].data_uv, d2[0].data_uv)


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — QC (runs on any data)
# ═══════════════════════════════════════════════════════════════════

class TestQC:
    """QC should never crash, even on tiny garbage data."""

    def test_qc_on_toy_data(self):
        wins = _make_toy_dataset(4)
        results = run_qc(wins, fs=1000.0, onset_s=0.5)
        assert len(results) == 4
        for r in results:
            assert "detected" in r
            assert "latency_s" in r
            assert "perturbation_type" in r

    def test_qc_all_baseline(self):
        wins = [_make_toy_window(seed=i) for i in range(3)]
        results = run_qc(wins, fs=1000.0, onset_s=0.5)
        assert all(r["category"] == "baseline" for r in results)

    def test_qc_single_window(self):
        results = run_qc([_make_toy_window()], fs=1000.0, onset_s=0.5)
        assert len(results) == 1

    def test_qc_empty_dataset(self):
        results = run_qc([], fs=1000.0, onset_s=0.5)
        assert results == []

    def test_qc_cusum_max_is_numeric(self):
        wins = _make_toy_dataset(2)
        results = run_qc(wins, fs=1000.0, onset_s=0.5)
        for r in results:
            if r["cusum_max"] is not None:
                assert isinstance(r["cusum_max"], float)


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — Classification guard behavior
# ═══════════════════════════════════════════════════════════════════

class TestClassificationGuards:
    """Classification stages must skip gracefully on toy data."""

    def test_rf_skips_on_toy_data(self):
        """4ch, 1kHz, 1s — way too small for RF."""
        wins = _make_toy_dataset(6)
        profile = DataProfile.from_dataset(wins)
        result = _train_rf_guarded(wins, profile, seed=42)
        assert result.status == "skipped"
        assert "Insufficient" in result.reason

    def test_deep_skips_on_toy_data(self):
        """4ch, 1kHz, 1s — way too small for deep model."""
        wins = _make_toy_dataset(6)
        profile = DataProfile.from_dataset(wins)
        result = _train_deep_guarded(wins, profile, seed=42)
        assert result.status == "skipped"
        assert "Insufficient" in result.reason

    def test_run_classification_returns_structure(self):
        """Even on garbage data, returns complete dict with status keys."""
        wins = _make_toy_dataset(4)
        result = run_classification(wins, seed=42)
        assert "stage2_rf" in result
        assert "stage2_deep" in result
        assert result["stage2_rf"]["status"] in ("ok", "skipped")
        assert result["stage2_deep"]["status"] in ("ok", "skipped")
        assert "reason" in result["stage2_rf"]
        assert "metrics" in result["stage2_rf"]

    def test_run_classification_both_skip_on_toy(self):
        wins = _make_toy_dataset(4)
        result = run_classification(wins, seed=42)
        assert result["stage2_rf"]["status"] == "skipped"
        assert result["stage2_deep"]["status"] == "skipped"

    def test_empty_dataset_skips_both(self):
        result = run_classification([], seed=42)
        assert result["stage2_rf"]["status"] == "skipped"
        assert result["stage2_deep"]["status"] == "skipped"


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — Figure functions (consume precomputed data, never train)
# ═══════════════════════════════════════════════════════════════════

class TestFigures:
    """Figures take precomputed arrays — no training, no data generation."""

    def test_fig6_stress_grid_with_data(self, tmp_path):
        noise_levels = [0.5, 1.0, 2.0]
        severities = [0.3, 0.6, 0.9]
        grids = {
            "CUSUM": np.random.rand(3, 3),
            "RF": np.random.rand(3, 3),
        }
        fig = make_fig6_stress_grid(grids, noise_levels, severities,
                                     output_path=tmp_path / "fig6.png")
        assert fig is not None
        assert (tmp_path / "fig6.png").exists()
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_fig6_stress_grid_no_methods(self, tmp_path):
        """If all methods skipped, figure still renders (with message)."""
        fig = make_fig6_stress_grid({}, [1.0], [0.5],
                                     output_path=tmp_path / "fig6_empty.png")
        assert fig is not None
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_fig6_stress_grid_cusum_only(self, tmp_path):
        fig = make_fig6_stress_grid(
            {"CUSUM": np.array([[0.8, 0.6], [0.9, 0.7]])},
            [1.0, 2.0], [0.3, 0.9],
            output_path=tmp_path / "fig6_cusum.png",
        )
        assert fig is not None
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_fig7_domain_shift_with_data(self, tmp_path):
        rf_accs = {"in-dist": 0.9, "noise_floor": 0.7, "coupling_shift": 0.6}
        deep_accs = {"in-dist": 0.85, "noise_floor": 0.8, "coupling_shift": 0.75}
        shift_types = ["noise_floor", "coupling_shift"]
        fig = make_fig7_domain_shift(rf_accs, deep_accs, shift_types,
                                      output_path=tmp_path / "fig7.png")
        assert fig is not None
        assert (tmp_path / "fig7.png").exists()
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_fig7_domain_shift_empty_accs(self, tmp_path):
        fig = make_fig7_domain_shift({}, {}, [],
                                      output_path=tmp_path / "fig7_empty.png")
        assert fig is not None
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_fig6_no_save_when_no_path(self):
        grids = {"CUSUM": np.array([[0.5]])}
        fig = make_fig6_stress_grid(grids, [1.0], [0.5], output_path=None)
        assert fig is not None
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_plot_model_comparison_with_mock_data(self, tmp_path):
        """plot_model_comparison takes precomputed results — no training."""
        from eval_report import ModelComparisonResult, plot_model_comparison
        comp = ModelComparisonResult(
            classical_acc=0.6, classical_bin_acc=0.5,
            classical_per_class={"baseline_stable": 0.8, "spike_suppression": 0.4},
            rf_acc=0.75, rf_binary_acc=0.8,
            rf_per_class={"baseline_stable": 0.9, "spike_suppression": 0.6},
            deep_acc=0.7, deep_bin_acc=0.65,
            deep_per_class={"baseline_stable": 0.85, "spike_suppression": 0.55},
            n_val=20, n_total=100,
        )
        fig = plot_model_comparison(comp, output_path=tmp_path / "fig5.png")
        assert fig is not None
        assert (tmp_path / "fig5.png").exists()
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_plot_model_comparison_empty(self, tmp_path):
        """Empty result still produces a figure."""
        from eval_report import ModelComparisonResult, plot_model_comparison
        comp = ModelComparisonResult()
        fig = plot_model_comparison(comp, output_path=tmp_path / "fig5_empty.png")
        assert fig is not None
        import matplotlib.pyplot as plt
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — IO writers
# ═══════════════════════════════════════════════════════════════════

class TestIOWriters:
    """CSV, JSON, and model card writers."""

    def test_write_predictions_csv(self, tmp_path):
        qc = [
            {"window_idx": 0, "detected": True, "latency_s": 0.1,
             "perturbation_type": "baseline_stable", "category": "baseline",
             "severity": 0.0, "cusum_max": 6.5},
            {"window_idx": 1, "detected": False, "latency_s": None,
             "perturbation_type": "spike_suppression", "category": "neurotox",
             "severity": 0.5, "cusum_max": 2.3},
        ]
        out = tmp_path / "pred.csv"
        write_predictions_csv(qc, out)
        assert out.exists()
        with open(out) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["detected"] == "True"

    def test_write_predictions_csv_empty(self, tmp_path):
        out = tmp_path / "pred_empty.csv"
        write_predictions_csv([], out)
        assert not out.exists()

    def test_write_metrics_json(self, tmp_path):
        meta = {"source": "synthetic", "seed": 42}
        qc = [
            {"detected": True, "latency_s": 0.05, "category": "neurotox",
             "perturbation_type": "spike_suppression", "severity": 0.5},
            {"detected": False, "latency_s": None, "category": "baseline",
             "perturbation_type": "baseline_stable", "severity": 0.0},
        ]
        classification = {
            "stage2_rf": {"status": "skipped", "reason": "toy data", "metrics": {}},
            "stage2_deep": {"status": "skipped", "reason": "toy data", "metrics": {}},
        }
        out = tmp_path / "metrics.json"
        write_metrics_json(meta, qc, classification, out,
                           training_mode="demo", data_source="synthetic")
        assert out.exists()
        with open(out) as f:
            data = json.load(f)
        assert data["pipeline_version"] == "2.0.0"
        assert data["training_mode"] == "demo"
        assert data["data_source"] == "synthetic"
        assert data["detection"]["n_windows"] == 2
        assert "classification" in data
        assert "per_category" in data["detection"]

    def test_write_model_card(self, tmp_path):
        out = tmp_path / "card.md"
        write_model_card(out, {"source": "synthetic", "seed": 42})
        assert out.exists()
        text = out.read_text()
        assert "Model Card" in text
        assert "Proxy Labels" in text
        assert "We never claim neurons" in text


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — Full pipeline on toy data (should never crash)
# ═══════════════════════════════════════════════════════════════════

class TestPipelineToy:
    """Pipeline runs on garbage inputs without crashing."""

    def test_pipeline_synthetic_minimal(self, tmp_path):
        """Minimal synthetic run: 1 per class, tiny data. Should not crash."""
        out = run_pipeline(
            source="synthetic",
            out_dir=str(tmp_path / "toy_run"),
            n_per_class=1,
            duration_s=5.0,
            onset_s=2.0,
            seed=42,
            deep_epochs=1,
            save_model=False,
        )
        assert out.exists()
        assert (out / "predictions.csv").exists()
        assert (out / "metrics.json").exists()
        assert (out / "model_card.md").exists()

        with open(out / "metrics.json") as f:
            data = json.load(f)
        assert data["source"]["source"] == "synthetic"

    def test_pipeline_produces_status_in_json(self, tmp_path):
        out = run_pipeline(
            source="synthetic",
            out_dir=str(tmp_path / "status_run"),
            n_per_class=1,
            duration_s=5.0,
            onset_s=2.0,
            seed=42,
            deep_epochs=1,
            save_model=False,
        )
        with open(out / "metrics.json") as f:
            data = json.load(f)
        cls = data["classification"]
        assert "stage2_rf" in cls
        assert "stage2_deep" in cls
        assert cls["stage2_rf"]["status"] in ("ok", "skipped")
        assert cls["stage2_deep"]["status"] in ("ok", "skipped")
        # Two-tier tags present
        assert data["training_mode"] in ("none", "demo", "report")
        assert data["data_source"] == "synthetic"

    def test_pipeline_missing_data_path_raises(self):
        with pytest.raises(ValueError, match="--data required"):
            run_pipeline(source="npz", data_path=None)

    def test_pipeline_unknown_source_raises(self):
        with pytest.raises(ValueError, match="Unknown source"):
            run_pipeline(source="invalid_source")


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — save_model_checkpoint guard
# ═══════════════════════════════════════════════════════════════════

class TestSaveModelCheckpoint:
    """Model checkpoint save is guarded."""

    def test_skips_on_toy_data(self, tmp_path):
        wins = _make_toy_dataset(4)
        path, status = save_model_checkpoint(tmp_path / "models", wins, seed=42)
        assert path is None
        assert "skipped" in status

    def test_skips_on_empty_data(self, tmp_path):
        path, status = save_model_checkpoint(tmp_path / "models", [], seed=42)
        assert path is None
        assert "skipped" in status


# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — generate_figures guard behavior
# ═══════════════════════════════════════════════════════════════════

class TestGenerateFigures:
    """Figures are guarded: skip when data insufficient."""

    def test_generates_fig1_on_toy_data(self, tmp_path):
        """Fig 1 (onset detection) has no training — should always run."""
        dataset, meta = acquire_synthetic(n_per_class=1, seed=42)
        saved = generate_figures(
            dataset, tmp_path,
            onset_s=2.0, fs=20_000.0, seed=42, dpi=72,
        )
        assert "fig1_onset_detection.png" in saved

    def test_skips_fig2_on_insufficient_data(self, tmp_path, capsys):
        """Fig 2 requires RF-sufficient data."""
        wins = _make_toy_dataset(4, n_channels=4, fs=1000.0, duration_s=1.0)
        saved = generate_figures(
            wins, tmp_path,
            onset_s=0.5, fs=1000.0, seed=42, dpi=72,
        )
        assert "fig2_cause_classification.png" not in saved


# ═══════════════════════════════════════════════════════════════════
#  TIER 2 — Slow integration tests (require realistic data + training)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestClassificationRealistic:
    """RF and Deep training on realistic data."""

    def test_rf_trains_on_realistic_data(self, realistic_dataset):
        profile = DataProfile.from_dataset(realistic_dataset)
        assert enough_data_for_rf(profile)
        result = _train_rf_guarded(realistic_dataset, profile, seed=42)
        assert result.status == "ok", f"RF unexpectedly skipped: {result.reason}"
        assert "accuracy_multi" in result.metrics
        assert "accuracy_binary" in result.metrics
        assert 0.0 <= result.metrics["accuracy_binary"] <= 1.0

    def test_deep_trains_on_realistic_data(self, realistic_dataset):
        profile = DataProfile.from_dataset(realistic_dataset)
        assert enough_data_for_deep(profile)
        result = _train_deep_guarded(
            realistic_dataset, profile, seed=42,
            max_samples=4000, n_epochs=2,
        )
        assert result.status == "ok", f"Deep unexpectedly skipped: {result.reason}"
        assert "accuracy" in result.metrics
        assert "binary_accuracy" in result.metrics
        assert result.metrics["n_params"] > 0

    def test_run_classification_realistic(self, realistic_dataset):
        result = run_classification(
            realistic_dataset, seed=42, max_samples=4000, deep_epochs=2,
        )
        assert result["stage2_rf"]["status"] == "ok"
        assert result["stage2_deep"]["status"] == "ok"


@pytest.mark.slow
class TestFullPipelineRealistic:
    """Full pipeline integration with realistic parameters."""

    def test_full_pipeline(self, tmp_path):
        out = run_pipeline(
            source="synthetic",
            out_dir=str(tmp_path / "full_run"),
            n_per_class=5,
            duration_s=5.0,
            onset_s=2.0,
            seed=42,
            max_samples=4000,
            deep_epochs=2,
            dpi=72,
            save_model=False,
        )
        assert out.exists()
        assert (out / "predictions.csv").exists()
        assert (out / "metrics.json").exists()
        assert (out / "model_card.md").exists()

        with open(out / "metrics.json") as f:
            data = json.load(f)
        assert data["classification"]["stage2_rf"]["status"] == "ok"


@pytest.mark.slow
class TestStressGridCompute:
    """Stress grid computation — trains models per cell."""

    def test_compute_stress_grid(self):
        from run_pipeline import compute_stress_grid
        grids, noise_levels, severities = compute_stress_grid(
            n_channels=16, fs=20_000.0, duration_s=5.0,
            onset_s=2.0, seed=42, max_samples=4000, deep_epochs=1,
        )
        assert "CUSUM" in grids
        assert grids["CUSUM"].shape == (4, 4)
        assert len(noise_levels) == 4
        assert len(severities) == 4
        # CUSUM should detect at least some perturbations
        assert np.any(grids["CUSUM"] > 0)


@pytest.mark.slow
class TestDomainShiftCompute:
    """Domain shift computation — trains on in-dist, evaluates on shifted."""

    def test_compute_domain_shift(self):
        from run_pipeline import compute_domain_shift
        rf_accs, deep_accs, shift_types = compute_domain_shift(
            n_channels=16, fs=20_000.0, duration_s=5.0,
            onset_s=2.0, seed=42, max_samples=4000, deep_epochs=2,
        )
        assert "in-dist" in rf_accs
        assert len(shift_types) == 5


@pytest.mark.slow
class TestSaveModelCheckpointRealistic:
    """Model checkpoint with realistic data."""

    def test_saves_checkpoint(self, tmp_path, realistic_dataset):
        path, status = save_model_checkpoint(
            tmp_path / "models", realistic_dataset, seed=42,
        )
        assert status == "ok"
        assert path is not None
        assert path.exists()
        import torch
        ckpt = torch.load(path, weights_only=False)
        assert "model_state" in ckpt
        assert "config" in ckpt
        assert ckpt["version"] == "1.0.0"
