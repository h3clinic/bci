# Phantom Validation Protocol v1.0

**Project:** BCIInterface — 16-Channel Electrophysiology Platform
**Date:** 2026-03-01
**Deadline:** 2026-03-11 (10 calendar days)
**PI:** A. Harshi
**Status:** DRAFT → execute on Day 4 (after board arrives)

---

## 1. Scientific Claim

> "We built and validated a 16-channel electrophysiology front-end that
> detects toxicant-relevant signal degradation signatures — impedance
> shift, inter-channel coupling, and broadband noise injection — in a
> saline phantom model with quantitative metrics, statistical thresholds,
> and sub-second detection latency across repeated trials."

This is **not** a neurotoxicity claim. It is a **platform validation** claim
with a neurotoxicity-relevant perturbation model.

---

## 2. Equipment Required

| Item | Role | Settings |
|------|------|----------|
| Intan RHD2000 USB interface board | SPI host + ADC readout | 20 kS/s, 16 channels |
| Headstage PCB (afe-headstage-v1) | DUT | 3.3V via Intan board |
| Bench DC power supply | Backup power verification | 3.3V, 50mA limit |
| Function generator | Perturbation B (coupling), C (injection) | 10 Hz–10 kHz |
| Oscilloscope | Signal verification, timing | ≥20 MHz BW |
| Multimeter | Impedance/resistance verification | Ω mode |
| Logic analyzer | SPI debug (if needed) | ≥10 MHz |

---

## 3. Phantom Construction

### 3.1 Saline Bath

- **Container:** Plastic petri dish or shallow rectangular container,
  ≥60mm × 40mm × 20mm deep.
- **Solution:** 0.9% NaCl (9g table salt per 1L distilled water).
  Prepare 200mL minimum. Use distilled water, not tap.
- **Temperature:** Room temperature (20–25°C). Record with thermometer
  at start and end of each session. No active temperature control.
- **Volume:** Fill to 15mm depth (≥36mL in a 60×40mm container).

### 3.2 Electrode Array (DIY)

Since no MEA is available, construct a **wire electrode array**:

1. Cut 16 pieces of solid-core copper wire, each 30mm long.
2. Strip 5mm of insulation from one end (the "electrode tip").
3. Strip the other end fully and solder to a breadboard header pin.
4. Arrange the 16 wires in a **2×8 grid** with **3mm center-to-center spacing**.
5. Fix the array to a rigid support (e.g., hot-glue wires to a popsicle
   stick or PCB scrap) so geometry is repeatable.
6. Connect header pins to the Omnetics connector via the Intan adapter
   or direct wiring to headstage J5.

**Electrode map:**

```
Row A:  CH0  CH1  CH2  CH3  CH4  CH5  CH6  CH7
Row B:  CH8  CH9  CH10 CH11 CH12 CH13 CH14 CH15
        |--- 3mm spacing ---|
```

Submerge electrode tips 10mm into saline. Keep wiring above waterline.

### 3.3 Reference Electrode

- **Ag/AgCl wire** (ideal) or bare copper wire (acceptable for phantom).
- Place at the **far end** of the container, ≥20mm from nearest signal electrode.
- Connect to headstage REF input (J5 pin 33).

### 3.4 Faraday Cage

- Wrap the container + headstage in **aluminum foil**, leaving only
  the USB cable exit.
- Connect foil to the GND terminal of the power supply or Intan board GND.
- This reduces 50/60Hz pickup from ~50µV to <5µV.

### 3.5 Perturbation Hardware

**Perturbation A — Impedance Shift:**
- Prepare 3 saline concentrations:
  - **Low impedance (baseline):** 0.9% NaCl (physiological saline)
  - **Medium impedance:** 0.3% NaCl (1/3 concentration)
  - **High impedance:** 0.1% NaCl (1/9 concentration)
- Perturbation = swap saline concentration mid-recording by pipetting
  out old solution and adding new (takes ~10 seconds, mark the time).

**Perturbation B — Controlled Coupling:**
- Bridge two electrodes (e.g., CH0 and CH1) with a resistor.
- Use a breadboard to switch coupling resistor values:
  - **No coupling:** open circuit (baseline)
  - **Weak coupling:** 1 MΩ bridge
  - **Strong coupling:** 100 kΩ bridge
  - **Severe coupling:** 10 kΩ bridge
- Insert/remove resistor mid-recording to create step perturbation.

**Perturbation C — Broadband Noise Injection:**
- Connect function generator output through a **1 MΩ series resistor**
  to one electrode (e.g., CH0).
- Function generator settings:
  - Waveform: **white noise** (or if unavailable, sweep 10Hz–5kHz)
  - Amplitude: start at **10 mVpp** (attenuated by 1MΩ → ~10nA into saline)
  - Step through: 0 mVpp → 10 mVpp → 50 mVpp → 100 mVpp
- This simulates increasing environmental noise / degrading shielding.

---

## 4. Experimental Protocol

### 4.1 Timeline

| Day | Activity |
|-----|----------|
| 1 (Mar 1) | Order PCB (JLCPCB, rush 3-day fab + 2-day ship) |
| 1 | Finalize protocol (this document) |
| 1 | Prepare saline solutions, build electrode array |
| 2–3 | Wait for PCB |
| 4 (Mar 5) | Receive PCB. Solder. Bring-up (steps 1-6 from design_notes_v1.md) |
| 4 | Session 0: Connectivity + noise floor baseline |
| 5 (Mar 6) | Session 1: Perturbation A trials (impedance shift) |
| 5 | Session 2: Perturbation B trials (coupling) |
| 6 (Mar 7) | Session 3: Perturbation C trials (noise injection) |
| 6 | Session 4: Repeat sessions 1-3 (replication) |
| 7 (Mar 8) | Run analysis pipeline. Generate figures. |
| 8 (Mar 9) | Write results. Refine figures. |
| 9 (Mar 10) | Final poster/paper. Review. |
| 10 (Mar 11) | Submit. |

### 4.2 Session Structure

Every recording session follows this structure:

```
|← 30s baseline →|← ~10s transition →|← 30s perturbed →|← 30s recovery →|
     epoch 1              (mark)            epoch 2           epoch 3
```

**Total recording time per trial:** 100 seconds (with margins).

**Per-session trial count:**

| Session | Perturbation | Conditions | Trials per condition | Total trials |
|---------|-------------|------------|---------------------|-------------|
| 0 | Baseline | 1 (none) | 5 | 5 |
| 1 | A: Impedance | 3 concentrations | 5 each | 15 |
| 2 | B: Coupling | 3 resistor values | 5 each | 15 |
| 3 | C: Injection | 3 amplitude levels | 5 each | 15 |
| 4 | Replication | Repeat sessions 1-3 | 3 each | 27 |

**Total trials:** 77 (5 + 15 + 15 + 15 + 27)
**Total recording time:** ~2.1 hours of data

### 4.3 Randomization

Within each session, randomize the order of perturbation levels.
Use a pre-generated random order (seed=42). Run:

```bash
python experiment/generate_trial_order.py --seed 42
```

This produces `experiment/session_log.csv` and `experiment/trial_orders.txt`.
Print `trial_orders.txt` and bring to the bench.

### 4.4 Blinding

This is a phantom study — true blinding is not applicable.
However: run the analysis pipeline **before** looking at which trial
corresponds to which condition. Then unblind and check concordance.

### 4.5 Recording Procedure (per trial)

1. Verify Faraday cage is closed.
2. Record temperature.
3. Start Intan recording (20 kS/s, 16 channels, save .rhd).
4. Wait 30 seconds (baseline epoch).
5. Apply perturbation (log exact time to nearest second).
6. Wait 30 seconds (perturbed epoch).
7. Remove perturbation / restore baseline condition.
8. Wait 30 seconds (recovery epoch).
9. Stop recording.
10. Save file with naming convention: `S{session}_T{trial}_{perturbation}_{level}.rhd`
11. Log trial in session spreadsheet.

---

## 5. Data Management

### 5.1 File Naming

```
data/
  raw/
    S0_T01_baseline_none.rhd
    S1_T01_impedance_0.3pct.rhd
    S1_T02_impedance_0.1pct.rhd
    ...
    S2_T01_coupling_1M.rhd
    ...
    S3_T01_injection_10mV.rhd
    ...
  processed/
    S1_T01_impedance_0.3pct.npz
    ...
  results.csv
```

### 5.2 Session Log

Pre-generated at `experiment/session_log.csv`. Fill in temperature
and notes columns during data collection.

### 5.3 Analysis Command

After data collection:

```bash
python analysis/make_all_figures.py \
    --session data/raw/ \
    --log experiment/session_log.csv \
    --onset 30 \
    --outdir out/figures \
    --dpi 300
```

---

## 6. Primary Endpoint

### Definition

**Primary endpoint:** Time-to-detection of perturbation onset.

For each trial:
1. Compute windowed RMS noise (1-second windows) across all 16 channels.
2. Compute mean RMS across channels per window.
3. Define baseline statistics from first 25 windows (25 seconds).
4. Compute z-score of each post-baseline window vs. baseline distribution.
5. **Detection** = first window where z > 3.0 for 3 consecutive windows.
6. **Detection latency** = detection_time − perturbation_onset_time.

### Success Criteria

| Metric | Target | Rationale |
|--------|--------|-----------|
| Detection rate (true positive) | ≥90% across all perturbation types | Must reliably detect |
| Detection latency | ≤5 seconds | Clinically useful response time |
| False positive rate (baseline trials) | ≤5% | Must not cry wolf |
| Detection latency CV | ≤30% | Must be repeatable |

### Secondary Endpoints

| Endpoint | Metric | Figure |
|----------|--------|--------|
| Noise floor stability | RMS CV across channels, drift over time | Figure 1 |
| Channel isolation | Crosstalk matrix, worst-case dB | Figure 2 |
| Multi-biomarker sensitivity | RMS + bandpower + slope combined z-score | Figure 3 |
| Dose-response | Detection latency vs. perturbation magnitude | Figure 3 |

---

## 7. Statistical Analysis Plan

### 7.1 Per-Trial Analysis

For each of the 77 trials, compute:
- `rms_baseline`: mean RMS over baseline epoch (channels × 1 scalar)
- `rms_perturbed`: mean RMS over perturbed epoch
- `rms_ratio`: perturbed / baseline (effect size)
- `detection_latency_s`: time from onset to detection (or NaN if missed)
- `detected`: boolean
- `bandpower_ratio`: 300-3000Hz power, perturbed / baseline
- `slope_shift`: spectral slope change (perturbed − baseline)

All implemented in `analysis/session_analyzer.py`.

### 7.2 Group-Level Statistics

**Within each perturbation type:**

1. **Detection rate:** proportion detected ± 95% Wilson confidence interval.
2. **Detection latency:** median ± IQR (non-parametric, small N per group).
3. **Effect size:** paired Wilcoxon signed-rank test, baseline vs. perturbed
   RMS/bandpower/slope. Report p-value and effect size (r = Z/√N).
4. **Dose-response:** Kruskal-Wallis test across perturbation levels.
   If significant (p < 0.05), pairwise Dunn's post-hoc with Bonferroni correction.

**Across perturbation types:**

5. **Combined detection rate:** overall TP rate with 95% CI.
6. **False positive rate:** from baseline-only epochs (perturbation = none).

### 7.3 Multiple Comparisons

- 3 perturbation types × 3 levels = 9 primary comparisons.
- Bonferroni-corrected α = 0.05 / 9 = 0.0056.
- Report both uncorrected and corrected p-values.

### 7.4 Reproducibility

Compare Session 1-3 results vs. Session 4 (replication):
- Intraclass correlation coefficient (ICC) for detection latency.
- Bland-Altman plot for RMS ratio (original vs. replication).

---

## 8. Figure Specifications

### Figure 1: Noise Floor & Stability (fig1_noise_stability.py)

**Data source:** Baseline epochs from ALL trials (no perturbation active).

| Panel | Content | Axes |
|-------|---------|------|
| A | Per-channel RMS noise (µVrms), mean ± SD bars | x: channel, y: µVrms |
| B | RMS over time (1s windows) for a representative 100s trial | x: time, y: µVrms |
| C | Cross-channel CV with 95% CI | x: epoch, y: CV |

**Key number:** "Mean noise floor = X.X ± X.X µVrms (N=77 trials, 16 channels)."

**Pass criterion:** Mean RMS ≤ 3.0 µVrms, CV < 15%.

### Figure 2: Channel Isolation / Crosstalk (fig2_crosstalk.py)

**Data source:** Session 2 (coupling perturbation) trials.

| Panel | Content | Axes |
|-------|---------|------|
| A | 16×16 crosstalk matrix (dB), baseline condition | x,y: channel, color: dB |
| B | Per-channel isolation bars, 3 coupling conditions overlaid | x: channel, y: dB |
| C | CH0-CH1 overlay: no coupling vs. 10kΩ bridge | x: time (ms), y: µV |

**Key number:** "Worst-case coupling = −XX dB (baseline), degrading to −XX dB under 10kΩ bridge."

**Pass criterion:** Baseline isolation ≥ −40 dB on all channel pairs.

### Figure 3: Perturbation Detection (fig3_detection.py)

**Data source:** All perturbation trials across sessions 1-3.

| Panel | Content | Axes |
|-------|---------|------|
| A | Detection latency vs. perturbation magnitude (3 types, color-coded) | x: magnitude, y: latency |
| B | Detection rate by type and level (grouped bar + CI) | x: condition, y: rate |
| C | Combined z-score for one representative trial | x: time, y: z-score |
| D | TP rate vs. FP rate across z-thresholds | x: FP, y: TP |

**Key number:** "Detection rate = XX% (95% CI: XX–XX%) with median latency X.Xs (IQR: X.X–X.Xs)."

**Pass criterion:** TP ≥ 90%, FP ≤ 5%, median latency ≤ 5s.

---

## 9. One-Sentence Results (Templates)

**If it works (expected):**
> "The 16-channel platform detected impedance shifts, inter-channel coupling,
> and broadband noise injection with XX% sensitivity (95% CI: XX–XX%),
> median detection latency of X.Xs (IQR: X.X–X.Xs), and ≤X% false-positive
> rate across 77 phantom trials, demonstrating sufficient sensitivity for
> toxicant-relevant signal degradation screening."

**If partially works:**
> "The platform reliably detected impedance shifts and noise injection
> (XX% and XX% sensitivity) but showed reduced sensitivity to weak coupling
> perturbations (XX%), suggesting a detection floor of approximately −XX dB
> inter-channel coupling change."

**If noise floor is too high:**
> "Baseline noise (X.X ± X.X µVrms) exceeded the 3.0 µVrms target,
> attributable to [cable noise / insufficient shielding / assembly defect].
> Despite elevated noise, perturbation detection remained possible for
> large-magnitude perturbations (XX% detection rate for ≥Xσ effects)."

---

## 10. Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| PCB arrives late (>Day 4) | Medium | Delays all data | Order ASAP; use JLCPCB rush; have backup fab |
| Solder bridging on QFN-56 | Medium | Board dead | Practice; order 2+ boards; inspect carefully |
| Noise floor >5µVrms | Low | Weak detection | Improve Faraday cage; shorter cables; add ferrite |
| Saline evaporation during session | Low | Impedance drift artifact | Cover container; short sessions; monitor level |
| Intan board communication failure | Low | No data | Test SPI before phantom; have spare cables |
| Insufficient time for replication | Medium | Weaker stats | Prioritize sessions 1-3; replication is bonus |

---

## 11. Minimum Viable Dataset

If time runs short, the **absolute minimum** for a defensible submission:

- [ ] 5 baseline-only trials (noise floor characterization)
- [ ] 5 impedance-shift trials (3 levels: low/med/high)
- [ ] 5 coupling trials (3 levels: 1MΩ/100kΩ/10kΩ)
- [ ] 5 noise-injection trials (3 levels: 10/50/100 mVpp)

**= 20 trials minimum.** Enough for Figure 1 + Figure 2 + Figure 3
with error bars, but no replication session.

---

## 12. Checklist

### Pre-Experiment (Days 1-3)
- [ ] Order headstage PCB (JLCPCB, rush)
- [ ] Order second headstage PCB (backup)
- [ ] Prepare 0.9% saline (200mL)
- [ ] Prepare 0.3% saline (200mL)
- [ ] Prepare 0.1% saline (200mL)
- [ ] Build 16-wire electrode array
- [ ] Build reference electrode
- [ ] Build Faraday cage (aluminum foil + container)
- [ ] Prepare breadboard with coupling resistors (1MΩ, 100kΩ, 10kΩ)
- [ ] Test function generator noise output through 1MΩ series resistor
- [ ] Verify Intan software records .rhd files correctly
- [ ] Generate randomized trial orders (seed=42)
- [ ] Print trial order sheets

### Board Bring-Up (Day 4)
- [ ] Visual inspection (microscope)
- [ ] Continuity checks (GND, AVDD)
- [ ] Power-on (3.3V, check current 10-15mA)
- [ ] SPI communication (Intan GUI detects chip)
- [ ] Impedance check (all 16 channels < 100kΩ)
- [ ] Noise floor measurement (inputs shorted, 60s, target ≤3µVrms)

### Data Collection (Days 5-6)
- [ ] Session 0: Connectivity + noise verification (5 trials)
- [ ] Session 1: Impedance perturbation (15 trials)
- [ ] Session 2: Coupling perturbation (15 trials)
- [ ] Session 3: Noise injection perturbation (15 trials)
- [ ] Session 4: Replication (27 trials)
- [ ] All session logs filled, all files named correctly

### Analysis + Figures (Day 7)
- [ ] Run `python make_all_figures.py --session data/raw/ --log experiment/session_log.csv`
- [ ] Verify Figure 1: noise floor + stability
- [ ] Verify Figure 2: crosstalk matrix + isolation
- [ ] Verify Figure 3: detection latency + rates + z-scores
- [ ] Compute all primary + secondary endpoint statistics
- [ ] Fill in one-sentence result templates

### Write-Up (Days 8-9)
- [ ] Abstract (250 words)
- [ ] Methods section (phantom, perturbations, endpoints, stats)
- [ ] Results section (key numbers, figure references)
- [ ] Discussion (limitations, future: 64ch, biological validation)
- [ ] Figure captions (quantitative, self-contained)

### Submit (Day 10)
- [ ] Final review
- [ ] Submit

---

## Appendix A: Saline Preparation

### 0.9% NaCl (physiological saline)
1. Weigh 1.8g NaCl (table salt).
2. Dissolve in 200mL distilled water.
3. Stir until fully dissolved.
4. Label container "0.9% NaCl".

### 0.3% NaCl
1. Weigh 0.6g NaCl.
2. Dissolve in 200mL distilled water.
3. Label "0.3% NaCl".

### 0.1% NaCl
1. Weigh 0.2g NaCl.
2. Dissolve in 200mL distilled water.
3. Label "0.1% NaCl".

**Note:** Use a kitchen scale (0.1g resolution is sufficient).
If no scale available, use volumetric dilution from 0.9% stock.

---

## Appendix B: Expected Signal Characteristics

| Parameter | Expected Value | Notes |
|-----------|---------------|-------|
| Noise floor (inputs shorted) | ~2.4 µVrms | RHD2132 typical |
| Noise floor (saline, 0.9%) | ~3-5 µVrms | Electrode-saline interface noise |
| Noise floor (saline, 0.1%) | ~8-15 µVrms | Higher impedance → more thermal noise |
| 60Hz pickup (no cage) | ~50-200 µVpp | Depends on environment |
| 60Hz pickup (foil cage) | ~2-10 µVpp | With grounded foil |
| Crosstalk (baseline) | < −40 dB | Channel-to-channel in saline |
| Crosstalk (10kΩ bridge) | ~ −20 dB | Depends on electrode impedance |

---

## Appendix C: Perturbation Magnitude Estimates

### Impedance shift
- 0.9% → 0.3%: ~3× impedance increase → ~1.7× noise increase (thermal noise ∝ √Z)
- 0.9% → 0.1%: ~9× impedance increase → ~3× noise increase

### Coupling bridge
- 1MΩ bridge on ~50kΩ electrode impedance: ~−26 dB coupling
- 100kΩ bridge: ~−6 dB coupling (strong)
- 10kΩ bridge: ~+14 dB coupling (dominant — clear on all metrics)

### Noise injection
- 10mVpp through 1MΩ at ~50kΩ electrode Z: ~0.5 µVrms at electrode (subtle)
- 50mVpp: ~2.5 µVrms (detectable, ~1× baseline noise)
- 100mVpp: ~5 µVrms (obvious, ~2× baseline noise)
