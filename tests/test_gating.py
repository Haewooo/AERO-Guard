"""Temporal gating of alert-raising marshalling signals.

Regression guard for the repeating master-warning takeover: a sustained
EMERGENCY STOP used to produce a new CRITICAL alert on every ~1.6s
classification window, so the operator could not clear the screen while
the marshaller kept signalling.
"""

import pytest

from backend.fusion.gating import SignalGate
from backend.fusion.risk import RiskEngine

EMERGENCY = {"signal": "emergency_stop", "confidence": 0.9}
CALM = {"signal": "move_ahead", "confidence": 0.9}


def test_single_window_does_not_fire():
    gate = SignalGate(confirmations=2, immediate=frozenset())
    assert gate.observe("emergency_stop") is False


def test_fires_once_confirmed():
    gate = SignalGate(confirmations=2, immediate=frozenset())
    gate.observe("emergency_stop")
    assert gate.observe("emergency_stop") is True


def test_emergency_stop_fires_on_the_first_window():
    """Waiting for a second window costs ~1.6s. For an emergency stop that
    is the wrong trade — it still latches, so it cannot repeat."""
    gate = SignalGate(confirmations=2)
    assert gate.observe("emergency_stop") is True
    assert gate.observe("emergency_stop") is False
    assert gate.observe("move_ahead") is False, "other signals still confirm"


def test_sustained_signal_fires_exactly_once():
    gate = SignalGate(confirmations=2, release=3, immediate=frozenset())
    fired = sum(gate.observe("emergency_stop") for _ in range(50))
    assert fired == 1, "a continuous event must produce a single alert"


def test_streak_resets_on_interruption():
    gate = SignalGate(confirmations=3, immediate=frozenset())
    gate.observe("emergency_stop")
    gate.observe("emergency_stop")
    gate.observe("move_ahead")          # streak broken
    gate.observe("emergency_stop")
    assert gate.observe("emergency_stop") is False, "needs 3 in a row"
    assert gate.observe("emergency_stop") is True


def test_latch_releases_only_after_sustained_absence():
    gate = SignalGate(confirmations=1, release=3)
    assert gate.observe("emergency_stop") is True
    for _ in range(2):                   # not yet absent long enough
        gate.observe("move_ahead")
    assert gate.observe("emergency_stop") is False
    for _ in range(3):
        gate.observe("move_ahead")
    assert gate.observe("emergency_stop") is True, "must re-arm after it clears"


def test_latches_are_tracked_per_signal():
    gate = SignalGate(confirmations=1, release=2)
    assert gate.observe("emergency_stop") is True
    assert gate.observe("stop") is True
    assert gate.observe("stop") is False


def test_state_is_reportable():
    gate = SignalGate(confirmations=2)
    gate.observe("emergency_stop")
    state = gate.state()
    assert state["current"] == "emergency_stop"
    assert state["streak"] == 1
    assert state["confirmations_required"] == 2


# ── through the risk engine ─────────────────────────────────────────
def test_live_stream_raises_one_alert_not_a_flood():
    """Simulates the HMI: a window every ~1.6s while the signal is held."""
    engine = RiskEngine(signal_confirmations=2, signal_release_windows=3)
    alerts = [engine.evaluate_signal(EMERGENCY) for _ in range(60)]
    raised = [a for a in alerts if a is not None]
    assert len(raised) == 1
    assert len(engine.recent_alerts(100)) == 1, "no duplicate alerts stored"


def test_second_event_after_the_signal_clears():
    engine = RiskEngine(signal_confirmations=2, signal_release_windows=3)
    for _ in range(5):
        engine.evaluate_signal(EMERGENCY)
    for _ in range(4):
        engine.evaluate_signal(CALM)
    for _ in range(5):
        engine.evaluate_signal(EMERGENCY)
    assert len(engine.recent_alerts(100)) == 2, "a genuine second event must alert"


@pytest.mark.parametrize("noise_signal", ["unknown", "move_ahead", "slow_down"])
def test_isolated_false_positive_is_suppressed(noise_signal):
    """One stray signal between other windows must not fire.

    emergency_stop is exempt by design (see SignalGate), so this covers the
    confirmed-signal path with a non-exempt signal.
    """
    engine = RiskEngine(signal_confirmations=2, signal_release_windows=3)
    engine._gate._immediate = frozenset()
    for signal in [noise_signal, "emergency_stop", noise_signal, noise_signal]:
        engine.evaluate_signal({"signal": signal, "confidence": 0.9})
    assert engine.recent_alerts(100) == []
