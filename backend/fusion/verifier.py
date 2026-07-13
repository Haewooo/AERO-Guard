"""Slot-level readback verification.

Compares controller instruction slots against pilot readback slots and
grades each finding by severity. Per ICAO Doc 4444 the controller must
correct erroneous readbacks immediately — this engine surfaces them.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    OK = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


SEVERITY_LABEL = {s: s.name for s in Severity}

# Severity when the readback VALUE differs from the instruction.
MISMATCH_SEVERITY: dict[str, Severity] = {
    "runway": Severity.CRITICAL,
    "clearance": Severity.CRITICAL,
    "hold_short": Severity.CRITICAL,
    "taxi_to": Severity.HIGH,
    "altitude": Severity.HIGH,
    "heading": Severity.HIGH,
    "callsign": Severity.HIGH,
    "route": Severity.MEDIUM,
    "frequency": Severity.MEDIUM,
    "squawk": Severity.MEDIUM,
    "qnh": Severity.MEDIUM,
}

# Severity when a safety slot is absent from the readback entirely.
# Hold-short omission is a known runway-incursion precursor.
MISSING_SEVERITY: dict[str, Severity] = {
    "hold_short": Severity.HIGH,
    "runway": Severity.MEDIUM,
    "clearance": Severity.MEDIUM,
    "altitude": Severity.MEDIUM,
    "taxi_to": Severity.MEDIUM,
    "heading": Severity.LOW,
    "route": Severity.LOW,
    "frequency": Severity.LOW,
    "squawk": Severity.LOW,
    "qnh": Severity.LOW,
}

_COMPARED_SLOTS = list(MISMATCH_SEVERITY.keys())


def _values_equal(slot: str, a: Any, b: Any) -> bool:
    if slot == "route":
        return list(a) == list(b)
    return a == b


def verify_readback(
    instruction_slots: dict[str, Any], readback_slots: dict[str, Any]
) -> dict[str, Any]:
    """Return findings list + overall status for an instruction/readback pair."""
    findings: list[dict[str, Any]] = []
    worst = Severity.OK

    for slot in _COMPARED_SLOTS:
        instructed = instruction_slots.get(slot)
        read_back = readback_slots.get(slot)
        if instructed is None:
            continue

        if read_back is None:
            severity = MISSING_SEVERITY.get(slot, Severity.LOW)
            findings.append(
                {
                    "slot": slot,
                    "type": "MISSING_READBACK",
                    "instructed": instructed,
                    "readback": None,
                    "severity": severity.name,
                }
            )
            worst = max(worst, severity)
        elif not _values_equal(slot, instructed, read_back):
            severity = MISMATCH_SEVERITY[slot]
            findings.append(
                {
                    "slot": slot,
                    "type": "MISMATCH",
                    "instructed": instructed,
                    "readback": read_back,
                    "severity": severity.name,
                }
            )
            worst = max(worst, severity)
        else:
            findings.append(
                {
                    "slot": slot,
                    "type": "MATCH",
                    "instructed": instructed,
                    "readback": read_back,
                    "severity": Severity.OK.name,
                }
            )

    if not findings:
        # Nothing comparable was extracted from the instruction: report
        # honestly instead of implying a verified match.
        status = "UNVERIFIABLE"
    elif worst == Severity.OK:
        status = "OK"
    else:
        status = "DISCREPANCY"

    return {
        "findings": findings,
        "overall_severity": worst.name,
        "overall_severity_level": int(worst),
        "status": status,
    }
