# Device Runtime Contract v1.0

**Project:** BCIInterface — 14-Channel Neural AFE Headstage  
**Date:** 2026-03-02  
**Status:** LOCKED — changes require version bump + regression  
**Applicable hardware:** afe-headstage-v1 (RHD2132 + Intan USB interface)

---

## 1. Transport Decision

**Selected:** USB CDC (serial) via Intan RHD2000 USB interface board.

**Rationale:** The headstage connects to an Intan RHD2000 USB interface
board which handles SPI readout + USB bulk transfer natively. The Intan
evaluation software outputs .rhd files. For our custom pipeline, we use
a CDC serial tap at reduced rate for bring-up, then switch to .rhd file
post-processing for production data collection.

**Bring-up path (Phase 1):**  
- Intan RHD2000 board → USB → Intan GUI → `.rhd` file on disk  
- Post-process: `rhd_loader.py` → `.npz` → analysis pipeline  

**Custom capture path (Phase 2, optional):**  
- Intan SPI bridge → FPGA (future meadaq-64ch) → USB bulk → `capture.py`  
- Only needed for real-time closed-loop; not required for BioGENEius

---

## 2. Acquisition Parameters

| Parameter | Value | Justification |
|-----------|-------|---------------|
| Sample rate (fs) | **20,000 S/s per channel** | Intan RHD2132 native rate; resolves spikes (300–6000 Hz band) |
| Active channels | **14** (CH0–CH13) | v1 PCB routes 14 of 32 RHD2132 inputs to J5 |
| ADC resolution | 16 bits unsigned | RHD2132 spec, 0.195 µV/LSB, offset at 32768 |
| Bandwidth | 0.1 Hz – 7.5 kHz (−3 dB) | RHD2132 on-chip bandpass, default settings |
| Recording duration | **100 s per trial** | 30s baseline + 10s transition + 30s perturbed + 30s recovery |
| Session duration | **≤60 min sustained** | 15 trials × 100s + setup overhead |

### Derived Throughput Budget

```
Per-channel: 20,000 S/s × 16 bits = 320,000 bits/s = 40 KB/s
14 channels: 14 × 40 KB/s = 560 KB/s = 4.48 Mbit/s
Per trial (100s): 560 KB/s × 100s = 54.7 MB
Per session (15 trials): ~820 MB
Full experiment (77 trials): ~4.1 GB
```

**Intan USB interface throughput:** 24 Mbit/s USB 2.0 bulk — 5× margin
over 4.48 Mbit/s requirement. No bottleneck.

**Disk write rate:** 560 KB/s sustained — trivially met by any SSD/HDD.

---

## 3. Integrity Specifications

These are **hard requirements**. A recording that violates any spec
is flagged and excluded from analysis.

### 3.1 Frame Integrity

| Metric | Requirement | Enforcement |
|--------|-------------|-------------|
| CRC pass rate | **100%** (zero tolerance) | CRC16-CCITT on every 256-byte frame; `frame_parser.py` validates |
| Frame drop rate | **< 0.1%** over any 10-minute window | `capture.py` counts frame_id gaps; `validate_recording()` rejects if exceeded |
| Timestamp monotonicity | **Strictly increasing** (allowing 32-bit wrap) | `validate_frame_sequence()` checks; violations flagged in CaptureStats |
| Reserved byte compliance | **All zero** for version 0x01 | `frame_parser.py` checks bytes 166–253 + status bits 4,3,1,0 |

### 3.2 Signal Integrity

| Metric | Requirement | Measurement |
|--------|-------------|-------------|
| Noise floor (inputs shorted) | **≤ 3.0 µVrms** per channel | 60s recording, RMS computed per 1s window, mean across windows |
| Noise floor (saline, 0.9% NaCl) | **≤ 5.0 µVrms** per channel | Same method; higher threshold accounts for electrode-saline interface |
| Channel-to-channel isolation | **≥ −40 dB** (baseline, no coupling) | Cross-correlation matrix from 30s baseline epoch |
| DC offset drift | **< 50 µV/min** per channel | Linear regression on 1s-windowed mean over 10-minute baseline |

### 3.3 How Drop Rate Is Computed

```
drop_rate = (frames_expected - frames_received) / frames_expected

where:
  frames_expected = last_frame_id - first_frame_id + 1
  frames_received = count of frames with valid CRC
```

This is implemented in `capture.py::validate_frame_sequence()` and
reported in `CaptureStats.drop_rate`.

**Frame ID gap detection:** Each frame carries a 32-bit monotonic
`frame_id`. A gap of N in frame_id indicates N−1 dropped frames.
The gap is logged with timestamps for debugging.

---

## 4. Repeatability Specification

| Metric | Requirement | Method |
|--------|-------------|--------|
| Noise floor CV across sessions | **< 15%** | Same electrode array, same saline, same temperature ±2°C |
| Detection latency ICC | **> 0.7** | Intraclass correlation across replication trials (Session 4 vs 1–3) |
| Trial-order invariance | **Same result distribution** (p > 0.05, KS test) | Compare analysis output for two orderings of same trial set |

### Computational Repeatability

| Aspect | Enforcement |
|--------|-------------|
| Random seeds | All generators and classifiers use explicit `seed` parameter |
| Dataset hashes | SHA-256 of training .npz verified before each analysis run |
| Software versions | `requirements.txt` pinned; Python version recorded in metadata |
| Figure reproduction | `python analysis/eval_report.py --out out/ --seed 42` produces identical output |

---

## 5. Acceptance Test Procedure

Run after every hardware change, cable swap, or firmware update.

### 5.1 Quick Smoke Test (< 2 min)

```bash
# Verify capture pipeline works end-to-end
python hardware/meadaq-64ch/host/capture.py --smoke-test
```

**Pass:** 101 valid frames, 0 CRC failures, 5 detected drops (intentional gap).

### 5.2 Noise Floor Test (5 min)

1. Short all 14 inputs to REF at the connector.
2. Record 60 seconds at 20 kS/s.
3. Run: `python analysis/neural_metrics.py --input recording.npz --check-noise`
4. **Pass:** All 14 channels ≤ 3.0 µVrms.

### 5.3 Sustained Capture Test (10 min)

1. Connect to saline phantom.
2. Record 10 minutes continuously.
3. Run: `python hardware/meadaq-64ch/host/capture.py recording.bin --offline --stats-only`
4. **Pass:** drop_rate < 0.1%, CRC failures = 0, no timestamp violations.

### 5.4 Detection Pipeline Test (5 min)

1. While recording, apply known perturbation at t=30s (e.g., swap saline).
2. Run analysis pipeline.
3. **Pass:** Perturbation detected with latency < 5s.

---

## 6. Failure Modes and Escalation

| Symptom | Likely Cause | Action |
|---------|-------------|--------|
| CRC failures > 0 | SPI bus noise, cold solder joint | Inspect solder joints; add decoupling; reduce SPI clock |
| Drop rate > 0.1% | USB buffer overflow, slow host | Check USB connection; close background processes; reduce fs |
| Noise > 5 µVrms | Poor shielding, ground loop, bad electrode | Check Faraday cage; verify GND connections; replace electrode wire |
| No frames received | SPI dead, chip not powered | Check 3.3V rail; verify MISO signal on scope; re-solder U1 |
| Timestamp non-monotonic | FPGA counter bug or USB reordering | Check firmware; verify single-threaded capture; reduce fs |

---

## 7. Version History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-03-02 | Initial contract. USB CDC bring-up path, 14ch @ 20kS/s, hard integrity specs. |
