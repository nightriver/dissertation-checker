"""Розбір звіту Plag: перелік джерел і події в тілі дисертації.

Підхід перенесено з прототипу `plag_filter_spike.py` (`pikepdf`) на PyMuPDF,
уже наявний у застосунку — PLAN_PLAG_FILTER.md, §7. Групи content-stream,
кольори заливок і порядок подій `walk_body` — з того самого прототипу.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from urllib.parse import urlparse

import fitz

from plag_filter.types import BodyEvent, OverlayItem, PlagReport, SourceRow

# Кольори заливок Plag (RGB, округлення до 3 знаків) — PLAN_PLAG_FILTER.md, §7.
GREEN = (0.2, 0.373, 0.373)
PINK = (1.0, 0.839, 0.839)
YELLOW = (1.0, 0.965, 0.761)
CITED = (0.706, 0.973, 0.831)
LISTPCT = (1.0, 0.847, 0.847)

# Тіло: "<r> <g> <b> rg" окремим рядком, далі "<x> <y> <w> <h> re f".
BODY_RECT = re.compile(rb"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) re f")
SET_RG = re.compile(rb"^([\d.]+) ([\d.]+) ([\d.]+) rg$", re.M)
# Перелік: колір усередині q/Q.
LIST_RECT = re.compile(
    rb"q ([\d.]+) ([\d.]+) ([\d.]+) rg\n(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) re f Q"
)
# Текстова група (і звичайний текст, і напис усередині заливки).
SPAN = re.compile(
    rb"q ([\d.]+) ([\d.]+) ([\d.]+) rg\s+0 Tr BT ([\d.]+) ([\d.]+) Td\s+"
    rb"(?:<([0-9A-Fa-f]*)>|\(([^)]*)\)) Tj ET Q"
)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

FOOTER_Y = 30.0  # колонтитул: групи нижче цієї межі ігноруються
BODY_FIRST = 3  # перший аркуш тіла — фіксований індекс шаблону Plag

# Сторінка протоколу — A4, поля 40 пт, інтервал між абзацами 10 пт — §8.
PROTOCOL_PAGE_WIDTH = 595.0
PROTOCOL_PAGE_HEIGHT = 842.0
PROTOCOL_MARGIN = 40.0
PROTOCOL_GAP = 10.0


class UnsupportedReportError(Exception):
    """Звіт не відповідає шаблону mPDF 7.1.9, очікуваному від Plag.

    Код причини — PLAN_PLAG_FILTER.md, §7: `multiple_streams`, `no_list`,
    `no_body`, `orphan_highlight`, `marker_not_in_list`, `link_unbound`,
    `row_without_link`.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _rgb(groups) -> tuple[float, float, float]:
    r, g, b = (round(float(x), 3) for x in groups)
    return (r, g, b)


def _span_text(match: re.Match[bytes]) -> str:
    if match.group(6):
        return bytes.fromhex(match.group(6).decode()).decode("latin-1")
    return (match.group(7) or b"").decode("latin-1")


def _read_stream(page: fitz.Page) -> bytes:
    if len(page.get_contents()) != 1:
        raise UnsupportedReportError("multiple_streams")
    return page.read_contents()


def _percent(text: str) -> float | None:
    if not text:
        return None
    m = PERCENT_RE.fullmatch(text.strip())
    return float(m.group(1)) if m else None


def _find_list_first(doc: fitz.Document) -> int:
    for i in range(doc.page_count):
        data = _read_stream(doc[i])
        for m in LIST_RECT.finditer(data):
            if _rgb(m.groups()[:3]) == LISTPCT:
                return i
    raise UnsupportedReportError("no_list")


def _parse_list(doc: fitz.Document, list_first: int, sha256: str) -> dict[int, SourceRow]:
    """Рядки переліку джерел і прив'язка URI-посилань — PLAN_PLAG_FILTER.md, §2."""
    rows: dict[int, SourceRow] = {}
    for page_index in range(list_first, doc.page_count):
        data = _read_stream(doc[page_index])
        rects = [
            (_rgb(m.groups()[:3]), float(m.group(5)) + float(m.group(7)), float(m.group(5)),
             m.start(), m.end())
            for m in LIST_RECT.finditer(data)
        ]
        spans = [
            (float(m.group(5)), _span_text(m).strip(), m.start(), m.end())
            for m in SPAN.finditer(data)
        ]

        page_rows: list[dict] = []
        for color, b0, b1, start, end in rects:
            if color != GREEN:
                continue
            cuts = [(start, end)]
            for c2, cb0, _cb1, s2, e2 in rects:
                if c2 == LISTPCT and abs(cb0 - b0) < 1:
                    cuts.append((s2, e2))
            number = None
            percent_text = ""
            label_parts: list[str] = []
            for ty, text, s2, e2 in spans:
                if not (b0 - 1 <= ty <= b1 - 3):
                    continue
                cuts.append((s2, e2))
                if text.isdigit() and number is None:
                    number = int(text)
                elif "%" in text:
                    percent_text = text
                elif text:
                    label_parts.append(text)
            if number is None:
                continue
            page_rows.append(
                {
                    "number": number,
                    "band": (b0, b1),
                    "cuts": cuts,
                    "percent_text": percent_text,
                    "label": " ".join(label_parts),
                    "urls": [],
                    "link_rects": [],
                }
            )

        page = doc[page_index]
        page_height = page.rect.height
        for link in page.get_links():
            uri = link.get("uri")
            if not uri:
                continue
            rect = link["from"]
            cy = page_height - (rect.y0 + rect.y1) / 2
            bound = None
            for meta in page_rows:
                b0, b1 = meta["band"]
                if b0 <= cy <= b1:
                    bound = meta
                    break
            if bound is None:
                raise UnsupportedReportError("link_unbound")
            bound["urls"].append(uri)
            bound["link_rects"].append((rect.x0, rect.y0, rect.x1, rect.y1))

        for meta in page_rows:
            if not meta["urls"]:
                raise UnsupportedReportError("row_without_link")
            number = meta["number"]
            b0, b1 = meta["band"]
            source_id = hashlib.sha256(
                f"{sha256}:{page_index}:{b0:.1f}:{b1:.1f}".encode()
            ).hexdigest()[:16]
            rows[number] = SourceRow(
                number=number,
                percent=_percent(meta["percent_text"]),
                percent_text=meta["percent_text"],
                label=meta["label"],
                urls=tuple(meta["urls"]),
                list_page=page_index,
                band=meta["band"],
                row_cuts=tuple(meta["cuts"]),
                link_rects=tuple(meta["link_rects"]),
                source_id=source_id,
            )
    return rows


TRUNCATED_LABEL = "поза переліком Plag"


def is_truncated_row(row: SourceRow) -> bool:
    """Рядок відтворено з маркера тіла, бо Plag обрізав перелік."""
    return not row.urls and row.label.startswith(TRUNCATED_LABEL)


def _truncated_row(rows: dict[int, SourceRow], number: int) -> SourceRow:
    """Рядок для маркера за межами обрізаного переліку Plag.

    Plag виводить у перелік не більше ~1001 джерела, упорядкованих за спаданням
    відсотка, але в тілі лишає маркери всіх джерел. Номер понад останній рядок
    переліку, коли останній рядок має 0.0 %, означає джерело з відсотком
    0.0 %. Інакше (пропуск усередині переліку чи ненульовий хвіст) звіт
    не відповідає шаблону — `marker_not_in_list`.
    """
    listed = [n for n, row in rows.items() if not is_truncated_row(row)]
    last = rows[max(listed)] if listed else None
    if last is None or number < last.number or last.percent != 0.0:
        raise UnsupportedReportError("marker_not_in_list")
    return SourceRow(
        number=number,
        percent=0.0,
        percent_text="0.0%",
        label=f"{TRUNCATED_LABEL} (перелік обрізано на № {last.number})",
        urls=(),
        list_page=last.list_page,
        band=(0.0, 0.0),
        row_cuts=(),
        link_rects=(),
        source_id=hashlib.sha256(f"{last.source_id}:truncated:{number}".encode()).hexdigest()[:16],
    )


def _walk_body(
    doc: fitz.Document, body_first: int, list_first: int, rows: dict[int, SourceRow]
) -> tuple[
    tuple[BodyEvent, ...],
    dict[int, tuple[int, ...]],
    dict[int, tuple[int, ...]],
    dict[int, float],
    dict[int, float],
]:
    """Події тіла й похідні лічильники — порядок `walk_body` з прототипу, §7."""
    events: list[BodyEvent] = []
    current: int | None = None
    run_width = 0.0
    highlight_width: dict[int, float] = {}
    longest_run: dict[int, float] = {}
    numbers_by_page: dict[int, list[int]] = {}
    pages_by_number: dict[int, list[int]] = {}

    def note(page_index: int, number: int) -> None:
        page_numbers = numbers_by_page.setdefault(page_index, [])
        if number not in page_numbers:
            page_numbers.append(number)
        number_pages = pages_by_number.setdefault(number, [])
        if page_index not in number_pages:
            number_pages.append(page_index)

    def close_run() -> None:
        nonlocal run_width
        if current is not None and run_width > longest_run.get(current, 0.0):
            longest_run[current] = run_width
        run_width = 0.0

    for page_index in range(body_first, list_first):
        data = _read_stream(doc[page_index])
        raw_events = [(m.start(), m.end(), "rect", m) for m in BODY_RECT.finditer(data)]
        raw_events += [(m.start(), m.end(), "span", m) for m in SPAN.finditer(data)]
        raw_events += [(m.start(), m.end(), "rg", m) for m in SET_RG.finditer(data)]
        raw_events.sort(key=lambda item: (item[0], item[1]))

        fill: tuple[float, float, float] | None = None
        pending: tuple[tuple[float, float, float] | None, int, int, float] | None = None
        for start, end, kind, m in raw_events:
            if kind == "rg":
                fill = _rgb(m.groups())
                continue
            if kind == "rect":
                pending = (fill, start, end, abs(float(m.group(3))))
                continue
            # kind == "span"
            if float(m.group(5)) < FOOTER_Y:  # колонтитул не змінює поточне джерело
                pending = None
                continue
            if pending is None:  # звичайний текст закриває фрагмент
                close_run()
                current = None
                continue
            color, r_start, r_end, width = pending
            pending = None
            if color == GREEN:
                close_run()
                raw = _span_text(m).strip()
                if not raw.isdigit():
                    current = None
                    continue
                number = int(raw)
                if number not in rows:
                    rows[number] = _truncated_row(rows, number)
                current = number
                note(page_index, number)
                events.append(
                    BodyEvent(
                        page=page_index,
                        number=number,
                        kind="marker",
                        cuts=((r_start, r_end), (start, end)),
                        width=0.0,
                    )
                )
            elif color in (PINK, YELLOW):
                if current is None:
                    raise UnsupportedReportError("orphan_highlight")
                events.append(
                    BodyEvent(
                        page=page_index,
                        number=current,
                        kind="highlight",
                        cuts=((r_start, r_end),),
                        width=width,
                    )
                )
                highlight_width[current] = highlight_width.get(current, 0.0) + width
                run_width += width
                note(page_index, current)
            else:  # цитований текст та інші кольори закривають фрагмент
                close_run()
                current = None
    close_run()

    numbers_by_page_t = {page: tuple(sorted(nums)) for page, nums in numbers_by_page.items()}
    pages_by_number_t = {num: tuple(sorted(pages)) for num, pages in pages_by_number.items()}
    return tuple(events), numbers_by_page_t, pages_by_number_t, highlight_width, longest_run


def _apply_cuts(page: fitz.Page, cuts: list[tuple[int, int]]) -> None:
    """Вирізати байтові діапазони `cuts` з потоку сторінки, з кінця потоку — §7."""
    if not cuts:
        return
    buf = bytearray(page.read_contents())
    for start, end in sorted(set(cuts), reverse=True):
        del buf[start:end]
    page.parent.update_stream(page.get_contents()[0], bytes(buf))


def filter_pdf(data: bytes, report: PlagReport, excluded: set[int]) -> bytes:
    """Прибрати розмітку виключених джерел — PLAN_PLAG_FILTER.md, §7, §9."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        cuts_by_page: dict[int, list[tuple[int, int]]] = {}
        for event in report.events:
            if event.number in excluded:
                cuts_by_page.setdefault(event.page, []).extend(event.cuts)
        for row in report.rows.values():
            if row.number in excluded:
                cuts_by_page.setdefault(row.list_page, []).extend(row.row_cuts)

        for page_index, cuts in cuts_by_page.items():
            _apply_cuts(doc[page_index], cuts)

        for row in report.rows.values():
            if row.number not in excluded:
                continue
            page = doc[row.list_page]
            targets = set(row.link_rects)
            for link in list(page.get_links()):
                rect = link.get("from")
                if rect is None:
                    continue
                key = (rect.x0, rect.y0, rect.x1, rect.y1)
                if key in targets:
                    page.delete_link(link)

        return doc.tobytes()
    finally:
        doc.close()


def render_page_png(
    data: bytes, report: PlagReport, page: int, excluded: set[int], dpi: int = 110
) -> bytes:
    """Растеризувати аркуш PDF з урахуванням поточних виключень — PLAN_PLAG_FILTER.md, §8, §9.

    Ріжеться лише потік запитаної сторінки — PLAN_PLAG_FILTER_V2.md, §9.2, етап 7,
    на відміну від `filter_pdf`, який обробляє весь документ.
    """
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if excluded:
            cuts: list[tuple[int, int]] = []
            for event in report.events:
                if event.page == page and event.number in excluded:
                    cuts.extend(event.cuts)
            for row in report.rows.values():
                if row.list_page == page and row.number in excluded:
                    cuts.extend(row.row_cuts)
            if cuts:
                _apply_cuts(doc[page], cuts)
        pixmap = doc[page].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")
    finally:
        doc.close()


# Кеш "чистих" сторінок — PLAN_PLAG_FILTER_V2.md, §9.2, етап 7: не більше
# `_CLEAN_PAGE_CACHE_LIMIT` записів, витіснення найстарішого.
_CLEAN_PAGE_CACHE_LIMIT = 64
_clean_page_cache: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()


def render_clean_page_png(
    data: bytes, report: PlagReport, page: int, dpi: int = 110
) -> bytes:
    """Аркуш без жодних номерів джерел — PLAN_PLAG_FILTER_V2.md, §8.5, §9.2, етап 7."""
    key = (report.sha256, page, dpi)
    cached = _clean_page_cache.get(key)
    if cached is not None:
        _clean_page_cache.move_to_end(key)
        return cached

    excluded = set(report.numbers_by_page.get(page, ()))
    result = render_page_png(data, report, page, excluded, dpi)

    _clean_page_cache[key] = result
    _clean_page_cache.move_to_end(key)
    if len(_clean_page_cache) > _CLEAN_PAGE_CACHE_LIMIT:
        _clean_page_cache.popitem(last=False)
    return result


# Відповідність кольору заливки та виду наведення — PLAN_PLAG_FILTER_V2.md, §9.2, етап 7.
_OVERLAY_COLORS: dict[tuple[float, float, float], tuple[str, str]] = {
    PINK: ("highlight", "pink"),
    YELLOW: ("highlight", "yellow"),
    GREEN: ("marker", "marker"),
}


def page_overlay(data: bytes, report: PlagReport, page: int) -> list[OverlayItem]:
    """Фігури наведення сторінки, зіставлені з подіями `report.events` — §8.5, §9.2, етап 7."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pdf_page = doc[page]
        width = pdf_page.rect.width
        height = pdf_page.rect.height

        drawings: list[tuple[str, str, fitz.Rect]] = []
        for drawing in pdf_page.get_drawings():
            fill = drawing.get("fill")
            if fill is None:
                continue
            kind_color = _OVERLAY_COLORS.get(_rgb(fill))
            if kind_color is None:
                continue
            drawings.append((kind_color[0], kind_color[1], drawing["rect"]))

        events = [event for event in report.events if event.page == page]
        if len(drawings) != len(events):
            raise UnsupportedReportError("overlay_mismatch")

        items: list[OverlayItem] = []
        for (draw_kind, color, rect), event in zip(drawings, events):
            if draw_kind != event.kind:
                raise UnsupportedReportError("overlay_mismatch")
            items.append(
                OverlayItem(
                    number=event.number,
                    kind=event.kind,
                    color=color,
                    x0=rect.x0 / width,
                    y0=rect.y0 / height,
                    x1=rect.x1 / width,
                    y1=rect.y1 / height,
                )
            )
        return items
    finally:
        doc.close()


def _protocol_html(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"<p>{escaped}</p>"


def _new_protocol_page(doc: fitz.Document) -> fitz.Page:
    return doc.new_page(width=PROTOCOL_PAGE_WIDTH, height=PROTOCOL_PAGE_HEIGHT)


def _protocol_rect(y: float) -> fitz.Rect:
    return fitz.Rect(
        PROTOCOL_MARGIN, y, PROTOCOL_PAGE_WIDTH - PROTOCOL_MARGIN, PROTOCOL_PAGE_HEIGHT - PROTOCOL_MARGIN
    )


def _fits(text: str, rect: fitz.Rect) -> bool:
    """Перевірити, чи текст влазить у прямокутник заданої висоти — §8.

    Перевірка йде на порожній пробній сторінці: `insert_htmlbox` враховує
    лише геометрію самого прямокутника, тож вміст поза ним не впливає.
    """
    probe = fitz.open()
    try:
        probe_page = _new_protocol_page(probe)
        spare, _scale = probe_page.insert_htmlbox(rect, _protocol_html(text), scale_low=1)
        return spare >= 0
    finally:
        probe.close()


def _split_to_fit(text: str, rect: fitz.Rect) -> tuple[str, str]:
    """Знайти найбільший префікс `text` за словами, що влазить у `rect` — §8.

    Абзац завжди фіксовано вставляється цілком, окрім переліку спірних
    номерів, довжина якого залежить від звіту: коли він не влазить навіть
    на порожню сторінку, його ділять по пробілах, а решту переносять далі.
    """
    words = text.split(" ")
    if len(words) <= 1:
        return text, ""
    low, high = 1, len(words)
    fit_count = 1
    while low <= high:
        mid = (low + high) // 2
        if _fits(" ".join(words[:mid]), rect):
            fit_count = mid
            low = mid + 1
        else:
            high = mid - 1
    head = " ".join(words[:fit_count])
    tail = " ".join(words[fit_count:])
    return head, tail


def _place_paragraph(
    doc: fitz.Document, page: fitz.Page, y: float, text: str
) -> tuple[fitz.Page, float]:
    """Вставити абзац, за потреби переносячи його на нові сторінки — §8."""
    remaining = text
    while remaining:
        if y > PROTOCOL_MARGIN and not _fits(remaining, _protocol_rect(y)):
            page = _new_protocol_page(doc)
            y = PROTOCOL_MARGIN

        rect = _protocol_rect(y)
        if _fits(remaining, rect):
            spare, _scale = page.insert_htmlbox(rect, _protocol_html(remaining), scale_low=1)
            used_height = rect.height - max(spare, 0.0)
            y = rect.y0 + used_height + PROTOCOL_GAP
            remaining = ""
        else:
            # Абзац не влазить навіть на порожню сторінку — переносимо частинами.
            head, tail = _split_to_fit(remaining, rect)
            spare, _scale = page.insert_htmlbox(rect, _protocol_html(head), scale_low=1)
            used_height = rect.height - max(spare, 0.0)
            y = rect.y0 + used_height + PROTOCOL_GAP
            remaining = tail
    return page, y


def append_protocol(pdf: bytes, paragraphs: list[str]) -> bytes:
    """Додати протокол очищення в кінець PDF, абзац за абзацом — PLAN_PLAG_FILTER.md, §8, §9."""
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page = _new_protocol_page(doc)
        y = PROTOCOL_MARGIN
        for text in paragraphs:
            page, y = _place_paragraph(doc, page, y, text)
        return doc.tobytes()
    finally:
        doc.close()


def parse_report(data: bytes) -> PlagReport:
    """Розібрати звіт Plag у структуру `PlagReport` — PLAN_PLAG_FILTER.md, §9."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        sha256 = hashlib.sha256(data).hexdigest()
        list_first = _find_list_first(doc)

        if doc.page_count <= BODY_FIRST + 1:
            raise UnsupportedReportError("no_body")
        body_text_1 = doc[BODY_FIRST].get_text()
        if "Збіги" not in body_text_1:
            raise UnsupportedReportError("no_body")
        body_text_2 = doc[BODY_FIRST + 1].get_text()

        rows = _parse_list(doc, list_first, sha256)
        events, numbers_by_page, pages_by_number, highlight_width, longest_run = _walk_body(
            doc, BODY_FIRST, list_first, rows
        )

        return PlagReport(
            sha256=sha256,
            page_count=doc.page_count,
            body_first=BODY_FIRST,
            list_first=list_first,
            rows=rows,
            events=events,
            pages_by_number=pages_by_number,
            numbers_by_page=numbers_by_page,
            highlight_width=highlight_width,
            longest_run=longest_run,
            title_text=body_text_1 + body_text_2,
        )
    finally:
        doc.close()
