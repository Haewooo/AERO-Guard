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

from typing import Any, NamedTuple

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


# Confidence floor for a rule that only just clears its thresholds. A
# decision sitting exactly on a boundary is a real decision, but it is not
# a strong one, and the HMI/risk engine need to be able to tell.
MIN_CONFIDENCE = 0.5


class Cond(NamedTuple):
    """One rule condition and how the observed window measured against it.

    Carrying the measurement, not just pass/fail, is what makes a
    non-recognition diagnosable: "unknown" alone gives an operator nothing
    to act on, whereas "emergency_stop needed r.elev_amp >= 25, saw 18.4"
    says which way to change the gesture — or which threshold is wrong for
    real cameras, since every threshold here was set against synthetic data.
    """

    label: str
    value: float
    test: str
    margin: float | None   # None when the condition is not satisfied

    @property
    def ok(self) -> bool:
        return self.margin is not None


def _margin(slack: float, scale: float) -> float | None:
    """Normalised distance past a threshold, or None if the test fails.

    `scale` is how far past the threshold counts as unambiguous, chosen
    per feature from its observed spread.
    """
    if slack < 0:
        return None
    return min(1.0, slack / scale)


def _gt(label: str, value: float, threshold: float, scale: float) -> Cond:
    return Cond(label, value, f">= {threshold:g}", _margin(value - threshold, scale))


def _lt(label: str, value: float, threshold: float, scale: float) -> Cond:
    return Cond(label, value, f"<= {threshold:g}", _margin(threshold - value, scale))


def _between(label: str, value: float, lo: float, hi: float, scale: float) -> Cond:
    return Cond(
        label, value, f"{lo:g}..{hi:g}", _margin(min(value - lo, hi - value), scale)
    )


def _score(*conditions: Cond) -> float | None:
    """Rule confidence: the weakest condition's margin.

    Replaces the per-signal constants the classifier used to report. Those
    were indistinguishable from a probability in the API and the HMI while
    carrying no information at all — every emergency_stop was 0.9 whether
    the evidence was overwhelming or marginal.
    """
    if any(not c.ok for c in conditions):
        return None
    return round(
        MIN_CONFIDENCE + (1.0 - MIN_CONFIDENCE) * min(c.margin for c in conditions), 3
    )


def _shortfall(conditions: tuple[Cond, ...]) -> float:
    """How far a rule is from firing, for ranking near-misses."""
    failed = [c for c in conditions if not c.ok]
    if not failed:
        return 0.0
    return len(failed) + sum(
        min(1.0, abs(c.value - _threshold_of(c)) / max(abs(_threshold_of(c)), 1e-6))
        for c in failed
    )


def _threshold_of(c: Cond) -> float:
    body = c.test.replace(">= ", "").replace("<= ", "")
    if ".." in body:
        lo, hi = (float(x) for x in body.split(".."))
        return lo if c.value < lo else hi
    return float(body)


def _arm(f: dict[str, float], side: str) -> dict[str, float]:
    return {
        "ext_mean": f[f"{side}_ext_mean"],
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


def _explain(name: str, conditions: tuple[Cond, ...]) -> dict[str, Any]:
    return {
        "signal": name,
        "satisfied": sum(1 for c in conditions if c.ok),
        "total": len(conditions),
        "failed": [
            {"feature": c.label, "measured": round(c.value, 3), "needs": c.test}
            for c in conditions
            if not c.ok
        ],
    }


def classify_window(frames: list[dict[str, Any]]) -> dict[str, Any]:
    f = window_features(frames)
    left, right = _arm(f, "l"), _arm(f, "r")
    signal, confidence, near_misses = _decide(f, left, right)
    return {
        "signal": signal,
        "label": SIGNAL_LABELS.get(signal, "Unknown"),
        "confidence": confidence,
        "features": {k: round(v, 3) for k, v in f.items()},
        # Present only when nothing matched: the rules that came closest,
        # with the measurement that fell short of each threshold.
        "near_misses": near_misses,
        "ai_assisted": True,
    }


def _decide(
    f: dict[str, float], left: dict[str, float], right: dict[str, float]
) -> tuple[str, float, list[dict[str, Any]]]:
    """Return (signal, confidence, near misses).

    Every rule is evaluated, not just up to the first match, so a window
    that matched nothing can still report which rules came closest and by
    how much. That is the only way to tell a gesture performed badly from a
    threshold that was set against synthetic data and does not survive a
    real camera.
    """
    tried: list[tuple[str, float | None, tuple[Cond, ...]]] = []

    def rule(name: str, *conditions: Cond) -> float | None:
        outcome = _score(*conditions)
        tried.append((name, outcome, conditions))
        return outcome

    # 1) STOP: wrists crossed above head, static.
    score = rule("stop",
        _gt("crossed_frac", f["crossed_frac"], 0.5, 0.3),
        _gt("above_head_frac", f["above_head_frac"], 0.5, 0.3),
        _lt("max_elev_amp", max(left["elev_amp"], right["elev_amp"]), 15, 10),
    )
    if score:
        return "stop", score, []

    # 2) EMERGENCY STOP: oscillation passing through crossed-overhead.
    # The 40 degree amplitude this used to demand was read off the
    # simulator, whose gesture sweeps ~72 degrees. A real marshaller
    # crossing wands overhead measures 27-44, so the rule could not fire on
    # an actual person. Fail-safe direction: a missed emergency stop is
    # worse than a takeover the operator dismisses, and gating already
    # collapses a sustained false positive to a single alert.
    score = rule("emergency_stop",
        _gt("crossed_frac", f["crossed_frac"], 0.15, 0.35),
        _gt("l.elev_amp", left["elev_amp"], 25, 40),
        _gt("r.elev_amp", right["elev_amp"], 25, 40),
    )
    if score:
        return "emergency_stop", score, []

    # 3) CHOCKS (ICAO: arms fully extended ABOVE HEAD, wands moving
    # inward until touching = inserted / outward = removed).
    score = rule("chocks",
        _gt("above_head_frac", f["above_head_frac"], 0.5, 0.3),
        _lt("crossed_frac", f["crossed_frac"], 0.15, 0.1),
        _gt("l.elev_mean", left["elev_mean"], 140, 20),
        _gt("r.elev_mean", right["elev_mean"], 140, 20),
        _gt("dist_ratio_amp", f["dist_ratio_amp"], 0.43, 0.5),
    )
    if score:
        if f["dist_ratio_mean"] < 1.0:
            return "chocks_inserted", score, []
        return "chocks_removed", score, []

    # 4) MOVE AHEAD: both arms raised, symmetric beckoning, never crossed.
    score = rule("move_ahead",
        _lt("crossed_frac", f["crossed_frac"], 0.15, 0.1),
        _gt("l.elev_mean", left["elev_mean"], 115, 25),
        _gt("r.elev_mean", right["elev_mean"], 115, 25),
        _gt("l.elev_amp", left["elev_amp"], 21, 20),
        _gt("r.elev_amp", right["elev_amp"], 21, 20),
    )
    if score:
        return "move_ahead", score, []

    # Amplitude lower bounds below are stated for the 10th-90th percentile
    # spread used since angles.py switched off max-min; that statistic runs
    # ~0.86x of the old one for the same motion, so the thresholds were
    # scaled to match or the motion rules would have silently tightened.
    # 5) SLOW DOWN: both arms extended near-horizontal, patting motion.
    score = rule("slow_down",
        _lt("crossed_frac", f["crossed_frac"], 0.15, 0.1),
        _between("l.elev_mean", left["elev_mean"], 60, 105, 20),
        _between("r.elev_mean", right["elev_mean"], 60, 105, 20),
        _between("l.elev_amp", left["elev_amp"], 5, 40, 15),
        _between("r.elev_amp", right["elev_amp"], 5, 40, 15),
        _gt("dist_ratio_mean", f["dist_ratio_mean"], 1.5, 0.5),
    )
    if score:
        return "slow_down", score, []

    # 6) TURN (ICAO Annex 2, pilot's POV): marshaller's right arm
    # (image-left) extended static = TURN LEFT; mirror = TURN RIGHT.
    for static, moving, name in ((left, right, "turn_left"), (right, left, "turn_right")):
        score = rule("turn",
            _between("static.elev_mean", static["elev_mean"], 70, 105, 15),
            _lt("static.elev_amp", static["elev_amp"], 12, 8),
            _gt("static.center_off", static["center_off"], 0.15, 0.1),
            _gt("moving.elev_amp", moving["elev_amp"], 13, 20),
            _gt("moving.elev_mean", moving["elev_mean"], 100, 30),
        )
        if score:
            return name, score, []

    # Single-arm signals: exactly one arm hangs down static.
    for down, active in ((left, right), (right, left)):
        if not _is_down_static(down) or _is_down_static(active):
            continue
        # 7) ALL CLEAR: active arm straight up, static, and fully
        # extended — without the extension test a hand held at the head
        # (phone, headset) points as steeply up as a raised arm does.
        score = rule("all_clear",
            _gt("active.elev_mean", active["elev_mean"], 140, 25),
            _lt("active.elev_amp", active["elev_amp"], 12, 8),
            _gt("active.ext_mean", active["ext_mean"], 1.05, 0.3),
        )
        if score:
            return "all_clear", score, []
        # 8) CUT ENGINES: horizontal sweep across the throat/neck line —
        # large x motion with flat y (elev amp is noisy near the shoulder,
        # so the y amplitude is the discriminating feature vs start_engines).
        score = rule("cut_engines",
            _gt("active.wx_amp", active["wx_amp"], 0.07, 0.08),
            _lt("active.wy_amp", active["wy_amp"], 0.05, 0.03),
            _between("active.elev_mean", active["elev_mean"], 80, 125, 15),
            _lt("active.center_off", active["center_off"], 0.12, 0.08),
        )
        if score:
            return "cut_engines", score, []
        # 9) START ENGINES: raised arm circular motion (x and y both move).
        score = rule("start_engines",
            _between("active.elev_mean", active["elev_mean"], 90, 150, 20),
            _gt("active.wx_amp", active["wx_amp"], 0.043, 0.05),
            _gt("active.wy_amp", active["wy_amp"], 0.043, 0.05),
        )
        if score:
            return "start_engines", score, []

    # Nothing fired. Report the closest rules so the miss is actionable.
    near = sorted((t for t in tried if t[1] is None), key=lambda t: _shortfall(t[2]))
    return "unknown", 0.0, [_explain(n, c) for n, _, c in near[:3]]
