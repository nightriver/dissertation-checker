"""Прив'язка жирних шматків звіту Plag до тексту дисертації — PLAN_PLAG_FILTER_V4.md, §6.2, §7 етап 2.

Порівнюються лише нормалізовані потоки: NFKC → нижній регістр → тільки
символи `str.isalnum()`. Так зникають розбіжності, яких Plag додає до тексту:
склеєні без пробілу абзаци («[506].Але»), перенесення, лапки, апострофи.
Пошук — лише точний збіг рядків, без нечітких порівнянь.

Шматок, який трапляється в дисертації кілька разів, прив'язується за
вікном свого фрагмента (фрагмент цілком знайдено рівно один раз), інакше —
за єдиністю довгого шматка, інакше — до найближчого вже знайденого шматка
того самого запису. Не знайдене не відкидається мовчки: його рахує
`locate_entry`.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

import fitz

from plag_filter.v4.report import PlagEntry

# Шматок, коротший за це число нормалізованих символів, не вважається
# єдиним навіть при одному входженні — §5.
MIN_UNIQUE_CHARS = 15

# Аркуші титулу для розпізнавання автора — §6.2.
TITLE_PAGES = 2


def _fold_char(char: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", char).lower() if c.isalnum())


def normalize(text: str) -> str:
    """NFKC → нижній регістр → лише символи `str.isalnum()` — §6.2."""
    return "".join(c for c in unicodedata.normalize("NFKC", text).lower() if c.isalnum())


@dataclass(frozen=True)
class DissText:
    """Нормалізований потік дисертації з координатами кожного символу."""

    stream: str
    char_page: tuple[int, ...]
    char_bbox: tuple[tuple[float, float, float, float], ...]
    word_start: tuple[bool, ...]
    page_count: int
    title_text: str


@dataclass(frozen=True)
class Hit:
    """Знайдений шматок: індекси потоку, `end` не включно."""

    start: int
    end: int
    how: Literal["unique", "fragment", "nearest", "first"]


def page_lines(page: fitz.Page) -> list[dict]:
    """Рядки аркуша `rawdict` у порядку, спільному для потоку й показу тексту."""
    return [
        line for block in page.get_text("rawdict")["blocks"] for line in block.get("lines", [])
    ]


def fold_line(line: dict) -> list[tuple[dict, str]]:
    """Символи рядка `rawdict` і нормалізовані символи, які кожен дає в потік.

    Порожній рядок — символ не дає нічого (не `isalnum` або комбінований
    знак). Комбінований знак (наприклад, бреве над «и») складається з
    попередньою літерою, як це робить NFKC у `normalize`. Ту саму функцію
    використовують `load_dissertation` і показ тексту аркуша, тож індекси
    потоку в них збігаються.
    """
    items: list[list] = []
    previous_alnum = False
    for span in line["spans"]:
        for raw_char in span["chars"]:
            char = raw_char["c"]
            if unicodedata.combining(char):
                if previous_alnum:
                    for item in reversed(items):
                        if item[1]:
                            composed = _fold_char(item[1][-1] + char)
                            if len(composed) == 1:
                                item[1] = item[1][:-1] + composed
                            break
                items.append([raw_char, ""])
                continue
            folded = _fold_char(char)
            items.append([raw_char, folded])
            previous_alnum = bool(folded)
    return [(raw_char, folded) for raw_char, folded in items]


def load_dissertation(data: bytes) -> DissText:
    """Потік дисертації з `get_text("rawdict")` — §6.2.

    Кінець рядка — межа слова. Комбінований знак складається з попередньою
    літерою (`fold_line`).
    """
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        chars: list[str] = []
        pages: list[int] = []
        boxes: list[tuple[float, float, float, float]] = []
        starts: list[bool] = []
        for page_index in range(doc.page_count):
            for line in page_lines(doc[page_index]):
                previous_alnum = False
                for raw_char, folded in fold_line(line):
                    if unicodedata.combining(raw_char["c"]):
                        continue
                    if not folded:
                        previous_alnum = False
                        continue
                    box = tuple(float(v) for v in raw_char["bbox"])
                    for position, out in enumerate(folded):
                        chars.append(out)
                        pages.append(page_index)
                        boxes.append(box)  # type: ignore[arg-type]
                        starts.append(position == 0 and not previous_alnum)
                    previous_alnum = True
        title_text = "".join(
            doc[index].get_text() for index in range(min(TITLE_PAGES, doc.page_count))
        )
        return DissText(
            stream="".join(chars),
            char_page=tuple(pages),
            char_bbox=tuple(boxes),
            word_start=tuple(starts),
            page_count=doc.page_count,
            title_text=title_text,
        )
    finally:
        doc.close()


def _single_occurrence(stream: str, needle: str) -> int | None:
    first = stream.find(needle)
    if first < 0 or stream.find(needle, first + 1) >= 0:
        return None
    return first


def _occurrences(stream: str, needle: str) -> list[int]:
    found: list[int] = []
    position = stream.find(needle)
    while position >= 0:
        found.append(position)
        position = stream.find(needle, position + 1)
    return found


def locate_entry(entry: PlagEntry, diss: DissText) -> tuple[list[Hit], int]:
    """Знайти жирні шматки запису в потоці дисертації — §6.2.

    Повертає знайдені шматки й кількість не знайдених. Порожній після
    нормалізації шматок пропускається без лічильника.
    """
    stream = diss.stream
    hits: list[Hit] = []
    missing = 0
    last_start: int | None = None

    for fragment in entry.fragments:
        window: tuple[int, int] | None = None
        normalized_fragment = normalize(fragment.text)
        if normalized_fragment:
            position = _single_occurrence(stream, normalized_fragment)
            if position is not None:
                window = (position, position + len(normalized_fragment))
        cursor = window[0] if window else 0

        for run in fragment.runs:
            needle = normalize(run)
            if not needle:
                continue

            hit: Hit | None = None
            if window is not None:
                found = stream.find(needle, cursor, window[1])
                if found < 0:
                    found = stream.find(needle, window[0], window[1])
                if found >= 0:
                    hit = Hit(found, found + len(needle), "fragment")
                    cursor = found + len(needle)

            if hit is None and len(needle) >= MIN_UNIQUE_CHARS:
                position = _single_occurrence(stream, needle)
                if position is not None:
                    hit = Hit(position, position + len(needle), "unique")

            if hit is None:
                found_all = _occurrences(stream, needle)
                if found_all:
                    if last_start is None:
                        hit = Hit(found_all[0], found_all[0] + len(needle), "first")
                    else:
                        anchor = last_start
                        best = min(found_all, key=lambda p: (abs(p - anchor), p))
                        hit = Hit(best, best + len(needle), "nearest")

            if hit is None:
                missing += 1
                continue
            hits.append(hit)
            last_start = hit.start

    return hits, missing
