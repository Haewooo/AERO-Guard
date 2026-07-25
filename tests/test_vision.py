import pytest

from backend.vision.classifier import MIN_CONFIDENCE, SIGNALS, classify_window
from backend.vision.simulator import generate_sequence
from evaluation.corpus import with_noise


@pytest.mark.parametrize("signal", SIGNALS)
@pytest.mark.parametrize("seed", [1, 7, 42, 99, 123])
def test_all_signals_roundtrip(signal, seed):
    result = classify_window(generate_sequence(signal, seed=seed))
    assert result["signal"] == signal
    assert result["confidence"] > MIN_CONFIDENCE
    assert result["ai_assisted"] is True


def test_confidence_reflects_margin_not_a_constant():
    """The score must vary with the quality of the evidence.

    It used to be a per-signal constant, which read like a probability in
    the API and the HMI while carrying no information — every
    emergency_stop reported 0.9 whether the match was marginal or
    overwhelming. A degraded capture must now score lower than a clean one.
    """
    clean = classify_window(generate_sequence("stop", seed=1))
    degraded = [
        classify_window(with_noise(generate_sequence("stop", seed=s), 0.012, s))
        for s in range(8)
    ]
    still_stop = [r["confidence"] for r in degraded if r["signal"] == "stop"]
    assert still_stop, "noisy input should still classify"
    assert min(still_stop) < clean["confidence"]
    assert len({round(c, 3) for c in still_stop}) > 1


def test_unknown_carries_no_confidence():
    frames = generate_sequence("stop", seed=1)
    flat = [{k: [0.5, 0.5] for k in f} for f in frames]
    result = classify_window(flat)
    assert result["signal"] == "unknown"
    assert result["confidence"] == 0.0


def test_scale_invariance():
    """Joint-angle features must survive camera distance changes."""
    frames = generate_sequence("stop", seed=42)
    scaled = [
        {k: [0.5 + (v[0] - 0.5) * 0.6, 0.5 + (v[1] - 0.5) * 0.6] for k, v in f.items()}
        for f in frames
    ]
    assert classify_window(scaled)["signal"] == "stop"


def test_rejects_short_window():
    frames = generate_sequence("stop", n_frames=2)
    with pytest.raises(ValueError):
        classify_window(frames)


def test_rejects_missing_keypoint():
    frames = generate_sequence("stop")
    del frames[0]["l_wrist"]
    with pytest.raises(ValueError):
        classify_window(frames)
