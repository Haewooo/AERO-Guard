"""ATC utterance normalization.

Converts spoken-style ATC English into a canonical token stream:
- ICAO phonetic alphabet words -> single uppercase letters (alpha -> A)
- spoken digits (including "niner", "fife", "tree") -> digit characters
- digit sequences merged ("three six" -> "36"), preserving leading zeros
- "thousand"/"hundred" multipliers ("four thousand five hundred" -> "4500")
- "decimal"/"point" joins ("one one eight decimal seven" -> "118.7")
"""

from __future__ import annotations

import re

PHONETIC = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D",
    "echo": "E", "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I",
    "juliett": "J", "juliet": "J", "kilo": "K", "lima": "L", "mike": "M",
    "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R",
    "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "xray": "X", "yankee": "Y", "zulu": "Z",
}

DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "tree": "3",
    "four": "4", "fower": "4", "five": "5", "fife": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "niner": "9",
}

MULTIPLIERS = {"thousand": 1000, "hundred": 100}
DECIMAL_WORDS = {"decimal", "point"}

_PUNCT_RE = re.compile(r"[^\w\s.\-]")
# keep only decimal points (digit.digit, e.g. "118.7"); sentence punctuation
# glued to words by ASR ("niner.", "one.") must not defeat digit-word lookup
_STRAY_DOT_RE = re.compile(r"(?<!\d)\.|\.(?!\d)")
_WORD_SPLIT_RE = re.compile(r"[\s\-]+")


def _flush_number(total: int, cur: str, out: list[str]) -> None:
    if total > 0:
        out.append(str(total + int(cur or 0)))
    elif cur:
        out.append(cur)


def normalize(text: str) -> str:
    """Return normalized utterance as a single spaced string."""
    cleaned = _STRAY_DOT_RE.sub(" ", _PUNCT_RE.sub(" ", text.strip().lower()))
    raw_tokens = _WORD_SPLIT_RE.split(cleaned)
    tokens: list[str] = []
    for tok in raw_tokens:
        if not tok:
            continue
        if tok in PHONETIC:
            tokens.append(PHONETIC[tok])
        elif tok in DIGIT_WORDS:
            tokens.append(DIGIT_WORDS[tok])
        elif len(tok) == 1 and tok.isalpha():
            # bare letters are taxiway/phonetic designators in ATC text
            # ("via A" typed instead of "via alpha") — canonical uppercase
            tokens.append(tok.upper())
        else:
            tokens.append(tok)

    out: list[str] = []
    total = 0
    cur = ""
    decimal_left: str | None = None

    def close_number() -> None:
        nonlocal total, cur, decimal_left
        if decimal_left is not None:
            frac = str(total + int(cur or 0)) if (total or cur) else "0"
            out.append(f"{decimal_left}.{frac}")
        else:
            _flush_number(total, cur, out)
        total = 0
        cur = ""
        decimal_left = None

    for tok in tokens:
        if tok.isdigit():
            cur += tok
        elif tok in MULTIPLIERS and (cur or total):
            total += int(cur or "1") * MULTIPLIERS[tok]
            cur = ""
        elif tok in DECIMAL_WORDS and (cur or total):
            decimal_left = str(total + int(cur or 0))
            total = 0
            cur = ""
        elif re.fullmatch(r"\d+\.\d+", tok):
            close_number()
            out.append(tok)
        else:
            close_number()
            out.append(tok)
    close_number()
    return " ".join(out)
