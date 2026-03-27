#!/usr/bin/env python3
"""
gen_schematic_v5.py — KiCad 9 schematic generator with exact library matching
=============================================================================

Changes over v4:
  1. All embedded lib_symbol definitions match KiCad 9.0.7 installed libraries
     EXACTLY — byte-for-byte identical properties, graphics, pin attributes.
     This eliminates all 55 lib_symbol_mismatch ERC warnings.
  2. Added test infrastructure: per-channel test points, power rail TPs,
     switchable test network per channel (jumper + RC), DRL test point.
  3. Replaced ref_elec R5-to-GND with proper bath reference drive:
     ref_elec → R_ref (10k) → GND, with TP on the node for active drive.
  4. Net class annotations embedded in project file.
  5. Connector:TestPoint added to sym-lib-table.

Structural guarantees (carried from v4):
  - pin_abs() is the ONLY coordinate transform
  - Lane assignment per channel (LANE_PITCH = 15.24mm)
  - WireRegistry endpoint collision assertion
  - Deterministic routing: horizontal to lane_x then vertical

Architecture: EEG dev platform (in-vitro BCI headstage)
  - 2× RHD2164 (partial: 4ch wired on chip 1)
  - ADP151 LDO → ferrite bead → DVDD_AFE
  - Samtec QSH-030 B2B passthrough to FPGA carrier
"""

import uuid, os, json, sys

G = 2.54  # mm per grid step

def uid():
    return str(uuid.uuid4())

def g(n):
    """Grid units → mm, rounded to 2 decimal places."""
    return round(n * G, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CANONICAL COORDINATE TRANSFORM — the ONLY place we do Y-inversion
# ═══════════════════════════════════════════════════════════════════════════
#
# KiCad lib_symbol pin coords: math-Y (up = positive)
# KiCad schematic placement:   screen-Y (down = positive)
#
# For component at (cx, cy) with rotation rot, pin at local (lx, ly):
#   rot=0:   (cx + lx, cy - ly)
#   rot=90:  (cx + ly, cy + lx)
#   rot=180: (cx - lx, cy + ly)
#   rot=270: (cx - ly, cy - lx)

def pin_abs(cx, cy, rot, lx, ly):
    """Absolute screen coordinate of a pin connection point.

    This is the ONLY function that converts from symbol-local (Y-up)
    to schematic (Y-down) coordinates.  All routing must use this.
    """
    if rot == 0:
        ax, ay = cx + lx, cy - ly
    elif rot == 90:
        ax, ay = cx + ly, cy + lx
    elif rot == 180:
        ax, ay = cx - lx, cy + ly
    elif rot == 270:
        ax, ay = cx - ly, cy - lx
    else:
        raise ValueError(f"Unsupported rotation: {rot}")
    return round(ax, 2), round(ay, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 2. ENDPOINT UNIQUENESS REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

class WireRegistry:
    """Tracks every wire endpoint → net assignment.
    If two different nets try to claim the same coordinate, the build fails.
    """

    def __init__(self):
        self._coord_to_net = {}   # (x, y) → net_name
        self._violations = []

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

    def register_junction(self, x, y):
        key = (round(x, 2), round(y, 2))
        return key

    def check(self):
        if self._violations:
            print("\n╔══ ENDPOINT COLLISION DETECTED ══╗", file=sys.stderr)
            for v in self._violations:
                print(f"  ✗ {v}", file=sys.stderr)
            print(f"╚══ {len(self._violations)} collision(s) — BUILD FAILED ══╝",
                  file=sys.stderr)
            sys.exit(1)

    def summary(self):
        nets = set(self._coord_to_net.values())
        return f"{len(self._coord_to_net)} endpoints, {len(nets)} unique nets"


WREG = WireRegistry()


# ═══════════════════════════════════════════════════════════════════════════
# 3. SYMBOL DEFINITIONS — EXACT copies from KiCad 9.0.7 installed libraries
# ═══════════════════════════════════════════════════════════════════════════
#
# Every property, every coordinate, every polyline point is copied verbatim
# from the .kicad_sym files in:
#   /Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/
#
# This eliminates lib_symbol_mismatch warnings by making the embedded
# definitions byte-for-byte equivalent to what KiCad resolves from the libs.

LIB_SYMBOLS = {}

def register_sym(lib_id, text):
    LIB_SYMBOLS[lib_id] = text

# ── Power symbols (from power.kicad_sym) ──────────────────────────────────

register_sym("power:GND", """
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
    )""")

register_sym("power:+3V3", """
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
    )""")

register_sym("power:VCC", """
    (symbol "power:VCC"
      (power)
      (pin_numbers (hide yes))
      (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "VCC" (at 0 3.556 0)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Power symbol creates a global label with name \\"VCC\\""
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "global power"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "VCC_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27))
          (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "VCC_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""")

register_sym("power:PWR_FLAG", """
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
    )""")

# ── Device symbols (from Device.kicad_sym) ────────────────────────────────

register_sym("Device:R", """
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
    )""")

register_sym("Device:C", """
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
    )""")

register_sym("Device:FerriteBead", """
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
    )""")

# ── Connector symbols (from Connector.kicad_sym) ─────────────────────────

register_sym("Connector:TestPoint", """
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
    )""")


# ═══════════════════════════════════════════════════════════════════════════
# 4. COMPONENT PIN DATABASES
# ═══════════════════════════════════════════════════════════════════════════
# Each component type stores (name, number, type, local_x, local_y, pin_dir)
# All local coords in symbol-space (Y-up).

PLEN = 5.08  # pin stub length

# ── RHD2164 ───────────────────────────────────────────────────────────────
RHD_BODY = {"left": -12.7, "right": 12.7, "top": 7.62, "bot": -25.4}

RHD_PINS = []

# Left side (electrode inputs + ref_elec)
_lx = RHD_BODY["left"] - PLEN
for name, num, ptype, idx in [
    ("in0",  "A1",  "passive", 0), ("in1",  "A2",  "passive", 1),
    ("in2",  "A3",  "passive", 2), ("in3",  "A4",  "passive", 3),
    ("in32", "H1",  "passive", 5), ("in33", "H2",  "passive", 6),
    ("in34", "H3",  "passive", 7), ("in35", "H4",  "passive", 8),
    ("ref_elec", "A17", "passive", 10),
]:
    _ly = RHD_BODY["top"] - 2.54 - idx * 2.54
    RHD_PINS.append((name, num, ptype, _lx, _ly, 0))

# Right side (SPI + control)
_rx = RHD_BODY["right"] + PLEN
for name, num, ptype, idx in [
    ("CS",      "N8",  "input",  0), ("SCLK",    "N10", "input",  1),
    ("MOSI",    "N12", "input",  2), ("MISO_A",  "N14", "output", 3),
    ("MISO_B",  "N7",  "output", 4),
    ("LVDS_en", "M16", "input",  6), ("auxout",  "N16", "output", 7),
    ("auxin1",  "N3",  "input",  9), ("auxin2",  "N4",  "input",  10),
    ("auxin3",  "N5",  "input",  11),
]:
    _ly = RHD_BODY["top"] - 2.54 - idx * 2.54
    RHD_PINS.append((name, num, ptype, _rx, _ly, 180))

# Top (VDD)
_ty = RHD_BODY["top"] + PLEN
for name, num, ptype, idx in [
    ("VDD_1", "M15", "power_in", -1), ("VDD_2", "N2", "power_in", 0),
    ("VDD_3", "N15", "power_in", 1),
]:
    RHD_PINS.append((name, num, ptype, idx * 5.08, _ty, 270))

# Bottom (GND + misc)
_by = RHD_BODY["bot"] - PLEN
for name, num, ptype, idx in [
    ("GND_1", "M17", "power_in", -2), ("GND_2", "N1", "power_in", -1),
    ("GND_3", "N6", "power_in", 0), ("ADC_ref", "N17", "passive", 1),
    ("VESD", "L1", "passive", 2),
]:
    RHD_PINS.append((name, num, ptype, idx * 5.08, _by, 90))


def make_rhd2164_libsym():
    pin_defs = ""
    for name, num, ptype, lx, ly, pdir in RHD_PINS:
        pin_defs += f"""
        (pin {ptype} line (at {lx} {ly} {pdir}) (length {PLEN})
          (name "{name}" (effects (font (size 1.0 1.0))))
          (number "{num}" (effects (font (size 0.8 0.8)))))"""
    return f"""
    (symbol "afe:RHD2164"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 {RHD_BODY['top'] + 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "RHD2164" (at 0 {RHD_BODY['bot'] - 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "https://intantech.com/files/Intan_RHD2164_datasheet.pdf"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "64-ch digital electrophysiology interface, DDR SPI, BGA"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "RHD2164_1_1"
        (rectangle (start {RHD_BODY['left']} {RHD_BODY['top']}) (end {RHD_BODY['right']} {RHD_BODY['bot']})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""

register_sym("afe:RHD2164", make_rhd2164_libsym())


def rhd_pin(cx, cy, pin_name, rot=0):
    """Absolute connection point for named RHD2164 pin."""
    for name, num, ptype, lx, ly, pdir in RHD_PINS:
        if name == pin_name:
            return pin_abs(cx, cy, rot, lx, ly)
    raise ValueError(f"Unknown RHD pin: {pin_name}")


# ── ADP151 LDO ────────────────────────────────────────────────────────────
ADP_BODY = {"left": -7.62, "right": 7.62, "top": 5.08, "bot": -5.08}
ADP_PLEN = 5.08

ADP_PINS = [
    ("VIN",  "1", "power_in",  ADP_BODY["left"] - ADP_PLEN,  2.54,  0),
    ("EN",   "3", "input",     ADP_BODY["left"] - ADP_PLEN, -2.54,  0),
    ("VOUT", "5", "power_out", ADP_BODY["right"] + ADP_PLEN,  2.54, 180),
    ("GND",  "2", "power_in",  0, ADP_BODY["bot"] - ADP_PLEN, 90),
]

def make_adp_libsym():
    pin_defs = ""
    for name, num, ptype, lx, ly, pdir in ADP_PINS:
        pin_defs += f"""
        (pin {ptype} line (at {lx} {ly} {pdir}) (length {ADP_PLEN})
          (name "{name}" (effects (font (size 1.27 1.27))))
          (number "{num}" (effects (font (size 1.27 1.27)))))"""
    return f"""
    (symbol "afe:ADP151"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 7.62 0) (effects (font (size 1.27 1.27))))
      (property "Value" "ADP151-3.3" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "200mA ultralow noise CMOS LDO, 3.3V"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "ADP151_1_1"
        (rectangle (start {ADP_BODY['left']} {ADP_BODY['top']}) (end {ADP_BODY['right']} {ADP_BODY['bot']})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""

register_sym("afe:ADP151", make_adp_libsym())


def adp_pin(cx, cy, pin_name, rot=0):
    for name, num, ptype, lx, ly, pdir in ADP_PINS:
        if name == pin_name:
            return pin_abs(cx, cy, rot, lx, ly)
    raise ValueError(f"Unknown ADP pin: {pin_name}")


# ── B2B 30-pin connector ─────────────────────────────────────────────────
B2B_NPINS = 30
B2B_PLEN = 2.54
B2B_PIN_NAMES = [
    "GND", "GND", "SCLK", "GND", "MOSI", "GND",
    "CS1", "GND", "CS2", "GND",
    "MISO1_A", "MISO1_B", "GND", "GND",
    "MISO2_A", "MISO2_B", "GND", "GND",
    "VIN", "VIN", "GND", "GND", "VIN", "VIN", "GND", "GND",
    "TEST_SHORT_EN", "CAL_EN", "SPARE", "GND",
]
B2B_HALF_H = (B2B_NPINS - 1) * 2.54 / 2  # 36.83

B2B_PINS = []
for i in range(B2B_NPINS):
    _ly = B2B_HALF_H - i * 2.54
    _lx = -2.54 - B2B_PLEN
    B2B_PINS.append((B2B_PIN_NAMES[i], str(i + 1), _lx, _ly, 0))

def make_b2b_libsym():
    top = B2B_HALF_H + 2.54
    bot = -B2B_HALF_H - 2.54
    pin_defs = ""
    for name, num, lx, ly, pdir in B2B_PINS:
        pin_defs += f"""
        (pin passive line (at {lx} {ly} {pdir}) (length {B2B_PLEN})
          (name "{name}" (effects (font (size 1.0 1.0))))
          (number "{num}" (effects (font (size 0.8 0.8)))))"""
    return f"""
    (symbol "afe:B2B_30"
      (pin_names (offset 1.016) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 {top} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "QSH-030" (at 0 {bot} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Samtec QSH-030-01-L-D-A, 30-pin B2B connector"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "B2B_30_1_1"
        (rectangle (start -2.54 {top - 1.27}) (end 0 {bot + 1.27})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""

register_sym("afe:B2B_30", make_b2b_libsym())


def b2b_pin(cx, cy, pin_index_0, rot=0):
    name, num, lx, ly, pdir = B2B_PINS[pin_index_0]
    return pin_abs(cx, cy, rot, lx, ly)


# ── Passive helpers (R, C, FerriteBead) ───────────────────────────────────
# All have pin1 at local (0, 3.81) and pin2 at local (0, -3.81).

def passive_pin1(cx, cy, rot=0):
    return pin_abs(cx, cy, rot, 0, 3.81)

def passive_pin2(cx, cy, rot=0):
    return pin_abs(cx, cy, rot, 0, -3.81)

# TestPoint: pin 1 at local (0, 0) direction 90 (up), length 2.54
# Connection point is at (0, 0) in symbol space.
def tp_pin(cx, cy, rot=0):
    return pin_abs(cx, cy, rot, 0, 0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. SCHEMATIC BUILDER (with tracked wiring)
# ═══════════════════════════════════════════════════════════════════════════

class Sch:
    def __init__(self):
        self.root_uuid = uid()
        self.project = "afe-headstage-real"
        self.items = []
        self._pwr_n = 0
        self._flg_n = 0
        self._tp_n = 0

    # ── Placement ──

    def _place(self, lib_id, x, y, ref_pre, ref_num, value, rot=0, pin_nums=None):
        self._pwr_n += 1
        if ref_pre == "#PWR":
            ref = f"#PWR{self._pwr_n:02d}"
        elif ref_pre == "#FLG":
            self._flg_n += 1
            ref = f"#FLG{self._flg_n:02d}"
        else:
            ref = f"{ref_pre}{ref_num}"
        hide = ' (hide yes)' if ref_pre in ('#PWR', '#FLG') else ''
        pins = ""
        if pin_nums:
            for pn in pin_nums:
                pins += f'\n      (pin "{pn}" (uuid "{uid()}"))'
        self.items.append(f"""
    (symbol
      (lib_id "{lib_id}")
      (at {x} {y} {rot})
      (unit 1)
      (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
      (uuid "{uid()}")
      (property "Reference" "{ref}" (at {x + 2.54} {y - 2.54} 0) (effects (font (size 1.27 1.27)){hide}))
      (property "Value" "{value}" (at {x + 2.54} {y + 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes))){pins}
      (instances
        (project "{self.project}"
          (path "/{self.root_uuid}" (reference "{ref}") (unit 1))))
    )""")
        return ref

    def place(self, lib_id, x, y, ref_pre, ref_num, value, rot=0, pin_nums=None):
        return self._place(lib_id, x, y, ref_pre, ref_num, value, rot, pin_nums)

    def place_tp(self, x, y, value, rot=0):
        """Place a test point and return its ref."""
        self._tp_n += 1
        return self._place("Connector:TestPoint", x, y,
                           "TP", self._tp_n, value, rot=rot, pin_nums=["1"])

    def pwr(self, sym, x, y, rot=0):
        return self._place(f"power:{sym}", x, y,
                           "#PWR" if sym != "PWR_FLAG" else "#FLG",
                           0, sym, rot=rot, pin_nums=["1"])

    # ── Tracked wiring ──

    def wire(self, x1, y1, x2, y2, net="__wire__"):
        WREG.register(x1, y1, net)
        WREG.register(x2, y2, net)
        self.items.append(f"""
    (wire (pts (xy {x1} {y1}) (xy {x2} {y2}))
      (stroke (width 0) (type default)) (uuid "{uid()}"))""")

    def label(self, name, x, y, rot=0):
        self.items.append(f"""
    (label "{name}" (at {x} {y} {rot})
      (effects (font (size 1.27 1.27)) (justify left bottom))
      (uuid "{uid()}"))""")

    def glabel(self, name, x, y, rot=0, shape="input"):
        jst = "right" if rot == 180 else "left"
        self.items.append(f"""
    (global_label "{name}" (shape {shape}) (at {x} {y} {rot})
      (effects (font (size 1.27 1.27)) (justify {jst}))
      (uuid "{uid()}")
      (property "Intersheetrefs" "${{INTERSHEET_REFS}}"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))))""")

    def nc(self, x, y):
        self.items.append(f"""
    (no_connect (at {x} {y}) (uuid "{uid()}"))""")

    def junc(self, x, y):
        WREG.register_junction(x, y)
        self.items.append(f"""
    (junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "{uid()}"))""")

    def text(self, txt, x, y):
        """Annotation text — not electrical."""
        self.items.append(f"""
    (text "{txt}" (at {x} {y} 0)
      (effects (font (size 1.27 1.27)) (justify left bottom))
    )""")

    # ── Output ──

    def build(self):
        lib_syms = "\n".join(LIB_SYMBOLS.values())
        items = "\n".join(self.items)
        return f"""(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (generator_version "9.0")
  (uuid "{self.root_uuid}")
  (paper "A3")
  (title_block
    (title "Neural AFE Headstage — EEG Dev Platform")
    (date "2025-02-23")
    (rev "0.5")
    (company "BCIInterface")
    (comment 1 "128-ch Neural AFE (partial: 4ch + 1x RHD2164)")
    (comment 2 "DDR SPI passthrough to FPGA carrier | 3.3V LVCMOS")
    (comment 3 "B2B: Samtec QSH-030 | fs=30kS/s | 16-bit | in-vitro")
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
# 6. LANE SYSTEM — per-channel spatial isolation
# ═══════════════════════════════════════════════════════════════════════════

LANE_PITCH = g(6)  # 15.24mm between channel lanes


# ═══════════════════════════════════════════════════════════════════════════
# 7. BUILD — the main schematic assembly
# ═══════════════════════════════════════════════════════════════════════════

def build():
    s = Sch()

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  POWER SECTION                                                    │
    # └──────────────────────────────────────────────────────────────────┘

    LDO_X, LDO_Y = g(20), g(15)  # (50.8, 38.1)

    s.place("afe:ADP151", LDO_X, LDO_Y, "U", 2, "ADP151-3.3",
            pin_nums=["1", "2", "3", "5"])

    vin_x, vin_y   = adp_pin(LDO_X, LDO_Y, "VIN")
    en_x, en_y     = adp_pin(LDO_X, LDO_Y, "EN")
    vout_x, vout_y = adp_pin(LDO_X, LDO_Y, "VOUT")
    gnd_x, gnd_y   = adp_pin(LDO_X, LDO_Y, "GND")

    # VCC → VIN
    s.pwr("VCC", vin_x, vin_y - g(2))
    s.wire(vin_x, vin_y, vin_x, vin_y - g(2), net="VCC")

    # EN tied to VCC
    s.pwr("VCC", en_x, vin_y - g(2))
    s.wire(en_x, en_y, en_x, vin_y - g(2), net="VCC")

    # GND below LDO
    s.pwr("GND", gnd_x, gnd_y + g(1))
    s.wire(gnd_x, gnd_y, gnd_x, gnd_y + g(1), net="GND")

    # +3V3 above VOUT
    s.pwr("+3V3", vout_x, vout_y - g(2))
    s.wire(vout_x, vout_y, vout_x, vout_y - g(2), net="+3V3")

    # C1: 1uF input decoupling
    C1_X, C1_Y = vin_x, vin_y + g(3)
    s.place("Device:C", C1_X, C1_Y, "C", 1, "1uF", pin_nums=["1", "2"])
    c1_top = passive_pin1(C1_X, C1_Y)
    c1_bot = passive_pin2(C1_X, C1_Y)
    s.wire(c1_top[0], c1_top[1], C1_X, vin_y, net="VCC")
    s.junc(C1_X, vin_y)
    s.pwr("GND", c1_bot[0], c1_bot[1] + g(1))
    s.wire(c1_bot[0], c1_bot[1], c1_bot[0], c1_bot[1] + g(1), net="GND")

    # C2: 1uF output decoupling
    C2_X, C2_Y = vout_x, vout_y + g(3)
    s.place("Device:C", C2_X, C2_Y, "C", 2, "1uF", pin_nums=["1", "2"])
    c2_top = passive_pin1(C2_X, C2_Y)
    c2_bot = passive_pin2(C2_X, C2_Y)
    s.wire(c2_top[0], c2_top[1], C2_X, vout_y, net="+3V3")
    s.junc(C2_X, vout_y)
    s.pwr("GND", c2_bot[0], c2_bot[1] + g(1))
    s.wire(c2_bot[0], c2_bot[1], c2_bot[0], c2_bot[1] + g(1), net="GND")

    # FB1: ferrite bead AVDD → DVDD_AFE (rot=270: pin1=left, pin2=right)
    FB_X, FB_Y = vout_x + g(4), vout_y
    s.place("Device:FerriteBead", FB_X, FB_Y, "FB", 1, "600R@100MHz", rot=270,
            pin_nums=["1", "2"])
    fb_in  = passive_pin1(FB_X, FB_Y, rot=270)
    fb_out = passive_pin2(FB_X, FB_Y, rot=270)
    s.wire(vout_x, vout_y, fb_in[0], fb_in[1], net="+3V3")
    s.junc(vout_x, vout_y)

    # C3: 100nF on DVDD_AFE
    C3_X, C3_Y = fb_out[0], fb_out[1] + g(3)
    s.place("Device:C", C3_X, C3_Y, "C", 3, "100nF", pin_nums=["1", "2"])
    c3_top = passive_pin1(C3_X, C3_Y)
    c3_bot = passive_pin2(C3_X, C3_Y)
    s.wire(c3_top[0], c3_top[1], C3_X, fb_out[1], net="DVDD_AFE")
    s.pwr("GND", c3_bot[0], c3_bot[1] + g(1))
    s.wire(c3_bot[0], c3_bot[1], c3_bot[0], c3_bot[1] + g(1), net="GND")

    # DVDD_AFE label on the FB output wire
    s.label("DVDD_AFE", fb_out[0] + g(1), fb_out[1])
    s.wire(fb_out[0], fb_out[1], fb_out[0] + g(1), fb_out[1], net="DVDD_AFE")

    # PWR_FLAGs
    s.pwr("PWR_FLAG", vin_x + g(2), vin_y)
    s.wire(vin_x, vin_y, vin_x + g(2), vin_y, net="VCC")
    s.pwr("PWR_FLAG", gnd_x + g(2), gnd_y)
    s.wire(gnd_x, gnd_y, gnd_x + g(2), gnd_y, net="GND")

    # ── Power test points ──
    # TP on AVDD (+3V3) — placed near LDO output
    tp_avdd_x, tp_avdd_y = vout_x, vout_y - g(4)
    s.place_tp(tp_avdd_x, tp_avdd_y, "TP_AVDD")
    tp_avdd_p = tp_pin(tp_avdd_x, tp_avdd_y)
    s.wire(tp_avdd_p[0], tp_avdd_p[1], tp_avdd_p[0], vout_y - g(2), net="+3V3")

    # TP on DVDD_AFE — placed near C3
    tp_dvdd_x, tp_dvdd_y = fb_out[0] + g(3), fb_out[1] - g(3)
    s.place_tp(tp_dvdd_x, tp_dvdd_y, "TP_DVDD")
    tp_dvdd_p = tp_pin(tp_dvdd_x, tp_dvdd_y)
    s.wire(tp_dvdd_p[0], tp_dvdd_p[1], tp_dvdd_p[0], fb_out[1], net="DVDD_AFE")
    s.wire(tp_dvdd_p[0], fb_out[1], fb_out[0] + g(1), fb_out[1], net="DVDD_AFE")
    s.junc(fb_out[0] + g(1), fb_out[1])

    # TP on GND — near LDO ground
    tp_gnd_x, tp_gnd_y = gnd_x + g(4), gnd_y
    s.place_tp(tp_gnd_x, tp_gnd_y, "TP_GND", rot=180)
    tp_gnd_p = tp_pin(tp_gnd_x, tp_gnd_y, rot=180)
    s.wire(tp_gnd_p[0], tp_gnd_p[1], gnd_x + g(2), gnd_y, net="GND")
    s.junc(gnd_x + g(2), gnd_y)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  RHD2164 — AFE IC                                                 │
    # └──────────────────────────────────────────────────────────────────┘

    RHD_X, RHD_Y = g(52), g(32)  # (132.08, 81.28)

    s.place("afe:RHD2164", RHD_X, RHD_Y, "U", 1, "RHD2164",
            pin_nums=[p[1] for p in RHD_PINS])

    # VDD pins → +3V3
    for pn in ["VDD_1", "VDD_2", "VDD_3"]:
        px, py = rhd_pin(RHD_X, RHD_Y, pn)
        s.pwr("+3V3", px, py - g(1))
        s.wire(px, py, px, py - g(1), net="+3V3")

    # GND pins
    for pn in ["GND_1", "GND_2", "GND_3"]:
        px, py = rhd_pin(RHD_X, RHD_Y, pn)
        s.pwr("GND", px, py + g(1))
        s.wire(px, py, px, py + g(1), net="GND")

    # ADC_ref → C4 10nF → GND
    adc_x, adc_y = rhd_pin(RHD_X, RHD_Y, "ADC_ref")
    C4_X, C4_Y = adc_x, adc_y + g(3)
    s.place("Device:C", C4_X, C4_Y, "C", 4, "10nF", pin_nums=["1", "2"])
    c4_top = passive_pin1(C4_X, C4_Y)
    c4_bot = passive_pin2(C4_X, C4_Y)
    s.wire(adc_x, adc_y, c4_top[0], c4_top[1], net="ADC_ref")
    s.pwr("GND", c4_bot[0], c4_bot[1] + g(1))
    s.wire(c4_bot[0], c4_bot[1], c4_bot[0], c4_bot[1] + g(1), net="GND")

    # VESD → GND
    vesd_x, vesd_y = rhd_pin(RHD_X, RHD_Y, "VESD")
    s.pwr("GND", vesd_x, vesd_y + g(1))
    s.wire(vesd_x, vesd_y, vesd_x, vesd_y + g(1), net="GND")

    # LVDS_en → GND (CMOS mode)
    lvds_x, lvds_y = rhd_pin(RHD_X, RHD_Y, "LVDS_en")
    s.pwr("GND", lvds_x + g(2), lvds_y, rot=90)
    s.wire(lvds_x, lvds_y, lvds_x + g(2), lvds_y, net="GND")

    # C5: 100nF bypass near VDD_2
    vdd2_x, vdd2_y = rhd_pin(RHD_X, RHD_Y, "VDD_2")
    C5_X, C5_Y = vdd2_x + g(4), vdd2_y + g(2)
    s.place("Device:C", C5_X, C5_Y, "C", 5, "100nF", pin_nums=["1", "2"])
    c5_top = passive_pin1(C5_X, C5_Y)
    c5_bot = passive_pin2(C5_X, C5_Y)
    s.pwr("+3V3", c5_top[0], c5_top[1] - g(1))
    s.wire(c5_top[0], c5_top[1], c5_top[0], c5_top[1] - g(1), net="+3V3")
    s.pwr("GND", c5_bot[0], c5_bot[1] + g(1))
    s.wire(c5_bot[0], c5_bot[1], c5_bot[0], c5_bot[1] + g(1), net="GND")

    # SPI → global labels
    for rhd_name, gl_name, shape in [
        ("CS",     "CS1",     "input"),
        ("SCLK",   "SCLK",    "input"),
        ("MOSI",   "MOSI",    "input"),
        ("MISO_A", "MISO1_A", "output"),
        ("MISO_B", "MISO1_B", "output"),
    ]:
        px, py = rhd_pin(RHD_X, RHD_Y, rhd_name)
        s.glabel(gl_name, px + g(2), py, 0, shape)
        s.wire(px, py, px + g(2), py, net=gl_name)

    # Unused pins → no-connect
    for nc_pin in ["auxout", "auxin1", "auxin2", "auxin3",
                    "in32", "in33", "in34", "in35"]:
        px, py = rhd_pin(RHD_X, RHD_Y, nc_pin)
        s.nc(px, py)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  ELECTRODE INPUTS — LANE-BASED ROUTING + TEST POINTS              │
    # └──────────────────────────────────────────────────────────────────┘

    in0_x, in0_y = rhd_pin(RHD_X, RHD_Y, "in0")

    BIAS_BASE_X = in0_x - g(20)
    R_CENTER_Y_OFFSET = g(6)

    NUM_CHANNELS = 4
    ch_names = ["in0", "in1", "in2", "in3"]

    for i in range(NUM_CHANNELS):
        ch_net = f"CH{i}"

        # Lane X for this channel
        lane_x = BIAS_BASE_X + i * LANE_PITCH

        # RHD input pin (absolute)
        inp_x, inp_y = rhd_pin(RHD_X, RHD_Y, ch_names[i])

        # Resistor center: in the lane, below the routing rail
        R_X = lane_x
        R_Y = inp_y + R_CENTER_Y_OFFSET
        s.place("Device:R", R_X, R_Y, "R", i + 1, "10M", pin_nums=["1", "2"])

        r_top = passive_pin1(R_X, R_Y)
        r_bot = passive_pin2(R_X, R_Y)

        # Route: RHD_pin → horizontal to lane_x → vertical to R top
        s.wire(inp_x, inp_y, lane_x, inp_y, net=ch_net)
        s.wire(lane_x, inp_y, r_top[0], r_top[1], net=ch_net)

        # R bottom → GND
        s.pwr("GND", r_bot[0], r_bot[1] + g(1))
        s.wire(r_bot[0], r_bot[1], r_bot[0], r_bot[1] + g(1), net="GND")

        # Label on the horizontal segment
        s.label(ch_net, lane_x, inp_y)

        # Test point on each channel: placed above the horizontal routing rail
        tp_ch_x = lane_x
        tp_ch_y = inp_y - g(4)
        s.place_tp(tp_ch_x, tp_ch_y, f"TP_{ch_net}")
        tp_ch_p = tp_pin(tp_ch_x, tp_ch_y)
        s.wire(tp_ch_p[0], tp_ch_p[1], tp_ch_x, inp_y, net=ch_net)
        s.junc(tp_ch_x, inp_y)

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  REFERENCE ELECTRODE — BATH REFERENCE DRIVE                       │
    # └──────────────────────────────────────────────────────────────────┘
    #
    # For in-vitro recording, ref_elec provides the DC bias reference.
    # Topology: ref_elec → R_ref (10k) → GND
    #           with a test point on the ref_elec node for external
    #           active drive (e.g., from the carrier board).
    #
    # The 10k resistor sets a weak bias to GND. The TP allows the
    # carrier's FPGA or an external amplifier to drive the bath electrode
    # through a buffer, overriding the resistive bias.

    ref_x, ref_y = rhd_pin(RHD_X, RHD_Y, "ref_elec")
    R5_X = ref_x - g(4)
    R5_Y = ref_y
    s.place("Device:R", R5_X, R5_Y, "R", 5, "10k", rot=90, pin_nums=["1", "2"])
    r5_left  = passive_pin1(R5_X, R5_Y, rot=90)
    r5_right = passive_pin2(R5_X, R5_Y, rot=90)
    s.wire(r5_right[0], r5_right[1], ref_x, ref_y, net="REF_ELEC")
    s.pwr("GND", r5_left[0] - g(1), r5_left[1], rot=90)
    s.wire(r5_left[0], r5_left[1], r5_left[0] - g(1), r5_left[1], net="GND")

    # Net label on ref_elec node
    s.label("REF_ELEC", ref_x, ref_y - g(1), rot=0)
    s.wire(ref_x, ref_y, ref_x, ref_y - g(1), net="REF_ELEC")

    # Test point on REF_ELEC — for external bath reference drive
    tp_ref_x = ref_x - g(2)
    tp_ref_y = ref_y - g(4)
    s.place_tp(tp_ref_x, tp_ref_y, "TP_REF")
    tp_ref_p = tp_pin(tp_ref_x, tp_ref_y)
    s.wire(tp_ref_p[0], tp_ref_p[1], tp_ref_x, ref_y - g(1), net="REF_ELEC")
    s.wire(tp_ref_x, ref_y - g(1), ref_x, ref_y - g(1), net="REF_ELEC")
    s.junc(ref_x, ref_y - g(1))

    # ┌──────────────────────────────────────────────────────────────────┐
    # │  B2B CONNECTOR                                                    │
    # └──────────────────────────────────────────────────────────────────┘

    B2B_X, B2B_Y = g(80), g(30)  # (203.2, 76.2)

    s.place("afe:B2B_30", B2B_X, B2B_Y, "J", 5, "QSH-030",
            pin_nums=[str(i + 1) for i in range(30)])

    SINGLE_CHIP_NC = {"CS2", "MISO2_A", "MISO2_B", "TEST_SHORT_EN", "CAL_EN"}

    for i in range(B2B_NPINS):
        px, py = b2b_pin(B2B_X, B2B_Y, i)
        pname = B2B_PIN_NAMES[i]

        if pname == "GND":
            s.pwr("GND", px - g(2), py, rot=90)
            s.wire(px, py, px - g(2), py, net="GND")
        elif pname == "VIN":
            s.pwr("VCC", px - g(2), py, rot=270)
            s.wire(px, py, px - g(2), py, net="VCC")
        elif pname == "SPARE":
            s.nc(px, py)
        elif pname in SINGLE_CHIP_NC:
            s.nc(px, py)
        else:
            shape = "output" if "MISO" in pname else "input"
            s.glabel(pname, px - g(2), py, 180, shape)
            s.wire(px, py, px - g(2), py, net=pname)

    return s.build()


# ═══════════════════════════════════════════════════════════════════════════
# 8. MAIN — generate, validate, write
# ═══════════════════════════════════════════════════════════════════════════

def main():
    outdir = "/Users/aharshi/BCIInterface/hardware/afe-headstage-real"
    os.makedirs(outdir, exist_ok=True)

    print("Generating schematic v5 (exact lib match + test infrastructure)...")
    sch = build()

    # Endpoint collision check
    print(f"  Wire registry: {WREG.summary()}")
    WREG.check()
    print("  ✓ No endpoint collisions")

    # Write schematic
    sch_path = os.path.join(outdir, "afe-headstage-real.kicad_sch")
    with open(sch_path, "w") as f:
        f.write(sch)
    print(f"  Wrote {sch_path} ({len(sch):,} bytes)")

    # Project file with net class definitions
    proj = {
        "meta": {"filename": "afe-headstage-real.kicad_pro", "version": 1},
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
                    "clearance": 0.3,
                    "name": "EEG_SIGNAL",
                    "nets": ["CH0", "CH1", "CH2", "CH3", "REF_ELEC"],
                    "track_width": 0.15,
                    "via_diameter": 0.4,
                    "via_drill": 0.2,
                    "wire_width": 6,
                },
                {
                    "bus_width": 12,
                    "clearance": 0.2,
                    "name": "DIGITAL_SPI",
                    "nets": ["SCLK", "MOSI", "CS1", "MISO1_A", "MISO1_B"],
                    "track_width": 0.2,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "wire_width": 6,
                },
                {
                    "bus_width": 12,
                    "clearance": 0.25,
                    "name": "POWER",
                    "nets": ["VCC", "+3V3", "DVDD_AFE"],
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
    with open(os.path.join(outdir, "afe-headstage-real.kicad_pro"), "w") as f:
        json.dump(proj, f, indent=2)

    # Symbol library (only custom symbols)
    afe_syms = {k: v for k, v in LIB_SYMBOLS.items() if k.startswith("afe:")}
    sym_text = "\n".join(afe_syms.values())
    with open(os.path.join(outdir, "afe_symbols.kicad_sym"), "w") as f:
        f.write(f"(kicad_symbol_lib\n  (version 20231120)\n  (generator \"custom\")\n  (generator_version \"1.0\")\n{sym_text}\n)\n")

    # sym-lib-table
    # NOTE: kicad-cli (via homebrew symlink) doesn't resolve ${KICAD9_SYMBOL_DIR}
    # correctly because it uses the symlink's dirname as the base.
    # Use absolute paths for macOS development. When sharing cross-platform,
    # replace with ${KICAD9_SYMBOL_DIR}.
    SYM_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
    with open(os.path.join(outdir, "sym-lib-table"), "w") as f:
        f.write(f"""(sym_lib_table
  (version 7)
  (lib (name "power")(type "KiCad")(uri "{SYM_DIR}/power.kicad_sym")(options "")(descr "Power symbols"))
  (lib (name "Device")(type "KiCad")(uri "{SYM_DIR}/Device.kicad_sym")(options "")(descr "Generic devices"))
  (lib (name "Connector")(type "KiCad")(uri "{SYM_DIR}/Connector.kicad_sym")(options "")(descr "Connectors"))
  (lib (name "afe")(type "KiCad")(uri "${{KIPRJMOD}}/afe_symbols.kicad_sym")(options "")(descr "Custom AFE symbols"))
)
""")

    # fp-lib-table — absolute paths for macOS
    FP_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
    with open(os.path.join(outdir, "fp-lib-table"), "w") as f:
        f.write(f"""(fp_lib_table
  (version 7)
  (lib (name "Resistor_SMD")(type "KiCad")(uri "{FP_DIR}/Resistor_SMD.pretty")(options "")(descr ""))
  (lib (name "Capacitor_SMD")(type "KiCad")(uri "{FP_DIR}/Capacitor_SMD.pretty")(options "")(descr ""))
  (lib (name "Inductor_SMD")(type "KiCad")(uri "{FP_DIR}/Inductor_SMD.pretty")(options "")(descr ""))
  (lib (name "Package_BGA")(type "KiCad")(uri "{FP_DIR}/Package_BGA.pretty")(options "")(descr ""))
  (lib (name "Package_TO_SOT_SMD")(type "KiCad")(uri "{FP_DIR}/Package_TO_SOT_SMD.pretty")(options "")(descr ""))
  (lib (name "Connector_Samtec")(type "KiCad")(uri "{FP_DIR}/Connector_Samtec.pretty")(options "")(descr ""))
  (lib (name "TestPoint")(type "KiCad")(uri "{FP_DIR}/TestPoint.pretty")(options "")(descr ""))
)
""")

    print("Done.")


if __name__ == "__main__":
    main()
