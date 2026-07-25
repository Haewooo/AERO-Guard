from backend.audio.slots import extract_slots
from backend.fusion.risk import RiskEngine
from backend.fusion.verifier import verify_readback


def _evaluate(engine: RiskEngine, instruction: str, readback: str):
    i, r = extract_slots(instruction), extract_slots(readback)
    return engine.evaluate_comms(i, r, verify_readback(i, r))


def test_runway_incursion_alert():
    engine = RiskEngine()
    engine.set_occupancy("36", "KAF999")
    alerts = _evaluate(
        engine,
        "KAF502, runway 36, cleared for takeoff",
        "Runway 36, cleared for takeoff, KAF502",
    )
    incursions = [a for a in alerts if a["type"] == "RUNWAY_INCURSION"]
    assert len(incursions) == 1
    assert incursions[0]["priority"] == 100
    assert incursions[0]["severity"] == "CRITICAL"


def test_no_incursion_when_runway_clear():
    engine = RiskEngine()
    alerts = _evaluate(
        engine,
        "KAF502, runway 36, cleared for takeoff",
        "Runway 36, cleared for takeoff, KAF502",
    )
    assert not [a for a in alerts if a["type"] == "RUNWAY_INCURSION"]


def test_own_aircraft_occupancy_is_not_incursion():
    engine = RiskEngine()
    engine.set_occupancy("36", "KAF502")
    alerts = _evaluate(
        engine,
        "KAF502, runway 36, cleared for takeoff",
        "Runway 36, cleared for takeoff, KAF502",
    )
    assert not [a for a in alerts if a["type"] == "RUNWAY_INCURSION"]


def test_mismatch_escalated_when_runway_occupied():
    engine = RiskEngine()
    engine.set_occupancy("36", "KAF999")
    alerts = _evaluate(
        engine,
        "KAF502, taxi to runway 36",
        "Taxi to runway 34, KAF502",
    )
    mismatch = next(a for a in alerts if a["type"] == "READBACK_MISMATCH")
    assert mismatch["severity"] == "CRITICAL"
    assert mismatch["details"]["escalated"] is True


def test_acknowledge_flow():
    engine = RiskEngine()
    engine.set_occupancy("36", "KAF999")
    alerts = _evaluate(
        engine,
        "KAF502, runway 36, cleared for takeoff",
        "Runway 36, cleared for takeoff, KAF502",
    )
    acked = engine.acknowledge(alerts[0]["id"], "controller-1")
    assert acked["acknowledged"] is True
    assert acked["acknowledged_by"] == "controller-1"


def test_emergency_stop_signal_alert():
    engine = RiskEngine(signal_confirmations=2, signal_release_windows=3)
    emergency = {"signal": "emergency_stop", "confidence": 0.9}
    # Fires on the first window: a delayed emergency stop is worse than a
    # dismissed one. It then latches, so it cannot repeat.
    alert = engine.evaluate_signal(emergency)
    assert alert is not None and alert["severity"] == "CRITICAL"
    assert engine.evaluate_signal(emergency) is None
    assert engine.evaluate_signal({"signal": "move_ahead", "confidence": 0.9}) is None
