from backend.audio.slots import extract_slots
from backend.fusion.verifier import verify_readback


def _verify(instruction: str, readback: str):
    return verify_readback(extract_slots(instruction), extract_slots(readback))


def test_perfect_readback():
    v = _verify(
        "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
        "Taxi to runway 36 via alpha, hold short of runway 36, KAF502",
    )
    assert v["status"] == "OK"
    assert v["overall_severity"] == "OK"


def test_runway_mismatch_is_critical():
    v = _verify(
        "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
        "Taxi to runway 34 via alpha, hold short of runway 34, KAF502",
    )
    assert v["status"] == "DISCREPANCY"
    assert v["overall_severity"] == "CRITICAL"
    mismatched = {f["slot"] for f in v["findings"] if f["type"] == "MISMATCH"}
    assert "runway" in mismatched
    assert "hold_short" in mismatched


def test_altitude_mismatch_is_high():
    v = _verify(
        "KAF502, descend and maintain five thousand",
        "Descend and maintain four thousand, KAF502",
    )
    assert v["overall_severity"] == "HIGH"
    finding = next(f for f in v["findings"] if f["slot"] == "altitude")
    assert finding["instructed"] == 5000
    assert finding["readback"] == 4000


def test_hold_short_omission_is_high():
    v = _verify(
        "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
        "Taxi to runway 36 via alpha, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "hold_short")
    assert finding["type"] == "MISSING_READBACK"
    assert finding["severity"] == "HIGH"


def test_spoken_vs_digits_equivalence():
    v = _verify(
        "KAF502, taxi to runway three six",
        "Taxi to runway 36, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "runway")
    assert finding["type"] == "MATCH"


def test_qnh_mismatch_is_medium():
    v = _verify(
        "KAF502, descend five thousand, QNH one zero one three",
        "Descend five thousand, QNH one zero one two, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "qnh")
    assert finding["type"] == "MISMATCH"
    assert finding["severity"] == "MEDIUM"


def test_unparseable_text_is_unverifiable_not_ok():
    v = _verify("hello nice weather today", "yes indeed it is")
    assert v["status"] == "UNVERIFIABLE"
    assert v["findings"] == []


def test_qnh_match():
    v = _verify(
        "KAF502, QNH one zero one three",
        "QNH one zero one three, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "qnh")
    assert finding["type"] == "MATCH"
