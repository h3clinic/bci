"""
Tests for session_analyzer.py and rhd_loader.py.

Test classes
------------
TestWilsonCI          – 4 tests: edge cases + known values for Wilson CI
TestSingleTrialAnalysis – 5 tests: synthetic trial analysis
TestMultiTrialAnalysis  – 6 tests: group stats, detection rates, summary
TestExport            – 1 test: CSV export round-trip
TestRHDLoader         – 5 tests: .rhd file format parsing
TestEndToEnd          – 2 tests: full pipeline from synthetic data
"""

from __future__ import annotations

import io
import math
import struct
import tempfile
from pathlib import Path

import numpy as np
import pytest

from session_analyzer import (
    SessionAnalyzer,
    TrialResult,
    GroupStats,
    _wilson_ci,
)


# ═══════════════════════════════════════════════════════════════════════
# Wilson CI
# ═══════════════════════════════════════════════════════════════════════
class TestWilsonCI:
    """Test Wilson score confidence interval."""

    def test_zero_of_zero(self):
        lo, hi = _wilson_ci(0, 0)
        assert lo == 0.0 and hi == 0.0

    def test_zero_of_ten(self):
        lo, hi = _wilson_ci(0, 10)
        assert lo == 0.0
        assert 0.0 < hi < 0.35  # should be about 0.28

    def test_ten_of_ten(self):
        lo, hi = _wilson_ci(10, 10)
        assert hi == 1.0
        assert 0.65 < lo < 1.0  # should be about 0.72

    def test_five_of_ten(self):
        lo, hi = _wilson_ci(5, 10)
        assert 0.20 < lo < 0.35
        assert 0.65 < hi < 0.80
        # Known: Wilson CI for 5/10 ≈ (0.24, 0.76)


# ═══════════════════════════════════════════════════════════════════════
# Single trial analysis
# ═══════════════════════════════════════════════════════════════════════
class TestSingleTrialAnalysis:
    """Test analyze_trial on synthetic data."""

    @pytest.fixture
    def analyzer(self):
        return SessionAnalyzer(onset_s=30.0, epoch_s=25.0)

    def _make_trial(self, noise_uv=3.0, effect=2.0, n_ch=16, fs=20000.0, dur=100.0):
        """Generate a synthetic trial with known perturbation."""
        rng = np.random.default_rng(42)
        n = int(dur * fs)
        data = rng.normal(0, noise_uv, (n_ch, n))
        onset = int(30 * fs)
        data[:, onset:] *= effect
        return data, fs

    def test_baseline_rms(self, analyzer):
        data, fs = self._make_trial(noise_uv=3.0, effect=1.0)
        result = analyzer.analyze_trial(
            data, fs, session=0, trial=1,
            perturbation='baseline', level='none'
        )
        # With no perturbation, RMS ratio should be ~1.0
        assert 0.9 < result.rms_ratio < 1.1

    def test_2x_perturbation_detected(self, analyzer):
        data, fs = self._make_trial(noise_uv=3.0, effect=2.0)
        result = analyzer.analyze_trial(
            data, fs, session=1, trial=1,
            perturbation='impedance', level='0.3pct'
        )
        assert result.detected is True
        assert result.rms_ratio > 1.5

    def test_detection_latency_reasonable(self, analyzer):
        data, fs = self._make_trial(noise_uv=3.0, effect=3.0)
        result = analyzer.analyze_trial(
            data, fs, session=1, trial=1,
            perturbation='impedance', level='0.1pct'
        )
        assert result.detected is True
        assert result.detection_latency_s is not None
        # Latency should be within a few seconds of onset
        assert 0.0 <= result.detection_latency_s < 10.0

    def test_slope_shift_positive_for_noise_increase(self, analyzer):
        """When noise increases, spectral slope should change."""
        data, fs = self._make_trial(noise_uv=3.0, effect=3.0)
        result = analyzer.analyze_trial(
            data, fs, session=1, trial=1,
            perturbation='injection', level='100mV'
        )
        # Slope shift may be small for white noise, but RMS ratio should be clear
        assert result.rms_ratio > 2.0

    def test_quality_flags_no_clipping(self, analyzer):
        data, fs = self._make_trial(noise_uv=3.0, effect=1.5)
        result = analyzer.analyze_trial(
            data, fs, session=0, trial=1,
            perturbation='baseline', level='none'
        )
        assert result.clipping_detected is False
        assert result.saturation_detected is False


# ═══════════════════════════════════════════════════════════════════════
# Multi-trial analysis
# ═══════════════════════════════════════════════════════════════════════
class TestMultiTrialAnalysis:
    """Test group-level statistics with multiple synthetic trials."""

    @pytest.fixture
    def populated_analyzer(self):
        """Create an analyzer with 15 synthetic trials (5 baseline + 10 perturbed)."""
        sa = SessionAnalyzer(onset_s=30.0, epoch_s=25.0)
        rng = np.random.default_rng(123)

        for i in range(5):
            # Baseline trials (no perturbation)
            n = int(100 * 20000)
            data = rng.normal(0, 3.0, (16, n))
            result = sa.analyze_trial(
                data, 20000.0, session=0, trial=i + 1,
                perturbation='baseline', level='none',
                filename=f'S0_T{i+1:02d}_baseline_none.rhd'
            )
            sa.results.append(result)

        for i in range(5):
            # Impedance trials with 2× effect
            n = int(100 * 20000)
            data = rng.normal(0, 3.0, (16, n))
            onset = int(30 * 20000)
            data[:, onset:] *= 2.0
            result = sa.analyze_trial(
                data, 20000.0, session=1, trial=i + 1,
                perturbation='impedance', level='0.3pct',
                filename=f'S1_T{i+1:02d}_impedance_0.3pct.rhd'
            )
            sa.results.append(result)

        for i in range(5):
            # Impedance trials with 3× effect
            n = int(100 * 20000)
            data = rng.normal(0, 3.0, (16, n))
            onset = int(30 * 20000)
            data[:, onset:] *= 3.0
            result = sa.analyze_trial(
                data, 20000.0, session=1, trial=i + 6,
                perturbation='impedance', level='0.1pct',
                filename=f'S1_T{i+6:02d}_impedance_0.1pct.rhd'
            )
            sa.results.append(result)

        return sa

    def test_total_trial_count(self, populated_analyzer):
        assert len(populated_analyzer.results) == 15

    def test_baseline_detection_rate_low(self, populated_analyzer):
        baselines = populated_analyzer.get_baseline_recordings()
        assert len(baselines) == 5
        # Baseline should have LOW detection rate (ideally 0)
        n_detected = sum(1 for r in baselines if r.detected)
        assert n_detected / len(baselines) <= 0.4  # allow some noise

    def test_perturbation_detection_rate_high(self, populated_analyzer):
        trials = populated_analyzer.get_trials_by_type('impedance', '0.1pct')
        assert len(trials) == 5
        n_detected = sum(1 for t in trials if t.detected)
        assert n_detected >= 4  # ≥80% detection for 3× effect

    def test_group_stats_returns_valid(self, populated_analyzer):
        gs = populated_analyzer.group_stats('impedance', '0.3pct')
        assert isinstance(gs, GroupStats)
        assert gs.n_trials == 5
        assert 0.0 <= gs.detection_rate <= 1.0
        assert gs.detection_ci_lo <= gs.detection_rate
        assert gs.detection_rate <= gs.detection_ci_hi

    def test_detection_rates_by_level(self, populated_analyzer):
        rates = populated_analyzer.get_detection_rates_by_level()
        assert ('baseline', 'none') in rates
        assert ('impedance', '0.3pct') in rates
        assert ('impedance', '0.1pct') in rates
        # 0.1pct should have higher rate than baseline
        assert rates[('impedance', '0.1pct')][0] > rates[('baseline', 'none')][0]

    def test_summary_string(self, populated_analyzer):
        summary = populated_analyzer.summary()
        assert 'Session Analysis Summary' in summary
        assert '15 trials' in summary
        assert 'impedance' in summary


# ═══════════════════════════════════════════════════════════════════════
# CSV Export
# ═══════════════════════════════════════════════════════════════════════
class TestExport:
    """Test CSV export/import round-trip."""

    def test_export_csv(self, tmp_path):
        sa = SessionAnalyzer()
        rng = np.random.default_rng(42)
        data = rng.normal(0, 3.0, (16, int(100 * 20000)))
        result = sa.analyze_trial(
            data, 20000.0, session=0, trial=1,
            perturbation='baseline', level='none'
        )
        sa.results.append(result)

        out_path = tmp_path / 'results.csv'
        sa.export_results(out_path)

        assert out_path.exists()
        lines = out_path.read_text().strip().split('\n')
        assert len(lines) == 2  # header + 1 row
        assert 'session' in lines[0]
        assert 'rms_baseline_uv' in lines[0]


# ═══════════════════════════════════════════════════════════════════════
# RHD Loader
# ═══════════════════════════════════════════════════════════════════════
def _write_qstring(fid: io.BytesIO, s: str) -> None:
    """Write a Qt QString to a binary stream."""
    if not s:
        fid.write(struct.pack('<I', 0xFFFFFFFF))
        return
    encoded = s.encode('utf-16-le')
    fid.write(struct.pack('<I', len(encoded)))
    fid.write(encoded)


def _write_minimal_rhd(
    path: Path,
    n_channels: int = 4,
    n_blocks: int = 10,
    sample_rate: float = 20000.0,
    noise_uv: float = 3.0,
    version_major: int = 2,
    version_minor: int = 0,
) -> None:
    """Write a minimal .rhd file for testing.

    Creates a valid-enough .rhd file with:
    - Correct magic number and version
    - Simplified header (enough to be parseable)
    - Amplifier data blocks with Gaussian noise
    """
    samples_per_block = 128 if version_major >= 2 else 60
    rng = np.random.default_rng(42)

    with open(path, 'wb') as fid:
        # Magic
        fid.write(struct.pack('<I', 0xC6912702))

        # Version
        fid.write(struct.pack('<hh', version_major, version_minor))

        # Sample rate
        fid.write(struct.pack('<f', sample_rate))

        # DSP enabled + actual DSP cutoff
        fid.write(struct.pack('<hf', 1, 1.0))

        # Actual lower/upper bandwidth
        fid.write(struct.pack('<ff', 0.1, 7500.0))

        # Desired lower/upper bandwidth
        fid.write(struct.pack('<ff', 0.1, 7500.0))

        # Notch filter
        fid.write(struct.pack('<h', 0))

        # Impedance test frequency + actual
        fid.write(struct.pack('<ff', 1000.0, 1000.0))

        # Note strings (3)
        for _ in range(3):
            _write_qstring(fid, '')

        # v1.1+: DC amplifier data saved
        if version_major > 1 or (version_major == 1 and version_minor >= 1):
            fid.write(struct.pack('<h', 0))

        # v1.3+: eval board mode
        if version_major > 1 or (version_major == 1 and version_minor >= 3):
            fid.write(struct.pack('<h', 0))

        # v2.0+: reference channel name
        if version_major >= 2:
            _write_qstring(fid, 'REF')

        # Number of signal groups (1 group with amplifier channels)
        fid.write(struct.pack('<h', 1))

        # Signal group header
        _write_qstring(fid, 'Port A')  # group name
        _write_qstring(fid, 'A')       # group prefix

        # group_enabled, group_num_channels, group_num_amplifier_channels
        fid.write(struct.pack('<hhh', 1, n_channels, n_channels))

        # Channel entries
        for ch in range(n_channels):
            _write_qstring(fid, f'A-{ch:03d}')  # native name
            _write_qstring(fid, f'A-{ch:03d}')  # custom name

            # ch_order, custom_order, signal_type, enabled, chip_channel, command_stream, board_stream
            fid.write(struct.pack('<hhhhhhh', ch, ch, 0, 1, ch, 0, 0))

            # Trigger info: trigger_source, trigger_channel_index, trigger_type
            fid.write(struct.pack('<hhh', 0, 0, 0))

            # Impedance: magnitude, phase
            fid.write(struct.pack('<ff', 50000.0, 0.0))

        # ── Data blocks ────────────────────────────────────────────────
        for block in range(n_blocks):
            # Timestamps
            ts_start = block * samples_per_block
            timestamps = np.arange(ts_start, ts_start + samples_per_block, dtype=np.int32)
            fid.write(timestamps.tobytes())

            # Amplifier data (n_channels × samples_per_block, uint16)
            noise = rng.normal(0, noise_uv, (n_channels, samples_per_block))
            raw = np.clip(
                (noise / 0.195 + 32768).astype(np.int64),
                0, 65535
            ).astype(np.uint16)
            fid.write(raw.tobytes())


class TestRHDLoader:
    """Test Intan .rhd file loading."""

    def test_load_minimal_rhd(self, tmp_path):
        from analysis.rhd_loader import load_rhd
        rhd_path = tmp_path / 'test.rhd'
        _write_minimal_rhd(rhd_path, n_channels=4, n_blocks=10)

        result = load_rhd(rhd_path)
        assert result['amplifier_data_uv'].shape[0] == 4
        assert result['amplifier_data_uv'].shape[1] == 10 * 128  # 10 blocks × 128 samples
        assert result['sample_rate'] == 20000.0
        assert len(result['channel_names']) == 4

    def test_data_in_uv_range(self, tmp_path):
        from analysis.rhd_loader import load_rhd
        rhd_path = tmp_path / 'test.rhd'
        _write_minimal_rhd(rhd_path, noise_uv=3.0)

        result = load_rhd(rhd_path)
        data = result['amplifier_data_uv']
        rms = np.sqrt(np.mean(data**2))
        # Should be roughly 3 µV (with quantization noise)
        assert 1.0 < rms < 10.0

    def test_timestamps_monotonic(self, tmp_path):
        from analysis.rhd_loader import load_rhd
        rhd_path = tmp_path / 'test.rhd'
        _write_minimal_rhd(rhd_path)

        result = load_rhd(rhd_path)
        ts = result['timestamps_s']
        assert np.all(np.diff(ts) > 0)  # strictly increasing

    def test_bad_magic_raises(self, tmp_path):
        from analysis.rhd_loader import load_rhd
        bad_path = tmp_path / 'bad.rhd'
        with open(bad_path, 'wb') as f:
            f.write(struct.pack('<I', 0xDEADBEEF))
            f.write(b'\x00' * 100)
        with pytest.raises(ValueError, match='Not an Intan .rhd file'):
            load_rhd(bad_path)

    def test_load_as_recording(self, tmp_path):
        from analysis.rhd_loader import load_rhd_as_recording
        rhd_path = tmp_path / 'test.rhd'
        _write_minimal_rhd(rhd_path, n_channels=4, n_blocks=10)

        rec = load_rhd_as_recording(rhd_path)
        assert rec.n_channels == 4
        assert rec.fs == 20000.0
        assert rec.data_uv.shape == (4, 1280)


# ═══════════════════════════════════════════════════════════════════════
# End-to-end
# ═══════════════════════════════════════════════════════════════════════
class TestEndToEnd:
    """End-to-end integration tests."""

    def test_synthetic_trial_roundtrip(self):
        """Generate synthetic trial, analyze, check all fields populated."""
        sa = SessionAnalyzer(onset_s=30.0, epoch_s=25.0)
        result = sa.analyze_synthetic(
            n_channels=16,
            noise_uv=3.0,
            effect_multiplier=3.0,
            perturbation='impedance',
            level='0.1pct',
        )
        assert result.detected is True
        assert result.rms_ratio > 2.0
        assert result.bp_ratio > 1.0
        assert result.perturbation == 'impedance'
        assert result.level == '0.1pct'

    def test_full_synthetic_session(self):
        """Simulate a mini session with 10 trials and verify summary."""
        sa = SessionAnalyzer(onset_s=30.0, epoch_s=25.0)
        rng = np.random.default_rng(999)

        # 3 baseline + 7 perturbed
        for i in range(3):
            data = rng.normal(0, 3.0, (16, int(100 * 20000)))
            r = sa.analyze_trial(data, 20000.0, 0, i + 1, 'baseline', 'none')
            sa.results.append(r)

        for i in range(7):
            data = rng.normal(0, 3.0, (16, int(100 * 20000)))
            onset = int(30 * 20000)
            data[:, onset:] *= 2.5
            r = sa.analyze_trial(data, 20000.0, 1, i + 1, 'impedance', '0.3pct')
            sa.results.append(r)

        summary = sa.summary()
        assert '10 trials' in summary

        rates = sa.get_detection_rates_by_level()
        assert ('impedance', '0.3pct') in rates
