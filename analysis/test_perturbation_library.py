"""
Tests for perturbation_library.py — labeled perturbation generator.

Covers:
  - Each generator produces correct shape + metadata
  - Severity interpolation works across [0,1]
  - Dataset balance (all types represented)
  - Baseline vs perturbation distinguishability
  - Edge cases (0/1 severity, minimal channels)
"""

import numpy as np
import pytest

from perturbation_library import (
    PerturbationMeta,
    LabeledWindow,
    SEVERITY_RANGES,
    GENERATORS,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
    generate_baseline,
    generate_impedance_drift,
    generate_broadband_noise,
    generate_line_interference,
    generate_crosstalk,
    generate_spike_suppression,
    generate_burst_collapse,
    generate_spectral_shift,
    generate_mixed_neurotox,
    generate_dataset,
    dataset_summary,
    _lerp,
    _lerp_int,
    DEFAULT_FS,
    DEFAULT_N_CHANNELS,
    DEFAULT_DURATION_S,
    DEFAULT_ONSET_S,
)


# ── Shared fixtures ──────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4       # small for fast tests
DUR = 5.0
ONSET = 2.0
SEED = 42
N_SAMPLES = int(DUR * FS)

COMMON = dict(seed=SEED, n_channels=N_CH, fs=FS, duration_s=DUR)
PERT_COMMON = dict(**COMMON, onset_s=ONSET)


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

class TestLerp:
    def test_lerp_low(self):
        assert _lerp(1.0, 5.0, 0.0) == 1.0

    def test_lerp_high(self):
        assert _lerp(1.0, 5.0, 1.0) == 5.0

    def test_lerp_mid(self):
        assert abs(_lerp(1.0, 5.0, 0.5) - 3.0) < 1e-10

    def test_lerp_clamp(self):
        assert _lerp(1.0, 5.0, 1.5) == 5.0
        assert _lerp(1.0, 5.0, -0.5) == 1.0

    def test_lerp_int(self):
        assert _lerp_int(1, 4, 0.5) == 2 or _lerp_int(1, 4, 0.5) == 3


# ═══════════════════════════════════════════════════════════════════
#  Individual generators — shape + metadata correctness
# ═══════════════════════════════════════════════════════════════════

class TestBaseline:
    def test_shape(self):
        w = generate_baseline(**COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)

    def test_metadata(self):
        w = generate_baseline(**COMMON)
        assert w.meta.category == "baseline"
        assert w.meta.perturbation_type == "baseline_stable"
        assert w.meta.severity == 0.0

    def test_onset_is_past_end(self):
        """Baseline onset should indicate 'no perturbation'."""
        w = generate_baseline(**COMMON)
        assert w.meta.onset_s >= DUR

    def test_float64(self):
        w = generate_baseline(**COMMON)
        assert w.data_uv.dtype == np.float64

    def test_no_nans(self):
        w = generate_baseline(**COMMON)
        assert not np.any(np.isnan(w.data_uv))


class TestImpedanceDrift:
    def test_shape(self):
        w = generate_impedance_drift(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)

    def test_metadata(self):
        w = generate_impedance_drift(severity=0.7, **PERT_COMMON)
        assert w.meta.category == "artifact"
        assert w.meta.perturbation_type == "impedance_drift"
        assert w.meta.severity == 0.7

    def test_post_onset_differs(self):
        w = generate_impedance_drift(severity=0.9, **PERT_COMMON)
        onset_idx = int(ONSET * FS)
        pre = w.data_uv[:, :onset_idx]
        post = w.data_uv[:, onset_idx:]
        # Post-onset mean should be different from pre for at least one channel
        pre_means = np.mean(pre, axis=1)
        post_means = np.mean(post, axis=1)
        assert np.any(np.abs(post_means - pre_means) > 0.1)


class TestBroadbandNoise:
    def test_shape_and_meta(self):
        w = generate_broadband_noise(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "artifact"
        assert w.meta.perturbation_type == "broadband_noise"

    def test_noise_increases_post_onset(self):
        w = generate_broadband_noise(severity=0.9, **PERT_COMMON)
        onset_idx = int(ONSET * FS)
        pre_rms = np.std(w.data_uv[:, :onset_idx])
        post_rms = np.std(w.data_uv[:, onset_idx:])
        assert post_rms > pre_rms * 1.1, "Post-onset noise should increase"


class TestLineInterference:
    def test_shape_and_meta(self):
        w = generate_line_interference(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "artifact"
        assert w.meta.perturbation_type == "line_interference"

    def test_60hz_present_post_onset(self):
        w = generate_line_interference(severity=0.8, **PERT_COMMON)
        onset_idx = int(ONSET * FS)
        post = w.data_uv[0, onset_idx:]
        # FFT to check 60 Hz peak
        freqs = np.fft.rfftfreq(len(post), d=1 / FS)
        power = np.abs(np.fft.rfft(post)) ** 2
        idx_60 = np.argmin(np.abs(freqs - 60.0))
        # 60 Hz bin should be much larger than nearby bins
        neighbors = power[max(0, idx_60 - 5): idx_60 - 1]
        assert power[idx_60] > np.mean(neighbors) * 5


class TestCrosstalk:
    def test_shape_and_meta(self):
        w = generate_crosstalk(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "artifact"
        assert w.meta.perturbation_type == "crosstalk_coupling"


class TestSpikeSuppression:
    def test_shape_and_meta(self):
        w = generate_spike_suppression(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "neurotox"
        assert w.meta.perturbation_type == "spike_suppression"

    def test_rms_preserved(self):
        """Spike suppression should NOT increase noise floor."""
        w = generate_spike_suppression(severity=0.7, **PERT_COMMON)
        onset_idx = int(ONSET * FS)
        pre_rms = np.std(w.data_uv[:, :onset_idx])
        post_rms = np.std(w.data_uv[:, onset_idx:])
        # RMS should stay roughly the same (≤20% increase is tolerable)
        assert post_rms < pre_rms * 1.2, \
            "Spike suppression should preserve noise floor"


class TestBurstCollapse:
    def test_shape_and_meta(self):
        w = generate_burst_collapse(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "neurotox"
        assert w.meta.perturbation_type == "burst_collapse"


class TestSpectralShift:
    def test_shape_and_meta(self):
        w = generate_spectral_shift(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "neurotox"
        assert w.meta.perturbation_type == "spectral_shift"


class TestMixedNeurotox:
    def test_shape_and_meta(self):
        w = generate_mixed_neurotox(severity=0.5, **PERT_COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)
        assert w.meta.category == "neurotox"
        assert w.meta.perturbation_type == "mixed_neurotox"

    def test_params_populated(self):
        w = generate_mixed_neurotox(severity=0.5, **PERT_COMMON)
        assert "suppression_frac" in w.meta.params
        assert "dropout_prob" in w.meta.params
        assert "noise_increase_uv" in w.meta.params


# ═══════════════════════════════════════════════════════════════════
#  Severity parametrization
# ═══════════════════════════════════════════════════════════════════

class TestSeverityGradient:
    """Higher severity → stronger effect (RMS or spike rate Δ)."""

    def test_broadband_noise_severity(self):
        rms_by_sev = []
        for sev in [0.1, 0.5, 0.9]:
            w = generate_broadband_noise(severity=sev, **PERT_COMMON)
            onset_idx = int(ONSET * FS)
            rms_by_sev.append(np.std(w.data_uv[:, onset_idx:]))
        # Strictly monotonic
        assert rms_by_sev[0] < rms_by_sev[1] < rms_by_sev[2]

    def test_line_interference_severity(self):
        power_by_sev = []
        for sev in [0.1, 0.5, 0.9]:
            w = generate_line_interference(severity=sev, **PERT_COMMON)
            onset_idx = int(ONSET * FS)
            post = w.data_uv[0, onset_idx:]
            freqs = np.fft.rfftfreq(len(post), d=1 / FS)
            psd = np.abs(np.fft.rfft(post)) ** 2
            idx_60 = np.argmin(np.abs(freqs - 60.0))
            power_by_sev.append(psd[idx_60])
        assert power_by_sev[0] < power_by_sev[2]


# ═══════════════════════════════════════════════════════════════════
#  Dataset generation + summary
# ═══════════════════════════════════════════════════════════════════

class TestDataset:
    def test_dataset_count(self):
        """n_per_class=2, 3 severities, 8 perturbation types + 1 baseline."""
        ds = generate_dataset(n_per_class=2, severities=[0.3, 0.6],
                              n_channels=N_CH, fs=FS,
                              duration_s=DUR, onset_s=ONSET, seed=SEED)
        # baseline: 2, others: 8 types × 2 severities × 2 = 32 → total 34
        assert len(ds) == 2 + 8 * 2 * 2

    def test_all_types_present(self):
        ds = generate_dataset(n_per_class=1, severities=[0.5],
                              n_channels=N_CH, fs=FS,
                              duration_s=DUR, onset_s=ONSET, seed=SEED)
        types_seen = {w.meta.perturbation_type for w in ds}
        expected = {"baseline_stable"} | set(ARTIFACT_TYPES) | set(NEUROTOX_TYPES)
        assert types_seen == expected

    def test_summary_counts(self):
        ds = generate_dataset(n_per_class=2, severities=[0.5],
                              n_channels=N_CH, fs=FS,
                              duration_s=DUR, onset_s=ONSET, seed=SEED)
        summary = dataset_summary(ds)
        assert summary["baseline/baseline_stable"] == 2
        assert summary["artifact/impedance_drift"] == 2
        assert summary["neurotox/spike_suppression"] == 2

    def test_reproducibility(self):
        ds1 = generate_dataset(n_per_class=1, severities=[0.5],
                               n_channels=N_CH, fs=FS,
                               duration_s=DUR, onset_s=ONSET, seed=99)
        ds2 = generate_dataset(n_per_class=1, severities=[0.5],
                               n_channels=N_CH, fs=FS,
                               duration_s=DUR, onset_s=ONSET, seed=99)
        for w1, w2 in zip(ds1, ds2):
            np.testing.assert_array_equal(w1.data_uv, w2.data_uv)


# ═══════════════════════════════════════════════════════════════════
#  Registry correctness
# ═══════════════════════════════════════════════════════════════════

class TestRegistry:
    def test_all_generators_registered(self):
        expected = {"baseline_stable"} | set(ARTIFACT_TYPES) | set(NEUROTOX_TYPES)
        assert set(GENERATORS.keys()) == expected

    def test_severity_ranges_cover_all_perturbations(self):
        for ptype in ARTIFACT_TYPES + NEUROTOX_TYPES:
            assert ptype in SEVERITY_RANGES, f"Missing severity range for {ptype}"

    def test_generators_callable_with_common_args(self):
        """Every registered generator accepts the common kwargs."""
        for name, fn in GENERATORS.items():
            if name == "baseline_stable":
                w = fn(seed=SEED, n_channels=N_CH, fs=FS, duration_s=DUR)
            else:
                w = fn(severity=0.5, seed=SEED, onset_s=ONSET,
                       n_channels=N_CH, fs=FS, duration_s=DUR)
            assert isinstance(w, LabeledWindow)
            assert w.data_uv.shape == (N_CH, N_SAMPLES)


# ═══════════════════════════════════════════════════════════════════
#  Frozen dataclass
# ═══════════════════════════════════════════════════════════════════

class TestPerturbationMeta:
    def test_frozen(self):
        m = PerturbationMeta(
            category="neurotox", perturbation_type="spike_suppression",
            severity=0.5, onset_s=10.0, duration_s=30.0, fs=20000.0,
            n_channels=16,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            m.severity = 0.9  # type: ignore[misc]
