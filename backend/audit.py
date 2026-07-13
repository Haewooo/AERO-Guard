"""Tamper-evident audit log (data governance).

Append-only SQLite log where every record carries a SHA-256 hash chained
to the previous record, so any modification or deletion of past entries
is detectable via verify_chain(). All AI-assisted decisions and operator
acknowledgements are recorded here (human-in-the-loop accountability).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
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
        self._conn.commit()

    @staticmethod
    def _compute_hash(
        ts: float, actor: str, event_type: str, payload: str, prev_hash: str
    ) -> str:
        material = f"{prev_hash}|{ts:.6f}|{actor}|{event_type}|{payload}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def append(self, actor: str, event_type: str, payload: dict[str, Any]) -> int:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ts = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = row[0] if row else GENESIS
            digest = self._compute_hash(ts, actor, event_type, body, prev_hash)
            cur = self._conn.execute(
                "INSERT INTO audit (ts, actor, event_type, payload, prev_hash, hash)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ts, actor, event_type, body, prev_hash, digest),
            )
            self._conn.commit()
            return int(cur.lastrowid)

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

    def verify_chain(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, actor, event_type, payload, prev_hash, hash"
                " FROM audit ORDER BY id ASC"
            ).fetchall()
        prev = GENESIS
        for r in rows:
            expected = self._compute_hash(r[1], r[2], r[3], r[4], prev)
            if r[5] != prev or r[6] != expected:
                return {"valid": False, "broken_at_id": r[0], "records": len(rows)}
            prev = r[6]
        return {"valid": True, "records": len(rows)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
