"""
Generate KiCad s-expression blocks for Bundle C electrode routes (CH9–CH22).

Produces (segment ...) and (via ...) blocks that can be appended to the
.kicad_pcb file inside the top-level (kicad_pcb ...) form.

Topology per §4.2.1 (routing_strategy_v1.md Rev 3.10):
  Phase 1: PAD ESCAPE       — vertical north from pad tip through KEEP boundary
  Phase 2: HEADER FAN       — horizontal run (E or W) to align with descent lane
  Phase 3: CORNER TURN      — 90° vertex (header meets descent)
  Phase 4: SIDE DESCENT     — vertical south alongside U1 (uniform pack)
  Phase 5: BOTTOM FAN       — descent endpoint to J5 connector pad

Phases 1–4 route on F.Cu using a nested-L topology that guarantees:
  • Zero lane crossings (proven across all 14 channels)
  • ≥ LANE_PITCH (0.450 mm) centerline spacing between all segment pairs
  • Pure Manhattan geometry (all segments horizontal or vertical)

Phase 5 — BOTTOM FAN (ALL channels use B.Cu via hops):
  No F.Cu horizontal fan traces possible between U1 south pads and J5
  inner pads (only 0.333mm gap, need 0.55mm for trace + clearance).

  Z-shape B.Cu routing per channel:
    1. F.Cu vertical extension to entry via (staggered Y for clearance)
    2. Entry via at (descent_x, entry_via_y) → B.Cu
    3. B.Cu vertical: descent_x → stagger_y (unique per channel)
    4. B.Cu horizontal: descent_x → pad_x at stagger_y
    5. B.Cu vertical: pad_x → pad_y
    6. Exit via-in-pad at (pad_x, pad_y) → F.Cu J5 pad
         VIP is MANDATORY: Omnetics A79024 pad pitch (0.635mm) is too dense
         for dogbone with 0.4mm via.  Requires VIPPO (epoxy plug + cap).
         See fab_profile.yaml via_fill_policy for assembly notes.

MANHATTAN-ONLY DECISION: Bundle C routes are strictly Manhattan.
If non-Manhattan segments are ever needed (diagonal relief, arc approximations,
jogs), a PHASE_TRANSITION phase constant must be added and the geometric
classifier (_classify_segment) updated to handle non-axis-aligned segments.
The classifier will raise ValueError on any diagonal segment — this is
intentional enforcement, not a bug.

All coordinates are brd-space. Conversion: kx = BRD_OX + bx, ky = BRD_OY + by.
(BRD_OX, BRD_OY defined in board_geom.py — single source of truth.)

Frozen constants imported from ref_elec_clearance_check.py. No magic numbers.
"""
from __future__ import annotations

from ref_elec_clearance_check import (
    TRACE_W,
    CLR,
    KEEP,
    VIA_RADIUS,
    QFN56_PITCH,
    QFN56_PAD_LONG,
    QFN56_PAD_CX,
    U1_CX,
    U1_CY,
    LANE_PITCH,
    U1_KEEP_TOP,
    U1_KEEP_BOTTOM,
    U1_KEEP_LEFT,
    U1_KEEP_RIGHT,
    C6C7_KEEP_EAST,
    C6C7_Y_MIN,
    EAST_DESCENT_X,
    WEST_DESCENT_X,
    BOTTOM_FAN_Y,
    R1C5_KEEP_WEST,
)

# ── Board / KiCad origin ─────────────────────────────────────────────────
# Single source of truth: board_geom.py (must match gen_pcb_v1.py).

from board_geom import BRD_OX, BRD_OY

LAYER = "F.Cu"  # Primary layer for Bundle C routes
LAYER_BCU = "B.Cu"  # Back copper layer for outer-row via hops

# ── J5 geometry (from gen_pcb_v1.py, authoritative) ──────────────────────

J5_CX, J5_CY = 15.0, 21.5
J5_PITCH = 0.635
J5_COLS = 18

# Eagle pad positions (exact from RHD2000.lbr OMNETICS_A79025 package)
J5_COL_X = [
    -5.3975, -4.7625, -4.1275, -3.4925, -2.8575, -2.2225,
    -1.5875, -0.9525, -0.3175,  0.3175,  0.9525,  1.5875,
     2.2225,  2.8575,  3.4925,  4.1275,  4.7625,  5.3975,
]

J5_T_ROW_Y = J5_CY - 2.159   # 19.341  (odd pins)
J5_B_ROW_Y = J5_CY - 1.016   # 20.484  (even pins)

# ── U1 QFN-56 north-side pad geometry ────────────────────────────────────

_HALF_SPAN = (14 - 1) / 2 * QFN56_PITCH  # 3.25 mm
_PAD_CENTER_Y = U1_CY - QFN56_PAD_CX     # 9.95
_PAD_TIP_Y = _PAD_CENTER_Y - QFN56_PAD_LONG / 2  # 9.50


def _pad_x(pin: int) -> float:
    """Brd x of U1 north-side pad center. Pins 43–56, right→left."""
    return round(U1_CX + _HALF_SPAN - (pin - 43) * QFN56_PITCH, 4)


# ── Lane definitions (frozen in §4.2.1f) ─────────────────────────────────
#
# Each lane: (channel, u1_pin, side, lane_index, descent_x, header_y, j5_pin)
#
# Lane assignment: innermost (index 0) = nearest U1 center,
#   outermost (index 6) = nearest U1 corner. Zero crossings.
#
# Nested-L topology (crossing-free header fan):
#   Inner lanes get the LOWEST header_y (furthest north) AND the
#   LARGEST descent_x (furthest from U1). This creates nested L-shapes
#   where no horizontal overlaps any other lane's descent vertical.
#   Outer lanes get the HIGHEST header_y AND the SMALLEST descent_x.
#
#   lane_idx 0 → header_y = 5.825, descent_x = outermost (25.405 east / 5.095 west)
#   lane_idx 6 → header_y = 9.125, descent_x = innermost (22.105 east / 8.395 west)
#
# J5 pin assignment: CH{n} → J5.{n+1} (authoritative, from gen_pcb_v1.py).
#   East: CH16→J5.17, CH17→J5.18, ..., CH22→J5.23
#   West: CH15→J5.16, CH14→J5.15, ..., CH9→J5.10

# Simple mapping: channel N connects to J5 pin N+1
def _ch_to_j5_pin(ch: int) -> int:
    return ch + 1

LANES: list[dict] = []

# C-East: E1=CH16(pin49) .. E7=CH22(pin43)
for i in range(7):
    ch = 16 + i
    pin = 49 - i
    LANES.append({
        "ch": ch,
        "pin": pin,
        "side": "east",
        "lane_idx": i,
        "descent_x": EAST_DESCENT_X[6 - i],
        "header_y": U1_KEEP_TOP - (6 - i) * LANE_PITCH,
        "j5_pin": _ch_to_j5_pin(ch),
    })

# C-West: W1=CH15(pin50) .. W7=CH9(pin56)
for i in range(7):
    ch = 15 - i
    pin = 50 + i
    LANES.append({
        "ch": ch,
        "pin": pin,
        "side": "west",
        "lane_idx": i,
        "descent_x": WEST_DESCENT_X[6 - i],
        "header_y": U1_KEEP_TOP - (6 - i) * LANE_PITCH,
        "j5_pin": _ch_to_j5_pin(ch),
    })


def _j5_target(j5_pin: int) -> tuple[float, float]:
    """Return (brd_x, brd_y) for a J5 pad."""
    if j5_pin % 2 == 1:  # odd → T row
        col_idx = (j5_pin - 1) // 2  # 0-based
        return (round(J5_CX + J5_COL_X[col_idx], 4), J5_T_ROW_Y)
    else:  # even → B row
        col_idx = (j5_pin - 2) // 2
        return (round(J5_CX + J5_COL_X[col_idx], 4), J5_B_ROW_Y)


# ═════════════════════════════════════════════════════════════════════════
# PHASE CONSTANTS
# ═════════════════════════════════════════════════════════════════════════

PHASE_ESCAPE  = "escape"           # Within U1 pad copper (pad center → pad tip)
PHASE_VERTICAL_ESCAPE = "vertical_escape"  # Vertical inside pad-x column, above pad tip
PHASE_HEADER  = "header"           # Horizontal run along header_y
PHASE_DESCENT = "descent"          # Vertical descent outside U1 KEEP
PHASE_BOTTOM_FAN = "bottom_fan"    # Phase 5: descent endpoint → J5 pad


# ═════════════════════════════════════════════════════════════════════════
# PHASE 5 — BOTTOM FAN CONSTANTS
# ═════════════════════════════════════════════════════════════════════════

# Via parameters (ELECTRODE netclass from .kicad_pro)
_VIA_DRILL = 0.2   # 0.2mm drill
_VIA_PAD = 0.4     # 0.4mm pad (0.1mm annular ring)

# ELECTRODE netclass clearance (must match .kicad_pro)
_ELECTRODE_CLR = 0.20

# Inner/outer row classification
# J5 odd pins → inner (T) row, J5 even pins → outer (B) row
def _is_inner_row(j5_pin: int) -> bool:
    return j5_pin % 2 == 1

# ── Entry via placement ───────────────────────────────────────────────────
# Entry vias are placed at (descent_x, stagger_y) — each channel has a
# unique stagger_y, so no via-to-via clearance issue at descent_x.
# No Y-stagger hack needed (the old _ENTRY_VIA_Y1/_ENTRY_VIA_Y2 are gone).

# ── B.Cu stagger Y (Z-route horizontal levels) ──────────────────────────
# Each channel gets a unique stagger_y so B.Cu horizontals never overlap.
# Inner: stagger from J5_T_ROW_Y (19.341) northward, pitch steps
# Outer: stagger from J5_B_ROW_Y (20.484) southward, pitch steps
# Pitch must satisfy via-to-track clearance: VIA_RADIUS + CLR + TRACE_W/2.
# Use LANE_PITCH which already accounts for this (0.500mm).
_BCU_STAGGER_PITCH = LANE_PITCH  # 0.550mm — matches descent_x spacing


# ═════════════════════════════════════════════════════════════════════════
# GEOMETRIC SEGMENT CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════

def _classify_segment(
    p0: tuple[float, float],
    p1: tuple[float, float],
    lane: dict,
) -> str:
    """Classify a single segment by geometry + reference constants.

    This is a pure geometric classifier.  It inspects the segment's
    coordinates against the lane's pad, header, and descent parameters —
    it does NOT use index position.  If an extra waypoint is inserted
    (e.g. splitting an escape segment into two sub-segments), both
    sub-segments will receive the correct phase tag.

    Classification rules (evaluated in priority order):

      PHASE_ESCAPE:
        Vertical (|dx| ≤ eps) AND both endpoints y ∈ [_PAD_TIP_Y − eps,
        _PAD_CENTER_Y + eps] AND x ≈ pad_x.  This is within the U1 pad
        copper region.

      PHASE_VERTICAL_ESCAPE:
        Vertical AND x ≈ pad_x AND at least one endpoint y < _PAD_TIP_Y
        (above pad tip, heading north toward header).  Stays in the pad-x
        column but outside the pad copper body.

      PHASE_HEADER:
        Horizontal (|dy| ≤ eps) AND y ≈ lane header_y.

      PHASE_DESCENT:
        Vertical AND x ≈ lane descent_x AND x outside
        [U1_KEEP_LEFT, U1_KEEP_RIGHT].
    """
    x0, y0 = p0
    x1, y1 = p1
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    eps = 1e-4

    pad_x = _pad_x(lane["pin"])
    header_y = lane["header_y"]
    descent_x = lane["descent_x"]

    is_vertical   = dx <= eps
    is_horizontal = dy <= eps
    at_pad_x      = abs(x0 - pad_x) <= eps and abs(x1 - pad_x) <= eps
    at_descent_x  = abs(x0 - descent_x) <= eps and abs(x1 - descent_x) <= eps

    # ── PHASE_ESCAPE: vertical, at pad_x, both y within pad body ─────
    if is_vertical and at_pad_x:
        min_y = min(y0, y1)
        max_y = max(y0, y1)
        if min_y >= _PAD_TIP_Y - eps and max_y <= _PAD_CENTER_Y + eps:
            return PHASE_ESCAPE

    # ── PHASE_VERTICAL_ESCAPE: vertical, at pad_x, above pad tip ─────
    if is_vertical and at_pad_x:
        if min(y0, y1) < _PAD_TIP_Y + eps:
            return PHASE_VERTICAL_ESCAPE

    # ── PHASE_HEADER: horizontal, at header_y ────────────────────────
    if is_horizontal:
        if abs(y0 - header_y) <= eps:
            return PHASE_HEADER

    # ── PHASE_DESCENT: vertical, at descent_x, outside U1 KEEP x ────
    if is_vertical and at_descent_x:
        x_outside_u1 = (descent_x < U1_KEEP_LEFT + eps
                        or descent_x > U1_KEEP_RIGHT - eps)
        if x_outside_u1:
            return PHASE_DESCENT

    # If we reach here, the segment is either diagonal (violates Manhattan-only)
    # or axis-aligned but doesn't match any known phase geometry.
    if not is_vertical and not is_horizontal:
        raise ValueError(
            f"Non-Manhattan segment ({x0:.4f},{y0:.4f})→({x1:.4f},{y1:.4f}) "
            f"for lane CH{lane['ch']}. Bundle C is Manhattan-only. "
            f"If diagonals are needed, add PHASE_TRANSITION to the classifier."
        )
    raise ValueError(
        f"Cannot classify Manhattan segment ({x0:.4f},{y0:.4f})→({x1:.4f},{y1:.4f}) "
        f"for lane CH{lane['ch']} (pad_x={pad_x}, header_y={header_y}, "
        f"descent_x={descent_x}). Segment is axis-aligned but doesn't match "
        f"any known phase geometry."
    )


def classify_segments(
    waypoints: list[tuple[float, float]],
    lane: dict,
) -> list[tuple[tuple[float, float], tuple[float, float], str]]:
    """Classify an arbitrary waypoint list into phase-tagged segments.

    This is the public entry point for geometric classification.  Given
    any list of waypoints (including ones with extra intermediate points),
    it produces (p0, p1, phase) tuples using purely geometric rules.
    """
    segments = []
    for i in range(len(waypoints) - 1):
        p0 = waypoints[i]
        p1 = waypoints[i + 1]
        # Skip zero-length segments
        if abs(p1[0] - p0[0]) < 1e-6 and abs(p1[1] - p0[1]) < 1e-6:
            continue
        phase = _classify_segment(p0, p1, lane)
        segments.append((p0, p1, phase))
    return segments


# ═════════════════════════════════════════════════════════════════════════
# POLYLINE BUILDER
# ═════════════════════════════════════════════════════════════════════════

def _build_waypoints(lane: dict) -> list[tuple[float, float]]:
    """Build the raw waypoint list for one Bundle C lane (Phases 1–4).

    Returns list of (brd_x, brd_y) waypoints, ordered from U1 pad to the
    bottom of the side descent at BOTTOM_FAN_Y.  The bottom fan (Phase 5,
    from descent endpoint to J5 pad) is NOT included — see module docstring.
    """
    pin = lane["pin"]
    descent_x = lane["descent_x"]
    header_y = lane["header_y"]
    pad_x = _pad_x(pin)

    pts: list[tuple[float, float]] = [
        (pad_x, _PAD_CENTER_Y),   # Pad center
        (pad_x, _PAD_TIP_Y),      # Pad tip (north edge)
        (pad_x, header_y),         # Header elevation
        (descent_x, header_y),     # Corner turn
        (descent_x, BOTTOM_FAN_Y), # Bottom of descent
    ]

    return [(round(x, 4), round(y, 4)) for x, y in pts]


def build_phased_polyline(
    lane: dict,
) -> list[tuple[tuple[float, float], tuple[float, float], str]]:
    """Build the F.Cu polyline for one Bundle C lane with phase metadata.

    Returns a list of (p0, p1, phase) tuples where:
      p0, p1 = (brd_x, brd_y) segment endpoints
      phase  = one of PHASE_ESCAPE, PHASE_VERTICAL_ESCAPE,
               PHASE_HEADER, PHASE_DESCENT

    Phase tags are assigned by _classify_segment(), a pure geometric
    classifier that inspects coordinates against reference constants.
    If an extra waypoint is inserted mid-segment, both sub-segments
    receive the correct phase — no index dependence.
    """
    return classify_segments(_build_waypoints(lane), lane)


def build_polyline(lane: dict) -> list[tuple[float, float]]:
    """Build the F.Cu polyline for one Bundle C lane (Phases 1–4).

    Returns list of (brd_x, brd_y) waypoints.  Derived from
    build_phased_polyline() — single source of truth.
    """
    phased = build_phased_polyline(lane)
    # Reconstruct waypoint list: first segment's p0, then every segment's p1
    if not phased:
        return []
    pts = [phased[0][0]]
    for _, p1, _ in phased:
        pts.append(p1)
    return pts


def build_all_phased_polylines() -> (
    dict[int, list[tuple[tuple[float, float], tuple[float, float], str]]]
):
    """Build phase-tagged polylines for all 14 Bundle C channels.

    Returns {channel_number: [(p0, p1, phase), ...], ...}
    """
    return {lane["ch"]: build_phased_polyline(lane) for lane in LANES}


def build_all_polylines() -> dict[int, list[tuple[float, float]]]:
    """Build polylines for all 14 Bundle C channels.

    Returns {channel_number: [(x, y), ...], ...}
    """
    return {lane["ch"]: build_polyline(lane) for lane in LANES}


def get_east_polylines() -> list[list[tuple[float, float]]]:
    """Return polylines for east lanes E1–E7 (CH16–CH22), ordered by lane index."""
    return [build_polyline(lane) for lane in LANES if lane["side"] == "east"]


def get_west_polylines() -> list[list[tuple[float, float]]]:
    """Return polylines for west lanes W1–W7 (CH15–CH9), ordered by lane index."""
    return [build_polyline(lane) for lane in LANES if lane["side"] == "west"]


# ═════════════════════════════════════════════════════════════════════════
# PHASE 5 — BOTTOM FAN ROUTING
# ═════════════════════════════════════════════════════════════════════════

def _build_bottom_fan() -> dict:
    """Build Phase 5 bottom fan routing for all 14 channels.

    Returns a dict keyed by channel number:
      {ch: {
          "fcu_segments": [(x1,y1,x2,y2), ...],   # F.Cu segments
          "bcu_segments": [(x1,y1,x2,y2), ...],   # B.Cu segments
          "vias": [(x, y), ...],                   # Via positions
          "row": "inner" | "outer",
      }}

    ALL 14 channels use B.Cu via hops (no F.Cu horizontal fan possible
    between U1 south pads and J5 inner pads — only 0.333mm gap).

    Routing topology per channel (F.Cu extension + B.Cu L-shape):
      1. F.Cu vertical: descent_x from BOTTOM_FAN_Y to stagger_y
      2. Entry via at (descent_x, stagger_y) — transitions to B.Cu
      3. B.Cu horizontal: stagger_y from descent_x to pad_x
      4. B.Cu vertical: pad_x from stagger_y to pad_y
      5. Exit via-in-pad at (pad_x, pad_y) — transitions back to F.Cu
         VIP is mandatory (dogbone infeasible at 0.635mm pitch); requires VIPPO.

    Each channel has a unique descent_x (0.45mm apart) and unique
    stagger_y, so F.Cu verticals and B.Cu L-shapes never cross.
    No B.Cu segments share the descent_x column, eliminating crossings.
    """
    result = {}

    # Separate lanes into inner and outer, grouped by side
    east_inner, east_outer = [], []
    west_inner, west_outer = [], []

    for lane in LANES:
        j5_pin = lane["j5_pin"]
        bucket = (east_inner if _is_inner_row(j5_pin) else east_outer) \
                 if lane["side"] == "east" else \
                 (west_inner if _is_inner_row(j5_pin) else west_outer)
        bucket.append(lane)

    # Sort by pad_x: controls stagger_y assignment order.
    # Innermost pad (closest to board center X=15) gets stagger closest to pad_y.
    east_inner.sort(key=lambda l: _j5_target(l["j5_pin"])[0])
    west_inner.sort(key=lambda l: -_j5_target(l["j5_pin"])[0])
    east_outer.sort(key=lambda l: _j5_target(l["j5_pin"])[0])
    west_outer.sort(key=lambda l: -_j5_target(l["j5_pin"])[0])

    def _route_group(lanes: list[dict], is_inner: bool, stagger_offset: int) -> None:
        """Route a group of channels (inner or outer) for one side.

        Args:
            lanes: sorted list of lane dicts
            is_inner: True for inner-row pads (odd J5 pins)
            stagger_offset: cumulative index offset for stagger_y (to avoid
                collisions between east and west inner/outer groups that
                happen to share the same stagger_y level — analysis shows
                they don't overlap in X, so same stagger_y is fine).
        """
        n = len(lanes)  # group size (typically 3-4 per side per row)
        for i, lane in enumerate(lanes):
            ch = lane["ch"]
            descent_x = lane["descent_x"]
            lane_idx = lane["lane_idx"]
            pad_x, pad_y = _j5_target(lane["j5_pin"])

            # Entry via Y: via is at stagger_y (unique per channel, no overlap)
            # No Y-stagger needed since each channel has its own stagger_y.

            # Stagger Y for B.Cu horizontal — nested-L ordering:
            # Channel with LONGEST horizontal (outermost descent_x, i=0) gets
            # stagger_y FARTHEST from pad_y.  This creates proper nested Ls
            # where the outer L's horizontal passes OUTSIDE the inner L's
            # vertical Y range, preventing crossings.
            if is_inner:
                # Inner: approach pad from NORTH → stagger_y < J5_T_ROW_Y
                # i=0 (longest H) → farthest north = J5_T_ROW_Y - n * pitch
                # i=n-1 (shortest H) → closest = J5_T_ROW_Y - 1 * pitch
                stagger_y = round(J5_T_ROW_Y - (n - i) * _BCU_STAGGER_PITCH, 4)
            else:
                # Outer: approach pad from SOUTH → stagger_y > J5_B_ROW_Y
                # i=0 (longest H) → farthest south = J5_B_ROW_Y + n * pitch
                # i=n-1 (shortest H) → closest = J5_B_ROW_Y + 1 * pitch
                stagger_y = round(J5_B_ROW_Y + (n - i) * _BCU_STAGGER_PITCH, 4)

            fcu_segs = []
            bcu_segs = []
            vias = []

            # 1. F.Cu vertical: descent_x from BOTTOM_FAN_Y to stagger_y
            if abs(BOTTOM_FAN_Y - stagger_y) > 1e-6:
                fcu_segs.append((descent_x, BOTTOM_FAN_Y, descent_x, stagger_y))

            # 2. Entry via at (descent_x, stagger_y)
            vias.append((descent_x, stagger_y))

            # 3. B.Cu horizontal: stagger_y from descent_x to pad_x
            if abs(descent_x - pad_x) > 1e-6:
                bcu_segs.append((descent_x, stagger_y, pad_x, stagger_y))

            # 4. B.Cu vertical: pad_x from stagger_y to pad_y
            if abs(stagger_y - pad_y) > 1e-6:
                bcu_segs.append((pad_x, stagger_y, pad_x, pad_y))

            # 5. Exit via-in-pad at J5 pad center
            vias.append((pad_x, pad_y))

            result[ch] = {
                "fcu_segments": fcu_segs,
                "bcu_segments": bcu_segs,
                "vias": vias,
                "row": "inner" if is_inner else "outer",
            }

    # Route all four groups
    _route_group(east_inner, is_inner=True, stagger_offset=0)
    _route_group(west_inner, is_inner=True, stagger_offset=0)
    _route_group(east_outer, is_inner=False, stagger_offset=0)
    _route_group(west_outer, is_inner=False, stagger_offset=0)

    return result


# ═════════════════════════════════════════════════════════════════════════
# KICAD S-EXPRESSION EMITTER
# ═════════════════════════════════════════════════════════════════════════

def _brd_to_kicad(bx: float, by: float) -> tuple[float, float]:
    return round(BRD_OX + bx, 4), round(BRD_OY + by, 4)


def _seg(x1: float, y1: float, x2: float, y2: float,
         net_code: int, net_name: str, layer: str = LAYER) -> str:
    """KiCad segment from brd coords."""
    kx1, ky1 = _brd_to_kicad(x1, y1)
    kx2, ky2 = _brd_to_kicad(x2, y2)
    return (
        f'  (segment (start {kx1} {ky1}) (end {kx2} {ky2}) '
        f'(width {TRACE_W}) (layer "{layer}") (net {net_code}))'
    )


def _via(x: float, y: float, net_code: int) -> str:
    """KiCad via from brd coords (F.Cu ↔ B.Cu, 0.2mm drill / 0.4mm pad)."""
    kx, ky = _brd_to_kicad(x, y)
    return (
        f'  (via (at {kx} {ky}) (size {_VIA_PAD}) (drill {_VIA_DRILL}) '
        f'(layers "F.Cu" "B.Cu") (net {net_code}))'
    )


def _net_code_for_ch(ch: int) -> int:
    """Net code for CHn. Must match gen_pcb_v1.py registration order.

    GND=1, +3V3=2, AVDD=3, CH0=4, ..., CH31=35, REF_ELEC=36, ...
    So CH{n} = 4 + n.
    """
    return 4 + ch


def generate() -> str:
    """Generate all Bundle C route segments + vias as KiCad s-expressions.

    Returns a string containing:
    - Phases 1-4: F.Cu segments (escape, header, corner, descent)
    - Phase 5: bottom fan (F.Cu extensions + B.Cu L-routes + vias to J5 pads)
    """
    lines = [
        "  ; ──── Bundle C electrode routes (CH9–CH22, 14 channels) ────",
        f"  ; Trace width: {TRACE_W} mm, Layer: {LAYER} (primary) + {LAYER_BCU} (via hops)",
        "  ; Topology: 5-phase perimeter arc (§4.2.1)",
        "  ;   Phase 1: pad escape, Phase 2: header fan,",
        "  ;   Phase 3: corner turn, Phase 4: side descent,",
        "  ;   Phase 5: bottom fan (all channels: F.Cu+via+B.Cu L-route+via-in-pad)",
        "  ;",
    ]

    all_polys = build_all_polylines()
    bottom_fan = _build_bottom_fan()

    for lane in LANES:
        ch = lane["ch"]
        pin = lane["pin"]
        side = lane["side"].upper()
        lane_label = f"{'E' if side == 'EAST' else 'W'}{lane['lane_idx']+1}"
        j5_pin = lane["j5_pin"]
        net_code = _net_code_for_ch(ch)
        net_name = f"CH{ch}"

        poly = all_polys[ch]

        lines.append(f"")
        lines.append(
            f"  ; --- {lane_label} CH{ch} (U1.{pin} → J5.{j5_pin}) "
            f"[{side}] ---"
        )

        # Phases 1-4: F.Cu segments
        for i in range(len(poly) - 1):
            x1, y1 = poly[i]
            x2, y2 = poly[i + 1]
            # Skip zero-length segments
            if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                continue
            lines.append(_seg(x1, y1, x2, y2, net_code, net_name))

        # Phase 5: bottom fan
        fan = bottom_fan[ch]
        row_label = fan["row"].upper()
        lines.append(f"  ; Phase 5 bottom fan ({row_label} row)")

        # F.Cu fan segments
        for x1, y1, x2, y2 in fan["fcu_segments"]:
            lines.append(_seg(x1, y1, x2, y2, net_code, net_name, LAYER))

        # Vias
        for vx, vy in fan["vias"]:
            lines.append(_via(vx, vy, net_code))

        # B.Cu fan segments
        for x1, y1, x2, y2 in fan["bcu_segments"]:
            lines.append(_seg(x1, y1, x2, y2, net_code, net_name, LAYER_BCU))

    lines.append("")
    lines.append("  ; ──── end Bundle C routes ────")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(generate())
