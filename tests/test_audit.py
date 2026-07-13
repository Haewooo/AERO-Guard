from backend.audit import AuditLog


def test_append_and_verify(tmp_path):
    log = AuditLog(str(tmp_path / "audit.db"))
    for i in range(10):
        log.append("tester", "EVENT", {"n": i})
    result = log.verify_chain()
    assert result == {"valid": True, "records": 10}
    log.close()


def test_tamper_detection(tmp_path):
    db = str(tmp_path / "audit.db")
    log = AuditLog(db)
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    # Simulate tampering with a past record.
    log._conn.execute("UPDATE audit SET payload = '{\"n\": 999}' WHERE id = 3")
    log._conn.commit()
    result = log.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_id"] == 3
    log.close()


def test_recent_order(tmp_path):
    log = AuditLog(str(tmp_path / "audit.db"))
    for i in range(5):
        log.append("tester", "EVENT", {"n": i})
    recent = log.recent(3)
    assert [r["payload"]["n"] for r in recent] == [4, 3, 2]
    log.close()
