"""Rule-based marshalling signal classifier over joint-angle features.

Classifies the 11 standard aircraft marshalling signals from a temporal
window of pose keypoints, following ICAO Annex 2 Appendix 1 (equivalent
to the Korean Aviation Safety Act standard aircraft marshalling signals).

Side convention: the camera is the aircraft/pilot point of view and the
marshaller faces the camera, so the marshaller's RIGHT arm appears on the
IMAGE-LEFT side. ICAO "turn left": right arm extended at 90°, left hand
beckons -> image-left arm static horizontal + image-right arm beckoning.

The feature interface (window_features) is shared with the planned
1D-CNN/Bi-LSTM upgrade path, so the rule engine can be swapped for a
learned model without changing callers.
"""

from __future__ import annotations

from typing import Any

from .angles import window_features

SIGNALS = [
    "move_ahead", "turn_left", "turn_right", "stop", "emergency_stop",
    "slow_down", "cut_engines", "start_engines", "chocks_inserted",
    "chocks_removed", "all_clear",
]

SIGNAL_LABELS = {
    "move_ahead": "Move Ahead",
    "turn_left": "Turn Left",
    "turn_right": "Turn Right",
    "stop": "Stop",
    "emergency_stop": "Emergency Stop",
    "slow_down": "Slow Down",
    "cut_engines": "Cut Engines",
    "start_engines": "Start Engines",
    "chocks_inserted": "Chocks Inserted",
    "chocks_removed": "Chocks Removed",
    "all_clear": "All Clear",
}


def _arm(f: dict[str, float], side: str) -> dict[str, float]:
    return {
        "elev_mean": f[f"{side}_elev_mean"],
        "elev_amp": f[f"{side}_elev_amp"],
        "wx_amp": f[f"{side}_wx_amp"],
        "wy_amp": f[f"{side}_wy_amp"],
        "center_off": f[f"{side}_wx_center_off"],
    }


def _is_down_static(arm: dict[str, float]) -> bool:
    return arm["elev_mean"] < 50 and arm["elev_amp"] < 15


def _is_horizontal_static(arm: dict[str, float]) -> bool:
    return 70 <= arm["elev_mean"] <= 105 and arm["elev_amp"] < 12


def classify_window(frames: list[dict[str, Any]]) -> dict[str, Any]:
    f = window_features(frames)
    left, right = _arm(f, "l"), _arm(f, "r")
    signal, confidence = _decide(f, left, right)
    return {
        "signal": signal,
        "label": SIGNAL_LABELS.get(signal, "Unknown"),
        "confidence": confidence,
        "features": {k: round(v, 3) for k, v in f.items()},
        "ai_assisted": True,
    }


def _decide(
    f: dict[str, float], left: dict[str, float], right: dict[str, float]
) -> tuple[str, float]:
    # 1) STOP: wrists crossed above head, static.
    if (
        f["crossed_frac"] > 0.5
        and f["above_head_frac"] > 0.5
        and max(left["elev_amp"], right["elev_amp"]) < 15
    ):
        return "stop", 0.93

    # 2) EMERGENCY STOP: large oscillation passing through crossed-overhead.
    if (
        f["crossed_frac"] > 0.15
        and left["elev_amp"] >= 40
        and right["elev_amp"] >= 40
    ):
        return "emergency_stop", 0.9

    # 3) CHOCKS (ICAO: arms fully extended ABOVE HEAD, wands moving
    # inward until touching = inserted / outward = removed).
    if (
        f["above_head_frac"] > 0.5
        and f["crossed_frac"] < 0.15
        and left["elev_mean"] > 140
        and right["elev_mean"] > 140
        and f["dist_ratio_amp"] > 0.5
    ):
        if f["dist_ratio_mean"] < 1.0:
            return "chocks_inserted", 0.82
        return "chocks_removed", 0.82

    # 4) MOVE AHEAD: both arms raised, symmetric beckoning, never crossed.
    if (
        f["crossed_frac"] < 0.15
        and left["elev_mean"] > 115
        and right["elev_mean"] > 115
        and left["elev_amp"] >= 25
        and right["elev_amp"] >= 25
    ):
        return "move_ahead", 0.88

    # 5) SLOW DOWN: both arms extended near-horizontal, patting motion.
    if (
        f["crossed_frac"] < 0.15
        and 60 <= left["elev_mean"] <= 105
        and 60 <= right["elev_mean"] <= 105
        and 5 <= left["elev_amp"] <= 40
        and 5 <= right["elev_amp"] <= 40
        and f["dist_ratio_mean"] > 1.5
    ):
        return "slow_down", 0.85

    # 6) TURN (ICAO Annex 2, pilot's POV): marshaller's right arm
    # (image-left) extended static = TURN LEFT; mirror = TURN RIGHT.
    for static, moving, name in ((left, right, "turn_left"), (right, left, "turn_right")):
        if (
            _is_horizontal_static(static)
            and static["center_off"] > 0.15
            and moving["elev_amp"] >= 15
            and moving["elev_mean"] > 100
        ):
            return name, 0.87

    # Single-arm signals: exactly one arm hangs down static.
    for down, active in ((left, right), (right, left)):
        if not _is_down_static(down) or _is_down_static(active):
            continue
        # 7) ALL CLEAR: active arm straight up, static.
        if active["elev_mean"] > 140 and active["elev_amp"] < 12:
            return "all_clear", 0.9
        # 8) CUT ENGINES: horizontal sweep across the throat/neck line —
        # large x motion with flat y (elev amp is noisy near the shoulder,
        # so the y amplitude is the discriminating feature vs start_engines).
        if (
            active["wx_amp"] >= 0.08
            and active["wy_amp"] < 0.05
            and 80 <= active["elev_mean"] <= 125
            and active["center_off"] < 0.12
        ):
            return "cut_engines", 0.84
        # 9) START ENGINES: raised arm circular motion (x and y both move).
        if (
            90 <= active["elev_mean"] <= 150
            and active["wx_amp"] >= 0.05
            and active["wy_amp"] >= 0.05
        ):
            return "start_engines", 0.84

    return "unknown", 0.3
