"""
signal_gen.py — Synthetic neural waveform generator for pipeline validation.

Generates multi-channel time-series data that mimics what the RHD2132/2164
would produce, including:
  - Thermal noise floor (configurable µVrms)
  - 60 Hz line interference
  - Known-amplitude sinusoidal test tones
  - Spike-like transients
  - Crosstalk coupling between channels
  - Neurotox-like perturbations (impedance drift, broadband noise injection)

All signals are generated in physical units (µV) and quantized to 16-bit
signed integers matching the Intan RHD ADC: ±6.4 mV range, 0.195 µV/bit.

Usage:
    from signal_gen import SyntheticRecording
    rec = SyntheticRecording(n_channels=16, fs=20_000, duration_s=60.0)
    rec.add_noise(rms_uv=3.0)
    rec.add_tone(channel=0, freq_hz=1000, amplitude_uv=100)
    data = rec.build()  # shape (n_channels, n_samples), int16
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# ── Intan RHD ADC constants ─────────────────────────────────────────
ADC_RANGE_UV = 6400.0        # ±6.4 mV = ±6400 µV
ADC_BITS = 16
ADC_LSB_UV = 0.195           # µV per LSB (from Intan datasheet)
ADC_MAX = 32767
ADC_MIN = -32768


def uv_to_adc(uv: NDArray[np.float64]) -> NDArray[np.int16]:
    """Convert µV float array to 16-bit ADC codes (Intan RHD convention)."""
    codes = np.round(uv / ADC_LSB_UV).astype(np.int64)
    return np.clip(codes, ADC_MIN, ADC_MAX).astype(np.int16)


def adc_to_uv(codes: NDArray[np.int16]) -> NDArray[np.float64]:
    """Convert 16-bit ADC codes back to µV (for analysis)."""
    return codes.astype(np.float64) * ADC_LSB_UV


class SyntheticRecording:
    """
    Builder for synthetic multi-channel neural recordings.

    Accumulates signal components in float µV, then quantizes to int16
    on build(). This matches the real data path: analog → ADC → frame.
    """

    def __init__(
        self,
        n_channels: int = 16,
        fs: float = 20_000.0,
        duration_s: float = 10.0,
        seed: int | None = 42,
    ) -> None:
        self.n_channels = n_channels
        self.fs = fs
        self.duration_s = duration_s
        self.n_samples = int(fs * duration_s)
        self.rng = np.random.default_rng(seed)

        # Accumulator: float64 µV, shape (n_channels, n_samples)
        self._data = np.zeros((n_channels, self.n_samples), dtype=np.float64)
        self._time = np.arange(self.n_samples) / fs

    @property
    def time(self) -> NDArray[np.float64]:
        """Time axis in seconds."""
        return self._time

    # ── Signal components ────────────────────────────────────────────

    def add_noise(
        self,
        rms_uv: float = 3.0,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Add Gaussian white noise to specified channels (default: all)."""
        ch = range(self.n_channels) if channels is None else channels
        for c in ch:
            self._data[c] += self.rng.normal(0, rms_uv, self.n_samples)
        return self

    def add_tone(
        self,
        channel: int,
        freq_hz: float,
        amplitude_uv: float,
        phase_rad: float = 0.0,
    ) -> SyntheticRecording:
        """Add a sinusoidal test tone to one channel."""
        self._data[channel] += amplitude_uv * np.sin(
            2 * np.pi * freq_hz * self._time + phase_rad
        )
        return self

    def add_line_noise(
        self,
        amplitude_uv: float = 50.0,
        freq_hz: float = 60.0,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Add power-line interference (common-mode)."""
        ch = range(self.n_channels) if channels is None else channels
        signal = amplitude_uv * np.sin(2 * np.pi * freq_hz * self._time)
        for c in ch:
            self._data[c] += signal
        return self

    def add_spikes(
        self,
        channel: int,
        rate_hz: float = 5.0,
        amplitude_uv: float = -200.0,
        duration_ms: float = 1.0,
    ) -> SyntheticRecording:
        """Add spike-like transients (negative-going template).

        Uses a simple Gaussian-modulated template:
            spike(t) = amplitude * exp(-(t - t_peak)^2 / (2*sigma^2))
        """
        spike_samples = int(self.fs * duration_ms / 1000)
        sigma = spike_samples / 4
        t_spike = np.arange(spike_samples) - spike_samples // 2
        template = amplitude_uv * np.exp(-t_spike**2 / (2 * sigma**2))

        # Random spike times (Poisson process)
        n_expected = rate_hz * self.duration_s
        n_spikes = self.rng.poisson(n_expected)
        spike_times = self.rng.integers(
            spike_samples, self.n_samples - spike_samples, size=n_spikes
        )

        for t0 in spike_times:
            start = t0 - spike_samples // 2
            end = start + spike_samples
            if 0 <= start and end <= self.n_samples:
                self._data[channel, start:end] += template

        return self

    def add_crosstalk(
        self,
        source_channel: int,
        target_channel: int,
        coupling_ratio: float = 0.01,
    ) -> SyntheticRecording:
        """Add capacitive/resistive crosstalk: target += source * ratio.

        coupling_ratio: fraction of source signal that leaks into target.
        E.g., 0.01 = -40 dB isolation.
        """
        self._data[target_channel] += coupling_ratio * self._data[source_channel]
        return self

    def add_impedance_drift(
        self,
        channel: int,
        start_s: float,
        ramp_uv_per_s: float = 0.5,
    ) -> SyntheticRecording:
        """Simulate electrode impedance drift as slow baseline wander.

        Starts at start_s, ramps linearly at ramp_uv_per_s.
        Models the thermal/chemical drift seen in saline phantoms.
        """
        start_idx = int(start_s * self.fs)
        ramp = np.zeros(self.n_samples)
        ramp[start_idx:] = np.arange(self.n_samples - start_idx) / self.fs * ramp_uv_per_s
        self._data[channel] += ramp
        return self

    def add_neurotox_perturbation(
        self,
        onset_s: float,
        noise_increase_uv: float = 10.0,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Simulate neurotoxic perturbation: step increase in noise floor.

        Models the effect of neurotoxic agents on electrode-tissue interface:
        increased impedance → increased thermal noise → higher noise floor.
        onset_s: time when perturbation begins.
        noise_increase_uv: additional RMS noise after onset.
        """
        ch = range(self.n_channels) if channels is None else channels
        onset_idx = int(onset_s * self.fs)
        n_post = self.n_samples - onset_idx
        for c in ch:
            self._data[c, onset_idx:] += self.rng.normal(0, noise_increase_uv, n_post)
        return self

    # ── Neurotoxicity perturbation models ────────────────────────────
    #
    # Each models a distinct electrophysiological signature of toxin
    # exposure, parameterized by severity (0.0 = control, 1.0 = maximum).
    # These are layered onto existing spike/noise data to create
    # realistic perturbation-modeled datasets.

    def add_amplitude_suppression(
        self,
        onset_s: float,
        suppression_frac: float = 0.4,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Model synaptic depression / receptor antagonism.

        Reduces signal amplitude by suppression_frac (0–1) after onset.
        Represents toxin-induced reduction in synaptic transmission
        (e.g., botulinum toxin, curare-like compounds).

        suppression_frac: fraction of amplitude removed (0.2=mild, 0.5=severe).
        """
        ch = range(self.n_channels) if channels is None else channels
        onset_idx = int(onset_s * self.fs)
        for c in ch:
            self._data[c, onset_idx:] *= (1.0 - suppression_frac)
        return self

    def add_noise_elevation(
        self,
        onset_s: float,
        noise_increase_uv: float = 4.0,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Model ion channel disruption / membrane instability.

        Adds broadband Gaussian noise to simulate increased thermal noise
        from toxin-induced ion channel dysfunction (e.g., organophosphates,
        heavy metals acting on Na+/K+ channels).

        noise_increase_uv: additional RMS noise in µV (2=mild, 8=severe).
        """
        ch = range(self.n_channels) if channels is None else channels
        onset_idx = int(onset_s * self.fs)
        n_post = self.n_samples - onset_idx
        for c in ch:
            self._data[c, onset_idx:] += self.rng.normal(0, noise_increase_uv, n_post)
        return self

    def add_burst_fragmentation(
        self,
        onset_s: float,
        dropout_prob: float = 0.3,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Model sodium channel block / burst desynchronization.

        Randomly zeroes out short segments (1–5 ms) after onset, simulating
        the loss of burst coherence seen with Na+ channel blockers
        (e.g., tetrodotoxin, local anesthetics).

        dropout_prob: probability of each 1ms window being zeroed (0.1=mild, 0.5=severe).
        """
        ch = range(self.n_channels) if channels is None else channels
        onset_idx = int(onset_s * self.fs)
        window_samples = max(1, int(self.fs * 0.001))  # 1 ms windows
        n_post = self.n_samples - onset_idx
        n_windows = n_post // window_samples

        mask = self.rng.random(n_windows) > dropout_prob
        # Expand mask to sample-level
        sample_mask = np.repeat(mask, window_samples)[:n_post]

        for c in ch:
            self._data[c, onset_idx:onset_idx + len(sample_mask)] *= sample_mask
        return self

    def add_firing_rate_suppression(
        self,
        onset_s: float,
        suppression_frac: float = 0.6,
        channels: list[int] | None = None,
    ) -> SyntheticRecording:
        """Model inhibitory neurotoxin effects / GABAergic enhancement.

        Reduces spike rate by probabilistically removing spike events after
        onset. Works by attenuating high-amplitude transients while preserving
        background noise, simulating toxin-induced neuroinhibition
        (e.g., barbiturates, benzodiazepine-like compounds).

        suppression_frac: fraction of spikes suppressed (0.3=mild, 0.8=severe).
        """
        ch = range(self.n_channels) if channels is None else channels
        onset_idx = int(onset_s * self.fs)

        for c in ch:
            post_data = self._data[c, onset_idx:].copy()
            # Use MAD-based noise estimate (robust to spikes)
            median_val = np.median(post_data)
            mad = np.median(np.abs(post_data - median_val))
            sigma = mad * 1.4826  # MAD → σ for Gaussian
            if sigma < 1e-10:
                continue
            spike_mask = np.abs(post_data - median_val) > 4 * sigma
            # Expand mask by ±1 ms to cover entire spike waveform
            half_win = max(1, int(self.fs * 0.001))
            expanded = np.zeros_like(spike_mask)
            spike_indices = np.where(spike_mask)[0]
            for idx in spike_indices:
                lo = max(0, idx - half_win)
                hi = min(len(expanded), idx + half_win + 1)
                expanded[lo:hi] = True
            # Probabilistically suppress each spike event (cluster of samples)
            # Group contiguous regions
            diff = np.diff(expanded.astype(int))
            starts = np.where(diff == 1)[0] + 1
            if expanded[0]:
                starts = np.concatenate(([0], starts))
            # Decide per-spike whether to suppress
            for s in starts:
                if self.rng.random() < suppression_frac:
                    # Find end of this cluster
                    end = s
                    while end < len(expanded) and expanded[end]:
                        end += 1
                    post_data[s:end] *= 0.05  # strong attenuation
            self._data[c, onset_idx:] = post_data
        return self

    # ── Build ────────────────────────────────────────────────────────

    def build(self) -> NDArray[np.int16]:
        """Quantize accumulated signals to int16 ADC codes."""
        return uv_to_adc(self._data)

    def build_uv(self) -> NDArray[np.float64]:
        """Return accumulated signals in µV (pre-quantization)."""
        return self._data.copy()


# ── Convenience function ─────────────────────────────────────────

def generate_neural_signal(
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 10.0,
    seed: int = 42,
    noise_rms_uv: float = 3.0,
    spike_rate_hz: float = 5.0,
    spike_amplitude_uv: float = -200.0,
    line_noise_uv: float = 2.0,
) -> NDArray[np.float64]:
    """Generate a realistic multi-channel neural recording in µV.

    Returns (n_channels, n_samples) float64 array with:
      - Background Gaussian noise (thermal floor)
      - Spike-like transients on each channel
      - Low-level 60 Hz line noise

    This is a convenience wrapper around SyntheticRecording for cases
    where you just need a quick realistic waveform without builder syntax.
    """
    rec = SyntheticRecording(
        n_channels=n_channels, fs=fs, duration_s=duration_s, seed=seed,
    )
    rec.add_noise(rms_uv=noise_rms_uv)
    for ch in range(n_channels):
        rec.add_spikes(
            channel=ch,
            rate_hz=spike_rate_hz,
            amplitude_uv=spike_amplitude_uv,
        )
    if line_noise_uv > 0:
        rec.add_line_noise(amplitude_uv=line_noise_uv)
    return rec.build_uv()
