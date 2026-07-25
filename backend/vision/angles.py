"""Joint-angle feature extraction from pose keypoints.

Keypoint frame format (normalized image coordinates, x right, y down,
0..1). "l_*" / "r_*" refer to IMAGE-left / IMAGE-right side:

    {"nose": [x, y], "l_shoulder": [...], "r_shoulder": [...],
     "l_elbow": [...], "r_elbow": [...], "l_wrist": [...],
     "r_wrist": [...], "l_hip": [...], "r_hip": [...]}

Angle-based features are invariant to camera distance (per the concept
design: joint angles instead of raw coordinates).
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

REQUIRED_POINTS = (
    "nose",
    "l_shoulder", "r_shoulder",
    "l_elbow", "r_elbow",
    "l_wrist", "r_wrist",
    "l_hip", "r_hip",
)


def validate_frame(frame: dict[str, Any]) -> None:
    for name in REQUIRED_POINTS:
        pt = frame.get(name)
        if (
            not isinstance(pt, (list, tuple))
            or len(pt) != 2
            or not all(isinstance(v, (int, float)) for v in pt)
        ):
            raise ValueError(f"invalid or missing keypoint: {name}")


def elevation_deg(shoulder: tuple[float, float], wrist: tuple[float, float]) -> float:
    """Arm elevation: 0 = straight down, 90 = horizontal, 180 = straight up."""
    vx = wrist[0] - shoulder[0]
    vy = wrist[1] - shoulder[1]
    norm = math.hypot(vx, vy)
    if norm < 1e-6:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, vy / norm))))


def frame_features(frame: dict[str, Any]) -> dict[str, float]:
    validate_frame(frame)
    ls, rs = frame["l_shoulder"], frame["r_shoulder"]
    lw, rw = frame["l_wrist"], frame["r_wrist"]
    nose = frame["nose"]
    shoulder_w = max(abs(rs[0] - ls[0]), 1e-6)
    return {
        "l_elev": elevation_deg(ls, lw),
        "r_elev": elevation_deg(rs, rw),
        "l_wx": lw[0], "l_wy": lw[1],
        "r_wx": rw[0], "r_wy": rw[1],
        # Arm extension separates a fully extended arm from a bent one at
        # the same elevation — a hand held at the head (phone, adjusting a
        # headset) points as steeply "up" as an ICAO all-clear does.
        "l_ext": math.hypot(lw[0] - ls[0], lw[1] - ls[1]) / shoulder_w,
        "r_ext": math.hypot(rw[0] - rs[0], rw[1] - rs[1]) / shoulder_w,
        "crossed": 1.0 if lw[0] > rw[0] + 0.01 else 0.0,
        "above_head": 1.0 if (lw[1] < nose[1] and rw[1] < nose[1]) else 0.0,
        "wrist_dist_ratio": math.hypot(lw[0] - rw[0], lw[1] - rw[1]) / shoulder_w,
        "center_x": (ls[0] + rs[0]) / 2.0,
    }


def _median3(per: list[dict[str, float]]) -> list[dict[str, float]]:
    """Three-point median filter over the per-frame feature series.

    MediaPipe landmarks jitter by ~0.01-0.03 in normalised coordinates, and
    a single bad frame used to propagate straight into the window
    statistics. A median rejects the spike without smearing real motion the
    way a mean would, and on the binary features (crossed, above_head) it
    acts as a majority vote that removes single-frame flicker.
    """
    if len(per) < 3:
        return per
    keys = list(per[0])
    out = []
    for i in range(len(per)):
        lo, hi = max(0, i - 1), min(len(per), i + 2)
        out.append({k: median([p[k] for p in per[lo:hi]]) for k in keys})
    return out


def window_features(frames: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate per-frame features over a temporal window."""
    if len(frames) < 4:
        raise ValueError("at least 4 frames required for temporal features")
    per = _median3([frame_features(f) for f in frames])

    def series(key: str) -> list[float]:
        return [p[key] for p in per]

    def agg(key: str) -> tuple[float, float]:
        """Mean, plus a robust amplitude.

        The amplitude was max-min, the statistic most sensitive to
        outliers there is: under realistic landmark noise a *static* arm
        measured 25-30 degrees of apparent swing against a 12-15 degree
        "is it still?" threshold, so every signal requiring a held arm
        (stop, all_clear, both turns, cut_engines) failed outright.
        The 10th-90th percentile range measures the same motion and
        ignores the tails.
        """
        s = sorted(series(key))
        n = len(s)
        spread = s[int(0.9 * (n - 1))] - s[int(0.1 * (n - 1))]
        return sum(s) / n, spread

    l_elev_mean, l_elev_amp = agg("l_elev")
    r_elev_mean, r_elev_amp = agg("r_elev")
    _, l_wx_amp = agg("l_wx")
    _, l_wy_amp = agg("l_wy")
    _, r_wx_amp = agg("r_wx")
    _, r_wy_amp = agg("r_wy")
    l_ext_mean, _ = agg("l_ext")
    r_ext_mean, _ = agg("r_ext")
    dist_mean, dist_amp = agg("wrist_dist_ratio")
    center_x = sum(series("center_x")) / len(per)
    l_wx_mean = sum(series("l_wx")) / len(per)
    r_wx_mean = sum(series("r_wx")) / len(per)

    return {
        "l_elev_mean": l_elev_mean, "l_elev_amp": l_elev_amp,
        "r_elev_mean": r_elev_mean, "r_elev_amp": r_elev_amp,
        "l_wx_amp": l_wx_amp, "l_wy_amp": l_wy_amp,
        "r_wx_amp": r_wx_amp, "r_wy_amp": r_wy_amp,
        "crossed_frac": sum(series("crossed")) / len(per),
        "above_head_frac": sum(series("above_head")) / len(per),
        "l_ext_mean": l_ext_mean, "r_ext_mean": r_ext_mean,
        "dist_ratio_mean": dist_mean, "dist_ratio_amp": dist_amp,
        "l_wx_center_off": abs(l_wx_mean - center_x),
        "r_wx_center_off": abs(r_wx_mean - center_x),
        "n_frames": float(len(per)),
    }
