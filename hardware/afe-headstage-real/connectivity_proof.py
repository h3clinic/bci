#!/usr/bin/env python3
"""Connectivity proof for Bundle C electrode routes (CH9–CH22).

Parses the final afe-headstage-v1.kicad_pcb, builds a per-layer copper
connectivity graph for each CH net, and verifies:
  1. U1 target pad → correct J5 target pad is reachable (BFS)
  2. No disconnected copper islands on the net
  3. Pad pin numbers match the canonical truth table

Graph model
-----------
  Node = (snap(x), snap(y), layer)   — integer grid keys
  Edge = segment endpoint-to-endpoint on the same layer, OR
         via connecting (x, y, "F.Cu") ↔ (x, y, "B.Cu")

Coordinate snapping
-------------------
Epsilon-grid snapping with integer keys (never decimal rounding).
  eps = 5e-5 mm (0.05 µm).
  Node key = (round(x / eps), round(y / eps), layer).
  All coordinate sources (segments, vias, pads) flow through _node()
  which calls _snap().  Raw floats are stored in parse results and
  only converted to grid keys at graph-building and pad-lookup time.

Pad/via attachment model
------------------------
Our generator always emits segment endpoints that are *exactly
coincident* with pad centers and via centers (verified by the
test_endpoints_coincide_with_targets gate in test_connectivity_proof.py).
Therefore strict snap-grid equality is correct for edge building.

If a future change (teardrops, via-in-pad shifts, fillet arcs) breaks
exact coincidence, the generator gate test will catch it before the
connectivity proof even runs.

Scope
-----
Connectivity is proven over (segments + vias + pads) only.
Filled zones, teardrops, and other non-primitive copper are NOT parsed.
"0 islands" means: no disconnected islands among routed primitives.

Canonical pad mapping
---------------------
  CH{n} connects U1.{u1_pin} → J5.{n+1}.
  Single source of truth: PAD_TRUTH_TABLE below.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict, deque
from pathlib import Path

BOARD_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_pcb"

# ═══════════════════════════════════════════════════════════════════════
# CANONICAL PAD TRUTH TABLE — single source of truth
# ═══════════════════════════════════════════════════════════════════════
# {channel: (u1_pin, j5_pin)}
#
# U1 (RHD2132 QFN-56) north-side pads:
#   Pin 43=CH22, 44=CH21, ..., 49=CH16 (east)
#   Pin 50=CH15, 51=CH14, ..., 56=CH9  (west)
#
# J5 (Omnetics A79024-001):
#   CH{n} → J5.{n+1} (from gen_pcb_v1.py net registration order)
#
# Used by both the proof script and the gate tests.
# Cross-checked against gen_bundle_c_route.LANES by test.

PAD_TRUTH_TABLE: dict[int, tuple[str, str]] = {
    9:  ("56", "10"),
    10: ("55", "11"),
    11: ("54", "12"),
    12: ("53", "13"),
    13: ("52", "14"),
    14: ("51", "15"),
    15: ("50", "16"),
    16: ("49", "17"),
    17: ("48", "18"),
    18: ("47", "19"),
    19: ("46", "20"),
    20: ("45", "21"),
    21: ("44", "22"),
    22: ("43", "23"),
}

# Channels we route (Bundle C)
CH_RANGE = range(9, 23)  # CH9..CH22
NET_CODE_FOR_CH = {ch: 4 + ch for ch in CH_RANGE}  # CH9=13 .. CH22=26
CH_FOR_NET_CODE = {v: k for k, v in NET_CODE_FOR_CH.items()}

# Net names for DRC JSON matching
CH_NET_NAMES = {ch: f"CH{ch}" for ch in CH_RANGE}


# ═══════════════════════════════════════════════════════════════════════
# EPSILON-GRID SNAPPING
# ═══════════════════════════════════════════════════════════════════════

SNAP_EPS = 5e-5  # mm — 0.05 µm


def _snap(v: float) -> int:
    """Snap a coordinate to the integer grid.

    This is the ONLY function that converts a float coordinate to a
    grid key.  Every code path that needs a comparable coordinate must
    call _snap() or _node().  No round(x, N) anywhere.
    """
    return round(v / SNAP_EPS)


def _node(x: float, y: float, layer: str) -> tuple[int, int, str]:
    """Create a graph node with epsilon-grid snapping."""
    return (_snap(x), _snap(y), layer)


def _node_xy(node: tuple[int, int, str]) -> tuple[float, float]:
    """Recover approximate (x, y) from a snapped node (for diagnostics)."""
    return (node[0] * SNAP_EPS, node[1] * SNAP_EPS)


# ═══════════════════════════════════════════════════════════════════════
# KiCad 9 PARSER — block-based (handles multi-line s-expressions)
# ═══════════════════════════════════════════════════════════════════════

def _extract_blocks(text: str, block_type: str) -> list[str]:
    """Extract top-level blocks of a given type from KiCad s-expression text.

    Handles KiCad 9 multi-line format by tracking parenthesis depth.
    Only extracts blocks at indent level 4 (top-level children of kicad_pcb).
    """
    blocks = []
    pattern = re.compile(rf'^    \({block_type}\b', re.MULTILINE)
    for m in pattern.finditer(text):
        start = m.start()
        depth = 0
        i = start
        while i < len(text):
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    blocks.append(text[start:i + 1])
                    break
            i += 1
    return blocks


def _extract_xy(block: str, field: str) -> tuple[float, float] | None:
    m = re.search(rf'\({field}\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\)', block)
    return (float(m.group(1)), float(m.group(2))) if m else None


def _extract_int(block: str, field: str) -> int | None:
    m = re.search(rf'\({field}\s+(\d+)', block)
    return int(m.group(1)) if m else None


def _extract_layer(block: str) -> str | None:
    m = re.search(r'\(layer\s+"([^"]+)"\)', block)
    return m.group(1) if m else None


def _extract_layers(block: str) -> tuple[str, str] | None:
    m = re.search(r'\(layers\s+"([^"]+)"\s+"([^"]+)"\)', block)
    return (m.group(1), m.group(2)) if m else None


def _extract_float(block: str, field: str) -> float | None:
    """Extract a floating-point value from a KiCad s-expression field."""
    m = re.search(rf'\({field}\s+([\d.Ee+-]+)\)', block)
    return float(m.group(1)) if m else None


def _extract_str(block: str, field: str) -> str | None:
    """Extract a quoted string value from a KiCad s-expression field."""
    m = re.search(rf'\({field}\s+"([^"]*)"\)', block)
    return m.group(1) if m else None


# ═══════════════════════════════════════════════════════════════════════
# PARSE BOARD
# ═══════════════════════════════════════════════════════════════════════

def parse_board(board_text: str) -> dict:
    """Parse segments, vias, and footprint pads from the board.

    Coordinates are stored as raw floats from the file.  Snapping to the
    epsilon grid happens only when building graph nodes or looking up pads.
    """
    # ── Segments ──
    seg_blocks = _extract_blocks(board_text, "segment")
    segments = []
    for blk in seg_blocks:
        start = _extract_xy(blk, "start")
        end = _extract_xy(blk, "end")
        layer = _extract_layer(blk)
        net = _extract_int(blk, "net")
        width = _extract_float(blk, "width")
        uuid = _extract_str(blk, "uuid")
        if start and end and layer and net is not None:
            segments.append({
                "start": start, "end": end,
                "layer": layer, "net": net,
                "width": width, "uuid": uuid,
            })

    # ── Vias ──
    via_blocks = _extract_blocks(board_text, "via")
    vias = []
    for blk in via_blocks:
        at = _extract_xy(blk, "at")
        layers = _extract_layers(blk)
        net = _extract_int(blk, "net")
        size = _extract_float(blk, "size")
        drill = _extract_float(blk, "drill")
        uuid = _extract_str(blk, "uuid")
        if at and layers and net is not None:
            vias.append({
                "at": at, "layers": layers, "net": net,
                "size": size, "drill": drill, "uuid": uuid,
            })

    # ── Footprints → Pads ──
    fp_blocks = _extract_blocks(board_text, "footprint")
    pads: dict[int, list[dict]] = defaultdict(list)

    for fp_blk in fp_blocks:
        fp_at = _extract_xy(fp_blk, "at")
        if not fp_at:
            continue
        fp_x, fp_y = fp_at

        ref_match = re.search(r'property "Reference" "([^"]+)"', fp_blk)
        ref = ref_match.group(1) if ref_match else "?"

        pad_re = re.compile(
            r'\(pad "([^"]+)"\s+(\w+)\s+\w+\s*\n'
            r'\s+\(at\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\)',
            re.MULTILINE,
        )
        for pm in pad_re.finditer(fp_blk):
            pin = pm.group(1)
            pad_type = pm.group(2)   # "smd" or "thru_hole"
            pad_dx, pad_dy = float(pm.group(3)), float(pm.group(4))
            abs_x, abs_y = fp_x + pad_dx, fp_y + pad_dy

            # Extract this pad's full block for net and layers
            pad_start = pm.start()
            depth = 0
            pad_end = pad_start
            for ci in range(pad_start, len(fp_blk)):
                if fp_blk[ci] == '(':
                    depth += 1
                elif fp_blk[ci] == ')':
                    depth -= 1
                    if depth == 0:
                        pad_end = ci
                        break
            pad_text = fp_blk[pad_start:pad_end + 1]

            net_m = re.search(r'\(net\s+(\d+)\s+"([^"]*)"', pad_text)
            if not net_m:
                continue
            pad_net = int(net_m.group(1))
            pad_net_name = net_m.group(2)

            layers_m = re.search(r'\(layers\s+([^)]+)\)', pad_text)
            pad_layers = re.findall(r'"([^"]+)"', layers_m.group(1)) if layers_m else []

            pads[pad_net].append({
                "abs_xy": (abs_x, abs_y),
                "layers": pad_layers,
                "ref": ref,
                "pin": pin,
                "net_name": pad_net_name,
                "pad_type": pad_type,
            })

    return {"segments": segments, "vias": vias, "pads": pads}


# ═══════════════════════════════════════════════════════════════════════
# GRAPH BUILDER
# ═══════════════════════════════════════════════════════════════════════

def build_net_graph(
    net_code: int,
    parsed: dict,
) -> tuple[dict[tuple, set[tuple]], list[dict], list[dict]]:
    """Build connectivity graph for a single net.

    All coordinate→key conversion goes through _node() → _snap().
    """
    graph: dict[tuple, set[tuple]] = defaultdict(set)

    net_segments = [s for s in parsed["segments"] if s["net"] == net_code]
    net_vias = [v for v in parsed["vias"] if v["net"] == net_code]

    for seg in net_segments:
        layer = seg["layer"]
        n1 = _node(seg["start"][0], seg["start"][1], layer)
        n2 = _node(seg["end"][0], seg["end"][1], layer)
        graph[n1].add(n2)
        graph[n2].add(n1)

    for via in net_vias:
        x, y = via["at"]
        l1, l2 = via["layers"]
        n1 = _node(x, y, l1)
        n2 = _node(x, y, l2)
        graph[n1].add(n2)
        graph[n2].add(n1)

    return graph, net_segments, net_vias


def bfs_reachable(
    graph: dict[tuple, set[tuple]],
    start: tuple,
) -> set[tuple]:
    """BFS from start, return all reachable nodes."""
    visited = set()
    queue = deque([start])
    visited.add(start)
    while queue:
        node = queue.popleft()
        for nbr in graph.get(node, set()):
            if nbr not in visited:
                visited.add(nbr)
                queue.append(nbr)
    return visited


# ═══════════════════════════════════════════════════════════════════════
# PAD → NODE LOOKUP
# ═══════════════════════════════════════════════════════════════════════

def find_pad_node(
    graph: dict[tuple, set[tuple]],
    abs_xy: tuple[float, float],
    pad_layers: list[str],
) -> tuple | None:
    """Find the graph node matching a pad position.

    Snaps pad center through _node() (same path as segment/via endpoints).
    Tries declared copper layers first, then any layer (via-in-pad).
    """
    x, y = abs_xy
    for layer in pad_layers:
        node = _node(x, y, layer)
        if node in graph:
            return node
    # Fallback: any layer at this XY (via-in-pad creates B.Cu node)
    ix, iy = _snap(x), _snap(y)
    for node in graph:
        if node[0] == ix and node[1] == iy:
            return node
    return None


def find_pad_by_ref_pin(
    pads_for_net: list[dict],
    ref: str,
    pin: str,
) -> dict | None:
    """Find a specific pad by reference designator and pin number."""
    for p in pads_for_net:
        if p["ref"] == ref and p["pin"] == pin:
            return p
    return None


# ═══════════════════════════════════════════════════════════════════════
# DRC JSON PARSER — structured net attribution
# ═══════════════════════════════════════════════════════════════════════

# Regex for KiCad DRC item descriptions:
#   "Pad 56 [CH9] of U1 on F.Cu"        → pin=56, net=CH9, ref=U1
#   "PTH pad EP [GND] of U1"            → pin=EP, net=GND, ref=U1
#   "Track [CH15] on F.Cu, length 0.45 mm" → net=CH15 (no pin/ref)
_DRC_PAD_RE = re.compile(
    r'(?:Pad|PTH pad)\s+(\S+)\s+\[([^\]]+)\]\s+of\s+(\w+)'
)

# Regex for Track descriptions (sabotage may produce these instead of Pad)
_DRC_TRACK_RE = re.compile(
    r'Track\s+\[([^\]]+)\]\s+on\s+'
)


def _extract_net_from_drc_item(item_desc: str) -> str | None:
    """Extract net name from a DRC item description.

    Handles three description formats from KiCad DRC:
      - Pad:     "Pad N [NETNAME] of REF on LAYER"
      - PTH pad: "PTH pad EP [NETNAME] of REF"
      - Track:   "Track [NETNAME] on LAYER, length X mm"

    Uses structured regex to pull net from '[NETNAME]' in the description.
    Returns None if the description doesn't match any expected format
    (which would be a parser bug to investigate).
    """
    m = _DRC_PAD_RE.search(item_desc)
    if m:
        return m.group(2)
    m = _DRC_TRACK_RE.search(item_desc)
    if m:
        return m.group(1)
    return None


def _extract_ref_pin_from_drc_item(item_desc: str) -> tuple[str, str] | None:
    """Extract (ref, pin) from a DRC item description."""
    m = _DRC_PAD_RE.search(item_desc)
    return (m.group(3), m.group(1)) if m else None


# Set of net names we care about: "CH9", "CH10", ..., "CH22"
_CH_NET_NAME_SET = {f"CH{ch}" for ch in CH_RANGE}


def parse_drc_json(drc_path: Path) -> dict:
    """Parse KiCad DRC JSON report with structured net attribution.

    Net attribution is done by regex-extracting the net name from each
    item's description field.  Handles three formats:
      - Pad:     'Pad N [NETNAME] of REF on LAYER'
      - PTH pad: 'PTH pad EP [NETNAME] of REF'
      - Track:   'Track [NETNAME] on LAYER, length X mm'

    This is NOT a string search for '[CHxx]' — we parse the actual net
    name from the structured description.

    Returns:
        {
            "unconnected_items": [...],       # raw from JSON
            "violations": [...],               # raw from JSON
            "ch_unconnected": [{...}, ...],    # entries attributed to CH9-CH22
            "ch_violations": [{...}, ...],     # entries attributed to CH9-CH22
            "attribution_failures": int,       # items where regex didn't match
        }
    """
    import json
    with open(drc_path) as f:
        drc = json.load(f)

    # ── Unconnected items parsing ──
    attribution_failures = 0
    total_sub_items = 0
    total_parsed = 0
    track_items_seen = 0  # Track descriptions (vs Pad/PTH) in unconnected
    non_pair_entries: list[int] = []  # entry indices with != 2 items

    ch_unconnected: list[dict] = []
    for entry_idx, entry in enumerate(drc.get("unconnected_items", [])):
        items = entry.get("items", [])
        # Structural check: every unconnected entry should have exactly 2 items
        # (Pad/PTH pad/Track — KiCad always reports a disconnected pair)
        if len(items) != 2:
            non_pair_entries.append(entry_idx)
        for sub_item in items:
            total_sub_items += 1
            desc = sub_item.get("description", "")
            net_name = _extract_net_from_drc_item(desc)
            if net_name is None:
                attribution_failures += 1
                continue
            total_parsed += 1
            # Track which regex matched: Pad/PTH vs Track
            if _DRC_TRACK_RE.search(desc):
                track_items_seen += 1
            if net_name in _CH_NET_NAME_SET:
                ref_pin = _extract_ref_pin_from_drc_item(desc)
                ch_unconnected.append({
                    "net_name": net_name,
                    "ref": ref_pin[0] if ref_pin else "?",
                    "pin": ref_pin[1] if ref_pin else "?",
                    "description": desc,
                    "pos": sub_item.get("pos"),
                })

    # ── Violations parsing ──
    # Not every violation item is net-attributed.  Courtyard overlaps,
    # silk issues, edge clearance, etc. have no [NETNAME] in their
    # description.  We split into three buckets:
    #
    #   viol_net_items:       description contains '[...]' → expected to parse
    #   viol_unattributed:    description has no '[...]'  → legitimately non-net
    #   viol_parse_failures:  description has '[...]' but regex failed → BUG
    #
    # Invariants:
    #   viol_net_items + viol_unattributed == viol_total_items  (no silent drops)
    #   viol_parse_failures == 0  (if it has a [NET], we must parse it)
    _BRACKET_NET_RE = re.compile(r'\[([^\]]+)\]')

    viol_total_items = 0
    viol_net_items = 0       # has [NET] in description
    viol_parsed_net = 0      # has [NET] AND regex extracted it
    viol_parse_failures = 0  # has [NET] BUT regex couldn't extract it
    viol_unattributed = 0    # no [NET] at all (legitimately non-net)

    ch_violations: list[dict] = []
    for v in drc.get("violations", []):
        for sub_item in v.get("items", []):
            viol_total_items += 1
            desc = sub_item.get("description", "")

            has_bracket_net = bool(_BRACKET_NET_RE.search(desc))
            if not has_bracket_net:
                viol_unattributed += 1
                continue

            # Description has [NET] — we MUST parse it
            viol_net_items += 1
            net_name = _extract_net_from_drc_item(desc)
            if net_name is None:
                viol_parse_failures += 1
                continue
            viol_parsed_net += 1
            if net_name in _CH_NET_NAME_SET:
                ch_violations.append({
                    "net_name": net_name,
                    "type": v["type"],
                    "description": desc,
                })

    return {
        "unconnected_items": drc.get("unconnected_items", []),
        "violations": drc.get("violations", []),
        "ch_unconnected": ch_unconnected,
        "ch_violations": ch_violations,
        # Unconnected items accounting
        "attribution_failures": attribution_failures,
        "total_sub_items": total_sub_items,
        "total_parsed": total_parsed,
        "track_items_seen": track_items_seen,
        "non_pair_entries": non_pair_entries,
        # Violations accounting (3-bucket)
        "viol_total_items": viol_total_items,
        "viol_net_items": viol_net_items,
        "viol_parsed_net": viol_parsed_net,
        "viol_parse_failures": viol_parse_failures,
        "viol_unattributed": viol_unattributed,
    }


# ═══════════════════════════════════════════════════════════════════════
# DRC RUNNER + SABOTAGE HELPERS (for self-test)
# ═══════════════════════════════════════════════════════════════════════

def run_kicad_drc(board_path: Path, hw_dir: Path | None = None) -> Path:
    """Run kicad-cli pcb drc on a board file, return path to JSON output.

    Raises FileNotFoundError if kicad-cli is not available.
    Raises RuntimeError if DRC JSON is not produced.
    """
    import shutil
    import subprocess
    import tempfile

    if shutil.which("kicad-cli") is None:
        raise FileNotFoundError("kicad-cli not found on PATH")

    if hw_dir is None:
        hw_dir = board_path.parent

    tmpdir = tempfile.mkdtemp(prefix="drc_")
    tmp_pcb = Path(tmpdir) / "v1.kicad_pcb"
    tmp_pro = Path(tmpdir) / "v1.kicad_pro"
    tmp_fp = Path(tmpdir) / "fp-lib-table"
    tmp_json = Path(tmpdir) / "v1_drc.json"

    shutil.copy2(board_path, tmp_pcb)

    drc_pro = hw_dir / "drc.kicad_pro"
    if drc_pro.exists():
        shutil.copy2(drc_pro, tmp_pro)

    fp_lib = hw_dir / "fp-lib-table"
    if fp_lib.exists():
        fp_text = fp_lib.read_text()
        fp_text = fp_text.replace("${KIPRJMOD}", str(hw_dir))
        tmp_fp.write_text(fp_text)

    result = subprocess.run(
        ["kicad-cli", "pcb", "drc",
         "--format", "json",
         "--output", str(tmp_json),
         "--exit-code-violations",
         str(tmp_pcb)],
        capture_output=True, text=True, timeout=120,
    )

    if not tmp_json.exists():
        raise RuntimeError(
            f"kicad-cli DRC produced no JSON.\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
    return tmp_json


def find_element_uuid(board_text: str, block_type: str,
                      target_net_code: int, index: int = 0) -> str | None:
    """Find the UUID of the Nth element of block_type on a given net.

    Args:
        board_text: Full board file text.
        block_type: "segment" or "via".
        target_net_code: Net code to match.
        index: 0-based index (which match to return).

    Returns the UUID string, or None if not found.
    """
    blocks = _extract_blocks(board_text, block_type)
    hits = 0
    for blk in blocks:
        net = _extract_int(blk, "net")
        if net == target_net_code:
            if hits == index:
                uuid_m = re.search(r'\(uuid "([^"]+)"\)', blk)
                return uuid_m.group(1) if uuid_m else None
            hits += 1
    return None


def net_code_for_name(board_text: str, net_name: str) -> int | None:
    """Derive net code from the board file by net name.

    Parses (net N "name") declarations. Returns None if not found.
    This is the ONLY correct way to get a net code — never hardcode.
    """
    decls = parse_board_net_declarations(board_text)
    for code, name in decls.items():
        if name == net_name:
            return code
    return None


def find_descent_segment_uuid(board_text: str, net_code: int) -> str | None:
    """Find the UUID of the descent segment on a net by geometry.

    The descent segment is the long F.Cu vertical that bridges the
    header region (near U1_KEEP_TOP) to the bottom-fan region
    (near BOTTOM_FAN_Y).  It is uniquely identifiable as:

      - Layer: F.Cu
      - Vertical (|dx| < 0.01 mm)
      - min_y ≤ BRD_OY + U1_KEEP_TOP + 0.1    (reaches into header)
      - max_y ≥ BRD_OY + BOTTOM_FAN_Y - 0.1    (reaches into bottom fan)

    These geometry constants come from the board layout.  The epsilon
    margin (0.1mm) accommodates minor shifts without matching wrong
    segments.  The predicate is conservative: no other segment type
    in Bundle C spans this full vertical range on F.Cu.

    If the geometry predicate matches zero or >1 segments, falls back
    to selecting the longest F.Cu segment on the net (which is always
    the descent by a large margin — 13mm vs 9.7mm next longest).

    Returns UUID string, or None if net has no F.Cu segments.
    """
    from board_geom import BRD_OY

    # Import geometry constants from the layout authority
    from ref_elec_clearance_check import U1_KEEP_TOP, BOTTOM_FAN_Y

    # Convert to KiCad coordinates
    header_y_kcd = BRD_OY + U1_KEEP_TOP   # 89.125
    bottom_fan_y_kcd = BRD_OY + BOTTOM_FAN_Y  # 98.875
    margin = 0.1  # mm tolerance

    blocks = _extract_blocks(board_text, "segment")
    candidates = []  # (uuid, length) pairs matching geometry predicate
    fcu_segs = []     # (uuid, length) for all F.Cu segments (fallback)
    # Diagnostics: (uuid, layer, length, start, end, match_reason)
    all_segs: list[tuple] = []

    for blk in blocks:
        net = _extract_int(blk, "net")
        if net != net_code:
            continue

        start = _extract_xy(blk, "start")
        end = _extract_xy(blk, "end")
        layer = _extract_layer(blk)
        if not start or not end or not layer:
            continue

        uuid_m = re.search(r'\(uuid "([^"]+)"\)', blk)
        if not uuid_m:
            continue
        uuid = uuid_m.group(1)

        dx = abs(start[0] - end[0])
        dy = abs(start[1] - end[1])
        length = (dx**2 + dy**2) ** 0.5

        # Build predicate-match reason for diagnostics
        reasons: list[str] = []
        if layer != "F.Cu":
            reasons.append(f"layer={layer}!=F.Cu")
            all_segs.append((uuid, layer, length, start, end,
                             ", ".join(reasons) or "—"))
            continue

        is_vertical = dx < 0.01
        min_y = min(start[1], end[1])
        max_y = max(start[1], end[1])
        reaches_header = min_y <= header_y_kcd + margin
        reaches_bottom = max_y >= bottom_fan_y_kcd - margin

        # Always show full predicate evaluation (even on match)
        reasons.append(f"vertical={'Y' if is_vertical else 'N'}(dx={dx:.3f})")
        reasons.append(
            f"header={'Y' if reaches_header else 'N'}"
            f"(min_y={min_y:.3f} vs {header_y_kcd + margin:.3f})")
        reasons.append(
            f"bottom={'Y' if reaches_bottom else 'N'}"
            f"(max_y={max_y:.3f} vs {bottom_fan_y_kcd - margin:.3f})")

        matched = is_vertical and reaches_header and reaches_bottom
        tag = "MATCH" if matched else "FAIL"
        match_reason = f"{tag}: {', '.join(reasons)}"
        all_segs.append((uuid, layer, length, start, end, match_reason))

        fcu_segs.append((uuid, length))

        if matched:
            candidates.append((uuid, length))

    # Primary: geometry predicate should match exactly 1
    if len(candidates) == 1:
        return candidates[0][0]

    # Fallback: longest F.Cu segment (always the descent by large margin)
    if fcu_segs:
        fcu_segs.sort(key=lambda x: x[1], reverse=True)
        return fcu_segs[0][0]

    # Total failure: no F.Cu segments on this net.  Print all segments
    # with predicate match reasons for CI debugging.
    import sys
    diag_lines = [
        f"find_descent_segment_uuid FAILED for net {net_code}:",
        f"  geometry candidates: {len(candidates)}",
        f"  F.Cu segments: {len(fcu_segs)}",
        f"  header_y_kcd={header_y_kcd}, bottom_fan_y_kcd={bottom_fan_y_kcd}",
        f"  All segments on net:",
        f"  {'uuid[:12]':14s} {'layer':5s} {'len_mm':>7s} {'start':>22s} {'end':>22s}  reason",
    ]
    for u, lay, l, s, e, reason in all_segs:
        diag_lines.append(
            f"    {u[:12]:14s} {lay:5s} {l:7.3f} "
            f"({s[0]:8.3f},{s[1]:8.3f})→({e[0]:8.3f},{e[1]:8.3f})  {reason}"
        )
    if not all_segs:
        diag_lines.append("    (none)")
    print("\n".join(diag_lines), file=sys.stderr)
    return None


def find_entry_via_uuid(board_text: str, net_code: int) -> str | None:
    """Find the UUID of the entry via (F.Cu→B.Cu bridge) on a net.

    The entry via is the transition point from F.Cu descent to B.Cu
    horizontal routing in the bottom-fan region.  It is identified as
    the via on this net that is farthest from the J5 connector center.

    In Bundle C topology, each channel has exactly 2 vias:
      - Entry via: at the bottom of the F.Cu extension (farther from J5)
      - Exit via-in-pad: at the J5 pad (closer to J5)

    We select by maximum Euclidean distance from J5 center, which is
    geometry-based and doesn't depend on block ordering.

    Returns UUID string, or None if net has no vias.
    """
    from board_geom import BRD_OX, BRD_OY

    # J5 center in KiCad coordinates
    j5_cx_kcd = BRD_OX + 15.0  # from gen_bundle_c_route.J5_CX
    j5_cy_kcd = BRD_OY + 21.5  # from gen_bundle_c_route.J5_CY

    blocks = _extract_blocks(board_text, "via")
    net_vias = []

    for blk in blocks:
        net = _extract_int(blk, "net")
        if net != net_code:
            continue
        at = _extract_xy(blk, "at")
        if not at:
            continue
        uuid_m = re.search(r'\(uuid "([^"]+)"\)', blk)
        if not uuid_m:
            continue
        uuid = uuid_m.group(1)
        dist = ((at[0] - j5_cx_kcd)**2 + (at[1] - j5_cy_kcd)**2) ** 0.5
        net_vias.append((uuid, dist))

    if not net_vias:
        return None

    # Entry via = farthest from J5 center
    net_vias.sort(key=lambda x: x[1], reverse=True)
    return net_vias[0][0]


def sabotage_board_by_uuid(board_path: Path, target_uuid: str) -> Path:
    """Create a board copy with exactly one element removed by UUID.

    Surgical deletion: finds the block containing (uuid "<target_uuid>")
    and removes that entire block.  Returns the path to the sabotaged
    board copy.

    This is the only acceptable sabotage method — it guarantees:
      - Exactly one element is removed (not more).
      - The specific element is identified (not pattern-matched).
      - The deletion is verifiable (UUID is logged).
    """
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="sabotage_")
    sabotaged = Path(tmpdir) / board_path.name

    text = board_path.read_text()

    # Find the block containing this UUID
    uuid_pattern = f'(uuid "{target_uuid}")'
    if uuid_pattern not in text:
        raise ValueError(f"UUID {target_uuid} not found in board")

    # Find the top-level block containing this UUID
    uuid_pos = text.index(uuid_pattern)

    # Walk backwards to find the opening of this block
    # (look for the nearest '    (' at line start before uuid_pos)
    block_start = text.rfind('\n    (', 0, uuid_pos)
    if block_start == -1:
        raise ValueError(f"Cannot find block start for UUID {target_uuid}")
    block_start += 1  # skip the \n

    # Walk forward from block_start to find matching close paren
    depth = 0
    block_end = block_start
    for i in range(block_start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                block_end = i + 1
                break

    # Remove the block (plus trailing newline if present)
    if block_end < len(text) and text[block_end] == '\n':
        block_end += 1

    text = text[:block_start] + text[block_end:]
    sabotaged.write_text(text)
    return sabotaged


def parse_board_net_declarations(board_text: str) -> dict[int, str]:
    """Extract net declarations from board: {net_code: net_name}.

    Parses top-level (net N "name") blocks from the kicad_pcb file.
    Used to verify that CH9–CH22 nets exist in the board netlist
    (catches renamed/removed nets that would silently pass DRC checks).
    """
    net_re = re.compile(r'^\s+\(net\s+(\d+)\s+"([^"]*)"\)', re.MULTILINE)
    return {int(m.group(1)): m.group(2) for m in net_re.finditer(board_text)}


# ═══════════════════════════════════════════════════════════════════════
# MAIN PROOF
# ═══════════════════════════════════════════════════════════════════════

def run_proof(board_path: Path = BOARD_PATH) -> bool:
    """Run full connectivity proof.  Returns True if all gate checks pass."""
    print("═══ Connectivity Proof for Bundle C (CH9–CH22) ═══")
    print(f"Board: {board_path.name}")
    print(f"Snap epsilon: {SNAP_EPS} mm ({SNAP_EPS*1e3:.1f} µm)")
    print(f"Scope: segments + vias + pads (zones/teardrops excluded)")
    print()

    board_text = board_path.read_text()
    parsed = parse_board(board_text)

    n_segs = len(parsed['segments'])
    n_vias = len(parsed['vias'])
    n_pads = sum(len(v) for v in parsed['pads'].values())
    print(f"Parsed: {n_segs} segments, {n_vias} vias, {n_pads} pads "
          f"across {len(parsed['pads'])} nets")

    ch_segments = [s for s in parsed["segments"]
                   if s["net"] in CH_FOR_NET_CODE]
    ch_vias = [v for v in parsed["vias"]
               if v["net"] in CH_FOR_NET_CODE]
    print(f"CH9–CH22 copper: {len(ch_segments)} segments, {len(ch_vias)} vias")
    print()

    gate_failures: list[str] = []
    info_notes: list[str] = []

    for ch in sorted(CH_RANGE):
        net_code = NET_CODE_FOR_CH[ch]
        net_name = f"CH{ch}"
        expected_u1_pin, expected_j5_pin = PAD_TRUTH_TABLE[ch]

        graph, net_segs, net_vias = build_net_graph(net_code, parsed)
        net_pads = parsed["pads"].get(net_code, [])

        u1_pad = find_pad_by_ref_pin(net_pads, "U1", expected_u1_pin)
        j5_pad = find_pad_by_ref_pin(net_pads, "J5", expected_j5_pin)

        if u1_pad is None:
            gate_failures.append(f"{net_name}: U1.{expected_u1_pin} pad MISSING")
            continue
        if j5_pad is None:
            gate_failures.append(f"{net_name}: J5.{expected_j5_pin} pad MISSING")
            continue

        u1_node = find_pad_node(graph, u1_pad["abs_xy"], u1_pad["layers"])
        j5_node = find_pad_node(graph, j5_pad["abs_xy"], j5_pad["layers"])

        if u1_node is None:
            gate_failures.append(
                f"{net_name}: U1.{expected_u1_pin} at "
                f"({u1_pad['abs_xy'][0]:.4f}, {u1_pad['abs_xy'][1]:.4f}) "
                f"not in copper graph"
            )
            continue
        if j5_node is None:
            gate_failures.append(
                f"{net_name}: J5.{expected_j5_pin} at "
                f"({j5_pad['abs_xy'][0]:.4f}, {j5_pad['abs_xy'][1]:.4f}) "
                f"not in copper graph"
            )
            continue

        reachable = bfs_reachable(graph, u1_node)
        connected = j5_node in reachable
        all_nodes = set(graph.keys())
        unreachable = all_nodes - reachable
        n_islands = len(unreachable)

        if not connected:
            gate_failures.append(
                f"{net_name}: U1.{expected_u1_pin} → J5.{expected_j5_pin} "
                f"NOT CONNECTED"
            )
        if n_islands > 0:
            gate_failures.append(
                f"{net_name}: {n_islands} disconnected node(s) — "
                f"{[(_node_xy(n), n[2]) for n in unreachable]}"
            )

        n_s, n_v, n_n = len(net_segs), len(net_vias), len(all_nodes)
        info_notes.append(f"{net_name}: {n_s} segs, {n_v} vias, {n_n} nodes")

        status = "✓" if (connected and n_islands == 0) else "✗"
        detail = " DISCONNECTED" if not connected else (
            f" {n_islands} ISLAND(S)" if n_islands > 0 else "")
        print(
            f"  {net_name} (net {net_code}): "
            f"U1.{expected_u1_pin} → J5.{expected_j5_pin}  "
            f"{n_s} segs, {n_v} vias, {n_n} nodes  "
            f"[{status}{detail}]"
        )

    print()
    if info_notes:
        print("── Informational (not gated) ──")
        for note in info_notes:
            print(f"  {note}")
        print()

    if gate_failures:
        print("══ GATE FAILURES ══")
        for gf in gate_failures:
            print(f"  ✗ {gf}")
        print()
        print("══ FAIL ══")
        return False

    print("══ PASS ══  All 14 channels: correct pads, connected, 0 islands.")
    print(f"  Scope: {len(ch_segments)} segments + {len(ch_vias)} vias "
          f"(zones/teardrops not in scope)")
    return True


# ═══════════════════════════════════════════════════════════════════════
# NETCLASS / ZONE / STACKUP PARSERS (Step C — Electrical Sanity)
# ═══════════════════════════════════════════════════════════════════════

def parse_netclasses(board_text: str) -> dict[str, dict]:
    """Parse net_class blocks from the board file.

    Returns {class_name: {
        "description": str,
        "clearance": float,
        "trace_width": float,
        "nets": [str, ...],
    }}.
    """
    nc_re = re.compile(
        r'^\s+\(net_class\s+"([^"]+)"\s+"([^"]*)"(.*?)\n\s+\)',
        re.MULTILINE | re.DOTALL,
    )
    result: dict[str, dict] = {}
    for m in nc_re.finditer(board_text):
        name = m.group(1)
        desc = m.group(2)
        body = m.group(3)
        clr_m = re.search(r'\(clearance\s+([\d.]+)\)', body)
        tw_m = re.search(r'\(trace_width\s+([\d.]+)\)', body)
        nets = re.findall(r'\(add_net\s+"([^"]+)"\)', body)
        result[name] = {
            "description": desc,
            "clearance": float(clr_m.group(1)) if clr_m else None,
            "trace_width": float(tw_m.group(1)) if tw_m else None,
            "nets": nets,
        }
    return result


def parse_zones(board_text: str) -> list[dict]:
    """Parse zone blocks from the board file.

    Returns [{
        "net": int,
        "net_name": str,
        "layer": str,
        "name": str | None,
        "clearance": float | None,
        "min_thickness": float | None,
    }, ...].
    """
    zone_blocks = _extract_blocks(board_text, "zone")
    zones = []
    for blk in zone_blocks:
        net = _extract_int(blk, "net")
        net_name_m = re.search(r'\(net_name\s+"([^"]+)"\)', blk)
        net_name = net_name_m.group(1) if net_name_m else ""
        layer = _extract_layer(blk)
        name_m = re.search(r'\(name\s+"([^"]+)"\)', blk)
        zone_name = name_m.group(1) if name_m else None
        clr_m = re.search(r'connect_pads\s+\(clearance\s+([\d.]+)\)', blk)
        clearance = float(clr_m.group(1)) if clr_m else None
        min_t_m = re.search(r'\(min_thickness\s+([\d.]+)\)', blk)
        min_t = float(min_t_m.group(1)) if min_t_m else None
        zones.append({
            "net": net,
            "net_name": net_name,
            "layer": layer,
            "name": zone_name,
            "clearance": clearance,
            "min_thickness": min_t,
        })
    return zones


def parse_stackup(board_text: str) -> dict:
    """Parse stackup from the board setup section.

    Returns {
        "total_thickness": float,    # from (general (thickness ...))
        "copper_finish": str | None,
        "tenting": str | None,       # e.g. "front back"
        "layers": [{
            "name": str,
            "type": str,
            "thickness": float | None,
            "material": str | None,
        }, ...],
    }.
    """
    # Total thickness
    thick_m = re.search(r'\(general\s*\n\s+\(thickness\s+([\d.]+)\)', board_text)
    total = float(thick_m.group(1)) if thick_m else None

    # Copper finish
    finish_m = re.search(r'\(copper_finish\s+"([^"]+)"\)', board_text)
    copper_finish = finish_m.group(1) if finish_m else None

    # Tenting
    tent_m = re.search(r'\(tenting\s+([^)]+)\)', board_text)
    tenting = tent_m.group(1).strip() if tent_m else None

    # Stackup layers — use proper block parsing with paren depth
    layers = []
    # Find the stackup section
    stackup_m = re.search(r'\(stackup\b', board_text)
    if stackup_m:
        # Find the balanced close of the stackup block
        start = stackup_m.start()
        depth = 0
        end = start
        for ci in range(start, len(board_text)):
            if board_text[ci] == '(':
                depth += 1
            elif board_text[ci] == ')':
                depth -= 1
                if depth == 0:
                    end = ci
                    break
        stackup_body = board_text[start:end + 1]

        # Find each (layer ...) block within the stackup
        layer_pat = re.compile(r'\(layer\s+"([^"]+)"')
        for lm in layer_pat.finditer(stackup_body):
            name = lm.group(1)
            # Extract balanced block
            lstart = lm.start()
            ldepth = 0
            lend = lstart
            for ci in range(lstart, len(stackup_body)):
                if stackup_body[ci] == '(':
                    ldepth += 1
                elif stackup_body[ci] == ')':
                    ldepth -= 1
                    if ldepth == 0:
                        lend = ci
                        break
            layer_block = stackup_body[lstart:lend + 1]
            type_m = re.search(r'\(type\s+"([^"]+)"\)', layer_block)
            thick_l = re.search(r'\(thickness\s+([\d.]+)\)', layer_block)
            mat_m = re.search(r'\(material\s+"([^"]+)"\)', layer_block)
            layers.append({
                "name": name,
                "type": type_m.group(1) if type_m else None,
                "thickness": float(thick_l.group(1)) if thick_l else None,
                "material": mat_m.group(1) if mat_m else None,
            })

    return {
        "total_thickness": total,
        "copper_finish": copper_finish,
        "tenting": tenting,
        "layers": layers,
    }


if __name__ == "__main__":
    ok = run_proof()
    sys.exit(0 if ok else 1)
