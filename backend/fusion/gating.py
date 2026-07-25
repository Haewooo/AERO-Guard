"""Temporal confirmation and latching for alert-raising signals.

A classification covers one 36-frame window, and the HMI produces a window
roughly every 1.6 s from a rolling buffer, so consecutive windows overlap
heavily. Raising an alert on every positive window turned a single
sustained EMERGENCY STOP into an unbounded stream of CRITICAL alerts, each
one re-triggering the master-warning takeover — the operator could not
clear the screen while the marshaller kept signalling.

Two gates fix that, and they solve different problems:

* **Confirmation** — a signal must hold across `confirmations` consecutive
  windows before it fires. A single spurious window is discarded, which
  matters because the rule engine misreads large two-arm motion as
  emergency_stop.
* **Latching** — once fired, a signal stays latched until it has been
  absent for `release` consecutive windows. One continuous event produces
  one alert, and re-arming requires the condition to genuinely clear.
"""

from __future__ import annotations

import threading


class SignalGate:
    """
    `immediate` names signals exempt from the confirmation delay. Waiting a
    second window costs ~1.6s, which is the wrong trade for EMERGENCY STOP:
    a missed one is an aircraft that does not stop, while a spurious one is
    a takeover the operator dismisses. Those signals still latch, so the
    fail-fast bias cannot bring back the repeating-takeover behaviour.
    """

    def __init__(
        self,
        confirmations: int = 2,
        release: int = 3,
        immediate: frozenset[str] = frozenset({"emergency_stop"}),
    ):
        self._lock = threading.Lock()
        self._confirmations = max(1, confirmations)
        self._release = max(1, release)
        self._immediate = immediate
        self._last: str | None = None
        self._count = 0
        # signal -> consecutive windows it has been absent for. Presence in
        # this map means "already alerted, do not fire again yet".
        self._latched: dict[str, int] = {}

    def observe(self, signal: str) -> bool:
        """Feed one classification; True when it qualifies as a new event.

        Must be called for every window, including non-alerting ones —
        that is what advances the streak and releases stale latches.
        """
        with self._lock:
            if signal == self._last:
                self._count += 1
            else:
                self._last, self._count = signal, 1

            for latched in list(self._latched):
                if latched == signal:
                    self._latched[latched] = 0
                else:
                    self._latched[latched] += 1
                    if self._latched[latched] >= self._release:
                        del self._latched[latched]

            if signal in self._latched:
                return False
            required = 1 if signal in self._immediate else self._confirmations
            if self._count >= required:
                self._latched[signal] = 0
                return True
            return False

    def state(self) -> dict[str, object]:
        with self._lock:
            return {
                "current": self._last,
                "streak": self._count,
                "confirmations_required": self._confirmations,
                "immediate": sorted(self._immediate),
                "latched": sorted(self._latched),
            }
