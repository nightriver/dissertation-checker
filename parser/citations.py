"""
citations.py — Bug fixes: Wingdings brackets, U+2212, multiline, comma-mode.
"""
from __future__ import annotations
import re

_DASHES = "[\u002d\u2013\u2212]"
_INNER_CHARS = r"[\d\s;,\u002d\u2013\u2212\.]"

# Standard [ ] and Wingdings PUA \uF05B / \uF05D
_BRACKET_RE = re.compile(
    r"(?:\[|\uF05B)(" + _INNER_CHARS + r"+)(?:\]|\uF05D)"
)
_RANGE_RE = re.compile(r"^(\d+)\s*" + _DASHES + r"\s*(\d+)$")


def _parse_token_as_source(token: str) -> set[int]:
    token = token.strip()
    if not token:
        return set()
    m = _RANGE_RE.match(token)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi and (hi - lo) <= 200:
            return set(range(lo, hi + 1))
        return set()
    if token.isdigit():
        return {int(token)}
    return set()


def _expand_bracket(content: str) -> set[int]:
    """
    Comma semantics depends on presence of semicolons:
      WITH    ';': comma = page separator  → [1, 15; 3; 5, 20-25] → {1,3,5}
      WITHOUT ';': comma = source sep      → [3, 7, 11] → {3,7,11}
    """
    result: set[int] = set()
    if ";" in content:
        for raw_group in content.split(";"):
            group = raw_group.strip()
            if group:
                result |= _parse_token_as_source(group.split(",")[0])
    else:
        for token in content.split(","):
            result |= _parse_token_as_source(token)
    return result


def find_citations(body_lines: list[dict]) -> set[int]:
    """
    Find all cited source numbers in body zone.
    Handles multiline citations via sliding two-line window.
    """
    used: set[int] = set()
    raw = [item["line"] for item in body_lines]

    def _scan(text: str) -> None:
        for match in _BRACKET_RE.finditer(text):
            used.update(_expand_bracket(match.group(1)))

    for i, line in enumerate(raw):
        _scan(line)
        if i + 1 < len(raw):
            _scan(line + " " + raw[i + 1])
    return used


def compare(bibliography: dict[int, str], citations: set[int]) -> dict:
    all_sources = set(bibliography.keys())
    return {
        "all_sources": all_sources,
        "used": citations & all_sources,
        "orphans": all_sources - citations,
        "phantom": citations - all_sources,
    }
