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


def test_qnh_not_mistaken_for_callsign():
    slots = extract_slots("QNH one zero one three, KAF502")
    assert slots["callsign"] == "KAF502"
    assert slots["qnh"] == "1013"


def test_holding_short_readback_form():
    slots = extract_slots("Holding short runway 27, KAF502")
    assert slots["hold_short"] == "27"


def test_crossing_readback_form():
    slots = extract_slots("Crossing runway 27, KAF502")
    assert slots["clearance"] == "cross"


def test_lining_up_readback_form():
    slots = extract_slots("Lining up runway 36, KAF502")
    assert slots["clearance"] == "line_up_wait"


def test_climb_to_phrasing():
    slots = extract_slots("KAF502, climb to five thousand")
    assert slots["altitude"] == 5000


def test_typed_taxiway_letter():
    slots = extract_slots("KAF502, taxi to runway 36 via A")
    assert slots["route"] == ["A"]


def test_alphanumeric_taxiway_joined():
    slots = extract_slots("KAF502, taxi to runway 36 via alpha one")
    assert slots["route"] == ["A1"]


def test_speed_keyword_form():
    slots = extract_slots("KAF502, reduce speed to one eight zero")
    assert slots["speed"] == 180
    assert slots["callsign"] == "KAF502"


def test_speed_knots_not_altitude():
    slots = extract_slots("KAF502, maintain two five zero knots")
    assert slots["speed"] == 250
    assert "altitude" not in slots


def test_wind_report_is_not_speed():
    slots = extract_slots(
        "KAF502, wind two seven zero at one zero knots, cleared to land runway 27"
    )
    assert "speed" not in slots
    assert slots["runway"] == "27"
    assert slots["clearance"] == "land"
