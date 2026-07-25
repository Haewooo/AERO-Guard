"""Metrics, request correlation, structured logs, audit retention."""

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from backend.audit import AuditLog
from backend.config import settings
from backend.main import app
from backend.observability import JsonFormatter, Metrics, request_id_var, route_of

HEADERS = {"X-API-Key": settings.api_key}
KEY = b"unit-test-audit-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


# ── request correlation ─────────────────────────────────────────────
def test_every_response_carries_a_request_id(client):
    assert client.get("/healthz").headers["X-Request-ID"]


def test_supplied_request_id_is_echoed(client):
    """Lets an operator quote one id across proxy, service and audit."""
    res = client.get("/healthz", headers={"X-Request-ID": "trace-me-123"})
    assert res.headers["X-Request-ID"] == "trace-me-123"


def test_request_ids_differ_between_requests(client):
    first = client.get("/healthz").headers["X-Request-ID"]
    second = client.get("/healthz").headers["X-Request-ID"]
    assert first != second


# ── structured logs ─────────────────────────────────────────────────
def test_json_formatter_emits_parseable_records():
    token = request_id_var.set("abc123")
    try:
        record = logging.LogRecord(
            "aeroguard", logging.WARNING, __file__, 1, "denied", None, None
        )
        record.status = 401
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["message"] == "denied"
    assert payload["level"] == "WARNING"
    assert payload["request_id"] == "abc123"
    assert payload["status"] == 401
    assert payload["ts"].endswith("Z")


def test_json_formatter_records_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            "aeroguard", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


# ── metrics ─────────────────────────────────────────────────────────
def test_metrics_endpoint_is_prometheus_text(client):
    client.get("/api/alerts", headers=HEADERS)
    body = client.get("/metrics").text
    assert "# TYPE aeroguard_requests_total counter" in body
    assert 'aeroguard_requests_total{route="/api/alerts",status="200"}' in body
    assert "aeroguard_request_duration_seconds_bucket" in body
    assert "aeroguard_audit_chain_valid 1" in body


def test_metrics_counts_auth_and_rate_limit_rejections(client):
    client.get("/api/alerts", headers={"X-API-Key": "wrong"})
    body = client.get("/metrics").text
    assert "aeroguard_auth_failures_total" in body
    assert 'aeroguard_requests_total{route="/api/alerts",status="401"}' in body


def _counter(body, name):
    for line in body.splitlines():
        if line.startswith(name):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_metrics_tracks_alerts_and_occupancy(client):
    # Counters are process-global by design, so assert on the delta rather
    # than an absolute value another test may already have moved.
    label = 'aeroguard_alerts_total{severity="CRITICAL",type="RUNWAY_INCURSION"}'
    before = _counter(client.get("/metrics").text, label)
    client.post(
        "/api/runway/occupancy", headers=HEADERS,
        json={"runway": "36", "callsign": "KAF999"},
    )
    client.post(
        "/api/comms/verify", headers=HEADERS,
        json={"instruction": "KAF502, runway 36, cleared for takeoff",
              "readback": "Runway 36, cleared for takeoff, KAF502"},
    )
    body = client.get("/metrics").text
    assert _counter(body, label) == before + 1
    assert "aeroguard_runway_occupied 1" in body


def test_route_labels_do_not_explode_cardinality():
    """Alert ids in the path must not become distinct metric series."""
    assert route_of("/api/alerts/abc123/ack") == "/api/alerts/{id}/ack"
    assert route_of("/api/alerts/def456/ack") == "/api/alerts/{id}/ack"
    assert route_of("/style.css") == "static"


def test_histogram_buckets_are_cumulative():
    m = Metrics()
    for seconds in (0.001, 0.02, 0.3, 7.0):
        m.observe("lat", seconds)
    body = m.render()
    assert 'lat_bucket{le="0.005"} 1' in body
    assert 'lat_bucket{le="0.05"} 2' in body
    assert 'lat_bucket{le="0.5"} 3' in body
    assert 'lat_bucket{le="+Inf"} 4' in body
    assert "lat_count 4" in body


# ── audit retention ─────────────────────────────────────────────────
def _log(tmp_path):
    return AuditLog(
        str(tmp_path / "audit.db"), key=KEY,
        anchor_path=str(tmp_path / "anchors.log"),
    )


def _append_aged(log, monkeypatch, count, days_ago, start=0):
    """Write records that are genuinely old.

    Back-dating with an UPDATE would not work: ts is part of the hashed
    material, so rewriting it breaks the chain — which is the point of the
    chain.
    """
    import backend.audit as audit_mod

    stamp = time.time() - days_ago * 86400
    monkeypatch.setattr(audit_mod.time, "time", lambda: stamp)
    try:
        for i in range(count):
            log.append("tester", "EVENT", {"n": start + i})
    finally:
        monkeypatch.undo()


def test_prune_drops_old_records_and_keeps_the_chain_valid(tmp_path, monkeypatch):
    log = _log(tmp_path)
    _append_aged(log, monkeypatch, 12, days_ago=100)
    for i in range(8):
        log.append("tester", "EVENT", {"n": 100 + i})

    result = log.prune(retention_days=90)
    assert result["pruned"] == 12
    assert result["retained"] == 8
    verified = log.verify_chain()
    assert verified["valid"] is True, verified
    assert verified["records"] == 8
    assert verified["anchors"]["valid"] is True
    log.close()


def test_pruned_log_keeps_accepting_and_verifying_records(tmp_path, monkeypatch):
    log = _log(tmp_path)
    _append_aged(log, monkeypatch, 6, days_ago=30)
    for i in range(4):
        log.append("tester", "EVENT", {"n": 100 + i})
    log.prune(retention_days=1)
    for i in range(5):
        log.append("tester", "EVENT", {"n": 200 + i})
    assert log.verify_chain()["valid"] is True
    log.close()


def test_prune_survives_a_reopen(tmp_path, monkeypatch):
    """The prune marker lives in the anchor log, so a fresh process must
    still accept the truncated chain."""
    log = _log(tmp_path)
    _append_aged(log, monkeypatch, 10, days_ago=200)
    for i in range(3):
        log.append("tester", "EVENT", {"n": 100 + i})
    log.prune(retention_days=90)
    log.close()

    reopened = _log(tmp_path)
    assert reopened.verify_chain()["valid"] is True
    reopened.close()


def test_truncation_without_a_prune_marker_is_still_detected(tmp_path):
    """Retention must not become a laundering route for deleting history."""
    import sqlite3

    db = str(tmp_path / "audit.db")
    log = _log(tmp_path)
    for i in range(10):
        log.append("tester", "EVENT", {"n": i})
    log.close()

    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER audit_no_delete")
    con.execute("DELETE FROM audit WHERE id <= 5")
    con.commit()
    con.close()

    log = _log(tmp_path)
    assert log.verify_chain()["valid"] is False
    log.close()


def test_retention_disabled_keeps_everything(tmp_path):
    log = _log(tmp_path)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    assert log.prune(retention_days=0)["pruned"] == 0
    assert log.verify_chain()["records"] == 5
    log.close()
