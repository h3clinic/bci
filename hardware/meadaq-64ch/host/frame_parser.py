"""
frame_parser.py — Host-side MEA DAQ 64-Channel Frame Parser
Rev 1.0 · 2025-06-01

Parses the 256-byte frame format produced by frame_packer.v:
  [0..15]     Header (magic, version, status, frame_id, timestamp, etc.)
  [16..143]   ADC payload (64 ch × 16-bit, big-endian, signed)
  [144..149]  AUX payload (3 ch × 16-bit, big-endian)
  [150..165]  Diagnostics (fifo_fill, drop_cnt, stall_cnt, spi_err)
  [166..253]  Reserved
  [254..255]  CRC16-CCITT-FALSE

Usage:
    from frame_parser import FrameParser, Frame

    parser = FrameParser()
    for frame in parser.parse_stream(raw_bytes):
        print(f"Frame {frame.frame_id}: {frame.adc_samples[0]}")
        if frame.status.drop_since_last:
            print("  WARNING: frame(s) dropped before this one")

Can also be run standalone for testing / offline analysis:
    python frame_parser.py input.bin [--stats] [--csv output.csv]
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from typing import Iterator


# ── Constants ────────────────────────────────────────────────────────
FRAME_SIZE      = 256
HDR_SIZE        = 16
ADC_CHANNELS    = 64
ADC_BYTES       = 128       # 64 × 2
AUX_CHANNELS    = 3
AUX_BYTES       = 6         # 3 × 2
DIAG_OFFSET     = HDR_SIZE + ADC_BYTES + AUX_BYTES  # 150
DIAG_BYTES      = 16
RSVD_OFFSET     = DIAG_OFFSET + DIAG_BYTES          # 166
RSVD_BYTES      = FRAME_SIZE - 2 - RSVD_OFFSET        # 88
CRC_OFFSET      = FRAME_SIZE - 2                     # 254
MAGIC           = 0xA55A
VERSION         = 0x01

# Status byte: bits 4, 3, 1, 0 are reserved-must-be-zero for version 0x01
STATUS_DEFINED_MASK   = 0b11100100   # bits 7,6,5,2
STATUS_RESERVED_MASK  = 0b00011011   # bits 4,3,1,0 — must be zero

# Sample rate code → Hz lookup
SAMPLE_RATE_MAP = {
    0x0001: 20_000,   # 20 kSPS (default)
    0x0002: 30_000,   # 30 kSPS
    0x0003: 25_000,   # 25 kSPS
}


# ── CRC16-CCITT-FALSE ───────────────────────────────────────────────
def crc16_ccitt(data: bytes | bytearray, init: int = 0xFFFF) -> int:
    """Compute CRC16-CCITT-FALSE (poly 0x1021, MSB-first)."""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ── Data classes ─────────────────────────────────────────────────────
@dataclass
class StatusByte:
    """Decoded status byte from frame header."""
    drop_since_last:  bool = False   # bit 7
    fifo_half_full:   bool = False   # bit 6
    fifo_near_full:   bool = False   # bit 5
    spi_crc_err:      bool = False   # bit 4
    spi_timeout:      bool = False   # bit 3
    overflow_sticky:  bool = False   # bit 2
    raw: int = 0

    @classmethod
    def from_byte(cls, b: int) -> StatusByte:
        return cls(
            drop_since_last = bool(b & 0x80),
            fifo_half_full  = bool(b & 0x40),
            fifo_near_full  = bool(b & 0x20),
            spi_crc_err     = bool(b & 0x10),
            spi_timeout     = bool(b & 0x08),
            overflow_sticky = bool(b & 0x04),
            raw             = b,
        )


@dataclass
class Diagnostics:
    """Decoded diagnostics block from frame."""
    fifo_fill:      int = 0   # FIFO fill level (bytes)
    drop_count:     int = 0   # Total dropped frames
    stall_count:    int = 0   # USB bridge stall cycles
    spi_err_count:  int = 0   # SPI errors (reserved)

    @classmethod
    def from_bytes(cls, data: bytes) -> Diagnostics:
        assert len(data) == DIAG_BYTES
        fifo_fill, drop_count, stall_count, spi_err = struct.unpack(
            ">IIII", data
        )
        return cls(fifo_fill, drop_count, stall_count, spi_err)


@dataclass
class Frame:
    """One parsed 256-byte frame.

    Reserved-field policy (version 0x01):
      - Bytes 166-253 MUST be 0x00.
      - Status bits 4,3,1,0 MUST be zero.
      reserved_clean and status_reserved_clean indicate compliance.

    drop_since_last semantics (status bit 7):
      Formally defined as: diag_drop_count changed since previous
      committed frame. Equivalently, at least one frame was dropped
      between this frame and the last successfully committed frame.
    """
    magic:          int = 0
    version:        int = 0
    status:         StatusByte = field(default_factory=StatusByte)
    frame_id:       int = 0
    timestamp:      int = 0
    channel_count:  int = 0
    sample_rate_code: int = 0
    adc_samples:    list[int] = field(default_factory=list)    # signed 16-bit
    aux_samples:    list[int] = field(default_factory=list)    # unsigned 16-bit
    diagnostics:    Diagnostics = field(default_factory=Diagnostics)
    crc_received:   int = 0
    crc_computed:   int = 0
    crc_valid:      bool = False
    reserved_clean: bool = True     # bytes 166-253 all zero
    status_reserved_clean: bool = True  # status bits 4,3,1,0 all zero
    raw:            bytes = b""

    @property
    def sample_rate_hz(self) -> int:
        return SAMPLE_RATE_MAP.get(self.sample_rate_code, 0)


# ── Parser ───────────────────────────────────────────────────────────
class FrameParser:
    """
    Stateful frame parser for MEA DAQ 64-channel USB stream.

    Scans for magic word 0xA55A at 256-byte boundaries, validates CRC,
    and tracks frame_id for drop detection.
    """

    def __init__(self, strict: bool = False) -> None:
        self.strict = strict
        self.last_frame_id: int | None = None
        self.total_frames: int = 0           # frames seen (parsed header)
        self.total_committed: int = 0        # frames returned to caller
        self.total_crc_errors: int = 0
        self.total_drops_detected: int = 0
        self.total_reserved_violations: int = 0
        self.total_status_reserved_violations: int = 0
        self._buffer: bytearray = bytearray()

    def parse_frame(self, data: bytes | bytearray) -> Frame | None:
        """
        Parse a single 256-byte frame.
        Returns Frame on success, None if magic/version mismatch.
        """
        if len(data) < FRAME_SIZE:
            return None

        raw = bytes(data[:FRAME_SIZE])

        # Header
        magic = struct.unpack_from(">H", raw, 0)[0]
        if magic != MAGIC:
            return None

        version = raw[2]
        status = StatusByte.from_byte(raw[3])
        frame_id, timestamp = struct.unpack_from(">II", raw, 4)
        channel_count, sample_rate_code = struct.unpack_from(">HH", raw, 12)

        # ADC payload: 64 × signed 16-bit, big-endian
        adc_samples = list(struct.unpack_from(
            f">{ADC_CHANNELS}h", raw, HDR_SIZE
        ))

        # AUX payload: 3 × unsigned 16-bit, big-endian
        aux_samples = list(struct.unpack_from(
            f">{AUX_CHANNELS}H", raw, HDR_SIZE + ADC_BYTES
        ))

        # Diagnostics
        diag = Diagnostics.from_bytes(raw[DIAG_OFFSET:DIAG_OFFSET + DIAG_BYTES])

        # CRC
        crc_received = struct.unpack_from(">H", raw, CRC_OFFSET)[0]
        crc_computed = crc16_ccitt(raw[:CRC_OFFSET])
        crc_valid = (crc_received == crc_computed)

        # Reserved bytes [166..253] must be zero for version 0x01
        reserved_region = raw[RSVD_OFFSET:CRC_OFFSET]
        reserved_clean = all(b == 0 for b in reserved_region)

        # Status reserved bits (4,3,1,0) must be zero for version 0x01
        status_reserved_clean = (status.raw & STATUS_RESERVED_MASK) == 0

        frame = Frame(
            magic=magic,
            version=version,
            status=status,
            frame_id=frame_id,
            timestamp=timestamp,
            channel_count=channel_count,
            sample_rate_code=sample_rate_code,
            adc_samples=adc_samples,
            aux_samples=aux_samples,
            diagnostics=diag,
            crc_received=crc_received,
            crc_computed=crc_computed,
            crc_valid=crc_valid,
            reserved_clean=reserved_clean,
            status_reserved_clean=status_reserved_clean,
            raw=raw,
        )

        # Statistics
        self.total_frames += 1
        if not crc_valid:
            self.total_crc_errors += 1
        if not reserved_clean:
            self.total_reserved_violations += 1
            if self.strict:
                return None  # strict mode: reject frame
        if not status_reserved_clean:
            self.total_status_reserved_violations += 1
            if self.strict:
                return None  # strict mode: reject frame

        # Committed — update drop baseline only on accepted frames
        self.total_committed += 1
        if self.last_frame_id is not None:
            gap = frame_id - self.last_frame_id
            if gap > 1:
                self.total_drops_detected += gap - 1
        self.last_frame_id = frame_id

        return frame

    def parse_stream(self, data: bytes | bytearray) -> Iterator[Frame]:
        """
        Parse a continuous byte stream, yielding Frame objects.
        Handles partial frames across calls (buffered).
        """
        self._buffer.extend(data)

        while len(self._buffer) >= FRAME_SIZE:
            # Look for magic at current position
            magic = struct.unpack_from(">H", self._buffer, 0)[0]

            if magic == MAGIC:
                frame = self.parse_frame(self._buffer[:FRAME_SIZE])
                self._buffer = self._buffer[FRAME_SIZE:]
                if frame is not None:
                    yield frame
            else:
                # Not aligned — scan forward for magic
                idx = self._find_magic(self._buffer, 1)
                if idx >= 0:
                    self._buffer = self._buffer[idx:]
                else:
                    # No magic found — discard all but last byte
                    self._buffer = self._buffer[-1:]
                    break

    @staticmethod
    def _find_magic(data: bytearray, start: int = 0) -> int:
        """Find 0xA55A in data starting at offset `start`."""
        for i in range(start, len(data) - 1):
            if data[i] == 0xA5 and data[i + 1] == 0x5A:
                return i
        return -1

    def summary(self) -> str:
        """Return a multi-line summary of parser statistics."""
        return (
            f"Frames seen:       {self.total_frames}\n"
            f"Frames committed:  {self.total_committed}\n"
            f"CRC errors:        {self.total_crc_errors}\n"
            f"Drops detected:    {self.total_drops_detected}\n"
            f"Reserved viol.:    {self.total_reserved_violations}\n"
            f"Status rsvd viol.: {self.total_status_reserved_violations}\n"
            f"Last frame ID:     {self.last_frame_id}"
        )


# ── Standalone CLI ───────────────────────────────────────────────────
def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Parse MEA DAQ 64ch frame stream"
    )
    ap.add_argument("input", help="Binary input file")
    ap.add_argument("--stats", action="store_true",
                    help="Print summary statistics")
    ap.add_argument("--csv", type=str, default=None,
                    help="Write per-frame CSV")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print each frame header")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--strict", action="store_true", default=False,
                      help="Release mode: reject frames with reserved violations")
    mode.add_argument("--lenient", action="store_true", default=False,
                      help="Lab mode: accept all frames, count violations (default)")
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        raw = f.read()

    parser = FrameParser(strict=args.strict)
    csv_f = None
    if args.csv:
        csv_f = open(args.csv, "w")
        csv_cols = [
            "frame_id", "timestamp", "crc_valid",
            "drop_since_last", "fifo_half", "fifo_near",
            "overflow", "fifo_fill", "drop_count",
            "stall_count"
        ] + [f"ch{i:02d}" for i in range(ADC_CHANNELS)]
        csv_f.write(",".join(csv_cols) + "\n")

    for frame in parser.parse_stream(raw):
        if args.verbose:
            print(
                f"Frame {frame.frame_id:>8d}  ts={frame.timestamp:>8d}  "
                f"CRC={'OK' if frame.crc_valid else 'BAD'}  "
                f"status=0x{frame.status.raw:02X}  "
                f"fill={frame.diagnostics.fifo_fill}  "
                f"drops={frame.diagnostics.drop_count}  "
                f"stalls={frame.diagnostics.stall_count}"
            )
        if csv_f:
            row = [
                str(frame.frame_id),
                str(frame.timestamp),
                str(int(frame.crc_valid)),
                str(int(frame.status.drop_since_last)),
                str(int(frame.status.fifo_half_full)),
                str(int(frame.status.fifo_near_full)),
                str(int(frame.status.overflow_sticky)),
                str(frame.diagnostics.fifo_fill),
                str(frame.diagnostics.drop_count),
                str(frame.diagnostics.stall_count),
            ] + [str(s) for s in frame.adc_samples]
            csv_f.write(",".join(row) + "\n")

    if csv_f:
        csv_f.close()
        print(f"Wrote {parser.total_frames} frames to {args.csv}")

    if args.stats or not args.csv:
        print(f"\n{parser.summary()}")


if __name__ == "__main__":
    main()
