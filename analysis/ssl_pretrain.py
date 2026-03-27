"""
ssl_pretrain.py — Self-supervised pretraining for neural time-series backbone.

Two pretext tasks (this is the pro move for accuracy without real data):

  1. Masked Reconstruction (MAE-style)
     - Mask random time chunks → reconstruct them from context
     - Teaches temporal structure of neural signals

  2. Contrastive Learning (SimCLR-style)
     - Two augmented views of same window → pull embeddings together
     - Two different windows → push embeddings apart
     - Uses augment.py for domain-randomized views

After pretraining, freeze or fine-tune backbone for downstream tasks.
The backbone learns transferable representations from unlabeled data,
so when real phantom data arrives, you need fewer labeled examples.

Usage:
    python ssl_pretrain.py --epochs 50 --batch-size 32 --out checkpoints/ssl.pt
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from deep_model import CNNBackbone, ModelConfig


# ── Configuration ────────────────────────────────────────────────────

@dataclass
class SSLConfig:
    """Hyperparameters for self-supervised pretraining."""
    model_cfg: ModelConfig | None = None
    # Masked reconstruction
    mask_ratio: float = 0.3       # fraction of time steps to mask
    mask_patch_size: int = 50     # samples per mask patch
    # Contrastive learning
    proj_dim: int = 32            # projection head output dimension
    temperature: float = 0.1     # NT-Xent temperature
    # Training
    lr: float = 1e-3
    weight_decay: float = 1e-4
    # Loss weighting
    lambda_recon: float = 1.0
    lambda_contrast: float = 0.5


# ── Masked Reconstruction Head ───────────────────────────────────────

class MaskedReconHead(nn.Module):
    """Decoder for masked time-series reconstruction.

    Takes temporal features and predicts the masked portions.
    Lightweight: single transpose-conv to upsample back to input space.
    """

    def __init__(self, embed_dim: int, n_channels: int):
        super().__init__()
        self.decoder = nn.Sequential(
            nn.Conv1d(embed_dim, embed_dim // 2, kernel_size=3, padding=1),
            nn.GELU(),
            nn.Conv1d(embed_dim // 2, n_channels, kernel_size=3, padding=1),
        )

    def forward(self, temporal_features: Tensor) -> Tensor:
        """
        Args:
            temporal_features: (batch, embed_dim, time)
        Returns:
            reconstruction: (batch, n_channels, time)
        """
        return self.decoder(temporal_features)


# ── Contrastive Projection Head ──────────────────────────────────────

class ProjectionHead(nn.Module):
    """MLP projection head for contrastive learning (SimCLR-style).

    Maps backbone embeddings to a lower-dimensional space where
    contrastive loss is computed. Discarded after pretraining.
    """

    def __init__(self, embed_dim: int, proj_dim: int = 32):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(embed_dim, embed_dim),
            nn.GELU(),
            nn.Linear(embed_dim, proj_dim),
        )

    def forward(self, embedding: Tensor) -> Tensor:
        """
        Args:
            embedding: (batch, embed_dim)
        Returns:
            projection: (batch, proj_dim) — L2-normalized
        """
        z = self.head(embedding)
        return F.normalize(z, dim=-1)


# ── NT-Xent Contrastive Loss ─────────────────────────────────────────

def nt_xent_loss(
    z1: Tensor,
    z2: Tensor,
    temperature: float = 0.1,
) -> Tensor:
    """Normalized Temperature-scaled Cross-Entropy loss (SimCLR).

    z1[i] and z2[i] are positive pairs (same window, different augmentations).
    All other pairs are negatives.

    Args:
        z1: (batch, proj_dim) — projections from view 1
        z2: (batch, proj_dim) — projections from view 2
        temperature: scaling factor (lower = harder)

    Returns:
        Scalar loss.
    """
    batch_size = z1.shape[0]
    z = torch.cat([z1, z2], dim=0)  # (2B, D)

    # Cosine similarity matrix
    sim = torch.mm(z, z.t()) / temperature  # (2B, 2B)

    # Mask out self-similarity
    mask = torch.eye(2 * batch_size, device=z.device, dtype=torch.bool)
    sim.masked_fill_(mask, -1e9)

    # Positive pairs: (i, i+B) and (i+B, i)
    labels = torch.cat([
        torch.arange(batch_size, 2 * batch_size),
        torch.arange(0, batch_size),
    ]).to(z.device)

    return F.cross_entropy(sim, labels)


# ── Masking utility ──────────────────────────────────────────────────

def generate_mask(
    n_samples: int,
    mask_ratio: float = 0.3,
    patch_size: int = 50,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Generate a binary mask for time-series masking.

    Returns:
        mask: (n_samples,) boolean — True = visible, False = masked
    """
    if rng is None:
        rng = np.random.default_rng()

    n_patches = n_samples // patch_size
    n_mask = max(1, int(n_patches * mask_ratio))

    mask = np.ones(n_samples, dtype=bool)
    patch_indices = rng.choice(n_patches, size=n_mask, replace=False)
    for idx in patch_indices:
        start = idx * patch_size
        end = min(start + patch_size, n_samples)
        mask[start:end] = False

    return mask


# ── SSL Pretraining Model ────────────────────────────────────────────

class SSLModel(nn.Module):
    """Self-supervised pretraining wrapper around CNN backbone.

    Combines:
      - Masked reconstruction (predict masked time chunks)
      - Contrastive learning (pull augmented views together)
    """

    def __init__(self, cfg: SSLConfig | None = None):
        super().__init__()
        self.cfg = cfg or SSLConfig()
        model_cfg = self.cfg.model_cfg or ModelConfig()

        self.backbone = CNNBackbone(model_cfg)
        self.recon_head = MaskedReconHead(model_cfg.embed_dim, model_cfg.n_channels)
        self.proj_head = ProjectionHead(model_cfg.embed_dim, self.cfg.proj_dim)

    def forward_masked(
        self,
        x: Tensor,
        mask: Tensor,
    ) -> tuple[Tensor, Tensor, Tensor]:
        """Forward pass for masked reconstruction.

        Args:
            x: (batch, n_channels, time) — original input
            mask: (batch, time) — True=visible, False=masked

        Returns:
            recon: (batch, n_channels, time) — reconstruction
            embedding: (batch, embed_dim)
            temporal: (batch, embed_dim, time)
        """
        # Apply mask to input: zero out masked regions
        x_masked = x * mask.unsqueeze(1).float()

        embedding, temporal, _ = self.backbone(x_masked)
        recon = self.recon_head(temporal)

        return recon, embedding, temporal

    def forward_contrastive(
        self,
        x1: Tensor,
        x2: Tensor,
    ) -> tuple[Tensor, Tensor]:
        """Forward pass for contrastive learning.

        Args:
            x1: (batch, n_channels, time) — augmented view 1
            x2: (batch, n_channels, time) — augmented view 2

        Returns:
            z1, z2: (batch, proj_dim) — projected embeddings
        """
        emb1, _, _ = self.backbone(x1)
        emb2, _, _ = self.backbone(x2)

        z1 = self.proj_head(emb1)
        z2 = self.proj_head(emb2)

        return z1, z2

    def compute_loss(
        self,
        x: Tensor,
        x_aug1: Tensor,
        x_aug2: Tensor,
        mask: Tensor,
    ) -> dict[str, Tensor]:
        """Compute combined SSL loss.

        Args:
            x: (batch, n_channels, time) — clean input
            x_aug1, x_aug2: augmented views for contrastive
            mask: (batch, time) — for masked reconstruction

        Returns:
            dict with 'total', 'recon_loss', 'contrast_loss'
        """
        # Masked reconstruction
        recon, _, _ = self.forward_masked(x, mask)
        # Loss only on masked regions
        inv_mask = (~mask.bool()).unsqueeze(1).float()  # (batch, 1, time)
        n_masked = inv_mask.sum().clamp(min=1.0)
        recon_loss = ((recon - x) ** 2 * inv_mask).sum() / (n_masked * x.shape[1])

        # Contrastive
        z1, z2 = self.forward_contrastive(x_aug1, x_aug2)
        contrast_loss = nt_xent_loss(z1, z2, self.cfg.temperature)

        total = (self.cfg.lambda_recon * recon_loss
                 + self.cfg.lambda_contrast * contrast_loss)

        return {
            "total": total,
            "recon_loss": recon_loss,
            "contrast_loss": contrast_loss,
        }

    def get_backbone(self) -> CNNBackbone:
        """Extract the pretrained backbone for downstream fine-tuning."""
        return self.backbone


# ── Training loop ────────────────────────────────────────────────────

def pretrain_ssl(
    model: SSLModel,
    windows: list[np.ndarray],
    fs: float,
    n_epochs: int = 50,
    batch_size: int = 16,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    seed: int = 42,
    verbose: bool = True,
) -> dict[str, list[float]]:
    """Run SSL pretraining loop.

    Args:
        model: SSLModel instance
        windows: list of (n_channels, n_samples) numpy arrays
        fs: sample rate
        n_epochs: training epochs
        batch_size: batch size
        lr: learning rate
        weight_decay: L2 regularization
        seed: for reproducibility
        verbose: print progress

    Returns:
        History dict with 'total_loss', 'recon_loss', 'contrast_loss' per epoch.
    """
    from augment import AugmentPipeline

    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr,
                                  weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=n_epochs, eta_min=lr / 10)

    pipe1 = AugmentPipeline(p_each=0.5, seed=seed)
    pipe2 = AugmentPipeline(p_each=0.5, seed=seed + 1000)

    history: dict[str, list[float]] = {
        "total_loss": [], "recon_loss": [], "contrast_loss": [],
    }

    model.train()
    n = len(windows)

    for epoch in range(n_epochs):
        perm = rng.permutation(n)
        epoch_losses = {"total": 0.0, "recon": 0.0, "contrast": 0.0}
        n_batches = 0

        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            indices = perm[start:end]
            bs = len(indices)

            # Build batch
            batch_clean = []
            batch_aug1 = []
            batch_aug2 = []
            batch_masks = []

            for idx in indices:
                w = windows[idx]
                n_samp = w.shape[1]

                # Augmented views
                aug1 = pipe1(w, fs, seed_offset=epoch * n + int(idx))
                aug2 = pipe2(w, fs, seed_offset=epoch * n + int(idx) + 500)

                # Mask
                mask = generate_mask(
                    n_samp,
                    mask_ratio=model.cfg.mask_ratio,
                    patch_size=model.cfg.mask_patch_size,
                    rng=rng,
                )

                batch_clean.append(w)
                batch_aug1.append(aug1)
                batch_aug2.append(aug2)
                batch_masks.append(mask)

            x = torch.tensor(np.stack(batch_clean), dtype=torch.float32)
            x1 = torch.tensor(np.stack(batch_aug1), dtype=torch.float32)
            x2 = torch.tensor(np.stack(batch_aug2), dtype=torch.float32)
            m = torch.tensor(np.stack(batch_masks), dtype=torch.bool)

            optimizer.zero_grad()
            losses = model.compute_loss(x, x1, x2, m)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            epoch_losses["total"] += losses["total"].item()
            epoch_losses["recon"] += losses["recon_loss"].item()
            epoch_losses["contrast"] += losses["contrast_loss"].item()
            n_batches += 1

        scheduler.step()

        avg_total = epoch_losses["total"] / max(n_batches, 1)
        avg_recon = epoch_losses["recon"] / max(n_batches, 1)
        avg_contrast = epoch_losses["contrast"] / max(n_batches, 1)

        history["total_loss"].append(avg_total)
        history["recon_loss"].append(avg_recon)
        history["contrast_loss"].append(avg_contrast)

        if verbose and (epoch + 1) % max(1, n_epochs // 10) == 0:
            print(f"  Epoch {epoch+1:3d}/{n_epochs}: "
                  f"total={avg_total:.4f}  recon={avg_recon:.4f}  "
                  f"contrast={avg_contrast:.4f}")

    return history
