#!/usr/bin/env python3
"""Inject Bundle C electrode routes + ELECTRODE netclass into the board.

Reads the clean board produced by gen_pcb_v1.py, generates route segments
and vias from gen_bundle_c_route.py, converts to KiCad 9 multi-line format
with fresh UUIDs, and inserts them into the board file.

Also injects the ELECTRODE netclass block (0.20 mm clearance — compatible
with J5 Omnetics 0.635 mm pitch / 0.254 mm pad-to-pad gap).

Phase 5 bottom fan adds:
  - B.Cu Z-route segments (ALL 14 channels)
  - Vias (0.2 mm drill / 0.4 mm pad) for F.Cu ↔ B.Cu transitions
  - Via-in-pad at J5 pads (exit vias)
"""

import re
import uuid
import sys

# ── Generate routes ─────────────────────────────────────────────────────
from gen_bundle_c_route import generate as gen_routes

raw_output = gen_routes()

# Parse single-line segments from generator output (F.Cu and B.Cu)
SEG_RE = re.compile(
    r'\(segment\s+'
    r'\(start\s+([\d.]+)\s+([\d.]+)\)\s+'
    r'\(end\s+([\d.]+)\s+([\d.]+)\)\s+'
    r'\(width\s+([\d.]+)\)\s+'
    r'\(layer\s+"([^"]+)"\)\s+'
    r'\(net\s+(\d+)\)\)'
)

# Parse single-line vias from generator output
VIA_RE = re.compile(
    r'\(via\s+'
    r'\(at\s+([\d.]+)\s+([\d.]+)\)\s+'
    r'\(size\s+([\d.]+)\)\s+'
    r'\(drill\s+([\d.]+)\)\s+'
    r'\(layers\s+"([^"]+)"\s+"([^"]+)"\)\s+'
    r'\(net\s+(\d+)\)\)'
)

segments = []
for m in SEG_RE.finditer(raw_output):
    x1, y1, x2, y2, w, layer, net = m.groups()
    segments.append({
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "w": w, "layer": layer, "net": int(net),
    })

vias = []
for m in VIA_RE.finditer(raw_output):
    x, y, size, drill, layer1, layer2, net = m.groups()
    vias.append({
        "x": x, "y": y, "size": size, "drill": drill,
        "layer1": layer1, "layer2": layer2, "net": int(net),
    })

print(f"Parsed {len(segments)} segments and {len(vias)} vias from generator")

# Phase 1-4: 56 F.Cu segments (4 per channel × 14)
# Phase 5: 14 F.Cu extensions + 28 B.Cu L-route segments = 42 phase-5 segments
# Total: 56 + 42 = 98 segments
# Vias: 28 (14 channels × 2: entry via + exit via-in-pad)
EXPECTED_SEGS = 98
EXPECTED_VIAS = 28
assert len(segments) == EXPECTED_SEGS, f"Expected {EXPECTED_SEGS} segments, got {len(segments)}"
assert len(vias) == EXPECTED_VIAS, f"Expected {EXPECTED_VIAS} vias, got {len(vias)}"

# ── Read clean board ────────────────────────────────────────────────────
BOARD_PATH = "afe-headstage-v1.kicad_pcb"
with open(BOARD_PATH) as f:
    board = f.read()

# ── Build ELECTRODE netclass block ──────────────────────────────────────
# Clearance 0.20 mm — physically compatible with J5 0.635 mm pitch pads
ELECTRODE_NETS = [f"CH{n}" for n in range(9, 23)]  # CH9..CH22

netclass_lines = [
    '    (net_class "ELECTRODE" "Bundle C electrode nets"',
    '        (clearance 0.2)',
    '        (trace_width 0.15)',
]
for net in ELECTRODE_NETS:
    netclass_lines.append(f'        (add_net "{net}")')
netclass_lines.append('    )')

netclass_block = "\n".join(netclass_lines)

# ── Build route blocks (KiCad 9 multi-line format) ─────────────────────
route_blocks = []

for s in segments:
    uid = str(uuid.uuid4())
    block = (
        f'    (segment\n'
        f'        (start {s["x1"]} {s["y1"]})\n'
        f'        (end {s["x2"]} {s["y2"]})\n'
        f'        (width {s["w"]})\n'
        f'        (layer "{s["layer"]}")\n'
        f'        (net {s["net"]})\n'
        f'        (uuid "{uid}")\n'
        f'    )'
    )
    route_blocks.append(block)

for v in vias:
    uid = str(uuid.uuid4())
    block = (
        f'    (via\n'
        f'        (at {v["x"]} {v["y"]})\n'
        f'        (size {v["size"]})\n'
        f'        (drill {v["drill"]})\n'
        f'        (layers "{v["layer1"]}" "{v["layer2"]}")\n'
        f'        (net {v["net"]})\n'
        f'        (uuid "{uid}")\n'
        f'    )'
    )
    route_blocks.append(block)

all_routes = "\n".join(route_blocks)

# ── Inject netclass: after last top-level (net ...) line ────────────────
# Top-level net declarations are at exactly 4-space indent, before footprints.
# Match "    (net N "name")" lines — exactly 4 leading spaces.
net_decl_re = re.compile(r'^    \(net \d+ "[^"]*"\)\s*$', re.MULTILINE)
all_net_decls = list(net_decl_re.finditer(board))
assert all_net_decls, "No top-level (net ...) declarations found in board"
last_net_end = all_net_decls[-1].end()

# Insert netclass block after the last net declaration
board = board[:last_net_end] + "\n\n" + netclass_block + "\n" + board[last_net_end:]

# ── Inject routes: before (embedded_fonts ...) ──────────────────────────
marker = "(embedded_fonts no)"
marker_pos = board.index(marker)
# Insert route blocks before the marker, with blank line separation
insertion = "\n" + all_routes + "\n\n  "
board = board[:marker_pos] + insertion + board[marker_pos:]

# ── Write result ────────────────────────────────────────────────────────
with open(BOARD_PATH, "w") as f:
    f.write(board)

# ── Validate ────────────────────────────────────────────────────────────
with open(BOARD_PATH) as f:
    final = f.read()

# Count segments
seg_count = len(re.findall(r'\(segment\b', final))
print(f"Segments in board: {seg_count}")

# Count vias (injected vias only — exclude footprint vias)
via_count = len(re.findall(r'^\s{4}\(via\b', final, re.MULTILINE))
print(f"Injected vias in board: {via_count}")

# Count netclass blocks
nc_count = len(re.findall(r'net_class', final))
print(f"Netclass blocks: {nc_count}")

# Count unique UUIDs
uuids = re.findall(r'\(uuid "([^"]+)"\)', final)
unique_uuids = len(set(uuids))
print(f"Unique UUIDs: {unique_uuids}/{len(uuids)}")

# Parenthesis balance
balance = final.count('(') - final.count(')')
print(f"Paren balance: {balance}")

# Verify clearance
clr_match = re.search(r'net_class "ELECTRODE".*?\(clearance ([\d.]+)\)', final, re.DOTALL)
if clr_match:
    print(f"ELECTRODE clearance: {clr_match.group(1)} mm")
else:
    print("WARNING: ELECTRODE netclass not found!")

# Verify netclass is at top level (not inside a footprint)
nc_pos = final.index('net_class "ELECTRODE"')
depth = 0
for ch in final[:nc_pos]:
    if ch == '(':
        depth += 1
    elif ch == ')':
        depth -= 1
print(f"Netclass nesting depth: {depth} (should be 2 = inside kicad_pcb + own paren)")

# Verify B.Cu segments exist
bcu_count = len(re.findall(r'\(layer "B\.Cu"\)', final))
print(f"B.Cu segments: {bcu_count}")

if balance != 0:
    print("ERROR: Parenthesis imbalance!")
    sys.exit(1)
# Note: seg_count includes EP thermal vias' segments if any, so use >=
if seg_count < EXPECTED_SEGS:
    print(f"ERROR: Expected at least {EXPECTED_SEGS} segments, got {seg_count}")
    sys.exit(1)
if via_count < EXPECTED_VIAS:
    print(f"ERROR: Expected at least {EXPECTED_VIAS} vias, got {via_count}")
    sys.exit(1)
if depth != 2:
    print(f"ERROR: Netclass at wrong nesting depth {depth}")
    sys.exit(1)

print(f"\n✓ Injection complete — {seg_count} segments + {via_count} vias — board is ready for DRC")
