# BioGENEius Neural Signal Analysis Pipeline

## Model Details

| Field | Value |
|-------|-------|
| **Name** | BioGENEius Computational Pipeline v1 |
| **Version** | 1.0.0 |
| **Architecture** | Two-stage: Classical (CUSUM/RF) + Deep (NeuralQANet) |
| **Parameters** | 76,045 (Deep model) |
| **Framework** | PyTorch (CPU-only, no GPU required) |
| **License** | Research use only |

## Framing

> Artifact-resilient detection of suppression-like electrophysiological
> perturbation signatures under a low-cost phantom protocol.

**Key constraint:** We never claim neurons until we have biology.

## Intended Use

Detection and classification of electrophysiological perturbation signatures
in multichannel time-series data acquired from a low-cost phantom system.
Designed for competition demonstration and research prototyping.

### Primary Use Cases
- Detecting onset of suppression-like perturbations in phantom recordings
- Classifying perturbation cause (9 synthetic proxy classes)
- Quantifying detection latency and classification confidence
- Generating reproducible evaluation reports

### Out of Scope
- Clinical diagnosis or treatment decisions
- Real-time closed-loop neural interfaces
- Claims about biological neurotoxicity from proxy labels

## Pipeline Architecture

```
Acquire (frames → ADC → µV)
  ↓
QC / Artifact Segmentation (ML mask + quality score)
  ↓
Detect Change (CUSUM or z-score baseline comparison)
  ↓
Classify Cause (RF vs Deep model, 9-class + binary)
  ↓
Report (figures + latency + CI + confidence + "unknown" option)
```

### Stage 1 — Classical Detection
- **CUSUM** change-point detection on channel-averaged RMS
- **Z-score** alternative with sliding baseline window
- Outputs: detected (bool), detection latency (seconds), CUSUM statistic

### Stage 2 — Classical Classification
- **Random Forest** on 20 handcrafted features (RMS ratio, spectral entropy, band powers, etc.)
- Binary (perturbation vs baseline) + 9-class cause classification
- Wilson confidence intervals on all accuracy metrics

### Stage 3 — Deep Classification (NeuralQANet)
- 1D CNN backbone → attention pooling → 3 heads:
  - Artifact mask (binary per-sample)
  - Cause classification (9-class)
  - Severity regression (continuous 0–1)
- Self-supervised pretraining: masked reconstruction + NT-Xent contrastive loss
- 7 domain-randomization augmentations: 60Hz injection, channel dropout, gain jitter, coupling, drift, clipping, time mask
- Temperature-calibrated output probabilities

## Proxy Labels — What We Claim and What We Don't

All perturbation classes are **proxy signatures†**, not validated biological effects:

| Class | Proxy Signature | What It Actually Is |
|-------|-----------------|---------------------|
| Spike Suppression† | Reduced spike amplitude/rate | Synthetic amplitude modulation |
| Burst Collapse† | Loss of burst patterns | Synthetic burst removal |
| Spectral Shift† | Changed power spectrum | Synthetic PSD warping |
| Mixed Suppression† | Combined signatures | Multiple synthetic effects |
| Amplitude Modulation† | Gain changes | Synthetic scaling |
| Frequency Drift† | Center frequency shift | Synthetic frequency modulation |
| Baseline Drift† | DC offset wander | Synthetic drift injection |
| Coupling Change† | Inter-channel correlation shift | Synthetic coupling matrix |
| Artifact Injection† | Non-physiological transient | Synthetic artifact waveforms |

**†** = proxy label. Biological validation requires phantom recordings
with known perturbation agents under controlled conditions.

## Training Data

- **Source**: `analysis/perturbation_library.py` — deterministic synthetic generators
- **Classes**: 9 perturbation types + baseline
- **Severities**: 3 levels (0.3, 0.6, 0.9)
- **Channels**: 16 (configurable)
- **Sample rate**: 20 kHz (configurable)
- **Augmentations**: 7 domain-randomization transforms

**No real biological data was used for training or evaluation.**

## Evaluation Protocol

| Figure | What It Tests |
|--------|--------------|
| Fig 1 | Onset detection latency + false positive rate |
| Fig 2 | Cause classification confusion matrix + ROC |
| Fig 3 | Robustness under noise and artifact stress |
| Fig 4 | Ablation — why two-stage matters |
| Fig 5 | 3-way comparison: Classical vs RF vs Deep |
| Fig 6 | Stress grid: SNR × severity (where Deep beats Classical) |
| Fig 7 | Domain shift: train A / test B generalization |

### ML Earns Its Keep
- **Random noise floor**: ML does NOT beat physics. PCB shielding + analog design does.
- **Structured artifacts**: ML DOES help with 60Hz, coupling, drift, and other patterned contamination.
- Deep model must beat RF baseline to be included; RF must beat CUSUM to be included.

## Known Limitations

1. **Synthetic only** — All training/evaluation on synthetic perturbation library. No real data yet.
2. **Domain shift** — Performance may degrade when real phantom data differs from synthetic assumptions.
3. **Small model** — 76K params is deliberately small for CPU inference; may under-fit complex patterns.
4. **Temperature calibration** — Calibrated on synthetic validation set; re-calibrate on real data.
5. **Proxy labels** — No biological ground truth. Labels describe electrical signatures, not mechanisms.
6. **Single phantom protocol** — Validated only against BioGENEius Phantom Protocol v2.

## "Unknown" Behavior

When the model encounters data that doesn't match any trained class:
- Top-class probability drops below 0.3
- System flags as "low-confidence" in predictions.csv
- **Intended behavior: don't make a call when you don't know**

## Reproducibility

```bash
# One command. Generates everything.
python analysis/run_pipeline.py --source synthetic --seed 42

# Outputs to out/run_YYYYMMDD_HHMMSS/:
#   fig1–fig7 (PNG), predictions.csv, metrics.json, model_card.md
```

All random state is seeded. Same seed → same results.

## Ethical Considerations

- This system processes synthetic electrical signals, not human data
- No patient data, no clinical decisions, no diagnostic claims
- Proxy labels are clearly marked to prevent over-interpretation
- "Unknown" class prevents forced classification of novel patterns
- All code and evaluation are fully reproducible and auditable

## Citation

If referencing this work, please cite the BioGENEius competition entry
and note that all results are from synthetic simulation studies.
