from backend.audio.slots import extract_slots


def test_taxi_instruction_full():
    slots = extract_slots(
        "KAF502, taxi to runway 36 via alpha, hold short of runway 36"
    )
    assert slots["callsign"] == "KAF502"
    assert slots["clearance"] == "taxi"
    assert slots["taxi_to"] == "36"
    assert slots["runway"] == "36"
    assert slots["route"] == ["A"]
    assert slots["hold_short"] == "36"


def test_takeoff_clearance():
    slots = extract_slots("KAF502, runway 36, cleared for takeoff")
    assert slots["clearance"] == "takeoff"
    assert slots["runway"] == "36"


def test_spoken_numbers_altitude():
    slots = extract_slots("KAF502, descend and maintain five thousand")
    assert slots["altitude"] == 5000


def test_flight_level():
    slots = extract_slots("KAF502, climb flight level three five zero")
    assert slots["altitude"] == 35000


def test_heading_padded():
    slots = extract_slots("KAF502, fly heading zero niner zero")
    assert slots["heading"] == "090"


def test_frequency_and_squawk():
    slots = extract_slots(
        "KAF502, contact tower one one eight decimal seven, squawk four two one five"
    )
    assert slots["frequency"] == "118.7"
    assert slots["squawk"] == "4215"


def test_runway_with_side():
    slots = extract_slots("KAF502, cleared to land runway 27 left")
    assert slots["runway"] == "27L"
    assert slots["clearance"] == "land"


def test_readback_trailing_callsign():
    slots = extract_slots("Taxi to runway 34 via alpha, KAF502")
    assert slots["callsign"] == "KAF502"
    assert slots["runway"] == "34"


def test_line_up_and_wait():
    slots = extract_slots("KAF502, runway 36, line up and wait")
    assert slots["clearance"] == "line_up_wait"


def test_qnh_spoken():
    slots = extract_slots("KAF502, descend five thousand, QNH one zero one three")
    assert slots["qnh"] == "1013"
    assert slots["altitude"] == 5000


def test_altimeter_keyword():
    slots = extract_slots("KAF502, altimeter two niner niner two")
    assert slots["qnh"] == "2992"
