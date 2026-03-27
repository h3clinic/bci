#!/usr/bin/env python3
"""Verify zone/via/net integrity, then fill zones headlessly.

Uses KiCad's pcbnew Python API (requires KiCad's bundled Python).
Run with: /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9 fill_zones.py
"""
import os
import sys

import pcbnew

PCB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "afe-headstage-real.kicad_pcb")

in_pcb = sys.argv[1] if len(sys.argv) > 1 else PCB_PATH
out_pcb = sys.argv[2] if len(sys.argv) > 2 else in_pcb

board = pcbnew.LoadBoard(in_pcb)

# ── Step 1: Verify net table ──
print("── Net table verification ──")
netinfo = board.GetNetInfo()

# Look up key nets by name instead of hardcoded number
for expected_name in ["GND", "+3V3", "/VDD_AFE_FILT", "/REF_ELEC"]:
    ni = netinfo.GetNetItem(expected_name)
    code = ni.GetNetCode() if ni else -1
    found = ni.GetNetname() if ni else "(not found)"
    assert found == expected_name, f"FAIL: lookup for \"{expected_name}\" returned \"{found}\""
    print(f"  ✓ \"{expected_name}\" = net {code}")
# ── Step 2: Verify zones ──
print("\n── Zone verification ──")
zones = board.Zones()
print(f"  Zone count: {len(zones)}")
expected_zones = {
    "F.Cu":   {"GND"},
    "In1.Cu": {"GND"},
    "In2.Cu": {"+3V3", "/VDD_AFE_FILT"},
    "B.Cu":   {"GND"},
}
found_zones = {}
for z in zones:
    layer_name = board.GetLayerName(z.GetFirstLayer())
    net_code = z.GetNetCode()
    net_name = z.GetNetname()
    zone_name = z.GetZoneName()
    priority = z.GetAssignedPriority()
    print(f"  Zone \"{zone_name}\": layer={layer_name}, net={net_code} \"{net_name}\", priority={priority}")
    found_zones.setdefault(layer_name, set()).add(net_name)

for layer, expected_nets in expected_zones.items():
    actual = found_zones.get(layer, set())
    for en in expected_nets:
        assert en in actual, f"FAIL: expected zone \"{en}\" on {layer}, found {actual}"
print("  ✓ All expected zones verified")

# ── Step 3: Verify vias ──
print("\n── Via verification ──")
vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
print(f"  Via count: {len(vias)}")

for v in vias:
    x = pcbnew.ToMM(v.GetX())
    y = pcbnew.ToMM(v.GetY())
    net_code = v.GetNetCode()
    net_name = v.GetNetname()
    drill = pcbnew.ToMM(v.GetDrillValue())

    # Check which layers this via spans
    top_layer = v.TopLayer()
    bot_layer = v.BottomLayer()
    top_name = board.GetLayerName(top_layer)
    bot_name = board.GetLayerName(bot_layer)

    # Does it span In1.Cu (layer 1)?
    spans_in1 = (top_layer <= 1 and bot_layer >= 1)
    # Does it span In2.Cu (layer 2)?
    spans_in2 = (top_layer <= 2 and bot_layer >= 2)

    status = "✓" if (spans_in1 and spans_in2) else "⚠"
    print(f"  {status} Via ({x:.2f}, {y:.2f}) net={net_code} \"{net_name}\" "
          f"drill={drill:.1f}mm layers={top_name}..{bot_name} "
          f"spans_In1={spans_in1} spans_In2={spans_in2}")

    if not spans_in1:
        print("    WARNING: via does not span In1.Cu (GND plane)")


# ── Step 4: Fill all zones ──
print("\n── Zone fill ──")
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
print("  Zones filled")

# ── Step 4.5: Area and priority guards ──
EXPECTED_VDD_AREA_MIN = 40.0
EXPECTED_VDD_AREA_MAX = 55.0
EXPECTED_VDD_PRIORITY = 1
EXPECTED_GND_PRIORITY = 0

vdd_area = None
vdd_priority = None
gnd_fc_priority = None
gnd_bc_priority = None

for z in board.Zones():
    net = z.GetNetname()
    layer = board.GetLayerName(z.GetFirstLayer())
    priority = z.GetAssignedPriority()
    if net == "/VDD_AFE_FILT" and layer == "In2.Cu":
        # Area: pcbnew internal units are nm, so Area() returns nm².
        # Convert to mm²: 1mm = 1e6 nm → 1mm² = 1e12 nm².
        polys = z.GetFilledPolysList(z.GetFirstLayer())
        area = polys.Area() / 1e12  # convert from nm^2 to mm^2
        vdd_area = area
        vdd_priority = priority
        print(f"  VDD_AFE_FILT area: {area:.2f} mm^2, priority: {priority}")
        if not (EXPECTED_VDD_AREA_MIN <= area <= EXPECTED_VDD_AREA_MAX):
            raise SystemExit(f"FAIL: VDD_AFE_FILT area {area:.2f} mm^2 out of range [{EXPECTED_VDD_AREA_MIN}, {EXPECTED_VDD_AREA_MAX}]")
        if priority != EXPECTED_VDD_PRIORITY:
            raise SystemExit(f"FAIL: VDD_AFE_FILT priority {priority} != {EXPECTED_VDD_PRIORITY}")
    if net == "GND" and layer == "F.Cu":
        gnd_fc_priority = priority
        print(f"  GND_F.Cu priority: {priority}")
        if priority != EXPECTED_GND_PRIORITY:
            raise SystemExit(f"FAIL: GND_F.Cu priority {priority} != {EXPECTED_GND_PRIORITY}")
    if net == "GND" and layer == "B.Cu":
        gnd_bc_priority = priority
        print(f"  GND_B.Cu priority: {priority}")
        if priority != EXPECTED_GND_PRIORITY:
            raise SystemExit(f"FAIL: GND_B.Cu priority {priority} != {EXPECTED_GND_PRIORITY}")

# ── Step 5: Save ──
pcbnew.SaveBoard(out_pcb, board)
print(f"\n  Wrote: {out_pcb} ({os.path.getsize(out_pcb):,} bytes)")
