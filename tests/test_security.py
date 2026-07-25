"""Authentication, operator attribution, client identification, rate limits."""

import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings, settings
from backend.main import app
from backend.security import ClientResolver, KeyRegistry, TokenBucket

HEADERS = {"X-API-Key": settings.api_key}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


# ── key comparison must never raise ─────────────────────────────────
@pytest.mark.parametrize(
    "key",
    [
        "ké",                  # non-ASCII: hmac.compare_digest rejects str inputs
        "ÿ" * 40,
        "🛬",
        "",
        "a" * 10_000,
    ],
)
def test_hostile_keys_are_rejected_not_crashed(key):
    """A key the comparison cannot handle must be a 401, never a 500."""
    registry = KeyRegistry({"console": "correct-key"})
    assert registry.identify(key) is None


def test_non_ascii_key_returns_401_over_http(client):
    # httpx encodes headers as latin-1, the same as an attacker's raw bytes.
    res = client.get("/api/alerts", headers={"X-API-Key": "kéy".encode("latin-1")})
    assert res.status_code == 401


def test_non_ascii_key_closes_websocket_cleanly(client):
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws?api_key=k%C3%A9"),
    ):
        pass
    assert exc.value.code == 4401


def test_valid_websocket_key_connects(client):
    # Base64 keys contain '+' and '=', which are query-string metacharacters
    # — the HMI percent-encodes them and so must anything else.
    key = quote(settings.api_key, safe="")
    with client.websocket_connect(f"/ws?api_key={key}") as ws:
        assert ws is not None


# ── operator identity is server-derived ─────────────────────────────
def test_audit_actor_ignores_client_supplied_operator(client):
    res = client.post(
        "/api/comms/verify",
        headers=HEADERS,
        json={
            "instruction": "KAF502, runway 36, cleared for takeoff",
            "readback": "Runway 36, cleared for takeoff, KAF502",
            "operator": "GEN. ANYONE-I-WANT",
        },
    )
    assert res.status_code == 200
    record = client.get("/api/audit/recent", headers=HEADERS).json()["records"][0]
    assert record["actor"] == settings.operator_name
    assert record["actor"] != "GEN. ANYONE-I-WANT"


def test_ack_records_the_authenticated_operator(client):
    client.post(
        "/api/runway/occupancy", headers=HEADERS,
        json={"runway": "36", "callsign": "KAF999"},
    )
    alerts = client.post(
        "/api/comms/verify", headers=HEADERS,
        json={"instruction": "KAF502, runway 36, cleared for takeoff",
              "readback": "Runway 36, cleared for takeoff, KAF502"},
    ).json()["alerts"]
    acked = client.post(
        f"/api/alerts/{alerts[0]['id']}/ack",
        headers=HEADERS,
        json={"operator": "someone-else"},   # ignored
    ).json()
    assert acked["acknowledged_by"] == settings.operator_name


def test_per_operator_keys_map_to_distinct_identities():
    resolved = Settings(api_keys="twr-1:KEY_ONE,gnd-2:KEY_TWO").resolve_keys()
    registry = KeyRegistry(resolved)
    assert registry.identify("KEY_ONE") == "twr-1"
    assert registry.identify("KEY_TWO") == "gnd-2"
    assert registry.identify("KEY_THREE") is None


@pytest.mark.parametrize("spec", ["nokey", "  :  ", ":justkey", "name:"])
def test_malformed_key_map_is_rejected(spec):
    with pytest.raises(ValueError):
        Settings(api_keys=spec).resolve_keys()


# ── X-Forwarded-For is only honoured for trusted proxies ────────────
class _Req:
    def __init__(self, peer, forwarded=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


def test_forwarded_header_ignored_from_untrusted_peer():
    resolver = ClientResolver("")
    assert resolver.client_ip(_Req("10.0.0.5", "203.0.113.9")) == "10.0.0.5"


def test_forwarded_header_honoured_from_trusted_proxy():
    resolver = ClientResolver("10.0.0.0/8")
    assert resolver.client_ip(_Req("10.0.0.5", "203.0.113.9")) == "203.0.113.9"


def test_spoofed_hops_left_of_the_proxy_chain_are_discarded():
    """A client that sends its own X-Forwarded-For must not choose its
    own rate-limit bucket."""
    resolver = ClientResolver("10.0.0.0/8")
    ip = resolver.client_ip(_Req("10.0.0.5", "1.2.3.4, 203.0.113.9"))
    assert ip == "203.0.113.9"


def test_chained_trusted_proxies_resolve_to_the_real_client():
    resolver = ClientResolver("10.0.0.0/8,192.168.0.0/16")
    ip = resolver.client_ip(_Req("10.0.0.5", "203.0.113.9, 192.168.1.7"))
    assert ip == "203.0.113.9"


def test_invalid_trusted_proxy_entries_are_ignored():
    resolver = ClientResolver("not-an-ip, 10.0.0.0/8")
    assert resolver.client_ip(_Req("10.0.0.5", "203.0.113.9")) == "203.0.113.9"


# ── rate limiter ────────────────────────────────────────────────────
def test_bucket_enforces_the_limit():
    bucket = TokenBucket(3)
    assert [bucket.allow("a") for _ in range(4)] == [True, True, True, False]
    assert bucket.allow("b") is True, "buckets are per client"


def test_idle_buckets_are_evicted():
    bucket = TokenBucket(60, idle_ttl=60.0, sweep_interval=0.0)
    for i in range(500):
        bucket.allow(f"10.0.0.{i}")
    assert bucket.tracked == 500
    # Age every bucket past the TTL, then trigger a sweep.
    with bucket._lock:
        bucket._buckets = {k: (v[0], v[1] - 120.0) for k, v in bucket._buckets.items()}
    bucket.allow("fresh")
    assert bucket.tracked == 1, "stale buckets must not accumulate"


def test_eviction_cannot_bypass_the_limit():
    """An evicted bucket has already refilled, so re-creating it grants
    nothing an idle client would not have had anyway."""
    bucket = TokenBucket(60, idle_ttl=60.0, sweep_interval=0.0)
    for _ in range(60):
        bucket.allow("x")
    assert bucket.allow("x") is False
    with bucket._lock:
        bucket._buckets = {k: (v[0], v[1] - 120.0) for k, v in bucket._buckets.items()}
    bucket.allow("trigger-sweep")
    # 120 simulated seconds is longer than the 60s full-refill window.
    assert bucket.allow("x") is True


def test_bucket_hard_cap_is_enforced():
    bucket = TokenBucket(60, idle_ttl=3600.0, max_buckets=50, sweep_interval=0.0)
    for i in range(200):
        bucket.allow(f"10.0.0.{i}")
        time.sleep(0)
    assert bucket.tracked <= 51
