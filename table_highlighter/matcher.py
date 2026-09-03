"""Детерміноване зіставлення слів без зміни вихідного тексту DOCX."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from rapidfuzz.distance import Levenshtein

from table_highlighter.zones import ParagraphZone, paragraph_text


# Вихідний алгоритм зіставляє кожен непорожній фрагмент, наприклад «слово,».
# Окремий варіант нижче потрібний лише для ключа «Ін- тернет» → «Інтернет»;
# сам вихідний текст і його межі не змінюються.
TOKEN_RE = re.compile(r"\S+", re.UNICODE)


@dataclass(frozen=True)
class WordToken:
    paragraph_index: int
    start: int
    end: int
    key: str


@dataclass(frozen=True)
class Alignment:
    statuses: dict[int, tuple[str | None, ...]]
    exact_words: int
    fuzzy_words: int


def _key(raw: str) -> str:
    normalized = re.sub(r"(\w)-\s+(\w)", r"\1\2", raw)
    return re.sub(r"[^\w]", "", normalized.lower())


def _tokens(paragraphs: tuple[ParagraphZone, ...]) -> tuple[list[WordToken], dict[int, str]]:
    tokens: list[WordToken] = []
    texts: dict[int, str] = {}
    for item in paragraphs:
        text = paragraph_text(item.paragraph)
        texts[item.index] = text
        matches = tuple(TOKEN_RE.finditer(text))
        position = 0
        while position < len(matches):
            match = matches[position]
            end = match.end()
            raw = match.group()
            # Word часто розділяє перенесене слово на окремі runs. Спочатку
            # знаходимо межі у вихідному тексті, а потім створюємо один ключ,
            # не змінюючи жодного символу, який буде записаний назад у DOCX.
            if position + 1 < len(matches):
                following = matches[position + 1]
                between = text[match.end():following.start()]
                if re.search(r"\w-$", raw) and between.isspace() and re.match(r"\w", following.group()):
                    end = following.end()
                    raw = text[match.start():end]
                    position += 1
            key = _key(raw)
            if key:
                tokens.append(WordToken(item.index, match.start(), end, key))
            position += 1
    return tokens, texts


def _threshold(left: str, right: str, value: int, relax_short_words: bool) -> float:
    if value == 100:
        return 100.0
    if relax_short_words and min(len(left), len(right)) <= 4:
        return 70.0
    return float(value)


def align(
    left_paragraphs: tuple[ParagraphZone, ...],
    right_paragraphs: tuple[ParagraphZone, ...],
    threshold: int,
    relax_short_words: bool,
) -> Alignment:
    """Повертає статуси символів, не нормалізуючи текст, що буде записаний."""
    left, left_texts = _tokens(left_paragraphs)
    right, right_texts = _tokens(right_paragraphs)
    left_status = ["diff"] * len(left)
    right_status = ["diff"] * len(right)
    exact_words = fuzzy_words = 0

    matcher = SequenceMatcher(None, [item.key for item in left], [item.key for item in right], autojunk=False)
    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            for index in range(i1, i2):
                left_status[index] = "match"
                exact_words += 1
            for index in range(j1, j2):
                right_status[index] = "match"
            continue
        if opcode != "replace":
            continue
        used_right: set[int] = set()
        for left_index in range(i1, i2):
            selected: int | None = None
            best_ratio = -1.0
            for right_index in range(j1, j2):
                if right_index in used_right:
                    continue
                required = _threshold(left[left_index].key, right[right_index].key, threshold, relax_short_words)
                ratio = Levenshtein.normalized_similarity(left[left_index].key, right[right_index].key) * 100
                if ratio >= required and ratio > best_ratio:
                    selected = right_index
                    best_ratio = ratio
            if selected is not None:
                left_status[left_index] = right_status[selected] = "match"
                used_right.add(selected)
                fuzzy_words += 1

    statuses: dict[int, list[str | None]] = {
        index: [None] * len(text) for index, text in {**left_texts, **right_texts}.items()
    }
    # Індекси абзаців з різних комірок можуть збігатися, тому будуємо їх окремо.
    left_map = _character_statuses(left, left_status, left_texts)
    right_map = _character_statuses(right, right_status, right_texts)
    # Праві ключі кодуємо негативними, щоб не змішати абзаци комірок.
    merged = {index: tuple(values) for index, values in left_map.items()}
    merged.update({-(index + 1): tuple(values) for index, values in right_map.items()})
    return Alignment(merged, exact_words, fuzzy_words)


def _character_statuses(
    tokens: list[WordToken], statuses: list[str], texts: dict[int, str]
) -> dict[int, list[str | None]]:
    result = {index: [None] * len(text) for index, text in texts.items()}
    for token, status in zip(tokens, statuses):
        result[token.paragraph_index][token.start:token.end] = [status] * (token.end - token.start)
    for paragraph_index, values in result.items():
        text = texts[paragraph_index]
        index = 0
        while index < len(values):
            if values[index] is not None:
                index += 1
                continue
            end = index
            while end < len(values) and values[end] is None:
                end += 1
            previous = values[index - 1] if index else None
            following = values[end] if end < len(values) else None
            # Як у вихідному алгоритмі, фарбуємо лише пробіл між двома
            # фрагментами однакового статусу. Розділовий знак не може
            # успадкувати колір через сусідні слова.
            if text[index:end].isspace() and previous is not None and previous == following:
                values[index:end] = [previous] * (end - index)
            index = end
    return result


def left_statuses(alignment: Alignment) -> dict[int, tuple[str | None, ...]]:
    return {index: value for index, value in alignment.statuses.items() if index >= 0}


def right_statuses(alignment: Alignment) -> dict[int, tuple[str | None, ...]]:
    return {-(index + 1): value for index, value in alignment.statuses.items() if index < 0}
