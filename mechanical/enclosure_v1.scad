// ═══════════════════════════════════════════════════════════════════════
// AFE Headstage v1 — Parametric Enclosure (OpenSCAD)
//
// Board: 30.0 × 24.0 × 1.6 mm, 4-layer, RHD2132 + Omnetics A79024
// Export: File → Export → Export as STL (for printing)
//         File → Export → Export as 3MF (if slicer supports it)
//
// To generate from command line:
//   openscad -o enclosure_bottom.stl -D 'part="bottom"' enclosure_v1.scad
//   openscad -o enclosure_lid.stl -D 'part="lid"' enclosure_v1.scad
// ═══════════════════════════════════════════════════════════════════════

// ── Which part to render ──────────────────────────────────────────────
// Set to "bottom", "lid", or "both" (exploded view)
part = "both";

// ── Board dimensions (from KiCad Edge.Cuts) ──────────────────────────
board_x     = 30.0;    // mm, length
board_y     = 24.0;    // mm, width
board_z     = 1.6;     // mm, PCB thickness

// ── Component clearance heights ──────────────────────────────────────
comp_top_z  = 3.0;     // mm, above board top (J5 unmated ~2.5 + margin)
comp_bot_z  = 0.5;     // mm, below board bottom (solder joints)

// ── Enclosure parameters ─────────────────────────────────────────────
wall        = 1.2;     // mm, wall thickness
clearance   = 0.4;     // mm, gap PCB-to-wall (FDM tolerance)
ledge_w     = 1.0;     // mm, PCB support ledge width
ledge_h     = 1.0;     // mm, ledge height from floor
floor_z     = 1.2;     // mm, bottom floor thickness
corner_r    = 1.5;     // mm, external corner fillet
lid_z       = 1.2;     // mm, lid plate thickness
lid_lip     = 2.0;     // mm, lid insertion lip depth
lid_clear   = 0.15;    // mm, clearance per side for lid lip

// ── Connector cutout (J5 on +X short edge) ───────────────────────────
conn_w      = 16.0;    // mm, cutout width
conn_h      = 5.0;     // mm, cutout height
conn_z_off  = 0.0;     // mm, offset from PCB top surface

// ── Derived ──────────────────────────────────────────────────────────
cavity_x    = board_x + 2 * clearance;
cavity_y    = board_y + 2 * clearance;
ext_x       = cavity_x + 2 * wall;
ext_y       = cavity_y + 2 * wall;
tray_int_z  = ledge_h + comp_bot_z + board_z + comp_top_z;
tray_ext_z  = floor_z + tray_int_z;

$fn = 40;  // smoothness for curves

// ═══════════════════════════════════════════════════════════════════════
// MODULES
// ═══════════════════════════════════════════════════════════════════════

module rounded_box(x, y, z, r) {
    // Box with rounded vertical edges, centered XY, bottom at Z=0
    translate([0, 0, z/2])
    hull() {
        for (dx = [-1, 1], dy = [-1, 1])
            translate([dx * (x/2 - r), dy * (y/2 - r), 0])
                cylinder(h = z, r = r, center = true);
    }
}

module bottom_tray() {
    difference() {
        // Outer shell
        rounded_box(ext_x, ext_y, tray_ext_z, corner_r);

        // Cavity (hollowed from top)
        translate([0, 0, floor_z])
            cube([cavity_x, cavity_y, tray_int_z + 1], center = true);
        // Fix: move cavity up so it starts at floor_z
        translate([0, 0, floor_z + (tray_int_z + 1)/2])
            cube([cavity_x, cavity_y, tray_int_z + 1], center = true);

        // Connector cutout on +X face
        conn_z_start = floor_z + ledge_h + comp_bot_z + conn_z_off;
        translate([ext_x/2, 0, conn_z_start + conn_h/2])
            cube([wall + 2, conn_w, conn_h], center = true);
    }

    // PCB support ledge
    difference() {
        translate([0, 0, floor_z])
            cube([cavity_x, cavity_y, ledge_h], center = true);
        // Center offset fix
        translate([0, 0, floor_z + ledge_h/2])
            cube([cavity_x, cavity_y, ledge_h], center = true);

        // Remove inner part (PCB drops into the ledge)
        translate([0, 0, floor_z + ledge_h/2])
            cube([cavity_x - 2*ledge_w, cavity_y - 2*ledge_w, ledge_h + 1],
                 center = true);
    }
}

// Corrected bottom tray using proper Z positioning
module bottom_tray_v2() {
    difference() {
        // Outer shell
        rounded_box(ext_x, ext_y, tray_ext_z, corner_r);

        // Cavity (hollowed from top, leaving floor)
        translate([0, 0, floor_z + tray_int_z/2 + 0.5])
            cube([cavity_x, cavity_y, tray_int_z + 1], center = true);

        // Connector cutout on +X face
        conn_z_start = floor_z + ledge_h + comp_bot_z + conn_z_off;
        translate([ext_x/2, 0, conn_z_start + conn_h/2])
            cube([wall * 3, conn_w, conn_h], center = true);
    }

    // PCB support ledge (ring inside cavity)
    translate([0, 0, floor_z + ledge_h/2])
    difference() {
        cube([cavity_x, cavity_y, ledge_h], center = true);
        cube([cavity_x - 2*ledge_w, cavity_y - 2*ledge_w, ledge_h + 0.1],
             center = true);
    }
}

module lid() {
    lip_x = cavity_x - 2 * lid_clear;
    lip_y = cavity_y - 2 * lid_clear;

    // Main lid plate
    rounded_box(ext_x, ext_y, lid_z, corner_r);

    // Insertion lip (extends downward)
    translate([0, 0, -lid_lip + lid_lip/2])
    difference() {
        cube([lip_x, lip_y, lid_lip], center = true);
        cube([lip_x - 2*wall, lip_y - 2*wall, lid_lip + 0.1], center = true);
    }
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER
// ═══════════════════════════════════════════════════════════════════════

if (part == "bottom") {
    bottom_tray_v2();
}
else if (part == "lid") {
    // Position lid right-side up for printing (lip pointing up)
    translate([0, 0, lid_lip])
        lid();
}
else {
    // "both" — exploded view for visualization
    color("SteelBlue", 0.8)
        bottom_tray_v2();

    color("Orange", 0.6)
        translate([0, 0, tray_ext_z + 5])  // 5mm gap for visibility
            lid();

    // Ghost PCB for reference
    color("DarkGreen", 0.3)
        translate([0, 0, floor_z + ledge_h + comp_bot_z + board_z/2])
            cube([board_x, board_y, board_z], center = true);
}
