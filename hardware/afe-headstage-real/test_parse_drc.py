from __future__ import annotations

"""
test_parse_drc.py - Unit tests for the DRC report parser
========================================================
Tests parse_drc_report() with canned .rpt snippets.
Verifies count reconciliation, net extraction, mismatch detection,
and empty/corrupt report handling.

Run:
    python3 -m pytest test_parse_drc.py -v
"""

import ast
import re
import tempfile
import textwrap
from pathlib import Path

import pytest

import parse_drc
from parse_drc import (
    BASELINE_VERSION,
    EXPECTED_DEFAULT_CLEARANCE,
    LIB_FOOTPRINT_ISSUES_CEILING,
    UNCONNECTED_CEILING,
    VIA_DANGLING_ALLOWLIST,
    check_unconnected_nets,
    parse_drc_report,
)


def test_pad_line_outside_block_not_counted():
    """A Pad line outside [unconnected_items] must not be counted as unconnected."""
    pad_line = "@(123.5000 mm, 100.5000 mm): Pad 99 [GND] of U99 on F.Cu"
    # Insert pad line into the header of the real fixture
    root = Path(__file__).parent / "tests/fixtures"
    src = root / "drc_expected_unconnected_empty.rpt"
    text = src.read_text()
    mutated = pad_line + "\n" + text
    # Should still be empty
    _, _, violations = parse_drc.parse_drc_report(Path(src))
    orig_set = normalized_unconnected_set(violations)
    # Now parse the mutated report
    with tempfile.NamedTemporaryFile("w+", delete=False) as f:
        f.write(mutated)
        f.flush()
        _, _, violations2 = parse_drc.parse_drc_report(Path(f.name))
        mutated_set = normalized_unconnected_set(violations2)
    assert orig_set == set(), f"Original fixture should be empty, got {orig_set}"
    assert mutated_set == set(), f"Pad line outside block should not be counted, got {mutated_set}"
    # Now inject the same pad line inside an [unconnected_items] block and assert it is counted
    injected = text.replace(
        "** Found 0 unconnected pads **",
        "** Found 1 unconnected pads **\n\n[unconnected_items]:\n    " + pad_line,
    )
    with tempfile.NamedTemporaryFile("w+", delete=False) as f2:
        f2.write(injected)
        f2.flush()
        _, _, violations3 = parse_drc.parse_drc_report(Path(f2.name))
        injected_set = normalized_unconnected_set(violations3)
    assert injected_set == {("U99", "99", "GND")}, (
        f"Pad line inside block should be counted, got {injected_set}"
    )


def test_future_import_first_and_unique():
    """Assert 'from __future__ import annotations' is first and unique in parse_drc.py."""
    path = Path(__file__).parent / "parse_drc.py"
    lines = path.read_text().splitlines()
    found = [i for i, l in enumerate(lines) if l.strip() == "from __future__ import annotations"]
    assert len(found) == 1, (
        f"Expected exactly one 'from __future__ import annotations',"
        f" found {len(found)} at lines {found}"
    )
    # Find first non-empty, non-comment, non-docstring line
    i = 0
    in_docstring = False
    while i < len(lines):
        l = lines[i].strip()
        if l.startswith('"""') or l.startswith("'''"):
            if not in_docstring:
                in_docstring = True
            elif in_docstring:
                in_docstring = False
            i += 1
            continue
        if in_docstring or not l or l.startswith("#"):
            i += 1
            continue
        break
    assert lines[i].strip() == "from __future__ import annotations", (
        f"First non-comment/docstring line is not future import: {lines[i].strip()}"
    )


def test_single_pad_line_re_definition():
    """Assert parse_drc.py has exactly one PAD_LINE_RE = assignment at module scope."""
    path = Path(__file__).parent / "parse_drc.py"
    text = path.read_text()
    hits = re.findall(r"^PAD_LINE_RE\s*=", text, flags=re.M)
    assert len(hits) == 1, (
        f"Expected exactly one 'PAD_LINE_RE =' at module scope, found {len(hits)}"
    )


def test_docstring_immediately_after_future_import():
    """Assert parse_drc.py AST: stmt[0] is future import, stmt[1] is module docstring."""
    path = Path(__file__).parent / "parse_drc.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    body = tree.body
    assert len(body) >= 2, "Module must have at least 2 top-level statements"

    # First statement: from __future__ import annotations
    stmt0 = body[0]
    assert isinstance(stmt0, ast.ImportFrom), (
        f"First statement should be 'from __future__ import', got {type(stmt0).__name__}"
    )
    assert stmt0.module == "__future__", (
        f"First import should be from __future__, got {stmt0.module!r}"
    )

    # Second statement: module docstring (Expr wrapping a Constant str)
    stmt1 = body[1]
    assert isinstance(stmt1, ast.Expr), (
        f"Second statement should be a docstring expression, got {type(stmt1).__name__}"
    )
    assert isinstance(stmt1.value, ast.Constant) and isinstance(stmt1.value.value, str), (
        "Second statement should be a string constant (module docstring)"
    )


def test_no_f_rule_suppression_in_legacy_per_file_ignores():
    from pathlib import Path

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads((Path(__file__).parent / "pyproject.toml").read_text(encoding="utf-8"))

    tool = data.get("tool", {})
    ruff = tool.get("ruff", {})
    lint = ruff.get("lint", {})
    per_file = lint.get("per-file-ignores", {})
    extend_per_file = lint.get("extend-per-file-ignores")

    assert isinstance(per_file, dict)
    assert extend_per_file is None or isinstance(extend_per_file, dict)

    def scan(table: dict) -> None:
        for key, val in table.items():
            if not isinstance(key, str):
                continue
            last = key.replace("\\", "/").split("/")[-1]
            if not (last.startswith("gen_") or last.startswith("verify_")):
                continue

            if isinstance(val, str):
                ignores = [val]
            elif isinstance(val, list):
                assert all(isinstance(x, str) for x in val)
                ignores = val
            else:
                raise AssertionError(f"Invalid ignore type under {key}: {type(val)}")

            bad = [c.strip().strip('"').strip("'") for c in ignores if c.strip().startswith("F")]
            assert not bad, f"Forbidden F* ignores under {key}: {bad}"

    scan(per_file)
    if extend_per_file is not None:
        scan(extend_per_file)


def test_exclude_knobs_forbidden():
    from pathlib import Path

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads((Path(__file__).parent / "pyproject.toml").read_text(encoding="utf-8"))

    tool = data.get("tool", {})
    ruff = tool.get("ruff", {})
    lint = ruff.get("lint", {})

    forbidden = [
        ("tool.ruff.exclude", ruff.get("exclude")),
        ("tool.ruff.extend-exclude", ruff.get("extend-exclude")),
        ("tool.ruff.force-exclude", ruff.get("force-exclude")),
        ("tool.ruff.lint.exclude", lint.get("exclude")),
        ("tool.ruff.lint.extend-exclude", lint.get("extend-exclude")),
        ("tool.ruff.lint.extend-per-file-ignores", lint.get("extend-per-file-ignores")),
    ]
    present = [k for k, v in forbidden if v is not None]
    assert not present, f"Forbidden config knobs present: {present}"


def parse_one_pad_identity(line: str):
    m = parse_drc.PAD_LINE_RE.search(line)
    assert m, f"PAD_LINE_RE did not match: {line!r}"
    ref = m.group("ref").strip()
    pad = m.group("pad").strip()
    net = m.group("net").strip()
    return (ref, pad, net)


@pytest.fixture
def tmp_rpt(tmp_path):
    """Factory fixture: write text to a temp .rpt file and return its Path."""

    def _write(content: str) -> Path:
        p = tmp_path / "test.rpt"
        p.write_text(textwrap.dedent(content))
        return p

    return _write


# ── Basic 2-section report ──────────────────────────────────────────────

BASIC_REPORT = """\
** Found 3 DRC violations **

[silk_overlap]: Silk text overlaps another item on layer "F.SilkS"; ...
    @(120.00 mm, 98.00 mm): Text "R1" on F.SilkS
    @(121.00 mm, 98.00 mm): Text "R2" on F.SilkS

[clearance]: Clearance violation ...
    @(110.50 mm, 91.00 mm): Pad 1 [/CH0] of TP4 on F.Cu
    @(110.20 mm, 91.30 mm): Pad 1 [/CH1] of TP5 on F.Cu

[silk_over_copper]: Silkscreen clipped by solder mask ...
    @(113.00 mm, 93.00 mm): Text "R1" on F.SilkS

** Found 2 unconnected pads **

[unconnected_items]: Missing connection between items
    @(107.50 mm, 87.50 mm): Pad 2 [GND] of J5 on F.Cu [F.Cu - B.Cu]
    @(113.51 mm, 93.00 mm): Pad 2 [GND] of R1 on F.Cu [F.Cu - B.Cu]

[unconnected_items]: Missing connection between items
    @(122.00 mm, 87.00 mm): Pad 1 [+3V3] of FB1 on F.Cu [F.Cu - B.Cu]
    @(121.10 mm, 102.50 mm): Pad 1 [+3V3] of C5 on F.Cu [F.Cu - B.Cu]

** Found 0 Footprint errors **

** End of Report **
"""


def test_basic_counts(tmp_rpt):
    """Parser reconciles KiCad section totals with parsed counts."""
    path = tmp_rpt(BASIC_REPORT)
    kicad_counts, histogram, violations = parse_drc_report(path)

    # KiCad section totals
    assert kicad_counts["drc_violations"] == 3
    assert kicad_counts["unconnected_pads"] == 2
    assert kicad_counts["footprint_errors"] == 0

    # Parsed histogram
    assert histogram["silk_overlap"] == 1
    assert histogram["clearance"] == 1
    assert histogram["silk_over_copper"] == 1
    assert histogram["unconnected_items"] == 2

    # Total reconciliation: 3 DRC + 2 unconnected = 5 total parsed
    total = sum(histogram.values())
    assert total == 5
    drc_parsed = total - histogram["unconnected_items"]
    assert drc_parsed == kicad_counts["drc_violations"]
    assert histogram["unconnected_items"] == kicad_counts["unconnected_pads"]


def test_net_extraction(tmp_rpt):
    """Net names extracted from body lines, layer annotations filtered."""
    path = tmp_rpt(BASIC_REPORT)
    _, _, violations = parse_drc_report(path)

    # Find the clearance violation — should have /CH0 and /CH1
    clearance_v = [v for v in violations if v["category"] == "clearance"]
    assert len(clearance_v) == 1
    assert "/CH0" in clearance_v[0]["nets"]
    assert "/CH1" in clearance_v[0]["nets"]

    # Find unconnected — should have GND, +3V3 but NOT "F.Cu - B.Cu"
    uncon = [v for v in violations if v["category"] == "unconnected_items"]
    assert len(uncon) == 2
    all_nets = set()
    for u in uncon:
        all_nets.update(u["nets"])
    assert "GND" in all_nets
    assert "+3V3" in all_nets
    # Layer spans must be filtered out
    assert "F.Cu - B.Cu" not in all_nets


def test_unconnected_allowlist(tmp_rpt):
    """Power nets on allowlist pass; non-power nets are flagged."""
    path = tmp_rpt(BASIC_REPORT)
    _, _, violations = parse_drc_report(path)

    # GND and +3V3 are on the allowlist — should produce no errors
    errors = check_unconnected_nets(violations)
    assert errors == []


REPORT_WITH_SIGNAL_UNCONNECTED = """\
** Found 0 DRC violations **

** Found 1 unconnected pads **

[unconnected_items]: Missing connection between items
    @(116.00 mm, 95.00 mm): Pad A1 [/CH0] of U1 on F.Cu
    @(112.49 mm, 93.00 mm): Pad 1 [/CH0] of R1 on F.Cu

** Found 0 Footprint errors **

** End of Report **
"""


def test_signal_net_unconnected_flagged(tmp_rpt):
    """Unconnected items on signal nets (not on allowlist) are errors."""
    path = tmp_rpt(REPORT_WITH_SIGNAL_UNCONNECTED)
    _, _, violations = parse_drc_report(path)

    errors = check_unconnected_nets(violations)
    assert len(errors) == 1
    assert "/CH0" in errors[0]


# ── Count mismatch detection ───────────────────────────────────────────

MISMATCHED_REPORT = """\
** Found 5 DRC violations **

[silk_overlap]: Text overlap
    @(120.00 mm, 98.00 mm): Text "R1" on F.SilkS

[silk_overlap]: Text overlap
    @(121.00 mm, 98.00 mm): Text "R2" on F.SilkS

** Found 0 unconnected pads **

** Found 0 Footprint errors **

** End of Report **
"""


def test_count_mismatch_detected(tmp_rpt):
    """Parser detects when parsed count doesn't match KiCad's declared total."""
    path = tmp_rpt(MISMATCHED_REPORT)
    kicad_counts, histogram, _ = parse_drc_report(path)

    # KiCad says 5 but we only parsed 2
    assert kicad_counts["drc_violations"] == 5
    total = sum(histogram.values())
    parsed_drc = total - histogram.get("unconnected_items", 0)
    assert parsed_drc == 2
    assert parsed_drc != kicad_counts["drc_violations"]


# ── Empty / corrupt report handling ─────────────────────────────────────


def test_empty_report_raises(tmp_rpt):
    """Empty report file raises RuntimeError."""
    path = tmp_rpt("")
    with pytest.raises(RuntimeError, match="empty"):
        parse_drc_report(path)


def test_whitespace_only_raises(tmp_rpt):
    """Whitespace-only report raises RuntimeError."""
    path = tmp_rpt("   \n\n  \n")
    with pytest.raises(RuntimeError, match="empty"):
        parse_drc_report(path)


def test_missing_drc_header_raises(tmp_rpt):
    """Report without DRC violations header raises RuntimeError."""
    path = tmp_rpt("Some random text\nno headers here\n")
    with pytest.raises(RuntimeError, match="Cannot find"):
        parse_drc_report(path)


# ── Edge cases ──────────────────────────────────────────────────────────

ZERO_REPORT = """\
** Found 0 DRC violations **

** Found 0 unconnected pads **

** Found 0 Footprint errors **

** End of Report **
"""


def test_zero_violations(tmp_rpt):
    """Clean report with zero violations in all sections."""
    path = tmp_rpt(ZERO_REPORT)
    kicad_counts, histogram, violations = parse_drc_report(path)

    assert kicad_counts["drc_violations"] == 0
    assert kicad_counts["unconnected_pads"] == 0
    assert kicad_counts["footprint_errors"] == 0
    assert sum(histogram.values()) == 0
    assert violations == []


MULTI_NET_BODY = """\
** Found 1 DRC violations **

[clearance]: Clearance violation (min 0.20mm; actual 0.15mm)
    @(110.00 mm, 91.00 mm): Pad 1 [/CH0] of TP4 on F.Cu
    @(110.00 mm, 93.50 mm): Pad 1 [/CH1] of TP5 on F.Cu
    @(112.49 mm, 93.00 mm): Pad 2 [GND] of R1 on F.Cu [F.Cu - B.Cu]

** Found 0 unconnected pads **

** Found 0 Footprint errors **

** End of Report **
"""


def test_multiple_nets_in_one_violation(tmp_rpt):
    """Single violation block can reference multiple nets."""
    path = tmp_rpt(MULTI_NET_BODY)
    _, _, violations = parse_drc_report(path)

    assert len(violations) == 1
    nets = violations[0]["nets"]
    assert "/CH0" in nets
    assert "/CH1" in nets
    assert "GND" in nets
    assert "F.Cu - B.Cu" not in nets


def test_baseline_constants_are_positive():
    """Sanity: ceiling values must be non-negative integers with a version tag."""
    assert isinstance(LIB_FOOTPRINT_ISSUES_CEILING, int)
    assert isinstance(UNCONNECTED_CEILING, int)
    assert LIB_FOOTPRINT_ISSUES_CEILING >= 0
    assert UNCONNECTED_CEILING >= 0
    assert BASELINE_VERSION.startswith("v")


def test_via_dangling_allowlist_is_frozen():
    """VIA_DANGLING_ALLOWLIST is a frozenset (empty after v35 GND pours)."""
    assert isinstance(VIA_DANGLING_ALLOWLIST, frozenset)
    # v35: allowlist should be empty — all stitch vias connect via GND pours
    for entry in VIA_DANGLING_ALLOWLIST:
        assert isinstance(entry, tuple)
        assert len(entry) == 3, f"Expected (x, y, net) tuple, got {entry}"
        assert isinstance(entry[0], (int, float))
        assert isinstance(entry[1], (int, float))
        assert isinstance(entry[2], str)
        assert entry[2], "Net name must not be empty"


def test_via_dangling_allowlist_is_empty():
    """v35: allowlist is empty — F.Cu+B.Cu GND pours eliminated all via_dangling."""
    assert len(VIA_DANGLING_ALLOWLIST) == 0


VIA_DANGLING_REPORT = """\
** Found 2 DRC violations **

[via_dangling]: Via is not connected or connected on only one layer
    Local override; warning
    @(108.0000 mm, 114.0000 mm): Via [GND] on F.Cu - B.Cu

[via_dangling]: Via is not connected or connected on only one layer
    Local override; warning
    @(999.0000 mm, 999.0000 mm): Via [GND] on F.Cu - B.Cu

** Found 0 unconnected pads **

** Found 0 Footprint errors **

** End of Report **
"""


def test_via_dangling_non_allowlisted_detected(tmp_rpt):
    """All via_dangling are flagged (allowlist is empty in v35)."""
    path = tmp_rpt(VIA_DANGLING_REPORT)
    _, _, violations = parse_drc_report(path)

    via_violations = [v for v in violations if v["category"] == "via_dangling"]
    assert len(via_violations) == 2

    # With empty allowlist, BOTH vias are non-allowlisted
    import re as _re

    non_allowlisted = []
    for v in via_violations:
        for bl in v["body"]:
            m = _re.search(
                r"@\(\s*([\d.]+)\s*mm\s*,\s*([\d.]+)\s*mm\s*\)"
                r":\s*Via\s*\[([^\]]+)\]",
                bl,
            )
            if m:
                key = (round(float(m.group(1)), 1), round(float(m.group(2)), 1), m.group(3).strip())
                if key not in VIA_DANGLING_ALLOWLIST:
                    non_allowlisted.append(key)
    assert len(non_allowlisted) == 2
    assert (108.0, 114.0, "GND") in non_allowlisted
    assert (999.0, 999.0, "GND") in non_allowlisted


VIA_DANGLING_WRONG_NET_REPORT = """\
** Found 1 DRC violations **

[via_dangling]: Via is not connected or connected on only one layer
    Local override; warning
    @(108.0000 mm, 114.0000 mm): Via [+3V3] on F.Cu - B.Cu

** Found 0 unconnected pads **

** Found 0 Footprint errors **

** End of Report **
"""


def test_via_dangling_wrong_net_rejected(tmp_rpt):
    """Via at allowlisted coordinate but wrong net is rejected."""
    path = tmp_rpt(VIA_DANGLING_WRONG_NET_REPORT)
    _, _, violations = parse_drc_report(path)

    via_violations = [v for v in violations if v["category"] == "via_dangling"]
    assert len(via_violations) == 1

    # (108.0, 114.0) is allowlisted but only for GND, not +3V3
    import re as _re

    non_allowlisted = []
    for v in via_violations:
        for bl in v["body"]:
            m = _re.search(
                r"@\(\s*([\d.]+)\s*mm\s*,\s*([\d.]+)\s*mm\s*\)"
                r":\s*Via\s*\[([^\]]+)\]",
                bl,
            )
            if m:
                key = (round(float(m.group(1)), 1), round(float(m.group(2)), 1), m.group(3).strip())
                if key not in VIA_DANGLING_ALLOWLIST:
                    non_allowlisted.append(key)
    assert len(non_allowlisted) == 1
    assert non_allowlisted[0] == (108.0, 114.0, "+3V3")


LIB_FP_REPORT = """\
** Found 1 DRC violations **

[lib_footprint_issues]: The current configuration does not include the footprint library 'afe_footprints'.
    Local override; warning
    @(120.0000 mm, 98.0000 mm): Footprint U1

** Found 0 unconnected pads **

** Found 0 Footprint errors **

** End of Report **
"""


def test_lib_footprint_counted_separately(tmp_rpt):
    """lib_footprint_issues tracked in its own bucket, not residual DRC."""
    path = tmp_rpt(LIB_FP_REPORT)
    _, histogram, _ = parse_drc_report(path)
    assert histogram["lib_footprint_issues"] == 1
    # Total DRC = 1, but residual = 1 - 1 (lib_fp) - 0 (via_dangling) = 0
    parsed_drc = sum(histogram.values()) - histogram.get("unconnected_items", 0)
    residual = (
        parsed_drc - histogram.get("lib_footprint_issues", 0) - histogram.get("via_dangling", 0)
    )
    assert residual == 0


def test_expected_default_clearance_is_sane():
    def test_dru_rule3_scoped_to_j5_gnd_14_18_22():
        """Guardrail B: Rule 3 in .kicad_dru must be scoped to only J5 GND pads 14,18,22."""
        dru_path = Path(__file__).parent / "afe-headstage-real.kicad_dru"
        text = dru_path.read_text()
        # Find Rule 3 block
        rule3 = None
        for line in text.splitlines():
            if 'rule "J5_GND_starved_pads_14_18_22"' in line:
                rule3 = line
                break
        assert rule3 is not None, "Rule 3 not found in .kicad_dru"
        # Check condition contains all required selectors
        assert "memberOfFootprint('J5')" in rule3
        assert "A.NetName == 'GND'" in rule3
        assert "A.Pad_Number == '14'" in rule3
        assert "A.Pad_Number == '18'" in rule3
        assert "A.Pad_Number == '22'" in rule3
        # Check no broad selectors (should not match all J5 pads)
        assert "memberOfFootprint('J5') && B.memberOfFootprint('J5')" not in rule3
        # Check no missing pad filter
        pad_filter_count = rule3.count("A.Pad_Number ==")
        assert pad_filter_count == 3, f"Rule 3 pad filter count: {pad_filter_count} (should be 3)"

    """Board's expected Default clearance constant is reasonable."""
    assert isinstance(EXPECTED_DEFAULT_CLEARANCE, (int, float))
    assert 0.05 <= EXPECTED_DEFAULT_CLEARANCE <= 0.5


PAD_LINE_RE = re.compile(
    r"""@\(\s*(?P<x>[-\d.]+)\s*mm\s*,\s*(?P<y>[-\d.]+)\s*mm\s*\)\s*:\s*
        Pad\s+(?P<pad>[A-Za-z0-9_.-]+)\s*\[\s*(?P<net>[^\]]+?)\s*\]\s*
        of\s+(?P<ref>[A-Za-z0-9_.-]+)\s+on\s+(?P<layer>[A-Za-z0-9. ]+?)\s*,?\s*$""",
    re.VERBOSE,
)


def _norm_layer(layer: str) -> str:
    return layer.strip().rstrip(",").lower()


def _norm_net(net: str) -> str:
    return net.strip()


def _norm_ref(ref: str) -> str:
    return ref.strip()


def _norm_pad(pad: str) -> str:
    return pad.strip()


def normalized_unconnected_set(report_or_violations):
    # Accept either a report string or violations list
    if isinstance(report_or_violations, str):
        # Parse as a report string (tolerant block detection)
        lines = report_or_violations.splitlines()
        in_unconnected = False
        actual_unconnected = set()
        for line in lines:
            line_s = line.lstrip()
            if line_s.startswith("[") and "]" in line_s:
                tag = (line_s.split("]", 1)[0] + "]").strip()
                in_unconnected = tag == "[unconnected_items]"
                continue
            if not in_unconnected:
                continue
            pad_m = PAD_LINE_RE.search(line)
            if pad_m:
                ref = _norm_ref(pad_m.group("ref"))
                pad = _norm_pad(pad_m.group("pad"))
                net = _norm_net(pad_m.group("net"))
                actual_unconnected.add((ref, pad, net))
        return actual_unconnected
    # Otherwise, assume violations list from parse_drc_report
    actual_unconnected = set()
    for v in report_or_violations:
        if v["category"] == "unconnected_items":
            pad_ids = v.get("pad_identities")
            if pad_ids is not None:
                for ref, pad, net, layer in pad_ids:
                    actual_unconnected.add((ref, pad, net))
    return actual_unconnected


def test_unconnected_extracts_expected_from_real_fixture():
    rpt_path = Path(__file__).parent / "tests/fixtures/drc_unconnected_known_vcc.rpt"
    _, _, violations = parse_drc.parse_drc_report(rpt_path)
    got = normalized_unconnected_set(violations)
    expected = {
        ("J5", "19", "VCC"),
        ("J5", "20", "VCC"),
        ("J5", "23", "VCC"),
        ("J5", "24", "VCC"),
        ("U2", "3", "VCC"),
    }
    assert got == expected, f"Expected {expected}, got {got}"


def test_unconnected_pad_line_format_drift_variants_parse_same():
    header = "[unconnected_items]: Missing connection between items\n"
    base_line = "@(123.5000 mm, 100.5000 mm): Pad M16 [GND] of U1 on F.Cu"
    variants = [
        base_line,
        "@( 123.5000mm,100.5000mm ):  Pad   M16   [GND]  of   U1  on  F.Cu ",
        "@(123.5000 mm, 100.5000 mm): Pad M16 [GND] of U1 on F.Cu,",
        "    @(123.5000 mm, 100.5000 mm): Pad M16 [GND] of U1 on F.Cu",  # leading spaces
        "@(123.5000 mm, 100.5000 mm): Pad M16-2 [GND] of U1-1 on F.Cu,",
        "@(123.5000 mm, 100.5000 mm): Pad M16.2 [GND] of U1.1 on F.Cu,",
    ]
    for v in variants:
        rpt = header + "    Local override; error\n    " + v + "\n"
        got = normalized_unconnected_set(rpt)
        assert got == {parse_one_pad_identity(v)}, (
            f"variant failed:\n{v}\n\ngot={got}\nwant={{parse_one_pad_identity(v)}}"
        )


EXPECTED_UNCONNECTED = {
    ("J5", "19", "VCC"),
    ("J5", "20", "VCC"),
    ("J5", "23", "VCC"),
    ("J5", "24", "VCC"),
    ("U2", "3", "VCC"),
}


def test_strict_unconnected_matches_empty_fixture():
    root = Path(__file__).parent / "tests/fixtures"
    rpt = root / "drc_expected_unconnected_empty.rpt"
    assert rpt.exists(), "Fixture missing; tests need a real-format report fixture"
    _, _, violations = parse_drc.parse_drc_report(rpt)
    got = normalized_unconnected_set(violations)
    expected = set()
    assert got == expected, (
        "Expected unconnected set mismatch.\n"
        f"Missing: {sorted(expected - got)}\n"
        f"Unexpected: {sorted(got - expected)}"
    )


def test_strict_unconnected_rejects_mutation_on_empty_fixture(tmp_path):
    root = Path(__file__).parent / "tests/fixtures"
    src = root / "drc_expected_unconnected_empty.rpt"
    dst = tmp_path / "mutated.rpt"
    text = src.read_text()
    # Add a fake unconnected pad line to simulate regression
    replacement = (
        "** Found 1 unconnected pads **\n\n"
        "[unconnected_items]: Missing connection between items\n"
        "    @(108.00 mm, 119.00 mm): Pad 19 [VCC] of J5 on F.Cu\n"
    )
    text = text.replace(
        "** Found 0 unconnected pads **",
        replacement,
    )
    dst.write_text(text)
    _, _, violations = parse_drc.parse_drc_report(dst)
    got = normalized_unconnected_set(violations)
    expected = set()
    assert got != expected, "Mutation should cause unconnected set mismatch"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
