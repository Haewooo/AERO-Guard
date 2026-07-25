"""Risk fusion engine.

Fuses readback-verification findings with operational context (runway
occupancy) into prioritized alerts. Escalation rule from the concept
design: a runway-related discrepancy while that runway is occupied is
raised to top priority (potential runway incursion).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from ..observability import metrics
from .gating import SignalGate
from .verifier import Severity

_PRIORITY = {
    Severity.CRITICAL: 90,
    Severity.HIGH: 70,
    Severity.MEDIUM: 40,
    Severity.LOW: 20,
}

_RUNWAY_ENTRY_CLEARANCES = {"takeoff", "land", "line_up_wait", "cross"}
_RUNWAY_SLOTS = {"runway", "hold_short", "taxi_to"}


class RiskEngine:
    """Thread-safe in-process operational state + alert store.

    Reads are served from memory; mutations are written through to the
    optional StateStore so runway occupancy and alerts survive a process
    restart. Losing occupancy silently disables the incursion rule while
    the service still looks healthy, so durability here is a safety
    property, not a convenience.

    Horizontal scaling note: state is process-local by design for the
    single-node on-premises PoC. For multi-instance deployment swap the
    store for a shared one (e.g. Redis) behind this same interface.
    """

    def __init__(
        self,
        max_alerts: int = 500,
        store: Any | None = None,
        signal_confirmations: int = 2,
        signal_release_windows: int = 3,
    ):
        self._lock = threading.Lock()
        self._store = store
        self._occupancy: dict[str, str] = {}
        self._alerts: list[dict[str, Any]] = []
        self._max_alerts = max_alerts
        self._gate = SignalGate(signal_confirmations, signal_release_windows)
        if store is not None:
            self._occupancy = store.load_occupancy()
            self._alerts = store.load_alerts(max_alerts)

    # ── runway occupancy ────────────────────────────────────────────
    def set_occupancy(self, runway: str, callsign: str | None) -> dict[str, str]:
        runway = runway.upper()
        with self._lock:
            if callsign:
                self._occupancy[runway] = callsign.upper()
            else:
                self._occupancy.pop(runway, None)
            snapshot = dict(self._occupancy)
        if self._store is not None:
            if callsign:
                self._store.set_occupancy(runway, callsign.upper(), time.time())
            else:
                self._store.clear_occupancy(runway)
        return snapshot

    def get_occupancy(self) -> dict[str, str]:
        with self._lock:
            return dict(self._occupancy)

    # ── alerts ──────────────────────────────────────────────────────
    def _add_alert(
        self,
        alert_type: str,
        severity: Severity,
        priority: int,
        message: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        alert = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "type": alert_type,
            "severity": severity.name,
            "priority": priority,
            "message": message,
            "details": details,
            "acknowledged": False,
            "ai_assisted": True,
        }
        metrics.inc(
            "aeroguard_alerts_total",
            {"type": alert_type, "severity": severity.name},
        )
        with self._lock:
            self._alerts.append(alert)
            trimmed = len(self._alerts) > self._max_alerts
            if trimmed:
                self._alerts = self._alerts[-self._max_alerts :]
        if self._store is not None:
            self._store.add_alert(alert)
            if trimmed:
                self._store.trim_alerts(self._max_alerts)
        return alert

    def acknowledge(self, alert_id: str, operator: str) -> dict[str, Any] | None:
        with self._lock:
            for alert in self._alerts:
                if alert["id"] == alert_id:
                    alert["acknowledged"] = True
                    alert["acknowledged_by"] = operator
                    alert["acknowledged_at"] = time.time()
                    updated = dict(alert)
                    break
            else:
                return None
        if self._store is not None:
            self._store.update_alert(updated)
        return updated

    def recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            alerts = list(self._alerts[-limit:])
        alerts.sort(key=lambda a: (a["acknowledged"], -a["priority"], -a["ts"]))
        return alerts

    # ── fusion ──────────────────────────────────────────────────────
    def evaluate_comms(
        self,
        instruction_slots: dict[str, Any],
        readback_slots: dict[str, Any],
        verification: dict[str, Any],
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        occupancy = self.get_occupancy()
        callsign = instruction_slots.get("callsign") or readback_slots.get("callsign")
        clearance = instruction_slots.get("clearance")
        runway = instruction_slots.get("runway")

        # 1) Runway incursion: entry clearance issued for an occupied runway.
        if clearance in _RUNWAY_ENTRY_CLEARANCES and runway:
            occupant = occupancy.get(runway)
            if occupant and occupant != callsign:
                alerts.append(
                    self._add_alert(
                        "RUNWAY_INCURSION",
                        Severity.CRITICAL,
                        100,
                        f"Runway {runway} occupied by {occupant} — "
                        f"'{clearance}' clearance issued to {callsign or 'aircraft'}",
                        {
                            "runway": runway,
                            "occupant": occupant,
                            "callsign": callsign,
                            "clearance": clearance,
                        },
                    )
                )

        # 2) Readback discrepancies -> alerts, with occupancy escalation.
        for finding in verification["findings"]:
            if finding["type"] == "MATCH":
                continue
            severity = Severity[finding["severity"]]
            priority = _PRIORITY[severity]
            escalated = False
            if finding["slot"] in _RUNWAY_SLOTS:
                involved = {
                    str(v)
                    for v in (finding.get("instructed"), finding.get("readback"))
                    if v
                }
                if any(r in occupancy for r in involved):
                    severity = Severity.CRITICAL
                    priority = 95
                    escalated = True

            kind = (
                "READBACK_MISMATCH"
                if finding["type"] == "MISMATCH"
                else "READBACK_MISSING"
            )
            if finding["type"] == "MISMATCH":
                msg = (
                    f"Readback mismatch [{finding['slot']}] instructed "
                    f"{finding['instructed']} ≠ readback {finding['readback']}"
                )
            else:
                msg = (
                    f"Readback missing [{finding['slot']}] "
                    f"instructed {finding['instructed']}"
                )
            if callsign:
                msg = f"{callsign}: {msg}"
            if escalated:
                msg += " (runway occupied — escalated)"

            alerts.append(
                self._add_alert(
                    kind,
                    severity,
                    priority,
                    msg,
                    {"finding": finding, "callsign": callsign, "escalated": escalated},
                )
            )

        return alerts

    def evaluate_signal(self, signal_result: dict[str, Any]) -> dict[str, Any] | None:
        """Emergency-stop marshalling signal raises an alert, once per event.

        Every window is fed to the gate — that is what advances the
        confirmation streak and releases stale latches — but only a newly
        confirmed, not-yet-latched emergency_stop produces an alert. See
        fusion/gating.py for why firing per window was wrong.
        """
        signal = str(signal_result.get("signal") or "unknown")
        metrics.inc("aeroguard_signals_total", {"signal": signal})
        confirmed = self._gate.observe(signal)
        if not confirmed or signal != "emergency_stop":
            return None
        return self._add_alert(
            "MARSHALLING_EMERGENCY",
            Severity.CRITICAL,
            100,
            "Marshaller EMERGENCY STOP hand signal detected",
            {
                "signal": signal,
                "confidence": signal_result.get("confidence"),
                "confirmed_windows": self._gate.state()["confirmations_required"],
            },
        )

    def signal_gate_state(self) -> dict[str, Any]:
        return self._gate.state()
