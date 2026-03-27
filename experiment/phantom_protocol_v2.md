# Phantom Validation Protocol v2.0

**Project:** BCIInterface — 14-Channel Electrophysiology Platform  
**Date:** 2026-03-02 (supersedes v1.0 2026-03-01)  
**PI:** A. Harshi  
**Status:** LOCKED — aligns with two-stage classifier labels  
**Runtime contract:** `experiment/device_runtime_contract_v1.md`

---

## 1. Scientific Claim (Revised)

> "A low-cost neural acquisition + computational QA stack that detects
> and classifies electrophysiological perturbations and separates
> 'instrument artifacts' from 'suppression-like signatures†', enabling
> scalable screening / validation workflows."

**†Proxy class disclaimer:** "Suppression-like" refers to signal
patterns (amplitude reduction, burst fragmentation, spectral
redistribution) that *resemble* biological suppression but are
produced here under phantom (non-biological) conditions. No
neurons are present. This is a platform validation study, not
a neurotoxicity claim.

---

## 2. Classification Label Alignment

Phantom trials are designed to produce signals that the two-stage
classifier will categorize. Every trial maps to a **training label**.

### 2.1 Artifact Trials (Stage 2 label: "Artifact")

These are instrumentation failure modes the system must classify
correctly as *not* biological.

| Trial type | Phantom method | Classifier label | Generator match |
|------------|---------------|-----------------|-----------------|
| **A1: Impedance drift** | Swap saline concentration (0.9% → 0.3% → 0.1%) | `impedance_drift` | `generate_impedance_drift()` |
| **A2: Broadband noise** | Inject white noise via function generator (10/50/100 mVpp through 1 MΩ) | `broadband_noise` | `generate_broadband_noise()` |
| **A3: Line interference** | Remove Faraday cage lid during recording (60 Hz pickup) | `line_interference` | `generate_line_interference()` |
| **A4: Crosstalk / coupling** | Bridge two electrodes with resistor (1MΩ / 100kΩ / 10kΩ) | `crosstalk_coupling` | `generate_crosstalk()` |

### 2.2 Suppression-Like† Proxy Trials (Stage 2 label: "Suppression-Like†")

These trials produce signal patterns that *look like* biological
suppression but are created by physical manipulation of the phantom.

| Trial type | Phantom method | Signal effect | Classifier label | Generator match |
|------------|---------------|---------------|-----------------|-----------------|
| **S1: Amplitude suppression proxy** | Increase electrode impedance by partially lifting electrodes from saline (raise 2mm) | Amplitude ↓ without noise ↑ (mimics `spike_suppression`) | `spike_suppression` | `generate_spike_suppression()` |
| **S2: Burst collapse proxy** | Inject pulse train (5 Hz, 1ms pulses) then suddenly stop at t=onset | Loss of periodic structure (mimics `burst_collapse`) | `burst_collapse` | `generate_burst_collapse()` |
| **S3: Spectral shift proxy** | Add low-pass RC filter inline (10 kΩ + 1 nF → fc ≈ 16 kHz, then 10 nF → fc ≈ 1.6 kHz) | High-frequency power ↓, spectral slope change | `spectral_shift` | `generate_spectral_shift()` |
| **S4: Mixed suppression proxy** | Combine S1 + S2: partial electrode lift + stop pulse train | Amplitude ↓ + periodic structure loss + mild spectral change | `mixed_neurotox` | `generate_mixed_neurotox()` |

### 2.3 Baseline Trials

| Trial type | Phantom method | Expected result |
|------------|---------------|-----------------|
| **B: Baseline** | No manipulation; electrodes in 0.9% saline, Faraday cage closed | No detection (Stage 1 silent), FP rate measurement |

### 2.4 Adversarial Trials (Stress Tests)

These intentionally combine artifact + suppression-like signatures to
test classifier robustness under ambiguous conditions.

| Trial type | Phantom method | Expected challenge |
|------------|---------------|-------------------|
| **X1: Drift + coupling** | Swap saline + bridge electrodes simultaneously | Compound artifact; should still classify as artifact |
| **X2: Suppression + 60 Hz** | Partial electrode lift + remove cage lid | Suppression-like signal with artifact overlay; should classify as suppression-like |

---

## 3. Trial Matrix

### 3.1 Session Structure

Each trial follows the standard epoch structure from the runtime contract:

```
|← 30s baseline →|← ~10s transition →|← 30s perturbed →|← 30s recovery →|
     epoch 1              (mark)            epoch 2           epoch 3
```

Total per trial: **100 seconds**. Mark exact onset time to nearest second.

### 3.2 Session Schedule

| Session | Type | Conditions | Levels | Trials/level | Total trials |
|---------|------|-----------|--------|-------------|-------------|
| 0 | Baseline (B) | 1 | — | 5 | **5** |
| 1 | Artifact A1 (impedance) | 1 | 3 (0.3%, 0.1%, dry) | 3 each | **9** |
| 2 | Artifact A2 (noise) | 1 | 3 (10, 50, 100 mVpp) | 3 each | **9** |
| 3 | Artifact A3 (line) | 1 | 2 (partial open, full open) | 3 each | **6** |
| 4 | Artifact A4 (coupling) | 1 | 3 (1MΩ, 100kΩ, 10kΩ) | 3 each | **9** |
| 5 | Suppression S1 (amplitude) | 1 | 3 (1mm, 2mm, 3mm lift) | 3 each | **9** |
| 6 | Suppression S2 (burst) | 1 | 2 (slow stop, abrupt stop) | 3 each | **6** |
| 7 | Suppression S3 (spectral) | 1 | 2 (1.6kHz, 500Hz cutoff) | 3 each | **6** |
| 8 | Suppression S4 (mixed) | 1 | 2 (mild, severe) | 3 each | **6** |
| 9 | Adversarial X1, X2 | 2 | 1 each | 3 each | **6** |
| 10 | Replication | Best 3 conditions from S1–S4 | 3 | 3 each | **9** |
| | | | | **Total** | **80** |

**Total recording time:** 80 trials × 100s = ~2.2 hours of data.

### 3.3 Randomization

Within each session, randomize trial order using pre-generated seed:

```bash
python experiment/generate_trial_order.py --seed 42 --protocol v2
```

---

## 4. Equipment

| Item | Role | Settings |
|------|------|----------|
| Intan RHD2000 USB interface board | SPI host + ADC readout | 20 kS/s, 14 channels |
| AFE Headstage v1 PCB | DUT | 3.3V via Intan board |
| Bench DC power supply | Backup power verify | 3.3V, 50 mA limit |
| Function generator | Perturbation A2, S2 | White noise / pulse train |
| Oscilloscope | Signal verification | ≥ 20 MHz BW |
| Multimeter | Impedance / resistance | Ω mode |
| Resistors (1MΩ, 100kΩ, 10kΩ) | Coupling bridges (A4) | Breadboard-mounted |
| RC filter (10kΩ + 1nF / 10nF) | Spectral shift (S3) | Inline with electrode |
| 3 saline solutions (0.9%, 0.3%, 0.1%) | Impedance levels | Distilled water + NaCl |
| Wire electrode array (14-wire) | Phantom electrodes | 2×7 grid, 3mm spacing |
| Faraday cage (aluminum foil) | Shielding control | Removable lid for A3 |
| Micrometer stage or ruler | Electrode lift (S1) | mm-precision lift |

---

## 5. Recording Procedure (Per Trial)

1. Verify Faraday cage closed (unless A3).
2. Record temperature (±0.5°C).
3. Start Intan recording: 20 kS/s, 14 channels, `.rhd` output.
4. Wait **30 seconds** (baseline epoch).
5. **Apply perturbation** — log exact time in session spreadsheet.
6. Wait **30 seconds** (perturbed epoch).
7. Remove perturbation / restore baseline.
8. Wait **30 seconds** (recovery epoch).
9. Stop recording. Total ~100s.
10. Save: `S{session}_T{trial}_{type}_{level}.rhd`
11. Log trial in `experiment/session_log_v2.csv`.

---

## 6. File Naming Convention

```
data/
  raw/
    S00_T01_baseline_none.rhd
    S01_T01_impedance_0.3pct.rhd
    S01_T02_impedance_0.1pct.rhd
    S02_T01_noise_10mV.rhd
    ...
    S05_T01_suppression_1mm.rhd
    ...
    S09_T01_adversarial_drift_coupling.rhd
    S09_T04_adversarial_suppression_60hz.rhd
    ...
  processed/
    S01_T01_impedance_0.3pct.npz
    ...
```

---

## 7. Analysis Pipeline

### 7.1 Offline Processing

```bash
# Convert all .rhd → .npz
python analysis/rhd_loader.py --input data/raw/ --output data/processed/

# Run session analyzer
python analysis/session_analyzer.py \
    --input data/processed/ \
    --log experiment/session_log_v2.csv \
    --onset 30 \
    --output results/session_results.csv

# Generate competition figures
python analysis/eval_report.py \
    --out out/figures/ \
    --seed 42 \
    --dpi 300
```

### 7.2 Validation Against Phantom Data

For each trial, the analysis pipeline:

1. **Stage 1 (CUSUM):** Detects onset of perturbation.
   - Compare detected onset vs. logged onset → latency.
   - FP rate measured on baseline trials.

2. **Stage 2 (RandomForest):** Classifies detected perturbation.
   - Compare predicted label vs. ground-truth trial type.
   - Binary: artifact vs. suppression-like.
   - Multi-class: specific perturbation type.

3. **Concordance:** Compare classifier output (trained on synthetic data)
   against actual phantom trial labels.
   - This is the key result: *does the model trained on synthetic
     generators actually work on real hardware recordings?*

---

## 8. Primary Endpoints

### 8.1 Detection (Stage 1)

| Metric | Target | Measurement |
|--------|--------|-------------|
| True positive rate | ≥ 90% | Across all perturbation trials |
| False positive rate | ≤ 5% | Baseline trials only |
| Detection latency | ≤ 5 seconds (median) | Time from true onset to CUSUM alarm |
| Detection latency IQR | ≤ 3 seconds | Consistency measure |

### 8.2 Classification (Stage 2)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Binary accuracy (artifact vs suppression-like) | ≥ 80% | Leave-one-out CV on phantom data |
| Binary AUROC | ≥ 0.85 | — |
| Concordance: synthetic → phantom | ≥ 70% | Train on synthetic, test on phantom |

### 8.3 Adversarial Robustness

| Metric | Target |
|--------|--------|
| Correct classification on X1 (drift+coupling) | ≥ 2/3 trials classified as "artifact" |
| Correct classification on X2 (suppression+60Hz) | ≥ 2/3 trials classified as "suppression-like" |

---

## 9. Statistical Analysis Plan

### 9.1 Per-Trial

- `rms_baseline`: mean RMS over baseline epoch (14 channels → scalar)
- `rms_perturbed`: mean RMS over perturbed epoch
- `rms_ratio`: perturbed / baseline
- `detection_latency_s`: onset to CUSUM alarm (NaN if missed)
- `detected`: boolean
- `binary_prediction`: "artifact" or "suppression-like"
- `multi_prediction`: specific perturbation type

### 9.2 Group-Level

1. **Detection rate:** proportion ± 95% Wilson CI per perturbation type.
2. **Latency:** median ± IQR per type.
3. **Effect size:** Wilcoxon signed-rank (baseline vs perturbed RMS).
4. **Concordance:** confusion matrix (phantom label vs classifier prediction).
5. **Adversarial:** proportion correctly classified ± exact binomial CI.

### 9.3 Multiple Comparisons

- 8 perturbation types + 2 adversarial = 10 comparisons.
- Bonferroni-corrected α = 0.05 / 10 = 0.005.

---

## 10. Footnote Policy

All user-facing outputs (figures, tables, text) must include:

> **†** Proxy label. "Suppression-like" describes signal patterns
> (amplitude reduction, burst fragmentation, spectral redistribution)
> produced under phantom conditions. No neurons are present. This
> is a platform validation study.

This footnote appears on:
- All evaluation figures (watermark: "SIMULATION STUDY")
- Confusion matrix axis labels
- Results tables
- Abstract / poster text

---

## 11. Success Criteria Summary

| Gate | Criterion | Verdict |
|------|-----------|---------|
| **GO** | TP ≥ 90%, FP ≤ 5%, latency ≤ 5s, binary accuracy ≥ 80% | Proceed to write-up |
| **PARTIAL** | TP ≥ 70% OR binary accuracy ≥ 70% | Report with limitations |
| **NO-GO** | TP < 70% AND accuracy < 70% | Debug hardware, retake data |

---

## 12. Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| PCB arrives late | Medium | Delays all data | Ordered JLCPCB rush; backup plan: computational-only submission |
| QFN solder bridge | Medium | Board dead | Order 5 boards; inspect under microscope before power-on |
| Noise floor > 5 µVrms | Low | Weak detection | Improve Faraday cage; shorter cables; add ferrite |
| Synthetic ≠ phantom signatures | Medium | Low concordance | Feature importance analysis; retrain on phantom data if needed |
| Saline evaporation | Low | Impedance drift | Cover container; short sessions; monitor level |
| Adversarial trials ambiguous | Medium | Low adversarial accuracy | Accept and report honestly; ambiguity IS the interesting result |

---

## 13. Version History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-03-01 | Initial protocol (3 perturbation types, 77 trials) |
| 2.0 | 2026-03-02 | Align with two-stage classifier labels. Add suppression-like proxy trials (S1–S4), adversarial trials (X1–X2), replication session. 80 trials. Proxy label policy enforced. |
