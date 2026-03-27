"""
test_contract_validation.py — Tests for device runtime contract enforcement.

Covers:
  - RUNTIME_CONTRACT constants
  - validate_against_contract() — pass and fail cases
  - contract_report() formatting
  - ContractViolation structure
  - Edge cases: zero-duration, zero-frames
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make capture.py importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hardware" / "meadaq-64ch" / "host"))

from capture import (
    CaptureStats,
    ContractViolation,
    RUNTIME_CONTRACT,
    validate_against_contract,
    contract_report,
)


def _make_stats(**overrides) -> CaptureStats:
    """Build a CaptureStats that passes all contract checks by default."""
    defaults = dict(
        frames_received=10000,
        frames_valid=10000,
        frames_crc_fail=0,
        frames_dropped=0,
        frames_reserved_dirty=0,
        timestamp_violations=0,
        overflow_events=0,
        spi_errors=0,
        start_time=0.0,
        end_time=60.0,
        first_frame_id=0,
        last_frame_id=9999,
        first_timestamp=0,
        last_timestamp=999900,
        bytes_received=60 * 500_000,  # 500 KB/s for 60s = well above min
    )
    defaults.update(overrides)
    return CaptureStats(**defaults)


# ── RUNTIME_CONTRACT constants ───────────────────────────────────────

class TestRuntimeContract:
    def test_keys_present(self):
        for key in ("expected_fs_hz", "expected_channels", "adc_bits",
                     "max_drop_rate", "max_crc_fail_rate",
                     "max_timestamp_violations", "min_throughput_bps",
                     "min_duration_s"):
            assert key in RUNTIME_CONTRACT

    def test_fs_20khz(self):
        assert RUNTIME_CONTRACT["expected_fs_hz"] == 20_000

    def test_channels_14(self):
        assert RUNTIME_CONTRACT["expected_channels"] == 14

    def test_crc_zero_tolerance(self):
        assert RUNTIME_CONTRACT["max_crc_fail_rate"] == 0.0

    def test_drop_rate_below_01pct(self):
        assert RUNTIME_CONTRACT["max_drop_rate"] <= 0.001


# ── validate_against_contract ────────────────────────────────────────

class TestValidateContract:
    def test_clean_stats_pass(self):
        stats = _make_stats()
        viols = validate_against_contract(stats)
        assert len(viols) == 0

    def test_drop_rate_fail(self):
        # 100 dropped out of 10100 total > 0.1%
        stats = _make_stats(frames_valid=10000, frames_dropped=100)
        viols = validate_against_contract(stats)
        checks = [v.check for v in viols]
        assert "max_drop_rate" in checks
        assert any(v.severity == "FAIL" for v in viols if v.check == "max_drop_rate")

    def test_crc_fail(self):
        stats = _make_stats(frames_crc_fail=1)
        viols = validate_against_contract(stats)
        checks = [v.check for v in viols]
        assert "max_crc_fail_rate" in checks

    def test_timestamp_violation_fail(self):
        stats = _make_stats(timestamp_violations=1)
        viols = validate_against_contract(stats)
        checks = [v.check for v in viols]
        assert "max_timestamp_violations" in checks

    def test_low_throughput_fail(self):
        # Only 1000 bytes in 60s = ~16.7 B/s
        stats = _make_stats(bytes_received=1000)
        viols = validate_against_contract(stats)
        checks = [v.check for v in viols]
        assert "min_throughput_bps" in checks

    def test_short_duration_warn(self):
        stats = _make_stats(start_time=0.0, end_time=5.0,
                            bytes_received=5 * 500_000)
        viols = validate_against_contract(stats)
        dur_viols = [v for v in viols if v.check == "min_duration_s"]
        assert len(dur_viols) == 1
        assert dur_viols[0].severity == "WARN"

    def test_custom_contract(self):
        custom = {
            "expected_fs_hz": 20_000,
            "expected_channels": 14,
            "adc_bits": 16,
            "max_drop_rate": 0.0,  # zero tolerance
            "max_crc_fail_rate": 0.0,
            "max_timestamp_violations": 0,
            "min_throughput_bps": 1_000_000,
            "min_duration_s": 5.0,
        }
        stats = _make_stats(frames_dropped=1)
        viols = validate_against_contract(stats, contract=custom)
        checks = [v.check for v in viols]
        assert "max_drop_rate" in checks

    def test_zero_frames_no_crash(self):
        stats = _make_stats(frames_received=0, frames_valid=0,
                            frames_dropped=0, frames_crc_fail=0,
                            bytes_received=0, start_time=0.0, end_time=0.0)
        viols = validate_against_contract(stats)
        # Should not crash — duration=0 means throughput check is skipped
        assert isinstance(viols, list)

    def test_multiple_violations(self):
        stats = _make_stats(
            frames_crc_fail=5,
            frames_dropped=500,
            timestamp_violations=3,
            bytes_received=100,
        )
        viols = validate_against_contract(stats)
        assert len(viols) >= 3  # at least CRC, drop, timestamp, throughput


# ── ContractViolation ────────────────────────────────────────────────

class TestContractViolation:
    def test_fields(self):
        v = ContractViolation(check="test", expected="≤ 0", actual="5",
                              severity="FAIL")
        assert v.check == "test"
        assert v.severity == "FAIL"


# ── contract_report ──────────────────────────────────────────────────

class TestContractReport:
    def test_pass_report(self):
        report = contract_report([])
        assert "ALL CHECKS PASSED" in report

    def test_fail_report(self):
        viols = [ContractViolation(
            check="max_drop_rate", expected="≤ 0.1000%",
            actual="5.0000%", severity="FAIL")]
        report = contract_report(viols)
        assert "NOT competition-ready" in report
        assert "max_drop_rate" in report

    def test_warn_only_report(self):
        viols = [ContractViolation(
            check="min_duration_s", expected="≥ 10.0 s",
            actual="5.0 s", severity="WARN")]
        report = contract_report(viols)
        assert "Competition-ready (with warnings)" in report
        assert "0 FAIL" in report
        assert "1 WARN" in report

    def test_mixed_report(self):
        viols = [
            ContractViolation(check="a", expected="x", actual="y", severity="FAIL"),
            ContractViolation(check="b", expected="x", actual="y", severity="WARN"),
        ]
        report = contract_report(viols)
        assert "1 FAIL" in report
        assert "1 WARN" in report
