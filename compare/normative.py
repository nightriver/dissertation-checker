"""Обережна евристика ймовірно нормативних формулювань."""

from __future__ import annotations

import re
from collections.abc import Sequence


NORMATIVE_EXACT = frozenset({
    "стаття", "статті", "статтею",
    "частина", "частини", "частиною",
    "пункт", "пункту", "абзац",
    "закон", "закону", "законом", "законодавство",
    "кодекс", "кодексу", "кк", "кпк", "цк", "цпк", "кзпп", "купап",
    "постанова", "наказ", "указ", "розпорядження",
    "відповідно", "згідно",
    "чинного", "редакції", "набрання", "чинності",
})
NORMATIVE_PREFIXES = ("передбачен", "встановлен", "визначен")
NORMATIVE_RE = (
    re.compile(r"\bч\.?\s*\d+\s*ст\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\bст\.?\s*\d+", re.IGNORECASE),
    re.compile(r"№\s*\d+[-–]?[IVXLC]*", re.IGNORECASE),
)

_AUTHOR_PREFIXES = (
    "пропону", "вважа", "обґрунтован", "запропонован", "удосконален", "доцільн",
)
_AUTHOR_PHRASES = ("на нашу думку",)
_UNDERSTAND_RE = re.compile(r"\bпід\b.{0,80}\bрозуміти\b", re.IGNORECASE)


def count_normative_refs(text: str) -> int:
    """Рахує посилання без подвійного обліку вкладених збігів."""
    spans: list[tuple[int, int]] = []
    for pattern in NORMATIVE_RE:
        for match in pattern.finditer(text):
            if not any(match.start() < end and start < match.end() for start, end in spans):
                spans.append(match.span())
    return len(spans)


def is_normative_token(token: str) -> bool:
    lowered = token.casefold()
    return lowered in NORMATIVE_EXACT or any(
        lowered.startswith(prefix) for prefix in NORMATIVE_PREFIXES
    )


def has_author_marker(tokens: Sequence[str], raw_text: str) -> bool:
    lowered = raw_text.casefold()
    return (
        any(token.casefold().startswith(_AUTHOR_PREFIXES) for token in tokens)
        or any(phrase in lowered for phrase in _AUTHOR_PHRASES)
        or bool(_UNDERSTAND_RE.search(raw_text))
    )


def is_possibly_normative(tokens: Sequence[str], raw_text: str) -> bool:
    if not tokens or has_author_marker(tokens, raw_text):
        return False
    density = sum(is_normative_token(token) for token in tokens) / len(tokens)
    return density >= 0.25 or count_normative_refs(raw_text) >= 2
