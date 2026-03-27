#!/usr/bin/env python3
"""
Generate randomized trial orders for the phantom validation experiment.

Produces:
  experiment/session_log.csv    – machine-readable session log
  experiment/trial_orders.txt   – printable bench sheets

Protocol summary:
  Session 0: 5 baseline trials
  Session 1: 15 impedance trials (5 × 3 levels)
  Session 2: 15 coupling trials (5 × 3 levels)
  Session 3: 15 injection trials (5 × 3 levels)
  Session 4: 27 replication trials (3 × 9 conditions from sessions 1-3)
  Total: 77 trials

Usage:
  python experiment/generate_trial_order.py [--seed 42] [--outdir experiment]
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np


# ── Trial definitions ──────────────────────────────────────────────────
SESSIONS = {
    0: {
        'name': 'Baseline',
        'trials': [
            ('baseline', 'none', 5),
        ],
    },
    1: {
        'name': 'Impedance Shift',
        'trials': [
            ('impedance', '0.3pct', 5),
            ('impedance', '0.1pct', 5),
            ('impedance', '0.9pct', 5),  # control: same as baseline
        ],
    },
    2: {
        'name': 'Coupling',
        'trials': [
            ('coupling', '1M', 5),
            ('coupling', '100k', 5),
            ('coupling', '10k', 5),
        ],
    },
    3: {
        'name': 'Noise Injection',
        'trials': [
            ('injection', '10mV', 5),
            ('injection', '50mV', 5),
            ('injection', '100mV', 5),
        ],
    },
    4: {
        'name': 'Replication',
        'trials': [
            # 3 reps of each condition from sessions 1-3
            ('impedance', '0.3pct', 3),
            ('impedance', '0.1pct', 3),
            ('impedance', '0.9pct', 3),
            ('coupling', '1M', 3),
            ('coupling', '100k', 3),
            ('coupling', '10k', 3),
            ('injection', '10mV', 3),
            ('injection', '50mV', 3),
            ('injection', '100mV', 3),
        ],
    },
}


def _filename(session: int, trial: int, perturbation: str, level: str) -> str:
    """Generate the standard filename for a trial recording."""
    return f"S{session}_T{trial:02d}_{perturbation}_{level}.rhd"


def generate_session_log(seed: int = 42) -> list[dict]:
    """Generate the full randomized session log.

    Parameters
    ----------
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    list[dict]
        Each dict has keys: session, trial, perturbation, level, filename,
        temperature_c, notes
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    for session_id, session_def in SESSIONS.items():
        # Build flat list of all trials in this session
        trial_list: list[tuple[str, str]] = []
        for perturbation, level, count in session_def['trials']:
            trial_list.extend([(perturbation, level)] * count)

        # Shuffle within session
        indices = rng.permutation(len(trial_list))
        shuffled = [trial_list[i] for i in indices]

        for trial_num, (perturbation, level) in enumerate(shuffled, start=1):
            rows.append({
                'session': session_id,
                'trial': trial_num,
                'perturbation': perturbation,
                'level': level,
                'filename': _filename(session_id, trial_num, perturbation, level),
                'temperature_c': '',
                'notes': '',
            })

    return rows


def write_session_log(rows: list[dict], path: Path) -> None:
    """Write session log to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ['session', 'trial', 'perturbation', 'level', 'filename',
                  'temperature_c', 'notes']
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_trial_orders(rows: list[dict], path: Path) -> None:
    """Write printable trial order sheets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    current_session = -1
    for row in rows:
        session = int(row['session'])
        if session != current_session:
            if current_session >= 0:
                lines.append('')
            session_name = SESSIONS[session]['name']
            lines.append(f"{'=' * 60}")
            lines.append(f"SESSION {session}: {session_name}")
            lines.append(f"{'=' * 60}")
            lines.append(f"{'Trial':>5}  {'Perturbation':<15}  {'Level':<10}  {'Filename':<40}  Temp  Notes")
            lines.append(f"{'-' * 5}  {'-' * 15}  {'-' * 10}  {'-' * 40}  {'----'}  {'-----'}")
            current_session = session

        lines.append(
            f"{row['trial']:>5}  {row['perturbation']:<15}  {row['level']:<10}  "
            f"{row['filename']:<40}  ____  _______________"
        )

    lines.append('')
    lines.append(f"Total trials: {len(rows)}")
    lines.append(f"Print this sheet and bring to the bench.")

    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(
        description='Generate randomized trial orders for phantom experiment'
    )
    parser.add_argument('--seed', type=int, default=42,
                       help='Random seed (default: 42)')
    parser.add_argument('--outdir', type=str, default='experiment',
                       help='Output directory (default: experiment)')
    args = parser.parse_args()

    outdir = Path(args.outdir)
    rows = generate_session_log(seed=args.seed)

    log_path = outdir / 'session_log.csv'
    write_session_log(rows, log_path)
    print(f"Session log → {log_path} ({len(rows)} trials)")

    orders_path = outdir / 'trial_orders.txt'
    write_trial_orders(rows, orders_path)
    print(f"Trial orders → {orders_path}")

    # Summary
    session_counts = {}
    for row in rows:
        s = int(row['session'])
        session_counts[s] = session_counts.get(s, 0) + 1

    print("\nBreakdown:")
    for s in sorted(session_counts):
        name = SESSIONS[s]['name']
        print(f"  Session {s} ({name}): {session_counts[s]} trials")
    print(f"  Total: {len(rows)} trials")


if __name__ == '__main__':
    main()
