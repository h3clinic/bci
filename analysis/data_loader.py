"""
data_loader.py — Frozen interface between frame transport and analysis.

Contract:
    load_recording(...) -> Recording(fs, data_uv, metadata)

Everything downstream of this module uses Recording objects.
When real hardware replaces synthetic data, ONLY this module changes.

Supported sources:
  - Synthetic (SyntheticRecording builder)
  - Binary frame stream (frame_parser.py → numpy)
  - Raw numpy .npz files (for cached/offline data)
"""

from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

# Cross-package import: hardware host module for frame parsing
_host_dir = os.path.join(os.path.dirname(__file__),
                         "..", "hardware", "meadaq-64ch", "host")
if _host_dir not in sys.path:
    sys.path.insert(0, _host_dir)

from signal_gen import SyntheticRecording, adc_to_uv


@dataclass
class RecordingMetadata:
    """Metadata about a recording session."""
    source: str = "unknown"              # "synthetic", "hardware", "file"
    board_id: str = ""                   # board serial / identifier
    n_channels: int = 0
    duration_s: float = 0.0
    total_frames: int = 0
    crc_errors: int = 0
    drops_detected: int = 0
    reserved_violations: int = 0
    notes: str = ""


@dataclass
class Recording:
    """Frozen analysis input: everything downstream uses this.

    Attributes:
        fs: Sample rate in Hz.
        data_uv: (n_channels, n_samples) float64 array in µV.
        metadata: RecordingMetadata with session info.
    """
    fs: float
    data_uv: NDArray[np.float64]
    metadata: RecordingMetadata = field(default_factory=RecordingMetadata)

    @property
    def n_channels(self) -> int:
        return self.data_uv.shape[0]

    @property
    def n_samples(self) -> int:
        return self.data_uv.shape[1]

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.fs

    @property
    def time(self) -> NDArray[np.float64]:
        return np.arange(self.n_samples) / self.fs


def load_synthetic(
    scenario: str = "baseline",
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 30.0,
    seed: int = 42,
) -> Recording:
    """Generate a synthetic recording for pipeline validation.

    Scenarios:
        "baseline"  — noise only (Figure 1)
        "crosstalk" — tone on CH0 + controlled coupling (Figure 2)
        "neurotox"  — baseline + perturbation at midpoint (Figure 3)
        "full"      — all features combined (end-to-end)
    """
    rec = SyntheticRecording(
        n_channels=n_channels, fs=fs, duration_s=duration_s, seed=seed,
    )

    if scenario == "baseline":
        rec.add_noise(rms_uv=3.0)
        rec.add_line_noise(amplitude_uv=20.0)
        notes = "Baseline: 3 µVrms white noise + 20 µV 60 Hz line"

    elif scenario == "crosstalk":
        rec.add_noise(rms_uv=2.0)
        rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=500.0)
        # Graduated coupling: nearest neighbor worst
        for ch in range(1, min(n_channels, 16)):
            coupling = 0.02 / ch  # 2% nearest, 1% next, etc.
            rec.add_crosstalk(source_channel=0, target_channel=ch,
                              coupling_ratio=coupling)
        notes = (f"Crosstalk: 500 µV tone on CH0, graduated coupling "
                 f"(2%/ch1, 1%/ch2, ...)")

    elif scenario == "neurotox":
        onset = duration_s / 2
        rec.add_noise(rms_uv=3.0)
        rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
        rec.add_spikes(channel=1, rate_hz=3.0, amplitude_uv=-150.0)
        rec.add_neurotox_perturbation(onset_s=onset, noise_increase_uv=12.0)
        rec.add_impedance_drift(channel=0, start_s=onset, ramp_uv_per_s=0.5)
        notes = (f"Neurotox proxy: 3 µVrms baseline, +12 µVrms perturbation "
                 f"at t={onset:.0f}s, impedance drift on CH0")

    elif scenario == "full":
        onset = duration_s / 2
        rec.add_noise(rms_uv=3.0)
        rec.add_line_noise(amplitude_uv=20.0)
        rec.add_spikes(channel=0, rate_hz=5.0, amplitude_uv=-200.0)
        rec.add_tone(channel=4, freq_hz=1000, amplitude_uv=300.0)
        rec.add_crosstalk(source_channel=4, target_channel=5, coupling_ratio=0.01)
        rec.add_neurotox_perturbation(onset_s=onset, noise_increase_uv=12.0)
        notes = "Full scenario: noise + line + spikes + tone + crosstalk + neurotox"

    else:
        raise ValueError(f"Unknown scenario: {scenario!r}")

    data_uv = rec.build_uv()

    return Recording(
        fs=fs,
        data_uv=data_uv,
        metadata=RecordingMetadata(
            source="synthetic",
            n_channels=n_channels,
            duration_s=duration_s,
            total_frames=int(duration_s * fs / 64),  # approximate
            notes=notes,
        ),
    )


def load_frame_stream(path: str | Path, strict: bool = False) -> Recording:
    """Load a binary frame stream captured from hardware.

    Uses frame_parser.py to decode 256-byte frames, then converts
    ADC codes to µV and packs into Recording.

    This is the function that changes when real hardware data arrives.
    """
    from frame_parser import FrameParser, FRAME_SIZE, ADC_CHANNELS

    path = Path(path)
    raw = path.read_bytes()

    parser = FrameParser(strict=strict)
    frames = list(parser.parse_stream(raw))

    if not frames:
        raise ValueError(f"No valid frames in {path}")

    fs_code = frames[0].sample_rate_code
    fs_map = {0x0001: 20_000.0, 0x0002: 30_000.0, 0x0003: 25_000.0}
    fs = fs_map.get(fs_code, 20_000.0)

    n_channels = frames[0].channel_count
    n_frames = len(frames)

    data = np.zeros((n_channels, n_frames), dtype=np.float64)
    for i, frame in enumerate(frames):
        for ch in range(min(n_channels, len(frame.adc_samples))):
            data[ch, i] = frame.adc_samples[ch] * 0.195  # ADC → µV

    return Recording(
        fs=fs,
        data_uv=data,
        metadata=RecordingMetadata(
            source="hardware",
            n_channels=n_channels,
            duration_s=n_frames / fs,
            total_frames=parser.total_frames,
            crc_errors=parser.total_crc_errors,
            drops_detected=parser.total_drops_detected,
            reserved_violations=parser.total_reserved_violations,
            notes=f"Loaded from {path.name}, {n_frames} frames",
        ),
    )


def load_npz(path: str | Path) -> Recording:
    """Load a cached recording from .npz file."""
    path = Path(path)
    npz = np.load(path, allow_pickle=True)
    meta_dict = npz.get("metadata", np.array({})).item()
    return Recording(
        fs=float(npz["fs"]),
        data_uv=npz["data_uv"],
        metadata=RecordingMetadata(**meta_dict) if meta_dict else RecordingMetadata(
            source="file", notes=f"Loaded from {path.name}",
        ),
    )


def save_npz(recording: Recording, path: str | Path) -> None:
    """Cache a recording to .npz file."""
    from dataclasses import asdict
    path = Path(path)
    np.savez_compressed(
        path,
        fs=recording.fs,
        data_uv=recording.data_uv,
        metadata=asdict(recording.metadata),
    )
