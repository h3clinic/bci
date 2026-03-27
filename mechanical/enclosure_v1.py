#!/usr/bin/env python3
"""
Parametric enclosure for afe-headstage-v1.

Board: 30.0 × 24.0 mm, 1.6 mm thick, 4-layer
Tallest component: Omnetics J5 connector (~2.5 mm unmated, ~4.5 mm mated)
QFN-56 U1: 1.0 mm height

Generates:
  enclosure_bottom.step / .stl   — base tray (PCB sits in ledge)
  enclosure_lid.step / .stl      — snap-fit lid

Usage:
  pip install cadquery
  python mechanical/enclosure_v1.py

  Or run in CQ-editor for live preview.

Design decisions:
  - FDM-optimized: 1.2 mm walls (3 perimeters at 0.4 mm nozzle)
  - 0.4 mm clearance around PCB edges (FDM tolerance)
  - PCB sits on a 1.0 mm internal ledge
  - Connector cutout on one short edge (J5 side)
  - No screws: friction fit + optional snap tabs
  - Cable strain relief slot
"""

from __future__ import annotations

try:
    import cadquery as cq
    from cadquery import exporters
    HAS_CQ = True
except ImportError:
    HAS_CQ = False
    print("WARNING: cadquery not installed. Run: pip install cadquery")
    print("Generating dimensions report only.\n")

# ═══════════════════════════════════════════════════════════════════════
# PARAMETERS — edit these to tune the enclosure
# ═══════════════════════════════════════════════════════════════════════

# Board dimensions (from KiCad Edge.Cuts)
BOARD_X = 30.0       # mm, length
BOARD_Y = 24.0       # mm, width
BOARD_Z = 1.6        # mm, PCB thickness

# Component clearance heights (above board top surface)
COMP_TOP_Z = 3.0     # mm — tallest top-side component (J5 unmated ~2.5, add margin)
COMP_BOT_Z = 0.5     # mm — bottom side clearance (solder joints, thermal vias)

# Enclosure parameters
WALL = 1.2            # mm — wall thickness (3 perimeters × 0.4mm nozzle)
CLEARANCE = 0.4       # mm — gap between PCB edge and inner wall (FDM tolerance)
LEDGE_W = 1.0         # mm — internal ledge width that PCB sits on
LEDGE_H = 1.0         # mm — ledge height (from bottom floor to PCB bottom surface)
FLOOR = 1.2           # mm — bottom floor thickness
LID_Z = 1.2           # mm — lid thickness (flat top)
CORNER_R = 1.5        # mm — external corner radius
SNAP_DEPTH = 0.4      # mm — snap-fit tab depth
SNAP_WIDTH = 4.0      # mm — snap-fit tab width
SNAP_HEIGHT = 1.0     # mm — snap-fit tab height

# Connector cutout (J5 Omnetics, on the +X short edge of the board)
# J5 is centered on one short edge. Cutout must clear the connector body + cable.
CONN_WIDTH = 16.0     # mm — cutout width (connector body ~14mm + margin)
CONN_HEIGHT = 5.0     # mm — cutout height from PCB bottom to top of connector
CONN_OFFSET_Z = 0.0   # mm — offset from PCB top surface to bottom of cutout

# ═══════════════════════════════════════════════════════════════════════
# DERIVED DIMENSIONS
# ═══════════════════════════════════════════════════════════════════════

# Internal cavity
CAVITY_X = BOARD_X + 2 * CLEARANCE
CAVITY_Y = BOARD_Y + 2 * CLEARANCE

# External dimensions
EXT_X = CAVITY_X + 2 * WALL
EXT_Y = CAVITY_Y + 2 * WALL

# Bottom tray: floor + ledge + board + top clearance
TRAY_INT_Z = LEDGE_H + COMP_BOT_Z + BOARD_Z + COMP_TOP_Z
TRAY_EXT_Z = FLOOR + TRAY_INT_Z

# Lid: just a flat cap with small lip that fits inside the tray
LID_LIP = 2.0        # mm — lip depth that inserts into tray


def print_dimensions():
    """Print a summary of all enclosure dimensions."""
    print("=" * 50)
    print("AFE Headstage v1 Enclosure — Dimensions")
    print("=" * 50)
    print(f"Board:         {BOARD_X} × {BOARD_Y} × {BOARD_Z} mm")
    print(f"Internal:      {CAVITY_X:.1f} × {CAVITY_Y:.1f} × {TRAY_INT_Z:.1f} mm")
    print(f"External:      {EXT_X:.1f} × {EXT_Y:.1f} × {TRAY_EXT_Z:.1f} mm")
    print(f"Wall:          {WALL} mm")
    print(f"Clearance:     {CLEARANCE} mm per side")
    print(f"Ledge:         {LEDGE_W} mm wide, {LEDGE_H} mm high")
    print(f"Floor:         {FLOOR} mm")
    print(f"Lid:           {EXT_X:.1f} × {EXT_Y:.1f} × {LID_Z} mm + {LID_LIP} mm lip")
    print(f"Connector cutout: {CONN_WIDTH} × {CONN_HEIGHT} mm on +X face")
    print(f"Total height (closed): {TRAY_EXT_Z + LID_Z:.1f} mm")
    print()
    print("Print settings (FDM):")
    print("  Material:    PLA or PETG")
    print("  Layer height: 0.2 mm")
    print("  Infill:      20%")
    print("  Supports:    not needed (designed for supportless)")
    print("  Orientation: print tray upside-down (open face up)")
    print()


def make_bottom_tray() -> "cq.Workplane":
    """Generate the bottom tray / base of the enclosure."""

    # Outer shell
    outer = (
        cq.Workplane("XY")
        .box(EXT_X, EXT_Y, TRAY_EXT_Z, centered=(True, True, False))
        .edges("|Z")
        .fillet(CORNER_R)
    )

    # Hollow out the cavity (from the top, leaving floor)
    inner = (
        cq.Workplane("XY")
        .workplane(offset=FLOOR)
        .box(CAVITY_X, CAVITY_Y, TRAY_INT_Z + 1, centered=(True, True, False))
    )
    tray = outer - inner

    # Add PCB ledge (inward step around the cavity perimeter)
    ledge_outer_x = CAVITY_X
    ledge_outer_y = CAVITY_Y
    ledge_inner_x = CAVITY_X - 2 * LEDGE_W
    ledge_inner_y = CAVITY_Y - 2 * LEDGE_W

    ledge = (
        cq.Workplane("XY")
        .workplane(offset=FLOOR)
        .box(ledge_outer_x, ledge_outer_y, LEDGE_H, centered=(True, True, False))
    )
    ledge_hole = (
        cq.Workplane("XY")
        .workplane(offset=FLOOR)
        .box(ledge_inner_x, ledge_inner_y, LEDGE_H + 1, centered=(True, True, False))
    )
    ledge = ledge - ledge_hole
    tray = tray + ledge

    # Connector cutout on +X face
    # The cutout goes through the wall on the +X side
    conn_z_start = FLOOR + LEDGE_H + COMP_BOT_Z + CONN_OFFSET_Z
    cutout = (
        cq.Workplane("XY")
        .workplane(offset=conn_z_start)
        .center(EXT_X / 2, 0)  # move to +X wall center
        .box(WALL + 2, CONN_WIDTH, CONN_HEIGHT, centered=(True, True, False))
    )
    tray = tray - cutout

    return tray


def make_lid() -> "cq.Workplane":
    """Generate the lid with insertion lip."""

    # Main lid plate
    lid = (
        cq.Workplane("XY")
        .box(EXT_X, EXT_Y, LID_Z, centered=(True, True, False))
        .edges("|Z")
        .fillet(CORNER_R)
    )

    # Insertion lip (fits inside the tray opening)
    lip_x = CAVITY_X - 0.3  # slight clearance for insertion
    lip_y = CAVITY_Y - 0.3
    lip = (
        cq.Workplane("XY")
        .workplane(offset=-LID_LIP)
        .box(lip_x, lip_y, LID_LIP, centered=(True, True, False))
    )
    # Hollow lip to save material
    lip_inner = (
        cq.Workplane("XY")
        .workplane(offset=-LID_LIP)
        .box(lip_x - 2 * WALL, lip_y - 2 * WALL, LID_LIP + 1, centered=(True, True, False))
    )
    lip = lip - lip_inner

    lid = lid + lip

    return lid


def export_all(output_dir: str = "mechanical/out"):
    """Generate and export both parts."""
    import os
    os.makedirs(output_dir, exist_ok=True)

    print("Generating bottom tray...")
    tray = make_bottom_tray()
    exporters.export(tray, f"{output_dir}/enclosure_bottom.step")
    exporters.export(tray, f"{output_dir}/enclosure_bottom.stl")
    print(f"  → {output_dir}/enclosure_bottom.step")
    print(f"  → {output_dir}/enclosure_bottom.stl")

    print("Generating lid...")
    lid = make_lid()
    exporters.export(lid, f"{output_dir}/enclosure_lid.step")
    exporters.export(lid, f"{output_dir}/enclosure_lid.stl")
    print(f"  → {output_dir}/enclosure_lid.step")
    print(f"  → {output_dir}/enclosure_lid.stl")

    print("\nDone. Send STEP files to factory, STL to slicer.")


# ═══════════════════════════════════════════════════════════════════════
# PRINT NOTES (for 3D print shop)
# ═══════════════════════════════════════════════════════════════════════

PRINT_NOTES = """\
PRINT NOTES — AFE Headstage v1 Enclosure
=========================================

Parts: 2 (bottom tray + lid)
Units: mm
Material: PLA or PETG (PETG preferred for temperature stability)
Tolerance: ±0.2 mm on all dimensions
Layer height: 0.20 mm
Infill: 20%

Critical dimensions:
  - Internal cavity: {cx:.1f} × {cy:.1f} mm (must fit {bx} × {by} mm PCB)
  - Connector cutout: {cw} × {ch} mm (must clear Omnetics A79024 + cable)
  - Ledge width: {lw} mm (PCB support shelf)
  - Lid lip: fits inside tray opening with 0.15 mm clearance per side

Orientation:
  - Bottom tray: print with open face UP (no supports needed)
  - Lid: print flat (bottom face down, lip pointing up)

Supports: NOT required for either part.

Quantity: 3 of each (1 good + 2 spare)

Notes:
  - Do NOT scale. Print at 1:1.
  - Connector cutout on one short edge — do not block.
  - If tolerance is poor (>0.3mm), increase CLEARANCE parameter in script.
""".format(
    cx=CAVITY_X, cy=CAVITY_Y,
    bx=BOARD_X, by=BOARD_Y,
    cw=CONN_WIDTH, ch=CONN_HEIGHT,
    lw=LEDGE_W,
)


def write_print_notes(output_dir: str = "mechanical/out"):
    """Write the print notes file."""
    import os
    os.makedirs(output_dir, exist_ok=True)
    path = f"{output_dir}/PRINT_NOTES.txt"
    with open(path, "w") as f:
        f.write(PRINT_NOTES)
    print(f"Print notes → {path}")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print_dimensions()
    write_print_notes()

    if HAS_CQ:
        export_all()
    else:
        print("Install cadquery to generate STEP/STL files:")
        print("  pip install cadquery")
        print("Then re-run this script.")
