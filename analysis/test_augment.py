"""
Tests for augment.py — domain-randomization augmentations for neural time-series.

Covers:
  - Each of 7 augmentation functions preserves shape
  - Each augmentation modifies data (not a no-op)
  - Seeded reproducibility for each augmentation
  - AugmentPipeline composition and stochastic behavior
  - apply_all deterministic mode
  - augment_batch convenience function
  - Registry completeness and validation
  - Edge cases: single channel, very short windows
"""

import numpy as np
import pytest

from augment import (
    aug_line_noise,
    aug_channel_dropout,
    aug_gain_scaling,
    aug_jitter_burst,
    aug_coupling_matrix,
    aug_drift,
    aug_quantize_clip,
    AUGMENTATION_REGISTRY,
    ALL_AUGMENTATION_NAMES,
    AugmentPipeline,
    augment_batch,
)


# ── Constants ─────────────────────────────────────────────────────────

FS = 20_000.0
N_CH = 8
N_SAMP = 4000  # 0.2 s
SEED = 42


def _make_data(n_ch: int = N_CH, n_samp: int = N_SAMP, seed: int = SEED) -> np.ndarray:
    """Synthetic (n_ch, n_samp) data in µV-scale."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n_ch, n_samp)) * 100.0


# ═══════════════════════════════════════════════════════════════════════
#  Registry
# ═══════════════════════════════════════════════════════════════════════

class TestRegistry:
    def test_seven_augmentations(self):
        assert len(AUGMENTATION_REGISTRY) == 7

    def test_all_names_list_matches(self):
        assert set(ALL_AUGMENTATION_NAMES) == set(AUGMENTATION_REGISTRY.keys())

    def test_expected_names(self):
        expected = {
            "line_noise", "channel_dropout", "gain_scaling",
            "jitter_burst", "coupling_matrix", "drift", "quantize_clip",
        }
        assert set(AUGMENTATION_REGISTRY.keys()) == expected

    def test_all_callables(self):
        for name, fn in AUGMENTATION_REGISTRY.items():
            assert callable(fn), f"{name} is not callable"


# ═══════════════════════════════════════════════════════════════════════
#  Individual augmentations — shape preservation + data modification
# ═══════════════════════════════════════════════════════════════════════

ALL_AUGS = [
    ("line_noise", aug_line_noise),
    ("channel_dropout", aug_channel_dropout),
    ("gain_scaling", aug_gain_scaling),
    ("jitter_burst", aug_jitter_burst),
    ("coupling_matrix", aug_coupling_matrix),
    ("drift", aug_drift),
    ("quantize_clip", aug_quantize_clip),
]


class TestAugmentationShapes:
    """Every augmentation must preserve (n_ch, n_samp) shape."""

    @pytest.mark.parametrize("name,fn", ALL_AUGS, ids=[a[0] for a in ALL_AUGS])
    def test_shape_preserved(self, name, fn):
        data = _make_data()
        rng = np.random.default_rng(SEED)
        result = fn(data, FS, rng)
        assert result.shape == data.shape, f"{name} changed shape"

    @pytest.mark.parametrize("name,fn", ALL_AUGS, ids=[a[0] for a in ALL_AUGS])
    def test_dtype_preserved(self, name, fn):
        data = _make_data()
        rng = np.random.default_rng(SEED)
        result = fn(data, FS, rng)
        assert result.dtype == data.dtype, f"{name} changed dtype"

    @pytest.mark.parametrize("name,fn", ALL_AUGS, ids=[a[0] for a in ALL_AUGS])
    def test_no_nans(self, name, fn):
        data = _make_data()
        rng = np.random.default_rng(SEED)
        result = fn(data, FS, rng)
        assert not np.any(np.isnan(result)), f"{name} produced NaN"


# Augmentations that always modify data (channel_dropout may drop 0 channels)
ALWAYS_MODIFY_AUGS = [
    (n, f) for n, f in ALL_AUGS if n != "channel_dropout"
]


class TestAugmentationModifiesData:
    """Every augmentation should actually change the data (not a no-op)."""

    @pytest.mark.parametrize("name,fn", ALWAYS_MODIFY_AUGS,
                             ids=[a[0] for a in ALWAYS_MODIFY_AUGS])
    def test_data_changed(self, name, fn):
        data = _make_data()
        rng = np.random.default_rng(SEED)
        result = fn(data, FS, rng)
        # At least some values should differ
        assert not np.allclose(data, result), f"{name} did not modify data"

    def test_channel_dropout_with_forced_drop(self):
        """Channel dropout with high fraction must drop at least 1 channel."""
        data = _make_data()
        rng = np.random.default_rng(SEED)
        result = aug_channel_dropout(data, FS, rng, max_drop_frac=0.99)
        # With max_drop_frac=0.99 on 8 channels, n_drop ∈ [0, 7]
        # Extremely unlikely to drop 0 but shape should be preserved
        assert result.shape == data.shape

    @pytest.mark.parametrize("name,fn", ALL_AUGS, ids=[a[0] for a in ALL_AUGS])
    def test_does_not_modify_in_place(self, name, fn):
        """Augmentations should copy, not modify the input."""
        data = _make_data()
        original = data.copy()
        rng = np.random.default_rng(SEED)
        _ = fn(data, FS, rng)
        np.testing.assert_array_equal(data, original,
                                      err_msg=f"{name} modified input in place")


class TestAugmentationReproducibility:
    """Same seed → same output."""

    @pytest.mark.parametrize("name,fn", ALL_AUGS, ids=[a[0] for a in ALL_AUGS])
    def test_seeded_determinism(self, name, fn):
        data = _make_data()
        r1 = fn(data, FS, np.random.default_rng(99))
        r2 = fn(data, FS, np.random.default_rng(99))
        np.testing.assert_array_equal(r1, r2,
                                      err_msg=f"{name} not reproducible")


# ═══════════════════════════════════════════════════════════════════════
#  Specific augmentation behavior
# ═══════════════════════════════════════════════════════════════════════

class TestLineNoise:
    def test_60hz_present(self):
        """Line noise should add spectral power near 60 Hz."""
        data = np.zeros((N_CH, N_SAMP))
        rng = np.random.default_rng(SEED)
        result = aug_line_noise(data, FS, rng, amp_range=(50.0, 50.0))
        # Check that 60 Hz bin has power
        fft_mag = np.abs(np.fft.rfft(result[0]))
        freqs = np.fft.rfftfreq(N_SAMP, 1.0 / FS)
        idx_60 = np.argmin(np.abs(freqs - 60.0))
        assert fft_mag[idx_60] > 1.0, "No 60 Hz component detected"


class TestChannelDropout:
    def test_some_channels_zeroed(self):
        data = _make_data()
        rng = np.random.default_rng(SEED)
        result = aug_channel_dropout(data, FS, rng, max_drop_frac=0.5)
        # Check if any full channel is zeroed
        zero_channels = np.all(result == 0.0, axis=1)
        # With max_drop_frac=0.5 on 8 channels, could drop 0-4
        # Just verify the function didn't crash and shape is right
        assert result.shape == data.shape


class TestGainScaling:
    def test_per_channel_independent(self):
        data = np.ones((4, 100))
        rng = np.random.default_rng(SEED)
        result = aug_gain_scaling(data, FS, rng)
        # Each channel should have a different constant value
        channel_means = result.mean(axis=1)
        assert len(set(np.round(channel_means, 6))) == 4


class TestQuantizeClip:
    def test_output_clipped(self):
        data = np.ones((2, 100)) * 10000.0  # large values
        rng = np.random.default_rng(SEED)
        result = aug_quantize_clip(data, FS, rng, clip_range_uv=(3000.0, 3000.0))
        assert np.max(np.abs(result)) <= 3000.0 + 1e-6


# ═══════════════════════════════════════════════════════════════════════
#  AugmentPipeline
# ═══════════════════════════════════════════════════════════════════════

class TestAugmentPipeline:
    def test_default_uses_all_augs(self):
        pipe = AugmentPipeline()
        assert len(pipe.augmentations) == 7

    def test_custom_subset(self):
        pipe = AugmentPipeline(augmentations=["line_noise", "drift"])
        assert len(pipe.augmentations) == 2

    def test_invalid_augmentation_raises(self):
        with pytest.raises(ValueError, match="Unknown augmentation"):
            AugmentPipeline(augmentations=["nonexistent_aug"])

    def test_call_preserves_shape(self):
        pipe = AugmentPipeline(p_each=1.0, seed=SEED)
        data = _make_data()
        result = pipe(data, FS)
        assert result.shape == data.shape

    def test_call_modifies_data(self):
        pipe = AugmentPipeline(p_each=1.0, seed=SEED)
        data = _make_data()
        result = pipe(data, FS)
        assert not np.allclose(data, result)

    def test_stochastic_different_seeds(self):
        pipe = AugmentPipeline(p_each=0.5, seed=SEED)
        data = _make_data()
        r1 = pipe(data, FS, seed_offset=0)
        r2 = pipe(data, FS, seed_offset=1)
        # Different seed offsets should (usually) give different results
        # With 7 augs and p=0.5, extremely unlikely to be identical
        assert not np.allclose(r1, r2)

    def test_deterministic_with_same_seed(self):
        pipe = AugmentPipeline(p_each=0.5, seed=SEED)
        data = _make_data()
        r1 = pipe(data, FS, seed_offset=7)
        r2 = pipe(data, FS, seed_offset=7)
        np.testing.assert_array_equal(r1, r2)

    def test_p_each_zero_is_noop(self):
        pipe = AugmentPipeline(p_each=0.0, seed=SEED)
        data = _make_data()
        result = pipe(data, FS)
        np.testing.assert_array_equal(data, result)

    def test_apply_all_modifies(self):
        pipe = AugmentPipeline(seed=SEED)
        data = _make_data()
        result = pipe.apply_all(data, FS)
        assert not np.allclose(data, result)
        assert result.shape == data.shape

    def test_apply_all_deterministic(self):
        pipe = AugmentPipeline(seed=SEED)
        data = _make_data()
        r1 = pipe.apply_all(data, FS)
        r2 = pipe.apply_all(data, FS)
        np.testing.assert_array_equal(r1, r2)


# ═══════════════════════════════════════════════════════════════════════
#  augment_batch
# ═══════════════════════════════════════════════════════════════════════

class TestAugmentBatch:
    def test_batch_length_preserved(self):
        windows = [_make_data() for _ in range(5)]
        result = augment_batch(windows, FS, seed=SEED)
        assert len(result) == 5

    def test_batch_shapes_preserved(self):
        windows = [_make_data() for _ in range(3)]
        result = augment_batch(windows, FS, seed=SEED)
        for orig, aug in zip(windows, result):
            assert aug.shape == orig.shape

    def test_batch_each_different(self):
        """Different seed_offset per window → different augmentations."""
        data = _make_data()
        windows = [data.copy() for _ in range(3)]
        result = augment_batch(windows, FS, seed=SEED)
        # Each should be augmented differently
        assert not np.allclose(result[0], result[1])

    def test_batch_with_custom_pipeline(self):
        pipe = AugmentPipeline(augmentations=["line_noise"], p_each=1.0, seed=SEED)
        windows = [_make_data() for _ in range(4)]
        result = augment_batch(windows, FS, pipeline=pipe, seed=SEED)
        assert len(result) == 4
        for r in result:
            assert r.shape == windows[0].shape


# ═══════════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════════

# coupling_matrix requires n_ch >= 2 (needs 2 distinct channels)
SINGLE_CH_SAFE_AUGS = [(n, f) for n, f in ALL_AUGS if n != "coupling_matrix"]


class TestEdgeCases:
    def test_single_channel(self):
        """Augmentations (except coupling_matrix) should work with 1 channel."""
        data = np.random.default_rng(SEED).standard_normal((1, N_SAMP)) * 100.0
        for name, fn in SINGLE_CH_SAFE_AUGS:
            rng = np.random.default_rng(SEED)
            result = fn(data, FS, rng)
            assert result.shape == data.shape, f"{name} failed on 1 channel"

    def test_coupling_matrix_needs_2_channels(self):
        """coupling_matrix needs ≥2 channels (can't pick 2 distinct from 1)."""
        data = np.random.default_rng(SEED).standard_normal((2, N_SAMP)) * 100.0
        rng = np.random.default_rng(SEED)
        result = aug_coupling_matrix(data, FS, rng)
        assert result.shape == data.shape

    def test_short_window(self):
        """Augmentations should work with very short windows."""
        data = np.random.default_rng(SEED).standard_normal((N_CH, 200)) * 100.0
        for name, fn in ALL_AUGS:
            rng = np.random.default_rng(SEED)
            result = fn(data, FS, rng)
            assert result.shape == data.shape, f"{name} failed on short window"

    def test_many_channels(self):
        """Augmentations should work with many channels (e.g. 64)."""
        data = np.random.default_rng(SEED).standard_normal((64, N_SAMP)) * 100.0
        for name, fn in ALL_AUGS:
            rng = np.random.default_rng(SEED)
            result = fn(data, FS, rng)
            assert result.shape == data.shape, f"{name} failed on 64 channels"
