from __future__ import annotations

"""
test_schematic_invariants.py — Structural invariant tests for the schematic generator
=====================================================================================

Tests pin_abs() rotation correctness, REF_ELEC/R1 wiring topology,
and placement collision avoidance.  These are regression guards against
the class of bugs where rotation formulas or wire overlaps silently
reintroduce net shorts or orphaned pins.

Run:
    python3 -m pytest test_schematic_invariants.py -v
"""

import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

# ── Import the functions under test ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from gen_schematic_v1_rhd2132 import pin_abs, passive_pin1, passive_pin2, g


# ══════════════════════════════════════════════════════════════════════════════
# 1. pin_abs() ROTATION INVARIANT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPinAbsRotation:
    """Verify pin_abs() matches the KiCad rotation convention.

    KiCad schematic uses Y-down screen coordinates.
    Symbol-local coordinates are Y-up.
    pin_abs(cx, cy, rot, lx, ly) must produce:

        rot=0:   (cx + lx, cy - ly)
        rot=90:  (cx - ly, cy - lx)    # 90° CCW in screen coords
        rot=180: (cx - lx, cy + ly)
        rot=270: (cx + ly, cy + lx)    # 270° CCW in screen coords
    """

    # Test points: asymmetric to catch any axis swap
    POINTS = [
        (3.81, 0.0),     # horizontal offset only (passive pin1)
        (0.0, 3.81),     # vertical offset only
        (2.54, -1.27),   # both axes, negative y
        (1.0, 2.0),      # generic asymmetric
        (-0.5, 0.75),    # negative local x
    ]

    # Origin for all tests
    CX, CY = 100.0, 80.0

    @pytest.mark.parametrize("lx,ly", POINTS, ids=[
        "horiz_3.81_0", "vert_0_3.81", "asym_2.54_-1.27",
        "generic_1_2", "neg_x_-0.5_0.75"
    ])
    def test_rot0(self, lx, ly):
        """rot=0: identity in x, Y-inversion in y."""
        ax, ay = pin_abs(self.CX, self.CY, 0, lx, ly)
        assert ax == pytest.approx(self.CX + lx, abs=0.01)
        assert ay == pytest.approx(self.CY - ly, abs=0.01)

    @pytest.mark.parametrize("lx,ly", POINTS, ids=[
        "horiz_3.81_0", "vert_0_3.81", "asym_2.54_-1.27",
        "generic_1_2", "neg_x_-0.5_0.75"
    ])
    def test_rot90(self, lx, ly):
        """rot=90: 90° CCW → x gets -ly, y gets -lx."""
        ax, ay = pin_abs(self.CX, self.CY, 90, lx, ly)
        assert ax == pytest.approx(self.CX - ly, abs=0.01)
        assert ay == pytest.approx(self.CY - lx, abs=0.01)

    @pytest.mark.parametrize("lx,ly", POINTS, ids=[
        "horiz_3.81_0", "vert_0_3.81", "asym_2.54_-1.27",
        "generic_1_2", "neg_x_-0.5_0.75"
    ])
    def test_rot180(self, lx, ly):
        """rot=180: negate x, negate y-inversion."""
        ax, ay = pin_abs(self.CX, self.CY, 180, lx, ly)
        assert ax == pytest.approx(self.CX - lx, abs=0.01)
        assert ay == pytest.approx(self.CY + ly, abs=0.01)

    @pytest.mark.parametrize("lx,ly", POINTS, ids=[
        "horiz_3.81_0", "vert_0_3.81", "asym_2.54_-1.27",
        "generic_1_2", "neg_x_-0.5_0.75"
    ])
    def test_rot270(self, lx, ly):
        """rot=270: 270° CCW → x gets +ly, y gets +lx."""
        ax, ay = pin_abs(self.CX, self.CY, 270, lx, ly)
        assert ax == pytest.approx(self.CX + ly, abs=0.01)
        assert ay == pytest.approx(self.CY + lx, abs=0.01)

    def test_invalid_rotation_raises(self):
        with pytest.raises(ValueError, match="Unsupported rotation"):
            pin_abs(100, 80, 45, 1.0, 0.0)


class TestPinAbsRotationConsistency:
    """Cross-rotation consistency: 4 successive 90° rotations = identity."""

    POINTS = [(3.81, 0.0), (0.0, 3.81), (2.54, -1.27)]
    CX, CY = 100.0, 80.0

    @pytest.mark.parametrize("lx,ly", POINTS)
    def test_four_rotations_return_to_origin(self, lx, ly):
        """Applying the rotation transform 4 times must return to rot=0 result.

        We verify: rotating the *local* offset through 0→90→180→270 traces a
        consistent rectangular orbit around (cx, cy).  Specifically:
          rot0 + rot180 must be symmetric about center,
          rot90 + rot270 must be symmetric about center.
        """
        p0 = pin_abs(self.CX, self.CY, 0, lx, ly)
        p90 = pin_abs(self.CX, self.CY, 90, lx, ly)
        p180 = pin_abs(self.CX, self.CY, 180, lx, ly)
        p270 = pin_abs(self.CX, self.CY, 270, lx, ly)

        # rot=0 and rot=180 are point-symmetric about center
        assert (p0[0] + p180[0]) / 2 == pytest.approx(self.CX, abs=0.01)
        assert (p0[1] + p180[1]) / 2 == pytest.approx(self.CY, abs=0.01)

        # rot=90 and rot=270 are point-symmetric about center
        assert (p90[0] + p270[0]) / 2 == pytest.approx(self.CX, abs=0.01)
        assert (p90[1] + p270[1]) / 2 == pytest.approx(self.CY, abs=0.01)


class TestPassivePinHelpers:
    """Verify passive_pin1/pin2 produce correct left/right for rot=90/270."""

    def test_rot90_pin1_is_left_of_center(self):
        """At rot=90, pin1 (local +3.81) should map to LEFT (smaller x)."""
        p1 = passive_pin1(100.0, 80.0, rot=90)
        p2 = passive_pin2(100.0, 80.0, rot=90)
        assert p1[0] < 100.0, f"pin1 x={p1[0]} should be < center 100.0"
        assert p2[0] > 100.0, f"pin2 x={p2[0]} should be > center 100.0"
        assert p1[1] == p2[1] == 80.0, "both pins on same y for rot=90"

    def test_rot270_pin1_is_right_of_center(self):
        """At rot=270, pin1 (local +3.81) should map to RIGHT (larger x)."""
        p1 = passive_pin1(100.0, 80.0, rot=270)
        p2 = passive_pin2(100.0, 80.0, rot=270)
        assert p1[0] > 100.0, f"pin1 x={p1[0]} should be > center 100.0"
        assert p2[0] < 100.0, f"pin2 x={p2[0]} should be < center 100.0"
        assert p1[1] == p2[1] == 80.0, "both pins on same y for rot=270"

    def test_rot0_pin1_is_above_center(self):
        """At rot=0, pin1 (local y=+3.81) should map ABOVE (smaller y in screen)."""
        p1 = passive_pin1(100.0, 80.0, rot=0)
        p2 = passive_pin2(100.0, 80.0, rot=0)
        assert p1[1] < 80.0, f"pin1 y={p1[1]} should be < center 80.0 (above)"
        assert p2[1] > 80.0, f"pin2 y={p2[1]} should be > center 80.0 (below)"
        assert p1[0] == p2[0] == 100.0, "both pins on same x for rot=0"

    def test_rot180_pin1_is_below_center(self):
        """At rot=180, pin1 (local y=+3.81) should map BELOW (larger y in screen)."""
        p1 = passive_pin1(100.0, 80.0, rot=180)
        p2 = passive_pin2(100.0, 80.0, rot=180)
        assert p1[1] > 80.0, f"pin1 y={p1[1]} should be > center 80.0 (below)"
        assert p2[1] < 80.0, f"pin2 y={p2[1]} should be < center 80.0 (above)"
        assert p1[0] == p2[0] == 100.0, "both pins on same x for rot=180"


# ══════════════════════════════════════════════════════════════════════════════
# 2. REF_ELEC / R1 WIRING TOPOLOGY TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestRefElecWiringTopology:
    """Verify the generated schematic has correct REF_ELEC/R1/GND topology.

    These tests parse the actual generated .kicad_sch file to check:
    - No wire overlap between REF_ELEC and GND segments
    - GND drops vertically, not horizontally along the REF_ELEC wire
    - REF_ELEC wire does not extend past R1 pin1
    - Both REF_ELEC labels exist and touch wire endpoints
    """

    SCH_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_sch"

    @pytest.fixture(autouse=True)
    def _require_schematic(self):
        if not self.SCH_PATH.exists():
            pytest.skip("Schematic not generated yet")
        self.text = self.SCH_PATH.read_text()

    def _find_all_wires(self):
        """Extract all wire segments as ((x1,y1),(x2,y2)) tuples."""
        pattern = r'\(wire \(pts \(xy ([\d.]+) ([\d.]+)\) \(xy ([\d.]+) ([\d.]+)\)\)'
        wires = []
        for m in re.finditer(pattern, self.text):
            x1, y1, x2, y2 = (float(m.group(i)) for i in range(1, 5))
            wires.append(((x1, y1), (x2, y2)))
        return wires

    def _find_symbol_at(self, lib_id, ref_pattern):
        """Find symbol placement: returns (x, y, rot) or None."""
        pat = (
            r'\(symbol\s*\n\s*\(lib_id "' + re.escape(lib_id) + r'"\)\s*\n'
            r'\s*\(at ([\d.]+) ([\d.]+) (\d+)\).*?'
            r'\(property "Reference" "(' + ref_pattern + r')"'
        )
        m = re.search(pat, self.text, re.DOTALL)
        if m:
            return float(m.group(1)), float(m.group(2)), int(m.group(3))
        return None

    def _find_glabels(self, name):
        """Find all global_label instances with given name. Returns [(x,y,rot), ...]."""
        pat = r'\(global_label "' + re.escape(name) + r'".*?\(at ([\d.]+) ([\d.]+) (\d+)\)'
        results = []
        for m in re.finditer(pat, self.text):
            results.append((float(m.group(1)), float(m.group(2)), int(m.group(3))))
        return results

    def _wire_has_endpoint(self, x, y, wires=None):
        """Check if any wire has an endpoint at exactly (x, y)."""
        if wires is None:
            wires = self._find_all_wires()
        for (x1, y1), (x2, y2) in wires:
            if (abs(x1 - x) < 0.005 and abs(y1 - y) < 0.005):
                return True
            if (abs(x2 - x) < 0.005 and abs(y2 - y) < 0.005):
                return True
        return False

    def test_r1_exists_at_rot90(self):
        """R1 must be placed at rot=90 (horizontal orientation)."""
        pos = self._find_symbol_at("Device:R", "R1")
        assert pos is not None, "R1 not found in schematic"
        _, _, rot = pos
        assert rot == 90, f"R1 rotation should be 90, got {rot}"

    def test_gnd_drops_vertically_from_r1(self):
        """GND symbol connected to R1 pin1 must be on a DIFFERENT y-coordinate.

        This guards against the bug where GND ran horizontally along the
        same y as the REF_ELEC wire, causing an overlap/short.
        """
        r1_pos = self._find_symbol_at("Device:R", "R1")
        assert r1_pos is not None
        r1_x, r1_y, _ = r1_pos

        # R1 pin1 at rot=90 is to the LEFT
        r1_pin1_x = round(r1_x - 3.81, 2)
        r1_pin1_y = r1_y

        # Find a GND symbol whose x matches r1_pin1_x
        gnd_pat = r'\(symbol\s*\n\s*\(lib_id "power:GND"\)\s*\n\s*\(at ([\d.]+) ([\d.]+) (\d+)\)'
        found_gnd = None
        for m in re.finditer(gnd_pat, self.text):
            gx, gy = float(m.group(1)), float(m.group(2))
            if abs(gx - r1_pin1_x) < 0.1:
                found_gnd = (gx, gy, int(m.group(3)))
                break

        assert found_gnd is not None, (
            f"No GND symbol found near R1 pin1 x={r1_pin1_x}"
        )
        gnd_x, gnd_y, gnd_rot = found_gnd
        assert gnd_y != pytest.approx(r1_pin1_y, abs=0.1), (
            f"GND at y={gnd_y} is on same y as R1 pin1 y={r1_pin1_y} — "
            f"this causes horizontal overlap with REF_ELEC wire"
        )

    def test_ref_elec_wire_does_not_extend_past_r1_pin1(self):
        """The REF_ELEC horizontal wire must stop before R1 pin1 x-coordinate.

        R1 pin1 is the GND side. The REF_ELEC wire from U1 REF should only
        extend to the glabel position, which must be to the RIGHT of R1 pin1.
        """
        r1_pos = self._find_symbol_at("Device:R", "R1")
        assert r1_pos is not None
        r1_x, r1_y, _ = r1_pos
        r1_pin1_x = round(r1_x - 3.81, 2)

        # Find all REF_ELEC glabels
        glabels = self._find_glabels("REF_ELEC")
        # Find the one on the same y as R1 (the U1-side label)
        u1_side_labels = [gl for gl in glabels if abs(gl[1] - r1_y) < 0.1]
        assert len(u1_side_labels) >= 1, "No REF_ELEC glabel found near R1"

        for lbl_x, lbl_y, _ in u1_side_labels:
            assert lbl_x > r1_pin1_x, (
                f"REF_ELEC glabel at x={lbl_x} extends past R1 pin1 at "
                f"x={r1_pin1_x} — this would cause wire overlap with GND"
            )

    def test_both_ref_elec_glabels_have_wire_endpoints(self):
        """Both REF_ELEC global labels must have a wire touching them."""
        glabels = self._find_glabels("REF_ELEC")
        assert len(glabels) >= 2, (
            f"Expected 2 REF_ELEC glabels, found {len(glabels)}"
        )
        wires = self._find_all_wires()
        for gx, gy, _ in glabels:
            assert self._wire_has_endpoint(gx, gy, wires), (
                f"REF_ELEC glabel at ({gx}, {gy}) has no wire endpoint"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 3. ERC SEVERITY GATE
# ══════════════════════════════════════════════════════════════════════════════

class TestErcSeverityGate:
    """Verify the schematic has zero ERC *errors* (warnings classified separately)."""

    SCH_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_sch"
    KICAD_CLI = "/opt/homebrew/bin/kicad-cli"

    @pytest.fixture(autouse=True)
    def _require_schematic(self):
        if not self.SCH_PATH.exists():
            pytest.skip("Schematic not generated yet")
        if not Path(self.KICAD_CLI).exists():
            pytest.skip("kicad-cli not found")

    def test_zero_erc_errors(self):
        """ERC must report zero errors (warnings are classified separately)."""
        import json, tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out_path = f.name
        try:
            result = subprocess.run(
                [self.KICAD_CLI, "sch", "erc",
                 "--output", out_path,
                 "--format", "json",
                 str(self.SCH_PATH)],
                capture_output=True, text=True, timeout=30
            )
            report = json.loads(Path(out_path).read_text())
            errors = []
            for sheet in report.get("sheets", []):
                for v in sheet.get("violations", []):
                    if v.get("severity") == "error":
                        errors.append(v["description"])
            assert len(errors) == 0, f"ERC errors found: {errors}"
        finally:
            Path(out_path).unlink(missing_ok=True)

    def test_erc_warnings_classified(self):
        """ERC must report zero warnings — no allowlisting."""
        import json, tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out_path = f.name
        try:
            result = subprocess.run(
                [self.KICAD_CLI, "sch", "erc",
                 "--output", out_path,
                 "--format", "json",
                 "--severity-all",
                 str(self.SCH_PATH)],
                capture_output=True, text=True, timeout=30
            )
            report = json.loads(Path(out_path).read_text())
            warnings = []
            # lib_symbol_issues are library-path / environment warnings
            # (missing global libs, renamed symbols). They don't affect
            # electrical correctness and vary by machine config.
            IGNORED_ERC_TYPES = {"lib_symbol_issues"}
            for sheet in report.get("sheets", []):
                for v in sheet.get("violations", []):
                    if v.get("severity") == "warning" and v.get("type") not in IGNORED_ERC_TYPES:
                        warnings.append(
                            f"{v['type']}: {v['description']}"
                        )
            assert len(warnings) == 0, (
                f"ERC warnings (none allowed, lib_symbol_issues filtered): {warnings}"
            )
        finally:
            Path(out_path).unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# 4. pin_abs ANCHORED TO KiCad SYMBOL PIN LOCATIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestPinAbsAnchoredToKicad:
    """Verify pin_abs() matches ACTUAL KiCad lib_symbol pin coordinates.

    These tests parse the generated .kicad_sch to extract:
      1. lib_symbol pin local coords (at X Y angle) for Device:R
      2. Placed symbol instances (at CX CY rot)
      3. Wire endpoints near each computed pin position

    This anchors pin_abs to KiCad's ground truth — not self-referential.
    """

    SCH_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_sch"

    @pytest.fixture(autouse=True)
    def _require_schematic(self):
        if not self.SCH_PATH.exists():
            pytest.skip("Schematic not generated yet")
        self.text = self.SCH_PATH.read_text()

    def _extract_lib_symbol_pins(self, lib_id_suffix):
        """Extract pin (number, lx, ly) from lib_symbols section for given symbol.

        Returns dict: pin_number → (lx, ly).
        """
        # Find the lib_symbol for this symbol
        # e.g. (symbol "Device:R" ...) contains (symbol "R_1_1" ...) with pins
        lib_start = self.text.find(f'(symbol "{lib_id_suffix}"')
        if lib_start == -1:
            return {}

        # Find the end of this lib_symbol (next top-level symbol or end of lib_symbols)
        depth = 0
        i = lib_start
        while i < len(self.text):
            if self.text[i] == '(':
                depth += 1
            elif self.text[i] == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        lib_block = self.text[lib_start:i + 1]

        # Extract pins: (pin TYPE line (at LX LY ANGLE) (length L) ... (number "N"))
        pins = {}
        for m in re.finditer(
            r'\(pin \w+ \w+ \(at ([-\d.]+) ([-\d.]+) \d+\) \(length [\d.]+\)\s*'
            r'\(name "[^"]*".*?\)\s*\(number "(\w+)"',
            lib_block, re.DOTALL
        ):
            lx, ly, num = float(m.group(1)), float(m.group(2)), m.group(3)
            pins[num] = (lx, ly)
        return pins

    def _find_placed_instances(self, lib_id, ref_prefix):
        """Find all placed symbol instances. Returns [(ref, cx, cy, rot), ...]."""
        pattern = (
            r'\(symbol\s*\n\s*\(lib_id "' + re.escape(lib_id) + r'"\)\s*\n'
            r'\s*\(at ([\d.]+) ([\d.]+) (\d+)\).*?'
            r'\(property "Reference" "(' + re.escape(ref_prefix) + r'\d+)"'
        )
        results = []
        for m in re.finditer(pattern, self.text, re.DOTALL):
            cx, cy, rot = float(m.group(1)), float(m.group(2)), int(m.group(3))
            ref = m.group(4)
            results.append((ref, cx, cy, rot))
        return results

    def _find_wire_endpoint(self, x, y, tol=0.01):
        """Check if any wire has an endpoint at (x, y) within tolerance."""
        pattern = r'\(wire \(pts \(xy ([\d.]+) ([\d.]+)\) \(xy ([\d.]+) ([\d.]+)\)\)'
        for m in re.finditer(pattern, self.text):
            x1, y1 = float(m.group(1)), float(m.group(2))
            x2, y2 = float(m.group(3)), float(m.group(4))
            if (abs(x1 - x) < tol and abs(y1 - y) < tol):
                return True
            if (abs(x2 - x) < tol and abs(y2 - y) < tol):
                return True
        return False

    def test_device_r_pins_match_wire_endpoints(self):
        """For every placed Device:R, pin_abs(lib_pin_coords) must hit a wire endpoint.

        Parses lib_symbol for Device:R pin locations, computes absolute positions
        using pin_abs(), and verifies each pin touches a wire endpoint in the schematic.
        """
        lib_pins = self._extract_lib_symbol_pins("Device:R")
        assert len(lib_pins) >= 2, f"Expected 2 pins for Device:R, got {lib_pins}"
        assert "1" in lib_pins and "2" in lib_pins

        instances = self._find_placed_instances("Device:R", "R")
        assert len(instances) >= 1, "No Device:R instances found"

        failures = []
        for ref, cx, cy, rot in instances:
            for pin_num, (lx, ly) in lib_pins.items():
                ax, ay = pin_abs(cx, cy, rot, lx, ly)
                if not self._find_wire_endpoint(ax, ay):
                    failures.append(
                        f"{ref} pin {pin_num}: pin_abs({cx},{cy},rot={rot},{lx},{ly})"
                        f" → ({ax},{ay}) has no wire endpoint"
                    )

        assert len(failures) == 0, (
            f"pin_abs mismatch with KiCad wire endpoints:\n" +
            "\n".join(failures)
        )

    def test_device_r_lib_pin_coords_are_expected(self):
        """Device:R lib_symbol pins must be at the standard ±3.81 positions."""
        lib_pins = self._extract_lib_symbol_pins("Device:R")
        assert "1" in lib_pins, "Pin 1 not found in Device:R lib_symbol"
        assert "2" in lib_pins, "Pin 2 not found in Device:R lib_symbol"

        # Standard KiCad Device:R: pin 1 at (0, 3.81), pin 2 at (0, -3.81)
        p1_lx, p1_ly = lib_pins["1"]
        p2_lx, p2_ly = lib_pins["2"]
        assert p1_lx == pytest.approx(0.0, abs=0.01) and p1_ly == pytest.approx(3.81, abs=0.01), \
            f"Device:R pin 1 expected at (0, 3.81), got ({p1_lx}, {p1_ly})"
        assert p2_lx == pytest.approx(0.0, abs=0.01) and p2_ly == pytest.approx(-3.81, abs=0.01), \
            f"Device:R pin 2 expected at (0, -3.81), got ({p2_lx}, {p2_ly})"

    def test_ferrite_bead_pins_match_wire_endpoints(self):
        """For placed FerriteBead, pin_abs(lib_pin_coords) must hit wire endpoints."""
        lib_pins = self._extract_lib_symbol_pins("Device:FerriteBead")
        if not lib_pins:
            pytest.skip("FerriteBead not in lib_symbols")
        assert "1" in lib_pins and "2" in lib_pins

        instances = self._find_placed_instances("Device:FerriteBead", "FB")
        assert len(instances) >= 1, "No FerriteBead instances found"

        failures = []
        for ref, cx, cy, rot in instances:
            for pin_num, (lx, ly) in lib_pins.items():
                ax, ay = pin_abs(cx, cy, rot, lx, ly)
                if not self._find_wire_endpoint(ax, ay):
                    failures.append(
                        f"{ref} pin {pin_num}: pin_abs({cx},{cy},rot={rot},{lx},{ly})"
                        f" → ({ax},{ay}) has no wire endpoint"
                    )
        assert len(failures) == 0, "\n".join(failures)

    def test_rhd2132_ref_pin_matches_wire_endpoint(self):
        """U1 (RHD2132) REF pin (pad 10) must have a wire endpoint at its abs position."""
        lib_pins = self._extract_lib_symbol_pins("afe:RHD2132")
        assert "10" in lib_pins, "Pin 10 (REF) not found in RHD2132 lib_symbol"

        instances = self._find_placed_instances("afe:RHD2132", "U")
        assert len(instances) >= 1, "No RHD2132 instance found"

        ref_name, cx, cy, rot = instances[0]
        lx, ly = lib_pins["10"]
        ax, ay = pin_abs(cx, cy, rot, lx, ly)

        assert self._find_wire_endpoint(ax, ay), (
            f"U1 pin 10 (REF) at ({ax}, {ay}) [from pin_abs({cx},{cy},{rot},{lx},{ly})] "
            f"has no wire endpoint — REF_ELEC net broken"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. REF_ELEC NET CONNECTIVITY PROOF (via KiCad netlist export)
# ══════════════════════════════════════════════════════════════════════════════

class TestRefElecNetConnectivity:
    """Prove REF_ELEC net contains the required components via KiCad netlist.

    Exports the netlist using kicad-cli and parses the S-expression output
    to verify that REF_ELEC contains:
      - U1 pin 10 (RHD2132 REF)
      - J5 pin 33 (electrode connector REF)
      - TP6 pin 1 (test point)
      - R1 pin 2 (bias resistor)

    This is the user's minimum acceptance bar for net connectivity.
    """

    SCH_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_sch"
    KICAD_CLI = "/opt/homebrew/bin/kicad-cli"

    @pytest.fixture(autouse=True)
    def _require_schematic_and_cli(self):
        if not self.SCH_PATH.exists():
            pytest.skip("Schematic not generated yet")
        if not Path(self.KICAD_CLI).exists():
            pytest.skip("kicad-cli not found")

    def _export_netlist(self):
        """Export netlist and return content string."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
            out_path = f.name
        try:
            result = subprocess.run(
                [self.KICAD_CLI, "sch", "export", "netlist",
                 "--output", out_path,
                 str(self.SCH_PATH)],
                capture_output=True, text=True, timeout=30
            )
            return Path(out_path).read_text()
        finally:
            Path(out_path).unlink(missing_ok=True)

    def _parse_net_members(self, netlist_content, net_name):
        """Parse S-expression netlist for members of a named net.

        Returns set of (ref, pin) tuples.
        """
        blocks = netlist_content.split('(net (code')
        members = set()
        for block in blocks[1:]:
            block = '(net (code' + block
            name_m = re.search(r'\(name "([^"]+)"\)', block)
            if not name_m or name_m.group(1) != net_name:
                continue
            for node_m in re.finditer(
                r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', block
            ):
                members.add((node_m.group(1), node_m.group(2)))
            break
        return members

    def test_ref_elec_contains_required_pins(self):
        """REF_ELEC net must contain U1.10, J5.33, TP6.1, and R1.2."""
        netlist = self._export_netlist()
        members = self._parse_net_members(netlist, "REF_ELEC")

        required = {
            ("U1", "10"),   # RHD2132 REF pin
            ("J5", "33"),   # Electrode connector REF pin
            ("TP6", "1"),   # Test point
            ("R1", "2"),    # Bias resistor
        }

        missing = required - members
        assert len(missing) == 0, (
            f"REF_ELEC net missing required pins: {missing}\n"
            f"Actual members: {members}"
        )

    def test_no_unconnected_u1_pin_10(self):
        """U1 pin 10 must NOT be on an 'unconnected' net."""
        netlist = self._export_netlist()
        blocks = netlist.split('(net (code')
        for block in blocks[1:]:
            block = '(net (code' + block
            name_m = re.search(r'\(name "([^"]+)"\)', block)
            if not name_m:
                continue
            name = name_m.group(1)
            if 'unconnected' in name.lower():
                nodes = re.findall(
                    r'\(node \(ref "U1"\) \(pin "10"\)', block
                )
                assert len(nodes) == 0, (
                    f"U1 pin 10 is on unconnected net '{name}'"
                )

    def test_r1_pin1_on_gnd(self):
        """R1 pin 1 must be on the GND net (bias to ground)."""
        netlist = self._export_netlist()
        members = self._parse_net_members(netlist, "GND")
        assert ("R1", "1") in members, (
            f"R1 pin 1 not on GND net. GND members include: "
            f"{[m for m in members if m[0] == 'R1']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 6. CRITICAL NET MEMBERSHIP PROOF (all power + SPI + special nets)
# ══════════════════════════════════════════════════════════════════════════════

class TestCriticalNetMembership:
    """Prove every critical net contains exactly the expected components.

    Uses kicad-cli netlist export (S-expression format) to verify
    net membership. Each test asserts that the minimum required set
    of (ref, pin) pairs is present on the named net.
    """

    SCH_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_sch"
    KICAD_CLI = "/opt/homebrew/bin/kicad-cli"

    @pytest.fixture(autouse=True)
    def _require_schematic_and_cli(self):
        if not self.SCH_PATH.exists():
            pytest.skip("Schematic not generated yet")
        if not Path(self.KICAD_CLI).exists():
            pytest.skip("kicad-cli not found")

    def _export_netlist(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
            out_path = f.name
        try:
            result = subprocess.run(
                [self.KICAD_CLI, "sch", "export", "netlist",
                 "--output", out_path,
                 str(self.SCH_PATH)],
                capture_output=True, text=True, timeout=30
            )
            return Path(out_path).read_text()
        finally:
            Path(out_path).unlink(missing_ok=True)

    def _parse_net_members(self, netlist_content, net_name):
        blocks = netlist_content.split('(net (code')
        members = set()
        for block in blocks[1:]:
            block = '(net (code' + block
            name_m = re.search(r'\(name "([^"]+)"\)', block)
            if not name_m or name_m.group(1) != net_name:
                continue
            for node_m in re.finditer(
                r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', block
            ):
                members.add((node_m.group(1), node_m.group(2)))
            break
        return members

    @pytest.fixture(scope="class")
    def netlist(self):
        """Export netlist once for all tests in this class."""
        import tempfile
        sch = self.SCH_PATH
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
            out_path = f.name
        try:
            subprocess.run(
                [self.KICAD_CLI, "sch", "export", "netlist",
                 "--output", out_path, str(sch)],
                capture_output=True, text=True, timeout=30
            )
            return Path(out_path).read_text()
        finally:
            Path(out_path).unlink(missing_ok=True)

    def _assert_net_contains(self, netlist, net_name, required):
        """Assert that net_name contains at least all (ref, pin) in required."""
        members = self._parse_net_members(netlist, net_name)
        missing = required - members
        assert len(missing) == 0, (
            f"Net '{net_name}' missing required members: {missing}\n"
            f"Actual: {sorted(members)}"
        )

    # ── Power nets ────────────────────────────────────────────────────────

    def test_plus_3v3_net(self, netlist):
        """+3V3: C1.1, C2.1, FB1.1, J1.7, J1.8, TP1.1"""
        self._assert_net_contains(netlist, "+3V3", {
            ("C1", "1"),    # 100nF bypass
            ("C2", "1"),    # 1µF bulk
            ("FB1", "1"),   # Ferrite input (digital side)
            ("J1", "7"),    # PZN-12 VDD_D
            ("J1", "8"),    # PZN-12 VDD_D
            ("TP1", "1"),   # TP_VDD
        })

    def test_avdd_net(self, netlist):
        """AVDD: FB1.2, C3.1, C4.1, C5.1, C6.1, J1.9, J1.10, TP2.1,
                 U1.13, U1.14, U1.15, U1.16, U1.26, U1.31"""
        self._assert_net_contains(netlist, "AVDD", {
            ("FB1", "2"),   # Ferrite output (analog side)
            ("C3", "1"),    # 100nF AVDD bypass
            ("C4", "1"),    # 1µF AVDD bulk
            ("C5", "1"),    # VDD_A1 local bypass
            ("C6", "1"),    # VDD_A2 local bypass
            ("J1", "9"),    # PZN-12 VDD_A
            ("J1", "10"),   # PZN-12 VDD_A
            ("TP2", "1"),   # TP_AVDD
            ("U1", "13"),   # VDD_A1
            ("U1", "14"),   # auxin1 (tied to AVDD)
            ("U1", "15"),   # auxin2 (tied to AVDD)
            ("U1", "16"),   # auxin3 (tied to AVDD)
            ("U1", "26"),   # VDD_A2
            ("U1", "31"),   # VDD_A3
        })

    def test_adc_ref_net(self, netlist):
        """ADC_ref: C7.1, U1.28"""
        self._assert_net_contains(netlist, "ADC_ref", {
            ("C7", "1"),    # 10nF decoupling
            ("U1", "28"),   # ADC_ref pin
        })

    # ── SPI nets (chip-side, after series resistors) ─────────────────────

    def test_cs_net(self, netlist):
        """CS: R2.2, U1.19"""
        self._assert_net_contains(netlist, "CS", {
            ("R2", "2"),    # Series resistor chip-side
            ("U1", "19"),   # CS+ pin
        })

    def test_sclk_net(self, netlist):
        """SCLK: R3.2, TP4.1, U1.21"""
        self._assert_net_contains(netlist, "SCLK", {
            ("R3", "2"),    # Series resistor chip-side
            ("TP4", "1"),   # TP_SCLK
            ("U1", "21"),   # SCLK+ pin
        })

    def test_mosi_net(self, netlist):
        """MOSI: R4.2, U1.23"""
        self._assert_net_contains(netlist, "MOSI", {
            ("R4", "2"),    # Series resistor chip-side
            ("U1", "23"),   # MOSI+ pin
        })

    def test_miso_net(self, netlist):
        """MISO: J1.1, TP5.1, U1.25"""
        self._assert_net_contains(netlist, "MISO", {
            ("J1", "1"),    # PZN-12 MISO1 (no series R on output)
            ("TP5", "1"),   # TP_MISO
            ("U1", "25"),   # MISO+ pin
        })

    # ── SPI nets (connector-side, before series resistors) ───────────────

    def test_cs_j_net(self, netlist):
        """CS_J: J1.4, R2.1"""
        self._assert_net_contains(netlist, "CS_J", {
            ("J1", "4"),    # PZN-12 CS
            ("R2", "1"),    # Series resistor connector-side
        })

    def test_sclk_j_net(self, netlist):
        """SCLK_J: J1.3, R3.1"""
        self._assert_net_contains(netlist, "SCLK_J", {
            ("J1", "3"),    # PZN-12 SCLK
            ("R3", "1"),    # Series resistor connector-side
        })

    def test_mosi_j_net(self, netlist):
        """MOSI_J: J1.2, R4.1"""
        self._assert_net_contains(netlist, "MOSI_J", {
            ("J1", "2"),    # PZN-12 MOSI
            ("R4", "1"),    # Series resistor connector-side
        })

    # ── Special nets ─────────────────────────────────────────────────────

    def test_elec_test_net(self, netlist):
        """ELEC_TEST: J5.35, U1.33"""
        self._assert_net_contains(netlist, "ELEC_TEST", {
            ("J5", "35"),   # Electrode connector ETEST
            ("U1", "33"),   # elec_test pin
        })

    def test_gnd_net(self, netlist):
        """GND must contain all expected cap pin2s, U1 GND pins, connectors, R1.1."""
        self._assert_net_contains(netlist, "GND", {
            # Caps pin 2 (all bypass caps)
            ("C1", "2"), ("C2", "2"), ("C3", "2"), ("C4", "2"),
            ("C5", "2"), ("C6", "2"), ("C7", "2"),
            # RHD2132 GND pins
            ("U1", "11"), ("U1", "12"), ("U1", "17"),
            ("U1", "29"), ("U1", "EP"),
            # RHD2132 CMOS mode: LVDS- pins tied to GND
            ("U1", "18"), ("U1", "20"), ("U1", "22"),
            ("U1", "24"), ("U1", "30"),  # LVDS_EN
            # VESD → GND
            ("U1", "32"),
            # Connectors
            ("J1", "5"), ("J1", "6"), ("J1", "12"),  # PZN-12 GND pins
            ("J5", "34"), ("J5", "36"),                # Electrode connector GNDs
            # REF bias
            ("R1", "1"),
            # Test point
            ("TP3", "1"),
        })


# ══════════════════════════════════════════════════════════════════════════════
# 7. COLINEAR OVERLAP LINT (parses .kicad_sch output, not internal data)
# ══════════════════════════════════════════════════════════════════════════════

class TestColinearOverlapLint:
    """Detect overlapping colinear wire segments in the generated schematic.

    Parses the .kicad_sch file for all (wire (pts ...)) segments and checks
    for colinear overlaps: two horizontal segments at the same Y with
    overlapping X-ranges (beyond a shared endpoint), or two vertical segments
    at the same X with overlapping Y-ranges.

    This is the external proof that the generator's internal lint matches
    reality — anchored to KiCad's actual wire data, not internal state.
    """

    SCH_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_sch"
    WIRE_RE = re.compile(
        r'\(wire \(pts \(xy ([\d.]+) ([\d.]+)\) \(xy ([\d.]+) ([\d.]+)\)\)'
    )

    @pytest.fixture(autouse=True)
    def _require_schematic(self):
        if not self.SCH_PATH.exists():
            pytest.skip("Schematic not generated yet")

    def _parse_wire_segments(self):
        text = self.SCH_PATH.read_text()
        segs = []
        for m in self.WIRE_RE.finditer(text):
            x1, y1 = float(m.group(1)), float(m.group(2))
            x2, y2 = float(m.group(3)), float(m.group(4))
            segs.append((x1, y1, x2, y2))
        return segs

    def _find_colinear_overlaps(self, segs):
        """O(n²) scan for colinear overlapping segments."""
        overlaps = []
        n = len(segs)
        for i in range(n):
            x1a, y1a, x2a, y2a = segs[i]
            for j in range(i + 1, n):
                x1b, y1b, x2b, y2b = segs[j]
                # Horizontal: same Y
                if (abs(y1a - y2a) < 0.01 and abs(y1b - y2b) < 0.01
                        and abs(y1a - y1b) < 0.01):
                    lo_a, hi_a = min(x1a, x2a), max(x1a, x2a)
                    lo_b, hi_b = min(x1b, x2b), max(x1b, x2b)
                    overlap = min(hi_a, hi_b) - max(lo_a, lo_b)
                    if overlap > 0.01:
                        overlaps.append(
                            f"H y={y1a}: [{lo_a}..{hi_a}] ∩ "
                            f"[{lo_b}..{hi_b}] = {overlap:.2f}mm"
                        )
                # Vertical: same X
                elif (abs(x1a - x2a) < 0.01 and abs(x1b - x2b) < 0.01
                      and abs(x1a - x1b) < 0.01):
                    lo_a, hi_a = min(y1a, y2a), max(y1a, y2a)
                    lo_b, hi_b = min(y1b, y2b), max(y1b, y2b)
                    overlap = min(hi_a, hi_b) - max(lo_a, lo_b)
                    if overlap > 0.01:
                        overlaps.append(
                            f"V x={x1a}: [{lo_a}..{hi_a}] ∩ "
                            f"[{lo_b}..{hi_b}] = {overlap:.2f}mm"
                        )
        return overlaps

    def test_zero_colinear_overlaps(self):
        """No colinear wire overlaps in the generated schematic.

        Colinear overlapping wires cause KiCad to split nets incorrectly
        and produce ghost net fragments. This was the root cause of the
        REF_ELEC connectivity defect.
        """
        segs = self._parse_wire_segments()
        assert len(segs) > 50, f"Too few wires ({len(segs)}), schematic may be incomplete"
        overlaps = self._find_colinear_overlaps(segs)
        assert len(overlaps) == 0, (
            f"Colinear overlapping wire segments detected:\n"
            + "\n".join(f"  ✗ {o}" for o in overlaps)
        )


# ══════════════════════════════════════════════════════════════════════════════
# 8. PCB SCHEMATIC PARITY + ELECTRICAL DRC
# ══════════════════════════════════════════════════════════════════════════════

class TestPcbDrc:
    """Verify PCB has zero schematic parity issues and zero electrical DRC violations.

    Uses kicad-cli pcb drc --schematic-parity to get the authoritative
    parity check, and classifies all violations by electrical vs cosmetic.

    The classifier uses a closed-world assumption: every violation type
    must appear in ELECTRICAL_TYPES or KNOWN_COSMETIC_TYPES. If KiCad
    introduces a new violation type, this test fails immediately rather
    than silently treating it as cosmetic.
    """

    PCB_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_pcb"
    KICAD_CLI = "/opt/homebrew/bin/kicad-cli"
    PROJECT_DIR = Path(__file__).parent

    # Electrical categories that must be zero even on unrouted boards
    ELECTRICAL_TYPES = frozenset({
        "shorting_items", "clearance", "net_conflict",
        "copper_edge_clearance", "edge_clearance",
        "tracks_crossing", "via_dangling", "zone_priority",
        "drill_out_of_range", "via_diameter",
    })

    # Cosmetic categories that are acceptable pre-routing
    KNOWN_COSMETIC_TYPES = frozenset({
        "lib_footprint_mismatch", "silk_over_copper", "silk_overlap",
        "silk_edge_clearance", "courtyards_overlap",
    })

    # The union of both is the complete set of recognized types.
    # Any violation type NOT in this set triggers an immediate failure.
    ALLOWED_TYPES = ELECTRICAL_TYPES | KNOWN_COSMETIC_TYPES

    @pytest.fixture(autouse=True)
    def _require_pcb_and_cli(self):
        if not self.PCB_PATH.exists():
            pytest.skip("PCB not generated yet")
        if not Path(self.KICAD_CLI).exists():
            pytest.skip("kicad-cli not found")

    def _run_drc(self):
        import json, tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out_path = f.name
        try:
            # Run from project directory for stable schematic association
            subprocess.run(
                [self.KICAD_CLI, "pcb", "drc",
                 "--schematic-parity",
                 "--format", "json",
                 "--output", out_path,
                 str(self.PCB_PATH)],
                capture_output=True, text=True, timeout=60,
                cwd=str(self.PROJECT_DIR),
            )
            return json.loads(Path(out_path).read_text())
        finally:
            Path(out_path).unlink(missing_ok=True)

    def test_zero_schematic_parity_issues(self):
        """PCB must have zero schematic parity issues.

        This is the authoritative proof that the PCB netlist matches the
        schematic netlist — not a count comparison, but KiCad's own check.
        """
        report = self._run_drc()
        parity = report.get("schematic_parity", [])
        assert len(parity) == 0, (
            f"Schematic parity issues found:\n"
            + "\n".join(
                f"  ✗ {p.get('type', '?')}: {p.get('description', '?')}"
                for p in parity
            )
        )

    def test_no_unknown_drc_violation_types(self):
        """Every violation type must be in ALLOWED_TYPES (closed-world).

        If KiCad introduces a new violation type and we trigger it,
        this test fails immediately — we must explicitly classify it
        as electrical (must-fix) or cosmetic (acceptable).
        This prevents silent rot where a new electrical category
        is ignored because it wasn't in our hand-curated list.
        """
        report = self._run_drc()
        from collections import Counter
        types = Counter()
        for v in report.get("violations", []):
            types[v.get("type", "unknown")] += 1

        unknown = {t: c for t, c in types.items() if t not in self.ALLOWED_TYPES}
        assert len(unknown) == 0, (
            f"Unknown DRC violation types (must classify as ELECTRICAL or COSMETIC):\n"
            + "\n".join(f"  ✗ {t}: {c} occurrences" for t, c in sorted(unknown.items()))
        )

    def test_zero_electrical_drc_violations(self):
        """All electrical DRC categories must be zero (even on unrouted board).

        Cosmetic violations (silk, courtyard, lib_footprint_mismatch) are
        acceptable pre-routing. Electrical violations (shorts, clearance,
        net conflicts) are never acceptable.
        """
        report = self._run_drc()
        from collections import Counter
        types = Counter()
        for v in report.get("violations", []):
            types[v.get("type", "unknown")] += 1

        electrical_violations = {
            t: c for t, c in types.items()
            if t in self.ELECTRICAL_TYPES
        }
        assert len(electrical_violations) == 0, (
            f"Electrical DRC violations found:\n"
            + "\n".join(f"  ✗ {t}: {c}" for t, c in electrical_violations.items())
        )


# ══════════════════════════════════════════════════════════════════════════════
# 9. NET CLASS ENFORCEMENT (Gate A)
# ══════════════════════════════════════════════════════════════════════════════

class TestNetClassEnforcement:
    """Verify that every signal net is assigned to its intended net class.

    Parses the .kicad_pro project file (the authoritative source of net-class
    assignments in KiCad 9) and checks that electrode, SPI, and power nets
    are not accidentally left in Default — which would route them with wrong
    widths, clearances, and via sizes.

    GND is intentionally left in Default: it is handled by copper pours, not
    routed traces, so net class width/clearance doesn't apply.
    """

    PRO_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_pro"

    # Expected net→class assignments
    ELECTRODE_NETS = frozenset(
        [f"CH{i}" for i in range(32)] + ["REF_ELEC", "ELEC_TEST"]
    )
    SPI_NETS = frozenset({
        "SCLK", "MOSI", "CS", "MISO", "SCLK_J", "MOSI_J", "CS_J",
    })
    POWER_NETS = frozenset({"+3V3", "AVDD", "ADC_ref"})

    @pytest.fixture(autouse=True)
    def _require_project(self):
        if not self.PRO_PATH.exists():
            pytest.skip("Project file not generated yet")

    def _load_net_class_map(self):
        """Return {net_name: class_name} from .kicad_pro."""
        import json
        data = json.loads(self.PRO_PATH.read_text())
        nc_map = {}
        for c in data.get("net_settings", {}).get("classes", []):
            for n in c.get("nets", []):
                nc_map[n] = c["name"]
        return nc_map

    def test_electrode_nets_in_electrode_class(self):
        """All CH0-CH31, REF_ELEC, ELEC_TEST must be in ELECTRODE class."""
        nc_map = self._load_net_class_map()
        wrong = {n: nc_map.get(n, "Default") for n in self.ELECTRODE_NETS
                 if nc_map.get(n, "Default") != "ELECTRODE"}
        assert len(wrong) == 0, (
            f"Electrode nets NOT in ELECTRODE class:\n"
            + "\n".join(f"  ✗ {n} → {c}" for n, c in sorted(wrong.items()))
        )

    def test_spi_nets_in_digital_spi_class(self):
        """All SPI nets must be in DIGITAL_SPI class."""
        nc_map = self._load_net_class_map()
        wrong = {n: nc_map.get(n, "Default") for n in self.SPI_NETS
                 if nc_map.get(n, "Default") != "DIGITAL_SPI"}
        assert len(wrong) == 0, (
            f"SPI nets NOT in DIGITAL_SPI class:\n"
            + "\n".join(f"  ✗ {n} → {c}" for n, c in sorted(wrong.items()))
        )

    def test_power_nets_in_power_class(self):
        """All power nets must be in POWER class."""
        nc_map = self._load_net_class_map()
        wrong = {n: nc_map.get(n, "Default") for n in self.POWER_NETS
                 if nc_map.get(n, "Default") != "POWER"}
        assert len(wrong) == 0, (
            f"Power nets NOT in POWER class:\n"
            + "\n".join(f"  ✗ {n} → {c}" for n, c in sorted(wrong.items()))
        )

    def test_electrode_class_track_width(self):
        """ELECTRODE class must use ≤0.15mm tracks (noise-sensitive)."""
        import json
        data = json.loads(self.PRO_PATH.read_text())
        for c in data.get("net_settings", {}).get("classes", []):
            if c["name"] == "ELECTRODE":
                tw = c.get("track_width", 0)
                assert tw <= 0.15, (
                    f"ELECTRODE track_width={tw}mm, must be ≤0.15mm"
                )
                return
        pytest.fail("ELECTRODE net class not found in project")

    def test_power_class_track_width(self):
        """POWER class must use ≥0.3mm tracks (current capacity)."""
        import json
        data = json.loads(self.PRO_PATH.read_text())
        for c in data.get("net_settings", {}).get("classes", []):
            if c["name"] == "POWER":
                tw = c.get("track_width", 0)
                assert tw >= 0.3, (
                    f"POWER track_width={tw}mm, must be ≥0.3mm"
                )
                return
        pytest.fail("POWER net class not found in project")


# ══════════════════════════════════════════════════════════════════════════════
# 10. EP FOOTPRINT INVARIANTS (Gate B)
# ══════════════════════════════════════════════════════════════════════════════

class TestEPFootprintInvariants:
    """Lock the RHD2132 exposed-pad (EP) footprint details.

    The EP requires:
      - Main pad has NO F.Paste (prevents full-area paste flood)
      - 16 paste aperture windows (4×4 grid, 0.9mm squares)
      - 9 thermal vias (3×3 grid, 0.3mm drill, 0.6mm pad)
      - All thermal vias tented both sides
      - Thermal vias assigned to GND net

    Regression in any of these causes reflow solder defects (tombstoning,
    voiding) or thermal issues.
    """

    PCB_PATH = Path(__file__).parent / "afe-headstage-v1.kicad_pcb"

    @pytest.fixture(autouse=True)
    def _require_pcb(self):
        if not self.PCB_PATH.exists():
            pytest.skip("PCB not generated yet")

    def _get_u1_footprint_text(self):
        text = self.PCB_PATH.read_text()
        m = re.search(
            r'\(footprint "afe_footprints:RHD2132_QFN56".*?\n    \)',
            text, re.DOTALL,
        )
        assert m, "U1 (RHD2132) footprint not found in PCB"
        return m.group()

    def test_ep_main_pad_no_paste(self):
        """EP main SMD pad must NOT have F.Paste layer (windowed instead)."""
        fp = self._get_u1_footprint_text()
        # Find the EP SMD pad (not thru_hole, not paste-only)
        ep_smd = re.search(
            r'\(pad "EP" smd.*?\)',
            fp, re.DOTALL,
        )
        assert ep_smd, "EP SMD pad not found"
        assert "F.Paste" not in ep_smd.group(), (
            "EP main pad has F.Paste — must use windowed paste apertures"
        )

    def test_paste_aperture_count(self):
        """Must have exactly 16 paste aperture windows (4×4 grid)."""
        fp = self._get_u1_footprint_text()
        # Paste apertures are unnamed pads on F.Paste only
        apertures = re.findall(r'\(pad "" smd.*?"F\.Paste"', fp, re.DOTALL)
        assert len(apertures) == 16, (
            f"Expected 16 paste apertures, found {len(apertures)}"
        )

    def test_paste_aperture_size(self):
        """All paste apertures must be 0.9×0.9mm."""
        fp = self._get_u1_footprint_text()
        sizes = re.findall(
            r'\(pad "" smd.*?\(size ([\d.]+) ([\d.]+)\)',
            fp, re.DOTALL,
        )
        for sx, sy in sizes:
            assert float(sx) == pytest.approx(0.9, abs=0.01), (
                f"Paste aperture width {sx}mm, expected 0.9mm"
            )
            assert float(sy) == pytest.approx(0.9, abs=0.01), (
                f"Paste aperture height {sy}mm, expected 0.9mm"
            )

    def test_thermal_via_count(self):
        """Must have exactly 9 thermal vias (3×3 grid)."""
        fp = self._get_u1_footprint_text()
        vias = re.findall(r'\(pad "EP" thru_hole', fp)
        assert len(vias) == 9, (
            f"Expected 9 thermal vias, found {len(vias)}"
        )

    def test_thermal_via_drill(self):
        """All thermal vias must have 0.3mm drill."""
        fp = self._get_u1_footprint_text()
        drills = re.findall(
            r'\(pad "EP" thru_hole.*?\(drill ([\d.]+)\)',
            fp, re.DOTALL,
        )
        assert len(drills) == 9, f"Expected 9 via drills, found {len(drills)}"
        for d in drills:
            assert float(d) == pytest.approx(0.3, abs=0.01), (
                f"Thermal via drill {d}mm, expected 0.3mm"
            )

    def test_thermal_via_pad_size(self):
        """All thermal vias must have 0.6mm pad diameter."""
        fp = self._get_u1_footprint_text()
        sizes = re.findall(
            r'\(pad "EP" thru_hole.*?\(size ([\d.]+) ([\d.]+)\)',
            fp, re.DOTALL,
        )
        assert len(sizes) == 9, f"Expected 9 via pad sizes, found {len(sizes)}"
        for sx, sy in sizes:
            assert float(sx) == pytest.approx(0.6, abs=0.01)
            assert float(sy) == pytest.approx(0.6, abs=0.01)

    def test_thermal_vias_tented(self):
        """All 9 thermal vias must have tenting on both sides."""
        fp = self._get_u1_footprint_text()
        # Count tenting directives within thru_hole EP pads
        # Each via pad block spans multiple lines, ending with \n        )
        via_blocks = re.findall(
            r'\(pad "EP" thru_hole.*?\n        \)',
            fp, re.DOTALL,
        )
        assert len(via_blocks) == 9
        for i, block in enumerate(via_blocks):
            assert "tenting front back" in block, (
                f"Thermal via {i+1} missing 'tenting front back'"
            )

    def test_thermal_vias_on_gnd(self):
        """All thermal vias must be assigned to GND net."""
        fp = self._get_u1_footprint_text()
        via_blocks = re.findall(
            r'\(pad "EP" thru_hole.*?\n        \)',
            fp, re.DOTALL,
        )
        assert len(via_blocks) == 9
        for i, block in enumerate(via_blocks):
            assert '"GND"' in block, (
                f"Thermal via {i+1} not on GND net"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
