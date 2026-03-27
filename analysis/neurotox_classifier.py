"""
neurotox_classifier.py — ML pipeline for neurotoxicity perturbation classification.

Extracts electrophysiological features from multichannel neural recordings
and trains a classifier to distinguish:
  - Control (baseline)
  - Mild perturbation
  - Severe perturbation

across four perturbation models:
  1. Amplitude suppression (synaptic depression)
  2. Noise elevation (ion channel disruption)
  3. Burst fragmentation (Na+ channel block)
  4. Firing rate suppression (GABAergic inhibition)

Feature set per trial window:
  - RMS noise (mean + std across channels)
  - Spike-band power 300–3000 Hz (mean)
  - High-gamma power 80–300 Hz (mean)
  - Spectral slope (1/f exponent)
  - Spike rate (threshold crossings/s)
  - Waveform kurtosis (shape metric)

Classifier: scikit-learn RandomForest with stratified cross-validation.
Outputs: AUROC, confusion matrix, feature importance.

Design: stateless functions + one pipeline class. No file I/O in core logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy import signal as sig

from neural_metrics import (
    noise_rms_uv,
    bandpower,
    spectral_slope,
    spike_rate,
)
from signal_gen import SyntheticRecording


# ── Feature extraction ───────────────────────────────────────────────

@dataclass
class TrialFeatures:
    """Feature vector for one trial window."""
    rms_mean: float
    rms_std: float
    spike_band_power: float     # 300–3000 Hz
    high_gamma_power: float     # 80–300 Hz
    spectral_slope_val: float   # 1/f exponent
    spike_rate_hz: float        # threshold crossings / s
    kurtosis: float             # waveform shape
    label: str = ""             # "control", "mild", "severe"
    perturbation_type: str = "" # "amplitude", "noise", "burst", "firing_rate"

    def to_array(self) -> NDArray[np.float64]:
        """Return feature vector as numpy array (7 features)."""
        return np.array([
            self.rms_mean,
            self.rms_std,
            self.spike_band_power,
            self.high_gamma_power,
            self.spectral_slope_val,
            self.spike_rate_hz,
            self.kurtosis,
        ], dtype=np.float64)


FEATURE_NAMES = [
    "rms_mean_uv",
    "rms_std_uv",
    "spike_band_power",
    "high_gamma_power",
    "spectral_slope",
    "spike_rate_hz",
    "kurtosis",
]


def extract_features(
    data_uv: NDArray[np.float64],
    fs: float = 20_000.0,
    label: str = "",
    perturbation_type: str = "",
) -> TrialFeatures:
    """Extract feature vector from a multichannel data window.

    Args:
        data_uv: (n_channels, n_samples) in µV
        fs: sample rate in Hz
        label: class label for this trial
        perturbation_type: perturbation model name

    Returns:
        TrialFeatures with all 7 features computed.
    """
    # RMS noise per channel
    rms = noise_rms_uv(data_uv, fs=fs, highpass_hz=1.0)
    rms_mean = float(np.mean(rms))
    rms_std = float(np.std(rms))

    # Spike-band power (300–3000 Hz) averaged across channels
    bp_spike = bandpower(data_uv, fs=fs, band=(300.0, 3000.0))
    spike_band_power = float(np.mean(bp_spike))

    # High-gamma power (80–300 Hz)
    bp_gamma = bandpower(data_uv, fs=fs, band=(80.0, 300.0))
    high_gamma_power = float(np.mean(bp_gamma))

    # Spectral slope (1/f exponent) averaged across channels
    slopes = spectral_slope(data_uv, fs=fs)
    slope_val = float(np.mean(slopes))

    # Spike rate averaged across channels
    rates = []
    for ch in range(data_uv.shape[0]):
        r = spike_rate(data_uv[ch:ch+1, :], fs=fs)
        rates.append(float(r[0]))
    rate_mean = float(np.mean(rates))

    # Kurtosis (waveform shape, averaged across channels)
    from scipy.stats import kurtosis as scipy_kurtosis
    kurt_vals = scipy_kurtosis(data_uv, axis=1, fisher=True)
    kurt_mean = float(np.mean(kurt_vals))

    return TrialFeatures(
        rms_mean=rms_mean,
        rms_std=rms_std,
        spike_band_power=spike_band_power,
        high_gamma_power=high_gamma_power,
        spectral_slope_val=slope_val,
        spike_rate_hz=rate_mean,
        kurtosis=kurt_mean,
        label=label,
        perturbation_type=perturbation_type,
    )


# ── Dataset generation ───────────────────────────────────────────────

# Severity parameters for each perturbation model
PERTURBATION_PARAMS = {
    "amplitude": {
        "mild":   {"suppression_frac": 0.2},
        "severe": {"suppression_frac": 0.5},
    },
    "noise": {
        "mild":   {"noise_increase_uv": 3.0},
        "severe": {"noise_increase_uv": 8.0},
    },
    "burst": {
        "mild":   {"dropout_prob": 0.15},
        "severe": {"dropout_prob": 0.45},
    },
    "firing_rate": {
        "mild":   {"suppression_frac": 0.3},
        "severe": {"suppression_frac": 0.7},
    },
}


def generate_dataset(
    n_trials_per_class: int = 30,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 5.0,
    onset_s: float = 1.0,
    baseline_noise_uv: float = 2.4,
    spike_rate_hz: float = 8.0,
    spike_amplitude_uv: float = -200.0,
    seed: int = 42,
) -> tuple[NDArray[np.float64], NDArray[np.int64], list[str], list[str]]:
    """Generate a labeled dataset of trial features.

    Creates control and perturbed trials across all 4 perturbation types
    and 2 severity levels.

    Returns:
        X: (n_trials, 7) feature matrix
        y: (n_trials,) integer labels: 0=control, 1=mild, 2=severe
        label_names: string labels per trial
        perturbation_types: perturbation type per trial
    """
    rng = np.random.default_rng(seed)
    features_list: list[NDArray] = []
    labels: list[int] = []
    label_names: list[str] = []
    perturbation_types: list[str] = []

    trial_seed = 0

    # Control trials
    for i in range(n_trials_per_class):
        trial_seed += 1
        rec = SyntheticRecording(
            n_channels=n_channels, fs=fs, duration_s=duration_s, seed=trial_seed
        )
        rec.add_noise(rms_uv=baseline_noise_uv)
        for ch in range(min(n_channels, 8)):
            rec.add_spikes(ch, rate_hz=spike_rate_hz + rng.normal(0, 1),
                          amplitude_uv=spike_amplitude_uv + rng.normal(0, 20))

        data = rec.build_uv()
        # Extract features from post-onset window (same as perturbed trials)
        onset_idx = int(onset_s * fs)
        window = data[:, onset_idx:]

        feat = extract_features(window, fs=fs, label="control", perturbation_type="none")
        features_list.append(feat.to_array())
        labels.append(0)
        label_names.append("control")
        perturbation_types.append("none")

    # Perturbed trials
    for ptype, params in PERTURBATION_PARAMS.items():
        for severity, kwargs in params.items():
            severity_label = 1 if severity == "mild" else 2
            for i in range(n_trials_per_class):
                trial_seed += 1
                rec = SyntheticRecording(
                    n_channels=n_channels, fs=fs, duration_s=duration_s, seed=trial_seed
                )
                rec.add_noise(rms_uv=baseline_noise_uv)
                for ch in range(min(n_channels, 8)):
                    rec.add_spikes(ch, rate_hz=spike_rate_hz + rng.normal(0, 1),
                                  amplitude_uv=spike_amplitude_uv + rng.normal(0, 20))

                # Apply perturbation
                if ptype == "amplitude":
                    rec.add_amplitude_suppression(onset_s=onset_s, **kwargs)
                elif ptype == "noise":
                    rec.add_noise_elevation(onset_s=onset_s, **kwargs)
                elif ptype == "burst":
                    rec.add_burst_fragmentation(onset_s=onset_s, **kwargs)
                elif ptype == "firing_rate":
                    rec.add_firing_rate_suppression(onset_s=onset_s, **kwargs)

                data = rec.build_uv()
                onset_idx = int(onset_s * fs)
                window = data[:, onset_idx:]

                feat = extract_features(
                    window, fs=fs,
                    label=severity,
                    perturbation_type=ptype,
                )
                features_list.append(feat.to_array())
                labels.append(severity_label)
                label_names.append(severity)
                perturbation_types.append(ptype)

    X = np.array(features_list, dtype=np.float64)
    y = np.array(labels, dtype=np.int64)
    return X, y, label_names, perturbation_types


# ── Classification pipeline ──────────────────────────────────────────

@dataclass
class ClassificationResult:
    """Results from a classification run."""
    accuracy: float
    auroc_macro: float
    auroc_per_class: dict[str, float]
    confusion_matrix: NDArray[np.int64]
    class_names: list[str]
    feature_importance: NDArray[np.float64]
    feature_names: list[str]
    cv_scores: NDArray[np.float64]
    n_trials: int
    n_features: int


def train_and_evaluate(
    X: NDArray[np.float64],
    y: NDArray[np.int64],
    n_cv_folds: int = 5,
    seed: int = 42,
) -> ClassificationResult:
    """Train RandomForest classifier with stratified cross-validation.

    Args:
        X: (n_trials, n_features) feature matrix
        y: (n_trials,) integer labels (0=control, 1=mild, 2=severe)
        n_cv_folds: number of cross-validation folds
        seed: random seed for reproducibility

    Returns:
        ClassificationResult with AUROC, confusion matrix, importances.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from sklearn.metrics import (
        confusion_matrix,
        roc_auc_score,
        accuracy_score,
    )
    from sklearn.preprocessing import StandardScaler, label_binarize

    # Normalize features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    class_names = ["control", "mild", "severe"]
    classes = np.array([0, 1, 2])

    # Cross-validation scores
    clf = RandomForestClassifier(
        n_estimators=100,
        max_depth=8,
        min_samples_leaf=3,
        random_state=seed,
        class_weight="balanced",
    )
    cv = StratifiedKFold(n_splits=n_cv_folds, shuffle=True, random_state=seed)
    cv_scores = cross_val_score(clf, X_scaled, y, cv=cv, scoring="accuracy")

    # Fit on full data for confusion matrix and feature importance
    clf.fit(X_scaled, y)
    y_pred = clf.predict(X_scaled)
    y_proba = clf.predict_proba(X_scaled)

    # Metrics
    acc = accuracy_score(y, y_pred)
    cm = confusion_matrix(y, y_pred, labels=[0, 1, 2])

    # AUROC (one-vs-rest, macro)
    y_bin = label_binarize(y, classes=classes)
    auroc_macro = roc_auc_score(y_bin, y_proba, multi_class="ovr", average="macro")

    # Per-class AUROC
    auroc_per_class = {}
    for i, name in enumerate(class_names):
        if y_bin[:, i].sum() > 0 and y_bin[:, i].sum() < len(y):
            auroc_per_class[name] = roc_auc_score(y_bin[:, i], y_proba[:, i])
        else:
            auroc_per_class[name] = float("nan")

    return ClassificationResult(
        accuracy=acc,
        auroc_macro=auroc_macro,
        auroc_per_class=auroc_per_class,
        confusion_matrix=cm,
        class_names=class_names,
        feature_importance=clf.feature_importances_,
        feature_names=FEATURE_NAMES,
        cv_scores=cv_scores,
        n_trials=len(y),
        n_features=X.shape[1],
    )


# ── Convenience: full pipeline ───────────────────────────────────────

def run_full_pipeline(
    n_trials_per_class: int = 30,
    seed: int = 42,
) -> ClassificationResult:
    """Generate dataset and run classification in one call."""
    X, y, _, _ = generate_dataset(n_trials_per_class=n_trials_per_class, seed=seed)
    return train_and_evaluate(X, y, seed=seed)
