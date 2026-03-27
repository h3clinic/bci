#!/usr/bin/env python3
"""
Generate a real KiCad 9 schematic for the Neural AFE Headstage.
V3: Fixed Y-axis inversion. In KiCad schematics, component placement uses
screen coordinates (Y increases downward), but pin offsets in lib_symbols
use standard math coordinates (Y increases upward). The absolute pin
connection point is: (comp_x + pin_x, comp_y - pin_y).
"""

import uuid, os, json

G = 2.54  # grid step in mm

def uid():
    return str(uuid.uuid4())

def g(n):
    """Grid units to mm"""
    return round(n * G, 4)


# ═════════════════════════════════════════════════════════════════════════════
# SYMBOL DEFINITIONS
# ═════════════════════════════════════════════════════════════════════════════

LIB_SYMBOLS = {}
def register_sym(lib_id, text):
    LIB_SYMBOLS[lib_id] = text

# Power symbols
register_sym("power:GND", """
    (symbol "power:GND"
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -6.35 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "GND" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Power symbol creates a global label with name \\"GND\\" , ground"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "global power" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "+3V3" (at 0 3.556 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Power symbol creates a global label with name \\"+3V3\\""
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "global power" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "+3V3_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
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
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "VCC" (at 0 3.556 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Power symbol creates a global label with name \\"VCC\\""
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "global power" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "VCC_0_1"
        (polyline (pts (xy -0.762 1.27) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 2.54) (xy 0.762 1.27)) (stroke (width 0) (type default)) (fill (type none)))
        (polyline (pts (xy 0 0) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
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
      (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Value" "PWR_FLAG" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Special symbol for telling ERC where power comes from"
        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "flag power" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "PWR_FLAG_0_1"
        (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27))
          (stroke (width 0) (type default)) (fill (type none)))
      )
      (symbol "PWR_FLAG_0_0"
        (pin power_out line (at 0 0 90) (length 0)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
      )
      (embedded_fonts no)
    )""")

register_sym("Connector:TestPoint", """
    (symbol "Connector:TestPoint"
      (pin_numbers (hide yes))
      (pin_names (offset 0.762) (hide yes))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "TP" (at 0 6.858 0) (effects (font (size 1.27 1.27))))
      (property "Value" "TestPoint" (at 0 5.08 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 5.08 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 5.08 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "test point" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "test point tp" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "Pin* Test*" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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

register_sym("Device:R", """
    (symbol "Device:R"
      (pin_numbers (hide yes))
      (pin_names (offset 0))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "R res resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "R_*" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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
      (property "Reference" "C" (at 0.635 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "C" (at 0.635 -2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 0.9652 -3.81 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Unpolarized capacitor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "cap capacitor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "C_*" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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
      (embedded_fonts no)
    )""")

register_sym("Device:FerriteBead", """
    (symbol "Device:FerriteBead"
      (pin_numbers (hide yes))
      (pin_names (offset 0))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "FB" (at -3.81 0.635 90) (effects (font (size 1.27 1.27))))
      (property "Value" "FerriteBead" (at 3.81 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "Ferrite bead" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_keywords" "L ferrite bead inductor filter" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "ki_fp_filters" "Inductor_* L_* *Ferrite*" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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


# ═════════════════════════════════════════════════════════════════════════════
# PIN COORDINATE SYSTEM
# ═════════════════════════════════════════════════════════════════════════════
# In KiCad lib_symbols:
#   - Pin (at px py dir): px,py is the CONNECTION point in symbol-local coords
#   - Symbol-local coords: X right, Y up (math standard)
#
# In KiCad schematic placement:
#   - Component (at cx cy rot): cx,cy is screen coords (X right, Y DOWN)
#   - To get absolute pin connection: transform local → screen
#
# Transform (no component rotation, rot=0):
#   abs_x = cx + pin_local_x
#   abs_y = cy - pin_local_y   ← Y inversion!
#
# With component rotation (KiCad CW in screen coords):
#   For rot=90 (CW 90° on screen):
#     abs_x = cx - pin_local_y
#     abs_y = cy - pin_local_x
#   For rot=180:
#     abs_x = cx - pin_local_x
#     abs_y = cy + pin_local_y
#   For rot=270 (CW 270° = CCW 90°):
#     abs_x = cx + pin_local_y
#     abs_y = cy + pin_local_x

def pin_abs(cx, cy, comp_rot, pin_lx, pin_ly):
    """Get absolute screen coordinates of a pin connection point.
    cx, cy: component placement (screen coords)
    comp_rot: component rotation (0, 90, 180, 270)
    pin_lx, pin_ly: pin position in symbol-local coords (Y-up)
    Returns rounded coordinates to avoid floating-point drift.
    """
    if comp_rot == 0:
        return round(cx + pin_lx, 2), round(cy - pin_ly, 2)
    elif comp_rot == 90:
        return round(cx - pin_ly, 2), round(cy - pin_lx, 2)
    elif comp_rot == 180:
        return round(cx - pin_lx, 2), round(cy + pin_ly, 2)
    elif comp_rot == 270:
        return round(cx + pin_ly, 2), round(cy + pin_lx, 2)
    else:
        raise ValueError(f"Unsupported rotation: {comp_rot}")


# ═════════════════════════════════════════════════════════════════════════════
# RHD2164 SYMBOL
# ═════════════════════════════════════════════════════════════════════════════
# Body: (-12.7, -25.4) to (12.7, 7.62) in local coords (Y-up)
# Pin connection points in local coords:

PLEN = 5.08  # pin length

RHD_BODY = {"left": -12.7, "right": 12.7, "top": 7.62, "bot": -25.4}

# Pin definitions: name, number, type, local_x, local_y
RHD_PINS = []

# Left side pins (at x = body_left - pin_length = -17.78, facing right)
left_x = RHD_BODY["left"] - PLEN
left_pins = [
    ("in0",  "A1",  "passive", 0),
    ("in1",  "A2",  "passive", 1),
    ("in2",  "A3",  "passive", 2),
    ("in3",  "A4",  "passive", 3),
    # gap
    ("in32", "H1",  "passive", 5),
    ("in33", "H2",  "passive", 6),
    ("in34", "H3",  "passive", 7),
    ("in35", "H4",  "passive", 8),
    # gap
    ("ref_elec", "A17", "passive", 10),
]
for name, num, ptype, idx in left_pins:
    local_y = RHD_BODY["top"] - 2.54 - idx * 2.54
    RHD_PINS.append((name, num, ptype, left_x, local_y, 0))  # dir=0 → right

# Right side pins (at x = body_right + pin_length = 17.78, facing left)
right_x = RHD_BODY["right"] + PLEN
right_pins = [
    ("CS",      "N8",  "input",  0),
    ("SCLK",    "N10", "input",  1),
    ("MOSI",    "N12", "input",  2),
    ("MISO_A",  "N14", "output", 3),
    ("MISO_B",  "N7",  "output", 4),
    # gap
    ("LVDS_en", "M16", "input",  6),
    ("auxout",  "N16", "output", 7),
    # gap
    ("auxin1",  "N3",  "input",  9),
    ("auxin2",  "N4",  "input",  10),
    ("auxin3",  "N5",  "input",  11),
]
for name, num, ptype, idx in right_pins:
    local_y = RHD_BODY["top"] - 2.54 - idx * 2.54
    RHD_PINS.append((name, num, ptype, right_x, local_y, 180))  # dir=180 → left

# Top pins (VDD, at y = body_top + pin_length = 12.7, facing down)
top_y = RHD_BODY["top"] + PLEN
top_pins = [
    ("VDD_1", "M15", "power_in", -1),
    ("VDD_2", "N2",  "power_in", 0),
    ("VDD_3", "N15", "power_in", 1),
]
for name, num, ptype, idx in top_pins:
    local_x = idx * 5.08
    RHD_PINS.append((name, num, ptype, local_x, top_y, 270))

# Bottom pins (GND + misc, at y = body_bot - pin_length = -30.48, facing up)
bot_y = RHD_BODY["bot"] - PLEN
bottom_pins = [
    ("GND_1",   "M17", "power_in", -2),
    ("GND_2",   "N1",  "power_in", -1),
    ("GND_3",   "N6",  "power_in", 0),
    ("ADC_ref", "N17", "passive",  1),
    ("VESD",    "L1",  "passive",  2),
]
for name, num, ptype, idx in bottom_pins:
    local_x = idx * 5.08
    RHD_PINS.append((name, num, ptype, local_x, bot_y, 90))


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


def rhd_pin(comp_x, comp_y, pin_name, comp_rot=0):
    """Get absolute connection point for a named RHD2164 pin"""
    for name, num, ptype, lx, ly, pdir in RHD_PINS:
        if name == pin_name:
            return pin_abs(comp_x, comp_y, comp_rot, lx, ly)
    raise ValueError(f"Unknown RHD pin: {pin_name}")


# ═════════════════════════════════════════════════════════════════════════════
# ADP151 LDO SYMBOL
# ═════════════════════════════════════════════════════════════════════════════
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


def adp_pin(comp_x, comp_y, pin_name, comp_rot=0):
    for name, num, ptype, lx, ly, pdir in ADP_PINS:
        if name == pin_name:
            return pin_abs(comp_x, comp_y, comp_rot, lx, ly)
    raise ValueError(f"Unknown ADP pin: {pin_name}")


# ═════════════════════════════════════════════════════════════════════════════
# B2B 30-PIN CONNECTOR SYMBOL
# ═════════════════════════════════════════════════════════════════════════════
B2B_NPINS = 30
B2B_PLEN = 2.54
B2B_PIN_NAMES = [
    "GND", "GND",
    "SCLK", "GND",
    "MOSI", "GND",
    "CS1", "GND",
    "CS2", "GND",
    "MISO1_A", "MISO1_B",
    "GND", "GND",
    "MISO2_A", "MISO2_B",
    "GND", "GND",
    "VIN", "VIN",
    "GND", "GND",
    "VIN", "VIN",
    "GND", "GND",
    "TEST_SHORT_EN", "CAL_EN",
    "SPARE", "GND",
]

B2B_HALF_H = (B2B_NPINS - 1) * 2.54 / 2  # 36.83

# Pin local coords: connection at left side, pin facing right
B2B_PINS = []  # (name, number, local_x, local_y)
for i in range(B2B_NPINS):
    local_y = B2B_HALF_H - i * 2.54
    local_x = -2.54 - B2B_PLEN  # connection point
    B2B_PINS.append((B2B_PIN_NAMES[i], str(i+1), local_x, local_y, 0))

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


def b2b_pin(comp_x, comp_y, pin_index_0, comp_rot=0):
    """Get absolute connection for B2B pin by 0-based index"""
    name, num, lx, ly, pdir = B2B_PINS[pin_index_0]
    return pin_abs(comp_x, comp_y, comp_rot, lx, ly)


# ═════════════════════════════════════════════════════════════════════════════
# PIN HELPERS FOR PASSIVES
# ═════════════════════════════════════════════════════════════════════════════
# Resistor & Cap: pin1 at local (0, 3.81), pin2 at local (0, -3.81)
# Ferrite: same

def passive_pin1(cx, cy, rot=0):
    """Top pin of R/C/FB"""
    return pin_abs(cx, cy, rot, 0, 3.81)

def passive_pin2(cx, cy, rot=0):
    """Bottom pin of R/C/FB"""
    return pin_abs(cx, cy, rot, 0, -3.81)

# Power symbols: GND pin at local (0,0) with dir=270 (length 0)
# +3V3/VCC pin at local (0,0) with dir=90 (length 0)
# So the connection point IS the placement point.
def pwr_conn(cx, cy, rot=0):
    """Power symbol connection point = placement point"""
    return cx, cy


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMATIC BUILDER
# ═════════════════════════════════════════════════════════════════════════════

class Sch:
    def __init__(self):
        self.root_uuid = uid()
        self.project = "afe-headstage-real"
        self.items = []
        self.pwr_n = 0
        self.flg_n = 0

    def _place(self, lib_id, x, y, ref_pre, ref_num, value, rot=0, pin_nums=None):
        self.pwr_n += 1
        if ref_pre == "#PWR":
            ref = f"#PWR{self.pwr_n:02d}"
        elif ref_pre == "#FLG":
            self.flg_n += 1
            ref = f"#FLG{self.flg_n:02d}"
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
      (property "Reference" "{ref}" (at {x+2.54} {y-2.54} 0) (effects (font (size 1.27 1.27)){hide}))
      (property "Value" "{value}" (at {x+2.54} {y+2.54} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes))){pins}
      (instances
        (project "{self.project}"
          (path "/{self.root_uuid}" (reference "{ref}") (unit 1))))
    )""")
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
        self.items.append(f"""
    (junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "{uid()}"))""")

    def wire_tee(self, x1, y1, x2, y2, xs, ys):
        """T-connection: split wire (x1,y1)→(x2,y2) at branch point (xs,ys).
        Emits two wire segments and a junction.  KiCad's connectivity engine
        does NOT auto-split a single wire at a mid-point junction — you MUST
        provide explicit segments on both sides of the branch.
        """
        self.wire(x1, y1, xs, ys)
        self.wire(xs, ys, x2, y2)
        self.junc(xs, ys)

    def pwr(self, sym, x, y, rot=0):
        return self._place(f"power:{sym}", x, y, "#PWR" if sym != "PWR_FLAG" else "#FLG", 0, sym, rot=rot, pin_nums=["1"])

    def place(self, lib_id, x, y, ref_pre, ref_num, value, rot=0, pin_nums=None):
        return self._place(lib_id, x, y, ref_pre, ref_num, value, rot=rot, pin_nums=pin_nums)

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
# BUILD
# ═════════════════════════════════════════════════════════════════════════════

def build():
    s = Sch()

    # ── POWER SECTION ──────────────────────────────────────────────────────
    LDO_X, LDO_Y = g(20), g(15)  # 50.8, 38.1

    s.place("afe:ADP151", LDO_X, LDO_Y, "U", 2, "ADP151-3.3",
            pin_nums=["1", "2", "3", "5"])

    # Pin absolute positions
    vin_x, vin_y = adp_pin(LDO_X, LDO_Y, "VIN")
    en_x, en_y   = adp_pin(LDO_X, LDO_Y, "EN")
    vout_x, vout_y = adp_pin(LDO_X, LDO_Y, "VOUT")
    gnd_x, gnd_y   = adp_pin(LDO_X, LDO_Y, "GND")

    print(f"  LDO VIN  pin: ({vin_x}, {vin_y})")
    print(f"  LDO EN   pin: ({en_x}, {en_y})")
    print(f"  LDO VOUT pin: ({vout_x}, {vout_y})")
    print(f"  LDO GND  pin: ({gnd_x}, {gnd_y})")

    # VCC → VIN
    s.pwr("VCC", vin_x, vin_y - g(2))
    s.wire(vin_x, vin_y, vin_x, vin_y - g(2))

    # EN tied to VCC (wire up then over)
    s.wire(en_x, en_y, en_x, vin_y - g(2))
    s.pwr("VCC", en_x, vin_y - g(2))

    # GND below LDO
    s.pwr("GND", gnd_x, gnd_y + g(1))
    s.wire(gnd_x, gnd_y, gnd_x, gnd_y + g(1))

    # +3V3 above VOUT
    s.pwr("+3V3", vout_x, vout_y - g(2))
    s.wire(vout_x, vout_y, vout_x, vout_y - g(2))

    # C1: 1uF input decoupling at VIN
    C1_X, C1_Y = vin_x, vin_y + g(3)
    s.place("Device:C", C1_X, C1_Y, "C", 1, "1uF", pin_nums=["1", "2"])
    c1_p1 = passive_pin1(C1_X, C1_Y)
    c1_p2 = passive_pin2(C1_X, C1_Y)
    s.wire(c1_p1[0], c1_p1[1], C1_X, vin_y)  # top to VIN rail
    s.junc(C1_X, vin_y)
    s.pwr("GND", c1_p2[0], c1_p2[1] + g(1))
    s.wire(c1_p2[0], c1_p2[1], c1_p2[0], c1_p2[1] + g(1))

    # C2: 1uF output decoupling at VOUT
    C2_X, C2_Y = vout_x, vout_y + g(3)
    s.place("Device:C", C2_X, C2_Y, "C", 2, "1uF", pin_nums=["1", "2"])
    c2_p1 = passive_pin1(C2_X, C2_Y)
    c2_p2 = passive_pin2(C2_X, C2_Y)
    s.wire(c2_p1[0], c2_p1[1], C2_X, vout_y)
    s.junc(C2_X, vout_y)
    s.pwr("GND", c2_p2[0], c2_p2[1] + g(1))
    s.wire(c2_p2[0], c2_p2[1], c2_p2[0], c2_p2[1] + g(1))

    # FB1: ferrite bead +3V3 → VDD_AFE_FILT (rotated 270°)
    # After rot=270: pin1(local top)=RIGHT, pin2(local bot)=LEFT
    FB_X, FB_Y = vout_x + g(4), vout_y
    s.place("Device:FerriteBead", FB_X, FB_Y, "FB", 1, "600R@100MHz", rot=270,
            pin_nums=["1", "2"])
    fb_p1 = passive_pin1(FB_X, FB_Y, rot=270)  # RIGHT (output → VDD_AFE_FILT)
    fb_p2 = passive_pin2(FB_X, FB_Y, rot=270)  # LEFT  (input  ← +3V3)
    print(f"  FB pin1: {fb_p1}, pin2: {fb_p2}")
    # Wire +3V3 → FB pin2 (input), T-split at TP1 tap point.
    # wire_tee prevents the KiCad mid-wire junction orphaning bug.
    tp1_junc_x = vout_x + g(2)
    s.wire_tee(vout_x, vout_y, fb_p2[0], fb_p2[1], tp1_junc_x, vout_y)
    s.junc(vout_x, vout_y)  # extra junction for C2/+3V3/LDO node

    # C3: 100nF on VDD_AFE_FILT (output side = pin1 = RIGHT)
    C3_X, C3_Y = fb_p1[0], fb_p1[1] + g(3)
    s.place("Device:C", C3_X, C3_Y, "C", 3, "100nF", pin_nums=["1", "2"])
    c3_p1 = passive_pin1(C3_X, C3_Y)
    c3_p2 = passive_pin2(C3_X, C3_Y)
    s.wire(c3_p1[0], c3_p1[1], C3_X, fb_p1[1])  # cap to FB output (pin1)
    s.pwr("GND", c3_p2[0], c3_p2[1] + g(1))
    s.wire(c3_p2[0], c3_p2[1], c3_p2[0], c3_p2[1] + g(1))

    # VDD_AFE_FILT label (filtered VDD for U1, fed through FB1 pin1)
    s.label("VDD_AFE_FILT", fb_p1[0] + g(1), fb_p1[1])
    s.wire(fb_p1[0], fb_p1[1], fb_p1[0] + g(1), fb_p1[1])

    # PWR_FLAGs for ERC
    s.pwr("PWR_FLAG", vin_x + g(2), vin_y)
    s.wire(vin_x, vin_y, vin_x + g(2), vin_y)
    s.pwr("PWR_FLAG", gnd_x + g(2), gnd_y)
    s.wire(gnd_x, gnd_y, gnd_x + g(2), gnd_y)
    # PWR_FLAG on /VDD_AFE_FILT — ferrite bead is passive, so ERC
    # sees VDD_AFE_FILT as "undriven". PWR_FLAG tells ERC power is present.
    s.pwr("PWR_FLAG", fb_p1[0], fb_p1[1] - g(1))
    s.wire(fb_p1[0], fb_p1[1], fb_p1[0], fb_p1[1] - g(1))
    s.junc(fb_p1[0], fb_p1[1])

    # ── RHD2164 ────────────────────────────────────────────────────────────
    RHD_X, RHD_Y = g(52), g(32)  # 132.08, 81.28

    s.place("afe:RHD2164", RHD_X, RHD_Y, "U", 1, "RHD2164",
            pin_nums=[p[1] for p in RHD_PINS])

    # Debug: print key pin positions
    for pn in ["VDD_1", "VDD_2", "VDD_3", "GND_1", "CS", "MISO_A", "in0", "ref_elec"]:
        px, py = rhd_pin(RHD_X, RHD_Y, pn)
        print(f"  RHD {pn}: ({px}, {py})")

    # VDD → /VDD_AFE_FILT (filtered supply through FB1)
    for pn in ["VDD_1", "VDD_2", "VDD_3"]:
        px, py = rhd_pin(RHD_X, RHD_Y, pn)
        s.label("VDD_AFE_FILT", px, py - g(1))
        s.wire(px, py, px, py - g(1))

    # GND
    for pn in ["GND_1", "GND_2", "GND_3"]:
        px, py = rhd_pin(RHD_X, RHD_Y, pn)
        s.pwr("GND", px, py + g(1))
        s.wire(px, py, px, py + g(1))

    # ADC_ref → C4 10nF → GND
    adc_x, adc_y = rhd_pin(RHD_X, RHD_Y, "ADC_ref")
    C4_X, C4_Y = adc_x, adc_y + g(3)
    s.place("Device:C", C4_X, C4_Y, "C", 4, "10nF", pin_nums=["1", "2"])
    c4_p1 = passive_pin1(C4_X, C4_Y)
    c4_p2 = passive_pin2(C4_X, C4_Y)
    s.wire(adc_x, adc_y, c4_p1[0], c4_p1[1])
    s.pwr("GND", c4_p2[0], c4_p2[1] + g(1))
    s.wire(c4_p2[0], c4_p2[1], c4_p2[0], c4_p2[1] + g(1))

    # VESD → GND
    vesd_x, vesd_y = rhd_pin(RHD_X, RHD_Y, "VESD")
    s.pwr("GND", vesd_x, vesd_y + g(1))
    s.wire(vesd_x, vesd_y, vesd_x, vesd_y + g(1))

    # LVDS_en → GND (CMOS mode)
    lvds_x, lvds_y = rhd_pin(RHD_X, RHD_Y, "LVDS_en")
    s.pwr("GND", lvds_x + g(2), lvds_y, rot=90)
    s.wire(lvds_x, lvds_y, lvds_x + g(2), lvds_y)

    # C5: 100nF bypass near RHD VDD
    vdd2_x, vdd2_y = rhd_pin(RHD_X, RHD_Y, "VDD_2")
    C5_X, C5_Y = vdd2_x + g(4), vdd2_y + g(2)
    s.place("Device:C", C5_X, C5_Y, "C", 5, "100nF", pin_nums=["1", "2"])
    c5_p1 = passive_pin1(C5_X, C5_Y)
    c5_p2 = passive_pin2(C5_X, C5_Y)
    s.pwr("+3V3", c5_p1[0], c5_p1[1] - g(1))
    s.wire(c5_p1[0], c5_p1[1], c5_p1[0], c5_p1[1] - g(1))
    s.pwr("GND", c5_p2[0], c5_p2[1] + g(1))
    s.wire(c5_p2[0], c5_p2[1], c5_p2[0], c5_p2[1] + g(1))

    # SPI → global labels
    spi_map = [
        ("CS",     "CS1",     "input"),
        ("SCLK",   "SCLK",    "input"),
        ("MOSI",   "MOSI",    "input"),
        ("MISO_A", "MISO1_A", "output"),
        ("MISO_B", "MISO1_B", "output"),
    ]
    for rhd_name, gl_name, shape in spi_map:
        px, py = rhd_pin(RHD_X, RHD_Y, rhd_name)
        s.glabel(gl_name, px + g(2), py, 0, shape)
        s.wire(px, py, px + g(2), py)

    # auxout, auxin1-3: no-connect
    for nc_pin in ["auxout", "auxin1", "auxin2", "auxin3"]:
        px, py = rhd_pin(RHD_X, RHD_Y, nc_pin)
        s.nc(px, py)

    # in32-35: no-connect (second chip channels, not used in partial build)
    for nc_pin in ["in32", "in33", "in34", "in35"]:
        px, py = rhd_pin(RHD_X, RHD_Y, nc_pin)
        s.nc(px, py)

    # ── ELECTRODE INPUTS (4ch) ─────────────────────────────────────────────
    ch_wire_points = []  # Store (x, y) on each CH horizontal wire for TPs
    for i, ch_name in enumerate(["in0", "in1", "in2", "in3"]):
        inp_x, inp_y = rhd_pin(RHD_X, RHD_Y, ch_name)

        # Bias R: 10M, vertical, spread horizontally to avoid overlapping wires
        R_X = inp_x - g(8) + i * g(2)  # stagger X positions
        R_Y = inp_y + g(4)
        s.place("Device:R", R_X, R_Y, "R", i + 1, "10M", pin_nums=["1", "2"])
        r_p1 = passive_pin1(R_X, R_Y)  # top
        r_p2 = passive_pin2(R_X, R_Y)  # bottom

        # Wire: R top → horizontal → RHD input
        s.wire(r_p1[0], r_p1[1], r_p1[0], inp_y)  # R top up to input level
        s.wire(r_p1[0], inp_y, inp_x, inp_y)       # horizontal to RHD

        # R bottom → GND
        s.pwr("GND", r_p2[0], r_p2[1] + g(1))
        s.wire(r_p2[0], r_p2[1], r_p2[0], r_p2[1] + g(1))

        # Label on the net (placed on the horizontal wire between R and RHD)
        label_x = r_p1[0] + g(1)
        s.label(f"CH{i}", label_x, inp_y)

        # Save wire junction point for TP placement
        ch_wire_points.append((r_p1[0], inp_y))

    # ref_elec → R5 0R → GND (horizontal)
    # After rot=90 fix: pin1(local top)=LEFT, pin2(local bot)=RIGHT
    ref_x, ref_y = rhd_pin(RHD_X, RHD_Y, "ref_elec")
    R5_X = ref_x - g(4)
    R5_Y = ref_y
    s.place("Device:R", R5_X, R5_Y, "R", 5, "0R", rot=90, pin_nums=["1", "2"])
    r5_p1 = passive_pin1(R5_X, R5_Y, rot=90)  # LEFT
    r5_p2 = passive_pin2(R5_X, R5_Y, rot=90)  # RIGHT (closer to ref_elec)
    print(f"  R5 pin1: {r5_p1}, pin2: {r5_p2}")
    # p2 (right) → ref_elec — this is the REF_ELEC net
    s.wire(r5_p2[0], r5_p2[1], ref_x, ref_y)
    # Label at the pin endpoint (not mid-wire) to avoid label_multiple_wires ERC
    s.label("REF_ELEC", r5_p2[0], r5_p2[1])
    # p1 (left) → GND
    s.pwr("GND", r5_p1[0] - g(1), r5_p1[1], rot=90)
    s.wire(r5_p1[0], r5_p1[1], r5_p1[0] - g(1), r5_p1[1])

    # ── B2B CONNECTOR ──────────────────────────────────────────────────────
    B2B_X, B2B_Y = g(80), g(30)  # 203.2, 76.2

    s.place("afe:B2B_30", B2B_X, B2B_Y, "J", 5, "QSH-030",
            pin_nums=[str(i+1) for i in range(30)])

    for i in range(B2B_NPINS):
        px, py = b2b_pin(B2B_X, B2B_Y, i)
        pname = B2B_PIN_NAMES[i]
        print(f"  B2B pin {i+1} ({pname}): ({px}, {py})")

        # Pins used in this partial build get global labels;
        # unused pins get no-connect markers.
        B2B_UNUSED = {"CS2", "MISO2_A", "MISO2_B", "TEST_SHORT_EN", "CAL_EN", "SPARE"}
        if pname == "GND":
            s.pwr("GND", px - g(2), py, rot=90)
            s.wire(px, py, px - g(2), py)
        elif pname == "VIN":
            s.pwr("VCC", px - g(2), py, rot=270)
            s.wire(px, py, px - g(2), py)
        elif pname in B2B_UNUSED:
            s.nc(px, py)
        else:
            shape = "output" if "MISO" in pname else "input"
            s.glabel(pname, px - g(2), py, 180, shape)
            s.wire(px, py, px - g(2), py)

    # ── TEST POINTS ────────────────────────────────────────────────────────
    # TP1: +3V3 (AVDD) — tap off the +3V3 rail near LDO output
    # Junction at (tp1_x, vout_y) is already emitted by wire_tee() above.
    tp1_x, tp1_y = vout_x + g(2), vout_y - g(2)
    s.place("Connector:TestPoint", tp1_x, tp1_y, "TP", 1, "TP_AVDD",
            pin_nums=["1"])
    s.wire(tp1_x, tp1_y, tp1_x, vout_y)

    # TP2: /VDD_AFE_FILT — tap off filtered rail near FB1 output (pin1 side)
    tp2_x, tp2_y = fb_p1[0] + g(1), fb_p1[1] - g(2)
    s.place("Connector:TestPoint", tp2_x, tp2_y, "TP", 2, "TP_DVDD",
            pin_nums=["1"])
    s.wire(tp2_x, tp2_y, tp2_x, fb_p1[1])
    s.junc(tp2_x, fb_p1[1])

    # TP3: GND — use its own GND power symbol
    tp3_x, tp3_y = gnd_x + g(4), gnd_y
    s.place("Connector:TestPoint", tp3_x, tp3_y, "TP", 3, "TP_GND",
            rot=180, pin_nums=["1"])
    s.pwr("GND", tp3_x, tp3_y + g(1))
    s.wire(tp3_x, tp3_y, tp3_x, tp3_y + g(1))

    # TP4-TP7: CH0-CH3 — tap off bias resistor → RHD input wires
    for ch_idx, (wx, wy) in enumerate(ch_wire_points):
        tp_x = wx - g(2)
        tp_y = wy
        s.place("Connector:TestPoint", tp_x, tp_y, "TP", 4 + ch_idx,
                f"TP_CH{ch_idx}", rot=270, pin_nums=["1"])
        s.wire(tp_x, tp_y, wx, wy)

    # TP8: REF_ELEC — tap off reference electrode (R5 pin2 = right = REF_ELEC side)
    tp8_x, tp8_y = r5_p2[0] + g(2), r5_p2[1]
    s.place("Connector:TestPoint", tp8_x, tp8_y, "TP", 8, "TP_REF",
            rot=90, pin_nums=["1"])
    s.wire(tp8_x, tp8_y, r5_p2[0], r5_p2[1])

    return s.build()


def main():
    outdir = "/Users/aharshi/BCIInterface/hardware/afe-headstage-real"
    os.makedirs(outdir, exist_ok=True)

    sch = build()
    sch_path = os.path.join(outdir, "afe-headstage-real.kicad_sch")
    with open(sch_path, "w") as f:
        f.write(sch)
    print(f"\nWrote {sch_path} ({len(sch)} bytes)")

    # Project file
    proj = {
        "meta": {"filename": "afe-headstage-real.kicad_pro", "version": 1},
        "net_settings": {"classes": [{"bus_width": 12, "clearance": 0.2, "name": "Default",
            "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6}],
            "meta": {"version": 3}},
        "schematic": {"annotate_start_num": 0, "meta": {"version": 1}},
        "sheets": [], "text_variables": {}
    }
    with open(os.path.join(outdir, "afe-headstage-real.kicad_pro"), "w") as f:
        json.dump(proj, f, indent=2)

    # Symbol library
    syms = "\n".join(LIB_SYMBOLS.values())
    with open(os.path.join(outdir, "afe_symbols.kicad_sym"), "w") as f:
        f.write(f"(kicad_symbol_lib\n  (version 20231120)\n  (generator \"custom\")\n  (generator_version \"1.0\")\n{syms}\n)\n")

    # sym-lib-table — only custom libs; power/Device/Connector come from KiCad global table
    with open(os.path.join(outdir, "sym-lib-table"), "w") as f:
        f.write("""(sym_lib_table
  (version 7)
  (lib (name "afe")(type "KiCad")(uri "${KIPRJMOD}/afe_symbols.kicad_sym")(options "")(descr "Custom AFE symbols"))
)
""")

    # fp-lib-table: only write if missing, CI=true, or GEN_WRITE_FP_LIB_TABLE=1.
    # This avoids stomping on interactive KiCad users' library config.
    fp_lib_path = os.path.join(outdir, "fp-lib-table")
    write_fp_lib = (
        not os.path.exists(fp_lib_path)
        or os.environ.get("CI") == "true"
        or os.environ.get("GEN_WRITE_FP_LIB_TABLE") == "1"
    )
    if write_fp_lib:
        with open(fp_lib_path, "w") as f:
            f.write('(fp_lib_table\n'
                    '  (version 7)\n'
                    '  (lib (name "afe_footprints")(type "KiCad")'
                    '(uri "${KIPRJMOD}/footprints.pretty")'
                    '(options "")(descr "AFE headstage footprints"))\n'
                    ')\n')

    print("Done.")


if __name__ == "__main__":
    main()
