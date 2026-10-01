"""Тести тексту аркуша й показників шапки — PLAN_PLAG_VIEW.md, §7 етап 1."""

from __future__ import annotations

import re
import time
from pathlib import Path

import fitz
import pytest

from plag_filter import text as text_module
from plag_filter.pdf import parse_report
from plag_filter.text import (
    FOOTER_TOP,
    HEADER_BOTTOM,
    Segment,
    page_text,
    report_scores,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "plag"
REPORTS = {
    "2020": EXAMPLES / "Plag_Originality_Report_2026-09-09_16-34-45.pdf",
    "2002": EXAMPLES / "Plag_Originality_Report_2026-09-09_16-36-02.pdf",
    "third": EXAMPLES / "!Plag_Originality_Report_2026-09-17_01-07-15.pdf",
}


@pytest.fixture(scope="module")
def parsed() -> dict:
    result = {}
    for name, path in REPORTS.items():
        data = path.read_bytes()
        result[name] = (data, parse_report(data))
    return result


def _segments(paragraphs) -> list[Segment]:
    return [segment for paragraph in paragraphs for segment in paragraph]


def _expected_without_spaces(data: bytes, report, page_index: int) -> str:
    """Текст аркуша з `get_text("dict")` після тих самих відсічок, без пробілів.

    Номери маркерів тут лишаються: тест вставляє їх у вивід `page_text` на
    місця маркер-сегментів і так перевіряє й текст, і положення маркерів.
    """
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        lines = [
            line
            for block in doc[page_index].get_text("dict")["blocks"]
            for line in block.get("lines", ())
        ]
    finally:
        doc.close()
    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    kept = []
    for line in lines:
        top = line["bbox"][1]
        if top >= FOOTER_TOP:
            continue
        if page_index == report.body_first and top < HEADER_BOTTOM:
            continue
        kept.append("".join(span["text"] for span in line["spans"]))
    return re.sub(r"\s+", "", "".join(kept))


@pytest.mark.corpus
@pytest.mark.parametrize("name", list(REPORTS))
def test_page_text_matches_events_and_text_on_every_body_page(parsed, name) -> None:
    data, report = parsed[name]
    for page_index in range(report.body_first, report.list_first):
        segments = _segments(page_text(data, report, page_index))
        events = [event for event in report.events if event.page == page_index]

        markers = [segment.number for segment in segments if segment.marker]
        assert markers == [event.number for event in events if event.kind == "marker"], page_index

        highlighted = {
            segment.number for segment in segments if not segment.marker and segment.number
        }
        assert highlighted == {event.number for event in events if event.kind == "highlight"}

        rebuilt = "".join(
            str(segment.number) if segment.marker else segment.text for segment in segments
        )
        assert re.sub(r"\s+", "", rebuilt) == _expected_without_spaces(data, report, page_index)

        assert all(segment.text == "" for segment in segments if segment.marker)
        assert all(segment.text for segment in segments if not segment.marker)
        assert not any("Звіт про виявлення ШІ" in segment.text for segment in segments)


def test_first_body_page_starts_with_dissertation_title(parsed) -> None:
    data, report = parsed["2020"]
    paragraphs = page_text(data, report, report.body_first)
    first = paragraphs[0][0]
    assert first.number is None and not first.marker
    assert first.text.startswith("УНІВЕРСИТЕТ МИТНОЇ СПРАВИ")


def test_adjacent_fragments_of_one_source_are_merged(parsed) -> None:
    data, report = parsed["2020"]
    for paragraph in page_text(data, report, report.body_first + 1):
        for left, right in zip(paragraph, paragraph[1:]):
            if not left.marker and not right.marker:
                assert left.number != right.number
            assert "  " not in left.text


def test_pages_outside_body_are_empty(parsed) -> None:
    data, report = parsed["2020"]
    assert page_text(data, report, 0) == ()
    assert page_text(data, report, report.body_first - 1) == ()
    assert page_text(data, report, report.list_first) == ()


def test_page_text_is_fast_without_cache(parsed) -> None:
    data, report = parsed["2020"]
    text_module._text_cache.clear()
    started = time.perf_counter()
    page_text(data, report, report.body_first + 50)
    assert time.perf_counter() - started <= 0.5


def test_page_text_cache_returns_same_object_and_is_bounded(parsed) -> None:
    data, report = parsed["2020"]
    text_module._text_cache.clear()
    first = page_text(data, report, report.body_first + 1)
    assert page_text(data, report, report.body_first + 1) is first
    for page_index in range(report.body_first, report.body_first + 70):
        page_text(data, report, page_index)
    assert len(text_module._text_cache) == 64


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("2020", ("76", "НАЙВИЩИЙ", "4%", "0%", "72%")),
        ("2002", ("58", "НАЙВИЩИЙ", "5%", "0%", "53%")),
        ("third", ("69", "НАЙВИЩИЙ", "4%", "0%", "65%")),
    ],
)
def test_report_scores_read_printed_values(parsed, name, expected) -> None:
    data, _report = parsed[name]
    scores = report_scores(data)
    assert (
        scores["similarity"],
        scores["risk"],
        scores["paraphrase"],
        scores["wrong_citation"],
        scores["text_matches"],
    ) == expected


def test_report_scores_are_none_without_labels(parsed) -> None:
    data, report = parsed["2020"]
    source = fitz.open(stream=data, filetype="pdf")
    single = fitz.open()
    try:
        single.insert_pdf(source, from_page=report.body_first + 5, to_page=report.body_first + 5)
        stripped = single.tobytes()
    finally:
        single.close()
        source.close()
    assert set(report_scores(stripped).values()) == {None}
