#!/usr/bin/env python3
"""verify_schematic.py — automated schematic connectivity validator.

Parses the KiCad XML netlist (.xml) exported from the schematic and checks:
  1. Every TestPoint pin is on a named net (never 'unconnected-*').
  2. Power chain continuity — critical nets contain the expected pads.
  3. Only allow-listed pads may appear on 'unconnected-*' nets.

Exits 0 on success, 1 on failure (with diagnostics).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# ── Expected power chain membership ──────────────────────────────────────────
POWER_CHAIN = {
    "+3V3": {
        "must_include": [
            ("U2", "5"),       # ADP151 VOUT
            ("FB1", "2"),      # Ferrite bead input
        ],
    },
    "/VDD_AFE_FILT": {
        "must_include": [
            ("FB1", "1"),      # Ferrite bead output
            ("U1", "M15"),     # RHD VDD
            ("U1", "N15"),     # RHD VDD
            ("U1", "N2"),      # RHD VDD
        ],
    },
    "VCC": {
        "must_include": [
            ("U2", "1"),       # ADP151 VIN
            ("J5", "19"),      # B2B VIN
        ],
    },
    "GND": {
        "must_include": [
            ("U1", "L1"),      # RHD GND (one of many)
            ("U2", "2"),       # ADP151 GND
        ],
    },
}

# ── Pads allowed to be unconnected ───────────────────────────────────────────
# Format: {("REF", "PIN"), ...}
UNCONNECTED_ALLOWLIST = set()
# J5 unused B2B pins (no-connect markers on CS2, MISO2_A/B, TEST_SHORT_EN, CAL_EN, SPARE)
for pin in ("9", "15", "16", "27", "28", "29"):
    UNCONNECTED_ALLOWLIST.add(("J5", pin))
# U1 (RHD2164) unused auxiliary and extra input pins
for pin in ("N3", "N4", "N5", "N16", "H1", "H2", "H3", "H4"):
    UNCONNECTED_ALLOWLIST.add(("U1", pin))


def parse_netlist(xml_path: Path) -> dict:
    """Parse KiCad XML netlist.  Returns dict with 'nets' and 'components'."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    nets = {}  # net_name -> [(ref, pin), ...]
    for net_el in root.iter("net"):
        net_name = net_el.get("name", "")
        pads = []
        for node in net_el.findall("node"):
            pads.append((node.get("ref"), node.get("pin")))
        nets[net_name] = pads

    components = {}  # ref -> {value, footprint, lib}
    for comp in root.iter("comp"):
        ref = comp.get("ref")
        value_el = comp.find("value")
        fp_el = comp.find("footprint")
        components[ref] = {
            "value": value_el.text if value_el is not None else "",
            "footprint": fp_el.text if fp_el is not None else "",
        }

    return {"nets": nets, "components": components}


def check_testpoints(data: dict) -> list[str]:
    """Every TestPoint pad must be on a real named net."""
    errors = []
    components = data["components"]
    nets = data["nets"]

    # Build reverse map: (ref, pin) -> net_name
    pad_to_net = {}
    for net_name, pads in nets.items():
        for ref, pin in pads:
            pad_to_net[(ref, pin)] = net_name

    for ref, info in components.items():
        if not ref.startswith("TP"):
            continue
        net = pad_to_net.get((ref, "1"), "")
        if not net or net.startswith("unconnected-"):
            errors.append(f"TestPoint {ref}.1 is on net '{net}' — expected a named net")

    return errors


def check_power_chains(data: dict) -> list[str]:
    """Verify that critical nets contain all required pads."""
    errors = []
    nets = data["nets"]

    for net_name, spec in POWER_CHAIN.items():
        if net_name not in nets:
            errors.append(f"Power net '{net_name}' not found in netlist")
            continue

        pads_on_net = set(nets[net_name])
        for ref, pin in spec["must_include"]:
            if (ref, pin) not in pads_on_net:
                errors.append(
                    f"Power chain '{net_name}' missing pad {ref}.{pin}"
                )

    return errors


def check_unconnected(data: dict) -> list[str]:
    """Flag any unconnected pads not on the allowlist."""
    errors = []
    nets = data["nets"]

    for net_name, pads in nets.items():
        if not net_name.startswith("unconnected-"):
            continue
        for ref, pin in pads:
            if (ref, pin) not in UNCONNECTED_ALLOWLIST:
                errors.append(
                    f"Unexpected unconnected pad: {ref}.{pin} on net '{net_name}'"
                )

    return errors


def check_fb1_bridge(data: dict) -> list[str]:
    """FB1 must bridge +3V3 (pin 2) to /VDD_AFE_FILT (pin 1)."""
    errors = []
    pad_to_net = {}
    for net_name, pads in data["nets"].items():
        for ref, pin in pads:
            pad_to_net[(ref, pin)] = net_name

    fb1_p1_net = pad_to_net.get(("FB1", "1"), "")
    fb1_p2_net = pad_to_net.get(("FB1", "2"), "")
    if fb1_p2_net != "+3V3":
        errors.append(f"FB1.2 should be on +3V3, got '{fb1_p2_net}'")
    if fb1_p1_net != "/VDD_AFE_FILT":
        errors.append(f"FB1.1 should be on /VDD_AFE_FILT, got '{fb1_p1_net}'")
    return errors


def check_u1_vdd_isolation(data: dict) -> list[str]:
    """No U1 VDD pad (M15, N2, N15) should be on +3V3 — they must be on /VDD_AFE_FILT."""
    errors = []
    nets = data["nets"]
    plus3v3_pads = set(nets.get("+3V3", []))
    u1_vdd_pins = [("U1", "M15"), ("U1", "N2"), ("U1", "N15")]
    for ref, pin in u1_vdd_pins:
        if (ref, pin) in plus3v3_pads:
            errors.append(f"{ref}.{pin} is on +3V3 — must be on /VDD_AFE_FILT")
    return errors


def check_adc_ref(data: dict) -> list[str]:
    """ADC_ref (U1.N17) should be on Net-(U1-ADC_ref), not GND or a power net."""
    errors = []
    pad_to_net = {}
    for net_name, pads in data["nets"].items():
        for ref, pin in pads:
            pad_to_net[(ref, pin)] = net_name

    adc_net = pad_to_net.get(("U1", "N17"), "")
    if adc_net != "Net-(U1-ADC_ref)":
        errors.append(f"U1.N17 (ADC_ref) should be on 'Net-(U1-ADC_ref)', got '{adc_net}'")
    return errors


def main():
    if len(sys.argv) < 2:
        # Default: look for the netlist next to this script
        here = Path(__file__).resolve().parent
        xml_path = here / "afe-headstage-real.xml"
    else:
        xml_path = Path(sys.argv[1])

    if not xml_path.exists():
        print(f"ERROR: Netlist not found: {xml_path}", file=sys.stderr)
        print("Run:  kicad-cli sch export netlist --format kicadxml <sch>", file=sys.stderr)
        sys.exit(1)

    data = parse_netlist(xml_path)
    print(f"Parsed {len(data['nets'])} nets, {len(data['components'])} components")

    all_errors = []
    all_errors += check_testpoints(data)
    all_errors += check_power_chains(data)
    all_errors += check_unconnected(data)
    all_errors += check_fb1_bridge(data)
    all_errors += check_u1_vdd_isolation(data)
    all_errors += check_adc_ref(data)

    if all_errors:
        print(f"\n✗ verify_schematic: {len(all_errors)} error(s):", file=sys.stderr)
        for e in all_errors:
            print(f"  • {e}", file=sys.stderr)
        sys.exit(1)
    else:
        print("✓ verify_schematic: all checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
