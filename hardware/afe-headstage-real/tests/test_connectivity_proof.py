"""Connectivity proof gate tests for Bundle C electrode routes (CH9–CH22).

Test structure:
  TestTruthTable           — 4 tests  (canonical pad table self-consistency)
  TestTruthTableVsGen      — 1 test   (exact bidirectional triple cross-check)
  TestBoardLevel           — 3 tests  (pads exist, nets have copper, net names)
  TestChannelGate          — 28 tests (14 × {connected, 0-islands})
  TestGenCoincidence       — 1 test   (generator endpoints == pad/via centers)
  TestDrcUnconnected       — 1 test   (KiCad DRC: 0 CH9-CH22 unconnected)
  TestDrcSabotage          — 1 test   (geometry-based break → DRC catches it)
  TestBfsSabotage          — 1 test   (via removal → BFS graph disconnected)
                           ─────────
                           40 tests total

Design decisions:
  - DRC attribution is structural: regex extracts (pin, net_name, ref) from
    each DRC item description.  NOT a string-search for "[CHxx]".
  - No stale-file fallback.  If kicad-cli is unavailable, DRC tests skip
    with a loud reason.  Falling back to cached JSON would mask regressions.
  - The sabotage tests prove the pipelines actually work:
      TestDrcSabotage: removes CH15 descent segment (found by geometry,
        not index), runs DRC, asserts parser flags CH15 as unconnected.
      TestBfsSabotage: removes CH15 entry via (found by geometry — farthest
        from J5), builds BFS graph, asserts U1.50 does NOT reach J5.16.
    If either test can't detect a known break, its gate is useless.
  - Net codes are derived from (net N "CH15") in the board file, never
    hardcoded.  Survives net renumbering.
  - Sabotage target selection is by geometry predicate, not element index.
    Survives KiCad block re-ordering.
  - Generator coincidence invariant: we require the generator to place
    segment endpoints exactly on pad centers and via centers.  This is
    an invariant used by the connectivity proof to avoid geometry
    intersection logic.  If we later add teardrops, fillets, or change
    endpoint targeting, this test will break (and it should).
  - Stats use record_property for CI JUnit visibility, PLUS a one-line
    print() summary for humans scanning logs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import pytest

# ── Project imports ──────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectivity_proof import (
    PAD_TRUTH_TABLE,
    CH_RANGE,
    NET_CODE_FOR_CH,
    SNAP_EPS,
    _snap,
    _node,
    parse_board,
    build_net_graph,
    bfs_reachable,
    find_pad_node,
    find_pad_by_ref_pin,
    parse_drc_json,
    run_kicad_drc,
    sabotage_board_by_uuid,
    find_element_uuid,
    parse_board_net_declarations,
    net_code_for_name,
    find_descent_segment_uuid,
    find_entry_via_uuid,
)

from gen_bundle_c_route import (
    LANES,
    _build_bottom_fan,
    build_all_polylines,
    _brd_to_kicad,
    _net_code_for_ch,
)

# ── Paths ────────────────────────────────────────────────────────────────

BOARD_PATH = Path(__file__).resolve().parent.parent / "afe-headstage-v1.kicad_pcb"
HW_DIR = Path(__file__).resolve().parent.parent

_HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def parsed():
    """Parse board once per module."""
    board_text = BOARD_PATH.read_text()
    return parse_board(board_text)


# ═══════════════════════════════════════════════════════════════════════════
# 1. TRUTH TABLE SELF-CONSISTENCY (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestTruthTable:
    """Validate the canonical PAD_TRUTH_TABLE."""

    def test_covers_all_channels(self):
        """Every channel CH9..CH22 is present."""
        assert set(PAD_TRUTH_TABLE.keys()) == set(CH_RANGE)

    def test_u1_pins_unique(self):
        """No two channels share a U1 pin."""
        u1_pins = [v[0] for v in PAD_TRUTH_TABLE.values()]
        assert len(u1_pins) == len(set(u1_pins))

    def test_j5_pins_unique(self):
        """No two channels share a J5 pin."""
        j5_pins = [v[1] for v in PAD_TRUTH_TABLE.values()]
        assert len(j5_pins) == len(set(j5_pins))

    def test_j5_pin_is_ch_plus_one(self):
        """J5 pin == str(ch + 1) for every channel (from gen_pcb_v1.py)."""
        for ch, (_, j5_pin) in PAD_TRUTH_TABLE.items():
            assert j5_pin == str(ch + 1), f"CH{ch}: expected J5.{ch+1}, got J5.{j5_pin}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. TRUTH TABLE ↔ GENERATOR STRICT CROSS-CHECK (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestTruthTableVsGen:
    """Strict bidirectional cross-check: truth table ↔ LANES.

    Gate:
      1. Every (ch, u1_pin, j5_pin) triple in PAD_TRUTH_TABLE appears in LANES.
      2. Every (ch, pin, j5_pin) triple in LANES appears in PAD_TRUTH_TABLE.
      3. No duplicate channels in LANES.
    """

    def test_exact_triple_match(self):
        tt_triples = {
            (ch, u1_pin, j5_pin)
            for ch, (u1_pin, j5_pin) in PAD_TRUTH_TABLE.items()
        }

        lanes_triples = set()
        lanes_channels = []
        for lane in LANES:
            ch = lane["ch"]
            lanes_triples.add((ch, str(lane["pin"]), str(lane["j5_pin"])))
            lanes_channels.append(ch)

        assert len(lanes_channels) == len(set(lanes_channels)), \
            f"Duplicate channels in LANES"

        assert tt_triples == lanes_triples, \
            f"Mismatch — only in truth table: {tt_triples - lanes_triples}, " \
            f"only in LANES: {lanes_triples - tt_triples}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. BOARD-LEVEL STRUCTURAL CHECKS (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestBoardLevel:
    """Board-level structural checks."""

    def test_correct_pads_exist(self, parsed):
        """Every channel has both U1 and J5 pads on the correct net."""
        for ch in CH_RANGE:
            net_code = NET_CODE_FOR_CH[ch]
            u1_pin, j5_pin = PAD_TRUTH_TABLE[ch]
            pads = parsed["pads"].get(net_code, [])
            u1_pad = find_pad_by_ref_pin(pads, "U1", u1_pin)
            j5_pad = find_pad_by_ref_pin(pads, "J5", j5_pin)
            assert u1_pad is not None, f"CH{ch}: U1.{u1_pin} pad not found on net {net_code}"
            assert j5_pad is not None, f"CH{ch}: J5.{j5_pin} pad not found on net {net_code}"

    def test_nets_have_copper(self, parsed):
        """Every CH net has at least one segment and one via."""
        for ch in CH_RANGE:
            net_code = NET_CODE_FOR_CH[ch]
            segs = [s for s in parsed["segments"] if s["net"] == net_code]
            vias = [v for v in parsed["vias"] if v["net"] == net_code]
            assert len(segs) > 0, f"CH{ch} (net {net_code}): no segments"
            assert len(vias) > 0, f"CH{ch} (net {net_code}): no vias"

    def test_pad_net_names_match(self, parsed):
        """Each pad's actual PCB net name is 'CH{ch}'.

        Catches the case where pin mapping is correct but net assignment
        drifted (rare, but catastrophic if it happens).
        """
        for ch in CH_RANGE:
            net_code = NET_CODE_FOR_CH[ch]
            expected_net_name = f"CH{ch}"
            u1_pin, j5_pin = PAD_TRUTH_TABLE[ch]
            pads = parsed["pads"].get(net_code, [])

            u1_pad = find_pad_by_ref_pin(pads, "U1", u1_pin)
            j5_pad = find_pad_by_ref_pin(pads, "J5", j5_pin)

            assert u1_pad is not None, f"CH{ch}: U1.{u1_pin} not found"
            assert u1_pad["net_name"] == expected_net_name, \
                f"CH{ch}: U1.{u1_pin} net_name={u1_pad['net_name']!r}, expected {expected_net_name!r}"

            assert j5_pad is not None, f"CH{ch}: J5.{j5_pin} not found"
            assert j5_pad["net_name"] == expected_net_name, \
                f"CH{ch}: J5.{j5_pin} net_name={j5_pad['net_name']!r}, expected {expected_net_name!r}"


# ═══════════════════════════════════════════════════════════════════════════
# 4. PER-CHANNEL CONNECTIVITY GATE (14 × 2 = 28 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestChannelGate:
    """Per-channel connectivity: correct pads connected, 0 islands.

    Parametrized over CH9..CH22.  Stats via record_property + print.
    """

    @pytest.fixture(params=list(CH_RANGE), ids=[f"CH{ch}" for ch in CH_RANGE])
    def ch_data(self, request, parsed, record_property):
        """Build graph for one channel, report stats."""
        ch = request.param
        net_code = NET_CODE_FOR_CH[ch]
        u1_pin, j5_pin = PAD_TRUTH_TABLE[ch]

        graph, net_segs, net_vias = build_net_graph(net_code, parsed)
        pads = parsed["pads"].get(net_code, [])

        u1_pad = find_pad_by_ref_pin(pads, "U1", u1_pin)
        j5_pad = find_pad_by_ref_pin(pads, "J5", j5_pin)

        u1_node = find_pad_node(graph, u1_pad["abs_xy"], u1_pad["layers"]) if u1_pad else None
        j5_node = find_pad_node(graph, j5_pad["abs_xy"], j5_pad["layers"]) if j5_pad else None

        n_nodes = len(graph)
        n_segs = len(net_segs)
        n_vias = len(net_vias)

        # CI-visible stats
        record_property(f"CH{ch}_segments", n_segs)
        record_property(f"CH{ch}_vias", n_vias)
        record_property(f"CH{ch}_nodes", n_nodes)

        return {
            "ch": ch,
            "graph": graph,
            "u1_node": u1_node,
            "j5_node": j5_node,
            "u1_pin": u1_pin,
            "j5_pin": j5_pin,
            "n_nodes": n_nodes,
        }

    def test_correct_pads_connected(self, ch_data):
        """U1.pin → J5.pin is BFS-reachable through copper graph."""
        ch = ch_data["ch"]
        u1_node = ch_data["u1_node"]
        j5_node = ch_data["j5_node"]

        assert u1_node is not None, \
            f"CH{ch}: U1.{ch_data['u1_pin']} not in copper graph"
        assert j5_node is not None, \
            f"CH{ch}: J5.{ch_data['j5_pin']} not in copper graph"

        reachable = bfs_reachable(ch_data["graph"], u1_node)
        assert j5_node in reachable, \
            f"CH{ch}: U1.{ch_data['u1_pin']} → J5.{ch_data['j5_pin']} NOT CONNECTED"

    def test_zero_islands(self, ch_data):
        """No disconnected copper nodes on this net."""
        ch = ch_data["ch"]
        graph = ch_data["graph"]
        u1_node = ch_data["u1_node"]

        assert u1_node is not None, f"CH{ch}: U1 node missing"

        reachable = bfs_reachable(graph, u1_node)
        all_nodes = set(graph.keys())
        unreachable = all_nodes - reachable
        assert len(unreachable) == 0, \
            f"CH{ch}: {len(unreachable)} disconnected node(s)"


# ═══════════════════════════════════════════════════════════════════════════
# 5. GENERATOR COINCIDENCE INVARIANT (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestGenCoincidence:
    """Verify the generator's exact-coincidence invariant.

    We require the generator to place endpoints exactly on targets
    (pad centers and via centers).  This is an invariant used by the
    connectivity proof to avoid geometry intersection logic.

    If we later add teardrops, fillets, or change endpoint targeting
    to "inside pad copper," this test will break (and it *should*).
    At that point, the connectivity proof must switch to distance-based
    pad/via matching.
    """

    def test_endpoints_coincide_with_targets(self, parsed):
        """All segment endpoints are at pad centers, via positions, or
        shared with another segment endpoint on the same net."""
        for ch in CH_RANGE:
            net_code = NET_CODE_FOR_CH[ch]
            net_segs = [s for s in parsed["segments"] if s["net"] == net_code]
            net_vias = [v for v in parsed["vias"] if v["net"] == net_code]
            pads = parsed["pads"].get(net_code, [])

            target_nodes: set[tuple[int, int, str]] = set()

            for via in net_vias:
                x, y = via["at"]
                for layer in via["layers"]:
                    target_nodes.add(_node(x, y, layer))

            for pad in pads:
                x, y = pad["abs_xy"]
                for layer in pad["layers"]:
                    target_nodes.add(_node(x, y, layer))

            all_endpoints: list[tuple[int, int, str]] = []
            for seg in net_segs:
                layer = seg["layer"]
                all_endpoints.append(_node(seg["start"][0], seg["start"][1], layer))
                all_endpoints.append(_node(seg["end"][0], seg["end"][1], layer))

            ep_counts = Counter(all_endpoints)

            orphans = []
            for ep, count in ep_counts.items():
                if ep not in target_nodes and count < 2:
                    xy = (ep[0] * SNAP_EPS, ep[1] * SNAP_EPS)
                    orphans.append(f"({xy[0]:.4f}, {xy[1]:.4f}) on {ep[2]}")

            assert not orphans, \
                f"CH{ch}: {len(orphans)} orphan endpoint(s) not at pad/via " \
                f"and not shared with another segment: {orphans}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. DRC JSON UNCONNECTED GATE (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestDrcUnconnected:
    """Gate: KiCad DRC reports 0 unconnected items for CH9–CH22 nets.

    Net attribution is structural: we regex-extract (pin, net_name, ref)
    from each DRC item description.  We do NOT string-search for "[CHxx]".

    Structural integrity assertions (unconnected_items):
      - Every entry has exactly 2 items (Pad/PTH/Track pair).
      - Every item description yields a net name (0 attribution failures).
      - total_parsed == total_sub_items (no silent parsing gaps).
      - track_items_seen == 0 on clean board (Track regex is unused here;
        exists only for sabotage/broken boards).

    Violations integrity (3-bucket):
      - Items with [NET] in description: must parse → viol_parse_failures == 0.
      - Items without [NET]: counted as viol_unattributed (legitimate).
      - Accounting: net_items + unattributed == total (no silent drops).

    Net presence assertion:
      - Each of CH9–CH22 exists in the board netlist with the correct name.
        Catches renamed/removed nets that would silently pass DRC checks.

    If kicad-cli is unavailable: pytest.skip (no stale-file fallback).
    """

    @pytest.mark.skipif(not _HAS_KICAD_CLI, reason="kicad-cli not on PATH")
    def test_zero_ch_unconnected(self, record_property):
        """KiCad DRC: 0 unconnected items on CH9–CH22 nets."""

        # ── Pre-check: CH9–CH22 nets exist in board netlist ──
        board_text = BOARD_PATH.read_text()
        net_decls = parse_board_net_declarations(board_text)
        for ch in CH_RANGE:
            expected_code = NET_CODE_FOR_CH[ch]
            expected_name = f"CH{ch}"
            actual_name = net_decls.get(expected_code)
            assert actual_name == expected_name, (
                f"CH{ch}: net {expected_code} declaration is {actual_name!r}, "
                f"expected {expected_name!r} — net was renamed or removed"
            )

        # ── Run DRC ──
        drc_json_path = run_kicad_drc(BOARD_PATH, HW_DIR)
        drc = parse_drc_json(drc_json_path)

        n_unconnected = len(drc["unconnected_items"])
        n_violations = len(drc["violations"])
        n_ch_unconnected = len(drc["ch_unconnected"])
        n_ch_violations = len(drc["ch_violations"])
        n_attribution_fail = drc["attribution_failures"]
        n_sub_items = drc["total_sub_items"]
        n_parsed = drc["total_parsed"]
        n_track_items = drc["track_items_seen"]
        non_pairs = drc["non_pair_entries"]
        # Violations 3-bucket accounting
        n_viol_items = drc["viol_total_items"]
        n_viol_net = drc["viol_net_items"]
        n_viol_parsed_net = drc["viol_parsed_net"]
        n_viol_parse_fail = drc["viol_parse_failures"]
        n_viol_unattr = drc["viol_unattributed"]

        # CI-visible stats
        record_property("drc_total_unconnected", n_unconnected)
        record_property("drc_total_violations", n_violations)
        record_property("drc_ch_unconnected", n_ch_unconnected)
        record_property("drc_ch_violations", n_ch_violations)
        record_property("drc_attribution_failures", n_attribution_fail)
        record_property("drc_total_sub_items", n_sub_items)
        record_property("drc_total_parsed", n_parsed)
        record_property("drc_track_items_seen", n_track_items)
        record_property("drc_viol_total_items", n_viol_items)
        record_property("drc_viol_net_items", n_viol_net)
        record_property("drc_viol_parsed_net", n_viol_parsed_net)
        record_property("drc_viol_parse_failures", n_viol_parse_fail)
        record_property("drc_viol_unattributed", n_viol_unattr)

        # Human-readable summary for log scanning
        print(
            f"\n  DRC: unconnected={n_unconnected}, violations={n_violations}, "
            f"CH_unconnected={n_ch_unconnected}, CH_violations={n_ch_violations}, "
            f"attribution_failures={n_attribution_fail}, "
            f"track_items={n_track_items}, "
            f"parsed={n_parsed}/{n_sub_items}\n"
            f"  Violations: total={n_viol_items}, "
            f"net_items={n_viol_net}, parsed_net={n_viol_parsed_net}, "
            f"parse_failures={n_viol_parse_fail}, "
            f"unattributed={n_viol_unattr}"
        )

        # ── STRUCTURAL INTEGRITY GATES ──

        # Every unconnected entry must have exactly 2 items (Pad/PTH/Track pair)
        assert not non_pairs, (
            f"{len(non_pairs)} unconnected entries don't have exactly 2 items "
            f"(indices: {non_pairs}). KiCad DRC format may have changed."
        )

        # Every item description must yield a net name (0 attribution failures)
        # Handles Pad, PTH pad, and Track descriptions.
        assert n_attribution_fail == 0, (
            f"{n_attribution_fail} DRC item(s) could not be attributed to a net — "
            f"parser regex doesn't match item description format "
            f"(expected Pad/PTH pad/Track)"
        )

        # Total parsed must equal total items seen (no silent gaps)
        assert n_parsed == n_sub_items, (
            f"Parsed {n_parsed} of {n_sub_items} items — "
            f"some items were silently skipped"
        )

        # On a clean board, all unconnected items should be Pad/PTH —
        # no Track stubs.  The Track regex should be unused here;
        # it exists only for sabotage / genuinely broken boards.
        assert n_track_items == 0, (
            f"Clean board DRC has {n_track_items} Track item(s) in "
            f"unconnected_items — expected 0. Track regex should only "
            f"fire on sabotaged/broken boards."
        )

        # ── VIOLATIONS COVERAGE (3-bucket) ──
        # Violation items split into:
        #   net_items:      has [NET] → must parse successfully
        #   unattributed:   no [NET]  → legitimately non-net (silk, edge, etc.)
        #   parse_failures: has [NET] but regex failed → BUG, must be 0

        # Accounting identity: no silent drops
        assert n_viol_net + n_viol_unattr == n_viol_items, (
            f"Violations accounting gap: net_items={n_viol_net} + "
            f"unattributed={n_viol_unattr} != total={n_viol_items}"
        )

        # Every net-attributed violation must parse (conditional integrity)
        assert n_viol_parsed_net == n_viol_net, (
            f"Violations: parsed {n_viol_parsed_net} of {n_viol_net} "
            f"net-attributed items — {n_viol_parse_fail} parse failure(s). "
            f"Parser regex doesn't handle a [NET]-containing description."
        )

        # Redundant explicit check: parse_failures must be 0
        assert n_viol_parse_fail == 0, (
            f"{n_viol_parse_fail} violation item(s) contain [NET] but "
            f"could not be parsed — this is a parser bug"
        )

        # ── PRIMARY GATE: zero CH9-CH22 unconnected ──
        if drc["ch_unconnected"]:
            details = "\n".join(
                f"  {item['net_name']}: {item['ref']}.{item['pin']} — "
                f"{item['description']}"
                for item in drc["ch_unconnected"]
            )
            pytest.fail(
                f"{n_ch_unconnected} CH9–CH22 unconnected items in DRC:\n"
                f"{details}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 7. DRC SABOTAGE SELF-TEST (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestDrcSabotage:
    """Prove the DRC attribution pipeline actually works.

    Non-negotiable sanity check: if you can't make the test fail on a
    known break, the test is useless.

    Method — surgical deletion by geometry:
      1. Derive CH15's net code from (net N "CH15") in the board file.
      2. Find the descent segment UUID by geometry predicate:
         F.Cu, vertical, spans U1_KEEP_TOP to BOTTOM_FAN_Y.
         No hardcoded indices — survives KiCad re-ordering.
      3. Create a board copy with that single segment removed (by UUID).
      4. Run kicad-cli DRC on the sabotaged copy.
      5. Parse the DRC JSON.
      6. Assert our parser flags CH15 as unconnected (≥ 1 hit).

    Why the descent segment?  It's the only segment whose removal
    definitively splits the route into two disconnected islands
    (U1-side and J5-side).  Removing a via or a short stub segment
    may leave enough connectivity through shared endpoints that
    KiCad doesn't flag it.  Verified empirically.

    We remove exactly one element by UUID — not by pattern or count.
    """

    SABOTAGE_CH = 15
    SABOTAGE_NET_NAME = "CH15"

    @pytest.mark.skipif(not _HAS_KICAD_CLI, reason="kicad-cli not on PATH")
    def test_sabotage_detected(self, record_property):
        """Remove CH15 descent segment by geometry → DRC flags CH15 → parser catches it."""

        board_text = BOARD_PATH.read_text()

        # 1. Derive net code from board file (not constant table)
        net_code = net_code_for_name(board_text, self.SABOTAGE_NET_NAME)
        assert net_code is not None, (
            f"Net '{self.SABOTAGE_NET_NAME}' not found in board netlist"
        )
        record_property("sabotage_net_code", net_code)

        # 2. Find descent segment UUID by geometry (not index)
        target_uuid = find_descent_segment_uuid(board_text, net_code)
        assert target_uuid is not None, (
            f"Cannot find descent segment for {self.SABOTAGE_NET_NAME} "
            f"(net {net_code}) by geometry"
        )

        record_property("sabotage_uuid", target_uuid)
        record_property("sabotage_method", "geometry_descent")
        record_property("sabotage_net_name", self.SABOTAGE_NET_NAME)

        # 3. Surgical deletion: remove exactly this one element
        sabotaged_board = sabotage_board_by_uuid(BOARD_PATH, target_uuid)

        # Verify exactly one element was removed
        sabotaged_text = sabotaged_board.read_text()
        assert board_text.count(target_uuid) == 1, "UUID not unique in original"
        assert sabotaged_text.count(target_uuid) == 0, "UUID still present after sabotage"

        # 4. Run DRC on sabotaged board
        try:
            drc_json_path = run_kicad_drc(sabotaged_board, HW_DIR)
        except RuntimeError as e:
            pytest.fail(f"kicad-cli DRC failed on sabotaged board: {e}")

        # 5. Parse DRC output
        drc = parse_drc_json(drc_json_path)

        n_ch_unconnected = len(drc["ch_unconnected"])
        record_property("sabotage_ch_unconnected_total", n_ch_unconnected)

        # Find hits specifically for CH15
        ch15_hits = [
            item for item in drc["ch_unconnected"]
            if item["net_name"] == self.SABOTAGE_NET_NAME
        ]
        record_property("sabotage_ch15_hits", len(ch15_hits))

        # Human-readable summary
        print(
            f"\n  Sabotage: removed descent segment "
            f"uuid={target_uuid[:12]}... "
            f"(net {net_code} = {self.SABOTAGE_NET_NAME}) → "
            f"CH_unconnected={n_ch_unconnected}, "
            f"{self.SABOTAGE_NET_NAME} hits={len(ch15_hits)}"
        )
        for hit in ch15_hits:
            print(f"    {hit['ref']}.{hit['pin']}: {hit['description']}")

        # 6. GATE: parser must find CH15 in unconnected items (≥ 1, not exact)
        assert len(ch15_hits) >= 1, (
            f"SABOTAGE SELF-TEST FAILED: removed descent segment "
            f"{target_uuid} ({self.SABOTAGE_NET_NAME}, net {net_code}) "
            f"but parser found 0 {self.SABOTAGE_NET_NAME} unconnected items.\n"
            f"Total CH unconnected={n_ch_unconnected}.\n"
            f"This means the DRC attribution pipeline is broken — "
            f"the test_zero_ch_unconnected gate cannot be trusted."
        )

        # Cleanup
        sabotaged_board.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# 8. BFS SABOTAGE SELF-TEST (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestBfsSabotage:
    """Prove the BFS connectivity model detects a missing layer bridge.

    Orthogonal to TestDrcSabotage: this tests OUR graph model, not KiCad's.

    Method — entry via removal:
      1. Derive CH15's net code from board file.
      2. Find the entry via UUID by geometry (farthest from J5 center).
      3. Create a board copy with that single via removed.
      4. Parse the sabotaged board and build the BFS graph.
      5. Assert U1.50 does NOT reach J5.16 (BFS disconnected).

    Why the entry via?  It's the F.Cu→B.Cu transition.  Removing it
    doesn't always trigger KiCad DRC (shared-endpoint effects), but our
    BFS model MUST detect it — the graph loses the layer bridge and the
    path is split.  This gives an orthogonal proof:
      - DRC sabotage = "KiCad agrees the break is real"
      - BFS sabotage = "our model detects missing layer bridge"
    """

    SABOTAGE_CH = 15
    SABOTAGE_NET_NAME = "CH15"

    @pytest.mark.skipif(not _HAS_KICAD_CLI, reason="kicad-cli not on PATH")
    def test_bfs_disconnected_on_via_removal(self, record_property):
        """Remove CH15 entry via → BFS fails to connect U1.50 to J5.16."""

        board_text = BOARD_PATH.read_text()

        # 1. Derive net code from board file
        net_code = net_code_for_name(board_text, self.SABOTAGE_NET_NAME)
        assert net_code is not None, (
            f"Net '{self.SABOTAGE_NET_NAME}' not found in board netlist"
        )
        record_property("bfs_sabotage_net_code", net_code)

        # 2. Find entry via UUID by geometry (farthest from J5)
        via_uuid = find_entry_via_uuid(board_text, net_code)
        assert via_uuid is not None, (
            f"Cannot find entry via for {self.SABOTAGE_NET_NAME} "
            f"(net {net_code})"
        )
        record_property("bfs_sabotage_via_uuid", via_uuid)

        # 3. Surgical deletion: remove exactly this via
        sabotaged_board = sabotage_board_by_uuid(BOARD_PATH, via_uuid)

        sabotaged_text = sabotaged_board.read_text()
        assert board_text.count(via_uuid) == 1, "UUID not unique in original"
        assert sabotaged_text.count(via_uuid) == 0, "UUID still present after sabotage"

        # 4. Parse sabotaged board and build graph
        sabotaged_parsed = parse_board(sabotaged_text)
        graph, _, _ = build_net_graph(net_code, sabotaged_parsed)
        pads = sabotaged_parsed["pads"].get(net_code, [])

        # Look up U1 and J5 pads
        u1_pin, j5_pin = PAD_TRUTH_TABLE[self.SABOTAGE_CH]
        u1_pad = find_pad_by_ref_pin(pads, "U1", u1_pin)
        j5_pad = find_pad_by_ref_pin(pads, "J5", j5_pin)
        assert u1_pad is not None, f"U1.{u1_pin} not found after sabotage"
        assert j5_pad is not None, f"J5.{j5_pin} not found after sabotage"

        u1_node = find_pad_node(graph, u1_pad["abs_xy"], u1_pad["layers"])
        j5_node = find_pad_node(graph, j5_pad["abs_xy"], j5_pad["layers"])

        # 5. BFS must NOT reach J5 from U1 (via removal breaks layer bridge)
        if u1_node is not None and j5_node is not None:
            reachable = bfs_reachable(graph, u1_node)
            connected = j5_node in reachable

            record_property("bfs_sabotage_connected", connected)
            record_property("bfs_sabotage_reachable_nodes", len(reachable))
            record_property("bfs_sabotage_total_nodes", len(graph))

            print(
                f"\n  BFS Sabotage: removed entry via "
                f"uuid={via_uuid[:12]}... "
                f"(net {net_code} = {self.SABOTAGE_NET_NAME}) → "
                f"connected={connected}, "
                f"reachable={len(reachable)}/{len(graph)} nodes"
            )

            assert not connected, (
                f"BFS SABOTAGE FAILED: removed entry via {via_uuid} "
                f"({self.SABOTAGE_NET_NAME}) but U1.{u1_pin} still reaches "
                f"J5.{j5_pin}.\n"
                f"This means the BFS connectivity model has a flaw — "
                f"the layer bridge removal should disconnect the path."
            )
        else:
            # If a pad node is missing from graph, that's also a disconnection
            # (the via removal may have isolated it completely)
            record_property("bfs_sabotage_connected", False)
            print(
                f"\n  BFS Sabotage: removed entry via "
                f"uuid={via_uuid[:12]}... "
                f"— pad node(s) not in graph (disconnected by definition)"
            )

        # Cleanup
        sabotaged_board.unlink(missing_ok=True)
