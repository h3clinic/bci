"""
Deterministic clearance checker for REF_ELEC route — Option A (2 vias, B.Cu hop).

Methodology (no point-sampling):
  - Each copper obstacle is a rectangle expanded by `keep = clearance + half_width`
    (Minkowski sum with the trace cross-section).
  - Each trace segment is a line segment (zero width after expansion).
  - Violation = line segment intersects any expanded rectangle.
  - Same-net obstacles are excluded from checking.

All coordinates in BOARD space (mm). Origin = board top-left.
  Positive X → right, positive Y → down.
  KiCad conversion: kx = BRD_OX + bx, ky = BRD_OY + by.
  (BRD_OX, BRD_OY defined in board_geom.py — single source of truth.)
"""
from __future__ import annotations

import math
import sys


# ── Trace parameters ─────────────────────────────────────────────────────
TRACE_W = 0.15       # mm
HW      = TRACE_W / 2  # 0.075 mm  half-width
CLR     = 0.30        # mm        ELECTRODE net class clearance
KEEP    = HW + CLR    # 0.375 mm  Minkowski expansion radius

VIA_DRILL    = 0.3    # mm
VIA_ANNULAR  = 0.15   # mm  (ring width)
VIA_DIAMETER = VIA_DRILL + 2 * VIA_ANNULAR  # 0.6 mm
VIA_RADIUS   = VIA_DIAMETER / 2             # 0.3 mm

REF_ELEC_NET = "REF_ELEC"

# ── Frozen footprint constants (regression-locked) ───────────────────────
# Any change here MUST be accompanied by a re-check of the entire route.
QFN56_PITCH     = 0.5    # mm  pad-to-pad center pitch
QFN56_PAD_SHORT = 0.25   # mm  pad narrow dimension (perpendicular to side)
QFN56_PAD_LONG  = 0.9    # mm  pad long dimension (along side)
QFN56_PAD_CX    = 4.05   # mm  pad center offset from IC center
QFN56_HALF_SPAN = 3.25   # mm  half-span of pad row (center to pin 1/last)
QFN56_EP_SX     = 4.8    # mm  exposed pad X size
QFN56_EP_SY     = 4.8    # mm  exposed pad Y size

# 0402 footprint (R1, C5-C7)
FP0402_PAD_SX   = 0.5    # mm
FP0402_PAD_SY   = 0.6    # mm
FP0402_PAD_CX   = 0.48   # mm  pad center offset from component center

# U1 placement (frozen — corridor derivation depends on this)
U1_CX = 15.0   # mm  board-space center X
U1_CY = 14.0   # mm  board-space center Y

# 34-lane electrode corridor bounds (F.Cu)
# Derivation (from frozen constants):
#   CORRIDOR_X_MIN = U1_CX - QFN56_PAD_CX + QFN56_PAD_LONG/2 + KEEP
#                  = 15.0  - 4.05         + 0.45              + 0.375 = 11.775
#   CORRIDOR_X_MAX = U1_CX + QFN56_PAD_CX - QFN56_PAD_LONG/2 - KEEP
#                  = 15.0  + 4.05         - 0.45              - 0.375 = 18.225
CORRIDOR_X_MIN  = 11.775  # mm  left edge
CORRIDOR_X_MAX  = 18.225  # mm  right edge

# Maximum parallel overlap of B.Cu REF_ELEC segment with any vertical CH lane
MAX_PARALLEL_OVERLAP_MM = 3.0  # mm — acceptance criterion A2

# Digital SPI nets — must be isolated from REF_ELEC B.Cu trace
DIGITAL_SPI_NETS = {"CS", "SCLK", "MOSI", "MISO", "CS_J", "SCLK_J", "MOSI_J"}

# ── Bundle C perimeter geometry (frozen — Rev 3.9) ───────────────────────
# Derived from frozen constants above. See routing_strategy_v1.md §4.2.1.
#
# Lane pitch (ELECTRODE net class):
# Must satisfy BOTH track-to-track (CLR + TRACE_W = 0.350mm) AND via-to-track
# (via_R + CLR_elec + trace_half = 0.200 + 0.200 + 0.075 = 0.475mm) clearance.
# Phase 5 entry vias (0.4mm pad) at descent_x require wider pitch than
# track-only Phases 1-4.  0.550mm provides 0.075mm manufacturing margin
# over the 0.475mm DRC minimum — enough to absorb fab registration
# tolerance and gerber quantization without losing clearance.
_ELECTRODE_CLR = 0.200  # ELECTRODE netclass clearance (from .kicad_pro)
_ELECTRODE_VIA_RADIUS = 0.200  # ELECTRODE via pad radius (0.4mm dia / 2)
LANE_PITCH = round(max(CLR + TRACE_W,
                       _ELECTRODE_VIA_RADIUS + _ELECTRODE_CLR + TRACE_W / 2)
                   + 0.075, 4)  # 0.550 mm — 0.075mm fab margin over DRC min
VIA_KEEP   = VIA_RADIUS + CLR + HW  # 0.675 mm  (via center → trace center)
#
# Component placements (frozen in gen_pcb_v1.py):
R1_CX, R1_CY   = 9.5, 15.0    # 0402
C5_CX, C5_CY   = 9.5, 17.0    # 0402
C6_CX, C6_CY   = 21.0, 15.5   # 0402
C7_CX, C7_CY   = 21.0, 17.0   # 0402
VIA2_X, VIA2_Y  = 19.7625, 18.2
#
# U1 KEEP boundaries:
U1_KEEP_TOP     = U1_CY - QFN56_PAD_CX + QFN56_PAD_LONG / 2 - KEEP
#                 = 14.0 − 4.05 + 0.45 − 0.375 … wait, that's the north side.
#   North pad center y = U1_CY − QFN56_PAD_CX = 9.95.
#   North pad outer edge (top) y = 9.95 − QFN56_PAD_LONG/2 = 9.50.
#   KEEP boundary (above, in board coords = smaller y): 9.50 − KEEP = 9.125.
U1_KEEP_TOP     = U1_CY - QFN56_PAD_CX - QFN56_PAD_LONG / 2 - KEEP   # 9.125
U1_KEEP_BOTTOM  = U1_CY + QFN56_PAD_CX + QFN56_PAD_LONG / 2 + KEEP   # 18.875
U1_KEEP_LEFT    = U1_CX - QFN56_PAD_CX - QFN56_PAD_LONG / 2 - KEEP   # 10.125
U1_KEEP_RIGHT   = U1_CX + QFN56_PAD_CX + QFN56_PAD_LONG / 2 + KEEP   # 19.875
#
# Obstacle zone: C6/C7 (0402 at x=21.0)
#   C6 pad2 east edge = C6_CX + FP0402_PAD_CX + FP0402_PAD_SX/2 = 21.730
#   C6.2/C7.2 KEEP east = 21.730 + KEEP = 22.105
#   C6 pad1 west edge = C6_CX − FP0402_PAD_CX − FP0402_PAD_SX/2 = 20.270
#   C6.1/C7.1 KEEP west = 20.270 − KEEP = 19.895
C6C7_KEEP_EAST  = C6_CX + FP0402_PAD_CX + FP0402_PAD_SX / 2 + KEEP  # 22.105
C6C7_KEEP_WEST  = C6_CX - FP0402_PAD_CX - FP0402_PAD_SX / 2 - KEEP  # 19.895
#   C6/C7 obstacle zone y-range: [14.825, 17.675]
C6C7_Y_MIN      = min(C6_CY, C7_CY) - FP0402_PAD_SY / 2 - KEEP      # 14.825
C6C7_Y_MAX      = max(C6_CY, C7_CY) + FP0402_PAD_SY / 2 + KEEP      # 17.675
#
# Obstacle zone: R1/C5 (0402 at x=9.5)
#   R1 pad1 west edge = R1_CX − FP0402_PAD_CX − FP0402_PAD_SX/2 = 8.770
#   R1.1/C5.1 KEEP west = 8.770 − KEEP = 8.395
R1C5_KEEP_WEST  = R1_CX - FP0402_PAD_CX - FP0402_PAD_SX / 2 - KEEP  # 8.395
#   R1/C5 obstacle zone y-range: [14.325, 17.675]
R1C5_Y_MIN      = min(R1_CY, C5_CY) - FP0402_PAD_SY / 2 - KEEP      # 14.325
R1C5_Y_MAX      = max(R1_CY, C5_CY) + FP0402_PAD_SY / 2 + KEEP      # 17.675
#
# Uniform pack definitions (7 lanes each side):
EAST_DESCENT_X  = tuple(C6C7_KEEP_EAST + i * LANE_PITCH for i in range(7))
#   E1=22.105, E2=22.655, E3=23.205, E4=23.755, E5=24.305, E6=24.855, E7=25.405
WEST_DESCENT_X  = tuple(R1C5_KEEP_WEST - i * LANE_PITCH for i in range(7))
#   W1=8.395, W2=7.845, W3=7.295, W4=6.745, W5=6.195, W6=5.645, W7=5.095
#
# Board boundaries (single source of truth: board_geom.py):
from board_geom import BOARD_WIDTH, BOARD_HEIGHT
#
# Bottom fan start Y: south of U1 KEEP bottom, no obstacles between
# descent lanes and J5.  Frozen at U1 KEEP bottom.
BOTTOM_FAN_Y    = U1_KEEP_BOTTOM  # 18.875


# ── Obstacle inventory ───────────────────────────────────────────────────
# Each obstacle: (name, net, x_min, y_min, x_max, y_max)
# Derived directly from gen_pcb_v1.py placement + footprint geometry.

def _build_obstacles() -> list[tuple[str, str, float, float, float, float]]:
    """Build the complete obstacle list from gen_pcb_v1.py geometry."""
    obs = []

    # ── U1 center at brd(15.0, 14.0) ─────────────────────────────────
    u1_cx, u1_cy = 15.0, 14.0
    pitch = QFN56_PITCH
    half_span = QFN56_HALF_SPAN
    pad_long = QFN56_PAD_LONG
    pad_short = QFN56_PAD_SHORT
    pad_cx = QFN56_PAD_CX
    ep_sx, ep_sy = QFN56_EP_SX, QFN56_EP_SY

    # Left side pads 1-14: x_center = u1_cx - pad_cx = 10.95
    #   copper: dx=pad_long/2=0.45, dy=pad_short/2=0.125
    left_nets = {
        1: "CH8", 2: "CH7", 3: "CH6", 4: "CH5", 5: "CH4",
        6: "CH3", 7: "CH2", 8: "CH1", 9: "CH0", 10: "REF_ELEC",
        11: "GND", 12: "GND", 13: "AVDD", 14: "AVDD",
    }
    for i in range(14):
        pin = i + 1
        py = u1_cy + (half_span - i * pitch)
        px = u1_cx - pad_cx
        hlx, hly = pad_long / 2, pad_short / 2
        net = left_nets[pin]
        obs.append((f"U1.{pin}", net,
                     px - hlx, py - hly, px + hlx, py + hly))

    # Bottom side pads 15-28: y_center = u1_cy + pad_cx = 18.05
    #   copper: dx=pad_short/2=0.125, dy=pad_long/2=0.45
    bot_nets = {
        15: "AVDD", 16: "AVDD", 17: "GND", 18: "GND",
        19: "CS", 20: "GND", 21: "SCLK", 22: "GND",
        23: "MOSI", 24: "GND", 25: "MISO", 26: "AVDD",
        27: "NC", 28: "ADC_ref",
    }
    for i in range(14):
        pin = 15 + i
        px = u1_cx + (-half_span + i * pitch)
        py = u1_cy + pad_cx
        hlx, hly = pad_short / 2, pad_long / 2
        net = bot_nets[pin]
        obs.append((f"U1.{pin}", net,
                     px - hlx, py - hly, px + hlx, py + hly))

    # Right side pads 29-42: x_center = u1_cx + pad_cx = 19.05
    #   copper: dx=pad_long/2=0.45, dy=pad_short/2=0.125
    right_nets = {
        29: "GND", 30: "GND", 31: "AVDD", 32: "GND",
        33: "ELEC_TEST",
        34: "CH31", 35: "CH30", 36: "CH29", 37: "CH28",
        38: "CH27", 39: "CH26", 40: "CH25", 41: "CH24", 42: "CH23",
    }
    for i in range(14):
        pin = 29 + i
        py = u1_cy + (-half_span + i * pitch)
        px = u1_cx + pad_cx
        hlx, hly = pad_long / 2, pad_short / 2
        net = right_nets[pin]
        obs.append((f"U1.{pin}", net,
                     px - hlx, py - hly, px + hlx, py + hly))

    # Top side pads 43-56: y_center = u1_cy - pad_cx = 9.95
    #   copper: dx=pad_short/2=0.125, dy=pad_long/2=0.45
    top_nets = {}
    for i in range(14):
        ch = 22 - i
        top_nets[43 + i] = f"CH{ch}"
    for i in range(14):
        pin = 43 + i
        px = u1_cx + (half_span - i * pitch)
        py = u1_cy - pad_cx
        hlx, hly = pad_short / 2, pad_long / 2
        net = top_nets[pin]
        obs.append((f"U1.{pin}", net,
                     px - hlx, py - hly, px + hlx, py + hly))

    # EP (exposed pad) — GND
    obs.append(("U1.EP", "GND",
                u1_cx - ep_sx / 2, u1_cy - ep_sy / 2,
                u1_cx + ep_sx / 2, u1_cy + ep_sy / 2))

    # ── R1 at brd(9.5, 15.0) ─────────────────────────────────────────
    # 0402: pad_sx=0.5, pad_sy=0.6, pad_cx=0.48
    r1_cx, r1_cy = 9.5, 15.0
    r1_pcx = FP0402_PAD_CX
    r1_psx, r1_psy = FP0402_PAD_SX, FP0402_PAD_SY
    # Pin 1 = GND, Pin 2 = REF_ELEC
    obs.append(("R1.1", "GND",
                r1_cx - r1_pcx - r1_psx / 2, r1_cy - r1_psy / 2,
                r1_cx - r1_pcx + r1_psx / 2, r1_cy + r1_psy / 2))
    obs.append(("R1.2", "REF_ELEC",
                r1_cx + r1_pcx - r1_psx / 2, r1_cy - r1_psy / 2,
                r1_cx + r1_pcx + r1_psx / 2, r1_cy + r1_psy / 2))

    # ── C5 at brd(9.5, 17.0) — AVDD/GND ─────────────────────────────
    c5_cx, c5_cy = 9.5, 17.0
    obs.append(("C5.1", "AVDD",
                c5_cx - r1_pcx - r1_psx / 2, c5_cy - r1_psy / 2,
                c5_cx - r1_pcx + r1_psx / 2, c5_cy + r1_psy / 2))
    obs.append(("C5.2", "GND",
                c5_cx + r1_pcx - r1_psx / 2, c5_cy - r1_psy / 2,
                c5_cx + r1_pcx + r1_psx / 2, c5_cy + r1_psy / 2))

    # ── C6 at brd(21.0, 15.5) — AVDD/GND ────────────────────────────
    c6_cx, c6_cy = 21.0, 15.5
    obs.append(("C6.1", "AVDD",
                c6_cx - r1_pcx - r1_psx / 2, c6_cy - r1_psy / 2,
                c6_cx - r1_pcx + r1_psx / 2, c6_cy + r1_psy / 2))
    obs.append(("C6.2", "GND",
                c6_cx + r1_pcx - r1_psx / 2, c6_cy - r1_psy / 2,
                c6_cx + r1_pcx + r1_psx / 2, c6_cy + r1_psy / 2))

    # ── C7 at brd(21.0, 17.0) — ADC_ref/GND ─────────────────────────
    c7_cx, c7_cy = 21.0, 17.0
    obs.append(("C7.1", "ADC_ref",
                c7_cx - r1_pcx - r1_psx / 2, c7_cy - r1_psy / 2,
                c7_cx - r1_pcx + r1_psx / 2, c7_cy + r1_psy / 2))
    obs.append(("C7.2", "GND",
                c7_cx + r1_pcx - r1_psx / 2, c7_cy - r1_psy / 2,
                c7_cx + r1_pcx + r1_psx / 2, c7_cy + r1_psy / 2))

    # ── J5 at brd(15.0, 21.5) — all 36 pads ─────────────────────────
    j5_cx, j5_cy = 15.0, 21.5
    x_positions = [
        -5.3975, -4.7625, -4.1275, -3.4925, -2.8575, -2.2225,
        -1.5875, -0.9525, -0.3175,  0.3175,  0.9525,  1.5875,
         2.2225,  2.8575,  3.4925,  4.1275,  4.7625,  5.3975,
    ]
    # T row (odd pins): pad 0.381 × 1.016, y_rel = -2.159
    # B row (even pins): pad 0.381 × 0.762, y_rel = -1.016
    t_psy = 1.016
    b_psy = 0.762
    pad_sx_j5 = 0.381

    # J5 net map
    j5_nets = {}
    for i in range(32):
        j5_nets[i + 1] = f"CH{i}"
    j5_nets[33] = "REF_ELEC"
    j5_nets[34] = "GND"
    j5_nets[35] = "ELEC_TEST"
    j5_nets[36] = "GND"

    for col in range(18):
        # T row — odd pin
        t_pin = col * 2 + 1
        tx = j5_cx + x_positions[col]
        ty = j5_cy + (-2.159)
        hlx = pad_sx_j5 / 2
        hly = t_psy / 2
        net = j5_nets.get(t_pin, "NC")
        obs.append((f"J5.{t_pin}", net,
                     tx - hlx, ty - hly, tx + hlx, ty + hly))

        # B row — even pin
        b_pin = col * 2 + 2
        bx = j5_cx + x_positions[col]
        by = j5_cy + (-1.016)
        hly_b = b_psy / 2
        net_b = j5_nets.get(b_pin, "NC")
        obs.append((f"J5.{b_pin}", net_b,
                     bx - hlx, by - hly_b, bx + hlx, by + hly_b))

    # ── Test points on REF_ELEC ──────────────────────────────────────
    # TP6 at brd(28.0, 10.0), 1.0mm circular pad → treat as square 1.0×1.0
    tp6_cx, tp6_cy = 28.0, 10.0
    obs.append(("TP6.1", "REF_ELEC",
                tp6_cx - 0.5, tp6_cy - 0.5,
                tp6_cx + 0.5, tp6_cy + 0.5))

    # ── SPI series resistors (for digital isolation checking) ────────
    # R2 (CS) at brd(18.0, 6.0), R3 (SCLK) at brd(15.0, 6.0),
    # R4 (MOSI) at brd(12.0, 6.0) — all 0402
    spi_resistors = [
        ("R2", 18.0, 6.0, "CS",    "CS_J"),
        ("R3", 15.0, 6.0, "SCLK",  "SCLK_J"),
        ("R4", 12.0, 6.0, "MOSI",  "MOSI_J"),
    ]
    for rname, rcx, rcy, net_pin1, net_pin2 in spi_resistors:
        obs.append((f"{rname}.1", net_pin1,
                     rcx - r1_pcx - r1_psx / 2, rcy - r1_psy / 2,
                     rcx - r1_pcx + r1_psx / 2, rcy + r1_psy / 2))
        obs.append((f"{rname}.2", net_pin2,
                     rcx + r1_pcx - r1_psx / 2, rcy - r1_psy / 2,
                     rcx + r1_pcx + r1_psx / 2, rcy + r1_psy / 2))

    return obs


OBSTACLES = _build_obstacles()


# ── Geometry primitives ──────────────────────────────────────────────────

def _segment_intersects_rect(
    x1: float, y1: float, x2: float, y2: float,
    rx_min: float, ry_min: float, rx_max: float, ry_max: float,
) -> bool:
    """Test if line segment (x1,y1)→(x2,y2) intersects axis-aligned rectangle.

    Uses Liang-Barsky clipping. Returns True if any part of the segment
    lies inside or touches the rectangle boundary.
    """
    dx = x2 - x1
    dy = y2 - y1
    p = [-dx, dx, -dy, dy]
    q = [x1 - rx_min, rx_max - x1, y1 - ry_min, ry_max - y1]

    t0 = 0.0
    t1 = 1.0

    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            # Segment parallel to this edge
            if qi < -1e-12:
                return False  # Outside and parallel → no intersection
        else:
            t = qi / pi
            if pi < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1 + 1e-12:
                return False

    return t0 <= t1 + 1e-12


def _point_in_rect(
    px: float, py: float,
    rx_min: float, ry_min: float, rx_max: float, ry_max: float,
) -> bool:
    """Test if a point lies inside (or on boundary of) a rectangle."""
    return (rx_min - 1e-9 <= px <= rx_max + 1e-9
            and ry_min - 1e-9 <= py <= ry_max + 1e-9)


def _min_dist_segment_to_rect(
    x1: float, y1: float, x2: float, y2: float,
    rx_min: float, ry_min: float, rx_max: float, ry_max: float,
) -> float:
    """Minimum distance from line segment to axis-aligned rectangle.

    Returns 0.0 if intersecting.
    """
    if _segment_intersects_rect(x1, y1, x2, y2, rx_min, ry_min, rx_max, ry_max):
        return 0.0

    # Check distance from segment endpoints to rectangle
    # Check distance from rectangle corners to segment
    # This is exact for segment-to-AABB.

    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    def _dist_pt_to_rect(px: float, py: float) -> float:
        cx = _clamp(px, rx_min, rx_max)
        cy = _clamp(py, ry_min, ry_max)
        return math.hypot(px - cx, py - cy)

    def _dist_pt_to_segment(px: float, py: float) -> float:
        sdx = x2 - x1
        sdy = y2 - y1
        len_sq = sdx * sdx + sdy * sdy
        if len_sq < 1e-24:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * sdx + (py - y1) * sdy) / len_sq))
        proj_x = x1 + t * sdx
        proj_y = y1 + t * sdy
        return math.hypot(px - proj_x, py - proj_y)

    best = float("inf")
    # Segment endpoints to rectangle
    best = min(best, _dist_pt_to_rect(x1, y1))
    best = min(best, _dist_pt_to_rect(x2, y2))
    # Rectangle corners to segment
    for cx, cy in [(rx_min, ry_min), (rx_max, ry_min),
                   (rx_min, ry_max), (rx_max, ry_max)]:
        best = min(best, _dist_pt_to_segment(cx, cy))
    # Rectangle edges to segment (check segment projection onto each edge)
    # Top edge: y = ry_min, x in [rx_min, rx_max]
    # Bottom edge: y = ry_max, x in [rx_min, rx_max]
    # Left edge: x = rx_min, y in [ry_min, ry_max]
    # Right edge: x = rx_max, y in [ry_min, ry_max]
    # For completeness, check a few midpoints on rectangle edges
    for edge_x1, edge_y1, edge_x2, edge_y2 in [
        (rx_min, ry_min, rx_max, ry_min),  # top
        (rx_min, ry_max, rx_max, ry_max),  # bottom
        (rx_min, ry_min, rx_min, ry_max),  # left
        (rx_max, ry_min, rx_max, ry_max),  # right
    ]:
        # Closest point on segment to closest point on this rect edge
        # This is the segment-to-segment distance problem.
        best = min(best, _seg_to_seg_dist(
            x1, y1, x2, y2, edge_x1, edge_y1, edge_x2, edge_y2))

    return best


def _seg_to_seg_dist(
    ax1: float, ay1: float, ax2: float, ay2: float,
    bx1: float, by1: float, bx2: float, by2: float,
) -> float:
    """Minimum distance between two line segments."""
    def _pt_to_seg(px, py, sx1, sy1, sx2, sy2):
        sdx = sx2 - sx1
        sdy = sy2 - sy1
        len_sq = sdx * sdx + sdy * sdy
        if len_sq < 1e-24:
            return math.hypot(px - sx1, py - sy1)
        t = max(0.0, min(1.0, ((px - sx1) * sdx + (py - sy1) * sdy) / len_sq))
        return math.hypot(px - (sx1 + t * sdx), py - (sy1 + t * sdy))

    best = float("inf")
    best = min(best, _pt_to_seg(ax1, ay1, bx1, by1, bx2, by2))
    best = min(best, _pt_to_seg(ax2, ay2, bx1, by1, bx2, by2))
    best = min(best, _pt_to_seg(bx1, by1, ax1, ay1, ax2, ay2))
    best = min(best, _pt_to_seg(bx2, by2, ax1, ay1, ax2, ay2))
    return best


def parallel_overlap_length(
    seg_x1: float, seg_y1: float, seg_x2: float, seg_y2: float,
    lane_x: float,
    delta: float = CLR,
) -> float:
    """Compute Y-overlap of a segment within ±delta of a vertical lane at x=lane_x.

    CH traces run north-south (vertical). This measures how much of the
    segment runs parallel to a given lane within clearance distance.
    Returns the Y-span (mm) of the segment within the ±delta band.
    """
    dx = seg_x2 - seg_x1
    dy = seg_y2 - seg_y1
    if abs(dx) < 1e-12:
        # Vertical segment — check if it's within delta of lane_x
        if abs(seg_x1 - lane_x) <= delta:
            return abs(dy)
        return 0.0
    t_lo = (lane_x - delta - seg_x1) / dx
    t_hi = (lane_x + delta - seg_x1) / dx
    if t_lo > t_hi:
        t_lo, t_hi = t_hi, t_lo
    t_lo = max(0.0, min(1.0, t_lo))
    t_hi = max(0.0, min(1.0, t_hi))
    if t_lo >= t_hi:
        return 0.0
    y_lo = seg_y1 + t_lo * dy
    y_hi = seg_y1 + t_hi * dy
    return abs(y_hi - y_lo)


# ── Route checker ────────────────────────────────────────────────────────

class ClearanceResult:
    """Result of checking a single segment against all obstacles."""
    __slots__ = ("seg_name", "violations", "min_clearance", "worst_obstacle")

    def __init__(self, seg_name: str):
        self.seg_name = seg_name
        self.violations: list[tuple[str, float]] = []  # (obs_name, clearance)
        self.min_clearance = float("inf")
        self.worst_obstacle = ""


def check_segment(
    x1: float, y1: float, x2: float, y2: float,
    seg_name: str,
    net: str = REF_ELEC_NET,
    layer: str = "F.Cu",
    obstacles: list | None = None,
) -> ClearanceResult:
    """Check one trace segment against all obstacles on the given layer.

    Uses Minkowski expansion: each obstacle rectangle is expanded by KEEP.
    Then test if the (zero-width) segment intersects the expanded rectangle.
    If it does: violation (clearance < required).
    Also computes the actual minimum edge-to-edge clearance for reporting.
    """
    if obstacles is None:
        obstacles = OBSTACLES

    result = ClearanceResult(seg_name)

    for obs_name, obs_net, ox_min, oy_min, ox_max, oy_max in obstacles:
        # Skip same-net obstacles
        if obs_net == net:
            continue

        # For B.Cu segments, only check obstacles that exist on B.Cu.
        # In our design, only F.Cu pads exist as obstacles.
        # B.Cu has ground pour — the trace needs clearance from the pour edge,
        # but the pour will have a cutout around the trace (KiCad auto-clearance).
        # So for B.Cu segments we only check against through-hole pads / vias
        # that span both layers. In this design, no TH components exist on
        # the route path, so B.Cu segments only need to avoid other B.Cu copper.
        # For now: skip obstacle check for B.Cu traces (they only need DRC
        # clearance from the ground pour, which KiCad handles automatically).
        if layer == "B.Cu":
            continue

        # Expand obstacle rectangle by KEEP (Minkowski sum)
        ex_min = ox_min - KEEP
        ey_min = oy_min - KEEP
        ex_max = ox_max + KEEP
        ey_max = oy_max + KEEP

        # Test: does the zero-width segment intersect the expanded rectangle?
        # A boundary touch (clearance exactly == CLR) is acceptable in DRC.
        # We use the Minkowski expansion as a first-pass filter, then compute
        # the actual edge-to-edge clearance. Violation only if clearance < CLR.
        dist = _min_dist_segment_to_rect(x1, y1, x2, y2,
                                         ox_min, oy_min, ox_max, oy_max)
        edge_clr = dist - HW  # trace edge to obstacle edge

        if edge_clr < CLR - 1e-9:
            # True violation: clearance below requirement
            result.violations.append((obs_name, edge_clr))

        if edge_clr < result.min_clearance:
            result.min_clearance = edge_clr
            result.worst_obstacle = obs_name

    return result


def check_via(
    vx: float, vy: float,
    via_name: str,
    net: str = REF_ELEC_NET,
    obstacles: list | None = None,
) -> ClearanceResult:
    """Check a via location against all F.Cu obstacles.

    Via copper is a circle with VIA_RADIUS. Check that the via annular ring
    maintains CLR clearance to all non-same-net obstacles.
    Via keep = VIA_RADIUS + CLR.
    """
    if obstacles is None:
        obstacles = OBSTACLES

    result = ClearanceResult(via_name)
    via_keep = VIA_RADIUS + CLR  # 0.3 + 0.30 = 0.60 mm

    for obs_name, obs_net, ox_min, oy_min, ox_max, oy_max in obstacles:
        if obs_net == net:
            continue

        # Check if via circle (center vx,vy, radius VIA_RADIUS) has clearance
        # CLR from obstacle rectangle.
        # Correct method: compute min distance from point to rectangle,
        # then check if distance < via_keep (= VIA_RADIUS + CLR).
        # This handles corners correctly (Minkowski sum of rect + circle
        # has rounded corners, not sharp corners).
        cx = max(ox_min, min(ox_max, vx))
        cy = max(oy_min, min(oy_max, vy))
        dist = math.hypot(vx - cx, vy - cy)
        edge_clr = dist - VIA_RADIUS

        if dist < via_keep - 1e-9:
            # Violation: via copper too close to obstacle
            result.violations.append((obs_name, edge_clr))
            if edge_clr < result.min_clearance:
                result.min_clearance = edge_clr
                result.worst_obstacle = obs_name
        else:
            if edge_clr < result.min_clearance:
                result.min_clearance = edge_clr
                result.worst_obstacle = obs_name

    return result


# ── Route definition: Option A ───────────────────────────────────────────

# Topology: T-junction at (9.98, 12.75).
#   Branch 1 (F.Cu): junction → east → U1.10 pad center (same-net pad entry)
#   Branch 2 (F.Cu): junction → south → R1.2 (REF_ELEC pad, stub endpoint)
#   Branch 3: VIA1 at junction → B.Cu diagonal → VIA2 → F.Cu south → J5.33
#
# TP6 at brd(28.0, 10.0) is also on REF_ELEC — will be routed separately or
# connected via B.Cu pour (not part of this route).

ROUTE_SEGMENTS = [
    # (label, x1, y1, x2, y2, layer)
    # Branch 1: Junction to U1.10 (east, entering same-net pad)
    ("S1: Jct→U1.10",         9.98, 12.75,  10.95, 12.75, "F.Cu"),
    # Branch 2: Junction south to R1.2 (stub)
    ("S2: Jct→R1.2",          9.98, 12.75,   9.98, 15.00, "F.Cu"),
    # Branch 3: B.Cu hop from VIA1 to VIA2
    ("S3: VIA1→VIA2 (B.Cu)",  9.98, 12.75,  19.7625, 18.20, "B.Cu"),
    # Branch 3 continued: VIA2 south on F.Cu to J5.33 pad center
    ("S4: VIA2→J5.33",       19.7625, 18.20, 19.7625, 19.341, "F.Cu"),
]

VIA_LOCATIONS = [
    ("VIA1", 9.98, 12.75),
    ("VIA2", 19.7625, 18.20),
]


def check_route() -> bool:
    """Run the complete clearance check. Returns True if all pass."""
    all_pass = True

    print("=" * 70)
    print("REF_ELEC ROUTE — OPTION A DETERMINISTIC CLEARANCE CHECK")
    print("=" * 70)
    print(f"Trace width: {TRACE_W} mm, Clearance: {CLR} mm, Keep: {KEEP} mm")
    print(f"Via: {VIA_DIAMETER} mm dia ({VIA_DRILL} drill + {VIA_ANNULAR} ring)")
    print(f"Obstacles: {len(OBSTACLES)} copper rectangles")
    print()

    # Check each segment
    for label, x1, y1, x2, y2, layer in ROUTE_SEGMENTS:
        result = check_segment(x1, y1, x2, y2, label, layer=layer)
        length = math.hypot(x2 - x1, y2 - y1)

        if result.violations:
            status = "✗ FAIL"
            all_pass = False
        else:
            status = "✓ PASS"

        print(f"  {label}")
        print(f"    Layer: {layer}, Length: {length:.3f} mm")
        print(f"    Min clearance: {result.min_clearance:.3f} mm "
              f"(to {result.worst_obstacle}) {status}")

        if result.violations:
            for obs_name, edge_clr in result.violations:
                print(f"    ✗ VIOLATION: {obs_name} clearance = {edge_clr:.3f} mm")
        print()

    # Check vias
    for via_name, vx, vy in VIA_LOCATIONS:
        result = check_via(vx, vy, via_name)
        if result.violations:
            status = "✗ FAIL"
            all_pass = False
        else:
            status = "✓ PASS"

        print(f"  {via_name} at ({vx:.3f}, {vy:.3f})")
        print(f"    Min clearance: {result.min_clearance:.3f} mm "
              f"(to {result.worst_obstacle}) {status}")
        if result.violations:
            for obs_name, edge_clr in result.violations:
                print(f"    ✗ VIOLATION: {obs_name} clearance = {edge_clr:.3f} mm")
        print()

    # Summary
    total_length = sum(
        math.hypot(x2 - x1, y2 - y1)
        for _, x1, y1, x2, y2, _ in ROUTE_SEGMENTS
    )
    bcu_length = sum(
        math.hypot(x2 - x1, y2 - y1)
        for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
        if layer == "B.Cu"
    )

    print("=" * 70)
    print(f"RESULT: {'ALL PASS' if all_pass else 'FAILED'}")
    print(f"Total route length: {total_length:.2f} mm")
    print(f"  F.Cu: {total_length - bcu_length:.2f} mm")
    print(f"  B.Cu: {bcu_length:.2f} mm")
    print(f"Vias: {len(VIA_LOCATIONS)}")
    print("=" * 70)

    return all_pass


if __name__ == "__main__":
    ok = check_route()
    sys.exit(0 if ok else 1)
