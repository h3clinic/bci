"""
make_all_figures.py — One command produces all validation figures.

Usage:
    python make_all_figures.py                    # synthetic data (default)
    python make_all_figures.py --data capture.bin # real hardware data
    python make_all_figures.py --outdir results/  # custom output directory

Produces:
    out/figures/fig1_noise_stability.png
    out/figures/fig2_crosstalk.png
    out/figures/fig3_detection.png
    out/figures/fig4_classification.png
    out/figures/fig5_perturbation_gallery.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

from data_loader import load_synthetic, load_frame_stream
from fig1_noise_stability import make_figure as make_fig1
from fig2_crosstalk import make_figure as make_fig2
from fig3_detection import make_figure as make_fig3
from fig4_classification import make_figure as make_fig4
from fig5_perturbation_gallery import make_figure as make_fig5


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate all validation figures for BCI Interface."
    )
    ap.add_argument("--data", type=str, default=None,
                    help="Binary frame stream from hardware (omit for synthetic)")
    ap.add_argument("--outdir", type=str, default="out/figures",
                    help="Output directory for figures")
    ap.add_argument("--duration", type=float, default=30.0,
                    help="Duration for synthetic recordings (seconds)")
    ap.add_argument("--channels", type=int, default=16,
                    help="Number of channels for synthetic recordings")
    ap.add_argument("--dpi", type=int, default=200,
                    help="Figure resolution")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print("=" * 60)
    print("  BCI Interface — Validation Figure Generator")
    print("=" * 60)

    if args.data:
        print(f"\nSource: hardware data ({args.data})")
        rec_hw = load_frame_stream(args.data)
        # Use same recording for all figures (real data mode)
        rec_baseline = rec_hw
        rec_crosstalk = rec_hw
        rec_neurotox = rec_hw
        onset_s = None  # unknown — detector will find it
    else:
        print(f"\nSource: synthetic ({args.channels} ch, {args.duration}s)")
        print("  (Replace with real data when hardware arrives)")
        rec_baseline = load_synthetic(
            "baseline", n_channels=args.channels, duration_s=args.duration,
        )
        rec_crosstalk = load_synthetic(
            "crosstalk", n_channels=args.channels, duration_s=5.0,
        )
        rec_neurotox = load_synthetic(
            "neurotox", n_channels=args.channels, duration_s=args.duration,
        )
        onset_s = args.duration / 2

    # ── Figure 1: Noise & Stability ──────────────────────────────
    print("\n[1/5] Figure 1 — Noise & Stability...")
    fig1_path = outdir / "fig1_noise_stability.png"
    make_fig1(rec_baseline, output_path=fig1_path, dpi=args.dpi)
    print(f"  → {fig1_path}")

    # ── Figure 2: Crosstalk ──────────────────────────────────────
    print("[2/5] Figure 2 — Crosstalk / Isolation...")
    fig2_path = outdir / "fig2_crosstalk.png"
    make_fig2(rec_crosstalk, source_channel=0, tone_freq_hz=1000.0,
              output_path=fig2_path, dpi=args.dpi)
    print(f"  → {fig2_path}")

    # ── Figure 3: Perturbation Detection ─────────────────────────
    print("[3/5] Figure 3 — Neurotox Proxy Detection...")
    fig3_path = outdir / "fig3_detection.png"
    make_fig3(rec_neurotox, perturbation_onset_s=onset_s,
              output_path=fig3_path, dpi=args.dpi)
    print(f"  → {fig3_path}")

    # ── Figure 4: Neurotox Classification ─────────────────────────
    print("[4/5] Figure 4 — Neurotoxicity Classification...")
    fig4_path = outdir / "fig4_classification.png"
    make_fig4(n_trials_per_class=30, seed=42,
              output_path=fig4_path, dpi=args.dpi)
    print(f"  → {fig4_path}")

    # ── Figure 5: Perturbation Gallery ────────────────────────────
    print("[5/5] Figure 5 — Perturbation Model Gallery...")
    fig5_path = outdir / "fig5_perturbation_gallery.png"
    make_fig5(output_path=fig5_path, dpi=args.dpi)
    print(f"  → {fig5_path}")

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  All 5 figures generated in {elapsed:.1f}s")
    print(f"  Output: {outdir.resolve()}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
