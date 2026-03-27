#!/usr/bin/env python3
"""
Generate a real KiCad 9 schematic for the Neural AFE Headstage.
V2: All coordinates on 2.54mm grid. Exact pin endpoint calculation.
"""

import uuid, os, json

G = 2.54  # grid step in mm

def uid():
    return str(uuid.uuid4())

# ═════════════════════════════════════════════════════════════════════════════
# GRID HELPERS
# ═════════════════════════════════════════════════════════════════════════════
def g(n):
    """Convert grid units to mm: g(10) = 25.4mm"""
    return round(n * G, 4)

def pin_endpoint(comp_x, comp_y, comp_rot, pin_x, pin_y, pin_rot_deg, pin_len):
    """
    Calculate the absolute connection point of a pin.
    comp_x/y: component placement position
    comp_rot: component rotation in degrees
    pin_x/y: pin position in symbol definition (relative to origin)
    pin_rot_deg: pin direction angle (0=right, 90=up, 180=left, 270=down)
    pin_len: pin length

    The pin's connection point (where a wire attaches) is at the START of the pin,
    which is at (pin_x, pin_y) in the symbol definition. The pin extends from there
    toward the component body.

    In KiCad: the pin "at" coordinate is the CONNECTION point (wire end).
    After component rotation, we need to transform pin's local coordinates.
    """
    import math
    # Pin connection point in component-local coordinates
    local_x = pin_x
    local_y = pin_y

    # Apply component rotation
    rad = math.radians(-comp_rot)  # KiCad uses CW rotation
    abs_x = comp_x + local_x * math.cos(rad) - local_y * math.sin(rad)
    abs_y = comp_y + local_x * math.sin(rad) + local_y * math.cos(rad)

    return round(abs_x, 4), round(abs_y, 4)


# ═════════════════════════════════════════════════════════════════════════════
# SYMBOL DEFINITIONS (for lib_symbols section)
# ═════════════════════════════════════════════════════════════════════════════

# All pin lengths = 2.54mm (1 grid unit) for consistency

LIB_SYMBOLS = {}

def register_sym(lib_id, definition):
    LIB_SYMBOLS[lib_id] = definition

# ---- GND ----
register_sym("power:GND", """
    (symbol "power:GND"
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -5.08 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "GND" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "GND_0_1"
        (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 270) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
    )""")

# ---- +3V3 (used as AVDD) ----
register_sym("power:+3V3", """
    (symbol "power:+3V3"
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "+3V3" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "+3V3_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27)) (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "+3V3_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
    )""")

# ---- VIN ----
register_sym("power:VCC", """
    (symbol "power:VCC"
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "VCC" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "VCC_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27)) (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "VCC_1_1"
        (pin power_in line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
    )""")

# ---- PWR_FLAG ----
register_sym("power:PWR_FLAG", """
    (symbol "power:PWR_FLAG"
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "PWR_FLAG" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "PWR_FLAG_0_1"
        (pin power_out line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
    )""")

# ---- Resistor (symmetric, pin1 at top, pin2 at bottom) ----
register_sym("Device:R", """
    (symbol "Device:R"
      (pin_names (offset 0))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 2.54 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at -2.54 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 90) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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
    )""")

# ---- Capacitor ----
register_sym("Device:C", """
    (symbol "Device:C"
      (pin_names (offset 0.254))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 1.27 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "C" (at 1.27 -2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "C_0_1"
        (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762))
          (stroke (width 0.508) (type default)) (fill (type none)))
        (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762))
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
    )""")

# ---- Ferrite Bead ----
register_sym("Device:FerriteBead", """
    (symbol "Device:FerriteBead"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "FB" (at 1.905 0.635 0) (effects (font (size 1.27 1.27))))
      (property "Value" "FerriteBead" (at 1.905 -1.905 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 90) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "FerriteBead_0_1"
        (rectangle (start -1.016 -2.54) (end 1.016 2.54)
          (stroke (width 0.254) (type default)) (fill (type none)))
      )
      (symbol "FerriteBead_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))
      )
    )""")

# ---- RHD2164 simplified ----
# Pin positions defined on 2.54mm grid, all pin lengths = 5.08mm (2 grid units)
# Body: rectangle from (-12.7, 7.62) to (12.7, -25.4)
# Left pins at x=-17.78 (body edge -12.7, pin length 5.08 → connection at -17.78)
# Right pins at x=17.78
# Top pins at y=12.7 (body edge 7.62, pin length 5.08 → connection at 12.7)
# Bottom pins at y=-30.48 (body edge -25.4, pin length 5.08 → connection at -30.48)
RHD_PIN_LEN = 5.08
RHD_BODY_LEFT = -12.7
RHD_BODY_RIGHT = 12.7
RHD_BODY_TOP = 7.62
RHD_BODY_BOT = -25.4

# Pin definitions: (name, number, type, side, offset_along_side)
# side: L=left, R=right, T=top, B=bottom
# offset: position along the side (in grid units from center or indexed)
RHD_PINS = [
    # Left side (analog inputs) - pin_x = RHD_BODY_LEFT - RHD_PIN_LEN = -17.78
    ("in0",      "A1",  "input",  "L", 0),
    ("in1",      "A2",  "input",  "L", 1),
    ("in2",      "A3",  "input",  "L", 2),
    ("in3",      "A4",  "input",  "L", 3),
    ("in32",     "H1",  "input",  "L", 5),
    ("in33",     "H2",  "input",  "L", 6),
    ("in34",     "H3",  "input",  "L", 7),
    ("in35",     "H4",  "input",  "L", 8),
    ("ref_elec", "A17", "input",  "L", 10),
    # Right side (SPI + control)
    ("CS",       "N8",  "input",  "R", 0),
    ("SCLK",     "N10", "input",  "R", 1),
    ("MOSI",     "N12", "input",  "R", 2),
    ("MISO_A",   "N14", "output", "R", 3),
    ("MISO_B",   "N7",  "output", "R", 4),
    ("LVDS_en",  "M16", "input",  "R", 6),
    ("auxout",   "N16", "output", "R", 7),
    ("auxin1",   "N3",  "input",  "R", 9),
    ("auxin2",   "N4",  "input",  "R", 10),
    ("auxin3",   "N5",  "input",  "R", 11),
    # Top (power in)
    ("VDD_1",    "M15", "power_in", "T", -1),
    ("VDD_2",    "N2",  "power_in", "T", 0),
    ("VDD_3",    "N15", "power_in", "T", 1),
    # Bottom (ground + misc)
    ("GND_1",    "M17", "power_in", "B", -2),
    ("GND_2",    "N1",  "power_in", "B", -1),
    ("GND_3",    "N6",  "power_in", "B", 0),
    ("ADC_ref",  "N17", "passive",  "B", 1),
    ("VESD",     "L1",  "passive",  "B", 2),
]

def make_rhd2164_libsym():
    pin_defs = ""
    for pname, pnum, ptype, side, idx in RHD_PINS:
        if side == "L":
            px = RHD_BODY_LEFT - RHD_PIN_LEN
            py = RHD_BODY_TOP - 2.54 - idx * 2.54
            pdir = 0  # facing right
        elif side == "R":
            px = RHD_BODY_RIGHT + RHD_PIN_LEN
            py = RHD_BODY_TOP - 2.54 - idx * 2.54
            pdir = 180  # facing left
        elif side == "T":
            px = idx * 5.08
            py = RHD_BODY_TOP + RHD_PIN_LEN
            pdir = 270  # facing down
        elif side == "B":
            px = idx * 5.08
            py = RHD_BODY_BOT - RHD_PIN_LEN
            pdir = 90  # facing up
        pin_defs += f"""
        (pin {ptype} line (at {px} {py} {pdir}) (length {RHD_PIN_LEN})
          (name "{pname}" (effects (font (size 1.0 1.0))))
          (number "{pnum}" (effects (font (size 0.8 0.8)))))"""

    return f"""
    (symbol "afe:RHD2164"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 {RHD_BODY_TOP + 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "RHD2164" (at 0 {RHD_BODY_BOT - 2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "https://intantech.com/files/Intan_RHD2164_datasheet.pdf"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "64-ch digital electrophysiology interface, DDR SPI, BGA"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "RHD2164_1_1"
        (rectangle (start {RHD_BODY_LEFT} {RHD_BODY_TOP}) (end {RHD_BODY_RIGHT} {RHD_BODY_BOT})
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""

register_sym("afe:RHD2164", make_rhd2164_libsym())

# Helper to get RHD pin connection point in absolute coords
def rhd_pin_abs(comp_x, comp_y, pin_name):
    """Get absolute connection point for a named RHD2164 pin"""
    for pname, pnum, ptype, side, idx in RHD_PINS:
        if pname == pin_name:
            if side == "L":
                px = RHD_BODY_LEFT - RHD_PIN_LEN
                py = RHD_BODY_TOP - 2.54 - idx * 2.54
            elif side == "R":
                px = RHD_BODY_RIGHT + RHD_PIN_LEN
                py = RHD_BODY_TOP - 2.54 - idx * 2.54
            elif side == "T":
                px = idx * 5.08
                py = RHD_BODY_TOP + RHD_PIN_LEN
            elif side == "B":
                px = idx * 5.08
                py = RHD_BODY_BOT - RHD_PIN_LEN
            return comp_x + px, comp_y + py
    raise ValueError(f"Unknown pin: {pin_name}")

# ---- ADP151 LDO ----
# Box: (-7.62, 5.08) to (7.62, -5.08)
# VIN: left at y=2.54, EN: left at y=-2.54, VOUT: right at y=2.54, GND: bottom
ADP_PINS = [
    ("VIN",  "1", "power_in",  "L", 2.54),
    ("EN",   "3", "input",     "L", -2.54),
    ("VOUT", "5", "power_out", "R", 2.54),
    ("GND",  "2", "power_in",  "B", 0),
]
ADP_PIN_LEN = 5.08

def make_adp151_libsym():
    pin_defs = ""
    for pname, pnum, ptype, side, offset in ADP_PINS:
        if side == "L":
            px, py, pdir = -7.62 - ADP_PIN_LEN, offset, 0
        elif side == "R":
            px, py, pdir = 7.62 + ADP_PIN_LEN, offset, 180
        elif side == "B":
            px, py, pdir = offset, -5.08 - ADP_PIN_LEN, 90
        pin_defs += f"""
        (pin {ptype} line (at {px} {py} {pdir}) (length {ADP_PIN_LEN})
          (name "{pname}" (effects (font (size 1.27 1.27))))
          (number "{pnum}" (effects (font (size 1.27 1.27)))))"""

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
        (rectangle (start -7.62 5.08) (end 7.62 -5.08)
          (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
      )
    )"""

register_sym("afe:ADP151", make_adp151_libsym())

def adp_pin_abs(comp_x, comp_y, pin_name):
    for pname, pnum, ptype, side, offset in ADP_PINS:
        if pname == pin_name:
            if side == "L":
                return comp_x - 7.62 - ADP_PIN_LEN, comp_y + offset
            elif side == "R":
                return comp_x + 7.62 + ADP_PIN_LEN, comp_y + offset
            elif side == "B":
                return comp_x + offset, comp_y - 5.08 - ADP_PIN_LEN
    raise ValueError(f"Unknown ADP pin: {pin_name}")

# ---- B2B 30-pin connector ----
B2B_NPINS = 30
B2B_PIN_LEN = 2.54
B2B_PIN_NAMES = [
    "GND", "GND",                # 1,2
    "SCLK", "GND",               # 3,4
    "MOSI", "GND",               # 5,6
    "CS1", "GND",                # 7,8 (GND inserted per fix)
    "CS2", "GND",                # 9,10
    "MISO1_A", "MISO1_B",        # 11,12
    "GND", "GND",                # 13,14
    "MISO2_A", "MISO2_B",        # 15,16
    "GND", "GND",                # 17,18
    "VIN", "VIN",                # 19,20
    "GND", "GND",                # 21,22
    "VIN", "VIN",                # 23,24
    "GND", "GND",                # 25,26
    "TEST_SHORT_EN", "CAL_EN",   # 27,28
    "SPARE", "GND",              # 29,30
]

def make_b2b_libsym():
    half_h = (B2B_NPINS - 1) * 2.54 / 2  # half-height
    pin_defs = ""
    for i in range(B2B_NPINS):
        y = half_h - i * 2.54
        pname = B2B_PIN_NAMES[i]
        pin_defs += f"""
        (pin passive line (at {-2.54 - B2B_PIN_LEN} {y} 0) (length {B2B_PIN_LEN})
          (name "{pname}" (effects (font (size 1.0 1.0))))
          (number "{i+1}" (effects (font (size 0.8 0.8)))))"""

    top = half_h + 2.54
    bot = -half_h - 2.54
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

def b2b_pin_abs(comp_x, comp_y, pin_index_0based):
    """Get absolute connection point for B2B pin (0-based index)"""
    half_h = (B2B_NPINS - 1) * 2.54 / 2
    y = half_h - pin_index_0based * 2.54
    x = -2.54 - B2B_PIN_LEN  # pin connection point
    return comp_x + x, comp_y + y


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMATIC BUILDER
# ═════════════════════════════════════════════════════════════════════════════

class Schematic:
    def __init__(self):
        self.root_uuid = uid()
        self.project = "afe-headstage-real"
        self.items = []
        self.pwr_n = 0
        self.flg_n = 0

    def _place(self, lib_id, x, y, ref_prefix, ref_num, value, rot=0, pin_nums=None, hide_ref=False):
        self.pwr_n += 1
        if ref_prefix == "#PWR":
            ref = f"#PWR{self.pwr_n:02d}"
        elif ref_prefix == "#FLG":
            self.flg_n += 1
            ref = f"#FLG{self.flg_n:02d}"
        else:
            ref = f"{ref_prefix}{ref_num}"

        hide = ' (hide yes)' if ref_prefix in ('#PWR', '#FLG') or hide_ref else ''

        pins = ""
        if pin_nums:
            for pn in pin_nums:
                pins += f'\n      (pin "{pn}" (uuid "{uid()}"))'

        block = f"""
    (symbol
      (lib_id "{lib_id}")
      (at {x} {y} {rot})
      (unit 1)
      (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
      (uuid "{uid()}")
      (property "Reference" "{ref}" (at {x+2.54} {y-2.54} 0) (effects (font (size 1.27 1.27)){hide}))
      (property "Value" "{value}" (at {x+2.54} {y+2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes))){pins}
      (instances
        (project "{self.project}"
          (path "/{self.root_uuid}" (reference "{ref}") (unit 1))))
    )"""
        self.items.append(block)
        return ref

    def wire(self, x1, y1, x2, y2):
        self.items.append(f"""
    (wire (pts (xy {x1} {y1}) (xy {x2} {y2}))
      (stroke (width 0) (type default)) (uuid "{uid()}"))""")

    def label(self, name, x, y, rot=0):
        self.items.append(f"""
    (label "{name}" (at {x} {y} {rot})
      (effects (font (size 1.27 1.27)) (justify left bottom))
      (uuid "{uid()}"))""")

    def glabel(self, name, x, y, rot=0, shape="input"):
        self.items.append(f"""
    (global_label "{name}" (shape {shape}) (at {x} {y} {rot})
      (effects (font (size 1.27 1.27)) (justify {"right" if rot == 180 else "left"}))
      (uuid "{uid()}")
      (property "Intersheetrefs" "${{INTERSHEET_REFS}}"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))))""")

    def no_connect(self, x, y):
        self.items.append(f"""
    (no_connect (at {x} {y}) (uuid "{uid()}"))""")

    def junction(self, x, y):
        self.items.append(f"""
    (junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "{uid()}"))""")

    # Convenience: place power symbol
    def pwr(self, sym, x, y, rot=0):
        """Place a power symbol (GND, +3V3, VCC, PWR_FLAG)"""
        return self._place(f"power:{sym}", x, y, "#PWR" if sym != "PWR_FLAG" else "#FLG", 0, sym, rot=rot, pin_nums=["1"])

    def place(self, lib_id, x, y, ref_prefix, ref_num, value, rot=0, pin_nums=None):
        return self._place(lib_id, x, y, ref_prefix, ref_num, value, rot=rot, pin_nums=pin_nums)

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
    (title "Neural AFE Headstage")
    (date "2025-02-17")
    (rev "0.1")
    (company "BCIInterface")
    (comment 1 "128-ch Neural AFE (partial: 4ch + 1x RHD2164)")
    (comment 2 "DDR SPI passthrough to FPGA carrier | 3.3V LVCMOS")
    (comment 3 "B2B: Samtec QSH-030 | fs=30kS/s | 16-bit")
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


# ═════════════════════════════════════════════════════════════════════════════
# BUILD THE SCHEMATIC
# ═════════════════════════════════════════════════════════════════════════════

def build():
    s = Schematic()

    # ────────────────────────────────────────────────────────────────────────
    # POWER SECTION (top-left area)
    # VCC (VIN from carrier) → ADP151 → +3V3 (AVDD) → FB → DVDD_AFE net
    # ────────────────────────────────────────────────────────────────────────
    LDO_X, LDO_Y = g(20), g(12)  # 50.8, 30.48

    # VIN pin positions:
    vin_x, vin_y = adp_pin_abs(LDO_X, LDO_Y, "VIN")    # left side
    en_x, en_y   = adp_pin_abs(LDO_X, LDO_Y, "EN")     # left side
    vout_x, vout_y = adp_pin_abs(LDO_X, LDO_Y, "VOUT") # right side
    gnd_x, gnd_y   = adp_pin_abs(LDO_X, LDO_Y, "GND")  # bottom

    s.place("afe:ADP151", LDO_X, LDO_Y, "U", 2, "ADP151-3.3",
            pin_nums=["1", "2", "3", "5"])

    # Power symbols at LDO
    s.pwr("VCC", vin_x, vin_y - g(2))              # VCC above VIN pin
    s.wire(vin_x, vin_y, vin_x, vin_y - g(2))      # wire VIN pin up to VCC
    s.pwr("VCC", en_x, en_y - g(2))                 # EN tied to VCC (always on)
    s.wire(en_x, en_y, en_x, en_y - g(2))
    s.pwr("GND", gnd_x, gnd_y + g(1))               # GND below LDO
    s.wire(gnd_x, gnd_y, gnd_x, gnd_y + g(1))
    s.pwr("+3V3", vout_x, vout_y - g(2))             # +3V3 above VOUT
    s.wire(vout_x, vout_y, vout_x, vout_y - g(2))

    # Input decoupling cap: C1, 1uF, at VIN pin
    C1_X = vin_x
    C1_Y = vin_y + g(3)
    s.place("Device:C", C1_X, C1_Y, "C", 1, "1uF", pin_nums=["1", "2"])
    # C1 pin1 (top) at C1_Y - 3.81 → wire to VIN rail
    s.wire(C1_X, C1_Y - 3.81, C1_X, vin_y)
    s.junction(C1_X, vin_y)
    # C1 pin2 (bottom) at C1_Y + 3.81 → GND
    s.pwr("GND", C1_X, C1_Y + 3.81 + g(1))
    s.wire(C1_X, C1_Y + 3.81, C1_X, C1_Y + 3.81 + g(1))

    # Output decoupling cap: C2, 1uF, at VOUT pin
    C2_X = vout_x
    C2_Y = vout_y + g(3)
    s.place("Device:C", C2_X, C2_Y, "C", 2, "1uF", pin_nums=["1", "2"])
    s.wire(C2_X, C2_Y - 3.81, C2_X, vout_y)
    s.junction(C2_X, vout_y)
    s.pwr("GND", C2_X, C2_Y + 3.81 + g(1))
    s.wire(C2_X, C2_Y + 3.81, C2_X, C2_Y + 3.81 + g(1))

    # Ferrite bead: AVDD → DVDD_AFE (horizontal, rotated 90°)
    # When FB is rotated 90°: pin1 (at 0,3.81 def, rotated→ -3.81,0) = left
    #                         pin2 (at 0,-3.81 def, rotated→ 3.81,0) = right
    FB_X = vout_x + g(4)
    FB_Y = vout_y
    s.place("Device:FerriteBead", FB_X, FB_Y, "FB", 1, "600R@100MHz", rot=90,
            pin_nums=["1", "2"])
    # pin1 connection (left of FB when rotated 90): FB_X - 3.81
    # pin2 connection (right of FB): FB_X + 3.81
    s.wire(vout_x, vout_y, FB_X - 3.81, FB_Y)
    s.junction(vout_x, vout_y)
    # DVDD_AFE label on output
    dvdd_x = FB_X + 3.81
    dvdd_y = FB_Y
    s.label("DVDD_AFE", dvdd_x + 1, dvdd_y)

    # C3: 100nF decoupling on DVDD_AFE
    C3_X = dvdd_x
    C3_Y = dvdd_y + g(3)
    s.place("Device:C", C3_X, C3_Y, "C", 3, "100nF", pin_nums=["1", "2"])
    s.wire(C3_X, C3_Y - 3.81, C3_X, dvdd_y)
    s.pwr("GND", C3_X, C3_Y + 3.81 + g(1))
    s.wire(C3_X, C3_Y + 3.81, C3_X, C3_Y + 3.81 + g(1))

    # PWR_FLAGs for ERC: one on VCC net, one on GND net
    s.pwr("PWR_FLAG", vin_x + g(2), vin_y)
    s.wire(vin_x, vin_y, vin_x + g(2), vin_y)
    s.pwr("PWR_FLAG", gnd_x + g(2), gnd_y)
    s.wire(gnd_x, gnd_y, gnd_x + g(2), gnd_y)

    # ────────────────────────────────────────────────────────────────────────
    # RHD2164 (center of schematic)
    # ────────────────────────────────────────────────────────────────────────
    RHD_X, RHD_Y = g(40), g(30)  # 101.6, 76.2

    s.place("afe:RHD2164", RHD_X, RHD_Y, "U", 1, "RHD2164",
            pin_nums=[p[1] for p in RHD_PINS])

    # VDD pins → +3V3
    for pname in ["VDD_1", "VDD_2", "VDD_3"]:
        px, py = rhd_pin_abs(RHD_X, RHD_Y, pname)
        s.pwr("+3V3", px, py - g(1))
        s.wire(px, py, px, py - g(1))

    # GND pins → GND
    for pname in ["GND_1", "GND_2", "GND_3"]:
        px, py = rhd_pin_abs(RHD_X, RHD_Y, pname)
        s.pwr("GND", px, py + g(1))
        s.wire(px, py, px, py + g(1))

    # ADC_ref → C4 10nF → GND
    adc_x, adc_y = rhd_pin_abs(RHD_X, RHD_Y, "ADC_ref")
    C4_X, C4_Y = adc_x, adc_y + g(3)
    s.place("Device:C", C4_X, C4_Y, "C", 4, "10nF", pin_nums=["1", "2"])
    s.wire(adc_x, adc_y, C4_X, C4_Y - 3.81)
    s.pwr("GND", C4_X, C4_Y + 3.81 + g(1))
    s.wire(C4_X, C4_Y + 3.81, C4_X, C4_Y + 3.81 + g(1))

    # VESD → GND
    vesd_x, vesd_y = rhd_pin_abs(RHD_X, RHD_Y, "VESD")
    s.pwr("GND", vesd_x, vesd_y + g(1))
    s.wire(vesd_x, vesd_y, vesd_x, vesd_y + g(1))

    # LVDS_en → GND (CMOS mode)
    lvds_x, lvds_y = rhd_pin_abs(RHD_X, RHD_Y, "LVDS_en")
    s.pwr("GND", lvds_x + g(2), lvds_y, rot=90)
    s.wire(lvds_x, lvds_y, lvds_x + g(2), lvds_y)

    # Bypass cap C5: 100nF at VDD
    vdd2_x, vdd2_y = rhd_pin_abs(RHD_X, RHD_Y, "VDD_2")
    C5_X = vdd2_x + g(4)
    C5_Y = vdd2_y + g(2)
    s.place("Device:C", C5_X, C5_Y, "C", 5, "100nF", pin_nums=["1", "2"])
    s.pwr("+3V3", C5_X, C5_Y - 3.81 - g(1))
    s.wire(C5_X, C5_Y - 3.81, C5_X, C5_Y - 3.81 - g(1))
    s.pwr("GND", C5_X, C5_Y + 3.81 + g(1))
    s.wire(C5_X, C5_Y + 3.81, C5_X, C5_Y + 3.81 + g(1))

    # SPI signals → global labels
    spi_map = [
        ("CS",     "CS1",     "input"),
        ("SCLK",   "SCLK",    "input"),
        ("MOSI",   "MOSI",    "input"),
        ("MISO_A", "MISO1_A", "output"),
        ("MISO_B", "MISO1_B", "output"),
    ]
    for rhd_name, gl_name, shape in spi_map:
        px, py = rhd_pin_abs(RHD_X, RHD_Y, rhd_name)
        s.glabel(gl_name, px + g(2), py, 0, shape)
        s.wire(px, py, px + g(2), py)

    # auxout, auxin1-3: no-connect
    for nc_pin in ["auxout", "auxin1", "auxin2", "auxin3"]:
        px, py = rhd_pin_abs(RHD_X, RHD_Y, nc_pin)
        s.no_connect(px, py)

    # Unused analog inputs: in32-in35 → no-connect for now
    for nc_pin in ["in32", "in33", "in34", "in35"]:
        px, py = rhd_pin_abs(RHD_X, RHD_Y, nc_pin)
        s.no_connect(px, py)

    # ────────────────────────────────────────────────────────────────────────
    # ELECTRODE INPUTS (4 channels: in0-in3)
    # Each: wire → bias R (10M to GND) → RHD2164 input
    # ────────────────────────────────────────────────────────────────────────
    for i, ch_name in enumerate(["in0", "in1", "in2", "in3"]):
        inp_x, inp_y = rhd_pin_abs(RHD_X, RHD_Y, ch_name)

        # Bias resistor: 10M to GND, placed to the left of the input
        R_X = inp_x - g(4)
        R_Y = inp_y + g(3)
        s.place("Device:R", R_X, R_Y, "R", i + 1, "10M", pin_nums=["1", "2"])
        # R pin1 (top) at R_Y - 3.81 → wire to input net
        s.wire(R_X, R_Y - 3.81, R_X, inp_y)
        s.wire(R_X, inp_y, inp_x, inp_y)
        # R pin2 (bottom) at R_Y + 3.81 → GND
        s.pwr("GND", R_X, R_Y + 3.81 + g(1))
        s.wire(R_X, R_Y + 3.81, R_X, R_Y + 3.81 + g(1))

        # Net label
        s.label(f"CH{i}", R_X - g(1), inp_y)

    # ref_elec → R5 0R → GND
    ref_x, ref_y = rhd_pin_abs(RHD_X, RHD_Y, "ref_elec")
    R5_X = ref_x - g(4)
    R5_Y = ref_y
    s.place("Device:R", R5_X, R5_Y, "R", 5, "0R", rot=90, pin_nums=["1", "2"])
    # Rotated 90°: pin1 at R5_X-3.81, pin2 at R5_X+3.81
    s.wire(R5_X + 3.81, R5_Y, ref_x, ref_y)
    s.pwr("GND", R5_X - 3.81 - g(1), R5_Y, rot=90)
    s.wire(R5_X - 3.81, R5_Y, R5_X - 3.81 - g(1), R5_Y)

    # ────────────────────────────────────────────────────────────────────────
    # B2B CONNECTOR (right side of schematic)
    # ────────────────────────────────────────────────────────────────────────
    B2B_X, B2B_Y = g(70), g(25)  # 177.8, 63.5

    s.place("afe:B2B_30", B2B_X, B2B_Y, "J", 5, "QSH-030",
            pin_nums=[str(i+1) for i in range(30)])

    # Wire each B2B pin
    for i in range(B2B_NPINS):
        px, py = b2b_pin_abs(B2B_X, B2B_Y, i)
        pname = B2B_PIN_NAMES[i]

        if pname == "GND":
            s.pwr("GND", px - g(2), py, rot=90)
            s.wire(px, py, px - g(2), py)
        elif pname == "VIN":
            s.pwr("VCC", px - g(2), py, rot=270)
            s.wire(px, py, px - g(2), py)
        elif pname == "SPARE":
            s.no_connect(px, py)
        else:
            shape = "output" if "MISO" in pname else "input"
            s.glabel(pname, px - g(2), py, 180, shape)
            s.wire(px, py, px - g(2), py)

    return s.build()


def main():
    outdir = "/Users/aharshi/BCIInterface/hardware/afe-headstage-real"
    os.makedirs(outdir, exist_ok=True)

    # Write schematic
    sch = build()
    sch_path = os.path.join(outdir, "afe-headstage-real.kicad_sch")
    with open(sch_path, "w") as f:
        f.write(sch)
    print(f"Wrote {sch_path} ({len(sch)} bytes)")

    # Write project file
    proj = {
        "meta": {"filename": "afe-headstage-real.kicad_pro", "version": 1},
        "net_settings": {
            "classes": [{
                "bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25,
                "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2,
                "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "name": "Default", "pcb_color": "rgba(0, 0, 0, 0.000)",
                "schematic_color": "rgba(0, 0, 0, 0.000)",
                "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3,
                "wire_width": 6
            }],
            "meta": {"version": 3},
            "net_colors": None, "netclass_assignments": None, "netclass_patterns": []
        },
        "pcbnew": {"last_paths": {
            "gencad": "", "idf": "", "netlist": "", "plot": "",
            "pos_files": "", "specctra_dsn": "", "step": "", "svg": "", "vrml": ""
        }, "page_layout_descr_file": ""},
        "schematic": {
            "annotate_start_num": 0, "drawing": {
                "default_line_thickness": 6.0, "default_text_size": 50.0,
                "field_names": [], "junction_size_choice": 3,
                "label_size_ratio": 0.375, "pin_symbol_size": 25.0,
                "text_offset_ratio": 0.15
            },
            "meta": {"version": 1},
            "page_layout_descr_file": ""
        },
        "sheets": [], "text_variables": {}
    }
    proj_path = os.path.join(outdir, "afe-headstage-real.kicad_pro")
    with open(proj_path, "w") as f:
        json.dump(proj, f, indent=2)
    print(f"Wrote {proj_path}")

    # Write symbol library (standalone, for reference)
    syms = "\n".join(LIB_SYMBOLS.values())
    sym_lib = f"""(kicad_symbol_lib
  (version 20231120)
  (generator "custom")
  (generator_version "1.0")
{syms}
)
"""
    sym_path = os.path.join(outdir, "afe_symbols.kicad_sym")
    with open(sym_path, "w") as f:
        f.write(sym_lib)
    print(f"Wrote {sym_path}")

    # sym-lib-table: only reference our local library (all symbols embedded in schematic anyway)
    slt = """(sym_lib_table
  (version 7)
  (lib (name "afe")(type "KiCad")(uri "${KIPRJMOD}/afe_symbols.kicad_sym")(options "")(descr "Custom AFE symbols"))
)
"""
    with open(os.path.join(outdir, "sym-lib-table"), "w") as f:
        f.write(slt)

    # fp-lib-table: minimal
    flt = """(fp_lib_table
  (version 7)
)
"""
    with open(os.path.join(outdir, "fp-lib-table"), "w") as f:
        f.write(flt)

    print("Done.")


if __name__ == "__main__":
    main()
