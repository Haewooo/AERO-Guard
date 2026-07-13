"""Concept(slot)-level extraction from normalized ATC utterances.

Extracted safety slots:
  callsign, clearance, runway, hold_short, taxi_to, route,
  altitude (ft), heading (3-digit), frequency, squawk, qnh
"""

from __future__ import annotations

import re
from typing import Any

from .normalizer import normalize

_KEYWORDS = {"via", "and", "the", "for", "to", "of"}

_CALLSIGN_RE = re.compile(r"\b([a-z]{2,4})\s?(\d{1,4})\b")
_RUNWAY_RE = re.compile(r"runway\s+(\d{1,2})\s*(left|right|center|l|r|c)?\b")
_HOLD_SHORT_RE = re.compile(
    r"hold\s+short\s+(?:of\s+)?(?:runway\s+)?(\d{1,2})\s*(left|right|center|l|r|c)?\b"
)
_TAXI_RE = re.compile(r"taxi\s+to\s+(?:holding\s+point\s+)?(?:runway\s+)?(\d{1,2})\s*(left|right|center|l|r|c)?")
_ROUTE_RE = re.compile(r"via((?:\s+[A-Z]\d?)+)")
_ALTITUDE_RE = re.compile(
    r"(?:climb|descend|maintain)(?:\s+and\s+maintain)?\s+(\d{3,5})(?:\s*(?:feet|ft))?"
)
_FLIGHT_LEVEL_RE = re.compile(r"flight\s+level\s+(\d{2,3})")
_HEADING_RE = re.compile(r"heading\s+(\d{1,3})")
_FREQUENCY_RE = re.compile(r"\b(1[0-3]\d\.\d{1,3})\b")
_SQUAWK_RE = re.compile(r"squawk\s+(\d{4})")
_QNH_RE = re.compile(r"(?:qnh|altimeter)\s+(\d{3,4})")

_SIDE_MAP = {"left": "L", "right": "R", "center": "C", "l": "L", "r": "R", "c": "C"}


def _fmt_runway(num: str, side: str | None) -> str:
    rwy = f"{int(num):02d}"
    if side:
        rwy += _SIDE_MAP[side.lower()]
    return rwy


def _detect_clearance(text: str) -> str | None:
    if re.search(r"cleared\s+for\s+(?:immediate\s+)?take\s?off", text):
        return "takeoff"
    if re.search(r"cleared\s+to\s+land", text):
        return "land"
    if re.search(r"line\s+up\s+and\s+wait", text):
        return "line_up_wait"
    if re.search(r"\bcross\s+runway", text):
        return "cross"
    if re.search(r"\btaxi\s+to\b", text):
        return "taxi"
    return None


def extract_slots(utterance: str) -> dict[str, Any]:
    """Extract safety slots from a raw ATC utterance."""
    text = normalize(utterance)
    slots: dict[str, Any] = {}

    for m in _CALLSIGN_RE.finditer(text):
        word = m.group(1)
        if word in _KEYWORDS or word in ("runway", "level", "heading", "squawk"):
            continue
        slots["callsign"] = (word + m.group(2)).upper()
        break

    hold = _HOLD_SHORT_RE.search(text)
    if hold:
        slots["hold_short"] = _fmt_runway(hold.group(1), hold.group(2))
    # Remove hold-short clauses so their runway number doesn't pollute the
    # primary runway slot.
    scrubbed = _HOLD_SHORT_RE.sub(" ", text)

    clearance = _detect_clearance(text)
    if clearance:
        slots["clearance"] = clearance

    taxi = _TAXI_RE.search(scrubbed)
    if taxi:
        slots["taxi_to"] = _fmt_runway(taxi.group(1), taxi.group(2))
        scrubbed = _TAXI_RE.sub(" ", scrubbed)

    rwy = _RUNWAY_RE.search(scrubbed)
    if rwy:
        slots["runway"] = _fmt_runway(rwy.group(1), rwy.group(2))
    elif "taxi_to" in slots and clearance == "taxi":
        slots["runway"] = slots["taxi_to"]

    route = _ROUTE_RE.search(text)
    if route:
        slots["route"] = route.group(1).split()

    fl = _FLIGHT_LEVEL_RE.search(text)
    alt = _ALTITUDE_RE.search(text)
    if fl:
        slots["altitude"] = int(fl.group(1)) * 100
    elif alt:
        slots["altitude"] = int(alt.group(1))

    hdg = _HEADING_RE.search(text)
    if hdg:
        slots["heading"] = f"{int(hdg.group(1)):03d}"

    freq = _FREQUENCY_RE.search(text)
    if freq:
        slots["frequency"] = freq.group(1)

    sqk = _SQUAWK_RE.search(text)
    if sqk:
        slots["squawk"] = sqk.group(1)

    qnh = _QNH_RE.search(text)
    if qnh:
        slots["qnh"] = qnh.group(1)

    slots["_normalized"] = text
    return slots
