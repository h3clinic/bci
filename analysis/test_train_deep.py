"""
Tests for train_deep.py — Fine-tuning and evaluation for NeuralQANet.

Covers:
  - PerturbationDataset __len__, __getitem__ shapes and types
  - collate_fn padding and stacking
  - TrainConfig / TrainResult defaults
  - train_model training loop returns TrainResult
  - evaluate_model returns EvalResult with all fields
  - quick_train_and_eval end-to-end pipeline
  - EvalResult per-class accuracy structure
"""

import numpy as np
import pytest
import torch

from train_deep import (
    PerturbationDataset,
    collate_fn,
    TrainConfig,
    TrainResult,
    EvalResult,
    train_model,
    evaluate_model,
    quick_train_and_eval,
)
from deep_model import NeuralQANet, ModelConfig, CLASS_NAMES, CLASS_TO_IDX
from perturbation_library import generate_dataset, LabeledWindow
from augment import AugmentPipeline


# ── Constants ─────────────────────────────────────────────────────────

SEED = 42
N_CH = 16
FS = 20_000.0
DUR = 2.0
ONSET = 0.8
MAX_SAMP = 2000


@pytest.fixture(scope="module")
def small_dataset():
    """Generate a tiny dataset once for all tests in this module."""
    return generate_dataset(
        n_per_class=2,
        n_channels=N_CH,
        fs=FS,
        duration_s=DUR,
        onset_s=ONSET,
        seed=SEED,
    )


@pytest.fixture
def split_dataset(small_dataset):
    """Split into train/val."""
    rng = np.random.default_rng(SEED)
    perm = rng.permutation(len(small_dataset))
    split = int(0.8 * len(small_dataset))
    train = [small_dataset[i] for i in perm[:split]]
    val = [small_dataset[i] for i in perm[split:]]
    return train, val


# ═══════════════════════════════════════════════════════════════════════
#  PerturbationDataset
# ═══════════════════════════════════════════════════════════════════════

class TestPerturbationDataset:
    def test_length(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        assert len(ds) == len(small_dataset)

    def test_getitem_keys(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        item = ds[0]
        expected_keys = {"x", "class_label", "mask_label", "severity",
                         "perturbation_type"}
        assert set(item.keys()) == expected_keys

    def test_getitem_x_shape(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        item = ds[0]
        x = item["x"]
        assert x.dim() == 2
        assert x.shape[0] == N_CH

    def test_getitem_x_dtype(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        item = ds[0]
        assert item["x"].dtype == torch.float32

    def test_class_label_range(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        for i in range(min(5, len(ds))):
            item = ds[i]
            assert 0 <= item["class_label"].item() < len(CLASS_NAMES)

    def test_mask_label_binary(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        for i in range(min(5, len(ds))):
            item = ds[i]
            assert item["mask_label"].item() in (0.0, 1.0)

    def test_severity_in_range(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        for i in range(min(5, len(ds))):
            item = ds[i]
            assert 0.0 <= item["severity"].item() <= 1.0

    def test_truncation(self, small_dataset):
        ds = PerturbationDataset(small_dataset, max_samples=500)
        item = ds[0]
        assert item["x"].shape[1] <= 500

    def test_augmentation(self, small_dataset):
        """With augmentation, data should differ from raw."""
        pipe = AugmentPipeline(p_each=1.0, seed=SEED)
        ds_plain = PerturbationDataset(small_dataset)
        ds_aug = PerturbationDataset(small_dataset, augment_pipeline=pipe)
        x_plain = ds_plain[0]["x"]
        x_aug = ds_aug[0]["x"]
        assert not torch.allclose(x_plain, x_aug)


# ═══════════════════════════════════════════════════════════════════════
#  collate_fn
# ═══════════════════════════════════════════════════════════════════════

class TestCollateFn:
    def test_stacks_to_batch(self, small_dataset):
        ds = PerturbationDataset(small_dataset, max_samples=MAX_SAMP)
        batch_items = [ds[i] for i in range(min(4, len(ds)))]
        batch = collate_fn(batch_items)
        assert batch["x"].dim() == 3  # (B, C, T)
        assert batch["class_labels"].dim() == 1
        assert batch["mask_labels"].dim() == 1
        assert batch["severity"].dim() == 1

    def test_pads_to_max_length(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        # Items might differ in length — collate should pad
        batch_items = [ds[i] for i in range(min(3, len(ds)))]
        batch = collate_fn(batch_items)
        # All should have same time dim
        assert batch["x"].shape[0] == len(batch_items)

    def test_perturbation_types_list(self, small_dataset):
        ds = PerturbationDataset(small_dataset)
        batch_items = [ds[i] for i in range(min(3, len(ds)))]
        batch = collate_fn(batch_items)
        assert isinstance(batch["perturbation_types"], list)
        assert len(batch["perturbation_types"]) == len(batch_items)


# ═══════════════════════════════════════════════════════════════════════
#  TrainConfig / TrainResult
# ═══════════════════════════════════════════════════════════════════════

class TestConfigs:
    def test_train_config_defaults(self):
        cfg = TrainConfig()
        assert cfg.n_epochs == 30
        assert cfg.batch_size == 16
        assert cfg.lr == 1e-3
        assert cfg.weight_decay == 1e-4
        assert cfg.augment is True

    def test_train_result_empty(self):
        result = TrainResult()
        assert result.train_losses == []
        assert result.val_losses == []
        assert result.best_val_acc == 0.0


# ═══════════════════════════════════════════════════════════════════════
#  train_model
# ═══════════════════════════════════════════════════════════════════════

class TestTrainModel:
    def test_returns_train_result(self, split_dataset):
        train_wins, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        train_cfg = TrainConfig(
            n_epochs=3,
            batch_size=min(8, len(train_wins)),
            max_samples=MAX_SAMP,
            seed=SEED,
        )
        result = train_model(model, train_wins, val_wins, train_cfg, verbose=False)
        assert isinstance(result, TrainResult)
        assert len(result.train_losses) == 3
        assert len(result.val_losses) == 3
        assert len(result.val_accuracy) == 3

    def test_n_params_recorded(self, split_dataset):
        train_wins, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        train_cfg = TrainConfig(n_epochs=2, batch_size=8,
                                max_samples=MAX_SAMP, seed=SEED)
        result = train_model(model, train_wins, val_wins, train_cfg, verbose=False)
        assert result.n_params == model.count_parameters()

    def test_losses_finite(self, split_dataset):
        train_wins, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        train_cfg = TrainConfig(n_epochs=3, batch_size=8,
                                max_samples=MAX_SAMP, seed=SEED)
        result = train_model(model, train_wins, val_wins, train_cfg, verbose=False)
        for loss in result.train_losses + result.val_losses:
            assert np.isfinite(loss)

    def test_temperature_recorded(self, split_dataset):
        train_wins, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        train_cfg = TrainConfig(n_epochs=3, batch_size=8,
                                max_samples=MAX_SAMP, seed=SEED)
        result = train_model(model, train_wins, val_wins, train_cfg, verbose=False)
        assert isinstance(result.temperature, float)
        assert result.temperature > 0


# ═══════════════════════════════════════════════════════════════════════
#  evaluate_model
# ═══════════════════════════════════════════════════════════════════════

class TestEvaluateModel:
    def test_returns_eval_result(self, split_dataset):
        train_wins, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        assert isinstance(result, EvalResult)

    def test_accuracy_in_range(self, split_dataset):
        _, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        assert 0.0 <= result.accuracy <= 1.0

    def test_binary_accuracy_in_range(self, split_dataset):
        _, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        assert 0.0 <= result.binary_accuracy <= 1.0

    def test_predictions_length(self, split_dataset):
        _, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        assert len(result.predictions) == len(val_wins)
        assert len(result.true_labels) == len(val_wins)

    def test_probabilities_length(self, split_dataset):
        _, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        assert len(result.probabilities) == len(val_wins)
        # Each prob vector should have 9 classes
        for prob in result.probabilities:
            assert len(prob) == len(CLASS_NAMES)

    def test_model_name(self, split_dataset):
        _, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        assert result.model_name == "NeuralQANet"

    def test_per_class_accuracy(self, split_dataset):
        _, val_wins = split_dataset
        cfg = ModelConfig(n_channels=N_CH)
        model = NeuralQANet(cfg)
        result = evaluate_model(model, val_wins, max_samples=MAX_SAMP)
        for cls_name, acc in result.per_class_accuracy.items():
            assert 0.0 <= acc <= 1.0


# ═══════════════════════════════════════════════════════════════════════
#  quick_train_and_eval (end-to-end)
# ═══════════════════════════════════════════════════════════════════════

class TestQuickTrainAndEval:
    def test_returns_tuple(self):
        model, train_res, eval_res = quick_train_and_eval(
            n_per_class=2, duration_s=DUR, onset_s=ONSET,
            max_samples=MAX_SAMP, n_epochs=3, seed=SEED, verbose=False,
        )
        assert isinstance(model, NeuralQANet)
        assert isinstance(train_res, TrainResult)
        assert isinstance(eval_res, EvalResult)

    def test_model_trained(self):
        model, train_res, _ = quick_train_and_eval(
            n_per_class=2, duration_s=DUR, onset_s=ONSET,
            max_samples=MAX_SAMP, n_epochs=3, seed=SEED, verbose=False,
        )
        assert len(train_res.train_losses) == 3
        assert train_res.n_params > 0

    def test_eval_has_predictions(self):
        _, _, eval_res = quick_train_and_eval(
            n_per_class=2, duration_s=DUR, onset_s=ONSET,
            max_samples=MAX_SAMP, n_epochs=3, seed=SEED, verbose=False,
        )
        assert len(eval_res.predictions) > 0
        assert len(eval_res.probabilities) > 0
