"""
Tests for capture.py — host-side capture + validation tool.

Covers:
  - Synthetic frame builder
  - CaptureStats
  - Frame sequence validation
  - .npz output writer
  - Smoke test
"""

import struct
import tempfile
from pathlib import Path

import numpy as np
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent))

from capture import (
    CaptureStats,
    validate_frame_sequence,
    frames_to_npz,
    _build_synthetic_frame,
    smoke_test,
)
from frame_parser import (
    FrameParser,
    Frame,
    FRAME_SIZE,
    ADC_CHANNELS,
    AUX_CHANNELS,
    CRC_OFFSET,
    MAGIC,
    VERSION,
)


# ═══════════════════════════════════════════════════════════════════
#  Synthetic frame builder
# ═══════════════════════════════════════════════════════════════════

class TestBuildSyntheticFrame:
    def test_correct_size(self):
        f = _build_synthetic_frame(frame_id=0, timestamp=0)
        assert len(f) == FRAME_SIZE

    def test_magic_bytes(self):
        f = _build_synthetic_frame(frame_id=0, timestamp=0)
        assert f[0] == 0xA5
        assert f[1] == 0x5A

    def test_version_byte(self):
        f = _build_synthetic_frame(frame_id=0, timestamp=0)
        assert f[2] == VERSION

    def test_frame_id_encoded(self):
        f = _build_synthetic_frame(frame_id=42, timestamp=0)
        fid = struct.unpack_from(">I", f, 4)[0]
        assert fid == 42

    def test_timestamp_encoded(self):
        f = _build_synthetic_frame(frame_id=0, timestamp=12345)
        ts = struct.unpack_from(">I", f, 8)[0]
        assert ts == 12345

    def test_parseable(self):
        """Synthetic frame should parse correctly."""
        raw = _build_synthetic_frame(frame_id=7, timestamp=100)
        parser = FrameParser()
        frame = parser.parse_frame(raw)
        assert frame is not None
        assert frame.frame_id == 7
        assert frame.timestamp == 100
        assert frame.crc_valid

    def test_custom_adc(self):
        adc = [i * 100 for i in range(ADC_CHANNELS)]
        raw = _build_synthetic_frame(frame_id=0, timestamp=0, adc_values=adc)
        parser = FrameParser()
        frame = parser.parse_frame(raw)
        assert frame is not None
        for i in range(ADC_CHANNELS):
            assert frame.adc_samples[i] == adc[i]


# ═══════════════════════════════════════════════════════════════════
#  CaptureStats
# ═══════════════════════════════════════════════════════════════════

class TestCaptureStats:
    def test_defaults(self):
        s = CaptureStats()
        assert s.frames_received == 0
        assert s.frames_valid == 0
        assert s.drop_rate == 0.0

    def test_drop_rate(self):
        s = CaptureStats(frames_valid=90, frames_dropped=10)
        assert abs(s.drop_rate - 0.1) < 0.001

    def test_duration(self):
        s = CaptureStats(start_time=10.0, end_time=20.0)
        assert s.duration_s == 10.0

    def test_throughput(self):
        s = CaptureStats(start_time=0.0, end_time=10.0, bytes_received=10240)
        assert abs(s.throughput_bps - 1024.0) < 0.01

    def test_summary_string(self):
        s = CaptureStats(frames_received=100, frames_valid=95)
        summary = s.summary()
        assert "100" in summary
        assert "95" in summary


# ═══════════════════════════════════════════════════════════════════
#  Frame sequence validation
# ═══════════════════════════════════════════════════════════════════

class TestValidateFrameSequence:
    def _parse_frames(self, raw_bytes: bytes) -> list[Frame]:
        parser = FrameParser()
        return list(parser.parse_stream(raw_bytes))

    def test_consecutive_frames(self):
        """100 consecutive frames → no drops, all valid."""
        raw = bytearray()
        for i in range(100):
            raw.extend(_build_synthetic_frame(frame_id=i, timestamp=i * 50))
        frames = self._parse_frames(bytes(raw))
        stats, valid = validate_frame_sequence(frames)
        assert stats.frames_valid == 100
        assert stats.frames_dropped == 0
        assert stats.frames_crc_fail == 0
        assert len(valid) == 100

    def test_gap_detection(self):
        """Gap in frame IDs → dropped frames detected."""
        raw = bytearray()
        raw.extend(_build_synthetic_frame(frame_id=0, timestamp=0))
        raw.extend(_build_synthetic_frame(frame_id=1, timestamp=50))
        raw.extend(_build_synthetic_frame(frame_id=10, timestamp=500))
        frames = self._parse_frames(bytes(raw))
        stats, valid = validate_frame_sequence(frames)
        assert stats.frames_valid == 3
        assert stats.frames_dropped == 8  # IDs 2-9 missing

    def test_crc_failure(self):
        """Corrupted frame → CRC failure counted."""
        good = _build_synthetic_frame(frame_id=0, timestamp=0)
        bad = bytearray(_build_synthetic_frame(frame_id=1, timestamp=50))
        bad[20] ^= 0xFF  # corrupt ADC payload
        raw = good + bytes(bad)
        frames = self._parse_frames(raw)
        stats, valid = validate_frame_sequence(frames)
        assert stats.frames_crc_fail >= 1
        assert len(valid) < len(frames)

    def test_empty_input(self):
        stats, valid = validate_frame_sequence([])
        assert stats.frames_received == 0
        assert len(valid) == 0

    def test_first_last_tracking(self):
        """First/last frame ID and timestamp should be tracked."""
        raw = bytearray()
        raw.extend(_build_synthetic_frame(frame_id=5, timestamp=100))
        raw.extend(_build_synthetic_frame(frame_id=6, timestamp=200))
        raw.extend(_build_synthetic_frame(frame_id=7, timestamp=300))
        frames = self._parse_frames(bytes(raw))
        stats, _ = validate_frame_sequence(frames)
        assert stats.first_frame_id == 5
        assert stats.last_frame_id == 7
        assert stats.first_timestamp == 100
        assert stats.last_timestamp == 300


# ═══════════════════════════════════════════════════════════════════
#  .npz writer
# ═══════════════════════════════════════════════════════════════════

class TestFramesToNpz:
    def test_write_and_read(self):
        """Write frames → read .npz → verify shapes."""
        raw = bytearray()
        n = 10
        for i in range(n):
            raw.extend(_build_synthetic_frame(frame_id=i, timestamp=i * 50))

        parser = FrameParser()
        frames = list(parser.parse_stream(bytes(raw)))

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.npz"
            frames_to_npz(frames, out)
            assert out.exists()

            data = np.load(out, allow_pickle=True)
            assert data["adc_data"].shape == (ADC_CHANNELS, n)
            assert data["aux_data"].shape == (AUX_CHANNELS, n)
            assert data["frame_ids"].shape == (n,)
            assert data["timestamps"].shape == (n,)
            assert int(data["n_frames"]) == n
            assert int(data["n_channels"]) == ADC_CHANNELS

    def test_empty_frames(self, capsys):
        """No frames → warning, no file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "empty.npz"
            frames_to_npz([], out)
            captured = capsys.readouterr()
            assert "WARNING" in captured.out
            assert not out.exists()


# ═══════════════════════════════════════════════════════════════════
#  Smoke test
# ═══════════════════════════════════════════════════════════════════

class TestSmokeTest:
    def test_passes(self):
        """The built-in smoke test should pass."""
        stats = smoke_test()
        assert stats.frames_valid == 101
        assert stats.frames_crc_fail == 0
        assert stats.frames_dropped == 5
