"""
Intan .rhd file loader.

Parses RHD2000 binary files produced by the Intan Recording System.
Returns amplifier data in microvolts plus channel metadata.

Reference: Intan Technologies RHD2000 data file format specification
           (importrhdutilities.py open-source reference implementation).

File format summary
-------------------
- Magic number: 0xC6912702 (little-endian uint32)
- Header: version, sample rate, frequency parameters, signal groups, channels
- Data blocks: each block = N samples per channel (N = 128 for v2.0+)
  - timestamps (int32)
  - amplifier data (uint16)  →  µV = 0.195 * (sample − 32768)
  - optional: aux, ADC, digital data
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

# ── Constants ──────────────────────────────────────────────────────────
MAGIC_NUMBER = 0xC6912702
ADC_UV_SCALE = 0.195       # µV per LSB
ADC_OFFSET   = 32768       # unsigned-to-signed midpoint


@dataclass
class RHDHeader:
    """Parsed header from an .rhd file."""
    version_major: int
    version_minor: int
    sample_rate: float
    num_amplifier_channels: int
    num_aux_channels: int
    num_adc_channels: int
    num_digital_in_channels: int
    num_digital_out_channels: int
    channel_names: list[str]
    samples_per_block: int


# ── QString reader ─────────────────────────────────────────────────────
def _read_qstring(fid: BinaryIO) -> str:
    """Read a Qt QString from binary stream (uint32 length + UTF-16 data).

    Qt QStrings store length in *bytes* as a big-endian uint32.
    A length of 0xFFFFFFFF means an empty/null string.
    The string data is UTF-16 (2 bytes per character).
    """
    (length,) = struct.unpack('<I', fid.read(4))
    if length == 0xFFFFFFFF or length == 0:
        return ''
    raw = fid.read(length)
    return raw.decode('utf-16-le', errors='replace').rstrip('\x00')


# ── Header parser ──────────────────────────────────────────────────────
def _read_header(fid: BinaryIO) -> RHDHeader:
    """Parse the .rhd file header. Returns an RHDHeader dataclass."""

    # Magic number
    magic = struct.unpack('<I', fid.read(4))[0]
    if magic != MAGIC_NUMBER:
        raise ValueError(
            f"Not an Intan .rhd file (magic 0x{magic:08X}, "
            f"expected 0x{MAGIC_NUMBER:08X})"
        )

    # Version
    ver_major, ver_minor = struct.unpack('<hh', fid.read(4))

    # Sample rate
    (sample_rate,) = struct.unpack('<f', fid.read(4))

    # DSP enabled + actual DSP cutoff
    _ = struct.unpack('<hf', fid.read(6))  # dsp_enabled, actual_dsp_cutoff

    # Bandwidth parameters (actual_lower, actual_upper)
    _ = struct.unpack('<ff', fid.read(8))

    # Desired bandwidth parameters
    _ = struct.unpack('<ff', fid.read(8))  # desired_lower, desired_upper

    # Notch filter mode
    _ = struct.unpack('<h', fid.read(2))

    # Desired impedance test frequency / actual impedance
    _ = struct.unpack('<ff', fid.read(8))

    # Note strings (3 of them)
    for _ in range(3):
        _read_qstring(fid)

    # v1.1+: DC amplifier data saved flag
    if ver_major > 1 or (ver_major == 1 and ver_minor >= 1):
        _ = struct.unpack('<h', fid.read(2))

    # v1.3+: eval board mode
    if ver_major > 1 or (ver_major == 1 and ver_minor >= 3):
        _ = struct.unpack('<h', fid.read(2))

    # v2.0+: reference channel name
    if ver_major >= 2:
        _read_qstring(fid)

    # ── Signal groups ──────────────────────────────────────────────────
    (num_signal_groups,) = struct.unpack('<h', fid.read(2))

    channel_names: list[str] = []
    num_amplifier = 0
    num_aux = 0
    num_adc = 0
    num_dig_in = 0
    num_dig_out = 0

    for _ in range(num_signal_groups):
        # Group name + prefix
        _read_qstring(fid)  # group name
        _read_qstring(fid)  # group prefix

        (group_enabled, group_num_channels, group_num_amplifier_channels) = \
            struct.unpack('<hhh', fid.read(6))

        for _ in range(group_num_channels):
            ch_name = _read_qstring(fid)
            _read_qstring(fid)  # custom channel name

            (ch_order, ch_custom_order, ch_signal_type,
             ch_enabled, ch_chip_channel, ch_command_stream,
             ch_board_stream) = struct.unpack('<hhhhhhh', fid.read(14))

            # Trigger info
            _ = struct.unpack('<hhh', fid.read(6))

            # Impedance
            _ = struct.unpack('<ff', fid.read(8))

            if ch_enabled:
                if ch_signal_type == 0:
                    num_amplifier += 1
                    channel_names.append(ch_name)
                elif ch_signal_type == 1:
                    num_aux += 1
                elif ch_signal_type == 2:
                    num_adc += 1
                elif ch_signal_type == 3:
                    num_dig_in += 1
                elif ch_signal_type == 4:
                    num_dig_out += 1

    # Samples per data block
    if ver_major > 1:
        samples_per_block = 128
    else:
        samples_per_block = 60

    return RHDHeader(
        version_major=ver_major,
        version_minor=ver_minor,
        sample_rate=sample_rate,
        num_amplifier_channels=num_amplifier,
        num_aux_channels=num_aux,
        num_adc_channels=num_adc,
        num_digital_in_channels=num_dig_in,
        num_digital_out_channels=num_dig_out,
        channel_names=channel_names,
        samples_per_block=samples_per_block,
    )


# ── Bytes per data block ──────────────────────────────────────────────
def _bytes_per_block(hdr: RHDHeader) -> int:
    """Compute the number of bytes in one data block."""
    n = hdr.samples_per_block
    n_amp = hdr.num_amplifier_channels
    n_aux = hdr.num_aux_channels
    n_adc = hdr.num_adc_channels
    n_dig_in = hdr.num_digital_in_channels
    n_dig_out = hdr.num_digital_out_channels

    # Timestamps: int32 × samples_per_block
    total = 4 * n

    # Amplifier data: uint16 × channels × samples_per_block
    total += 2 * n_amp * n

    # Aux data: uint16 × aux_channels × (samples_per_block / 4)
    # Aux is sampled at 1/4 rate
    if n_aux > 0:
        total += 2 * n_aux * (n // 4)

    # Board ADC data: uint16 × adc_channels × samples_per_block
    total += 2 * n_adc * n

    # Digital inputs: uint16 × samples_per_block (packed bitfield)
    if n_dig_in > 0:
        total += 2 * n

    # Digital outputs: uint16 × samples_per_block (packed bitfield)
    if n_dig_out > 0:
        total += 2 * n

    return total


# ── Main loader ────────────────────────────────────────────────────────
def load_rhd(path: str | Path) -> dict:
    """Load an Intan .rhd file.

    Parameters
    ----------
    path : str or Path
        Path to the .rhd file.

    Returns
    -------
    dict with keys:
        - ``amplifier_data_uv``: ndarray, shape (n_channels, n_samples), float64, µV
        - ``timestamps_s``: ndarray, shape (n_samples,), float64, seconds
        - ``sample_rate``: float, Hz
        - ``channel_names``: list[str]
        - ``header``: RHDHeader
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, 'rb') as fid:
        header = _read_header(fid)
        header_size = fid.tell()

        # Calculate data size
        fid.seek(0, 2)  # seek to end
        file_size = fid.tell()
        data_size = file_size - header_size
        block_size = _bytes_per_block(header)

        if block_size == 0:
            raise ValueError("No data channels found in file")

        n_blocks = data_size // block_size
        if n_blocks == 0:
            raise ValueError("No data blocks in file")

        n_samples = n_blocks * header.samples_per_block
        n_amp = header.num_amplifier_channels
        n_aux = header.num_aux_channels
        n_adc = header.num_adc_channels
        n_dig_in = header.num_digital_in_channels
        n_dig_out = header.num_digital_out_channels
        spb = header.samples_per_block

        # Pre-allocate
        timestamps = np.empty(n_samples, dtype=np.int32)
        amplifier_raw = np.empty((n_amp, n_samples), dtype=np.uint16)

        fid.seek(header_size)

        for block in range(n_blocks):
            start = block * spb

            # Timestamps
            ts_data = np.frombuffer(fid.read(4 * spb), dtype='<i4')
            timestamps[start:start + spb] = ts_data

            # Amplifier data (stored channel-interleaved: ch0[0..N], ch1[0..N], ...)
            amp_data = np.frombuffer(
                fid.read(2 * n_amp * spb), dtype='<u2'
            ).reshape(n_amp, spb)
            amplifier_raw[:, start:start + spb] = amp_data

            # Skip aux data
            if n_aux > 0:
                fid.read(2 * n_aux * (spb // 4))

            # Skip board ADC
            if n_adc > 0:
                fid.read(2 * n_adc * spb)

            # Skip digital in
            if n_dig_in > 0:
                fid.read(2 * spb)

            # Skip digital out
            if n_dig_out > 0:
                fid.read(2 * spb)

    # Convert to µV
    amplifier_uv = ADC_UV_SCALE * (amplifier_raw.astype(np.float64) - ADC_OFFSET)

    # Convert timestamps to seconds
    timestamps_s = timestamps.astype(np.float64) / header.sample_rate

    return {
        'amplifier_data_uv': amplifier_uv,
        'timestamps_s': timestamps_s,
        'sample_rate': header.sample_rate,
        'channel_names': header.channel_names,
        'header': header,
    }


# ── Integration with data_loader ──────────────────────────────────────
def load_rhd_as_recording(path: str | Path):
    """Load an .rhd file and return a Recording object.

    This bridges rhd_loader into the data_loader.Recording interface.
    """
    from analysis.data_loader import Recording, RecordingMetadata

    result = load_rhd(path)
    meta = RecordingMetadata(
        source=str(path),
        n_channels=result['amplifier_data_uv'].shape[0],
        duration_s=result['timestamps_s'][-1] - result['timestamps_s'][0],
    )
    return Recording(
        fs=result['sample_rate'],
        data_uv=result['amplifier_data_uv'],
        metadata=meta,
    )
