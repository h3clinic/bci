"""
test_frame_parser.py — Tests for host-side MEA DAQ frame parser.

Generates synthetic 256-byte frames matching the RTL frame_packer.v
output format and verifies that frame_parser.py correctly:
  1. Parses header fields
  2. Validates CRC16-CCITT-FALSE
  3. Detects frame drops via frame_id gaps
  4. Decodes status byte flags
  5. Extracts diagnostics
  6. Handles stream alignment and partial frames
  7. Rejects corrupted CRC
"""

import struct
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from frame_parser import (
    Frame, FrameParser, StatusByte, Diagnostics,
    crc16_ccitt, FRAME_SIZE, MAGIC, ADC_CHANNELS, AUX_CHANNELS,
    HDR_SIZE, ADC_BYTES, AUX_BYTES, DIAG_OFFSET, DIAG_BYTES, CRC_OFFSET,
    RSVD_OFFSET, RSVD_BYTES, STATUS_RESERVED_MASK,
)


def build_frame(
    frame_id: int = 0,
    timestamp: int = 0,
    status: int = 0x00,
    channel_count: int = 64,
    sample_rate_code: int = 0x0001,
    adc_samples: list[int] | None = None,
    aux_samples: list[int] | None = None,
    fifo_fill: int = 100,
    drop_count: int = 0,
    stall_count: int = 0,
    spi_err_count: int = 0,
    corrupt_crc: bool = False,
    dirty_reserved_offsets: list[int] | None = None,
) -> bytes:
    """Build a 256-byte frame matching frame_packer.v output.

    dirty_reserved_offsets: list of ABSOLUTE byte offsets to set to 0xFF.
        Must be in range [RSVD_OFFSET, CRC_OFFSET).
    """
    buf = bytearray(FRAME_SIZE)

    # Header
    struct.pack_into(">H", buf, 0, MAGIC)
    buf[2] = 0x01  # version
    buf[3] = status
    struct.pack_into(">I", buf, 4, frame_id)
    struct.pack_into(">I", buf, 8, timestamp)
    struct.pack_into(">H", buf, 12, channel_count)
    struct.pack_into(">H", buf, 14, sample_rate_code)

    # ADC payload
    if adc_samples is None:
        adc_samples = [i * 100 for i in range(ADC_CHANNELS)]
    for i, s in enumerate(adc_samples):
        struct.pack_into(">h", buf, HDR_SIZE + i * 2, s)

    # AUX payload
    if aux_samples is None:
        aux_samples = [1000, 2000, 3000]
    for i, s in enumerate(aux_samples):
        struct.pack_into(">H", buf, HDR_SIZE + ADC_BYTES + i * 2, s)

    # Diagnostics
    struct.pack_into(">I", buf, DIAG_OFFSET, fifo_fill)
    struct.pack_into(">I", buf, DIAG_OFFSET + 4, drop_count)
    struct.pack_into(">I", buf, DIAG_OFFSET + 8, stall_count)
    struct.pack_into(">I", buf, DIAG_OFFSET + 12, spi_err_count)

    # Reserved region: already zero from bytearray init
    if dirty_reserved_offsets:
        for off in dirty_reserved_offsets:
            assert RSVD_OFFSET <= off < CRC_OFFSET, f"offset {off} out of reserved range"
            buf[off] = 0xFF

    # CRC16 over bytes 0..253
    crc = crc16_ccitt(buf[:CRC_OFFSET])
    if corrupt_crc:
        crc ^= 0xFFFF  # Intentionally wrong
    struct.pack_into(">H", buf, CRC_OFFSET, crc)

    return bytes(buf)


# ── CRC16 reference tests ───────────────────────────────────────────

def test_crc16_known_vector():
    """Standard CRC16-CCITT-FALSE check value."""
    result = crc16_ccitt(b"123456789")
    assert result == 0x29B1, f"Expected 0x29B1, got 0x{result:04X}"


def test_crc16_empty():
    """Empty input returns init value."""
    assert crc16_ccitt(b"") == 0xFFFF


def test_crc16_self_check():
    """Append CRC to message, recompute → 0."""
    msg = b"123456789"
    crc = crc16_ccitt(msg)
    msg_with_crc = msg + struct.pack(">H", crc)
    assert crc16_ccitt(msg_with_crc) == 0x0000


# ── Single frame parsing ────────────────────────────────────────────

def test_parse_basic_frame():
    """Parse a single valid frame with default values."""
    raw = build_frame(frame_id=42, timestamp=100)
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.magic == MAGIC
    assert frame.version == 0x01
    assert frame.frame_id == 42
    assert frame.timestamp == 100
    assert frame.channel_count == 64
    assert frame.sample_rate_code == 0x0001
    assert frame.crc_valid is True
    assert len(frame.adc_samples) == ADC_CHANNELS
    assert len(frame.aux_samples) == AUX_CHANNELS


def test_parse_adc_samples():
    """ADC samples correctly decoded as signed big-endian."""
    samples = list(range(-32, 32))  # 64 values, some negative
    raw = build_frame(adc_samples=samples)
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.adc_samples == samples


def test_parse_negative_samples():
    """Negative ADC samples round-trip correctly."""
    samples = [-32768, -1, 0, 1, 32767] + [0] * 59
    raw = build_frame(adc_samples=samples)
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.adc_samples[:5] == [-32768, -1, 0, 1, 32767]


def test_parse_aux_samples():
    """AUX samples correctly decoded."""
    aux = [4095, 2048, 0]
    raw = build_frame(aux_samples=aux)
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.aux_samples == aux


def test_parse_diagnostics():
    """Diagnostics block decoded correctly."""
    raw = build_frame(fifo_fill=500, drop_count=3, stall_count=9999)
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.diagnostics.fifo_fill == 500
    assert frame.diagnostics.drop_count == 3
    assert frame.diagnostics.stall_count == 9999
    assert frame.diagnostics.spi_err_count == 0


# ── Status byte ──────────────────────────────────────────────────────

def test_status_byte_flags():
    """Status byte flags decoded individually."""
    raw = build_frame(status=0b10100100)  # drop_since_last, fifo_near_full, overflow
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.status.drop_since_last is True
    assert frame.status.fifo_half_full is False
    assert frame.status.fifo_near_full is True
    assert frame.status.spi_crc_err is False
    assert frame.status.spi_timeout is False
    assert frame.status.overflow_sticky is True


def test_status_byte_all_clear():
    """All status flags clear."""
    raw = build_frame(status=0x00)
    parser = FrameParser()
    frame = parser.parse_frame(raw)

    assert frame is not None
    assert frame.status.drop_since_last is False
    assert frame.status.overflow_sticky is False


# ── CRC validation ───────────────────────────────────────────────────

def test_crc_valid():
    """Valid frame has crc_valid=True."""
    raw = build_frame()
    parser = FrameParser()
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.crc_valid is True


def test_crc_corrupted():
    """Corrupted CRC detected."""
    raw = build_frame(corrupt_crc=True)
    parser = FrameParser()
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.crc_valid is False
    assert parser.total_crc_errors == 1


def test_crc_matches_rtl_convention():
    """CRC computed over bytes 0..253, stored at 254..255 big-endian."""
    raw = build_frame(frame_id=7, timestamp=42)
    expected_crc = crc16_ccitt(raw[:254])
    actual_crc = struct.unpack_from(">H", raw, 254)[0]
    assert actual_crc == expected_crc


# ── Drop detection ───────────────────────────────────────────────────

def test_no_drops():
    """Consecutive frame_ids → 0 drops."""
    parser = FrameParser()
    for fid in range(10):
        raw = build_frame(frame_id=fid)
        frame = parser.parse_frame(raw)
        assert frame is not None
    assert parser.total_drops_detected == 0


def test_single_drop():
    """Gap of 1 (frame_id 0, 2) → 1 drop detected."""
    parser = FrameParser()
    parser.parse_frame(build_frame(frame_id=0))
    parser.parse_frame(build_frame(frame_id=2))
    assert parser.total_drops_detected == 1


def test_multi_drop():
    """Gap of 5 (frame_id 10, 16) → 5 drops."""
    parser = FrameParser()
    parser.parse_frame(build_frame(frame_id=10))
    parser.parse_frame(build_frame(frame_id=16))
    assert parser.total_drops_detected == 5


def test_cumulative_drops():
    """Drops accumulate across multiple gaps."""
    parser = FrameParser()
    parser.parse_frame(build_frame(frame_id=0))
    parser.parse_frame(build_frame(frame_id=3))   # +2
    parser.parse_frame(build_frame(frame_id=4))   # +0
    parser.parse_frame(build_frame(frame_id=10))  # +5
    assert parser.total_drops_detected == 7


# ── Stream parsing ───────────────────────────────────────────────────

def test_stream_aligned():
    """Parse aligned multi-frame stream."""
    frames_data = b"".join(
        build_frame(frame_id=i, timestamp=i * 100) for i in range(5)
    )
    parser = FrameParser()
    frames = list(parser.parse_stream(frames_data))
    assert len(frames) == 5
    assert [f.frame_id for f in frames] == list(range(5))


def test_stream_with_garbage_prefix():
    """Recover from garbage before first frame."""
    garbage = b"\x00\xFF\x42" * 20  # 60 bytes of junk
    frames_data = garbage + build_frame(frame_id=0)
    parser = FrameParser()
    frames = list(parser.parse_stream(frames_data))
    assert len(frames) == 1
    assert frames[0].frame_id == 0
    assert frames[0].crc_valid is True


def test_stream_partial_frame():
    """Partial frame at end is buffered, completed on next call."""
    full = build_frame(frame_id=0)
    half1 = full[:128]
    half2 = full[128:]

    parser = FrameParser()
    frames1 = list(parser.parse_stream(half1))
    assert len(frames1) == 0  # Not enough data yet

    frames2 = list(parser.parse_stream(half2))
    assert len(frames2) == 1
    assert frames2[0].frame_id == 0


def test_stream_statistics():
    """Parser statistics accumulate across stream calls."""
    parser = FrameParser()

    # 3 frames, 1 CRC error, 1 drop
    data = (
        build_frame(frame_id=0) +
        build_frame(frame_id=1, corrupt_crc=True) +
        build_frame(frame_id=3)  # skip frame_id=2
    )
    frames = list(parser.parse_stream(data))
    assert len(frames) == 3
    assert parser.total_frames == 3
    assert parser.total_crc_errors == 1
    assert parser.total_drops_detected == 1


# ── Edge cases ───────────────────────────────────────────────────────

def test_empty_input():
    """Empty bytes returns no frames."""
    parser = FrameParser()
    frames = list(parser.parse_stream(b""))
    assert len(frames) == 0


def test_too_short():
    """Input shorter than one frame returns None."""
    parser = FrameParser()
    assert parser.parse_frame(b"\xA5\x5A" + b"\x00" * 100) is None


def test_wrong_magic():
    """Wrong magic returns None."""
    raw = bytearray(build_frame())
    raw[0] = 0xDE
    raw[1] = 0xAD
    parser = FrameParser()
    assert parser.parse_frame(raw) is None


def test_sample_rate_lookup():
    """Sample rate code 0x0001 → 20000 Hz."""
    raw = build_frame(sample_rate_code=0x0001)
    parser = FrameParser()
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.sample_rate_hz == 20_000


def test_unknown_sample_rate():
    """Unknown sample rate code → 0."""
    raw = build_frame(sample_rate_code=0xFFFF)
    parser = FrameParser()
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.sample_rate_hz == 0


# ── Reserved byte enforcement ────────────────────────────────────────

def test_reserved_bytes_clean():
    """Default frame has reserved_clean=True."""
    raw = build_frame()
    parser = FrameParser()
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.reserved_clean is True
    assert parser.total_reserved_violations == 0


def test_reserved_bytes_dirty_lenient():
    """Non-zero reserved byte accepted in lenient mode, counter incremented."""
    raw = build_frame(dirty_reserved_offsets=[RSVD_OFFSET])
    parser = FrameParser(strict=False)
    frame = parser.parse_frame(raw)
    assert frame is not None                     # accepted
    assert frame.reserved_clean is False
    assert parser.total_reserved_violations == 1


def test_reserved_bytes_dirty_strict():
    """Non-zero reserved byte rejected in strict mode."""
    raw = build_frame(dirty_reserved_offsets=[RSVD_OFFSET, RSVD_OFFSET + 40])
    parser = FrameParser(strict=True)
    frame = parser.parse_frame(raw)
    assert frame is None                         # rejected
    assert parser.total_reserved_violations == 1


def test_reserved_bytes_multiple_dirty():
    """Multiple dirty reserved bytes still counted as one violation per frame."""
    raw = build_frame(dirty_reserved_offsets=[RSVD_OFFSET, RSVD_OFFSET + 1, CRC_OFFSET - 1])
    parser = FrameParser(strict=False)
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.reserved_clean is False
    assert parser.total_reserved_violations == 1  # one per frame, not per byte


# ── Status reserved bit enforcement ─────────────────────────────────

def test_status_reserved_bits_clean():
    """Status byte with only defined bits set passes reserved check."""
    # bits 7,6,5,2 = defined → 0b11100100 = 0xE4
    raw = build_frame(status=0xE4)
    parser = FrameParser()
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.status_reserved_clean is True
    assert parser.total_status_reserved_violations == 0


def test_status_reserved_bits_dirty_lenient():
    """Reserved status bits set → accepted in lenient, counter incremented."""
    # bit 0 (reserved) set → 0x01
    raw = build_frame(status=0x01)
    parser = FrameParser(strict=False)
    frame = parser.parse_frame(raw)
    assert frame is not None
    assert frame.status_reserved_clean is False
    assert parser.total_status_reserved_violations == 1


def test_status_reserved_bits_dirty_strict():
    """Reserved status bits set → rejected in strict mode."""
    # bits 4,3 (reserved) set → 0x18
    raw = build_frame(status=0x18)
    parser = FrameParser(strict=True)
    frame = parser.parse_frame(raw)
    assert frame is None
    assert parser.total_status_reserved_violations == 1


def test_strict_mode_rejects_both_violations():
    """Strict mode: frame with both reserved byte AND status bit violations is rejected."""
    raw = build_frame(status=0x0B, dirty_reserved_offsets=[200])
    parser = FrameParser(strict=True)
    frame = parser.parse_frame(raw)
    assert frame is None
    # First violation (reserved bytes) triggers rejection; counter incremented
    assert parser.total_reserved_violations == 1


def test_strict_reject_does_not_update_last_frame_id():
    """Strict-rejected frame must NOT advance the drop-detection baseline.

    Sequence: commit frame_id=0, reject frame_id=1 (dirty reserved),
    commit frame_id=2.  If baseline incorrectly advanced to 1 on rejection,
    gap 1→2 = 1 (no drop).  Correctly, baseline stays at 0, gap 0→2 = 1 drop.
    """
    parser = FrameParser(strict=True)

    # Frame 0: clean → committed
    f0 = parser.parse_frame(build_frame(frame_id=0))
    assert f0 is not None
    assert parser.last_frame_id == 0
    assert parser.total_committed == 1

    # Frame 1: dirty reserved byte → rejected
    f1 = parser.parse_frame(build_frame(frame_id=1, dirty_reserved_offsets=[170]))
    assert f1 is None
    assert parser.last_frame_id == 0   # NOT updated
    assert parser.total_committed == 1
    assert parser.total_frames == 2    # seen, not committed

    # Frame 2: clean → committed, gap from 0→2 = 1 drop
    f2 = parser.parse_frame(build_frame(frame_id=2))
    assert f2 is not None
    assert parser.last_frame_id == 2
    assert parser.total_committed == 2
    assert parser.total_drops_detected == 1


def test_strict_reject_status_does_not_update_last_frame_id():
    """Same as above but rejection triggered by dirty status bits."""
    parser = FrameParser(strict=True)

    parser.parse_frame(build_frame(frame_id=10))
    f = parser.parse_frame(build_frame(frame_id=11, status=0x08))  # bit 3 reserved
    assert f is None
    assert parser.last_frame_id == 10  # NOT updated

    f2 = parser.parse_frame(build_frame(frame_id=15))
    assert f2 is not None
    assert parser.total_drops_detected == 4  # gap 10→15


def test_committed_vs_seen_counters():
    """total_frames counts all parsed, total_committed counts only accepted."""
    parser = FrameParser(strict=True)
    parser.parse_frame(build_frame(frame_id=0))                                # committed
    parser.parse_frame(build_frame(frame_id=1, dirty_reserved_offsets=[200]))   # rejected
    parser.parse_frame(build_frame(frame_id=2, status=0x02))                    # rejected (bit 1)
    parser.parse_frame(build_frame(frame_id=3))                                # committed
    assert parser.total_frames == 4
    assert parser.total_committed == 2


# ── Run with pytest or standalone ────────────────────────────────────
if __name__ == "__main__":
    test_funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for func in test_funcs:
        try:
            func()
            print(f"  PASS: {func.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {func.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"frame_parser tests: {passed} PASS, {failed} FAIL")
    print(f"{'='*50}")
    sys.exit(1 if failed else 0)
