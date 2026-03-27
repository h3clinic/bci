"""
test_streaming_detector.py — Tests for the streaming two-stage detector.

Tests:
  TestAlert             — Alert dataclass construction
  TestStreamingDetector — from_training_data, process_recording
  TestSweepResult       — SweepResult dataclass
  TestNoiseSweep        — run_noise_sweep on tiny data
  TestWriteSweep        — write_sweep_results produces CSV + PNG
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
import matplotlib
matplotlib.use("Agg")

from streaming_detector import (
    Alert,
    StreamingDetector,
    StreamingSummary,
    SweepResult,
    run_noise_sweep,
    write_sweep_results,
)
from perturbation_library import generate_dataset


# ── Fixtures ─────────────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DURATION = 10.0
ONSET = 4.0
SEED = 99


@pytest.fixture(scope="module")
def train_data():
    """Small training set (4 channels, short duration)."""
    return generate_dataset(
        n_per_class=2,
        severities=[0.5],
        n_channels=N_CH,
        fs=FS,
        duration_s=DURATION,
        onset_s=ONSET,
        seed=SEED,
    )


@pytest.fixture(scope="module")
def detector(train_data):
    """Trained streaming detector on small data."""
    return StreamingDetector.from_training_data(
        train_data, fs=FS, window_s=1.0, seed=SEED,
        baseline_s=ONSET - 1.0,
    )


# ── TestAlert ────────────────────────────────────────────────────────

class TestAlert:
    def test_construction(self):
        a = Alert(
            timestamp_s=5.0, latency_s=1.0,
            binary_label="neurotox", confidence=0.85,
            reason_code="spike_suppression",
            reason_probs={"spike_suppression": 0.85, "baseline": 0.15},
            stage1_stat=6.5, window_idx=5,
        )
        assert a.binary_label == "neurotox"
        assert a.confidence == 0.85
        assert a.window_idx == 5

    def test_artifact_label(self):
        a = Alert(
            timestamp_s=3.0, latency_s=None,
            binary_label="artifact", confidence=0.2,
            reason_code="sixty_hz",
            reason_probs={"sixty_hz": 0.8},
            stage1_stat=5.5, window_idx=3,
        )
        assert a.binary_label == "artifact"
        assert a.confidence < 0.5


# ── TestStreamingDetector ────────────────────────────────────────────

class TestStreamingDetector:
    def test_from_training_data(self, detector):
        assert detector.rf_binary is not None
        assert detector.rf_multi is not None
        assert detector.scaler is not None
        assert len(detector.type_names) > 0

    def test_process_clean_recording(self, detector):
        """Clean recording → no alerts (or very few false alarms)."""
        rng = np.random.default_rng(123)
        clean = rng.normal(0, 3.0, (N_CH, int(DURATION * FS)))
        summary = detector.process_recording(clean)
        assert isinstance(summary, StreamingSummary)
        assert summary.n_windows > 0
        # Clean signal should have very few alerts
        assert summary.n_alerts <= 2  # allow ≤2 false alarms

    def test_process_perturbed_recording(self, detector):
        """Recording with spike suppression → should trigger alert."""
        from signal_gen import generate_neural_signal
        data = generate_neural_signal(
            n_channels=N_CH, fs=FS, duration_s=DURATION, seed=200,
        )
        # Apply heavy suppression after onset
        onset_samp = int(ONSET * FS)
        data[:, onset_samp:] *= 0.1

        summary = detector.process_recording(data, onset_s_true=ONSET)
        assert isinstance(summary, StreamingSummary)
        # Should detect the change
        assert summary.n_alerts >= 1
        if summary.alerts:
            assert summary.alerts[0].timestamp_s > 0

    def test_summary_fields(self, detector):
        rng = np.random.default_rng(456)
        data = rng.normal(0, 3.0, (N_CH, int(DURATION * FS)))
        summary = detector.process_recording(data)
        assert hasattr(summary, "n_windows")
        assert hasattr(summary, "n_alerts")
        assert hasattr(summary, "false_alarms")
        assert hasattr(summary, "elapsed_s")
        assert summary.elapsed_s > 0

    def test_short_recording(self, detector):
        """Very short recording → no crash."""
        data = np.random.default_rng(789).normal(0, 3.0, (N_CH, 1000))
        summary = detector.process_recording(data)
        assert summary.n_windows >= 0


# ── TestSweepResult ──────────────────────────────────────────────────

class TestSweepResult:
    def test_construction(self):
        r = SweepResult(
            noise_type="sixty_hz", severity=0.6,
            n_alerts=1, n_neurotox=0, n_artifact=1,
            false_alarms=0, latency_median_s=2.0,
            latency_p90_s=2.5, confidence_mean=0.3,
            processing_time_s=0.5,
        )
        assert r.noise_type == "sixty_hz"
        assert r.n_artifact == 1


# ── TestNoiseSweep ───────────────────────────────────────────────────

@pytest.mark.slow
class TestNoiseSweep:
    def test_sweep_completes(self, detector):
        """Noise sweep runs and returns results."""
        results = run_noise_sweep(
            detector, n_channels=N_CH, fs=FS,
            duration_s=DURATION, onset_s=ONSET, seed=SEED,
        )
        assert len(results) > 0
        assert all(isinstance(r, SweepResult) for r in results)

    def test_sweep_has_baseline(self, detector):
        results = run_noise_sweep(
            detector, n_channels=N_CH, fs=FS,
            duration_s=DURATION, onset_s=ONSET, seed=SEED,
        )
        baselines = [r for r in results if r.noise_type == "baseline"]
        assert len(baselines) >= 1


# ── TestWriteSweep ───────────────────────────────────────────────────

class TestWriteSweep:
    def test_write_produces_files(self):
        results = [
            SweepResult("baseline", 0.0, 0, 0, 0, 0, None, None, 0.0, 0.1),
            SweepResult("sixty_hz", 0.6, 1, 0, 1, 0, 2.0, 2.5, 0.3, 0.2),
            SweepResult("spike_suppression", 0.6, 1, 1, 0, 0, 1.5, 2.0, 0.8, 0.3),
        ]
        with tempfile.TemporaryDirectory() as td:
            write_sweep_results(results, td)
            files = list(Path(td).iterdir())
            names = {f.name for f in files}
            assert "noise_sweep.csv" in names
            assert "noise_sweep.png" in names
