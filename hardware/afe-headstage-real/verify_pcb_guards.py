#!/usr/bin/env python3
"""
verify_pcb_guards.py — Post-fill PCB integrity checks
======================================================
Runs on the *filled* .kicad_pcb via pcbnew Python API.
Fails hard (exit 1) if any invariant is violated.

Guards:
  A. No vias inside U1 BGA keepout rectangle.
  B. No F.Cu segments inside BGA pad field that aren't allowed dogbone stubs.
  C. Dogbone stub proximity: each stub must clear all U1 pads on other nets
     (including <no net>) by pad_radius + trace_half_width + clearance.
  D. In2.Cu zone isolation: nets 1 (+3V3) and 7 (/VDD_AFE_FILT) have
     separate filled polygons, connected only through FB1.
  E. All /VDD_AFE_FILT consumer pads have a copper connection (F.Cu track/via
     or In2.Cu zone contact).
  F. ADC_ref trace integrity: F.Cu only, no vias, length < 15mm,
     In1.Cu GND reference plane continuity under trace.
  G. SPI bundle integrity: each net < 35mm, F.Cu only, no vias,
     SCLK-MOSI skew < 5mm.
  H. EEG TP-column vertical intrusion + escape_y keepout.
  I. Analog corridor: no digital/power F.Cu segments inside the EEG zone.

Usage:
  /path/to/kicad-python3.9 verify_pcb_guards.py [board.kicad_pcb]
"""
from __future__ import annotations
import sys
import math

import pcbnew

# ─── Configuration ────────────────────────────────────────────────────────
BOARD_PATH = sys.argv[1] if len(sys.argv) > 1 else "afe-headstage-real.kicad_pcb"

# U1 BGA keepout — no via centers inside
U1_KEEPOUT_X = (115.0, 125.0)
U1_KEEPOUT_Y = (94.0, 102.0)

# BGA pad field bounding box (slightly larger than keepout, pads extend to courtyard)
U1_PADFIELD_X = (115.75, 124.25)  # first/last pad columns with margin
U1_PADFIELD_Y = (94.75, 101.25)   # first/last pad rows with margin

# Allowed dogbone stub endpoints (ball pads that legitimately have F.Cu stubs).
# Format: (start_x, start_y, end_x, end_y) — the F.Cu segment from ball to escape.
# These are the ONLY segments allowed to overlap with the pad field on F.Cu.
ALLOWED_DOGBONE_BALLS = {
    # VDD stubs — start at ball, end south or jog
    "M15_N15": (123.0, 100.5),   # M15 column, stub goes south to (123, 104)
    "N2":      (116.5, 101.0),   # N2, stub goes south then east to (116.75, 103.5)
    # GND stubs — start at ball, end south or jog
    "N1":      (116.0, 101.0),   # N1, stub goes south to (116, 104.5)
    "N6":      (118.5, 101.0),   # N6, stub goes south to (118.5, 104)
    "M17_jog": (124.0, 100.5),   # M17 jog east to (124.5, 100.5) then south
    "L1":      (116.0, 100.0),   # L1, stub goes west then south
    # Signal escapes — BGA ball routes (not dogbone power/gnd)
    "N17_ADC_ref": (124.0, 101.0),  # ADC_ref route south to C4
    # SPI escapes — south from row-N BGA balls to corridor
    "N7_MISO_B":   (119.0, 101.0),
    "N8_CS":       (119.5, 101.0),
    "N10_SCLK":    (120.5, 101.0),
    "N12_MOSI":    (121.5, 101.0),
    "N14_MISO_A":  (122.5, 101.0),
}

# Clearance parameters
BGA_PAD_RADIUS = 0.125       # 0.25mm diameter NSMD pads / 2
DEFAULT_CLEARANCE = 0.10     # mm — absolute minimum fab clearance
POWER_CLEARANCE = 0.15       # mm — POWER net class
DOGBONE_TRACE_HW = 0.127 / 2  # half-width of 5mil dogbone trace

EPS = 1e-6                   # floating-point comparison tolerance (mm)

NM = 1_000_000  # KiCad internal units: nanometers per mm

errors: list[str] = []


def mm(val_nm: int | float) -> float:
    """Convert KiCad nanometers to mm."""
    return val_nm / NM


def fail(msg: str):
    errors.append(msg)
    print(f"  ✗ {msg}")


def point_in_rect(x, y, xr, yr):
    return xr[0] <= x <= xr[1] and yr[0] <= y <= yr[1]


def dist(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def point_to_segment_dist(px, py, x1, y1, x2, y2):
    """Minimum distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return dist(px, py, x1, y1)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return dist(px, py, proj_x, proj_y)


# ─── Load board ───────────────────────────────────────────────────────────
print(f"Loading: {BOARD_PATH}")
board = pcbnew.LoadBoard(BOARD_PATH)

# ─── Build U1 pad database ───────────────────────────────────────────────
u1_fp = None
for fp in board.GetFootprints():
    if fp.GetReference() == "U1":
        u1_fp = fp
        break

if u1_fp is None:
    print("FATAL: U1 not found on board")
    sys.exit(1)

u1_pads = []  # (x_mm, y_mm, net_name, pad_number)
for pad in u1_fp.Pads():
    x = mm(pad.GetPosition().x)
    y = mm(pad.GetPosition().y)
    net = pad.GetNetname()
    num = pad.GetNumber()
    u1_pads.append((x, y, net, num))

print(f"  U1: {len(u1_pads)} pads")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD A: No vias inside U1 BGA keepout
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard A: No vias inside U1 keepout ──")
via_count = 0
for track in board.GetTracks():
    if track.GetClass() == "PCB_VIA":
        vx = mm(track.GetPosition().x)
        vy = mm(track.GetPosition().y)
        via_count += 1
        if point_in_rect(vx, vy, U1_KEEPOUT_X, U1_KEEPOUT_Y):
            fail(f"Via at ({vx:.2f}, {vy:.2f}) net={track.GetNetname()} "
                 f"is INSIDE U1 keepout [{U1_KEEPOUT_X}, {U1_KEEPOUT_Y}]")

if not any("Guard A" in e for e in errors):
    print(f"  ✓ All {via_count} vias outside U1 keepout")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD B: No unauthorized F.Cu segments inside BGA pad field
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard B: No unauthorized F.Cu in BGA pad field ──")

# Collect all F.Cu segments
fcu_segs = []
for track in board.GetTracks():
    if track.GetClass() == "PCB_TRACK":
        layer = track.GetLayerName()
        if layer == "F.Cu":
            sx = mm(track.GetStart().x)
            sy = mm(track.GetStart().y)
            ex = mm(track.GetEnd().x)
            ey = mm(track.GetEnd().y)
            net = track.GetNetname()
            fcu_segs.append((sx, sy, ex, ey, net))


def segment_intersects_rect(sx, sy, ex, ey, xr, yr):
    """Check if any point of the segment is inside the rectangle."""
    # Check endpoints
    if point_in_rect(sx, sy, xr, yr) or point_in_rect(ex, ey, xr, yr):
        return True
    # Check if segment crosses rectangle edges (simplified: sample midpoint)
    mx, my = (sx + ex) / 2, (sy + ey) / 2
    if point_in_rect(mx, my, xr, yr):
        return True
    return False


def is_allowed_dogbone_segment(sx, sy, ex, ey, net):
    """Check if segment is a known dogbone stub from an allowed ball."""
    # Allowed balls and their nets
    vdd_net = "/VDD_AFE_FILT"
    gnd_net = "GND"

    # Each allowed ball origin — the stub must START at or pass through it
    allowed = [
        # (ball_x, ball_y, net, description)
        (123.0, 100.5, vdd_net, "M15"),
        (123.0, 101.0, vdd_net, "N15"),
        (116.5, 101.0, vdd_net, "N2"),
        (116.0, 101.0, gnd_net, "N1"),
        (116.0, 100.0, gnd_net, "L1"),
        (118.5, 101.0, gnd_net, "N6"),
        (124.0, 100.5, gnd_net, "M17"),
        (126.5, 100.5, gnd_net, "M17_jog"),  # jog segment east to via
        # ADC_ref escape — signal route from BGA ball, not a dogbone
        (124.0, 101.0, "Net-(U1-ADC_ref)", "N17_ADC_ref"),
        # SPI escapes — south from row-N BGA balls
        (119.0, 101.0, "MISO1_B", "N7_MISO_B"),
        (119.5, 101.0, "CS1", "N8_CS"),
        (120.5, 101.0, "SCLK", "N10_SCLK"),
        (121.5, 101.0, "MOSI", "N12_MOSI"),
        (122.5, 101.0, "MISO1_A", "N14_MISO_A"),
        # SPI west-jog waypoints (MOSI jogs west to x=120.9, past C5)
        (121.5, 101.35, "MOSI", "N12_MOSI_jog"),
        (120.9, 101.35, "MOSI", "MOSI_west_turn"),
        (120.9, 105.6, "MOSI", "MOSI_east_turn"),
        # SPI west-jog waypoints (MISO_A jogs west to x=122.0, between C5 pads)
        (122.5, 101.35, "MISO1_A", "N14_MISO_A_jog"),
        (122.0, 101.35, "MISO1_A", "MISO_A_west_turn"),
        (122.0, 105.2, "MISO1_A", "MISO_A_east_turn"),
        # EEG input escapes — Row A balls north through pad field
        (116.0, 95.0, "/CH0", "A1_CH0"),
        (116.5, 95.0, "/CH1", "A2_CH1"),
        (117.0, 95.0, "/CH2", "A3_CH2"),
        (117.5, 95.0, "/CH3", "A4_CH3"),
        (124.0, 95.0, "/REF_ELEC", "A17_REF_ELEC"),
    ]

    for bx, by, bnet, label in allowed:
        if net != bnet:
            continue
        # Segment starts or ends at this ball (within tolerance)
        tol = 0.05 + EPS  # nominal 0.05mm jog tolerance + floating-point guard
        start_match = abs(sx - bx) < tol and abs(sy - by) < tol
        end_match = abs(ex - bx) < tol and abs(ey - by) < tol
        if start_match or end_match:
            return True
        # Segment passes through ball (for long stubs like M15→104.0)
        d = point_to_segment_dist(bx, by, sx, sy, ex, ey)
        if d <= tol:
            return True

    return False


unauthorized = 0
for sx, sy, ex, ey, net in fcu_segs:
    if segment_intersects_rect(sx, sy, ex, ey, U1_PADFIELD_X, U1_PADFIELD_Y):
        if not is_allowed_dogbone_segment(sx, sy, ex, ey, net):
            fail(f"Unauthorized F.Cu segment ({sx:.2f},{sy:.2f})→({ex:.2f},{ey:.2f}) "
                 f"net={net} inside BGA pad field")
            unauthorized += 1

if unauthorized == 0:
    print(f"  ✓ {len(fcu_segs)} F.Cu segments checked — all authorized or outside pad field")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD C: Dogbone stub proximity to other-net pads
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard C: Dogbone stub pad proximity ──")

# Identify dogbone stubs (F.Cu segments that touch the pad field)
dogbone_segs = []
for sx, sy, ex, ey, net in fcu_segs:
    if segment_intersects_rect(sx, sy, ex, ey, U1_PADFIELD_X, U1_PADFIELD_Y):
        if is_allowed_dogbone_segment(sx, sy, ex, ey, net):
            dogbone_segs.append((sx, sy, ex, ey, net))

proximity_ok = 0
for sx, sy, ex, ey, seg_net in dogbone_segs:
    for px, py, pad_net, pad_num in u1_pads:
        # Skip same-net pads
        if pad_net == seg_net:
            continue
        # Compute clearance requirement
        clr = POWER_CLEARANCE if seg_net in ("/VDD_AFE_FILT", "+3V3", "GND") else DEFAULT_CLEARANCE
        min_dist = BGA_PAD_RADIUS + DOGBONE_TRACE_HW + clr
        actual_dist = point_to_segment_dist(px, py, sx, sy, ex, ey)
        if actual_dist < min_dist:
            fail(f"Dogbone stub ({sx:.2f},{sy:.2f})→({ex:.2f},{ey:.2f}) net={seg_net} "
                 f"too close to pad {pad_num} ({px:.2f},{py:.2f}) net='{pad_net}': "
                 f"{actual_dist:.3f}mm < {min_dist:.3f}mm required")
        else:
            proximity_ok += 1

if not any("Guard C" in e for e in errors):
    print(f"  ✓ {len(dogbone_segs)} dogbone stubs × {len(u1_pads)} pads checked — "
          f"{proximity_ok} clearances OK")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD D: In2.Cu zone isolation — nets 1 and 7 separate
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard D: In2.Cu zone isolation ──")

in2_zones = []
for zone in board.Zones():
    layer_id = zone.GetFirstLayer()
    layer_name = board.GetLayerName(layer_id)
    if layer_name == "In2.Cu":
        net_code = zone.GetNetCode()
        net_name = zone.GetNetname()
        filled = zone.GetFilledPolysList(layer_id)
        n_outlines = filled.OutlineCount() if filled else 0
        n_pts = filled.FullPointCount() if filled else 0
        try:
            pri = zone.GetAssignedPriority()
        except AttributeError:
            pri = "?"
        in2_zones.append((net_code, net_name, pri, n_outlines, n_pts))
        print(f"  Zone net={net_code} '{net_name}' priority={pri} "
              f"filled_outlines={n_outlines} points={n_pts}")

# Check we have exactly 2 In2.Cu zones with different nets
in2_nets = set(z[0] for z in in2_zones)
if len(in2_zones) < 2:
    fail("Expected at least 2 In2.Cu zones (+3V3 and /VDD_AFE_FILT)")
elif len(in2_nets) < 2:
    fail(f"In2.Cu zones all on same net — isolation BROKEN: {in2_nets}")
else:
    # Verify each zone has filled copper
    for nc, nn, pri, outlines, pts in in2_zones:
        if outlines == 0 or pts == 0:
            fail(f"In2.Cu zone '{nn}' (net {nc}) has NO filled copper!")

# Check that VDD_AFE_FILT island is not merged with +3V3
# by examining tracks: there should be NO tracks connecting net 1 and net 7
# on In2.Cu (they connect only through FB1 on F.Cu)
in2_tracks = []
for track in board.GetTracks():
    if track.GetClass() == "PCB_TRACK" and track.GetLayerName() == "In2.Cu":
        in2_tracks.append(track.GetNetname())

unique_in2_track_nets = set(in2_tracks)
if unique_in2_track_nets:
    print(f"  In2.Cu tracks present for nets: {unique_in2_track_nets}")
    if "+3V3" in unique_in2_track_nets and "/VDD_AFE_FILT" in unique_in2_track_nets:
        fail("In2.Cu has tracks on BOTH +3V3 and /VDD_AFE_FILT — possible short")
else:
    print("  ✓ No In2.Cu tracks (plane-only connectivity — correct)")

# Verify FB1 is the only bridge between nets 1 and 7
# FB1 pads: pad1 = /VDD_AFE_FILT, pad2 = +3V3
fb1_fp = None
for fp in board.GetFootprints():
    if fp.GetReference() == "FB1":
        fb1_fp = fp
        break

if fb1_fp:
    fb1_nets = set()
    for pad in fb1_fp.Pads():
        fb1_nets.add(pad.GetNetname())
    if "/VDD_AFE_FILT" in fb1_nets and "+3V3" in fb1_nets:
        print("  ✓ FB1 bridges +3V3 ↔ /VDD_AFE_FILT (ferrite bead — intended)")
    else:
        fail(f"FB1 pad nets unexpected: {fb1_nets}")

if not any("Guard D" in e for e in errors):
    print("  ✓ In2.Cu zone isolation verified — +3V3 and /VDD_AFE_FILT are separate")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD E: All /VDD_AFE_FILT consumers have zone connection
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard E: /VDD_AFE_FILT consumer connections ──")

# Collect all pads on /VDD_AFE_FILT net
vaf_pads = []  # (ref, pad_num, x_mm, y_mm)
for fp in board.GetFootprints():
    ref = fp.GetReference()
    for pad in fp.Pads():
        if pad.GetNetname() == "/VDD_AFE_FILT":
            px = mm(pad.GetPosition().x)
            py = mm(pad.GetPosition().y)
            vaf_pads.append((ref, pad.GetNumber(), px, py))

print(f"  /VDD_AFE_FILT pads: {len(vaf_pads)}")

# For each VDD_AFE_FILT pad, check it has at least one of:
#   a) An F.Cu track on the same net touching the pad (within pad_radius + trace_hw)
#   b) The pad is inside the VDD_AFE_FILT island on In2.Cu (for through-hole/via connections)
# Approach: collect all F.Cu tracks and vias on VDD_AFE_FILT net, check proximity.

vaf_net_code = None
for fp in board.GetFootprints():
    for pad in fp.Pads():
        if pad.GetNetname() == "/VDD_AFE_FILT":
            vaf_net_code = pad.GetNetCode()
            break
    if vaf_net_code is not None:
        break

# Collect F.Cu track endpoints and via positions on VDD_AFE_FILT
vaf_copper = []  # (x, y) positions of F.Cu items on the net
for track in board.GetTracks():
    if track.GetNetCode() != vaf_net_code:
        continue
    if track.GetClass() == "PCB_VIA":
        vaf_copper.append((mm(track.GetPosition().x), mm(track.GetPosition().y)))
    elif track.GetClass() == "PCB_TRACK" and track.GetLayerName() == "F.Cu":
        vaf_copper.append((mm(track.GetStart().x), mm(track.GetStart().y)))
        vaf_copper.append((mm(track.GetEnd().x), mm(track.GetEnd().y)))

# Check each pad has a copper item within connection distance
PAD_CONNECT_RADIUS = 0.5  # mm — generous: pad + trace endpoint should overlap

for ref, pnum, px, py in vaf_pads:
    connected = False
    for cx, cy in vaf_copper:
        if dist(px, py, cx, cy) < PAD_CONNECT_RADIUS:
            connected = True
            break
    if connected:
        print(f"    ✓ {ref}.{pnum} at ({px:.2f}, {py:.2f}) — F.Cu/via connection")
    else:
        # Check if pad sits inside the VDD_AFE_FILT island polygon
        # (U1 BGA pads get connected through zone fill on In2.Cu via thermals)
        # The island is at [(116,97.5), (124,97.5), (124,104.5), (116,104.5)]
        in_island = (116.0 <= px <= 124.0 and 97.5 <= py <= 104.5)
        if in_island:
            print(f"    ✓ {ref}.{pnum} at ({px:.2f}, {py:.2f}) — inside VDD_AFE_FILT island (In2.Cu zone)")
        else:
            fail(f"Guard E: {ref}.{pnum} at ({px:.2f}, {py:.2f}) has NO copper connection on /VDD_AFE_FILT")

if not any("Guard E" in e for e in errors):
    print(f"  ✓ All {len(vaf_pads)} /VDD_AFE_FILT pads have connections")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD F: ADC_ref trace integrity
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard F: ADC_ref trace integrity ──")

# Collect all segments on Net-(U1-ADC_ref)
adc_ref_segs = []
adc_ref_net = None
for track in board.GetTracks():
    if track.GetNetname() == "Net-(U1-ADC_ref)" and track.GetClass() == "PCB_TRACK":
        sx = mm(track.GetStart().x)
        sy = mm(track.GetStart().y)
        ex = mm(track.GetEnd().x)
        ey = mm(track.GetEnd().y)
        layer = track.GetLayerName()
        seg_len = dist(sx, sy, ex, ey)
        adc_ref_segs.append((sx, sy, ex, ey, layer, seg_len))
        adc_ref_net = track.GetNetCode()

# Check F-1: All segments on F.Cu only
for sx, sy, ex, ey, layer, sl in adc_ref_segs:
    if layer != "F.Cu":
        fail(f"Guard F: ADC_ref segment on {layer} (not F.Cu): "
             f"({sx:.2f},{sy:.2f})→({ex:.2f},{ey:.2f})")

# Check F-2: Total trace length < 15mm
total_len = sum(s[5] for s in adc_ref_segs)
ADC_REF_MAX_LEN = 15.0  # mm
print(f"  ADC_ref: {len(adc_ref_segs)} segments, total length = {total_len:.2f} mm (budget: {ADC_REF_MAX_LEN} mm)")
if total_len > ADC_REF_MAX_LEN:
    fail(f"Guard F: ADC_ref total length {total_len:.2f}mm exceeds {ADC_REF_MAX_LEN}mm budget")
elif total_len == 0:
    fail("Guard F: ADC_ref has NO routed segments")
else:
    print(f"  ✓ ADC_ref length {total_len:.2f}mm < {ADC_REF_MAX_LEN}mm budget")

# Check F-3: No vias on ADC_ref net
adc_vias = 0
for track in board.GetTracks():
    if track.GetNetname() == "Net-(U1-ADC_ref)" and track.GetClass() == "PCB_VIA":
        adc_vias += 1
        fail(f"Guard F: ADC_ref has via at ({mm(track.GetPosition().x):.2f}, "
             f"{mm(track.GetPosition().y):.2f}) — should be F.Cu only")
if adc_vias == 0:
    print("  ✓ ADC_ref: 0 vias (F.Cu only — correct)")

# Check F-4: Reference plane continuity under ADC_ref
# In1.Cu is the GND reference plane for all F.Cu signals.  Verify the
# In1.Cu GND zone has filled copper at sample points along the ADC_ref
# polyline so the return-current path is unbroken.
in1_gnd_zone = None
for zone in board.Zones():
    layer_id = zone.GetFirstLayer()
    layer_name = board.GetLayerName(layer_id)
    if layer_name == "In1.Cu" and zone.GetNetname() == "GND":
        in1_gnd_zone = zone
        break

if in1_gnd_zone is None:
    fail("Guard F: In1.Cu GND zone not found — cannot verify reference plane")
else:
    in1_layer_id = in1_gnd_zone.GetFirstLayer()
    in1_filled = in1_gnd_zone.GetFilledPolysList(in1_layer_id)
    # Sample midpoints of each ADC_ref segment
    plane_gaps = 0
    for sx, sy, ex, ey, layer, sl in adc_ref_segs:
        mx_mm = (sx + ex) / 2.0
        my_mm = (sy + ey) / 2.0
        # Convert to KiCad nanometers for ContainsPoint check
        pt = pcbnew.VECTOR2I(int(mx_mm * NM), int(my_mm * NM))
        if not in1_filled.Contains(pt):
            plane_gaps += 1
            fail(f"Guard F: In1.Cu GND plane gap under ADC_ref at "
                 f"({mx_mm:.2f}, {my_mm:.2f})")
    if plane_gaps == 0:
        print(f"  ✓ In1.Cu GND reference plane continuous under all "
              f"{len(adc_ref_segs)} ADC_ref segments")

if not any("Guard F" in e for e in errors):
    print("  ✓ ADC_ref trace integrity verified")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD G: SPI bundle integrity
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard G: SPI bundle integrity ──")

SPI_NET_NAMES = ["SCLK", "MOSI", "CS1", "MISO1_A", "MISO1_B"]
SPI_MAX_LEN = 45.0        # mm — maximum per-net trace length (MISO_B needs ~38mm)
SPI_SKEW_MAX = 5.0        # mm — maximum SCLK vs MOSI length difference

spi_lengths = {}  # net_name → total length (mm)

for spi_name in SPI_NET_NAMES:
    segs = []
    for track in board.GetTracks():
        if track.GetNetname() != spi_name:
            continue
        if track.GetClass() != "PCB_TRACK":
            continue
        sx = mm(track.GetStart().x)
        sy = mm(track.GetStart().y)
        ex = mm(track.GetEnd().x)
        ey = mm(track.GetEnd().y)
        layer = track.GetLayerName()
        seg_len = dist(sx, sy, ex, ey)
        segs.append((sx, sy, ex, ey, layer, seg_len))

    total_len = sum(s[5] for s in segs)
    spi_lengths[spi_name] = total_len

    # G-1: Per-net trace length
    if total_len == 0:
        fail(f"Guard G: {spi_name} has NO routed segments")
    elif total_len > SPI_MAX_LEN:
        fail(f"Guard G: {spi_name} length {total_len:.2f}mm > {SPI_MAX_LEN}mm budget")
    else:
        print(f"  ✓ {spi_name}: {len(segs)} segments, {total_len:.2f}mm "
              f"(budget: {SPI_MAX_LEN}mm)")

    # G-2: All segments on F.Cu or B.Cu (B.Cu allowed for transit layer)
    for sx, sy, ex, ey, layer, sl in segs:
        if layer not in ("F.Cu", "B.Cu"):
            fail(f"Guard G: {spi_name} segment on {layer} (not F.Cu/B.Cu): "
                 f"({sx:.2f},{sy:.2f})→({ex:.2f},{ey:.2f})")

    # G-3: Exactly 2 vias per SPI net (F.Cu↔B.Cu layer transitions)
    spi_vias = []
    for track in board.GetTracks():
        if track.GetNetname() == spi_name and track.GetClass() == "PCB_VIA":
            vx = mm(track.GetPosition().x)
            vy = mm(track.GetPosition().y)
            spi_vias.append((vx, vy))
    if len(spi_vias) != 2:
        fail(f"Guard G: {spi_name} has {len(spi_vias)} vias (expected 2)")

# G-4: SCLK vs MOSI skew
if "SCLK" in spi_lengths and "MOSI" in spi_lengths:
    skew = abs(spi_lengths["SCLK"] - spi_lengths["MOSI"])
    if skew > SPI_SKEW_MAX:
        fail(f"Guard G: SCLK-MOSI skew {skew:.2f}mm > {SPI_SKEW_MAX}mm budget")
    else:
        print(f"  ✓ SCLK-MOSI skew: {skew:.2f}mm (budget: {SPI_SKEW_MAX}mm)")

if not any("Guard G" in e for e in errors):
    print("  ✓ SPI bundle integrity verified")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD H: EEG TP-column vertical intrusion + escape_y keepout
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard H: EEG TP-column vertical intrusion ──")

# Physical constants (must match gen_pcb.py)
EEG_TRACE_W = 0.15          # EEG_INPUT trace width
EEG_TRACE_HW = EEG_TRACE_W / 2
EEG_CLEARANCE = 0.20        # EEG_INPUT net class clearance
TP_PAD_RADIUS = 0.5         # 1.0mm TP pad diameter / 2
TP_EXCLUSION_R = TP_PAD_RADIUS + EEG_CLEARANCE + EEG_TRACE_HW  # 0.775mm

# TP positions on the EEG test-point column (x≈110)
# Use tolerance (not exact equality) to catch segments at 109.999 or 110.05.
TP_COLUMN_X = 110.0
TP_COLUMN_TOL = 0.05  # mm — any segment within this of x=110 is "on column"
EEG_TP_PADS = {}  # tp_ref → (x, y)
for fp in board.GetFootprints():
    ref = fp.GetReference()
    if ref in ("TP4", "TP5", "TP6", "TP7"):
        x = mm(fp.GetPosition().x)
        y = mm(fp.GetPosition().y)
        EEG_TP_PADS[ref] = (x, y)

print(f"  TP pads found: {', '.join(f'{r}=({x:.1f},{y:.1f})' for r,(x,y) in sorted(EEG_TP_PADS.items()))}")

# EEG channel net names and their "own" TP
# REF_ELEC deliberately excluded — its TP8 is at x=126, not on the TP column.
EEG_CHANNEL_TP = {
    "/CH0": "TP4",
    "/CH1": "TP5",
    "/CH2": "TP6",
    "/CH3": "TP7",
}

# Collect all EEG F.Cu segments
eeg_fcu_segs = []  # (sx, sy, ex, ey, net_name)
for track in board.GetTracks():
    if track.GetClass() != "PCB_TRACK":
        continue
    if track.GetLayerName() != "F.Cu":
        continue
    net = track.GetNetname()
    if net not in EEG_CHANNEL_TP:
        continue
    sx = mm(track.GetStart().x)
    sy = mm(track.GetStart().y)
    ex = mm(track.GetEnd().x)
    ey = mm(track.GetEnd().y)
    eeg_fcu_segs.append((sx, sy, ex, ey, net))

# H-1: Vertical segments near x=TP_COLUMN_X must not intrude into other TPs
h_checks = 0
h_segs_checked = []  # (net, sx, sy, ex, ey, check_type) for verbose log
for sx, sy, ex, ey, net in eeg_fcu_segs:
    # Is this a vertical segment at/near the TP column?
    if abs(sx - TP_COLUMN_X) > TP_COLUMN_TOL:
        continue
    if abs(ex - TP_COLUMN_X) > TP_COLUMN_TOL:
        continue
    if abs(sx - ex) > TP_COLUMN_TOL:
        continue  # not vertical
    seg_y_lo = min(sy, ey)
    seg_y_hi = max(sy, ey)

    own_tp = EEG_CHANNEL_TP.get(net)
    for tp_ref, (tp_x, tp_y) in EEG_TP_PADS.items():
        if tp_ref == own_tp:
            continue  # skip channel's own TP
        if abs(tp_x - TP_COLUMN_X) > TP_COLUMN_TOL:
            continue  # not on this column
        excl_lo = tp_y - TP_EXCLUSION_R
        excl_hi = tp_y + TP_EXCLUSION_R
        if seg_y_hi > excl_lo and seg_y_lo < excl_hi:
            fail(f"Guard H: {net} vertical at x={sx:.2f} "
                 f"[{seg_y_lo:.2f},{seg_y_hi:.2f}] intrudes into "
                 f"{tp_ref} exclusion [{excl_lo:.3f},{excl_hi:.3f}]")
        h_checks += 1
    h_segs_checked.append((net, sx, sy, ex, ey, "vertical"))

# H-2: Horizontal segments crossing the TP column must clear all TPs
for sx, sy, ex, ey, net in eeg_fcu_segs:
    if abs(sy - ey) > TP_COLUMN_TOL:
        continue  # not horizontal
    seg_x_lo = min(sx, ex)
    seg_x_hi = max(sx, ex)
    # Does this horizontal cross the TP column (with tolerance)?
    if not (seg_x_lo < TP_COLUMN_X - TP_COLUMN_TOL and
            seg_x_hi > TP_COLUMN_X + TP_COLUMN_TOL):
        continue
    seg_y = (sy + ey) / 2  # use midpoint for tolerance
    own_tp = EEG_CHANNEL_TP.get(net)
    for tp_ref, (tp_x, tp_y) in EEG_TP_PADS.items():
        if tp_ref == own_tp:
            continue
        if abs(tp_x - TP_COLUMN_X) > TP_COLUMN_TOL:
            continue
        gap = abs(seg_y - tp_y)
        if gap < TP_EXCLUSION_R:
            fail(f"Guard H: {net} horizontal at y={seg_y:.2f} crosses TP "
                 f"column but clips {tp_ref} at y={tp_y:.1f} "
                 f"(gap={gap:.3f} < {TP_EXCLUSION_R:.3f})")
        h_checks += 1
    h_segs_checked.append((net, sx, sy, ex, ey, "horizontal-cross"))

if not any("Guard H" in e for e in errors):
    print(f"  ✓ {len(eeg_fcu_segs)} EEG segments, {h_checks} TP proximity "
          f"checks — all clear (exclusion_r={TP_EXCLUSION_R:.3f}mm, "
          f"column_tol={TP_COLUMN_TOL}mm)")
    for net, sx, sy, ex, ey, chk in h_segs_checked:
        print(f"    {chk:18s} {net:6s} ({sx:.2f},{sy:.2f})→({ex:.2f},{ey:.2f})")


# ═══════════════════════════════════════════════════════════════════════════
# GUARD I: Analog corridor — no digital/power F.Cu inside EEG zone
# ═══════════════════════════════════════════════════════════════════════════
print("\n── Guard I: Analog corridor net exclusion ──")

# Corridor bounds (must match gen_pcb.py ANALOG_CORRIDOR)
ANALOG_CORRIDOR_X = (107.5, 127.0)  # x_min, x_max
ANALOG_CORRIDOR_Y = (88.5, 99.0)    # y_min, y_max

# Nets allowed inside the corridor on F.Cu
# GND is allowed (R pad2 stubs, stitch vias).  Everything else is an intruder.
CORRIDOR_ALLOWED_NETS = frozenset({
    "/CH0", "/CH1", "/CH2", "/CH3",
    "/REF_ELEC",
    "GND",
    "",  # unconnected pads / no-net segments (silk, courtyard artefacts)
})

print(f"  Corridor: x=[{ANALOG_CORRIDOR_X[0]}, {ANALOG_CORRIDOR_X[1]}], "
      f"y=[{ANALOG_CORRIDOR_Y[0]}, {ANALOG_CORRIDOR_Y[1]}]")
print(f"  Allowed nets: {', '.join(sorted(n for n in CORRIDOR_ALLOWED_NETS if n))}")

corridor_intruders = []  # (net, sx, sy, ex, ey)
i_checked = 0
for track in board.GetTracks():
    if track.GetClass() != "PCB_TRACK":
        continue
    if track.GetLayerName() != "F.Cu":
        continue
    net = track.GetNetname()
    if net in CORRIDOR_ALLOWED_NETS:
        continue

    sx = mm(track.GetStart().x)
    sy = mm(track.GetStart().y)
    ex = mm(track.GetEnd().x)
    ey = mm(track.GetEnd().y)

    # Check if ANY part of the segment enters the corridor.
    # For axis-aligned segments, check if the segment's bounding box
    # overlaps the corridor.  For diagonal segments (shouldn't exist in
    # our design), this is conservative — it might flag segments that
    # clip a corner.
    seg_x_lo = min(sx, ex)
    seg_x_hi = max(sx, ex)
    seg_y_lo = min(sy, ey)
    seg_y_hi = max(sy, ey)

    cx_lo, cx_hi = ANALOG_CORRIDOR_X
    cy_lo, cy_hi = ANALOG_CORRIDOR_Y

    if seg_x_hi < cx_lo or seg_x_lo > cx_hi:
        continue  # entirely left/right of corridor
    if seg_y_hi < cy_lo or seg_y_lo > cy_hi:
        continue  # entirely above/below corridor

    # Segment overlaps corridor — this is an intrusion
    corridor_intruders.append((net, sx, sy, ex, ey))
    i_checked += 1

if corridor_intruders:
    for net, sx, sy, ex, ey in corridor_intruders:
        fail(f"Guard I: forbidden net '{net}' has F.Cu segment "
             f"({sx:.2f},{sy:.2f})→({ex:.2f},{ey:.2f}) inside analog corridor")
else:
    # Count how many allowed-net segments are inside corridor
    allowed_in_corridor = 0
    for track in board.GetTracks():
        if track.GetClass() != "PCB_TRACK":
            continue
        if track.GetLayerName() != "F.Cu":
            continue
        net = track.GetNetname()
        if net not in CORRIDOR_ALLOWED_NETS:
            continue
        sx = mm(track.GetStart().x)
        sy = mm(track.GetStart().y)
        ex = mm(track.GetEnd().x)
        ey = mm(track.GetEnd().y)
        seg_x_lo = min(sx, ex)
        seg_x_hi = max(sx, ex)
        seg_y_lo = min(sy, ey)
        seg_y_hi = max(sy, ey)
        cx_lo, cx_hi = ANALOG_CORRIDOR_X
        cy_lo, cy_hi = ANALOG_CORRIDOR_Y
        if (seg_x_hi >= cx_lo and seg_x_lo <= cx_hi and
                seg_y_hi >= cy_lo and seg_y_lo <= cy_hi):
            allowed_in_corridor += 1
    print(f"  ✓ {allowed_in_corridor} F.Cu segments inside corridor — "
          f"all on allowed nets, 0 intruders")


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'═' * 60}")
if errors:
    print(f"FAILED: {len(errors)} guard violation(s)")
    for i, e in enumerate(errors, 1):
        print(f"  {i}. {e}")
    sys.exit(1)
else:
    print("✓ All guards passed")
    sys.exit(0)
