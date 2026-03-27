#!/usr/bin/env python3
"""Post-fill topology verification using pcbnew API.

Queries the FILLED board to answer three questions:
  1. FB1 pad nets + positions (orientation sanity)
  2. U1 VDD pads: net + whether they sit inside the /VDD_AFE_FILT filled island
  3. In2.Cu zones: net + priority + filled polygon count

Run with KiCad Python against the filled board:
  KICAD_PY fill_zones.py <unfilled> <filled>
  KICAD_PY verify_topology.py <filled>
"""
import sys, os
import pcbnew

FILLED_PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".tmp.filled.kicad_pcb")

board = pcbnew.LoadBoard(FILLED_PCB)

# ═══════════════════════════════════════════════════════════════════════
# CHECK 1: FB1 pad positions + nets
# ═══════════════════════════════════════════════════════════════════════
print("═══ CHECK 1: FB1 pad positions + nets ═══")
fb1 = board.FindFootprintByReference("FB1")
assert fb1, "FB1 not found"
fb1_x = pcbnew.ToMM(fb1.GetX())
fb1_y = pcbnew.ToMM(fb1.GetY())
print(f"  FB1 centre: ({fb1_x:.2f}, {fb1_y:.2f})")

for pad in fb1.Pads():
    px = pcbnew.ToMM(pad.GetX())
    py = pcbnew.ToMM(pad.GetY())
    print(f"  FB1 pad {pad.GetName():>2s}: ({px:.2f}, {py:.2f})  net=\"{pad.GetNetname()}\"")

# Also print U1 centre for distance context
u1 = board.FindFootprintByReference("U1")
u1_x = pcbnew.ToMM(u1.GetX())
u1_y = pcbnew.ToMM(u1.GetY())
print(f"  U1  centre: ({u1_x:.2f}, {u1_y:.2f})")

# ═══════════════════════════════════════════════════════════════════════
# CHECK 2: U1 VDD pads — net + inside /VDD_AFE_FILT filled copper?
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ CHECK 2: U1 VDD pads vs /VDD_AFE_FILT island ═══")

# Find the /VDD_AFE_FILT zone on In2.Cu
vdd_zone = None
in2_layer = board.GetLayerID("In2.Cu")
for z in board.Zones():
    if z.GetFirstLayer() == in2_layer and z.GetNetname() == "/VDD_AFE_FILT":
        vdd_zone = z
        break
assert vdd_zone, "/VDD_AFE_FILT zone on In2.Cu not found"

# Also find the +3V3 zone to verify it does NOT pour into the island
v33_zone = None
for z in board.Zones():
    if z.GetFirstLayer() == in2_layer and z.GetNetname() == "+3V3":
        v33_zone = z
        break

# Get the filled polygon set for the VDD_AFE_FILT zone
# In KiCad 9, GetFilledPolysList returns a SHAPE_POLY_SET
vdd_filled = vdd_zone.GetFilledPolysList(in2_layer)
vdd_poly_count = vdd_filled.OutlineCount() if vdd_filled else 0

# Helper: check if a point (in nm) is inside a SHAPE_POLY_SET
def point_in_polyset(polyset, x_nm, y_nm):
    """Check if point is inside any outline of the polyset."""
    pt = pcbnew.VECTOR2I(int(x_nm), int(y_nm))
    try:
        return polyset.Contains(pt)
    except Exception:
        # Fallback: check if point is within bounding box at least
        bbox = polyset.BBox()
        return (bbox.GetLeft() <= x_nm <= bbox.GetRight() and
                bbox.GetTop() <= y_nm <= bbox.GetBottom())

for pad_name in ["M15", "N15", "N2"]:
    pad = None
    for p in u1.Pads():
        if p.GetName() == pad_name:
            pad = p
            break
    assert pad, f"U1 pad {pad_name} not found"

    px_mm = pcbnew.ToMM(pad.GetX())
    py_mm = pcbnew.ToMM(pad.GetY())
    net = pad.GetNetname()

    # Check containment in filled copper (using nanometer coords)
    inside_vdd = point_in_polyset(vdd_filled, pad.GetX(), pad.GetY()) if vdd_poly_count > 0 else False

    # Check that +3V3 does NOT cover this pad
    inside_33 = False
    if v33_zone:
        v33_filled = v33_zone.GetFilledPolysList(in2_layer)
        if v33_filled and v33_filled.OutlineCount() > 0:
            inside_33 = point_in_polyset(v33_filled, pad.GetX(), pad.GetY())

    status = "✓" if (net == "/VDD_AFE_FILT" and inside_vdd and not inside_33) else "✗"
    print(f"  {status} U1.{pad_name}: ({px_mm:.2f}, {py_mm:.2f})  "
          f"net=\"{net}\"  in_VDD_island={inside_vdd}  in_+3V3={inside_33}")

# ═══════════════════════════════════════════════════════════════════════
# CHECK 3: In2.Cu zones — net + priority + filled polygon count
# ═══════════════════════════════════════════════════════════════════════
print("\n═══ CHECK 3: In2.Cu zones — net + priority + polygon count ═══")
for z in board.Zones():
    layer = z.GetFirstLayer()
    layer_name = board.GetLayerName(layer)
    if layer_name != "In2.Cu":
        continue
    filled = z.GetFilledPolysList(layer)
    poly_count = filled.OutlineCount() if filled else 0
    zone_name = z.GetZoneName()
    net_name = z.GetNetname()
    priority = z.GetAssignedPriority()
    # Bounding box of the filled copper
    if filled and poly_count > 0:
        bbox = filled.BBox()
        bx1 = pcbnew.ToMM(bbox.GetLeft())
        by1 = pcbnew.ToMM(bbox.GetTop())
        bx2 = pcbnew.ToMM(bbox.GetRight())
        by2 = pcbnew.ToMM(bbox.GetBottom())
        bbox_str = f"  bbox=({bx1:.1f},{by1:.1f})→({bx2:.1f},{by2:.1f})"
    else:
        bbox_str = "  NO FILLED COPPER"
    print(f"  Zone \"{zone_name}\": net=\"{net_name}\"  priority={priority}  "
          f"filled_polys={poly_count}{bbox_str}")
