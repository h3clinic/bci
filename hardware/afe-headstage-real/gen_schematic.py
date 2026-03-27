#!/usr/bin/env python3
"""
Generate a real KiCad 8 schematic for the Neural AFE Headstage.
Partial build: B2B connector + 1x RHD2164 + power + 4 electrode channels.

This outputs a valid .kicad_sch that KiCad can open, ERC-check, and export a netlist from.
"""

import uuid

def uid():
    return str(uuid.uuid4())

# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL LIBRARY DEFINITIONS (embedded in the schematic's lib_symbols section)
# ─────────────────────────────────────────────────────────────────────────────

def make_power_sym(name, glyph="arrow_up", pin_dir=90):
    """Generate a power symbol definition (GND, AVDD, etc.)"""
    if name == "GND":
        drawing = """
                        (symbol "GND_0_1"
                                (polyline
                                        (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))
                                        (stroke (width 0) (type default))
                                        (fill (type none))
                                )
                        )
                        (symbol "GND_1_1"
                                (pin power_in line (at 0 0 270) (length 0)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                        )"""
    else:
        # Arrow-up style for VDD/AVDD/etc
        drawing = f"""
                        (symbol "{name}_0_1"
                                (polyline
                                        (pts (xy -0.762 1.27) (xy 0 2.54))
                                        (stroke (width 0) (type default))
                                        (fill (type none))
                                )
                                (polyline
                                        (pts (xy 0 2.54) (xy 0.762 1.27))
                                        (stroke (width 0) (type default))
                                        (fill (type none))
                                )
                                (polyline
                                        (pts (xy 0 0) (xy 0 2.54))
                                        (stroke (width 0) (type default))
                                        (fill (type none))
                                )
                        )
                        (symbol "{name}_1_1"
                                (pin power_in line (at 0 0 90) (length 0)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                        )"""

    return f"""
                (symbol "power:{name}"
                        (power)
                        (pin_numbers (hide yes))
                        (pin_names (offset 0) (hide yes))
                        (exclude_from_sim no)
                        (in_bom yes)
                        (on_board yes)
                        (property "Reference" "#PWR"
                                (at 0 -3.81 0)
                                (effects (font (size 1.27 1.27)) (hide yes))
                        )
                        (property "Value" "{name}"
                                (at 0 3.556 0)
                                (effects (font (size 1.27 1.27)))
                        )
                        (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "Power symbol creates a global label with name \\"{name}\\""
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "ki_keywords" "global power"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
{drawing}
                        (embedded_fonts no)
                )"""


def make_connector_sym(name, npins, description, pin_names_list=None):
    """Generate a generic connector symbol with N pins."""
    half = npins * 2.54 / 2
    top_y = half - 1.27
    bot_y = -(half - 1.27)

    pins_text = ""
    rects_text = ""
    for i in range(npins):
        y = top_y - i * 2.54
        pname = pin_names_list[i] if pin_names_list else f"Pin_{i+1}"
        pnum = str(i + 1)
        pins_text += f"""
                        (pin passive line (at -5.08 {y:.2f} 0) (length 3.81)
                                (name "{pname}" (effects (font (size 1.27 1.27))))
                                (number "{pnum}" (effects (font (size 1.27 1.27))))
                        )"""
        rects_text += f"""
                        (rectangle (start -1.27 {y+0.127:.3f}) (end 0 {y-0.127:.3f})
                                (stroke (width 0.1524) (type default)) (fill (type none)))"""

    return f"""
                (symbol "afe:{name}"
                        (pin_names (offset 1.016) (hide yes))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "J"
                                (at 0 {top_y+2.54:.2f} 0) (effects (font (size 1.27 1.27))))
                        (property "Value" "{name}"
                                (at 0 {bot_y-2.54:.2f} 0) (effects (font (size 1.27 1.27))))
                        (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "{description}"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (symbol "{name}_1_1"
                                (rectangle (start -1.27 {top_y+1.27:.2f}) (end 1.27 {bot_y-1.27:.2f})
                                        (stroke (width 0.254) (type default)) (fill (type background)))
{rects_text}
{pins_text}
                        )
                        (embedded_fonts no)
                )"""


def make_rhd2164_sym():
    """Generate a simplified RHD2164 symbol for schematic capture.
    Pins grouped: Power, SPI, Analog inputs (only first 4 + last 4 for partial build), Control, Aux.
    Using BGA pin numbers from the datasheet.
    """
    # We'll create a box symbol with pins on the sides
    # Left side: analog inputs (in0-in3, ref_elec)
    # Right side: SPI (CS+, SCLK+, MOSI+, MISO+), control (LVDS_en, auxout)
    # Top: VDD
    # Bottom: GND, ADC_ref, VESD

    # Left side - electrode inputs (only 4 channels for partial build + ref)
    left_pins = [
        ("in0", "A1", "input", 0),
        ("in1", "A2", "input", -2.54),
        ("in2", "A3", "input", -5.08),
        ("in3", "A4", "input", -7.62),
        # Skip in4-in31 (not instantiated in partial build)
        ("in32", "H1", "input", -12.7),
        ("in33", "H2", "input", -15.24),
        ("in34", "H3", "input", -17.78),
        ("in35", "H4", "input", -20.32),
        ("ref_elec", "A17", "input", -25.4),
    ]

    right_pins = [
        ("CS+", "N8", "input", 0),
        ("SCLK+", "N10", "input", -2.54),
        ("MOSI+", "N12", "input", -5.08),
        ("MISO+", "N14", "output", -7.62),
        ("LVDS_en", "M16", "input", -12.7),
        ("auxout", "N16", "output", -15.24),
        ("auxin1", "N3", "input", -20.32),
        ("auxin2", "N4", "input", -22.86),
        ("auxin3", "N5", "input", -25.4),
    ]

    top_pins = [
        ("VDD_1", "M15", "power_in", -5.08),
        ("VDD_2", "N2", "power_in", 0),
        ("VDD_3", "N15", "power_in", 5.08),
    ]

    bottom_pins = [
        ("GND_1", "M17", "power_in", -7.62),
        ("GND_2", "N1", "power_in", -2.54),
        ("GND_3", "N6", "power_in", 2.54),
        ("ADC_ref", "N17", "passive", 7.62),
        ("VESD", "L1", "passive", 12.7),
    ]

    pin_defs = ""
    # Left pins (facing right, at x=-20.32)
    for pname, pnum, ptype, y_off in left_pins:
        pin_defs += f"""
                        (pin {ptype} line (at -20.32 {y_off:.2f} 0) (length 5.08)
                                (name "{pname}" (effects (font (size 1.0 1.0))))
                                (number "{pnum}" (effects (font (size 0.8 0.8))))
                        )"""

    # Right pins (facing left, at x=20.32)
    for pname, pnum, ptype, y_off in right_pins:
        pin_defs += f"""
                        (pin {ptype} line (at 20.32 {y_off:.2f} 180) (length 5.08)
                                (name "{pname}" (effects (font (size 1.0 1.0))))
                                (number "{pnum}" (effects (font (size 0.8 0.8))))
                        )"""

    # Top pins (facing down, at y=top)
    for pname, pnum, ptype, x_off in top_pins:
        pin_defs += f"""
                        (pin {ptype} line (at {x_off:.2f} 10.16 270) (length 5.08)
                                (name "{pname}" (effects (font (size 1.0 1.0))))
                                (number "{pnum}" (effects (font (size 0.8 0.8))))
                        )"""

    # Bottom pins (facing up, at y=bottom)
    for pname, pnum, ptype, x_off in bottom_pins:
        pin_defs += f"""
                        (pin {ptype} line (at {x_off:.2f} -30.48 90) (length 5.08)
                                (name "{pname}" (effects (font (size 1.0 1.0))))
                                (number "{pnum}" (effects (font (size 0.8 0.8))))
                        )"""

    return f"""
                (symbol "afe:RHD2164"
                        (pin_names (offset 1.016))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "U"
                                (at 0 12.7 0) (effects (font (size 1.27 1.27))))
                        (property "Value" "RHD2164"
                                (at 0 -33.02 0) (effects (font (size 1.27 1.27))))
                        (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "https://intantech.com/files/Intan_RHD2164_datasheet.pdf"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "64-channel digital electrophysiology interface chip, DDR SPI, BGA"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (symbol "RHD2164_1_1"
                                (rectangle (start -15.24 5.08) (end 15.24 -25.4)
                                        (stroke (width 0.254) (type default)) (fill (type background)))
{pin_defs}
                        )
                        (embedded_fonts no)
                )"""


def make_resistor_sym():
    return """
                (symbol "Device:R"
                        (pin_names (offset 0))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "R"
                                (at 2.032 0 90) (effects (font (size 1.27 1.27))))
                        (property "Value" "R"
                                (at -2.032 0 90) (effects (font (size 1.27 1.27))))
                        (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                                (pin passive line (at 0 -3.81 90) (length 1.27)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "2" (effects (font (size 1.27 1.27))))
                                )
                        )
                        (embedded_fonts no)
                )"""


def make_cap_sym():
    return """
                (symbol "Device:C"
                        (pin_names (offset 0.254))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "C"
                                (at 0.635 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
                        (property "Value" "C"
                                (at 0.635 -2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
                        (property "Footprint" "" (at 0.9652 -3.81 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "Unpolarized capacitor"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "ki_keywords" "cap capacitor"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "ki_fp_filters" "C_*"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (symbol "C_0_1"
                                (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762))
                                        (stroke (width 0.508) (type default)) (fill (type none)))
                                (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762))
                                        (stroke (width 0.508) (type default)) (fill (type none)))
                        )
                        (symbol "C_1_1"
                                (pin passive line (at 0 3.81 270) (length 2.794)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                                (pin passive line (at 0 -3.81 90) (length 2.794)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "2" (effects (font (size 1.27 1.27))))
                                )
                        )
                        (embedded_fonts no)
                )"""


def make_ferrite_sym():
    return """
                (symbol "Device:FerriteBead"
                        (pin_names (offset 1.016))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "FB"
                                (at 1.905 0.635 0) (effects (font (size 1.27 1.27))))
                        (property "Value" "FerriteBead"
                                (at 1.905 -1.905 0) (effects (font (size 1.27 1.27))))
                        (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "Ferrite bead"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (symbol "FerriteBead_0_1"
                                (rectangle (start -1.016 -2.54) (end 1.016 2.54)
                                        (stroke (width 0.254) (type default)) (fill (type none)))
                        )
                        (symbol "FerriteBead_1_1"
                                (pin passive line (at 0 3.81 270) (length 1.27)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                                (pin passive line (at 0 -3.81 90) (length 1.27)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "2" (effects (font (size 1.27 1.27))))
                                )
                        )
                        (embedded_fonts no)
                )"""


def make_ldo_sym():
    """ADP151 / generic 3-pin LDO symbol: VIN, VOUT, GND (+ optional EN)"""
    return """
                (symbol "afe:ADP151"
                        (pin_names (offset 1.016))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "U"
                                (at 0 7.62 0) (effects (font (size 1.27 1.27))))
                        (property "Value" "ADP151-3.3"
                                (at 0 -7.62 0) (effects (font (size 1.27 1.27))))
                        (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "200mA ultralow noise CMOS LDO, 3.3V output"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (symbol "ADP151_1_1"
                                (rectangle (start -7.62 5.08) (end 7.62 -5.08)
                                        (stroke (width 0.254) (type default)) (fill (type background)))
                                (pin power_in line (at -12.7 2.54 0) (length 5.08)
                                        (name "VIN" (effects (font (size 1.27 1.27))))
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                                (pin power_out line (at 12.7 2.54 180) (length 5.08)
                                        (name "VOUT" (effects (font (size 1.27 1.27))))
                                        (number "5" (effects (font (size 1.27 1.27))))
                                )
                                (pin power_in line (at 0 -10.16 90) (length 5.08)
                                        (name "GND" (effects (font (size 1.27 1.27))))
                                        (number "2" (effects (font (size 1.27 1.27))))
                                )
                                (pin input line (at -12.7 -2.54 0) (length 5.08)
                                        (name "EN" (effects (font (size 1.27 1.27))))
                                        (number "3" (effects (font (size 1.27 1.27))))
                                )
                        )
                        (embedded_fonts no)
                )"""


def make_pwr_flag_sym():
    return """
                (symbol "power:PWR_FLAG"
                        (power)
                        (pin_numbers (hide yes))
                        (pin_names (offset 0) (hide yes))
                        (exclude_from_sim no) (in_bom yes) (on_board yes)
                        (property "Reference" "#FLG"
                                (at 0 1.905 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Value" "PWR_FLAG"
                                (at 0 3.81 0) (effects (font (size 1.27 1.27))))
                        (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "Description" "Special symbol for telling ERC where power comes from"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (property "ki_keywords" "flag power"
                                (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
                        (symbol "PWR_FLAG_0_1"
                                (pin power_out line (at 0 0 90) (length 0)
                                        (name "~" (effects (font (size 1.27 1.27))))
                                        (number "1" (effects (font (size 1.27 1.27))))
                                )
                        )
                        (embedded_fonts no)
                )"""


# ─────────────────────────────────────────────────────────────────────────────
# COMPONENT INSTANCE GENERATION
# ─────────────────────────────────────────────────────────────────────────────

class SchematicBuilder:
    def __init__(self):
        self.root_uuid = uid()
        self.project_name = "afe-headstage-real"
        self.symbols = []       # placed component instances
        self.wires = []         # wire segments
        self.labels = []        # net labels
        self.global_labels = [] # global net labels
        self.no_connects = []   # no-connect markers
        self.pwr_counter = 0
        self.flg_counter = 0

    def place_symbol(self, lib_id, x, y, ref_prefix, ref_num, value, properties=None, rotation=0, mirror=None, pin_uuids=None):
        """Place a component instance"""
        self.pwr_counter += 1
        comp_uuid = uid()

        if ref_prefix == "#PWR":
            ref_des = f"#PWR{self.pwr_counter:02d}"
        elif ref_prefix == "#FLG":
            self.flg_counter += 1
            ref_des = f"#FLG{self.flg_counter:02d}"
        else:
            ref_des = f"{ref_prefix}{ref_num}"

        mirror_str = ""
        if mirror:
            mirror_str = f"\n                (mirror {mirror})"

        # Generate pin UUID entries
        pins_str = ""
        if pin_uuids:
            for pnum in pin_uuids:
                pins_str += f"""
                (pin "{pnum}" (uuid "{uid()}"))"""

        props_str = ""
        if properties:
            for k, v in properties.items():
                props_str += f"""
                (property "{k}" "{v}"
                        (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))"""

        block = f"""
        (symbol
                (lib_id "{lib_id}")
                (at {x:.2f} {y:.2f} {rotation})
                (unit 1)
                (exclude_from_sim no)
                (in_bom yes)
                (on_board yes)
                (dnp no){mirror_str}
                (uuid "{comp_uuid}")
                (property "Reference" "{ref_des}"
                        (at {x+2:.2f} {y-2:.2f} 0)
                        (effects (font (size 1.27 1.27)){"(hide yes)" if ref_prefix in ("#PWR", "#FLG") else ""})
                )
                (property "Value" "{value}"
                        (at {x+2:.2f} {y+2:.2f} 0)
                        (effects (font (size 1.27 1.27)))
                )
                (property "Footprint" ""
                        (at {x:.2f} {y:.2f} 0)
                        (effects (font (size 1.27 1.27)) (hide yes))
                )
                (property "Datasheet" ""
                        (at {x:.2f} {y:.2f} 0)
                        (effects (font (size 1.27 1.27)) (hide yes))
                ){props_str}{pins_str}
                (instances
                        (project "{self.project_name}"
                                (path "/{self.root_uuid}"
                                        (reference "{ref_des}")
                                        (unit 1)
                                )
                        )
                )
        )"""
        self.symbols.append(block)
        return comp_uuid

    def wire(self, x1, y1, x2, y2):
        self.wires.append(f"""
        (wire
                (pts (xy {x1:.2f} {y1:.2f}) (xy {x2:.2f} {y2:.2f}))
                (stroke (width 0) (type default))
                (uuid "{uid()}")
        )""")

    def label(self, name, x, y, rotation=0):
        self.labels.append(f"""
        (label "{name}"
                (at {x:.2f} {y:.2f} {rotation})
                (effects (font (size 1.27 1.27)) (justify left bottom))
                (uuid "{uid()}")
        )""")

    def global_label(self, name, x, y, rotation=0, shape="input"):
        self.global_labels.append(f"""
        (global_label "{name}"
                (shape {shape})
                (at {x:.2f} {y:.2f} {rotation})
                (effects (font (size 1.27 1.27)) (justify left))
                (uuid "{uid()}")
                (property "Intersheetrefs" "${{INTERSHEET_REFS}}"
                        (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
        )""")

    def no_connect(self, x, y):
        self.no_connects.append(f"""
        (no_connect
                (at {x:.2f} {y:.2f})
                (uuid "{uid()}")
        )""")


def build_schematic():
    sb = SchematicBuilder()

    # ═══════════════════════════════════════════════════════════════════════
    # LAYOUT PLAN (all coordinates in mm, origin top-left)
    #
    # Power section:    X=30,  Y=30
    # RHD2164:          X=130, Y=80
    # B2B connector:    X=230, Y=80
    # Electrode conn:   X=30,  Y=100
    # ═══════════════════════════════════════════════════════════════════════

    # ─── POWER SECTION ─────────────────────────────────────────────────────
    # VIN → ADP151 (LDO) → AVDD
    # AVDD → FB2 (ferrite) → DVDD_AFE

    # ADP151 LDO at (50, 35)
    ldo_x, ldo_y = 50, 35
    sb.place_symbol("afe:ADP151", ldo_x, ldo_y, "U", 2, "ADP151-3.3",
                    pin_uuids=["1", "2", "3", "5"])

    # Input cap C_IN: 1uF at VIN input
    cin_x, cin_y = 30, 40
    sb.place_symbol("Device:C", cin_x, cin_y, "C", 1, "1uF",
                    pin_uuids=["1", "2"])

    # Output cap C_OUT: 1uF at AVDD output
    cout_x, cout_y = 70, 40
    sb.place_symbol("Device:C", cout_x, cout_y, "C", 2, "1uF",
                    pin_uuids=["1", "2"])

    # Ferrite bead FB2: AVDD → DVDD_AFE
    fb_x, fb_y = 90, 35
    sb.place_symbol("Device:FerriteBead", fb_x, fb_y, "FB", 1, "600R@100MHz",
                    rotation=90, pin_uuids=["1", "2"])

    # DVDD decoupling cap
    cdvdd_x, cdvdd_y = 105, 40
    sb.place_symbol("Device:C", cdvdd_x, cdvdd_y, "C", 3, "100nF",
                    pin_uuids=["1", "2"])

    # Power symbols
    # VIN power at LDO input
    sb.place_symbol("power:VIN", 30, 30, "#PWR", 0, "VIN", pin_uuids=["1"])
    # GND at LDO
    sb.place_symbol("power:GND", ldo_x, ldo_y + 15, "#PWR", 0, "GND",
                    rotation=0, pin_uuids=["1"])
    # GND at C_IN bottom
    sb.place_symbol("power:GND", cin_x, cin_y + 5, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    # GND at C_OUT bottom
    sb.place_symbol("power:GND", cout_x, cout_y + 5, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    # GND at C_DVDD bottom
    sb.place_symbol("power:GND", cdvdd_x, cdvdd_y + 5, "#PWR", 0, "GND",
                    pin_uuids=["1"])

    # PWR_FLAGs for ERC
    sb.place_symbol("power:PWR_FLAG", 30, 28, "#FLG", 0, "PWR_FLAG", pin_uuids=["1"])
    sb.place_symbol("power:PWR_FLAG", ldo_x, ldo_y + 17, "#FLG", 0, "PWR_FLAG", pin_uuids=["1"])

    # Wires for power section
    # VIN → C_IN top → LDO VIN
    sb.wire(30, 30, 30, 36.19)        # VIN sym to C_IN top
    sb.wire(30, 32.54, 37.3, 32.54)   # VIN to LDO VIN
    sb.wire(37.3, 32.54, 37.3, 37.46) # down to LDO input pin
    # EN tied to VIN
    sb.wire(37.3, 37.54, 30, 37.54)   # EN to VIN rail
    sb.wire(30, 37.54, 30, 32.54)
    # LDO VOUT → C_OUT → AVDD label
    sb.wire(62.7, 32.54, 70, 32.54)   # LDO VOUT to C_OUT
    sb.wire(70, 32.54, 70, 36.19)     # to C_OUT top
    # AVDD → ferrite
    sb.wire(70, 32.54, 86.19, 32.54)  # AVDD to ferrite pin1 (rotated)
    sb.wire(93.81, 32.54, 105, 32.54) # ferrite pin2 to C_DVDD
    sb.wire(105, 32.54, 105, 36.19)   # to C_DVDD top
    # LDO GND
    sb.wire(ldo_x, ldo_y + 10.16, ldo_x, ldo_y + 15) # LDO GND to GND sym
    # C_IN GND, C_OUT GND, C_DVDD GND
    sb.wire(cin_x, cin_y + 3.81, cin_x, cin_y + 5)
    sb.wire(cout_x, cout_y + 3.81, cout_x, cout_y + 5)
    sb.wire(cdvdd_x, cdvdd_y + 3.81, cdvdd_x, cdvdd_y + 5)

    # Net labels
    sb.label("VIN", 32, 32.54)
    sb.label("AVDD", 72, 32.54)
    sb.label("DVDD_AFE", 96, 32.54)

    # ─── RHD2164 ────────────────────────────────────────────────────────────
    rhd_x, rhd_y = 140, 90
    sb.place_symbol("afe:RHD2164", rhd_x, rhd_y, "U", 1, "RHD2164",
                    pin_uuids=["A1", "A2", "A3", "A4", "H1", "H2", "H3", "H4",
                               "A17", "N8", "N10", "N12", "N14", "M16", "N16",
                               "N3", "N4", "N5", "M15", "N2", "N15", "M17",
                               "N1", "N6", "N17", "L1"])

    # RHD2164 power connections
    # VDD pins to AVDD
    sb.place_symbol("power:AVDD", rhd_x - 5.08, rhd_y - 15, "#PWR", 0, "AVDD",
                    pin_uuids=["1"])
    sb.place_symbol("power:AVDD", rhd_x, rhd_y - 15, "#PWR", 0, "AVDD",
                    pin_uuids=["1"])
    sb.place_symbol("power:AVDD", rhd_x + 5.08, rhd_y - 15, "#PWR", 0, "AVDD",
                    pin_uuids=["1"])

    # GND pins
    sb.place_symbol("power:GND", rhd_x - 7.62, rhd_y + 30, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    sb.place_symbol("power:GND", rhd_x - 2.54, rhd_y + 30, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    sb.place_symbol("power:GND", rhd_x + 2.54, rhd_y + 30, "#PWR", 0, "GND",
                    pin_uuids=["1"])

    # Wire VDD pins up
    for dx in [-5.08, 0, 5.08]:
        sb.wire(rhd_x + dx, rhd_y - 10.16, rhd_x + dx, rhd_y - 15)

    # Wire GND pins down
    for dx in [-7.62, -2.54, 2.54]:
        sb.wire(rhd_x + dx, rhd_y + 25.4, rhd_x + dx, rhd_y + 30)

    # ADC_ref cap (10nF to GND)
    adc_ref_x = rhd_x + 7.62
    sb.place_symbol("Device:C", adc_ref_x, rhd_y + 30, "C", 4, "10nF",
                    pin_uuids=["1", "2"])
    sb.place_symbol("power:GND", adc_ref_x, rhd_y + 35, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    sb.wire(adc_ref_x, rhd_y + 25.4, adc_ref_x, rhd_y + 26.19) # ADC_ref to cap
    sb.wire(adc_ref_x, rhd_y + 33.81, adc_ref_x, rhd_y + 35)   # cap to GND

    # VESD to GND
    vesd_x = rhd_x + 12.7
    sb.place_symbol("power:GND", vesd_x, rhd_y + 30, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    sb.wire(vesd_x, rhd_y + 25.4, vesd_x, rhd_y + 30)

    # LVDS_en to GND (standard CMOS mode)
    lvds_en_x = rhd_x + 20.32
    lvds_en_y = rhd_y - 12.7
    sb.place_symbol("power:GND", lvds_en_x + 5, lvds_en_y, "#PWR", 0, "GND",
                    rotation=90, pin_uuids=["1"])
    sb.wire(lvds_en_x, lvds_en_y, lvds_en_x + 5, lvds_en_y)

    # Bypass cap (100nF) near RHD2164
    cbp_x, cbp_y = rhd_x - 20, rhd_y - 10
    sb.place_symbol("Device:C", cbp_x, cbp_y, "C", 5, "100nF",
                    pin_uuids=["1", "2"])
    sb.place_symbol("power:AVDD", cbp_x, cbp_y - 6, "#PWR", 0, "AVDD",
                    pin_uuids=["1"])
    sb.place_symbol("power:GND", cbp_x, cbp_y + 6, "#PWR", 0, "GND",
                    pin_uuids=["1"])
    sb.wire(cbp_x, cbp_y - 3.81, cbp_x, cbp_y - 6)
    sb.wire(cbp_x, cbp_y + 3.81, cbp_x, cbp_y + 6)

    # ─── SPI NETS (RHD2164 → B2B connector) ────────────────────────────────
    # SPI pins are on the right side of RHD2164 at x = rhd_x + 20.32
    spi_x = rhd_x + 20.32
    spi_signals = [
        ("CS1",     rhd_y + 0),
        ("SCLK",    rhd_y - 2.54),
        ("MOSI",    rhd_y - 5.08),
        ("MISO1_A", rhd_y - 7.62),
    ]
    for name, y in spi_signals:
        shape = "output" if "MISO" in name else "input"
        sb.global_label(name, spi_x + 2, y, 0, shape)
        sb.wire(spi_x, y, spi_x + 2, y)

    # auxout, auxin1-3: no-connect for now
    for y_off in [-15.24, -20.32, -22.86, -25.4]:
        sb.no_connect(spi_x, rhd_y + y_off)

    # ─── ELECTRODE CONNECTOR (4 channels of 32) ────────────────────────────
    elec_x, elec_y = 50, 100
    # Simple 4-pin connector for channels 0-3
    sb.place_symbol("afe:ELEC_4CH", elec_x, elec_y, "J", 1, "J1_ELEC",
                    pin_uuids=["1", "2", "3", "4"])

    # Bias resistors (10M) from each channel to GND
    for i in range(4):
        r_x = 70 + i * 15
        r_y = 115
        sb.place_symbol("Device:R", r_x, r_y, "R", i + 1, "10M",
                        pin_uuids=["1", "2"])
        sb.place_symbol("power:GND", r_x, r_y + 7, "#PWR", 0, "GND",
                        pin_uuids=["1"])
        sb.wire(r_x, r_y + 3.81, r_x, r_y + 7) # R to GND

    # Wire electrode pins to RHD2164 inputs and bias resistors
    # RHD2164 left-side inputs at x = rhd_x - 20.32
    inp_x = rhd_x - 20.32
    for i in range(4):
        ch_y = rhd_y + 0 - i * 2.54  # in0, in1, in2, in3
        r_x = 70 + i * 15
        # Wire from electrode side to RHD input
        sb.wire(r_x, 111.19, r_x, ch_y)   # bias R top to horizontal
        sb.wire(r_x, ch_y, inp_x, ch_y)   # horizontal to RHD2164 input
        sb.label(f"CH{i}", r_x + 2, ch_y)

    # ref_elec to GND via 0R (optional)
    ref_y = rhd_y - 25.4
    sb.place_symbol("Device:R", inp_x - 10, ref_y, "R", 5, "0R",
                    rotation=90, pin_uuids=["1", "2"])
    sb.place_symbol("power:GND", inp_x - 20, ref_y, "#PWR", 0, "GND",
                    rotation=90, pin_uuids=["1"])
    sb.wire(inp_x, ref_y, inp_x - 6.19, ref_y)
    sb.wire(inp_x - 13.81, ref_y, inp_x - 20, ref_y)

    # ─── B2B CONNECTOR ──────────────────────────────────────────────────────
    b2b_x, b2b_y = 230, 50
    b2b_pins = [
        "GND", "GND",                # 1,2
        "SCLK", "GND",               # 3,4
        "MOSI", "GND",               # 5,6
        "CS1", "GND",                # 7,8  (GND between CS per Fix 1)
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
    sb.place_symbol("afe:B2B_30", b2b_x, b2b_y, "J", 5, "QSH-030",
                    pin_uuids=[str(i+1) for i in range(30)])

    # Wire B2B pins to global labels
    for i, pname in enumerate(b2b_pins):
        pin_y = b2b_y + (14 * 2.54) / 2 - i * 2.54  # pins go top to bottom
        pin_conn_x = b2b_x - 5.08  # pin connection point
        label_x = pin_conn_x - 2

        if pname == "GND":
            sb.place_symbol("power:GND", label_x, pin_y, "#PWR", 0, "GND",
                            rotation=90, pin_uuids=["1"])
            sb.wire(pin_conn_x, pin_y, label_x, pin_y)
        elif pname == "VIN":
            sb.place_symbol("power:VIN", label_x, pin_y, "#PWR", 0, "VIN",
                            rotation=270, pin_uuids=["1"])
            sb.wire(pin_conn_x, pin_y, label_x, pin_y)
        elif pname in ("SPARE",):
            sb.no_connect(pin_conn_x, pin_y)
        else:
            shape = "output" if "MISO" in pname else "input"
            sb.global_label(pname, label_x, pin_y, 180, shape)
            sb.wire(pin_conn_x, pin_y, label_x, pin_y)

    # ═══════════════════════════════════════════════════════════════════════
    # ASSEMBLE THE SCHEMATIC FILE
    # ═══════════════════════════════════════════════════════════════════════

    lib_symbols = "\n".join([
        make_power_sym("GND"),
        make_power_sym("VIN"),
        make_power_sym("AVDD"),
        make_power_sym("DVDD_AFE"),
        make_pwr_flag_sym(),
        make_rhd2164_sym(),
        make_ldo_sym(),
        make_connector_sym("B2B_30", 30, "Samtec QSH-030-01-L-D-A, 30-pin B2B connector",
                          b2b_pins),
        make_connector_sym("ELEC_4CH", 4, "Electrode connector, 4 channels (partial build)",
                          ["CH0", "CH1", "CH2", "CH3"]),
        make_resistor_sym(),
        make_cap_sym(),
        make_ferrite_sym(),
    ])

    symbols_placed = "\n".join(sb.symbols)
    wires_placed = "\n".join(sb.wires)
    labels_placed = "\n".join(sb.labels)
    global_labels_placed = "\n".join(sb.global_labels)
    no_connects_placed = "\n".join(sb.no_connects)

    schematic = f"""(kicad_sch
        (version 20231120)
        (generator "eeschema")
        (generator_version "8.0")
        (uuid "{sb.root_uuid}")
        (paper "A3")
        (title_block
                (title "Neural AFE Headstage - Real Schematic")
                (date "2026-02-17")
                (rev "2.0")
                (company "BCIInterface")
                (comment 1 "128-Channel Neural AFE (partial: 4ch + 1x RHD2164)")
                (comment 2 "DDR SPI passthrough to carrier FPGA | 3.3V LVCMOS")
                (comment 3 "B2B: Samtec QSH-030 | fs=30kS/s | 16-bit")
        )

        (lib_symbols
{lib_symbols}
        )

{wires_placed}
{no_connects_placed}
{labels_placed}
{global_labels_placed}
{symbols_placed}

        (sheet_instances
                (path "/"
                        (page "1")
                )
        )
        (embedded_fonts no)
)
"""
    return schematic


def main():
    import os
    outdir = "/Users/aharshi/BCIInterface/hardware/afe-headstage-real"
    os.makedirs(outdir, exist_ok=True)

    # Generate schematic
    sch = build_schematic()
    sch_path = os.path.join(outdir, "afe-headstage-real.kicad_sch")
    with open(sch_path, "w") as f:
        f.write(sch)
    print(f"Wrote: {sch_path}")

    # Generate project file
    proj = """{
  "meta": {
    "filename": "afe-headstage-real.kicad_pro",
    "version": 1
  },
  "net_settings": {
    "classes": [
      {
        "bus_width": 12,
        "clearance": 0.2,
        "diff_pair_gap": 0.25,
        "diff_pair_via_gap": 0.25,
        "diff_pair_width": 0.2,
        "line_style": 0,
        "microvia_diameter": 0.3,
        "microvia_drill": 0.1,
        "name": "Default",
        "pcb_color": "rgba(0, 0, 0, 0.000)",
        "schematic_color": "rgba(0, 0, 0, 0.000)",
        "track_width": 0.2,
        "via_diameter": 0.6,
        "via_drill": 0.3,
        "wire_width": 6
      }
    ],
    "meta": {
      "version": 3
    },
    "net_colors": null,
    "netclass_assignments": null,
    "netclass_patterns": []
  },
  "pcbnew": {
    "last_paths": {
      "gencad": "",
      "idf": "",
      "netlist": "",
      "plot": "",
      "pos_files": "",
      "specctra_dsn": "",
      "step": "",
      "svg": "",
      "vrml": ""
    },
    "page_layout_descr_file": ""
  },
  "schematic": {
    "annotate_start_num": 0,
    "bom_fmt_presets": [],
    "bom_fmt_settings": {
      "field_delimiter": ",",
      "keep_line_breaks": false,
      "keep_tabs": false,
      "name": "",
      "ref_delimiter": ",",
      "ref_range_delimiter": "",
      "string_delimiter": "\\""
    },
    "bom_presets": [],
    "connection_grid_size": 50.0,
    "drawing": {
      "dashed_lines_dash_length_ratio": 12.0,
      "dashed_lines_gap_length_ratio": 3.0,
      "default_line_thickness": 6.0,
      "default_text_size": 50.0,
      "field_names": [],
      "intersheets_ref_own_page": false,
      "intersheets_ref_prefix": "",
      "intersheets_ref_short": false,
      "intersheets_ref_show": false,
      "intersheets_ref_suffix": "",
      "junction_size_choice": 3,
      "label_size_ratio": 0.375,
      "operating_point_overlay_i_precision": 3,
      "operating_point_overlay_i_range": "~A",
      "operating_point_overlay_v_precision": 3,
      "operating_point_overlay_v_range": "~V",
      "overbar_offset_ratio": 1.23,
      "pin_symbol_size": 25.0,
      "text_offset_ratio": 0.15
    },
    "legacy_lib_dir": "",
    "legacy_lib_list": [],
    "meta": {
      "version": 1
    },
    "net_format_name": "",
    "page_layout_descr_file": "",
    "plot_directory": "",
    "spice_current_sheet_as_root": false,
    "spice_external_command": "spice \\\"%I\\\"",
    "spice_model_current_sheet_as_root": true,
    "spice_save_all_currents": false,
    "spice_save_all_dissipations": false,
    "spice_save_all_voltages": false,
    "subpart_first_id": 65,
    "subpart_id_separator": 0
  },
  "sheets": [],
  "text_variables": {}
}"""
    proj_path = os.path.join(outdir, "afe-headstage-real.kicad_pro")
    with open(proj_path, "w") as f:
        f.write(proj)
    print(f"Wrote: {proj_path}")

    # Generate sym-lib-table (references built-in KiCad libs + our local lib)
    sym_lib = """(sym_lib_table
  (version 7)
  (lib (name "power")(type "KiCad")(uri "${KICAD8_SYMBOL_DIR}/power.kicad_sym")(options "")(descr "Power symbols"))
  (lib (name "Device")(type "KiCad")(uri "${KICAD8_SYMBOL_DIR}/Device.kicad_sym")(options "")(descr "Generic devices"))
  (lib (name "Connector_Generic")(type "KiCad")(uri "${KICAD8_SYMBOL_DIR}/Connector_Generic.kicad_sym")(options "")(descr "Generic connectors"))
  (lib (name "afe")(type "KiCad")(uri "${KIPRJMOD}/afe_symbols.kicad_sym")(options "")(descr "Custom AFE symbols"))
)"""
    sym_path = os.path.join(outdir, "sym-lib-table")
    with open(sym_path, "w") as f:
        f.write(sym_lib)
    print(f"Wrote: {sym_path}")

    # Generate fp-lib-table (mostly empty for now)
    fp_lib = """(fp_lib_table
  (version 7)
  (lib (name "Resistor_SMD")(type "KiCad")(uri "${KICAD8_3DMODEL_DIR}/../footprints/Resistor_SMD.pretty")(options "")(descr ""))
  (lib (name "Capacitor_SMD")(type "KiCad")(uri "${KICAD8_3DMODEL_DIR}/../footprints/Capacitor_SMD.pretty")(options "")(descr ""))
)"""
    fp_path = os.path.join(outdir, "fp-lib-table")
    with open(fp_path, "w") as f:
        f.write(fp_lib)
    print(f"Wrote: {fp_path}")

    # Generate a minimal custom symbol library file (for afe:RHD2164, afe:B2B_30, etc)
    # The symbols are already embedded in lib_symbols in the schematic, but KiCad
    # also needs a standalone .kicad_sym file for the sym-lib-table to reference
    afe_lib_content = f"""(kicad_symbol_lib
        (version 20231120)
        (generator "custom")
        (generator_version "1.0")
{make_rhd2164_sym().replace("                (symbol", "        (symbol", 1)}
{make_ldo_sym().replace("                (symbol", "        (symbol", 1)}
{make_connector_sym("B2B_30", 30, "Samtec QSH-030-01-L-D-A", [
        "GND", "GND", "SCLK", "GND", "MOSI", "GND",
        "CS1", "GND", "CS2", "GND",
        "MISO1_A", "MISO1_B", "GND", "GND",
        "MISO2_A", "MISO2_B", "GND", "GND",
        "VIN", "VIN", "GND", "GND", "VIN", "VIN", "GND", "GND",
        "TEST_SHORT_EN", "CAL_EN", "SPARE", "GND"
    ]).replace("                (symbol", "        (symbol", 1)}
{make_connector_sym("ELEC_4CH", 4, "Electrode connector, 4 channels",
        ["CH0", "CH1", "CH2", "CH3"]).replace("                (symbol", "        (symbol", 1)}
)
"""
    afe_lib_path = os.path.join(outdir, "afe_symbols.kicad_sym")
    with open(afe_lib_path, "w") as f:
        f.write(afe_lib_content)
    print(f"Wrote: {afe_lib_path}")

    print("\nDone. Try: kicad-cli sch erc --severity-all afe-headstage-real.kicad_sch")


if __name__ == "__main__":
    main()
