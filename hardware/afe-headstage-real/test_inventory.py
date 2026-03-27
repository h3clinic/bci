#!/usr/bin/env python3
"""Print authoritative test inventory.

Usage:
    python test_inventory.py          # print summary
    python test_inventory.py --check  # also verify against pytest collection

Single source of truth for test counts.  Stop doing mental arithmetic.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HW_DIR = Path(__file__).resolve().parent
TESTS_DIR = HW_DIR / "tests"


def collect_from_pytest() -> dict[str, int]:
    """Run pytest --collect-only and parse file→count mapping."""
    python = sys.executable
    result = subprocess.run(
        [python, "-m", "pytest", "--collect-only", "-q", str(TESTS_DIR)],
        capture_output=True, text=True, cwd=str(HW_DIR), timeout=60,
    )
    # Output format: "tests/file.py::Class::test_name"
    # Last line: "N tests collected"
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line:
            fname = line.split("::")[0]
            counts[fname] = counts.get(fname, 0) + 1
    return counts


def main():
    check = "--check" in sys.argv

    counts = collect_from_pytest()
    total = sum(counts.values())

    print("=" * 60)
    print("  TEST INVENTORY — AFE Headstage v1")
    print("=" * 60)
    for fname in sorted(counts):
        print(f"  {fname:<50s} {counts[fname]:>4d}")
    print("  " + "-" * 56)
    print(f"  {'TOTAL':<50s} {total:>4d}")
    print("=" * 60)

    if check:
        # Run actual tests and compare
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(TESTS_DIR), "-q", "--tb=no"],
            capture_output=True, text=True, cwd=str(HW_DIR), timeout=300,
        )
        # Parse "N passed" from output
        m = re.search(r"(\d+) passed", result.stdout)
        if m:
            ran = int(m.group(1))
            if ran == total:
                print(f"\n  ✓ pytest ran {ran} tests — matches inventory.")
            else:
                print(f"\n  ✗ MISMATCH: inventory={total}, pytest ran={ran}")
                sys.exit(1)
        else:
            print(f"\n  ✗ Could not parse pytest output:")
            print(result.stdout[-500:])
            sys.exit(1)

        if result.returncode != 0:
            print(f"\n  ✗ pytest exited with code {result.returncode}")
            # Print failures
            fail_match = re.search(r"(\d+) failed", result.stdout)
            if fail_match:
                print(f"    {fail_match.group(0)}")
            sys.exit(1)

        print("  ✓ All tests passed.")


if __name__ == "__main__":
    main()
