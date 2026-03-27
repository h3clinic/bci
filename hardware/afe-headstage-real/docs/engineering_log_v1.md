# v1 RHD2132 Headstage — Engineering Log

**Rev:** 1.1 · **Date:** 2026-02-27 · **Status:** Pre-routing, all gates green

This log documents bugs found, root causes, fixes applied, and verification
gates established during script-generated schematic + PCB development.
It is the "what went wrong and how we proved it's right" companion to
`design_notes_v1.md`.

---

## 0. Reproducibility

### 0.1 Environment

| Tool | Version | Notes |
|------|---------|-------|
| KiCad CLI | 9.0.7 | `/opt/homebrew/bin/kicad-cli` |
| Python | 3.14.3 | System python3 via Homebrew |
| pytest | 9.0.2 | |
| Ruff | 0.15.2 (9d18ee911 2026-02-19) | |
| OS | macOS 15.5 (Darwin 24.5.0 arm64) | Apple Silicon |

### 0.2 Reproduce clean state from scratch

All commands run from `hardware/afe-headstage-real/`:

```bash
# 1. Generate schematic
python3 gen_schematic_v1_rhd2132.py

# 2. Run ERC (must be 0 errors, 0 warnings)
kicad-cli sch erc --format json --severity-all \
    -o erc_report.json afe-headstage-v1.kicad_sch

# 3. Export netlist (for net membership verification)
kicad-cli sch export netlist \
    -o afe-headstage-v1.xml afe-headstage-v1.kicad_sch

# 4. Generate PCB
python3 gen_pcb_v1.py

# 5. Run DRC with schematic parity (must be 0 parity, 0 electrical)
kicad-cli pcb drc --schematic-parity --format json \
    -o drc_report.json afe-headstage-v1.kicad_pcb

# 6. Run full test suite (must be 97/97)
python3 -m pytest test_parse_drc.py test_schematic_invariants.py -v
```

### 0.3 Event timeline

| Date | Commit | Event |
|------|--------|-------|
| 2026-02-26 | — | Initial schematic + PCB generators, CI guardrails (27 tests) |
| 2026-02-26 | — | pin_abs() rot=90/270 swap fix, removed PCB net swap hacks |
| 2026-02-26 | — | REF_ELEC connectivity bug found — colinear overlap root cause |
| 2026-02-26 | — | 5 colinear overlaps fixed, ERC 0/0 achieved |
| 2026-02-27 | — | Net membership tests (12 nets), colinear lint on .kicad_sch output |
| 2026-02-27 | — | Parity + electrical DRC gates, closed-world DRC type enforcement |
| 2026-02-27 | — | Net class enforcement (Gate A), EP invariants (Gate B) |
| 2026-02-27 | — | ELEC_TEST → ELECTRODE, ADC_ref → POWER net class fixes |
| 2026-02-27 | 1f64324 | Pre-routing freeze: 97/97 tests green |

---

## 1. Schematic / PCB Correctness Fixes

### 1.1 Net naming mismatches

**Symptom:** Schematic↔PCB parity DRC produced spurious violations.
**Root cause:** AVDD label vs global label inconsistency; `ADC_ref` global
label missing from schematic.
**Fix:** Unified all net names between schematic generator and PCB generator.

### 1.2 `pin_abs()` rotation formula was wrong

**Symptom:** PCB pad-to-net assignments required ad-hoc pin/net swaps to
produce correct connectivity.
**Root cause:** The rot=90 and rot=270 formulas in `pin_abs()` were swapped.
The function computes KiCad schematic coordinates from symbol-local pin
coordinates; getting 90/270 confused silently produces wrong wire endpoints.
**Fix:** Corrected formulas, removed all swap hacks. Verified against KiCad
wire endpoint positions for Device:R, FerriteBead, and RHD2132 REF pin.
**Gate:** `TestPinAbsAnchoredToKicad` — 4 tests parse the generated
`.kicad_sch` and verify wire endpoints land on computed pin positions.

### 1.3 Test point numbering drift

**Symptom:** TP4–TP10 in the schematic didn't match PCB expectations.
**Root cause:** Schematic generator placement order diverged from PCB
generator's assumed TP numbering.
**Fix:** Aligned both generators to a single numbering convention.

### 1.4 NC pad net conflicts

**Symptom:** J1 pad 11 (MISO2) and U1 pad 27 (AUXOUT) caused parity
violations as "extra net" / "missing net."
**Root cause:** KiCad expects unconnected pads to have explicit
`unconnected-(Ref-Name-PadN)` net names for clean parity.
**Fix:** Assigned explicit unconnected net names in both generators.

### 1.5 Missing Footprint properties in schematic symbol instances

**Symptom:** Mass `footprint_symbol_mismatch` DRC violations.
**Root cause:** Schematic symbol instances lacked Footprint property fields.
**Fix:** Added Footprint properties to all symbol instances in the schematic
generator.

---

## 2. REF_ELEC Connectivity Bug

This was the most consequential bug found during development.

### 2.1 Symptom

- ERC reported `global_label_dangling` on `REF_ELEC`.
- U1 pin 10 (REF) was not reliably connected to the REF_ELEC net.
- Netlist export showed U1.10 missing from REF_ELEC members.

### 2.2 Wrong hypothesis (rejected)

> "KiCad has a single-sheet quirk where it sometimes misreports global
> label connectivity. The wiring is fine; the warning is cosmetic."

This was wrong. The warning indicated a **real connectivity defect**.
Dismissing tool warnings as "KiCad quirks" without proof is how you
ship open nets.

### 2.3 Actual root cause

The schematic generator produced **overlapping colinear wire segments**.
Two failure modes:

1. **Colinear overlap:** Two wire segments at the same Y-coordinate with
   overlapping X-ranges (beyond a shared endpoint). KiCad's connectivity
   engine splits the overlap region and may assign fragments to different
   nets. This is not a KiCad bug — it's the generator producing
   ambiguous topology that KiCad resolves unpredictably.

2. **Pass-through wire:** A single wire from point A through pin B to
   point C gets split at pin B. The far segment (B→C) may not bind to
   the intended net.

### 2.4 Fix pattern

**Rule: every wire segment connects exactly two electrical vertices.
No pass-through. No overlapping segments.**

Applied to five sites:

| Location | Old topology | New topology |
|----------|-------------|-------------|
| REF_ELEC | Single wire glabel→U1.REF passing through R1.pin2 | Two segments: glabel→R1.pin2, R1.pin2→U1.REF |
| +3V3 bus (y=25.4) | PWR_FLAG/C2/TP_VDD all extending to fb_in (3 overlaps) | Chain: PWR_FLAG→C2→TP_VDD→fb_in |
| AVDD bus (y=25.4) | C4/TP_AVDD overlapping | Chain: C4→TP_AVDD→AVDD label |
| SPI TPs (x=185.42) | TP_SCLK/TP_MISO at same X (vertical overlap) | TP_MISO offset to different X-column |

### 2.5 Enforcement

| Gate | Method | Assertion |
|------|--------|-----------|
| ERC | `kicad-cli sch erc` JSON | 0 errors, 0 warnings (no allowlisting) |
| Net membership | `kicad-cli sch export netlist` | REF_ELEC = {U1.10, J5.33, TP6.1, R1.2} |
| Colinear overlap | Parse `.kicad_sch` wire segments, O(n²) scan | 0 overlaps |
| Pin not orphaned | Netlist membership | U1.10 not in any `unconnected-*` net |
| R1 grounded | Netlist membership | R1.1 on GND |

---

## 3. Exposed Pad (EP) Reliability

### 3.1 Problem

The original EP implementation had F.Paste on the full 4.8×4.8mm pad.
During reflow, this causes:
- Solder voiding (trapped flux gas)
- Die float / tombstoning on adjacent pads
- Poor thermal coupling

### 3.2 Fix

- **Removed F.Paste** from EP main pad
- Added **16 paste aperture windows** (4×4 grid, 0.9×0.9mm, ~56% coverage)
- Added **9 thermal vias** (3×3 grid, 0.3mm drill, 0.6mm pad)
- All vias **tented front and back** (prevents solder wicking)
- All vias on **GND net**

### 3.3 Gate

`TestEPFootprintInvariants` — 8 tests parse the generated `.kicad_pcb`:

| Test | Locked value |
|------|-------------|
| EP main pad has no F.Paste | ✓ |
| Paste aperture count | 16 |
| Paste aperture size | 0.9×0.9mm |
| Thermal via count | 9 |
| Thermal via drill | 0.3mm |
| Thermal via pad size | 0.6mm |
| Thermal vias tented | front + back, all 9 |
| Thermal vias on GND | all 9 |

---

## 4. Verification Gate Inventory

### 4.1 Hard gates (must pass before routing)

| Gate | Source | Assertion |
|------|--------|-----------|
| ERC errors | `kicad-cli sch erc` | 0 |
| ERC warnings | `kicad-cli sch erc --severity-all` | 0 |
| Schematic parity | `kicad-cli pcb drc --schematic-parity` | 0 issues |
| Electrical DRC | `kicad-cli pcb drc` JSON classification | All 8 electrical categories = 0 |
| Unknown DRC types | Closed-world check | Every violation type in ELECTRICAL ∪ COSMETIC |
| Net membership | `kicad-cli sch export netlist` | 12 critical nets verified |
| Colinear overlap | Parse `.kicad_sch` wire segments | 0 overlaps |
| Net class: electrode | Parse `.kicad_pro` | CH0–CH31 + REF_ELEC + ELEC_TEST in ELECTRODE |
| Net class: SPI | Parse `.kicad_pro` | 7 SPI nets in DIGITAL_SPI |
| Net class: power | Parse `.kicad_pro` | +3V3, AVDD, ADC_ref in POWER |
| Track width: electrode | Parse `.kicad_pro` | ≤0.15mm |
| Track width: power | Parse `.kicad_pro` | ≥0.3mm |
| EP invariants | Parse `.kicad_pcb` | 8 sub-checks (see §3.3) |

### 4.2 Electrical DRC taxonomy (closed-world)

**Must be zero:**
`shorting_items`, `clearance`, `net_conflict`, `copper_edge_clearance`,
`edge_clearance`, `tracks_crossing`, `via_dangling`, `zone_priority`

**Allowed to float (cosmetic):**
`lib_footprint_mismatch`, `silk_over_copper`, `silk_overlap`,
`silk_edge_clearance`, `courtyards_overlap`

**Any type not in either set → test fails immediately.** This prevents
silent rot when KiCad adds new violation types.

### 4.3 Net class data fixed during development

Two nets were found in Default that should not have been:

| Net | Was | Now | Why |
|-----|-----|----|-----|
| ELEC_TEST | Default | ELECTRODE | Routes to U1 elec_test pin alongside electrode inputs |
| ADC_ref | Default | POWER | Analog reference voltage, needs POWER clearance/width |

### 4.4 Test count

| File | Tests | Category |
|------|-------|----------|
| `test_parse_drc.py` | 27 | DRC report parsing + CI governance |
| `test_schematic_invariants.py` | 70 | Schematic/PCB structural invariants |
| **Total** | **97** | All passing |

---

## 5. Pre-Routing Freeze Criteria

Routing is not permitted until **all** of the following are true.
This is not a guideline — it is enforced by `test_schematic_invariants.py`.

| # | Criterion | Test | Status |
|---|-----------|------|--------|
| 1 | ERC errors = 0 | `TestErcSeverityGate::test_zero_erc_errors` | ✅ |
| 2 | ERC warnings = 0 (no allowlist) | `TestErcSeverityGate::test_erc_warnings_classified` | ✅ |
| 3 | Schematic↔PCB parity = 0 | `TestPcbDrc::test_zero_schematic_parity_issues` | ✅ |
| 4 | Electrical DRC categories = 0 | `TestPcbDrc::test_zero_electrical_drc_violations` | ✅ |
| 5 | No unknown DRC violation types | `TestPcbDrc::test_no_unknown_drc_violation_types` | ✅ |
| 6 | All 12 critical nets verified | `TestCriticalNetMembership` (12 tests) | ✅ |
| 7 | 0 colinear wire overlaps | `TestColinearOverlapLint::test_zero_colinear_overlaps` | ✅ |
| 8 | Net classes correct | `TestNetClassEnforcement` (5 tests) | ✅ |
| 9 | EP footprint locked | `TestEPFootprintInvariants` (8 tests) | ✅ |

Freeze achieved: **2026-02-27**, 97/97 tests passing.

**Scope boundary:** This freeze guarantees schematic correctness, parity,
EP reliability, and net-class integrity. It does **not** guarantee signal
integrity, return path continuity, or analog performance. Those properties
require routed-board DRC, impedance analysis, and measurement.

**Version coupling:** These gates are validated against KiCad 9.0.7.
Changes in KiCad's DRC classification taxonomy or JSON output structure
may require updating the closed-world type sets in
`test_schematic_invariants.py` (§4.2).

---

## 6. Design Rules Learned

### Cardinal rule

> **The generator must never rely on implicit KiCad topology resolution.
> All electrical connections must be expressed as explicit wire segments
> between electrical vertices. No pass-through wires. No overlapping
> segments. No assumptions about how KiCad resolves ambiguous geometry.**

This rule was learned from the REF_ELEC defect (§2). Violating it
produces connectivity failures that are silent at generation time and
only visible through ERC or netlist export. It cost multiple debug
cycles to identify.

### Supporting rules

1. **Never draw a wire that passes through a pin unless it terminates there.**
   KiCad splits pass-through wires at the pin, and the far segment may not
   bind to the intended net.

2. **Never create colinear overlapping wire segments.**
   KiCad's connectivity engine may assign overlapping fragments to different
   nets. This was the root cause of the REF_ELEC defect.

3. **Build nets as explicit segment chains between electrical vertices.**
   Every wire segment should connect exactly two electrical points
   (pin endpoint, junction, label). No redundant overlapping segments.

4. **"Internal self-consistency" ≠ correctness.**
   Tests that only check the generator's internal data structures prove
   nothing about what KiCad will do with the output. All correctness tests
   must parse KiCad tool outputs (ERC JSON, netlist export, DRC JSON, or
   the `.kicad_sch`/`.kicad_pcb` files directly).

5. **Closed-world classification prevents silent rot.**
   When asserting "no electrical DRC violations," you must also assert that
   every observed violation type is in your known set. Otherwise a new KiCad
   violation type gets silently treated as cosmetic.

6. **DRC parity depends on stable project context.**
   `kicad-cli pcb drc --schematic-parity` behavior can vary if the working
   directory or project file association changes. Always run from the project
   directory with explicit paths.

7. **Don't dismiss tool warnings as quirks without proof.**
   The initial REF_ELEC hypothesis ("KiCad misreports this") was wrong.
   If a tool reports a problem and you think it's wrong, prove it with a
   second tool output — don't explain it away.

---

## 7. What's Next

Board is at routing-readiness. All pre-routing gates are green.

**Routing order:**
1. Electrode corridor (CH0–CH31 + REF_ELEC + ELEC_TEST): J5 → U1,
   shortest paths, minimize vias on electrode inputs
2. SPI: J1 → R2–R4 → U1, routed away from electrode region
3. Power: J1 → FB1 → U1 VDD pins, wide traces

**Routing constraints enforced by net classes:**
- ELECTRODE: 0.15mm track, 0.3mm clearance, 0.2mm via drill
- DIGITAL_SPI: 0.2mm track, 0.2mm clearance
- POWER: 0.4mm track, 0.25mm clearance, 0.4mm via drill
