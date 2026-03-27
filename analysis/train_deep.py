"""
train_deep.py — Fine-tuning and training for NeuralQANet.

Handles:
  1. Dataset preparation from perturbation_library windows
  2. Multi-task training loop (denoise + classify + severity)
  3. Temperature calibration on validation set
  4. Model evaluation and comparison with classical baselines
  5. Checkpoint save/load

Usage:
  # Train from scratch:
  python train_deep.py --epochs 30 --out checkpoints/model.pt

  # Fine-tune from SSL checkpoint:
  python train_deep.py --ssl-ckpt checkpoints/ssl.pt --epochs 20 --out model.pt

  # Evaluate only:
  python train_deep.py --eval --ckpt checkpoints/model.pt
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch import Tensor
from torch.utils.data import Dataset, DataLoader

from deep_model import (
    NeuralQANet,
    ModelConfig,
    MultiTaskLoss,
    calibrate_temperature,
    encode_labels,
    CLASS_NAMES,
    CLASS_TO_IDX,
)
from perturbation_library import (
    generate_dataset,
    LabeledWindow,
    ARTIFACT_TYPES,
    NEUROTOX_TYPES,
)
from augment import AugmentPipeline


# ── Dataset ──────────────────────────────────────────────────────────

class PerturbationDataset(Dataset):
    """PyTorch dataset wrapping LabeledWindows.

    Each item is a dict with:
        'x': (n_channels, n_samples) float32 tensor
        'class_label': int
        'mask_label': float (0=clean, 1=contaminated)
        'severity': float
        'perturbation_type': str
    """

    def __init__(
        self,
        windows: list[LabeledWindow],
        augment_pipeline: AugmentPipeline | None = None,
        fs: float = 20_000.0,
        max_samples: int | None = None,
    ):
        self.windows = windows
        self.augment = augment_pipeline
        self.fs = fs
        self.max_samples = max_samples

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, idx: int) -> dict[str, Tensor | str]:
        w = self.windows[idx]
        data = w.data_uv

        # Truncate if needed (for fast training)
        if self.max_samples is not None and data.shape[1] > self.max_samples:
            data = data[:, :self.max_samples]

        # Apply augmentation
        if self.augment is not None:
            data = self.augment(data, self.fs, seed_offset=idx)

        x = torch.tensor(data, dtype=torch.float32)

        cls_idx = CLASS_TO_IDX.get(w.meta.perturbation_type, 0)
        mask_label = 0.0 if w.meta.category == "baseline" else 1.0

        return {
            "x": x,
            "class_label": torch.tensor(cls_idx, dtype=torch.long),
            "mask_label": torch.tensor(mask_label, dtype=torch.float32),
            "severity": torch.tensor(w.meta.severity, dtype=torch.float32),
            "perturbation_type": w.meta.perturbation_type,
        }


def collate_fn(batch: list[dict]) -> dict[str, Tensor | list[str]]:
    """Custom collate: pad to max length in batch, stack tensors."""
    max_len = max(item["x"].shape[1] for item in batch)

    xs = []
    for item in batch:
        x = item["x"]
        if x.shape[1] < max_len:
            pad = torch.zeros(x.shape[0], max_len - x.shape[1])
            x = torch.cat([x, pad], dim=1)
        xs.append(x)

    return {
        "x": torch.stack(xs),
        "class_labels": torch.stack([item["class_label"] for item in batch]),
        "mask_labels": torch.stack([item["mask_label"] for item in batch]),
        "severity": torch.stack([item["severity"] for item in batch]),
        "perturbation_types": [item["perturbation_type"] for item in batch],
    }


# ── Training ─────────────────────────────────────────────────────────

@dataclass
class TrainConfig:
    """Training hyperparameters."""
    n_epochs: int = 30
    batch_size: int = 16
    lr: float = 1e-3
    weight_decay: float = 1e-4
    max_samples: int | None = None  # truncate windows for speed
    augment: bool = True
    seed: int = 42


@dataclass
class TrainResult:
    """Training result summary."""
    train_losses: list[float] = field(default_factory=list)
    val_losses: list[float] = field(default_factory=list)
    val_accuracy: list[float] = field(default_factory=list)
    best_val_acc: float = 0.0
    best_epoch: int = 0
    temperature: float = 1.5
    n_params: int = 0


def train_model(
    model: NeuralQANet,
    train_windows: list[LabeledWindow],
    val_windows: list[LabeledWindow],
    cfg: TrainConfig | None = None,
    verbose: bool = True,
) -> TrainResult:
    """Train NeuralQANet with multi-task loss.

    Args:
        model: NeuralQANet instance
        train_windows: training data
        val_windows: validation data
        cfg: training config
        verbose: print progress

    Returns:
        TrainResult with loss history and best metrics.
    """
    cfg = cfg or TrainConfig()
    torch.manual_seed(cfg.seed)

    # Build datasets
    aug_pipe = AugmentPipeline(p_each=0.3, seed=cfg.seed) if cfg.augment else None
    train_ds = PerturbationDataset(train_windows, augment_pipeline=aug_pipe,
                                   max_samples=cfg.max_samples)
    val_ds = PerturbationDataset(val_windows, max_samples=cfg.max_samples)

    train_loader = DataLoader(train_ds, batch_size=cfg.batch_size,
                              shuffle=True, collate_fn=collate_fn)
    val_loader = DataLoader(val_ds, batch_size=cfg.batch_size,
                            shuffle=False, collate_fn=collate_fn)

    criterion = MultiTaskLoss(model.cfg)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr,
                                  weight_decay=cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.n_epochs, eta_min=cfg.lr / 10)

    result = TrainResult(n_params=model.count_parameters())
    best_state = None

    for epoch in range(cfg.n_epochs):
        # ── Train ────────────────────────────────────────────────
        model.train()
        epoch_loss = 0.0
        n_train = 0

        for batch in train_loader:
            x = batch["x"]
            targets = {
                "class_labels": batch["class_labels"],
                "mask_labels": batch["mask_labels"],
                "severity": batch["severity"],
            }

            optimizer.zero_grad()
            outputs = model(x)
            losses = criterion(outputs, targets)
            losses["total"].backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            epoch_loss += losses["total"].item() * x.shape[0]
            n_train += x.shape[0]

        scheduler.step()
        avg_train_loss = epoch_loss / max(n_train, 1)
        result.train_losses.append(avg_train_loss)

        # ── Validate ─────────────────────────────────────────────
        model.eval()
        val_loss = 0.0
        correct = 0
        total = 0

        with torch.no_grad():
            for batch in val_loader:
                x = batch["x"]
                targets = {
                    "class_labels": batch["class_labels"],
                    "mask_labels": batch["mask_labels"],
                    "severity": batch["severity"],
                }

                outputs = model(x)
                losses = criterion(outputs, targets)
                val_loss += losses["total"].item() * x.shape[0]

                preds = outputs["cls_logits"].argmax(dim=1)
                correct += (preds == targets["class_labels"]).sum().item()
                total += x.shape[0]

        avg_val_loss = val_loss / max(total, 1)
        val_acc = correct / max(total, 1)
        result.val_losses.append(avg_val_loss)
        result.val_accuracy.append(val_acc)

        if val_acc > result.best_val_acc:
            result.best_val_acc = val_acc
            result.best_epoch = epoch
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if verbose and (epoch + 1) % max(1, cfg.n_epochs // 10) == 0:
            print(f"  Epoch {epoch+1:3d}/{cfg.n_epochs}: "
                  f"train_loss={avg_train_loss:.4f}  "
                  f"val_loss={avg_val_loss:.4f}  "
                  f"val_acc={val_acc:.3f}")

    # Restore best model
    if best_state is not None:
        model.load_state_dict(best_state)

    # Temperature calibration on val set
    model.eval()
    all_logits = []
    all_labels = []
    with torch.no_grad():
        for batch in val_loader:
            outputs = model(batch["x"])
            all_logits.append(outputs["cls_logits"])
            all_labels.append(batch["class_labels"])

    if all_logits:
        val_logits = torch.cat(all_logits)
        val_labels = torch.cat(all_labels)
        result.temperature = calibrate_temperature(model, val_logits, val_labels)

    return result


# ── Evaluation ───────────────────────────────────────────────────────

@dataclass
class EvalResult:
    """Evaluation metrics for model comparison."""
    model_name: str
    accuracy: float
    per_class_accuracy: dict[str, float] = field(default_factory=dict)
    binary_accuracy: float = 0.0  # artifact vs suppression-like
    predictions: list[int] = field(default_factory=list)
    true_labels: list[int] = field(default_factory=list)
    probabilities: list[list[float]] = field(default_factory=list)


def evaluate_model(
    model: NeuralQANet,
    windows: list[LabeledWindow],
    max_samples: int | None = None,
    batch_size: int = 32,
) -> EvalResult:
    """Evaluate trained model on a set of windows.

    Returns:
        EvalResult with accuracy metrics and predictions.
    """
    ds = PerturbationDataset(windows, max_samples=max_samples)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False,
                        collate_fn=collate_fn)

    model.eval()
    all_preds = []
    all_labels = []
    all_probs = []

    with torch.no_grad():
        for batch in loader:
            outputs = model(batch["x"])
            probs = model.calibrated_probs(outputs["cls_logits"])
            preds = outputs["cls_logits"].argmax(dim=1)

            all_preds.extend(preds.tolist())
            all_labels.extend(batch["class_labels"].tolist())
            all_probs.extend(probs.tolist())

    # Overall accuracy
    correct = sum(p == l for p, l in zip(all_preds, all_labels))
    accuracy = correct / max(len(all_preds), 1)

    # Per-class accuracy
    per_class: dict[str, float] = {}
    for cls_name in CLASS_NAMES:
        cls_idx = CLASS_TO_IDX[cls_name]
        mask = [l == cls_idx for l in all_labels]
        if any(mask):
            cls_correct = sum(p == l for p, l, m in zip(all_preds, all_labels, mask) if m)
            cls_total = sum(mask)
            per_class[cls_name] = cls_correct / cls_total

    # Binary: artifact vs suppression-like
    artifact_idx = {CLASS_TO_IDX[a] for a in ARTIFACT_TYPES}
    neurotox_idx = {CLASS_TO_IDX[n] for n in NEUROTOX_TYPES}

    binary_correct = 0
    binary_total = 0
    for p, l in zip(all_preds, all_labels):
        if l in artifact_idx or l in neurotox_idx:
            pred_is_neurotox = p in neurotox_idx
            true_is_neurotox = l in neurotox_idx
            if pred_is_neurotox == true_is_neurotox:
                binary_correct += 1
            binary_total += 1

    return EvalResult(
        model_name="NeuralQANet",
        accuracy=accuracy,
        per_class_accuracy=per_class,
        binary_accuracy=binary_correct / max(binary_total, 1),
        predictions=all_preds,
        true_labels=all_labels,
        probabilities=all_probs,
    )


# ── Quick end-to-end pipeline ────────────────────────────────────────

def quick_train_and_eval(
    dataset: list[LabeledWindow] | None = None,
    n_per_class: int = 3,
    n_channels: int = 16,
    fs: float = 20_000.0,
    duration_s: float = 5.0,
    onset_s: float = 2.0,
    max_samples: int = 4000,
    n_epochs: int = 10,
    seed: int = 42,
    verbose: bool = True,
) -> tuple[NeuralQANet, TrainResult, EvalResult]:
    """End-to-end: train → evaluate.

    Args:
        dataset: Pre-generated LabeledWindows. If None, generates
                 synthetic data from the perturbation library (legacy
                 behavior for demos/tests). When running pipelines on
                 real or custom data, always pass dataset explicitly.
        n_per_class: Samples per class (only used when dataset is None).
        n_channels: Channels (only used when dataset is None).
        fs: Sample rate (only used when dataset is None).
        duration_s: Trial duration (only used when dataset is None).
        onset_s: Perturbation onset (only used when dataset is None).
        max_samples: Truncation length per window.
        n_epochs: Training epochs.
        seed: Random seed.
        verbose: Print progress.

    Returns:
        (model, train_result, eval_result)
    """
    if dataset is None:
        if verbose:
            print("Generating synthetic dataset...")
        dataset = generate_dataset(
            n_per_class=n_per_class,
            n_channels=n_channels,
            fs=fs,
            duration_s=duration_s,
            onset_s=onset_s,
            seed=seed,
        )
    else:
        if verbose:
            print(f"Using provided dataset ({len(dataset)} windows)")
        # Infer n_channels from data
        n_channels = dataset[0].data_uv.shape[0] if dataset else n_channels

    # Split 80/20
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(dataset))
    split = int(0.8 * len(dataset))
    train_wins = [dataset[i] for i in perm[:split]]
    val_wins = [dataset[i] for i in perm[split:]]

    if verbose:
        print(f"Train: {len(train_wins)}, Val: {len(val_wins)}")

    cfg = ModelConfig(n_channels=n_channels)
    model = NeuralQANet(cfg)

    if verbose:
        print(f"Model parameters: {model.count_parameters():,}")

    train_cfg = TrainConfig(
        n_epochs=n_epochs,
        batch_size=min(16, len(train_wins)),
        max_samples=max_samples,
        seed=seed,
    )

    if verbose:
        print("Training...")
    result = train_model(model, train_wins, val_wins, train_cfg, verbose=verbose)

    if verbose:
        print("Evaluating...")
    eval_result = evaluate_model(model, val_wins, max_samples=max_samples)

    if verbose:
        print(f"\nResults:")
        print(f"  Best val accuracy: {result.best_val_acc:.3f} (epoch {result.best_epoch+1})")
        print(f"  Final eval accuracy: {eval_result.accuracy:.3f}")
        print(f"  Binary accuracy: {eval_result.binary_accuracy:.3f}")
        print(f"  Temperature: {result.temperature:.3f}")

    return model, result, eval_result


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Train and evaluate NeuralQANet multi-task model.",
    )
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--n-per-class", type=int, default=3)
    ap.add_argument("--duration", type=float, default=5.0)
    ap.add_argument("--onset", type=float, default=2.0)
    ap.add_argument("--max-samples", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    model, result, eval_result = quick_train_and_eval(
        n_per_class=args.n_per_class,
        duration_s=args.duration,
        onset_s=args.onset,
        max_samples=args.max_samples,
        n_epochs=args.epochs,
        seed=args.seed,
        verbose=not args.quiet,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model_state": model.state_dict(),
            "config": model.cfg,
            "train_result": {
                "best_val_acc": result.best_val_acc,
                "best_epoch": result.best_epoch,
                "temperature": result.temperature,
                "n_params": result.n_params,
            },
        }, out_path)
        print(f"✓ Saved model to {out_path}")


if __name__ == "__main__":
    main()
