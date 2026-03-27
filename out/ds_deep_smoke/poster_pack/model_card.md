# Model Card — Domain Shift Suite v2

## Overview
Systematic evaluation of detection and classification robustness
under 13 domain-shift conditions × 3 severities × 2 noise regimes.
Recording-level split enforcement prevents window-level leakage.

- **Config hash**: `7bed58df5159`
- **Timestamp**: 2026-03-04T02:44:46.705608+00:00
- **Training mode**: demo
- **Data source**: synthetic

## Domain Shift Summary (Mean AUROC)

| Model | Domain A | Domain B | Drop |
|-------|----------|----------|------|
| CUSUM | 0.000 | 0.000 | +0.000 |
| RF | 0.000 | 0.000 | +0.000 |
| Deep | 0.556 | 0.597 | -0.042 |

## Shift Conditions

- **60 Hz Interference** (`sixty_hz`)
- **EMI-Like Burst** (`emi_burst`)
- **DC Drift / Wander** (`dc_drift`)
- **ADC Clipping** (`adc_clipping`)
- **Channel Dropout** (`channel_dropout`)
- **Coupling Shift** (`coupling_shift`)
- **Gain Mismatch** (`gain_mismatch`)
- **Partial Suppression†** (`partial_suppression`)
- **Rate Decrease†** (`rate_decrease`)
- **Spike Jitter†** (`spike_jitter`)
- **Bandpass Mismatch** (`bandpass_mismatch`)
- **Sample-Rate Mismatch** (`sample_rate_mismatch`)
- **Quantization (Cheap ADC)** (`quantization_change`)

## Metrics Reported
- FPR @ TPR=0.9 (detection specificity)
- AUROC (binary classification)
- Latency median + IQR (detection speed)
- ECE (calibration error)
- Holdout accuracy (unseen perturbation types)

## Known Limitations
- All data is synthetic (perturbation library generators)
- Domain B applies artificial corruptions, not real-world recordings
- Holdout type is a single blend (partial_suppression_holdout)
- Deep model performance limited by small dataset size in demo mode
- ECE computed with 10 bins; may be noisy at small sample counts

## Proxy Language
All "neurotox-like" labels are proxy signatures (†).
No claim of biological neurotoxicity detection.
Results are from a simulation study on synthetic data.

## Artifacts Produced
- `domain_shift_results.json` — Full metrics + CI + config hash
- `fig_domain_shift.png` — Performance drop A→B visualization
- `table_domain_shift.csv` — Model × shift × severity table
- `model_card.md` — This file
