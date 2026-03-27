# 01 - Requirements Template

## ⚠️ FREEZE THESE BEFORE DRAWING ANYTHING

Fill in every field. If you cannot answer a field, you are designing blind.

---

## Signal Acquisition

| Parameter | Value | Notes |
|-----------|-------|-------|
| Channel count | 128 | |
| Modalities | [ ] Spikes only (300–6000 Hz) | |
| | [ ] LFP + Spikes (1–6000 Hz) | |
| | [ ] DC-coupled (for stimulation artifacts) | |
| Sample rate per channel | ______ kS/s | |
| Input range (electrode signal) | ______ mVpp | Typical: ±5 mV |
| Input-referred noise target | ______ µVrms | Band-limited to signal BW |
| ADC resolution required | ______ bits | Calculated from range/noise |

### Noise Budget Calculation

```
Effective bits needed = log₂(Input Range / Noise Floor)

Example:
  Input range = 10 mVpp = 10,000 µV
  Noise floor target = 5 µVrms
  Dynamic range = 10,000 / 5 = 2000
  Bits needed = log₂(2000) ≈ 11 bits

  → Use 16-bit ADC for margin
```

Your calculation:
- Input range: ______ µV
- Noise target: ______ µVrms  
- Bits needed: ______
- ADC selected: ______ bits

---

## Electrode Interface

| Parameter | Value | Notes |
|-----------|-------|-------|
| Electrode type | [ ] MEA (planar) | |
| | [ ] Omnetics connector | |
| | [ ] Custom probe | |
| | [ ] Other: ____________ | |
| Electrode impedance range | ______ kΩ to ______ MΩ | At 1 kHz |
| Reference electrode type | [ ] Dedicated reference | |
| | [ ] Average reference | |
| | [ ] User-selectable | |
| Expected environment | [ ] Saline bath | |
| | [ ] Gel/agarose | |
| | [ ] Air (dry electrodes) | |

---

## Data Throughput

| Parameter | Calculation | Value |
|-----------|-------------|-------|
| Bits per sample | ADC resolution | ______ bits |
| Samples per second (total) | channels × fs | ______ S/s |
| Raw data rate | bits × samples / 8 | ______ MB/s |
| With overhead (~20%) | raw × 1.2 | ______ MB/s |

### Throughput Example (128ch @ 30 kS/s, 16-bit)
```
128 × 30,000 × 16 / 8 = 7.68 MB/s raw
With overhead: ~9.2 MB/s
```

Your throughput: ______ MB/s

---

## Digital Interface

| Parameter | Selection |
|-----------|-----------|
| Primary egress | [ ] USB 2.0 HS (480 Mbps, ~40 MB/s practical) |
| | [ ] USB 3.0 SS (5 Gbps, ~400 MB/s practical) |
| | [ ] Gigabit Ethernet (~100 MB/s practical) |
| | [ ] Board-to-board (to separate carrier) |
| | [ ] PCIe (rare for this application) |
| Timestamping | [ ] Hardware timestamp per packet |
| | [ ] Sequence number only |
| | [ ] External sync input |

---

## Power

| Parameter | Value |
|-----------|-------|
| Power source | [ ] USB bus power (500 mA / 900 mA) |
| | [ ] External DC (voltage: ______V) |
| | [ ] Battery |
| Estimated analog power | ______ mW |
| Estimated digital power | ______ mW |
| Thermal constraints | [ ] Passive cooling only |
| | [ ] Active cooling allowed |

---

## Physical

| Parameter | Value |
|-----------|-------|
| Max board dimensions | ______ × ______ mm |
| Layer count budget | [ ] 4-layer |
| | [ ] 6-layer (recommended) |
| | [ ] 8-layer |
| Mounting | [ ] Standoffs |
| | [ ] Enclosure |
| | [ ] Stacking on carrier |

---

## Testability Requirements

| Feature | Required | Notes |
|---------|----------|-------|
| Input shorting option | [ ] Yes [ ] No | For noise floor measurement |
| Calibration injection | [ ] Yes [ ] No | Known signal injection |
| Per-rail test points | [ ] Yes [ ] No | |
| Analog/digital isolation jumper | [ ] Yes [ ] No | Debug aid |
| JTAG/SWD access | [ ] Yes [ ] No | For MCU/FPGA |

---

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System architect | | | |
| Analog designer | | | |
| Digital designer | | | |
| Layout engineer | | | |

**Requirements frozen date:** _______________

**Change control:** Any changes after freeze require documented justification and sign-off.
