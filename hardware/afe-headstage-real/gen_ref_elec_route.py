"""
Generate KiCad s-expression blocks for REF_ELEC route (Option A).

Produces (segment ...) and (via ...) blocks that can be appended to the
.kicad_pcb file inside the top-level (kicad_pcb ...) form.

Coordinate conversion: kx = 100.0 + bx,  ky = 68.58 + by
"""
from __future__ import annotations

# Board origin in KiCad coordinates
BRD_OX = 100.0
BRD_OY = 68.58

# Route parameters
NET_CODE = 36
NET_NAME = "REF_ELEC"
TRACE_W = 0.15

# Via parameters
VIA_SIZE = 0.6
VIA_DRILL = 0.3


def brd_to_kicad(bx: float, by: float) -> tuple[float, float]:
    return round(BRD_OX + bx, 4), round(BRD_OY + by, 4)


def _seg(x1: float, y1: float, x2: float, y2: float,
         layer: str, width: float = TRACE_W, net: int = NET_CODE) -> str:
    kx1, ky1 = brd_to_kicad(x1, y1)
    kx2, ky2 = brd_to_kicad(x2, y2)
    return (
        f"  (segment (start {kx1} {ky1}) (end {kx2} {ky2}) "
        f"(width {width}) (layer \"{layer}\") (net {net}))"
    )


def _via(bx: float, by: float, net: int = NET_CODE,
         layers: tuple[str, str] = ("F.Cu", "B.Cu")) -> str:
    kx, ky = brd_to_kicad(bx, by)
    return (
        f"  (via (at {kx} {ky}) (size {VIA_SIZE}) (drill {VIA_DRILL}) "
        f"(layers \"{layers[0]}\" \"{layers[1]}\") (net {net}))"
    )


def generate() -> str:
    lines = [
        f"  ; ──── REF_ELEC route (Option A: 2 vias, B.Cu hop) ────",
        f"  ; Net {NET_CODE} \"{NET_NAME}\"",
        f"  ; Trace width: {TRACE_W} mm, Via: {VIA_SIZE}/{VIA_DRILL} mm",
        f"  ;",
        f"  ; Topology: T-junction at brd(9.98, 12.75)",
        f"  ;   Branch 1 F.Cu: junction → U1.10 pad",
        f"  ;   Branch 2 F.Cu: junction → R1.2 pad (stub)",
        f"  ;   Branch 3 B.Cu: VIA1 → VIA2 (diagonal under IC)",
        f"  ;   Branch 3 F.Cu: VIA2 → J5.33 pad",
        "",
        "  ; --- F.Cu segment: Junction → U1.10 (east) ---",
        _seg(9.98, 12.75, 10.95, 12.75, "F.Cu"),
        "",
        "  ; --- F.Cu segment: Junction → R1.2 (south, stub) ---",
        _seg(9.98, 12.75, 9.98, 15.00, "F.Cu"),
        "",
        "  ; --- VIA 1 at junction ---",
        _via(9.98, 12.75),
        "",
        "  ; --- B.Cu segment: VIA1 → VIA2 (diagonal) ---",
        _seg(9.98, 12.75, 19.7625, 18.20, "B.Cu"),
        "",
        "  ; --- VIA 2 near J5.33 ---",
        _via(19.7625, 18.20),
        "",
        "  ; --- F.Cu segment: VIA2 → J5.33 pad (south) ---",
        _seg(19.7625, 18.20, 19.7625, 19.341, "F.Cu"),
        "",
        f"  ; ──── end REF_ELEC route ────",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(generate())
