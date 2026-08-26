"""
search/normalization.py
Єдина нормалізація та токенізація тексту для режиму пошуку, з картою
символів `NormalizedText.origins` до вихідного `raw_text`. Специфікація —
PLAN_SEARCH.md, §7.

Крок 3 (§22) реалізує лише мінімальний тонкий зріз конвеєра: NFKC,
видалення м'якого переносу (soft hyphen) та символьну карту походження.
Нормалізація апострофів, гоміогліфів, склейка переносу через дефіс і повна
токенізація зі скороченнями — крок 4.
"""

from __future__ import annotations

import re
import unicodedata

from search.types import CharOrigin, NormalizedText, RawSpan, SearchToken, SourceSpan

# Символ м'якого переносу (soft hyphen) видаляється без сліду в
# нормалізованому тексті; вихідний `raw_text` не змінюється (§7, п.3).
_SOFT_HYPHEN = "­"

# Слово: буквено-цифрові символи з можливими внутрішніми апострофом/дефісом
# (для української це не розбиває "розум'я", "будь-який" тощо). Число:
# послідовність цифр з десятковим/тисячним роздільником всередині.
WORD_TOKEN_RE = re.compile(
    r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d+(?:[.,]\d+)*",
    re.UNICODE,
)


def normalize_text(raw_text: str) -> NormalizedText:
    """
    Мінімальна нормалізація: Unicode NFKC посимвольно та видалення
    soft hyphen, з побудовою `origins` — кожен вихідний символ вказує на
    свій вихідний символьний інтервал у `raw_text`.

    Посимвольний NFKC не обробляє багатосимвольні графемні кластери (напр.
    комбіновані діакритики), що коректно для звичайного українського/
    російського тексту без таких кластерів; повна обробка — крок 4 (§7).
    """
    chars: list[str] = []
    origins: list[CharOrigin] = []
    for i, ch in enumerate(raw_text):
        if ch == _SOFT_HYPHEN:
            continue
        piece = unicodedata.normalize("NFKC", ch)
        if not piece:
            continue
        origin = CharOrigin(raw_start=i, raw_end=i + 1)
        for out_ch in piece:
            chars.append(out_ch)
            origins.append(origin)
    return NormalizedText(text="".join(chars), origins=tuple(origins))


def map_normalized_offsets(
    normalized: NormalizedText, start: int, end: int
) -> tuple[tuple[int, int], ...]:
    """
    Перетворює півінтервал `[start, end)` нормалізованого тексту на
    впорядкований список невічних (raw_start, raw_end) інтервалів вихідного
    тексту, об'єднуючи суміжні вихідні інтервали (§7).
    """
    if start < 0 or end > len(normalized.origins) or start >= end:
        raise ValueError("Некоректний нормалізований діапазон")

    origins = normalized.origins[start:end]
    parts: list[tuple[int, int]] = []
    cur_start, cur_end = origins[0].raw_start, origins[0].raw_end
    for origin in origins[1:]:
        if origin.raw_start <= cur_end:
            cur_end = max(cur_end, origin.raw_end)
        else:
            parts.append((cur_start, cur_end))
            cur_start, cur_end = origin.raw_start, origin.raw_end
    parts.append((cur_start, cur_end))
    return tuple(parts)


def map_normalized_span(
    normalized: NormalizedText,
    start: int,
    end: int,
    *,
    block_id: str,
    physical_page: int,
) -> SourceSpan:
    """Обгортає `map_normalized_offsets` у `SourceSpan` для конкретного блоку."""
    offsets = map_normalized_offsets(normalized, start, end)
    return SourceSpan(
        parts=tuple(RawSpan(block_id, physical_page, s, e) for s, e in offsets)
    )


def tokenize(raw_text: str, normalized: NormalizedText) -> tuple[SearchToken, ...]:
    """
    Токенізація нормалізованого тексту зі збереженням символьних інтервалів
    у нормалізованому та вихідному тексті. Слово — буквена або числова
    послідовність (`WORD_TOKEN_RE`); інші не пробільні символи — по
    одному символьні токени пунктуації.
    """
    text = normalized.text
    tokens: list[SearchToken] = []
    pos = 0
    for match in WORD_TOKEN_RE.finditer(text):
        if match.start() > pos:
            _append_punctuation_tokens(tokens, raw_text, normalized, text, pos, match.start())
        tokens.append(_make_token(raw_text, normalized, text, match.start(), match.end(), True))
        pos = match.end()
    if pos < len(text):
        _append_punctuation_tokens(tokens, raw_text, normalized, text, pos, len(text))
    return tuple(tokens)


def _append_punctuation_tokens(
    tokens: list[SearchToken],
    raw_text: str,
    normalized: NormalizedText,
    text: str,
    start: int,
    end: int,
) -> None:
    for i in range(start, end):
        if text[i].isspace():
            continue
        tokens.append(_make_token(raw_text, normalized, text, i, i + 1, False))


def _make_token(
    raw_text: str,
    normalized: NormalizedText,
    text: str,
    n_start: int,
    n_end: int,
    is_word: bool,
) -> SearchToken:
    raw_start = normalized.origins[n_start].raw_start
    raw_end = normalized.origins[n_end - 1].raw_end
    return SearchToken(
        raw=raw_text[raw_start:raw_end],
        normalized=text[n_start:n_end],
        raw_start=raw_start,
        raw_end=raw_end,
        normalized_start=n_start,
        normalized_end=n_end,
        is_word=is_word,
    )
