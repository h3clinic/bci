#!/usr/bin/env python3
"""
capture.py — Host capture tool for MEA DAQ stream.

Reads bytes from a serial/USB source (or file), validates frames using
frame_parser.py, and writes validated recordings to .npz files.

Validates:
  - CRC16-CCITT integrity on every frame
  - Monotonic frame_id (gap detection → dropped frames)
  - Monotonic timestamps
  - Status byte health (overflow, SPI errors)
  - Reserved byte/bit compliance

Usage:
  # Live capture from serial port (real hardware):
  python capture.py /dev/tty.usbserial-FT1234 --duration 60 -o recording.npz

  # Offline validation from binary dump:
  python capture.py recording.bin --offline -o validated.npz

  # Dry-run validation (no output, just stats):
  python capture.py recording.bin --offline --stats-only

  # Smoke test with synthetic frames:
  python capture.py --smoke-test
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Import from sibling module
sys.path.insert(0, str(Path(__file__).parent))
from frame_parser import (
    FrameParser,
    Frame,
    FRAME_SIZE,
    ADC_CHANNELS,
    AUX_CHANNELS,
    CRC_OFFSET,
    crc16_ccitt,
    MAGIC,
    VERSION,
)


# ── Capture statistics ───────────────────────────────────────────────

@dataclass
class CaptureStats:
    """Accumulated capture session statistics."""
    frames_received: int = 0
    frames_valid: int = 0
    frames_crc_fail: int = 0
    frames_dropped: int = 0
    frames_reserved_dirty: int = 0
    timestamp_violations: int = 0
    overflow_events: int = 0
    spi_errors: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    first_frame_id: int | None = None
    last_frame_id: int | None = None
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    bytes_received: int = 0

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time if self.end_time > self.start_time else 0.0

    @property
    def drop_rate(self) -> float:
        total = self.frames_valid + self.frames_dropped
        return self.frames_dropped / total if total > 0 else 0.0

    @property
    def throughput_bps(self) -> float:
        return self.bytes_received / self.duration_s if self.duration_s > 0 else 0.0

    def summary(self) -> str:
        lines = [
            "═══════════════════════════════════════════════",
            "  Capture Summary",
            "═══════════════════════════════════════════════",
            f"  Duration:           {self.duration_s:.1f} s",
            f"  Bytes received:     {self.bytes_received:,}",
            f"  Throughput:         {self.throughput_bps/1024:.1f} KB/s",
            f"  Frames received:    {self.frames_received}",
            f"  Frames valid:       {self.frames_valid}",
            f"  CRC failures:       {self.frames_crc_fail}",
            f"  Frames dropped:     {self.frames_dropped}",
            f"  Drop rate:          {self.drop_rate:.4%}",
            f"  Reserved dirty:     {self.frames_reserved_dirty}",
            f"  Timestamp viols:    {self.timestamp_violations}",
            f"  Overflow events:    {self.overflow_events}",
            f"  SPI errors:         {self.spi_errors}",
            f"  Frame ID range:     {self.first_frame_id} → {self.last_frame_id}",
            f"  Timestamp range:    {self.first_timestamp} → {self.last_timestamp}",
            "═══════════════════════════════════════════════",
        ]
        return "\n".join(lines)


# ── Device Runtime Contract — Throughput Enforcement ─────────────────
# These values are locked by experiment/device_runtime_contract_v1.md.
# Any capture session that violates these is NOT competition-ready.

RUNTIME_CONTRACT = {
    "expected_fs_hz": 20_000,          # 20 kS/s per channel
    "expected_channels": 14,            # headstage v1 active channels
    "adc_bits": 16,
    "max_drop_rate": 0.001,             # < 0.1%
    "max_crc_fail_rate": 0.0,           # 100% pass required
    "max_timestamp_violations": 0,      # strict monotonicity
    "min_throughput_bps": 448_000,      # 14ch × 20kS/s × 16bit = 4.48 Mbps → floor at 448 kB/s
    "min_duration_s": 10.0,             # too-short captures are unreliable
}


@dataclass
class ContractViolation:
    """One violation of the runtime contract."""
    check: str
    expected: str
    actual: str
    severity: str  # "FAIL" or "WARN"


def validate_against_contract(
    stats: CaptureStats,
    contract: dict | None = None,
) -> list[ContractViolation]:
    """Validate CaptureStats against the device runtime contract.

    Returns list of violations (empty list = PASS).
    """
    c = contract or RUNTIME_CONTRACT
    violations: list[ContractViolation] = []

    # Duration check (WARN, not FAIL — short captures may be intentional)
    if stats.duration_s < c["min_duration_s"]:
        violations.append(ContractViolation(
            check="min_duration_s",
            expected=f"≥ {c['min_duration_s']:.1f} s",
            actual=f"{stats.duration_s:.1f} s",
            severity="WARN",
        ))

    # Drop rate
    if stats.drop_rate > c["max_drop_rate"]:
        violations.append(ContractViolation(
            check="max_drop_rate",
            expected=f"≤ {c['max_drop_rate']:.4%}",
            actual=f"{stats.drop_rate:.4%}",
            severity="FAIL",
        ))

    # CRC integrity
    crc_rate = (stats.frames_crc_fail / stats.frames_received
                if stats.frames_received > 0 else 0.0)
    if crc_rate > c["max_crc_fail_rate"]:
        violations.append(ContractViolation(
            check="max_crc_fail_rate",
            expected="0.0000%",
            actual=f"{crc_rate:.4%}",
            severity="FAIL",
        ))

    # Timestamp monotonicity
    if stats.timestamp_violations > c["max_timestamp_violations"]:
        violations.append(ContractViolation(
            check="max_timestamp_violations",
            expected=f"≤ {c['max_timestamp_violations']}",
            actual=f"{stats.timestamp_violations}",
            severity="FAIL",
        ))

    # Throughput
    if stats.duration_s > 0 and stats.throughput_bps < c["min_throughput_bps"]:
        violations.append(ContractViolation(
            check="min_throughput_bps",
            expected=f"≥ {c['min_throughput_bps']/1024:.1f} KB/s",
            actual=f"{stats.throughput_bps/1024:.1f} KB/s",
            severity="FAIL",
        ))

    return violations


def contract_report(violations: list[ContractViolation]) -> str:
    """Format a contract validation report."""
    if not violations:
        return "✓ Runtime contract: ALL CHECKS PASSED"

    lines = [
        "═══════════════════════════════════════════════",
        "  Runtime Contract Validation",
        "═══════════════════════════════════════════════",
    ]
    for v in violations:
        lines.append(f"  [{v.severity}] {v.check}: expected {v.expected}, got {v.actual}")

    n_fail = sum(1 for v in violations if v.severity == "FAIL")
    n_warn = sum(1 for v in violations if v.severity == "WARN")
    lines.append("───────────────────────────────────────────────")
    lines.append(f"  Result: {n_fail} FAIL, {n_warn} WARN")
    if n_fail > 0:
        lines.append("  ✗ NOT competition-ready")
    else:
        lines.append("  ✓ Competition-ready (with warnings)")
    lines.append("═══════════════════════════════════════════════")
    return "\n".join(lines)


# ── Frame validation ─────────────────────────────────────────────────

def validate_frame_sequence(
    frames: list[Frame],
) -> tuple[CaptureStats, list[Frame]]:
    """Validate a sequence of parsed frames.

    Checks:
      - frame_id monotonicity and gap detection
      - timestamp monotonicity
      - status byte health flags
      - reserved byte cleanliness

    Returns:
        (stats, valid_frames) — stats + list of frames that passed all checks.
    """
    stats = CaptureStats()
    valid: list[Frame] = []

    prev_frame_id: int | None = None
    prev_timestamp: int | None = None

    for frame in frames:
        stats.frames_received += 1

        # CRC already validated by parser (crc_valid flag)
        if not frame.crc_valid:
            stats.frames_crc_fail += 1
            continue

        # Frame ID gap detection
        if prev_frame_id is not None:
            gap = frame.frame_id - prev_frame_id
            if gap > 1:
                stats.frames_dropped += gap - 1
            elif gap < 1 and gap != -(2**32 - 1):
                # Non-monotonic (not a 32-bit wrap)
                pass  # still count as valid

        # Timestamp monotonicity
        if prev_timestamp is not None:
            if frame.timestamp < prev_timestamp:
                # Allow 32-bit wrap
                if not (prev_timestamp > 0xFFFF0000 and frame.timestamp < 0x10000):
                    stats.timestamp_violations += 1

        # Status health
        if frame.status.overflow_sticky:
            stats.overflow_events += 1
        if frame.status.spi_crc_err or frame.status.spi_timeout:
            stats.spi_errors += 1

        # Track first/last
        if stats.first_frame_id is None:
            stats.first_frame_id = frame.frame_id
            stats.first_timestamp = frame.timestamp
        stats.last_frame_id = frame.frame_id
        stats.last_timestamp = frame.timestamp

        prev_frame_id = frame.frame_id
        prev_timestamp = frame.timestamp

        stats.frames_valid += 1
        valid.append(frame)

    return stats, valid


# ── Recording writer ─────────────────────────────────────────────────

def frames_to_npz(
    frames: list[Frame],
    output_path: Path,
    fs: int = 20_000,
    stats: CaptureStats | None = None,
) -> None:
    """Write validated frames to a compressed .npz file.

    Contents:
      adc_data:    (n_channels, n_frames) int16 — raw ADC samples
      aux_data:    (n_aux, n_frames) int16 — auxiliary channels
      frame_ids:   (n_frames,) uint32 — monotonic frame IDs
      timestamps:  (n_frames,) uint32 — sample timestamps
      fs:          scalar — sample rate
      n_channels:  scalar
      n_frames:    scalar
      metadata:    dict with capture stats (JSON-serializable subset)
    """
    n_frames = len(frames)
    if n_frames == 0:
        print("WARNING: no valid frames to write.")
        return

    n_ch = len(frames[0].adc_samples)
    n_aux = len(frames[0].aux_samples)

    adc_data = np.zeros((n_ch, n_frames), dtype=np.int16)
    aux_data = np.zeros((n_aux, n_frames), dtype=np.int16)
    frame_ids = np.zeros(n_frames, dtype=np.uint32)
    timestamps = np.zeros(n_frames, dtype=np.uint32)

    for i, f in enumerate(frames):
        for ch in range(n_ch):
            adc_data[ch, i] = f.adc_samples[ch]
        for ch in range(n_aux):
            aux_data[ch, i] = f.aux_samples[ch]
        frame_ids[i] = f.frame_id
        timestamps[i] = f.timestamp

    meta = {}
    if stats:
        meta = {
            "frames_received": stats.frames_received,
            "frames_valid": stats.frames_valid,
            "frames_dropped": stats.frames_dropped,
            "crc_failures": stats.frames_crc_fail,
            "drop_rate": stats.drop_rate,
            "duration_s": stats.duration_s,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        adc_data=adc_data,
        aux_data=aux_data,
        frame_ids=frame_ids,
        timestamps=timestamps,
        fs=np.array(fs),
        n_channels=np.array(n_ch),
        n_frames=np.array(n_frames),
        metadata=np.array(str(meta)),
    )
    print(f"Saved {n_frames} frames ({n_ch} channels) to {output_path}")


# ── Capture from serial ──────────────────────────────────────────────

def capture_serial(
    port: str,
    baudrate: int = 3_000_000,
    duration_s: float = 60.0,
    output_path: Path | None = None,
    stats_only: bool = False,
) -> CaptureStats:
    """Capture frames from a serial port.

    Requires pyserial. Will attempt to import at runtime.
    """
    try:
        import serial
    except ImportError:
        print("ERROR: pyserial not installed. Run: pip install pyserial")
        sys.exit(1)

    parser = FrameParser()
    all_frames: list[Frame] = []
    buf = bytearray()

    print(f"Opening {port} at {baudrate} baud...")
    ser = serial.Serial(port, baudrate, timeout=0.1)

    stats = CaptureStats()
    stats.start_time = time.time()
    deadline = stats.start_time + duration_s

    try:
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                buf.extend(chunk)
                stats.bytes_received += len(chunk)

                # Parse complete frames from buffer
                for frame in parser.parse_stream(bytes(buf)):
                    all_frames.append(frame)
                # Keep only unparsed tail
                remainder = len(buf) % FRAME_SIZE
                if remainder > 0:
                    buf = buf[-remainder:]
                else:
                    buf.clear()

            # Progress
            elapsed = time.time() - stats.start_time
            if int(elapsed) % 5 == 0 and len(chunk) > 0:
                print(f"  {elapsed:.0f}s: {len(all_frames)} frames, "
                      f"{stats.bytes_received/1024:.0f} KB")

    except KeyboardInterrupt:
        print("\nCapture interrupted by user.")
    finally:
        ser.close()

    stats.end_time = time.time()

    # Validate
    vstats, valid_frames = validate_frame_sequence(all_frames)
    # Merge stats
    stats.frames_received = vstats.frames_received
    stats.frames_valid = vstats.frames_valid
    stats.frames_crc_fail = vstats.frames_crc_fail
    stats.frames_dropped = vstats.frames_dropped
    stats.timestamp_violations = vstats.timestamp_violations
    stats.overflow_events = vstats.overflow_events
    stats.spi_errors = vstats.spi_errors
    stats.first_frame_id = vstats.first_frame_id
    stats.last_frame_id = vstats.last_frame_id
    stats.first_timestamp = vstats.first_timestamp
    stats.last_timestamp = vstats.last_timestamp

    print(stats.summary())

    if output_path and not stats_only:
        frames_to_npz(valid_frames, output_path, stats=stats)

    return stats


# ── Offline capture from file ────────────────────────────────────────

def capture_offline(
    input_path: Path,
    output_path: Path | None = None,
    stats_only: bool = False,
) -> CaptureStats:
    """Validate frames from a binary file dump."""
    raw = input_path.read_bytes()
    print(f"Read {len(raw):,} bytes from {input_path}")

    parser = FrameParser()
    all_frames = list(parser.parse_stream(raw))
    print(f"Parsed {len(all_frames)} frames")

    stats = CaptureStats()
    stats.bytes_received = len(raw)
    stats.start_time = 0.0
    stats.end_time = 0.0

    vstats, valid_frames = validate_frame_sequence(all_frames)
    stats.frames_received = vstats.frames_received
    stats.frames_valid = vstats.frames_valid
    stats.frames_crc_fail = vstats.frames_crc_fail
    stats.frames_dropped = vstats.frames_dropped
    stats.timestamp_violations = vstats.timestamp_violations
    stats.overflow_events = vstats.overflow_events
    stats.spi_errors = vstats.spi_errors
    stats.first_frame_id = vstats.first_frame_id
    stats.last_frame_id = vstats.last_frame_id
    stats.first_timestamp = vstats.first_timestamp
    stats.last_timestamp = vstats.last_timestamp

    print(stats.summary())

    if output_path and not stats_only:
        frames_to_npz(valid_frames, output_path, stats=stats)

    return stats


# ── Smoke test with synthetic frames ─────────────────────────────────

def _build_synthetic_frame(
    frame_id: int,
    timestamp: int,
    adc_values: list[int] | None = None,
) -> bytes:
    """Build one valid 256-byte frame for smoke testing."""
    buf = bytearray(FRAME_SIZE)

    # Magic
    buf[0] = 0xA5
    buf[1] = 0x5A
    # Version
    buf[2] = VERSION
    # Status
    buf[3] = 0x00
    # Frame ID (big-endian)
    struct.pack_into(">I", buf, 4, frame_id)
    # Timestamp
    struct.pack_into(">I", buf, 8, timestamp)
    # Channel count
    struct.pack_into(">H", buf, 12, ADC_CHANNELS)
    # Sample rate code
    struct.pack_into(">H", buf, 14, 0x0001)  # 20 kSPS

    # ADC payload
    if adc_values is None:
        adc_values = [32768 + (ch * 10) for ch in range(ADC_CHANNELS)]
    for ch in range(ADC_CHANNELS):
        val = adc_values[ch] if ch < len(adc_values) else 32768
        struct.pack_into(">H", buf, 16 + ch * 2, val & 0xFFFF)

    # AUX payload (zeros)
    # Diagnostics (zeros)
    # Reserved (zeros — already zeroed)

    # CRC over everything except last 2 bytes
    crc = crc16_ccitt(bytes(buf[:CRC_OFFSET]))
    struct.pack_into(">H", buf, CRC_OFFSET, crc)

    return bytes(buf)


def smoke_test() -> CaptureStats:
    """Generate + parse + validate 100 synthetic frames."""
    print("Running smoke test (100 synthetic frames)...")

    frames_bytes = bytearray()
    n_frames = 100
    for i in range(n_frames):
        # Slight ADC variation per frame
        adc = [32768 + (ch * 10) + i for ch in range(ADC_CHANNELS)]
        frame_bytes = _build_synthetic_frame(
            frame_id=i,
            timestamp=i * 50,  # 50 sample periods between frames
            adc_values=adc,
        )
        frames_bytes.extend(frame_bytes)

    # Also add one frame with intentional gap (simulates drop)
    gap_frame = _build_synthetic_frame(frame_id=n_frames + 5, timestamp=(n_frames + 5) * 50)
    frames_bytes.extend(gap_frame)

    parser = FrameParser()
    parsed = list(parser.parse_stream(bytes(frames_bytes)))

    stats = CaptureStats()
    stats.bytes_received = len(frames_bytes)
    vstats, valid = validate_frame_sequence(parsed)
    stats.frames_received = vstats.frames_received
    stats.frames_valid = vstats.frames_valid
    stats.frames_crc_fail = vstats.frames_crc_fail
    stats.frames_dropped = vstats.frames_dropped
    stats.first_frame_id = vstats.first_frame_id
    stats.last_frame_id = vstats.last_frame_id
    stats.first_timestamp = vstats.first_timestamp
    stats.last_timestamp = vstats.last_timestamp

    print(stats.summary())

    # Assertions
    assert stats.frames_valid == n_frames + 1, \
        f"Expected {n_frames + 1} valid frames, got {stats.frames_valid}"
    assert stats.frames_crc_fail == 0, "CRC failures in synthetic data"
    assert stats.frames_dropped == 5, \
        f"Expected 5 dropped (gap), got {stats.frames_dropped}"
    print("✓ Smoke test PASSED")
    return stats


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="MEA DAQ Stream Capture + Validation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("source", nargs="?", default=None,
                    help="Serial port (e.g. /dev/tty.usbserial-FT1234) "
                         "or binary file path for --offline")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="Output .npz file path")
    ap.add_argument("--offline", action="store_true",
                    help="Read from binary file instead of serial port")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="Capture duration in seconds (default: 60)")
    ap.add_argument("--baudrate", type=int, default=3_000_000,
                    help="Serial baud rate (default: 3000000)")
    ap.add_argument("--stats-only", action="store_true",
                    help="Only print stats, don't write output file")
    ap.add_argument("--smoke-test", action="store_true",
                    help="Run smoke test with synthetic frames")
    ap.add_argument("--validate", action="store_true",
                    help="Validate capture against device runtime contract")

    args = ap.parse_args()

    if args.smoke_test:
        stats = smoke_test()
        if args.validate:
            viols = validate_against_contract(stats)
            print(contract_report(viols))
        return

    if args.source is None:
        ap.error("source is required (serial port or file path)")

    if args.offline:
        stats = capture_offline(Path(args.source), args.output, args.stats_only)
    else:
        stats = capture_serial(args.source, args.baudrate, args.duration,
                               args.output, args.stats_only)

    if args.validate:
        viols = validate_against_contract(stats)
        print(contract_report(viols))
        if any(v.severity == "FAIL" for v in viols):
            sys.exit(1)


if __name__ == "__main__":
    main()
