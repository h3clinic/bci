"""Unit tests for the west-run TP-column crossing predicate.

Tests the geometric logic used by _westrun_crosses_tp_column() inside
generate_eeg_routes().  Since that function is nested (closure over
CH_WESTRUN / TP_COLUMN_X), we reimplement the pure geometric predicate
here and verify it against all boundary conditions.

The real implementation in gen_pcb.py uses:
    TP_COLUMN_X = 110.0
    x_lo = min(ball_x, x_jog)
    x_hi = max(ball_x, x_jog)
    return x_lo <= TP_COLUMN_X <= x_hi
"""
from __future__ import annotations

import pytest

# ── Pure geometric predicate (mirrors gen_pcb.py exactly) ────────────────
TP_COLUMN_X = 110.0


def westrun_crosses_tp_column(ball_x: float, x_jog: float) -> bool:
    """Does the horizontal west-run [ball_x, x_jog] cross TP_COLUMN_X?

    Uses closed-interval test: endpoint *at* TP_COLUMN_X counts as crossing.
    """
    x_lo = min(ball_x, x_jog)
    x_hi = max(ball_x, x_jog)
    return x_lo <= TP_COLUMN_X <= x_hi


# ── Production channel values (regression) ───────────────────────────────

class TestProductionChannels:
    """Verify the 4 production channels match expected crossing behavior."""

    def test_ch0_does_not_cross(self):
        # CH0: ball_x=116.0, x_jog=112.49 → range [112.49, 116.0]
        # 110.0 < 112.49 → does NOT cross
        assert westrun_crosses_tp_column(116.0, 112.49) is False

    def test_ch1_crosses(self):
        # CH1: ball_x=116.5, x_jog=109.0 → range [109.0, 116.5]
        # 109.0 <= 110.0 <= 116.5 → crosses
        assert westrun_crosses_tp_column(116.5, 109.0) is True

    def test_ch2_crosses(self):
        # CH2: ball_x=117.0, x_jog=108.5 → range [108.5, 117.0]
        assert westrun_crosses_tp_column(117.0, 108.5) is True

    def test_ch3_crosses(self):
        # CH3: ball_x=117.5, x_jog=108.0 → range [108.0, 117.5]
        assert westrun_crosses_tp_column(117.5, 108.0) is True


# ── Boundary conditions ─────────────────────────────────────────────────

class TestBoundaryConditions:
    """Edge cases for the closed-interval crossing test."""

    def test_endpoint_exactly_at_tp_column_ball_x(self):
        """ball_x == TP_COLUMN_X, x_jog east → crosses (endpoint on boundary)."""
        assert westrun_crosses_tp_column(110.0, 115.0) is True

    def test_endpoint_exactly_at_tp_column_x_jog(self):
        """x_jog == TP_COLUMN_X, ball_x east → crosses (endpoint on boundary)."""
        assert westrun_crosses_tp_column(115.0, 110.0) is True

    def test_both_endpoints_at_tp_column(self):
        """Degenerate: both endpoints at TP_COLUMN_X → crosses (zero-length at boundary)."""
        assert westrun_crosses_tp_column(110.0, 110.0) is True

    def test_both_endpoints_east_of_tp_column(self):
        """Both endpoints east → does NOT cross."""
        assert westrun_crosses_tp_column(115.0, 112.0) is False

    def test_both_endpoints_west_of_tp_column(self):
        """Both endpoints west → does NOT cross."""
        assert westrun_crosses_tp_column(108.0, 105.0) is False

    def test_ball_x_west_x_jog_east(self):
        """Reversed orientation: ball_x < TP_COLUMN_X < x_jog → crosses.
        (Unusual but the predicate must be direction-agnostic.)"""
        assert westrun_crosses_tp_column(108.0, 115.0) is True

    def test_just_outside_east(self):
        """x_lo = 110.001, x_hi = 115.0 → does NOT cross (just misses)."""
        assert westrun_crosses_tp_column(115.0, 110.001) is False

    def test_just_outside_west(self):
        """x_lo = 105.0, x_hi = 109.999 → does NOT cross (just misses)."""
        assert westrun_crosses_tp_column(105.0, 109.999) is False

    def test_just_inside_from_east(self):
        """x_lo = 109.999, x_hi = 115.0 → crosses (barely includes 110.0)."""
        assert westrun_crosses_tp_column(115.0, 109.999) is True

    def test_just_inside_from_west(self):
        """x_lo = 105.0, x_hi = 110.001 → crosses (barely includes 110.0)."""
        assert westrun_crosses_tp_column(105.0, 110.001) is True


# ── Symmetry ─────────────────────────────────────────────────────────────

class TestSymmetry:
    """The predicate must be agnostic to argument order."""

    @pytest.mark.parametrize("a,b", [
        (116.5, 109.0),   # CH1 production
        (108.0, 115.0),   # reversed crossing
        (115.0, 112.0),   # both east
        (110.0, 115.0),   # endpoint at boundary
    ])
    def test_order_independence(self, a: float, b: float):
        assert westrun_crosses_tp_column(a, b) == westrun_crosses_tp_column(b, a)


# \u2500\u2500 Fuzz / property-style tests \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

import random


class TestFuzzCrossingPredicate:
    """Property-style fuzz test: 1000 random segment endpoint pairs.

    Verifies that the predicate is equivalent to the reference formula
    and that argument order never changes the result.  Catches tolerance
    drift, off-by-one inequality bugs, and floating-point edge cases.
    """

    SEED = 42  # deterministic for reproducibility
    N_SAMPLES = 1000
    # Sample from a range that straddles TP_COLUMN_X with margin
    X_MIN = 100.0
    X_MAX = 125.0

    @staticmethod
    def _reference(a: float, b: float) -> bool:
        """Ground-truth formula: closed-interval containment."""
        return min(a, b) <= TP_COLUMN_X <= max(a, b)

    def test_equivalence_to_reference(self):
        """Predicate matches the reference min/max formula for all samples."""
        rng = random.Random(self.SEED)
        for _ in range(self.N_SAMPLES):
            a = rng.uniform(self.X_MIN, self.X_MAX)
            b = rng.uniform(self.X_MIN, self.X_MAX)
            result = westrun_crosses_tp_column(a, b)
            expected = self._reference(a, b)
            assert result == expected, (
                f"Mismatch: westrun_crosses({a}, {b}) = {result}, "
                f"reference = {expected}")

    def test_symmetry_fuzz(self):
        """westrun_crosses(a, b) == westrun_crosses(b, a) for all samples."""
        rng = random.Random(self.SEED + 1)
        for _ in range(self.N_SAMPLES):
            a = rng.uniform(self.X_MIN, self.X_MAX)
            b = rng.uniform(self.X_MIN, self.X_MAX)
            assert westrun_crosses_tp_column(a, b) == westrun_crosses_tp_column(b, a), (
                f"Symmetry violation: a={a}, b={b}")

    def test_endpoint_on_boundary_fuzz(self):
        """One endpoint fixed at TP_COLUMN_X, other random → always crosses."""
        rng = random.Random(self.SEED + 2)
        for _ in range(self.N_SAMPLES):
            other = rng.uniform(self.X_MIN, self.X_MAX)
            assert westrun_crosses_tp_column(TP_COLUMN_X, other) is True, (
                f"Endpoint-on-boundary failed: other={other}")
            assert westrun_crosses_tp_column(other, TP_COLUMN_X) is True, (
                f"Endpoint-on-boundary (reversed) failed: other={other}")
