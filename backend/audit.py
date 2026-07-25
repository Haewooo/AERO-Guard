"""Tamper-evident audit log (data governance).

Append-only SQLite log where every record carries a digest chained to the
previous record, so any modification or deletion of past entries is
detectable via verify_chain(). All AI-assisted decisions and operator
acknowledgements are recorded here (human-in-the-loop accountability).

Three independent layers protect the trail:

1. **Keyed chain (HMAC-SHA256).** An unkeyed hash chain only detects
   careless edits — an attacker who can write to the database can delete a
   record and recompute every subsequent digest. Keying the chain means
   forging it also requires the audit key, which lives outside the
   database.
2. **Append-only triggers.** UPDATE and DELETE on the audit table are
   rejected by SQLite itself, so in-band tampering through the application
   is impossible. An attacker with raw file access can drop the triggers;
   that is what layer 1 and 3 are for.
3. **External anchors.** The chain head is appended to a separate log once
   per UTC day. Rewriting the whole database — key included — still
   contradicts the anchors, provided the anchor log lives on different
   (ideally WORM or remote) storage.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("aeroguard")

GENESIS = "0" * 64
ALGO_KEYED = "hmac-sha256"
ALGO_PLAIN = "sha256"


def load_or_create_key(path: str) -> bytes:
    """Load the audit key from disk, creating it on first run.

    Production deployments should inject AEROGUARD_AUDIT_KEY from a secret
    store instead; this keeps the chain verifiable across restarts for
    on-premises installs that have no secret manager.
    """
    key_file = Path(path)
    if key_file.exists():
        return key_file.read_bytes().strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode()
    # Write 0600 from the start — never world-readable, even briefly.
    fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.warning(
        "audit key generated at %s — back it up; losing it makes the "
        "existing audit chain unverifiable",
        key_file,
    )
    return key


class AuditLog:
    def __init__(
        self,
        db_path: str,
        key: bytes | None = None,
        anchor_path: str | None = None,
    ):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._key = key
        self._algo = ALGO_KEYED if key else ALGO_PLAIN
        self._anchor_path = Path(anchor_path) if anchor_path else None
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            )
            """
        )
        self._migrate()
        self._install_append_only_triggers()
        self._conn.commit()
        self._anchor_day = self._last_anchored_day()

    # ── schema ──────────────────────────────────────────────────────
    def _migrate(self) -> None:
        """Add the per-record algorithm column to pre-existing databases.

        Records written before the chain was keyed keep algo='sha256' and
        stay verifiable; new records are written keyed.
        """
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(audit)")}
        if "algo" not in columns:
            self._conn.execute(
                f"ALTER TABLE audit ADD COLUMN algo TEXT NOT NULL"
                f" DEFAULT '{ALGO_PLAIN}'"
            )

    def _install_append_only_triggers(self) -> None:
        for op in ("UPDATE", "DELETE"):
            self._conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS audit_no_{op.lower()}
                BEFORE {op} ON audit
                BEGIN
                    SELECT RAISE(ABORT, 'audit log is append-only');
                END
                """
            )

    # ── digests ─────────────────────────────────────────────────────
    def _digest(self, material: str, algo: str) -> str | None:
        raw = material.encode("utf-8")
        if algo == ALGO_KEYED:
            if self._key is None:
                return None  # keyed records cannot be checked without the key
            return hmac.new(self._key, raw, hashlib.sha256).hexdigest()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _material(
        ts: float, actor: str, event_type: str, payload: str, prev_hash: str
    ) -> str:
        return f"{prev_hash}|{ts:.6f}|{actor}|{event_type}|{payload}"

    # ── write ───────────────────────────────────────────────────────
    def append(self, actor: str, event_type: str, payload: dict[str, Any]) -> int:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ts = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = row[0] if row else GENESIS
            digest = self._digest(
                self._material(ts, actor, event_type, body, prev_hash), self._algo
            )
            cur = self._conn.execute(
                "INSERT INTO audit (ts, actor, event_type, payload, prev_hash,"
                " hash, algo) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, actor, event_type, body, prev_hash, digest, self._algo),
            )
            self._conn.commit()
            record_id = int(cur.lastrowid)
        # Anchor outside the lock: anchor() takes it itself.
        self._maybe_anchor()
        return record_id

    # ── read ────────────────────────────────────────────────────────
    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, actor, event_type, payload, hash FROM audit"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "actor": r[2],
                "event_type": r[3],
                "payload": json.loads(r[4]),
                "hash": r[5],
            }
            for r in rows
        ]

    # ── external anchors ────────────────────────────────────────────
    def _last_anchored_day(self) -> str | None:
        if not self._anchor_path or not self._anchor_path.exists():
            return None
        day = None
        for line in self._anchor_path.read_text().splitlines():
            try:
                day = json.loads(json.loads(line)["anchor"])["day"]
            except (ValueError, KeyError, TypeError):
                continue
        return day

    def _maybe_anchor(self) -> None:
        if not self._anchor_path:
            return
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._anchor_day:
            self.anchor()

    def anchor(self) -> dict[str, Any] | None:
        """Append the current chain head to the external anchor log."""
        if not self._anchor_path:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id, hash FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            records = self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        payload = {
            "day": time.strftime("%Y-%m-%d", time.gmtime()),
            "ts": time.time(),
            "last_id": row[0],
            "head_hash": row[1],
            "records": records,
        }
        self._write_anchor_entry(payload)
        self._anchor_day = payload["day"]
        return payload

    def _write_anchor_entry(self, payload: dict[str, Any]) -> None:
        if not self._anchor_path:
            return
        body = json.dumps(payload, sort_keys=True)
        entry = {"anchor": body, "algo": self._algo, "mac": self._digest(body, self._algo)}
        self._anchor_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._anchor_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _prune_floor(self) -> int:
        """Lowest retained id authorised by a MAC-verified prune marker."""
        if not self._anchor_path or not self._anchor_path.exists():
            return 0
        floor = 0
        for line in self._anchor_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                body = entry["anchor"]
                payload = json.loads(body)
            except (ValueError, KeyError):
                continue
            if payload.get("event") != "prune":
                continue
            expected = self._digest(body, entry.get("algo", ALGO_PLAIN))
            if expected and hmac.compare_digest(expected, str(entry.get("mac", ""))):
                floor = max(floor, int(payload["floor_id"]))
        return floor

    def verify_anchors(self) -> dict[str, Any]:
        """Cross-check anchored chain heads against the current database."""
        if not self._anchor_path:
            return {"enabled": False, "checked": 0, "valid": True}
        if not self._anchor_path.exists():
            return {"enabled": True, "checked": 0, "valid": True}
        entries: list[dict[str, Any]] = []
        for line in self._anchor_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    return {
                        "enabled": True,
                        "checked": len(entries),
                        "valid": False,
                        "reason": "anchor log is corrupt",
                    }
        # Resolved up front, not accumulated while walking: a prune marker
        # is appended after the anchors it invalidates, but the history it
        # removed was attested to by those earlier anchors, so the floor
        # applies retroactively.
        floor = self._prune_floor()
        for entry in entries:
            body, algo = entry.get("anchor", ""), entry.get("algo", ALGO_PLAIN)
            expected = self._digest(body, algo)
            if expected is None:
                return {
                    "enabled": True,
                    "checked": len(entries),
                    "valid": False,
                    "reason": "audit key unavailable for anchor verification",
                }
            if not hmac.compare_digest(expected, str(entry.get("mac", ""))):
                return {
                    "enabled": True,
                    "checked": len(entries),
                    "valid": False,
                    "reason": "anchor log was modified",
                }
            payload = json.loads(body)
            # A MAC'd prune marker records history this service removed on
            # purpose. Entries below the floor are gone legitimately;
            # forging one requires the key, so truncation still shows up.
            if payload.get("event") == "prune":
                continue
            if payload["last_id"] < floor:
                continue
            with self._lock:
                row = self._conn.execute(
                    "SELECT hash FROM audit WHERE id = ?", (payload["last_id"],)
                ).fetchone()
            if row is None:
                return {
                    "enabled": True,
                    "checked": len(entries),
                    "valid": False,
                    "reason": f"history truncated below anchored id {payload['last_id']}",
                    "anchored_day": payload["day"],
                }
            if row[0] != payload["head_hash"]:
                return {
                    "enabled": True,
                    "checked": len(entries),
                    "valid": False,
                    "reason": f"history rewritten at or before id {payload['last_id']}",
                    "anchored_day": payload["day"],
                }
        return {"enabled": True, "checked": len(entries), "valid": True}

    # ── verification ────────────────────────────────────────────────
    def verify_chain(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, actor, event_type, payload, prev_hash, hash, algo"
                " FROM audit ORDER BY id ASC"
            ).fetchall()
        prev = GENESIS
        if rows and rows[0][5] != GENESIS and rows[0][0] == self._prune_floor():
            # Retention removed the records below this one. The boundary
            # record was never rewritten, so the chain resumes from its own
            # stored prev_hash — authorised by a MAC'd prune marker, which
            # an attacker cannot mint without the audit key.
            prev = rows[0][5]
        seen_keyed = False
        for r in rows:
            # Algorithm strength is monotonic. Records predating the keyed
            # chain stay verifiable, but once keying starts a later record
            # claiming plain SHA-256 is a downgrade attempt — otherwise an
            # attacker could rewrite history unkeyed and relabel it. A
            # wholesale downgrade of every record is caught by the anchors.
            if r[7] == ALGO_KEYED:
                seen_keyed = True
            elif seen_keyed:
                return {
                    "valid": False,
                    "broken_at_id": r[0],
                    "records": len(rows),
                    "algo": self._algo,
                    "reason": "chain algorithm downgraded",
                    "anchors": self.verify_anchors(),
                }
            expected = self._digest(self._material(r[1], r[2], r[3], r[4], prev), r[7])
            if expected is None:
                return {
                    "valid": False,
                    "broken_at_id": r[0],
                    "records": len(rows),
                    "algo": self._algo,
                    "reason": "audit key unavailable",
                    "anchors": self.verify_anchors(),
                }
            if r[5] != prev or r[6] != expected:
                return {
                    "valid": False,
                    "broken_at_id": r[0],
                    "records": len(rows),
                    "algo": self._algo,
                    "reason": "chain digest mismatch",
                    "anchors": self.verify_anchors(),
                }
            prev = r[6]
        anchors = self.verify_anchors()
        return {
            "valid": anchors["valid"],
            "records": len(rows),
            "algo": self._algo,
            "anchors": anchors,
            **({} if anchors["valid"] else {"reason": anchors.get("reason")}),
        }

    # ── retention ───────────────────────────────────────────────────
    def prune(self, retention_days: int) -> dict[str, Any]:
        """Drop records older than the retention window, keeping the chain
        verifiable from the oldest survivor onward.

        The audit log otherwise grows without bound — a live classification
        writes a record every ~1.6s — and verify_chain() reads the whole
        table on every startup, so boot time tracked total history.

        Pruning necessarily breaks the link to genesis, so the surviving
        head is anchored first: the anchor log retains proof of what was
        removed even though the records themselves are gone. Records are
        never rewritten, only dropped from the tail.
        """
        if retention_days <= 0:
            return {"pruned": 0, "retained": self.verify_chain()["records"]}
        cutoff = time.time() - retention_days * 86400
        self.anchor()
        with self._lock:
            keep = self._conn.execute(
                "SELECT id FROM audit WHERE ts >= ? ORDER BY id ASC LIMIT 1", (cutoff,)
            ).fetchone() or self._conn.execute(
                # Everything predates the window: keep the newest record so
                # the chain always has an anchorable head.
                "SELECT id FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if keep is None:
                return {"pruned": 0, "retained": 0}
            floor = int(keep[0])
            doomed = self._conn.execute(
                "SELECT COUNT(*) FROM audit WHERE id < ?", (floor,)
            ).fetchone()[0]
            if doomed:
                # Triggers guard against tampering, not against the
                # service's own retention policy. Records are only ever
                # dropped, never rewritten — the retained boundary record
                # keeps its original prev_hash, so every digest and every
                # past anchor stays valid.
                self._conn.execute("DROP TRIGGER IF EXISTS audit_no_delete")
                self._conn.execute("DELETE FROM audit WHERE id < ?", (floor,))
                self._install_append_only_triggers()
                self._conn.commit()
            retained = self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        if doomed:
            # The floor marker is MAC'd like any other anchor, so a
            # truncation cannot be passed off as retention without the key.
            self._write_anchor_entry({
                "event": "prune",
                "day": time.strftime("%Y-%m-%d", time.gmtime()),
                "ts": time.time(),
                "floor_id": floor,
                "pruned": doomed,
                "retention_days": retention_days,
            })
            logger.info(
                "audit retention: pruned %s records older than %s days, %s retained",
                doomed, retention_days, retained,
            )
        return {"pruned": doomed, "retained": retained, "floor_id": floor}

    def close(self) -> None:
        try:
            self.anchor()
        except Exception:  # never block shutdown on the anchor log
            logger.exception("failed to write shutdown audit anchor")
        with self._lock:
            self._conn.close()
