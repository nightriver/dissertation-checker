"""Тести експорту з протоколом режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 8.

Дані вигадані — PLAN_PLAG_FILTER.md, §3, приклад автора «Петренко», «О. А.».
"""

from __future__ import annotations

import fitz
import pytest

from plag_filter.pdf import append_protocol
from plag_filter.project import REQUIRED_PROTOCOL_PHRASE, protocol_paragraphs
from plag_filter.types import PlagProject, PlagReport, SourceRow, SourceState
from plag_filter.ui import _filtered_pdf_name


def _make_pdf(page_count: int = 2) -> bytes:
    doc = fitz.open()
    try:
        for i in range(page_count):
            page = doc.new_page()
            page.insert_htmlbox(fitz.Rect(72, 72, 500, 300), f"<p>Аркуш {i + 1}</p>")
        return doc.tobytes()
    finally:
        doc.close()


def make_row(number: int, percent: float = 5.0) -> SourceRow:
    return SourceRow(
        number=number,
        percent=percent,
        percent_text=f"{percent}%",
        label="example.org",
        urls=("https://example.org/doc",),
        list_page=10,
        band=(0.0, 10.0),
        row_cuts=(),
        link_rects=(),
        source_id=f"src{number}",
    )


def make_report(rows: dict[int, SourceRow]) -> PlagReport:
    return PlagReport(
        sha256="deadbeef",
        page_count=100,
        body_first=3,
        list_first=50,
        rows=rows,
        events=(),
        pages_by_number={},
        numbers_by_page={},
        highlight_width={},
        longest_run={},
        title_text="",
    )


def make_state(number: int, decision: str = "disputed", reason: str = "unchecked") -> SourceState:
    return SourceState(
        number=number,
        source_id=f"src{number}",
        check=None,
        manual=None,
        alt_url=None,
        decision=decision,
        reason=reason,
    )


def make_project(states: dict[int, SourceState]) -> PlagProject:
    return PlagProject(
        schema_version=1,
        report_sha256="deadbeef",
        report_name="report.pdf",
        surname="Петренко",
        initials="О. А.",
        year=2020,
        confirmed=True,
        states=states,
    )


# ---------------------------------------------------------------------------
# append_protocol — сторінки, оригінал незмінний — §8, §9
# ---------------------------------------------------------------------------


def test_append_protocol_adds_exactly_the_protocol_page_count() -> None:
    original = _make_pdf(page_count=2)
    paragraphs = ["Автор дисертації: Петренко О. А.", REQUIRED_PROTOCOL_PHRASE]

    result = append_protocol(original, paragraphs)

    original_doc = fitz.open(stream=original, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        added = result_doc.page_count - original_doc.page_count
        assert added == 1
        for page_index in range(original_doc.page_count, result_doc.page_count):
            assert result_doc[page_index].get_text().strip() != ""
    finally:
        original_doc.close()
        result_doc.close()


def test_append_protocol_leaves_original_pages_unchanged() -> None:
    original = _make_pdf(page_count=2)
    paragraphs = ["Автор дисертації: Петренко О. А.", REQUIRED_PROTOCOL_PHRASE]

    result = append_protocol(original, paragraphs)

    original_doc = fitz.open(stream=original, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        for page_index in range(original_doc.page_count):
            assert original_doc[page_index].read_contents() == result_doc[page_index].read_contents()
    finally:
        original_doc.close()
        result_doc.close()


def test_append_protocol_preserves_required_phrase_and_cyrillic_letters() -> None:
    original = _make_pdf(page_count=1)
    paragraphs = [
        "Літери ґ є ї не спотворюються.",
        REQUIRED_PROTOCOL_PHRASE,
    ]

    result = append_protocol(original, paragraphs)

    original_doc = fitz.open(stream=original, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        raw_text = "".join(
            result_doc[i].get_text() for i in range(original_doc.page_count, result_doc.page_count)
        )
    finally:
        original_doc.close()
        result_doc.close()

    # Перенос рядка при обтіканні може замінити пробіл — зводимо пробільні
    # символи до одного, щоб порівнювати послідовність слів, а не розбивку рядків.
    text = " ".join(raw_text.split())
    assert "ґ є ї" in text
    assert REQUIRED_PROTOCOL_PHRASE in text


@pytest.mark.parametrize("disputed_count", [700])
def test_append_protocol_spans_multiple_pages_with_all_disputed_numbers(disputed_count: int) -> None:
    numbers = list(range(1, disputed_count + 1))
    rows = {number: make_row(number) for number in numbers}
    report = make_report(rows)
    states = {number: make_state(number) for number in numbers}
    project = make_project(states)
    paragraphs = protocol_paragraphs(project, report)

    original = _make_pdf(page_count=1)
    result = append_protocol(original, paragraphs)

    original_doc = fitz.open(stream=original, filetype="pdf")
    result_doc = fitz.open(stream=result, filetype="pdf")
    try:
        protocol_pages = range(original_doc.page_count, result_doc.page_count)
        assert len(protocol_pages) > 1
        raw_text = " ".join(result_doc[i].get_text() for i in protocol_pages)
    finally:
        original_doc.close()
        result_doc.close()

    # Перенос рядка в потоці HTML-обтікання може замінити пробіл — зводимо
    # всі пробільні символи до одного, щоб порівнювати послідовність токенів.
    text = " ".join(raw_text.split())
    for number in numbers:
        assert f"№{number} (" in text


# ---------------------------------------------------------------------------
# Ім'я файлу — §8
# ---------------------------------------------------------------------------


def test_filtered_download_filename_ends_with_filtered_pdf() -> None:
    filename = "Plag_Originality_Report_2026-09-09_16-36-02.pdf"

    assert _filtered_pdf_name(filename).endswith("_filtered.pdf")
