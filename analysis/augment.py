"""
augment.py — Domain-randomization augmentations for neural time-series.

Purpose: transform (Nch × T) windows to increase training diversity,
simulating real-world structured contamination that ML *can* beat:
  - 60 Hz line noise with random phase + harmonics
  - Random channel dropout (dead/shorted channels)
  - Per-channel gain scaling (gain mismatch / drift)
  - Jitter bursts (contact bounce / motion artifacts)
  - Coupling matrix randomization (crosstalk)
  - Low-frequency drift (impedance change)
  - Quantization + clipping events (ADC saturation)

Design rules:
  1. Every augmentation is a pure function: (data, rng) → data
  2. Composable via AugmentPipeline
  3. All parameters drawn from physically motivated ranges
  4. Seeded for reproducibility
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from numpy.typing import NDArray


# ── Individual augmentations ─────────────────────────────────────────
# Each takes (data_uv, fs, rng) → augmented data_uv (same shape).
# data_uv is (n_channels, n_samples), float64, in µV.

def aug_line_noise(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    amp_range: tuple[float, float] = (2.0, 60.0),
    freq: float = 60.0,
    max_harmonics: int = 3,
) -> NDArray[np.float64]:
    """Add 60 Hz + harmonics with random amplitude and phase.

    Simulates powerline contamination at random coupling strength.
    """
    n_ch, n_samp = data_uv.shape
    out = data_uv.copy()
    amp = rng.uniform(*amp_range)
    phase = rng.uniform(0, 2 * np.pi)
    n_harm = rng.integers(1, max_harmonics + 1)
    t = np.arange(n_samp) / fs

    for h in range(1, n_harm + 1):
        tone = (amp / h) * np.sin(2 * np.pi * freq * h * t + phase * h)
        # Apply to random subset of channels
        n_affected = rng.integers(max(1, n_ch // 2), n_ch + 1)
        chs = rng.choice(n_ch, size=n_affected, replace=False)
        for ch in chs:
            out[ch] += tone
    return out


def aug_channel_dropout(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    max_drop_frac: float = 0.25,
) -> NDArray[np.float64]:
    """Zero out random channels (dead/shorted electrode simulation).

    Up to max_drop_frac of channels are silenced entirely.
    """
    n_ch, _ = data_uv.shape
    out = data_uv.copy()
    n_drop = rng.integers(0, max(1, int(n_ch * max_drop_frac)) + 1)
    if n_drop > 0:
        drop_chs = rng.choice(n_ch, size=n_drop, replace=False)
        out[drop_chs] = 0.0
    return out


def aug_gain_scaling(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    gain_range: tuple[float, float] = (0.7, 1.3),
) -> NDArray[np.float64]:
    """Per-channel random gain (PGA mismatch, impedance variation)."""
    n_ch, _ = data_uv.shape
    gains = rng.uniform(*gain_range, size=n_ch)
    return data_uv * gains[:, np.newaxis]


def aug_jitter_burst(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    n_bursts_range: tuple[int, int] = (1, 5),
    burst_dur_ms_range: tuple[float, float] = (1.0, 20.0),
    burst_amp_range: tuple[float, float] = (50.0, 500.0),
) -> NDArray[np.float64]:
    """Inject short random-amplitude bursts (contact bounce / motion).

    Models intermittent mechanical disruptions at the electrode.
    """
    n_ch, n_samp = data_uv.shape
    out = data_uv.copy()
    n_bursts = rng.integers(*n_bursts_range)

    for _ in range(n_bursts):
        dur_ms = rng.uniform(*burst_dur_ms_range)
        dur_samp = max(1, int(dur_ms * fs / 1000))
        amp = rng.uniform(*burst_amp_range)
        start = rng.integers(0, max(1, n_samp - dur_samp))
        # Affect 1–3 channels
        n_affected = rng.integers(1, min(4, n_ch + 1))
        chs = rng.choice(n_ch, size=n_affected, replace=False)
        burst = amp * rng.standard_normal(dur_samp)
        for ch in chs:
            out[ch, start:start + dur_samp] += burst
    return out


def aug_coupling_matrix(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    max_pairs: int = 4,
    coupling_range: tuple[float, float] = (0.005, 0.05),
) -> NDArray[np.float64]:
    """Random inter-channel crosstalk coupling.

    Simulates capacitive/resistive coupling between traces or electrodes.
    """
    n_ch, _ = data_uv.shape
    out = data_uv.copy()
    n_pairs = rng.integers(1, min(max_pairs, n_ch // 2) + 1)

    for _ in range(n_pairs):
        src, tgt = rng.choice(n_ch, size=2, replace=False)
        ratio = rng.uniform(*coupling_range)
        out[tgt] += ratio * data_uv[src]
    return out


def aug_drift(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    max_ramp_uv_per_s: float = 2.0,
    max_channels: int = 4,
) -> NDArray[np.float64]:
    """Low-frequency baseline drift (impedance change).

    Linear ramp from random start point, physically motivated by
    electrode-electrolyte interface drift.
    """
    n_ch, n_samp = data_uv.shape
    out = data_uv.copy()
    n_drift = rng.integers(1, min(max_channels, n_ch) + 1)
    chs = rng.choice(n_ch, size=n_drift, replace=False)
    t = np.arange(n_samp) / fs

    for ch in chs:
        ramp_rate = rng.uniform(0.1, max_ramp_uv_per_s)
        start_frac = rng.uniform(0.0, 0.5)
        start_idx = int(start_frac * n_samp)
        ramp = np.zeros(n_samp)
        ramp[start_idx:] = np.arange(n_samp - start_idx) / fs * ramp_rate
        # Random sign
        if rng.random() < 0.5:
            ramp = -ramp
        out[ch] += ramp
    return out


def aug_quantize_clip(
    data_uv: NDArray[np.float64],
    fs: float,
    rng: np.random.Generator,
    *,
    clip_range_uv: tuple[float, float] = (3000.0, 6400.0),
    quantize_bits: tuple[int, int] = (10, 16),
) -> NDArray[np.float64]:
    """ADC quantization + clipping (saturation events).

    Simulates reduced effective resolution and rail clipping from
    electrode offset or large transients.
    """
    out = data_uv.copy()
    # Random clipping level
    clip_uv = rng.uniform(*clip_range_uv)
    out = np.clip(out, -clip_uv, clip_uv)
    # Random quantization
    bits = rng.integers(*quantize_bits)
    lsb = (2 * clip_uv) / (2 ** bits)
    out = np.round(out / lsb) * lsb
    return out


# ── Augmentation registry ────────────────────────────────────────────

AugFn = Callable[[NDArray[np.float64], float, np.random.Generator], NDArray[np.float64]]

AUGMENTATION_REGISTRY: dict[str, AugFn] = {
    "line_noise": aug_line_noise,
    "channel_dropout": aug_channel_dropout,
    "gain_scaling": aug_gain_scaling,
    "jitter_burst": aug_jitter_burst,
    "coupling_matrix": aug_coupling_matrix,
    "drift": aug_drift,
    "quantize_clip": aug_quantize_clip,
}

ALL_AUGMENTATION_NAMES: list[str] = list(AUGMENTATION_REGISTRY.keys())


# ── Composable pipeline ──────────────────────────────────────────────

@dataclass
class AugmentPipeline:
    """Composable augmentation pipeline with per-augmentation probability.

    Usage:
        pipe = AugmentPipeline(augmentations=["line_noise", "drift"],
                               p_each=0.5, seed=42)
        augmented = pipe(data_uv, fs)
    """
    augmentations: list[str] = field(default_factory=lambda: ALL_AUGMENTATION_NAMES.copy())
    p_each: float = 0.5    # probability each augmentation is applied
    seed: int = 42

    def __post_init__(self) -> None:
        for name in self.augmentations:
            if name not in AUGMENTATION_REGISTRY:
                raise ValueError(f"Unknown augmentation: {name}. "
                                 f"Available: {ALL_AUGMENTATION_NAMES}")

    def __call__(
        self,
        data_uv: NDArray[np.float64],
        fs: float,
        seed_offset: int = 0,
    ) -> NDArray[np.float64]:
        """Apply augmentations stochastically.

        Args:
            data_uv: (n_channels, n_samples) input
            fs: sample rate
            seed_offset: added to base seed for per-sample variation
        """
        rng = np.random.default_rng(self.seed + seed_offset)
        out = data_uv.copy()

        for name in self.augmentations:
            if rng.random() < self.p_each:
                fn = AUGMENTATION_REGISTRY[name]
                out = fn(out, fs, rng)

        return out

    def apply_all(
        self,
        data_uv: NDArray[np.float64],
        fs: float,
    ) -> NDArray[np.float64]:
        """Apply ALL augmentations deterministically (no randomness in selection)."""
        rng = np.random.default_rng(self.seed)
        out = data_uv.copy()
        for name in self.augmentations:
            fn = AUGMENTATION_REGISTRY[name]
            out = fn(out, fs, rng)
        return out


def augment_batch(
    windows: list[NDArray[np.float64]],
    fs: float,
    pipeline: AugmentPipeline | None = None,
    seed: int = 42,
) -> list[NDArray[np.float64]]:
    """Augment a batch of windows, each with different random state.

    Args:
        windows: list of (n_channels, n_samples) arrays
        fs: sample rate
        pipeline: AugmentPipeline instance (default: all augs, p=0.5)
        seed: base seed

    Returns:
        List of augmented windows (same shapes).
    """
    if pipeline is None:
        pipeline = AugmentPipeline(seed=seed)
    else:
        pipeline.seed = seed

    return [pipeline(w, fs, seed_offset=i) for i, w in enumerate(windows)]
