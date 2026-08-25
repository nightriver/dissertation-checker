"""Пошукова нормалізація зі збереженням координат оригіналу."""

from __future__ import annotations

import unicodedata

from compare.types import CompareToken, TokenPart
from parser.text_forensics import normalize_mixed_homoglyphs
from parser.types import LineItem


_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "´": "'"})
_HYPHENS = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})
_LINE_HYPHENS = frozenset("-‐‑‒–—−")


def _is_invisible(char: str) -> bool:
    return unicodedata.category(char) in {"Cc", "Cf", "Cs"}


def normalize_search_token(raw: str) -> str:
    """Нормалізує пошуковий токен, не перетворюючи звичайну латиницю."""
    clean = "".join(char for char in raw if not _is_invisible(char))
    clean = unicodedata.normalize("NFKC", clean)
    clean = clean.translate(_APOSTROPHES).translate(_HYPHENS).lower()
    return normalize_mixed_homoglyphs(clean)


def _line_tokens(text: str, line_index: int, page: int | None) -> list[CompareToken]:
    tokens: list[CompareToken] = []
    start: int | None = None

    def append(end: int) -> None:
        nonlocal start
        if start is None:
            return
        raw = text[start:end]
        normalized = normalize_search_token(raw)
        if normalized:
            tokens.append(CompareToken(
                raw=raw,
                normalized=normalized,
                parts=(TokenPart(line_index, start, end, page),),
            ))
        start = None

    for index, char in enumerate(text):
        if char.isalnum() or _is_invisible(char):
            if start is None:
                start = index
        else:
            append(index)
    append(len(text))
    return tokens


def _has_trailing_hyphen(text: str, token: CompareToken) -> bool:
    tail = text[token.parts[-1].char_end:].strip()
    return len(tail) == 1 and tail in _LINE_HYPHENS


def _starts_with_token(text: str, token: CompareToken) -> bool:
    return not text[:token.parts[0].char_start].strip()


def tokenize_lines(lines: list[LineItem]) -> list[CompareToken]:
    """Будує токени та склеює перенос лише між сусідніми рядками."""
    result: list[CompareToken] = []
    previous_line_tokens: list[CompareToken] = []
    previous_text = ""
    previous_index = -2

    for line_index, item in enumerate(lines):
        text = item.get("line") or ""
        current = _line_tokens(text, line_index, item.get("page"))
        if (
            result
            and previous_line_tokens
            and current
            and previous_index + 1 == line_index
            and _has_trailing_hyphen(previous_text, previous_line_tokens[-1])
            and _starts_with_token(text, current[0])
        ):
            left = result.pop()
            right = current[0]
            merged = CompareToken(
                raw=left.raw + right.raw,
                normalized=left.normalized + right.normalized,
                parts=left.parts + right.parts,
            )
            result.append(merged)
            current = current[1:]

        result.extend(current)
        previous_line_tokens = _line_tokens(text, line_index, item.get("page"))
        previous_text = text
        previous_index = line_index

    return result
