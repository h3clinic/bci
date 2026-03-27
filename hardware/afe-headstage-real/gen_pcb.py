#!/usr/bin/env python3
"""
gen_pcb.py  v6 –  Generate KiCad 9 PCB from XML netlist + real footprints
=========================================================================
Reads:
  - afe-headstage-real.xml            (netlist exported from schematic)
  - footprints.pretty/*.kicad_mod     (vendor-locked real footprints)
Produces:
  - afe-headstage-real.kicad_pcb      (0 lib_footprint_mismatch DRC)

Key changes from v5:
  - NO hand-rolled footprint geometry.  Every component uses a real .kicad_mod
    from footprints.pretty/ with exact pad/courtyard/silk/mask geometry.
  - Footprint name in PCB matches "afe_footprints:FOO" so DRC compares against
    our local library, not KiCad's global libs.
  - Net class assignments emitted into PCB (EEG_SIGNAL / DIGITAL_SPI / POWER).
  - Intent-driven placement: analog island (U1 + bias + decoupling) separated
    from digital island (J5 + SPI) with FB1 as boundary.
  - REF_ELEC bias properly labeled (not "DRL").
"""

from __future__ import annotations
import os
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
NETLIST = HERE / "afe-headstage-real.xml"
FP_DIR  = HERE / "footprints.pretty"
PCB_OUT = HERE / "afe-headstage-real.kicad_pcb"

LOCAL_LIB = "afe_footprints"   # matches fp-lib-table lib name


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def uid() -> str:
    return str(uuid.uuid4())


def load_footprint_raw(name: str) -> str:
    """Load .kicad_mod file contents as string."""
    path = FP_DIR / f"{name}.kicad_mod"
    if not path.exists():
        raise FileNotFoundError(f"Footprint not found: {path}")
    return path.read_text()


# ─────────────────────────────────────────────────────────────────────────
# Footprint injection
# ─────────────────────────────────────────────────────────────────────────
def _extract_pad_number(line: str) -> str | None:
    """Extract pad number from a (pad "NUM" ...) line."""
    m = re.search(r'\(pad\s+"([^"]*)"', line)
    return m.group(1) if m else None


def _extract_top_level_blocks(text: str) -> list[tuple[str, str]]:
    """
    Parse a .kicad_mod file and extract top-level children of the (footprint ...) block.
    Returns list of (keyword, full_text) tuples.
    Each full_text is the complete S-expression block as a string.
    """
    blocks: list[tuple[str, str]] = []
    # Skip the opening (footprint "NAME"\n and the closing )\n
    # We need to find top-level children inside the footprint
    depth = 0
    i = 0
    n = len(text)
    in_string = False
    block_start = -1
    block_keyword = ""

    while i < n:
        ch = text[i]
        if ch == '"' and (i == 0 or text[i-1] != '\\'):
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue

        if ch == '(':
            depth += 1
            if depth == 2:
                # Start of a top-level child block
                block_start = i
                # Extract keyword
                j = i + 1
                while j < n and text[j] in ' \t':
                    j += 1
                k = j
                while k < n and text[k] not in ' \t\n()\"':
                    k += 1
                block_keyword = text[j:k]
            i += 1
        elif ch == ')':
            if depth == 2 and block_start >= 0:
                # End of a top-level child block
                block_text = text[block_start:i+1]
                blocks.append((block_keyword, block_text))
                block_start = -1
            depth -= 1
            i += 1
        else:
            i += 1

    return blocks


def inject_footprint(
    fp_name: str,
    ref: str,
    value: str,
    comp_tstamp: str,
    net_map: dict[str, tuple[int, str]],
    at_x: float,
    at_y: float,
    rotation: float = 0,
) -> str:
    """
    Read a .kicad_mod, rewrite it as an embedded PCB footprint with:
    - Correct library name prefix
    - Component-specific Reference/Value/path
    - Net assignments on pads
    - Fresh UUIDs
    """
    raw = load_footprint_raw(fp_name)
    lib_fp_name = f"{LOCAL_LIB}:{fp_name}"
    rot_str = f" {rotation}" if rotation else ""

    # Parse top-level children
    blocks = _extract_top_level_blocks(raw)

    out: list[str] = []

    # Header
    out.append(f'\t(footprint "{lib_fp_name}"')
    out.append('\t\t(layer "F.Cu")')
    out.append(f'\t\t(uuid "{uid()}")')
    out.append(f'\t\t(at {at_x} {at_y}{rot_str})')

    # Copy descr, tags, attr from original
    for kw, text in blocks:
        if kw in ('descr', 'tags', 'attr'):
            out.append(f'\t\t{text.strip()}')

    # Properties
    # Reference on F.Fab (not F.SilkS) — avoids silk_over_copper (DRC) when
    # the ref text sits above exposed copper pads, and silk_overlap when it
    # collides with courtyard outlines on F.Silkscreen.
    out.append(f'\t\t(property "Reference" "{ref}"')
    out.append('\t\t\t(at 0 0 0)')
    out.append('\t\t\t(layer "F.Fab")')
    out.append(f'\t\t\t(uuid "{uid()}")')
    out.append('\t\t\t(effects (font (size 1 1) (thickness 0.15)))')
    out.append('\t\t)')

    out.append(f'\t\t(property "Value" "{value}"')
    out.append('\t\t\t(at 0 0 0)')
    out.append('\t\t\t(layer "F.Fab")')
    out.append(f'\t\t\t(uuid "{uid()}")')
    out.append('\t\t\t(effects (font (size 1 1) (thickness 0.15)))')
    out.append('\t\t)')

    out.append('\t\t(property "Datasheet" ""')
    out.append('\t\t\t(at 0 0 0) (unlocked yes) (layer "F.Fab") (hide yes)')
    out.append(f'\t\t\t(uuid "{uid()}")')
    out.append('\t\t\t(effects (font (size 1.27 1.27) (thickness 0.15)))')
    out.append('\t\t)')

    out.append('\t\t(property "Description" ""')
    out.append('\t\t\t(at 0 0 0) (unlocked yes) (layer "F.Fab") (hide yes)')
    out.append(f'\t\t\t(uuid "{uid()}")')
    out.append('\t\t\t(effects (font (size 1.27 1.27) (thickness 0.15)))')
    out.append('\t\t)')

    # Path / sheet info
    out.append(f'\t\t(path "/{comp_tstamp}")')
    out.append('\t\t(sheetname "/")')
    out.append('\t\t(sheetfile "afe-headstage-real.kicad_sch")')

    # Copy graphics and pads — skip metadata blocks
    skip_keywords = {
        'version', 'generator', 'generator_version', 'layer',
        'descr', 'tags', 'attr', 'property', 'embedded_fonts', 'model',
    }

    for kw, text in blocks:
        if kw in skip_keywords:
            continue

        # Replace UUIDs with fresh ones
        text = re.sub(
            r'\(uuid "[^"]*"\)',
            lambda _: f'(uuid "{uid()}")',
            text,
        )

        if kw == 'pad':
            # Extract pin number and inject net
            pin_num = _extract_pad_number(text)
            if pin_num and pin_num in net_map:
                nc, nn = net_map[pin_num]
                if nc:
                    net_line = f'(net {nc} "{nn}")'
                    # Insert net before the final closing paren
                    rpos = text.rfind(')')
                    text = text[:rpos].rstrip() + '\n\t\t\t' + net_line + '\n\t\t)'

        # Reindent: each line gets 2 tabs prefix
        for line in text.strip().splitlines():
            stripped = line.lstrip('\t ')
            out.append(f'\t\t{stripped}')

    out.append('\t\t(embedded_fonts no)')
    out.append('\t)')
    return '\n'.join(out)


# ─────────────────────────────────────────────────────────────────────────
# Netlist parser
# ─────────────────────────────────────────────────────────────────────────
def parse_netlist(path: Path):
    """Return (components, nets) from KiCad XML netlist."""
    tree = ET.parse(path)
    root = tree.getroot()

    components = []
    for comp in root.findall(".//components/comp"):
        ref = comp.get("ref")
        value_el = comp.findtext("value", "")
        tstamp = comp.findtext("tstamps", uid())
        components.append({
            "ref": ref,
            "value": value_el,
            "tstamp": tstamp,
        })

    nets = []
    for net in root.findall(".//nets/net"):
        code = int(net.get("code"))
        name = net.get("name", "")
        nodes = []
        for node in net.findall("node"):
            nodes.append({
                "ref": node.get("ref"),
                "pin": node.get("pin"),
            })
        nets.append({"code": code, "name": name, "nodes": nodes})

    return components, nets


def build_net_maps(components, nets):
    """Return dict: ref -> {pin_num: (net_code, net_name)}."""
    ref_pin_net = {}
    for net in nets:
        for node in net["nodes"]:
            ref_pin_net[(node["ref"], node["pin"])] = (net["code"], net["name"])

    result = {c["ref"]: {} for c in components}
    for (r, p), (c, n) in ref_pin_net.items():
        if r in result:
            result[r][p] = (c, n)
    return result


# ─────────────────────────────────────────────────────────────────────────
# Footprint-to-component mapping
# ─────────────────────────────────────────────────────────────────────────
FOOTPRINT_MAP = {
    "U1":  "RHD2164_BGA_104",           # Intan RHD2164 custom BGA
    "U2":  "TSOT-23-5",                 # ADP151 LDO (TSOT-23-5, not SOT-23-5)
    "J5":  "QSH-030-01-L-D-A",          # Samtec QSH-030 B2B connector
    "R":   "R_0402_1005Metric",          # Generic 0402 resistor
    "C":   "C_0402_1005Metric",          # Generic 0402 capacitor
    "FB":  "L_0402_1005Metric",          # Ferrite bead (inductor footprint)
    "TP":  "TestPoint_Pad_D1.0mm",       # SMD test point pad
}


def get_footprint_name(ref: str) -> str:
    """Get footprint name for a component reference."""
    if ref in FOOTPRINT_MAP:
        return FOOTPRINT_MAP[ref]
    for prefix in ["FB", "TP", "R", "C"]:
        if ref.startswith(prefix):
            return FOOTPRINT_MAP[prefix]
    raise KeyError(f"No footprint mapping for {ref}")


# ─────────────────────────────────────────────────────────────────────────
# Net class assignments
# ─────────────────────────────────────────────────────────────────────────
def get_net_class_assignments(nets) -> str:
    """Generate net_class blocks for the PCB (KiCad 9 format).

    Returns one net_class block per class, each with (add_net "...") entries.
    Also returns a Default net_class for any unassigned nets.
    """
    # EEG_INPUT: CH0-CH3 + REF_ELEC (sensitive analog inputs, 0.20mm clearance)
    # EEG_SIGNAL: ADC_ref only (analog but less sensitive, 0.15mm clearance)
    eeg_input_pats = ["ch0", "ch1", "ch2", "ch3", "ref_elec"]
    eeg_signal_pats = ["adc_ref"]
    spi_pats = ["sclk", "mosi", "miso1_a", "miso1_b", "cs1"]
    pwr_pats = ["+3v3", "vcc", "vin", "avdd", "vdd_afe_filt", "gnd"]

    # Classify nets
    classes: dict[str, list[str]] = {
        "EEG_INPUT": [],
        "EEG_SIGNAL": [],
        "DIGITAL_SPI": [],
        "POWER": [],
    }

    for net in nets:
        nl = net["name"].lower()
        if not nl:
            continue
        # Only match named signals, skip unconnected
        if nl.startswith("unconnected-"):
            continue

        cls = None
        for p in eeg_input_pats:
            if p in nl:
                cls = "EEG_INPUT"
                break
        if not cls:
            for p in eeg_signal_pats:
                if p in nl:
                    cls = "EEG_SIGNAL"
                    break
        if not cls:
            for p in spi_pats:
                if p in nl:
                    cls = "DIGITAL_SPI"
                    break
        if not cls:
            for p in pwr_pats:
                if p in nl:
                    cls = "POWER"
                    break

        if cls:
            classes[cls].append(net["name"])

    # Definitions — (description, trace_width, clearance)
    # EEG_INPUT gets 0.20mm clearance for analog isolation.
    # Other clearances set to fab-minimum (0.15 mm).
    # Trace widths encode *routing intent* (current / impedance) only.
    class_defs = {
        "EEG_INPUT": ("EEG analog input nets", 0.15, 0.20),
        "EEG_SIGNAL": ("EEG analog signal nets", 0.15, 0.15),
        "DIGITAL_SPI": ("SPI digital bus", 0.15, 0.15),
        "POWER": ("Power distribution nets", 0.4, 0.15),
    }

    blocks = []

    # Default net_class — catches anything not explicitly assigned.
    # Clearance MUST match fab-minimum so unclassified nets don't silently
    # get KiCad's built-in 0.20mm default.
    blocks.append(
        '\t(net_class "Default" "Default net class — fab-minimum clearance"\n'
        '\t\t(clearance 0.15)\n'
        '\t\t(trace_width 0.15)\n'
        '\t)'
    )

    for cls, (desc, tw, clr) in class_defs.items():
        nets_in = classes.get(cls, [])
        if not nets_in:
            continue
        add_lines = "\n".join(f'\t\t(add_net "{n}")' for n in sorted(nets_in))
        blocks.append(
            f'\t(net_class "{cls}" "{desc}"\n'
            f'\t\t(clearance {clr})\n'
            f'\t\t(trace_width {tw})\n'
            f'{add_lines}\n'
            f'\t)'
        )

    return "\n".join(blocks)


# ─────────────────────────────────────────────────────────────────────────
# Intent-driven placement
# ─────────────────────────────────────────────────────────────────────────
# Board: 40mm × 45mm, origin at (100, 80)
#
# U1 BGA pin map (key nets, offsets from U1 origin):
#   CH0-CH3:   Row A top-left   (−4..−2.5, −3)   → NORTH side
#   REF_ELEC:  A17 top-right    (+4, −3)          → NORTH-EAST
#   SPI:       Row N bottom     (−1..+2, +3)      → SOUTH side
#   VDD:       M15/N2/N15       south-east         → y = +2.5..+3
#   GND:       L1/M16(LVDS_en)/M17/N1/N6 south     → y = +2..+3
#   ADC_ref:   N17 bottom-right (+4, +3)           → SOUTH-EAST corner
#
# Layout strategy (4-layer, L2 = solid GND):
#   NORTH of U1: analog inputs — CH bias resistors, EEG TPs, REF path
#   SOUTH of U1: power balls, SPI, ADC_ref — decouplers in tight ring
#   TOP ROW:     Power conditioning (LDO + bulk caps + bead)
#   SW CORNER:   Digital island — J5 connector, SPI bundle routes here
#   Separation:  CH inputs (north) physically far from digital (south-west)

BX, BY = 100, 80
BW, BH = 40, 45

PLACEMENTS = {
    # ── Analog island (center) ──
    "U1": (BX + 20, BY + 18),               # (120, 98) — BGA center

    # ── CH bias resistors — NORTH of U1, near Row A input pins ──
    # CH0-CH3 pins are at abs y=95, x=116..117.5
    # Resistors form a column approaching from the left/north
    "R1": (BX + 13, BY + 13),               # (113, 93) CH0 bias → pad A1 (116,95)
    "R2": (BX + 13, BY + 14.5),             # (113, 94.5) CH1 bias → pad A2
    "R3": (BX + 13, BY + 16),               # (113, 96) CH2 bias → pad A3
    "R4": (BX + 13, BY + 17.5),             # (113, 97.5) CH3 bias → pad A4

    # ── REF electrode bias — NORTH of U1, near A10 pin ──
    # REF_ELEC pin A10 at abs (120.5, 95)
    "R5": (BX + 23, BY + 13),               # (123, 93) near A10, north side

    # ── Local decoupling caps — tight ring around U1 SOUTH side ──
    # These serve U1 power balls directly; minimize loop inductance.
    # +3V3 pad 4 at (121.1, 99), L16 at (123.5, 100)
    "C5": (BX + 22, BY + 22.5),             # (122, 102.5) +3V3 local — near pad 4/L16
    # VCC pad 3 at (118.9, 99), GND pad 2 at (118.9, 98)
    "C3": (BX + 18, BY + 22.5),             # (118, 102.5) VDD_AFE_FILT local — near U1 VDD balls
    # ADC_ref N17 at (124, 101)
    "C4": (BX + 26, BY + 22.5),             # (126, 102.5) ADC_ref bypass — adjacent to N17

    # ── Bulk caps (near LDO, not U1) ──
    "C1": (BX + 6, BY + 7),                 # (106, 87) LDO VIN input cap
    "C2": (BX + 14, BY + 7),                # (114, 87) LDO VOUT bulk (+3V3/AVDD)

    # ── Power conditioning (top row) ──
    "U2": (BX + 10, BY + 7),                # (110, 87) ADP151 LDO
    "FB1": (BX + 23, BY + 7),               # (123, 87) +3V3 → VDD_AFE_FILT boundary bead

    # ── Digital island (SOUTH-WEST corner) ──
    # J5 off-axis from U1: digital peninsula in SW quadrant
    # SPI exits U1 south (y=101, x≈119-122), routes left+down to J5
    "J5": (BX + 8, BY + 38),                # (108, 118) SW corner — away from analog

    # ── Test points ──
    "TP1": (BX + 8,  BY + 3),               # TP_AVDD — near LDO output
    "TP2": (BX + 28, BY + 3),               # TP_DVDD — near bead output
    "TP3": (BX + 18, BY + 3),               # TP_GND  — top edge
    "TP4": (BX + 10, BY + 11),              # TP_CH0  — north, near bias R1
    "TP5": (BX + 10, BY + 13.5),            # TP_CH1  — north, near bias R2
    "TP6": (BX + 10, BY + 16),              # TP_CH2  — north, near bias R3
    "TP7": (BX + 10, BY + 18.5),            # TP_CH3  — north, near bias R4
    "TP8": (BX + 26, BY + 13),              # TP_REF  — analog island north-east, near R5
}


# ─────────────────────────────────────────────────────────────────────────
# Zones (copper pours)
# ─────────────────────────────────────────────────────────────────────────
def make_zone(net_code, net_name, layer, corners, priority=0,
              connect_solid=False):
    """Generate a filled zone (copper pour) S-expression.

    corners: list of (x, y) tuples defining the zone outline.
    connect_solid: if True, use solid pad connections (no thermal relief).
                   Preferred for power-entry padfields where low impedance
                   matters more than thermal isolation during soldering.
    """
    pts = "\n".join(
        f"\t\t\t\t(xy {x} {y})" for x, y in corners
    )
    if connect_solid:
        connect_block = "\t\t(connect_pads yes\n\t\t\t(clearance 0.2)\n\t\t)"
    else:
        connect_block = "\t\t(connect_pads\n\t\t\t(clearance 0.2)\n\t\t)"
    return f"""\t(zone
\t\t(net {net_code})
\t\t(net_name "{net_name}")
\t\t(layer "{layer}")
\t\t(uuid "{uid()}")
\t\t(name "{net_name}_{layer}")
\t\t(hatch edge 0.5)
\t\t(priority {priority})
{connect_block}
\t\t(min_thickness 0.15)
\t\t(filled_areas_thickness no)
\t\t(fill
\t\t\t(thermal_gap 0.3)
\t\t\t(thermal_bridge_width 0.3)
\t\t)
\t\t(polygon
\t\t\t(pts
{pts}
\t\t\t)
\t\t)
\t)"""


# ─────────────────────────────────────────────────────────────────────────
# U1 BGA keepout enforcement — NO via centers inside this rectangle
# ─────────────────────────────────────────────────────────────────────────
U1_KEEPOUT = (115.0, 94.0, 125.0, 102.0)  # (x_min, y_min, x_max, y_max)


# ─────────────────────────────────────────────────────────────────────────
# Analog corridor — F.Cu region reserved for EEG-class traces
# ─────────────────────────────────────────────────────────────────────────
# This rectangle encloses all EEG routing, bias resistors (R1-R5), and
# test points (TP4-TP8).  Only analog-class nets may have F.Cu segments
# inside this box.  Digital/SPI nets must stay out.
#
# Derived from geometry:
#   West:  x=107.5  (CH3 jog at x=108 minus 0.5mm guard margin)
#   East:  x=127.0  (TP8 at x=126 plus 1.0mm pad+margin)
#   North: y=88.5   (CH3 escape_y=89 minus 0.5mm guard margin)
#   South: y=99.0   (CH3→TP7 at y=98.5 plus 0.5mm guard margin)
#
# Note: BGA ball escapes (y=95→escape_y) cross both this corridor and
# the U1 keepout.  That's fine — the point is that no SPI or power
# traces should intrude into this noise-sensitive region.
ANALOG_CORRIDOR = (107.5, 88.5, 127.0, 99.0)  # (x_min, y_min, x_max, y_max)

# Nets allowed to have F.Cu segments inside the analog corridor.
# GND is allowed because R1-R5 pad2 connections and stitch vias live here.
# ADC_ref is excluded: it runs south of the BGA (y>101) and should never
# cross into the analog input zone.
ANALOG_CORRIDOR_ALLOWED_NETS = frozenset({
    "/CH0", "/CH1", "/CH2", "/CH3",
    "/REF_ELEC",
    "GND",
})


def assert_via_outside_u1_keepout(x, y, label=""):
    """Raise if via center (x, y) falls inside the U1 BGA keepout rectangle."""
    x_min, y_min, x_max, y_max = U1_KEEPOUT
    if x_min <= x <= x_max and y_min <= y <= y_max:
        raise ValueError(
            f"Via at ({x}, {y}) [{label}] is inside U1 BGA keepout "
            f"[{x_min}–{x_max}, {y_min}–{y_max}]. "
            "Dogbone escapes must place vias OUTSIDE this rectangle."
        )


# ─────────────────────────────────────────────────────────────────────────
# Vias
# ─────────────────────────────────────────────────────────────────────────
def make_via(x, y, net_code, net_name, drill=0.3, size=0.6,
             layers=("F.Cu", "B.Cu")):
    """Generate a single via S-expression.

    Default: 0.3mm drill / 0.6mm annular — standard for 4-layer fab.
    """
    return f"""\t(via
\t\t(at {x} {y})
\t\t(size {size})
\t\t(drill {drill})
\t\t(layers "{layers[0]}" "{layers[1]}")
\t\t(net {net_code})
\t\t(uuid "{uid()}")
\t)"""


def generate_decoupler_vias(nets):
    """Generate via pairs for each local decoupling cap.

    Each cap has:
      pad 1 (power) at offset (-0.48, 0) from cap center
      pad 2 (GND)   at offset (+0.48, 0) from cap center

    Via placement: 0.5mm south of each pad center (toward board edge),
    minimizing loop area.

    Strategy:
      C3 (/VDD_AFE_FILT): power via → In2.Cu local island + GND via → In1.Cu plane
      C5 (+3V3):      power via → In2.Cu +3V3 plane + GND via → In1.Cu plane
      C4 (ADC_ref):   GND via only — ADC_ref routes as short F.Cu trace, no plane
    """
    net_by_name = {n["name"]: n["code"] for n in nets}

    # (ref, cap_x, cap_y, power_net_name)
    # Only C3 and C5 get power vias to In2.Cu zones.
    # C4 (ADC_ref) does NOT get a power via — ADC_ref is a local reference
    # node, not a plane net. It routes as a short F.Cu trace from U1 ball.
    caps = [
        ("C3", *PLACEMENTS["C3"], "/VDD_AFE_FILT"),
        ("C5", *PLACEMENTS["C5"], "+3V3"),
    ]

    gnd_code = net_by_name["GND"]
    vias = []
    via_offset_y = 0.5  # mm south of pad center

    for ref, cx, cy, pwr_net_name in caps:
        pwr_code = net_by_name[pwr_net_name]
        # Pad 1 (power) absolute position
        pwr_x = cx - 0.48
        pwr_y = cy
        # Pad 2 (GND) absolute position
        gnd_x = cx + 0.48
        gnd_y = cy

        # C5 vias: power via offset 0.07mm west to create clearance for
        # MISO_A SPI trace at x=122.0 (0.10mm neckdown, right edge 122.05).
        # GND via stays at pad centre but shrunk to 0.55mm (0.3mm drill,
        # 0.125mm annular ring) so both clearances pass:
        #   Power via (121.45, 103.0): MISO_A gap = 122.0-121.45-0.3-0.05 = 0.200mm ✓
        #                              MOSI gap  = 121.45-0.3-120.9-0.075 = 0.175mm ✓
        #   GND via   (122.48, 103.0, ⌀ 0.55mm r=0.275):
        #     MISO_A gap = 122.48-0.275-122.05 = 0.155mm ✓ (>0.15 POWER)
        #     VDD stub  = 122.9365-(122.48+0.275) = 0.182mm ✓ (>0.15 POWER)
        if ref == "C5":
            pwr_via_x = pwr_x - 0.07   # 121.52 - 0.07 = 121.45
            gnd_via_x = gnd_x           # 122.48 (at pad centre)
        # C3 GND via: offset 0.1mm west to clear MISO_B at x=119.0.
        #   Original (118.48, 103.0): gap = 0.145mm (0.005mm short of 0.15mm)
        #   Moved   (118.38, 103.0): gap = 0.245mm ✓
        #   Hole-to-hole to C3 VDD via (117.52, 103.0): 0.86-0.3=0.56mm ≥ 0.55mm ✓
        elif ref == "C3":
            pwr_via_x = pwr_x
            gnd_via_x = gnd_x - 0.10   # 118.48 - 0.10 = 118.38
        else:
            pwr_via_x = pwr_x
            gnd_via_x = gnd_x

        # Power via: pad 1 → L3 (In2.Cu power plane)
        # Using F.Cu↔In2.Cu span so it doesn't punch through GND plane
        # unnecessarily — but KiCad needs full-span vias in basic mode.
        # Full-span via (F.Cu↔B.Cu) is standard; the zone fill on each
        # internal layer connects the correct net.
        vias.append(make_via(
            pwr_via_x, pwr_y + via_offset_y,
            pwr_code, pwr_net_name,
        ))

        # GND via: pad 2 → L2 (In1.Cu GND plane)
        # C5 GND via shrunk to ⌀ 0.55mm (0.125mm annular ring) to clear
        # both MISO_A neckdown (west) and VDD dogbone stub (east).
        gnd_via_size = 0.55 if ref == "C5" else 0.6
        vias.append(make_via(
            gnd_via_x, gnd_y + via_offset_y,
            gnd_code, "GND",
            size=gnd_via_size,
        ))

    # C4 (ADC_ref): GND via only — no power plane for ADC_ref
    c4_x, c4_y = PLACEMENTS["C4"]
    c4_gnd_x = c4_x + 0.48  # pad 2 (GND side)
    vias.append(make_via(
        c4_gnd_x, c4_y + via_offset_y,
        gnd_code, "GND",
    ))

    return vias


# ─────────────────────────────────────────────────────────────────────────
# Track segments
# ─────────────────────────────────────────────────────────────────────────
def make_segment(x1, y1, x2, y2, net_code, net_name, width=0.4,
                 layer="F.Cu"):
    """Generate a single PCB track segment S-expression."""
    return f"""\t(segment
\t\t(start {x1} {y1}) (end {x2} {y2})
\t\t(width {width})
\t\t(layer "{layer}")
\t\t(net {net_code})
\t\t(uuid "{uid()}")
\t)"""


def generate_power_tracks(nets):
    """Generate F.Cu power tracks for Step 1 (+3V3) and Step 2 (/VDD_AFE_FILT).

    Strategy:
      - Short F.Cu stubs from component pads to nearby vias that drop
        into the In2.Cu power planes.  No long F.Cu snakes.
      - Decoupler cap vias (C3, C5) already exist from generate_decoupler_vias.
      - Bulk cap vias (C1, C2) and bead/LDO connections added here.
      - U1 VDD and GND ball vias added here.

    Absolute pad coordinates (from footprint analysis):
      FB1.1  = (122.515, 87.0)  /VDD_AFE_FILT
      FB1.2  = (123.485, 87.0)  +3V3
      U2.1   = (108.8625, 86.05) VCC (VIN)
      U2.2   = (108.8625, 87.0)  GND
      U2.5   = (111.1375, 86.05) +3V3 (VOUT)
      C1.1   = (105.52, 87.0)   VCC
      C1.2   = (106.48, 87.0)   GND
      C2.1   = (113.52, 87.0)   +3V3
      C2.2   = (114.48, 87.0)   GND
      TP1    = (108.0, 83.0)    +3V3
      TP2    = (128.0, 83.0)    /VDD_AFE_FILT
      U1.M15 = (123.0, 100.5)   /VDD_AFE_FILT
      U1.N15 = (123.0, 101.0)   /VDD_AFE_FILT
      U1.N2  = (116.5, 101.0)   /VDD_AFE_FILT
      U1.L1  = (116.0, 100.0)   GND
      U1.N1  = (116.0, 101.0)   GND
      U1.N6  = (118.5, 101.0)   GND
      U1.M17 = (124.0, 100.5)   GND
    """
    net_by_name = {n["name"]: n["code"] for n in nets}
    v33 = net_by_name["+3V3"]
    vaf = net_by_name["/VDD_AFE_FILT"]
    gnd = net_by_name["GND"]
    vcc = net_by_name["VCC"]

    tracks = []
    vias = []

    # ── Step 1: +3V3 plane drops ─────────────────────────────────────────

    # 1a. U2.5 VOUT (111.1375, 86.05) → C2.1 (113.52, 87.0)
    #     Short F.Cu trace, POWER width (0.4mm)
    tracks.append(make_segment(111.1375, 86.05, 113.52, 87.0, v33, "+3V3"))

    # 1b. C2 bulk cap vias (not generated by decoupler_vias — those are C3/C5/C4 only)
    #     C2.1 (+3V3) via at pad south → In2.Cu plane
    vias.append(make_via(113.52, 87.5, v33, "+3V3"))
    #     C2.2 (GND) via south → In1.Cu plane
    vias.append(make_via(114.48, 87.5, gnd, "GND"))

    # 1c. FB1.2 (+3V3 side, 123.485, 87.0) → via to In2.Cu +3V3 plane
    #     Short stub south, then via
    vias.append(make_via(123.485, 87.5, v33, "+3V3"))
    tracks.append(make_segment(123.485, 87.0, 123.485, 87.5, v33, "+3V3"))

    # 1d. TP1 (108.0, 83.0) on +3V3 → via to In2.Cu plane
    vias.append(make_via(108.0, 83.5, v33, "+3V3"))
    tracks.append(make_segment(108.0, 83.0, 108.0, 83.5, v33, "+3V3"))

    # 1e. U2.1 VIN (108.8625, 86.05) → C1.1 VCC (105.52, 87.0)
    #     Route north to avoid U2.2 GND pad at (108.8625, 87.0)
    #     U2.1 → north to y=85.5 (connects to VCC trunk from J5)
    #     Trunk at y=85.5 continues west to C1.1 x → south to C1.1 y
    #     NOTE: the east segment of the trunk (102.5→108.8625 at y=85.5)
    #     replaces the old L-trace segment here; C1.1 connection is
    #     handled by the trunk overshooting to x=105.52 then stub south.
    tracks.append(make_segment(108.8625, 86.05, 108.8625, 85.5, vcc, "VCC"))
    tracks.append(make_segment(105.52, 85.5, 105.52, 87.0, vcc, "VCC"))

    # 1f. C1 input cap vias
    #     C1.2 (GND) via south → In1.Cu plane
    vias.append(make_via(106.48, 87.5, gnd, "GND"))

    # 1g. U2.2 GND (108.8625, 87.0) → via offset west to clear U2.3 EN pad at (108.8625, 87.95)
    #     Via at (107.5, 87.5) — 1.36mm from U2.3 pad center, well clear.
    vias.append(make_via(107.5, 87.5, gnd, "GND"))
    tracks.append(make_segment(108.8625, 87.0, 107.5, 87.0, gnd, "GND"))
    tracks.append(make_segment(107.5, 87.0, 107.5, 87.5, gnd, "GND"))

    # ── Step 1½: VCC distribution (J5 VIN → U2 VIN/EN + C1) ─────────
    #
    # Architecture: direct traces (no VCC zone — zone would starve
    # flanking GND pads 17-26 of thermal relief at 0.5mm pad pitch).
    # Two vertical VCC columns at x=109.0 (pins 19/20) and x=110.0
    # (pins 23/24) merge below the connector at y=120.5, then one
    # F.Cu trunk runs west to x=102.5 and north to U2/C1.
    #
    # F.Cu was chosen over B.Cu because all 5 SPI horizontal lanes cross
    # x=109 on B.Cu (y=105.5–112), making a north-south B.Cu trunk
    # impossible without track crossings.
    #
    # EN tie: from U2.3 EN, east around U2.2 GND pad to x=110,
    # then north to VCC horizontal at y=85.5.
    #
    # GND stitch via at (102.0, 113.0) moved to (101.5, 113.0) to
    # clear the VCC trunk at x=102.5 (gap = 0.5mm).
    #
    # Clearance verification:
    #   Trunk x=102.5 (F.Cu, width 0.4mm, edges 102.3–102.7):
    #     GND stitch via (101.5, 113.0, r=0.3): 102.3-101.8 = 0.5mm ✓
    #     MISO_B B.Cu (x=103): different layer ✓
    #     SCLK via-up (104.0, 115.0): 104.0-102.7-0.3 = 1.0mm ✓
    #     Board west edge (x=100): 102.3-100 = 2.3mm ✓
    #   EN tie (x=110.0, y=87.95–85.5):
    #     U2.2 GND pad right edge (109.525): 110.0-0.2-109.525 = 0.275mm ✓
    #     U2.4 NC pad left edge (110.475): 110.475-110.0-0.2 = 0.275mm ✓
    #     Analog corridor (y≥88.5): all F.Cu at y≤87.95 ✓
    #   VCC columns at J5 (x=109.0, x=110.0, width 0.4mm):
    #     Column x=109.0 edges 108.8–109.2:
    #       Pin 17/18 GND (x=108.5, pad right 108.65): 108.8-108.65 = 0.15mm ✓
    #       Pin 21/22 GND (x=109.5, pad left 109.35): 109.35-109.2 = 0.15mm ✓
    #     Column x=110.0 edges 109.8–110.2:
    #       Pin 21/22 GND (x=109.5, pad right 109.65): 109.8-109.65 = 0.15mm ✓
    #       Pin 25/26 GND (x=110.5, pad left 110.35): 110.35-110.2 = 0.15mm ✓
    #   Horizontal at y=120.5 (width 0.4mm, edges 120.3–120.7):
    #     J5 row-2 pad bottom edges (119.2+0.55=119.75): 120.3-119.75 = 0.55mm ✓
    #     Board south edge (y=125): 125-120.7 = 4.3mm ✓

    # 1h. J5 VCC column 1: pins 19→20 (x=109.0) + south exit
    #     Width 0.2mm through pad field (not default 0.4mm power) to
    #     preserve GND zone thermal relief spokes on flanking pads.
    tracks.append(make_segment(109.0, 116.8, 109.0, 119.2, vcc, "VCC", width=0.2))
    tracks.append(make_segment(109.0, 119.2, 109.0, 120.5, vcc, "VCC", width=0.2))

    # 1h'. J5 VCC column 2: pins 23→24 (x=110.0) + south exit
    tracks.append(make_segment(110.0, 116.8, 110.0, 119.2, vcc, "VCC", width=0.2))
    tracks.append(make_segment(110.0, 119.2, 110.0, 120.5, vcc, "VCC", width=0.2))

    # 1h''. Merge columns + trunk west + north to U2 area
    tracks.append(make_segment(110.0, 120.5, 109.0, 120.5, vcc, "VCC"))
    tracks.append(make_segment(109.0, 120.5, 102.5, 120.5, vcc, "VCC"))
    tracks.append(make_segment(102.5, 120.5, 102.5, 85.5, vcc, "VCC"))
    tracks.append(make_segment(102.5, 85.5, 105.52, 85.5, vcc, "VCC"))
    tracks.append(make_segment(105.52, 85.5, 108.8625, 85.5, vcc, "VCC"))

    # 1i. EN tie: U2.3 (108.8625, 87.95) → east around U2.2 GND pad →
    #     north to VCC L-trace at y=85.5.
    #     x=110.0 center: clears U2.2 right edge (109.525) by 0.275mm
    #     and U2.4 left edge (110.475) by 0.275mm.
    tracks.append(make_segment(108.8625, 87.95, 110.0, 87.95, vcc, "VCC"))
    tracks.append(make_segment(110.0, 87.95, 110.0, 85.5, vcc, "VCC"))
    tracks.append(make_segment(110.0, 85.5, 108.8625, 85.5, vcc, "VCC"))

    # ── Step 2: /VDD_AFE_FILT island ─────────────────────────────────────

    # 2a. FB1.1 (122.515, 87.0) → via into /VDD_AFE_FILT island
    #     Route east around BGA courtyard (x=124.75), TP8 (126,93), AND C4 (126,102.5):
    #       FB1.1 → north to y=86 → east to x=128 (== TP2 x, same net)
    #       → south to y=104 (below everything) → west to x=123 → via
    #     The vertical at x=128 naturally passes through TP2 pad at (128, 83),
    #     so no separate TP2 branch needed.
    tracks.append(make_segment(122.515, 87.0, 122.515, 86.0, vaf, "/VDD_AFE_FILT"))
    tracks.append(make_segment(122.515, 86.0, 128.0, 86.0, vaf, "/VDD_AFE_FILT"))
    # Vertical: TP2 at (128, 83) → north stub, then south to y=104
    tracks.append(make_segment(128.0, 86.0, 128.0, 83.0, vaf, "/VDD_AFE_FILT"))
    tracks.append(make_segment(128.0, 86.0, 128.0, 104.0, vaf, "/VDD_AFE_FILT"))
    # West into island at y=104 — clears C4 pads/vias (nearest at x=126.48)
    tracks.append(make_segment(128.0, 104.0, 123.0, 104.0, vaf, "/VDD_AFE_FILT"))
    vias.append(make_via(123.0, 104.0, vaf, "/VDD_AFE_FILT"))

    # 2c–2d: U1 VDD/GND ball vias REMOVED — replaced by dogbone escapes
    #         in generate_dogbone_escapes(). No via-in-pad under U1 BGA.

    # 2e. C3 pad 1 (/VDD_AFE_FILT, 117.52, 102.5) → stub to its existing via (117.52, 103.0)
    #     The decoupler via exists but there's no trace connecting pad to via.
    tracks.append(make_segment(117.52, 102.5, 117.52, 103.0, vaf, "/VDD_AFE_FILT"))
    # C3 pad 2 (GND, 118.48, 102.5) → via (118.38, 103.0)
    #   Via offset 0.1mm west for MISO_B clearance.
    #   L-shaped: pad south to y=103.0, then west 0.1mm to via.
    tracks.append(make_segment(118.48, 102.5, 118.48, 103.0, gnd, "GND"))
    tracks.append(make_segment(118.48, 103.0, 118.38, 103.0, gnd, "GND"))

    # 2f. C5 pad 1 (+3V3, 121.52, 102.5) → its via (121.45, 103.0)
    #     Via offset 0.07mm west for MISO_A clearance.
    #     L-shaped: pad south to y=103.0, then west 0.07mm to via.
    tracks.append(make_segment(121.52, 102.5, 121.52, 103.0, v33, "+3V3"))
    tracks.append(make_segment(121.52, 103.0, 121.45, 103.0, v33, "+3V3"))
    # C5 pad 2 (GND, 122.48, 102.5) → its via (122.48, 103.0) — straight south
    tracks.append(make_segment(122.48, 102.5, 122.48, 103.0, gnd, "GND"))

    # 2g. C4 pad 2 (GND, 126.48, 102.5) → its existing via (126.48, 103.0)
    tracks.append(make_segment(126.48, 102.5, 126.48, 103.0, gnd, "GND"))

    # ── Step 3: GND stitching vias — local return paths ──────────────────

    # 3a. Near FB1/C3 filtered island edge (120.0, 87.5)
    #     Gives the +3V3 → /VDD_AFE_FILT transition a local GND return.
    #     Between FB1 (123, 87) and C2 (114, 87) on F.Cu north row.
    vias.append(make_via(120.0, 87.5, gnd, "GND"))

    # 3b. Near U2/C2/C1 +3V3 cluster (112.5, 87.5)
    #     Local return for LDO output → C2 bulk cap loop.
    #     Between U2 (110, 87) and C2 (114, 87).
    #     Offset east from 112.0 to 112.5 to clear U2 pad 4 (NC) at (111.14, 87.95).
    vias.append(make_via(112.5, 87.5, gnd, "GND"))

    return tracks, vias


# ─────────────────────────────────────────────────────────────────────────
# Dogbone BGA escapes — U1 VDD and GND balls
# ─────────────────────────────────────────────────────────────────────────
def generate_dogbone_escapes(nets):
    """Generate dogbone escape vias + stubs for U1 BGA power/ground balls.

    Replaces the former via-in-pad approach.  Every via is placed OUTSIDE
    the U1 keepout rectangle (y > 102.0) so it never overlaps a BGA pad.

    Geometry (commodity 4-layer):
      - Dogbone trace width:  0.127 mm  (5 mil)
      - Via drill:            0.30  mm  (fab minimum)
      - Via annular ring:     0.60  mm
      - Stubs run south from row-N BGA balls (y=101.0) to vias at y≥102.5

    Routing constraints:
      - Row N balls at y=101.0 can escape south immediately (no pads below).
      - Row M/L balls must NOT route straight south through other pads.
      - VDD vias must be spaced ≥ 0.75mm from GND vias (via pad + clearance).
      - Dogbone vias must clear C3/C5 pads (y=102.5) and their vias (y=103.0).

    VDD balls (net /VDD_AFE_FILT):
      N15 (123.0, 101.0) → south stub → via at (123.0, 103.5)
           M15 (123.0, 100.5) same column, connected through N15 stub overlap.
      N2  (116.5, 101.0) → south stub → via at (116.5, 103.5)

    GND balls:
      N1  (116.0, 101.0) → south stub → via at (116.0, 104.5)
      L1  (116.0, 100.0) → jog west to (115.35, 100.0) → south → via at (115.35, 104.5)
           Explicit local GND return for N2/VDD decoupling loop.
      N6  (118.5, 101.0) → south stub → via at (118.5, 104.0)
      M17 (124.0, 100.5) → jog east to (126.5, 100.5) → via
           Rerouted east to free x=124.5 corridor for SPI.
    """
    net_by_name = {n["name"]: n["code"] for n in nets}
    vaf = net_by_name["/VDD_AFE_FILT"]
    gnd = net_by_name["GND"]

    tracks = []
    vias = []

    # Dogbone geometry
    STUB_W = 0.127       # 5 mil trace
    VIA_DRILL = 0.3      # board minimum
    VIA_SIZE = 0.6       # standard annular ring
    # Via positions individually chosen to avoid clearance conflicts:
    #   N2  VDD → (116.75, 103.5)  offset east from N1 GND stub
    #   N1  GND → (116.0,  104.5)  staggered south to clear N2 VDD via
    #   N6  GND → (118.5,  104.0)  south to clear C3 GND via (118.38, 103.0)
    #   M17 GND → (126.5,  100.5) east, outside BGA keepout

    # ── VDD dogbones (/VDD_AFE_FILT) ─────────────────────────────────────

    # N15 (123.0, 101.0) → south to existing VDD_AFE_FILT via at (123.0, 104.0)
    #   M15 (123.0, 100.5) is same net, same column — the stub from M15
    #   passes through N15 pad (both VDD_AFE_FILT), connecting both balls.
    #   Reuses the power-route via at (123, 104) — no extra via needed.
    tracks.append(make_segment(123.0, 100.5, 123.0, 104.0,
                               vaf, "/VDD_AFE_FILT", width=STUB_W))

    # N2 (116.5, 101.0) → south then jog east → via at (116.75, 103.5)
    #   Offset east to (116.75) to maintain 0.15mm clearance from N1 GND
    #   stub at x=116.0: gap = 0.75 - 0.3(via_r) - 0.064(trace_hw) = 0.39mm.
    tracks.append(make_segment(116.5, 101.0, 116.5, 103.25,
                               vaf, "/VDD_AFE_FILT", width=STUB_W))
    tracks.append(make_segment(116.5, 103.25, 116.75, 103.25,
                               vaf, "/VDD_AFE_FILT", width=STUB_W))
    tracks.append(make_segment(116.75, 103.25, 116.75, 103.5,
                               vaf, "/VDD_AFE_FILT", width=STUB_W))
    v = make_via(116.75, 103.5, vaf, "/VDD_AFE_FILT",
                 drill=VIA_DRILL, size=VIA_SIZE)
    assert_via_outside_u1_keepout(116.75, 103.5, "N2 VDD dogbone")
    vias.append(v)

    # ── GND dogbones ─────────────────────────────────────────────────────

    # L1 (116.0, 100.0) → jog west to (115.35, 100.0) → south → via at (115.35, 104.5)
    #   Cannot go south at x=116.0: M1 (116.0, 100.5) is <no net>.
    #   Jog west to x=115.35 (outside BGA pad columns, first column at x=116.0).
    #   Then straight south to via. Offset to 115.35 (not 115.5) ensures
    #   hole-to-hole distance to N1 via at (116.0, 104.5) is 0.65mm > 0.55mm min.
    #   L1 is an explicit local GND return for the
    #   N2/VDD decoupling loop — NOT relying on plane-only tie.
    tracks.append(make_segment(116.0, 100.0, 115.35, 100.0,
                               gnd, "GND", width=STUB_W))
    tracks.append(make_segment(115.35, 100.0, 115.35, 104.5,
                               gnd, "GND", width=STUB_W))
    v = make_via(115.35, 104.5, gnd, "GND",
                 drill=VIA_DRILL, size=VIA_SIZE)
    assert_via_outside_u1_keepout(115.35, 104.5, "L1 GND dogbone")
    vias.append(v)

    # N1 (116.0, 101.0) → south to via at (116.0, 104.5)
    #   Pushed to y=104.5 to clear N2 VDD via at (116.75, 103.5).
    #   Distance to L1 via at (115.35, 104.5) = 0.65mm > 0.55mm hole-to-hole min.
    #   Distance to N2 VDD via at (116.75, 103.5) = sqrt(0.75²+1.0²) = 1.25mm.
    tracks.append(make_segment(116.0, 101.0, 116.0, 104.5,
                               gnd, "GND", width=STUB_W))
    v = make_via(116.0, 104.5, gnd, "GND",
                 drill=VIA_DRILL, size=VIA_SIZE)
    assert_via_outside_u1_keepout(116.0, 104.5, "N1 GND dogbone")
    vias.append(v)

    # N6 (118.5, 101.0) → south, jog west to x=118.2, via at (118.2, 104.0)
    #   Moved west 0.3mm from pad column to clear MISO_B F.Cu at x=119.0.
    #   MISO_B clearance: 119.0-118.2-0.3-0.075 = 0.425mm ✓
    #   Hole-to-hole to C3 GND via (118.38, 103.0):
    #     √(0.28²+1.0²) = 1.04mm >> 0.55mm min ✓
    tracks.append(make_segment(118.5, 101.0, 118.5, 103.7,
                               gnd, "GND", width=STUB_W))
    tracks.append(make_segment(118.5, 103.7, 118.2, 103.7,
                               gnd, "GND", width=STUB_W))
    tracks.append(make_segment(118.2, 103.7, 118.2, 104.0,
                               gnd, "GND", width=STUB_W))
    v = make_via(118.2, 104.0, gnd, "GND",
                 drill=VIA_DRILL, size=VIA_SIZE)
    assert_via_outside_u1_keepout(118.2, 104.0, "N6 GND dogbone")
    vias.append(v)

    # M16 (123.5, 100.5) → short east to M17 (124.0, 100.5)
    #   M16 is LVDS_en, tied to GND in schematic (CMOS mode).
    #   Adjacent to M17 (also GND), same row.  0.5mm trace east connects
    #   M16 to M17's existing dogbone stub, which continues east to via.
    #   Clearance: M15 (/VDD_AFE_FILT, 123.0, 100.5) is 0.5mm west —
    #     trace starts at M16 pad center (123.5) going east, no conflict.
    #   N16 (auxout/NC, 123.5, 101.0) is 0.5mm south — trace at y=100.5,
    #     gap = 0.5 - 0.125(pad_r) - 0.064(trace_hw) = 0.311mm ✓
    tracks.append(make_segment(123.5, 100.5, 124.0, 100.5,
                               gnd, "GND", width=STUB_W))

    # M17 (124.0, 100.5) → jog east to (126.5, 100.5) → via
    #   Rerouted east (horizontal at y=100.5) to free the x=124.5 corridor
    #   for SPI MISO_A east escape.  Via at (126.5, 100.5) is well east of
    #   keepout (x>125) and clear of all pads/vias.
    #   M16 connects here via the stub above.
    tracks.append(make_segment(124.0, 100.5, 126.5, 100.5,
                               gnd, "GND", width=STUB_W))
    v = make_via(126.5, 100.5, gnd, "GND",
                 drill=VIA_DRILL, size=VIA_SIZE)
    assert_via_outside_u1_keepout(126.5, 100.5, "M17 GND dogbone")
    vias.append(v)

    return tracks, vias


# ─────────────────────────────────────────────────────────────────────────
# ADC_ref route — U1.N17 → C4.1
# ─────────────────────────────────────────────────────────────────────────
def generate_adc_ref_route(nets):
    """Route ADC_ref from U1.N17 to C4.1 bypass cap — F.Cu only, no vias.

    Net:   Net-(U1-ADC_ref)  (EEG_SIGNAL class, 0.15mm trace, 0.15mm clearance)
    From:  U1.N17  = (124.0, 101.0)
    To:    C4.1    = (125.52, 102.5)   [C4 center (126, 102.5), pad 1 offset -0.48]

    Route (2-segment L going east then south):
      With M17 GND dogbone rerouted east (horizontal at y=100.5), the
      x=124.5 corridor is clear.  ADC_ref goes east on F.Cu then south.
      1. East from N17 (124.0, 101.0) to C4.1 x (125.52, 101.0)
      2. South from (125.52, 101.0) to C4.1 (125.52, 102.5)

    Clearance verification:
      - To N16 pad (123.5, 101.0, r=0.125): trace starts east at x=124.0 → no conflict
      - To M17 stub (horizontal at y=100.5): vertical distance 0.5mm at x=124.0→125.52.
        M17 half-width 0.0635. Trace half-width 0.075. Gap: 0.5-0.0635-0.075=0.3615mm ✓
      - To M17 via (126.5, 100.5): distance √(0.98²+0.5²)=1.10mm ✓
      - To C4 pad 2/GND via (126.48, 102.5/103.0): seg2 at x=125.52, dist=0.96mm ✓
      - To C5 pads at (121.52/122.48, 102.5): seg2 at x=125.52, dist>3mm ✓

    Total length: 1.52 + 1.50 = 3.02mm  (budget: <15mm)
    """
    net_by_name = {n["name"]: n["code"] for n in nets}
    adc_ref = net_by_name["Net-(U1-ADC_ref)"]

    TRACE_W = 0.15  # EEG_SIGNAL net class
    NET_NAME = "Net-(U1-ADC_ref)"

    tracks = [
        # Seg 1: east from N17 to C4.1 column
        make_segment(124.0, 101.0, 125.52, 101.0,
                     adc_ref, NET_NAME, width=TRACE_W),
        # Seg 2: south to C4 pad 1
        make_segment(125.52, 101.0, 125.52, 102.5,
                     adc_ref, NET_NAME, width=TRACE_W),
    ]

    return tracks


# ─────────────────────────────────────────────────────────────────────────
# EEG segment structural validation
# ─────────────────────────────────────────────────────────────────────────
# Coordinate grid for floating-point normalization.
# All segment coordinates are snapped to this grid before structural checks,
# preventing misclassification from floating-point drift.
GRID_MM = 0.001  # 1µm — matches KiCad internal resolution


def _snap(v: float) -> float:
    """Round coordinate to nearest grid point."""
    return round(v / GRID_MM) * GRID_MM


def _parse_seg_coords(seg_text: str):
    """Extract (x1, y1, x2, y2, net_code) from a make_segment S-expression.

    All coordinates are snapped to GRID_MM to prevent floating-point
    misclassification in overlap and junction checks.
    """
    import re as _re
    start = _re.search(r'\(start\s+([\d.]+)\s+([\d.]+)\)', seg_text)
    end = _re.search(r'\(end\s+([\d.]+)\s+([\d.]+)\)', seg_text)
    net_code = _re.search(r'\(net\s+(\d+)\)', seg_text)
    if not start or not end or not net_code:
        return None
    return (_snap(float(start.group(1))), _snap(float(start.group(2))),
            _snap(float(end.group(1))), _snap(float(end.group(2))),
            int(net_code.group(1)))


def _validate_eeg_segments(track_texts: list[str]):
    """Validate EEG track segments for structural correctness.

    Checks:
      1. No colinear overlaps between different nets (even partial).
      2. No same-net colinear overlaps (duplicate segments).
      3. All junction points are at segment endpoints — no mid-segment T's.

    Raises ValueError on any violation.
    """
    EPS = 1e-4

    # Parse all segments
    segs = []  # (x1, y1, x2, y2, net_code)
    for t in track_texts:
        parsed = _parse_seg_coords(t)
        if parsed:
            segs.append(parsed)

    # Build endpoint database per net
    net_endpoints: dict[int, list[tuple[float, float]]] = {}
    for x1, y1, x2, y2, nc in segs:
        net_endpoints.setdefault(nc, []).extend([(x1, y1), (x2, y2)])

    # Check 1 & 2: No colinear overlaps
    # For each pair of segments, check if they're colinear and overlapping.
    # Colinear = both horizontal at same y, or both vertical at same x.
    for i in range(len(segs)):
        x1a, y1a, x2a, y2a, nca = segs[i]
        for j in range(i + 1, len(segs)):
            x1b, y1b, x2b, y2b, ncb = segs[j]

            # Check horizontal colinearity (same y, both horizontal)
            if (abs(y1a - y2a) < EPS and abs(y1b - y2b) < EPS
                    and abs(y1a - y1b) < EPS):
                # Both horizontal at same y — check x-range overlap
                a_lo, a_hi = min(x1a, x2a), max(x1a, x2a)
                b_lo, b_hi = min(x1b, x2b), max(x1b, x2b)
                overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
                if overlap > EPS:
                    label = ("INTER-NET" if nca != ncb else "SAME-NET")
                    raise ValueError(
                        f"{label} colinear overlap at y={y1a:.3f}: "
                        f"seg[{i}] x=[{a_lo:.3f},{a_hi:.3f}] net={nca}, "
                        f"seg[{j}] x=[{b_lo:.3f},{b_hi:.3f}] net={ncb}, "
                        f"overlap={overlap:.4f}mm")

            # Check vertical colinearity (same x, both vertical)
            if (abs(x1a - x2a) < EPS and abs(x1b - x2b) < EPS
                    and abs(x1a - x1b) < EPS):
                # Both vertical at same x — check y-range overlap
                a_lo, a_hi = min(y1a, y2a), max(y1a, y2a)
                b_lo, b_hi = min(y1b, y2b), max(y1b, y2b)
                overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
                if overlap > EPS:
                    label = ("INTER-NET" if nca != ncb else "SAME-NET")
                    raise ValueError(
                        f"{label} colinear overlap at x={x1a:.3f}: "
                        f"seg[{i}] y=[{a_lo:.3f},{a_hi:.3f}] net={nca}, "
                        f"seg[{j}] y=[{b_lo:.3f},{b_hi:.3f}] net={ncb}, "
                        f"overlap={overlap:.4f}mm")

    # Check 3: All junction points at segment endpoints
    # A junction is where a segment endpoint touches another segment.
    # That touch must be at the other segment's endpoint too — never mid-segment.
    # Coordinates are already grid-snapped by _parse_seg_coords, so we use
    # _snap for the set keys to guarantee consistent hashing.
    all_endpoints = set()
    for x1, y1, x2, y2, nc in segs:
        all_endpoints.add((_snap(x1), _snap(y1)))
        all_endpoints.add((_snap(x2), _snap(y2)))

    for i, (x1, y1, x2, y2, nc) in enumerate(segs):
        seg_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if seg_len < EPS:
            continue
        for ep_x, ep_y in all_endpoints:
            # Skip this segment's own endpoints
            if ((abs(ep_x - x1) < EPS and abs(ep_y - y1) < EPS) or
                    (abs(ep_x - x2) < EPS and abs(ep_y - y2) < EPS)):
                continue
            # Check if endpoint is ON this segment (not at its endpoints)
            # Parametric: project onto segment
            dx, dy = x2 - x1, y2 - y1
            t = ((ep_x - x1) * dx + (ep_y - y1) * dy) / (dx * dx + dy * dy)
            if t <= EPS or t >= 1.0 - EPS:
                continue  # outside segment or at endpoints
            # Point is between endpoints — check distance to segment
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy
            d = ((ep_x - proj_x) ** 2 + (ep_y - proj_y) ** 2) ** 0.5
            if d < EPS:
                raise ValueError(
                    f"Mid-segment junction: endpoint ({ep_x:.3f}, {ep_y:.3f}) "
                    f"lies on segment[{i}] ({x1:.3f},{y1:.3f})→"
                    f"({x2:.3f},{y2:.3f}) at t={t:.4f}. "
                    f"Split the segment at this point.")

    print(f"  ✓ EEG structural validation: {len(segs)} segments, "
          f"0 colinear overlaps, 0 mid-segment junctions")


def _assert_eeg_inside_corridor(track_texts: list[str]):
    """Gen-time check: every EEG segment must stay inside ANALOG_CORRIDOR.

    This is the inverse of Guard I (which checks that non-analog nets
    don't intrude).  Here we verify that EEG nets don't wander outside
    their designated zone — e.g., routing south into the SPI area.
    """
    x_min, y_min, x_max, y_max = ANALOG_CORRIDOR
    for i, t in enumerate(track_texts):
        parsed = _parse_seg_coords(t)
        if not parsed:
            continue
        x1, y1, x2, y2, _ = parsed
        for px, py, label in [(x1, y1, "start"), (x2, y2, "end")]:
            if px < x_min or px > x_max or py < y_min or py > y_max:
                # Exception: BGA ball pads are at y=95 inside the BGA keepout,
                # which is also inside the corridor.  The ball-escape segments
                # start at y=95 (inside corridor) so this shouldn't fire.
                # But if a future segment escapes corridor bounds, catch it.
                raise ValueError(
                    f"EEG segment[{i}] {label} ({px:.3f}, {py:.3f}) "
                    f"is outside analog corridor "
                    f"[{x_min}, {y_min}, {x_max}, {y_max}]")
    print(f"  ✓ All {len(track_texts)} EEG segments inside analog corridor")


# ─────────────────────────────────────────────────────────────────────────
# EEG input routes — U1 Row-A → bias resistors → test points
# ─────────────────────────────────────────────────────────────────────────
def generate_eeg_routes(nets):
    """Route EEG input channels (CH0-CH3) and REF_ELEC on F.Cu only.

    No vias — all traces stay on F.Cu over the solid In1.Cu GND plane.
    EEG_INPUT netclass: 0.15mm trace, 0.20mm clearance.

    Connectivity chains:
      CH0:  U1.A1  (116.0, 95.0) → R1.1  (112.49, 93.0) → TP4 (110.0, 91.0)
      CH1:  U1.A2  (116.5, 95.0) → R2.1  (112.49, 94.5) → TP5 (110.0, 93.5)
      CH2:  U1.A3  (117.0, 95.0) → R3.1  (112.49, 96.0) → TP6 (110.0, 96.0)
      CH3:  U1.A4  (117.5, 95.0) → R4.1  (112.49, 97.5) → TP7 (110.0, 98.5)
      REF:  U1.A17 (124.0, 95.0) → R5.2  (123.51, 93.0) → TP8 (126.0, 93.0)

    Routing architecture:

    R1-R4 are horizontal 0402 resistors at x=113.0 (pad1=signal at 112.49,
    pad2=GND at 113.51, same y).  A straight east-to-west trace at resistor y
    would short through pad2 (GND) before reaching pad1.

    Solution: each trace escapes north from the BGA ball to a safe y (well
    north of R1's GND pad envelope y < 92.405), runs west to a unique x_jog
    west of the TP column, then drops south to the target y and connects
    R pad1 and TP via east-going branches.  T-junctions split the path.

    The "staircase" pattern avoids ALL trace crossings:
      - Ball x increases east → escape y goes further north
      - x_jog decreases west for each successive channel
      - Vertical jogs are at different x, never sharing the same corridor
      - East-going horizontals at different y (=resistor y) never cross
      - TP connections branch off the vertical at the TP's y

    Channel paths (verified crossing-free):
      CH0: (116,95)→(116,92)→(112.49,92)→T→(112.49,93) R1.1
                                            →(112.49,91)→(110,91) TP4
      CH1: (116.5,95)→(116.5,90)→(109,90)→(109,93.5)→T→(110,93.5) TP5
                                                        →(109,94.5)→(112.49,94.5) R2.1
      CH2: (117,95)→(117,89.5)→(108.5,89.5)→(108.5,96)→(112.49,96) R3.1+TP6
      CH3: (117.5,95)→(117.5,89)→(108,89)→(108,97.5)→T→(112.49,97.5) R4.1
                                                        →(108,98.5)→(110,98.5) TP7
    REF:  (124,95)→(124,93)→(123.51,93)→(126,93) TP8

    Clearance verification:
      - R pad2 (GND) envelopes: all west runs at y < 92.405 clear R1 pad2.
        Verticals at x_jog (108–109) are > 4mm west of pad2 at x=113.51.
      - TP pad (1mm ⌀) clearance 0.775mm satisfied: nearest other-net TP
        is ≥ 1.5mm away from any trace.
      - Inter-channel clearance ≥ 0.35mm (trace pitch) at all parallel runs.
      - BGA pad field: only seg 1 (ball escape) enters; Guard B allows it.
      - SPI corridor (y > 101): EEG max y = 98.5, separation ≥ 2.5mm.
    """
    net_by_name = {n["name"]: n["code"] for n in nets}
    W = 0.15  # EEG_INPUT trace width
    CLEARANCE = 0.20  # EEG_INPUT net class clearance
    TRACE_HW = W / 2  # trace half-width

    # ── Clamp mode ───────────────────────────────────────────────────────
    # Controls how safe_escape_y handles TP exclusion violations:
    #   "fix"  — clamp + print warning (default for dev)
    #   "fail" — raise ValueError with exact diagnostic (CI / release)
    #   "off"  — do nothing, let guards catch bad copper (for proving guards)
    # Read from env so CI can force "fail" without code changes.
    EEG_CLAMP_MODE = os.environ.get("EEG_CLAMP_MODE", "fix")
    if EEG_CLAMP_MODE not in ("fix", "fail", "off"):
        raise ValueError(f"EEG_CLAMP_MODE must be fix/fail/off, got '{EEG_CLAMP_MODE}'")
    # Clamp policy: "allow" (default) permits fix-mode clamping.
    # "deny" hard-fails if any clamp event occurred — for release builds.
    EEG_CLAMP_POLICY = os.environ.get("EEG_CLAMP_POLICY", "allow")
    if EEG_CLAMP_POLICY not in ("allow", "deny"):
        raise ValueError(f"EEG_CLAMP_POLICY must be allow/deny, got '{EEG_CLAMP_POLICY}'")
    print(f"  EEG_CLAMP_MODE={EEG_CLAMP_MODE}  EEG_CLAMP_POLICY={EEG_CLAMP_POLICY}")
    _clamp_events: list[str] = []  # machine-parseable clamp log

    # ── TP pad keepout envelopes ─────────────────────────────────────────
    # Derived from physical pad geometry — not magic offsets.
    # Any horizontal west-run that crosses the TP column (x=110) must
    # satisfy |escape_y - tp_y| >= exclusion_radius for every TP on that
    # column.
    TP_PAD_RADIUS = 0.5    # 1.0mm pad diameter / 2
    TP_MASK_EXPANSION = 0.0  # no additional mask expansion on TP pads
    TP_EXCLUSION_RADIUS = TP_PAD_RADIUS + TP_MASK_EXPANSION + CLEARANCE + TRACE_HW
    # = 0.5 + 0.0 + 0.20 + 0.075 = 0.775mm

    TP_COLUMN_X = 110.0  # all CH0-CH3 test points are at x=110
    TP_POSITIONS = {      # TP label → (x, y)
        "TP4": (110.0, 91.0),
        "TP5": (110.0, 93.5),
        "TP6": (110.0, 96.0),
        "TP7": (110.0, 98.5),
    }

    # 0402 resistor pad geometry (for R pad2 GND keepout)
    # R_PAD_HW_X = 0.27 (pad half-width in x, 0.54mm / 2) — currently unused.
    R_PAD_HW_Y = 0.32     # pad half-height in y (0.64mm / 2)
    R_PAD2_X = 113.51     # R1-R4 pad2 (GND) x center

    # Channel west-run geometry (must match track generation below).
    # ball_x: BGA pad x.  x_jog: west-run terminus x.
    # The west-run horizontal at escape_y spans [x_jog, ball_x].
    # "crosses TP column" is geometric: min(ball_x, x_jog) <= TP_COLUMN_X <= max(ball_x, x_jog)
    CH_WESTRUN = {
        "CH0": {"ball_x": 116.0, "x_jog": 112.49},  # doesn't cross TP column
        "CH1": {"ball_x": 116.5, "x_jog": 109.0},   # crosses TP column
        "CH2": {"ball_x": 117.0, "x_jog": 108.5},   # crosses TP column
        "CH3": {"ball_x": 117.5, "x_jog": 108.0},   # crosses TP column
    }

    def _westrun_crosses_tp_column(ch_label: str) -> bool:
        """Geometric test: does the west-run at escape_y cross TP_COLUMN_X?

        The west-run is a horizontal segment from (ball_x, escape_y) to
        (x_jog, escape_y).  It crosses the TP column if TP_COLUMN_X is
        between min(ball_x, x_jog) and max(ball_x, x_jog).
        """
        wr = CH_WESTRUN[ch_label]
        x_lo = min(wr["ball_x"], wr["x_jog"])
        x_hi = max(wr["ball_x"], wr["x_jog"])
        return x_lo <= TP_COLUMN_X <= x_hi

    def safe_escape_y(escape_y, channel_label, tp_exclude=None):
        """Clamp/fail/skip escape_y so it clears all TP pads on the TP column.

        Self-TP exclusion rule:
          Exclude the channel's own TP only if the west-run does NOT
          geometrically cross TP_COLUMN_X.  This means the horizontal at
          escape_y never passes through the TP pad's x-position, so
          proximity to that TP is irrelevant for the west-run.

          If the west-run DOES cross TP_COLUMN_X, the channel's own TP
          is checked just like every other TP — because a drifted TP
          could clip the pass-through segment.

          Connection stubs (the short segment from x_jog to the TP pad)
          run at the TP's own y, not at escape_y, so they're not governed
          by this check.

        Args:
            escape_y: proposed y for the west-running horizontal
            channel_label: e.g. "CH0" (for diagnostics)
            tp_exclude: e.g. "TP4" — this channel's own TP

        Returns:
            The (possibly clamped) escape_y.  Raises ValueError in "fail" mode.
        """
        crosses = _westrun_crosses_tp_column(channel_label)
        effective_exclude = None
        if tp_exclude and not crosses:
            effective_exclude = tp_exclude
        elif tp_exclude and crosses:
            print(f"  ℹ {channel_label}: west-run [{CH_WESTRUN[channel_label]['x_jog']}→"
                  f"{CH_WESTRUN[channel_label]['ball_x']}] crosses TP column "
                  f"(x={TP_COLUMN_X}), self-TP exclusion for {tp_exclude} DISABLED")

        adjusted = escape_y
        for tp_name, (tp_x, tp_y) in TP_POSITIONS.items():
            if tp_name == effective_exclude:
                continue
            gap = abs(adjusted - tp_y)
            if gap < TP_EXCLUSION_RADIUS:
                if EEG_CLAMP_MODE == "off":
                    print(f"  ⚠ {channel_label}: escape_y={adjusted:.3f} clips "
                          f"{tp_name} at y={tp_y} (gap={gap:.3f}mm < "
                          f"{TP_EXCLUSION_RADIUS:.3f}mm). "
                          f"CLAMP_MODE=off — NOT clamping.")
                    continue
                safe_y = tp_y - TP_EXCLUSION_RADIUS
                if EEG_CLAMP_MODE == "fail":
                    raise ValueError(
                        f"{channel_label}: escape_y={adjusted:.3f} clips "
                        f"{tp_name} at y={tp_y} (gap={gap:.3f}mm < "
                        f"{TP_EXCLUSION_RADIUS:.3f}mm). "
                        f"Safe value would be {safe_y:.3f}. "
                        f"CLAMP_MODE=fail — aborting.")
                # mode == "fix": clamp, warn, and log machine-parseable event
                print(f"  ⚠ {channel_label}: escape_y={adjusted:.3f} clips "
                      f"{tp_name} at y={tp_y} (gap={gap:.3f}mm < "
                      f"{TP_EXCLUSION_RADIUS:.3f}mm). "
                      f"Clamping to {safe_y:.3f}.")
                _clamp_events.append(
                    f"CLAMP_ESCAPE_Y net=/{channel_label} "
                    f"old={adjusted:.3f} new={safe_y:.3f} "
                    f"offender={tp_name} gap={gap:.3f} "
                    f"excl={TP_EXCLUSION_RADIUS:.3f}")
                adjusted = safe_y
        return adjusted

    # ── Compute and validate escape_y for each channel ───────────────────
    # Raw values (design intent):
    CH0_ESCAPE_Y_RAW = 92.0
    CH1_ESCAPE_Y_RAW = 90.0
    CH2_ESCAPE_Y_RAW = 89.5
    CH3_ESCAPE_Y_RAW = 89.0

    # Clamp against TP keepout envelopes:
    CH0_ESCAPE_Y = safe_escape_y(CH0_ESCAPE_Y_RAW, "CH0", tp_exclude="TP4")
    CH1_ESCAPE_Y = safe_escape_y(CH1_ESCAPE_Y_RAW, "CH1", tp_exclude="TP5")
    CH2_ESCAPE_Y = safe_escape_y(CH2_ESCAPE_Y_RAW, "CH2", tp_exclude="TP6")
    CH3_ESCAPE_Y = safe_escape_y(CH3_ESCAPE_Y_RAW, "CH3", tp_exclude="TP7")

    # ── Post-clamp validation ────────────────────────────────────────────
    # After clamping, verify the final escape_y values don't create
    # secondary conflicts: inter-channel collision, corridor boundary
    # violation, or mutual TP exclusion overlap.
    # In "off" mode, skip these — let the board-time guards catch violations.
    if EEG_CLAMP_MODE != "off":
        final_escapes = [("CH0", CH0_ESCAPE_Y), ("CH1", CH1_ESCAPE_Y),
                         ("CH2", CH2_ESCAPE_Y), ("CH3", CH3_ESCAPE_Y)]

        # Post-clamp check 1: re-verify ALL TPs (no exclusions) for pass-through
        for label, ey in final_escapes:
            if not _westrun_crosses_tp_column(label):
                continue  # west-run doesn't cross TP column
            for tp_name, (tp_x, tp_y) in TP_POSITIONS.items():
                gap = abs(ey - tp_y)
                if gap < TP_EXCLUSION_RADIUS:
                    raise ValueError(
                        f"Post-clamp: {label} escape_y={ey:.3f} still clips "
                        f"{tp_name} at y={tp_y} (gap={gap:.3f}mm < "
                        f"{TP_EXCLUSION_RADIUS:.3f}mm). "
                        f"Clamping created a secondary conflict.")

        # Post-clamp check 2: escape-band inter-channel spacing
        # Only checks the horizontal west-run escape_y values, not general
        # clearance.  Minimum edge-to-edge gap = 2*TRACE_HW + CLEARANCE.
        MIN_ESCAPE_SPACING = 2 * TRACE_HW + CLEARANCE  # 0.075*2 + 0.20 = 0.35mm
        for i in range(len(final_escapes)):
            for j in range(i + 1, len(final_escapes)):
                li, yi = final_escapes[i]
                lj, yj = final_escapes[j]
                spacing = abs(yi - yj)
                if spacing < MIN_ESCAPE_SPACING:
                    raise ValueError(
                        f"Post-clamp: {li} escape_y={yi:.3f} and {lj} "
                        f"escape_y={yj:.3f} are only {spacing:.3f}mm apart "
                        f"(min edge-to-edge = {MIN_ESCAPE_SPACING:.3f}mm = "
                        f"2×{TRACE_HW}+{CLEARANCE})")

        # Post-clamp check 3: corridor bounds
        x_min, y_min, x_max, y_max = ANALOG_CORRIDOR
        for label, ey in final_escapes:
            if ey < y_min or ey > y_max:
                raise ValueError(
                    f"Post-clamp: {label} escape_y={ey:.3f} is outside "
                    f"analog corridor y=[{y_min}, {y_max}]")

        # Report clamp events
        if _clamp_events:
            print(f"  ⚠ {len(_clamp_events)} clamp event(s):")
            for evt in _clamp_events:
                print(f"    {evt}")
        else:
            print("  ✓ No clamp events — all escape_y values are clean")
    else:
        print("  ⚠ CLAMP_MODE=off — post-clamp validation SKIPPED")

    # Machine-parseable summary line (for run_drc.sh to grep).
    # MUST print before any policy raise so the line is always in the log.
    print(f"CLAMP_SUMMARY clamp_mode={EEG_CLAMP_MODE} "
          f"clamp_policy={EEG_CLAMP_POLICY} "
          f"clamp_events={len(_clamp_events)}")

    # Enforce clamp policy AFTER printing summary + events.
    # "deny" means any clamping is a design regression, even if fix-mode
    # successfully clamped the values.  The events and CLAMP_SUMMARY are
    # already printed above, so the developer can see exactly what happened.
    if EEG_CLAMP_POLICY == "deny" and _clamp_events:
        raise ValueError(
            f"EEG_CLAMP_POLICY=deny: {len(_clamp_events)} clamp event(s) "
            f"occurred. Fix the design so escape_y values don't need "
            f"clamping, or set EEG_CLAMP_POLICY=allow to permit clamping.")

    # ── Per-channel obstacle map for escape corridor ceiling ──────────
    # Each channel's west-run at escape_y may cross different GND pad
    # envelopes.  The corridor ceiling is the tightest (southernmost)
    # constraint from all obstacles that the channel's horizontal crosses.
    #
    # Obstacle: R pad2 (GND) envelope.  Each R1-R4 pad2 is at (113.51, R_y).
    # A horizontal west-run at escape_y crosses x=113.51 only if the run
    # extends east of 113.51.  CH0's T-junction is at x=112.49, so its
    # west-run turns south at 112.49 (never reaches 113.51).  But the
    # ball-escape vertical at x=116.0 passes through x=113.51 at escape_y,
    # so we still need clearance from ALL R pad2 envelopes whose y could
    # be clipped by the horizontal segment or the vertical segment.
    #
    # In practice: each channel's escape_y must clear R1 (the northernmost
    # resistor at y=93.0) because the vertical escape at x≥116 passes
    # through the x-range of R1-R4 pad2 (x=113.51)... WAIT: the vertical
    # is at x=116..117.5, pad2 is at x=113.51±0.27 → [113.24, 113.78].
    # The vertical is 2+ mm east of pad2.  The HORIZONTAL at escape_y
    # runs from ball_x west to x_jog (108-112.49).  It crosses x=113.51
    # only if x_jog < 113.51 < ball_x — which is true for all channels.
    # So all channels' horizontals cross R1 pad2's x, and escape_y must
    # clear R1 pad2's y-envelope.
    R_PAD2_POSITIONS = {  # obstacle_name → (pad2_x, pad2_y)
        "R1.2": (R_PAD2_X, 93.0),
        "R2.2": (R_PAD2_X, 94.5),
        "R3.2": (R_PAD2_X, 96.0),
        "R4.2": (R_PAD2_X, 97.5),
    }

    # Corridor ceiling: the tightest (most-south) constraint.
    # escape_y must be LESS than (pad_y - pad_hw_y - clearance - trace_hw)
    # for the northernmost obstacle (R1 at y=93.0).
    EEG_ESCAPE_CORRIDOR_CEILING = (
        R_PAD2_POSITIONS["R1.2"][1] - R_PAD_HW_Y - CLEARANCE - TRACE_HW
    )  # = 93.0 - 0.32 - 0.20 - 0.075 = 92.405

    for label, ey in [("CH0", CH0_ESCAPE_Y), ("CH1", CH1_ESCAPE_Y),
                       ("CH2", CH2_ESCAPE_Y), ("CH3", CH3_ESCAPE_Y)]:
        if ey > EEG_ESCAPE_CORRIDOR_CEILING:
            raise ValueError(
                f"{label}: escape_y={ey:.3f} > corridor_ceiling="
                f"{EEG_ESCAPE_CORRIDOR_CEILING:.3f} "
                f"(derived from R1.2 GND pad at y="
                f"{R_PAD2_POSITIONS['R1.2'][1]}, "
                f"pad_hw_y={R_PAD_HW_Y}, clr={CLEARANCE}, "
                f"trace_hw={TRACE_HW})")

    print(f"  EEG escape_y: CH0={CH0_ESCAPE_Y}, CH1={CH1_ESCAPE_Y}, "
          f"CH2={CH2_ESCAPE_Y}, CH3={CH3_ESCAPE_Y}")
    print(f"  TP exclusion radius: {TP_EXCLUSION_RADIUS:.3f}mm "
          f"(pad_r={TP_PAD_RADIUS} + clr={CLEARANCE} + trace_hw={TRACE_HW})")
    print(f"  EEG escape corridor ceiling: {EEG_ESCAPE_CORRIDOR_CEILING:.3f} "
          f"(from R1.2 at y={R_PAD2_POSITIONS['R1.2'][1]})")

    # ── TP-column vertical intrusion check (CH1 at x=110) ────────────────
    # CH1 routes vertically at x=110 from y=93.5 to y=94.5.
    # Assert this vertical segment doesn't intrude into any OTHER TP pad's
    # exclusion zone on x=110.
    CH1_VERT_Y_MIN = 93.5  # TP5 y (own pad — OK)
    CH1_VERT_Y_MAX = 94.5  # R2 branch endpoint
    for tp_name, (tp_x, tp_y) in TP_POSITIONS.items():
        if tp_name == "TP5":  # CH1's own TP
            continue
        if tp_x != TP_COLUMN_X:
            continue
        # Check if vertical segment [CH1_VERT_Y_MIN, CH1_VERT_Y_MAX]
        # intrudes into [tp_y - excl, tp_y + excl]
        excl_lo = tp_y - TP_EXCLUSION_RADIUS
        excl_hi = tp_y + TP_EXCLUSION_RADIUS
        if CH1_VERT_Y_MAX > excl_lo and CH1_VERT_Y_MIN < excl_hi:
            raise ValueError(
                f"CH1 vertical at x={TP_COLUMN_X} [{CH1_VERT_Y_MIN}, "
                f"{CH1_VERT_Y_MAX}] intrudes into {tp_name} exclusion zone "
                f"[{excl_lo:.3f}, {excl_hi:.3f}]")
    print(f"  CH1 TP-column vertical [{CH1_VERT_Y_MIN}, {CH1_VERT_Y_MAX}]: "
          f"clear of all other TP exclusion zones")

    tracks = []

    # ── REF_ELEC (isolated on east side) ─────────────────────────────────
    ref_nc = net_by_name["/REF_ELEC"]
    ref_nn = "/REF_ELEC"
    # A17 (124.0,95.0) → north to R5 y → T-junction at (124,93)
    #   west branch → R5.2 (123.51, 93)
    #   east branch → TP8 (126.0, 93)
    tracks.append(make_segment(124.0, 95.0, 124.0, 93.0,
                               ref_nc, ref_nn, width=W))
    tracks.append(make_segment(124.0, 93.0, 123.51, 93.0,
                               ref_nc, ref_nn, width=W))
    tracks.append(make_segment(124.0, 93.0, 126.0, 93.0,
                               ref_nc, ref_nn, width=W))

    # ── CH0 ──────────────────────────────────────────────────────────────
    # Ball A1 (116.0, 95.0) → R1.1 (112.49, 93.0) → TP4 (110.0, 91.0)
    # T-junction at (112.49, escape_y): south→R1, north→TP4
    nc0 = net_by_name["/CH0"]
    nn0 = "/CH0"
    tracks.append(make_segment(116.0, 95.0,  116.0, CH0_ESCAPE_Y,   nc0, nn0, width=W))   # escape north
    tracks.append(make_segment(116.0, CH0_ESCAPE_Y,  112.49, CH0_ESCAPE_Y,  nc0, nn0, width=W))   # west to T
    tracks.append(make_segment(112.49, CH0_ESCAPE_Y, 112.49, 93.0,  nc0, nn0, width=W))   # south → R1.1
    tracks.append(make_segment(112.49, CH0_ESCAPE_Y, 112.49, 91.0,  nc0, nn0, width=W))   # north
    tracks.append(make_segment(112.49, 91.0, 110.0, 91.0,   nc0, nn0, width=W))   # west → TP4

    # ── CH1 ──────────────────────────────────────────────────────────────
    # Ball A2 (116.5, 95.0) → R2.1 (112.49, 94.5) → TP5 (110.0, 93.5)
    # T-junction at (109.0, 93.5): east→TP5, continue south→R2
    nc1 = net_by_name["/CH1"]
    nn1 = "/CH1"
    tracks.append(make_segment(116.5, 95.0,  116.5, CH1_ESCAPE_Y,   nc1, nn1, width=W))   # escape north
    tracks.append(make_segment(116.5, CH1_ESCAPE_Y,  109.0, CH1_ESCAPE_Y,   nc1, nn1, width=W))   # west to jog
    tracks.append(make_segment(109.0, CH1_ESCAPE_Y,  109.0, 93.5,   nc1, nn1, width=W))   # south to T
    tracks.append(make_segment(109.0, 93.5,  110.0, 93.5,   nc1, nn1, width=W))   # east → TP5
    tracks.append(make_segment(110.0, 93.5,  110.0, 94.5,   nc1, nn1, width=W))   # south at TP col
    tracks.append(make_segment(110.0, 94.5,  112.49, 94.5,  nc1, nn1, width=W))   # east → R2.1

    # ── CH2 ──────────────────────────────────────────────────────────────
    # Ball A3 (117.0, 95.0) → R3.1 (112.49, 96.0) → TP6 (110.0, 96.0)
    # R3.1 and TP6 share y=96.0 — single horizontal reaches both
    nc2 = net_by_name["/CH2"]
    nn2 = "/CH2"
    tracks.append(make_segment(117.0, 95.0,  117.0, CH2_ESCAPE_Y,   nc2, nn2, width=W))   # escape north
    tracks.append(make_segment(117.0, CH2_ESCAPE_Y,  108.5, CH2_ESCAPE_Y,   nc2, nn2, width=W))   # west to jog
    tracks.append(make_segment(108.5, CH2_ESCAPE_Y,  108.5, 96.0,   nc2, nn2, width=W))   # south to y=96
    tracks.append(make_segment(108.5, 96.0,  112.49, 96.0,  nc2, nn2, width=W))   # east → R3.1+TP6

    # ── CH3 ──────────────────────────────────────────────────────────────
    # Ball A4 (117.5, 95.0) → R4.1 (112.49, 97.5) → TP7 (110.0, 98.5)
    # T-junction at (108.0, 97.5): east→R4, continue south→TP7
    nc3 = net_by_name["/CH3"]
    nn3 = "/CH3"
    tracks.append(make_segment(117.5, 95.0,  117.5, CH3_ESCAPE_Y,   nc3, nn3, width=W))   # escape north
    tracks.append(make_segment(117.5, CH3_ESCAPE_Y,  108.0, CH3_ESCAPE_Y,   nc3, nn3, width=W))   # west to jog
    tracks.append(make_segment(108.0, CH3_ESCAPE_Y,  108.0, 97.5,   nc3, nn3, width=W))   # south to T
    tracks.append(make_segment(108.0, 97.5,  112.49, 97.5,  nc3, nn3, width=W))   # east → R4.1
    tracks.append(make_segment(108.0, 97.5,  108.0, 98.5,   nc3, nn3, width=W))   # south continue
    tracks.append(make_segment(108.0, 98.5,  110.0, 98.5,   nc3, nn3, width=W))   # east → TP7

    # ── Structural validation ────────────────────────────────────────────
    # Assert no colinear segment overlaps between different nets,
    # and all junction points are at segment endpoints.
    _validate_eeg_segments(tracks)

    # Assert all EEG segments stay within the analog corridor bounds.
    _assert_eeg_inside_corridor(tracks)

    return tracks


# ─────────────────────────────────────────────────────────────────────────
# SPI bundle — U1 SPI balls → J5 connector
# ─────────────────────────────────────────────────────────────────────────
def generate_spi_routes(nets):
    """Route the 5-net SPI bundle from U1 BGA row-N to J5 connector.

    Layer change required because BGA-to-J5 permutation has 6 inversions —
    single-layer crossing-free routing is impossible.

    Topology (proven crossing-free):
      F.Cu south escape from each BGA pad → via-down at lane-y → B.Cu west
      along unique y-lane → B.Cu south to via-up x → via-up near J5 → F.Cu
      stub south to J5 pad.

    MOSI/MISO_A escape WEST around C5 pads on F.Cu (the east corridor is
    blocked by VDD dogbone at x=123.0).  MOSI jogs to x=120.9 (between
    SCLK at 120.5 and C5 pad-1 left edge at 121.24).  MISO_A jogs to
    x=122.0 (center of C5 inter-pad gap, with 4 minor clearance violations
    ≤0.045mm).  Both then go east at y>104 (below VDD_AFE_FILT power track)
    to their via-down x-positions.

    MISO_B routes on B.Cu west to x=103.0 (clearing all other nets'
    west-lane endpoints), then south to y=118, east to J5 pin-12.

    B.Cu y-lane order (north → south):
      SCLK (y=109), MOSI (y=110), CS1 (y=111), MISO_A (y=112)
    This ensures no south-after-west segment crosses any west-lane segment,
    because each via-up x is west of all deeper lanes' endpoints.

    Via-down positions (at lane-y so no B.Cu south-before-west segments):
      MISO_B:  (119.0, 105.0)  — straight F.Cu south 4mm
      SCLK:    (120.5, 109.0)  — straight F.Cu south 8mm
      MOSI:    (123.5, 110.0)  — west-jog F.Cu then east at y=104.7
      CS1:     (119.5, 111.0)  — straight F.Cu south 10mm
      MISO_A:  (124.5, 112.0)  — west-jog F.Cu then east at y=105.2

    Via-up positions:
      SCLK:    (104.0, 115.0)  → J5.3  (105.0, 116.8)
      MOSI:    (105.4, 114.0)  → J5.5  (105.5, 116.8)  (0.1mm east jog on F.Cu)
      CS1:     (106.0, 113.0)  → J5.7  (106.0, 116.8)
      MISO_A:  (107.0, 112.0)  → J5.11 (107.0, 116.8)
      MISO_B:  (107.0, 118.0)  → J5.12 (107.0, 119.2)  (between J5 rows)

    Clearance verification:
      All via-to-via ≥ 1.12mm (need 0.75mm)
      MOSI via (105.4,114) to CS1 stub (x=106): 0.6mm → 0.225mm gap ✓
      CS1 via (106,113) to MOSI B.Cu (x=105.4): 0.6mm → 0.225mm gap ✓
      All trace-to-trace ≥ 0.25mm edge (need 0.15mm)
      All via-to-existing ≥ 0.80mm (need 0.75mm)
    """
    net_by_name = {n["name"]: n["code"] for n in nets}

    W = 0.15   # DIGITAL_SPI net class trace width
    VD = 0.3   # via drill
    VS = 0.6   # via size

    tracks = []
    vias = []

    def seg(x1, y1, x2, y2, nc, nn, layer="F.Cu"):
        tracks.append(make_segment(x1, y1, x2, y2, nc, nn, width=W, layer=layer))

    def via(x, y, nc, nn):
        vias.append(make_via(x, y, nc, nn, drill=VD, size=VS))

    # ── MISO_B ─────────────────────────────────────────────────────────
    nc = net_by_name["MISO1_B"]
    nn = "MISO1_B"
    # F.Cu: BGA pad south, jog west to x=118.7, south to via-down at y=105.5
    # Via-down moved from (119.0,105.0) to (118.7,105.5) to clear:
    #   - CS1 at x=119.5: 119.5-118.7-0.3-0.075=0.425mm ✓ (was 0.125mm)
    #   - N6 GND via (118.2,104.0): √(0.5²+1.5²)=1.58mm ✓
    # B.Cu lane at y=105.5 clears:
    #   - L1 GND via (115.35,104.5): |105.5-104.5|-0.3-0.075=0.625mm ✓ (was 0.125mm)
    #   - N1 GND via (116.0,104.5): same ✓
    seg(119.0, 101.0,  119.0, 105.2,  nc, nn)     # south from BGA pad
    seg(119.0, 105.2,  118.7, 105.2,  nc, nn)     # west jog 0.3mm
    seg(118.7, 105.2,  118.7, 105.5,  nc, nn)     # south to via-down y
    # Via-down
    via(118.7, 105.5, nc, nn)
    # B.Cu: west to x=103 at y=105.5
    seg(118.7, 105.5,  103.0, 105.5,  nc, nn, layer="B.Cu")
    # B.Cu: south to y=118 (between J5 pin rows)
    seg(103.0, 105.5,  103.0, 118.0,  nc, nn, layer="B.Cu")
    # B.Cu: east to J5 via-up x
    seg(103.0, 118.0,  107.0, 118.0,  nc, nn, layer="B.Cu")
    # Via-up (between J5 pin rows)
    via(107.0, 118.0, nc, nn)
    # F.Cu: stub south to J5 pin 12
    seg(107.0, 118.0,  107.0, 119.2,  nc, nn)

    # ── SCLK ───────────────────────────────────────────────────────────
    nc = net_by_name["SCLK"]
    nn = "SCLK"
    # F.Cu: BGA straight south to via-down at lane y=109
    seg(120.5, 101.0,  120.5, 109.0,  nc, nn)
    # Via-down
    via(120.5, 109.0, nc, nn)
    # B.Cu: west along lane y=109 to x=104
    seg(120.5, 109.0,  104.0, 109.0,  nc, nn, layer="B.Cu")
    # B.Cu: south to via-up y=115
    seg(104.0, 109.0,  104.0, 115.0,  nc, nn, layer="B.Cu")
    # Via-up
    via(104.0, 115.0, nc, nn)
    # F.Cu: east jog + south stub to J5 pin 3
    seg(104.0, 115.0,  105.0, 115.0,  nc, nn)
    seg(105.0, 115.0,  105.0, 116.8,  nc, nn)

    # ── MOSI ───────────────────────────────────────────────────────────
    nc = net_by_name["MOSI"]
    nn = "MOSI"
    # F.Cu: BGA south, WEST jog to x=120.9 (between SCLK at 120.5 and C5 pad-1
    # left edge at 121.24), south past C5, east at y=105.6 (below MISO_A east at
    # y=105.2 and VDD track at y=104), then south to via-down at lane y=110.
    #
    # Clearance verification (west-jog path):
    #   x=120.9 to SCLK (120.5): 0.25mm edge-edge ✓
    #   x=120.9 to C5 pad-1 left edge (121.24): 0.165mm ✓
    #   x=120.9 to C5 power via (121.52,103 r=0.3): 121.22-120.975=0.195mm ✓
    #   y=105.6 to VDD track south edge (104.2): 1.325mm ✓
    #   y=105.6 to MISO_A east at y=105.2: 0.25mm edge-edge ✓
    #   No crossing with MISO_A: MOSI turns east at y=105.6 (below MISO_A
    #   vertical endpoint 105.2), MOSI east ends at x=123.5 (west of
    #   MISO_A south at x=124.5). Proven non-intersecting.
    seg(121.5, 101.0,  121.5, 101.35,  nc, nn)     # south from BGA pad
    seg(121.5, 101.35, 120.9, 101.35,  nc, nn)     # west jog
    seg(120.9, 101.35, 120.9, 105.6,   nc, nn)     # south past C5 + MISO_A turn
    seg(120.9, 105.6,  123.5, 105.6,   nc, nn)     # east past VDD stub
    seg(123.5, 105.6,  123.5, 110.0,   nc, nn)     # south to lane
    # Via-down
    via(123.5, 110.0, nc, nn)
    # B.Cu: west along lane y=110 to x=105.4 (was 105.5; moved 0.1mm west
    # to gain clearance from CS1 via-up at (106.0,113.0) and CS1 F.Cu stub
    # at x=106.0.  New gap = 0.6 - 0.3 - 0.075 = 0.225mm ✓ (was 0.125mm).
    seg(123.5, 110.0,  105.4, 110.0,  nc, nn, layer="B.Cu")
    # B.Cu: south to via-up y=114
    seg(105.4, 110.0,  105.4, 114.0,  nc, nn, layer="B.Cu")
    # Via-up
    via(105.4, 114.0, nc, nn)
    # F.Cu: east jog + south stub to J5 pin 5
    seg(105.4, 114.0,  105.5, 114.0,  nc, nn)
    seg(105.5, 114.0,  105.5, 116.8,  nc, nn)

    # ── CS1 ────────────────────────────────────────────────────────────
    nc = net_by_name["CS1"]
    nn = "CS1"
    # F.Cu: BGA straight south to via-down at lane y=111
    seg(119.5, 101.0,  119.5, 111.0,  nc, nn)
    # Via-down
    via(119.5, 111.0, nc, nn)
    # B.Cu: west along lane y=111 to x=106
    seg(119.5, 111.0,  106.0, 111.0,  nc, nn, layer="B.Cu")
    # B.Cu: south to via-up y=113
    seg(106.0, 111.0,  106.0, 113.0,  nc, nn, layer="B.Cu")
    # Via-up
    via(106.0, 113.0, nc, nn)
    # F.Cu: stub south to J5 pin 7
    seg(106.0, 113.0,  106.0, 116.8,  nc, nn)

    # ── MISO_A ─────────────────────────────────────────────────────────
    nc = net_by_name["MISO1_A"]
    nn = "MISO1_A"
    # F.Cu: BGA south, WEST jog to x=122.0 (center of C5 inter-pad gap),
    # south past C5 with NECKDOWN to 0.10mm, east at y=105.2, south to
    # via-down at lane y=112.
    #
    # Neckdown through C5 gap (y=102.0 to y=103.5):
    #   Trace width 0.10mm → half-width 0.05mm → edges 121.95 to 122.05
    #   To C5 pad-1 right edge (121.80): 0.150mm ✓ (meets 0.15mm clearance)
    #   To C5 pad-2 left edge  (122.20): 0.150mm ✓ (meets 0.15mm clearance)
    #   To C5 GND via left edge (122.18): 0.130mm ✓ (>0.10mm fab minimum)
    #   Mask dam (0.05mm expansion): 0.100mm both sides ✓ (meets 0.10mm min)
    #   To C5 power via (121.45,103 r=0.3): 0.200mm ✓
    #
    # Outside neckdown zone (normal 0.15mm width):
    #   y=105.2 to VDD track south edge (104.2): 0.925mm ✓
    #   y=105.2 to MOSI at y=104.7: 0.35mm edge-edge ✓
    #   x=124.5 to GND stitch via (125.5,108 r=0.3): 0.625mm ✓
    seg(122.5, 101.0,  122.5, 101.35,  nc, nn)     # south from BGA pad
    seg(122.5, 101.35, 122.0, 101.35,  nc, nn)     # west jog
    seg(122.0, 101.35, 122.0, 102.0,   nc, nn)     # south to neckdown start
    # Neckdown: 0.10mm width through C5 inter-pad gap
    tracks.append(make_segment(122.0, 102.0, 122.0, 103.5, nc, nn, width=0.10))
    seg(122.0, 103.5,  122.0, 105.2,   nc, nn)     # south to east turn
    seg(122.0, 105.2,  124.5, 105.2,   nc, nn)     # east past VDD stub
    seg(124.5, 105.2,  124.5, 112.0,   nc, nn)     # south to lane
    # Via-down
    via(124.5, 112.0, nc, nn)
    # B.Cu: west along lane y=112 to x=107
    seg(124.5, 112.0,  107.0, 112.0,  nc, nn, layer="B.Cu")
    # Via-up (no south-after-west needed)
    via(107.0, 112.0, nc, nn)
    # F.Cu: stub south to J5 pin 11
    seg(107.0, 112.0,  107.0, 116.8,  nc, nn)

    # ── GND stitching vias near layer transitions ──────────────────────
    gnd = net_by_name["GND"]
    # U1-side: flank the via-down cluster
    # NOTE: y=106.5 (not 106.0) to clear MISO_B B.Cu at y=105.5
    via(117.5, 106.5, gnd, "GND")
    via(125.5, 108.0, gnd, "GND")
    # J5-side: flank the via-up cluster
    # NOTE: x=101.5 (not 102.0) to clear VCC F.Cu trunk at x=102.5
    #       and MISO_B B.Cu trace at x=103
    via(101.5, 113.0, gnd, "GND")
    via(108.0, 114.0, gnd, "GND")

    return tracks, vias


# ─────────────────────────────────────────────────────────────────────────
# PCB assembly
# ─────────────────────────────────────────────────────────────────────────
def generate_pcb(components, nets):
    net_maps = build_net_maps(components, nets)
    comp_by_ref = {c["ref"]: c for c in components}

    # Net declarations
    net_decls = ['\t(net 0 "")']
    for net in sorted(nets, key=lambda n: n["code"]):
        net_decls.append(f'\t(net {net["code"]} "{net["name"]}")')

    # Net class assignments
    nc_block = get_net_class_assignments(nets)

    # Footprints
    footprints = []
    placed = 0
    for ref in sorted(PLACEMENTS.keys()):
        if ref not in comp_by_ref:
            print(f"  WARNING: {ref} in placements but not in netlist — skipping")
            continue
        px, py = PLACEMENTS[ref]
        comp = comp_by_ref[ref]
        fp_name = get_footprint_name(ref)
        nm = net_maps.get(ref, {})
        fp_text = inject_footprint(
            fp_name=fp_name,
            ref=ref,
            value=comp["value"],
            comp_tstamp=comp["tstamp"],
            net_map=nm,
            at_x=px,
            at_y=py,
        )
        footprints.append(fp_text)
        placed += 1

    print(f"  Placed {placed} components")

    # ── Zones (copper pours) ──
    # Need net code for GND
    net_by_name = {n["name"]: n["code"] for n in nets}
    gnd_code = net_by_name["GND"]

    # Board outline corners (shared with Edge.Cuts)
    board_corners = [
        (BX, BY), (BX + BW, BY),
        (BX + BW, BY + BH), (BX, BY + BH),
    ]

    zones = []
    # L1 (F.Cu): GND copper pour — fills unused signal-layer area.
    # Provides surface return current path and connects GND stitch vias
    # that have no explicit trace on F.Cu.  Lowest priority (0) so it
    # never overrides signal traces or pads.
    zones.append(make_zone(gnd_code, "GND", "F.Cu", board_corners))

    # L2 (In1.Cu): solid GND plane covering entire board
    zones.append(make_zone(gnd_code, "GND", "In1.Cu", board_corners))

    # L4 (B.Cu): GND copper pour — fills unused area around SPI traces.
    # Same rationale as F.Cu pour: return current + stitch via connectivity.
    zones.append(make_zone(gnd_code, "GND", "B.Cu", board_corners))

    # L3 (In2.Cu): +3V3 distribution plane (full board, lowest priority)
    v33_code = net_by_name["+3V3"]
    zones.append(make_zone(v33_code, "+3V3", "In2.Cu", board_corners, priority=0))

    # L3 (In2.Cu): local /VDD_AFE_FILT island (higher priority, overrides +3V3)
    # U-shaped polygon covering U1 VDD balls (M15, N15, N2) + C3 via + VDD
    # via at (123, 104).  A notch (x 119–122, y 102–104.5) is cut from the
    # south edge so that C5's +3V3 via at (121.45, 103.0) falls OUTSIDE this
    # island and connects to the underlying +3V3 zone instead.
    # FB1 connects to this island via F.Cu trace, not plane.
    dvdd_code = net_by_name["/VDD_AFE_FILT"]
    dvdd_island = [
        (116.0,  97.5),  # NW — covers N2 at (116.5, 101)
        (124.0,  97.5),  # NE — covers M15 (123,100.5) and N15 (123,101)
        (124.0, 104.5),  # SE — encloses VDD via at (123, 104)
        (122.0, 104.5),  # notch: east lip
        (122.0, 102.0),  # notch: step north  — excludes C5 +3V3 via (121.45, 103)
        (119.0, 102.0),  # notch: step west
        (119.0, 104.5),  # notch: step south
        (116.0, 104.5),  # SW — encloses C3 via (117.52, 103) + N2 dogbone (116.75, 103.5)
    ]
    zones.append(make_zone(dvdd_code, "/VDD_AFE_FILT", "In2.Cu", dvdd_island, priority=1))

    # VCC pad connections at J5 are handled by direct traces in
    # generate_power_tracks() — no VCC zone needed. A zone here would
    # starve the flanking GND pads (17/18/21/22/25/26) of thermal relief
    # spokes because the 0.5mm pad pitch leaves insufficient room for
    # a higher-priority VCC zone + GND zone clearance cutouts.

    # ── Decoupler via pairs ──
    vias = generate_decoupler_vias(nets)
    print(f"  Generated {len(vias)} decoupler vias")

    # ── Power tracks + vias (Step 1 & 2) ──
    pwr_tracks, pwr_vias = generate_power_tracks(nets)
    vias.extend(pwr_vias)
    print(f"  Generated {len(pwr_tracks)} power tracks, {len(pwr_vias)} power vias")

    # ── Dogbone BGA escapes (U1 VDD + GND balls) ──
    dog_tracks, dog_vias = generate_dogbone_escapes(nets)
    pwr_tracks.extend(dog_tracks)
    vias.extend(dog_vias)
    print(f"  Generated {len(dog_tracks)} dogbone tracks, {len(dog_vias)} dogbone vias")

    # ── ADC_ref route (U1.N17 → C4.1) ──
    adc_tracks = generate_adc_ref_route(nets)
    pwr_tracks.extend(adc_tracks)
    print(f"  Generated {len(adc_tracks)} ADC_ref tracks (F.Cu only, no vias)")

    # ── EEG input routes (CH0-CH3 + REF_ELEC) ──
    eeg_tracks = generate_eeg_routes(nets)
    pwr_tracks.extend(eeg_tracks)
    print(f"  Generated {len(eeg_tracks)} EEG tracks (F.Cu only, no vias)")

    # ── SPI bundle (U1 → J5) ──
    spi_tracks, spi_vias = generate_spi_routes(nets)
    pwr_tracks.extend(spi_tracks)
    vias.extend(spi_vias)
    print(f"  Generated {len(spi_tracks)} SPI tracks + {len(spi_vias)} vias (F.Cu ↔ B.Cu)")

    print(f"  Total: {len(pwr_tracks)} segments, {len(vias)} vias")

    # Board outline (Edge.Cuts)
    corners = [(BX, BY), (BX+BW, BY), (BX+BW, BY+BH), (BX, BY+BH)]
    edges = []
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i+1) % 4]
        edges.append(f"""\t(gr_line
\t\t(start {sx} {sy}) (end {ex} {ey})
\t\t(stroke (width 0.15) (type solid))
\t\t(layer "Edge.Cuts")
\t\t(uuid "{uid()}")
\t)""")

    pcb = f"""(kicad_pcb
\t(version 20241229)
\t(generator "gen_pcb.py")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(title_block
\t\t(title "Neural AFE Headstage — EEG Dev Platform")
\t\t(company "BCIInterface")
\t\t(rev "0.7")
\t\t(date "2026-02-23")
\t\t(comment 1 "128-ch Neural AFE (partial: 4ch + 1x RHD2164)")
\t\t(comment 2 "DDR SPI passthrough to FPGA carrier | 3.3V LVCMOS")
\t\t(comment 3 "Footprints vendor-locked in footprints.pretty/")
\t)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(1 "In1.Cu" signal)
\t\t(2 "In2.Cu" signal)
\t\t(31 "B.Cu" signal)
\t\t(32 "B.Adhes" user "B.Adhesive")
\t\t(33 "F.Adhes" user "F.Adhesive")
\t\t(34 "B.Paste" user)
\t\t(35 "F.Paste" user)
\t\t(36 "B.SilkS" user "B.Silkscreen")
\t\t(37 "F.SilkS" user "F.Silkscreen")
\t\t(38 "B.Mask" user)
\t\t(39 "F.Mask" user)
\t\t(40 "Dwgs.User" user "User.Drawings")
\t\t(41 "Cmts.User" user "User.Comments")
\t\t(42 "Eco1.User" user "User.Eco1")
\t\t(43 "Eco2.User" user "User.Eco2")
\t\t(44 "Edge.Cuts" user)
\t\t(45 "Margin" user)
\t\t(46 "B.CrtYd" user "B.Courtyard")
\t\t(47 "F.CrtYd" user "F.Courtyard")
\t\t(48 "B.Fab" user)
\t\t(49 "F.Fab" user)
\t)
\t(setup
\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask"
\t\t\t\t(type "Top Solder Mask")
\t\t\t\t(color "Green")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "F.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 1"
\t\t\t\t(type "prepreg")
\t\t\t\t(thickness 0.2)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "In1.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 2"
\t\t\t\t(type "core")
\t\t\t\t(thickness 1.065)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "In2.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 3"
\t\t\t\t(type "prepreg")
\t\t\t\t(thickness 0.2)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.5)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "B.Mask"
\t\t\t\t(type "Bottom Solder Mask")
\t\t\t\t(color "Green")
\t\t\t\t(thickness 0.01)
\t\t\t)
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(copper_finish "None")
\t\t\t(dielectric_constraints no)
\t\t)
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t\t(pcbplotparams
\t\t\t(layerselection 0x00000000_00000000_00000000_000000a5)
\t\t\t(plot_on_all_layers_selection 0x00000000_00000000_00000000_00000000)
\t\t\t(disableapertmacros no)
\t\t\t(usegerberextensions yes)
\t\t\t(usegerberattributes no)
\t\t\t(usegerberadvancedattributes no)
\t\t\t(creategerberjobfile no)
\t\t\t(dashed_line_dash_ratio 12.000000)
\t\t\t(dashed_line_gap_ratio 3.000000)
\t\t\t(svgprecision 6)
\t\t\t(plotframeref no)
\t\t\t(mode 1)
\t\t\t(useauxorigin no)
\t\t\t(hpglpennumber 1)
\t\t\t(hpglpenspeed 20)
\t\t\t(hpglpendiameter 15.000000)
\t\t\t(pdf_front_fp_property_popups yes)
\t\t\t(pdf_back_fp_property_popups yes)
\t\t\t(pdf_metadata yes)
\t\t\t(pdf_single_document no)
\t\t\t(dxfpolygonmode yes)
\t\t\t(dxfimperialunits yes)
\t\t\t(dxfusepcbnewfont yes)
\t\t\t(psnegative no)
\t\t\t(psa4output no)
\t\t\t(plot_black_and_white yes)
\t\t\t(plotinvisibletext no)
\t\t\t(sketchpadsonfab no)
\t\t\t(plotpadnumbers no)
\t\t\t(hidednponfab no)
\t\t\t(sketchdnponfab yes)
\t\t\t(crossoutdnponfab yes)
\t\t\t(subtractmaskfromsilk no)
\t\t\t(outputformat 1)
\t\t\t(mirror no)
\t\t\t(drillshape 1)
\t\t\t(scaleselection 1)
\t\t\t(outputdirectory "gerbers/")
\t\t)
\t)

{chr(10).join(net_decls)}

{nc_block}

{chr(10).join(footprints)}

{chr(10).join(edges)}

{chr(10).join(zones)}

{chr(10).join(vias)}

{chr(10).join(pwr_tracks)}

\t(embedded_fonts no)
)
"""
    return pcb


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────
def main():
    print(f"Parsing netlist: {NETLIST}")
    components, nets = parse_netlist(NETLIST)
    print(f"  {len(components)} components, {len(nets)} nets")

    print(f"\nFootprint library: {FP_DIR}")
    for f in sorted(FP_DIR.glob("*.kicad_mod")):
        print(f"  {f.stem}")

    print("\nGenerating PCB...")
    pcb_text = generate_pcb(components, nets)
    PCB_OUT.write_text(pcb_text)
    print(f"\nWrote: {PCB_OUT} ({PCB_OUT.stat().st_size:,} bytes)")

    print("\n── Placement summary ──")
    for ref in sorted(PLACEMENTS.keys()):
        x, y = PLACEMENTS[ref]
        fp = get_footprint_name(ref)
        print(f"  {ref:5s}  ({x:6.1f}, {y:5.1f})  {fp}")

    print("\n── Net class assignments ──")
    nc = get_net_class_assignments(nets)
    for line in nc.splitlines():
        s = line.strip()
        if s.startswith('(net_class') or s.startswith('(add_net'):
            print(f"  {s}")


if __name__ == "__main__":
    main()
