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
        "crossed": 1.0 if lw[0] > rw[0] + 0.01 else 0.0,
        "above_head": 1.0 if (lw[1] < nose[1] and rw[1] < nose[1]) else 0.0,
        "wrist_dist_ratio": math.hypot(lw[0] - rw[0], lw[1] - rw[1]) / shoulder_w,
        "center_x": (ls[0] + rs[0]) / 2.0,
    }


def window_features(frames: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate per-frame features over a temporal window."""
    if len(frames) < 4:
        raise ValueError("at least 4 frames required for temporal features")
    per = [frame_features(f) for f in frames]

    def series(key: str) -> list[float]:
        return [p[key] for p in per]

    def agg(key: str) -> tuple[float, float]:
        s = series(key)
        return sum(s) / len(s), max(s) - min(s)

    l_elev_mean, l_elev_amp = agg("l_elev")
    r_elev_mean, r_elev_amp = agg("r_elev")
    _, l_wx_amp = agg("l_wx")
    _, l_wy_amp = agg("l_wy")
    _, r_wx_amp = agg("r_wx")
    _, r_wy_amp = agg("r_wy")
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
        "dist_ratio_mean": dist_mean, "dist_ratio_amp": dist_amp,
        "l_wx_center_off": abs(l_wx_mean - center_x),
        "r_wx_center_off": abs(r_wx_mean - center_x),
        "n_frames": float(len(per)),
    }
