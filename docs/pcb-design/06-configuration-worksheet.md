# 06 - Configuration Worksheet

## ⚠️ FILL THIS OUT FIRST

This worksheet captures the two critical parameters needed for precise design directions. Complete this before requesting detailed guidance.

---

## Your Configuration

### Sample Rate

```
fs = ________ kS/s per channel
```

Common values:
| Application | Typical fs |
|-------------|------------|
| LFP only (1-300 Hz) | 1-2 kS/s |
| Spikes (300-6000 Hz) | 20-30 kS/s |
| Spikes + headroom | 30-40 kS/s |
| Wideband (LFP + spikes) | 30 kS/s |

### Digital Egress

```
egress = ________________
```

Options:
- [ ] USB 2.0 (easiest, limited to ~40 MB/s practical)
- [ ] USB 3.0 (higher bandwidth, more complex)
- [ ] Gigabit Ethernet (good for distance, moderate complexity)
- [ ] Board-to-board (headstage → separate carrier)

---

## Bandwidth Calculation

Given your fs, calculate required bandwidth:

```
Channels:        128
Sample rate:     ________ kS/s
Bits per sample: 16

Raw data rate = 128 × fs × 16 / 8 = ________ MB/s
With overhead:  ________ MB/s × 1.2 = ________ MB/s
```

### Interface Feasibility Check

| Interface | Max Practical BW | Your Requirement | Feasible? |
|-----------|------------------|------------------|-----------|
| USB 2.0 HS | ~40 MB/s | ________ MB/s | ☐ Yes ☐ No |
| USB 3.0 SS | ~400 MB/s | ________ MB/s | ☐ Yes ☐ No |
| Gigabit Ethernet | ~100 MB/s | ________ MB/s | ☐ Yes ☐ No |
| Board-to-board LVDS | >100 MB/s | ________ MB/s | ☐ Yes ☐ No |

---

## Example Configurations

### Configuration A: Low-Rate LFP System
```
fs = 2 kS/s per channel
egress = USB 2.0

Calculation:
  128 × 2000 × 16 / 8 = 0.51 MB/s
  With overhead: 0.6 MB/s
  
USB 2.0 feasibility: YES (0.6 << 40 MB/s)

Recommendation:
  - Single-board possible
  - Simple USB 2.0 implementation
  - 4-layer PCB acceptable
```

### Configuration B: Standard Spike Recording
```
fs = 30 kS/s per channel
egress = USB 3.0

Calculation:
  128 × 30000 × 16 / 8 = 7.68 MB/s
  With overhead: 9.2 MB/s
  
USB 3.0 feasibility: YES (9.2 << 400 MB/s)
USB 2.0 feasibility: YES (9.2 < 40 MB/s, but tight)

Recommendation:
  - USB 2.0 possible but margin is low
  - USB 3.0 recommended for headroom
  - 6-layer PCB recommended
```

### Configuration C: High-Rate Research System
```
fs = 40 kS/s per channel
egress = Gigabit Ethernet

Calculation:
  128 × 40000 × 16 / 8 = 10.24 MB/s
  With overhead: 12.3 MB/s
  
Gigabit Ethernet feasibility: YES (12.3 << 100 MB/s)

Recommendation:
  - Ethernet provides distance flexibility
  - May want headstage + carrier split
  - 6-layer PCB required
```

### Configuration D: Headstage + Carrier Architecture
```
fs = 30 kS/s per channel
egress = Board-to-board to carrier

Headstage → LVDS → Carrier → USB 3.0 → Host

Recommendation:
  - Headstage: compact, 4-layer, LVDS out
  - Carrier: 6-layer, FPGA/MCU, USB 3.0
  - Best noise isolation
  - Most flexibility
```

---

## Your Selection

Complete this section:

```
Selected sample rate: fs = ________ kS/s

Selected egress: ________________

Calculated data rate: ________ MB/s

Architecture decision:
  [ ] Single board
  [ ] Headstage + Carrier

Layer count:
  [ ] 4-layer
  [ ] 6-layer
```

---

## Next Steps

Once you complete this worksheet, you will receive:

1. **Concrete schematic sheet checklist** — exact signals/rails to include
2. **Recommended layer stackup** — impedance targets, layer assignment
3. **Connector pinout strategy** — 128-channel routing plan
4. **Customized bring-up checklist** — specific to your architecture

---

## Submit Your Configuration

Provide the following:

```
fs = ____ kS/s per channel
egress = USB / Ethernet / board-to-board to carrier
```

Example response:
```
fs = 30 kS/s per channel
egress = USB 3.0
```
