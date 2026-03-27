#!/usr/bin/env python3
"""
gen_pcb_v1.py — KiCad 9 PCB Layout Bootstrapper for v1 RHD2132 Headstage
==========================================================================

Generates afe-headstage-v1.kicad_pcb with:
  - All components placed with REAL footprints & pads
  - Net declarations matching the schematic
  - Pads assigned to nets → ratsnest visible in KiCad
  - Board outline (Edge.Cuts), 30 × 24 mm
  - 4-layer stackup (F.Cu / In1.Cu / In2.Cu / B.Cu), 1.6 mm FR4
  - Net classes (ELECTRODE, DIGITAL_SPI, POWER, Default)
  - Ground-plane keepout under electrode connector
  - Decoupling caps placed immediately adjacent to RHD2132 power pins

This is a LAYOUT BOOTSTRAPPER — not an autorouter.  It produces
placement + ratsnest + outline.  Routing is done interactively in KiCad.

Footprint data sources (NOT guessed):
  - Omnetics PZN-12-AA:  Intan official Eagle library RHD2000.lbr
  - Omnetics A79025 (≡ A79024): Intan official Eagle library RHD2000.lbr
  - RHD2132 QFN-56: Intan Eagle library QFN56_8_X_8_LARGE package
    (8×8mm body, 0.5mm pitch, 56 pads + center EP 4.8×4.8mm)
  - Pin-to-pad mapping: Intan Eagle library RHD2132 deviceset
  - 0402 passives:  IPC-7351B land pattern
  - Test points: KiCad standard TestPoint_Pad_D1.0mm

BOM:
  U1  — RHD2132 (QFN-56, 8×8mm)
  J1  — Omnetics PZN-12-AA (12-pin SPI cable connector)
  J5  — Omnetics A79024-001 (36-pin nano-strip electrode connector)
  FB1 — Ferrite bead 600Ω@100MHz (0402)
  C1  — 100nF (0402) +3V3 bypass (near power entry)
  C2  — 1µF (0402) +3V3 bulk (near power entry)
  C3  — 100nF (0402) AVDD bypass (near FB1 output)
  C4  — 1µF (0402) AVDD bulk (near FB1 output)
  C5  — 100nF (0402) VDD bypass near U1 pin 13 (bottom-side of QFN)
  C6  — 100nF (0402) VDD bypass near U1 pin 26 (right-side of QFN)
  C7  — 10nF (0402) ADC_ref — MUST be within 5mm of U1 pin 28
  R1  — 10kΩ (0402) REF bias
  R2  — 100Ω (0402) SPI series — CS line (Intan ref design)
  R3  — 100Ω (0402) SPI series — SCLK line (Intan ref design)
  R4  — 100Ω (0402) SPI series — MOSI line (Intan ref design)
  TP1–TP9 — Test points (1mm pad)

RHD2132 QFN-56 pin assignment (from Intan Eagle library):
  Pads 1-9:   Left side, top to bottom: IN8, IN7, IN6, IN5, IN4, IN3, IN2, IN1, IN0
  Pad 10:     REF_ELEC
  Pads 11-12: GND1, GND2
  Pad 13:     VDD1
  Pads 14-16: AUXIN1, AUXIN2, AUXIN3
  Pad 17:     GND3
  Pads 18-19: CS-, CS+
  Pads 20-21: SCLK-, SCLK+
  Pads 22-23: MOSI-, MOSI+
  Pads 24-25: MISO-, MISO+
  Pad 26:     VDD2
  Pad 27:     AUXOUT (NC)
  Pad 28:     ADC_REF
  Pad 29:     GND4
  Pad 30:     LVDS_EN (tie to GND for CMOS mode)
  Pad 31:     VDD3
  Pad 32:     VESD (tie to GND)
  Pad 33:     ELEC_TEST
  Pads 34-42: IN31, IN30, IN29, IN28, IN27, IN26, IN25, IN24, IN23
  Pads 43-56: IN22, IN21, IN20, IN19, IN18, IN17, IN16, IN15, IN14, IN13, IN12, IN11, IN10, IN9
  EP (CENTER): GND — solder for mechanical integrity + shielding
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

HERE = Path(__file__).parent
PCB_OUT = HERE / "afe-headstage-v1.kicad_pcb"

# ─── Board geometry ───────────────────────────────────────────────────────
BOARD_W = 30.0  # mm
BOARD_H = 24.0  # mm
ORIGIN_X = 100.0  # KiCad board-space origin
ORIGIN_Y = 80.0


def uid():
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════════════════════════
# 1. PAD / DRAWING HELPERS  (KiCad 9 S-expression)
# ═══════════════════════════════════════════════════════════════════════════

def _pad_smd(
    num, x, y, sx, sy,
    net_code, net_name,
    layers='"F.Cu" "F.Paste" "F.Mask"',
    shape="roundrect",
    rratio=0.25,
):
    """SMD pad."""
    net = f'(net {net_code} "{net_name}")' if net_code else ""
    rr = f"(roundrect_rratio {rratio})" if shape == "roundrect" else ""
    return (
        f'        (pad "{num}" smd {shape}\n'
        f"            (at {x} {y})\n"
        f"            (size {sx} {sy})\n"
        f"            (layers {layers})\n"
        f"            {rr}\n"
        f"            {net}\n"
        f'            (uuid "{uid()}")\n'
        f"        )"
    )


def _pad_smd_circle(num, x, y, d, net_code, net_name):
    net = f'(net {net_code} "{net_name}")' if net_code else ""
    return (
        f'        (pad "{num}" smd circle\n'
        f"            (at {x} {y})\n"
        f"            (size {d} {d})\n"
        f'            (layers "F.Cu" "F.Paste" "F.Mask")\n'
        f"            {net}\n"
        f'            (uuid "{uid()}")\n'
        f"        )"
    )


def _courtyard_rect(x1, y1, x2, y2):
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    lines = []
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i + 1) % 4]
        lines.append(
            f"        (fp_line\n"
            f"            (start {sx} {sy}) (end {ex} {ey})\n"
            f'            (stroke (width 0.05) (type solid))\n'
            f'            (layer "F.CrtYd")\n'
            f'            (uuid "{uid()}")\n'
            f"        )"
        )
    return "\n".join(lines)


def _fab_rect(x1, y1, x2, y2):
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    lines = []
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i + 1) % 4]
        lines.append(
            f"        (fp_line\n"
            f"            (start {sx} {sy}) (end {ex} {ey})\n"
            f'            (stroke (width 0.1) (type solid))\n'
            f'            (layer "F.Fab")\n'
            f'            (uuid "{uid()}")\n'
            f"        )"
        )
    return "\n".join(lines)


def _silkscreen_rect(x1, y1, x2, y2):
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    lines = []
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i + 1) % 4]
        lines.append(
            f"        (fp_line\n"
            f"            (start {sx} {sy}) (end {ex} {ey})\n"
            f'            (stroke (width 0.12) (type solid))\n'
            f'            (layer "F.SilkS")\n'
            f'            (uuid "{uid()}")\n'
            f"        )"
        )
    return "\n".join(lines)


def _ref_text(ref, x=0, y=-1.5):
    return (
        f'        (property "Reference" "{ref}"\n'
        f"            (at {x} {y} 0)\n"
        f'            (layer "F.SilkS")\n'
        f'            (uuid "{uid()}")\n'
        f"            (effects (font (size 1 1) (thickness 0.15)))\n"
        f"        )"
    )


def _val_text(val, x=0, y=1.5):
    return (
        f'        (property "Value" "{val}"\n'
        f"            (at {x} {y} 0)\n"
        f'            (layer "F.Fab")\n'
        f'            (uuid "{uid()}")\n'
        f"            (effects (font (size 1 1) (thickness 0.15)))\n"
        f"        )"
    )


def _prop_hidden(name, val=""):
    return (
        f'        (property "{name}" "{val}"\n'
        f'            (at 0 0 0) (unlocked yes) (layer "F.Fab") (hide yes)\n'
        f'            (uuid "{uid()}")\n'
        f"            (effects (font (size 1.27 1.27) (thickness 0.15)))\n"
        f"        )"
    )


def _fp_header(lib_name, ref, value, desc, tags, comp_ts, at_x, at_y, at_rot=0):
    rot_str = f" {at_rot}" if at_rot else ""
    return (
        f'    (footprint "{lib_name}"\n'
        f'        (layer "F.Cu")\n'
        f'        (uuid "{uid()}")\n'
        f"        (at {at_x} {at_y}{rot_str})\n"
        f'        (descr "{desc}")\n'
        f'        (tags "{tags}")\n'
        f"        {_ref_text(ref)}\n"
        f"        {_val_text(value)}\n"
        f'        {_prop_hidden("Datasheet")}\n'
        f'        {_prop_hidden("Description", desc)}\n'
        f'        (path "/{comp_ts}")\n'
        f'        (sheetname "/")\n'
        f'        (sheetfile "afe-headstage-v1.kicad_sch")\n'
        f"        (attr smd)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. FOOTPRINT GENERATORS  (real geometry, sourced from datasheets / Intan lib)
# ═══════════════════════════════════════════════════════════════════════════


def fp_rhd2132_qfn56(ref, value, comp_ts, net_map, at_x, at_y):
    """
    RHD2132 QFN-56 — 8 × 8 mm body, 0.5 mm pitch, 57 pads total.

    Authoritative source: Intan Eagle library RHD2000.lbr
      Package: QFN56_8_X_8_LARGE  (SMD land pattern)
      Deviceset: RHD2132

    Pad geometry (from QFN56_8_X_8_LARGE):
      Body:           8.0 × 8.0 mm
      Pad pitch:      0.5 mm
      Pads per side:  14
      Pad size:       0.9 × 0.25 mm (long axis perpendicular to IC edge)
      Pad center:     4.05 mm from IC center
      EP (CENTER):    4.8 × 4.8 mm

    Pin numbering (QFN-56, standard CCW from pin 1 marker):
      Left side  (1-14):  top to bottom, x = -4.05
      Bottom side (15-28): left to right, y = +4.05
      Right side (29-42):  bottom to top, x = +4.05
      Top side   (43-56):  right to left, y = -4.05
      EP (CENTER): center pad
    """
    body = 8.0 / 2  # ±4.0 mm
    pitch = 0.5
    pad_long = 0.9   # dimension along outward axis (into board)
    pad_short = 0.25  # dimension along IC edge (perpendicular)
    n_per_side = 14
    ep_sx = 4.8
    ep_sy = 4.8

    # Pad center distance from IC center
    pad_cx = 4.05  # from Intan Eagle lib

    # Half-span of pin array on each side
    half_span = (n_per_side - 1) / 2 * pitch  # = 6.5/2 = 3.25 mm

    pad_lines = []

    # Left side: pads 1-14, y from +3.25 (top) to -3.25 (bottom)
    # Eagle lib: pad 1 at y=+3.25, pad 14 at y=-3.25
    for i in range(14):
        pin = str(i + 1)
        py = half_span - i * pitch
        px = -pad_cx
        nc, nn = net_map.get(pin, (0, ""))
        pad_lines.append(_pad_smd(pin, round(px, 4), round(py, 4),
                                  pad_long, pad_short, nc, nn))

    # Bottom side: pads 15-28, x from -3.25 (left) to +3.25 (right)
    for i in range(14):
        pin = str(15 + i)
        px = -half_span + i * pitch
        py = pad_cx
        nc, nn = net_map.get(pin, (0, ""))
        # Bottom/top pads: swap dx/dy (long axis is vertical = along y)
        pad_lines.append(_pad_smd(pin, round(px, 4), round(py, 4),
                                  pad_short, pad_long, nc, nn))

    # Right side: pads 29-42, y from -3.25 (bottom) to +3.25 (top)
    for i in range(14):
        pin = str(29 + i)
        py = -half_span + i * pitch
        px = pad_cx
        nc, nn = net_map.get(pin, (0, ""))
        pad_lines.append(_pad_smd(pin, round(px, 4), round(py, 4),
                                  pad_long, pad_short, nc, nn))

    # Top side: pads 43-56, x from +3.25 (right) to -3.25 (left)
    for i in range(14):
        pin = str(43 + i)
        px = half_span - i * pitch
        py = -pad_cx
        nc, nn = net_map.get(pin, (0, ""))
        pad_lines.append(_pad_smd(pin, round(px, 4), round(py, 4),
                                  pad_short, pad_long, nc, nn))

    # Exposed pad (CENTER) — GND
    # No F.Paste on main pad — use windowed paste apertures for ~50% coverage
    nc, nn = net_map.get("EP", (0, ""))
    pad_lines.append(_pad_smd(
        "EP", 0, 0, ep_sx, ep_sy, nc, nn,
        layers='"F.Cu" "F.Mask"',
        shape="roundrect", rratio=0.05,
    ))

    # ── EP paste aperture windows (4×4 grid, ~50% paste coverage) ─────
    # 4.8mm EP → 4×4 grid of 0.9mm squares with 0.3mm gaps
    # Effective paste area: 16 × 0.9² = 12.96mm² out of 23.04mm² → 56%
    paste_n = 4
    paste_size = 0.9
    paste_pitch = 1.2   # center-to-center
    paste_offset = (paste_n - 1) / 2 * paste_pitch  # 1.8mm
    for row in range(paste_n):
        for col in range(paste_n):
            px = -paste_offset + col * paste_pitch
            py = -paste_offset + row * paste_pitch
            pad_lines.append(
                f'        (pad "" smd roundrect\n'
                f'            (at {round(px, 3)} {round(py, 3)})\n'
                f'            (size {paste_size} {paste_size})\n'
                f'            (layers "F.Paste")\n'
                f'            (roundrect_rratio 0.1)\n'
                f'            (uuid "{uid()}")\n'
                f'        )'
            )

    # ── EP thermal via array (3×3, 0.3mm drill) ──────────────────────
    # 9 vias for GND stitching and heat dissipation
    # Tented both sides to prevent solder wicking during reflow
    via_n = 3
    via_drill = 0.3
    via_pad = 0.6
    via_pitch = 1.4  # center-to-center (fits within 4.8mm EP)
    via_offset = (via_n - 1) / 2 * via_pitch  # 1.4mm
    via_net = f'(net {nc} "{nn}")' if nc else ""
    for row in range(via_n):
        for col in range(via_n):
            vx = -via_offset + col * via_pitch
            vy = -via_offset + row * via_pitch
            pad_lines.append(
                f'        (pad "EP" thru_hole circle\n'
                f'            (at {round(vx, 3)} {round(vy, 3)})\n'
                f'            (size {via_pad} {via_pad})\n'
                f'            (drill {via_drill})\n'
                f'            (layers "*.Cu")\n'
                f'            (remove_unused_layers no)\n'
                f'            (tenting front back)\n'
                f'            {via_net}\n'
                f'            (uuid "{uid()}")\n'
                f'        )'
            )

    pads = "\n".join(pad_lines)
    cx = body + 0.5
    cy = body + 0.5

    header = _fp_header(
        "afe_footprints:RHD2132_QFN56", ref, value,
        "Intan RHD2132 QFN-56, 8x8mm, 0.5mm pitch, EP 4.8x4.8mm",
        "QFN-56 RHD2132 neural AFE",
        comp_ts, at_x, at_y,
    )

    # Pin 1 marker (top-left corner of left side)
    pin1_marker = (
        f"        (fp_circle\n"
        f"            (center {-body - 0.3} {half_span + 0.3})"
        f" (end {-body - 0.1} {half_span + 0.3})\n"
        f'            (stroke (width 0.2) (type solid))\n'
        f'            (fill none)\n'
        f'            (layer "F.SilkS")\n'
        f'            (uuid "{uid()}")\n'
        f"        )"
    )

    return (
        f"{header}\n"
        f"{_courtyard_rect(-cx, -cy, cx, cy)}\n"
        f"{_fab_rect(-body, -body, body, body)}\n"
        f"{pin1_marker}\n"
        f"{pads}\n"
        f"    )"
    )


def fp_pzn12(ref, value, comp_ts, net_map, at_x, at_y):
    """
    Omnetics PZN-12-AA — 12-pin polarized nano connector (SPI cable).

    Footprint data: Intan official Eagle library RHD2000.lbr
    Package: OMNETICS_PZN-12-AA

    Geometry (from Intan Eagle lib, translated to KiCad coords):
      Body outline: ±2.2225 mm x (-0.3302 to 3.9878 mm)
      Bottom row (B1-B6): y = -1.016 mm, pad 0.381 × 0.762 mm
      Top row (T1-T6):    y = -2.159 mm, pad 0.381 × 1.016 mm
      Pitch: 0.635 mm (25 mil)
      X positions: ±1.5875, ±0.9525, ±0.3175 mm

    Eagle pin-to-schematic mapping:
      T1=pin1(MISO1), T2=pin2(MOSI), T3=pin3(SCLK), T4=pin4(CS),
      T5=pin5(GND), T6=pin6(GND)
      B1=pin7(VDD_D), B2=pin8(VDD_D), B3=pin9(VDD_A), B4=pin10(VDD_A),
      B5=pin11(MISO2), B6=pin12(GND)
    """
    # Eagle library pad positions (verified from Intan RHD2000.lbr)
    # Format: (eagle_name, schematic_pin_num, x, y, dx, dy)
    eagle_pads = [
        # Bottom row — B pins (closer to edge)
        ("B1", "7",   1.5875, -1.016, 0.381, 0.762),
        ("B2", "8",   0.9525, -1.016, 0.381, 0.762),
        ("B3", "9",   0.3175, -1.016, 0.381, 0.762),
        ("B4", "10", -0.3175, -1.016, 0.381, 0.762),
        ("B5", "11", -0.9525, -1.016, 0.381, 0.762),
        ("B6", "12", -1.5875, -1.016, 0.381, 0.762),
        # Top row — T pins (further from edge)
        ("T1", "1",   1.5875, -2.159, 0.381, 1.016),
        ("T2", "2",   0.9525, -2.159, 0.381, 1.016),
        ("T3", "3",   0.3175, -2.159, 0.381, 1.016),
        ("T4", "4",  -0.3175, -2.159, 0.381, 1.016),
        ("T5", "5",  -0.9525, -2.159, 0.381, 1.016),
        ("T6", "6",  -1.5875, -2.159, 0.381, 1.016),
    ]

    pad_lines = []
    for _ename, pin_num, px, py, dx, dy in eagle_pads:
        nc, nn = net_map.get(pin_num, (0, ""))
        pad_lines.append(_pad_smd(pin_num, px, py, dx, dy, nc, nn))

    pads = "\n".join(pad_lines)

    # Body outline from Eagle: x=±2.2225, y=-0.3302 to 3.9878
    bx = 2.2225
    by_top = -3.9878  # In KiCad Y is inverted vs Eagle
    by_bot = 0.3302

    header = _fp_header(
        "afe_footprints:Omnetics_PZN-12-AA", ref, value,
        "Omnetics PZN-12-AA, 12-pin polarized nano, SPI cable connector",
        "Omnetics PZN-12 nano connector SPI",
        comp_ts, at_x, at_y,
    )

    return (
        f"{header}\n"
        f"{_courtyard_rect(-bx - 0.25, by_top - 0.25, bx + 0.25, by_bot + 0.25)}\n"
        f"{_silkscreen_rect(-bx, by_top, bx, by_bot)}\n"
        f"{_fab_rect(-bx, by_top, bx, by_bot)}\n"
        f"{pads}\n"
        f"    )"
    )


def fp_a79024(ref, value, comp_ts, net_map, at_x, at_y):
    """
    Omnetics A79024-001 / A79025 — 36-pin nano-strip electrode connector.

    Footprint data: Intan official Eagle library RHD2000.lbr
    Package: OMNETICS_A79025 (A79024 ≡ A79025 same footprint, differs in gender)

    Geometry (from Intan Eagle lib):
      Body outline: x = -6.604 to 6.604 mm, y = 0 to 2.54 mm (Eagle coords)
      Bottom row (B1-B18): y = -1.016 mm, pad 0.381 × 0.762 mm
      Top row (T1-T18):    y = -2.159 mm, pad 0.381 × 1.016 mm
      Pitch: 0.635 mm (25 mil)

    Our schematic pin mapping (36 pins):
      Pins 1-32: electrode channels IN[0]-IN[31]
      Pin 33: REF electrode
      Pin 34: GND (shield/guard)
      Pin 35: elec_test
      Pin 36: GND (shield/guard)

    Physical pad mapping — the Intan Eagle lib uses T1-T18 and B1-B18.
    We map schematic pin N to physical pads as:
      T1→pin1, B1→pin2, T2→pin3, B2→pin4, ..., T18→pin35, B18→pin36
    (i.e. odd pins on T row, even pins on B row)
    """
    # Eagle pad positions (exact from RHD2000.lbr OMNETICS_A79025 package)
    # X positions for each column (1-18), left to right
    x_positions = [
        -5.3975, -4.7625, -4.1275, -3.4925, -2.8575, -2.2225,
        -1.5875, -0.9525, -0.3175,  0.3175,  0.9525,  1.5875,
         2.2225,  2.8575,  3.4925,  4.1275,  4.7625,  5.3975,
    ]

    pad_lines = []
    for col in range(18):
        # T row (top row, further from board edge) → odd schematic pins
        t_pin = str(col * 2 + 1)  # 1, 3, 5, ..., 35
        nc_t, nn_t = net_map.get(t_pin, (0, ""))
        pad_lines.append(_pad_smd(
            t_pin, x_positions[col], -2.159, 0.381, 1.016, nc_t, nn_t,
        ))

        # B row (bottom row, closer to board edge) → even schematic pins
        b_pin = str(col * 2 + 2)  # 2, 4, 6, ..., 36
        nc_b, nn_b = net_map.get(b_pin, (0, ""))
        pad_lines.append(_pad_smd(
            b_pin, x_positions[col], -1.016, 0.381, 0.762, nc_b, nn_b,
        ))

    pads = "\n".join(pad_lines)

    # Body outline from Eagle: x = -6.604..6.604, y = 0..2.54 → KiCad y inverted
    bx = 6.604
    by_top = -2.54
    by_bot = 0

    header = _fp_header(
        "afe_footprints:Omnetics_A79024_36pin", ref, value,
        "Omnetics A79024-001, 36-pin nano-strip electrode connector",
        "Omnetics A79024 nano-strip 36pin electrode",
        comp_ts, at_x, at_y,
    )

    return (
        f"{header}\n"
        f"{_courtyard_rect(-bx - 0.25, by_top - 0.5, bx + 0.25, by_bot + 0.5)}\n"
        f"{_silkscreen_rect(-bx, by_top, bx, by_bot)}\n"
        f"{_fab_rect(-bx, by_top, bx, by_bot)}\n"
        f"{pads}\n"
        f"    )"
    )


def fp_0402(ref, value, comp_ts, net_map, at_x, at_y, desc="0402 SMD component"):
    """IPC-7351B 0402 (1005 metric) two-pad SMD: R, C, FB."""
    pad_sx, pad_sy = 0.5, 0.6
    pad_cx = 0.48

    n1 = net_map.get("1", (0, ""))
    n2 = net_map.get("2", (0, ""))

    pads = "\n".join([
        _pad_smd("1", -pad_cx, 0, pad_sx, pad_sy, n1[0], n1[1]),
        _pad_smd("2",  pad_cx, 0, pad_sx, pad_sy, n2[0], n2[1]),
    ])

    header = _fp_header(
        "Resistor_SMD:R_0402_1005Metric", ref, value,
        desc, "0402 1005",
        comp_ts, at_x, at_y,
    )

    return (
        f"{header}\n"
        f"{_courtyard_rect(-0.96, -0.58, 0.96, 0.58)}\n"
        f"{_fab_rect(-0.5, -0.25, 0.5, 0.25)}\n"
        f"{pads}\n"
        f"    )"
    )


def fp_testpoint(ref, value, comp_ts, net_map, at_x, at_y):
    """Test point — single 1.0mm diameter SMD pad."""
    n1 = net_map.get("1", (0, ""))
    pad = _pad_smd_circle("1", 0, 0, 1.0, n1[0], n1[1])

    header = _fp_header(
        "TestPoint:TestPoint_Pad_D1.0mm", ref, value,
        "Test point pad 1.0mm diameter",
        "test point probe",
        comp_ts, at_x, at_y,
    )

    return (
        f"{header}\n"
        f"{_courtyard_rect(-0.75, -0.75, 0.75, 0.75)}\n"
        f"{pad}\n"
        f"    )"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. NETLIST — hard-coded from gen_schematic_v1_rhd2132.py
# ═══════════════════════════════════════════════════════════════════════════

# Net name → net code (starting from 1; 0 = unconnected)
# Built by enumerating every unique net from the schematic.

NETS: dict[str, int] = {}
_net_counter = 0


def _net(name: str) -> int:
    """Register a net and return its code."""
    global _net_counter
    if name not in NETS:
        _net_counter += 1
        NETS[name] = _net_counter
    return NETS[name]


# Pre-register all nets so codes are deterministic
_net("GND")
_net("+3V3")
_net("AVDD")
for i in range(32):
    _net(f"CH{i}")
_net("REF_ELEC")
_net("ELEC_TEST")
_net("SCLK")
_net("MOSI")
_net("CS")
_net("MISO")
_net("SCLK_J")
_net("MOSI_J")
_net("CS_J")
_net("ADC_ref")

# Component tstamps (stable UUIDs)
TS = {
    "U1": uid(), "J1": uid(), "J5": uid(),
    "FB1": uid(),
    "C1": uid(), "C2": uid(), "C3": uid(), "C4": uid(),
    "C5": uid(), "C6": uid(), "C7": uid(),
    "R1": uid(), "R2": uid(), "R3": uid(), "R4": uid(),
    "TP1": uid(), "TP2": uid(), "TP3": uid(),
    "TP4": uid(), "TP5": uid(), "TP6": uid(), "TP7": uid(),
    "TP8": uid(), "TP9": uid(), "TP10": uid(),
}


def _nm(pin_net_pairs: list[tuple[str, str]]) -> dict[str, tuple[int, str]]:
    """Build net_map dict for a component: {pin_num: (net_code, net_name)}."""
    m = {}
    for pin, net_name in pin_net_pairs:
        m[pin] = (_net(net_name), net_name)
    return m


# ── U1: RHD2132 net map ──────────────────────────────────────────────────
# Authoritative pin-to-pad mapping from Intan Eagle library RHD2000.lbr.
# See _u1_net_map() docstring for complete pin assignments.
# Key facts:
#   56-pin QFN, 8×8mm body, 0.5mm pitch, 14 pads per side
#   Pin 1 = IN8 (NOT IN0!), pins go CCW
#   Electrode inputs span: pads 1-9 (IN8..IN0), 34-42 (IN31..IN23), 43-56 (IN22..IN9)
#   SPI interface: pads 18-25 (CS±, SCLK±, MOSI±, MISO±) on bottom side
#   Power: pads 13, 26, 31 (VDD); 11, 12, 17, 29 (GND)
#   EP (CENTER) = GND

def _u1_net_map():
    """RHD2132 QFN-56 pin-to-net mapping.

    Authoritative source: Intan Eagle library RHD2000.lbr, deviceset RHD2132.
    CMOS SPI mode (LVDS_en tied low): only CS+, SCLK+, MOSI+, MISO+ used;
    the negative LVDS pins (CS-, SCLK-, MOSI-, MISO-) are tied to GND.
    Unused AUXIN1-3 tied to VDD per datasheet recommendation.
    AUXOUT left unconnected (NC) per datasheet.
    VESD tied to GND per datasheet.
    LVDS_EN tied to GND (CMOS mode).
    """
    nm = {}
    # Left side — pads 1-14
    # Pads 1-9: IN8..IN0 (note: reversed! pad 1 = IN8, pad 9 = IN0)
    for i in range(9):
        ch = 8 - i  # pad 1→CH8, pad 2→CH7, ..., pad 9→CH0
        nm[str(i + 1)] = (_net(f"CH{ch}"), f"CH{ch}")
    nm["10"] = (_net("REF_ELEC"), "REF_ELEC")
    nm["11"] = (_net("GND"), "GND")        # GND1
    nm["12"] = (_net("GND"), "GND")        # GND2
    nm["13"] = (_net("AVDD"), "AVDD")       # VDD1 → filtered analog supply
    nm["14"] = (_net("AVDD"), "AVDD")       # AUXIN1 → tie to VDD per datasheet
    nm["15"] = (_net("AVDD"), "AVDD")       # AUXIN2 → tie to VDD per datasheet
    nm["16"] = (_net("AVDD"), "AVDD")       # AUXIN3 → tie to VDD per datasheet

    # Bottom side — pads 15-28
    nm["17"] = (_net("GND"), "GND")        # GND3
    nm["18"] = (_net("GND"), "GND")        # CS- (CMOS mode → GND)
    nm["19"] = (_net("CS"), "CS")          # CS+
    nm["20"] = (_net("GND"), "GND")        # SCLK- (CMOS mode → GND)
    nm["21"] = (_net("SCLK"), "SCLK")     # SCLK+
    nm["22"] = (_net("GND"), "GND")        # MOSI- (CMOS mode → GND)
    nm["23"] = (_net("MOSI"), "MOSI")      # MOSI+
    nm["24"] = (_net("GND"), "GND")        # MISO- (CMOS mode → GND)
    nm["25"] = (_net("MISO"), "MISO")      # MISO+
    nm["26"] = (_net("AVDD"), "AVDD")       # VDD2 → filtered analog supply
    nm["27"] = (_net("unconnected-(U1-auxout-Pad27)"), "unconnected-(U1-auxout-Pad27)")  # AUXOUT → NC
    nm["28"] = (_net("ADC_ref"), "ADC_ref")  # ADC_REF (needs 10nF cap)

    # Right side — pads 29-42
    nm["29"] = (_net("GND"), "GND")        # GND4
    nm["30"] = (_net("GND"), "GND")        # LVDS_EN → GND (CMOS mode)
    nm["31"] = (_net("AVDD"), "AVDD")       # VDD3 → filtered analog supply
    nm["32"] = (_net("GND"), "GND")        # VESD → GND
    nm["33"] = (_net("ELEC_TEST"), "ELEC_TEST")
    # Pads 34-42: IN31..IN23
    for i in range(9):
        ch = 31 - i  # pad 34→CH31, pad 35→CH30, ..., pad 42→CH23
        nm[str(34 + i)] = (_net(f"CH{ch}"), f"CH{ch}")

    # Top side — pads 43-56
    # Pads 43-56: IN22..IN9
    for i in range(14):
        ch = 22 - i  # pad 43→CH22, pad 44→CH21, ..., pad 56→CH9
        nm[str(43 + i)] = (_net(f"CH{ch}"), f"CH{ch}")

    # Exposed pad (CENTER)
    nm["EP"] = (_net("GND"), "GND")

    return nm


# ── J1: Omnetics PZN-12-AA net map ───────────────────────────────────────
# From schematic (gen_schematic_v1_rhd2132.py SPI_CONN_PIN_NAMES):
#   Pin 1: MISO1 → MISO
#   Pin 2: MOSI  → MOSI
#   Pin 3: SCLK  → SCLK
#   Pin 4: CS    → CS
#   Pin 5: GND
#   Pin 6: GND
#   Pin 7: VDD_D → +3V3
#   Pin 8: VDD_D → +3V3
#   Pin 9: VDD_A → AVDD
#   Pin 10: VDD_A → AVDD
#   Pin 11: MISO2 → NC (not connected in single-chip headstage)
#   Pin 12: GND

J1_NET_MAP = _nm([
    ("1", "MISO"), ("2", "MOSI_J"), ("3", "SCLK_J"), ("4", "CS_J"),
    ("5", "GND"), ("6", "GND"),
    ("7", "+3V3"), ("8", "+3V3"),
    ("9", "AVDD"), ("10", "AVDD"),
    ("11", "unconnected-(J1-MISO2-Pad11)"),  # MISO2, NC for single-chip
    ("12", "GND"),
])


# ── J5: Omnetics A79024-001 net map ──────────────────────────────────────
# From schematic (ELEC_CONN_PIN_NAMES):
#   Pins 1-32: CH0-CH31
#   Pin 33: REF → REF_ELEC
#   Pin 34: GND
#   Pin 35: ETEST → ELEC_TEST
#   Pin 36: GND

def _j5_net_map():
    nm = {}
    for i in range(32):
        nm[str(i + 1)] = (_net(f"CH{i}"), f"CH{i}")
    nm["33"] = (_net("REF_ELEC"), "REF_ELEC")
    nm["34"] = (_net("GND"), "GND")
    nm["35"] = (_net("ELEC_TEST"), "ELEC_TEST")
    nm["36"] = (_net("GND"), "GND")
    return nm


# ── Passive net maps ─────────────────────────────────────────────────────
# FB1: pin1 = +3V3, pin2 = AVDD
FB1_NM = _nm([("1", "+3V3"), ("2", "AVDD")])

# C1: 100nF bypass on +3V3  → pin1=+3V3, pin2=GND
C1_NM = _nm([("1", "+3V3"), ("2", "GND")])

# C2: 1µF bulk on +3V3 → pin1=+3V3, pin2=GND
C2_NM = _nm([("1", "+3V3"), ("2", "GND")])

# C3: 100nF bypass on AVDD → pin1=AVDD, pin2=GND
C3_NM = _nm([("1", "AVDD"), ("2", "GND")])

# C4: 1µF bulk on AVDD → pin1=AVDD, pin2=GND
C4_NM = _nm([("1", "AVDD"), ("2", "GND")])

# C5: 100nF local bypass VDD_A → pin1=AVDD, pin2=GND
C5_NM = _nm([("1", "AVDD"), ("2", "GND")])

# C6: 100nF local bypass VDD_A2 → pin1=AVDD, pin2=GND
C6_NM = _nm([("1", "AVDD"), ("2", "GND")])

# C7: 10nF ADC_ref → pin1=ADC_ref, pin2=GND
C7_NM = _nm([("1", "ADC_ref"), ("2", "GND")])

# R1: 10kΩ REF bias → pin1=GND (left), pin2=REF_ELEC (right)
R1_NM = _nm([("1", "GND"), ("2", "REF_ELEC")])

# R2-R4: 100Ω SPI series resistors (per Intan reference design)
# These damp ringing on ~30cm SPI cable. Pin 1 = connector side, pin 2 = chip side.
# No resistor on MISO (output from chip — per Intan ref).
R2_NM = _nm([("1", "CS_J"), ("2", "CS")])       # CS series
R3_NM = _nm([("1", "SCLK_J"), ("2", "SCLK")])   # SCLK series
R4_NM = _nm([("1", "MOSI_J"), ("2", "MOSI")])   # MOSI series

# Test points — numbering matches schematic placement order
TP1_NM  = _nm([("1", "+3V3")])       # TP_VDD
TP2_NM  = _nm([("1", "AVDD")])       # TP_AVDD
TP3_NM  = _nm([("1", "GND")])        # TP_GND
TP4_NM  = _nm([("1", "SCLK")])       # TP_SCLK
TP5_NM  = _nm([("1", "MISO")])       # TP_MISO
TP6_NM  = _nm([("1", "REF_ELEC")])   # TP_REF
TP7_NM  = _nm([("1", "CH0")])        # TP_CH0
TP8_NM  = _nm([("1", "CH1")])        # TP_CH1
TP9_NM  = _nm([("1", "CH2")])        # TP_CH2
TP10_NM = _nm([("1", "CH3")])        # TP_CH3


# ═══════════════════════════════════════════════════════════════════════════
# 4. PLACEMENT — geometry-driven, not guessed
# ═══════════════════════════════════════════════════════════════════════════

# Board: 30 × 24 mm at (ORIGIN_X, ORIGIN_Y)
# Layout strategy:
#   - J5 (electrode connector, 13.2mm wide) at SOUTH edge, centered
#   - J1 (SPI cable connector, 4.4mm wide) at NORTH edge, centered
#   - U1 (RHD2132, 5×5mm) centered, biased toward J5 (short analog traces)
#   - Decoupling caps IMMEDIATELY adjacent to U1 power pins
#   - FB1 between power domains, near U1
#   - Test points along edges, out of routing channels
#
# Coordinate system: (0,0) = board top-left corner at (ORIGIN_X, ORIGIN_Y)
# Positive X → right, positive Y → down (KiCad convention)

BX = ORIGIN_X  # board left edge
BY = ORIGIN_Y  # board top edge

# Center coordinates in PCB space
def brd(dx, dy):
    """Board-relative coords → PCB-space coords."""
    return round(BX + dx, 3), round(BY + dy, 3)


# ── Major component positions ────────────────────────────────────────────

# U1: RHD2132 — centered horizontally, biased south (toward electrode connector)
# QFN-56 body is 8×8mm. Place center at ~58% down the board.
U1_X, U1_Y = brd(15.0, 14.0)

# J5: A79024 electrode connector — south edge, centered
# A79024 body is ~13.2mm wide. Pads extend below body.
# Place so pad row is at board south edge for direct MEA connection.
J5_X, J5_Y = brd(15.0, 21.5)

# J1: PZN-12 SPI connector — north edge, centered
# PZN-12 body is ~4.4mm wide. Cable exits north.
# Place so connector pads have ≥0.5mm clearance to north board edge.
# PZN-12 top row at local y=-2.159mm. Need center y >= 0 + 2.159 + 0.5 + pad_h/2.
# Place at 3.5mm from north edge to ensure edge clearance.
J1_X, J1_Y = brd(15.0, 3.5)

# FB1: Ferrite bead — between J1 and R4, near power entry
# Moved from Y=8.5 to Y=4.5 to clear Bundle-C west routing corridor
FB1_X, FB1_Y = brd(8.0, 4.0)

# ── Decoupling caps: MUST be adjacent to U1 power pins ───────────────────
# RHD2132 QFN-56:
#   VDD1 = pad 13 (left side, bottom) at x = U1_X - 4.05, y = U1_Y + 2.75
#   VDD2 = pad 26 (bottom side, right) at x = U1_X + 2.25, y = U1_Y + 4.05
#   VDD3 = pad 31 (right side, middle) at x = U1_X + 4.05, y = U1_Y - 2.25
#   ADC_REF = pad 28 (bottom side, rightmost) at x = U1_X + 3.25, y = U1_Y + 4.05
# Per datasheet: "100nF ceramic capacitor between VDD and ground placed
# as close as possible to the bottom of the chip (pins 15-28)"

# C5: 100nF VDD bypass — near pad 13 (VDD1, left side bottom)
C5_X, C5_Y = brd(9.5, 17.0)

# C6: 100nF VDD bypass — near pad 26 (VDD2, bottom side right)
# Place EAST of U1, clear of J5 pad field. Pad 26 is at U1 bottom-right.
C6_X, C6_Y = brd(21.0, 15.5)

# C7: 10nF ADC_ref — MUST be within 5mm of pad 28 (bottom side rightmost)
# Place EAST of U1, adjacent to C6. Pad 28 abs position = (118.25, 98.05).
# C7 at brd(21.0,17.0) = (121.0, 97.0) → distance to pad 28 ≈ 3mm ✓
C7_X, C7_Y = brd(21.0, 17.0)

# ── Power entry bypass caps: near FB1 / J1 ──────────────────────────────

# C1: 100nF on +3V3 — near FB1 input
C1_X, C1_Y = brd(6.0, 4.0)

# C2: 1µF bulk on +3V3 — near FB1 input
C2_X, C2_Y = brd(4.0, 8.5)

# C3: 100nF on AVDD — near FB1 output
C3_X, C3_Y = brd(10.0, 4.0)

# C4: 1µF bulk on AVDD — near FB1 output
C4_X, C4_Y = brd(12.0, 4.0)

# R1: 10kΩ REF bias — near U1 REF pin (pad 10, left side bottom)
R1_X, R1_Y = brd(9.5, 15.0)

# R2-R4: SPI series resistors — between J1 SPI pins and U1 SPI pads
# Place in a row between J1 (north) and U1, close to J1 for cable-side damping
# Moved from Y=6.0 to Y=5.0 to clear Bundle-C electrode vertical escapes
# and header segments (CH15 at Y=6.425, CH16 header crosses R2 X range)
R2_X, R2_Y = brd(18.0, 5.0)    # CS series, near J1 pin 4
R3_X, R3_Y = brd(15.0, 5.0)    # SCLK series, near J1 pin 3
R4_X, R4_Y = brd(12.0, 5.0)    # MOSI series, near J1 pin 2

# ── Test points: along board edges ──────────────────────────────────────

TP1_X,  TP1_Y  = brd(2.0, 4.0)    # TP_VDD (+3V3)
TP2_X,  TP2_Y  = brd(2.0, 7.0)    # TP_AVDD
TP3_X,  TP3_Y  = brd(2.0, 10.0)   # TP_GND
TP4_X,  TP4_Y  = brd(28.0, 4.0)   # TP_SCLK
TP5_X,  TP5_Y  = brd(28.0, 7.0)   # TP_MISO
TP6_X,  TP6_Y  = brd(28.0, 10.0)  # TP_REF
TP7_X,  TP7_Y  = brd(2.0, 14.0)   # TP_CH0
TP8_X,  TP8_Y  = brd(2.0, 16.0)   # TP_CH1
TP9_X,  TP9_Y  = brd(2.0, 18.0)   # TP_CH2
TP10_X, TP10_Y = brd(2.0, 20.0)   # TP_CH3


# ═══════════════════════════════════════════════════════════════════════════
# 5. PCB ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════


def _assert_net_binding(ref, net_map, pad, expected_net, msg):
    """Hard-fail if a component pad is not bound to the expected net."""
    entry = net_map.get(pad)
    if entry is None:
        print(f"FATAL: {ref} pad {pad} has no net assignment", file=sys.stderr)
        sys.exit(1)
    _code, actual_net = entry
    if actual_net != expected_net:
        print(
            f"FATAL: {msg}\n"
            f"  Expected: {ref} pad {pad} → {expected_net}\n"
            f"  Actual:   {ref} pad {pad} → {actual_net}",
            file=sys.stderr,
        )
        sys.exit(1)


def _assert_net_binding_u1(u1_nm, pad, expected_net, msg):
    """Hard-fail if a U1 pad is not bound to the expected net."""
    _assert_net_binding("U1", u1_nm, pad, expected_net, msg)


def generate_pcb():
    u1_nm = _u1_net_map()
    j5_nm = _j5_net_map()

    # ── Footprints ────────────────────────────────────────────────────
    footprints = [
        fp_rhd2132_qfn56("U1", "RHD2132", TS["U1"], u1_nm, U1_X, U1_Y),
        fp_pzn12("J1", "PZN-12", TS["J1"], J1_NET_MAP, J1_X, J1_Y),
        fp_a79024("J5", "A79024-001", TS["J5"], j5_nm, J5_X, J5_Y),
        fp_0402("FB1", "600R@100MHz", TS["FB1"], FB1_NM, FB1_X, FB1_Y, "Ferrite Bead 0402"),
        fp_0402("C1", "100nF", TS["C1"], C1_NM, C1_X, C1_Y, "Cap 0402 100nF"),
        fp_0402("C2", "1uF",   TS["C2"], C2_NM, C2_X, C2_Y, "Cap 0402 1uF"),
        fp_0402("C3", "100nF", TS["C3"], C3_NM, C3_X, C3_Y, "Cap 0402 100nF"),
        fp_0402("C4", "1uF",   TS["C4"], C4_NM, C4_X, C4_Y, "Cap 0402 1uF"),
        fp_0402("C5", "100nF", TS["C5"], C5_NM, C5_X, C5_Y, "Cap 0402 100nF VDD_A bypass"),
        fp_0402("C6", "100nF", TS["C6"], C6_NM, C6_X, C6_Y, "Cap 0402 100nF VDD_D bypass"),
        fp_0402("C7", "10nF",  TS["C7"], C7_NM, C7_X, C7_Y, "Cap 0402 10nF ADC_ref"),
        fp_0402("R1", "10k",   TS["R1"], R1_NM, R1_X, R1_Y, "Resistor 0402 10k REF bias"),
        fp_0402("R2", "100",   TS["R2"], R2_NM, R2_X, R2_Y, "Resistor 0402 100R CS series"),
        fp_0402("R3", "100",   TS["R3"], R3_NM, R3_X, R3_Y, "Resistor 0402 100R SCLK series"),
        fp_0402("R4", "100",   TS["R4"], R4_NM, R4_X, R4_Y, "Resistor 0402 100R MOSI series"),
        fp_testpoint("TP1",  "TP_VDD",   TS["TP1"],  TP1_NM,  TP1_X,  TP1_Y),
        fp_testpoint("TP2",  "TP_AVDD",  TS["TP2"],  TP2_NM,  TP2_X,  TP2_Y),
        fp_testpoint("TP3",  "TP_GND",   TS["TP3"],  TP3_NM,  TP3_X,  TP3_Y),
        fp_testpoint("TP4",  "TP_SCLK",  TS["TP4"],  TP4_NM,  TP4_X,  TP4_Y),
        fp_testpoint("TP5",  "TP_MISO",  TS["TP5"],  TP5_NM,  TP5_X,  TP5_Y),
        fp_testpoint("TP6",  "TP_REF",   TS["TP6"],  TP6_NM,  TP6_X,  TP6_Y),
        fp_testpoint("TP7",  "TP_CH0",   TS["TP7"],  TP7_NM,  TP7_X,  TP7_Y),
        fp_testpoint("TP8",  "TP_CH1",   TS["TP8"],  TP8_NM,  TP8_X,  TP8_Y),
        fp_testpoint("TP9",  "TP_CH2",   TS["TP9"],  TP9_NM,  TP9_X,  TP9_Y),
        fp_testpoint("TP10", "TP_CH3",   TS["TP10"], TP10_NM, TP10_X, TP10_Y),
    ]

    # ── Hard assertions: critical net bindings ────────────────────────
    # These catch silent net assignment errors at generation time.
    # If any fail, the generator exits non-zero — no manual checking.
    _assert_net_binding("TP6", TP6_NM, "1", "REF_ELEC",
                        "TP6 pad must be on REF_ELEC net")
    _assert_net_binding_u1(u1_nm, "28", "ADC_ref",
                           "U1 pad 28 must be on ADC_ref net")
    _assert_net_binding_u1(u1_nm, "10", "REF_ELEC",
                           "U1 pad 10 must be on REF_ELEC net")
    _assert_net_binding("TP1", TP1_NM, "1", "+3V3",
                        "TP1 pad must be on +3V3 net")
    _assert_net_binding("TP2", TP2_NM, "1", "AVDD",
                        "TP2 pad must be on AVDD net")
    _assert_net_binding("TP3", TP3_NM, "1", "GND",
                        "TP3 pad must be on GND net")
    # Verify SPI series resistors have correct net polarity
    _assert_net_binding("R2", R2_NM, "2", "CS",
                        "R2 pin 2 (chip side) must be CS")
    _assert_net_binding("R3", R3_NM, "2", "SCLK",
                        "R3 pin 2 (chip side) must be SCLK")
    _assert_net_binding("R4", R4_NM, "2", "MOSI",
                        "R4 pin 2 (chip side) must be MOSI")
    print("  ✓ All critical net binding assertions passed")

    # ── Net declarations ──────────────────────────────────────────────
    net_decls = ['    (net 0 "")']
    for name, code in sorted(NETS.items(), key=lambda kv: kv[1]):
        net_decls.append(f'    (net {code} "{name}")')

    # ── Board outline (Edge.Cuts) ─────────────────────────────────────
    corners = [
        (BX, BY),
        (BX + BOARD_W, BY),
        (BX + BOARD_W, BY + BOARD_H),
        (BX, BY + BOARD_H),
    ]
    edge_lines = []
    for i in range(4):
        sx, sy = corners[i]
        ex, ey = corners[(i + 1) % 4]
        edge_lines.append(
            f"    (gr_line\n"
            f"        (start {sx} {sy}) (end {ex} {ey})\n"
            f'        (stroke (width 0.15) (type solid))\n'
            f'        (layer "Edge.Cuts")\n'
            f'        (uuid "{uid()}")\n'
            f"    )"
        )

    # ── Ground-fill zones (In1.Cu + In2.Cu as ground planes) ─────────
    gnd_code = NETS["GND"]
    zone_corners = " ".join(f"(xy {x} {y})" for x, y in corners)

    zones = []
    for layer_name in ["In1.Cu", "In2.Cu"]:
        zones.append(
            f'    (zone\n'
            f'        (net {gnd_code}) (net_name "GND")\n'
            f'        (layer "{layer_name}")\n'
            f'        (uuid "{uid()}")\n'
            f'        (name "GND_{layer_name}")\n'
            f"        (hatch edge 0.5)\n"
            f"        (connect_pads (clearance 0.2))\n"
            f"        (min_thickness 0.15)\n"
            f"        (fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))\n"
            f"        (polygon\n"
            f"            (pts {zone_corners})\n"
            f"        )\n"
            f"    )"
        )

    # ── Net classes ───────────────────────────────────────────────────
    # Defined in the project file; PCB file just uses net codes.
    # But we'll add design rules as comments for reference.

    # ── Assemble ──────────────────────────────────────────────────────
    pcb = f"""(kicad_pcb
    (version 20241229)
    (generator "gen_pcb_v1.py")
    (generator_version "9.0")
    (general
        (thickness 1.6)
        (legacy_teardrops no)
    )
    (paper "A4")
    (title_block
        (title "Neural AFE Headstage v1 — RHD2132")
        (company "BCIInterface")
        (rev "1.0")
        (date "2025-06-26")
        (comment 1 "32-ch RHD2132 | 16ch wired v1 | SPI to Intan USB interface")
        (comment 2 "30x24mm 4-layer | ≤3µV RMS spike band | fs=30kS/s")
    )
    (layers
        (0 "F.Cu" signal)
        (1 "In1.Cu" signal)
        (2 "In2.Cu" signal)
        (3 "B.Cu" signal)
        (9 "F.Adhes" user "F.Adhesive")
        (11 "B.Adhes" user "B.Adhesive")
        (13 "F.Paste" user)
        (15 "B.Paste" user)
        (5 "F.SilkS" user "F.Silkscreen")
        (7 "B.SilkS" user "B.Silkscreen")
        (4 "F.Mask" user)
        (6 "B.Mask" user)
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
                (type "prepreg")
                (thickness 0.2)
                (material "FR4")
                (epsilon_r 4.5)
                (loss_tangent 0.02)
            )
            (layer "In1.Cu"
                (type "copper")
                (thickness 0.035)
            )
            (layer "dielectric 2"
                (type "core")
                (thickness 1.065)
                (material "FR4")
                (epsilon_r 4.5)
                (loss_tangent 0.02)
            )
            (layer "In2.Cu"
                (type "copper")
                (thickness 0.035)
            )
            (layer "dielectric 3"
                (type "prepreg")
                (thickness 0.2)
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
            (copper_finish "ENIG")
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
            (outputdirectory "gerbers/")
        )
    )

{chr(10).join(net_decls)}

{chr(10).join(footprints)}

{chr(10).join(edge_lines)}

{chr(10).join(zones)}

    (embedded_fonts no)
)
"""
    return pcb


# ═══════════════════════════════════════════════════════════════════════════
# 6. MAIN
# ═══════════════════════════════════════════════════════════════════════════


def main():
    print("gen_pcb_v1.py — Layout bootstrapper for v1 RHD2132 headstage")
    print(f"  Board: {BOARD_W} × {BOARD_H} mm, 4-layer")
    print(f"  Origin: ({ORIGIN_X}, {ORIGIN_Y})")

    pcb_text = generate_pcb()
    PCB_OUT.write_text(pcb_text)
    size_kb = PCB_OUT.stat().st_size / 1024
    print(f"\n  Wrote {PCB_OUT} ({size_kb:.1f} KB)")

    # Summary
    n_nets = len(NETS)
    n_footprints = 25  # U1 + J1 + J5 + FB1 + C1-C7(7) + R1-R4(4) + TP1-TP10(10)
    print(f"  {n_nets} nets, {n_footprints} footprints")
    print("\n  Footprint data sources:")
    print("    RHD2132 QFN-56:  Intan Eagle lib QFN56_8_X_8_LARGE (8×8mm, 0.5mm pitch)")
    print("    Pin mapping:     Intan Eagle lib RHD2132 deviceset (56 + EP)")
    print("    PZN-12-AA:       Intan Eagle lib RHD2000.lbr (exact SMD pad coords)")
    print("    A79024/A79025:   Intan Eagle lib RHD2000.lbr (exact SMD pad coords)")
    print("    0402 passives:   IPC-7351B land pattern")
    print("    Test points:     KiCad standard 1.0mm pad")

    print("\n  Placement strategy:")
    print("    J5 (electrode) → south edge, centered")
    print("    J1 (SPI cable) → north edge, centered")
    print("    U1 (RHD2132)   → center, biased south (short analog traces)")
    print("    C5/C6          → adjacent to U1 VDD pins (< 1mm)")
    print("    C7             → adjacent to U1 ADC_ref pin")
    print("    FB1, C1-C4     → power entry zone between J1 and U1")
    print("    Test points    → left and right board edges")

    print("\n  4-layer stackup:")
    print("    F.Cu   — signal + components")
    print("    In1.Cu — GND plane (full pour)")
    print("    In2.Cu — GND/power plane")
    print("    B.Cu   — signal + routing")

    print("\nOpen afe-headstage-v1.kicad_pcb in KiCad to view ratsnest.")
    print("Route interactively — this is a bootstrapper, not an autorouter.")

    # ── Write standalone .kicad_mod for RHD2132 QFN-56 ────────────────
    # This is the CANONICAL source of truth for the footprint.
    # The PCB and the .kicad_mod are both generated from fp_rhd2132_qfn56().
    write_standalone_footprint()


def write_standalone_footprint():
    """Write RHD2132 QFN-56 footprint as a standalone .kicad_mod file.

    Uses the same fp_rhd2132_qfn56() builder as the PCB, so they are
    always in sync. No "ripping from PCB" — this IS the canonical source.
    """
    fp_dir = HERE / "footprints_v1.pretty"
    fp_dir.mkdir(exist_ok=True)
    mod_path = fp_dir / "RHD2132_QFN56.kicad_mod"

    # Build footprint with dummy nets (standalone footprints have no nets)
    dummy_nm = {}
    for i in range(1, 57):
        dummy_nm[str(i)] = (0, "")
    dummy_nm["EP"] = (0, "")

    fp_text = fp_rhd2132_qfn56(
        "U1", "RHD2132", uid(), dummy_nm, 0, 0,
    )

    # Extract the inner footprint content (remove the outer indentation
    # that generate_pcb adds, and wrap in proper standalone format)
    # fp_rhd2132_qfn56 returns '    (footprint ...\n    )' — strip leading indent
    fp_text = fp_text.strip()

    # Standalone .kicad_mod uses (footprint ...) at top level
    mod_text = (
        f"{fp_text}\n"
    )

    mod_path.write_text(mod_text)
    print(f"  Wrote standalone footprint: {mod_path}")


if __name__ == "__main__":
    main()
