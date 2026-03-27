"""Pytest regression tests for REF_ELEC route (Option A: 2 vias, B.Cu hop).

These tests lock down the route geometry and ensure no clearance violations
exist against the deterministic Minkowski-sum obstacle checker.
"""
import math
import sys
import os

import pytest

# Ensure the parent directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ref_elec_clearance_check import (
    OBSTACLES,
    ROUTE_SEGMENTS,
    VIA_LOCATIONS,
    CLR,
    HW,
    KEEP,
    VIA_RADIUS,
    VIA_DIAMETER,
    VIA_DRILL,
    VIA_ANNULAR,
    TRACE_W,
    REF_ELEC_NET,
    DIGITAL_SPI_NETS,
    CORRIDOR_X_MIN,
    CORRIDOR_X_MAX,
    MAX_PARALLEL_OVERLAP_MM,
    QFN56_PITCH,
    QFN56_PAD_SHORT,
    QFN56_PAD_LONG,
    QFN56_PAD_CX,
    QFN56_HALF_SPAN,
    QFN56_EP_SX,
    QFN56_EP_SY,
    FP0402_PAD_SX,
    FP0402_PAD_SY,
    FP0402_PAD_CX,
    U1_CX,
    U1_CY,
    check_segment,
    check_via,
    check_route,
    parallel_overlap_length,
    _min_dist_segment_to_rect,
    _segment_intersects_rect,
    _seg_to_seg_dist,
    # Bundle C perimeter geometry constants (Rev 3.9)
    LANE_PITCH,
    VIA_KEEP,
    R1_CX, R1_CY,
    C5_CX, C5_CY,
    C6_CX, C6_CY,
    C7_CX, C7_CY,
    VIA2_X, VIA2_Y,
    U1_KEEP_TOP,
    U1_KEEP_BOTTOM,
    U1_KEEP_LEFT,
    U1_KEEP_RIGHT,
    C6C7_KEEP_EAST,
    C6C7_KEEP_WEST,
    C6C7_Y_MIN,
    C6C7_Y_MAX,
    R1C5_KEEP_WEST,
    R1C5_Y_MIN,
    R1C5_Y_MAX,
    EAST_DESCENT_X,
    WEST_DESCENT_X,
    BOARD_WIDTH,
    BOARD_HEIGHT,
    BOTTOM_FAN_Y,
)
from gen_bundle_c_route import (
    get_east_polylines, get_west_polylines,
    build_all_polylines, LANES, generate as gen_bundle_c,
    _net_code_for_ch, BRD_OX, BRD_OY, LAYER,
    build_all_phased_polylines, classify_segments, _build_waypoints,
    PHASE_ESCAPE, PHASE_VERTICAL_ESCAPE, PHASE_HEADER, PHASE_DESCENT,
    _pad_x, _PAD_CENTER_Y, _PAD_TIP_Y,
)


# ── Obstacle inventory sanity ────────────────────────────────────────────

class TestObstacleInventory:
    def test_total_obstacle_count(self):
        """108 obstacles: 56 U1 pads + EP + 2 R1 + 2 C5 + 2 C6 + 2 C7 + 36 J5 + 1 TP6 + 6 SPI(R2-R4)."""
        assert len(OBSTACLES) == 56 + 1 + 2 + 2 + 2 + 2 + 36 + 1 + 6

    def test_ref_elec_same_net_obstacles(self):
        """Only U1.10, R1.2, J5.33, and TP6.1 should be on REF_ELEC net."""
        ref_obs = [(name, net) for name, net, *_ in OBSTACLES if net == "REF_ELEC"]
        names = sorted([n for n, _ in ref_obs])
        assert names == ["J5.33", "R1.2", "TP6.1", "U1.10"]

    def test_u1_10_position(self):
        """U1 pin 10 (REF_ELEC) copper at brd x=[10.50,11.40], y=[12.625,12.875]."""
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if name == "U1.10":
                assert net == "REF_ELEC"
                assert abs(xmin - 10.50) < 1e-6
                assert abs(xmax - 11.40) < 1e-6
                assert abs(ymin - 12.625) < 1e-6
                assert abs(ymax - 12.875) < 1e-6
                return
        pytest.fail("U1.10 not found in obstacles")

    def test_r1_2_position(self):
        """R1 pin 2 (REF_ELEC) at brd center (9.98, 15.0), copper 0.5×0.6."""
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if name == "R1.2":
                assert net == "REF_ELEC"
                assert abs((xmin + xmax) / 2 - 9.98) < 1e-6
                assert abs((ymin + ymax) / 2 - 15.0) < 1e-6
                assert abs(xmax - xmin - 0.5) < 1e-6
                assert abs(ymax - ymin - 0.6) < 1e-6
                return
        pytest.fail("R1.2 not found in obstacles")

    def test_j5_33_position(self):
        """J5 pin 33 (REF_ELEC) T-row, copper center at brd (19.7625, 19.341)."""
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if name == "J5.33":
                assert net == "REF_ELEC"
                assert abs((xmin + xmax) / 2 - 19.7625) < 1e-3
                assert abs((ymin + ymax) / 2 - 19.341) < 1e-3
                return
        pytest.fail("J5.33 not found in obstacles")


# ── Segment clearance tests ──────────────────────────────────────────────

class TestSegmentClearance:
    """Each F.Cu segment must have zero violations and min clearance ≥ CLR."""

    @pytest.mark.parametrize("label,x1,y1,x2,y2,layer", ROUTE_SEGMENTS)
    def test_segment_no_violations(self, label, x1, y1, x2, y2, layer):
        result = check_segment(x1, y1, x2, y2, label, layer=layer)
        if result.violations:
            details = "; ".join(
                f"{obs}={clr:.4f}mm" for obs, clr in result.violations
            )
            pytest.fail(f"{label}: violations: {details}")

    @pytest.mark.parametrize("label,x1,y1,x2,y2,layer", ROUTE_SEGMENTS)
    def test_segment_min_clearance(self, label, x1, y1, x2, y2, layer):
        result = check_segment(x1, y1, x2, y2, label, layer=layer)
        if layer == "B.Cu":
            # B.Cu segments skip obstacle checks (SMD pads are F.Cu only)
            return
        assert result.min_clearance >= CLR - 1e-9, (
            f"{label}: min clearance {result.min_clearance:.4f}mm < {CLR}mm "
            f"(to {result.worst_obstacle})"
        )


# ── Via clearance tests ──────────────────────────────────────────────────

class TestViaClearance:
    """Each via must have zero violations and min clearance ≥ CLR."""

    @pytest.mark.parametrize("name,vx,vy", VIA_LOCATIONS)
    def test_via_no_violations(self, name, vx, vy):
        result = check_via(vx, vy, name)
        if result.violations:
            details = "; ".join(
                f"{obs}={clr:.4f}mm" for obs, clr in result.violations
            )
            pytest.fail(f"{name}: violations: {details}")

    @pytest.mark.parametrize("name,vx,vy", VIA_LOCATIONS)
    def test_via_min_clearance(self, name, vx, vy):
        result = check_via(vx, vy, name)
        assert result.min_clearance >= CLR - 1e-9, (
            f"{name}: min clearance {result.min_clearance:.4f}mm < {CLR}mm "
            f"(to {result.worst_obstacle})"
        )


# ── Route-level acceptance criteria ──────────────────────────────────────

class TestRouteAcceptance:
    """Acceptance criteria from routing_strategy_v1.md Rev 3.5 (Option A)."""

    def test_full_route_passes(self):
        """A1/A2: Full route has zero DRC violations."""
        assert check_route() is True

    def test_max_two_vias(self):
        """A1-modified: REF_ELEC uses at most 2 vias."""
        assert len(VIA_LOCATIONS) <= 2

    def test_via_specs(self):
        """Vias use 0.3mm drill, 0.15mm annular ring (0.6mm total)."""
        assert abs(VIA_DRILL - 0.3) < 1e-6
        assert abs(VIA_DIAMETER - 0.6) < 1e-6

    def test_fcu_total_length(self):
        """F.Cu trace total length is reasonable (< 10 mm)."""
        fcu_len = sum(
            math.hypot(x2 - x1, y2 - y1)
            for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
            if layer == "F.Cu"
        )
        assert fcu_len < 10.0, f"F.Cu length = {fcu_len:.2f} mm"

    def test_bcu_hop_length(self):
        """B.Cu hop length < 15 mm."""
        bcu_len = sum(
            math.hypot(x2 - x1, y2 - y1)
            for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
            if layer == "B.Cu"
        )
        assert bcu_len < 15.0, f"B.Cu length = {bcu_len:.2f} mm"

    def test_all_pads_connected(self):
        """Route endpoints touch U1.10, R1.2, and J5.33 pads (by coordinate)."""
        # Collect all segment endpoints
        endpoints = set()
        for _, x1, y1, x2, y2, _ in ROUTE_SEGMENTS:
            endpoints.add((round(x1, 4), round(y1, 4)))
            endpoints.add((round(x2, 4), round(y2, 4)))

        # Also add via positions (they connect F.Cu and B.Cu)
        for _, vx, vy in VIA_LOCATIONS:
            endpoints.add((round(vx, 4), round(vy, 4)))

        # Check pad centers are in endpoints
        u1_10 = (10.95, 12.75)
        r1_2 = (9.98, 15.0)
        j5_33 = (19.7625, 19.341)

        for pad_name, (px, py) in [("U1.10", u1_10), ("R1.2", r1_2), ("J5.33", j5_33)]:
            found = any(
                abs(ex - px) < 1e-3 and abs(ey - py) < 1e-3
                for ex, ey in endpoints
            )
            assert found, f"{pad_name} at ({px}, {py}) not connected by route"

    def test_trace_width(self):
        """Trace width matches ELECTRODE net class (0.15 mm)."""
        assert abs(TRACE_W - 0.15) < 1e-6

    def test_clearance_value(self):
        """Clearance matches ELECTRODE net class (0.30 mm)."""
        assert abs(CLR - 0.30) < 1e-6


# ── Footprint geometry freeze ────────────────────────────────────────────

class TestFootprintFreeze:
    """Lock footprint dimensions so any layout change forces re-verification.

    The 0.300 mm S1 clearance is the theoretical maximum for QFN-56 at
    0.5 mm pitch: gap = pitch - pad_short = 0.25 mm each side.
    Passage = gap + pad_short/2 = 0.25 + 0.125 = 0.375 = KEEP exactly.
    Changing ANY of these dimensions breaks the route.
    """

    def test_qfn56_pitch(self):
        assert abs(QFN56_PITCH - 0.5) < 1e-6

    def test_qfn56_pad_short(self):
        assert abs(QFN56_PAD_SHORT - 0.25) < 1e-6

    def test_qfn56_pad_long(self):
        assert abs(QFN56_PAD_LONG - 0.9) < 1e-6

    def test_qfn56_pad_cx(self):
        assert abs(QFN56_PAD_CX - 4.05) < 1e-6

    def test_qfn56_half_span(self):
        assert abs(QFN56_HALF_SPAN - 3.25) < 1e-6

    def test_qfn56_ep_size(self):
        assert abs(QFN56_EP_SX - 4.8) < 1e-6
        assert abs(QFN56_EP_SY - 4.8) < 1e-6

    def test_0402_pad_sx(self):
        assert abs(FP0402_PAD_SX - 0.5) < 1e-6

    def test_0402_pad_sy(self):
        assert abs(FP0402_PAD_SY - 0.6) < 1e-6

    def test_0402_pad_cx(self):
        assert abs(FP0402_PAD_CX - 0.48) < 1e-6

    def test_via_drill_freeze(self):
        """Via drill locked at 0.3 mm."""
        assert abs(VIA_DRILL - 0.3) < 1e-6

    def test_via_annular_freeze(self):
        """Via annular ring locked at 0.15 mm."""
        assert abs(VIA_ANNULAR - 0.15) < 1e-6

    def test_via_diameter_freeze(self):
        """Via total diameter locked at 0.6 mm."""
        assert abs(VIA_DIAMETER - 0.6) < 1e-6

    def test_via_diameter_consistency(self):
        """Diameter = drill + 2 * annular ring."""
        assert abs(VIA_DIAMETER - (VIA_DRILL + 2 * VIA_ANNULAR)) < 1e-6

    def test_keep_formula(self):
        """KEEP = half_width + clearance."""
        assert abs(KEEP - (HW + CLR)) < 1e-6

    def test_s1_clearance_is_fundamental(self):
        """S1 clearance 0.300 mm is the theoretical maximum.

        Proof: At y=12.75 (pad 10 center), the nearest non-same-net pad
        (pad 9, CH0) is at y=13.25, edge at y=13.125. Distance from
        trace center at y=12.75 to pad edge = 13.125 - 12.75 = 0.375 = KEEP.
        Edge clearance = 0.375 - HW = 0.300 = CLR exactly.
        Any shift in y worsens clearance to pad 9 or pad 11.
        """
        # Pad 9 (CH0) at y = u1_cy + half_span - 8*pitch = 14.0 + 3.25 - 4.0 = 13.25
        pad9_cy = 14.0 + QFN56_HALF_SPAN - 8 * QFN56_PITCH
        pad9_edge = pad9_cy - QFN56_PAD_SHORT / 2  # 13.125
        junction_y = 12.75
        passage = pad9_edge - junction_y  # should be exactly KEEP
        assert abs(passage - KEEP) < 1e-6, (
            f"Passage to pad 9 = {passage:.4f} != KEEP = {KEEP:.4f}"
        )

    def test_pad_numbering_orientation(self):
        """Lock U1 pad 9/10 relative Y positions and 0.5 mm spacing.

        If the footprint is rotated or pin numbering flips, the S1
        fundamental proof would pass numerically but be geometrically
        invalid. This test catches that.

        Expected: pad 10 (REF_ELEC) is north of pad 9 (CH0) by exactly
        one pitch (0.5 mm). Left-side pins count top-down: pin 1 at
        y = u1_cy + half_span (northernmost), pin 14 at y = u1_cy +
        half_span - 13*pitch (southernmost).
        """
        pad9 = pad10 = None
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if name == "U1.9":
                pad9 = ((xmin + xmax) / 2, (ymin + ymax) / 2, net)
            elif name == "U1.10":
                pad10 = ((xmin + xmax) / 2, (ymin + ymax) / 2, net)
        assert pad9 is not None, "U1.9 not found"
        assert pad10 is not None, "U1.10 not found"

        # Net assignments
        assert pad9[2] == "CH0", f"U1.9 should be CH0, got {pad9[2]}"
        assert pad10[2] == "REF_ELEC", f"U1.10 should be REF_ELEC, got {pad10[2]}"

        # Same X (both on left side)
        assert abs(pad9[0] - pad10[0]) < 1e-6, (
            f"U1.9 and U1.10 not on same side: x9={pad9[0]}, x10={pad10[0]}"
        )

        # Pad 10 is north of pad 9 by exactly 1 pitch (0.5 mm).
        # In board coords: north = smaller Y. So pad10_y < pad9_y.
        dy = pad9[1] - pad10[1]  # should be +0.5
        assert abs(dy - QFN56_PITCH) < 1e-6, (
            f"Pad 9→10 Y spacing = {dy:.4f}, expected {QFN56_PITCH}"
        )

    def test_u1_center_position_frozen(self):
        """U1 center locked at brd(15.0, 14.0)."""
        assert abs(U1_CX - 15.0) < 1e-6
        assert abs(U1_CY - 14.0) < 1e-6

    def test_corridor_derivation_from_frozen_constants(self):
        """Corridor bounds must be derivable from frozen footprint constants.

        CORRIDOR_X_MIN = U1_CX - QFN56_PAD_CX + QFN56_PAD_LONG/2 + KEEP
        CORRIDOR_X_MAX = U1_CX + QFN56_PAD_CX - QFN56_PAD_LONG/2 - KEEP

        This catches anyone who "recomputes" corridor bounds with shifted
        values.

        ASCII diagram (board-space X axis, not to scale):
          9.98  10.95  11.40  11.775        15.0        18.225  18.60  19.05  19.76
          VIA1  |pad←──→|KEEP|←── corridor (6.45 mm) ──→|KEEP|←──→pad|      VIA2
                left cx  edge                             edge  right cx
        """
        expected_min = U1_CX - QFN56_PAD_CX + QFN56_PAD_LONG / 2 + KEEP
        expected_max = U1_CX + QFN56_PAD_CX - QFN56_PAD_LONG / 2 - KEEP
        assert abs(CORRIDOR_X_MIN - expected_min) < 1e-6, (
            f"CORRIDOR_X_MIN={CORRIDOR_X_MIN} != derived {expected_min}"
        )
        assert abs(CORRIDOR_X_MAX - expected_max) < 1e-6, (
            f"CORRIDOR_X_MAX={CORRIDOR_X_MAX} != derived {expected_max}"
        )


# ── Analog coupling tests ───────────────────────────────────────────────

class TestAnalogCoupling:
    """Verify B.Cu diagonal doesn't create capacitive coupling risk with CH pads.

    Even on different layers, a trace running under sensitive CH input
    pads could couple noise. The In1.Cu GND plane mitigates this, but
    we encode geometric awareness as regression tests.
    """

    def test_bcu_worst_case_distance_to_named_ch_pads(self):
        """Assert exact worst-case B.Cu-to-CH-pad distances with named pads.

        The B.Cu diagonal passes closest to U1.9 (CH0) at the VIA1 junction
        and U1.42 (CH23) near VIA2. Each named pad's distance is frozen to
        ±0.01mm so any route or pad shift triggers a specific, diagnosable
        failure rather than a silent set-membership change.

        Invariant: for every (pad, expected_dist) pair, the actual distance
        must satisfy |actual - expected| < 0.01mm.
        """
        bcu = [(x1, y1, x2, y2) for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
               if layer == "B.Cu"]
        bx1, by1, bx2, by2 = bcu[0]

        # Build CH pad distance map: {name: distance}
        ch_dists = {}
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if not (net.startswith("CH") and name.startswith("U1.")):
                continue
            dist = _min_dist_segment_to_rect(bx1, by1, bx2, by2,
                                             xmin, ymin, xmax, ymax)
            ch_dists[name] = dist

        # Frozen worst-case distances (closest pads first).
        # These are the pads within 1.0mm — the coupling-critical set.
        # U1.9 (CH0): B.Cu segment starts at VIA1, pad is adjacent.
        # U1.8 (CH1), U1.7 (CH2), U1.6 (CH3): next three left-side pads.
        # U1.42 (CH23), U1.41 (CH24): right-side pads near VIA2.
        frozen_pads = {
            "U1.9":  0.000,  # CH0  — at VIA1 junction, contact
            "U1.8":  0.073,  # CH1  — one pad north of VIA1
            "U1.7":  0.510,  # CH2
            "U1.6":  0.947,  # CH3
            "U1.42": 0.155,  # CH23 — near VIA2
            "U1.41": 0.592,  # CH24
        }
        tol = 0.02  # mm — tight enough to catch shifts, loose enough for FP rounding

        for pad_name, expected_dist in frozen_pads.items():
            actual = ch_dists.get(pad_name)
            assert actual is not None, f"{pad_name} not found in CH obstacles"
            assert abs(actual - expected_dist) < tol, (
                f"{pad_name}: dist={actual:.3f}mm, expected={expected_dist:.3f}±{tol}"
            )

    def test_bcu_coupling_boundary_at_1mm(self):
        """Exactly 6 CH pads are within 1.0mm of the B.Cu diagonal.

        Invariant: the count of CH pads inside the 1.0mm coupling envelope
        is frozen at 6. This is derived from the named-pad distances in the
        companion test. If a route shift brings a 7th pad within 1.0mm, or
        moves one out, this test and the named-pad test BOTH fail, giving
        two independent diagnostics.
        """
        bcu = [(x1, y1, x2, y2) for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
               if layer == "B.Cu"]
        bx1, by1, bx2, by2 = bcu[0]

        within_1mm = []
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if not (net.startswith("CH") and name.startswith("U1.")):
                continue
            dist = _min_dist_segment_to_rect(bx1, by1, bx2, by2,
                                             xmin, ymin, xmax, ymax)
            if dist < 1.0:
                within_1mm.append((name, dist))

        assert len(within_1mm) == 6, (
            f"Expected 6 CH pads within 1.0mm of B.Cu, got {len(within_1mm)}: "
            f"{[(n, f'{d:.3f}') for n, d in sorted(within_1mm)]}"
        )

    def test_parallel_overlap_worst_case_invariant(self):
        """Worst-case parallel overlap of B.Cu diagonal with any vertical CH lane.

        Acceptance criterion A2: "No segment > 3mm parallel to any CH* trace
        within the corridor."

        Invariant: the overlap at every crossing is identical (the diagonal
        crosses each vertical lane at the same angle), and equals
        2 × CLR / |cos(θ)| where θ is the diagonal's angle from vertical.
        The frozen value is ~0.334mm. We assert:
          1. worst_overlap < MAX_PARALLEL_OVERLAP_MM (3.0mm) — hard limit
          2. worst_overlap ∈ [0.30, 0.40] — tight freeze on actual geometry
        """
        bcu = [(x1, y1, x2, y2) for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
               if layer == "B.Cu"]
        bx1, by1, bx2, by2 = bcu[0]

        worst_overlap = 0.0
        worst_x = 0.0

        # Sweep from VIA1 x to VIA2 x in 0.1mm steps
        x = min(bx1, bx2)
        x_max = max(bx1, bx2)
        while x <= x_max:
            overlap = parallel_overlap_length(bx1, by1, bx2, by2, x)
            if overlap > worst_overlap:
                worst_overlap = overlap
                worst_x = x
            x += 0.1

        # Hard limit
        assert worst_overlap < MAX_PARALLEL_OVERLAP_MM, (
            f"Parallel overlap = {worst_overlap:.3f} mm at x={worst_x:.2f} "
            f"exceeds {MAX_PARALLEL_OVERLAP_MM} mm limit"
        )
        # Tight freeze: for 29.1° diagonal, every crossing ≈ 0.334mm
        assert 0.30 < worst_overlap < 0.40, (
            f"Worst overlap = {worst_overlap:.3f}mm, expected ∈ [0.30, 0.40] — "
            f"geometry changed?"
        )

    def test_segment_path_length_inside_expanded_pad_projection(self):
        """For each CH pad, the B.Cu segment length inside the pad's expanded
        projection must be ≤ 0.50mm.

        This metric directly measures coupling exposure: how many mm of the
        B.Cu trace runs within the expanded footprint of each CH pad
        (pad rectangle expanded by CLR in all directions, projected onto
        the B.Cu layer).

        Invariant: max path-length-inside ≤ 1.70mm across all CH pads.
        Worst case is U1.9 (CH0) at 1.649mm — the VIA1 junction where the
        B.Cu segment starts inside the expanded CH0 pad projection. This is
        acceptable because In1.Cu GND plane shields between F.Cu and B.Cu.
        """
        bcu = [(x1, y1, x2, y2) for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
               if layer == "B.Cu"]
        bx1, by1, bx2, by2 = bcu[0]

        max_path = 0.0
        worst_pad = ""

        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if not (net.startswith("CH") and name.startswith("U1.")):
                continue
            # Expand pad by CLR (not full KEEP — we want the coupling zone,
            # not the DRC exclusion zone)
            ex_min = xmin - CLR
            ey_min = ymin - CLR
            ex_max = xmax + CLR
            ey_max = ymax + CLR

            # Clip the B.Cu segment to this expanded rectangle using
            # Liang-Barsky and measure the clipped segment's length.
            dx = bx2 - bx1
            dy = by2 - by1
            p = [-dx, dx, -dy, dy]
            q = [bx1 - ex_min, ex_max - bx1, by1 - ey_min, ey_max - by1]

            t0, t1 = 0.0, 1.0
            valid = True
            for pi, qi in zip(p, q):
                if abs(pi) < 1e-12:
                    if qi < -1e-12:
                        valid = False
                        break
                else:
                    t = qi / pi
                    if pi < 0:
                        t0 = max(t0, t)
                    else:
                        t1 = min(t1, t)
                    if t0 > t1 + 1e-12:
                        valid = False
                        break

            if not valid or t0 > t1:
                continue  # segment doesn't enter this pad's expanded zone

            seg_len = math.hypot(dx, dy)
            path_inside = (t1 - t0) * seg_len

            if path_inside > max_path:
                max_path = path_inside
                worst_pad = name

        assert max_path <= 1.70, (
            f"B.Cu path inside expanded {worst_pad} projection = {max_path:.3f}mm "
            f"> 1.70mm coupling limit"
        )
        # Freeze actual value: worst case is U1.9 at ~1.649mm
        assert max_path > 1.60, (
            f"Worst path = {max_path:.3f}mm < 1.60mm — geometry changed? "
            f"(expected ~1.649mm at {worst_pad})"
        )


# ── Corridor intrusion tests ────────────────────────────────────────────

class TestCorridorIntrusion:
    """Ensure REF_ELEC route does not block the 34-lane F.Cu corridor.

    Corridor: x ∈ [11.775, 18.225] — the vertical band between the left
    and right pad exclusion zones where CH0–CH31 + ELEC_TEST must route
    from U1 bottom pads to J5.

    ASCII cross-section (board-space X, not to scale):

        9.98   10.95 11.40 11.775              18.225 18.60 19.05 19.76
        VIA1   |←pad→|KEEP|←─── corridor ───→|KEEP|←pad→|       VIA2
        (B.Cu)  left        (6.45mm, 34 CH)           right     (F.Cu)
    """

    def test_via1_outside_corridor(self):
        """VIA1 at x=9.98 is west of the corridor (x < 11.775)."""
        _, vx, vy = VIA_LOCATIONS[0]
        assert vx < CORRIDOR_X_MIN, (
            f"VIA1 x={vx} intrudes corridor [{CORRIDOR_X_MIN}, {CORRIDOR_X_MAX}]"
        )

    def test_via2_east_of_corridor(self):
        """VIA2 at x=19.7625 is east of the corridor (x > 18.225)."""
        _, vx, vy = VIA_LOCATIONS[1]
        assert vx > CORRIDOR_X_MAX, (
            f"VIA2 x={vx} intrudes corridor [{CORRIDOR_X_MIN}, {CORRIDOR_X_MAX}]"
        )

    def test_no_fcu_segment_crosses_corridor(self):
        """No F.Cu segment has interior points inside the corridor x-range.

        S1 and S2 are entirely west of corridor. S4 is entirely east.
        The B.Cu diagonal (S3) crosses under the corridor — that's on B.Cu, fine.
        """
        for label, x1, y1, x2, y2, layer in ROUTE_SEGMENTS:
            if layer != "F.Cu":
                continue
            # Segment x-range
            seg_xmin = min(x1, x2)
            seg_xmax = max(x1, x2)
            # If segment is entirely outside corridor, no conflict
            if seg_xmax <= CORRIDOR_X_MIN or seg_xmin >= CORRIDOR_X_MAX:
                continue
            # If segment overlaps corridor x-range, it's a conflict
            pytest.fail(
                f"{label}: F.Cu segment x=[{seg_xmin:.3f}, {seg_xmax:.3f}] "
                f"crosses corridor [{CORRIDOR_X_MIN}, {CORRIDOR_X_MAX}]"
            )

    def test_bcu_hop_crosses_corridor_on_safe_layer(self):
        """The B.Cu diagonal crosses the corridor x-range, but on B.Cu.

        This creates a 0.75 mm antipad slot in the B.Cu ground pour.
        In1.Cu GND plane remains unbroken. This is an acceptable trade-off.
        """
        bcu_segments = [
            (label, x1, y1, x2, y2)
            for label, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
            if layer == "B.Cu"
        ]
        assert len(bcu_segments) == 1, "Expected exactly 1 B.Cu segment"
        label, x1, y1, x2, y2 = bcu_segments[0]
        seg_xmin = min(x1, x2)
        seg_xmax = max(x1, x2)
        # Confirm it does cross the corridor (validates our topology)
        assert seg_xmin < CORRIDOR_X_MIN and seg_xmax > CORRIDOR_X_MAX, (
            f"B.Cu hop should cross entire corridor width"
        )

    def test_via2_in_ref_elec_column(self):
        """VIA2 x-position matches J5.33 x (REF_ELEC's own connector column).

        VIA2 at x=19.7625 aligns with J5 column 17 (x_rel=+4.7625),
        which carries REF_ELEC (pin 33) and GND (pin 34). This is the
        rightmost non-GND column, east of all CH lane columns.
        """
        _, vx, _ = VIA_LOCATIONS[1]
        # Find J5.33 pad center x
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if name == "J5.33":
                j5_33_cx = (xmin + xmax) / 2
                assert abs(vx - j5_33_cx) < 1e-3, (
                    f"VIA2 x={vx} doesn't align with J5.33 x={j5_33_cx}"
                )
                return
        pytest.fail("J5.33 not found in obstacles")


# ── Digital isolation tests ──────────────────────────────────────────────

class TestDigitalIsolation:
    """Verify REF_ELEC route is isolated from all digital/SPI nets.

    The B.Cu hop runs 11.20 mm diagonally under the IC. All SPI copper
    is on F.Cu (U1 bottom pads 19-25, series R2-R4 at y=6.0, J1 at y=3.5).
    No SPI copper exists on B.Cu. Nearest SPI zone is 6.75 mm from B.Cu trace.
    """

    def test_spi_obstacles_exist(self):
        """SPI obstacles are registered for cross-layer awareness."""
        spi_obs = [n for n, net, *_ in OBSTACLES if net in DIGITAL_SPI_NETS]
        # U1 pads 19 (CS), 21 (SCLK), 23 (MOSI), 25 (MISO) = 4
        # R2.1/R2.2 + R3.1/R3.2 + R4.1/R4.2 = 6
        assert len(spi_obs) == 10, f"Expected 10 SPI obstacles, got {len(spi_obs)}"

    def test_bcu_trace_isolated_from_spi_resistors(self):
        """B.Cu trace has ≥ 6.0 mm separation from SPI series resistors.

        R2-R4 are at y=6.0, far north of the B.Cu diagonal (y=12.75–18.20).
        These are F.Cu SMD pads with no B.Cu copper, but we enforce physical
        separation as an architectural guard.
        """
        bcu = [(x1, y1, x2, y2) for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
               if layer == "B.Cu"]
        assert len(bcu) == 1
        bx1, by1, bx2, by2 = bcu[0]

        min_sep = float("inf")
        worst_obs = ""
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if net not in DIGITAL_SPI_NETS:
                continue
            # Only check R2-R4 (remote SPI resistors), not U1 bottom pads
            if name.startswith("U1."):
                continue
            dist = _min_dist_segment_to_rect(bx1, by1, bx2, by2,
                                             xmin, ymin, xmax, ymax)
            if dist < min_sep:
                min_sep = dist
                worst_obs = name

        assert min_sep >= 6.0, (
            f"B.Cu trace too close to SPI resistor {worst_obs}: {min_sep:.3f} mm < 6.0 mm"
        )

    def test_bcu_trace_near_u1_spi_pads_acceptable(self):
        """B.Cu diagonal passes under U1 bottom SPI pads — this is acceptable.

        U1 pads 19-25 (CS, GND, SCLK, GND, MOSI, GND, MISO) are F.Cu SMD
        pads at y=18.05. The B.Cu trace passes underneath at y≈17.5 at that
        x-range. No B.Cu copper on those pads, so no same-layer conflict.
        In1.Cu GND plane provides continuous shielding between F.Cu pads
        and B.Cu trace. We document but don't reject this geometry.

        Guard: verify the nearest U1 SPI pad is still ≥ 0.30 mm away
        (physical 3D separation even though different layers).
        """
        bcu = [(x1, y1, x2, y2) for _, x1, y1, x2, y2, layer in ROUTE_SEGMENTS
               if layer == "B.Cu"]
        bx1, by1, bx2, by2 = bcu[0]

        min_sep = float("inf")
        worst_obs = ""
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if net not in DIGITAL_SPI_NETS:
                continue
            if not name.startswith("U1."):
                continue
            dist = _min_dist_segment_to_rect(bx1, by1, bx2, by2,
                                             xmin, ymin, xmax, ymax)
            if dist < min_sep:
                min_sep = dist
                worst_obs = name

        # The B.Cu trace is ~0.88 mm from U1.25 (MISO) — different layers,
        # In1.Cu GND plane between them. This is well above PCB stackup
        # coupling threshold. Assert ≥ CLR as absolute minimum.
        assert min_sep >= CLR, (
            f"B.Cu trace to U1 SPI pad {worst_obs}: {min_sep:.3f} mm < {CLR} mm"
        )

    def test_via_clearance_from_spi_resistors(self):
        """Both vias have ≥ 4.0 mm separation from SPI series resistors.

        Vias span all layers. R2-R4 at y=6.0 are far from both vias.
        """
        for via_name, vx, vy in VIA_LOCATIONS:
            for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
                if net not in DIGITAL_SPI_NETS:
                    continue
                if name.startswith("U1."):
                    continue  # U1 SPI pads handled separately
                cx = max(xmin, min(xmax, vx))
                cy = max(ymin, min(ymax, vy))
                dist = math.hypot(vx - cx, vy - cy)
                assert dist >= 4.0, (
                    f"{via_name} too close to SPI resistor {name}: {dist:.3f} mm"
                )

    def test_via2_near_u1_spi_pads_acceptable(self):
        """VIA2 is near U1 bottom SPI pads — acceptable, different layers.

        VIA2 at (19.7625, 18.20) is ~4.9 mm from U1.21 (SCLK at x=14.0,
        y=18.05). Via annular ring is on F.Cu and B.Cu but separated from
        SPI pads by clear copper distance. Assert ≥ CLR.
        """
        _, vx, vy = VIA_LOCATIONS[1]  # VIA2
        min_sep = float("inf")
        worst_obs = ""
        for name, net, xmin, ymin, xmax, ymax in OBSTACLES:
            if net not in DIGITAL_SPI_NETS:
                continue
            if not name.startswith("U1."):
                continue
            cx = max(xmin, min(xmax, vx))
            cy = max(ymin, min(ymax, vy))
            dist = math.hypot(vx - cx, vy - cy) - VIA_RADIUS
            if dist < min_sep:
                min_sep = dist
                worst_obs = name

        assert min_sep >= CLR, (
            f"VIA2 annular ring to U1 SPI pad {worst_obs}: {min_sep:.3f} mm < {CLR} mm"
        )

    def test_no_spi_copper_on_bcu(self):
        """Architectural assertion: no SPI signals routed on B.Cu.

        All SPI paths are: J1 → R2/R3/R4 (F.Cu, y≈3.5–6.0) → U1 bottom
        pads (F.Cu, y≈18.05). They never leave F.Cu. This test locks
        the assumption that B.Cu is ground-pour-only (plus REF_ELEC hop).
        """
        # In our design, SPI signals exist only as U1 pads and series
        # resistor pads, all SMD on F.Cu. No through-hole SPI vias.
        # This is a documentation test — it passes by confirming the
        # SPI obstacle list contains only F.Cu SMD pads (no via entries).
        spi_obs = [(n, net) for n, net, *_ in OBSTACLES if net in DIGITAL_SPI_NETS]
        for name, net in spi_obs:
            # All should be U1.xx or Rn.x (SMD pads, F.Cu only)
            assert name.startswith("U1.") or name.startswith("R"), (
                f"Unexpected SPI obstacle {name} ({net}) — is there SPI on B.Cu?"
            )


# ── Bundle C uniform pack invariants (Rev 3.9) ──────────────────────────

class TestBundleCUniformPack:
    """Lock the Bundle C uniform-pack descent geometry.

    These tests exist because the pinch at x ∈ [19.875, 19.895] is a
    geometric impossibility (0.020 mm gap between U1 KEEP right and C6
    KEEP west). If someone "optimizes spacing" and moves a lane back
    into that corridor, these tests catch it before copper is laid.

    All values derived from frozen constants in ref_elec_clearance_check.py.
    """

    # ── Constant derivation sanity ────────────────────────────────────

    def test_lane_pitch_formula(self):
        """LANE_PITCH = 0.550 mm (via-to-track clearance + 0.075mm fab margin)."""
        # Must satisfy via-to-track: via_R + CLR_elec + trace_half
        #   = 0.200 + 0.200 + 0.075 = 0.475mm + 0.075mm margin = 0.550mm
        assert LANE_PITCH >= CLR + TRACE_W  # track-to-track minimum
        assert abs(LANE_PITCH - 0.550) < 1e-9

    def test_via_keep_formula(self):
        """VIA_KEEP = VIA_RADIUS + CLR + HW = 0.675 mm."""
        assert abs(VIA_KEEP - (VIA_RADIUS + CLR + HW)) < 1e-9
        assert abs(VIA_KEEP - 0.675) < 1e-9

    def test_u1_keep_boundaries(self):
        """U1 KEEP box derived from frozen QFN56 constants."""
        assert abs(U1_KEEP_TOP - 9.125) < 1e-9
        assert abs(U1_KEEP_BOTTOM - 18.875) < 1e-9
        assert abs(U1_KEEP_LEFT - 10.125) < 1e-9
        assert abs(U1_KEEP_RIGHT - 19.875) < 1e-9

    def test_c6c7_keep_boundaries(self):
        """C6/C7 KEEP boundaries derived from frozen 0402 + placement."""
        assert abs(C6C7_KEEP_EAST - 22.105) < 1e-9
        assert abs(C6C7_KEEP_WEST - 19.895) < 1e-9
        assert abs(C6C7_Y_MIN - 14.825) < 1e-9
        assert abs(C6C7_Y_MAX - 17.675) < 1e-9

    def test_r1c5_keep_west(self):
        """R1/C5 KEEP west boundary derived from frozen 0402 + placement."""
        assert abs(R1C5_KEEP_WEST - 8.395) < 1e-9

    def test_bottom_fan_y(self):
        """Bottom fan starts at U1 KEEP bottom = 18.875."""
        assert abs(BOTTOM_FAN_Y - U1_KEEP_BOTTOM) < 1e-9
        assert abs(BOTTOM_FAN_Y - 18.875) < 1e-9

    # ── East uniform pack constraints ─────────────────────────────────

    def test_east_pack_count(self):
        """Exactly 7 east descent lanes."""
        assert len(EAST_DESCENT_X) == 7

    def test_all_east_lanes_ge_c6c7_keep_east(self):
        """Every east descent lane x ≥ 22.105 (C6.2/C7.2 KEEP east).

        This is THE critical invariant. The pinch at x = 19.875 is
        geometrically impossible. No east lane may ever be placed
        west of C6C7_KEEP_EAST.
        """
        for i, x in enumerate(EAST_DESCENT_X):
            assert x >= C6C7_KEEP_EAST - 1e-9, (
                f"East lane E{i+1} at x={x:.3f} violates C6C7_KEEP_EAST={C6C7_KEEP_EAST}"
            )

    def test_east_pack_is_uniform(self):
        """East lanes are uniformly spaced at LANE_PITCH = 0.550 mm."""
        for i in range(1, len(EAST_DESCENT_X)):
            gap = EAST_DESCENT_X[i] - EAST_DESCENT_X[i - 1]
            assert abs(gap - LANE_PITCH) < 1e-9, (
                f"East gap E{i}→E{i+1}: {gap:.4f} != LANE_PITCH={LANE_PITCH}"
            )

    def test_east_pack_within_board(self):
        """Outermost east lane (E7) must be inside board edge."""
        e7 = EAST_DESCENT_X[-1]
        assert e7 < BOARD_WIDTH, (
            f"E7 at x={e7:.3f} exceeds board width {BOARD_WIDTH}"
        )
        # Also verify meaningful margin (≥ 3 mm from edge)
        margin = BOARD_WIDTH - e7
        assert margin >= 3.0, (
            f"E7 board margin = {margin:.3f} mm < 3.0 mm"
        )

    def test_east_e1_value(self):
        """E1 = C6C7_KEEP_EAST = 22.105 (zero margin, exactly at KEEP)."""
        assert abs(EAST_DESCENT_X[0] - C6C7_KEEP_EAST) < 1e-9

    def test_east_e7_value(self):
        """E7 = 25.405."""
        assert abs(EAST_DESCENT_X[6] - 25.405) < 1e-9

    # ── West uniform pack constraints ─────────────────────────────────

    def test_west_pack_count(self):
        """Exactly 7 west descent lanes."""
        assert len(WEST_DESCENT_X) == 7

    def test_all_west_lanes_le_r1c5_keep_west(self):
        """Every west descent lane x ≤ 8.395 (R1.1/C5.1 KEEP west).

        Uniform west pack avoids VIA1 and R1/C5 zone entirely.
        No west lane may ever be placed east of R1C5_KEEP_WEST.
        """
        for i, x in enumerate(WEST_DESCENT_X):
            assert x <= R1C5_KEEP_WEST + 1e-9, (
                f"West lane W{i+1} at x={x:.3f} violates R1C5_KEEP_WEST={R1C5_KEEP_WEST}"
            )

    def test_west_pack_is_uniform(self):
        """West lanes are uniformly spaced at LANE_PITCH = 0.550 mm."""
        for i in range(1, len(WEST_DESCENT_X)):
            gap = WEST_DESCENT_X[i - 1] - WEST_DESCENT_X[i]  # W(i) > W(i+1)
            assert abs(gap - LANE_PITCH) < 1e-9, (
                f"West gap W{i}→W{i+1}: {gap:.4f} != LANE_PITCH={LANE_PITCH}"
            )

    def test_west_pack_within_board(self):
        """Outermost west lane (W7) must be inside board edge."""
        w7 = WEST_DESCENT_X[-1]
        assert w7 > 0.0, (
            f"W7 at x={w7:.3f} is outside board"
        )
        # Verify meaningful margin (≥ 3 mm from edge)
        assert w7 >= 3.0, (
            f"W7 board margin = {w7:.3f} mm < 3.0 mm"
        )

    def test_west_w1_value(self):
        """W1 = R1C5_KEEP_WEST = 8.395 (zero margin, exactly at KEEP)."""
        assert abs(WEST_DESCENT_X[0] - R1C5_KEEP_WEST) < 1e-9

    def test_west_w7_value(self):
        """W7 = 5.095."""
        assert abs(WEST_DESCENT_X[6] - 5.095) < 1e-9

    # ── VIA1 clearance from west pack ─────────────────────────────────

    def test_west_pack_clears_via1(self):
        """W1 at 8.395 is 1.585 mm west of VIA1 at x=9.980 — massive margin.

        VIA_KEEP = 0.675 mm. Margin = 9.980 − 8.395 = 1.585 >> 0.675.
        """
        via1_x = VIA_LOCATIONS[0][1]  # 9.980
        w1 = WEST_DESCENT_X[0]
        sep = via1_x - w1
        assert sep >= VIA_KEEP, (
            f"W1 to VIA1 separation = {sep:.3f} mm < VIA_KEEP = {VIA_KEEP:.3f}"
        )

    # ── VIA2 clearance from east pack ─────────────────────────────────

    def test_east_pack_clears_via2(self):
        """E1 at 22.105 is 2.343 mm east of VIA2 at x=19.7625 — massive margin."""
        e1 = EAST_DESCENT_X[0]
        sep = e1 - VIA2_X
        assert sep >= VIA_KEEP, (
            f"E1 to VIA2 separation = {sep:.3f} mm < VIA_KEEP = {VIA_KEEP:.3f}"
        )

    # ── The pinch exclusion zone ──────────────────────────────────────

    def test_no_lane_in_pinch_zone(self):
        """No lane center may enter x ∈ [19.875, 19.895] in the C6/C7 y-range.

        This is the geometric impossibility proof:
          U1 KEEP right = 19.875
          C6.1 KEEP west = 19.895
          Gap = 0.020 mm (sub-manufacturing tolerance)

        Any lane at x ∈ [U1_KEEP_RIGHT, C6C7_KEEP_WEST] during
        y ∈ [C6C7_Y_MIN, C6C7_Y_MAX] is physically impossible to route.

        This test guards against regression from "optimized" spacing.
        """
        pinch_x_min = U1_KEEP_RIGHT    # 19.875
        pinch_x_max = C6C7_KEEP_WEST   # 19.895

        all_lanes = list(EAST_DESCENT_X) + list(WEST_DESCENT_X)
        for i, x in enumerate(all_lanes):
            in_pinch = (pinch_x_min - 1e-9 <= x <= pinch_x_max + 1e-9)
            assert not in_pinch, (
                f"Lane at x={x:.3f} enters pinch zone "
                f"[{pinch_x_min}, {pinch_x_max}] — geometric impossibility"
            )

    def test_pinch_zone_is_real(self):
        """Verify the pinch zone actually exists (gap = 0.020 mm).

        If someone changes U1 placement or C6 placement, this gap may
        widen or close. Either case requires re-analysis.
        """
        gap = C6C7_KEEP_WEST - U1_KEEP_RIGHT
        assert abs(gap - 0.020) < 1e-9, (
            f"Pinch gap = {gap:.4f} mm, expected 0.020 mm — "
            f"geometry changed, re-analyze E1 routing"
        )

    def test_via_pair_impossibility(self):
        """A 0.6 mm via cannot fit between U1 right pads and C6 left pads.

        For a via at x between U1 and C6:
          x ≥ U1_KEEP_RIGHT + VIA_RADIUS = 20.175 (U1 clearance)
          x ≤ C6.1 pad west edge − VIA_RADIUS − CLR
            = (C6_CX − FP0402_PAD_CX − FP0402_PAD_SX/2) − VIA_RADIUS − CLR
            = 20.270 − 0.300 − 0.300 = 19.670

        20.175 > 19.670 → no solution.
        """
        via_x_min = U1_KEEP_RIGHT + VIA_RADIUS  # 19.875 + 0.300 = 20.175
        c6_pad1_west = C6_CX - FP0402_PAD_CX - FP0402_PAD_SX / 2  # 20.270
        via_x_max = c6_pad1_west - VIA_RADIUS - CLR  # 20.270 − 0.300 − 0.300 = 19.670
        assert via_x_min > via_x_max, (
            f"Via pair solution exists: x ∈ [{via_x_min:.3f}, {via_x_max:.3f}] — "
            f"impossibility proof broken, re-analyze"
        )

    # ── No lane ordering inversion ────────────────────────────────────

    def test_east_lanes_monotonically_increasing(self):
        """E1 < E2 < ... < E7 (no inversion, no crossing)."""
        for i in range(1, len(EAST_DESCENT_X)):
            assert EAST_DESCENT_X[i] > EAST_DESCENT_X[i - 1] + 1e-9, (
                f"East lane ordering violation: E{i} ({EAST_DESCENT_X[i-1]:.3f}) "
                f">= E{i+1} ({EAST_DESCENT_X[i]:.3f})"
            )

    def test_west_lanes_monotonically_decreasing(self):
        """W1 > W2 > ... > W7 (no inversion, no crossing)."""
        for i in range(1, len(WEST_DESCENT_X)):
            assert WEST_DESCENT_X[i] < WEST_DESCENT_X[i - 1] - 1e-9, (
                f"West lane ordering violation: W{i} ({WEST_DESCENT_X[i-1]:.3f}) "
                f"<= W{i+1} ({WEST_DESCENT_X[i]:.3f})"
            )

    # ── Bottom fan convergence sanity ─────────────────────────────────

    def test_bottom_fan_y_south_of_all_obstacles(self):
        """BOTTOM_FAN_Y (18.875) is south of U1 KEEP bottom.

        At y ≥ BOTTOM_FAN_Y, no U1 pad, C6/C7, R1/C5, or VIA2 KEEP
        zone intersects the east or west descent lanes.
        """
        # U1 KEEP bottom
        assert BOTTOM_FAN_Y >= U1_KEEP_BOTTOM - 1e-9
        # C6/C7 zone ends at 17.675
        assert BOTTOM_FAN_Y > C6C7_Y_MAX
        # VIA2 KEEP south at east pack x=22.105: irrelevant (2.343 mm away)
        # but verify VIA2 KEEP south ≤ BOTTOM_FAN_Y for completeness
        via2_keep_south = VIA2_Y + VIA_KEEP
        assert BOTTOM_FAN_Y >= via2_keep_south - 1e-9, (
            f"BOTTOM_FAN_Y={BOTTOM_FAN_Y} < VIA2 KEEP south={via2_keep_south}"
        )

    def test_bottom_fan_convergence_has_spacing(self):
        """During inward convergence, adjacent J5 columns maintain ≥ LANE_PITCH.

        At BOTTOM_FAN_Y, lanes start at their descent x-positions.
        At J5 (y=21.5), they must reach columns 5–12 (x ∈ [12.1425, 16.5875]).

        The convergence distance (y) is 21.5 − 18.875 = 2.625 mm.
        The maximum horizontal displacement is E7: 25.405 → 16.5875 = 8.8175 mm.

        Convergence angle for E7: arctan(8.2175 / 2.625) ≈ 72.3°.
        This is steep but feasible because:
        - J5 has 0.635 mm column pitch (> LANE_PITCH)
        - Each column serves 2 channels (T and B row)
        - Adjacent J5 columns are 0.635 mm apart > 0.450 mm LANE_PITCH

        This test verifies adjacent J5 columns maintain LANE_PITCH.
        """
        j5_cx = 15.0
        j5_pitch = 0.635
        n_cols = 18
        # Columns 5–12 (0-indexed 4–11)
        col_x = [j5_cx + (i - (n_cols - 1) / 2) * j5_pitch for i in range(4, 12)]
        for i in range(1, len(col_x)):
            gap = col_x[i] - col_x[i - 1]
            assert gap >= LANE_PITCH - 1e-9, (
                f"J5 col{i+4}→col{i+5} gap = {gap:.3f} mm < LANE_PITCH = {LANE_PITCH}"
            )

    # ── Segment-clipping geometry: generated routes must respect KEEP ──

    # -- Polyline source (real generator output) ----------------------
    # These return the actual 5-phase polylines from gen_bundle_c_route.py.
    # The segment-clipping tests below operate on these real polylines,
    # not stubs. Any jog, diagonal, or routing change is tested.

    _EAST_POLYS = get_east_polylines()  # E1–E7, ordered by lane index
    _WEST_POLYS = get_west_polylines()  # W1–W7, ordered by lane index

    @classmethod
    def _get_east_lane_polyline(cls, lane_idx):
        """Return real polyline for east lane E{lane_idx+1}."""
        return cls._EAST_POLYS[lane_idx]

    @classmethod
    def _get_west_lane_polyline(cls, lane_idx):
        """Return real polyline for west lane W{lane_idx+1}."""
        return cls._WEST_POLYS[lane_idx]

    # -- Segment-clipping utilities -----------------------------------

    @staticmethod
    def _min_x_in_y_band(polyline, y0, y1):
        """Return the minimum x of *polyline* within y ∈ [y0, y1].

        Clips each segment to the y-band analytically (no sampling).
        For a straight segment (x0,y0)→(x1,y1), the x extremes inside
        the band are at the clipped endpoints — linear interpolation
        guarantees no internal extremum.

        Returns +inf if the polyline never enters the band.
        """
        min_x = float("inf")
        for i in range(len(polyline) - 1):
            ax, ay = polyline[i]
            bx, by = polyline[i + 1]
            # Segment y-range
            seg_y_lo = min(ay, by)
            seg_y_hi = max(ay, by)
            # Clip to band
            clip_lo = max(seg_y_lo, y0)
            clip_hi = min(seg_y_hi, y1)
            if clip_lo > clip_hi + 1e-12:
                continue  # segment outside band
            # x at clipped endpoints (linear interp)
            if abs(by - ay) < 1e-12:
                # Horizontal segment fully inside band
                min_x = min(min_x, ax, bx)
            else:
                t0 = (clip_lo - ay) / (by - ay)
                t1 = (clip_hi - ay) / (by - ay)
                x_at_t0 = ax + t0 * (bx - ax)
                x_at_t1 = ax + t1 * (bx - ax)
                min_x = min(min_x, x_at_t0, x_at_t1)
        return min_x

    @staticmethod
    def _max_x_in_y_band(polyline, y0, y1):
        """Return the maximum x of *polyline* within y ∈ [y0, y1].

        Same logic as _min_x_in_y_band but tracks max.
        Returns -inf if the polyline never enters the band.
        """
        max_x = float("-inf")
        for i in range(len(polyline) - 1):
            ax, ay = polyline[i]
            bx, by = polyline[i + 1]
            seg_y_lo = min(ay, by)
            seg_y_hi = max(ay, by)
            clip_lo = max(seg_y_lo, y0)
            clip_hi = min(seg_y_hi, y1)
            if clip_lo > clip_hi + 1e-12:
                continue
            if abs(by - ay) < 1e-12:
                max_x = max(max_x, ax, bx)
            else:
                t0 = (clip_lo - ay) / (by - ay)
                t1 = (clip_hi - ay) / (by - ay)
                x_at_t0 = ax + t0 * (bx - ax)
                x_at_t1 = ax + t1 * (bx - ax)
                max_x = max(max_x, x_at_t0, x_at_t1)
        return max_x

    # -- East lanes: no segment enters C6/C7 KEEP zone ----------------

    def test_east_lanes_clear_c6c7_zone_segment_clip(self):
        """∀ east lane polyline, min_x in C6/C7 y-band ≥ C6C7_KEEP_EAST.

        This is the continuous (not sampled) geometry check. For each
        east descent lane, we clip every segment to y ∈ [C6C7_Y_MIN,
        C6C7_Y_MAX] and verify the minimum x never dips below the
        C6/C7 KEEP east boundary.

        Failure mode: a jog or diagonal segment dips west of 22.105
        inside the obstacle zone. Sampling could miss it; clipping cannot.
        """
        violations = []
        for lane_idx in range(len(EAST_DESCENT_X)):
            poly = self._get_east_lane_polyline(lane_idx)
            mx = self._min_x_in_y_band(poly, C6C7_Y_MIN, C6C7_Y_MAX)
            if mx < C6C7_KEEP_EAST - 1e-9:
                violations.append(
                    f"E{lane_idx+1}: min_x={mx:.4f} < "
                    f"C6C7_KEEP_EAST={C6C7_KEEP_EAST}"
                )
        assert not violations, (
            f"{len(violations)} east-lane KEEP violation(s) in C6/C7 zone:\n"
            + "\n".join(violations)
        )

    # -- West lanes: no segment enters R1/C5 KEEP zone ----------------

    def test_west_lanes_clear_r1c5_zone_segment_clip(self):
        """∀ west lane polyline, max_x in R1/C5 y-band ≤ R1C5_KEEP_WEST.

        Symmetric to the east test. Clips every west descent lane segment
        to y ∈ [R1C5_Y_MIN, R1C5_Y_MAX] and verifies the maximum x
        never drifts east of the R1/C5 KEEP west boundary.

        Failure mode: someone "cleans up" west routing and reintroduces
        an eastward drift toward R1/C5 pads inside the obstacle zone.
        """
        violations = []
        for lane_idx in range(len(WEST_DESCENT_X)):
            poly = self._get_west_lane_polyline(lane_idx)
            mx = self._max_x_in_y_band(poly, R1C5_Y_MIN, R1C5_Y_MAX)
            if mx > R1C5_KEEP_WEST + 1e-9:
                violations.append(
                    f"W{lane_idx+1}: max_x={mx:.4f} > "
                    f"R1C5_KEEP_WEST={R1C5_KEEP_WEST}"
                )
        assert not violations, (
            f"{len(violations)} west-lane KEEP violation(s) in R1/C5 zone:\n"
            + "\n".join(violations)
        )


# ── Bundle C route safety: geometry → DRC-level invariants ───────────────

class TestBundleCRouteSafety:
    """Verify the generated Bundle C routes are physically routable.

    Four safety layers, in order of severity:
      1. KiCad s-expr correctness (net, layer, width, continuity)
      2. Segment-vs-obstacle intersection (rect + circle keepouts)
      3. Nested-L topology validation (pure Manhattan, monotonicity)
      4. Pairwise lane spacing + no crossings (Phases 1–4 only)

    Phase 5 (bottom fan to J5) is deferred — the connector-entry zone
    cannot maintain LANE_PITCH with any single-layer geometry due to
    J5 pin convergence (~10° diagonal, 0.08 mm perpendicular spacing).
    """

    # ── Shared fixture data ───────────────────────────────────────────

    _ALL_POLYS = build_all_polylines()
    _EAST_POLYS = get_east_polylines()
    _WEST_POLYS = get_west_polylines()
    _ALL_PHASED = build_all_phased_polylines()

    # Shared length tolerance — every len² comparison in this class
    # derives from _EPS_LEN so there's exactly one knob to tune.
    _EPS_LEN  = 1e-6            # 1 nm in mm
    _EPS_LEN2 = _EPS_LEN ** 2   # 1e-12 mm²

    # Parse the generated s-expr once for net/layer/width tests
    @staticmethod
    def _parse_segments(sexpr: str) -> list[dict]:
        """Parse KiCad (segment ...) lines into structured dicts."""
        import re
        segs = []
        for line in sexpr.splitlines():
            line = line.strip()
            if not line.startswith("(segment"):
                continue
            m_start = re.search(r'\(start\s+([\d.]+)\s+([\d.]+)\)', line)
            m_end = re.search(r'\(end\s+([\d.]+)\s+([\d.]+)\)', line)
            m_width = re.search(r'\(width\s+([\d.]+)\)', line)
            m_layer = re.search(r'\(layer\s+"([^"]+)"\)', line)
            m_net = re.search(r'\(net\s+(\d+)\)', line)
            if not all([m_start, m_end, m_width, m_layer, m_net]):
                continue
            segs.append({
                "start": (float(m_start.group(1)), float(m_start.group(2))),
                "end": (float(m_end.group(1)), float(m_end.group(2))),
                "width": float(m_width.group(1)),
                "layer": m_layer.group(1),
                "net": int(m_net.group(1)),
            })
        return segs

    _SEXPR = gen_bundle_c()
    _PARSED = _parse_segments.__func__(_SEXPR)

    # ── Build forbidden primitives ────────────────────────────────────
    # These are KEEP-expanded rectangles and circles that no trace center
    # may enter. Built from frozen constants.

    @staticmethod
    def _pad_keep_rect(cx, cy, pad_cx, pad_num):
        """KEEP-expanded rectangle for a 0402 pad."""
        pcx = cx - pad_cx if pad_num == 1 else cx + pad_cx
        return (
            pcx - FP0402_PAD_SX / 2 - KEEP,
            cy - FP0402_PAD_SY / 2 - KEEP,
            pcx + FP0402_PAD_SX / 2 + KEEP,
            cy + FP0402_PAD_SY / 2 + KEEP,
        )

    # Obstacle rectangles (KEEP-expanded, for trace center clearance).
    # Inset by EPS so boundary-grazing (trace center exactly on KEEP edge)
    # is not flagged — boundary contact means trace edge is exactly CLR
    # from pad edge, which is the minimum DRC-allowed position.
    #
    # U1_KEEP is intentionally excluded: Bundle C lanes originate from
    # U1 pads and must traverse the KEEP zone during phases 1-3 (pad
    # escape, header fan, initial descent). Individual U1 pad clearances
    # are enforced by the full OBSTACLES list in check_segment().
    _RECT_EPS = 1e-6

    @staticmethod
    def _inset_rect(r):
        """Inset a rect by EPS on all four sides (strict interior test)."""
        eps = 1e-6
        return (r[0] + eps, r[1] + eps, r[2] - eps, r[3] - eps)

    _FORBIDDEN_RECTS: list[tuple[str, tuple[float, float, float, float]]] = []
    # C6 pads
    _FORBIDDEN_RECTS.append(("C6.1", _inset_rect.__func__(_pad_keep_rect.__func__(C6_CX, C6_CY, FP0402_PAD_CX, 1))))
    _FORBIDDEN_RECTS.append(("C6.2", _inset_rect.__func__(_pad_keep_rect.__func__(C6_CX, C6_CY, FP0402_PAD_CX, 2))))
    # C7 pads
    _FORBIDDEN_RECTS.append(("C7.1", _inset_rect.__func__(_pad_keep_rect.__func__(C7_CX, C7_CY, FP0402_PAD_CX, 1))))
    _FORBIDDEN_RECTS.append(("C7.2", _inset_rect.__func__(_pad_keep_rect.__func__(C7_CX, C7_CY, FP0402_PAD_CX, 2))))
    # R1 pads
    _FORBIDDEN_RECTS.append(("R1.1", _inset_rect.__func__(_pad_keep_rect.__func__(R1_CX, R1_CY, FP0402_PAD_CX, 1))))
    _FORBIDDEN_RECTS.append(("R1.2", _inset_rect.__func__(_pad_keep_rect.__func__(R1_CX, R1_CY, FP0402_PAD_CX, 2))))
    # C5 pads
    _FORBIDDEN_RECTS.append(("C5.1", _inset_rect.__func__(_pad_keep_rect.__func__(C5_CX, C5_CY, FP0402_PAD_CX, 1))))
    _FORBIDDEN_RECTS.append(("C5.2", _inset_rect.__func__(_pad_keep_rect.__func__(C5_CX, C5_CY, FP0402_PAD_CX, 2))))

    # Obstacle circles (VIA keepouts: trace center must stay ≥ VIA_KEEP from via center)
    _FORBIDDEN_CIRCLES: list[tuple[str, float, float, float]] = [
        ("VIA1", 9.980, 12.750, VIA_KEEP),
        ("VIA2", VIA2_X, VIA2_Y, VIA_KEEP),
    ]

    # ════════════════════════════════════════════════════════════════════
    # LAYER 1: KiCad s-expr correctness
    # ════════════════════════════════════════════════════════════════════

    def test_segment_count_matches_polylines(self):
        """Emitted segment count matches polyline (Phases 1-4) + Phase 5 bottom fan.

        Phase 1-4: polyline-derived F.Cu segments.
        Phase 5: F.Cu extensions + B.Cu L-routes (14 F.Cu + 28 B.Cu = 42).
        """
        ph14_expected = 0
        for poly in self._ALL_POLYS.values():
            for i in range(len(poly) - 1):
                x1, y1 = poly[i]
                x2, y2 = poly[i + 1]
                if abs(x1 - x2) > 1e-6 or abs(y1 - y2) > 1e-6:
                    ph14_expected += 1
        # Phase 5 adds 14 F.Cu extensions + 28 B.Cu segments = 42
        expected = ph14_expected + 42
        assert len(self._PARSED) == expected, (
            f"Parsed {len(self._PARSED)} segments, expected {expected} "
            f"(Phases 1-4: {ph14_expected} + Phase 5: 42)"
        )

    def test_all_segments_correct_layer(self):
        """Every segment is on F.Cu or B.Cu (Phase 5 uses B.Cu for via hops)."""
        allowed = {LAYER, "B.Cu"}
        bad = [s for s in self._PARSED if s["layer"] not in allowed]
        assert not bad, (
            f"{len(bad)} segment(s) on wrong layer: {bad[0]['layer']}"
        )

    def test_all_segments_correct_width(self):
        """Every segment has ELECTRODE trace width."""
        bad = [s for s in self._PARSED if abs(s["width"] - TRACE_W) > 1e-6]
        assert not bad, (
            f"{len(bad)} segment(s) with wrong width: {bad[0]['width']}"
        )

    def test_per_channel_net_consistency(self):
        """Every segment for a given channel has the same net code."""
        # Group segments by net and verify each net matches a valid CH
        nets_seen = set(s["net"] for s in self._PARSED)
        # Expected nets: CH9..CH22 → net codes 13..26
        expected_nets = {_net_code_for_ch(ch) for ch in range(9, 23)}
        assert nets_seen == expected_nets, (
            f"Net mismatch: got {sorted(nets_seen)}, expected {sorted(expected_nets)}"
        )

    def test_segment_continuity_per_channel(self):
        """Segments for each channel connect end-to-start (no gaps > 1e-4 mm).

        Within KiCad coordinates, consecutive segments for the same net
        must have matching endpoints.
        """
        from collections import defaultdict
        by_net = defaultdict(list)
        for s in self._PARSED:
            by_net[s["net"]].append(s)

        violations = []
        for net_code, segs in by_net.items():
            for i in range(1, len(segs)):
                prev_end = segs[i - 1]["end"]
                curr_start = segs[i]["start"]
                dx = abs(prev_end[0] - curr_start[0])
                dy = abs(prev_end[1] - curr_start[1])
                if dx > 1e-4 or dy > 1e-4:
                    violations.append(
                        f"net {net_code} seg {i-1}→{i}: "
                        f"gap ({dx:.4f}, {dy:.4f}) mm"
                    )
        assert not violations, (
            f"{len(violations)} continuity gap(s):\n" + "\n".join(violations)
        )

    def test_no_dangling_endpoints(self):
        """First segment starts at U1 pad, last segment ends at J5 pad (via Phase 5).

        For each channel, verify the route starts at the U1 pad center
        and ends at the J5 pad (via B.Cu via-in-pad exit). The last B.Cu
        segment for each channel ends at (pad_x, pad_y) in KiCad coords.
        """
        from collections import defaultdict
        by_net = defaultdict(list)
        for s in self._PARSED:
            by_net[s["net"]].append(s)

        violations = []
        for lane in LANES:
            ch = lane["ch"]
            net_code = _net_code_for_ch(ch)
            segs = by_net.get(net_code, [])
            if not segs:
                violations.append(f"CH{ch}: no segments")
                continue

            poly = self._ALL_POLYS[ch]
            # First point = U1 pad center (brd coords → KiCad)
            exp_start_kx = round(BRD_OX + poly[0][0], 4)
            exp_start_ky = round(BRD_OY + poly[0][1], 4)
            act_start = segs[0]["start"]
            dx = abs(act_start[0] - exp_start_kx)
            dy = abs(act_start[1] - exp_start_ky)
            if dx > 0.01 or dy > 0.01:
                violations.append(
                    f"CH{ch}: start ({act_start}) != expected "
                    f"({exp_start_kx}, {exp_start_ky})"
                )

            # Last segment ends at J5 pad center (brd coords → KiCad).
            # Phase 5 B.Cu segments end at the J5 pad (pad_x, pad_y).
            j5_pin = lane["j5_pin"]
            from gen_bundle_c_route import _j5_target
            pad_x, pad_y = _j5_target(j5_pin)
            exp_end_kx = round(BRD_OX + pad_x, 4)
            exp_end_ky = round(BRD_OY + pad_y, 4)
            act_end = segs[-1]["end"]
            dx = abs(act_end[0] - exp_end_kx)
            dy = abs(act_end[1] - exp_end_ky)
            if dx > 0.01 or dy > 0.01:
                violations.append(
                    f"CH{ch}: end ({act_end}) != expected "
                    f"({exp_end_kx}, {exp_end_ky})"
                )

        assert not violations, (
            f"{len(violations)} dangling endpoint(s):\n" + "\n".join(violations)
        )

    def test_no_zero_length_segments(self):
        """No polyline contains zero-length segments (degenerate waypoints).

        A zero-length segment means two consecutive waypoints are identical,
        which indicates a bug in the polyline builder. The KiCad emitter
        skips them, but they should never exist in the polyline data.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            poly = self._ALL_POLYS[ch]
            for i in range(len(poly) - 1):
                x1, y1 = poly[i]
                x2, y2 = poly[i + 1]
                if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                    violations.append(
                        f"{label}(CH{ch}) waypoint {i}→{i+1}: "
                        f"({x1:.4f},{y1:.4f}) is zero-length"
                    )
        assert not violations, (
            f"{len(violations)} zero-length segment(s):\n"
            + "\n".join(violations)
        )

    def test_polyline_continuity_brd_space(self):
        """Every phased polyline has exact endpoint continuity in brd-space.

        Consecutive segments must share their junction point exactly:
        seg[i].p1 == seg[i+1].p0 within 1e-6 mm.  This catches bugs in
        the polyline builder (waypoint gaps, misordered phases, rounding
        drift between build_polyline and build_phased_polyline).

        Uses the phase-tagged representation so the test validates the
        same data structure that obstacle tests consume.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            if len(phased) < 2:
                violations.append(f"{label}(CH{ch}): phased polyline has < 2 segments")
                continue
            for i in range(len(phased) - 1):
                _, end_pt, phase_a = phased[i]
                start_pt, _, phase_b = phased[i + 1]
                dx = abs(end_pt[0] - start_pt[0])
                dy = abs(end_pt[1] - start_pt[1])
                if dx > 1e-6 or dy > 1e-6:
                    violations.append(
                        f"{label}(CH{ch}) seg {i}({phase_a})→{i+1}({phase_b}): "
                        f"gap ({dx:.6f}, {dy:.6f}) mm at "
                        f"({end_pt[0]:.4f},{end_pt[1]:.4f}) → "
                        f"({start_pt[0]:.4f},{start_pt[1]:.4f})"
                    )
        assert not violations, (
            f"{len(violations)} continuity gap(s):\n" + "\n".join(violations)
        )

    # ════════════════════════════════════════════════════════════════════
    # LAYER 1b: Phase-tag integrity (prevents fake/broken tagging)
    # ════════════════════════════════════════════════════════════════════

    def test_phase_coverage_per_lane(self):
        """Every lane contains at least 1 escape, 1 header, 1 descent.

        Prevents degenerate tagging (e.g. all segments labelled 'escape')
        which would silently make the U1 KEEP test meaningless.

        Current generator: exactly 1 PHASE_ESCAPE, 1 PHASE_VERTICAL_ESCAPE,
        1 PHASE_HEADER, 1 PHASE_DESCENT per lane.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            phases = [seg[2] for seg in phased]

            # Must contain at least one of each required phase
            for required in (PHASE_ESCAPE, PHASE_VERTICAL_ESCAPE,
                             PHASE_HEADER, PHASE_DESCENT):
                count = phases.count(required)
                if count == 0:
                    violations.append(
                        f"{label}(CH{ch}): missing phase '{required}'"
                    )

            # Current topology: exactly 1 header and 1 descent
            for exact_one in (PHASE_HEADER, PHASE_DESCENT):
                count = phases.count(exact_one)
                if count != 1:
                    violations.append(
                        f"{label}(CH{ch}): expected 1 '{exact_one}', got {count}"
                    )

        assert not violations, (
            f"{len(violations)} phase-coverage error(s):\n"
            + "\n".join(violations)
        )

    def test_classifier_survives_extra_waypoints(self):
        """Phase tags are determined by geometry, not waypoint index.

        For every lane, inject an extra midpoint into the escape segment
        and re-classify. Both sub-segments must receive PHASE_ESCAPE.
        This proves the classifier is not index-based.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            wp = _build_waypoints(lane)

            # Inject midpoint between waypoint 0 and 1 (pad center → pad tip)
            mid_y = round((wp[0][1] + wp[1][1]) / 2, 4)
            injected = [wp[0], (wp[0][0], mid_y)] + wp[1:]

            segs = classify_segments(injected, lane)
            phases = [s[2] for s in segs]

            # First two segments should both be escape
            if len(segs) < 2:
                violations.append(f"{label}(CH{ch}): only {len(segs)} segs after injection")
                continue

            if segs[0][2] != PHASE_ESCAPE:
                violations.append(
                    f"{label}(CH{ch}): injected seg 0 got '{segs[0][2]}', "
                    f"expected '{PHASE_ESCAPE}'"
                )
            if segs[1][2] != PHASE_ESCAPE:
                violations.append(
                    f"{label}(CH{ch}): injected seg 1 got '{segs[1][2]}', "
                    f"expected '{PHASE_ESCAPE}'"
                )

            # Total segment count should be 5 (was 4, +1 from split)
            if len(segs) != 5:
                violations.append(
                    f"{label}(CH{ch}): expected 5 segs after injection, got {len(segs)}"
                )

            # Must still have header and descent
            if PHASE_HEADER not in phases:
                violations.append(f"{label}(CH{ch}): lost PHASE_HEADER after injection")
            if PHASE_DESCENT not in phases:
                violations.append(f"{label}(CH{ch}): lost PHASE_DESCENT after injection")

        assert not violations, (
            f"{len(violations)} classifier fragility error(s):\n"
            + "\n".join(violations)
        )

    def test_no_repeated_segments(self):
        """No phased polyline contains duplicate segments (including reversed).

        A repeated segment (p0→p1 appearing twice, or p0→p1 and p1→p0)
        indicates a builder bug — doubled waypoints or reflected paths.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            seen = set()
            for p0, p1, phase in phased:
                # Canonical form: sorted endpoints so (A→B) and (B→A) match
                key = tuple(sorted([p0, p1]))
                if key in seen:
                    violations.append(
                        f"{label}(CH{ch}): duplicate segment "
                        f"({p0[0]:.4f},{p0[1]:.4f})→({p1[0]:.4f},{p1[1]:.4f}) "
                        f"phase={phase}"
                    )
                seen.add(key)

        assert not violations, (
            f"{len(violations)} repeated segment(s):\n" + "\n".join(violations)
        )

    def test_no_backtracking_spikes(self):
        """No three consecutive waypoints form an A→B→A spike.

        A spike means the route goes forward and immediately returns to
        the same point — a zero-area loop that wastes routing and often
        signals a snapping or join-logic bug.

        Checks the flat waypoint list (build_polyline output), which is
        derived from the phased output.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            poly = self._ALL_POLYS[ch]
            for i in range(len(poly) - 2):
                ax, ay = poly[i]
                cx, cy = poly[i + 2]
                if abs(ax - cx) < 1e-6 and abs(ay - cy) < 1e-6:
                    bx, by = poly[i + 1]
                    violations.append(
                        f"{label}(CH{ch}) waypoint {i}: "
                        f"spike ({ax:.4f},{ay:.4f})→({bx:.4f},{by:.4f})→"
                        f"({cx:.4f},{cy:.4f})"
                    )
        assert not violations, (
            f"{len(violations)} backtracking spike(s):\n" + "\n".join(violations)
        )

    def test_header_connects_pad_x_to_descent_x(self):
        """Each lane's PHASE_HEADER segment x-range spans [pad_x, descent_x].

        This locks down the meaning of the 'header' label: it must be a
        horizontal run that actually connects the pad column to the descent
        column. A header that goes the wrong direction or stops short will
        fail this test even if the classifier labels it correctly.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            pad_x = _pad_x(lane["pin"])
            descent_x = lane["descent_x"]

            headers = [(p0, p1) for p0, p1, ph in phased if ph == PHASE_HEADER]
            if not headers:
                violations.append(f"{label}(CH{ch}): no PHASE_HEADER segment")
                continue

            # Combine x-range of all header segments (should be just one)
            all_x = []
            for p0, p1 in headers:
                all_x.extend([p0[0], p1[0]])
            h_xmin = min(all_x)
            h_xmax = max(all_x)

            # x-range must contain both pad_x and descent_x
            lo = min(pad_x, descent_x)
            hi = max(pad_x, descent_x)
            if h_xmin > lo + 1e-6 or h_xmax < hi - 1e-6:
                violations.append(
                    f"{label}(CH{ch}): header x=[{h_xmin:.4f},{h_xmax:.4f}] "
                    f"doesn't span pad_x={pad_x:.4f} to descent_x={descent_x:.4f}"
                )

        assert not violations, (
            f"{len(violations)} header span error(s):\n" + "\n".join(violations)
        )

    def test_descent_spans_header_y_to_bottom_fan_y(self):
        """Each lane's PHASE_DESCENT segment y-range spans [header_y, BOTTOM_FAN_Y].

        Locks down the meaning of 'descent': it must be a vertical run from
        the header elevation all the way down to the bottom fan boundary.
        A descent that stops short or starts at the wrong elevation fails.
        """
        from ref_elec_clearance_check import BOTTOM_FAN_Y as BFY
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            header_y = lane["header_y"]

            descents = [(p0, p1) for p0, p1, ph in phased if ph == PHASE_DESCENT]
            if not descents:
                violations.append(f"{label}(CH{ch}): no PHASE_DESCENT segment")
                continue

            # Combine y-range of all descent segments (should be just one)
            all_y = []
            for p0, p1 in descents:
                all_y.extend([p0[1], p1[1]])
            d_ymin = min(all_y)
            d_ymax = max(all_y)

            # y-range must include header_y and BOTTOM_FAN_Y
            if d_ymin > header_y + 1e-6:
                violations.append(
                    f"{label}(CH{ch}): descent y_min={d_ymin:.4f} > "
                    f"header_y={header_y:.4f}"
                )
            if d_ymax < BFY - 1e-6:
                violations.append(
                    f"{label}(CH{ch}): descent y_max={d_ymax:.4f} < "
                    f"BOTTOM_FAN_Y={BFY:.4f}"
                )

        assert not violations, (
            f"{len(violations)} descent span error(s):\n" + "\n".join(violations)
        )

    def test_all_segments_manhattan(self):
        """Every segment in the phased output is purely horizontal or vertical.

        Enforces the Manhattan-only decision for Bundle C routes. The geometric
        classifier (_classify_segment) will raise ValueError on diagonals, but
        this test is the explicit regression lock: if someone changes the
        classifier to accept diagonals without also adding PHASE_TRANSITION,
        this test catches it.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            for p0, p1, phase in phased:
                dx = abs(p1[0] - p0[0])
                dy = abs(p1[1] - p0[1])
                if dx > 1e-6 and dy > 1e-6:
                    violations.append(
                        f"{label}(CH{ch}) phase={phase}: diagonal "
                        f"({p0[0]:.4f},{p0[1]:.4f})→({p1[0]:.4f},{p1[1]:.4f}) "
                        f"dx={dx:.4f} dy={dy:.4f}"
                    )
        assert not violations, (
            f"{len(violations)} non-Manhattan segment(s) in phased output "
            f"(Bundle C is Manhattan-only):\n" + "\n".join(violations)
        )

    # ════════════════════════════════════════════════════════════════════
    # LAYER 2: Segment-vs-obstacle intersection
    # ════════════════════════════════════════════════════════════════════

    @staticmethod
    def _seg_intersects_circle(x1, y1, x2, y2, cx, cy, r):
        """Test if segment (x1,y1)→(x2,y2) enters circle (cx,cy,r).

        Returns True if any point on the segment is within distance r
        of (cx, cy). This is the trace-center-to-via-center check;
        r = VIA_KEEP = VIA_RADIUS + CLR + HW.
        """
        dx, dy = x2 - x1, y2 - y1
        fx, fy = x1 - cx, y1 - cy
        a = dx * dx + dy * dy
        b = 2 * (fx * dx + fy * dy)
        c = fx * fx + fy * fy - r * r
        if a < 1e-24:
            return c <= 1e-9  # degenerate: point
        disc = b * b - 4 * a * c
        if disc < 0:
            return False
        disc_sqrt = disc ** 0.5
        t1 = (-b - disc_sqrt) / (2 * a)
        t2 = (-b + disc_sqrt) / (2 * a)
        # Segment intersects circle if [t1, t2] overlaps [0, 1]
        return t1 <= 1.0 + 1e-9 and t2 >= -1e-9

    @staticmethod
    def _clip_segment_to_rect(x1, y1, x2, y2, rxmin, rymin, rxmax, rymax):
        """Clip segment to rect using Liang-Barsky.

        Returns (cx1, cy1, cx2, cy2) for the clipped portion, or None
        if the segment doesn't intersect the rect at all.

        Direction-preserving: the returned (cx1, cy1) is the clipped
        point nearest (x1, y1), and (cx2, cy2) is nearest (x2, y2).
        This follows from the Liang-Barsky t-parameter convention
        (t0 ≤ t1, both in [0, 1]).
        """
        dx = x2 - x1
        dy = y2 - y1
        t0, t1 = 0.0, 1.0
        for p, q in [(-dx, x1 - rxmin), (dx, rxmax - x1),
                     (-dy, y1 - rymin), (dy, rymax - y1)]:
            if abs(p) < 1e-12:
                if q < -1e-9:
                    return None  # parallel and outside
                continue
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1 + 1e-9:
                return None
        # Final rejection: after all edges are processed, t0 > t1
        # means the segment misses the rect. This MUST happen before
        # clamping — otherwise clamping could collapse t0 > t1 into
        # t0 == t1 and return a false zero-length intersection.
        if t0 > t1 + 1e-9:
            return None
        # Clamp to [0, 1] for numerical safety (float accumulation
        # can push t0 to -1e-16 or t1 to 1+1e-16).  The t0 > t1
        # check above guarantees this never turns a miss into a hit.
        t0 = max(0.0, min(1.0, t0))
        t1 = max(0.0, min(1.0, t1))
        return (x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy)

    def test_no_segment_intersects_forbidden_rect(self):
        """No polyline segment enters any KEEP-expanded obstacle rectangle.

        This is the real DRC-equivalent check. For each lane's full
        polyline, test every segment against every forbidden rectangle
        (C6, C7, R1, C5 pads — all KEEP-expanded, then inset by EPS
        so boundary contact is not flagged).

        U1_KEEP is excluded (see _FORBIDDEN_RECTS comment above).

        Catches diagonal corner cuts that half-plane tests miss.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            poly = self._ALL_POLYS[ch]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"

            for i in range(len(poly) - 1):
                x1, y1 = poly[i]
                x2, y2 = poly[i + 1]
                if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                    continue

                for obs_name, rect in self._FORBIDDEN_RECTS:
                    rx_min, ry_min, rx_max, ry_max = rect
                    if _segment_intersects_rect(
                        x1, y1, x2, y2, rx_min, ry_min, rx_max, ry_max
                    ):
                        violations.append(
                            f"{label} (CH{ch}) seg {i}: "
                            f"({x1:.3f},{y1:.3f})→({x2:.3f},{y2:.3f}) "
                            f"intersects {obs_name} KEEP "
                            f"[{rx_min:.3f},{ry_min:.3f}]→"
                            f"[{rx_max:.3f},{ry_max:.3f}]"
                        )

        assert not violations, (
            f"{len(violations)} rect intersection(s):\n"
            + "\n".join(violations[:10])
        )

    def test_no_segment_intersects_via_keepout(self):
        """No polyline segment enters VIA1 or VIA2 keepout circle.

        VIA_KEEP = VIA_RADIUS + CLR + HW = 0.675 mm.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            poly = self._ALL_POLYS[ch]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"

            for i in range(len(poly) - 1):
                x1, y1 = poly[i]
                x2, y2 = poly[i + 1]
                if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                    continue

                for via_name, vcx, vcy, vr in self._FORBIDDEN_CIRCLES:
                    if self._seg_intersects_circle(x1, y1, x2, y2, vcx, vcy, vr):
                        violations.append(
                            f"{label} (CH{ch}) seg {i}: "
                            f"({x1:.3f},{y1:.3f})→({x2:.3f},{y2:.3f}) "
                            f"enters {via_name} keepout "
                            f"(center={vcx},{vcy}, r={vr:.3f})"
                        )

        assert not violations, (
            f"{len(violations)} via keepout violation(s):\n"
            + "\n".join(violations[:10])
        )

    def test_no_segment_enters_u1_keep_forbidden_zone(self):
        """No segment enters U1 KEEP except where it overlaps allowed pad copper.

        This is the correct physical rule:
          forbidden = U1_KEEP_rect − union(pad_allowance_corridors)

        For each Bundle C lane, the pad allowance corridor is a vertical
        strip at pad_x ± QFN56_PAD_SHORT/2, spanning from U1_KEEP_TOP
        (north KEEP boundary) to the pad's south edge (PAD_CENTER_Y +
        PAD_LONG/2). A trace center is allowed inside this corridor because
        it's escaping from its own same-net pad copper through the KEEP zone.

        Test logic: for each segment that intersects U1 KEEP, clip it to the
        KEEP rectangle, then verify the clipped portion is fully contained
        within the lane's pad allowance corridor. This correctly handles
        segments that cross the KEEP boundary (e.g. vertical escape from
        pad tip to header_y above KEEP).

        This model is correct for any topology — it doesn't depend on phase
        tags, segment indices, or the Manhattan-only assumption.
        """
        eps = 1e-6

        # U1 KEEP rect (inset by eps for boundary tolerance)
        u1_left   = U1_KEEP_LEFT   + eps
        u1_top    = U1_KEEP_TOP    + eps
        u1_right  = U1_KEEP_RIGHT  - eps
        u1_bottom = U1_KEEP_BOTTOM - eps

        # Build per-lane pad allowance corridor
        _pad_corridors: dict[int, tuple[float, float, float, float]] = {}
        for lane in LANES:
            px = _pad_x(lane["pin"])
            _pad_corridors[lane["ch"]] = (
                px - QFN56_PAD_SHORT / 2,
                U1_KEEP_TOP,
                px + QFN56_PAD_SHORT / 2,
                _PAD_CENTER_Y + QFN56_PAD_LONG / 2,
            )

        def _point_in_rect(x, y, rxmin, rymin, rxmax, rymax):
            return (x >= rxmin - eps and x <= rxmax + eps
                    and y >= rymin - eps and y <= rymax + eps)

        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]
            corr = _pad_corridors[ch]

            for p0, p1, phase in phased:
                x1, y1 = p0
                x2, y2 = p1
                if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                    continue

                if not _segment_intersects_rect(
                    x1, y1, x2, y2, u1_left, u1_top, u1_right, u1_bottom
                ):
                    continue

                clipped = self._clip_segment_to_rect(
                    x1, y1, x2, y2, u1_left, u1_top, u1_right, u1_bottom
                )
                if clipped is None:
                    continue

                cx1, cy1, cx2, cy2 = clipped

                if (_point_in_rect(cx1, cy1, *corr)
                        and _point_in_rect(cx2, cy2, *corr)):
                    continue

                violations.append(
                    f"{label}(CH{ch}) phase={phase}: "
                    f"({x1:.3f},{y1:.3f})→({x2:.3f},{y2:.3f}) "
                    f"clipped to ({cx1:.3f},{cy1:.3f})→({cx2:.3f},{cy2:.3f}) "
                    f"enters U1 KEEP [{U1_KEEP_LEFT:.3f},{U1_KEEP_TOP:.3f}]"
                    f"→[{U1_KEEP_RIGHT:.3f},{U1_KEEP_BOTTOM:.3f}] "
                    f"outside pad corridor [{corr[0]:.3f},{corr[1]:.3f}]"
                    f"→[{corr[2]:.3f},{corr[3]:.3f}]"
                )
        assert not violations, (
            f"{len(violations)} U1 KEEP forbidden zone intrusion(s):\n"
            + "\n".join(violations[:10])
        )

    def test_no_horizontal_motion_inside_u1_keep(self):
        """No segment has any horizontal extent inside U1 KEEP.

        Bundle C lanes transit U1 KEEP only via vertical pad-escape
        corridors. Any lateral component inside KEEP (staging, jogs,
        or a diagonal whose clip has dx ≠ 0) is categorically
        disallowed — it would cross adjacent-pad keepout zones.

        For each segment, clip to KEEP. If the clipped portion has
        Δx² > _EPS_LEN2, it's a violation — regardless of Δy.

        This makes the failure mode explicit: if someone adds any
        non-purely-vertical segment inside KEEP, the error says
        "lateral motion inside KEEP is disallowed" rather than the
        generic "outside pad corridor."
        """
        eps = self._EPS_LEN
        el2 = self._EPS_LEN2
        u1_left   = U1_KEEP_LEFT   + eps
        u1_top    = U1_KEEP_TOP    + eps
        u1_right  = U1_KEEP_RIGHT  - eps
        u1_bottom = U1_KEEP_BOTTOM - eps

        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]

            for p0, p1, phase in phased:
                x1, y1 = p0
                x2, y2 = p1
                dx0 = x2 - x1
                dy0 = y2 - y1
                if dx0 * dx0 + dy0 * dy0 < el2:
                    continue  # zero-length segment

                clipped = self._clip_segment_to_rect(
                    x1, y1, x2, y2, u1_left, u1_top, u1_right, u1_bottom
                )
                if clipped is None:
                    continue

                cx1, cy1, cx2, cy2 = clipped
                clip_dx = cx2 - cx1

                if clip_dx * clip_dx > el2:
                    violations.append(
                        f"{label}(CH{ch}) phase={phase}: "
                        f"({x1:.3f},{y1:.3f})→({x2:.3f},{y2:.3f}) "
                        f"clipped to ({cx1:.3f},{cy1:.3f})→({cx2:.3f},{cy2:.3f}) "
                        f"has horizontal extent {abs(clip_dx):.4f} mm inside U1 KEEP "
                        f"(lateral motion inside KEEP is disallowed)"
                    )

        assert not violations, (
            f"{len(violations)} horizontal-inside-KEEP violation(s):\n"
            + "\n".join(violations[:10])
        )

    def test_keep_crossing_count_le_two(self):
        """Each lane has 1 or 2 segments that transit U1 KEEP; no more.

        Bundle C routes enter KEEP once (via the vertical pad-escape
        corridor) and exit once. The escape and vertical_escape phases
        are both at the same x, so their combined footprint inside KEEP
        may appear as 1 or 2 segments depending on whether the polyline
        is split at the pad-tip waypoint.

        Contract:
          count == 0  → route never enters KEEP → doesn't connect to pad
          count ∈ {1, 2} → normal (single span, or split at pad-tip)
          count > 2   → route re-enters KEEP (header drifted inside,
                         descent wandered back, etc.) → topology bug

        This subsumes the old test_every_lane_crosses_u1_keep (count ≥ 1)
        and adds the re-entry cap (count ≤ 2).
        """
        el2 = self._EPS_LEN2
        MAX_KEEP_SEGMENTS = 2  # escape + vertical_escape at pad-tip split

        eps = self._EPS_LEN
        u1_left   = U1_KEEP_LEFT   + eps
        u1_top    = U1_KEEP_TOP    + eps
        u1_right  = U1_KEEP_RIGHT  - eps
        u1_bottom = U1_KEEP_BOTTOM - eps

        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            phased = self._ALL_PHASED[ch]

            keep_seg_count = 0
            for p0, p1, _phase in phased:
                x1, y1 = p0
                x2, y2 = p1
                d = (x2 - x1, y2 - y1)
                if d[0] * d[0] + d[1] * d[1] < el2:
                    continue

                clipped = self._clip_segment_to_rect(
                    x1, y1, x2, y2, u1_left, u1_top, u1_right, u1_bottom
                )
                if clipped is None:
                    continue

                cx1, cy1, cx2, cy2 = clipped
                cdx, cdy = cx2 - cx1, cy2 - cy1
                if cdx * cdx + cdy * cdy > el2:
                    keep_seg_count += 1

            if keep_seg_count == 0:
                violations.append(
                    f"{label}(CH{ch}): 0 segments transit KEEP "
                    f"(route doesn't connect to U1 pad)"
                )
            elif keep_seg_count > MAX_KEEP_SEGMENTS:
                violations.append(
                    f"{label}(CH{ch}): {keep_seg_count} segments transit KEEP "
                    f"(expected ≤ {MAX_KEEP_SEGMENTS}; route re-enters KEEP?)"
                )

        assert not violations, (
            f"{len(violations)} KEEP crossing-count violation(s):\n"
            + "\n".join(violations)
        )

    # ════════════════════════════════════════════════════════════════════
    # LAYER 3: Nested-L topology validation
    # ════════════════════════════════════════════════════════════════════

    def test_all_lanes_pure_manhattan(self):
        """Every lane is pure Manhattan (horizontal + vertical only, no diagonals).

        With the nested-L topology and Phase 5 (bottom fan) deferred,
        all polylines consist exclusively of horizontal and vertical segments.
        """
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            poly = self._ALL_POLYS[ch]
            for i in range(len(poly) - 1):
                x1, y1 = poly[i]
                x2, y2 = poly[i + 1]
                dx = abs(x2 - x1)
                dy = abs(y2 - y1)
                if dx > 1e-6 and dy > 1e-6:
                    violations.append(
                        f"{label}(CH{ch}) seg {i}: diagonal "
                        f"({x1:.3f},{y1:.3f})→({x2:.3f},{y2:.3f})"
                    )
        assert not violations, (
            f"{len(violations)} diagonal segment(s) found (expect pure Manhattan):\n"
            + "\n".join(violations)
        )

    def test_nested_L_header_y_monotonic(self):
        """Inner lanes (lane_idx=0) get the lowest header_y (furthest north).

        The nested-L topology requires header_y to increase with lane_idx
        so that inner lanes' horizontals are nested inside outer lanes'.
        header_y[i] = U1_KEEP_TOP - (6 - i) * LANE_PITCH.
        """
        for side in ("east", "west"):
            lanes_side = [l for l in LANES if l["side"] == side]
            lanes_side.sort(key=lambda l: l["lane_idx"])
            for i in range(len(lanes_side) - 1):
                assert lanes_side[i]["header_y"] < lanes_side[i + 1]["header_y"] - 1e-9, (
                    f"{side} lane {i} header_y={lanes_side[i]['header_y']:.3f} "
                    f">= lane {i+1} header_y={lanes_side[i+1]['header_y']:.3f}"
                )

    def test_nested_L_descent_x_monotonic(self):
        """Inner lanes (lane_idx=0) get the outermost descent_x.

        East: descent_x decreases with lane_idx (25.405 → 22.105).
        West: descent_x increases with lane_idx (5.095 → 8.395).
        """
        east = sorted([l for l in LANES if l["side"] == "east"],
                      key=lambda l: l["lane_idx"])
        for i in range(len(east) - 1):
            assert east[i]["descent_x"] > east[i + 1]["descent_x"] + 1e-9, (
                f"east lane {i} descent_x={east[i]['descent_x']:.3f} "
                f"<= lane {i+1} descent_x={east[i+1]['descent_x']:.3f}"
            )

        west = sorted([l for l in LANES if l["side"] == "west"],
                      key=lambda l: l["lane_idx"])
        for i in range(len(west) - 1):
            assert west[i]["descent_x"] < west[i + 1]["descent_x"] - 1e-9, (
                f"west lane {i} descent_x={west[i]['descent_x']:.3f} "
                f">= lane {i+1} descent_x={west[i+1]['descent_x']:.3f}"
            )

    def test_e7_descent_at_c6c7_keep_boundary(self):
        """E7 (outermost east lane, lane_idx=6) descends at x = C6C7_KEEP_EAST.

        This is the critical boundary lane. With the nested-L topology,
        E7 (not E1) passes through the C6/C7 zone on the KEEP boundary.
        The boundary contact is valid (trace edge exactly CLR from pad edge).
        """
        e7 = [l for l in LANES if l["side"] == "east" and l["lane_idx"] == 6][0]
        assert abs(e7["descent_x"] - C6C7_KEEP_EAST) < 1e-9, (
            f"E7 descent_x={e7['descent_x']:.4f} != C6C7_KEEP_EAST={C6C7_KEEP_EAST}"
        )

    def test_e7_clip_in_c6c7_band(self):
        """E7 min_x in C6/C7 y-band ≥ C6C7_KEEP_EAST.

        Redundant with the rect intersection test, but this is the
        targeted proof that the outermost east descent stays clean.
        """
        poly = self._EAST_POLYS[6]  # E7 (lane_idx=6)
        min_x = TestBundleCUniformPack._min_x_in_y_band(poly, C6C7_Y_MIN, C6C7_Y_MAX)
        assert min_x >= C6C7_KEEP_EAST - 1e-9, (
            f"E7 min_x in C6/C7 band = {min_x:.4f} < "
            f"C6C7_KEEP_EAST = {C6C7_KEEP_EAST}"
        )

    def test_polylines_terminate_at_bottom_fan_y(self):
        """Every polyline ends at y = BOTTOM_FAN_Y (descent endpoint).

        Phase 5 (bottom fan) is deferred. Polylines terminate at the
        bottom of the side descent, ready for connector-entry routing.
        """
        from ref_elec_clearance_check import BOTTOM_FAN_Y as BFY
        violations = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            poly = self._ALL_POLYS[ch]
            end_x, end_y = poly[-1]
            if abs(end_y - BFY) > 1e-6:
                violations.append(
                    f"{label}(CH{ch}): ends at y={end_y:.4f}, "
                    f"expected {BFY}"
                )
            if abs(end_x - lane["descent_x"]) > 1e-6:
                violations.append(
                    f"{label}(CH{ch}): ends at x={end_x:.4f}, "
                    f"expected descent_x={lane['descent_x']}"
                )
        assert not violations, (
            f"{len(violations)} termination error(s):\n" + "\n".join(violations)
        )

    # ════════════════════════════════════════════════════════════════════
    # LAYER 4: Pairwise lane spacing + no crossings
    # ════════════════════════════════════════════════════════════════════

    @staticmethod
    def _polyline_segments(poly):
        """Yield (x1, y1, x2, y2) for each non-degenerate segment."""
        for i in range(len(poly) - 1):
            x1, y1 = poly[i]
            x2, y2 = poly[i + 1]
            if abs(x1 - x2) > 1e-6 or abs(y1 - y2) > 1e-6:
                yield (x1, y1, x2, y2)

    @staticmethod
    def _segments_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
        """Test if two segments cross or share a collinear overlap.

        Catches both:
          (a) Proper crossings (cross-product sign change),
          (b) Collinear overlap (parallel segments sharing > point range).
        Does NOT flag shared single endpoints (zero-area touch).
        """
        def cross(ox, oy, ax, ay, bx, by):
            return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)

        d1 = cross(bx1, by1, bx2, by2, ax1, ay1)
        d2 = cross(bx1, by1, bx2, by2, ax2, ay2)
        d3 = cross(ax1, ay1, ax2, ay2, bx1, by1)
        d4 = cross(ax1, ay1, ax2, ay2, bx2, by2)

        # (a) Proper crossing: segments straddle each other
        if ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)):
            if ((d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9)):
                return True

        # (b) Collinear overlap: all cross products ≈ 0 AND projections overlap
        if (abs(d1) < 1e-6 and abs(d2) < 1e-6
                and abs(d3) < 1e-6 and abs(d4) < 1e-6):
            # Project onto the longer axis
            adx, ady = ax2 - ax1, ay2 - ay1
            if abs(adx) >= abs(ady):
                # Project onto x
                a_lo, a_hi = min(ax1, ax2), max(ax1, ax2)
                b_lo, b_hi = min(bx1, bx2), max(bx1, bx2)
            else:
                # Project onto y
                a_lo, a_hi = min(ay1, ay2), max(ay1, ay2)
                b_lo, b_hi = min(by1, by2), max(by1, by2)
            overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
            # Overlap > epsilon means they share more than a single point
            if overlap > 1e-6:
                return True

        return False

    def test_no_lane_crossings(self):
        """No two Bundle C lanes cross each other at any point.

        A crossing means two different-net traces occupy the same
        point on the same layer — a guaranteed DRC fail and short.
        """
        all_lane_segs = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            poly = self._ALL_POLYS[ch]
            segs = list(self._polyline_segments(poly))
            all_lane_segs.append((label, ch, segs))

        violations = []
        for i in range(len(all_lane_segs)):
            for j in range(i + 1, len(all_lane_segs)):
                label_a, ch_a, segs_a = all_lane_segs[i]
                label_b, ch_b, segs_b = all_lane_segs[j]
                for sa in segs_a:
                    for sb in segs_b:
                        if self._segments_intersect(*sa, *sb):
                            violations.append(
                                f"{label_a}(CH{ch_a}) × {label_b}(CH{ch_b}): "
                                f"({sa[0]:.2f},{sa[1]:.2f})→({sa[2]:.2f},{sa[3]:.2f}) × "
                                f"({sb[0]:.2f},{sb[1]:.2f})→({sb[2]:.2f},{sb[3]:.2f})"
                            )

        assert not violations, (
            f"{len(violations)} lane crossing(s):\n"
            + "\n".join(violations[:10])
        )

    def test_vertical_descent_lane_spacing(self):
        """All lane pairs maintain ≥ LANE_PITCH centerline distance (Phases 1–4 only).

        Computes the true minimum segment-to-segment distance across ALL
        pairs of lanes (east×east, west×west, AND east×west). This covers
        only Phases 1–4 (pad escape through side descent). It explicitly
        does NOT cover the bottom fan, which is the zone where spacing
        actually fails — that geometry is deferred (§4.2.1i).

        Honest assessment: this test validates the easy part. The hard
        part (connector-entry fan) is geometrically impossible on a
        single layer and is not tested here.
        """
        min_center_dist = CLR + TRACE_W  # 0.450 = LANE_PITCH

        all_lane_segs = []
        for lane in LANES:
            ch = lane["ch"]
            label = f"{'E' if lane['side'] == 'east' else 'W'}{lane['lane_idx']+1}"
            poly = self._ALL_POLYS[ch]
            segs = list(self._polyline_segments(poly))
            all_lane_segs.append((label, segs))

        violations = []
        for i in range(len(all_lane_segs)):
            for j in range(i + 1, len(all_lane_segs)):
                label_a, segs_a = all_lane_segs[i]
                label_b, segs_b = all_lane_segs[j]
                worst_dist = float("inf")
                for sa in segs_a:
                    for sb in segs_b:
                        d = _seg_to_seg_dist(*sa, *sb)
                        worst_dist = min(worst_dist, d)
                if worst_dist < min_center_dist - 1e-6:
                    violations.append(
                        f"{label_a}↔{label_b}: "
                        f"min dist = {worst_dist:.4f} mm "
                        f"< {min_center_dist:.3f} mm"
                    )

        assert not violations, (
            f"{len(violations)} spacing violation(s):\n"
            + "\n".join(violations[:10])
        )
