"""Concept(slot)-level extraction from normalized ATC utterances.

Extracted safety slots (mandatory readback items per ICAO Doc 4444 4.5.7.5.1):
  callsign, clearance, runway, hold_short, taxi_to, route,
  altitude (ft), heading (3-digit), speed (kt), frequency, squawk, qnh
"""

from __future__ import annotations

import re
from typing import Any

from .normalizer import normalize

# Words that precede a number in standard phraseology and must never be
# mistaken for the letter part of a callsign (e.g. "QNH 1013" != QNH1013).
_NON_CALLSIGN_WORDS = {
    "via", "and", "the", "for", "to", "of", "at", "on",
    "runway", "level", "heading", "squawk", "qnh", "wind", "gate",
    "stand", "taxi", "hold", "turn", "climb", "down", "up", "point",
    "left", "right", "fly", "over", "wait", "line", "cross",
}

_SIDE = r"(left|right|center|[lrcLRC])"
# 1-4 letters + 1-4 digits; single letters cover military types ("F16").
# Matched against text with all slot phrases scrubbed out, so slot values
# ("runway 34", "via A 1", "QNH 1013") can never be mistaken for callsigns.
_CALLSIGN_RE = re.compile(r"\b([a-zA-Z]{1,4})[\s\-]?(\d{1,4})\b")
_RUNWAY_RE = re.compile(rf"runway\s+(\d{{1,2}})\s*{_SIDE}?\b")
# "hold short" (instruction) and "holding short" (standard pilot readback)
_HOLD_SHORT_RE = re.compile(
    rf"hold(?:ing)?\s+short\s+(?:of\s+)?(?:runway\s+)?(\d{{1,2}})\s*{_SIDE}?\b"
)
_TAXI_RE = re.compile(
    rf"taxi\s+to\s+(?:holding\s+point\s+)?(?:runway\s+)?(\d{{1,2}})\s*{_SIDE}?"
)
# taxiway designators: letters with optional numeric suffix ("A", "A 1" -> A1)
_ROUTE_RE = re.compile(r"via((?:\s+[A-Z](?:\s?\d{1,2})?)+)")
_ALTITUDE_RE = re.compile(
    r"\b(?:climb|descend|maintain|down|up)\b(?:\s+and\s+maintain)?(?:\s+to)?"
    r"\s+(\d{3,5})(?!\s*knots)(?:\s*(?:feet|ft))?"
)
_FLIGHT_LEVEL_RE = re.compile(r"flight\s+level\s+(\d{2,3})")
_HEADING_RE = re.compile(r"heading\s+(\d{1,3})")
_FREQUENCY_RE = re.compile(r"\b(1[0-3]\d\.\d{1,3})\b")
_SQUAWK_RE = re.compile(r"squawk(?:ing)?\s+(\d{4})")
_QNH_RE = re.compile(r"(?:qnh|altimeter)\s+(\d{3,4})")
# speed instructions are a mandatory readback item (ICAO Doc 4444 4.5.7.5.1)
_SPEED_KW_RE = re.compile(r"speed\s+(?:to\s+)?(\d{2,3})\b")
_SPEED_KT_RE = re.compile(r"\b(\d{2,3})\s+knots\b")
# wind reports ("wind 270 at 10 knots") carry knots values that are not
# speed instructions — scrubbed before speed extraction
_WIND_RE = re.compile(r"\bwind\s+\d{1,3}(?:\s+degrees)?\s+(?:at\s+)?\d{1,3}(?:\s*knots)?")

_SIDE_MAP = {"left": "L", "right": "R", "center": "C", "l": "L", "r": "R", "c": "C"}


def _fmt_runway(num: str, side: str | None) -> str:
    rwy = f"{int(num):02d}"
    if side:
        rwy += _SIDE_MAP[side.lower()]
    return rwy


def _detect_clearance(text: str) -> str | None:
    # each pattern also accepts the continuous form pilots use in readbacks
    # ("crossing", "lining up") — ICAO Doc 4444 treats them as equivalent
    if re.search(r"cleared\s+for\s+(?:immediate\s+)?take\s?off", text):
        return "takeoff"
    if re.search(r"cleared\s+to\s+land", text):
        return "land"
    if re.search(r"\blin(?:e|ing)\s+up(?:\s+and\s+wait)?", text):
        return "line_up_wait"
    if re.search(r"\bcross(?:ing)?\s+runway", text):
        return "cross"
    if re.search(r"\btaxi\s+to\b", text):
        return "taxi"
    return None


def extract_slots(utterance: str) -> dict[str, Any]:
    """Extract safety slots from a raw ATC utterance."""
    text = normalize(utterance)
    slots: dict[str, Any] = {}

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
        # join alphanumeric designators split by the normalizer ("A 1" -> "A1")
        # so a wrong-taxiway readback (A1 vs A2) is comparable
        parsed: list[str] = []
        for tok in route.group(1).split():
            if tok.isdigit() and parsed:
                parsed[-1] += tok
            else:
                parsed.append(tok)
        slots["route"] = parsed

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

    spd_text = _WIND_RE.sub(" ", text)
    spd = _SPEED_KW_RE.search(spd_text) or _SPEED_KT_RE.search(spd_text)
    if spd:
        slots["speed"] = int(spd.group(1))

    # callsign last, on text with every recognized slot phrase scrubbed out
    cs_text = _WIND_RE.sub(" ", scrubbed)
    for pat in (
        _RUNWAY_RE, _ROUTE_RE, _FLIGHT_LEVEL_RE, _ALTITUDE_RE,
        _HEADING_RE, _FREQUENCY_RE, _SQUAWK_RE, _QNH_RE,
        _SPEED_KW_RE, _SPEED_KT_RE,
    ):
        cs_text = pat.sub(" ", cs_text)
    for m in _CALLSIGN_RE.finditer(cs_text):
        if m.group(1).lower() in _NON_CALLSIGN_WORDS:
            continue
        slots["callsign"] = (m.group(1) + m.group(2)).upper()
        break

    slots["_normalized"] = text
    return slots
