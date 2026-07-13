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


def test_qnh_mismatch_is_high():
    # wrong altimeter setting is a level-bust precursor (FAA JO 7110.65)
    v = _verify(
        "KAF502, descend five thousand, QNH one zero one three",
        "Descend five thousand, QNH one zero one two, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "qnh")
    assert finding["type"] == "MISMATCH"
    assert finding["severity"] == "HIGH"


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
    assert v["status"] == "OK"  # QNH must not be misread as a callsign


def test_value_echo_counts_as_readback():
    # pilots read the value back without repeating the keyword
    v = _verify(
        "KAF502, turn left heading two seven zero, descend and maintain three thousand",
        "Left two seven zero, down to three thousand, KAF502",
    )
    assert v["status"] == "OK"
    echo = {f["slot"] for f in v["findings"] if f.get("echo")}
    assert "heading" in echo


def test_echo_does_not_apply_to_hold_short():
    # phrase slots still require the phrase — a bare runway number is not
    # an acceptable hold-short readback
    v = _verify(
        "KAF502, hold short of runway 27",
        "Runway 27, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "hold_short")
    assert finding["type"] == "MISSING_READBACK"
    assert finding["severity"] == "HIGH"


def test_crossing_readback_matches_cross_clearance():
    v = _verify(
        "KAF502, cross runway 27 at alpha",
        "Crossing runway 27 at alpha, KAF502",
    )
    assert v["status"] == "OK"


def test_single_letter_military_callsign_mismatch():
    # "F16" instructed vs "F14" read back must not pass verification
    v = _verify(
        "F16, taxi to runway 34 via alpha, hold short of runway 34",
        "Taxi to runway 34 via alpha, hold short of runway 34, F14",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "callsign")
    assert finding["type"] == "MISMATCH"
    assert finding["instructed"] == "F16"
    assert finding["readback"] == "F14"
    assert v["overall_severity"] == "HIGH"


def test_unexpected_value_safety_net():
    # a value the instruction never stated, outside any modelled slot
    # comparison, must still be surfaced
    v = _verify(
        "KAF502, taxi to runway 34",
        "Taxi to runway 34, squawk four five two one, KAF502",
    )
    finding = next(f for f in v["findings"] if f["type"] == "UNEXPECTED_VALUE")
    assert finding["readback"] == "4521"
    assert finding["severity"] == "MEDIUM"


def test_speed_mismatch_is_medium():
    v = _verify(
        "KAF502, reduce speed to one eight zero",
        "Reduce speed one six zero, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "speed")
    assert finding["type"] == "MISMATCH"
    assert finding["instructed"] == 180
    assert finding["readback"] == 160
    assert finding["severity"] == "MEDIUM"


def test_speed_value_echo():
    v = _verify(
        "KAF502, reduce speed to one eight zero",
        "One eight zero, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "speed")
    assert finding["type"] == "MATCH"
    assert v["status"] == "OK"


def test_callsign_omitted_from_readback():
    # a readback without the aircraft callsign is invalid (Doc 4444 4.5.7.5.2)
    v = _verify(
        "KAF502, descend and maintain five thousand",
        "Descend and maintain five thousand",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "callsign")
    assert finding["type"] == "MISSING_READBACK"
    assert finding["severity"] == "MEDIUM"
    assert v["status"] == "DISCREPANCY"


def test_takeoff_clearance_omission_is_high():
    # runway-entry clearances are mandatory readback items (Doc 9870)
    v = _verify(
        "KAF502, runway 36, cleared for takeoff",
        "Roger, KAF502",
    )
    missing = {
        f["slot"]: f["severity"]
        for f in v["findings"]
        if f["type"] == "MISSING_READBACK"
    }
    assert missing["clearance"] == "HIGH"
    assert missing["runway"] == "HIGH"
    assert v["overall_severity"] == "HIGH"


def test_wrong_alphanumeric_taxiway_detected():
    v = _verify(
        "KAF502, taxi to runway 36 via alpha one",
        "Taxi to runway 36 via alpha two, KAF502",
    )
    finding = next(f for f in v["findings"] if f["slot"] == "route")
    assert finding["type"] == "MISMATCH"
