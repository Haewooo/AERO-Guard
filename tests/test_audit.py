"""Audit-trail integrity: keyed chain, append-only storage, external anchors.

Each test targets one layer, and the tamper tests escalate: an attacker
with API access, then with raw file access, then holding the audit key.
"""

import hashlib
import json
import sqlite3

import pytest

from backend.audit import AuditLog, load_or_create_key

KEY = b"unit-test-audit-key"


def _log(tmp_path, key=KEY, anchors=True):
    return AuditLog(
        str(tmp_path / "audit.db"),
        key=key,
        anchor_path=str(tmp_path / "anchors.log") if anchors else None,
    )


def _drop_triggers(db):
    """Simulate an attacker with raw file access, not just API access."""
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER audit_no_update")
    con.execute("DROP TRIGGER audit_no_delete")
    con.commit()
    return con


def test_append_and_verify(tmp_path):
    log = _log(tmp_path)
    for i in range(10):
        log.append("tester", "EVENT", {"n": i})
    result = log.verify_chain()
    assert result["valid"] is True
    assert result["records"] == 10
    assert result["algo"] == "hmac-sha256"
    log.close()


def test_recent_order(tmp_path):
    log = _log(tmp_path)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    assert [r["payload"]["n"] for r in log.recent(3)] == [4, 3, 2]
    log.close()


# ── layer 1: append-only storage ────────────────────────────────────
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit SET payload = '{\"n\": 999}' WHERE id = 3",
        "DELETE FROM audit WHERE id = 3",
    ],
)
def test_storage_rejects_in_band_tampering(tmp_path, sql):
    log = _log(tmp_path)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._conn.execute(sql)
    assert log.verify_chain()["valid"] is True
    log.close()


# ── layer 2: keyed chain ────────────────────────────────────────────
def test_naive_edit_is_detected(tmp_path):
    db = str(tmp_path / "audit.db")
    log = _log(tmp_path)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    log.close()

    con = _drop_triggers(db)
    con.execute("UPDATE audit SET payload = '{\"n\": 999}' WHERE id = 3")
    con.commit()
    con.close()

    log = _log(tmp_path)
    result = log.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_id"] == 3
    log.close()


def test_informed_rewrite_without_the_key_is_detected(tmp_path):
    """Delete a record and recompute the whole chain — the attack an
    unkeyed SHA-256 chain cannot survive."""
    db = str(tmp_path / "audit.db")
    log = _log(tmp_path)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    log.close()

    con = _drop_triggers(db)
    con.execute("DELETE FROM audit WHERE id = 3")
    rows = con.execute(
        "SELECT id, ts, actor, event_type, payload FROM audit ORDER BY id"
    ).fetchall()
    prev = "0" * 64
    for r in rows:
        material = f"{prev}|{r[1]:.6f}|{r[2]}|{r[3]}|{r[4]}"
        digest = hashlib.sha256(material.encode()).hexdigest()
        con.execute(
            "UPDATE audit SET prev_hash=?, hash=?, algo='sha256' WHERE id=?",
            (prev, digest, r[0]),
        )
        prev = digest
    con.commit()
    con.close()

    log = _log(tmp_path)
    assert log.verify_chain()["valid"] is False, "unkeyed rewrite must not verify"
    log.close()


def test_partial_downgrade_to_unkeyed_is_rejected(tmp_path):
    db = str(tmp_path / "audit.db")
    log = _log(tmp_path)
    for i in range(4):
        log.append("tester", "EVENT", {"n": i})
    log.close()

    con = _drop_triggers(db)
    con.execute("UPDATE audit SET algo='sha256' WHERE id >= 3")
    con.commit()
    con.close()

    log = _log(tmp_path)
    result = log.verify_chain()
    assert result["valid"] is False
    assert result["reason"] == "chain algorithm downgraded"
    log.close()


# ── layer 3: external anchors ───────────────────────────────────────
def test_anchor_detects_rewrite_by_an_attacker_holding_the_key(tmp_path):
    """Worst case: the attacker has the audit key and rebuilds a clean,
    internally consistent chain. The anchor log still contradicts it."""
    db = str(tmp_path / "audit.db")
    log = _log(tmp_path)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    log.anchor()
    log.close()

    con = _drop_triggers(db)
    con.execute("DELETE FROM audit")
    con.commit()
    con.close()

    forged = _log(tmp_path)
    for i in range(3):
        forged.append("tester", "EVENT", {"n": i})  # internally valid new chain
    result = forged.verify_chain()
    assert result["valid"] is False
    assert result["anchors"]["valid"] is False
    forged.close()


def test_anchor_log_tampering_is_detected(tmp_path):
    log = _log(tmp_path)
    log.append("tester", "EVENT", {"n": 1})
    log.anchor()
    log.close()

    anchors = tmp_path / "anchors.log"
    entry = json.loads(anchors.read_text().splitlines()[0])
    body = json.loads(entry["anchor"])
    body["head_hash"] = "0" * 64
    entry["anchor"] = json.dumps(body, sort_keys=True)
    anchors.write_text(json.dumps(entry, sort_keys=True) + "\n")

    log = _log(tmp_path)
    assert log.verify_chain()["anchors"]["reason"] == "anchor log was modified"
    log.close()


def test_anchor_written_once_per_day(tmp_path):
    log = _log(tmp_path)
    for i in range(20):
        log.append("tester", "EVENT", {"n": i})
    assert len((tmp_path / "anchors.log").read_text().splitlines()) == 1
    log.close()


# ── key management ──────────────────────────────────────────────────
def test_key_file_is_created_private_and_stable(tmp_path):
    path = tmp_path / "keys" / "audit.key"
    first = load_or_create_key(str(path))
    assert load_or_create_key(str(path)) == first
    assert (path.stat().st_mode & 0o077) == 0, "audit key must not be group/world readable"


def test_legacy_unkeyed_records_stay_verifiable(tmp_path):
    """A database written before the chain was keyed must still verify."""
    log = _log(tmp_path, key=None, anchors=False)
    for i in range(3):
        log.append("tester", "EVENT", {"n": i})
    assert log.verify_chain()["algo"] == "sha256"
    log.close()

    keyed = _log(tmp_path, anchors=False)  # same database, now keyed
    keyed.append("tester", "EVENT", {"n": 99})
    result = keyed.verify_chain()
    assert result["valid"] is True
    assert result["records"] == 4
    keyed.close()
