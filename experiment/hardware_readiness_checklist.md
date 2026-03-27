# Hardware Readiness Checklist v1.0

**Board:** AFE Headstage v1 (afe-headstage-v1)  
**Order:** JLCPCB 12006563A-Y3  
**Date:** 2026-03-02  
**Runtime contract:** `experiment/device_runtime_contract_v1.md`  
**Phantom protocol:** `experiment/phantom_protocol_v2.md`

---

## Pre-Requisites

- [ ] Board(s) received and inspected for shipping damage
- [ ] Stencil received (if ordered)
- [ ] All components in BOM available (see `docs/BOM_v1.csv`)
- [ ] Soldering station operational (hot air + iron + microscope)
- [ ] Multimeter, oscilloscope, logic analyzer available

---

## Phase 1 — Bare Board Inspection

*Ref: `hardware/afe-headstage-real/bringup_checklist.txt` Phase 1–3*

| # | Check | Tool | Pass Criterion | Result |
|---|-------|------|-----------------|--------|
| 1.1 | Board dimensions | Calipers | 30.0 × 24.0 mm ± 0.1 mm | ☐ |
| 1.2 | 4-layer stackup | Edge inspection | 4 copper layers visible | ☐ |
| 1.3 | ENIG finish quality | Microscope 20× | Uniform gold, no nickel bleed | ☐ |
| 1.4 | Soldermask integrity | Microscope 20× | No pinholes, no mask on pads | ☐ |
| 1.5 | Silkscreen legible | Visual | Component refs readable | ☐ |
| 1.6 | Edge cuts clean | Visual | No burrs, no delamination | ☐ |
| 1.7 | J5 VIP pads flat | Microscope 40× | No dimples > 25 µm | ☐ |
| 1.8 | VIP pad count | Count against `vip_vias.csv` | 14 VIP pads present | ☐ |
| 1.9 | No epoxy bleed at VIP | Microscope 40× | Epoxy within pad boundary | ☐ |

**Phase 1 verdict:** ☐ GO / ☐ NO-GO (if any fail → reject board)

---

## Phase 2 — Continuity Checks (Before Assembly)

| # | Check | Probe Points | Pass Criterion | Result |
|---|-------|-------------|-----------------|--------|
| 2.1 | GND continuity | Any two GND vias, opposite sides | < 1 Ω | ☐ |
| 2.2 | VDD33 continuity | J1 power pin → nearest bypass cap pad | < 1 Ω | ☐ |
| 2.3 | No electrode shorts | Adjacent electrode pads at J5 | > 10 MΩ each pair | ☐ |
| 2.4 | SPI trace continuity | J1 SCLK pin → U1 SCLK pad (via R-pad) | < 5 Ω | ☐ |
| 2.5 | No power-GND short | VDD33 to GND | > 1 MΩ | ☐ |

**Phase 2 verdict:** ☐ GO / ☐ NO-GO

---

## Phase 3 — Assembly

| # | Step | Method | Verification |
|---|------|--------|-------------|
| 3.1 | Apply solder paste | Stencil (0.12mm) or manual | Inspect paste deposit under microscope |
| 3.2 | Place U1 (RHD2132, QFN-56) | Vacuum pen, microscope | Pad alignment ±0.1mm, EP centered |
| 3.3 | Place bypass caps (C1–C6) | Tweezers | Correct orientation (if polarized) |
| 3.4 | Place ferrite bead (FB1) | Tweezers | — |
| 3.5 | Place series resistors (R1–R4) | Tweezers | — |
| 3.6 | Place connector J1 | — | Pin 1 orientation verified |
| 3.7 | Reflow | Hot air 245°C, 30s | No solder bridges visible |
| 3.8 | Post-reflow inspection | Microscope 40× | All QFN pins wetted, no bridges |
| 3.9 | Touch-up (if needed) | Iron + flux | Document any rework |

**Assembly verdict:** ☐ GO / ☐ NO-GO

---

## Phase 4 — Power-On (Current-Limited)

**⚠ USE CURRENT-LIMITED POWER SUPPLY ⚠**

| # | Check | Procedure | Pass Criterion | Result |
|---|-------|-----------|-----------------|--------|
| 4.1 | Set supply limits | 3.3V, **50 mA current limit** | — | ☐ |
| 4.2 | Connect power | J1 pins 7,8 (+3V3), J1 GND pins | Supply shows voltage | ☐ |
| 4.3 | Initial current draw | Read supply ammeter | **10–20 mA** (RHD2132 typical: 13 mA) | ☐ |
| 4.4 | No thermal issues | Touch U1 after 30s | Not hot to touch (< 40°C) | ☐ |
| 4.5 | AVDD rail voltage | Probe U1 VDD1 pin to GND | 3.30V ± 0.1V | ☐ |
| 4.6 | AVDD post-ferrite | Probe FB1 output | 3.25–3.30V (slight drop OK) | ☐ |
| 4.7 | No oscillation | Scope on AVDD, AC-coupled, 100 MHz BW | No ringing > 50 mVpp | ☐ |

**If current > 50 mA:** STOP. Likely short. Power off immediately.
Inspect solder joints under microscope. Do not proceed.

**Phase 4 verdict:** ☐ GO / ☐ NO-GO

---

## Phase 5 — SPI Bring-Up

| # | Check | Procedure | Pass Criterion | Result |
|---|-------|-----------|-----------------|--------|
| 5.1 | Connect to Intan board | Omnetics cable J1 → RHD2000 USB | Physical connection secure | ☐ |
| 5.2 | Launch Intan GUI | USB enumeration | Board detected in software | ☐ |
| 5.3 | Chip detection | Intan GUI auto-detect | RHD2132 identified, 32 channels listed | ☐ |
| 5.4 | SCLK signal quality | Scope on U1 SCLK pin | Clean clock, correct frequency | ☐ |
| 5.5 | MISO signal | Scope on U1 MISO pin | Transitions visible during SPI read | ☐ |
| 5.6 | Register read | Intan GUI: read chip ID register | Returns correct device ID | ☐ |
| 5.7 | Impedance measurement | Intan GUI: run impedance test, all 14 CH | All < 100 kΩ (inputs open) | ☐ |

**Phase 5 verdict:** ☐ GO / ☐ NO-GO

---

## Phase 6 — Noise Floor Verification

| # | Check | Procedure | Pass Criterion | Result |
|---|-------|-----------|-----------------|--------|
| 6.1 | Short all inputs | Connect all 14 electrode inputs to REF at J5 | — | ☐ |
| 6.2 | Record 60s | Intan GUI: 20 kS/s, 14 channels, save .rhd | File saved, correct size | ☐ |
| 6.3 | RMS noise per channel | `session_analyzer.py` or manual FFT | **All 14 channels ≤ 3.0 µVrms** | ☐ |
| 6.4 | No 60 Hz spike | FFT of any channel | 60 Hz peak < 1.0 µVrms | ☐ |
| 6.5 | Channel-to-channel isolation | Cross-correlation matrix | All off-diagonal < −40 dB | ☐ |
| 6.6 | DC offset stability | 60s mean per channel | Drift < 50 µV over 60s | ☐ |

**If noise > 3.0 µVrms:** Check Faraday cage, cable routing, GND.
Try adding ferrite on USB cable. Repeat measurement.

**Phase 6 verdict:** ☐ GO / ☐ NO-GO

---

## Phase 7 — First Frames (Software Pipeline Acceptance)

| # | Check | Command | Pass Criterion | Result |
|---|-------|---------|-----------------|--------|
| 7.1 | Smoke test | `python capture.py --smoke-test` | 101 valid, 0 CRC fail, 5 drops | ☐ |
| 7.2 | Load .rhd file | `python rhd_loader.py --input noise_test.rhd --output noise_test.npz` | .npz created, correct shape | ☐ |
| 7.3 | Run neural metrics | `python neural_metrics.py --input noise_test.npz` | Metrics computed, no errors | ☐ |
| 7.4 | Run session analyzer | `python session_analyzer.py --input noise_test.npz --onset 30` | Results CSV generated | ☐ |
| 7.5 | Run eval_report (synthetic) | `python eval_report.py --out out/test_panels/` | 4 PNG figures generated | ☐ |

**Phase 7 verdict:** ☐ GO / ☐ NO-GO

---

## Phase 8 — Saline Phantom Dry Run

| # | Check | Procedure | Pass Criterion | Result |
|---|-------|-----------|-----------------|--------|
| 8.1 | Electrode array construction | Build 14-wire 2×7 array per protocol | Wires secure, tips stripped 5mm | ☐ |
| 8.2 | Saline preparation | 0.9% NaCl, 200 mL distilled water | Dissolved, labeled | ☐ |
| 8.3 | Submerge electrodes | 10mm depth, reference at far end | All tips submerged | ☐ |
| 8.4 | Noise floor in saline | Record 60s, compute RMS | **All channels ≤ 5.0 µVrms** | ☐ |
| 8.5 | Apply test perturbation | Swap to 0.1% saline (impedance shift) | Detection within 5s by eye on scope | ☐ |
| 8.6 | Run analysis on test trial | Full pipeline: record → .rhd → .npz → session_analyzer | Perturbation detected, latency reported | ☐ |

**Phase 8 verdict:** ☐ GO to full experiment / ☐ Iterate

---

## Sign-Off

| Phase | Verdict | Date | Initials | Notes |
|-------|---------|------|----------|-------|
| 1. Bare board | | | | |
| 2. Continuity | | | | |
| 3. Assembly | | | | |
| 4. Power-on | | | | |
| 5. SPI | | | | |
| 6. Noise floor | | | | |
| 7. First frames | | | | |
| 8. Phantom dry run | | | | |

**Overall hardware readiness:** ☐ READY / ☐ NOT READY

---

## Appendix: Failure Escalation Quick Reference

| Symptom | Phase | Action |
|---------|-------|--------|
| VIP pads dimpled | 1 | Request vendor cross-section; reject lot if > 25 µm |
| Short between power and GND | 2 | Do NOT assemble; inspect for fab defect |
| Current > 50 mA at power-on | 4 | Power off immediately; inspect QFN solder bridges |
| Intan GUI doesn't detect chip | 5 | Check SCLK/MOSI with scope; re-solder SPI pins |
| Noise > 5 µVrms | 6 | Improve shielding; check GND continuity; try shorter cables |
| Pipeline errors | 7 | Check Python environment; run `pytest` regression |
