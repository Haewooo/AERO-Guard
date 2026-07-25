"""Evaluation corpus for the marshalling classifier.

**These sequences are synthetic.** They are produced by the same
generator the classifier's thresholds were tuned against, so in-
distribution accuracy on them is a consistency check, not a measurement
of field accuracy. Nothing here licenses a claim about real marshallers.

What the corpus does measure honestly:

* **Degradation** under landmark noise, off-axis capture and dropped
  frames — the classifier's sensitivity to conditions it was not tuned
  for.
* **False positives** on ordinary apron motion that is not a marshalling
  signal at all. This needs no ground-truth video to be meaningful: any
  confident classification of "person standing still" is wrong by
  construction.

`load_real_corpus()` reads a field dataset when one exists, so the same
report can be regenerated against real data without changing the harness.
Until then the report must be read as synthetic.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from backend.vision.simulator import BASE, L_DOWN, generate_sequence

# Non-signal motion an apron camera sees constantly. None of these is a
# marshalling signal, so any confident classification is a false positive.
NON_SIGNAL_KINDS = (
    "standing",
    "waving_hello",
    "scratching_head",
    "phone_to_ear",
    "stretching",
    "carrying_box",
    "walking_past",
    "pointing",
    "adjusting_vest",
    "checking_watch",
    # Large two-arm motion. Kept deliberately: it is what the
    # emergency_stop rule keys on (crossed overhead + high amplitude), so
    # dropping it from the corpus would hide the classifier's most
    # safety-relevant confusion behind a comfortable overall number.
    "both_arms_flagging",
    "heaving_load",
)


def _frame(lw, rw, jitter):
    ls, rs = BASE["l_shoulder"], BASE["r_shoulder"]
    return {
        "nose": jitter(BASE["nose"]),
        "l_shoulder": jitter(ls),
        "r_shoulder": jitter(rs),
        "l_elbow": jitter(((ls[0] + lw[0]) / 2, (ls[1] + lw[1]) / 2)),
        "r_elbow": jitter(((rs[0] + rw[0]) / 2, (rs[1] + rw[1]) / 2)),
        "l_wrist": jitter(lw),
        "r_wrist": jitter(rw),
        "l_hip": jitter(BASE["l_hip"]),
        "r_hip": jitter(BASE["r_hip"]),
    }


def non_signal_sequence(kind: str, seed: int, n_frames: int = 36) -> list[dict[str, Any]]:
    """Ordinary human motion that is not a marshalling signal."""
    rng = random.Random(seed)

    def jitter(pt):
        return [
            round(pt[0] + rng.gauss(0, 0.006), 4),
            round(pt[1] + rng.gauss(0, 0.006), 4),
        ]

    frames = []
    for i in range(n_frames):
        ph = i / n_frames
        if kind == "standing":
            lw = (0.40 + 0.01 * math.sin(6 * ph), 0.55)
            rw = (0.60 - 0.01 * math.sin(6 * ph), 0.55)
        elif kind == "waving_hello":
            lw, rw = L_DOWN, (0.66 + 0.07 * math.sin(8 * math.pi * ph), 0.16)
        elif kind == "scratching_head":
            lw, rw = L_DOWN, (0.52, 0.20 + 0.06 * math.sin(2 * math.pi * ph))
        elif kind == "phone_to_ear":
            lw, rw = L_DOWN, (0.55, 0.19)
        elif kind == "stretching":
            y = 0.14 + 0.05 * math.sin(2 * math.pi * ph)
            lw, rw = (0.34, y), (0.66, y)
        elif kind == "carrying_box":
            lw, rw = (0.44, 0.42), (0.56, 0.42)
        elif kind == "walking_past":
            swing = 0.04 * math.sin(4 * math.pi * ph)
            lw, rw = (0.40 + swing, 0.55 - swing), (0.60 - swing, 0.55 + swing)
        elif kind == "pointing":
            lw, rw = L_DOWN, (0.78, 0.34)
        elif kind == "adjusting_vest":
            lw = (0.46, 0.44 + 0.03 * math.sin(4 * math.pi * ph))
            rw = (0.54, 0.44 - 0.03 * math.sin(4 * math.pi * ph))
        elif kind == "both_arms_flagging":
            # Waving both arms overhead to get someone's attention — the
            # everyday gesture that most resembles ICAO emergency stop.
            y = 0.30 - 0.16 * abs(math.sin(3 * math.pi * ph))
            spread = 0.20 * math.cos(3 * math.pi * ph)
            lw, rw = (0.50 - spread, y), (0.50 + spread, y)
        elif kind == "heaving_load":
            y = 0.50 - 0.34 * abs(math.sin(2 * math.pi * ph))
            lw, rw = (0.42, y), (0.58, y)
        else:  # checking_watch
            lw, rw = (0.47, 0.40), (0.53, 0.38)
        frames.append(_frame(lw, rw, jitter))
    return frames


# ── perturbations the generator never applies ───────────────────────
def with_noise(frames, sigma, seed):
    rng = random.Random(seed)
    return [
        {k: [round(v[0] + rng.gauss(0, sigma), 4), round(v[1] + rng.gauss(0, sigma), 4)]
         for k, v in f.items()}
        for f in frames
    ]


def off_axis(frames, kx):
    """Marshaller not squarely facing the camera (horizontal foreshortening)."""
    return [{k: [0.5 + (v[0] - 0.5) * kx, v[1]] for k, v in f.items()} for f in frames]


def dropped_frames(frames, fraction, seed):
    """Pose tracker losing lock: repeat the previous frame instead."""
    rng = random.Random(seed)
    out, last = [], frames[0]
    for f in frames:
        if rng.random() < fraction:
            out.append(dict(last))
        else:
            out.append(f)
            last = f
    return out


def signal_cases(seeds: range) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    from backend.vision.classifier import SIGNALS

    for signal in SIGNALS:
        for seed in seeds:
            yield signal, generate_sequence(signal, seed=seed)


def non_signal_cases(seeds: range) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    for kind in NON_SIGNAL_KINDS:
        for seed in seeds:
            yield kind, non_signal_sequence(kind, seed)


def load_real_corpus(root: str | Path) -> list[tuple[str, list[dict[str, Any]]]]:
    """Load a field dataset: <root>/<label>/<clip>.json, each a frame list.

    Frames use the keypoint schema of backend/vision/angles.py. Returns an
    empty list when the directory does not exist, so the harness runs on
    synthetic data until real recordings are available.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    cases = []
    for label_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for clip in sorted(label_dir.glob("*.json")):
            cases.append((label_dir.name, json.loads(clip.read_text())))
    return cases
