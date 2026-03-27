# 00 - Board Architecture Decision

## The Three Board Types (Don't Confuse Them)

| Type | What It Contains | Proximity to Electrodes |
|------|------------------|-------------------------|
| **AFE Headstage** | Analog front-end + ADC | Closest (cm scale) |
| **Digital Carrier** | FPGA/MCU, USB/Ethernet, storage | Further (can be meters) |
| **Breakout/Adapter** | Connectors, protection, routing | At electrode interface |

## 128-Channel Recommendation

For 128 channels, **always separate (1) and (2)** with a controlled interconnect.

```
┌─────────────────┐         ┌──────────────────┐
│  AFE Headstage  │◄───────►│  Digital Carrier │
│  (Analog Core)  │  short  │  (FPGA/MCU/USB)  │
│                 │  cable  │                  │
└────────┬────────┘         └──────────────────┘
         │
         ▼
   ┌───────────┐
   │ Electrodes│
   │ (MEA/etc) │
   └───────────┘
```

### Why Separate?

| Problem | Single-Board | Two-Board |
|---------|--------------|-----------|
| Digital switching noise coupling into analog | Severe | Controllable |
| Thermal management | Hard (MCU heats AFE) | Isolated |
| Iteration speed | Respin entire system | Update carrier only |
| Form factor at electrodes | Bulky | Compact headstage |
| EMI compliance | Nightmare | Manageable |

## Interconnect Options (Headstage ↔ Carrier)

| Method | Bandwidth | Length | Complexity |
|--------|-----------|--------|------------|
| Flex cable (LVDS) | High | 5–30 cm | Medium |
| Board-to-board connector | High | <5 cm | Low |
| SPI over shielded cable | Medium | <1 m | Low |
| Serialized LVDS (e.g., TI DS90) | Very high | 1–3 m | High |

### Recommended Starting Point

**Board-to-board connector** (Samtec, Hirose) if headstage sits on carrier.
**Flex cable with LVDS** if headstage needs physical separation (typical for MEA setups).

## Decision Checkpoint

Before proceeding, confirm:

- [ ] I am building a **headstage board** (analog core)
- [ ] I am building a **carrier board** (digital backend)
- [ ] I am building **both** as a system
- [ ] Interconnect method selected: _______________
- [ ] Maximum interconnect length: _______________ cm

## Single-Board Exception

Only consider single-board if:
- Channel count < 32
- Sample rate < 10 kS/s
- You have extensive mixed-signal layout experience
- Physical constraints absolutely require it

Even then, **partition the layout** as if they were separate boards.
