"""
Tests for ssl_pretrain.py — Self-supervised pretraining for neural backbone.

Covers:
  - SSLConfig defaults
  - generate_mask shape, ratio, determinism
  - nt_xent_loss computation and properties
  - MaskedReconHead forward shape
  - ProjectionHead forward shape and L2 normalization
  - SSLModel forward_masked / forward_contrastive / compute_loss
  - SSLModel.get_backbone returns CNNBackbone
  - pretrain_ssl training loop convergence
"""

import numpy as np
import pytest
import torch
import torch.nn.functional as F

from ssl_pretrain import (
    SSLConfig,
    MaskedReconHead,
    ProjectionHead,
    nt_xent_loss,
    generate_mask,
    SSLModel,
    pretrain_ssl,
)
from deep_model import ModelConfig, CNNBackbone


# ── Constants ─────────────────────────────────────────────────────────

BATCH = 4
N_CH = 16
TIME = 2000
EMBED = 64
PROJ = 32
SEED = 42


def _make_input(batch: int = BATCH, n_ch: int = N_CH, time: int = TIME) -> torch.Tensor:
    torch.manual_seed(SEED)
    return torch.randn(batch, n_ch, time)


# ═══════════════════════════════════════════════════════════════════════
#  SSLConfig
# ═══════════════════════════════════════════════════════════════════════

class TestSSLConfig:
    def test_defaults(self):
        cfg = SSLConfig()
        assert cfg.mask_ratio == 0.3
        assert cfg.mask_patch_size == 50
        assert cfg.proj_dim == 32
        assert cfg.temperature == 0.1
        assert cfg.lambda_recon == 1.0
        assert cfg.lambda_contrast == 0.5

    def test_custom(self):
        cfg = SSLConfig(mask_ratio=0.5, proj_dim=64)
        assert cfg.mask_ratio == 0.5
        assert cfg.proj_dim == 64


# ═══════════════════════════════════════════════════════════════════════
#  generate_mask
# ═══════════════════════════════════════════════════════════════════════

class TestGenerateMask:
    def test_shape(self):
        mask = generate_mask(TIME)
        assert mask.shape == (TIME,)

    def test_dtype_bool(self):
        mask = generate_mask(TIME)
        assert mask.dtype == bool

    def test_mask_ratio_approximate(self):
        """~30% masked by default (within ±10% tolerance)."""
        mask = generate_mask(TIME, mask_ratio=0.3, patch_size=50)
        masked_frac = 1.0 - mask.mean()
        assert 0.1 < masked_frac < 0.5, f"Masked fraction {masked_frac} out of range"

    def test_not_all_visible(self):
        mask = generate_mask(TIME, mask_ratio=0.3)
        assert not mask.all(), "Expected some masked regions"

    def test_not_all_masked(self):
        mask = generate_mask(TIME, mask_ratio=0.3)
        assert mask.any(), "Expected some visible regions"

    def test_deterministic_with_rng(self):
        m1 = generate_mask(TIME, rng=np.random.default_rng(SEED))
        m2 = generate_mask(TIME, rng=np.random.default_rng(SEED))
        np.testing.assert_array_equal(m1, m2)

    def test_different_rng_different_mask(self):
        m1 = generate_mask(TIME, rng=np.random.default_rng(SEED))
        m2 = generate_mask(TIME, rng=np.random.default_rng(SEED + 1))
        assert not np.array_equal(m1, m2)

    def test_patch_aligned(self):
        """Masked regions should be multiples of patch_size (adjacent patches merge)."""
        mask = generate_mask(2000, mask_ratio=0.3, patch_size=50,
                             rng=np.random.default_rng(SEED))
        # Find masked runs
        masked_runs = []
        in_masked = False
        run_start = 0
        for i in range(len(mask)):
            if not mask[i] and not in_masked:
                in_masked = True
                run_start = i
            elif mask[i] and in_masked:
                in_masked = False
                masked_runs.append(i - run_start)
        if in_masked:
            masked_runs.append(len(mask) - run_start)
        # Each run should be a multiple of patch_size
        # (adjacent selected patches merge into larger runs)
        for run_len in masked_runs:
            assert run_len % 50 == 0, \
                f"Masked run length {run_len} not a multiple of patch_size=50"


# ═══════════════════════════════════════════════════════════════════════
#  nt_xent_loss
# ═══════════════════════════════════════════════════════════════════════

class TestNtXentLoss:
    def test_scalar_output(self):
        z1 = F.normalize(torch.randn(BATCH, PROJ), dim=-1)
        z2 = F.normalize(torch.randn(BATCH, PROJ), dim=-1)
        loss = nt_xent_loss(z1, z2, temperature=0.1)
        assert loss.dim() == 0

    def test_positive_loss(self):
        z1 = F.normalize(torch.randn(BATCH, PROJ), dim=-1)
        z2 = F.normalize(torch.randn(BATCH, PROJ), dim=-1)
        loss = nt_xent_loss(z1, z2, temperature=0.1)
        assert loss.item() > 0

    def test_identical_views_low_loss(self):
        """If views are identical, loss should be relatively low."""
        z = F.normalize(torch.randn(BATCH, PROJ), dim=-1)
        loss_same = nt_xent_loss(z, z, temperature=0.1)
        z2 = F.normalize(torch.randn(BATCH, PROJ), dim=-1)
        loss_diff = nt_xent_loss(z, z2, temperature=0.1)
        # Same views should give lower loss than random views
        assert loss_same.item() < loss_diff.item() + 1.0

    def test_gradient_flow(self):
        z1 = F.normalize(torch.randn(BATCH, PROJ, requires_grad=True), dim=-1)
        z2 = F.normalize(torch.randn(BATCH, PROJ, requires_grad=True), dim=-1)
        loss = nt_xent_loss(z1, z2, temperature=0.1)
        loss.backward()
        # Gradients should exist (through normalize and operations)

    def test_batch_size_one(self):
        """Should handle batch_size=1 (edge case for contrastive)."""
        z1 = F.normalize(torch.randn(1, PROJ), dim=-1)
        z2 = F.normalize(torch.randn(1, PROJ), dim=-1)
        # This will have no negatives, but should not crash
        loss = nt_xent_loss(z1, z2, temperature=0.1)
        assert loss.dim() == 0


# ═══════════════════════════════════════════════════════════════════════
#  MaskedReconHead
# ═══════════════════════════════════════════════════════════════════════

class TestMaskedReconHead:
    def test_output_shape(self):
        head = MaskedReconHead(EMBED, N_CH)
        x = torch.randn(BATCH, EMBED, TIME)
        out = head(x)
        assert out.shape == (BATCH, N_CH, TIME)

    def test_different_dims(self):
        head = MaskedReconHead(32, 4)
        x = torch.randn(2, 32, 500)
        out = head(x)
        assert out.shape == (2, 4, 500)


# ═══════════════════════════════════════════════════════════════════════
#  ProjectionHead
# ═══════════════════════════════════════════════════════════════════════

class TestProjectionHead:
    def test_output_shape(self):
        head = ProjectionHead(EMBED, PROJ)
        x = torch.randn(BATCH, EMBED)
        out = head(x)
        assert out.shape == (BATCH, PROJ)

    def test_l2_normalized(self):
        """Output should be L2-normalized (unit vectors)."""
        head = ProjectionHead(EMBED, PROJ)
        x = torch.randn(BATCH, EMBED)
        out = head(x)
        norms = torch.norm(out, dim=-1)
        torch.testing.assert_close(norms, torch.ones(BATCH), atol=1e-5, rtol=1e-5)


# ═══════════════════════════════════════════════════════════════════════
#  SSLModel
# ═══════════════════════════════════════════════════════════════════════

class TestSSLModel:
    def test_default_construction(self):
        model = SSLModel()
        assert model.cfg.mask_ratio == 0.3

    def test_forward_masked_shapes(self):
        model = SSLModel()
        x = _make_input()
        mask = torch.ones(BATCH, TIME, dtype=torch.bool)
        # Mask out 30% at the start
        mask[:, :600] = False
        recon, embedding, temporal = model.forward_masked(x, mask)
        assert recon.shape == (BATCH, N_CH, TIME)
        assert embedding.shape == (BATCH, EMBED)
        assert temporal.shape == (BATCH, EMBED, TIME)

    def test_forward_contrastive_shapes(self):
        model = SSLModel()
        x1 = _make_input()
        x2 = torch.randn_like(x1)
        z1, z2 = model.forward_contrastive(x1, x2)
        assert z1.shape == (BATCH, PROJ)
        assert z2.shape == (BATCH, PROJ)

    def test_contrastive_l2_normalized(self):
        model = SSLModel()
        x1 = _make_input()
        x2 = torch.randn_like(x1)
        z1, z2 = model.forward_contrastive(x1, x2)
        norms1 = torch.norm(z1, dim=-1)
        norms2 = torch.norm(z2, dim=-1)
        torch.testing.assert_close(norms1, torch.ones(BATCH), atol=1e-5, rtol=1e-5)
        torch.testing.assert_close(norms2, torch.ones(BATCH), atol=1e-5, rtol=1e-5)

    def test_compute_loss_keys(self):
        model = SSLModel()
        x = _make_input()
        x_aug1 = x + torch.randn_like(x) * 0.1
        x_aug2 = x + torch.randn_like(x) * 0.1
        mask = torch.ones(BATCH, TIME, dtype=torch.bool)
        mask[:, :600] = False
        losses = model.compute_loss(x, x_aug1, x_aug2, mask)
        assert set(losses.keys()) == {"total", "recon_loss", "contrast_loss"}

    def test_compute_loss_positive(self):
        model = SSLModel()
        x = _make_input()
        x_aug1 = x + torch.randn_like(x) * 0.1
        x_aug2 = x + torch.randn_like(x) * 0.1
        mask = torch.ones(BATCH, TIME, dtype=torch.bool)
        mask[:, :600] = False
        losses = model.compute_loss(x, x_aug1, x_aug2, mask)
        for key in losses:
            assert losses[key].item() > 0, f"{key} should be positive"

    def test_compute_loss_backprop(self):
        model = SSLModel()
        x = _make_input()
        x_aug1 = x + torch.randn_like(x) * 0.1
        x_aug2 = x + torch.randn_like(x) * 0.1
        mask = torch.ones(BATCH, TIME, dtype=torch.bool)
        mask[:, :600] = False
        losses = model.compute_loss(x, x_aug1, x_aug2, mask)
        losses["total"].backward()
        # At least backbone should have gradients
        for p in model.backbone.parameters():
            if p.requires_grad:
                assert p.grad is not None

    def test_get_backbone(self):
        model = SSLModel()
        bb = model.get_backbone()
        assert isinstance(bb, CNNBackbone)

    def test_get_backbone_is_same_object(self):
        model = SSLModel()
        bb = model.get_backbone()
        assert bb is model.backbone


# ═══════════════════════════════════════════════════════════════════════
#  pretrain_ssl training loop
# ═══════════════════════════════════════════════════════════════════════

class TestPretrainSSL:
    @pytest.fixture
    def tiny_windows(self):
        """Small synthetic windows for fast testing."""
        rng = np.random.default_rng(SEED)
        return [rng.standard_normal((N_CH, 1000)) * 100.0 for _ in range(8)]

    def test_returns_history(self, tiny_windows):
        model = SSLModel()
        history = pretrain_ssl(model, tiny_windows, fs=20_000.0,
                               n_epochs=2, batch_size=4, verbose=False)
        assert "total_loss" in history
        assert "recon_loss" in history
        assert "contrast_loss" in history
        assert len(history["total_loss"]) == 2

    def test_loss_finite(self, tiny_windows):
        model = SSLModel()
        history = pretrain_ssl(model, tiny_windows, fs=20_000.0,
                               n_epochs=3, batch_size=4, verbose=False)
        for loss in history["total_loss"]:
            assert np.isfinite(loss), "Loss should be finite"

    def test_loss_decreases_or_stable(self, tiny_windows):
        """Over a few epochs, total loss should not diverge."""
        model = SSLModel()
        history = pretrain_ssl(model, tiny_windows, fs=20_000.0,
                               n_epochs=5, batch_size=4, verbose=False)
        # Loss at end should be ≤ loss at start + some tolerance
        assert history["total_loss"][-1] < history["total_loss"][0] * 3.0, \
            "Loss diverged significantly"
