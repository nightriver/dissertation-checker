"""
citations.py — Citation parser with full format support.

find_citations() returns dict[int, str]:
    key   = source number
    value = first bracket string where this source was found
            e.g. {50: "[49; 51; 55; 60; 63–65]", 89: "[89, с. 11; 98]"}

This lets the caller show the reader exactly which bracket to search for
in the original document — useful for verifying citations manually.

Supported bracket formats:
  [1]                     → {1: "[1]"}
  [1, 15]                 → {1: "[1, 15]", 15: "[1, 15]"}   no ';' → comma = source sep
  [1; 3; 7]               → {1: "...", 3: "...", 7: "..."}
  [1, 15; 3; 5, 20-25]    → {1,3,5}   has ';' → comma = page separator
  [89, с. 11]             → {89: "[89, с. 11]"}
  [15-18] / [15–18] / [15−18] → {15,16,17,18}  all dash variants
  \\uF05B94; 108\\uF05D   → {94, 108}  Wingdings/Symbol PUA brackets
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

_DASHES = "[\u002d\u2013\u2212]"

# Inner bracket chars: digits, whitespace, ;,. dashes + Cyrillic с/С (page marker)
# U+0441/U+0421 = Cyrillic с/С  (page marker «с. 11»)
# U+0063/U+0043 = Latin   c/C   (same marker in some dissertations «c. 35»)
_INNER = r"[\d\s;,\u002d\u2013\u2212\.\u0441\u0421\u0063\u0043]"

# Standard [ ] and Wingdings PUA \uF05B / \uF05D
_BRACKET_RE = re.compile(
    r"(?:\[|\uF05B)(" + _INNER + r"+)(?:\]|\uF05D)"
)

_RANGE_RE = re.compile(r"^(\d+)\s*" + _DASHES + r"\s*(\d+)$")

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_token(token: str) -> set[int]:
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
      WITH    ';': comma = page separator  → take only first token of each ;-group
      WITHOUT ';': comma = source separator → all tokens are sources
    """
    result: set[int] = set()
    if ";" in content:
        for group in content.split(";"):
            group = group.strip()
            if group:
                result |= _parse_token(group.split(",")[0])
    else:
        for token in content.split(","):
            result |= _parse_token(token)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_citations(body_lines: list[dict]) -> dict[int, str]:
    """
    Scans body zone and returns a dict mapping each cited source number
    to the first bracket string in which it appeared.

        {50: "[49; 51; 55; 60; 63–65]", 89: "[89, с. 11; 98]", ...}

    The bracket string is the original text including the outer brackets,
    useful for manual verification via Ctrl+F in the original document.

    Uses full-text join to handle brackets split across any number of lines
    by PyMuPDF.
    """
    full_text = " ".join(item["line"] for item in body_lines)
    result: dict[int, str] = {}

    for match in _BRACKET_RE.finditer(full_text):
        bracket_str = match.group(0)   # full bracket including [ ]
        inner = match.group(1)
        for num in _expand_bracket(inner):
            if num not in result:      # keep first occurrence only
                result[num] = bracket_str

    return result


def compare(bibliography: dict[int, str], citations: "dict[int, str] | set[int]") -> dict:
    """
    Compares bibliography dict with citations.

    citations can be:
      - dict[int, str]  (returned by find_citations)
      - set[int]        (accepted for backwards-compat and tests)

    Returns:
        all_sources : set[int]
        used        : set[int]
        orphans     : set[int]  — in bibliography but never cited
        phantom     : set[int]  — cited but not in bibliography
    """
    all_sources = set(bibliography.keys())
    cited_nums = set(citations.keys()) if isinstance(citations, dict) else set(citations)
    return {
        "all_sources": all_sources,
        "used": cited_nums & all_sources,
        "orphans": all_sources - cited_nums,
        "phantom": cited_nums - all_sources,
    }
