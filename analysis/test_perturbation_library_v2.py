"""
Tests for v2 additions to perturbation_library.py:

  - generate_partial_suppression: holdout perturbation type
  - HOLDOUT_TYPES list
"""

import numpy as np
import pytest

from perturbation_library import (
    generate_partial_suppression,
    HOLDOUT_TYPES,
    GENERATORS,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
    LabeledWindow,
    generate_dataset,
)


# ── Shared params ────────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DUR = 5.0
ONSET = 2.0
SEED = 42
N_SAMPLES = int(DUR * FS)

COMMON = dict(seed=SEED, n_channels=N_CH, fs=FS, duration_s=DUR, onset_s=ONSET)


# ═══════════════════════════════════════════════════════════════════
#  Holdout generator
# ═══════════════════════════════════════════════════════════════════

class TestPartialSuppression:
    def test_shape(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert w.data_uv.shape == (N_CH, N_SAMPLES)

    def test_metadata_category(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert w.meta.category == "neurotox"

    def test_metadata_type(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert w.meta.perturbation_type == "partial_suppression_holdout"

    def test_metadata_severity(self):
        w = generate_partial_suppression(severity=0.7, **COMMON)
        assert w.meta.severity == 0.7

    def test_params_populated(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert "suppression_frac" in w.meta.params
        assert "ramp_uv_per_s" in w.meta.params
        assert "n_drift_channels" in w.meta.params

    def test_no_nans(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert not np.any(np.isnan(w.data_uv))

    def test_float64(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert w.data_uv.dtype == np.float64

    def test_reproducibility(self):
        w1 = generate_partial_suppression(severity=0.5, **COMMON)
        w2 = generate_partial_suppression(severity=0.5, **COMMON)
        np.testing.assert_array_equal(w1.data_uv, w2.data_uv)

    def test_severity_range(self):
        """Higher severity should produce different output than lower."""
        w_lo = generate_partial_suppression(severity=0.1, **COMMON)
        w_hi = generate_partial_suppression(severity=0.9, **COMMON)
        # They should be different
        assert not np.array_equal(w_lo.data_uv, w_hi.data_uv)

    def test_isinstance_labeled_window(self):
        w = generate_partial_suppression(severity=0.5, **COMMON)
        assert isinstance(w, LabeledWindow)


# ═══════════════════════════════════════════════════════════════════
#  HOLDOUT_TYPES registry
# ═══════════════════════════════════════════════════════════════════

class TestHoldoutTypes:
    def test_list_populated(self):
        assert len(HOLDOUT_TYPES) > 0

    def test_contains_partial_suppression(self):
        assert "partial_suppression_holdout" in HOLDOUT_TYPES

    def test_not_in_training_generators(self):
        """Holdout types should NOT be in the main GENERATORS registry."""
        for ht in HOLDOUT_TYPES:
            assert ht not in GENERATORS, f"{ht} should not be in GENERATORS"

    def test_not_in_artifact_or_neurotox_types(self):
        """Holdout types should NOT be in training type lists."""
        for ht in HOLDOUT_TYPES:
            assert ht not in ARTIFACT_TYPES
            assert ht not in NEUROTOX_TYPES

    def test_not_in_dataset(self):
        """generate_dataset() should NOT produce holdout types."""
        ds = generate_dataset(
            n_per_class=1, severities=[0.5],
            n_channels=N_CH, fs=FS,
            duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        types = {w.meta.perturbation_type for w in ds}
        for ht in HOLDOUT_TYPES:
            assert ht not in types, f"{ht} should not appear in training dataset"
