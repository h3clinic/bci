#!/usr/bin/env python3
"""Generate BioGENEius competition poster (48×36 in) from competition_pack/ assets."""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
import os

# ---------------------------------------------------------------------------
# Poster geometry — 48 × 36 inches (standard landscape science poster)
# ---------------------------------------------------------------------------
W, H = 48, 36

prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
BG         = RGBColor(0xFF, 0xFF, 0xFF)
TITLE_BG   = RGBColor(0x1B, 0x3A, 0x5C)  # dark navy
ACCENT     = RGBColor(0x2E, 0x86, 0xC1)  # bright blue
TEXT_DARK  = RGBColor(0x22, 0x22, 0x22)
TEXT_LIGHT = RGBColor(0xFF, 0xFF, 0xFF)
PANEL_BG   = RGBColor(0xF5, 0xF7, 0xFA)  # light grey-blue
BORDER     = RGBColor(0xCC, 0xCC, 0xCC)
GREEN      = RGBColor(0x27, 0xAE, 0x60)
RED        = RGBColor(0xE7, 0x4C, 0x3C)

PACK = "competition_pack"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def add_rect(slide, left, top, width, height, fill=None, border_color=None):
    from pptx.util import Emu as _E
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE.RECTANGLE
        Inches(left), Inches(top), Inches(width), Inches(height)
    )
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    else:
        shape.fill.background()
    if border_color:
        shape.line.color.rgb = border_color
        shape.line.width = Pt(1)
    else:
        shape.line.fill.background()
    return shape

def add_text_box(slide, left, top, width, height, text, font_size=18,
                 bold=False, color=TEXT_DARK, align=PP_ALIGN.LEFT,
                 font_name="Arial", anchor=MSO_ANCHOR.TOP):
    txBox = slide.shapes.add_textbox(Inches(left), Inches(top),
                                     Inches(width), Inches(height))
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    try:
        tf.vertical_anchor = anchor
    except Exception:
        pass
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = align
    return txBox

def add_multiline(slide, left, top, width, height, lines, font_size=16,
                  color=TEXT_DARK, bullet=False, bold_first=False,
                  font_name="Arial", line_spacing=1.15):
    """Add a text box with multiple paragraphs."""
    txBox = slide.shapes.add_textbox(Inches(left), Inches(top),
                                     Inches(width), Inches(height))
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    for i, line in enumerate(lines):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        prefix = "• " if bullet else ""
        p.text = prefix + line
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = font_name
        p.space_after = Pt(font_size * 0.3)
        if bold_first and i == 0:
            p.font.bold = True
        try:
            p.line_spacing = Pt(font_size * line_spacing)
        except Exception:
            pass
    return txBox

def add_image(slide, path, left, top, width=None, height=None):
    return slide.shapes.add_picture(path, Inches(left), Inches(top),
                                    Inches(width) if width else None,
                                    Inches(height) if height else None)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
MARGIN = 0.6
COL_GAP = 0.5
PANEL_PAD = 0.3

# 3-column layout
col_w = (W - 2*MARGIN - 2*COL_GAP) / 3  # ~15.1" each

col1_x = MARGIN
col2_x = MARGIN + col_w + COL_GAP
col3_x = MARGIN + 2*(col_w + COL_GAP)

# ---------------------------------------------------------------------------
# TITLE BAR (full width, top)
# ---------------------------------------------------------------------------
TITLE_H = 4.0
add_rect(slide, 0, 0, W, TITLE_H, fill=TITLE_BG)

add_text_box(slide, MARGIN, 0.4, W - 2*MARGIN, 2.0,
             "Low-Cost Real-Time Electrophysiological Screening\n"
             "with Explicit False-Alarm Budget and Conservative Triage\n"
             "Under Adversarial Noise",
             font_size=40, bold=True, color=TEXT_LIGHT, align=PP_ALIGN.CENTER,
             font_name="Arial")

add_text_box(slide, MARGIN, 2.6, W - 2*MARGIN, 1.0,
             "A. Harshi  •  BioGENEius 2026  •  Commit 01d4694  •  github.com/aharshi/BCIInterface",
             font_size=20, color=RGBColor(0xBB, 0xCC, 0xDD), align=PP_ALIGN.CENTER)

body_top = TITLE_H + 0.3

# ---------------------------------------------------------------------------
# COLUMN 1 — Problem + System + Key Numbers
# ---------------------------------------------------------------------------

# --- Panel A: Problem & Accessibility ---
pA_y = body_top
pA_h = 7.5
add_rect(slide, col1_x, pA_y, col_w, pA_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col1_x + PANEL_PAD, pA_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "A  Problem & Accessibility", font_size=24, bold=True, color=ACCENT)

add_multiline(slide, col1_x + PANEL_PAD, pA_y + 1.0, col_w - 2*PANEL_PAD, 6.0,
              [
                  "Neurotoxicity screening requires $50K–200K MEA systems, weeks of wet-lab work, and specialized facilities",
                  "Most high school and undergraduate labs lack access to any of these",
                  "We built a <$30 headstage (Intan RHD2132, 16 ch) + open-source computational pipeline validated entirely on synthetic phantom signals",
                  "Contribution: an end-to-end system with quantifiable operating guarantees — not just a classifier",
                  "All labels are proxy signatures — no clinical neurotoxicity claims",
              ],
              font_size=17, bullet=True, line_spacing=1.3)

# --- Panel B: System Diagram ---
pB_y = pA_y + pA_h + 0.4
pB_h = 8.5
add_rect(slide, col1_x, pB_y, col_w, pB_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col1_x + PANEL_PAD, pB_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "B  Two-Stage Pipeline Architecture", font_size=24, bold=True, color=ACCENT)

# System diagram as text (ASCII-style, monospace)
diagram_lines = [
    "┌──────────────────────────────────────────────┐",
    "│  MEA → AFE Headstage → SPI → Frame Parser    │",
    "│              ↓                                │",
    "│    1-second Feature Windows (23 features)     │",
    "│              ↓                                │",
    "│  ┌─── Stage 1: CUSUM Detector ───┐           │",
    "│  │  Cumulative sum on Z-scores    │           │",
    "│  │  Threshold: 10σ, Cooldown: 30s │           │",
    "│  └──────────┬─────────────────────┘           │",
    "│             ↓ trigger                         │",
    "│  ┌─── Stage 2: RF Classifier ────┐           │",
    "│  │  100 trees, max_depth=6       │           │",
    "│  │  + K-of-N confirmation (3/5)  │           │",
    "│  └──────────┬─────────────────────┘           │",
    "│        ┌────┼────┐                            │",
    "│        ↓    ↓    ↓                            │",
    "│   ALERT  REVIEW  FILTER                       │",
    "│  (suppr.) (unc.) (artifact)                   │",
    "└──────────────────────────────────────────────┘",
]
add_multiline(slide, col1_x + PANEL_PAD, pB_y + 1.0, col_w - 2*PANEL_PAD, 7.0,
              diagram_lines, font_size=13, color=TEXT_DARK, font_name="Courier New",
              line_spacing=1.05)

# --- Key Numbers Table ---
pT_y = pB_y + pB_h + 0.4
pT_h = 7.8
add_rect(slide, col1_x, pT_y, col_w, pT_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col1_x + PANEL_PAD, pT_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "Key Results", font_size=24, bold=True, color=ACCENT)

metrics = [
    ("Sensitivity (proxy suppression-like)", "6 / 6  (100%)"),
    ("Worst-case FPR/hr @ operating point", "0.00"),
    ("Median detection latency", "1.0 s"),
    ("Throughput", "1,393× real-time"),
    ("Calibration ECE", "0.048"),
    ("Review load (artifact FPs)", "31%  (5/16)"),
    ("RF avg AUROC (Domain B)", "0.990"),
    ("Deep avg AUROC (Domain B)", "0.527"),
    ("AFE headstage BOM", "< $30"),
    ("Test suite", "629 tests pass"),
]

for i, (metric, value) in enumerate(metrics):
    y = pT_y + 1.1 + i * 0.62
    add_text_box(slide, col1_x + PANEL_PAD, y, col_w * 0.62, 0.55,
                 metric, font_size=16, color=TEXT_DARK)
    add_text_box(slide, col1_x + col_w * 0.62, y, col_w * 0.35, 0.55,
                 value, font_size=16, bold=True, color=TITLE_BG, align=PP_ALIGN.RIGHT)

# ---------------------------------------------------------------------------
# COLUMN 2 — Money Plot (Pareto) + Adversarial Sweep
# ---------------------------------------------------------------------------

# --- Panel C: Pareto Curve (MONEY PLOT) ---
pC_y = body_top
pC_h = 12.5
add_rect(slide, col2_x, pC_y, col_w, pC_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col2_x + PANEL_PAD, pC_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "C  Operating Point Selection (Pareto Front)", font_size=24, bold=True, color=ACCENT)

add_image(slide, os.path.join(PACK, "pareto_curve.png"),
          col2_x + 0.5, pC_y + 1.1, width=col_w - 1.0)

add_multiline(slide, col2_x + PANEL_PAD, pC_y + 9.2, col_w - 2*PANEL_PAD, 3.0,
              [
                  "Pareto front: neurotox sensitivity vs worst-case FPR/hr across 5 electromagnetic noise regimes (clean, 60 Hz mild, 60 Hz heavy+harmonics, EMI bursts, thermal drift).",
                  "Selected operating point (10σ threshold, 30s cooldown): 6/6 sensitivity at 0 FA/hr. Conservative: no silent dismissals.",
              ],
              font_size=14, color=TEXT_DARK, line_spacing=1.2)

# --- Panel C2: Adversarial Sweep ---
pC2_y = pC_y + pC_h + 0.4
pC2_h = 18.5
add_rect(slide, col2_x, pC2_y, col_w, pC2_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col2_x + PANEL_PAD, pC2_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "C'  Adversarial Sweep — 30 Conditions", font_size=24, bold=True, color=ACCENT)

add_image(slide, os.path.join(PACK, "noise_immunity.png"),
          col2_x + 0.3, pC2_y + 1.1, width=col_w - 0.6)

add_multiline(slide, col2_x + PANEL_PAD, pC2_y + 14.0, col_w - 2*PANEL_PAD, 4.0,
              [
                  "10 perturbation types × 3 severities in 30-second recordings.",
                  "All 6 neurotox conditions detected and correctly triaged (suppression-like) with ≤2s latency.",
                  "11/16 detected artifacts correctly filtered or flagged uncertain.",
                  "5/16 persistent hardware shifts → suppression-like (review load). These are NOT silent misses.",
              ],
              font_size=14, color=TEXT_DARK, line_spacing=1.2)

# ---------------------------------------------------------------------------
# COLUMN 3 — Domain Shift + FP Diagnostic + Limitations
# ---------------------------------------------------------------------------

# --- Panel D: Domain Shift Robustness ---
pD_y = body_top
pD_h = 14.0
add_rect(slide, col3_x, pD_y, col_w, pD_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col3_x + PANEL_PAD, pD_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "D  Domain Shift Robustness", font_size=24, bold=True, color=ACCENT)

add_image(slide, os.path.join(PACK, "ds_poster_fig.png"),
          col3_x + 0.3, pD_y + 1.1, width=col_w - 0.6)

add_multiline(slide, col3_x + PANEL_PAD, pD_y + 10.8, col_w - 2*PANEL_PAD, 3.0,
              [
                  "13 domain-shift types × 3 severities. RF maintains avg AUROC 0.990 (worst 0.774, rate_decrease).",
                  "Deep model (CNN+Attention, 76K params) collapses to chance (0.527) — not a tuning failure, but a data-scale + domain-shift limitation.",
                  "RF's hand-crafted features encode prior knowledge that deep models can't learn from ~250 windows.",
              ],
              font_size=14, color=TEXT_DARK, line_spacing=1.2)

# --- Panel E: FP Diagnostic (Honesty + Safety) ---
pE_y = pD_y + pD_h + 0.4
pE_h = 10.0
add_rect(slide, col3_x, pE_y, col_w, pE_h, fill=PANEL_BG, border_color=BORDER)
add_text_box(slide, col3_x + PANEL_PAD, pE_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "E  Failure Mode Analysis — Why It's Safe", font_size=24, bold=True, color=ACCENT)

add_image(slide, os.path.join(PACK, "fp_diagnostic.png"),
          col3_x + 0.3, pE_y + 1.1, width=col_w - 0.6)

add_multiline(slide, col3_x + PANEL_PAD, pE_y + 6.2, col_w - 2*PANEL_PAD, 3.5,
              [
                  "5 persistent FPs share a common mechanism: hardware artifacts produce the same directional feature changes (↓RMS, ↓bandpower, ↓variability) as biological suppression.",
                  "Feature attribution confirms rms_max is the top driver in all 5 FP conditions.",
                  "We tested 5 additional features, a multi-class subtype classifier, and type-specific overrides. None improved: this is an information bottleneck, not a model limitation.",
                  "Resolution requires new observables (power monitoring, impedance probing) — planned for hardware v2.",
              ],
              font_size=14, color=TEXT_DARK, line_spacing=1.2)

# --- Limitations + Future Work ---
pF_y = pE_y + pE_h + 0.4
pF_h = 6.8
add_rect(slide, col3_x, pF_y, col_w, pF_h, fill=RGBColor(0xFD, 0xF2, 0xE9), border_color=BORDER)
add_text_box(slide, col3_x + PANEL_PAD, pF_y + 0.2, col_w - 2*PANEL_PAD, 0.6,
             "Limitations & Future Work", font_size=22, bold=True, color=RED)

add_multiline(slide, col3_x + PANEL_PAD, pF_y + 1.0, col_w - 2*PANEL_PAD, 5.5,
              [
                  "All data is synthetic — phantom + wet-lab validation planned but not yet performed",
                  "5/16 artifact conditions produce feature signatures indistinguishable from suppression with current observables",
                  "Deep learning failed at this data scale; SSL pretraining on real EEG/MEA data is the natural next step",
                  "Hardware v2: supply current monitoring + impedance probing to break the observability limit",
                  "Conformal prediction for principled uncertainty quantification (replace Mahalanobis)",
              ],
              font_size=15, bullet=True, color=TEXT_DARK, line_spacing=1.3)

# --- Bottom bar: core claim ---
FOOTER_H = 1.6
add_rect(slide, 0, H - FOOTER_H, W, FOOTER_H, fill=TITLE_BG)
add_text_box(slide, MARGIN, H - FOOTER_H + 0.25, W - 2*MARGIN, 1.0,
             "\"We don't claim perfect classification. We claim an explicit false-alarm budget "
             "and conservative triage under adversarial noise — so failures become review load, not silent misses.\"",
             font_size=22, bold=False, color=TEXT_LIGHT, align=PP_ALIGN.CENTER,
             font_name="Arial")

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
out_path = os.path.join(PACK, "poster_48x36.pptx")
prs.save(out_path)
print(f"✓ Poster saved: {out_path}")
print(f"  Size: {os.path.getsize(out_path) / 1024:.0f} KB")
print(f"  Dimensions: {W}×{H} inches")
print(f"\nNext: open in PowerPoint/Slides, adjust font sizes if needed,")
print(f"      then File → Export → PDF for submission.")
