"""
Session-level multi-trial analysis engine.

Ingests a directory of .rhd (or .npz) recordings plus a session log CSV,
runs per-trial metrics (noise, bandpower, spectral slope, detection latency),
then aggregates group-level statistics with confidence intervals.

Designed to support the phantom validation protocol (phantom_protocol_v1.md).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import stats as sp_stats

# ── Local imports ──────────────────────────────────────────────────────
from neural_metrics import (
    noise_rms_uv,
    noise_rms_windowed,
    bandpower,
    spectral_slope,
    detect_perturbation,
)


# ── Utility: Wilson CI ─────────────────────────────────────────────────
def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Parameters
    ----------
    k : int
        Number of successes.
    n : int
        Number of trials.
    z : float
        Z-score for desired confidence level (default 1.96 → 95% CI).

    Returns
    -------
    (lower, upper) : tuple of float
        Bounds of the Wilson CI, clipped to [0, 1].
    """
    if n == 0:
        return (0.0, 0.0)
    p_hat = k / n
    denom = 1 + z**2 / n
    centre = (p_hat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom
    lo = max(0.0, centre - margin)
    hi = min(1.0, centre + margin)
    return (lo, hi)


# ── Data classes ───────────────────────────────────────────────────────
@dataclass
class TrialResult:
    """Per-trial analysis results."""
    # Identity
    session: int
    trial: int
    perturbation: str       # 'baseline', 'impedance', 'coupling', 'injection'
    level: str              # 'none', '0.3pct', '0.1pct', '1M', '100k', '10k', '10mV', '50mV', '100mV'
    filename: str

    # Noise
    rms_baseline_uv: float
    rms_perturbed_uv: float
    rms_ratio: float
    rms_recovery_uv: float

    # Bandpower
    bp_baseline: float
    bp_perturbed: float
    bp_ratio: float

    # Spectral slope
    slope_baseline: float
    slope_perturbed: float
    slope_shift: float

    # Detection
    detected: bool
    detection_latency_s: Optional[float]   # None if not detected

    # Crosstalk (optional, only for coupling trials)
    crosstalk_baseline_db: Optional[float] = None
    crosstalk_perturbed_db: Optional[float] = None

    # Per-channel RMS arrays (for detailed analysis)
    rms_per_channel_baseline: Optional[np.ndarray] = None
    rms_per_channel_perturbed: Optional[np.ndarray] = None

    # Quality flags
    clipping_detected: bool = False
    saturation_detected: bool = False
    epoch_too_short: bool = False

    # Temperature (from session log)
    temperature_c: Optional[float] = None
    notes: str = ''


@dataclass
class GroupStats:
    """Group-level statistics for a set of trials (one perturbation type + level)."""
    perturbation: str
    level: str
    n_trials: int

    # Detection
    detection_rate: float
    detection_ci_lo: float
    detection_ci_hi: float

    # Latency (median + IQR, only for detected trials)
    latency_median_s: Optional[float]
    latency_iqr_lo_s: Optional[float]
    latency_iqr_hi_s: Optional[float]

    # Paired test: baseline vs perturbed RMS
    wilcoxon_p: Optional[float]
    effect_size_r: Optional[float]

    # Mean RMS ratio
    rms_ratio_mean: float
    rms_ratio_std: float

    # Mean BP ratio
    bp_ratio_mean: float
    bp_ratio_std: float

    # Mean slope shift
    slope_shift_mean: float
    slope_shift_std: float


# ── Session Analyzer ───────────────────────────────────────────────────
class SessionAnalyzer:
    """Multi-trial analysis engine for phantom validation experiments.

    Usage
    -----
    >>> sa = SessionAnalyzer(onset_s=30.0, epoch_s=25.0)
    >>> sa.load_session_log('experiment/session_log.csv')
    >>> sa.analyze_all('data/raw/')
    >>> print(sa.summary())
    """

    def __init__(
        self,
        onset_s: float = 30.0,
        epoch_s: float = 25.0,
        z_threshold: float = 3.0,
        n_consecutive: int = 3,
        window_s: float = 1.0,
    ):
        """
        Parameters
        ----------
        onset_s : float
            Time of perturbation onset within each trial (seconds).
        epoch_s : float
            Duration of each epoch used for metrics (seconds).
            Baseline = [5, onset_s], Perturbed = [onset_s+5, onset_s+epoch_s+5].
        z_threshold : float
            Z-score threshold for detection.
        n_consecutive : int
            Number of consecutive windows above threshold for detection.
        window_s : float
            Window duration for time-series metrics.
        """
        self.onset_s = onset_s
        self.epoch_s = epoch_s
        self.z_threshold = z_threshold
        self.n_consecutive = n_consecutive
        self.window_s = window_s

        self.session_log: list[dict] = []
        self.results: list[TrialResult] = []

    # ── Session log ────────────────────────────────────────────────────
    def load_session_log(self, path: str | Path) -> None:
        """Load a session_log.csv file.

        Expected columns: session, trial, perturbation, level, filename
        Optional columns: temperature_c, notes
        """
        path = Path(path)
        with open(path, 'r') as f:
            reader = csv.DictReader(f)
            self.session_log = list(reader)

    # ── Single trial ───────────────────────────────────────────────────
    def load_trial(self, path: str | Path) -> dict:
        """Load a single trial recording.

        Supports .rhd and .npz formats.
        Returns dict with 'data' (n_channels, n_samples) and 'fs'.
        """
        path = Path(path)
        if path.suffix == '.rhd':
            from analysis.rhd_loader import load_rhd
            result = load_rhd(path)
            return {
                'data': result['amplifier_data_uv'],
                'fs': result['sample_rate'],
            }
        elif path.suffix == '.npz':
            npz = np.load(path)
            return {
                'data': npz['data'],
                'fs': float(npz['fs']),
            }
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

    def analyze_trial(
        self,
        data: np.ndarray,
        fs: float,
        session: int,
        trial: int,
        perturbation: str,
        level: str,
        filename: str = '',
        temperature_c: Optional[float] = None,
        notes: str = '',
    ) -> TrialResult:
        """Analyze a single trial.

        Parameters
        ----------
        data : ndarray, shape (n_channels, n_samples)
            Amplifier data in µV.
        fs : float
            Sample rate in Hz.
        session, trial : int
            Session and trial numbers.
        perturbation, level : str
            Perturbation type and level.
        filename : str
            Source filename for traceability.
        temperature_c : float, optional
            Temperature at time of recording.
        notes : str
            Any notes from session log.

        Returns
        -------
        TrialResult
        """
        n_channels, n_samples = data.shape
        total_duration = n_samples / fs

        # Define epoch boundaries (in samples)
        baseline_start = int(5 * fs)
        baseline_end = int(self.onset_s * fs)
        perturbed_start = int((self.onset_s + 5) * fs)
        perturbed_end = min(
            int((self.onset_s + 5 + self.epoch_s) * fs),
            n_samples
        )
        recovery_start = int((self.onset_s + 35) * fs) if self.onset_s + 35 < total_duration else perturbed_end
        recovery_end = min(
            int((self.onset_s + 60) * fs),
            n_samples
        )

        # Check for short epochs
        epoch_too_short = (baseline_end - baseline_start < int(5 * fs) or
                          perturbed_end - perturbed_start < int(5 * fs))

        # Extract epochs
        baseline_data = data[:, baseline_start:baseline_end]
        perturbed_data = data[:, perturbed_start:perturbed_end]
        recovery_data = data[:, recovery_start:recovery_end] if recovery_end > recovery_start else baseline_data

        # ── RMS noise ──────────────────────────────────────────────────
        rms_per_ch_bl = noise_rms_uv(baseline_data, fs, highpass_hz=1.0)
        rms_per_ch_pt = noise_rms_uv(perturbed_data, fs, highpass_hz=1.0)
        rms_per_ch_rc = noise_rms_uv(recovery_data, fs, highpass_hz=1.0)

        rms_bl = float(np.mean(rms_per_ch_bl))
        rms_pt = float(np.mean(rms_per_ch_pt))
        rms_rc = float(np.mean(rms_per_ch_rc))
        rms_ratio = rms_pt / rms_bl if rms_bl > 0 else float('inf')

        # ── Bandpower ──────────────────────────────────────────────────
        bp_bl_arr = bandpower(baseline_data, fs, band=(300, 3000))
        bp_pt_arr = bandpower(perturbed_data, fs, band=(300, 3000))
        bp_bl = float(np.mean(bp_bl_arr))
        bp_pt = float(np.mean(bp_pt_arr))
        bp_ratio = bp_pt / bp_bl if bp_bl > 0 else float('inf')

        # ── Spectral slope ─────────────────────────────────────────────
        slope_bl_arr = spectral_slope(baseline_data, fs)
        slope_pt_arr = spectral_slope(perturbed_data, fs)
        slope_bl = float(np.mean(slope_bl_arr))
        slope_pt = float(np.mean(slope_pt_arr))
        slope_shift = slope_pt - slope_bl

        # ── Detection latency ─────────────────────────────────────────
        # Build a windowed RMS timeseries (mean across channels) for detection
        win_samples = int(self.window_s * fs)
        n_windows = n_samples // win_samples
        rms_ts = np.zeros(n_windows)
        for w in range(n_windows):
            s0 = w * win_samples
            s1 = s0 + win_samples
            chunk = data[:, s0:s1]
            rms_ts[w] = float(np.mean(noise_rms_uv(chunk, fs, highpass_hz=None)))

        baseline_end_idx = int(self.onset_s / self.window_s)
        det_idx, det_z = detect_perturbation(
            rms_ts,
            baseline_end_idx=baseline_end_idx,
            sigma_threshold=self.z_threshold,
        )
        if det_idx is not None:
            detected = True
            det_latency = (det_idx - baseline_end_idx) * self.window_s
            if det_latency < 0:
                det_latency = 0.0
        else:
            detected = False
            det_latency = None

        # ── Quality flags ──────────────────────────────────────────────
        clipping = bool(np.any(np.abs(data) > 6000))  # ±6400 µV ADC range
        saturation = bool(np.any(np.abs(data) > 6300))

        return TrialResult(
            session=session,
            trial=trial,
            perturbation=perturbation,
            level=level,
            filename=filename,
            rms_baseline_uv=rms_bl,
            rms_perturbed_uv=rms_pt,
            rms_ratio=rms_ratio,
            rms_recovery_uv=rms_rc,
            bp_baseline=bp_bl,
            bp_perturbed=bp_pt,
            bp_ratio=bp_ratio,
            slope_baseline=slope_bl,
            slope_perturbed=slope_pt,
            slope_shift=slope_shift,
            detected=detected,
            detection_latency_s=det_latency,
            rms_per_channel_baseline=rms_per_ch_bl,
            rms_per_channel_perturbed=rms_per_ch_pt,
            clipping_detected=clipping,
            saturation_detected=saturation,
            epoch_too_short=epoch_too_short,
            temperature_c=temperature_c,
            notes=notes,
        )

    # ── Batch analysis ─────────────────────────────────────────────────
    def analyze_all(self, data_dir: str | Path) -> list[TrialResult]:
        """Analyze all trials listed in the session log.

        Parameters
        ----------
        data_dir : str or Path
            Directory containing .rhd or .npz files.

        Returns
        -------
        list[TrialResult]
        """
        data_dir = Path(data_dir)
        self.results = []

        for row in self.session_log:
            filename = row['filename']
            filepath = data_dir / filename

            if not filepath.exists():
                continue

            trial_data = self.load_trial(filepath)
            temp = None
            if row.get('temperature_c'):
                try:
                    temp = float(row['temperature_c'])
                except (ValueError, TypeError):
                    pass

            result = self.analyze_trial(
                data=trial_data['data'],
                fs=trial_data['fs'],
                session=int(row['session']),
                trial=int(row['trial']),
                perturbation=row['perturbation'],
                level=row['level'],
                filename=filename,
                temperature_c=temp,
                notes=row.get('notes', ''),
            )
            self.results.append(result)

        return self.results

    def analyze_synthetic(
        self,
        n_channels: int = 16,
        fs: float = 20000.0,
        duration_s: float = 100.0,
        noise_uv: float = 3.0,
        perturbation: str = 'impedance',
        level: str = '0.3pct',
        effect_multiplier: float = 2.0,
    ) -> TrialResult:
        """Generate and analyze a synthetic trial for testing.

        Parameters
        ----------
        n_channels : int
            Number of channels.
        fs : float
            Sample rate.
        duration_s : float
            Total duration.
        noise_uv : float
            Baseline noise RMS.
        perturbation : str
            Perturbation type label.
        level : str
            Perturbation level label.
        effect_multiplier : float
            How much louder the perturbed epoch is vs baseline.

        Returns
        -------
        TrialResult
        """
        rng = np.random.default_rng(42)
        n_samples = int(duration_s * fs)
        data = rng.normal(0, noise_uv, (n_channels, n_samples))

        # Add perturbation effect after onset
        onset_sample = int(self.onset_s * fs)
        data[:, onset_sample:] *= effect_multiplier

        return self.analyze_trial(
            data=data,
            fs=fs,
            session=0,
            trial=1,
            perturbation=perturbation,
            level=level,
            filename='synthetic',
        )

    # ── Group statistics ───────────────────────────────────────────────
    def get_baseline_recordings(self) -> list[TrialResult]:
        """Return all baseline (no-perturbation) trials."""
        return [r for r in self.results if r.perturbation == 'baseline']

    def get_trials_by_type(self, perturbation: str, level: str = '') -> list[TrialResult]:
        """Filter trials by perturbation type and optionally level."""
        trials = [r for r in self.results if r.perturbation == perturbation]
        if level:
            trials = [r for r in trials if r.level == level]
        return trials

    def get_detection_rates_by_level(self) -> dict[tuple[str, str], tuple[float, float, float]]:
        """Compute detection rate + Wilson CI for each (perturbation, level) group.

        Returns
        -------
        dict mapping (perturbation, level) → (rate, ci_lo, ci_hi)
        """
        groups: dict[tuple[str, str], list[bool]] = {}
        for r in self.results:
            key = (r.perturbation, r.level)
            groups.setdefault(key, []).append(r.detected)

        rates = {}
        for key, detected_list in groups.items():
            k = sum(detected_list)
            n = len(detected_list)
            rate = k / n if n > 0 else 0.0
            ci_lo, ci_hi = _wilson_ci(k, n)
            rates[key] = (rate, ci_lo, ci_hi)

        return rates

    def group_stats(self, perturbation: str, level: str) -> GroupStats:
        """Compute group-level statistics for a specific condition.

        Parameters
        ----------
        perturbation : str
            Perturbation type.
        level : str
            Perturbation level.

        Returns
        -------
        GroupStats
        """
        trials = self.get_trials_by_type(perturbation, level)
        n = len(trials)

        if n == 0:
            return GroupStats(
                perturbation=perturbation, level=level, n_trials=0,
                detection_rate=0.0, detection_ci_lo=0.0, detection_ci_hi=0.0,
                latency_median_s=None, latency_iqr_lo_s=None, latency_iqr_hi_s=None,
                wilcoxon_p=None, effect_size_r=None,
                rms_ratio_mean=0.0, rms_ratio_std=0.0,
                bp_ratio_mean=0.0, bp_ratio_std=0.0,
                slope_shift_mean=0.0, slope_shift_std=0.0,
            )

        # Detection rate + CI
        k = sum(1 for t in trials if t.detected)
        rate = k / n
        ci_lo, ci_hi = _wilson_ci(k, n)

        # Latency (detected trials only)
        latencies = [t.detection_latency_s for t in trials if t.detected and t.detection_latency_s is not None]
        if latencies:
            lat_median = float(np.median(latencies))
            lat_q25 = float(np.percentile(latencies, 25))
            lat_q75 = float(np.percentile(latencies, 75))
        else:
            lat_median = lat_q25 = lat_q75 = None

        # Wilcoxon signed-rank: baseline RMS vs perturbed RMS
        rms_bl = np.array([t.rms_baseline_uv for t in trials])
        rms_pt = np.array([t.rms_perturbed_uv for t in trials])
        if n >= 6 and not np.allclose(rms_bl, rms_pt):
            try:
                stat_result = sp_stats.wilcoxon(rms_bl, rms_pt, alternative='two-sided')
                wilcoxon_p = float(stat_result.pvalue)
                # Effect size r = Z / sqrt(N)
                # For Wilcoxon, Z ≈ (W - mean) / std under null
                z_val = sp_stats.norm.ppf(wilcoxon_p / 2)
                effect_r = abs(z_val) / math.sqrt(n)
            except Exception:
                wilcoxon_p = None
                effect_r = None
        else:
            wilcoxon_p = None
            effect_r = None

        # Aggregate ratios
        rms_ratios = [t.rms_ratio for t in trials]
        bp_ratios = [t.bp_ratio for t in trials]
        slope_shifts = [t.slope_shift for t in trials]

        return GroupStats(
            perturbation=perturbation,
            level=level,
            n_trials=n,
            detection_rate=rate,
            detection_ci_lo=ci_lo,
            detection_ci_hi=ci_hi,
            latency_median_s=lat_median,
            latency_iqr_lo_s=lat_q25,
            latency_iqr_hi_s=lat_q75,
            wilcoxon_p=wilcoxon_p,
            effect_size_r=effect_r,
            rms_ratio_mean=float(np.mean(rms_ratios)),
            rms_ratio_std=float(np.std(rms_ratios)),
            bp_ratio_mean=float(np.mean(bp_ratios)),
            bp_ratio_std=float(np.std(bp_ratios)),
            slope_shift_mean=float(np.mean(slope_shifts)),
            slope_shift_std=float(np.std(slope_shifts)),
        )

    # ── Summary ────────────────────────────────────────────────────────
    def summary(self) -> str:
        """Generate a human-readable summary of all results."""
        if not self.results:
            return "No results. Run analyze_all() first."

        lines = []
        lines.append(f"Session Analysis Summary ({len(self.results)} trials)")
        lines.append("=" * 60)

        # Overall detection
        n_detected = sum(1 for r in self.results if r.detected)
        n_total = len(self.results)
        rate = n_detected / n_total
        ci_lo, ci_hi = _wilson_ci(n_detected, n_total)
        lines.append(f"\nOverall detection rate: {rate:.1%} "
                     f"(95% CI: {ci_lo:.1%}–{ci_hi:.1%}), "
                     f"N={n_total}")

        # False positive rate (baseline trials)
        baselines = self.get_baseline_recordings()
        if baselines:
            n_fp = sum(1 for r in baselines if r.detected)
            fp_rate = n_fp / len(baselines)
            lines.append(f"False positive rate (baseline): {fp_rate:.1%} "
                        f"({n_fp}/{len(baselines)})")

        # Per-type breakdown
        perturbation_types = sorted(set(
            (r.perturbation, r.level) for r in self.results
        ))

        lines.append(f"\n{'Perturbation':<15} {'Level':<10} {'N':>3} "
                     f"{'Det%':>6} {'95%CI':>12} {'Lat(s)':>8} {'RMS×':>6}")
        lines.append("-" * 65)

        for ptype, plevel in perturbation_types:
            gs = self.group_stats(ptype, plevel)
            lat_str = f"{gs.latency_median_s:.1f}" if gs.latency_median_s is not None else "N/A"
            lines.append(
                f"{gs.perturbation:<15} {gs.level:<10} {gs.n_trials:>3} "
                f"{gs.detection_rate:>5.0%} "
                f"[{gs.detection_ci_lo:.0%}–{gs.detection_ci_hi:.0%}]"
                f"{lat_str:>7}  "
                f"{gs.rms_ratio_mean:>5.2f}"
            )

        # Quality flags
        n_clip = sum(1 for r in self.results if r.clipping_detected)
        n_sat = sum(1 for r in self.results if r.saturation_detected)
        n_short = sum(1 for r in self.results if r.epoch_too_short)
        if n_clip or n_sat or n_short:
            lines.append(f"\n⚠ Quality flags: {n_clip} clipping, "
                        f"{n_sat} saturation, {n_short} short epochs")

        return '\n'.join(lines)

    # ── Export ─────────────────────────────────────────────────────────
    def export_results(self, path: str | Path) -> None:
        """Export per-trial results to CSV.

        Parameters
        ----------
        path : str or Path
            Output CSV path.
        """
        if not self.results:
            raise ValueError("No results to export")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Get field names (exclude ndarray fields)
        exclude = {'rms_per_channel_baseline', 'rms_per_channel_perturbed'}
        fieldnames = [f for f in TrialResult.__dataclass_fields__ if f not in exclude]

        with open(path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.results:
                row = {k: v for k, v in asdict(r).items() if k not in exclude}
                # Convert numpy types to native Python
                for k, v in row.items():
                    if isinstance(v, (np.floating, np.integer)):
                        row[k] = float(v) if isinstance(v, np.floating) else int(v)
                writer.writerow(row)
