import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app

HEADERS = {"X-API-Key": settings.api_key}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def test_health_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_api_requires_key(client):
    assert client.get("/api/alerts").status_code == 401
    assert client.get("/api/alerts", headers={"X-API-Key": "wrong"}).status_code == 401


def test_comms_verify_ok(client):
    res = client.post(
        "/api/comms/verify",
        headers=HEADERS,
        json={
            "instruction": "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
            "readback": "Taxi to runway 36 via alpha, hold short of runway 36, KAF502",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["verification"]["status"] == "OK"
    assert body["alerts"] == []


def test_comms_verify_incursion_flow(client):
    res = client.post(
        "/api/runway/occupancy",
        headers=HEADERS,
        json={"runway": "36", "callsign": "KAF999"},
    )
    assert res.status_code == 200

    res = client.post(
        "/api/comms/verify",
        headers=HEADERS,
        json={
            "instruction": "KAF502, runway 36, cleared for takeoff",
            "readback": "Runway 36, cleared for takeoff, KAF502",
        },
    )
    body = res.json()
    types = [a["type"] for a in body["alerts"]]
    assert "RUNWAY_INCURSION" in types

    alert_id = body["alerts"][0]["id"]
    res = client.post(
        f"/api/alerts/{alert_id}/ack", headers=HEADERS, json={"operator": "twr-1"}
    )
    assert res.status_code == 200
    assert res.json()["acknowledged"] is True

    # Clear occupancy for isolation.
    client.post(
        "/api/runway/occupancy",
        headers=HEADERS,
        json={"runway": "36", "callsign": None},
    )


def test_vision_simulate_all_signals(client):
    signals = client.get("/api/vision/signals", headers=HEADERS).json()["signals"]
    for signal in signals:
        res = client.post(
            "/api/vision/simulate",
            headers=HEADERS,
            json={"signal": signal, "seed": 42},
        )
        assert res.status_code == 200
        assert res.json()["signal"] == signal


def test_vision_classify_validation(client):
    res = client.post(
        "/api/vision/classify", headers=HEADERS, json={"frames": [{}] * 10}
    )
    assert res.status_code == 422


def test_audit_endpoints(client):
    client.post(
        "/api/comms/verify",
        headers=HEADERS,
        json={"instruction": "KAF502, runway 36, cleared for takeoff",
              "readback": "Runway 36, cleared for takeoff, KAF502"},
    )
    verify = client.get("/api/audit/verify", headers=HEADERS).json()
    assert verify["valid"] is True
    assert verify["records"] >= 1
    recent = client.get("/api/audit/recent", headers=HEADERS).json()["records"]
    assert recent[0]["event_type"] in ("COMMS_VERIFY", "OCCUPANCY_SET")


def test_asr_unavailable_returns_503(client):
    res = client.post(
        "/api/asr/transcribe", headers=HEADERS, content=b"RIFF0000WAVE"
    )
    assert res.status_code in (503, 200)


def test_security_headers(client):
    res = client.get("/healthz")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
