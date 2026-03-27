#!/usr/bin/env python3
"""
gen_schematic_v1_rhd2132.py — v1 Neural AFE Headstage for Intan RHD USB System
================================================================================

Architecture:
  - RHD2132 (32-ch neural AFE, QFN-56, unipolar inputs)
  - Omnetics A79024-001 (36-pin nano-strip) for electrode input (J5)
  - Omnetics PZN-12 (12-pin polarized nano) for SPI cable to Intan USB board (J1)
  - Power from Intan USB interface board via SPI cable (3.3V analog + digital)
  - No on-board LDO — power rail is pre-regulated by Intan interface
  - 16 channels wired for v1, all 32 routed on connector for v2
  - Ferrite bead isolates AVDD from DVDD
  - Test points on power rails, SPI bus, and 4 electrode channels

Target backend: Intan RHD2000 USB interface board (known-good)
Cable: ~30 cm Omnetics PZN-12 SPI cable
Faraday cage: aluminum mesh tabletop around MEA + headstage
Medium: ACSF, room temp → 37°C later

RHD2132 QFN-56 Pin Map (from Intan RHD2000 series datasheet):
  Pins 1-32:  IN[0]-IN[31] — amplifier electrode inputs (unipolar)
  Pin 33:     elec_test — electrode test input
  Pin 34:     REF — reference electrode
  Pin 35:     VDD (analog supply, 3.3V)
  Pin 36:     VDD (analog supply, 3.3V)
  Pin 37:     MISO — SPI master-in slave-out
  Pin 38:     MOSI — SPI master-out slave-in
  Pin 39:     SCLK — SPI clock
  Pin 40:     CS_b — SPI chip select (active low)
  EP (pad 41): GND — exposed thermal/ground pad

  Additional (from datasheet ballmap, functional blocks):
  auxin1, auxin2, auxin3: auxiliary ADC inputs
  auxout: auxiliary DAC output
  ADCref: ADC reference (bypass to GND with 10nF)
  VESD: ESD clamp (tie to GND)
  resA/resB: reserved (leave floating or tie to VDD per datasheet)

Omnetics PZN-12 SPI Cable Pinout (Intan standard):
  Pin 1:  MISO (MISO1)
  Pin 2:  MOSI
  Pin 3:  SCLK
  Pin 4:  CS
  Pin 5:  GND
  Pin 6:  GND
  Pin 7:  VDD_D (+3.3V digital)
  Pin 8:  VDD_D (+3.3V digital)
  Pin 9:  VDD_A (+3.3V analog)
  Pin 10: VDD_A (+3.3V analog)
  Pin 11: MISO2 (optional, unused for single-chip headstage)
  Pin 12: GND

Omnetics A79024-001 (36-pin nano-strip, electrode connector):
  Pins 1-32: electrode channels IN[0]-IN[31]
  Pin 33: REF electrode
  Pin 34: GND (shield/guard)
  Pin 35: elec_test
  Pin 36: GND (shield/guard)
"""

import json
import os
import sys
import uuid

G = 2.54  # mm per grid step


def uid():
    return str(uuid.uuid4())


def g(n):
    """Grid units → mm, rounded to 2 decimal places."""
    return round(n * G, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CANONICAL COORDINATE TRANSFORM — the ONLY place we do Y-inversion
# ═══════════════════════════════════════════════════════════════════════════


def pin_abs(cx, cy, rot, lx, ly):
    """Absolute screen coordinate of a pin connection point.

    This is the ONLY function that converts from symbol-local (Y-up)
    to schematic (Y-down) coordinates.  All routing must use this.
    """
    if rot == 0:
        ax, ay = cx + lx, cy - ly
    elif rot == 90:
        ax, ay = cx - ly, cy - lx
    elif rot == 180:
        ax, ay = cx - lx, cy + ly
    elif rot == 270:
        ax, ay = cx + ly, cy + lx
    else:
        raise ValueError(f"Unsupported rotation: {rot}")
    return round(ax, 2), round(ay, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 2. ENDPOINT UNIQUENESS REGISTRY
# ═══════════════════════════════════════════════════════════════════════════


class WireRegistry:
    """Tracks every wire endpoint → net assignment and detects colinear overlaps."""

    def __init__(self):
        self._coord_to_net = {}
        self._violations = []
        self._segments = []  # (x1, y1, x2, y2) for colinear overlap detection

    def register(self, x, y, net_name):
        key = (round(x, 2), round(y, 2))
        if key in self._coord_to_net:
            existing = self._coord_to_net[key]
            if existing != net_name and existing != "__junction__":
                self._violations.append(
                    f"COLLISION at ({key[0]}, {key[1]}): "
                    f"net '{existing}' already occupies this point, "
                    f"tried to add net '{net_name}'"
                )
                return key
        self._coord_to_net[key] = net_name
        return key

    def register_segment(self, x1, y1, x2, y2):
        """Record a wire segment for colinear overlap checking."""
        self._segments.append((round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)))

    def register_junction(self, x, y):
        key = (round(x, 2), round(y, 2))
        return key

    def _check_colinear_overlaps(self):
        """Detect overlapping colinear wire segments.

        Two horizontal segments overlap if they share the same Y and their
        X-ranges intersect (beyond a single shared endpoint).
        Same logic for vertical segments sharing the same X.
        """
        overlaps = []
        n = len(self._segments)
        for i in range(n):
            x1a, y1a, x2a, y2a = self._segments[i]
            for j in range(i + 1, n):
                x1b, y1b, x2b, y2b = self._segments[j]
                # Horizontal: same Y
                if y1a == y2a == y1b == y2b:
                    lo_a, hi_a = min(x1a, x2a), max(x1a, x2a)
                    lo_b, hi_b = min(x1b, x2b), max(x1b, x2b)
                    overlap = min(hi_a, hi_b) - max(lo_a, lo_b)
                    if overlap > 0.01:  # more than a shared endpoint
                        overlaps.append(
                            f"COLINEAR OVERLAP on y={y1a}: "
                            f"[{lo_a}..{hi_a}] ∩ [{lo_b}..{hi_b}] = {overlap:.2f}mm"
                        )
                # Vertical: same X
                elif x1a == x2a == x1b == x2b:
                    lo_a, hi_a = min(y1a, y2a), max(y1a, y2a)
                    lo_b, hi_b = min(y1b, y2b), max(y1b, y2b)
                    overlap = min(hi_a, hi_b) - max(lo_a, lo_b)
                    if overlap > 0.01:
                        overlaps.append(
                            f"COLINEAR OVERLAP on x={x1a}: "
                            f"[{lo_a}..{hi_a}] ∩ [{lo_b}..{hi_b}] = {overlap:.2f}mm"
                        )
        return overlaps

    def check(self):
        overlaps = self._check_colinear_overlaps()
        all_errors = self._violations + overlaps
        if all_errors:
            print("\n╔══ WIRE TOPOLOGY DEFECT DETECTED ══╗", file=sys.stderr)
            for v in all_errors:
                print(f"  ✗ {v}", file=sys.stderr)
            print(
                f"╚══ {len(all_errors)} defect(s) — BUILD FAILED ══╝",
                file=sys.stderr,
            )
            sys.exit(1)

    def summary(self):
        nets = set(self._coord_to_net.values())
        return f"{len(self._coord_to_net)} endpoints, {len(nets)} unique nets"


WREG = WireRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# 3. SYMBOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

LIB_SYMBOLS = {}


def register_sym(lib_id, text):
    LIB_SYMBOLS[lib_id] = text


# ── Power symbols ─────────────────────────────────────────────────────────

register_sym(
    "power:GND",
    """
    (symbol "power:GND"
      (power)
      (pin_numbers (hide yes))
      (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -6.35 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "GND" (at 0 -3.81 0)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Power symbol creates a global label with name \\"GND\\" , ground"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "global power"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "GND_0_1"
        (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 270) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""",
)

register_sym(
    "power:+3V3",
    """
    (symbol "power:+3V3"
      (power)
      (pin_numbers (hide yes))
      (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "+3V3" (at 0 3.556 0)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Power symbol creates a global label with name \\"+3V3\\""
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "global power"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "+3V3_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27))
          (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "+3V3_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""",
)

register_sym(
    "power:PWR_FLAG",
    """
    (symbol "power:PWR_FLAG"
      (power)
      (pin_numbers (hide yes))
      (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#FLG" (at 0 1.905 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "PWR_FLAG" (at 0 3.81 0)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Special symbol for telling ERC where power comes from"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "flag power"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "PWR_FLAG_0_0"
        (pin power_out line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
      (symbol "PWR_FLAG_0_1"
        (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (embedded_fonts no)
    )""",
)

# ── Device symbols ────────────────────────────────────────────────────────

register_sym(
    "Device:R",
    """
    (symbol "Device:R"
      (pin_numbers (hide yes))
      (pin_names (offset 0))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 2.032 0 90)
        (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 90)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at -1.778 0 90)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Resistor"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "R res resistor"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "R_*"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "R_0_1"
        (rectangle (start -1.016 -2.54) (end 1.016 2.54)
          (stroke (width 0.254) (type default)) (fill (type none)))
      )
      (symbol "R_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""",
)

register_sym(
    "Device:C",
    """
    (symbol "Device:C"
      (pin_numbers (hide yes))
      (pin_names (offset 0.254))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 0.635 2.54 0)
        (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "C" (at 0.635 -2.54 0)
        (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 0.9652 -3.81 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Unpolarized capacitor"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "cap capacitor"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "C_*"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "C_0_1"
        (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762))
          (stroke (width 0.508) (type default)) (fill (type none)))
        (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762))
          (stroke (width 0.508) (type default)) (fill (type none)))
      )
      (symbol "C_1_1"
        (pin passive line (at 0 3.81 270) (length 2.794)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 2.794)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""",
)

register_sym(
    "Device:FerriteBead",
    """
    (symbol "Device:FerriteBead"
      (pin_numbers (hide yes))
      (pin_names (offset 0))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "FB" (at -3.81 0.635 90)
        (effects (font (size 1.27 1.27))))
      (property "Value" "FerriteBead" (at 3.81 0 90)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at -1.778 0 90)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Ferrite bead"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "L ferrite bead inductor filter"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "Inductor_* L_* *Ferrite*"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "FerriteBead_0_1"
        (polyline (pts (xy -2.7686 0.4064) (xy -1.7018 2.2606) (xy 2.7686 -0.3048) (xy 1.6764 -2.159) (xy -2.7686 0.4064))
          (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 1.27) (xy 0 1.2954))
          (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 -1.27) (xy 0 -1.2192))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "FerriteBead_1_1"
        (pin passive line (at 0 3.81 270) (length 2.54)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 2.54)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""",
)

register_sym(
    "Connector:TestPoint",
    """
    (symbol "Connector:TestPoint"
      (pin_numbers (hide yes))
      (pin_names (offset 0.762) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "TP" (at 0 6.858 0)
        (effects (font (size 1.27 1.27))))
      (property "Value" "TestPoint" (at 0 5.08 0)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 5.08 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 5.08 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "test point"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "test point tp"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "Pin* Test*"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "TestPoint_0_1"
        (circle (center 0 3.302) (radius 0.762)
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "TestPoint_1_1"
        (pin passive line (at 0 0 90) (length 2.54)
          (name "1" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""",
)


# ═══════════════════════════════════════════════════════════════════════════
# 4. COMPONENT PIN DATABASES
# ═══════════════════════════════════════════════════════════════════════════

PLEN = 5.08  # pin stub length

# ── RHD2132 (QFN-56) ─────────────────────────────────────────────────────
#
# Physical: QFN-56, 8×8mm body, 0.5mm pitch, exposed pad (EP)
# 57 pads total (56 perimeter + 1 EP)
#
# Pin numbering: PHYSICAL QFN-56 pad numbers from Intan Eagle library.
# This ensures schematic pin N == PCB pad N for Update-PCB-from-Schematic.
#
# Authoritative source: Intan RHD2000.lbr, deviceset RHD2132
#   Left side pads 1-14:    IN8..IN0(1-9), REF(10), GND(11-12), VDD1(13), AUXIN1(14)
#   Bottom side pads 15-28: AUXIN2(15), AUXIN3(16), GND(17), CS-(18), CS+(19),
#                           SCLK-(20), SCLK+(21), MOSI-(22), MOSI+(23),
#                           MISO-(24), MISO+(25), VDD2(26), AUXOUT(27), ADC_REF(28)
#   Right side pads 29-42:  GND(29), LVDS_EN(30), VDD3(31), VESD(32),
#                           ELEC_TEST(33), IN31..IN23(34-42)
#   Top side pads 43-56:    IN22..IN9(43-56)
#   EP:                     GND
#
# Schematic visual layout groups functionally, but uses physical pin numbers.

# Enlarged body to fit 33 rows on left side (32 inputs + REF)
RHD_BODY = {"left": -27.94, "right": 27.94, "top": 43.18, "bot": -43.18}

RHD_PINS = []

# ── Left side: IN0-IN31 + REF (using physical pad numbers) ──────────
# Visual order top→bottom: IN0(pad 9), IN1(pad 8), ..., IN31(pad 34), REF(pad 10)
_lx = RHD_BODY["left"] - PLEN
_left_inputs = []
# IN0-IN8 are on physical pads 9,8,7,6,5,4,3,2,1 (reversed on left side)
for ch in range(9):
    _left_inputs.append((f"IN{ch}", str(9 - ch)))  # IN0→pad9, IN1→pad8, ..., IN8→pad1
# IN9-IN22 are on physical pads 56,55,...,43 (top side, reversed)
for ch in range(9, 23):
    _left_inputs.append((f"IN{ch}", str(56 - (ch - 9))))  # IN9→pad56, IN10→pad55, ..., IN22→pad43
# IN23-IN31 are on physical pads 42,41,...,34 (right side, reversed)
for ch in range(23, 32):
    _left_inputs.append((f"IN{ch}", str(42 - (ch - 23))))  # IN23→pad42, IN24→pad41, ..., IN31→pad34

for i, (name, num) in enumerate(_left_inputs):
    _ly = RHD_BODY["top"] - 2.54 - i * 2.54
    RHD_PINS.append((name, num, "passive", _lx, _ly, 0))

# REF below all inputs
_ref_y = RHD_BODY["top"] - 2.54 - 32 * 2.54
RHD_PINS.append(("REF", "10", "passive", _lx, _ref_y, 0))

# ── Right side: SPI, AUX, ELEC_TEST, misc control ───────────────────
_rx = RHD_BODY["right"] + PLEN
_right_pins = [
    # SPI (CMOS mode: only + pins used, - pins tied to GND)
    ("CS_b", "19", "input", 0),        # CS+ (active low)
    ("SCLK", "21", "input", 1),        # SCLK+
    ("MOSI", "23", "input", 2),        # MOSI+
    ("MISO", "25", "output", 3),       # MISO+
    # LVDS negative pins (tied to GND in CMOS mode)
    ("CS_N", "18", "passive", 5),      # CS-
    ("SCLK_N", "20", "passive", 6),    # SCLK-
    ("MOSI_N", "22", "passive", 7),    # MOSI-
    ("MISO_N", "24", "passive", 8),    # MISO-
    # LVDS enable
    ("LVDS_EN", "30", "passive", 10),  # LVDS_EN → GND for CMOS
    # AUX I/O
    ("auxin1", "14", "input", 12),     # tie to VDD per datasheet
    ("auxin2", "15", "input", 13),     # tie to VDD per datasheet
    ("auxin3", "16", "input", 14),     # tie to VDD per datasheet
    ("auxout", "27", "output", 16),    # NC per datasheet
    # Test
    ("elec_test", "33", "passive", 18),
]
for name, num, ptype, idx in _right_pins:
    _ly = RHD_BODY["top"] - 2.54 - idx * 2.54
    RHD_PINS.append((name, num, ptype, _rx, _ly, 180))

# ── Top side: Power pins (VDD×3, ADC_ref) ───────────────────────────
_ty = RHD_BODY["top"] + PLEN
_top_pins = [
    ("VDD_A1", "13", "power_in", -4),    # VDD1 (analog supply)
    ("VDD_A2", "26", "power_in", -2),    # VDD2 (analog supply)
    ("VDD_A3", "31", "power_in", 0),     # VDD3 (analog supply)
    ("ADC_ref", "28", "passive", 3),     # ADC reference (needs 10nF cap)
]
for name, num, ptype, idx in _top_pins:
    RHD_PINS.append((name, num, ptype, idx * 2.54, _ty, 270))

# ── Bottom side: GND pins, VESD, EP ─────────────────────────────────
_by = RHD_BODY["bot"] - PLEN
_bot_pins = [
    ("GND_1", "11", "power_in", -5),    # GND on left side
    ("GND_2", "12", "power_in", -4),    # GND on left side
    ("GND_3", "17", "power_in", -3),    # GND on bottom side
    ("GND_4", "29", "power_in", -2),    # GND on right side
    ("VESD", "32", "passive", 0),       # ESD clamp → GND
    ("EP", "EP", "power_in", 2),        # Exposed pad → GND
]
for name, num, ptype, idx in _bot_pins:
    RHD_PINS.append((name, num, ptype, idx * 2.54, _by, 90))


def make_rhd2132_libsym():
    pin_defs = ""
    for name, num, ptype, lx, ly, pdir in RHD_PINS:
        pin_defs += f"""
        (pin {ptype} line (at {lx} {ly} {pdir}) (length {PLEN})
          (name "{name}" (effects (font (size 1.0 1.0))))
          (number "{num}" (effects (font (size 0.8 0.8)))))"""
    return f"""
    (symbol "afe:RHD2132"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 {RHD_BODY["top"] + 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "RHD2132" (at 0 {RHD_BODY["bot"] - 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "https://intantech.com/files/Intan_RHD2000_series_datasheet.pdf"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "32-ch digital electrophysiology interface, SPI, QFN-56"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "RHD2132_1_1"
        (rectangle (start {RHD_BODY["left"]} {RHD_BODY["top"]}) (end {RHD_BODY["right"]} {RHD_BODY["bot"]})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""


register_sym("afe:RHD2132", make_rhd2132_libsym())


def rhd_pin(cx, cy, pin_name, rot=0):
    """Absolute connection point for named RHD2132 pin."""
    for name, num, ptype, lx, ly, pdir in RHD_PINS:
        if name == pin_name:
            return pin_abs(cx, cy, rot, lx, ly)
    raise ValueError(f"Unknown RHD pin: {pin_name}")


# ── Omnetics PZN-12 SPI connector (J1) ───────────────────────────────────

SPI_CONN_NPINS = 12
SPI_CONN_PLEN = 2.54
SPI_CONN_PIN_NAMES = [
    "MISO1",
    "MOSI",
    "SCLK",
    "CS",
    "GND",
    "GND",
    "VDD_D",
    "VDD_D",
    "VDD_A",
    "VDD_A",
    "MISO2",
    "GND",
]
SPI_CONN_HALF_H = (SPI_CONN_NPINS - 1) * 2.54 / 2  # 13.97

SPI_CONN_PINS = []
for i in range(SPI_CONN_NPINS):
    _ly = SPI_CONN_HALF_H - i * 2.54
    _lx = -2.54 - SPI_CONN_PLEN
    SPI_CONN_PINS.append((SPI_CONN_PIN_NAMES[i], str(i + 1), _lx, _ly, 0))


def make_spi_conn_libsym():
    top = SPI_CONN_HALF_H + 2.54
    bot = -SPI_CONN_HALF_H - 2.54
    pin_defs = ""
    for name, num, lx, ly, pdir in SPI_CONN_PINS:
        pin_defs += f"""
        (pin passive line (at {lx} {ly} {pdir}) (length {SPI_CONN_PLEN})
          (name "{name}" (effects (font (size 1.0 1.0))))
          (number "{num}" (effects (font (size 0.8 0.8)))))"""
    return f"""
    (symbol "afe:PZN12"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 {top} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "PZN-12" (at 0 {bot} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Omnetics PZN-12 polarized nano connector, SPI cable to Intan USB interface"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "PZN12_1_1"
        (rectangle (start -2.54 {top - 1.27}) (end 0 {bot + 1.27})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""


register_sym("afe:PZN12", make_spi_conn_libsym())


def spi_conn_pin(cx, cy, pin_index_0, rot=0):
    _name, _num, lx, ly, _pdir = SPI_CONN_PINS[pin_index_0]
    return pin_abs(cx, cy, rot, lx, ly)


# ── Omnetics A79024-001 (36-pin electrode connector, J5) ─────────────────

ELEC_CONN_NPINS = 36
ELEC_CONN_PLEN = 2.54
ELEC_CONN_PIN_NAMES = [f"IN{i}" for i in range(32)] + ["REF", "GND", "ETEST", "GND"]
ELEC_CONN_HALF_H = (ELEC_CONN_NPINS - 1) * 2.54 / 2  # 44.45

ELEC_CONN_PINS = []
for i in range(ELEC_CONN_NPINS):
    _ly = ELEC_CONN_HALF_H - i * 2.54
    _lx = -2.54 - ELEC_CONN_PLEN
    ELEC_CONN_PINS.append((ELEC_CONN_PIN_NAMES[i], str(i + 1), _lx, _ly, 0))


def make_elec_conn_libsym():
    top = ELEC_CONN_HALF_H + 2.54
    bot = -ELEC_CONN_HALF_H - 2.54
    pin_defs = ""
    for name, num, lx, ly, pdir in ELEC_CONN_PINS:
        pin_defs += f"""
        (pin passive line (at {lx} {ly} {pdir}) (length {ELEC_CONN_PLEN})
          (name "{name}" (effects (font (size 1.0 1.0))))
          (number "{num}" (effects (font (size 0.8 0.8)))))"""
    return f"""
    (symbol "afe:A79024"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 {top} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "A79024-001" (at 0 {bot} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Omnetics A79024-001, 36-pin nano-strip, electrode connector"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "A79024_1_1"
        (rectangle (start -2.54 {top - 1.27}) (end 0 {bot + 1.27})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""


register_sym("afe:A79024", make_elec_conn_libsym())


def elec_conn_pin(cx, cy, pin_index_0, rot=0):
    _name, _num, lx, ly, _pdir = ELEC_CONN_PINS[pin_index_0]
    return pin_abs(cx, cy, rot, lx, ly)


# ── Passive helpers ───────────────────────────────────────────────────────


def passive_pin1(cx, cy, rot=0):
    return pin_abs(cx, cy, rot, 0, 3.81)


def passive_pin2(cx, cy, rot=0):
    return pin_abs(cx, cy, rot, 0, -3.81)


def tp_pin(cx, cy, rot=0):
    return pin_abs(cx, cy, rot, 0, 0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. SCHEMATIC BUILDER
# ═══════════════════════════════════════════════════════════════════════════


def _c(v):
    """Round coordinate to 2 decimal places — kills floating-point drift."""
    return round(v, 2)


class Sch:
    def __init__(self):
        self.root_uuid = uid()
        self.project = "afe-headstage-v1"
        self.items = []
        self._pwr_n = 0
        self._flg_n = 0
        self._tp_n = 0

    def _place(self, lib_id, x, y, ref_pre, ref_num, value, rot=0, pin_nums=None, footprint=""):
        self._pwr_n += 1
        if ref_pre == "#PWR":
            ref = f"#PWR{self._pwr_n:02d}"
        elif ref_pre == "#FLG":
            self._flg_n += 1
            ref = f"#FLG{self._flg_n:02d}"
        else:
            ref = f"{ref_pre}{ref_num}"
        hide = " (hide yes)" if ref_pre in ("#PWR", "#FLG") else ""
        pins = ""
        if pin_nums:
            for pn in pin_nums:
                pins += f'\n      (pin "{pn}" (uuid "{uid()}"))'
        x, y = _c(x), _c(y)
        self.items.append(f"""
    (symbol
      (lib_id "{lib_id}")
      (at {x} {y} {rot})
      (unit 1)
      (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
      (uuid "{uid()}")
      (property "Reference" "{ref}" (at {_c(x + 2.54)} {_c(y - 2.54)} 0) (effects (font (size 1.27 1.27)){hide}))
      (property "Value" "{value}" (at {_c(x + 2.54)} {_c(y + 2.54)} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "{footprint}" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes))){pins}
      (instances
        (project "{self.project}"
          (path "/{self.root_uuid}" (reference "{ref}") (unit 1))))
    )""")
        return ref

    def place(self, lib_id, x, y, ref_pre, ref_num, value, rot=0, pin_nums=None, footprint=""):
        return self._place(lib_id, x, y, ref_pre, ref_num, value, rot, pin_nums, footprint)

    def place_tp(self, x, y, value, rot=0):
        self._tp_n += 1
        return self._place(
            "Connector:TestPoint", x, y, "TP", self._tp_n, value, rot=rot,
            pin_nums=["1"], footprint="TestPoint:TestPoint_Pad_D1.0mm"
        )

    def pwr(self, sym, x, y, rot=0):
        return self._place(
            f"power:{sym}",
            x,
            y,
            "#PWR" if sym != "PWR_FLAG" else "#FLG",
            0,
            sym,
            rot=rot,
            pin_nums=["1"],
        )

    def wire(self, x1, y1, x2, y2, net="__wire__"):
        x1, y1, x2, y2 = _c(x1), _c(y1), _c(x2), _c(y2)
        WREG.register(x1, y1, net)
        WREG.register(x2, y2, net)
        WREG.register_segment(x1, y1, x2, y2)
        self.items.append(f"""
    (wire (pts (xy {x1} {y1}) (xy {x2} {y2}))
      (stroke (width 0) (type default)) (uuid "{uid()}"))""")

    def label(self, name, x, y, rot=0):
        x, y = _c(x), _c(y)
        self.items.append(f"""
    (label "{name}" (at {x} {y} {rot})
      (effects (font (size 1.27 1.27)) (justify left bottom))
      (uuid "{uid()}"))""")

    def glabel(self, name, x, y, rot=0, shape="input"):
        x, y = _c(x), _c(y)
        jst = "right" if rot == 180 else "left"
        self.items.append(f"""
    (global_label "{name}" (shape {shape}) (at {x} {y} {rot})
      (effects (font (size 1.27 1.27)) (justify {jst}))
      (uuid "{uid()}")
      (property "Intersheetrefs" "${{INTERSHEET_REFS}}"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))))""")

    def nc(self, x, y):
        x, y = _c(x), _c(y)
        self.items.append(f"""
    (no_connect (at {x} {y}) (uuid "{uid()}"))""")

    def junc(self, x, y):
        x, y = _c(x), _c(y)
        WREG.register_junction(x, y)
        self.items.append(f"""
    (junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "{uid()}"))""")

    def text(self, txt, x, y):
        x, y = _c(x), _c(y)
        self.items.append(f"""
    (text "{txt}" (at {x} {y} 0)
      (effects (font (size 1.27 1.27)) (justify left bottom))
    )""")

    def build(self):
        lib_syms = "\n".join(LIB_SYMBOLS.values())
        items = "\n".join(self.items)
        return f"""(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (generator_version "9.0")
  (uuid "{self.root_uuid}")
  (paper "A2")
  (title_block
    (title "Neural AFE Headstage v1 — RHD2132 + Intan USB Interface")
    (date "2026-02-26")
    (rev "1.0")
    (company "BCIInterface")
    (comment 1 "32-ch RHD2132 | 16ch wired v1, 32ch routed v2")
    (comment 2 "SPI to Intan RHD2000 USB interface | 3.3V from cable")
    (comment 3 "Omnetics PZN-12 SPI | A79024-001 electrode | fs=30kS/s")
  )
  (lib_symbols
{lib_syms}
  )
{items}
  (sheet_instances
    (path "/" (page "1"))
  )
)
"""


# ═══════════════════════════════════════════════════════════════════════════
# 6. BUILD — main schematic assembly
# ═══════════════════════════════════════════════════════════════════════════

LANE_PITCH = g(3)  # 7.62mm between channel lanes (tighter for 32ch)


def build():
    s = Sch()

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  POWER SECTION — no LDO, power from Intan USB interface cable    │
    # │  VDD_A and VDD_D come in on PZN-12 connector J1                  │
    # │  FB1 isolates analog from digital supply domain                  │
    # └──────────────────────────────────────────────────────────────────┘

    # The SPI cable provides:
    #   VDD_A (3.3V analog) on pins 9,10
    #   VDD_D (3.3V digital) on pins 7,8
    # We use +3V3 as the unified power net (Intan pre-regulates)
    # FB1 provides additional analog/digital isolation on-board

    # Ferrite bead: +3V3 → AVDD (analog domain for RHD2132)
    FB_X, FB_Y = g(30), g(10)
    s.place(
        "Device:FerriteBead",
        FB_X,
        FB_Y,
        "FB",
        1,
        "600R@100MHz",
        rot=270,
        pin_nums=["1", "2"],
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    fb_in = passive_pin1(FB_X, FB_Y, rot=270)
    fb_out = passive_pin2(FB_X, FB_Y, rot=270)

    # With fixed pin_abs, rot=270: pin1 (fb_in) = RIGHT, pin2 (fb_out) = LEFT
    # +3V3 enters on pin 1 (RIGHT), AVDD exits on pin 2 (LEFT)

    # +3V3 label on input side (RIGHT of FB)
    s.pwr("+3V3", fb_in[0], fb_in[1] - g(2))
    s.wire(fb_in[0], fb_in[1], fb_in[0], fb_in[1] - g(2), net="+3V3")

    # AVDD label on output side (LEFT of FB)
    s.glabel("AVDD", fb_out[0] - g(1), fb_out[1], 180, "passive")
    s.wire(fb_out[0], fb_out[1], fb_out[0] - g(1), fb_out[1], net="AVDD")

    # C1: 100nF bypass on +3V3 (right side of FB)
    C1_X, C1_Y = fb_in[0], fb_in[1] + g(4)
    s.place("Device:C", C1_X, C1_Y, "C", 1, "100nF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c1_top = passive_pin1(C1_X, C1_Y)
    c1_bot = passive_pin2(C1_X, C1_Y)
    s.wire(c1_top[0], c1_top[1], C1_X, fb_in[1], net="+3V3")
    s.junc(C1_X, fb_in[1])
    s.pwr("GND", c1_bot[0], c1_bot[1] + g(1))
    s.wire(c1_bot[0], c1_bot[1], c1_bot[0], c1_bot[1] + g(1), net="GND")

    # C2: 1uF bulk bypass on +3V3 (further right)
    C2_X, C2_Y = fb_in[0] + g(4), fb_in[1] + g(4)
    s.place("Device:C", C2_X, C2_Y, "C", 2, "1uF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c2_top = passive_pin1(C2_X, C2_Y)
    c2_bot = passive_pin2(C2_X, C2_Y)
    s.wire(c2_top[0], c2_top[1], C2_X, fb_in[1], net="+3V3")
    # Horizontal stub: C2 tee-down to TP_VDD x (will be chained later)
    # Do NOT wire all the way to fb_in — that creates colinear overlap.
    # Instead, chain: PWR_FLAG → C2 → TP_VDD → fb_in (see below).
    s.junc(C2_X, fb_in[1])
    s.pwr("GND", c2_bot[0], c2_bot[1] + g(1))
    s.wire(c2_bot[0], c2_bot[1], c2_bot[0], c2_bot[1] + g(1), net="GND")

    # C3: 100nF bypass on AVDD (left side of FB)
    C3_X, C3_Y = fb_out[0], fb_out[1] + g(4)
    s.place("Device:C", C3_X, C3_Y, "C", 3, "100nF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c3_top = passive_pin1(C3_X, C3_Y)
    c3_bot = passive_pin2(C3_X, C3_Y)
    s.wire(c3_top[0], c3_top[1], C3_X, fb_out[1], net="AVDD")
    s.junc(C3_X, fb_out[1])
    s.pwr("GND", c3_bot[0], c3_bot[1] + g(1))
    s.wire(c3_bot[0], c3_bot[1], c3_bot[0], c3_bot[1] + g(1), net="GND")

    # C4: 1uF bulk bypass on AVDD (further left)
    C4_X, C4_Y = fb_out[0] - g(4), fb_out[1] + g(4)
    s.place("Device:C", C4_X, C4_Y, "C", 4, "1uF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c4_top = passive_pin1(C4_X, C4_Y)
    c4_bot = passive_pin2(C4_X, C4_Y)
    s.wire(c4_top[0], c4_top[1], C4_X, fb_out[1], net="AVDD")
    # Horizontal stub: C4 tee-down to TP_AVDD x (will be chained later)
    # Do NOT wire all the way to AVDD label — that creates colinear overlap.
    # Instead, chain: C4 → TP_AVDD → AVDD label junction (see below).
    s.junc(C4_X, fb_out[1])
    s.pwr("GND", c4_bot[0], c4_bot[1] + g(1))
    s.wire(c4_bot[0], c4_bot[1], c4_bot[0], c4_bot[1] + g(1), net="GND")

    # PWR_FLAGs — chain: PWR_FLAG(pf_x) → C2(C2_X) on the +3V3 bus
    pf_x = fb_in[0] + g(6)
    s.pwr("PWR_FLAG", pf_x, fb_in[1])
    s.wire(pf_x, fb_in[1], C2_X, fb_in[1], net="+3V3")
    s.junc(C2_X, fb_in[1])

    # GND PWR_FLAG
    gnd_flag_x, gnd_flag_y = c1_bot[0] + g(3), c1_bot[1] + g(1)
    s.pwr("PWR_FLAG", gnd_flag_x, gnd_flag_y)
    s.pwr("GND", gnd_flag_x, gnd_flag_y)

    # AVDD PWR_FLAG — drives the AVDD net for ERC (ferrite bead is passive)
    avdd_flag_x = fb_out[0] - g(1)
    avdd_flag_y = fb_out[1] - g(2)
    s.pwr("PWR_FLAG", avdd_flag_x, avdd_flag_y)
    s.wire(avdd_flag_x, avdd_flag_y, avdd_flag_x, fb_out[1], net="AVDD")
    s.junc(avdd_flag_x, fb_out[1])

    # Power test points — chained bus segments (no colinear overlaps)
    # +3V3 bus chain: PWR_FLAG(pf_x) → C2(C2_X) → TP_VDD(tp_vdd_x) → fb_in
    tp_vdd_x, tp_vdd_y = fb_in[0] + g(2), fb_in[1] - g(3)
    s.place_tp(tp_vdd_x, tp_vdd_y, "TP_VDD")
    tp_vdd_p = tp_pin(tp_vdd_x, tp_vdd_y)
    s.wire(tp_vdd_p[0], tp_vdd_p[1], tp_vdd_x, fb_in[1], net="+3V3")
    # Chain: C2 → TP_VDD → fb_in (two non-overlapping segments)
    s.wire(C2_X, fb_in[1], tp_vdd_x, fb_in[1], net="+3V3")
    s.junc(tp_vdd_x, fb_in[1])
    s.wire(tp_vdd_x, fb_in[1], fb_in[0], fb_in[1], net="+3V3")
    s.junc(fb_in[0], fb_in[1])

    # AVDD bus chain: C4(C4_X) → TP_AVDD(tp_avdd_x) → AVDD label junction
    tp_avdd_x, tp_avdd_y = fb_out[0] - g(2), fb_out[1] - g(3)
    s.place_tp(tp_avdd_x, tp_avdd_y, "TP_AVDD")
    tp_avdd_p = tp_pin(tp_avdd_x, tp_avdd_y)
    s.wire(tp_avdd_p[0], tp_avdd_p[1], tp_avdd_x, fb_out[1], net="AVDD")
    # Chain: C4 → TP_AVDD → AVDD label junction (two non-overlapping segments)
    s.wire(C4_X, fb_out[1], tp_avdd_x, fb_out[1], net="AVDD")
    s.junc(tp_avdd_x, fb_out[1])
    s.wire(tp_avdd_x, fb_out[1], fb_out[0] - g(1), fb_out[1], net="AVDD")
    s.junc(fb_out[0] - g(1), fb_out[1])

    tp_gnd_x, tp_gnd_y = c1_bot[0] + g(3), c1_bot[1]
    s.place_tp(tp_gnd_x, tp_gnd_y, "TP_GND")
    tp_gnd_p = tp_pin(tp_gnd_x, tp_gnd_y)
    s.pwr("GND", tp_gnd_p[0], tp_gnd_p[1] + g(1))
    s.wire(tp_gnd_p[0], tp_gnd_p[1], tp_gnd_p[0], tp_gnd_p[1] + g(1), net="GND")

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  RHD2132 — 32-ch Neural AFE IC (QFN-56)                          │
    # │  All three VDD pins → AVDD (single analog supply)                │
    # │  CMOS SPI mode: LVDS negative pins → GND, LVDS_EN → GND         │
    # └──────────────────────────────────────────────────────────────────┘

    RHD_X, RHD_Y = g(55), g(55)   # shifted down for taller symbol

    s.place(
        "afe:RHD2132",
        RHD_X,
        RHD_Y,
        "U",
        1,
        "RHD2132",
        pin_nums=[p[1] for p in RHD_PINS],
        footprint="afe_footprints:RHD2132_QFN56",
    )

    # VDD_A1, VDD_A2, VDD_A3 → all AVDD (all three are analog supply)
    for vdd_name in ["VDD_A1", "VDD_A2", "VDD_A3"]:
        vx, vy = rhd_pin(RHD_X, RHD_Y, vdd_name)
        s.glabel("AVDD", vx, vy - g(1), 90, "passive")
        s.wire(vx, vy, vx, vy - g(1), net="AVDD")

    # C5: 100nF bypass close to VDD_A1 (pad 13)
    vdda1_x, vdda1_y = rhd_pin(RHD_X, RHD_Y, "VDD_A1")
    C5_X, C5_Y = vdda1_x - g(3), vdda1_y + g(2)
    s.place("Device:C", C5_X, C5_Y, "C", 5, "100nF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c5_top = passive_pin1(C5_X, C5_Y)
    c5_bot = passive_pin2(C5_X, C5_Y)
    s.wire(c5_top[0], c5_top[1], C5_X, vdda1_y, net="AVDD")
    s.wire(C5_X, vdda1_y, vdda1_x, vdda1_y, net="AVDD")
    s.junc(vdda1_x, vdda1_y)
    s.pwr("GND", c5_bot[0], c5_bot[1] + g(1))
    s.wire(c5_bot[0], c5_bot[1], c5_bot[0], c5_bot[1] + g(1), net="GND")

    # C6: 100nF bypass close to VDD_A2 (pad 26) — NOT a digital supply
    vdda2_x, vdda2_y = rhd_pin(RHD_X, RHD_Y, "VDD_A2")
    C6_X, C6_Y = vdda2_x + g(3), vdda2_y + g(2)
    s.place("Device:C", C6_X, C6_Y, "C", 6, "100nF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c6_top = passive_pin1(C6_X, C6_Y)
    c6_bot = passive_pin2(C6_X, C6_Y)
    s.wire(c6_top[0], c6_top[1], C6_X, vdda2_y, net="AVDD")
    s.wire(C6_X, vdda2_y, vdda2_x, vdda2_y, net="AVDD")
    s.junc(vdda2_x, vdda2_y)
    s.pwr("GND", c6_bot[0], c6_bot[1] + g(1))
    s.wire(c6_bot[0], c6_bot[1], c6_bot[0], c6_bot[1] + g(1), net="GND")

    # GND pins (4 perimeter + EP)
    for gnd_name in ["GND_1", "GND_2", "GND_3", "GND_4", "EP"]:
        gx, gy = rhd_pin(RHD_X, RHD_Y, gnd_name)
        s.pwr("GND", gx, gy + g(1))
        s.wire(gx, gy, gx, gy + g(1), net="GND")

    # CMOS mode: LVDS negative pins → GND
    for lvds_name in ["CS_N", "SCLK_N", "MOSI_N", "MISO_N", "LVDS_EN"]:
        lx, ly = rhd_pin(RHD_X, RHD_Y, lvds_name)
        s.pwr("GND", lx + g(2), ly, rot=90)
        s.wire(lx, ly, lx + g(2), ly, net="GND")

    # ADC_ref → C7 10nF → GND
    adc_x, adc_y = rhd_pin(RHD_X, RHD_Y, "ADC_ref")
    C7_X, C7_Y = adc_x, adc_y + g(3)
    s.place("Device:C", C7_X, C7_Y, "C", 7, "10nF", pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    c7_top = passive_pin1(C7_X, C7_Y)
    c7_bot = passive_pin2(C7_X, C7_Y)
    s.glabel("ADC_ref", adc_x, adc_y - g(1), 90, "passive")
    s.wire(adc_x, adc_y, adc_x, adc_y - g(1), net="ADC_ref")
    s.junc(adc_x, adc_y)
    s.wire(adc_x, adc_y, c7_top[0], c7_top[1], net="ADC_ref")
    s.pwr("GND", c7_bot[0], c7_bot[1] + g(1))
    s.wire(c7_bot[0], c7_bot[1], c7_bot[0], c7_bot[1] + g(1), net="GND")

    # VESD → GND (ESD clamp)
    vesd_x, vesd_y = rhd_pin(RHD_X, RHD_Y, "VESD")
    s.pwr("GND", vesd_x, vesd_y + g(2))
    s.wire(vesd_x, vesd_y, vesd_x, vesd_y + g(2), net="GND")

    # elec_test → global label (for electrode impedance testing)
    et_x, et_y = rhd_pin(RHD_X, RHD_Y, "elec_test")
    s.glabel("ELEC_TEST", et_x + g(2), et_y, 0, "bidirectional")
    s.wire(et_x, et_y, et_x + g(2), et_y, net="ELEC_TEST")

    # Auxiliary inputs → tie to AVDD per datasheet recommendation
    for aux_name in ["auxin1", "auxin2", "auxin3"]:
        ax, ay = rhd_pin(RHD_X, RHD_Y, aux_name)
        s.glabel("AVDD", ax + g(2), ay, 0, "passive")
        s.wire(ax, ay, ax + g(2), ay, net="AVDD")

    # AUXOUT → NC (not used)
    auxout_x, auxout_y = rhd_pin(RHD_X, RHD_Y, "auxout")
    s.nc(auxout_x, auxout_y)

    # SPI → global labels
    for rhd_name, gl_name, shape in [
        ("CS_b", "CS", "input"),
        ("SCLK", "SCLK", "input"),
        ("MOSI", "MOSI", "input"),
        ("MISO", "MISO", "output"),
    ]:
        px, py = rhd_pin(RHD_X, RHD_Y, rhd_name)
        s.glabel(gl_name, px + g(2), py, 0, shape)
        s.wire(px, py, px + g(2), py, net=gl_name)

    # SPI test points — offset MISO TP to different x to avoid colinear vertical overlap
    sclk_x, sclk_y = rhd_pin(RHD_X, RHD_Y, "SCLK")
    tp_sclk_x = sclk_x + g(5)
    tp_sclk_y = sclk_y - g(3)
    s.place_tp(tp_sclk_x, tp_sclk_y, "TP_SCLK")
    tp_sclk_p = tp_pin(tp_sclk_x, tp_sclk_y)
    s.wire(tp_sclk_p[0], tp_sclk_p[1], tp_sclk_x, sclk_y, net="SCLK")
    s.wire(tp_sclk_x, sclk_y, sclk_x + g(2), sclk_y, net="SCLK")
    s.junc(sclk_x + g(2), sclk_y)

    miso_x, miso_y = rhd_pin(RHD_X, RHD_Y, "MISO")
    tp_miso_x = miso_x + g(7)  # g(7) not g(5) — different x-column from SCLK TP
    tp_miso_y = miso_y - g(3)
    s.place_tp(tp_miso_x, tp_miso_y, "TP_MISO")
    tp_miso_p = tp_pin(tp_miso_x, tp_miso_y)
    s.wire(tp_miso_p[0], tp_miso_p[1], tp_miso_x, miso_y, net="MISO")
    s.wire(tp_miso_x, miso_y, miso_x + g(2), miso_y, net="MISO")
    s.junc(miso_x + g(2), miso_y)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  REFERENCE ELECTRODE — bath reference bias                       │
    # │  ref_elec → R1 (10k) → GND                                      │
    # │  Test point for external drive                                   │
    # └──────────────────────────────────────────────────────────────────┘

    ref_x, ref_y = rhd_pin(RHD_X, RHD_Y, "REF")
    R1_X = round(ref_x - g(4), 2)
    R1_Y = ref_y
    s.place("Device:R", R1_X, R1_Y, "R", 1, "10k", rot=90, pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
    r1_left = passive_pin1(R1_X, R1_Y, rot=90)
    r1_right = passive_pin2(R1_X, R1_Y, rot=90)

    # GND below R1 pin1 — vertical drop avoids overlap with REF_ELEC wire
    gnd_r1_x = r1_left[0]
    gnd_r1_y = r1_left[1] + g(2)
    s.pwr("GND", gnd_r1_x, gnd_r1_y, rot=0)
    s.wire(r1_left[0], r1_left[1], gnd_r1_x, gnd_r1_y, net="GND")

    # REF_ELEC: TWO non-overlapping wires. KiCad splits pass-through wires
    # at pin locations and may fail to connect the far segment.
    ref_label_x = r1_right[0] - g(1)
    s.glabel("REF_ELEC", ref_label_x, ref_y, 180, "bidirectional")
    s.wire(ref_label_x, ref_y, r1_right[0], r1_right[1], net="REF_ELEC")  # glabel → R1 pin2
    s.wire(r1_right[0], r1_right[1], ref_x, ref_y, net="REF_ELEC")        # R1 pin2 → U1 REF
    s.junc(r1_right[0], r1_right[1])  # junction where R1 pin2 meets REF_ELEC wire

    # Test point on REF — taps REF_ELEC wire at R1 pin2 junction
    tp_ref_x = r1_right[0]
    tp_ref_y = ref_y + g(2)
    s.place_tp(tp_ref_x, tp_ref_y, "TP_REF")
    tp_ref_p = tp_pin(tp_ref_x, tp_ref_y)
    s.wire(tp_ref_p[0], tp_ref_p[1], r1_right[0], ref_y, net="REF_ELEC")
    s.junc(r1_right[0], ref_y)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  ELECTRODE INPUTS — all 32 channels routed                       │
    # │  v1: channels 0-15 wired with bias resistors + test points       │
    # │  v2: channels 16-31 have connector pads but no bias R (DNP)      │
    # └──────────────────────────────────────────────────────────────────┘

    # v1 channels (0-15): full wiring with 10M bias resistor and TP on first 4
    NUM_V1_CH = 16

    for i in range(NUM_V1_CH):
        ch_net = f"CH{i}"
        inp_x, inp_y = rhd_pin(RHD_X, RHD_Y, f"IN{i}")

        # Global label for electrode connection
        s.glabel(ch_net, inp_x - g(3), inp_y, 180, "bidirectional")
        s.wire(inp_x, inp_y, inp_x - g(3), inp_y, net=ch_net)

        # Test points on first 4 channels (validation Layer C/D)
        if i < 4:
            tp_ch_x = inp_x - g(5) - i * g(2)
            tp_ch_y = inp_y
            s.place_tp(tp_ch_x, tp_ch_y, f"TP_CH{i}", rot=270)
            tp_ch_p = tp_pin(tp_ch_x, tp_ch_y, rot=270)
            s.wire(tp_ch_p[0], tp_ch_p[1], inp_x - g(3), inp_y, net=ch_net)
            s.junc(inp_x - g(3), inp_y)

    # v2 channels (16-31): all on left side of symbol now (QFN-56 layout)
    for i in range(16, 32):
        inp_x, inp_y = rhd_pin(RHD_X, RHD_Y, f"IN{i}")
        ch_net = f"CH{i}"
        s.glabel(ch_net, inp_x - g(3), inp_y, 180, "bidirectional")
        s.wire(inp_x, inp_y, inp_x - g(3), inp_y, net=ch_net)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  OMNETICS PZN-12 SPI CONNECTOR (J1)                              │
    # │  To Intan RHD2000 USB interface board via ~30cm cable            │
    # └──────────────────────────────────────────────────────────────────┘

    SPI_X, SPI_Y = g(95), g(30)

    s.place(
        "afe:PZN12",
        SPI_X,
        SPI_Y,
        "J",
        1,
        "PZN-12",
        pin_nums=[str(i + 1) for i in range(SPI_CONN_NPINS)],
        footprint="afe_footprints:Omnetics_PZN-12-AA",
    )

    for i in range(SPI_CONN_NPINS):
        px, py = spi_conn_pin(SPI_X, SPI_Y, i)
        pname = SPI_CONN_PIN_NAMES[i]

        if pname == "GND":
            s.pwr("GND", px - g(2), py, rot=90)
            s.wire(px, py, px - g(2), py, net="GND")
        elif pname == "VDD_D":
            s.pwr("+3V3", px - g(2), py, rot=270)
            s.wire(px, py, px - g(2), py, net="+3V3")
        elif pname == "VDD_A":
            s.glabel("AVDD", px - g(3), py, 180, "passive")
            s.wire(px, py, px - g(3), py, net="AVDD")
        elif pname == "MISO1":
            s.glabel("MISO", px - g(2), py, 180, "output")
            s.wire(px, py, px - g(2), py, net="MISO")
        elif pname == "MISO2":
            # Unused for single-chip headstage
            s.nc(px, py)
        elif pname == "MOSI":
            s.glabel("MOSI_J", px - g(2), py, 180, "input")
            s.wire(px, py, px - g(2), py, net="MOSI_J")
        elif pname == "SCLK":
            s.glabel("SCLK_J", px - g(2), py, 180, "input")
            s.wire(px, py, px - g(2), py, net="SCLK_J")
        elif pname == "CS":
            s.glabel("CS_J", px - g(2), py, 180, "input")
            s.wire(px, py, px - g(2), py, net="CS_J")

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  SPI SERIES RESISTORS R2-R4 (100Ω)                               │
    # │  Damps ringing on ~30cm SPI cable per Intan reference design     │
    # │  CS_J → R2 → CS,  SCLK_J → R3 → SCLK,  MOSI_J → R4 → MOSI    │
    # │  No resistor on MISO (output from chip — per Intan ref)          │
    # └──────────────────────────────────────────────────────────────────┘

    SPI_R_X = SPI_X - g(10)
    SPI_R_Y_BASE = SPI_Y - g(2)

    for r_idx, (conn_net, chip_net, r_num) in enumerate([
        ("CS_J", "CS", 2),
        ("SCLK_J", "SCLK", 3),
        ("MOSI_J", "MOSI", 4),
    ]):
        rx = SPI_R_X
        ry = SPI_R_Y_BASE + r_idx * g(3)
        s.place("Device:R", rx, ry, "R", r_num, "100", rot=90, pin_nums=["1", "2"], footprint="Resistor_SMD:R_0402_1005Metric")
        r_left = passive_pin1(rx, ry, rot=90)
        r_right = passive_pin2(rx, ry, rot=90)
        # Connector side (left)
        s.glabel(conn_net, r_left[0] - g(1), r_left[1], 180, "input")
        s.wire(r_left[0], r_left[1], r_left[0] - g(1), r_left[1], net=conn_net)
        # Chip side (right)
        s.glabel(chip_net, r_right[0] + g(1), r_right[1], 0, "input")
        s.wire(r_right[0], r_right[1], r_right[0] + g(1), r_right[1], net=chip_net)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  OMNETICS A79024-001 ELECTRODE CONNECTOR (J5)                    │
    # │  36-pin nano-strip: 32 electrodes + REF + GND + ETEST + GND     │
    # └──────────────────────────────────────────────────────────────────┘

    ELEC_X, ELEC_Y = g(5), g(45)

    s.place(
        "afe:A79024",
        ELEC_X,
        ELEC_Y,
        "J",
        5,
        "A79024-001",
        pin_nums=[str(i + 1) for i in range(ELEC_CONN_NPINS)],
        footprint="afe_footprints:Omnetics_A79024_36pin",
    )

    for i in range(ELEC_CONN_NPINS):
        px, py = elec_conn_pin(ELEC_X, ELEC_Y, i)
        pname = ELEC_CONN_PIN_NAMES[i]

        if pname == "GND":
            s.pwr("GND", px - g(2), py, rot=90)
            s.wire(px, py, px - g(2), py, net="GND")
        elif pname == "REF":
            s.glabel("REF_ELEC", px - g(2), py, 180, "bidirectional")
            s.wire(px, py, px - g(2), py, net="REF_ELEC")
        elif pname == "ETEST":
            s.glabel("ELEC_TEST", px - g(2), py, 180, "bidirectional")
            s.wire(px, py, px - g(2), py, net="ELEC_TEST")
        elif pname.startswith("IN"):
            ch_idx = int(pname[2:])
            ch_net = f"CH{ch_idx}"
            s.glabel(ch_net, px - g(2), py, 180, "bidirectional")
            s.wire(px, py, px - g(2), py, net=ch_net)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  ANNOTATIONS                                                      │
    # └──────────────────────────────────────────────────────────────────┘

    s.text("v1 Neural AFE Headstage — RHD2132", g(2), g(2))
    s.text("Target: ≤3µV RMS in 300Hz-7kHz spike band", g(2), g(4))
    s.text("Backend: Intan RHD2000 USB interface board", g(2), g(6))
    s.text("Cable: ~30cm Omnetics PZN-12 SPI", g(2), g(8))

    return s.build()


# ═══════════════════════════════════════════════════════════════════════════
# 7. MAIN — generate, validate, write
# ═══════════════════════════════════════════════════════════════════════════


def main():
    outdir = "/Users/aharshi/BCIInterface/hardware/afe-headstage-real"
    os.makedirs(outdir, exist_ok=True)

    print("Generating v1 schematic (RHD2132 + Intan USB interface)...")
    sch = build()

    # Endpoint collision check
    print(f"  Wire registry: {WREG.summary()}")
    WREG.check()
    print("  ✓ No endpoint collisions")

    # Write schematic
    sch_path = os.path.join(outdir, "afe-headstage-v1.kicad_sch")
    with open(sch_path, "w") as f:
        f.write(sch)
    print(f"  Wrote {sch_path} ({len(sch):,} bytes)")

    # Write project file
    proj = {
        "meta": {"filename": "afe-headstage-v1.kicad_pro", "version": 1},
        "net_settings": {
            "classes": [
                {
                    "bus_width": 12,
                    "clearance": 0.2,
                    "name": "Default",
                    "track_width": 0.2,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "wire_width": 6,
                },
                {
                    "bus_width": 12,
                    "clearance": 0.2,
                    "name": "ELECTRODE",
                    "nets": [f"CH{i}" for i in range(32)] + ["REF_ELEC"],
                    "track_width": 0.15,
                    "via_diameter": 0.4,
                    "via_drill": 0.2,
                    "wire_width": 6,
                },
                {
                    "bus_width": 12,
                    "clearance": 0.2,
                    "name": "DIGITAL_SPI",
                    "nets": ["SCLK", "MOSI", "CS", "MISO", "SCLK_J", "MOSI_J", "CS_J"],
                    "track_width": 0.2,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "wire_width": 6,
                },
                {
                    "bus_width": 12,
                    "clearance": 0.25,
                    "name": "POWER",
                    "nets": ["+3V3", "AVDD"],
                    "track_width": 0.4,
                    "via_diameter": 0.8,
                    "via_drill": 0.4,
                    "wire_width": 6,
                },
            ],
            "meta": {"version": 3},
        },
        "schematic": {"annotate_start_num": 0, "meta": {"version": 1}},
        "sheets": [],
        "text_variables": {},
    }
    proj_path = os.path.join(outdir, "afe-headstage-v1.kicad_pro")
    with open(proj_path, "w") as f:
        json.dump(proj, f, indent=2)
    print(f"  Wrote {proj_path}")

    # Write custom symbol library
    afe_syms = {k: v for k, v in LIB_SYMBOLS.items() if k.startswith("afe:")}
    sym_text = "\n".join(afe_syms.values())
    sym_path = os.path.join(outdir, "afe_symbols_v1.kicad_sym")
    with open(sym_path, "w") as f:
        f.write(
            "(kicad_symbol_lib\n"
            "  (version 20231120)\n"
            '  (generator "custom")\n'
            '  (generator_version "1.0")\n'
            f"{sym_text}\n"
            ")\n"
        )
    print(f"  Wrote {sym_path}")

    # Write sym-lib-table
    SYM_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
    symlib_path = os.path.join(outdir, "sym-lib-table")
    with open(symlib_path, "w") as f:
        f.write(
            f"""(sym_lib_table
  (version 7)
  (lib (name "power")(type "KiCad")(uri "{SYM_DIR}/power.kicad_sym")(options "")(descr "Power symbols"))
  (lib (name "Device")(type "KiCad")(uri "{SYM_DIR}/Device.kicad_sym")(options "")(descr "Generic devices"))
  (lib (name "Connector")(type "KiCad")(uri "{SYM_DIR}/Connector.kicad_sym")(options "")(descr "Connectors"))
  (lib (name "afe")(type "KiCad")(uri "${{KIPRJMOD}}/afe_symbols_v1.kicad_sym")(options "")(descr "Custom AFE symbols v1"))
)
"""
        )

    # Write fp-lib-table
    FP_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
    fplib_path = os.path.join(outdir, "fp-lib-table")
    with open(fplib_path, "w") as f:
        f.write(
            f"""(fp_lib_table
  (version 7)
  (lib (name "Resistor_SMD")(type "KiCad")(uri "{FP_DIR}/Resistor_SMD.pretty")(options "")(descr ""))
  (lib (name "Capacitor_SMD")(type "KiCad")(uri "{FP_DIR}/Capacitor_SMD.pretty")(options "")(descr ""))
  (lib (name "Inductor_SMD")(type "KiCad")(uri "{FP_DIR}/Inductor_SMD.pretty")(options "")(descr ""))
  (lib (name "Package_QFP")(type "KiCad")(uri "{FP_DIR}/Package_QFP.pretty")(options "")(descr ""))
  (lib (name "Package_DFN_QFN")(type "KiCad")(uri "{FP_DIR}/Package_DFN_QFN.pretty")(options "")(descr ""))
  (lib (name "Connector")(type "KiCad")(uri "{FP_DIR}/Connector.pretty")(options "")(descr ""))
  (lib (name "TestPoint")(type "KiCad")(uri "{FP_DIR}/TestPoint.pretty")(options "")(descr ""))
  (lib (name "afe_footprints")(type "KiCad")(uri "${{KIPRJMOD}}/footprints_v1.pretty")(options "")(descr "Custom v1 footprints"))
)
"""
        )

    print("\nBOM Summary:")
    print("  U1  — RHD2132 (Intan, QFN-56, 8×8mm)")
    print("  J1  — Omnetics PZN-12 (SPI cable connector)")
    print("  J5  — Omnetics A79024-001 (36-pin electrode)")
    print("  FB1 — Ferrite bead 600Ω@100MHz (0402)")
    print("  C1  — 100nF (0402) +3V3 bypass")
    print("  C2  — 1µF (0402) +3V3 bulk")
    print("  C3  — 100nF (0402) AVDD bypass")
    print("  C4  — 1µF (0402) AVDD bulk")
    print("  C5  — 100nF (0402) VDD_A local bypass")
    print("  C6  — 100nF (0402) VDD_D local bypass")
    print("  C7  — 10nF (0402) ADC_ref")
    print("  R1  — 10kΩ (0402) REF bias to GND")
    print("  R2  — 100Ω (0402) CS series (Intan ref design)")
    print("  R3  — 100Ω (0402) SCLK series (Intan ref design)")
    print("  R4  — 100Ω (0402) MOSI series (Intan ref design)")
    print("  TP1-TP9 — Test points (1mm pad)")
    print("\nOpen afe-headstage-v1.kicad_sch in KiCad to view.")


if __name__ == "__main__":
    main()
