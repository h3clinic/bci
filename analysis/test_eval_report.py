"""
Tests for eval_report.py — v2 evaluation figures.

Covers:
  - Each make_fig* function runs without error
  - Each returns a matplotlib Figure
  - make_eval_report returns dict with all 4 figures
  - Proxy labels are used (no bare "neurotox")
"""

import pytest
import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend for CI
import matplotlib.pyplot as plt

from perturbation_library import generate_dataset
from eval_report import (
    make_fig1_onset_detection,
    make_fig2_cause_classification,
    make_fig3_robustness,
    make_fig4_ablation,
    make_fig5_model_comparison,
    compute_model_comparison,
    plot_model_comparison,
    ModelComparisonResult,
    make_eval_report,
    DISPLAY_LABELS,
    BINARY_LABELS,
)


# ── Shared fixtures ──────────────────────────────────────────────────

FS = 20_000.0
N_CH = 4
DUR = 5.0
ONSET = 2.0
SEED = 42


@pytest.fixture(scope="module")
def small_dataset():
    """Minimal dataset for figure generation tests."""
    return generate_dataset(
        n_per_class=2, severities=[0.3, 0.7],
        n_channels=N_CH, fs=FS,
        duration_s=DUR, onset_s=ONSET, seed=SEED,
    )


# ═══════════════════════════════════════════════════════════════════
#  Individual figure functions
# ═══════════════════════════════════════════════════════════════════

class TestFig1:
    def test_runs_without_error(self, small_dataset):
        fig = make_fig1_onset_detection(
            small_dataset, onset_s=ONSET, fs=FS,
        )
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_returns_figure(self, small_dataset):
        fig = make_fig1_onset_detection(
            small_dataset, onset_s=ONSET, fs=FS,
        )
        assert fig is not None
        plt.close(fig)


class TestFig2:
    def test_runs_without_error(self, small_dataset):
        fig = make_fig2_cause_classification(small_dataset, seed=SEED)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_returns_figure(self, small_dataset):
        fig = make_fig2_cause_classification(small_dataset, seed=SEED)
        assert fig is not None
        plt.close(fig)


class TestFig3:
    def test_runs_without_error(self, small_dataset):
        fig = make_fig3_robustness(
            small_dataset, fs=FS, onset_s=ONSET, seed=SEED,
        )
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_returns_figure(self, small_dataset):
        fig = make_fig3_robustness(
            small_dataset, fs=FS, onset_s=ONSET, seed=SEED,
        )
        assert fig is not None
        plt.close(fig)


class TestFig4:
    def test_runs_without_error(self, small_dataset):
        fig = make_fig4_ablation(
            small_dataset, onset_s=ONSET, fs=FS, seed=SEED,
        )
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_returns_figure(self, small_dataset):
        fig = make_fig4_ablation(
            small_dataset, onset_s=ONSET, fs=FS, seed=SEED,
        )
        assert fig is not None
        plt.close(fig)


class TestFig5ComputePlot:
    """Fig 5 compute/plot separation: compute trains, plot only renders."""

    def test_compute_returns_result(self, small_dataset):
        comp = compute_model_comparison(
            small_dataset, onset_s=ONSET, fs=FS, seed=SEED, deep_epochs=2,
        )
        assert isinstance(comp, ModelComparisonResult)
        assert comp.n_total == len(small_dataset)
        assert comp.n_val > 0

    def test_plot_renders_from_result(self, small_dataset):
        comp = compute_model_comparison(
            small_dataset, onset_s=ONSET, fs=FS, seed=SEED, deep_epochs=2,
        )
        fig = plot_model_comparison(comp)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_backward_compat_wrapper(self, small_dataset):
        fig = make_fig5_model_comparison(
            small_dataset, onset_s=ONSET, fs=FS, seed=SEED, deep_epochs=2,
        )
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    def test_plot_with_mock_data(self):
        """plot_model_comparison renders from mock — zero training."""
        comp = ModelComparisonResult(
            classical_acc=0.6, classical_bin_acc=0.5,
            rf_acc=0.75, rf_binary_acc=0.8,
            deep_acc=0.7, deep_bin_acc=0.65,
            n_val=20, n_total=100,
        )
        fig = plot_model_comparison(comp)
        assert isinstance(fig, plt.Figure)
        plt.close(fig)


# ═══════════════════════════════════════════════════════════════════
#  make_eval_report
# ═══════════════════════════════════════════════════════════════════

class TestEvalReport:
    def test_returns_dict(self):
        figs = make_eval_report(
            n_per_class=1, duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        assert isinstance(figs, dict)
        for fig in figs.values():
            plt.close(fig)

    def test_all_figures_present(self):
        figs = make_eval_report(
            n_per_class=1, duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        expected = {"fig1_onset_detection", "fig2_cause_classification",
                    "fig3_robustness", "fig4_ablation", "fig5_model_comparison"}
        assert set(figs.keys()) == expected
        for fig in figs.values():
            plt.close(fig)

    def test_all_are_figures(self):
        figs = make_eval_report(
            n_per_class=1, duration_s=DUR, onset_s=ONSET, seed=SEED,
        )
        for name, fig in figs.items():
            assert isinstance(fig, plt.Figure), f"{name} is not a Figure"
            plt.close(fig)


# ═══════════════════════════════════════════════════════════════════
#  Proxy label correctness
# ═══════════════════════════════════════════════════════════════════

class TestProxyLabels:
    def test_display_labels_populated(self):
        """DISPLAY_LABELS should map internal names to user-friendly ones."""
        assert len(DISPLAY_LABELS) > 0

    def test_no_bare_neurotox_in_display(self):
        """No display label should be just 'neurotox' without qualification."""
        for key, val in DISPLAY_LABELS.items():
            if "neurotox" in key.lower():
                # Display value should use "suppression" language
                assert "suppression" in val.lower() or "†" in val, \
                    f"Display label for '{key}' should use proxy language, got '{val}'"

    def test_binary_labels_use_proxy(self):
        """Binary labels should say 'Suppression-Like†' not 'neurotox'."""
        joined = " ".join(BINARY_LABELS)
        assert "neurotox" not in joined.lower()
        assert "suppression" in joined.lower() or "Suppression" in joined
