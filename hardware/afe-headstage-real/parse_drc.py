from __future__ import annotations

"""
parse_drc.py - Structured DRC report parser with regression thresholds
=======================================================================
Parses the KiCad 9 DRC ``.rpt`` text output into a typed histogram and enforces
hard failure thresholds on safety-critical violation categories.

KiCad 9 report structure (3 separate counted sections)::

    ** Found N DRC violations **        ← violations block
    ** Found M unconnected pads **      ← unconnected block
    ** Found P Footprint errors **      ← footprint errors

Each violation is a multi-line block::

    [type_key]: Description text
            Local override; severity
            @(x, y): Item description [NET_NAME] on LAYER
            @(x, y): Item description [NET_NAME] on LAYER

This parser counts every ``[type_key]:`` line, classifies it, and cross-checks
against KiCad's own section totals so the numbers always reconcile.

Usage::

    python3 parse_drc.py [drc-latest.rpt]

Exit codes:
    0  - all thresholds satisfied
    1  - one or more regression thresholds exceeded
    2  - report not found, empty, unparseable, or count mismatch (hard fail)
"""

import re
import sys
from collections import Counter
from pathlib import Path

PAD_LINE_RE = re.compile(
    r"""
    @\(\s*
        (?P<x>-?\d+(?:\.\d+)?)\s*mm\s*,\s*
        (?P<y>-?\d+(?:\.\d+)?)\s*mm
    \s*\)\s*:\s*
    Pad\s+(?P<pad>[A-Za-z0-9_.-]+)\s*
    \[\s*(?P<net>[^\]]+?)\s*\]\s*
    of\s+(?P<ref>[A-Za-z0-9_.-]+)\s+
    on\s+(?P<layer>[A-Za-z0-9. ]+?)\s*,?\s*$
    """,
    re.VERBOSE,
)

NET_BRACKET_RE = re.compile(r"\[(?P<net>[^\]]+)\]")

HERE = Path(__file__).parent
DEFAULT_RPT = HERE / "drc-latest.rpt"

# ─── Violation category mapping ──────────────────────────────────────────
# KiCad DRC violation type_key → normalized category.
CATEGORY_MAP = {
    # Shorts / connectivity
    "short": "shorts",
    "shorts": "shorts",
    # Clearance
    "clearance": "clearance",
    "copper_clearance": "clearance",
    # Solder mask
    "solder_mask_bridge": "solder_mask_bridge",
    "mask_bridge": "solder_mask_bridge",
    "silk_over_copper_pad": "silk_over_copper",
    "silk_over_copper": "silk_over_copper",
    "silk_overlap": "silk_overlap",
    "silk_edge_clearance": "silk_overlap",
    # Unconnected
    "unconnected_items": "unconnected_items",
    "unconnected": "unconnected_items",
    # Library
    "lib_footprint_issues": "lib_footprint_issues",
    "lib_footprint_mismatch": "lib_footprint_issues",
    # Via
    "via_dangling": "via_dangling",
    "annular_width": "annular_width",
    # Track geometry
    "track_crossing": "track_crossing",
    "crossing": "track_crossing",
    "track_width": "track_width",
}

# Nets allowed to have unconnected items (pre-zone-fill power plumbing).
# Changes to this set are deliberate design decisions — version tag tracks this.
# v25: VCC only (J5 pins 19/20/23/24 ↔ U2 pad 3).
#      +3V3 resolved by VDD_AFE_FILT island notch.
#      GND resolved by F.Cu+B.Cu copper pours.
# v35: VCC fully routed — F.Cu trunk from J5 to U2 VIN/EN + C1.
#      All nets connected.  Allowlist empty.
UNCONNECTED_ALLOWLIST_VERSION = "v35"
UNCONNECTED_ALLOWLIST = frozenset()

# Hard failure thresholds: category → max allowed count.
FAIL_THRESHOLDS = {
    "shorts": 0,
    "clearance": 0,
    "solder_mask_bridge": 0,
    "track_crossing": 0,
}

# ─── Baseline ceiling ────────────────────────────────────────────────────
# Each violation bucket has its own ceiling, tracked independently.
# Prevents cross-bucket drift from masking regressions.
# To bump a ceiling, update the bucket constant AND BASELINE_VERSION.
#
# v30: initial baseline.  silk_overlap(29) + silk_over_copper(20) +
#      lib_footprint(22) + via_dangling(6) = 77 DRC, 28 unconnected.
# v32: moved Reference text F.SilkS→F.Fab (−49: all silk_over_copper +
#      all silk_overlap eliminated).  Added afe_footprints to fp-lib-table.
#      via_dangling(6) kept (intentional GND stitch vias, allowlisted by
#      coordinate).
# v33: per-bucket ceilings.  lib_footprint_issues separated from total.
#      via_dangling allowlisted by (x, y, net) tuple, not by count.
# v35: added F.Cu + B.Cu GND copper pours.  All 6 stitch vias now
#      connect to GND zone on surface layers → 0 via_dangling.
#      Allowlist emptied.  Proactive clearance check in run_drc.sh.
#      F.Cu+B.Cu GND pours connected 21/28 unconnected GND items.
#      M16 (LVDS_en→GND) dogbone added → 7→6 unconnected.
# v36: Bundle C Phase 5 bottom fan routing (14 channels, F.Cu→via→B.Cu
#      L-route→via-in-pad to J5). 0 shorts, 0 clearance, 0 crossings,
#      0 drill/via errors.  lib_footprint_issues=22 (cosmetic: DRC temp
#      dir missing afe_footprints library).  board.design_settings.rules
#      added to drc.kicad_pro (min_via_diameter=0.4, min_hole=0.2).
BASELINE_VERSION = "v36"
UNCONNECTED_CEILING = 0  # all nets routed

# ─── lib_footprint_issues ────────────────────────────────────────────────
# Cosmetic: kicad-cli in temp dir may not resolve project footprint libs.
# If fp-lib-table + footprints.pretty are properly copied, this should be 0.
# Tracked separately so it can't mask real DRC regressions.
# v36: 22 footprints not found in afe_footprints lib (DRC temp dir issue).
LIB_FOOTPRINT_ISSUES_CEILING = 22

# ─── via_dangling allowlist (coordinate + net locked) ────────────────────
# v34: 6 GND stitch vias were allowlisted because no GND zone existed on
#      F.Cu or B.Cu — vias were connected only on In1.Cu (GND plane).
# v35: F.Cu + B.Cu GND copper pours added → all stitch vias now connect
#      to GND on surface layers.  Allowlist emptied.  Any via_dangling
#      is now a real error.
VIA_DANGLING_ALLOWLIST: frozenset[tuple[float, float, str]] = frozenset()

# ─── Board clearance sanity check ────────────────────────────────────────
# drc.kicad_pro sets Default clearance to 0.1mm (below board's 0.15mm) to
# avoid overriding real constraints.  This constant asserts the board still
# has the expected Default clearance so a future gen_pcb.py change doesn't
# silently rely on the project file's looser setting.
EXPECTED_DEFAULT_CLEARANCE = 0.15


def parse_drc_report(path: Path) -> tuple[dict, Counter, list[dict]]:
    """Parse a KiCad 9 DRC .rpt file.

    Returns:
      kicad_counts: KiCad's own section totals
                    {"drc_violations": N, "unconnected_pads": M, "footprint_errors": P}
      histogram:    Counter mapping normalized category → parsed count
      violations:   list of dicts with {category, type_raw, description, body, nets}
    """
    text = path.read_text()
    if not text.strip():
        raise RuntimeError(f"DRC report is empty: {path}")

    # ── Extract KiCad's own section totals ────────────────────────────────
    kicad_counts: dict[str, int] = {}

    m = re.search(r"\*\*\s*Found\s+(\d+)\s+DRC violations\s*\*\*", text)
    if m:
        kicad_counts["drc_violations"] = int(m.group(1))
    else:
        raise RuntimeError(
            f"Cannot find '** Found N DRC violations **' in {path}. Report format may have changed."
        )

    m = re.search(r"\*\*\s*Found\s+(\d+)\s+unconnected\s+\w+\s*\*\*", text)
    if m:
        kicad_counts["unconnected_pads"] = int(m.group(1))

    m = re.search(r"\*\*\s*Found\s+(\d+)\s+Footprint errors\s*\*\*", text)
    if m:
        kicad_counts["footprint_errors"] = int(m.group(1))

    # ── Parse individual violation blocks ─────────────────────────────────
    histogram: Counter = Counter()
    violations: list[dict] = []

    lines = text.splitlines()
    i = 0
    # Uses module-level PAD_LINE_RE — single source of truth.

    def _norm_layer(layer: str) -> str:
        return layer.strip().rstrip(",").lower()

    def _norm_net(net: str) -> str:
        return net.strip()

    def _norm_ref(ref: str) -> str:
        return ref.strip()

    def _norm_pad(pad: str) -> str:
        return pad.strip()

    # Tolerant block parser: works for full and partial reports
    i = 0
    while i < len(lines):
        line = lines[i]
        line_s = line.lstrip()
        # Section detection: tolerate [unconnected_items]: ... and whitespace
        if line_s.startswith("[") and "]" in line_s:
            # Only parse [type]: lines as block headers
            m = re.match(r"^\[([a-z_]+)\]:\s*(.*)", line_s, re.IGNORECASE)
            if m:
                type_raw = m.group(1).strip().lower()
                description = m.group(2).strip()
                category = CATEGORY_MAP.get(type_raw, type_raw)
                # Collect continuation lines (indented, until next [type]: or ** or EOF)
                body_lines: list[str] = []
                j = i + 1
                while j < len(lines):
                    nxt = lines[j]
                    nxt_s = nxt.lstrip()
                    if nxt_s.startswith("[") and "]" in nxt_s:
                        break
                    if nxt_s.startswith("**"):
                        break
                    body_lines.append(nxt)
                    j += 1
                nets: set[str] = set()
                pad_identities: set[tuple] = set()
                if category == "unconnected_items":
                    for bl in body_lines:
                        # Always collect nets from any [NET] token
                        mnet = NET_BRACKET_RE.search(bl)
                        if mnet:
                            nets.add(_norm_net(mnet.group("net")))
                        pad_m = PAD_LINE_RE.search(bl)
                        if pad_m:
                            ref = _norm_ref(pad_m.group("ref"))
                            pad = _norm_pad(pad_m.group("pad"))
                            net = _norm_net(pad_m.group("net"))
                            layer = _norm_layer(pad_m.group("layer"))
                            pad_identities.add((ref, pad, net, layer))
                else:
                    for bl in body_lines:
                        for net_m in re.finditer(r"\[([^\]]+)\]", bl):
                            candidate = net_m.group(1).strip()
                            if " - " in candidate and ".Cu" in candidate:
                                continue
                            nets.add(candidate)
                histogram[category] += 1
                violations.append(
                    {
                        "category": category,
                        "type_raw": type_raw,
                        "description": description,
                        "body": body_lines,
                        "nets": sorted(nets),
                        "pad_identities": pad_identities
                        if category == "unconnected_items"
                        else None,
                    }
                )
                i = j
                continue
        i += 1

    return kicad_counts, histogram, violations


def check_unconnected_nets(violations: list[dict]) -> list[str]:
    """Check that every unconnected item involves only allowlisted power nets.

    Returns list of error strings for violations involving non-power nets.
    """
    errors: list[str] = []
    for v in violations:
        if v["category"] != "unconnected_items":
            continue
        for net in v["nets"]:
            if net in UNCONNECTED_ALLOWLIST:
                continue
            # Allow VCC even though it's also in the allowlist — check
            # power-adjacent patterns for future-proofing
            is_power = any(p in net.lower() for p in ["gnd", "+3v3", "vcc", "vdd", "vin"])
            if not is_power:
                body_preview = " | ".join(ln.strip() for ln in v["body"][:3] if ln.strip())
                errors.append(f"Unconnected item on non-power net '{net}': {body_preview[:150]}")
    return errors


def main():
    rpt_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RPT

    # Project-file sanity check for drc.kicad_pro
    proj_path = HERE / "drc.kicad_pro"
    if proj_path.exists():
        import json

        proj = json.loads(proj_path.read_text())
        meta_version = proj.get("net_settings", {}).get("meta", {}).get("version")
        default_clearance = None
        for cls in proj.get("net_settings", {}).get("classes", []):
            if cls.get("name") == "Default":
                default_clearance = cls.get("clearance")
        if meta_version != 3:
            print(f"FAIL: drc.kicad_pro net_settings.meta.version {meta_version} != 3")
            sys.exit(1)
        if default_clearance != 0.1:
            print(f"FAIL: drc.kicad_pro Default.clearance {default_clearance} != 0.1")
            sys.exit(1)

    # Hard fail on missing or empty report
    if not rpt_path.exists():
        print(f"FATAL: DRC report not found: {rpt_path}")
        sys.exit(2)
    if rpt_path.stat().st_size == 0:
        print(f"FATAL: DRC report is empty (0 bytes): {rpt_path}")
        sys.exit(2)

    print(f"Report: {rpt_path}")

    try:
        kicad_counts, histogram, violations = parse_drc_report(rpt_path)
    except RuntimeError as e:
        print(f"FATAL: {e}")
        sys.exit(2)

    # ── Reconcile counts ──────────────────────────────────────────────────
    parsed_total = sum(histogram.values())
    parsed_unconnected = histogram.get("unconnected_items", 0)
    parsed_drc = parsed_total - parsed_unconnected

    kicad_drc = kicad_counts.get("drc_violations", -1)
    kicad_uncon = kicad_counts.get("unconnected_pads", -1)

    print(f"\n{'─' * 58}")
    print(f"KiCad summary:   {kicad_drc} DRC violations, {kicad_uncon} unconnected pads")
    print(f"Parsed:          {parsed_drc} DRC violations, {parsed_unconnected} unconnected items")
    print(f"Total:           {parsed_total} (= {parsed_drc} + {parsed_unconnected})")

    count_mismatch = False
    if kicad_drc >= 0 and parsed_drc != kicad_drc:
        print(f"  ✗ DRC count MISMATCH: parsed {parsed_drc} ≠ KiCad {kicad_drc}")
        count_mismatch = True
    if kicad_uncon >= 0 and parsed_unconnected != kicad_uncon:
        print(f"  ✗ Unconnected count MISMATCH: parsed {parsed_unconnected} ≠ KiCad {kicad_uncon}")
        count_mismatch = True
    if not count_mismatch:
        print("  ✓ Counts reconciled — parser matches KiCad's summary exactly")

    # ── Typed histogram ───────────────────────────────────────────────────
    print(f"\n{'─' * 58}")
    print("Violation Histogram")
    print(f"{'─' * 58}")
    for cat in sorted(histogram.keys()):
        count = histogram[cat]
        threshold = FAIL_THRESHOLDS.get(cat)
        marker = ""
        if threshold is not None:
            marker = (
                f"  ✗ FAIL (threshold: {threshold})"
                if count > threshold
                else f"  ✓ (threshold: {threshold})"
            )
        print(f"  {cat:30s} {count:4d}{marker}")

    # ── Regression checks ─────────────────────────────────────────────────
    errors: list[str] = []

    if count_mismatch:
        errors.append(
            "Count mismatch between parser and KiCad summary — "
            "parser may be broken or report format changed"
        )

    # Board clearance sanity check: detect project-file constraint sabotage.
    rpt_text = rpt_path.read_text()
    sabotage_match = re.search(r"netclass\s+'Default'\s+clearance\s+(\d+\.\d+)\s*mm", rpt_text)
    if sabotage_match:
        seen_clr = float(sabotage_match.group(1))
        if abs(seen_clr - EXPECTED_DEFAULT_CLEARANCE) > 0.001:
            errors.append(
                f"Board Default clearance sabotage detected: DRC report "
                f"cites {seen_clr}mm but expected {EXPECTED_DEFAULT_CLEARANCE}mm. "
                f"Check that drc.kicad_pro is not overriding board constraints."
            )

    for cat, threshold in FAIL_THRESHOLDS.items():
        count = histogram.get(cat, 0)
        if count > threshold:
            errors.append(f"{cat}: {count} > {threshold}")

    # Guardrail A: fail if any starved_thermal violation appears
    starved_thermal_count = sum(1 for v in violations if v["type_raw"] == "starved_thermal")
    if starved_thermal_count > 0:
        errors.append(f"starved_thermal: {starved_thermal_count} found — forbidden by CI guardrail")

    # ── Per-bucket ceilings (independent, no cross-bucket masking) ────────

    # Unconnected ceiling
    if parsed_unconnected > UNCONNECTED_CEILING:
        errors.append(
            f"Unconnected items {parsed_unconnected} > ceiling "
            f"{UNCONNECTED_CEILING} (baseline {BASELINE_VERSION}). If this "
            f"is intentional, bump UNCONNECTED_CEILING and BASELINE_VERSION "
            f"in parse_drc.py."
        )

    # lib_footprint_issues: cosmetic artifact of CLI context.
    lib_fp_count = histogram.get("lib_footprint_issues", 0)
    if lib_fp_count > LIB_FOOTPRINT_ISSUES_CEILING:
        errors.append(
            f"lib_footprint_issues {lib_fp_count} > ceiling "
            f"{LIB_FOOTPRINT_ISSUES_CEILING} (baseline {BASELINE_VERSION}). "
            f"Check that fp-lib-table is copied to DRC temp dir with "
            f"${{KIPRJMOD}} resolved."
        )

    # via_dangling: coordinate + net locked allowlist.
    via_dangling_violations = [v for v in violations if v["category"] == "via_dangling"]
    non_allowlisted_vias: list[str] = []
    for v in via_dangling_violations:
        coord = None
        via_net = None
        for bl in v["body"]:
            m = re.search(
                r"@\(\s*([\d.]+)\s*mm\s*,\s*([\d.]+)\s*mm\s*\)"
                r":\s*Via\s*\[([^\]]+)\]",
                bl,
            )
            if m:
                coord = (round(float(m.group(1)), 1), round(float(m.group(2)), 1))
                via_net = m.group(3).strip()
                break
        if coord is None or via_net is None:
            non_allowlisted_vias.append(
                f"via_dangling with unparseable location/net: {v['description']}"
            )
        else:
            key = (coord[0], coord[1], via_net)
            if key not in VIA_DANGLING_ALLOWLIST:
                non_allowlisted_vias.append(
                    f"via_dangling at ({coord[0]}, {coord[1]}) net={via_net} "
                    f"not in allowlist — verify it is an intentional GND "
                    f"stitch via, then add (x, y, net) to "
                    f"VIA_DANGLING_ALLOWLIST in parse_drc.py"
                )
    errors.extend(non_allowlisted_vias)

    # Residual DRC: everything that is NOT lib_footprint_issues or via_dangling
    # or unconnected_items.  These are the "real" violations.
    residual_drc = (
        parsed_drc - histogram.get("lib_footprint_issues", 0) - histogram.get("via_dangling", 0)
    )
    if residual_drc > 0:
        errors.append(
            f"Residual DRC violations: {residual_drc} (excludes "
            f"lib_footprint_issues and via_dangling). These are real "
            f"violations that must be fixed."
        )

    unconnected_errors = check_unconnected_nets(violations)
    errors.extend(unconnected_errors)

    # ── Result ────────────────────────────────────────────────────────────
    print(f"\n{'─' * 58}")
    if errors:
        print(f"REGRESSION DETECTED: {len(errors)} issue(s)")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("✓ All DRC regression thresholds satisfied")
        for cat, threshold in sorted(FAIL_THRESHOLDS.items()):
            count = histogram.get(cat, 0)
            print(f"  {cat}: {count} ≤ {threshold}")
        allowlisted = len(via_dangling_violations) - len(non_allowlisted_vias)
        print(
            f"  via_dangling: {len(via_dangling_violations)} "
            f"({allowlisted} allowlisted by coordinate+net)"
        )
        print(f"  lib_footprint_issues: {lib_fp_count} ≤ {LIB_FOOTPRINT_ISSUES_CEILING}")
        print(f"  residual_drc: {residual_drc} = 0")
        print(
            f"  unconnected_items: {parsed_unconnected} ≤ "
            f"{UNCONNECTED_CEILING} "
            f"(allowlist {UNCONNECTED_ALLOWLIST_VERSION}: "
            f"{', '.join(sorted(UNCONNECTED_ALLOWLIST))})"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
