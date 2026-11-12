"""Розбір звіту Plag нового формату — PLAN_PLAG_FILTER_V4.md, §6.1, §7 етап 1.

Звіт нового формату не має тіла дисертації з підсвіткою й посилань: перелік
джерел — це блоки розділу 3 («Збіги з іншими джерелами») і розділу 4
(«Перекладений текст»), а адреса надрукована текстом. Розбір іде у два кроки:
`extract_lines` читає рядки й шматки шрифту з `get_text("dict")`, а
`parse_lines` — чиста функція над рядками, тому її перевіряють на ручних
рядках без PDF.

Збіглі слова у фрагментах роботи надруковано жирним шрифтом: з них
складаються «жирні шматки» (`PlagFragment.runs`), які далі шукаються в
тексті дисертації. Межі фрагментів — символ «…». Таблицю коротких збігів
лише лічимо (`short_count`), джерелами вони не стають (§1.1).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import fitz

from plag_filter.pdf import UnsupportedReportError

ReportKind = Literal["classic", "v4"]

# Ознаки нового формату на перших двох аркушах — §6.1.
_KIND_MARKERS = ("Звіт про перевірку роботи", "3. Збіги з іншими джерелами")

_FOOTER = re.compile(r"^(Plag · R-\d+ · v\d+ · .*|Сторінка \d+ з \d+)$")
_SPACES = re.compile(r"\s+")

_MAIN_HEADER = re.compile(r"^(\d+)\. (\S+)$")
_TRANSLATED_HEADER = re.compile(r"^(\d+)\. (\S+) · (\d+) речень · max (\d+)%$")
_WORDS_LINE = re.compile(r"^Слів, що збігаються: (\d+) \((\d+(?:\.\d+)?)% від обсягу роботи\)$")
_SECTION_HEADING = re.compile(r"^(\d)\. ")
_REPORT_ID = re.compile(r"R-\d+")

# Заголовок розділу набрано 12,5 pt, заголовок запису — 10 pt, текст
# фрагментів — 9,5 pt (замір §3). Межі — посередині.
_SECTION_MIN_SIZE = 12.0
_HEADER_MIN_SIZE = 9.75
# Значення показника розділу 2 набрано 15 pt, підпис — 8 pt.
_SCORE_VALUE_MIN_SIZE = 12.0
# Рядки однієї колонки розділу 2 мають один лівий край (±15 pt).
_SCORE_COLUMN_TOLERANCE = 15.0
# Допуск лівого краю колонки таблиці (pt).
_COLUMN_TOLERANCE = 2.0

_WORK_HEADER = "Фрагменти в роботі"
_SOURCE_HEADER = "Текст джерела"
_SHORT_HEADER = "Інші джерела (короткі збіги)"
_SHORT_WORDS_HEADER = "Слів"
_PERCENT_HEADER = "%"
_ELLIPSIS = "…"


@dataclass(frozen=True)
class Span:
    """Шматок рядка звіту одним шрифтом."""

    text: str  # NFKC
    bold: bool  # "Bold" в імені шрифту
    x0: float
    size: float


@dataclass(frozen=True)
class Line:
    """Рядок звіту без колонтитула."""

    page: int  # аркуш звіту з 0
    text: str  # NFKC, пробіли схлопнуто, strip
    x0: float
    spans: tuple[Span, ...]


@dataclass(frozen=True)
class PlagFragment:
    """Фрагмент роботи із запису Plag."""

    text: str  # без «…» по краях
    runs: tuple[str, ...]  # жирні шматки по порядку


@dataclass(frozen=True)
class PlagEntry:
    """Запис розділу 3 (`main`) або 4 (`translated`)."""

    section: Literal["main", "translated"]
    number: int
    domain: str
    url: str
    words: int | None
    percent: float | None
    sentences: int | None
    max_percent: int | None
    fragments: tuple[PlagFragment, ...]
    source_texts: tuple[str, ...]
    report_page: int


@dataclass(frozen=True)
class V4Report:
    """Розібраний звіт нового формату."""

    report_id: str
    file_name: str
    total_words: int
    uploaded: str
    scores: tuple[tuple[str, str], ...]
    entries: tuple[PlagEntry, ...]
    short_count: int


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _nfkc(text: str) -> str:
    """NFKC без розкладання «…»: NFKC робить з нього три крапки, а «…» —
    межа фрагментів Plag."""
    return _ELLIPSIS.join(unicodedata.normalize("NFKC", part) for part in text.split(_ELLIPSIS))


def detect_report_kind(data: bytes) -> ReportKind:
    """`"v4"`, якщо на аркушах 1–2 є обидві ознаки нового формату — §6.1."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        lines: set[str] = set()
        for page_index in range(min(2, doc.page_count)):
            for raw in doc[page_index].get_text().splitlines():
                lines.add(_collapse(_nfkc(raw)))
    finally:
        doc.close()
    return "v4" if all(marker in lines for marker in _KIND_MARKERS) else "classic"


def extract_lines(data: bytes) -> list[Line]:
    """Рядки звіту з `get_text("dict")`, без колонтитула й порожніх — §6.1."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        lines: list[Line] = []
        for page_index in range(doc.page_count):
            for block in doc[page_index].get_text("dict")["blocks"]:
                for raw_line in block.get("lines", []):
                    spans = tuple(
                        Span(
                            text=_nfkc(raw_span["text"]),
                            bold="Bold" in raw_span["font"],
                            x0=float(raw_span["bbox"][0]),
                            size=float(raw_span["size"]),
                        )
                        for raw_span in raw_line["spans"]
                    )
                    text = _collapse("".join(span.text for span in spans))
                    if not text or _FOOTER.match(text):
                        continue
                    lines.append(
                        Line(
                            page=page_index,
                            text=text,
                            x0=float(raw_line["bbox"][0]),
                            spans=spans,
                        )
                    )
        return lines
    finally:
        doc.close()


def _all_bold(line: Line) -> bool:
    return all(span.bold for span in line.spans if span.text.strip())


def _max_size(line: Line) -> float:
    return max((span.size for span in line.spans if span.text.strip()), default=0.0)


def _is_section_heading(line: Line) -> bool:
    return (
        _SECTION_HEADING.match(line.text) is not None
        and _all_bold(line)
        and _max_size(line) >= _SECTION_MIN_SIZE
    )


def _find_heading(lines: list[Line], text: str, start: int = 0) -> int | None:
    for index in range(start, len(lines)):
        if lines[index].text == text and _is_section_heading(lines[index]):
            return index
    return None


def _value_after(lines: list[Line], label: str, start: int, end: int) -> str | None:
    for index in range(start, end - 1):
        if lines[index].text == label:
            return lines[index + 1].text
    return None


def _parse_scores(lines: list[Line]) -> tuple[tuple[str, str], ...]:
    """Показники розділу 2 так, як їх надруковано: (підпис, значення)."""
    columns: list[tuple[float, list[Line]]] = []
    for line in lines:
        for anchor, members in columns:
            if abs(anchor - line.x0) <= _SCORE_COLUMN_TOLERANCE:
                members.append(line)
                break
        else:
            columns.append((line.x0, [line]))

    scores: list[tuple[str, str]] = []
    for _anchor, members in sorted(columns, key=lambda item: item[0]):
        label: list[str] = []
        value: list[str] = []
        for line in members:
            if value or _max_size(line) >= _SCORE_VALUE_MIN_SIZE:
                value.append(line.text)
            else:
                label.append(line.text)
        if label and value:
            scores.append((" ".join(label), " ".join(value)))
    return tuple(scores)


def _fragments(lines: list[Line]) -> list[PlagFragment]:
    """Фрагменти й жирні шматки — §6.1.

    Межа фрагмента — «…». Жирні шпани одного рядка склеюються без
    роздільника, перенесення рядка всередині шматка дає пробіл. Не жирний
    шпан або «…» закриває шматок.
    """
    fragments: list[PlagFragment] = []
    text_parts: list[str] = []
    runs: list[str] = []
    run: list[str] = []

    def end_run() -> None:
        collapsed = _collapse("".join(run))
        if collapsed:
            runs.append(collapsed)
        run.clear()

    def end_fragment() -> None:
        end_run()
        collapsed = _collapse("".join(text_parts))
        if collapsed:
            fragments.append(PlagFragment(text=collapsed, runs=tuple(runs)))
        text_parts.clear()
        runs.clear()

    for line_index, line in enumerate(lines):
        if line_index:
            text_parts.append(" ")
            if run:
                run.append(" ")
        for span in line.spans:
            for piece_index, piece in enumerate(span.text.split(_ELLIPSIS)):
                if piece_index:
                    end_fragment()
                if span.bold:
                    if piece:
                        run.append(piece)
                else:
                    end_run()
                text_parts.append(piece)
    end_fragment()
    return fragments


def _is_main_header(lines: list[Line], index: int, end: int) -> re.Match[str] | None:
    line = lines[index]
    match = _MAIN_HEADER.match(line.text)
    if (
        match is None
        or not _all_bold(line)
        or _max_size(line) < _HEADER_MIN_SIZE
        or index + 1 >= end
        or not lines[index + 1].text.startswith("http")
    ):
        return None
    return match


def _parse_main_block(number: int, domain: str, page: int, block: list[Line]) -> PlagEntry:
    words_index = next(
        (i for i, line in enumerate(block) if line.text.startswith("Слів, що збігаються")),
        None,
    )
    if words_index is None:
        raise UnsupportedReportError("v4_bad_entry")
    words_match = _WORDS_LINE.match(block[words_index].text)
    if words_match is None:
        raise UnsupportedReportError("v4_bad_entry")
    url = "".join(line.text.replace(" ", "") for line in block[:words_index])

    work: list[Line] = []
    source: list[Line] = []
    target: list[Line] | None = None
    for line in block[words_index + 1 :]:
        if line.text == _WORK_HEADER and _all_bold(line):
            target = work
            continue
        if line.text == _SOURCE_HEADER and _all_bold(line):
            target = source
            continue
        if target is not None:
            target.append(line)

    source_text = _collapse(" ".join(line.text for line in source))
    return PlagEntry(
        section="main",
        number=number,
        domain=domain,
        url=url,
        words=int(words_match.group(1)),
        percent=float(words_match.group(2)),
        sentences=None,
        max_percent=None,
        fragments=tuple(_fragments(work)),
        source_texts=(source_text,) if source_text else (),
        report_page=page,
    )


def _parse_main(lines: list[Line], start: int, end: int) -> list[PlagEntry]:
    headers: list[tuple[int, re.Match[str]]] = []
    for index in range(start, end):
        match = _is_main_header(lines, index, end)
        if match is not None:
            headers.append((index, match))

    entries: list[PlagEntry] = []
    for position, (index, match) in enumerate(headers):
        number = int(match.group(1))
        if number != len(entries) + 1:
            raise UnsupportedReportError("v4_bad_sequence")
        block_end = headers[position + 1][0] if position + 1 < len(headers) else end
        entries.append(
            _parse_main_block(number, match.group(2), lines[index].page, lines[index + 1 : block_end])
        )
    return entries


def _count_short(lines: list[Line]) -> int:
    """Рядки таблиці коротких збігів: число в колонці «Слів» закриває рядок."""
    words_x: float | None = None
    count = 0
    for line in lines:
        if words_x is None:
            if line.text == _SHORT_WORDS_HEADER and _all_bold(line):
                words_x = line.x0
            continue
        if line.text.isdigit() and line.x0 >= words_x - _COLUMN_TOLERANCE:
            count += 1
    return count


def _translated_url(lines: list[Line]) -> str:
    for index, line in enumerate(lines):
        position = line.text.find("http")
        if position >= 0:
            tail = [line.text[position:]] + [rest.text for rest in lines[index + 1 :]]
            return "".join(part.replace(" ", "") for part in tail)
    return ""


def _parse_translated_block(
    number: int, domain: str, sentences: int, max_percent: int, page: int, block: list[Line]
) -> PlagEntry:
    work_index = next(
        (i for i, line in enumerate(block) if line.text == _WORK_HEADER and _all_bold(line)),
        None,
    )
    if work_index is None:
        raise UnsupportedReportError("v4_bad_entry")
    url = _translated_url(block[:work_index])

    column_x: float | None = None
    percent_x: float | None = None
    for line in block[work_index + 1 :]:
        if not _all_bold(line):
            continue
        if line.text == _SOURCE_HEADER and column_x is None:
            column_x = line.x0
        elif line.text == _PERCENT_HEADER and percent_x is None:
            percent_x = line.x0
    if column_x is None or percent_x is None:
        raise UnsupportedReportError("v4_bad_entry")

    fragments: list[PlagFragment] = []
    source_texts: list[str] = []
    left: list[Line] = []
    right: list[Line] = []

    def close_row() -> None:
        fragments.extend(_fragments(left))
        text = _collapse(" ".join(line.text for line in right))
        if text:
            source_texts.append(text)
        left.clear()
        right.clear()

    for line in block[work_index + 1 :]:
        if line.text in (_WORK_HEADER, _SOURCE_HEADER, _PERCENT_HEADER) and _all_bold(line):
            continue
        if line.x0 >= percent_x - _COLUMN_TOLERANCE and line.text.isdigit():
            close_row()
        elif line.x0 < column_x - _COLUMN_TOLERANCE:
            left.append(line)
        else:
            right.append(line)
    if left or right:
        close_row()

    return PlagEntry(
        section="translated",
        number=number,
        domain=domain,
        url=url,
        words=None,
        percent=None,
        sentences=sentences,
        max_percent=max_percent,
        fragments=tuple(fragments),
        source_texts=tuple(source_texts),
        report_page=page,
    )


def _parse_translated(lines: list[Line], start: int, end: int) -> list[PlagEntry]:
    headers: list[tuple[int, re.Match[str]]] = []
    for index in range(start, end):
        line = lines[index]
        match = _TRANSLATED_HEADER.match(line.text)
        if match is not None and _all_bold(line):
            headers.append((index, match))

    entries: list[PlagEntry] = []
    for position, (index, match) in enumerate(headers):
        number = int(match.group(1))
        if number != len(entries) + 1:
            raise UnsupportedReportError("v4_bad_sequence")
        block_end = headers[position + 1][0] if position + 1 < len(headers) else end
        entries.append(
            _parse_translated_block(
                number,
                match.group(2),
                int(match.group(3)),
                int(match.group(4)),
                lines[index].page,
                lines[index + 1 : block_end],
            )
        )
    return entries


def parse_lines(lines: list[Line]) -> V4Report:
    """Розібрати рядки звіту нового формату — §6.1.

    `UnsupportedReportError`: `v4_no_meta`, `v4_no_sources`, `v4_bad_entry`,
    `v4_bad_sequence`.
    """
    work_heading = _find_heading(lines, "1. Робота")
    if work_heading is None:
        raise UnsupportedReportError("v4_no_meta")
    scores_heading = _find_heading(lines, "2. Показники", work_heading + 1)
    sources_heading = _find_heading(lines, "3. Збіги з іншими джерелами", work_heading + 1)
    meta_end = scores_heading or sources_heading or len(lines)

    total_words_text = _value_after(lines, "Слів", work_heading + 1, meta_end)
    if total_words_text is None or not total_words_text.isdigit():
        raise UnsupportedReportError("v4_no_meta")
    file_name = _value_after(lines, "Файл", work_heading + 1, meta_end) or ""
    uploaded = _value_after(lines, "Завантажено", work_heading + 1, meta_end) or ""
    report_id = ""
    for line in lines[:work_heading]:
        if "Номер звіту" in line.text:
            match = _REPORT_ID.search(line.text)
            if match is not None:
                report_id = match.group(0)
                break

    if sources_heading is None:
        raise UnsupportedReportError("v4_no_sources")

    scores: tuple[tuple[str, str], ...] = ()
    if scores_heading is not None and scores_heading < sources_heading:
        score_lines: list[Line] = []
        for line in lines[scores_heading + 1 : sources_heading]:
            if line.text.startswith("Показники є"):
                break
            score_lines.append(line)
        scores = _parse_scores(score_lines)

    translated_heading = _find_heading(lines, "4. Перекладений текст", sources_heading + 1)
    tail_start = translated_heading if translated_heading is not None else sources_heading
    end = next(
        (
            index
            for index in range(tail_start + 1, len(lines))
            if _is_section_heading(lines[index])
        ),
        len(lines),
    )
    sources_end = translated_heading if translated_heading is not None else end
    short_heading = next(
        (
            index
            for index in range(sources_heading + 1, sources_end)
            if lines[index].text == _SHORT_HEADER and _all_bold(lines[index])
        ),
        None,
    )
    main_end = short_heading if short_heading is not None else sources_end

    entries = _parse_main(lines, sources_heading + 1, main_end)
    short_count = (
        _count_short(lines[short_heading + 1 : sources_end]) if short_heading is not None else 0
    )
    if translated_heading is not None:
        entries += _parse_translated(lines, translated_heading + 1, end)

    return V4Report(
        report_id=report_id,
        file_name=file_name,
        total_words=int(total_words_text),
        uploaded=uploaded,
        scores=scores,
        entries=tuple(entries),
        short_count=short_count,
    )


def parse_v4_report(data: bytes) -> V4Report:
    """Розібрати PDF звіту нового формату — §6.1."""
    return parse_lines(extract_lines(data))
