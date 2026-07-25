"""Marshalling classifier evaluation report.

Usage:
    python -m evaluation.evaluate_classifier [--real DIR] [--out FILE]

Produces a confusion matrix, per-class precision/recall/F1, a degradation
sweep over landmark noise / off-axis capture / dropped frames, and the
false-positive rate on non-marshalling motion.

Read evaluation/corpus.py before quoting any number from this: without
--real the corpus is synthetic and shares assumptions with the classifier,
so in-distribution accuracy is a consistency check rather than a field
measurement. The false-positive and degradation figures carry more weight,
because they probe conditions the generator was never tuned for.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.vision.classifier import SIGNALS, classify_window
from evaluation import corpus

UNKNOWN = "unknown"


def _classify(frames):
    try:
        r = classify_window(frames)
        return r["signal"], r["confidence"]
    except ValueError:
        return UNKNOWN, 0.0


def confusion(cases) -> tuple[dict, list[str]]:
    matrix: dict[str, Counter] = defaultdict(Counter)
    labels: set[str] = set()
    for truth, frames in cases:
        predicted, _ = _classify(frames)
        matrix[truth][predicted] += 1
        labels.update((truth, predicted))
    return matrix, sorted(labels)


def per_class(matrix, labels):
    rows = []
    for label in labels:
        tp = matrix[label][label]
        actual = sum(matrix[label].values())
        predicted = sum(matrix[t][label] for t in matrix)
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append((label, actual, precision, recall, f1))
    return rows


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", help="directory of labelled field clips")
    ap.add_argument("--out", default="evaluation/REPORT.md")
    ap.add_argument("--seeds", type=int, default=40)
    args = ap.parse_args()

    seeds = range(args.seeds)
    real = corpus.load_real_corpus(args.real) if args.real else []
    synthetic = list(corpus.signal_cases(seeds))
    cases = real or synthetic
    source = f"field dataset ({args.real})" if real else "SYNTHETIC generator"

    lines = [
        "# Marshalling classifier — evaluation report",
        "",
        f"- Corpus: **{source}**, {len(cases)} labelled sequences",
        f"- Non-signal corpus: {len(corpus.NON_SIGNAL_KINDS)} motion types "
        f"x {args.seeds} seeds",
        "- Regenerate: `python -m evaluation.evaluate_classifier`",
        "",
    ]
    if not real:
        lines += [
            "> **These numbers are synthetic.** The sequences come from the same",
            "> generator the classifier's thresholds were tuned against, so the",
            "> in-distribution accuracy below is a consistency check and must not",
            "> be quoted as field accuracy. The degradation and false-positive",
            "> sections probe conditions the generator does not model and are the",
            "> meaningful results until field recordings exist.",
            "",
        ]

    matrix, labels = confusion(cases)
    total = sum(sum(c.values()) for c in matrix.values())
    correct = sum(matrix[label][label] for label in matrix)
    lines += [
        "## 1. In-distribution confusion matrix",
        "",
        f"Overall accuracy: **{100 * correct / total:.1f}%** ({correct}/{total})",
        "",
        md_table(
            ["truth \\ predicted", *labels],
            [[t, *[matrix[t][p] or "·" for p in labels]] for t in sorted(matrix)],
        ),
        "",
        "### Per class",
        "",
        md_table(
            ["signal", "n", "precision", "recall", "F1"],
            [[lab, n, f"{p:.3f}", f"{r:.3f}", f"{f:.3f}"]
             for lab, n, p, r, f in per_class(matrix, labels)],
        ),
        "",
        "## 2. Degradation outside the generator's assumptions",
        "",
    ]

    noise_rows = []
    for sigma in (0.004, 0.010, 0.020, 0.030, 0.050):
        hit = sum(
            _classify(corpus.with_noise(frames, sigma, seed))[0] == truth
            for seed, (truth, frames) in enumerate(synthetic)
        )
        noise_rows.append([f"{sigma:.3f}", f"{100 * hit / len(synthetic):.1f}%"])
    lines += [
        "MediaPipe landmark jitter in normalised image coordinates. The "
        "generator uses sigma=0.004; a real webcam is closer to 0.01-0.03.",
        "",
        md_table(["landmark noise sigma", "accuracy"], noise_rows),
        "",
    ]

    axis_rows = []
    for kx in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
        hit = sum(
            _classify(corpus.off_axis(frames, kx))[0] == truth
            for truth, frames in synthetic
        )
        axis_rows.append([f"{kx:.1f}", f"{100 * hit / len(synthetic):.1f}%"])
    lines += [
        md_table(["horizontal scale (off-axis)", "accuracy"], axis_rows),
        "",
    ]

    drop_rows = []
    for fraction in (0.0, 0.1, 0.25, 0.5):
        hit = sum(
            _classify(corpus.dropped_frames(frames, fraction, seed))[0] == truth
            for seed, (truth, frames) in enumerate(synthetic)
        )
        drop_rows.append([f"{fraction:.0%}", f"{100 * hit / len(synthetic):.1f}%"])
    lines += [
        md_table(["frames lost (tracker drop-out)", "accuracy"], drop_rows),
        "",
        "## 3. False positives on non-marshalling motion",
        "",
        "Nobody is signalling in any of these. Every classification other "
        "than `unknown` is wrong by construction, and one that maps to "
        "`emergency_stop` fires the CRITICAL master-warning takeover.",
        "",
    ]

    fp_rows, fp_total, fp_hits, emergencies = [], 0, 0, 0
    for kind in corpus.NON_SIGNAL_KINDS:
        hits: Counter = Counter()
        for seed in seeds:
            predicted, _ = _classify(corpus.non_signal_sequence(kind, seed))
            fp_total += 1
            if predicted != UNKNOWN:
                fp_hits += 1
                hits[predicted] += 1
                if predicted == "emergency_stop":
                    emergencies += 1
        verdict = ", ".join(f"`{k}` x{v}" for k, v in hits.most_common()) or "—"
        fp_rows.append([kind, f"{100 * sum(hits.values()) / args.seeds:.0f}%", verdict])
    lines += [
        md_table(["motion", "false-signal rate", "classified as"], fp_rows),
        "",
        f"**Overall false-signal rate: {100 * fp_hits / fp_total:.1f}%** "
        f"({fp_hits}/{fp_total}); of those, **{emergencies}** classified as "
        "`emergency_stop`.",
        "",
        "Temporal gating (`backend/fusion/gating.py`) requires the signal to "
        "hold across consecutive windows before it alerts, so an isolated "
        "false positive does not reach the operator. These rates are "
        "per-window, before gating.",
        "",
        "## 4. Confidence calibration",
        "",
    ]

    conf_rows = []
    for signal in SIGNALS:
        scores = [
            _classify(frames)[1]
            for truth, frames in synthetic
            if truth == signal and _classify(frames)[0] == signal
        ]
        if scores:
            conf_rows.append([
                signal, len(scores), f"{min(scores):.3f}",
                f"{sum(scores) / len(scores):.3f}", f"{max(scores):.3f}",
            ])
    lines += [
        "The score is the weakest satisfied condition's margin past its "
        "threshold, not a probability. A narrow range for a signal means "
        "its rule is only marginally satisfied even by data tuned to it.",
        "",
        md_table(["signal", "n", "min", "mean", "max"], conf_rows),
        "",
    ]

    report = "\n".join(lines)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report)
    print(report)
    print(f"\nwritten to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
