from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib  # py>=3.11
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


LEGACY_KEY_PREFIXES = ("gen_", "verify_")
FORBIDDEN_PREFIX = "F"


def _is_legacy_key(key: str) -> bool:
    last = key.replace("\\", "/").split("/")[-1]
    return any(last.startswith(p) for p in LEGACY_KEY_PREFIXES)


def _iter_codes(val: object) -> tuple[list[str], bool]:
    """Return (codes, ok_type)."""
    if isinstance(val, str):
        return [val], True
    if isinstance(val, list):
        if all(isinstance(x, str) for x in val):
            return list(val), True
        return [x for x in val if isinstance(x, str)], False
    return [], False


def _strip_code(code: str) -> str:
    return code.strip().strip('"').strip("'")


def main() -> int:
    pyproject = Path(__file__).with_name("pyproject.toml")
    if not pyproject.exists():
        print(f"ERROR: {pyproject} not found", file=sys.stderr)
        return 2

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    tool = data.get("tool", {})
    ruff = tool.get("ruff", {})
    lint = ruff.get("lint", {})

    # Guard 0a: union of select + extend-select must include F (or ALL).
    effective_select: set[str] = set()
    for sel_field in ("select", "extend-select"):
        sel_val = lint.get(sel_field)
        if sel_val is None:
            continue
        codes, ok = _iter_codes(sel_val)
        if not ok:
            print(
                f"ERROR: [tool.ruff.lint].{sel_field} must be str or list[str]",
                file=sys.stderr,
            )
            return 2
        effective_select |= {_strip_code(c) for c in codes}

    if effective_select and "ALL" not in effective_select and "F" not in effective_select:
        print(
            "ERROR: [tool.ruff.lint] select ∪ extend-select does not include 'F' (policy)",
            file=sys.stderr,
        )
        return 1

    # Guard 0b: all exclude variants are forbidden — use per-file-ignores.
    for exc_field, exc_table in [
        ("exclude", ruff),
        ("extend-exclude", ruff),
        ("force-exclude", ruff),
        ("exclude", lint),
        ("extend-exclude", lint),
    ]:
        if exc_table.get(exc_field) is not None:
            print(
                f"ERROR: {exc_field} is forbidden by policy; use per-file-ignores instead",
                file=sys.stderr,
            )
            return 1

    # Guard 0c: extend-per-file-ignores is forbidden.
    if lint.get("extend-per-file-ignores") is not None:
        print(
            "ERROR: extend-per-file-ignores is forbidden by policy; use per-file-ignores instead",
            file=sys.stderr,
        )
        return 1

    # Guard 1: global ignore / extend-ignore cannot contain any F*.
    for field in ("ignore", "extend-ignore"):
        val = lint.get(field)
        if val is None:
            continue
        codes, ok = _iter_codes(val)
        if not ok:
            print(
                f"ERROR: [tool.ruff.lint].{field} must be str or list[str]",
                file=sys.stderr,
            )
            return 2
        bad = [_strip_code(c) for c in codes if _strip_code(c).startswith(FORBIDDEN_PREFIX)]
        if bad:
            print(
                f"ERROR: global {field} contains forbidden F* ignores: {sorted(bad)}",
                file=sys.stderr,
            )
            return 1

    # Guard 2: legacy per-file-ignores cannot contain any F*.
    # Scan both per-file-ignores and extend-per-file-ignores (defense-in-depth).
    def _scan_legacy_per_file(
        table_name: str, per_file_obj: object
    ) -> tuple[list[str], int | None]:
        """Return (violations, fatal_exit_code_or_None)."""
        if per_file_obj is None:
            return [], None
        if not isinstance(per_file_obj, dict):
            print(
                f"ERROR: [tool.ruff.lint.{table_name}] is not a table",
                file=sys.stderr,
            )
            return [], 2

        violations: list[str] = []
        for k, v in per_file_obj.items():
            if not isinstance(k, str):
                continue
            if not _is_legacy_key(k):
                continue

            codes, ok = _iter_codes(v)
            if not isinstance(v, (str, list)):
                print(
                    f"ERROR: {table_name}[{k!r}] must be str or list[str]",
                    file=sys.stderr,
                )
                return [], 2
            if not ok:
                print(
                    f"ERROR: {table_name}[{k!r}] contains non-string entries",
                    file=sys.stderr,
                )
                return [], 2

            for code in codes:
                c = _strip_code(code)
                if c.startswith(FORBIDDEN_PREFIX):
                    violations.append(f"{table_name}.{k}: {c}")

        return violations, None

    violations: list[str] = []

    v1, fatal = _scan_legacy_per_file("per-file-ignores", lint.get("per-file-ignores"))
    if fatal is not None:
        return fatal
    violations.extend(v1)

    v2, fatal = _scan_legacy_per_file(
        "extend-per-file-ignores", lint.get("extend-per-file-ignores")
    )
    if fatal is not None:
        return fatal
    violations.extend(v2)

    if violations:
        print("ERROR: Forbidden F* per-file ignores in legacy bucket:")
        for line in sorted(violations):
            print(f"  - {line}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
