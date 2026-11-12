"""Текст і аркуш дисертації з підсвіткою джерел — PLAN_PLAG_FILTER_V4.md, §6.4, §7 етап 4.

Аркуш береться з PDF самої дисертації: рядки `rawdict` у тому самому
порядку й з тією самою нормалізацією (`align.fold_line`), що й потік
`DissText`, тож кожен символ аркуша знає свій індекс у потоці. Символ
належить усім джерелам, чиї острови його покривають; яке з них показати,
вирішує `view` за поточними виключеннями.
"""

from __future__ import annotations

import re
import statistics
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass

import fitz

from plag_filter.pdf import UnsupportedReportError
from plag_filter.types import OverlayItem, PlagReport
from plag_filter.v4.align import fold_line, page_lines

# Абзац — PLAN_PLAG_FILTER_V4.md, §5: відступ першого рядка або великий інтервал.
PARAGRAPH_INDENT = 15.0  # pt понад найменший лівий край аркуша
PARAGRAPH_GAP_RATIO = 1.6  # × медіана кроку рядків аркуша

_TEXT_CACHE_LIMIT = 64
_text_cache: OrderedDict[tuple[str, int], tuple[tuple["SegmentV4", ...], ...]] = OrderedDict()

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class SegmentV4:
    """Шматок абзацу: текст і номери джерел, що його покривають, за зростанням."""

    text: str
    numbers: tuple[int, ...]


def _page_bounds(report: PlagReport, page_index: int) -> tuple[int, int]:
    pages = report.v4.diss.char_page
    return bisect_left(pages, page_index), bisect_left(pages, page_index + 1)


def _coverage(report: PlagReport, page_index: int) -> list[tuple[int, int, int]]:
    """Острови, що зачіпають аркуш: (start, end, номер)."""
    covered: list[tuple[int, int, int]] = []
    for number, islands in report.v4.islands.items():
        for island in islands:
            if page_index in island.pages:
                covered.append((island.start, island.end, number))
    return covered


def _numbers_at(index: int, coverage: list[tuple[int, int, int]]) -> tuple[int, ...]:
    return tuple(sorted({number for start, end, number in coverage if start <= index < end}))


def _paragraph_breaks(lines: list[dict]) -> list[bool]:
    """Чи починає рядок новий абзац — §5."""
    if not lines:
        return []
    tops = [line["bbox"][1] for line in lines]
    gaps = [b - a for a, b in zip(tops, tops[1:])]
    positive = [gap for gap in gaps if gap > 1.0]
    step = statistics.median(positive) if positive else 0.0
    left = min(line["bbox"][0] for line in lines)
    breaks = [True]
    for index in range(1, len(lines)):
        gap = gaps[index - 1]
        indented = lines[index]["bbox"][0] >= left + PARAGRAPH_INDENT
        breaks.append(indented or gap < -1.0 or (step > 0 and gap > PARAGRAPH_GAP_RATIO * step))
    return breaks


def _finish(chars: list[tuple[str, tuple[int, ...] | None]]) -> tuple[SegmentV4, ...]:
    """Знаки без власного джерела отримують спільні джерела сусідніх букв;
    однакові сусіди зливаються, пробіли схлопуються."""
    resolved: list[tuple[str, tuple[int, ...]]] = []
    previous: tuple[int, ...] | None = None
    following: list[tuple[int, ...] | None] = [None] * len(chars)
    upcoming: tuple[int, ...] | None = None
    for index in range(len(chars) - 1, -1, -1):
        following[index] = upcoming
        if chars[index][1] is not None:
            upcoming = chars[index][1]
    for index, (char, numbers) in enumerate(chars):
        if numbers is not None:
            previous = numbers
            resolved.append((char, numbers))
            continue
        nxt = following[index]
        shared = tuple(n for n in previous if n in nxt) if previous and nxt else ()
        resolved.append((char, shared))

    segments: list[list] = []
    for char, numbers in resolved:
        if segments and segments[-1][1] == numbers:
            segments[-1][0] += char
        else:
            segments.append([char, numbers])

    result: list[SegmentV4] = []
    for text, numbers in segments:
        text = _SPACES.sub(" ", text)
        if result and result[-1].text.endswith(" ") and text.startswith(" "):
            text = text[1:]
        if not result:
            text = text.lstrip()
        if text:
            result.append(SegmentV4(text, numbers))
    while result and not result[-1].text.strip():
        result.pop()
    if result:
        last = result[-1]
        result[-1] = SegmentV4(last.text.rstrip(), last.numbers)
    return tuple(result)


def _extract(data: bytes, report: PlagReport, page_index: int) -> tuple[tuple[SegmentV4, ...], ...]:
    start, end = _page_bounds(report, page_index)
    coverage = _coverage(report, page_index)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        lines = page_lines(doc[page_index])
    finally:
        doc.close()

    paragraphs: list[tuple[SegmentV4, ...]] = []
    current: list[tuple[str, tuple[int, ...] | None]] = []
    index = start
    for line, new_paragraph in zip(lines, _paragraph_breaks(lines)):
        if new_paragraph and current:
            paragraphs.append(_finish(current))
            current = []
        elif current:
            current.append((" ", None))
        for raw_char, folded in fold_line(line):
            if folded:
                current.append((raw_char["c"], _numbers_at(index, coverage)))
                index += len(folded)
            else:
                current.append((raw_char["c"], None))
    if current:
        paragraphs.append(_finish(current))
    if index != end:
        raise UnsupportedReportError("v4_stream_mismatch")
    return tuple(paragraph for paragraph in paragraphs if paragraph)


def page_text_v4(data: bytes, report: PlagReport, page_index: int) -> tuple[tuple[SegmentV4, ...], ...]:
    """Абзаци аркуша дисертації — §6.4. `data` — PDF дисертації.

    `UnsupportedReportError("v4_stream_mismatch")`: аркуш дав інше число
    символів потоку, ніж при завантаженні дисертації (інший файл).
    """
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


def page_overlay_v4(data: bytes, report: PlagReport, page_index: int) -> list[OverlayItem]:
    """Прямокутники підсвітки аркуша — §6.4: на рядок і джерело один
    прямокутник, координати в частках аркуша. `data` — PDF дисертації."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        rect = doc[page_index].rect
    finally:
        doc.close()
    width, height = rect.width, rect.height
    page_start, page_end = _page_bounds(report, page_index)

    items: list[OverlayItem] = []
    for start, end, number in sorted(_coverage(report, page_index), key=lambda c: (c[2], c[0])):
        for box in line_boxes(report, max(start, page_start), min(end, page_end)):
            items.append(_overlay_item(number, list(box), width, height))
    return items


def line_boxes(report: PlagReport, start: int, end: int) -> list[tuple[float, float, float, float]]:
    """Прямокутники (pt) символів потоку `[start, end)` одного аркуша: один на рядок.

    Наступний символ продовжує прямокутник, якщо його середина по вертикалі
    в межах прямокутника й він не лівіше за початок; інакше — новий рядок.
    """
    diss = report.v4.diss
    boxes: list[list[float]] = []
    for index in range(start, end):
        x0, y0, x1, y1 = diss.char_bbox[index]
        middle = (y0 + y1) / 2
        box = boxes[-1] if boxes else None
        if box is not None and box[1] <= middle <= box[3] and x0 >= box[0] - 1.0:
            box[0] = min(box[0], x0)
            box[1] = min(box[1], y0)
            box[2] = max(box[2], x1)
            box[3] = max(box[3], y1)
        else:
            boxes.append([x0, y0, x1, y1])
    return [tuple(box) for box in boxes]  # type: ignore[misc]


def island_page_spans(report: PlagReport, start: int, end: int) -> list[tuple[int, int, int]]:
    """Частини острова по аркушах: (аркуш, start, end)."""
    pages = report.v4.diss.char_page
    spans: list[tuple[int, int, int]] = []
    index = start
    while index < end:
        page = pages[index]
        page_end = min(end, bisect_left(pages, page + 1))
        spans.append((page, index, page_end))
        index = page_end
    return spans


def _overlay_item(number: int, box: list[float], width: float, height: float) -> OverlayItem:
    return OverlayItem(
        number=number,
        kind="highlight",
        color="pink",
        x0=box[0] / width,
        y0=box[1] / height,
        x1=box[2] / width,
        y1=box[3] / height,
    )


def render_page_png_v4(data: bytes, page_index: int, dpi: int = 110) -> bytes:
    """Аркуш дисертації як PNG — §6.4."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return doc[page_index].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()
