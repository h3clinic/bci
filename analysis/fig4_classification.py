"""
fig4_classification.py — Figure 4: Neurotoxicity Classification Performance.

Produces a 4-panel figure:
  A) ROC curves (one-vs-rest for each class)
  B) Confusion matrix heatmap
  C) Feature importance bar chart
  D) Cross-validation accuracy distribution

This is the ML result figure — demonstrates the platform's ability to
discriminate neurotoxic perturbation severity levels.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
from sklearn.preprocessing import label_binarize
from sklearn.metrics import roc_curve, auc

from neurotox_classifier import (
    generate_dataset,
    train_and_evaluate,
    ClassificationResult,
    FEATURE_NAMES,
)


def make_figure(
    result: ClassificationResult | None = None,
    X: np.ndarray | None = None,
    y: np.ndarray | None = None,
    n_trials_per_class: int = 30,
    seed: int = 42,
    output_path: str | Path | None = None,
    dpi: int = 200,
) -> plt.Figure:
    """Generate the 4-panel classification figure.

    Args:
        result: Pre-computed ClassificationResult (optional).
        X: Feature matrix (optional, used for ROC computation).
        y: Labels (optional, used for ROC computation).
        n_trials_per_class: Trials per class for dataset generation.
        seed: Random seed.
        output_path: If provided, save figure.
        dpi: Output resolution.

    Returns:
        matplotlib Figure object.
    """
    # Generate data if not provided
    if X is None or y is None:
        X, y, _, _ = generate_dataset(n_trials_per_class=n_trials_per_class, seed=seed)

    if result is None:
        result = train_and_evaluate(X, y, seed=seed)

    # For ROC curves, we need to retrain and get probabilities
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import StratifiedKFold

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    clf = RandomForestClassifier(
        n_estimators=100, max_depth=8, min_samples_leaf=3,
        random_state=seed, class_weight="balanced",
    )
    clf.fit(X_scaled, y)
    y_proba = clf.predict_proba(X_scaled)

    classes = [0, 1, 2]
    class_names = result.class_names
    y_bin = label_binarize(y, classes=classes)

    # ── Layout ───────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 14))
    fig.suptitle(
        "Figure 4 — Neurotoxicity Classification Performance",
        fontsize=14, fontweight="bold", y=0.98,
    )

    gs = gridspec.GridSpec(2, 2, hspace=0.35, wspace=0.35,
                           left=0.08, right=0.95, top=0.92, bottom=0.08)

    # ── Panel A: ROC Curves ──────────────────────────────────────
    ax_a = fig.add_subplot(gs[0, 0])
    colors = ["#2196F3", "#FF9800", "#F44336"]
    for i, (name, color) in enumerate(zip(class_names, colors)):
        fpr, tpr, _ = roc_curve(y_bin[:, i], y_proba[:, i])
        roc_auc = auc(fpr, tpr)
        ax_a.plot(fpr, tpr, color=color, linewidth=2,
                  label=f"{name} (AUC={roc_auc:.3f})")

    ax_a.plot([0, 1], [0, 1], "k--", linewidth=1, alpha=0.5)
    ax_a.set_xlabel("False Positive Rate")
    ax_a.set_ylabel("True Positive Rate")
    ax_a.set_title(f"A) ROC Curves (Macro AUROC = {result.auroc_macro:.3f})")
    ax_a.legend(loc="lower right", fontsize=9)
    ax_a.set_xlim(-0.02, 1.02)
    ax_a.set_ylim(-0.02, 1.02)
    ax_a.grid(True, alpha=0.3)

    # ── Panel B: Confusion Matrix ────────────────────────────────
    ax_b = fig.add_subplot(gs[0, 1])
    cm = result.confusion_matrix
    im = ax_b.imshow(cm, cmap="Blues", aspect="auto")

    # Annotate cells
    for i in range(3):
        for j in range(3):
            color = "white" if cm[i, j] > cm.max() * 0.5 else "black"
            ax_b.text(j, i, str(cm[i, j]), ha="center", va="center",
                     fontsize=14, fontweight="bold", color=color)

    ax_b.set_xticks(range(3))
    ax_b.set_yticks(range(3))
    ax_b.set_xticklabels(class_names)
    ax_b.set_yticklabels(class_names)
    ax_b.set_xlabel("Predicted")
    ax_b.set_ylabel("True")
    ax_b.set_title(f"B) Confusion Matrix (Accuracy = {result.accuracy:.3f})")
    fig.colorbar(im, ax=ax_b, shrink=0.8)

    # ── Panel C: Feature Importance ──────────────────────────────
    ax_c = fig.add_subplot(gs[1, 0])
    importance = result.feature_importance
    sorted_idx = np.argsort(importance)[::-1]
    names_sorted = [FEATURE_NAMES[i] for i in sorted_idx]
    importance_sorted = importance[sorted_idx]

    bars = ax_c.barh(range(len(names_sorted)), importance_sorted,
                     color=plt.cm.viridis(np.linspace(0.3, 0.8, len(names_sorted))))
    ax_c.set_yticks(range(len(names_sorted)))
    ax_c.set_yticklabels(names_sorted, fontsize=9)
    ax_c.set_xlabel("Importance (Gini)")
    ax_c.set_title("C) Feature Importance")
    ax_c.invert_yaxis()
    ax_c.grid(True, alpha=0.3, axis="x")

    # ── Panel D: CV Score Distribution ───────────────────────────
    ax_d = fig.add_subplot(gs[1, 1])
    cv = result.cv_scores
    ax_d.bar(range(len(cv)), cv, color="#4CAF50", alpha=0.8, width=0.6)
    ax_d.axhline(cv.mean(), color="red", linestyle="--", linewidth=2,
                 label=f"Mean: {cv.mean():.3f} ± {cv.std():.3f}")
    ax_d.set_xlabel("CV Fold")
    ax_d.set_ylabel("Accuracy")
    ax_d.set_title("D) Stratified Cross-Validation")
    ax_d.set_xticks(range(len(cv)))
    ax_d.set_xticklabels([f"Fold {i+1}" for i in range(len(cv))])
    ax_d.set_ylim(0, 1.1)
    ax_d.legend(fontsize=10)
    ax_d.grid(True, alpha=0.3, axis="y")

    # ── Summary annotation ───────────────────────────────────────
    summary = (
        f"Classifier: RandomForest (100 trees, max_depth=8)\n"
        f"Trials: {result.n_trials} ({result.n_trials//9} per class per type)\n"
        f"Features: {result.n_features} electrophysiological metrics\n"
        f"Macro AUROC: {result.auroc_macro:.3f}"
    )
    fig.text(0.5, 0.01, summary, ha="center", fontsize=9,
             style="italic", color="gray")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    return fig
