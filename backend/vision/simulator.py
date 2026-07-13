"""Synthetic keypoint sequence generator for the 11 marshalling signals.

Produces realistic pose keypoint windows (image-space, normalized) used
for demo round-trips and classifier regression tests. Wrist trajectories
are parameterized per signal; elbows are placed midway shoulder→wrist.
"""

from __future__ import annotations

import math
import random
from typing import Any

BASE = {
    "nose": (0.50, 0.20),
    "l_shoulder": (0.42, 0.32),
    "r_shoulder": (0.58, 0.32),
    "l_hip": (0.44, 0.60),
    "r_hip": (0.56, 0.60),
}

L_DOWN = (0.40, 0.55)
R_DOWN = (0.60, 0.55)


def _osc(a: float, b: float, phase: float) -> float:
    """Oscillate between a and b, phase in [0, 1)."""
    return a + (b - a) * (0.5 - 0.5 * math.cos(2 * math.pi * phase))


def _wrists(signal: str, phase: float) -> tuple[tuple[float, float], tuple[float, float]]:
    if signal == "stop":
        return (0.56, 0.12), (0.44, 0.12)
    if signal == "emergency_stop":
        lx = _osc(0.15, 0.56, phase)
        rx = _osc(0.85, 0.44, phase)
        ly = _osc(0.32, 0.12, phase)
        return (lx, ly), (rx, ly)
    if signal == "move_ahead":
        y = _osc(0.14, 0.30, phase)
        return (0.36, y), (0.64, y)
    if signal == "slow_down":
        y = _osc(0.31, 0.41, phase)
        return (0.16, y), (0.84, y)
    # ICAO Annex 2 App. 1, pilot POV: marshaller's RIGHT arm = IMAGE-LEFT.
    if signal == "turn_left":
        y = _osc(0.14, 0.30, phase)
        return (0.16, 0.32), (0.64, y)
    if signal == "turn_right":
        y = _osc(0.14, 0.30, phase)
        return (0.36, y), (0.84, 0.32)
    if signal == "all_clear":
        return L_DOWN, (0.62, 0.10)
    if signal == "cut_engines":
        rx = _osc(0.38, 0.54, phase)
        return L_DOWN, (rx, 0.30)
    if signal == "start_engines":
        ang = 2 * math.pi * phase
        return L_DOWN, (0.68 + 0.06 * math.cos(ang), 0.22 + 0.06 * math.sin(ang))
    # ICAO chocks: arms fully extended above head, wands move inward
    # until touching (inserted) or sweep outward (removed).
    if signal == "chocks_inserted":
        lx = _osc(0.44, 0.49, phase)
        rx = _osc(0.56, 0.51, phase)
        return (lx, 0.10), (rx, 0.10)
    if signal == "chocks_removed":
        lx = _osc(0.44, 0.30, phase)
        rx = _osc(0.56, 0.70, phase)
        return (lx, 0.10), (rx, 0.10)
    raise ValueError(f"unknown signal: {signal}")


def generate_sequence(
    signal: str, n_frames: int = 36, noise: float = 0.004, seed: int | None = None
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    frames: list[dict[str, Any]] = []
    cycles = 2.0
    for i in range(n_frames):
        phase = (cycles * i / n_frames) % 1.0
        lw, rw = _wrists(signal, phase)

        def jitter(pt: tuple[float, float]) -> list[float]:
            return [
                round(pt[0] + rng.gauss(0, noise), 4),
                round(pt[1] + rng.gauss(0, noise), 4),
            ]

        ls, rs = BASE["l_shoulder"], BASE["r_shoulder"]
        frame = {
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
        frames.append(frame)
    return frames
