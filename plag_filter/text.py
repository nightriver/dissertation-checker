"""Текст аркуша звіту Plag з прив'язкою фрагментів до джерел і показники шапки.

Контракт — `PLAN_PLAG_VIEW.md`, §7 етап 1, числа — §5. Текст береться з
`get_text("rawdict")`: рядки сортуються, колонтитул і шапка першого аркуша
тіла відкидаються, кожен символ належить джерелу за прямокутником
`page_overlay`, у який потрапляє його центр. Номер маркера в текст не
потрапляє: його малює плашка компонента.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

import fitz

from plag_filter.pdf import page_overlay
from plag_filter.types import OverlayItem, PlagReport

# Відсічки й поріг абзацу в пунктах, y згори донизу — PLAN_PLAG_VIEW.md, §5.
FOOTER_TOP = 800.0  # рядки з верхом не вище цієї межі — колонтитул Plag
HEADER_BOTTOM = 200.0  # лише на `body_first`: рядки з верхом нижче — шапка «Збіги»
PARAGRAPH_GAP = 23.0  # відстань між верхами рядків, більша за неї, — новий абзац

_TEXT_CACHE_LIMIT = 64
_text_cache: OrderedDict[tuple[str, int], tuple[tuple["Segment", ...], ...]] = OrderedDict()

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Segment:
    """Шматок абзацу: звичайний текст, фрагмент джерела або маркер номера."""

    text: str  # для маркера — "" (номер малює плашка)
    number: int | None  # джерело; None — звичайний текст
    marker: bool  # True — маркер номера Plag (зелений прямокутник)


Paragraph = tuple[Segment, ...]


def _owner(items: list[OverlayItem], cx: float, cy: float) -> OverlayItem | None:
    for item in items:
        if item.x0 <= cx <= item.x1 and item.y0 <= cy <= item.y1:
            return item
    return None


def _finish(raw: list[Segment]) -> Paragraph:
    """Схлопнути пробіли, злити сусідні фрагменти одного джерела, викинути порожні."""
    merged: list[Segment] = []
    for segment in raw:
        if segment.marker:
            merged.append(segment)
            continue
        text = _SPACES.sub(" ", segment.text)
        previous_text = next((s.text for s in reversed(merged) if not s.marker), "")
        if (not previous_text or previous_text.endswith(" ")) and text.startswith(" "):
            text = text[1:]
        if not text:
            continue
        last = merged[-1] if merged else None
        if last is not None and not last.marker and last.number == segment.number:
            merged[-1] = Segment(last.text + text, last.number, False)
        else:
            merged.append(Segment(text, segment.number, False))

    # Кінцевий пробіл абзацу не потрібен.
    for index in range(len(merged) - 1, -1, -1):
        segment = merged[index]
        if segment.marker:
            continue
        stripped = segment.text.rstrip()
        if stripped:
            merged[index] = Segment(stripped, segment.number, False)
            break
        del merged[index]
    return tuple(merged)


def _extract(data: bytes, report: PlagReport, page_index: int) -> tuple[Paragraph, ...]:
    items = page_overlay(data, report, page_index)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[page_index]
        width = page.rect.width
        height = page.rect.height
        lines = [
            line
            for block in page.get_text("rawdict")["blocks"]
            for line in block.get("lines", ())
        ]
    finally:
        doc.close()

    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    paragraphs: list[Paragraph] = []
    current: list[Segment] = []
    previous_top: float | None = None
    for line in lines:
        top = line["bbox"][1]
        if top >= FOOTER_TOP:
            continue
        if page_index == report.body_first and top < HEADER_BOTTOM:
            continue
        if previous_top is not None:
            if top - previous_top > PARAGRAPH_GAP:
                paragraphs.append(_finish(current))
                current = []
            else:
                # Рядки одного абзацу з'єднуються одним пробілом.
                current.append(Segment(" ", _last_number(current), False))
        previous_top = top

        # Підсвічування Plag не завжди збігається з межами спану, тому джерело
        # визначається для кожного символу окремо.
        marker_open: OverlayItem | None = None
        for span in line["spans"]:
            for char in span["chars"]:
                x0, y0, x1, y1 = char["bbox"]
                owner = _owner(items, (x0 + x1) / 2 / width, (y0 + y1) / 2 / height)
                if owner is not None and owner.kind == "marker":
                    if owner is not marker_open:
                        current.append(Segment("", owner.number, True))
                        marker_open = owner
                    continue
                marker_open = None
                current.append(Segment(char["c"], owner.number if owner else None, False))

    if current:
        paragraphs.append(_finish(current))
    return tuple(paragraph for paragraph in paragraphs if paragraph)


def _last_number(segments: list[Segment]) -> int | None:
    """Номер останнього текстового шматка: пробіл між рядками йде до нього."""
    for segment in reversed(segments):
        if not segment.marker:
            return segment.number
    return None


def page_text(data: bytes, report: PlagReport, page_index: int) -> tuple[Paragraph, ...]:
    """Абзаци аркуша тіла дисертації — PLAN_PLAG_VIEW.md, §7 етап 1.

    `page_index` — індекс аркуша PDF з нуля. Поза тілом (`body_first` …
    `list_first − 1`) — порожній кортеж. `UnsupportedReportError` з
    `page_overlay` не перехоплюється.
    """
    if not report.body_first <= page_index < report.list_first:
        return ()
    key = (report.sha256, page_index)
    cached = _text_cache.get(key)
    if cached is not None:
        _text_cache.move_to_end(key)
        return cached

    result = _extract(data, report, page_index)

    _text_cache[key] = result
    _text_cache.move_to_end(key)
    if len(_text_cache) > _TEXT_CACHE_LIMIT:
        _text_cache.popitem(last=False)
    return result


# Мітки показників і індекс аркуша, на якому їх друкує Plag — PLAN_PLAG_VIEW.md, §7 етап 1.
_SCORE_LABELS: tuple[tuple[str, str, int], ...] = (
    ("similarity", "Оцінка схожості", 0),
    ("risk", "Ризик плагіату", 0),
    ("paraphrase", "Перефразування", 2),
    ("wrong_citation", "Неправильне цитування", 2),
    ("text_matches", "Збіги тексту", 2),
)


def _value_after(lines: list[str], label: str) -> str | None:
    for index, line in enumerate(lines):
        if line.strip() == label:
            for following in lines[index + 1 :]:
                if following.strip():
                    return following.strip()
            return None
    return None


def report_scores(data: bytes) -> dict[str, str | None]:
    """Показники шапки так, як їх надрукував Plag; не знайдено — `None`."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        lines_by_page: dict[int, list[str]] = {}
        for _key, _label, page_index in _SCORE_LABELS:
            if page_index < doc.page_count and page_index not in lines_by_page:
                lines_by_page[page_index] = doc[page_index].get_text().splitlines()
    finally:
        doc.close()
    return {
        key: _value_after(lines_by_page.get(page_index, []), label)
        for key, label, page_index in _SCORE_LABELS
    }
