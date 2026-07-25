"""Measured bounds on classifier behaviour.

These assert the numbers in evaluation/REPORT.md do not silently regress.
They are deliberately loose where the classifier is known to be weak — the
point is to notice a change, and to keep the weaknesses visible in the
test suite rather than only in a report nobody regenerates.

The corpus is synthetic (see evaluation/corpus.py); nothing here is a
claim about field accuracy.
"""

import pytest

from backend.fusion.risk import RiskEngine
from backend.vision.classifier import classify_window
from evaluation import corpus

SEEDS = range(20)


def _accuracy(transform=lambda frames, seed: frames):
    cases = list(corpus.signal_cases(SEEDS))
    hits = sum(
        classify_window(transform(frames, i))["signal"] == truth
        for i, (truth, frames) in enumerate(cases)
    )
    return hits / len(cases)


def test_in_distribution_accuracy_is_total():
    assert _accuracy() == 1.0


@pytest.mark.parametrize(
    "sigma,floor",
    [
        (0.004, 0.99),   # the generator's own noise level
        (0.010, 0.95),   # realistic MediaPipe jitter
        (0.015, 0.90),
        (0.020, 0.80),
    ],
)
def test_accuracy_under_landmark_noise(sigma, floor):
    """Documents degradation rather than pretending it does not exist."""
    accuracy = _accuracy(lambda f, seed: corpus.with_noise(f, sigma, seed))
    assert accuracy >= floor, f"accuracy {accuracy:.2f} fell below {floor}"


def test_off_axis_capture_is_tolerated():
    assert _accuracy(lambda f, _: corpus.off_axis(f, 0.7)) >= 0.95


def test_tracker_dropouts_are_tolerated():
    assert _accuracy(lambda f, seed: corpus.dropped_frames(f, 0.25, seed)) >= 0.95


def test_false_signal_rate_on_non_marshalling_motion():
    cases = list(corpus.non_signal_cases(SEEDS))
    false_positives = [
        (kind, classify_window(frames)["signal"])
        for kind, frames in cases
        if classify_window(frames)["signal"] != "unknown"
    ]
    rate = len(false_positives) / len(cases)
    assert rate <= 0.10, f"false-signal rate regressed to {rate:.0%}"

    # The known offenders. A new motion type appearing here is a
    # regression worth looking at, not a threshold to raise.
    offenders = {kind for kind, _ in false_positives}
    assert offenders <= {"both_arms_flagging"}


def test_gating_collapses_a_sustained_false_positive_to_one_alert():
    """Two-arm flagging reads as emergency_stop on every window.

    Gating cannot make that classification correct — the operator still
    gets one spurious takeover — but it must stop the takeover repeating
    for as long as the person keeps waving.
    """
    engine = RiskEngine(signal_confirmations=2, signal_release_windows=3)
    for seed in range(30):
        frames = corpus.non_signal_sequence("both_arms_flagging", seed)
        engine.evaluate_signal(classify_window(frames))
    assert len(engine.recent_alerts(100)) <= 1


def test_emergency_stop_confidence_is_not_overstated():
    """Its rule is only marginally satisfied even by data tuned to it."""
    scores = [
        classify_window(frames)["confidence"]
        for truth, frames in corpus.signal_cases(SEEDS)
        if truth == "emergency_stop"
    ]
    assert max(scores) < 0.75, "score should reflect the thin margin"
