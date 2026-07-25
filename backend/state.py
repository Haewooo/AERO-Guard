"""Durable operational state: runway occupancy and alerts.

RiskEngine keeps this state in memory for speed. Without a durable copy a
process restart — a crash, `docker restart`, a redeploy — silently clears
runway occupancy, which disables the incursion rule while every health
probe still reports green. That is a safety-relevant failure mode, so
mutations are written through to SQLite and reloaded on startup.

Reads stay in memory; only mutations touch the database, and those are
operator-paced (occupancy changes, alert creation, acknowledgements)
rather than per-frame.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS occupancy (
                runway   TEXT PRIMARY KEY,
                callsign TEXT NOT NULL,
                ts       REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id           TEXT PRIMARY KEY,
                ts           REAL NOT NULL,
                priority     INTEGER NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                body         TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS alerts_ts ON alerts (ts)")
        self._conn.commit()

    # ── runway occupancy ────────────────────────────────────────────
    def load_occupancy(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT runway, callsign FROM occupancy").fetchall()
        return {r[0]: r[1] for r in rows}

    def set_occupancy(self, runway: str, callsign: str, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO occupancy (runway, callsign, ts) VALUES (?, ?, ?)"
                " ON CONFLICT(runway) DO UPDATE SET callsign=excluded.callsign,"
                " ts=excluded.ts",
                (runway, callsign, ts),
            )
            self._conn.commit()

    def clear_occupancy(self, runway: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM occupancy WHERE runway = ?", (runway,))
            self._conn.commit()

    # ── alerts ──────────────────────────────────────────────────────
    def load_alerts(self, limit: int) -> list[dict[str, Any]]:
        """Most recent alerts, oldest first (RiskEngine keeps them in order)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT body FROM alerts ORDER BY ts DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def add_alert(self, alert: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO alerts (id, ts, priority, acknowledged, body)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    alert["id"],
                    alert["ts"],
                    alert["priority"],
                    int(bool(alert.get("acknowledged"))),
                    json.dumps(alert, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()

    def update_alert(self, alert: dict[str, Any]) -> None:
        self.add_alert(alert)

    def trim_alerts(self, max_alerts: int) -> None:
        """Bound the stored history to match the in-memory window."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM alerts WHERE id NOT IN ("
                " SELECT id FROM alerts ORDER BY ts DESC, rowid DESC LIMIT ?)",
                (max_alerts,),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
