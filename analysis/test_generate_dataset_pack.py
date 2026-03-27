"""
test_generate_dataset_pack.py — Tests for reproducible dataset bundle.

Covers:
  - generate_pack() creates all files
  - verify_pack() validates hashes
  - Metadata JSON structure and content
  - Reproducibility (same seed → same hashes)
  - _windows_to_npz() shape correctness
  - CLI round-trip
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from generate_dataset_pack import (
    _sha256,
    _windows_to_npz,
    generate_pack,
    verify_pack,
)
from perturbation_library import (
    generate_baseline,
    LabeledWindow,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
    HOLDOUT_TYPES,
)
from features import N_FEATURES, FEATURE_NAMES


@pytest.fixture()
def tmp_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ── _windows_to_npz ─────────────────────────────────────────────────

class TestWindowsToNpz:
    def test_creates_file(self, tmp_dir: Path):
        w = generate_baseline(seed=1, n_channels=4, fs=1000, duration_s=1.0)
        out = tmp_dir / "test.npz"
        _windows_to_npz([w], out)
        assert out.exists()

    def test_shapes(self, tmp_dir: Path):
        wins = [generate_baseline(seed=i, n_channels=4, fs=1000, duration_s=1.0)
                for i in range(3)]
        out = tmp_dir / "test.npz"
        _windows_to_npz(wins, out)
        with np.load(out) as d:
            assert d["data_uv"].shape == (3, 4, 1000)
            assert len(d["categories"]) == 3
            assert len(d["perturbation_types"]) == 3
            assert len(d["severities"]) == 3
            assert int(d["n_windows"]) == 3

    def test_empty_noop(self, tmp_dir: Path):
        out = tmp_dir / "empty.npz"
        _windows_to_npz([], out)
        assert not out.exists()

    def test_metadata_arrays(self, tmp_dir: Path):
        w = generate_baseline(seed=7, n_channels=4, fs=1000, duration_s=1.0)
        out = tmp_dir / "test.npz"
        _windows_to_npz([w], out)
        with np.load(out) as d:
            assert str(d["categories"][0]) == "baseline"
            assert str(d["perturbation_types"][0]) == "baseline_stable"
            assert float(d["severities"][0]) == 0.0


# ── generate_pack ────────────────────────────────────────────────────

class TestGeneratePack:
    def test_creates_all_files(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=99, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert (tmp_dir / "train.npz").exists()
        assert (tmp_dir / "holdout.npz").exists()
        assert (tmp_dir / "metadata.json").exists()

    def test_metadata_version(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=99, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert meta["version"] == "1.0"

    def test_metadata_parameters(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=2, n_holdout=3,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        p = meta["parameters"]
        assert p["seed"] == 42
        assert p["n_per_class"] == 2
        assert p["n_holdout"] == 3
        assert p["n_channels"] == 4

    def test_training_label_counts(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        # 1 baseline + 8 types × 3 severities × 1 = 25
        assert meta["training"]["n_windows"] == 25

    def test_holdout_count(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=7,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert meta["holdout"]["n_windows"] == 7

    def test_hashes_present(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert len(meta["training"]["sha256"]) == 64  # hex SHA-256
        assert len(meta["holdout"]["sha256"]) == 64

    def test_hashes_match_files(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert _sha256(tmp_dir / "train.npz") == meta["training"]["sha256"]
        assert _sha256(tmp_dir / "holdout.npz") == meta["holdout"]["sha256"]

    def test_feature_spec(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert meta["feature_spec"]["n_features"] == N_FEATURES
        assert meta["feature_spec"]["feature_names"] == FEATURE_NAMES

    def test_perturbation_types_listed(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        ptypes = meta["training"]["perturbation_types"]
        assert "baseline_stable" in ptypes
        for a in ARTIFACT_TYPES:
            assert a in ptypes
        for n in NEUROTOX_TYPES:
            assert n in ptypes
        for h in HOLDOUT_TYPES:
            assert h in meta["holdout"]["perturbation_types"]

    def test_reproduction_command(self, tmp_dir: Path):
        meta = generate_pack(tmp_dir, seed=42, n_per_class=2, n_holdout=3,
                             n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        cmd = meta["reproduction"]["command"]
        assert "--seed 42" in cmd
        assert "--n-per-class 2" in cmd
        assert "--n-holdout 3" in cmd


# ── verify_pack ──────────────────────────────────────────────────────

class TestVerifyPack:
    def test_valid_pack_passes(self, tmp_dir: Path):
        generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                      n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        assert verify_pack(tmp_dir) is True

    def test_tampered_file_fails(self, tmp_dir: Path):
        generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                      n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        # Tamper with train.npz
        train_path = tmp_dir / "train.npz"
        data = bytearray(train_path.read_bytes())
        data[-1] ^= 0xFF  # flip last byte
        train_path.write_bytes(bytes(data))
        assert verify_pack(tmp_dir) is False

    def test_missing_file_fails(self, tmp_dir: Path):
        generate_pack(tmp_dir, seed=42, n_per_class=1, n_holdout=1,
                      n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
        (tmp_dir / "holdout.npz").unlink()
        assert verify_pack(tmp_dir) is False

    def test_no_metadata_fails(self, tmp_dir: Path):
        assert verify_pack(tmp_dir) is False


# ── Reproducibility ──────────────────────────────────────────────────

class TestReproducibility:
    def test_same_seed_same_hashes(self):
        d1 = Path(tempfile.mkdtemp())
        d2 = Path(tempfile.mkdtemp())
        try:
            m1 = generate_pack(d1, seed=42, n_per_class=1, n_holdout=2,
                               n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
            m2 = generate_pack(d2, seed=42, n_per_class=1, n_holdout=2,
                               n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
            assert m1["training"]["sha256"] == m2["training"]["sha256"]
            assert m1["holdout"]["sha256"] == m2["holdout"]["sha256"]
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)

    def test_different_seed_different_hashes(self):
        d1 = Path(tempfile.mkdtemp())
        d2 = Path(tempfile.mkdtemp())
        try:
            m1 = generate_pack(d1, seed=42, n_per_class=1, n_holdout=1,
                               n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
            m2 = generate_pack(d2, seed=99, n_per_class=1, n_holdout=1,
                               n_channels=4, fs=1000, duration_s=1.0, onset_s=0.3)
            assert m1["training"]["sha256"] != m2["training"]["sha256"]
        finally:
            shutil.rmtree(d1, ignore_errors=True)
            shutil.rmtree(d2, ignore_errors=True)
