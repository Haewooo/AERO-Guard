"""Durability of safety state across a restart.

Losing runway occupancy does not fail loudly — health probes stay green
while the incursion rule silently stops firing — so these tests assert the
state actually survives a process lifecycle.
"""

import pytest
from fastapi.testclient import TestClient

from backend.audio.slots import extract_slots
from backend.config import settings
from backend.fusion.risk import RiskEngine
from backend.fusion.verifier import verify_readback
from backend.main import app
from backend.state import StateStore

HEADERS = {"X-API-Key": settings.api_key}


def _evaluate(engine, instruction, readback):
    i, r = extract_slots(instruction), extract_slots(readback)
    return engine.evaluate_comms(i, r, verify_readback(i, r))


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "state.db"))
    yield s
    s.close()


def test_occupancy_survives_engine_restart(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    engine = RiskEngine(store=store)
    engine.set_occupancy("36", "KAF999")
    store.close()

    reopened = StateStore(db)
    restored = RiskEngine(store=reopened)
    assert restored.get_occupancy() == {"36": "KAF999"}
    reopened.close()


def test_cleared_occupancy_stays_cleared(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    engine = RiskEngine(store=store)
    engine.set_occupancy("36", "KAF999")
    engine.set_occupancy("36", None)
    store.close()

    reopened = StateStore(db)
    assert RiskEngine(store=reopened).get_occupancy() == {}
    reopened.close()


def test_alerts_and_acknowledgements_survive_restart(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    engine = RiskEngine(store=store)
    engine.set_occupancy("36", "KAF999")
    alerts = _evaluate(
        engine,
        "KAF502, runway 36, cleared for takeoff",
        "Runway 36, cleared for takeoff, KAF502",
    )
    engine.acknowledge(alerts[0]["id"], "twr-1")
    store.close()

    reopened = StateStore(db)
    restored = RiskEngine(store=reopened)
    recovered = {a["id"]: a for a in restored.recent_alerts(100)}
    assert set(recovered) == {a["id"] for a in alerts}
    assert recovered[alerts[0]["id"]]["acknowledged"] is True
    assert recovered[alerts[0]["id"]]["acknowledged_by"] == "twr-1"
    reopened.close()


def test_incursion_rule_still_fires_after_restart(tmp_path):
    """The regression that matters: occupancy loss disables the rule."""
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    RiskEngine(store=store).set_occupancy("36", "KAF999")
    store.close()

    reopened = StateStore(db)
    engine = RiskEngine(store=reopened)
    alerts = _evaluate(
        engine,
        "KAF502, runway 36, cleared for takeoff",
        "Runway 36, cleared for takeoff, KAF502",
    )
    assert [a for a in alerts if a["type"] == "RUNWAY_INCURSION"]
    reopened.close()


def test_stored_alerts_are_trimmed_to_the_window(tmp_path):
    db = str(tmp_path / "state.db")
    store = StateStore(db)
    engine = RiskEngine(max_alerts=5, store=store)
    for _ in range(12):
        _evaluate(
            engine,
            "KAF502, taxi to runway 36",
            "Taxi to runway 34, KAF502",
        )
    assert len(store.load_alerts(100)) == 5
    store.close()


def test_engine_without_a_store_still_works():
    """Unit tests and embedded uses construct RiskEngine with no store."""
    engine = RiskEngine()
    engine.set_occupancy("36", "KAF999")
    assert engine.get_occupancy() == {"36": "KAF999"}


def test_occupancy_survives_app_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "app.db"))
    with TestClient(app) as c:
        c.post(
            "/api/runway/occupancy", headers=HEADERS,
            json={"runway": "36", "callsign": "KAF999"},
        )
    with TestClient(app) as c:  # lifespan re-run == process restart
        occupancy = c.get("/api/runway/occupancy", headers=HEADERS).json()["occupancy"]
    assert occupancy == {"36": "KAF999"}
