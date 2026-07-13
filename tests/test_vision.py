import pytest

from backend.vision.classifier import SIGNALS, classify_window
from backend.vision.simulator import generate_sequence


@pytest.mark.parametrize("signal", SIGNALS)
@pytest.mark.parametrize("seed", [1, 7, 42, 99, 123])
def test_all_signals_roundtrip(signal, seed):
    result = classify_window(generate_sequence(signal, seed=seed))
    assert result["signal"] == signal
    assert result["confidence"] >= 0.8
    assert result["ai_assisted"] is True


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
