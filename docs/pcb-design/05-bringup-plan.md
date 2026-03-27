# 05 - Bring-Up Plan

## Philosophy

**Do not go straight to 128 channels.** If you do, you will have no idea what broke.

Bring-up is systematic. Each phase has a clear pass/fail criterion before proceeding.

---

## Timeline Overview

| Phase | Duration | Description |
|-------|----------|-------------|
| 0 | Day 0 | Visual inspection |
| 1 | Day 1-2 | Power verification |
| 2 | Day 2-3 | Clock verification |
| 3 | Day 3-4 | Digital link verification |
| 4 | Day 4-5 | Analog noise floor (inputs shorted) |
| 5 | Day 5-7 | Calibration injection and channel mapping |
| 6 | Day 7-10 | Scale-up (8→32→64→128) |
| 7 | Day 10+ | Full system validation |

---

## Phase 0: Visual Inspection (Before Power)

### Checklist

- [ ] No obvious solder bridges
- [ ] All components placed and oriented correctly
- [ ] No missing components
- [ ] Connector pins not bent
- [ ] No PCB damage (scratches to traces)

### Tools
- Magnifying glass or microscope
- Good lighting

### Pass Criteria
- No visible defects
- All critical components present

---

## Phase 1: Power Verification

### Equipment
- Bench power supply with current limiting
- Multimeter
- Oscilloscope (for noise measurement)

### Procedure

#### Step 1.1: Initial Power-Up (Current Limited)
```
1. Set bench supply to expected input voltage (e.g., 5V)
2. Set current limit to 50 mA (well below expected draw)
3. Connect to board (with load switches OFF if present)
4. Monitor current draw
```

**Expected:** <10 mA quiescent (regulators only, no load)
**Red flag:** Current limit hit immediately → short circuit

#### Step 1.2: Rail Verification
```
Measure each rail at test point:
- VIN: ______V (expected: 5.0V)
- AVDD: ______V (expected: 3.3V)
- DVDD: ______V (expected: 3.3V)
- VREF: ______V (expected: per design)
- VCORE: ______V (expected: 1.2V, if present)
```

**Pass criteria:** All rails within ±5% of nominal

#### Step 1.3: Rail Noise Measurement
```
Using oscilloscope (AC coupled, 20 MHz BW limit):
Measure ripple/noise at each rail test point.

- AVDD ripple: ______mVpp (target: <10 mVpp)
- DVDD ripple: ______mVpp (target: <50 mVpp)
- VREF noise: ______µVrms (target: <100 µVrms)
```

#### Step 1.4: Thermal Check
```
After 5 minutes at full power:
- Touch-test all regulators (should be warm, not hot)
- Thermal camera if available
- Any component >60°C is a concern
```

### Phase 1 Sign-Off

| Item | Pass | Fail | Notes |
|------|------|------|-------|
| No short circuits | ☐ | ☐ | |
| All rails correct voltage | ☐ | ☐ | |
| AVDD noise acceptable | ☐ | ☐ | |
| No thermal issues | ☐ | ☐ | |

**Proceed to Phase 2 only if all pass.**

---

## Phase 2: Clock Verification

### Equipment
- Oscilloscope (≥100 MHz BW)
- Frequency counter (optional)

### Procedure

#### Step 2.1: Clock Presence
```
Probe MCLK test point:
- Frequency: ______MHz (expected: per design)
- Amplitude: ______Vpp (expected: rail-to-rail or per spec)
- Waveform: Clean square/sine (no ringing, no missing cycles)
```

#### Step 2.2: Clock Quality
```
Using oscilloscope:
- Check for overshoot/undershoot: <10% of amplitude
- Check for ringing: settled within one cycle
- Verify no missing clock edges (run for 1 minute)
```

#### Step 2.3: Clock at AFE Input
```
If accessible, probe clock at AFE MCLK pin:
- Confirm same frequency
- Amplitude appropriate for IC input
- No excessive reflections
```

### Phase 2 Sign-Off

| Item | Pass | Fail | Notes |
|------|------|------|-------|
| Clock present | ☐ | ☐ | |
| Correct frequency | ☐ | ☐ | |
| Clean waveform | ☐ | ☐ | |
| Stable (1 min) | ☐ | ☐ | |

---

## Phase 3: Digital Link Verification

### Equipment
- Host computer with software
- Logic analyzer (optional)
- Oscilloscope

### Procedure

#### Step 3.1: Basic Communication
```
1. Connect board to host
2. Enumerate device (USB) or establish connection (Ethernet)
3. Send basic command (e.g., read chip ID)
```

**Expected:** AFE chip ID matches datasheet value

#### Step 3.2: Register Access
```
1. Write known pattern to writable register
2. Read back
3. Verify match
```

**Example:**
```
Write: 0xABCD to test register
Read: _______ (expected: 0xABCD)
```

#### Step 3.3: Continuous Data Transfer
```
1. Configure AFE for minimal data (e.g., 1 channel, low rate)
2. Start acquisition
3. Verify packets received
4. Check sequence numbers are sequential
5. Run for 1 minute
```

**Pass criteria:**
- Packets received continuously
- No sequence number gaps
- No CRC errors

#### Step 3.4: Throughput Test
```
1. Configure for full bandwidth (128 ch, target sample rate)
2. Run for 1 minute
3. Count dropped packets
```

**Pass criteria:** 0 dropped packets in 1 minute

### Phase 3 Sign-Off

| Item | Pass | Fail | Notes |
|------|------|------|-------|
| Device enumerates/connects | ☐ | ☐ | |
| Chip ID correct | ☐ | ☐ | |
| Register R/W works | ☐ | ☐ | |
| Continuous data OK | ☐ | ☐ | |
| Full throughput achieved | ☐ | ☐ | |

---

## Phase 4: Analog Noise Floor

### Equipment
- Acquisition software with noise analysis
- Oscilloscope (for debugging)

### Setup
```
1. Enable input shorting (all inputs to reference)
2. Disable calibration injection
3. Configure AFE for target bandwidth and sample rate
4. Acquire data for all channels
```

### Procedure

#### Step 4.1: Measure Input-Referred Noise
```
1. Acquire 10 seconds of data (inputs shorted)
2. Compute RMS noise per channel
3. Compare to specification
```

**Calculation:**
```
For each channel:
  noise_rms = std(samples) × (Vref / 2^bits) × 1e6  [in µVrms]
```

**Expected:** < target noise floor (e.g., <5 µVrms)

#### Step 4.2: Noise Spectrum Analysis
```
1. Compute FFT of shorted-input data
2. Look for:
   - 50/60 Hz line noise (and harmonics)
   - Clock frequency spurs
   - Broadband elevation
```

**Pass criteria:**
- No narrowband spurs >3 dB above noise floor
- 50/60 Hz < specification
- Spectrum shape matches expected (1/f + white)

#### Step 4.3: Channel-to-Channel Consistency
```
1. Compare noise across all channels
2. Flag any channel >2× median noise
```

**Pass criteria:** All channels within 2× of each other

### Noise Troubleshooting Guide

| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| All channels high noise | Power supply issue | Check AVDD noise |
| 50/60 Hz spike | Ground loop | Check ground connections |
| Clock frequency spike | Digital coupling | Review layout, shielding |
| One channel bad | Solder issue or ESD damage | Inspect, reflow |
| Noise increases over time | Thermal issue | Check regulator temp |

### Phase 4 Sign-Off

| Item | Pass | Fail | Notes |
|------|------|------|-------|
| Average noise < spec | ☐ | ☐ | Value: ___µVrms |
| No line frequency spurs | ☐ | ☐ | |
| No clock spurs | ☐ | ☐ | |
| All channels consistent | ☐ | ☐ | |

---

## Phase 5: Calibration Injection

### Equipment
- Signal generator or DAC output
- Acquisition software

### Setup
```
1. Disable input shorting
2. Enable calibration injection
3. Configure injection signal (e.g., 1 kHz sine, 100 µVpp)
```

### Procedure

#### Step 5.1: Injection Signal Verification
```
1. Probe CAL_INJECT test point with oscilloscope
2. Verify amplitude and frequency
```

**Expected:** 
- Frequency: ______ Hz (as configured)
- Amplitude: ______ µVpp (per attenuation calculation)

#### Step 5.2: Single Channel Capture
```
1. Route injection to one channel
2. Acquire data
3. Verify signal appears in correct channel
4. Verify amplitude matches expectation
```

**Pass criteria:**
- Signal in correct channel
- Amplitude within ±10% of expected
- SNR > 20 dB

#### Step 5.3: Channel Mapping
```
1. Inject to each channel sequentially (or all simultaneously)
2. Verify each input maps to expected data channel
3. Create mapping table:
```

| Connector Pin | AFE Input | Data Channel | Status |
|---------------|-----------|--------------|--------|
| 1 | IN0 | CH0 | ☐ OK |
| 2 | IN1 | CH1 | ☐ OK |
| ... | ... | ... | ... |
| 128 | IN127 | CH127 | ☐ OK |

#### Step 5.4: Frequency Response (Optional)
```
1. Sweep injection frequency (10 Hz to 10 kHz)
2. Measure amplitude at each frequency
3. Verify bandwidth matches specification
```

**Expected:** -3 dB points at specified HPF and LPF frequencies

### Phase 5 Sign-Off

| Item | Pass | Fail | Notes |
|------|------|------|-------|
| Injection signal correct | ☐ | ☐ | |
| Channel mapping verified | ☐ | ☐ | |
| Gain within spec | ☐ | ☐ | |
| Bandwidth within spec | ☐ | ☐ | |

---

## Phase 6: Scale-Up Testing

### Rationale

Do not jump straight to 128 channels. Verify in stages to isolate issues.

### Procedure

#### Step 6.1: 8-Channel Test
```
1. Connect 8 electrodes (or simulate with resistors)
2. Acquire data
3. Verify noise, crosstalk, signal integrity
```

**Crosstalk test:**
- Inject signal on channel 1
- Measure amplitude on channels 2-8
- Crosstalk should be < -40 dB

#### Step 6.2: 32-Channel Test
```
1. Connect 32 electrodes
2. Repeat noise and crosstalk tests
3. Verify digital throughput stable
```

#### Step 6.3: 64-Channel Test
```
1. Connect 64 electrodes
2. Repeat all tests
3. Monitor thermal behavior
```

#### Step 6.4: 128-Channel Test
```
1. Connect all 128 electrodes
2. Final verification of all parameters
```

### Scale-Up Checklist

| Channels | Noise OK | Crosstalk OK | Throughput OK | Thermal OK |
|----------|----------|--------------|---------------|------------|
| 8 | ☐ | ☐ | ☐ | ☐ |
| 32 | ☐ | ☐ | ☐ | ☐ |
| 64 | ☐ | ☐ | ☐ | ☐ |
| 128 | ☐ | ☐ | ☐ | ☐ |

---

## Phase 7: Full System Validation

### Tests

#### 7.1: Long-Term Stability
```
1. Run acquisition for 24 hours
2. Monitor for:
   - Dropped packets
   - Noise drift
   - Thermal stability
```

#### 7.2: Environmental
```
If relevant:
- Temperature cycling (bench ambient variation)
- Vibration (tap test)
- EMI susceptibility (bring phone near, etc.)
```

#### 7.3: In-Vitro Test (With Real Electrodes)
```
1. Connect to actual MEA/electrode array
2. Place in saline bath
3. Verify noise floor with real electrode impedances
4. Verify ability to see simulated signals
```

### Final Sign-Off

| Item | Pass | Fail | Notes |
|------|------|------|-------|
| 24-hour stability | ☐ | ☐ | |
| Environmental OK | ☐ | ☐ | |
| In-vitro test passed | ☐ | ☐ | |

---

## Debug Appendix

### Common Problems and Solutions

| Problem | Possible Causes | Debug Steps |
|---------|-----------------|-------------|
| No power | Open circuit, blown fuse | Check continuity, inspect solder |
| High noise all channels | Power supply noise, grounding | Scope power rails, check grounds |
| One channel noisy | Solder defect, ESD damage | Visual inspect, reflow |
| Digital link fails | Termination, clock | Check eye diagram, clock quality |
| Crosstalk | Layout, common impedance | Review routing, add shielding |
| Intermittent | Mechanical, thermal | Flex test, thermal cycle |

### Isolation Techniques

1. **Analog-Digital Isolation:**
   Remove isolation resistor between analog and digital power
   → Power analog only → measure noise
   → If noise is low, problem is digital coupling

2. **Per-Channel Isolation:**
   Disconnect suspect channel from AFE input
   → If noise drops, problem is input network
   → If noise stays, problem is AFE or downstream

3. **Clock Isolation:**
   Temporarily stop clock (put AFE in standby)
   → If noise drops dramatically, clock coupling is the issue

---

## Documentation Template

### Bring-Up Report

```
Board ID: _______________
Date: _______________
Engineer: _______________

Phase 1 - Power:
  VIN: _____V  AVDD: _____V  DVDD: _____V  VREF: _____V
  AVDD noise: _____mVpp
  Current draw: _____mA
  Status: PASS / FAIL

Phase 2 - Clock:
  Frequency: _____MHz
  Status: PASS / FAIL

Phase 3 - Digital:
  Chip ID: _____
  Throughput: _____MB/s
  Dropped packets (1 min): _____
  Status: PASS / FAIL

Phase 4 - Noise:
  Average noise (shorted): _____µVrms
  Worst channel: _____µVrms
  Line noise: _____dB
  Status: PASS / FAIL

Phase 5 - Calibration:
  Channel mapping verified: YES / NO
  Gain error: _____%
  Status: PASS / FAIL

Phase 6 - Scale-Up:
  128-channel status: PASS / FAIL

Phase 7 - Validation:
  24-hour test: PASS / FAIL

OVERALL STATUS: _______________

Notes:
_________________________________
_________________________________
_________________________________
```
