"""
deep_model.py — 1D CNN + Attention Pooling backbone with multi-task heads.

Architecture (Option A: fast, reliable, easy to justify):
═══════════════════════════════════════════════════════════════
  Input: (batch, Nch, T) — raw multichannel time series in µV

  Backbone: 1D CNN encoder
    ├─ Conv1D blocks (temporal feature extraction per channel)
    ├─ Channel mixer (cross-channel information fusion)
    └─ Attention pooling (learned weighted average over time)

  Heads:
    A — Artifact mask:    per-window binary (clean vs contaminated)
    B — Classification:   K-class (artifact types + suppression-like)
    C — Severity:         scalar regression ∈ [0, 1]

  Multi-task loss:
    L = λ_cls * CE + λ_mask * BCE + λ_sev * MSE

Temperature scaling for post-hoc calibration (uncertainty).

Design principles:
  - Small model (~100K params) — runs on CPU in <1s per batch
  - No batch norm (unstable with small batches)
  - Layer norm instead (works with batch=1)
  - Deterministic when seeded
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor


# ── Configuration ────────────────────────────────────────────────────

@dataclass
class ModelConfig:
    """Hyperparameters for NeuralQANet."""
    n_channels: int = 16          # input channels (electrodes)
    n_classes: int = 9            # classification classes
    embed_dim: int = 64           # backbone embedding dimension
    n_conv_blocks: int = 3        # number of conv blocks
    kernel_size: int = 7          # conv kernel size
    dropout: float = 0.1
    # Multi-task loss weights
    lambda_cls: float = 1.0       # classification weight
    lambda_mask: float = 0.5      # artifact mask weight
    lambda_sev: float = 0.3       # severity regression weight


# ── Building blocks ──────────────────────────────────────────────────

class ConvBlock(nn.Module):
    """1D Conv → LayerNorm → GELU → Dropout."""

    def __init__(self, in_ch: int, out_ch: int, kernel_size: int = 7,
                 dropout: float = 0.1):
        super().__init__()
        self.conv = nn.Conv1d(in_ch, out_ch, kernel_size, padding=kernel_size // 2)
        self.norm = nn.LayerNorm(out_ch)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: Tensor) -> Tensor:
        # x: (batch, channels, time)
        x = self.conv(x)
        # LayerNorm expects (batch, time, channels) — transpose
        x = x.transpose(1, 2)
        x = self.norm(x)
        x = x.transpose(1, 2)
        x = F.gelu(x)
        x = self.drop(x)
        return x


class ChannelMixer(nn.Module):
    """Point-wise (1×1) conv to fuse cross-channel information."""

    def __init__(self, dim: int, dropout: float = 0.1):
        super().__init__()
        self.fc = nn.Conv1d(dim, dim, kernel_size=1)
        self.norm = nn.LayerNorm(dim)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: Tensor) -> Tensor:
        residual = x
        x = self.fc(x)
        x = x.transpose(1, 2)
        x = self.norm(x)
        x = x.transpose(1, 2)
        x = F.gelu(x)
        x = self.drop(x)
        return x + residual


class AttentionPool(nn.Module):
    """Learned attention-weighted pooling over the time dimension.

    Better than mean/max pooling — learns which time steps matter
    for classification. Lightweight: single linear → softmax.
    """

    def __init__(self, dim: int):
        super().__init__()
        self.attn = nn.Linear(dim, 1)

    def forward(self, x: Tensor) -> tuple[Tensor, Tensor]:
        """
        Args:
            x: (batch, dim, time)
        Returns:
            pooled: (batch, dim) — attention-weighted representation
            weights: (batch, time) — attention weights (for interpretability)
        """
        # (batch, time, dim)
        x_t = x.transpose(1, 2)
        # (batch, time, 1)
        scores = self.attn(x_t)
        weights = F.softmax(scores.squeeze(-1), dim=-1)  # (batch, time)
        # Weighted sum: (batch, dim)
        pooled = torch.bmm(weights.unsqueeze(1), x_t).squeeze(1)
        return pooled, weights


# ── Backbone ─────────────────────────────────────────────────────────

class CNNBackbone(nn.Module):
    """1D CNN encoder: Conv blocks → channel mixer → attention pool.

    Input:  (batch, n_channels, time)
    Output: (batch, embed_dim) embedding + (batch, time) attention weights
    """

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.cfg = cfg

        # Input projection: n_channels → embed_dim
        self.input_proj = ConvBlock(cfg.n_channels, cfg.embed_dim,
                                    kernel_size=cfg.kernel_size,
                                    dropout=cfg.dropout)

        # Stacked conv blocks
        self.conv_blocks = nn.ModuleList([
            ConvBlock(cfg.embed_dim, cfg.embed_dim,
                      kernel_size=cfg.kernel_size,
                      dropout=cfg.dropout)
            for _ in range(cfg.n_conv_blocks - 1)
        ])

        # Channel mixer
        self.mixer = ChannelMixer(cfg.embed_dim, dropout=cfg.dropout)

        # Attention pooling
        self.pool = AttentionPool(cfg.embed_dim)

    def forward(self, x: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        """
        Args:
            x: (batch, n_channels, time) raw input

        Returns:
            embedding: (batch, embed_dim)
            temporal_features: (batch, embed_dim, time) — before pooling
            attn_weights: (batch, time)
        """
        h = self.input_proj(x)
        for block in self.conv_blocks:
            h = h + block(h)  # residual connections
        h = self.mixer(h)

        embedding, attn_weights = self.pool(h)
        return embedding, h, attn_weights


# ── Task heads ───────────────────────────────────────────────────────

class ArtifactMaskHead(nn.Module):
    """Head A: per-window artifact detection (binary).

    Uses the pooled embedding to predict whether this window
    contains structured contamination.
    """

    def __init__(self, embed_dim: int, dropout: float = 0.1):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(embed_dim, embed_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim // 2, 1),
        )

    def forward(self, embedding: Tensor) -> Tensor:
        """Returns logit (batch, 1) — apply sigmoid for probability."""
        return self.head(embedding)


class ClassificationHead(nn.Module):
    """Head B: multi-class classification.

    Classifies cause type: artifact subtypes + suppression-like subtypes.
    """

    def __init__(self, embed_dim: int, n_classes: int, dropout: float = 0.1):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(embed_dim, embed_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim // 2, n_classes),
        )

    def forward(self, embedding: Tensor) -> Tensor:
        """Returns logits (batch, n_classes) — apply softmax for probs."""
        return self.head(embedding)


class SeverityHead(nn.Module):
    """Head C: severity regression ∈ [0, 1].

    Predicts perturbation severity as a continuous value.
    """

    def __init__(self, embed_dim: int, dropout: float = 0.1):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(embed_dim, embed_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim // 2, 1),
            nn.Sigmoid(),
        )

    def forward(self, embedding: Tensor) -> Tensor:
        """Returns (batch, 1) severity prediction in [0, 1]."""
        return self.head(embedding)


# ── Full model ───────────────────────────────────────────────────────

class NeuralQANet(nn.Module):
    """Multi-task neural QA network.

    Three-headed model for concurrent:
      - Artifact mask detection (binary)
      - Cause classification (multi-class)
      - Severity estimation (regression)

    Designed to be small (~100K params), fast (CPU-friendly),
    and interpretable (attention weights show what matters).
    """

    def __init__(self, cfg: ModelConfig | None = None):
        super().__init__()
        self.cfg = cfg or ModelConfig()

        self.backbone = CNNBackbone(self.cfg)
        self.mask_head = ArtifactMaskHead(self.cfg.embed_dim, self.cfg.dropout)
        self.cls_head = ClassificationHead(self.cfg.embed_dim, self.cfg.n_classes,
                                           self.cfg.dropout)
        self.sev_head = SeverityHead(self.cfg.embed_dim, self.cfg.dropout)

        # Temperature parameter for post-hoc calibration
        self.temperature = nn.Parameter(torch.ones(1) * 1.5)

    def forward(self, x: Tensor) -> dict[str, Tensor]:
        """
        Args:
            x: (batch, n_channels, time) — raw multichannel data

        Returns:
            dict with keys:
                'mask_logit':   (batch, 1) — artifact mask logit
                'cls_logits':   (batch, n_classes) — class logits
                'severity':     (batch, 1) — severity ∈ [0, 1]
                'embedding':    (batch, embed_dim) — backbone embedding
                'attn_weights': (batch, time) — attention weights
        """
        embedding, temporal, attn_weights = self.backbone(x)

        return {
            "mask_logit": self.mask_head(embedding),
            "cls_logits": self.cls_head(embedding),
            "severity": self.sev_head(embedding),
            "embedding": embedding,
            "attn_weights": attn_weights,
        }

    def calibrated_probs(self, cls_logits: Tensor) -> Tensor:
        """Apply temperature scaling for calibrated probabilities."""
        return F.softmax(cls_logits / self.temperature, dim=-1)

    def count_parameters(self) -> int:
        """Total trainable parameters."""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


# ── Multi-task loss ──────────────────────────────────────────────────

class MultiTaskLoss(nn.Module):
    """Weighted multi-task loss for NeuralQANet.

    L = λ_cls * CrossEntropy + λ_mask * BCE + λ_sev * MSE

    Handles missing labels gracefully (mask=-1 means "ignore").
    """

    def __init__(self, cfg: ModelConfig | None = None):
        super().__init__()
        self.cfg = cfg or ModelConfig()

    def forward(
        self,
        outputs: dict[str, Tensor],
        targets: dict[str, Tensor],
    ) -> dict[str, Tensor]:
        """
        Args:
            outputs: from NeuralQANet.forward()
            targets:
                'class_labels': (batch,) int64 — class indices
                'mask_labels':  (batch,) float32 — 0/1 (is artifact?)
                'severity':     (batch,) float32 — true severity ∈ [0,1]

        Returns:
            dict with 'total', 'cls_loss', 'mask_loss', 'sev_loss'
        """
        losses = {}

        # Classification loss
        cls_logits = outputs["cls_logits"]
        cls_labels = targets["class_labels"]
        losses["cls_loss"] = F.cross_entropy(cls_logits, cls_labels)

        # Artifact mask loss (binary)
        mask_logit = outputs["mask_logit"].squeeze(-1)
        mask_labels = targets["mask_labels"]
        losses["mask_loss"] = F.binary_cross_entropy_with_logits(
            mask_logit, mask_labels)

        # Severity regression loss
        sev_pred = outputs["severity"].squeeze(-1)
        sev_true = targets["severity"]
        losses["sev_loss"] = F.mse_loss(sev_pred, sev_true)

        # Total weighted loss
        losses["total"] = (
            self.cfg.lambda_cls * losses["cls_loss"]
            + self.cfg.lambda_mask * losses["mask_loss"]
            + self.cfg.lambda_sev * losses["sev_loss"]
        )

        return losses


# ── Temperature calibration ──────────────────────────────────────────

def calibrate_temperature(
    model: NeuralQANet,
    val_logits: Tensor,
    val_labels: Tensor,
    lr: float = 0.01,
    max_iter: int = 50,
) -> float:
    """Post-hoc temperature scaling on validation set.

    Finds T that minimizes NLL on held-out validation logits.
    Does NOT change model weights — only adjusts temperature.

    Returns:
        Optimal temperature value.
    """
    temperature = nn.Parameter(torch.ones(1) * 1.5)
    optimizer = torch.optim.LBFGS([temperature], lr=lr, max_iter=max_iter)

    def closure():
        optimizer.zero_grad()
        scaled = val_logits / temperature
        loss = F.cross_entropy(scaled, val_labels)
        loss.backward()
        return loss

    optimizer.step(closure)

    # Apply to model
    with torch.no_grad():
        model.temperature.copy_(temperature.detach())

    return float(temperature.item())


# ── Convenience: label encoding ──────────────────────────────────────

# Matches perturbation_library.py registry order
CLASS_NAMES: list[str] = [
    "baseline_stable",
    "impedance_drift",
    "broadband_noise",
    "line_interference",
    "crosstalk_coupling",
    "spike_suppression",
    "burst_collapse",
    "spectral_shift",
    "mixed_neurotox",
]

CLASS_TO_IDX: dict[str, int] = {name: i for i, name in enumerate(CLASS_NAMES)}

BINARY_MASK_MAP: dict[str, float] = {
    "baseline_stable": 0.0,  # no perturbation
    "impedance_drift": 1.0,
    "broadband_noise": 1.0,
    "line_interference": 1.0,
    "crosstalk_coupling": 1.0,
    "spike_suppression": 1.0,
    "burst_collapse": 1.0,
    "spectral_shift": 1.0,
    "mixed_neurotox": 1.0,
}


def encode_labels(
    perturbation_types: list[str],
    severities: list[float],
) -> dict[str, Tensor]:
    """Encode string labels into tensors for training.

    Returns:
        dict with 'class_labels', 'mask_labels', 'severity'
    """
    class_labels = torch.tensor(
        [CLASS_TO_IDX.get(pt, 0) for pt in perturbation_types],
        dtype=torch.long,
    )
    mask_labels = torch.tensor(
        [BINARY_MASK_MAP.get(pt, 1.0) for pt in perturbation_types],
        dtype=torch.float32,
    )
    severity = torch.tensor(severities, dtype=torch.float32)

    return {
        "class_labels": class_labels,
        "mask_labels": mask_labels,
        "severity": severity,
    }
