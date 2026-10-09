"""Short channel names from LMS course titles.

Rules go by the shape of a title (bracket tags, a leading term code, parentheses, a
trailing section number), never by one school's strings, so any LMS can use them.
"""

import re
from typing import NamedTuple


class CourseName(NamedTuple):
    title: str
    section: str | None  # e.g. "02분반", "Section 2"; used only to tell names apart


_BRACKETS = re.compile(r"\[[^\]]*\]")
# Whole leading tokens such as 262R, 2026, 2026-1, 26F, Spring, 1학기. "3D" or "CS101"
# don't match: a term code has at least two leading digits or is a season word.
_TERM = re.compile(
    r"^(?:\d{2,4}(?:[-/.]\d{1,2})?[A-Za-z]?|spring|summer|fall|autumn|winter"
    r"|봄|여름|가을|겨울|\d학기)(?:학기)?\s+",
    re.IGNORECASE,
)
_SECTIONS = [
    re.compile(r"\s*[-–]\s*(\d{1,3}\s*(?:분반|반)?)\s*$"),
    re.compile(r"\s+((?:section|sec\.?)\s*\d{1,3})\s*$", re.IGNORECASE),
    re.compile(r"\s*(\d{1,3}\s*(?:분반|반))\s*$"),
    re.compile(r"\s*\(\s*(\d{1,3}\s*(?:분반|반)?)\s*\)\s*$"),
]
_INNER_PARENS = re.compile(r"\([^()]*\)")
_EDGES = " \t-–_,:·|/"


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(_EDGES)


def clean_course_name(raw: str) -> CourseName:
    text = _tidy(_BRACKETS.sub(" ", raw))
    section = None
    for pattern in _SECTIONS:
        if match := pattern.search(text):
            section = match.group(1)
            text = _tidy(text[: match.start()])
            break
    while match := _TERM.match(text):
        text = text[match.end() :]
    while _INNER_PARENS.search(text):
        text = _INNER_PARENS.sub(" ", text)
    text = _tidy(text)
    if len(text) < 2:
        return CourseName(raw, None)
    return CourseName(text, section)
