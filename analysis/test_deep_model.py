"""
Tests for deep_model.py — 1D CNN + Attention Pooling multi-task model.

Covers:
  - ModelConfig defaults and customization
  - ConvBlock, ChannelMixer, AttentionPool building blocks
  - CNNBackbone forward pass shapes
  - NeuralQANet full forward pass shapes
  - NeuralQANet parameter count
  - MultiTaskLoss computation and gradients
  - calibrate_temperature utility
  - encode_labels label encoding
  - CLASS_NAMES / CLASS_TO_IDX / BINARY_MASK_MAP consistency
  - Calibrated probabilities sum to 1
  - Deterministic under torch.manual_seed
"""

import numpy as np
import pytest
import torch
import torch.nn.functional as F

from deep_model import (
    ModelConfig,
    ConvBlock,
    ChannelMixer,
    AttentionPool,
    CNNBackbone,
    ArtifactMaskHead,
    ClassificationHead,
    SeverityHead,
    NeuralQANet,
    MultiTaskLoss,
    calibrate_temperature,
    encode_labels,
    CLASS_NAMES,
    CLASS_TO_IDX,
    BINARY_MASK_MAP,
)


# ── Constants ─────────────────────────────────────────────────────────

BATCH = 4
N_CH = 16
TIME = 2000
N_CLASSES = 9
EMBED = 64
SEED = 42


def _make_input(batch: int = BATCH, n_ch: int = N_CH, time: int = TIME) -> torch.Tensor:
    torch.manual_seed(SEED)
    return torch.randn(batch, n_ch, time)


# ═══════════════════════════════════════════════════════════════════════
#  ModelConfig
# ═══════════════════════════════════════════════════════════════════════

class TestModelConfig:
    def test_defaults(self):
        cfg = ModelConfig()
        assert cfg.n_channels == 16
        assert cfg.n_classes == 9
        assert cfg.embed_dim == 64
        assert cfg.n_conv_blocks == 3
        assert cfg.kernel_size == 7
        assert cfg.dropout == 0.1

    def test_custom(self):
        cfg = ModelConfig(n_channels=4, n_classes=5, embed_dim=32)
        assert cfg.n_channels == 4
        assert cfg.n_classes == 5
        assert cfg.embed_dim == 32

    def test_loss_weights(self):
        cfg = ModelConfig()
        assert cfg.lambda_cls == 1.0
        assert cfg.lambda_mask == 0.5
        assert cfg.lambda_sev == 0.3


# ═══════════════════════════════════════════════════════════════════════
#  Building blocks
# ═══════════════════════════════════════════════════════════════════════

class TestConvBlock:
    def test_shape(self):
        block = ConvBlock(N_CH, EMBED, kernel_size=7)
        x = _make_input()
        out = block(x)
        assert out.shape == (BATCH, EMBED, TIME)

    def test_different_kernel(self):
        block = ConvBlock(N_CH, EMBED, kernel_size=3)
        x = _make_input()
        out = block(x)
        assert out.shape == (BATCH, EMBED, TIME)


class TestChannelMixer:
    def test_shape_and_residual(self):
        mixer = ChannelMixer(EMBED)
        x = torch.randn(BATCH, EMBED, TIME)
        out = mixer(x)
        assert out.shape == x.shape

    def test_not_identity(self):
        """Mixer should modify data (not just pass through residual)."""
        mixer = ChannelMixer(EMBED)
        x = torch.randn(BATCH, EMBED, TIME)
        out = mixer(x)
        # Due to GELU, norm, fc, output should differ from input
        assert not torch.allclose(x, out, atol=1e-3)


class TestAttentionPool:
    def test_shape(self):
        pool = AttentionPool(EMBED)
        x = torch.randn(BATCH, EMBED, TIME)
        pooled, weights = pool(x)
        assert pooled.shape == (BATCH, EMBED)
        assert weights.shape == (BATCH, TIME)

    def test_weights_sum_to_one(self):
        pool = AttentionPool(EMBED)
        x = torch.randn(BATCH, EMBED, TIME)
        _, weights = pool(x)
        sums = weights.sum(dim=1)
        torch.testing.assert_close(sums, torch.ones(BATCH), atol=1e-5, rtol=1e-5)

    def test_weights_nonnegative(self):
        pool = AttentionPool(EMBED)
        x = torch.randn(BATCH, EMBED, TIME)
        _, weights = pool(x)
        assert (weights >= 0).all()


# ═══════════════════════════════════════════════════════════════════════
#  CNNBackbone
# ═══════════════════════════════════════════════════════════════════════

class TestCNNBackbone:
    def test_output_shapes(self):
        cfg = ModelConfig()
        backbone = CNNBackbone(cfg)
        x = _make_input()
        embedding, temporal, attn_weights = backbone(x)
        assert embedding.shape == (BATCH, EMBED)
        assert temporal.shape == (BATCH, EMBED, TIME)
        assert attn_weights.shape == (BATCH, TIME)

    def test_custom_config(self):
        cfg = ModelConfig(n_channels=4, embed_dim=32, n_conv_blocks=2)
        backbone = CNNBackbone(cfg)
        x = torch.randn(BATCH, 4, TIME)
        embedding, temporal, attn_weights = backbone(x)
        assert embedding.shape == (BATCH, 32)
        assert temporal.shape == (BATCH, 32, TIME)


# ═══════════════════════════════════════════════════════════════════════
#  Task heads
# ═══════════════════════════════════════════════════════════════════════

class TestTaskHeads:
    def test_mask_head_shape(self):
        head = ArtifactMaskHead(EMBED)
        emb = torch.randn(BATCH, EMBED)
        out = head(emb)
        assert out.shape == (BATCH, 1)

    def test_cls_head_shape(self):
        head = ClassificationHead(EMBED, N_CLASSES)
        emb = torch.randn(BATCH, EMBED)
        out = head(emb)
        assert out.shape == (BATCH, N_CLASSES)

    def test_severity_head_range(self):
        head = SeverityHead(EMBED)
        emb = torch.randn(BATCH, EMBED)
        out = head(emb)
        assert out.shape == (BATCH, 1)
        assert (out >= 0.0).all() and (out <= 1.0).all(), \
            "Severity head must output in [0, 1]"


# ═══════════════════════════════════════════════════════════════════════
#  NeuralQANet — full model
# ═══════════════════════════════════════════════════════════════════════

class TestNeuralQANet:
    def test_forward_shapes(self):
        model = NeuralQANet()
        x = _make_input()
        out = model(x)
        assert out["cls_logits"].shape == (BATCH, N_CLASSES)
        assert out["mask_logit"].shape == (BATCH, 1)
        assert out["severity"].shape == (BATCH, 1)
        assert out["embedding"].shape == (BATCH, EMBED)
        assert out["attn_weights"].shape == (BATCH, TIME)

    def test_all_keys_present(self):
        model = NeuralQANet()
        x = _make_input()
        out = model(x)
        expected_keys = {"cls_logits", "mask_logit", "severity",
                         "embedding", "attn_weights"}
        assert set(out.keys()) == expected_keys

    def test_parameter_count(self):
        model = NeuralQANet()
        n = model.count_parameters()
        assert n > 10_000, "Model too small"
        assert n < 500_000, "Model too large for CPU"

    def test_param_count_exact(self):
        model = NeuralQANet()
        assert model.count_parameters() == 76_045

    def test_severity_in_range(self):
        model = NeuralQANet()
        x = _make_input()
        out = model(x)
        sev = out["severity"]
        assert (sev >= 0.0).all() and (sev <= 1.0).all()

    def test_batch_one(self):
        """Should work with batch_size=1 (LayerNorm, not BatchNorm)."""
        model = NeuralQANet()
        x = torch.randn(1, N_CH, TIME)
        out = model(x)
        assert out["cls_logits"].shape == (1, N_CLASSES)

    def test_variable_length(self):
        """Should handle different time lengths."""
        model = NeuralQANet()
        for t in [500, 1000, 4000]:
            x = torch.randn(2, N_CH, t)
            out = model(x)
            assert out["cls_logits"].shape == (2, N_CLASSES)
            assert out["attn_weights"].shape == (2, t)

    def test_default_config_used(self):
        model = NeuralQANet()
        assert model.cfg.n_channels == 16

    def test_custom_config(self):
        cfg = ModelConfig(n_channels=4, n_classes=5, embed_dim=32)
        model = NeuralQANet(cfg)
        x = torch.randn(2, 4, 1000)
        out = model(x)
        assert out["cls_logits"].shape == (2, 5)

    def test_calibrated_probs(self):
        model = NeuralQANet()
        logits = torch.randn(BATCH, N_CLASSES)
        probs = model.calibrated_probs(logits)
        # Probabilities sum to 1
        sums = probs.sum(dim=1)
        torch.testing.assert_close(sums, torch.ones(BATCH), atol=1e-5, rtol=1e-5)
        # All non-negative
        assert (probs >= 0.0).all()

    def test_deterministic(self):
        torch.manual_seed(SEED)
        model = NeuralQANet()
        model.eval()
        x = _make_input()
        out1 = model(x)
        out2 = model(x)
        torch.testing.assert_close(out1["cls_logits"], out2["cls_logits"])

    def test_gradient_flow(self):
        """All parameters (except temperature) should receive gradients."""
        model = NeuralQANet()
        x = _make_input()
        out = model(x)
        loss = out["cls_logits"].sum() + out["mask_logit"].sum() + out["severity"].sum()
        loss.backward()
        for name, p in model.named_parameters():
            if p.requires_grad and name != "temperature":
                assert p.grad is not None, f"No gradient for {name}"


# ═══════════════════════════════════════════════════════════════════════
#  MultiTaskLoss
# ═══════════════════════════════════════════════════════════════════════

class TestMultiTaskLoss:
    def _make_targets(self):
        return {
            "class_labels": torch.randint(0, N_CLASSES, (BATCH,)),
            "mask_labels": torch.randint(0, 2, (BATCH,)).float(),
            "severity": torch.rand(BATCH),
        }

    def test_loss_keys(self):
        model = NeuralQANet()
        criterion = MultiTaskLoss()
        x = _make_input()
        outputs = model(x)
        targets = self._make_targets()
        losses = criterion(outputs, targets)
        assert set(losses.keys()) == {"total", "cls_loss", "mask_loss", "sev_loss"}

    def test_loss_positive(self):
        model = NeuralQANet()
        criterion = MultiTaskLoss()
        x = _make_input()
        outputs = model(x)
        targets = self._make_targets()
        losses = criterion(outputs, targets)
        for key, val in losses.items():
            assert val.item() > 0.0, f"{key} should be positive"

    def test_loss_scalar(self):
        model = NeuralQANet()
        criterion = MultiTaskLoss()
        x = _make_input()
        outputs = model(x)
        targets = self._make_targets()
        losses = criterion(outputs, targets)
        for key, val in losses.items():
            assert val.dim() == 0, f"{key} should be scalar"

    def test_total_is_weighted_sum(self):
        cfg = ModelConfig()
        model = NeuralQANet(cfg)
        criterion = MultiTaskLoss(cfg)
        x = _make_input()
        outputs = model(x)
        targets = self._make_targets()
        losses = criterion(outputs, targets)
        expected = (cfg.lambda_cls * losses["cls_loss"]
                    + cfg.lambda_mask * losses["mask_loss"]
                    + cfg.lambda_sev * losses["sev_loss"])
        torch.testing.assert_close(losses["total"], expected, atol=1e-6, rtol=1e-6)

    def test_backprop(self):
        model = NeuralQANet()
        criterion = MultiTaskLoss()
        x = _make_input()
        outputs = model(x)
        targets = self._make_targets()
        losses = criterion(outputs, targets)
        losses["total"].backward()
        # Check gradients exist (temperature only used in calibration)
        for name, p in model.named_parameters():
            if p.requires_grad and name != "temperature":
                assert p.grad is not None, f"No gradient for {name}"


# ═══════════════════════════════════════════════════════════════════════
#  Temperature calibration
# ═══════════════════════════════════════════════════════════════════════

class TestCalibrateTemperature:
    def test_returns_float(self):
        model = NeuralQANet()
        logits = torch.randn(20, N_CLASSES)
        labels = torch.randint(0, N_CLASSES, (20,))
        temp = calibrate_temperature(model, logits, labels)
        assert isinstance(temp, float)

    def test_positive_temperature(self):
        model = NeuralQANet()
        logits = torch.randn(20, N_CLASSES)
        labels = torch.randint(0, N_CLASSES, (20,))
        temp = calibrate_temperature(model, logits, labels)
        assert temp > 0, "Temperature must be positive"

    def test_model_temperature_updated(self):
        model = NeuralQANet()
        old_temp = model.temperature.item()
        logits = torch.randn(30, N_CLASSES)
        labels = torch.randint(0, N_CLASSES, (30,))
        new_temp = calibrate_temperature(model, logits, labels)
        assert model.temperature.item() == pytest.approx(new_temp, abs=1e-4)


# ═══════════════════════════════════════════════════════════════════════
#  Label encoding
# ═══════════════════════════════════════════════════════════════════════

class TestLabelEncoding:
    def test_class_names_count(self):
        assert len(CLASS_NAMES) == 9

    def test_class_to_idx_matches(self):
        assert len(CLASS_TO_IDX) == len(CLASS_NAMES)
        for i, name in enumerate(CLASS_NAMES):
            assert CLASS_TO_IDX[name] == i

    def test_binary_mask_map_coverage(self):
        for name in CLASS_NAMES:
            assert name in BINARY_MASK_MAP

    def test_baseline_not_masked(self):
        assert BINARY_MASK_MAP["baseline_stable"] == 0.0

    def test_non_baseline_masked(self):
        for name in CLASS_NAMES:
            if name != "baseline_stable":
                assert BINARY_MASK_MAP[name] == 1.0

    def test_encode_labels_shapes(self):
        types = ["baseline_stable", "line_interference", "spike_suppression"]
        sevs = [0.0, 0.5, 0.8]
        result = encode_labels(types, sevs)
        assert result["class_labels"].shape == (3,)
        assert result["mask_labels"].shape == (3,)
        assert result["severity"].shape == (3,)

    def test_encode_labels_values(self):
        types = ["baseline_stable", "line_interference"]
        sevs = [0.0, 0.7]
        result = encode_labels(types, sevs)
        assert result["class_labels"][0].item() == CLASS_TO_IDX["baseline_stable"]
        assert result["class_labels"][1].item() == CLASS_TO_IDX["line_interference"]
        assert result["mask_labels"][0].item() == 0.0
        assert result["mask_labels"][1].item() == 1.0

    def test_encode_unknown_defaults_to_zero(self):
        result = encode_labels(["unknown_type"], [0.5])
        assert result["class_labels"][0].item() == 0
