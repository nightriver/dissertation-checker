"""
search/markers.py
Канали A, N, B, T, L: виявлення кандидатів, сигнали K та оцінка балів.
Специфікація — PLAN_SEARCH.md, §10 та §11.

Крок 3 (§22) реалізує лише канал A (§10.2) — рішення оркестратора для
тонкого наскрізного зрізу: він найдетермінованіший зі змістовних каналів.
Канали N, B, K, T, L, D та множники §11.1 (окрім тривіального випадку
"×1" для розділу без ВСТУП/ВИСНОВКІВ і без наукової новизни) — крок 9.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelASignal:
    """Один збіг маркера каналу A у нормалізованому тексті речення."""
    rule_id: str
    start: int
    end: int


# Балли каналу A (§10.2, §11.1): +2 за кожен збіг, максимум +6.
CHANNEL_A_SIGNAL_SCORE = 2.0
CHANNEL_A_MAX_SCORE = 6.0

# Основа зі слова: після основи має йти українське буквене продовження
# (§10.2, дослівно "з українським буквеним продовженням") — тобто МІНІМУМ
# один буквений символ, самі основи не є словниковими словами ("пропон",
# "вважа" голими не трапляються в українському тексті). Тому `+`, а не `*`.
# Групи впорядковано для стабільного rule_id.
_A_STEMS: tuple[str, ...] = (
    "пропон",
    "вважа",
    "доцільн",
    "обґрунтован",
    "запропонован",
    "удосконален",
)
_UK_LETTER_TAIL = "а-щьюяіїєґ'’-"
_A_STEM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(rf"\b{stem}[{_UK_LETTER_TAIL}]+", re.IGNORECASE | re.UNICODE),
        f"A.stem.{stem}",
    )
    for stem in _A_STEMS
)

# Точні послідовності (§10.2).
_A_PHRASES: tuple[str, ...] = (
    "на нашу думку",
    "дійшли висновку",
    "у такій редакції",
)
_A_PHRASE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE | re.UNICODE), f"A.phrase.{i}")
    for i, phrase in enumerate(_A_PHRASES)
)

# "під", потім 1-8 словесних токенів, потім форма "розуміти" (§10.2).
_A_UNDER_UNDERSTAND_RE = re.compile(
    r"\bпід\b(?:\s+\S+){1,8}?\s+розум[а-щьюяіїєґ]*\b",
    re.IGNORECASE | re.UNICODE,
)

# "нами", необов'язково "було", потім не далі ніж через два токени форма
# "опитано"/"проаналізовано"/"досліджено" (§10.2).
_A_WE_SURVEYED_RE = re.compile(
    r"\bнами\b(?:\s+було\b)?(?:\s+\S+){0,2}?\s+(опитано|проаналізовано|досліджено)\b",
    re.IGNORECASE | re.UNICODE,
)


def find_channel_a_signals(normalized_text: str) -> tuple[ChannelASignal, ...]:
    """Усі збіги маркерів каналу A у нормалізованому тексті, за зростанням позиції."""
    matches: list[ChannelASignal] = []
    for pattern, rule_id in _A_STEM_PATTERNS:
        for m in pattern.finditer(normalized_text):
            matches.append(ChannelASignal(rule_id, m.start(), m.end()))
    for pattern, rule_id in _A_PHRASE_PATTERNS:
        for m in pattern.finditer(normalized_text):
            matches.append(ChannelASignal(rule_id, m.start(), m.end()))
    for m in _A_UNDER_UNDERSTAND_RE.finditer(normalized_text):
        matches.append(ChannelASignal("A.under_understand", m.start(), m.end()))
    for m in _A_WE_SURVEYED_RE.finditer(normalized_text):
        matches.append(ChannelASignal("A.we_surveyed", m.start(), m.end()))
    matches.sort(key=lambda s: (s.start, s.end, s.rule_id))
    return tuple(matches)


def score_channel_a(signals: tuple[ChannelASignal, ...]) -> float:
    """Балл каналу A: +2 за кожен збіг, обмежено зверху 6 (§10.2, §11.1)."""
    return min(len(signals) * CHANNEL_A_SIGNAL_SCORE, CHANNEL_A_MAX_SCORE)
