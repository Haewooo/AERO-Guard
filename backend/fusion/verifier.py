"""Slot-level readback verification.

Compares controller instruction slots against pilot readback slots and
grades each finding by severity. Per ICAO Doc 4444 the controller must
correct erroneous readbacks immediately — this engine surfaces them.
"""

from __future__ import annotations

import re
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    OK = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


# Severity when the readback VALUE differs from the instruction.
# Grading follows ICAO Doc 9870 (runway instructions dominate incursion
# statistics) and FAA JO 7110.65 readback/hearback guidance: wrong QNH is
# a level-bust precursor, so it grades with altitude rather than with the
# administrative slots.
MISMATCH_SEVERITY: dict[str, Severity] = {
    "runway": Severity.CRITICAL,
    "clearance": Severity.CRITICAL,
    "hold_short": Severity.CRITICAL,
    "taxi_to": Severity.HIGH,
    "altitude": Severity.HIGH,
    "heading": Severity.HIGH,
    "callsign": Severity.HIGH,
    "qnh": Severity.HIGH,
    "route": Severity.MEDIUM,
    "frequency": Severity.MEDIUM,
    "squawk": Severity.MEDIUM,
    "speed": Severity.MEDIUM,
}

# Severity when a mandatory readback item (ICAO Doc 4444 4.5.7.5.1) is
# absent from the readback entirely. Omitted runway / runway-entry
# clearance / hold-short readbacks are the classic hearback failure that
# precedes runway incursions (Doc 9870), so they grade HIGH. A readback
# without the aircraft callsign is not a valid readback at all per
# Doc 4444 4.5.7.5.2.
MISSING_SEVERITY: dict[str, Severity] = {
    "hold_short": Severity.HIGH,
    "runway": Severity.HIGH,
    "clearance": Severity.HIGH,
    "altitude": Severity.MEDIUM,
    "taxi_to": Severity.MEDIUM,
    "callsign": Severity.MEDIUM,
    "heading": Severity.LOW,
    "route": Severity.LOW,
    "frequency": Severity.LOW,
    "squawk": Severity.LOW,
    "qnh": Severity.LOW,
    "speed": Severity.LOW,
}

_COMPARED_SLOTS = list(MISMATCH_SEVERITY.keys())

# Value slots that pilots routinely read back without the keyword
# ("two niner niner two" for QNH 2992, "left 270" for heading 270).
# A bare readback token equal to the instructed value counts as a match.
# Phrase slots (runway/clearance/hold_short/...) still require the phrase.
_ECHO_SLOTS = {"altitude", "heading", "frequency", "squawk", "qnh", "speed"}


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
    rb_tokens = set(str(readback_slots.get("_normalized") or "").split())

    for slot in _COMPARED_SLOTS:
        instructed = instruction_slots.get(slot)
        read_back = readback_slots.get(slot)
        if instructed is None:
            continue

        if read_back is None and slot in _ECHO_SLOTS and str(instructed) in rb_tokens:
            findings.append(
                {
                    "slot": slot,
                    "type": "MATCH",
                    "instructed": instructed,
                    "readback": instructed,
                    "severity": Severity.OK.name,
                    "echo": True,
                }
            )
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

    # Safety net beyond the slot grammar: any digit sequence the pilot read
    # back that the controller never stated is a potential corruption (a
    # mangled callsign, an invented value) and must be surfaced even when no
    # slot models it. Values already handled by a slot finding are skipped.
    instr_runs = set(re.findall(r"\d+", str(instruction_slots.get("_normalized") or "")))
    covered = set(re.findall(r"\d+", str(readback_slots.get("callsign") or "")))
    for f in findings:
        if f["readback"] is not None:
            covered.update(re.findall(r"\d+", str(f["readback"])))
    rb_norm = str(readback_slots.get("_normalized") or "")
    for run in sorted(set(re.findall(r"\d+", rb_norm))):
        if run in instr_runs or run in covered:
            continue
        findings.append(
            {
                "slot": "value",
                "type": "UNEXPECTED_VALUE",
                "instructed": None,
                "readback": run,
                "severity": Severity.MEDIUM.name,
            }
        )
        worst = max(worst, Severity.MEDIUM)

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
