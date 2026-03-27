"""
perturbation_library.py — Labeled perturbation window generator.

Generates multichannel time-series windows with ground-truth metadata
for training and evaluating a two-stage perturbation detector.

Design: perturbation-detection as a binary problem on microvolt time
series, NOT "AI magic." Every generated window has:
  - data_uv: (n_channels, n_samples) float64
  - metadata: PerturbationMeta with type, severity, onset, params

Perturbation taxonomy (defensible operational definition):
═══════════════════════════════════════════════════════════
  BASELINE
    baseline_stable      Pure noise + spikes, no perturbation

  ARTIFACT (instrumentation, NOT biological)
    impedance_drift      Slow baseline wander on subset of channels
    broadband_noise      Step increase in noise floor (all channels)
    line_interference    50/60 Hz + harmonics injection
    crosstalk_coupling   Known % coupling between channel pairs

  NEUROTOX-LIKE (biological perturbation signatures)
    spike_suppression    Spike rate ↓, preserved noise floor
    burst_collapse       Burst fragmentation + inter-burst interval ↑
    spectral_shift       Power redistribution between bands (slope Δ)
    mixed_neurotox       Spike suppression + burst collapse + slope Δ

The key claim: "We detect neurotoxicity-*like* electrophysiological
perturbation signatures and distinguish them from non-toxic
instrumentation artifacts."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from signal_gen import SyntheticRecording


# ── Metadata ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PerturbationMeta:
    """Ground-truth metadata for one generated window."""
    category: str           # "baseline", "artifact", "neurotox"
    perturbation_type: str  # e.g. "spike_suppression", "impedance_drift"
    severity: float         # 0.0 = none, 1.0 = maximum
    onset_s: float          # perturbation onset in seconds
    duration_s: float       # total window duration
    fs: float               # sample rate
    n_channels: int
    params: dict[str, Any] = field(default_factory=dict)
    seed: int = 0


@dataclass
class LabeledWindow:
    """One labeled data window + metadata."""
    data_uv: NDArray[np.float64]  # (n_channels, n_samples)
    meta: PerturbationMeta


# ── Default parameter ranges ────────────────────────────────────────
# Anchored on spike suppression + burst collapse as primary effect.

# Baseline biophysical parameters
DEFAULT_NOISE_UV = 2.4          # RHD2132 spec: 2.4 µVrms input-referred
DEFAULT_SPIKE_RATE_HZ = 8.0    # typical cortical spontaneous rate
DEFAULT_SPIKE_AMP_UV = -200.0  # typical extracellular spike amplitude
DEFAULT_N_CHANNELS = 16
DEFAULT_FS = 20_000.0
DEFAULT_DURATION_S = 30.0
DEFAULT_ONSET_S = 10.0          # perturbation at 1/3 into recording

# Severity lookup: maps severity ∈ [0,1] to physical parameter ranges
# for each perturbation type. Interpolated linearly.
SEVERITY_RANGES = {
    # ── Artifacts ──
    "impedance_drift": {
        "ramp_uv_per_s": (0.2, 2.0),       # µV/s drift rate
        "n_affected_channels": (1, 4),       # how many channels drift
    },
    "broadband_noise": {
        "noise_increase_uv": (1.0, 10.0),   # additional RMS µV
    },
    "line_interference": {
        "amplitude_uv": (5.0, 80.0),        # 60 Hz amplitude
        "n_harmonics": (1, 3),               # harmonics count
    },
    "crosstalk_coupling": {
        "coupling_ratio": (0.005, 0.05),     # 0.5% to 5%
        "n_pairs": (1, 4),                   # how many channel pairs
    },
    # ── Neurotox-like (primary: spike suppression + burst collapse) ──
    "spike_suppression": {
        "suppression_frac": (0.2, 0.8),      # fraction of spikes removed
    },
    "burst_collapse": {
        "dropout_prob": (0.1, 0.5),          # burst fragmentation
        "suppression_frac": (0.15, 0.5),     # concurrent spike reduction
    },
    "spectral_shift": {
        "noise_increase_uv": (1.5, 6.0),    # broadband + slope change
        "suppression_frac": (0.1, 0.3),      # mild amplitude reduction
    },
    "mixed_neurotox": {
        "suppression_frac": (0.3, 0.7),      # spike suppression
        "dropout_prob": (0.1, 0.4),           # burst collapse
        "noise_increase_uv": (0.5, 3.0),     # mild noise elevation
    },
}


def _lerp(lo: float, hi: float, severity: float) -> float:
    """Linear interpolation between lo and hi based on severity ∈ [0,1]."""
    return lo + (hi - lo) * np.clip(severity, 0.0, 1.0)


def _lerp_int(lo: int, hi: int, severity: float) -> int:
    return int(round(_lerp(float(lo), float(hi), severity)))


# ── Generator functions ──────────────────────────────────────────────

def _make_base_recording(
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    noise_uv: float = DEFAULT_NOISE_UV,
    spike_rate_hz: float = DEFAULT_SPIKE_RATE_HZ,
    spike_amp_uv: float = DEFAULT_SPIKE_AMP_UV,
    seed: int = 42,
    rng: np.random.Generator | None = None,
) -> SyntheticRecording:
    """Build a baseline recording with noise + spikes on all channels."""
    rec = SyntheticRecording(n_channels=n_channels, fs=fs, duration_s=duration_s,
                             seed=seed)
    rec.add_noise(rms_uv=noise_uv)

    if rng is None:
        rng = np.random.default_rng(seed + 10000)

    for ch in range(n_channels):
        rate = max(0.5, spike_rate_hz + rng.normal(0, 1.5))
        amp = spike_amp_uv + rng.normal(0, 30)
        rec.add_spikes(ch, rate_hz=rate, amplitude_uv=amp)

    return rec


def generate_baseline(
    seed: int = 42,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Generate a stable baseline window (no perturbation)."""
    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="baseline",
            perturbation_type="baseline_stable",
            severity=0.0,
            onset_s=duration_s,  # no onset
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            seed=seed,
        ),
    )


# ── Artifact generators ─────────────────────────────────────────────

def generate_impedance_drift(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Impedance drift artifact: slow baseline wander on subset of channels."""
    ranges = SEVERITY_RANGES["impedance_drift"]
    ramp = _lerp(*ranges["ramp_uv_per_s"], severity)
    n_affected = _lerp_int(*ranges["n_affected_channels"], severity)

    rng = np.random.default_rng(seed + 20000)
    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)

    affected_chs = rng.choice(n_channels, size=min(n_affected, n_channels),
                              replace=False).tolist()
    for ch in affected_chs:
        rec.add_impedance_drift(ch, start_s=onset_s, ramp_uv_per_s=ramp)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="artifact",
            perturbation_type="impedance_drift",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"ramp_uv_per_s": ramp, "n_affected": n_affected,
                    "affected_channels": affected_chs},
            seed=seed,
        ),
    )


def generate_broadband_noise(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Broadband noise artifact: step noise floor increase."""
    ranges = SEVERITY_RANGES["broadband_noise"]
    noise_inc = _lerp(*ranges["noise_increase_uv"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    rec.add_noise_elevation(onset_s=onset_s, noise_increase_uv=noise_inc)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="artifact",
            perturbation_type="broadband_noise",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"noise_increase_uv": noise_inc},
            seed=seed,
        ),
    )


def generate_line_interference(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Line interference artifact: 60 Hz + harmonics injected post-onset."""
    ranges = SEVERITY_RANGES["line_interference"]
    amp = _lerp(*ranges["amplitude_uv"], severity)
    n_harm = _lerp_int(*ranges["n_harmonics"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)

    # Inject line noise only after onset by manipulating data directly
    onset_idx = int(onset_s * fs)
    t_post = np.arange(rec.n_samples - onset_idx) / fs
    for h in range(1, n_harm + 1):
        freq = 60.0 * h
        tone = amp / h * np.sin(2 * np.pi * freq * t_post)
        for ch in range(n_channels):
            rec._data[ch, onset_idx:] += tone

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="artifact",
            perturbation_type="line_interference",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"amplitude_uv": amp, "n_harmonics": n_harm},
            seed=seed,
        ),
    )


def generate_crosstalk(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Crosstalk artifact: coupling between channel pairs post-onset."""
    ranges = SEVERITY_RANGES["crosstalk_coupling"]
    ratio = _lerp(*ranges["coupling_ratio"], severity)
    n_pairs = _lerp_int(*ranges["n_pairs"], severity)

    rng = np.random.default_rng(seed + 30000)
    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)

    onset_idx = int(onset_s * fs)
    pairs = []
    for _ in range(min(n_pairs, n_channels // 2)):
        src, tgt = rng.choice(n_channels, size=2, replace=False)
        pairs.append((int(src), int(tgt)))
        # Apply coupling only post-onset
        rec._data[tgt, onset_idx:] += ratio * rec._data[src, onset_idx:]

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="artifact",
            perturbation_type="crosstalk_coupling",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"coupling_ratio": ratio, "n_pairs": n_pairs, "pairs": pairs},
            seed=seed,
        ),
    )


# ── Neurotox-like generators ────────────────────────────────────────

def generate_spike_suppression(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Neurotox: spike rate suppression with preserved noise floor.

    Models inhibitory neurotoxin (e.g., TTX-like, GABAergic):
    spike rate drops, noise floor stays constant.
    """
    ranges = SEVERITY_RANGES["spike_suppression"]
    supp = _lerp(*ranges["suppression_frac"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    rec.add_firing_rate_suppression(onset_s=onset_s, suppression_frac=supp)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="neurotox",
            perturbation_type="spike_suppression",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"suppression_frac": supp},
            seed=seed,
        ),
    )


def generate_burst_collapse(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Neurotox: burst fragmentation + concurrent spike rate reduction.

    Models Na+ channel block (e.g., TTX, local anesthetics):
    bursts break apart, spike rate drops, noise floor preserved.
    """
    ranges = SEVERITY_RANGES["burst_collapse"]
    dropout = _lerp(*ranges["dropout_prob"], severity)
    supp = _lerp(*ranges["suppression_frac"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    rec.add_burst_fragmentation(onset_s=onset_s, dropout_prob=dropout)
    rec.add_firing_rate_suppression(onset_s=onset_s, suppression_frac=supp)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="neurotox",
            perturbation_type="burst_collapse",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"dropout_prob": dropout, "suppression_frac": supp},
            seed=seed,
        ),
    )


def generate_spectral_shift(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Neurotox: spectral power redistribution + slope change.

    Models excitotoxic / ion-channel disruption: broadband power
    shifts, spectral slope flattens, mild amplitude reduction.
    """
    ranges = SEVERITY_RANGES["spectral_shift"]
    noise_inc = _lerp(*ranges["noise_increase_uv"], severity)
    supp = _lerp(*ranges["suppression_frac"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    rec.add_noise_elevation(onset_s=onset_s, noise_increase_uv=noise_inc)
    rec.add_amplitude_suppression(onset_s=onset_s, suppression_frac=supp)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="neurotox",
            perturbation_type="spectral_shift",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"noise_increase_uv": noise_inc, "suppression_frac": supp},
            seed=seed,
        ),
    )


def generate_mixed_neurotox(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Neurotox: mixed signature (spike suppression + burst collapse + noise).

    Most realistic model — combines multiple effects as real toxins
    would produce. Primary anchor for the narrative.
    """
    ranges = SEVERITY_RANGES["mixed_neurotox"]
    supp = _lerp(*ranges["suppression_frac"], severity)
    dropout = _lerp(*ranges["dropout_prob"], severity)
    noise_inc = _lerp(*ranges["noise_increase_uv"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    rec.add_firing_rate_suppression(onset_s=onset_s, suppression_frac=supp)
    rec.add_burst_fragmentation(onset_s=onset_s, dropout_prob=dropout)
    rec.add_noise_elevation(onset_s=onset_s, noise_increase_uv=noise_inc)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="neurotox",
            perturbation_type="mixed_neurotox",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"suppression_frac": supp, "dropout_prob": dropout,
                    "noise_increase_uv": noise_inc},
            seed=seed,
        ),
    )


# ── Registry ─────────────────────────────────────────────────────────

GENERATORS = {
    "baseline_stable":    generate_baseline,
    "impedance_drift":    generate_impedance_drift,
    "broadband_noise":    generate_broadband_noise,
    "line_interference":  generate_line_interference,
    "crosstalk_coupling": generate_crosstalk,
    "spike_suppression":  generate_spike_suppression,
    "burst_collapse":     generate_burst_collapse,
    "spectral_shift":     generate_spectral_shift,
    "mixed_neurotox":     generate_mixed_neurotox,
}

ARTIFACT_TYPES = ["impedance_drift", "broadband_noise",
                  "line_interference", "crosstalk_coupling"]
NEUROTOX_TYPES = ["spike_suppression", "burst_collapse",
                  "spectral_shift", "mixed_neurotox"]


# ── Holdout generator (NEVER in training set) ────────────────────────
# This is the "unseen 9th variant" that demonstrates generalization.
# It's a partial-suppression + slow noise ramp — a blend that the
# classifier has never seen during training.

def generate_partial_suppression(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Holdout: partial spike suppression + slow impedance-like drift.

    A blend of neurotox + artifact signatures the classifier has never
    seen. Used ONLY for evaluation — proves the model generalizes beyond
    the exact generator combinations it was trained on.
    """
    supp = _lerp(0.15, 0.45, severity)       # mild suppression
    ramp = _lerp(0.1, 0.8, severity)          # slow drift

    rng = np.random.default_rng(seed + 50000)
    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)
    rec.add_firing_rate_suppression(onset_s=onset_s, suppression_frac=supp)

    # Add slow drift on ~half the channels (artifact-like)
    n_drift = max(1, n_channels // 3)
    drift_chs = rng.choice(n_channels, size=n_drift, replace=False).tolist()
    for ch in drift_chs:
        rec.add_impedance_drift(ch, start_s=onset_s, ramp_uv_per_s=ramp)

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="neurotox",  # true label for evaluation
            perturbation_type="partial_suppression_holdout",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"suppression_frac": supp, "ramp_uv_per_s": ramp,
                    "n_drift_channels": n_drift},
            seed=seed,
        ),
    )


HOLDOUT_TYPES = ["partial_suppression_holdout"]


# ── Adversarial generators (compound perturbations for X1/X2) ────────
# These map to Phantom Protocol v2 trials X1 and X2.
# They combine two independent perturbation mechanisms simultaneously
# to stress-test the classifier with "confusing" mixtures that blur
# the artifact ↔ neurotox boundary.

SEVERITY_RANGES["adversarial_drift_coupling"] = {
    "ramp_uv_per_s": (0.3, 1.8),
    "coupling_ratio": (0.008, 0.04),
    "n_affected_channels": (1, 3),
    "n_pairs": (1, 3),
}

SEVERITY_RANGES["adversarial_suppression_60hz"] = {
    "suppression_frac": (0.2, 0.6),
    "amplitude_uv": (8.0, 60.0),
    "n_harmonics": (1, 3),
}


def generate_adversarial_drift_coupling(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Adversarial X1: impedance drift + crosstalk coupling simultaneously.

    Combines two artifact mechanisms with independent parameters.
    Stresses the classifier because drift alone and coupling alone
    are trained classes, but their concurrent activation is novel.
    Maps to Phantom Protocol v2 trial X1.
    """
    ranges = SEVERITY_RANGES["adversarial_drift_coupling"]
    ramp = _lerp(*ranges["ramp_uv_per_s"], severity)
    ratio = _lerp(*ranges["coupling_ratio"], severity)
    n_drift = _lerp_int(*ranges["n_affected_channels"], severity)
    n_pairs = _lerp_int(*ranges["n_pairs"], severity)

    rng = np.random.default_rng(seed + 60000)
    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)

    # Impedance drift on subset of channels
    drift_chs = rng.choice(n_channels, size=min(n_drift, n_channels),
                           replace=False).tolist()
    for ch in drift_chs:
        rec.add_impedance_drift(ch, start_s=onset_s, ramp_uv_per_s=ramp)

    # Crosstalk coupling on independent pairs
    onset_idx = int(onset_s * fs)
    pairs = []
    for _ in range(min(n_pairs, n_channels // 2)):
        src, tgt = rng.choice(n_channels, size=2, replace=False)
        pairs.append((int(src), int(tgt)))
        rec._data[tgt, onset_idx:] += ratio * rec._data[src, onset_idx:]

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="artifact",
            perturbation_type="adversarial_drift_coupling",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"ramp_uv_per_s": ramp, "coupling_ratio": ratio,
                    "n_drift_channels": n_drift, "n_pairs": n_pairs,
                    "drift_channels": drift_chs, "pairs": pairs},
            seed=seed,
        ),
    )


def generate_adversarial_suppression_60hz(
    severity: float = 0.5,
    seed: int = 42,
    onset_s: float = DEFAULT_ONSET_S,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    **kwargs: Any,
) -> LabeledWindow:
    """Adversarial X2: spike suppression + 60 Hz line interference.

    Combines a neurotox mechanism (suppression) with an artifact
    mechanism (line noise). The classifier must decide: is this an
    artifact with incidental suppression, or a neurotox event masked
    by line noise? Ground truth: neurotox (suppression is the primary
    biological effect; line noise is environmental).
    Maps to Phantom Protocol v2 trial X2.
    """
    ranges = SEVERITY_RANGES["adversarial_suppression_60hz"]
    supp = _lerp(*ranges["suppression_frac"], severity)
    amp = _lerp(*ranges["amplitude_uv"], severity)
    n_harm = _lerp_int(*ranges["n_harmonics"], severity)

    rec = _make_base_recording(n_channels=n_channels, fs=fs,
                               duration_s=duration_s, seed=seed)

    # Neurotox: spike suppression
    rec.add_firing_rate_suppression(onset_s=onset_s, suppression_frac=supp)

    # Artifact: 60 Hz line interference post-onset
    onset_idx = int(onset_s * fs)
    t_post = np.arange(rec.n_samples - onset_idx) / fs
    for h in range(1, n_harm + 1):
        freq = 60.0 * h
        tone = amp / h * np.sin(2 * np.pi * freq * t_post)
        for ch in range(n_channels):
            rec._data[ch, onset_idx:] += tone

    return LabeledWindow(
        data_uv=rec.build_uv(),
        meta=PerturbationMeta(
            category="neurotox",  # primary effect is suppression
            perturbation_type="adversarial_suppression_60hz",
            severity=severity,
            onset_s=onset_s,
            duration_s=duration_s,
            fs=fs,
            n_channels=n_channels,
            params={"suppression_frac": supp, "amplitude_uv": amp,
                    "n_harmonics": n_harm},
            seed=seed,
        ),
    )


ADVERSARIAL_TYPES = ["adversarial_drift_coupling",
                     "adversarial_suppression_60hz"]

ADVERSARIAL_GENERATORS = {
    "adversarial_drift_coupling": generate_adversarial_drift_coupling,
    "adversarial_suppression_60hz": generate_adversarial_suppression_60hz,
}


def generate_dataset(
    n_per_class: int = 10,
    severities: list[float] | None = None,
    n_channels: int = DEFAULT_N_CHANNELS,
    fs: float = DEFAULT_FS,
    duration_s: float = DEFAULT_DURATION_S,
    onset_s: float = DEFAULT_ONSET_S,
    seed: int = 42,
) -> list[LabeledWindow]:
    """Generate a full labeled dataset across all perturbation types.

    Args:
        n_per_class: number of windows per (type, severity) combination
        severities: list of severity levels to sample. Default [0.3, 0.6, 0.9].
        n_channels: channels per recording
        fs: sample rate
        duration_s: recording length
        onset_s: perturbation onset
        seed: base seed (incremented per trial for reproducibility)

    Returns:
        List of LabeledWindow objects with ground-truth metadata.
    """
    if severities is None:
        severities = [0.3, 0.6, 0.9]

    dataset: list[LabeledWindow] = []
    trial_seed = seed

    # Baseline
    for i in range(n_per_class):
        trial_seed += 1
        dataset.append(generate_baseline(
            seed=trial_seed, n_channels=n_channels, fs=fs, duration_s=duration_s,
        ))

    # Artifacts + neurotox
    for ptype, gen_fn in GENERATORS.items():
        if ptype == "baseline_stable":
            continue
        for sev in severities:
            for i in range(n_per_class):
                trial_seed += 1
                dataset.append(gen_fn(
                    severity=sev, seed=trial_seed, onset_s=onset_s,
                    n_channels=n_channels, fs=fs, duration_s=duration_s,
                ))

    return dataset


def dataset_summary(dataset: list[LabeledWindow]) -> dict[str, int]:
    """Count windows by category and type."""
    counts: dict[str, int] = {}
    for w in dataset:
        key = f"{w.meta.category}/{w.meta.perturbation_type}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
