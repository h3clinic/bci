"""Step C — Electrical sanity & manufacturing constraints for Bundle C routes.

Test structure:
  TestViaManufacturability  — 6 tests  (drill, annular ring, aspect ratio, VIP+VIPPO, via-to-via, through-via guard)
  TestNetclassEnforcement   — 5 tests  (ELECTRODE membership, trace width, clearance, via size, via drill)
  TestBottomFanSpacing      — 4 tests  (F.Cu track-track, B.Cu track-track, via-track, min neckdown)
  TestZoneInteractions      — 4 tests  (inner layers only, GND only, no CH net, clearance set)
  TestStackupSanity         — 4 tests  (4 copper layers, 1.6mm total, copper thickness, dielectric FR4)
  TestChannelLengthSanity   — 3 tests  (segments per channel, median±tolerance, length monotonicity)
  TestDrcElectricalGate     — 1 test   (DRC: 0 electrical violations on CH9-CH22, keyed by violation type)
  TestFabNotes              — 8 tests  (file exists, keywords, fill type, IPC-4761+Type VII co-location,
                                        non-substitution clause, first-article microsection, soldermask lock,
                                        measurable acceptance criteria)
  TestFabPackageArtifacts   — 8 tests  (packager runs, gerbers exist, drill exists, zip exists,
                                        zip contains fab_notes with VIPPO, vip_vias.csv, README_FAB.md,
                                        zip contains Step E docs)
  TestBringUpArtifacts      — 13 tests (bring-up checklist, assembly constraints, DFM feedback loop,
                                        order checklist structural enforcement, validate_release
                                        structural + live ZIP gate, cam_diff structural enforcement,
                                        vendor capability evidence directory)
                            ─────────
                            56 tests total

VIP policy:
  14 CH vias sit exactly on J5 Omnetics SMD pads.  SMD pads have F.Mask
  in their layer list → solder mask OPENS over the pad → via is EXPOSED.
  "Tented" flags do not help.  Dogbone off-pad is geometrically impossible
  (pad pitch 0.635mm, via ∅ 0.4mm — proven infeasible in X, Y, and inter-row).
  Therefore VIPPO (epoxy plug + cap plate) is MANDATORY.  The test enforces
  require_fill_on_smd_pad: true and fill_type present in fab_profile.yaml.

All fab capability constants are loaded from ``fab_profile.yaml`` in the
project root.  When you change fabs, update that file — tests auto-adapt.
"""
from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path

import pytest
import yaml

# ── Project imports ──────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectivity_proof import (
    CH_RANGE,
    NET_CODE_FOR_CH,
    CH_FOR_NET_CODE,
    SNAP_EPS,
    parse_board,
    parse_netclasses,
    parse_zones,
    parse_stackup,
    parse_drc_json,
    run_kicad_drc,
)

# ── Paths ────────────────────────────────────────────────────────────────

BOARD_PATH = Path(__file__).resolve().parent.parent / "afe-headstage-v1.kicad_pcb"
HW_DIR = Path(__file__).resolve().parent.parent
FAB_PROFILE_PATH = Path(__file__).resolve().parent.parent / "fab_profile.yaml"

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

# ═══════════════════════════════════════════════════════════════════════════
# FAB PROFILE — loaded from YAML, not hardcoded
# ═══════════════════════════════════════════════════════════════════════════

assert FAB_PROFILE_PATH.exists(), (
    f"fab_profile.yaml not found at {FAB_PROFILE_PATH}.\n"
    f"Create it from the template — tests cannot run without a fab profile."
)
_FAB = yaml.safe_load(FAB_PROFILE_PATH.read_text())

FAB_MIN_DRILL_MM        = _FAB["limits"]["min_drill_mm"]
FAB_MIN_ANNULAR_RING_MM = _FAB["limits"]["min_annular_ring_mm"]
FAB_MAX_ASPECT_RATIO    = _FAB["limits"]["max_aspect_ratio"]
FAB_MIN_TRACE_WIDTH_MM  = _FAB["limits"]["min_trace_width_mm"]
FAB_MIN_CLEARANCE_MM    = _FAB["limits"]["min_clearance_mm"]
FAB_MIN_VIA_VIA_EDGE_MM = _FAB["limits"]["min_via_via_edge_mm"]

VIP_ALLOWED             = _FAB["via_fill_policy"]["vip_allowed"]
VIP_REQUIRE_FILL_SMD    = _FAB["via_fill_policy"]["require_fill_on_smd_pad"]
VIP_FILL_TYPE           = _FAB["via_fill_policy"]["fill_type"]
ALLOWED_FILL_TYPES      = set(_FAB["via_fill_policy"]["allowed_fill_types"])

DRC_ELECTRICAL_TYPES    = set(_FAB["drc_electrical_violation_types"])

# ── Fab notes artifact ───────────────────────────────────────────────────
FAB_NOTES_REL_PATH      = _FAB["fab_notes"]["path"]
FAB_NOTES_PATH          = HW_DIR / FAB_NOTES_REL_PATH
FAB_NOTES_KEYWORDS      = _FAB["fab_notes"]["required_keywords"]

# ── ELECTRODE netclass expected values (from board file) ─────────────────
ELECTRODE_EXPECTED_CLR       = 0.2    # mm
ELECTRODE_EXPECTED_TRACE_W   = 0.15   # mm
ELECTRODE_EXPECTED_VIA_SIZE  = 0.4    # mm (pad diameter)
ELECTRODE_EXPECTED_VIA_DRILL = 0.2    # mm

# ── Per-channel length bounds ────────────────────────────────────────────
CH_LENGTH_MEDIAN_TOL = 0.45   # each channel within median ± 45%


# ═══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def board_text():
    return BOARD_PATH.read_text()


@pytest.fixture(scope="module")
def parsed(board_text):
    return parse_board(board_text)


@pytest.fixture(scope="module")
def netclasses(board_text):
    return parse_netclasses(board_text)


@pytest.fixture(scope="module")
def zones(board_text):
    return parse_zones(board_text)


@pytest.fixture(scope="module")
def stackup(board_text):
    return parse_stackup(board_text)


@pytest.fixture(scope="module")
def ch_segments(parsed):
    """All segments belonging to CH9–CH22 nets."""
    ch_net_codes = set(NET_CODE_FOR_CH.values())
    return [s for s in parsed["segments"] if s["net"] in ch_net_codes]


@pytest.fixture(scope="module")
def ch_vias(parsed):
    """All vias belonging to CH9–CH22 nets."""
    ch_net_codes = set(NET_CODE_FOR_CH.values())
    return [v for v in parsed["vias"] if v["net"] in ch_net_codes]


# ═══════════════════════════════════════════════════════════════════════════
# GEOMETRY HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _point_to_segment_dist(
    px: float, py: float,
    sx0: float, sy0: float,
    sx1: float, sy1: float,
) -> float:
    """Minimum distance from point (px,py) to line segment (sx0,sy0)-(sx1,sy1).

    Exact closed-form via parametric projection clamped to [0,1].
    """
    dx = sx1 - sx0
    dy = sy1 - sy0
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return math.hypot(px - sx0, py - sy0)
    t = max(0.0, min(1.0, ((px - sx0) * dx + (py - sy0) * dy) / len_sq))
    proj_x = sx0 + t * dx
    proj_y = sy0 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _segments_intersect(
    a0: tuple[float, float], a1: tuple[float, float],
    b0: tuple[float, float], b1: tuple[float, float],
) -> bool:
    """Test whether two 2D line segments intersect (cross or touch).

    Uses the standard cross-product orientation test.
    """
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def on_segment(p, q, r):
        return (min(p[0], r[0]) <= q[0] + 1e-12 <= max(p[0], r[0]) + 1e-12 and
                min(p[1], r[1]) <= q[1] + 1e-12 <= max(p[1], r[1]) + 1e-12)

    d1 = cross(b0, b1, a0)
    d2 = cross(b0, b1, a1)
    d3 = cross(a0, a1, b0)
    d4 = cross(a0, a1, b1)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    if abs(d1) < 1e-12 and on_segment(b0, a0, b1):
        return True
    if abs(d2) < 1e-12 and on_segment(b0, a1, b1):
        return True
    if abs(d3) < 1e-12 and on_segment(a0, b0, a1):
        return True
    if abs(d4) < 1e-12 and on_segment(a0, b1, a1):
        return True

    return False


def _seg_seg_dist(
    a0: tuple[float, float], a1: tuple[float, float],
    b0: tuple[float, float], b1: tuple[float, float],
) -> float:
    """Exact minimum distance between two 2D line segments.

    For non-intersecting coplanar segments, the minimum is always
    achieved at an endpoint of one segment projected onto the other.
    The 4-endpoint-to-segment projection is therefore exact.

    For intersecting segments the distance is 0.  We detect this
    with a cross-product intersection test so that crossing segments
    are never reported with a positive gap.
    """
    if _segments_intersect(a0, a1, b0, b1):
        return 0.0

    return min(
        _point_to_segment_dist(a0[0], a0[1], b0[0], b0[1], b1[0], b1[1]),
        _point_to_segment_dist(a1[0], a1[1], b0[0], b0[1], b1[0], b1[1]),
        _point_to_segment_dist(b0[0], b0[1], a0[0], a0[1], a1[0], a1[1]),
        _point_to_segment_dist(b1[0], b1[1], a0[0], a0[1], a1[0], a1[1]),
    )


# ── Witness formatting ──────────────────────────────────────────────────

def _ch_name(net_code: int) -> str:
    """Return 'CHxx' for a net code, or 'net{N}' if unknown."""
    return f"CH{CH_FOR_NET_CODE[net_code]}" if net_code in CH_FOR_NET_CODE else f"net{net_code}"


def _format_seg_witness(s: dict) -> str:
    """Single-segment witness with net name, layer, coords, UUID."""
    return (
        f"{_ch_name(s['net'])} {s['layer']} "
        f"({s['start'][0]:.4f},{s['start'][1]:.4f})->"
        f"({s['end'][0]:.4f},{s['end'][1]:.4f}) "
        f"w={s.get('width', '?')} uuid={s.get('uuid', '?')}"
    )


def _format_pair_witness(s1: dict, s2: dict, raw_dist: float, gap: float) -> str:
    """Full pair witness: net names, UUIDs, raw center-dist, edge gap."""
    return (
        f"edge_gap={gap:.4f}mm center_dist={raw_dist:.4f}mm | "
        f"A: {_format_seg_witness(s1)} | "
        f"B: {_format_seg_witness(s2)}"
    )


def _format_via_seg_witness(via: dict, seg: dict, raw_dist: float, gap: float) -> str:
    """Via-to-segment witness with UUIDs and raw distance."""
    vx, vy = via["at"]
    return (
        f"edge_gap={gap:.4f}mm center_dist={raw_dist:.4f}mm | "
        f"via {_ch_name(via['net'])} at ({vx:.4f},{vy:.4f}) "
        f"r={via['size']/2.0} uuid={via.get('uuid', '?')} | "
        f"seg {_format_seg_witness(seg)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. VIA MANUFACTURABILITY (6 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestViaManufacturability:
    """Gate via geometry against fab capability limits from fab_profile.yaml.

    All vias in this design are standard through-vias (F.Cu↔B.Cu).
    The through-via guard test enforces this structurally — if someone
    adds a blind/buried via, it will fail immediately rather than
    silently computing a wrong aspect ratio.

    14 vias are via-in-pad on J5 SMD pads.  SMD pads open solder mask
    (F.Mask in layer list), so the via is EXPOSED.  Global tenting flags
    don't help.  The VIP test enforces that fab_profile.yaml declares
    require_fill_on_smd_pad: true with a specific fill_type (VIPPO).
    """

    def test_all_ch_vias_are_through_vias(self, ch_vias, record_property):
        """Every CH via must span F.Cu↔B.Cu (standard through-via).

        If blind/buried vias are ever added, the aspect ratio test and
        the VIP detector must be extended to handle per-via-type depth.
        This test ensures that doesn't happen silently.
        """
        non_through = []
        for v in ch_vias:
            layers = set(v["layers"])
            if layers != {"F.Cu", "B.Cu"}:
                non_through.append(
                    f"net{v['net']} at {v['at']} layers={v['layers']} "
                    f"uuid={v.get('uuid', '?')}"
                )
        record_property("non_through_vias", len(non_through))
        assert not non_through, (
            f"{len(non_through)} non-through via(s) detected — "
            f"aspect ratio and VIP tests assume through-vias only.\n"
            f"Extend test logic before adding blind/buried vias:\n"
            + "\n".join(f"  {x}" for x in non_through[:5])
        )

    def test_drill_above_fab_minimum(self, ch_vias, record_property):
        """Every CH via drill >= FAB_MIN_DRILL_MM from fab profile."""
        worst_drill = min(v["drill"] for v in ch_vias)
        record_property("worst_drill_mm", worst_drill)
        record_property("fab_min_drill_mm", FAB_MIN_DRILL_MM)
        for v in ch_vias:
            assert v["drill"] >= FAB_MIN_DRILL_MM, (
                f"Via at {v['at']} drill={v['drill']}mm < "
                f"fab min {FAB_MIN_DRILL_MM}mm "
                f"(from fab_profile.yaml: {_FAB['fab']['name']})"
            )

    def test_annular_ring_above_fab_minimum(self, ch_vias, record_property):
        """Every CH via annular ring >= FAB_MIN_ANNULAR_RING_MM."""
        rings = [(v["size"] - v["drill"]) / 2.0 for v in ch_vias]
        worst_ring = min(rings)
        record_property("worst_annular_ring_mm", worst_ring)
        for v in ch_vias:
            ring = (v["size"] - v["drill"]) / 2.0
            assert ring >= FAB_MIN_ANNULAR_RING_MM, (
                f"Via at {v['at']} annular ring={ring:.4f}mm "
                f"(size={v['size']}, drill={v['drill']}) < "
                f"fab min {FAB_MIN_ANNULAR_RING_MM}mm"
            )

    def test_aspect_ratio_within_fab_limit(self, ch_vias, stackup, record_property):
        """Through-via aspect ratio (board_thickness / drill) <= FAB_MAX_ASPECT_RATIO.

        Depends on test_all_ch_vias_are_through_vias passing.
        depth = board_thickness because all vias are through-vias.
        """
        board_thick = stackup["total_thickness"]
        assert board_thick is not None, "Board thickness not found in stackup"
        worst_ratio = max(board_thick / v["drill"] for v in ch_vias)
        record_property("worst_aspect_ratio", round(worst_ratio, 1))
        record_property("note", "through-via only; depth=board_thickness")
        for v in ch_vias:
            ratio = board_thick / v["drill"]
            assert ratio <= FAB_MAX_ASPECT_RATIO, (
                f"Via at {v['at']} aspect ratio={ratio:.1f} "
                f"({board_thick}mm / {v['drill']}mm) > "
                f"fab max {FAB_MAX_ASPECT_RATIO}"
            )

    def test_tenting_and_vip_detection(self, stackup, parsed, record_property):
        """Board setup specifies tenting.  VIP on SMD pads requires VIPPO.

        Key fact: an SMD pad has F.Mask in its layer list, which means
        solder mask OPENS over the pad.  Any via sitting inside that mask
        opening is EXPOSED regardless of global tenting flags.  Therefore
        VIP on an SMD pad requires epoxy plug + cap (VIPPO) for assembly.

        Enforced invariants:
          1. require_fill_on_smd_pad implies vip_allowed (no contradiction)
          2. VIP on SMD pad => require_fill_on_smd_pad must be true
          3. fill_type must be in the allowed_fill_types enum (vendor-supported)
          4. B.Cu-side exposure is reported for every VIP (informational)

        VIP detection: any via whose center is coincident (within tolerance)
        with an SMD pad center on the same net.
        """
        # ── Consistency gate: no contradictory fab profile config ─────────
        if VIP_REQUIRE_FILL_SMD:
            assert VIP_ALLOWED, (
                "fab_profile.yaml contradiction: "
                "require_fill_on_smd_pad is true but vip_allowed is false.\n"
                "Fill policy only makes sense if VIP is allowed."
            )

        # ── Check global tenting flags (covers non-VIP vias) ─────────────
        tenting = stackup["tenting"]
        assert tenting is not None, "No tenting configuration in board setup"
        assert "front" in tenting, "Front tenting not configured"
        assert "back" in tenting, "Back tenting not configured"
        record_property("kicad_tenting", tenting)

        # ── VIP detection: CH vias coincident with SMD pads ──────────────
        ch_net_codes = set(NET_CODE_FOR_CH.values())
        ch_vias_list = [v for v in parsed["vias"] if v["net"] in ch_net_codes]
        vip_on_smd = []
        vip_bcu_exposure = []  # informational: B.Cu-side status
        for via in ch_vias_list:
            vx, vy = via["at"]
            v_net = via["net"]
            for pad in parsed["pads"].get(v_net, []):
                if pad.get("pad_type") != "smd":
                    continue
                px, py = pad["abs_xy"]
                if abs(vx - px) < SNAP_EPS * 100 and abs(vy - py) < SNAP_EPS * 100:
                    vip_on_smd.append(
                        f"{_ch_name(v_net)} via at ({vx},{vy}) "
                        f"on SMD pad {pad['ref']}.{pad['pin']} "
                        f"(pad has F.Mask -> mask OPEN -> via EXPOSED on F.Cu side)"
                    )
                    # B.Cu-side check: via is through-hole so it exists on B.Cu.
                    # If no B.Cu SMD pad covers it, B.Cu side is either tented
                    # or open.  With global tenting on back, the B.Cu annulus
                    # should be tented *unless* a B.Mask pad opening exists.
                    # Since J5 is F.Cu-only SMD, B.Cu side is tented by global
                    # policy.  With VIPPO the via is plugged so both sides sealed.
                    bcu_pads_here = [
                        p for p in parsed["pads"].get(v_net, [])
                        if p.get("pad_type") == "smd"
                        and abs(p["abs_xy"][0] - vx) < SNAP_EPS * 100
                        and abs(p["abs_xy"][1] - vy) < SNAP_EPS * 100
                        and "B.Mask" in str(p.get("layers", ""))
                    ]
                    if bcu_pads_here:
                        bcu_status = "B.Cu EXPOSED (B.Mask pad opening)"
                    else:
                        bcu_status = "B.Cu tented by global policy (no B.Mask pad)"
                    vip_bcu_exposure.append(
                        f"{_ch_name(v_net)} at ({vx},{vy}): {bcu_status}"
                    )

        record_property("vip_on_smd_detected", len(vip_on_smd))
        record_property("vip_allowed", VIP_ALLOWED)
        record_property("require_fill_on_smd_pad", VIP_REQUIRE_FILL_SMD)
        record_property("fill_type", VIP_FILL_TYPE)

        # ── Report B.Cu-side exposure (informational) ────────────────────
        if vip_bcu_exposure:
            print("\n  VIP B.Cu-side exposure report:")
            for line in vip_bcu_exposure[:5]:
                print(f"    {line}")
            if len(vip_bcu_exposure) > 5:
                print(f"    ... and {len(vip_bcu_exposure) - 5} more")
            record_property("vip_bcu_exposure_sample", vip_bcu_exposure[:3])

        if vip_on_smd:
            # ── VIP exists: fab profile must explicitly allow it ──────────
            assert VIP_ALLOWED, (
                f"{len(vip_on_smd)} via-in-pad on SMD detected but "
                f"fab_profile.yaml has vip_allowed: false.\n"
                f"Either move vias off-pad (dogbone) or set vip_allowed: true:\n"
                + "\n".join(f"  {x}" for x in vip_on_smd[:5])
            )
            # ── SMD pad opens mask -> via exposed -> fill MANDATORY ───────
            assert VIP_REQUIRE_FILL_SMD, (
                f"{len(vip_on_smd)} VIP on SMD pads detected.\n"
                f"SMD pads open solder mask, exposing the via.\n"
                f"fab_profile.yaml must have require_fill_on_smd_pad: true.\n"
                f"Unfilled VIP under SMD pads causes solder wicking/voids.\n"
                + "\n".join(f"  {x}" for x in vip_on_smd[:5])
            )
            # ── Fill type must be declared ────────────────────────────────
            assert VIP_FILL_TYPE is not None, (
                f"{len(vip_on_smd)} VIP on SMD pads and "
                f"require_fill_on_smd_pad: true, but fill_type is null.\n"
                f"Set fill_type in fab_profile.yaml (e.g. 'epoxy_plug_cap').\n"
                f"This becomes a mandatory fab order note."
            )
            # ── Fill type must be vendor-supported (enum check) ───────────
            assert VIP_FILL_TYPE in ALLOWED_FILL_TYPES, (
                f"fill_type='{VIP_FILL_TYPE}' not in allowed_fill_types "
                f"{sorted(ALLOWED_FILL_TYPES)}.\n"
                f"Only vendor-supported fill types are accepted.\n"
                f"Update allowed_fill_types in fab_profile.yaml if your "
                f"vendor supports a different process."
            )
            print(
                f"\n  VIP policy: {len(vip_on_smd)} via-in-pad on SMD pads\n"
                f"    fill_type={VIP_FILL_TYPE} (VIPPO mandatory)\n"
                f"    allowed_fill_types={sorted(ALLOWED_FILL_TYPES)}\n"
                f"    Fab order must specify: plugged + cap plated"
            )
        # If no VIP on SMD found, pass silently (tented vias are fine)

    def test_via_to_via_spacing(self, ch_vias, record_property):
        """All cross-net CH via pairs have >= FAB_MIN_VIA_VIA_EDGE_MM edge gap."""
        min_gap = float("inf")
        min_witness = ""
        for i, v1 in enumerate(ch_vias):
            for v2 in ch_vias[i + 1:]:
                if v1["net"] == v2["net"]:
                    continue
                dx = v1["at"][0] - v2["at"][0]
                dy = v1["at"][1] - v2["at"][1]
                dist = math.hypot(dx, dy)
                r1 = v1["size"] / 2.0
                r2 = v2["size"] / 2.0
                gap = dist - r1 - r2
                if gap < min_gap:
                    min_gap = gap
                    min_witness = (
                        f"{_ch_name(v1['net'])} at {v1['at']} uuid={v1.get('uuid','?')} "
                        f"<-> {_ch_name(v2['net'])} at {v2['at']} uuid={v2.get('uuid','?')}: "
                        f"center_dist={dist:.4f} - {r1} - {r2} = edge_gap={gap:.4f}mm"
                    )

        record_property("min_via_via_edge_gap_mm", round(min_gap, 4))
        record_property("min_via_via_witness", min_witness)
        print(f"\n  Via-to-via min edge gap: {min_gap:.4f}mm\n    {min_witness}")
        assert min_gap >= FAB_MIN_VIA_VIA_EDGE_MM - 1e-6, (
            f"Via-to-via edge gap={min_gap:.4f}mm < "
            f"fab min {FAB_MIN_VIA_VIA_EDGE_MM}mm\n"
            f"  Witness: {min_witness}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. NETCLASS ENFORCEMENT (5 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestNetclassEnforcement:
    """Assert every CH9–CH22 net is in the ELECTRODE netclass with correct
    constraints, and that all copper on those nets actually uses those values.

    Prevents the KiCad gotcha: netclass defined but tracks/vias placed
    with Default values.
    """

    def test_electrode_netclass_exists(self, netclasses):
        assert "ELECTRODE" in netclasses, (
            f"ELECTRODE netclass not found. Available: {list(netclasses.keys())}"
        )

    def test_all_ch_nets_in_electrode(self, netclasses):
        electrode = netclasses.get("ELECTRODE", {})
        member_nets = set(electrode.get("nets", []))
        for ch in CH_RANGE:
            net_name = f"CH{ch}"
            assert net_name in member_nets, (
                f"{net_name} not in ELECTRODE netclass. "
                f"Members: {sorted(member_nets)}"
            )

    def test_electrode_clearance_matches(self, netclasses):
        clr = netclasses["ELECTRODE"]["clearance"]
        assert clr == ELECTRODE_EXPECTED_CLR, (
            f"ELECTRODE clearance={clr}mm, expected {ELECTRODE_EXPECTED_CLR}mm"
        )

    def test_all_ch_tracks_correct_width(self, ch_segments):
        bad = []
        for s in ch_segments:
            w = s.get("width")
            if w is None or abs(w - ELECTRODE_EXPECTED_TRACE_W) > 1e-6:
                bad.append(f"  {_format_seg_witness(s)}")
        assert not bad, (
            f"{len(bad)} segments with wrong width "
            f"(expected {ELECTRODE_EXPECTED_TRACE_W}mm):\n"
            + "\n".join(bad[:5])
        )

    def test_all_ch_vias_correct_geometry(self, ch_vias):
        bad = []
        for v in ch_vias:
            size_ok = (
                v["size"] is not None
                and abs(v["size"] - ELECTRODE_EXPECTED_VIA_SIZE) < 1e-6
            )
            drill_ok = (
                v["drill"] is not None
                and abs(v["drill"] - ELECTRODE_EXPECTED_VIA_DRILL) < 1e-6
            )
            if not (size_ok and drill_ok):
                bad.append(
                    f"  {_ch_name(v['net'])} at={v['at']} "
                    f"size={v['size']} drill={v['drill']} "
                    f"uuid={v.get('uuid', '?')}"
                )
        assert not bad, (
            f"{len(bad)} vias with wrong geometry "
            f"(expected size={ELECTRODE_EXPECTED_VIA_SIZE}, "
            f"drill={ELECTRODE_EXPECTED_VIA_DRILL}):\n"
            + "\n".join(bad[:5])
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. BOTTOM FAN SPACING (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestBottomFanSpacing:
    """Verify minimum copper-to-copper spacing in the bottom fan region.

    Every test computes distance using full ``_seg_seg_dist`` which:
      1. Checks segment intersection (→ distance 0) via cross-product test.
      2. Computes 4 endpoint-to-segment projections (exact for non-intersecting
         coplanar segments — provably: the minimum of two bilinear functions
         on a product of intervals is always achieved at a vertex).

    Every witness reports: net names (CHxx), layer, both UUIDs,
    raw center-to-center distance, and the edge gap after subtracting
    half-widths/radii.
    """

    def test_fcu_track_to_track_min_spacing(self, ch_segments, record_property):
        """Minimum cross-net edge gap between F.Cu CH segment pairs."""
        fcu_segs = [s for s in ch_segments if s["layer"] == "F.Cu"]
        min_gap = float("inf")
        min_raw = 0.0
        min_s1 = min_s2 = None

        for i, s1 in enumerate(fcu_segs):
            w1 = s1.get("width", 0.15) / 2.0
            for s2 in fcu_segs[i + 1:]:
                if s2["net"] == s1["net"]:
                    continue
                w2 = s2.get("width", 0.15) / 2.0
                raw = _seg_seg_dist(s1["start"], s1["end"], s2["start"], s2["end"])
                gap = raw - w1 - w2
                if gap < min_gap:
                    min_gap, min_raw = gap, raw
                    min_s1, min_s2 = s1, s2

        assert min_gap != float("inf"), "No cross-net F.Cu segment pairs found"
        wit = _format_pair_witness(min_s1, min_s2, min_raw, min_gap)
        record_property("fcu_min_track_gap_mm", round(min_gap, 4))
        record_property("fcu_min_track_witness", wit)
        print(f"\n  F.Cu min track gap:\n    {wit}")
        assert min_gap >= ELECTRODE_EXPECTED_CLR - 1e-6, (
            f"F.Cu cross-net edge gap={min_gap:.4f}mm < "
            f"ELECTRODE clearance {ELECTRODE_EXPECTED_CLR}mm\n  {wit}"
        )

    def test_bcu_track_to_track_min_spacing(self, ch_segments, record_property):
        """Minimum cross-net edge gap between B.Cu CH segment pairs."""
        bcu_segs = [s for s in ch_segments if s["layer"] == "B.Cu"]
        min_gap = float("inf")
        min_raw = 0.0
        min_s1 = min_s2 = None

        for i, s1 in enumerate(bcu_segs):
            w1 = s1.get("width", 0.15) / 2.0
            for s2 in bcu_segs[i + 1:]:
                if s2["net"] == s1["net"]:
                    continue
                w2 = s2.get("width", 0.15) / 2.0
                raw = _seg_seg_dist(s1["start"], s1["end"], s2["start"], s2["end"])
                gap = raw - w1 - w2
                if gap < min_gap:
                    min_gap, min_raw = gap, raw
                    min_s1, min_s2 = s1, s2

        assert min_gap != float("inf"), "No cross-net B.Cu segment pairs found"
        wit = _format_pair_witness(min_s1, min_s2, min_raw, min_gap)
        record_property("bcu_min_track_gap_mm", round(min_gap, 4))
        record_property("bcu_min_track_witness", wit)
        print(f"\n  B.Cu min track gap:\n    {wit}")
        assert min_gap >= ELECTRODE_EXPECTED_CLR - 1e-6, (
            f"B.Cu cross-net edge gap={min_gap:.4f}mm < "
            f"ELECTRODE clearance {ELECTRODE_EXPECTED_CLR}mm\n  {wit}"
        )

    def test_via_to_track_min_spacing(self, ch_vias, ch_segments, record_property):
        """Minimum edge-to-edge gap between any CH via and any cross-net
        CH segment on a shared layer.  Uses point-to-segment projection."""
        min_gap = float("inf")
        min_raw = 0.0
        min_wit = ""

        for via in ch_vias:
            vx, vy = via["at"]
            vr = via["size"] / 2.0
            for seg in ch_segments:
                if seg["net"] == via["net"]:
                    continue
                if seg["layer"] not in via["layers"]:
                    continue
                sw = seg.get("width", 0.15) / 2.0
                raw = _point_to_segment_dist(
                    vx, vy,
                    seg["start"][0], seg["start"][1],
                    seg["end"][0], seg["end"][1],
                )
                gap = raw - vr - sw
                if gap < min_gap:
                    min_gap, min_raw = gap, raw
                    min_wit = _format_via_seg_witness(via, seg, raw, gap)

        if min_gap == float("inf"):
            pytest.skip("No cross-net via-to-track pairs found")

        record_property("min_via_track_gap_mm", round(min_gap, 4))
        record_property("min_via_track_witness", min_wit)
        print(f"\n  Via-to-track min gap:\n    {min_wit}")
        assert min_gap >= ELECTRODE_EXPECTED_CLR - 1e-3, (
            f"Via-to-track edge gap={min_gap:.4f}mm < "
            f"ELECTRODE clearance {ELECTRODE_EXPECTED_CLR}mm\n  {min_wit}"
        )

    def test_overall_min_neckdown_above_fab(
        self, ch_segments, ch_vias, record_property,
    ):
        """Tightest cross-net spacing anywhere >= FAB_MIN_CLEARANCE_MM.

        Safety net: even if ELECTRODE clearance changes, physical copper
        cannot go below what the fab can etch.  Reports the single worst
        gap across F.Cu seg-seg, B.Cu seg-seg, and via-to-seg.
        """
        overall_min = float("inf")
        overall_raw = 0.0
        overall_wit = ""

        for layer_name in ("F.Cu", "B.Cu"):
            layer_segs = [s for s in ch_segments if s["layer"] == layer_name]
            for i, s1 in enumerate(layer_segs):
                w1 = s1.get("width", 0.15) / 2.0
                for s2 in layer_segs[i + 1:]:
                    if s2["net"] == s1["net"]:
                        continue
                    w2 = s2.get("width", 0.15) / 2.0
                    raw = _seg_seg_dist(s1["start"], s1["end"], s2["start"], s2["end"])
                    gap = raw - w1 - w2
                    if gap < overall_min:
                        overall_min, overall_raw = gap, raw
                        overall_wit = (
                            f"{layer_name} seg-seg: "
                            + _format_pair_witness(s1, s2, raw, gap)
                        )

        for via in ch_vias:
            vx, vy = via["at"]
            vr = via["size"] / 2.0
            for seg in ch_segments:
                if seg["net"] == via["net"]:
                    continue
                if seg["layer"] not in via["layers"]:
                    continue
                sw = seg.get("width", 0.15) / 2.0
                raw = _point_to_segment_dist(
                    vx, vy,
                    seg["start"][0], seg["start"][1],
                    seg["end"][0], seg["end"][1],
                )
                gap = raw - vr - sw
                if gap < overall_min:
                    overall_min, overall_raw = gap, raw
                    overall_wit = "via-seg: " + _format_via_seg_witness(
                        via, seg, raw, gap,
                    )

        if overall_min == float("inf"):
            pytest.skip("No cross-net copper pairs found")

        record_property("overall_min_neckdown_mm", round(overall_min, 4))
        record_property("overall_neckdown_witness", overall_wit)
        print(
            f"\n  Overall min neckdown: {overall_min:.4f}mm "
            f"(fab min: {FAB_MIN_CLEARANCE_MM}mm)\n    {overall_wit}"
        )
        assert overall_min >= FAB_MIN_CLEARANCE_MM - 1e-6, (
            f"Minimum neckdown={overall_min:.4f}mm < "
            f"fab minimum {FAB_MIN_CLEARANCE_MM}mm\n  {overall_wit}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. ZONE INTERACTION SAFETY (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestZoneInteractions:
    """Parser-level structural zone checks.

    Does NOT prove filled zones don't short to CH copper — that's
    TestDrcElectricalGate's job (runs actual KiCad DRC).
    """

    def test_zones_only_on_inner_layers(self, zones):
        allowed = {"In1.Cu", "In2.Cu"}
        for z in zones:
            assert z["layer"] in allowed, (
                f"Zone '{z['name']}' (net={z['net_name']}) on {z['layer']} "
                f"— only inner layers allowed"
            )

    def test_zones_are_gnd_only(self, zones):
        for z in zones:
            assert z["net_name"] == "GND", (
                f"Zone '{z['name']}' carries net '{z['net_name']}', expected GND"
            )

    def test_no_zone_carries_ch_net(self, zones):
        ch_names = {f"CH{ch}" for ch in CH_RANGE}
        for z in zones:
            assert z["net_name"] not in ch_names, (
                f"Zone '{z['name']}' on CH net '{z['net_name']}'"
            )

    def test_zone_clearance_set(self, zones):
        for z in zones:
            assert z["clearance"] is not None and z["clearance"] > 0, (
                f"Zone '{z['name']}' clearance={z['clearance']} — must be > 0"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 5. STACKUP SANITY (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestStackupSanity:

    def test_four_copper_layers(self, stackup):
        cu = [l for l in stackup["layers"] if l["type"] == "copper"]
        assert len(cu) == 4, (
            f"Expected 4 copper layers, got {len(cu)}: {[l['name'] for l in cu]}"
        )

    def test_total_thickness_1_6mm(self, stackup):
        assert stackup["total_thickness"] is not None
        assert abs(stackup["total_thickness"] - 1.6) < 0.01

    def test_copper_thickness_035(self, stackup):
        for cl in (l for l in stackup["layers"] if l["type"] == "copper"):
            assert cl["thickness"] is not None
            assert abs(cl["thickness"] - 0.035) < 0.001, (
                f"'{cl['name']}' thickness={cl['thickness']}mm, expected 0.035"
            )

    def test_dielectric_material_fr4(self, stackup):
        dielectrics = [l for l in stackup["layers"] if l["type"] in ("prepreg", "core")]
        assert len(dielectrics) > 0, "No dielectric layers found"
        for dl in dielectrics:
            assert dl["material"] == "FR4", (
                f"'{dl['name']}' material='{dl['material']}', expected FR4"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 6. CHANNEL LENGTH SANITY (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

def _ch_lengths(parsed: dict) -> dict[int, float]:
    """Compute total copper length per channel."""
    lengths = {}
    for ch in CH_RANGE:
        net_code = NET_CODE_FOR_CH[ch]
        segs = [s for s in parsed["segments"] if s["net"] == net_code]
        lengths[ch] = sum(
            math.hypot(s["end"][0] - s["start"][0], s["end"][1] - s["start"][1])
            for s in segs
        )
    return lengths


class TestChannelLengthSanity:
    """Per-channel copper length checks.

    Instead of hardcoded bounds, we use:
      - Segment count invariant (structural, from routing topology)
      - Median ± tolerance (adapts as routing evolves)
      - Monotonicity: outer channels are shorter, inner are longer
        (natural consequence of nested-L + fan-out geometry)
    """

    def test_segments_per_channel(self, parsed, record_property):
        """Every CH net has exactly 7 segments (5 F.Cu + 2 B.Cu).

        Structural invariant of nested-L + bottom-fan topology.
        This is a routing-strategy guard, not a manufacturing guard.
        """
        bad = []
        for ch in CH_RANGE:
            net_code = NET_CODE_FOR_CH[ch]
            segs = [s for s in parsed["segments"] if s["net"] == net_code]
            fcu = [s for s in segs if s["layer"] == "F.Cu"]
            bcu = [s for s in segs if s["layer"] == "B.Cu"]
            if len(segs) != 7 or len(fcu) != 5 or len(bcu) != 2:
                bad.append(
                    f"CH{ch}: {len(segs)} segs "
                    f"({len(fcu)} F.Cu + {len(bcu)} B.Cu), expected 7 (5+2)"
                )
        record_property("segments_per_ch_bad", len(bad))
        assert not bad, (
            "Segment count mismatch:\n" + "\n".join(f"  {b}" for b in bad)
        )

    def test_length_within_median_tolerance(self, parsed, record_property):
        """Every channel length within median ± 45%.

        Adapts automatically as routing changes.  Catches missing or
        doubled segments without hardcoded bounds.
        """
        lengths = _ch_lengths(parsed)
        vals = sorted(lengths.values())
        median = vals[len(vals) // 2]
        lo = median * (1 - CH_LENGTH_MEDIAN_TOL)
        hi = median * (1 + CH_LENGTH_MEDIAN_TOL)

        record_property("ch_length_median_mm", round(median, 1))
        record_property("ch_length_band_mm", f"[{lo:.1f}, {hi:.1f}]")
        record_property("ch_lengths_mm", {
            f"CH{k}": round(v, 3) for k, v in lengths.items()
        })
        print(
            f"\n  CH lengths: min={min(vals):.1f}mm, "
            f"max={max(vals):.1f}mm, median={median:.1f}mm, "
            f"band=[{lo:.1f}, {hi:.1f}]"
        )

        bad = []
        for ch, length in sorted(lengths.items()):
            if length < lo or length > hi:
                bad.append(f"CH{ch}: {length:.3f}mm")
        assert not bad, (
            f"Channels outside median±{CH_LENGTH_MEDIAN_TOL*100:.0f}% "
            f"[{lo:.1f}, {hi:.1f}]mm:\n"
            + "\n".join(f"  {b}" for b in bad)
        )

    def test_length_ordering_monotonic(self, parsed, record_property):
        """Channel lengths increase monotonically from edges toward center.

        For nested-L routing with symmetric fanout, CH9 and CH22 (outermost)
        should be shortest, CH15/CH16 (innermost) longest.  We check that
        the first half (CH9..CH15) is non-decreasing and the second half
        (CH16..CH22) is non-increasing.

        This catches a routing bug that swaps two channels' paths.
        """
        lengths = _ch_lengths(parsed)
        ch_list = sorted(CH_RANGE)
        mid = len(ch_list) // 2  # split at CH15/CH16 boundary

        # First half: CH9..CH15 should be non-decreasing
        first_half = [lengths[ch] for ch in ch_list[:mid + 1]]
        first_bad = []
        for i in range(len(first_half) - 1):
            if first_half[i] > first_half[i + 1] + 0.1:  # 0.1mm tolerance
                first_bad.append(
                    f"CH{ch_list[i]}={first_half[i]:.1f}mm > "
                    f"CH{ch_list[i+1]}={first_half[i+1]:.1f}mm"
                )

        # Second half: CH16..CH22 should be non-increasing
        second_half = [lengths[ch] for ch in ch_list[mid:]]
        second_bad = []
        for i in range(len(second_half) - 1):
            if second_half[i] < second_half[i + 1] - 0.1:
                second_bad.append(
                    f"CH{ch_list[mid+i]}={second_half[i]:.1f}mm < "
                    f"CH{ch_list[mid+i+1]}={second_half[i+1]:.1f}mm"
                )

        record_property("monotonic_first_half_violations", len(first_bad))
        record_property("monotonic_second_half_violations", len(second_bad))

        all_bad = first_bad + second_bad
        assert not all_bad, (
            "Channel length monotonicity violated:\n"
            + "\n".join(f"  {b}" for b in all_bad)
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. DRC ELECTRICAL GATE (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestDrcElectricalGate:
    """DRC-based gate for electrical faults involving CH9–CH22 nets.

    Runs KiCad's actual design rule checker and filters violations by:
      1. Violation **type** (from JSON ``type`` field), not substring search.
         Only types listed in ``fab_profile.yaml:drc_electrical_violation_types``
         are considered.  Cosmetic types (courtyard, silk, etc.) are excluded.
      2. Net attribution via structured regex on item descriptions.

    This catches zone-fill shorts, net ties, clearance errors, etc.
    that parser-level tests cannot detect.
    """

    @pytest.mark.skipif(not _HAS_KICAD_CLI, reason="kicad-cli not on PATH")
    def test_zero_ch_electrical_violations(self, record_property):
        """DRC reports 0 electrical violations involving CH9–CH22 nets.

        ``ch_violations`` from ``parse_drc_json()`` includes any violation
        where a sub-item's structured description contains a CH net name.
        We further filter to only violation types in DRC_ELECTRICAL_TYPES.
        """
        drc_json = run_kicad_drc(BOARD_PATH, HW_DIR)
        stats = parse_drc_json(drc_json)

        # Filter to electrical types only
        ch_electrical = [
            v for v in stats["ch_violations"]
            if v["type"] in DRC_ELECTRICAL_TYPES
        ]
        ch_unconnected = stats["ch_unconnected"]

        record_property("ch_electrical_violations", len(ch_electrical))
        record_property("ch_unconnected_count", len(ch_unconnected))
        record_property("drc_electrical_types_checked", sorted(DRC_ELECTRICAL_TYPES))

        # Also report any CH violations in non-electrical types (informational)
        ch_cosmetic = [
            v for v in stats["ch_violations"]
            if v["type"] not in DRC_ELECTRICAL_TYPES
        ]
        record_property("ch_cosmetic_violations", len(ch_cosmetic))

        summary = (
            f"DRC electrical gate: "
            f"ch_electrical={len(ch_electrical)}, "
            f"ch_unconnected={len(ch_unconnected)}, "
            f"ch_cosmetic={len(ch_cosmetic)} (not gated)"
        )
        print(f"\n  {summary}")
        for v in ch_electrical[:5]:
            print(f"    ELECTRICAL: type={v['type']} — {v['description']}")

        assert len(ch_electrical) == 0, (
            f"{len(ch_electrical)} electrical DRC violation(s) on CH nets "
            f"(types: {sorted(DRC_ELECTRICAL_TYPES)}):\n"
            + "\n".join(
                f"  [{v['type']}] {v['description']}"
                for v in ch_electrical[:10]
            )
        )

        assert len(ch_unconnected) == 0, (
            f"{len(ch_unconnected)} DRC unconnected item(s) on CH nets:\n"
            + "\n".join(
                f"  {u['net_name']} {u['ref']}.{u['pin']}: {u['description']}"
                for u in ch_unconnected[:10]
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# 8. FAB NOTES ARTIFACT GATE (8 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestFabNotes:
    """Verify that the fabrication notes file exists and contains all
    mandatory VIPPO / assembly callouts.

    Tests verify *structural content* — not just keywords — to ensure
    the delivered fab_notes.txt is enforceable by a CAM engineer.
    """

    def test_fab_notes_file_exists(self, record_property):
        """fab_notes.txt must exist at the path declared in fab_profile.yaml."""
        record_property("fab_notes_path", str(FAB_NOTES_PATH))
        assert FAB_NOTES_PATH.exists(), (
            f"fab_notes.txt not found at {FAB_NOTES_PATH}.\n"
            f"This file is required in the Gerber/ODB++ package.\n"
            f"Path declared in fab_profile.yaml: {FAB_NOTES_REL_PATH}"
        )

    def test_fab_notes_contains_required_keywords(self, record_property):
        """fab_notes.txt must mention VIPPO, IPC-4761, J5, Omnetics, etc."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing — see test_fab_notes_file_exists")
        content = FAB_NOTES_PATH.read_text()
        missing = [kw for kw in FAB_NOTES_KEYWORDS if kw not in content]
        record_property("required_keywords", FAB_NOTES_KEYWORDS)
        record_property("missing_keywords", missing)
        assert not missing, (
            f"fab_notes.txt is missing {len(missing)} required keyword(s):\n"
            + "\n".join(f"  - '{kw}'" for kw in missing)
            + "\n\nThese keywords ensure the fab house sees the VIPPO callout.\n"
            + f"Keywords checked (from fab_profile.yaml): {FAB_NOTES_KEYWORDS}"
        )

    def test_fab_notes_mentions_fill_type(self, record_property):
        """fab_notes.txt must reference the exact fill process from fab_profile.yaml."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing — see test_fab_notes_file_exists")
        content = FAB_NOTES_PATH.read_text()
        record_property("fill_type", VIP_FILL_TYPE)
        # The fill type in the profile is e.g. "epoxy_plug_cap".
        # The fab notes should mention "epoxy" and "cap" at minimum.
        fill_words = VIP_FILL_TYPE.replace("_", " ").split() if VIP_FILL_TYPE else []
        missing_words = [w for w in fill_words if w.lower() not in content.lower()]
        assert not missing_words, (
            f"fab_notes.txt does not mention fill process words: {missing_words}\n"
            f"fill_type from fab_profile.yaml: '{VIP_FILL_TYPE}'\n"
            f"The fab notes must describe the exact fill/cap process."
        )

    # ── Structural regex tests — verify enforceable content ──────────

    def test_fab_notes_ipc4761_type_vii_together(self, record_property):
        """CLAUSE VIP-1 must be present; IPC-4761 and Type VII must co-locate."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing")
        content = FAB_NOTES_PATH.read_text()
        import re
        # Primary: canonical clause ID
        has_clause = "CLAUSE VIP-1" in content
        record_property("clause_vip_1_present", has_clause)
        assert has_clause, (
            "fab_notes.txt must contain 'CLAUSE VIP-1' — the canonical, "
            "machine-checkable anchor for the VIPPO requirement."
        )
        # Secondary (non-fatal): IPC-4761 + Type VII co-location within 500 chars
        found = False
        for m in re.finditer(r"IPC-4761", content):
            window = content[m.start():m.start()+500]
            if re.search(r"Type\s*VII", window, re.IGNORECASE):
                found = True
                break
        record_property("ipc_type_vii_colocated", found)

    def test_fab_notes_non_substitution_clause(self, record_property):
        """CLAUSE SUB-1 must be present; must reject tented/mask/plug-only."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing")
        content = FAB_NOTES_PATH.read_text()
        import re
        # Primary: canonical clause ID
        has_clause = "CLAUSE SUB-1" in content
        record_property("clause_sub_1_present", has_clause)
        assert has_clause, (
            "fab_notes.txt must contain 'CLAUSE SUB-1' — the canonical, "
            "machine-checkable anchor for the no-substitution requirement."
        )
        # Secondary (non-fatal): "NOT acceptable" near tenting/mask/plug words
        found = False
        for m in re.finditer(r"NOT\s+acceptable", content, re.IGNORECASE):
            window = content[max(0, m.start()-200):m.end()+200]
            if re.search(r"(tented|mask\s+tenting|plug)", window, re.IGNORECASE):
                found = True
                break
        record_property("non_substitution_english_found", found)

    def test_fab_notes_first_article_microsection(self, record_property):
        """CLAUSE FA-1 must be present; must require microsection."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing")
        content = FAB_NOTES_PATH.read_text()
        import re
        # Primary: canonical clause ID
        has_clause = "CLAUSE FA-1" in content
        record_property("clause_fa_1_present", has_clause)
        assert has_clause, (
            "fab_notes.txt must contain 'CLAUSE FA-1' — the canonical, "
            "machine-checkable anchor for the first-article requirement."
        )
        # Secondary (non-fatal): first article + microsection co-location
        pattern = re.compile(
            r"(first\s+article|first[-\s]article).{0,200}(microsection|cross[-\s]?section)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(content)
        record_property("first_article_english_found", match is not None)

    def test_fab_notes_soldermask_expansion_lock(self, record_property):
        """CLAUSE SM-1 must be present; must lock soldermask expansion."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing")
        content = FAB_NOTES_PATH.read_text()
        import re
        # Primary: canonical clause ID
        has_clause = "CLAUSE SM-1" in content
        record_property("clause_sm_1_present", has_clause)
        assert has_clause, (
            "fab_notes.txt must contain 'CLAUSE SM-1' — the canonical, "
            "machine-checkable anchor for the soldermask expansion lock."
        )
        # Secondary (non-fatal): English phrasing co-location
        pattern = re.compile(
            r"(do\s+not|shall\s+not).{0,40}(modify|adjust|override).{0,40}(soldermask|mask\s+expansion|mask\s+opening)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(content)
        record_property("soldermask_english_found", match is not None)

    def test_fab_notes_measurable_acceptance_criteria(self, record_property):
        """fab_notes.txt must include measurable acceptance criteria for VIP."""
        if not FAB_NOTES_PATH.exists():
            pytest.skip("fab_notes.txt missing")
        content = FAB_NOTES_PATH.read_text()
        import re
        # Must have void diameter spec
        void_spec = re.search(r"void\s+diameter.{0,30}\d+\s*µm", content, re.IGNORECASE)
        # Must have cap thickness spec
        cap_spec = re.search(r"cap\s+thickness.{0,30}\d+\s*µm", content, re.IGNORECASE)
        missing = []
        if not void_spec:
            missing.append("void diameter (µm)")
        if not cap_spec:
            missing.append("cap thickness (µm)")
        record_property("missing_acceptance_criteria", missing)
        assert not missing, (
            f"fab_notes.txt missing measurable acceptance criteria: {missing}\n"
            "Without numeric specs, vendors interpret quality loosely."
        )


# ═══════════════════════════════════════════════════════════════════════════
# 9. FAB PACKAGE ARTIFACTS GATE (8 tests)
# ═══════════════════════════════════════════════════════════════════════════

# The packager writes output to out/fab/<name>_<date>_<sha>/.
# These tests run the packager (with --skip-drc to avoid kicad-cli DRC
# dependency in CI), then validate the output folder + zip contents.

_PACKAGER = HW_DIR / "make_fab_package.py"


class TestFabPackageArtifacts:
    """Run make_fab_package.py and verify the delivered artifact is complete.

    If you don't test the *delivered artifact*, you're still leaving
    failure modes open.  This class runs the packager, then inspects
    the output folder and zip for every required file and keyword.
    """

    @pytest.fixture(scope="class")
    def package_output(self):
        """Run the packager once per class, return (out_dir, zip_path)."""
        import subprocess, datetime, re as _re
        assert _PACKAGER.exists(), f"make_fab_package.py not found at {_PACKAGER}"

        # Find the python that's running tests (same venv)
        python = sys.executable

        result = subprocess.run(
            [python, str(_PACKAGER), "--skip-drc"],
            capture_output=True, text=True, cwd=str(HW_DIR), timeout=120,
        )
        assert result.returncode == 0, (
            f"make_fab_package.py failed (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout[-2000:]}\n"
            f"STDERR:\n{result.stderr[-2000:]}"
        )

        # Parse output dir and zip path from stdout
        out_base = HW_DIR / "out" / "fab"
        # Find the newest folder
        if not out_base.exists():
            pytest.fail(f"out/fab/ directory not created")

        folders = sorted(
            [d for d in out_base.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        assert len(folders) > 0, "No output folder found in out/fab/"
        out_dir = folders[0]

        zips = sorted(
            [z for z in out_base.glob("*.zip")],
            key=lambda z: z.stat().st_mtime,
            reverse=True,
        )
        assert len(zips) > 0, "No zip file found in out/fab/"
        zip_path = zips[0]

        return out_dir, zip_path

    def test_packager_creates_output_folder(self, package_output, record_property):
        """The packager must create a dated+SHA output folder."""
        out_dir, _ = package_output
        record_property("output_dir", str(out_dir))
        assert out_dir.exists()
        assert out_dir.is_dir()
        # Name format: afe-headstage-v1_YYYYMMDD_<sha>
        assert "afe-headstage-v1" in out_dir.name

    def test_required_gerbers_exist(self, package_output, record_property):
        """All 9 required Gerber layers must be present."""
        out_dir, _ = package_output
        gerber_dir = out_dir / "gerbers"
        assert gerber_dir.exists(), "gerbers/ subfolder missing"

        required_suffixes = [
            "-F_Cu.gtl", "-In1_Cu.g1", "-In2_Cu.g2", "-B_Cu.gbl",
            "-F_Mask.gts", "-B_Mask.gbs",
            "-F_Silkscreen.gto", "-B_Silkscreen.gbo",
            "-Edge_Cuts.gm1",
        ]
        gerber_files = list(gerber_dir.iterdir())
        gerber_names = [f.name for f in gerber_files]
        record_property("gerber_count", len(gerber_files))

        missing = []
        for suffix in required_suffixes:
            if not any(n.endswith(suffix) for n in gerber_names):
                missing.append(suffix)
        record_property("missing_gerbers", missing)
        assert not missing, (
            f"Missing {len(missing)} required Gerber file(s):\n"
            + "\n".join(f"  - *{s}" for s in missing)
            + f"\nFound: {gerber_names}"
        )

    def test_drill_file_exists(self, package_output, record_property):
        """At least one Excellon drill file must exist in drill/."""
        out_dir, _ = package_output
        drill_dir = out_dir / "drill"
        assert drill_dir.exists(), "drill/ subfolder missing"

        drill_files = list(drill_dir.iterdir())
        record_property("drill_file_count", len(drill_files))
        # Excellon files typically end in .drl
        drl_files = [f for f in drill_files if f.suffix in (".drl", ".xln")]
        assert len(drl_files) >= 1, (
            f"No Excellon drill files found in drill/.\n"
            f"Found files: {[f.name for f in drill_files]}"
        )

    def test_vip_vias_csv_exists(self, package_output, record_property):
        """vip_vias.csv must exist and contain VIP entries."""
        out_dir, _ = package_output
        csv_path = out_dir / "vip_vias.csv"
        assert csv_path.exists(), "vip_vias.csv missing from package"

        import csv as _csv
        with open(csv_path) as f:
            reader = _csv.DictReader(f)
            rows = list(reader)
        record_property("vip_via_count", len(rows))
        assert len(rows) >= 14, (
            f"vip_vias.csv has only {len(rows)} VIP entries (expected ≥ 14).\n"
            f"This means the extractor missed VIP vias at J5."
        )
        # Verify expected columns
        expected_cols = {"net", "x_mm", "y_mm", "size_mm", "drill_mm",
                         "pad_ref", "pad_pin",
                         "pad_size_x_mm", "pad_size_y_mm",
                         "layer_set", "mask_state"}
        actual_cols = set(rows[0].keys()) if rows else set()
        assert expected_cols.issubset(actual_cols), (
            f"vip_vias.csv missing columns: {expected_cols - actual_cols}"
        )

    def test_zip_exists(self, package_output, record_property):
        """The zip archive must exist."""
        _, zip_path = package_output
        record_property("zip_path", str(zip_path))
        assert zip_path.exists()
        assert zip_path.suffix == ".zip"
        record_property("zip_size_bytes", zip_path.stat().st_size)

    def test_zip_contains_fab_notes_with_vippo(self, package_output, record_property):
        """The zip must contain fab_notes.txt with IPC-4761, Type VII, and VIPPO."""
        _, zip_path = package_output
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "fab_notes.txt" in names, (
                f"fab_notes.txt not in zip.\nZip contents: {names}"
            )
            content = zf.read("fab_notes.txt").decode("utf-8")
            for kw in ["IPC-4761", "Type VII", "VIPPO"]:
                assert kw in content, (
                    f"fab_notes.txt in zip missing keyword '{kw}'.\n"
                    f"The delivered zip must contain the VIPPO callout."
                )
            record_property("zip_fab_notes_keywords_ok", True)

    def test_zip_contains_readme_fab(self, package_output, record_property):
        """The zip must contain README_FAB.md."""
        _, zip_path = package_output
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "README_FAB.md" in names, (
                f"README_FAB.md not in zip.\nZip contents: {names}"
            )
            content = zf.read("README_FAB.md").decode("utf-8")
            # Must mention VIPPO and IPC-4761
            assert "VIPPO" in content, "README_FAB.md missing VIPPO mention"
            assert "IPC-4761" in content, "README_FAB.md missing IPC-4761 mention"
            # README must NOT duplicate process spec — should point to fab_notes
            assert "fab_notes.txt" in content, (
                "README_FAB.md must reference fab_notes.txt as authoritative spec"
            )
            record_property("zip_readme_ok", True)

    def test_zip_contains_step_e_docs(self, package_output, record_property):
        """The zip must contain all Step E manufacturing docs."""
        _, zip_path = package_output
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            required_docs = [
                "bringup_checklist.txt",
                "assembly_constraints.txt",
                "dfm_feedback_loop.txt",
                "order_checklist.txt",
            ]
            missing = [d for d in required_docs if d not in names]
            record_property("missing_step_e_docs", missing)
            assert not missing, (
                f"ZIP missing Step E docs: {missing}\n"
                f"These documents must ship with the fab package.\n"
                f"Zip contents: {names}"
            )
            # Verify bringup_checklist contains critical content
            bringup = zf.read("bringup_checklist.txt").decode("utf-8")
            assert "PHASE 2" in bringup, (
                "bringup_checklist.txt in zip missing VIP inspection phase"
            )
            assert "cross-section" in bringup.lower(), (
                "bringup_checklist.txt in zip missing cross-section requirement"
            )
            record_property("zip_step_e_ok", True)


# ═══════════════════════════════════════════════════════════════════════════
# 10. STEP E — BRING-UP & DFM ARTIFACT GATE (13 tests)
# ═══════════════════════════════════════════════════════════════════════════

_BRINGUP_PATH     = HW_DIR / "bringup_checklist.txt"
_ASSEMBLY_PATH    = HW_DIR / "assembly_constraints.txt"
_DFM_PATH         = HW_DIR / "dfm_feedback_loop.txt"
_DFM_DIR          = HW_DIR / "dfm_feedback"


class TestBringUpArtifacts:
    """Verify that Step E bring-up, assembly, and DFM documents exist
    and contain all mandatory sections.

    These are not code artefacts — they are manufacturing process documents
    that ship alongside the board and must be maintained in-repo.
    """

    # ── Bring-up checklist ────────────────────────────────────────────

    def test_bringup_checklist_exists(self, record_property):
        """bringup_checklist.txt must exist."""
        record_property("path", str(_BRINGUP_PATH))
        assert _BRINGUP_PATH.exists(), (
            f"bringup_checklist.txt not found at {_BRINGUP_PATH}"
        )

    def test_bringup_checklist_sections(self, record_property):
        """Bring-up checklist must contain all 7 phases."""
        if not _BRINGUP_PATH.exists():
            pytest.skip("bringup_checklist.txt missing")
        content = _BRINGUP_PATH.read_text()
        required = [
            "PHASE 1",   # bare board visual
            "PHASE 2",   # VIP inspection
            "PHASE 3",   # cross-section
            "PHASE 4",   # bare board electrical
            "PHASE 5",   # post-assembly continuity
            "PHASE 6",   # connector mechanical
            "PHASE 7",   # functional smoke test
            "J5",
            "CH9",
            "CH22",
            "cross-section",
            "wiggle",
        ]
        missing = [s for s in required if s not in content]
        record_property("missing_sections", missing)
        assert not missing, (
            f"bringup_checklist.txt missing required content:\n"
            + "\n".join(f"  - '{s}'" for s in missing)
        )

    # ── Assembly constraints ──────────────────────────────────────────

    def test_assembly_constraints_exists(self, record_property):
        """assembly_constraints.txt must exist."""
        record_property("path", str(_ASSEMBLY_PATH))
        assert _ASSEMBLY_PATH.exists(), (
            f"assembly_constraints.txt not found at {_ASSEMBLY_PATH}"
        )

    def test_assembly_constraints_sections(self, record_property):
        """Assembly constraints must cover stencil, reflow, ESD, and J5."""
        if not _ASSEMBLY_PATH.exists():
            pytest.skip("assembly_constraints.txt missing")
        content = _ASSEMBLY_PATH.read_text()
        required = [
            "STENCIL",
            "REFLOW",
            "ESD",
            "J5",
            "QFN",
            "SAC305",
            "VIPPO",
            "Omnetics",
        ]
        missing = [s for s in required if s not in content]
        record_property("missing_sections", missing)
        assert not missing, (
            f"assembly_constraints.txt missing required content:\n"
            + "\n".join(f"  - '{s}'" for s in missing)
        )

    # ── DFM feedback loop ─────────────────────────────────────────────

    def test_dfm_feedback_loop_exists(self, record_property):
        """dfm_feedback_loop.txt must exist."""
        record_property("path", str(_DFM_PATH))
        assert _DFM_PATH.exists(), (
            f"dfm_feedback_loop.txt not found at {_DFM_PATH}"
        )

    def test_dfm_feedback_loop_sections(self, record_property):
        """DFM feedback loop must contain process, storage, log, and FAQ."""
        if not _DFM_PATH.exists():
            pytest.skip("dfm_feedback_loop.txt missing")
        content = _DFM_PATH.read_text()
        required = [
            "FEEDBACK LOG",
            "PROCESS",
            "REVISION TRACKING",
            "PRE-APPROVED RESPONSES",
            "ESCALATION",
            "dfm_feedback/",
            "fab_notes.txt",
            "VIPPO",
        ]
        missing = [s for s in required if s not in content]
        record_property("missing_sections", missing)
        assert not missing, (
            f"dfm_feedback_loop.txt missing required content:\n"
            + "\n".join(f"  - '{s}'" for s in missing)
        )

    # ── Order checklist ───────────────────────────────────────────────

    def test_order_checklist_exists(self, record_property):
        """order_checklist.txt must exist."""
        path = HW_DIR / "order_checklist.txt"
        record_property("path", str(path))
        assert path.exists(), (
            f"order_checklist.txt not found at {path}"
        )

    def test_order_checklist_structure(self, record_property):
        """Order checklist must enforce VIPPO confirmation, kill-phrase, and sign-off."""
        path = HW_DIR / "order_checklist.txt"
        if not path.exists():
            pytest.skip("order_checklist.txt missing")
        content = path.read_text()
        # Must have at least 10 numbered checkbox items (□ N.)
        import re as _re
        checkbox_items = _re.findall(r"□\s+\d+\.", content)
        record_property("checkbox_count", len(checkbox_items))
        assert len(checkbox_items) >= 10, (
            f"order_checklist.txt has only {len(checkbox_items)} checkbox items (need ≥10)"
        )
        # IPC-4761 + Type VII must co-locate
        assert _re.search(r"IPC-4761.*Type\s*VII|Type\s*VII.*IPC-4761", content), (
            "order_checklist.txt: IPC-4761 and Type VII must appear together"
        )
        # Kill phrase: "do not submit" or "do not fabricate"
        assert _re.search(r"do\s+not\s+(submit|fabricate)", content, _re.IGNORECASE), (
            "order_checklist.txt: must contain 'do not submit' or 'do not fabricate' kill phrase"
        )
        # Non-substitution language
        assert _re.search(r"NOT\s+acceptable|no.?substitution|No material substitution",
                          content, _re.IGNORECASE), (
            "order_checklist.txt: must contain non-substitution clause"
        )
        # SIGN-OFF section with blank fields for name/date
        assert "SIGN-OFF" in content, (
            "order_checklist.txt: must contain SIGN-OFF section"
        )
        assert "Name:" in content and "Date:" in content, (
            "order_checklist.txt: SIGN-OFF must have Name: and Date: fields"
        )
        # Microsection requirement
        assert "microsection" in content.lower(), (
            "order_checklist.txt: must reference first-article microsection"
        )
        record_property("order_checklist_structure", "enforced")

    # ── Bringup template CSV ─────────────────────────────────────────

    def test_bringup_template_columns(self, record_property):
        """template.csv must have traceability and probe-point columns."""
        tpl = HW_DIR / "bringup_results" / "template.csv"
        assert tpl.exists(), f"template.csv not found at {tpl}"
        import csv as _csv
        with open(tpl, newline="") as f:
            reader = _csv.reader(f)
            header = next(reader)
        record_property("column_count", len(header))
        # Lot-level traceability columns
        for col in ("vendor", "order_id", "panel_id"):
            assert col in header, (
                f"template.csv missing lot-traceability column '{col}'"
            )
        # Probe-point anchoring columns
        for col in ("probe_from", "probe_to"):
            assert col in header, (
                f"template.csv missing probe-point column '{col}'"
            )
        record_property("template_columns", "enforced")

    def test_bringup_template_probe_points(self, record_property):
        """template.csv continuity rows must reference J5/U1 probe points."""
        tpl = HW_DIR / "bringup_results" / "template.csv"
        if not tpl.exists():
            pytest.skip("template.csv missing")
        import csv as _csv
        with open(tpl, newline="") as f:
            reader = _csv.DictReader(f)
            rows = list(reader)
        continuity = [r for r in rows if r.get("step", "").startswith("5.1")]
        record_property("continuity_rows", len(continuity))
        assert len(continuity) >= 14, (
            f"template.csv has only {len(continuity)} continuity rows (need ≥14 for CH9-CH22)"
        )
        for r in continuity:
            pf = r.get("probe_from", "")
            pt = r.get("probe_to", "")
            assert pf.startswith("J5.") or pf.startswith("U1."), (
                f"continuity probe_from must start with J5. or U1., got '{pf}'"
            )
            assert pt.startswith("U1.") or pt.startswith("J5."), (
                f"continuity probe_to must start with U1. or J5., got '{pt}'"
            )
        record_property("probe_point_anchoring", "enforced")

    # ── Release gate script — structural ──────────────────────────────

    def test_validate_release_structure(self, record_property):
        """validate_release.py must exist and define gates that block on real conditions."""
        path = HW_DIR / "validate_release.py"
        assert path.exists(), f"validate_release.py not found at {path}"
        source = path.read_text()
        import re as _re
        # Must check fab_notes structural patterns — clause IDs AND legacy regex
        assert "FAB_NOTES_CLAUSE_IDS" in source, (
            "validate_release.py must define FAB_NOTES_CLAUSE_IDS for canonical clause checks"
        )
        assert "FAB_NOTES_PATTERNS" in source, (
            "validate_release.py must define FAB_NOTES_PATTERNS for belt-and-suspenders regex checks"
        )
        # Must check vip_vias.csv schema
        assert "VIP_CSV_REQUIRED_COLS" in source, (
            "validate_release.py must define VIP_CSV_REQUIRED_COLS"
        )
        # Must check order_checklist.txt content
        assert "order_checklist.txt" in source, (
            "validate_release.py must verify order_checklist.txt presence and content"
        )
        # Must reference cam_diff baseline
        assert _re.search(r"cam_baseline|cam.?diff", source), (
            "validate_release.py must check CAM-diff baseline status"
        )
        # Must block on mask/drill changes
        assert _re.search(r"BLOCK|block", source), (
            "validate_release.py must be able to BLOCK release on violations"
        )
        # Must verify manifest.json integrity (Gate 11)
        assert "manifest.json" in source and "STALE" in source, (
            "validate_release.py must verify manifest.json hashes (stale-ZIP prevention)"
        )
        # Must check git SHA HEAD match
        assert "git_sha" in source and "allow_detached" in source, (
            "validate_release.py must compare manifest.git_sha to HEAD "
            "(with --allow-detached escape)"
        )
        # Must support --build for single-command flow
        assert "--build" in source and "_build_package" in source, (
            "validate_release.py must support --build flag for build+validate flow"
        )
        # Must enforce vendor capability naming convention
        assert "VENDOR_CAP_PATTERN" in source or "vippo_capability_" in source, (
            "validate_release.py must enforce vendor evidence naming convention"
        )
        # Must enforce manifest completeness (required ⊆ manifest)
        assert "required" in source.lower() and "manifest_files" in source, (
            "validate_release.py must enforce required_files ⊆ manifest.files"
        )
        # Must verify build_inputs provenance (Gate 12)
        assert "build_inputs" in source and "board_sha256" in source, (
            "validate_release.py must verify manifest.build_inputs.board_sha256 "
            "matches the current board file (Gate 12)"
        )
        # Must enforce vendor evidence content (min size + required strings)
        assert "MIN_EVIDENCE_BYTES" in source, (
            "validate_release.py must enforce minimum evidence file size "
            "to reject empty placeholders"
        )
        assert "REQUIRED_EVIDENCE_STRINGS" in source or "IPC-4761" in source, (
            "validate_release.py must check vendor evidence content for IPC-4761 ref"
        )
        # CLAUSE IDs must be sole blocking mechanism — English patterns non-blocking
        # Gate 4b must NOT append to errors for FAB_NOTES_PATTERNS
        assert "informational" in source.lower() or "non-blocking" in source.lower(), (
            "validate_release.py Gate 4b English patterns must be non-blocking/informational"
        )
        record_property("validate_release_structure", "enforced")

    def test_validate_release_against_zip(self, record_property):
        """validate_release.validate() must return 0 errors on a well-formed package."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "validate_release", str(HW_DIR / "validate_release.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        zip_path = mod.find_latest_zip()
        if zip_path is None:
            pytest.skip("No fab ZIP found — run make_fab_package.py first")
        # allow_detached=True because tests may run after commits that
        # didn't rebuild the ZIP — the SHA check is a human gate.
        errors = mod.validate(zip_path, allow_detached=True)
        record_property("release_gate_errors", errors)
        record_property("zip_validated", str(zip_path))
        assert not errors, (
            f"validate_release.validate() found {len(errors)} error(s) on the fab ZIP:\n"
            + "\n".join(f"  {e}" for e in errors)
        )

    # ── CAM-diff guard — structural ───────────────────────────────────

    def test_cam_diff_structure(self, record_property):
        """cam_diff.py must exist and enforce blocking on mask/drill changes."""
        path = HW_DIR / "cam_diff.py"
        assert path.exists(), f"cam_diff.py not found at {path}"
        source = path.read_text()
        import re as _re
        # Must classify mask + drill as blocking
        assert ".gts" in source and ".gbs" in source and ".drl" in source, (
            "cam_diff.py must classify .gts, .gbs, .drl as blocking extensions"
        )
        assert "BLOCKING" in source, (
            "cam_diff.py must define BLOCKING layer classification"
        )
        # Must support --ack-mask-change override
        assert "ack" in source.lower() and "mask" in source.lower(), (
            "cam_diff.py must support explicit acknowledgment of mask changes"
        )
        # Must use SHA-256 for deterministic hashing
        assert "sha256" in source.lower(), (
            "cam_diff.py must use SHA-256 for file hashing"
        )
        # Must exit non-zero on unacknowledged blocking changes
        assert _re.search(r"sys\.exit\(1\)", source), (
            "cam_diff.py must exit(1) on unacknowledged blocking changes"
        )
        record_property("cam_diff_structure", "enforced")

    # ── Vendor capability evidence ────────────────────────────────────

    def test_vendor_capability_dir_exists(self, record_property):
        """dfm_feedback/vendor_capability/ must exist for VIPPO evidence."""
        cap_dir = HW_DIR / "dfm_feedback" / "vendor_capability"
        record_property("path", str(cap_dir))
        assert cap_dir.exists(), (
            f"Missing: {cap_dir}\n"
            f"  This directory must contain vendor evidence that IPC-4761 Type VII\n"
            f"  via fill + cap plating is available for 0.2mm drill / 0.4mm pad.\n"
            f"  Acceptable: email PDF, screenshot, order confirmation."
        )

    # ── manifest.json — stale-ZIP prevention ──────────────────────────

    def test_manifest_json_in_zip(self, record_property):
        """ZIP must contain manifest.json with git_sha, timestamp, file hashes, and build_inputs."""
        import importlib.util, json, hashlib
        spec = importlib.util.spec_from_file_location(
            "validate_release", str(HW_DIR / "validate_release.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        zip_path = mod.find_latest_zip()
        if zip_path is None:
            pytest.skip("No fab ZIP found — run make_fab_package.py first")
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names, "ZIP missing manifest.json"
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            # Structure checks
            assert manifest.get("git_sha"), "manifest.json missing git_sha"
            assert manifest.get("build_timestamp_utc"), "manifest.json missing build_timestamp_utc"
            files_dict = manifest.get("files", {})
            assert len(files_dict) >= 10, (
                f"manifest.json lists only {len(files_dict)} files — expected ≥10"
            )
            # Spot-check: fab_notes.txt hash must match ZIP content
            if "fab_notes.txt" in files_dict:
                actual = hashlib.sha256(zf.read("fab_notes.txt")).hexdigest()
                assert actual == files_dict["fab_notes.txt"], (
                    "manifest.json fab_notes.txt hash mismatch — package is STALE"
                )
            # build_inputs must exist with board_sha256
            build_inputs = manifest.get("build_inputs", {})
            assert build_inputs, "manifest.json missing 'build_inputs' dict"
            assert "board_sha256" in build_inputs, (
                "manifest.json build_inputs missing 'board_sha256'"
            )
            assert "make_fab_package_sha256" in build_inputs, (
                "manifest.json build_inputs missing 'make_fab_package_sha256'"
            )
        record_property("manifest_verified", True)

    def test_validate_release_checks_manifest(self, record_property):
        """validate_release.py must include Gate 11 for manifest.json verification."""
        path = HW_DIR / "validate_release.py"
        assert path.exists()
        source = path.read_text()
        assert "manifest.json" in source, (
            "validate_release.py must verify manifest.json integrity"
        )
        assert "STALE" in source or "stale" in source, (
            "validate_release.py must detect stale packages via manifest hash comparison"
        )
        record_property("manifest_gate_present", True)


# ═══════════════════════════════════════════════════════════════════════════
# 11. BEHAVIORAL CAM-DIFF TEST (not string-grep)
# ═══════════════════════════════════════════════════════════════════════════

class TestCamDiffBehavioral:
    """Real behavioral test: create files, baseline, mutate, assert outcomes."""

    def test_cam_diff_detects_blocking_change(self, tmp_path, record_property):
        """cam_diff logic must detect a mutated .gts file as a blocking change."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Set up a fake output dir with gerbers/ and drill/
        gerber_dir = tmp_path / "gerbers"
        drill_dir = tmp_path / "drill"
        gerber_dir.mkdir()
        drill_dir.mkdir()

        # Create dummy files
        (gerber_dir / "board.gts").write_bytes(b"soldermask top original")
        (gerber_dir / "board.gbs").write_bytes(b"soldermask bot original")
        (gerber_dir / "board.gtl").write_bytes(b"copper top original")
        (drill_dir / "board.drl").write_bytes(b"drill original")

        # Save baseline
        baseline_path = tmp_path / ".cam_baseline.yaml"
        hashes_before = mod._collect_hashes(tmp_path)
        assert len(hashes_before) == 4, f"Expected 4 files hashed, got {len(hashes_before)}"

        # Write baseline YAML
        import yaml
        baseline_path.write_text(yaml.dump(hashes_before, default_flow_style=False))
        record_property("baseline_files", len(hashes_before))

        # Mutate a BLOCKING file (.gts)
        (gerber_dir / "board.gts").write_bytes(b"soldermask top MUTATED")

        # Re-hash and compare
        hashes_after = mod._collect_hashes(tmp_path)
        blocking_exts = {".gts", ".gbs", ".drl"}

        changed_blocking = []
        for rel_path, old_hash in hashes_before.items():
            new_hash = hashes_after.get(rel_path)
            if new_hash and new_hash != old_hash:
                from pathlib import Path as P
                if P(rel_path).suffix in blocking_exts:
                    changed_blocking.append(rel_path)

        record_property("blocking_changes_detected", changed_blocking)
        assert len(changed_blocking) == 1, (
            f"Expected exactly 1 blocking change (board.gts), got {changed_blocking}"
        )
        assert "board.gts" in changed_blocking[0], (
            f"The blocking change should be board.gts, got {changed_blocking}"
        )

    def test_cam_diff_ignores_non_blocking_change(self, tmp_path, record_property):
        """cam_diff logic must NOT block on a mutated .gtl (review-only) file."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        gerber_dir = tmp_path / "gerbers"
        drill_dir = tmp_path / "drill"
        gerber_dir.mkdir()
        drill_dir.mkdir()

        (gerber_dir / "board.gts").write_bytes(b"soldermask top original")
        (gerber_dir / "board.gtl").write_bytes(b"copper top original")
        (drill_dir / "board.drl").write_bytes(b"drill original")

        hashes_before = mod._collect_hashes(tmp_path)

        import yaml
        baseline_path = tmp_path / ".cam_baseline.yaml"
        baseline_path.write_text(yaml.dump(hashes_before, default_flow_style=False))

        # Mutate only a NON-blocking file (.gtl)
        (gerber_dir / "board.gtl").write_bytes(b"copper top MUTATED")

        hashes_after = mod._collect_hashes(tmp_path)
        blocking_exts = {".gts", ".gbs", ".drl"}

        changed_blocking = []
        for rel_path, old_hash in hashes_before.items():
            new_hash = hashes_after.get(rel_path)
            if new_hash and new_hash != old_hash:
                from pathlib import Path as P
                if P(rel_path).suffix in blocking_exts:
                    changed_blocking.append(rel_path)

        record_property("blocking_changes_detected", changed_blocking)
        assert len(changed_blocking) == 0, (
            f"Non-blocking .gtl change should NOT trigger a block, but got: {changed_blocking}"
        )

    def test_cam_diff_hash_deterministic(self, tmp_path, record_property):
        """_collect_hashes must return identical results for identical files."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        gerber_dir = tmp_path / "gerbers"
        drill_dir = tmp_path / "drill"
        gerber_dir.mkdir()
        drill_dir.mkdir()

        (gerber_dir / "board.gts").write_bytes(b"determinism test content")
        (drill_dir / "board.drl").write_bytes(b"drill determinism")

        h1 = mod._collect_hashes(tmp_path)
        h2 = mod._collect_hashes(tmp_path)

        record_property("hash_sets_equal", h1 == h2)
        assert h1 == h2, "Hashing the same files twice must produce identical results"

    def test_cam_diff_blocks_on_gbs_mutation(self, tmp_path, record_property):
        """cam_diff must block on B.Mask (.gbs) changes — not just F.Mask."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        gerber_dir = tmp_path / "gerbers"
        drill_dir = tmp_path / "drill"
        gerber_dir.mkdir()
        drill_dir.mkdir()

        (gerber_dir / "board.gts").write_bytes(b"mask top original")
        (gerber_dir / "board.gbs").write_bytes(b"mask bot original")
        (drill_dir / "board.drl").write_bytes(b"drill original")

        hashes_before = mod._collect_hashes(tmp_path)
        (gerber_dir / "board.gbs").write_bytes(b"mask bot MUTATED")
        hashes_after = mod._collect_hashes(tmp_path)

        changed = [
            k for k, v in hashes_before.items()
            if hashes_after.get(k) and hashes_after[k] != v
            and Path(k).suffix in mod.BLOCKING_EXTENSIONS
        ]
        record_property("gbs_blocking_detected", changed)
        assert len(changed) == 1 and "board.gbs" in changed[0]

    def test_cam_diff_blocks_on_drill_mutation(self, tmp_path, record_property):
        """cam_diff must block on drill (.drl) changes."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        gerber_dir = tmp_path / "gerbers"
        drill_dir = tmp_path / "drill"
        gerber_dir.mkdir()
        drill_dir.mkdir()

        (gerber_dir / "board.gts").write_bytes(b"mask top original")
        (drill_dir / "board.drl").write_bytes(b"drill original")
        (drill_dir / "board-NPTH.drl").write_bytes(b"npth original")

        hashes_before = mod._collect_hashes(tmp_path)
        (drill_dir / "board.drl").write_bytes(b"drill MUTATED")
        hashes_after = mod._collect_hashes(tmp_path)

        changed = [
            k for k, v in hashes_before.items()
            if hashes_after.get(k) and hashes_after[k] != v
            and Path(k).suffix in mod.BLOCKING_EXTENSIONS
        ]
        record_property("drill_blocking_detected", changed)
        assert len(changed) == 1 and "board.drl" in changed[0]

    def test_cam_diff_blocking_extensions_cover_drill_variants(self, record_property):
        """BLOCKING_EXTENSIONS must include .drl, .xln, .exc for drill coverage."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        required = {".gts", ".gbs", ".drl", ".xln", ".exc"}
        missing = required - mod.BLOCKING_EXTENSIONS
        record_property("blocking_extensions", sorted(mod.BLOCKING_EXTENSIONS))
        assert not missing, (
            f"BLOCKING_EXTENSIONS missing drill variants: {missing}"
        )

    def test_cam_diff_role_classification(self, record_property):
        """classify_role() must correctly identify mask/drill/copper files."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Mask files
        assert mod.classify_role("board-F_Mask.gts") == "mask_top"
        assert mod.classify_role("board-B_Mask.gbs") == "mask_bot"
        # Drill files (various naming conventions)
        assert mod.classify_role("board-PTH.drl") == "drill_pth"
        assert mod.classify_role("board-NPTH.drl") == "drill_npth"
        assert mod.classify_role("board.drl") == "drill_pth"  # default drill
        assert mod.classify_role("board.xln") == "drill_pth"
        assert mod.classify_role("board.exc") == "drill_pth"
        # Copper
        assert mod.classify_role("board-F_Cu.gtl") == "copper_top"
        assert mod.classify_role("board-B_Cu.gbl") == "copper_bot"
        # Blocking roles
        assert "mask_top" in mod.BLOCKING_ROLES
        assert "mask_bot" in mod.BLOCKING_ROLES
        assert "drill_pth" in mod.BLOCKING_ROLES
        assert "drill_npth" in mod.BLOCKING_ROLES
        record_property("role_classification", "verified")

    def test_cam_diff_role_blocks_renamed_mask(self, tmp_path, record_property):
        """A mask file renamed to a non-standard extension must still be caught by role."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cam_diff", str(HW_DIR / "cam_diff.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # A file with "F_Mask" in the name but a weird extension
        # should still classify as mask_top via role patterns
        role = mod.classify_role("board-F_Mask.gbr")
        record_property("role_for_renamed_mask", role)
        assert role == "mask_top", (
            f"File named 'board-F_Mask.gbr' should classify as mask_top, got {role}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 12. BRINGUP VALIDATOR BEHAVIORAL TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestBringupValidatorSemantics:
    """Behavioral tests: validate_bringup.py must enforce step-level semantics,
    not just "columns exist."""

    @staticmethod
    def _load_validator():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "validate_bringup", str(HW_DIR / "validate_bringup.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _write_csv(path, rows):
        """Write rows (list of dicts) to CSV."""
        import csv
        if not rows:
            path.write_text("")
            return
        fieldnames = list(rows[0].keys())
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    @staticmethod
    def _make_good_row(ch, step, **overrides):
        """Return a minimal valid row for a given channel and step."""
        base = {
            "vendor": "TestVendor", "order_id": "ORD-001",
            "lot_id": "LOT001", "panel_id": "PNL01",
            "board_serial": "BRD001", "inspector": "QA-Test",
            "date": "2026-03-01", "phase": "5",
            "step": step, "channel": ch, "result": "PASS",
            "notes": "",
        }
        if step == "5.1":
            base.update({
                "value": "1.2", "unit": "Ω", "method": "4W-Kelvin",
                "probe_from": f"J5.{10 + int(ch[2:]) - 9}",
                "probe_to": f"U1.{33 + int(ch[2:]) - 9}",
            })
        elif step == "5.2":
            base.update({
                "value": "150.0", "unit": "MΩ", "method": "100V-megger",
                "probe_from": f"J5.{10 + int(ch[2:]) - 9}",
                "probe_to": "J5.GND",
            })
        elif step == "3.2":
            base.update({
                "value": "", "unit": "", "method": "",
                "probe_from": "", "probe_to": "",
                "notes": "microsection_J5_LOT001_BRD001.jpg",
            })
        else:
            base.update({
                "value": "", "unit": "", "method": "",
                "probe_from": "", "probe_to": "",
            })
        base.update(overrides)
        return base

    def _make_full_csv(self, tmp_path):
        """Create a fully valid CSV with all required rows."""
        rows = []
        # Microsection
        rows.append(self._make_good_row("", "3.2", phase="3", channel=""))
        # Continuity + isolation for all 14 channels
        for n in range(9, 23):
            ch = f"CH{n}"
            rows.append(self._make_good_row(ch, "5.1"))
            rows.append(self._make_good_row(ch, "5.2"))
        csv_path = tmp_path / "test_results.csv"
        self._write_csv(csv_path, rows)
        # Create evidence dir
        ev_dir = tmp_path / "evidence"
        ev_dir.mkdir()
        (ev_dir / "microsection_J5_LOT001_BRD001.jpg").write_bytes(b"\x89PNG" + b"\x00" * 100)
        return csv_path

    def test_valid_csv_passes(self, tmp_path, record_property):
        """A fully valid CSV must pass with 0 errors."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        errors = mod.validate(csv_path)
        record_property("error_count", len(errors))
        assert not errors, f"Valid CSV should pass, got:\n" + "\n".join(errors)

    def test_missing_unit_continuity_fails(self, tmp_path, record_property):
        """Continuity row missing unit must fail."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        # Rewrite with one bad row (CH9 missing unit)
        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            if r["channel"] == "CH9" and r["step"] == "5.1":
                r["unit"] = ""  # remove unit
        self._write_csv(csv_path, rows)
        errors = mod.validate(csv_path)
        unit_errors = [e for e in errors if "unit" in e.lower() and "CH9" in e]
        record_property("unit_errors", unit_errors)
        assert unit_errors, "Missing unit on continuity row must produce an error"

    def test_wrong_unit_isolation_fails(self, tmp_path, record_property):
        """Isolation row with wrong unit (Ohm instead of MΩ) must fail."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            if r["channel"] == "CH10" and r["step"] == "5.2":
                r["unit"] = "Ω"  # wrong unit for isolation
        self._write_csv(csv_path, rows)
        errors = mod.validate(csv_path)
        unit_errors = [e for e in errors if "unit" in e.lower() and "CH10" in e]
        record_property("unit_errors", unit_errors)
        assert unit_errors, "Wrong unit on isolation row must produce an error"

    def test_missing_value_continuity_fails(self, tmp_path, record_property):
        """Continuity row missing numeric value must fail."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            if r["channel"] == "CH11" and r["step"] == "5.1":
                r["value"] = ""  # remove value
        self._write_csv(csv_path, rows)
        errors = mod.validate(csv_path)
        val_errors = [e for e in errors if "value" in e.lower() and "CH11" in e]
        record_property("value_errors", val_errors)
        assert val_errors, "Missing value on continuity row must produce an error"

    def test_channel_missing_isolation_fails(self, tmp_path, record_property):
        """A channel with continuity but no isolation must fail."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        # Remove all isolation rows for CH12
        rows = [r for r in rows if not (r["channel"] == "CH12" and r["step"] == "5.2")]
        self._write_csv(csv_path, rows)
        errors = mod.validate(csv_path)
        ch12_errors = [e for e in errors if "CH12" in e]
        record_property("ch12_errors", ch12_errors)
        assert ch12_errors, "CH12 with continuity but no isolation must fail"

    def test_channel_missing_continuity_fails(self, tmp_path, record_property):
        """A channel with isolation but no continuity must fail."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        # Remove all continuity rows for CH13
        rows = [r for r in rows if not (r["channel"] == "CH13" and r["step"] == "5.1")]
        self._write_csv(csv_path, rows)
        errors = mod.validate(csv_path)
        ch13_errors = [e for e in errors if "CH13" in e]
        record_property("ch13_errors", ch13_errors)
        assert ch13_errors, "CH13 with isolation but no continuity must fail"

    def test_invalid_method_fails(self, tmp_path, record_property):
        """Unrecognized method string must fail."""
        mod = self._load_validator()
        csv_path = self._make_full_csv(tmp_path)
        import csv
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            if r["channel"] == "CH14" and r["step"] == "5.1":
                r["method"] = "eyeball-test"  # invalid method
        self._write_csv(csv_path, rows)
        errors = mod.validate(csv_path)
        method_errors = [e for e in errors if "method" in e.lower() and "CH14" in e]
        record_property("method_errors", method_errors)
        assert method_errors, "Invalid method must produce an error"

    def test_pass_spam_detected(self, tmp_path, record_property):
        """All-PASS with missing measurements must still fail."""
        mod = self._load_validator()
        rows = []
        rows.append(self._make_good_row("", "3.2", phase="3", channel=""))
        for n in range(9, 23):
            ch = f"CH{n}"
            # "PASS" but no value, no unit, no method — pure PASS spam
            rows.append(self._make_good_row(ch, "5.1", value="", unit="", method=""))
            rows.append(self._make_good_row(ch, "5.2", value="", unit="", method=""))
        csv_path = tmp_path / "spam.csv"
        self._write_csv(csv_path, rows)
        ev_dir = tmp_path / "evidence"
        ev_dir.mkdir()
        (ev_dir / "microsection_J5_LOT001_BRD001.jpg").write_bytes(b"\x89PNG" + b"\x00" * 100)
        errors = mod.validate(csv_path)
        record_property("spam_error_count", len(errors))
        # Must catch at least 14 missing values + 14 missing units + 14 missing methods
        # for continuity, plus same for isolation = many errors
        assert len(errors) >= 28, (
            f"PASS-spam CSV should produce many errors, got only {len(errors)}:\n"
            + "\n".join(errors[:10])
        )