"""
test_noise_immunity.py — Tests for the noise immunity proof module.

Tests:
  TestFPRBudget        — FPR sweep returns rows, correct schema
  TestCalibration      — Calibration produces valid ECE, p*, bins
  TestAdversarial      — Adversarial sweep covers all conditions
  TestOperatingPoint   — Operating point summary renders
  TestWriteArtifacts   — CSV/PNG files are created
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
import matplotlib
matplotlib.use("Agg")

from noise_immunity import (
    FPRRow,
    run_fpr_budget_sweep,
    write_fpr_budget,
    CalibrationResult,
    compute_calibration,
    write_calibration,
    AdversarialRow,
    run_adversarial_sweep,
    write_adversarial_results,
    write_operating_point,
    _apply_cooldown,
    _build_adversarial_conditions,
)
from streaming_detector import StreamingDetector, Alert
from perturbation_library import generate_dataset


# ── Shared fixtures ──────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DURATION = 10.0
ONSET = 4.0
SEED = 77


@pytest.fixture(scope="module")
def train_data():
    return generate_dataset(
        n_per_class=2, severities=[0.5],
        n_channels=N_CH, fs=FS,
        duration_s=DURATION, onset_s=ONSET, seed=SEED,
    )


@pytest.fixture(scope="module")
def detector(train_data):
    return StreamingDetector.from_training_data(
        train_data, fs=FS, window_s=1.0, seed=SEED,
        baseline_s=ONSET - 1.0,
    )


# ── TestFPRBudget ────────────────────────────────────────────────

class TestFPRBudget:
    def test_returns_rows(self, detector):
        rows = run_fpr_budget_sweep(
            detector,
            thresholds=[5.0, 8.0],
            cooldowns_s=[0.0, 5.0],
            noise_conditions={"clean": lambda d, fs, rng: d},
            n_channels=N_CH, fs=FS,
            duration_min=0.5,  # 30s — fast
            seed=SEED,
        )
        assert len(rows) > 0
        assert all(isinstance(r, FPRRow) for r in rows)

    def test_row_fields(self, detector):
        rows = run_fpr_budget_sweep(
            detector,
            thresholds=[5.0],
            cooldowns_s=[0.0],
            noise_conditions={"clean": lambda d, fs, rng: d},
            n_channels=N_CH, fs=FS,
            duration_min=0.5,
            seed=SEED,
        )
        r = rows[0]
        assert r.cusum_threshold == 5.0
        assert r.cooldown_s == 0.0
        assert r.noise_condition == "clean"
        assert r.fpr_per_hour >= 0

    def test_higher_threshold_fewer_alarms(self, detector):
        """Higher CUSUM threshold should produce same or fewer false alarms."""
        rows = run_fpr_budget_sweep(
            detector,
            thresholds=[3.0, 10.0],
            cooldowns_s=[0.0],
            noise_conditions={"clean": lambda d, fs, rng: d},
            n_channels=N_CH, fs=FS,
            duration_min=0.5,
            seed=SEED,
        )
        low_thresh = [r for r in rows if r.cusum_threshold == 3.0]
        high_thresh = [r for r in rows if r.cusum_threshold == 10.0]
        assert low_thresh[0].n_false_alarms >= high_thresh[0].n_false_alarms

    def test_write_fpr_budget(self, detector):
        rows = run_fpr_budget_sweep(
            detector,
            thresholds=[5.0],
            cooldowns_s=[0.0],
            noise_conditions={"clean": lambda d, fs, rng: d},
            n_channels=N_CH, fs=FS,
            duration_min=0.5,
            seed=SEED,
        )
        with tempfile.TemporaryDirectory() as td:
            write_fpr_budget(rows, td)
            assert (Path(td) / "fpr_budget.csv").exists()
            assert (Path(td) / "fpr_budget.png").exists()


# ── TestCalibration ──────────────────────────────────────────────

class TestCalibration:
    def test_produces_result(self, detector, train_data):
        cal = compute_calibration(detector, train_data, n_bins=5, fs=FS)
        assert isinstance(cal, CalibrationResult)
        assert 0 <= cal.ece <= 1
        assert 0 < cal.optimal_threshold < 1
        assert cal.n_samples == len(train_data)

    def test_bin_counts_sum_to_total(self, detector, train_data):
        cal = compute_calibration(detector, train_data, n_bins=5, fs=FS)
        assert cal.bin_counts.sum() == cal.n_samples

    def test_write_calibration(self, detector, train_data):
        cal = compute_calibration(detector, train_data, n_bins=5, fs=FS)
        with tempfile.TemporaryDirectory() as td:
            write_calibration(cal, td)
            assert (Path(td) / "calibration.png").exists()


# ── TestCooldown ─────────────────────────────────────────────────

class TestCooldown:
    def test_no_cooldown_keeps_all(self):
        alerts = [
            Alert(1.0, None, "neurotox", 0.9, "spike_suppression", {}, 6.0, 1),
            Alert(2.0, None, "artifact", 0.3, "sixty_hz", {}, 5.5, 2),
            Alert(3.0, None, "neurotox", 0.8, "spike_suppression", {}, 7.0, 3),
        ]
        result = _apply_cooldown(alerts, cooldown_s=0.0)
        assert len(result) == 3

    def test_cooldown_filters(self):
        alerts = [
            Alert(1.0, None, "neurotox", 0.9, "spike_suppression", {}, 6.0, 1),
            Alert(2.0, None, "artifact", 0.3, "sixty_hz", {}, 5.5, 2),
            Alert(15.0, None, "neurotox", 0.8, "spike_suppression", {}, 7.0, 3),
        ]
        result = _apply_cooldown(alerts, cooldown_s=10.0)
        assert len(result) == 2  # first + third (2.0 too close to 1.0)

    def test_empty_alerts(self):
        assert _apply_cooldown([], cooldown_s=5.0) == []


# ── TestAdversarial ──────────────────────────────────────────────

class TestAdversarial:
    def test_conditions_list(self):
        conds = _build_adversarial_conditions()
        assert len(conds) >= 20  # 10 types × 3 severities - some may vary
        names = set(c["name"] for c in conds)
        assert "60Hz+harmonics+drift" in names
        assert "spike_suppression" in names
        assert "ADC_clipping" in names

    def test_sweep_returns_rows(self, detector):
        # Run with just 2 conditions for speed
        conds = _build_adversarial_conditions()[:2]
        rows = run_adversarial_sweep(
            detector, n_channels=N_CH, fs=FS,
            duration_s=DURATION, onset_s=ONSET,
            seed=SEED, conditions=conds,
        )
        assert len(rows) == 2
        assert all(isinstance(r, AdversarialRow) for r in rows)

    def test_write_adversarial_results(self):
        rows = [
            AdversarialRow(
                condition="sixty_hz", severity=0.6, description="test",
                n_alerts=1, n_neurotox=0, n_artifact=1, n_uncertain=0,
                false_alarms=0, latency_median_s=2.0, latency_p90_s=2.5,
                confidence_mean=0.3, correct_classification=True,
                triaged_label="artifact", mahal_distance=3.5,
                ms_per_s=1.0, realtime_factor=1000.0,
            ),
            AdversarialRow(
                condition="spike_suppression", severity=0.6,
                description="test", n_alerts=1, n_neurotox=1,
                n_artifact=0, n_uncertain=0, false_alarms=0,
                latency_median_s=1.5, latency_p90_s=2.0,
                confidence_mean=0.8, correct_classification=True,
                triaged_label="suppression-like", mahal_distance=2.1,
                ms_per_s=1.0, realtime_factor=1000.0,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            write_adversarial_results(rows, td)
            assert (Path(td) / "noise_immunity_table.csv").exists()
            assert (Path(td) / "noise_immunity.png").exists()


# ── TestOperatingPoint ───────────────────────────────────────────

class TestOperatingPoint:
    def test_write_operating_point(self):
        fpr_rows = [
            FPRRow(5.0, 0.0, "clean", 10.0, 0, 0, 0.0, 0.0),
            FPRRow(5.0, 5.0, "clean", 10.0, 0, 0, 0.0, 0.0),
        ]
        cal = CalibrationResult(
            bin_edges=np.linspace(0, 1, 6),
            bin_means_predicted=np.array([0.1, 0.3, 0.5, 0.7, 0.9]),
            bin_means_actual=np.array([0.0, 0.2, 0.5, 0.8, 1.0]),
            bin_counts=np.array([10, 10, 10, 10, 10]),
            ece=0.05, mce=0.1, n_samples=50,
            optimal_threshold=0.55,
        )
        adv_rows = [
            AdversarialRow(
                condition="sixty_hz", severity=0.6, description="test",
                n_alerts=1, n_neurotox=0, n_artifact=1, n_uncertain=0,
                false_alarms=0, latency_median_s=2.0, latency_p90_s=2.5,
                confidence_mean=0.3, correct_classification=True,
                triaged_label="artifact", mahal_distance=3.5,
                ms_per_s=1.0, realtime_factor=1000.0,
            ),
            AdversarialRow(
                condition="spike_suppression", severity=0.6,
                description="test", n_alerts=1, n_neurotox=1,
                n_artifact=0, n_uncertain=0, false_alarms=0,
                latency_median_s=1.5, latency_p90_s=2.0,
                confidence_mean=0.8, correct_classification=True,
                triaged_label="suppression-like", mahal_distance=2.1,
                ms_per_s=1.0, realtime_factor=1000.0,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            text = write_operating_point(fpr_rows, cal, adv_rows, td)
            assert "Operating Point Summary" in text
            assert (Path(td) / "operating_point.txt").exists()
