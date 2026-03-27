"""
Single source of truth for v1 board geometry (RHD2132 QFN-56, 30×24 mm).

Every script that needs board dimensions, KiCad origin, or the brd↔KiCad
coordinate transform MUST import from here.  No other file may hardcode
BRD_OX, BRD_OY, BOARD_WIDTH, or BOARD_HEIGHT.

Consumers:
  - gen_pcb_v1.py        (board generator)
  - gen_bundle_c_route.py (Bundle C route emitter)
  - ref_elec_clearance_check.py (clearance checker)

Board-space coordinates:
  Origin = board top-left (NW corner).
  Positive X → right, positive Y → down.
  KiCad conversion:  kx = BRD_OX + bx,  ky = BRD_OY + by.
"""

# ── KiCad board-space origin (NW corner of board outline) ─────────────
# Must match gen_pcb_v1.py ORIGIN_X / ORIGIN_Y exactly.
BRD_OX: float = 100.0
BRD_OY: float = 80.0

# ── Board outline dimensions ──────────────────────────────────────────
BOARD_WIDTH:  float = 30.0   # mm
BOARD_HEIGHT: float = 24.0   # mm
