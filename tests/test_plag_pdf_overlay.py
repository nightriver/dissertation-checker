"""Тести швидкого аркуша й наведення — PLAN_PLAG_FILTER_V2.md, §9.2, етап 7."""

from __future__ import annotations

import time
from pathlib import Path

import fitz
import pytest

from plag_filter.pdf import (
    filter_pdf,
    page_overlay,
    parse_report,
    render_clean_page_png,
    render_page_png,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "plag"
REPORT_2020 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-34-45.pdf"
REPORT_2002 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"


@pytest.fixture(scope="module")
def data_2002() -> bytes:
    return REPORT_2002.read_bytes()


@pytest.fixture(scope="module")
def report_2002(data_2002: bytes):
    return parse_report(data_2002)


@pytest.fixture(scope="module")
def data_2020() -> bytes:
    return REPORT_2020.read_bytes()


@pytest.fixture(scope="module")
def report_2020(data_2020: bytes):
    return parse_report(data_2020)


@pytest.mark.corpus
@pytest.mark.parametrize("report_name", ["2002", "2020"])
def test_page_overlay_matches_events_on_every_body_page(
    report_name, data_2002, report_2002, data_2020, report_2020
) -> None:
    data, report = (data_2002, report_2002) if report_name == "2002" else (data_2020, report_2020)

    for page_index in range(report.body_first, report.list_first):
        events = [event for event in report.events if event.page == page_index]
        items = page_overlay(data, report, page_index)

        assert len(items) == len(events)
        for item, event in zip(items, events):
            assert item.number == event.number
            assert item.kind == event.kind
            if event.kind == "marker":
                assert item.color == "marker"
            else:
                assert item.color in ("pink", "yellow")
            for coord in (item.x0, item.y0, item.x1, item.y1):
                assert 0.0 <= coord <= 1.0


@pytest.mark.corpus
@pytest.mark.parametrize("page_index", [3, 9, 49])
def test_render_page_png_equals_full_document_filter_for_small_exclusions(
    data_2002: bytes, report_2002, page_index: int
) -> None:
    below_threshold = {
        number
        for number, row in report_2002.rows.items()
        if row.percent is not None and row.percent < 0.1
    }
    excluded = below_threshold & set(report_2002.numbers_by_page.get(page_index, ()))
    assert excluded, "на обраному аркуші повинен бути номер джерела нижче 0,1 %"

    fast = render_page_png(data_2002, report_2002, page_index, excluded)

    filtered_document = filter_pdf(data_2002, report_2002, excluded)
    doc = fitz.open(stream=filtered_document, filetype="pdf")
    try:
        slow = doc[page_index].get_pixmap(dpi=110).tobytes("png")
    finally:
        doc.close()

    assert fast == slow


@pytest.mark.corpus
def test_render_clean_page_png_returns_same_cached_object(data_2002: bytes, report_2002) -> None:
    first = render_clean_page_png(data_2002, report_2002, report_2002.body_first)
    second = render_clean_page_png(data_2002, report_2002, report_2002.body_first)
    assert first is second
    assert first.startswith(b"\x89PNG")


@pytest.mark.corpus
def test_render_clean_page_png_removes_all_markers_on_page(data_2002: bytes, report_2002) -> None:
    page_index = None
    for candidate in range(report_2002.body_first, report_2002.list_first):
        if report_2002.numbers_by_page.get(candidate):
            page_index = candidate
            break
    assert page_index is not None

    png_plain = render_page_png(data_2002, report_2002, page_index, set())
    png_clean = render_clean_page_png(data_2002, report_2002, page_index)
    assert png_clean != png_plain


@pytest.mark.corpus
def test_second_cached_call_is_fast(data_2002: bytes, report_2002) -> None:
    render_clean_page_png(data_2002, report_2002, 5)
    page_overlay(data_2002, report_2002, 5)

    start = time.monotonic()
    render_clean_page_png(data_2002, report_2002, 5)
    page_overlay(data_2002, report_2002, 5)
    elapsed_ms = (time.monotonic() - start) * 1000
    assert elapsed_ms <= 150
