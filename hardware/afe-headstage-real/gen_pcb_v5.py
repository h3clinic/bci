#!/usr/bin/env python3
"""
gen_pcb.py  –  Generate KiCad 9 PCB file from XML netlist
=========================================================
Reads afe-headstage-real.xml (exported from the ERC-clean schematic)
and produces afe-headstage-real.kicad_pcb with:
  - All components placed with real footprints & pads
  - Net declarations from the netlist
  - Pads assigned to nets  →  ratsnest automatically visible
  - Board outline (Edge.Cuts)
  - 2-layer stackup (F.Cu + B.Cu), 1.6mm FR4
  - Test points (TP1-TP8) for probing

No routed traces — just placement + ratsnest.
"""

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
NETLIST = HERE / "afe-headstage-real.xml"
PCB_OUT = HERE / "afe-headstage-real.kicad_pcb"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def uid():
    return str(uuid.uuid4())


def parse_netlist(path):
    """Return (components, nets) from KiCad XML netlist."""
    tree = ET.parse(path)
    root = tree.getroot()

    # Components: [{ref, value, lib, part, tstamp, footprint_name}]
    components = []
    for comp in root.findall(".//components/comp"):
        ref = comp.get("ref")
        value = comp.findtext("value", "")
        libsrc = comp.find("libsource")
        lib = libsrc.get("lib", "") if libsrc is not None else ""
        part = libsrc.get("part", "") if libsrc is not None else ""
        tstamp = comp.findtext("tstamps", uid())
        components.append({
            "ref": ref,
            "value": value,
            "lib": lib,
            "part": part,
            "tstamp": tstamp,
        })

    # Nets: [{code, name, nodes: [{ref, pin, pinfunction, pintype}]}]
    nets = []
    for net in root.findall(".//nets/net"):
        code = int(net.get("code"))
        name = net.get("name", "")
        nodes = []
        for node in net.findall("node"):
            nodes.append({
                "ref": node.get("ref"),
                "pin": node.get("pin"),
                "pinfunction": node.get("pinfunction", ""),
                "pintype": node.get("pintype", "passive"),
            })
        nets.append({"code": code, "name": name, "nodes": nodes})

    return components, nets


# ---------------------------------------------------------------------------
# Footprint generators  (minimal but valid for ratsnest)
# ---------------------------------------------------------------------------
# Each returns a string block.  Coordinates are relative to footprint origin.
# The 'net_map' dict maps pin_number -> (net_code, net_name).

def _pad_smd(num, x, y, sx, sy, net_code, net_name, pin_fn="", pin_type="passive", shape="roundrect"):
    """SMD pad on F.Cu."""
    net_str = f'(net {net_code} "{net_name}")' if net_code else ""
    pf_str = f'(pinfunction "{pin_fn}")' if pin_fn else ""
    pt_str = f'(pintype "{pin_type}")' if pin_type else ""
    rr = ""
    if shape == "roundrect":
        rr = "(roundrect_rratio 0.25)"
    return f"""        (pad "{num}" smd {shape}
            (at {x} {y})
            (size {sx} {sy})
            (layers "F.Cu" "F.Paste" "F.Mask")
            {rr}
            {net_str}
            {pf_str}
            {pt_str}
            (uuid "{uid()}")
        )"""


def _pad_smd_circle(num, x, y, d, net_code, net_name, pin_fn="", pin_type="passive"):
    """Circular SMD pad (for BGA balls)."""
    net_str = f'(net {net_code} "{net_name}")' if net_code else ""
    pf_str = f'(pinfunction "{pin_fn}")' if pin_fn else ""
    pt_str = f'(pintype "{pin_type}")' if pin_type else ""
    return f"""        (pad "{num}" smd circle
            (at {x} {y})
            (size {d} {d})
            (layers "F.Cu" "F.Paste" "F.Mask")
            {net_str}
            {pf_str}
            {pt_str}
            (uuid "{uid()}")
        )"""


def _courtyard_rect(x1, y1, x2, y2):
    """F.CrtYd rectangle from four lines."""
    lines = []
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i + 1) % 4]
        lines.append(f"""        (fp_line
            (start {sx} {sy}) (end {ex} {ey})
            (stroke (width 0.05) (type solid))
            (layer "F.CrtYd")
            (uuid "{uid()}")
        )""")
    return "\n".join(lines)


def _fab_rect(x1, y1, x2, y2):
    """F.Fab rectangle."""
    lines = []
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i + 1) % 4]
        lines.append(f"""        (fp_line
            (start {sx} {sy}) (end {ex} {ey})
            (stroke (width 0.1) (type solid))
            (layer "F.Fab")
            (uuid "{uid()}")
        )""")
    return "\n".join(lines)


def _ref_text(ref, x=0, y=-1.5):
    return f"""        (property "Reference" "{ref}"
            (at {x} {y} 0)
            (layer "F.SilkS")
            (uuid "{uid()}")
            (effects (font (size 1 1) (thickness 0.15)))
        )"""


def _val_text(val, x=0, y=1.5):
    return f"""        (property "Value" "{val}"
            (at {x} {y} 0)
            (layer "F.Fab")
            (uuid "{uid()}")
            (effects (font (size 1 1) (thickness 0.15)))
        )"""


def _prop_hidden(name, val=""):
    return f"""        (property "{name}" "{val}"
            (at 0 0 0) (unlocked yes) (layer "F.Fab") (hide yes)
            (uuid "{uid()}")
            (effects (font (size 1.27 1.27) (thickness 0.15)))
        )"""


def fp_0402_2pad(ref, value, comp_tstamp, net_map, at_x, at_y, desc=""):
    """Generic 0402 (1005 metric) 2-pad SMD: R, C, FB."""
    # 0402 body: 1.0 x 0.5 mm.  Pads: 0.5 x 0.6 mm, centers ±0.48 mm
    pad_sx, pad_sy = 0.5, 0.6
    pad_cx = 0.48  # center offset from origin

    n1 = net_map.get("1", (0, ""))
    n2 = net_map.get("2", (0, ""))

    pads = "\n".join([
        _pad_smd("1", -pad_cx, 0, pad_sx, pad_sy, n1[0], n1[1], shape="roundrect"),
        _pad_smd("2",  pad_cx, 0, pad_sx, pad_sy, n2[0], n2[1], shape="roundrect"),
    ])

    return f"""    (footprint "Resistor_SMD:R_0402_1005Metric"
        (layer "F.Cu")
        (uuid "{uid()}")
        (at {at_x} {at_y})
        (descr "{desc or '0402 SMD component'}")
        (tags "0402 1005")
        {_ref_text(ref)}
        {_val_text(value)}
        {_prop_hidden("Datasheet")}
        {_prop_hidden("Description", desc)}
        (path "/{comp_tstamp}")
        (sheetname "/")
        (sheetfile "afe-headstage-real.kicad_sch")
        (attr smd)
{_courtyard_rect(-0.96, -0.58, 0.96, 0.58)}
{_fab_rect(-0.5, -0.25, 0.5, 0.25)}
{pads}
    )"""


def fp_tsot5(ref, value, comp_tstamp, net_map, at_x, at_y):
    """ADP151 TSOT-23-5 / SOT-23-5 footprint (5 pads)."""
    # SOT-23-5:  pins 1-3 on bottom row (y=+0.95), pins 4-5 on top row (y=-0.95)
    # Pin pitch 0.95mm horizontal, pad size 0.6x1.1
    # Pin numbering (TSOT-5): 1=VIN, 2=GND, 3=EN, 4=NC, 5=VOUT
    # ADP151 has no pin 4 in our netlist (NC)
    pad_sx, pad_sy = 0.6, 1.1
    pin_positions = {
        "1": (-0.95, 0.95),   # VIN  (bottom left)
        "2": ( 0.00, 0.95),   # GND  (bottom center)
        "3": ( 0.95, 0.95),   # EN   (bottom right)
        "5": (-0.95, -0.95),  # VOUT (top left)
        # pin 4 would be (0.95, -0.95) but ADP151 has no pin 4
    }

    pad_lines = []
    for pin_num, (px, py) in pin_positions.items():
        nc, nn = net_map.get(pin_num, (0, ""))
        pf = {"1": "VIN", "2": "GND", "3": "EN", "5": "VOUT"}.get(pin_num, "")
        pad_lines.append(_pad_smd(pin_num, px, py, pad_sx, pad_sy, nc, nn, pin_fn=pf))

    pads = "\n".join(pad_lines)

    return f"""    (footprint "Package_TO_SOT_SMD:SOT-23-5"
        (layer "F.Cu")
        (uuid "{uid()}")
        (at {at_x} {at_y})
        (descr "SOT-23-5 / TSOT-5 package for ADP151 LDO")
        (tags "SOT-23-5 TSOT-5 LDO")
        {_ref_text(ref, y=-2.0)}
        {_val_text(value, y=2.0)}
        {_prop_hidden("Datasheet")}
        {_prop_hidden("Description", "200mA ultralow noise CMOS LDO")}
        (path "/{comp_tstamp}")
        (sheetname "/")
        (sheetfile "afe-headstage-real.kicad_sch")
        (attr smd)
{_courtyard_rect(-1.7, -1.8, 1.7, 1.8)}
{_fab_rect(-0.8, -0.65, 0.8, 0.65)}
{pads}
    )"""


def fp_bga_rhd2164(ref, value, comp_tstamp, net_map, at_x, at_y):
    """
    RHD2164 BGA package: 9.0 x 7.0 mm body, 0.5mm ball pitch.
    BGA grid: rows A-N (14 rows), columns 1-17 (17 cols).
    Only pins used in our netlist are placed as pads.
    Ball diameter 0.25mm (pad 0.275mm for paste).
    """
    # Row mapping: A=0, B=1, ... N=13
    row_letters = "ABCDEFGHJKLMN"  # Note: I is skipped in BGA convention
    row_map = {ch: i for i, ch in enumerate(row_letters)}

    # BGA origin at center.  Column 1 = left edge, column 17 = right edge
    # pitch = 0.5mm
    pitch = 0.5
    num_cols = 17
    num_rows = len(row_letters)  # 13 rows (A-N, skip I)
    col_center = (num_cols - 1) / 2.0  # = 8.0
    row_center = (num_rows - 1) / 2.0  # = 6.0

    def pin_xy(pin_name):
        """Convert pin name like 'A1', 'N17', 'M15' to (x, y) relative to center."""
        row_ch = pin_name[0]
        col_num = int(pin_name[1:])
        row_idx = row_map[row_ch]
        col_idx = col_num - 1
        x = (col_idx - col_center) * pitch
        y = (row_idx - row_center) * pitch
        return round(x, 3), round(y, 3)

    ball_d = 0.275  # pad diameter

    pad_lines = []
    for pin_num, (nc, nn) in net_map.items():
        try:
            px, py = pin_xy(pin_num)
        except (KeyError, ValueError):
            continue
        # Lookup pin function from our known pin list
        pin_fns = {
            "A1": "in0", "A2": "in1", "A3": "in2", "A4": "in3", "A17": "ref_elec",
            "H1": "in32", "H2": "in33", "H3": "in34", "H4": "in35",
            "L1": "VESD",
            "M15": "VDD_1", "M16": "LVDS_en", "M17": "GND_1",
            "N1": "GND_2", "N2": "VDD_2", "N3": "auxin1", "N4": "auxin2",
            "N5": "auxin3", "N6": "GND_3", "N7": "MISO_B", "N8": "CS",
            "N10": "SCLK", "N12": "MOSI", "N14": "MISO_A",
            "N15": "VDD_3", "N16": "auxout", "N17": "ADC_ref",
        }
        pf = pin_fns.get(pin_num, "")
        pad_lines.append(_pad_smd_circle(pin_num, px, py, ball_d, nc, nn, pin_fn=pf))

    pads = "\n".join(pad_lines)

    # Body extents: 9.0 x 7.0 mm  →  ±4.5 x ±3.5
    bx, by = 4.5, 3.5
    cx, cy = bx + 0.25, by + 0.25  # courtyard

    return f"""    (footprint "Package_BGA:BGA-196_9.0x7.0mm_Layout14x14_P0.5mm"
        (layer "F.Cu")
        (uuid "{uid()}")
        (at {at_x} {at_y})
        (descr "Intan RHD2164 BGA, 9.0x7.0mm body, 0.5mm pitch")
        (tags "BGA RHD2164 neural AFE")
        {_ref_text(ref, y=-(by+1.5))}
        {_val_text(value, y=(by+1.5))}
        {_prop_hidden("Datasheet", "https://intantech.com/files/Intan_RHD2164_datasheet.pdf")}
        {_prop_hidden("Description", "64-ch digital electrophysiology interface, DDR SPI, BGA")}
        (path "/{comp_tstamp}")
        (sheetname "/")
        (sheetfile "afe-headstage-real.kicad_sch")
        (attr smd)
{_courtyard_rect(-cx, -cy, cx, cy)}
{_fab_rect(-bx, -by, bx, by)}
{pads}
    )"""


def fp_b2b_30pin(ref, value, comp_tstamp, net_map, at_x, at_y):
    """
    Samtec QSH-030-01-L-D-A: 30-pin, 0.5mm pitch, dual-row.
    Two rows of 15 pins each.  Row spacing ~1.5mm.
    Odd pins on one row, even on the other.
    Pad size: 0.3 x 1.0 mm.
    """
    pitch = 0.5
    num_per_row = 15
    row_spacing = 1.5  # distance between row centers

    pad_lines = []
    for pin_i in range(1, 31):
        idx_in_row = (pin_i - 1) // 2  # 0..14
        is_odd = (pin_i % 2 == 1)
        x = (idx_in_row - (num_per_row - 1) / 2.0) * pitch
        y = -row_spacing / 2 if is_odd else row_spacing / 2

        nc, nn = net_map.get(str(pin_i), (0, ""))
        pin_names = {
            1: "GND", 2: "GND", 3: "SCLK", 4: "GND", 5: "MOSI",
            6: "GND", 7: "CS1", 8: "GND", 9: "CS2", 10: "GND",
            11: "MISO1_A", 12: "MISO1_B", 13: "GND", 14: "GND",
            15: "MISO2_A", 16: "MISO2_B", 17: "GND", 18: "GND",
            19: "VIN", 20: "VIN", 21: "GND", 22: "GND",
            23: "VIN", 24: "VIN", 25: "GND", 26: "GND",
            27: "TEST_SHORT_EN", 28: "CAL_EN", 29: "SPARE", 30: "GND",
        }
        pf = pin_names.get(pin_i, "")
        pad_lines.append(_pad_smd(str(pin_i), round(x, 3), round(y, 3),
                                   0.3, 1.0, nc, nn, pin_fn=pf))

    pads = "\n".join(pad_lines)

    # Body: ~8mm x 4mm
    bx, by = 4.5, 2.5
    cx, cy = bx + 0.25, by + 0.25

    return f"""    (footprint "Connector_Samtec:Samtec_QSH-030-01-x-D-A_2x15_P0.50mm"
        (layer "F.Cu")
        (uuid "{uid()}")
        (at {at_x} {at_y})
        (descr "Samtec QSH-030-01-L-D-A, 30-pin B2B connector, 0.5mm pitch")
        (tags "Samtec QSH B2B 30pin")
        {_ref_text(ref, y=-(by+1.5))}
        {_val_text(value, y=(by+1.5))}
        {_prop_hidden("Datasheet")}
        {_prop_hidden("Description", "Samtec QSH-030-01-L-D-A, 30-pin B2B connector")}
        (path "/{comp_tstamp}")
        (sheetname "/")
        (sheetfile "afe-headstage-real.kicad_sch")
        (attr smd)
{_courtyard_rect(-cx, -cy, cx, cy)}
{_fab_rect(-bx, -by, bx, by)}
{pads}
    )"""


def fp_testpoint(ref, value, comp_tstamp, net_map, at_x, at_y):
    """
    Test point: single SMD pad, 1.0mm diameter circle.
    TestPoint.pretty footprint from KiCad library.
    """
    n1 = net_map.get("1", (0, ""))

    pad = _pad_smd_circle("1", 0, 0, 1.0, n1[0], n1[1], pin_fn="1", pin_type="passive")

    return f"""    (footprint "TestPoint:TestPoint_Pad_D1.0mm"
        (layer "F.Cu")
        (uuid "{uid()}")
        (at {at_x} {at_y})
        (descr "Test point pad, 1.0mm diameter")
        (tags "test point probe")
        {_ref_text(ref, y=-1.2)}
        {_val_text(value, y=1.2)}
        {_prop_hidden("Datasheet")}
        {_prop_hidden("Description", "test point")}
        (path "/{comp_tstamp}")
        (sheetname "/")
        (sheetfile "afe-headstage-real.kicad_sch")
        (attr smd)
{_courtyard_rect(-0.75, -0.75, 0.75, 0.75)}
{pad}
    )"""


# ---------------------------------------------------------------------------
# Build net maps per component
# ---------------------------------------------------------------------------
def build_net_maps(components, nets):
    """
    Return dict: ref -> {pin_num: (net_code, net_name)}.
    Net code 0 is reserved for "unconnected" in KiCad.
    We assign net codes starting from 1.
    """
    # Build a map from (ref, pin) -> (code, name)
    ref_pin_net = {}
    for net in nets:
        for node in net["nodes"]:
            ref_pin_net[(node["ref"], node["pin"])] = (net["code"], net["name"])

    result = {}
    for comp in components:
        ref = comp["ref"]
        result[ref] = {}

    for (r, p), (c, n) in ref_pin_net.items():
        if r not in result:
            result[r] = {}
        result[r][p] = (c, n)

    return result


# ---------------------------------------------------------------------------
# PCB file assembly
# ---------------------------------------------------------------------------
def generate_pcb(components, nets):
    net_maps = build_net_maps(components, nets)

    # Comp lookup by ref
    comp_by_ref = {c["ref"]: c for c in components}

    # ---  Net declarations  ---
    net_decls = ['    (net 0 "")']
    for net in sorted(nets, key=lambda n: n["code"]):
        net_decls.append(f'    (net {net["code"]} "{net["name"]}")')

    # ---  Component placement  ---
    # Layout: place components on a grid inside the board
    # Board size: 40mm x 45mm  (room for 22 components + test points)
    # Origin at (100, 80) in PCB space (typical KiCad centering)
    bx, by = 100, 80   # board top-left
    bw, bh = 40, 45    # board width, height

    # Placement positions (hand-tuned for clean ratsnest viewing)
    placements = {
        # AFE IC — center
        "U1": (bx + 20, by + 14),
        # LDO — top left
        "U2": (bx + 6, by + 6),
        # B2B connector — bottom
        "J5": (bx + 20, by + 38),
        # Input caps for LDO
        "C1": (bx + 3, by + 6),    # VIN input cap
        "C2": (bx + 10, by + 6),   # VOUT output cap
        # DVDD bypass
        "C3": (bx + 30, by + 6),   # DVDD_AFE cap
        # ADC ref cap
        "C4": (bx + 32, by + 14),  # ADC_ref cap
        # Extra bypass
        "C5": (bx + 14, by + 6),   # +3V3 bypass
        # Ferrite bead
        "FB1": (bx + 22, by + 6),  # AVDD -> DVDD_AFE
        # Bias resistors
        "R1": (bx + 6, by + 16),
        "R2": (bx + 6, by + 19),
        "R3": (bx + 6, by + 22),
        "R4": (bx + 6, by + 25),
        # Ref electrode resistor
        "R5": (bx + 32, by + 20),
        # Test points — power rails along top edge
        "TP1": (bx + 10, by + 2),   # TP_AVDD
        "TP2": (bx + 28, by + 2),   # TP_DVDD
        "TP3": (bx + 16, by + 2),   # TP_GND (near LDO)
        # Test points — electrode channels along left edge
        "TP4": (bx + 2, by + 16),   # TP_CH0
        "TP5": (bx + 2, by + 19),   # TP_CH1
        "TP6": (bx + 2, by + 22),   # TP_CH2
        "TP7": (bx + 2, by + 25),   # TP_CH3
        # Test point — reference electrode
        "TP8": (bx + 36, by + 20),  # TP_REF
    }

    footprints = []
    for ref, (px, py) in placements.items():
        comp = comp_by_ref[ref]
        nm = net_maps.get(ref, {})
        ts = comp["tstamp"]
        val = comp["value"]

        if ref == "U1":
            footprints.append(fp_bga_rhd2164(ref, val, ts, nm, px, py))
        elif ref == "U2":
            footprints.append(fp_tsot5(ref, val, ts, nm, px, py))
        elif ref == "J5":
            footprints.append(fp_b2b_30pin(ref, val, ts, nm, px, py))
        elif ref.startswith("TP"):
            footprints.append(fp_testpoint(ref, val, ts, nm, px, py))
        elif ref.startswith("C"):
            footprints.append(fp_0402_2pad(ref, val, ts, nm, px, py, desc="Capacitor 0402"))
        elif ref.startswith("R"):
            footprints.append(fp_0402_2pad(ref, val, ts, nm, px, py, desc="Resistor 0402"))
        elif ref.startswith("FB"):
            footprints.append(fp_0402_2pad(ref, val, ts, nm, px, py, desc="Ferrite Bead 0402"))

    # ---  Board outline (Edge.Cuts)  ---
    outline_coords = [
        (bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)
    ]
    edge_lines = []
    for i in range(4):
        sx, sy = outline_coords[i]
        ex, ey = outline_coords[(i + 1) % 4]
        edge_lines.append(f"""    (gr_line
        (start {sx} {sy}) (end {ex} {ey})
        (stroke (width 0.15) (type solid))
        (layer "Edge.Cuts")
        (uuid "{uid()}")
    )""")

    # ---  Assemble the full PCB  ---
    pcb = f"""(kicad_pcb
    (version 20241229)
    (generator "gen_pcb.py")
    (generator_version "9.0")
    (general
        (thickness 1.6)
        (legacy_teardrops no)
    )
    (paper "A4")
    (title_block
        (title "Neural AFE Headstage")
        (company "BCIInterface")
        (rev "0.1")
        (date "2025-02-17")
        (comment 1 "128-ch Neural AFE (partial: 4ch + 1x RHD2164)")
        (comment 2 "DDR SPI passthrough to FPGA carrier | 3.3V LVCMOS")
    )
    (layers
        (0 "F.Cu" signal)
        (2 "B.Cu" signal)
        (9 "F.Adhes" user "F.Adhesive")
        (11 "B.Adhes" user "B.Adhesive")
        (13 "F.Paste" user)
        (15 "B.Paste" user)
        (5 "F.SilkS" user "F.Silkscreen")
        (7 "B.SilkS" user "B.Silkscreen")
        (1 "F.Mask" user)
        (3 "B.Mask" user)
        (17 "Dwgs.User" user "User.Drawings")
        (19 "Cmts.User" user "User.Comments")
        (21 "Eco1.User" user "User.Eco1")
        (23 "Eco2.User" user "User.Eco2")
        (25 "Edge.Cuts" user)
        (27 "Margin" user)
        (31 "F.CrtYd" user "F.Courtyard")
        (29 "B.CrtYd" user "B.Courtyard")
    )
    (setup
        (stackup
            (layer "F.SilkS" (type "Top Silk Screen"))
            (layer "F.Paste" (type "Top Solder Paste"))
            (layer "F.Mask"
                (type "Top Solder Mask")
                (color "Green")
                (thickness 0.01)
            )
            (layer "F.Cu"
                (type "copper")
                (thickness 0.035)
            )
            (layer "dielectric 1"
                (type "core")
                (thickness 1.51)
                (material "FR4")
                (epsilon_r 4.5)
                (loss_tangent 0.02)
            )
            (layer "B.Cu"
                (type "copper")
                (thickness 0.035)
            )
            (layer "B.Mask"
                (type "Bottom Solder Mask")
                (color "Green")
                (thickness 0.01)
            )
            (layer "B.Paste" (type "Bottom Solder Paste"))
            (layer "B.SilkS" (type "Bottom Silk Screen"))
            (copper_finish "None")
            (dielectric_constraints no)
        )
        (pad_to_mask_clearance 0)
        (allow_soldermask_bridges_in_footprints no)
        (tenting front back)
        (pcbplotparams
            (layerselection 0x00000000_00000000_00000000_000000a5)
            (plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)
            (disableapertmacros no)
            (usegerberextensions yes)
            (usegerberattributes no)
            (usegerberadvancedattributes no)
            (creategerberjobfile no)
            (dashed_line_dash_ratio 12.000000)
            (dashed_line_gap_ratio 3.000000)
            (svgprecision 6)
            (plotframeref no)
            (mode 1)
            (useauxorigin no)
            (hpglpennumber 1)
            (hpglpenspeed 20)
            (hpglpendiameter 15.000000)
            (pdf_front_fp_property_popups yes)
            (pdf_back_fp_property_popups yes)
            (pdf_metadata yes)
            (pdf_single_document no)
            (dxfpolygonmode yes)
            (dxfimperialunits yes)
            (dxfusepcbnewfont yes)
            (psnegative no)
            (psa4output no)
            (plot_black_and_white yes)
            (plotinvisibletext no)
            (sketchpadsonfab no)
            (plotpadnumbers no)
            (hidednponfab no)
            (sketchdnponfab yes)
            (crossoutdnponfab yes)
            (subtractmaskfromsilk no)
            (outputformat 1)
            (mirror no)
            (drillshape 1)
            (scaleselection 1)
            (outputdirectory "")
        )
    )

{chr(10).join(net_decls)}

{chr(10).join(footprints)}

{chr(10).join(edge_lines)}

    (embedded_fonts no)
)
"""
    return pcb


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Parsing netlist: {NETLIST}")
    components, nets = parse_netlist(NETLIST)
    print(f"  {len(components)} components, {len(nets)} nets")

    pcb_text = generate_pcb(components, nets)
    PCB_OUT.write_text(pcb_text)
    print(f"Wrote PCB: {PCB_OUT} ({PCB_OUT.stat().st_size:,} bytes)")
    print("Open in KiCad to see ratsnest (rubber-band) connections.")


if __name__ == "__main__":
    main()
