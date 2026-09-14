"""Тести розбору звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 1."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import fitz
import pytest

from plag_filter.pdf import UnsupportedReportError, parse_report

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "plag"
REPORT_2020 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-34-45.pdf"
REPORT_2002 = EXAMPLES / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"

# Очікувані числа — PLAN_PLAG_FILTER.md, §2 і §10.2 (етап 1).
EXPECTED = {
    REPORT_2020: {"rows": 913, "percent_ge": 121, "marker": 7843, "highlight": 15733},
    REPORT_2002: {"rows": 697, "percent_ge": 149, "marker": 4361, "highlight": 7080},
}


@pytest.fixture(scope="module")
def report_2020():
    return parse_report(REPORT_2020.read_bytes())


@pytest.fixture(scope="module")
def report_2002():
    return parse_report(REPORT_2002.read_bytes())


@pytest.mark.corpus
@pytest.mark.parametrize("report_path", [REPORT_2020, REPORT_2002], ids=["2020", "2002"])
def test_row_and_event_counts_match_measured_numbers(report_path) -> None:
    report = parse_report(report_path.read_bytes())
    expected = EXPECTED[report_path]

    assert len(report.rows) == expected["rows"]

    with_percent = sum(
        1 for row in report.rows.values() if row.percent is not None and row.percent >= 0.1
    )
    assert with_percent == expected["percent_ge"]

    markers = sum(1 for event in report.events if event.kind == "marker")
    highlights = sum(1 for event in report.events if event.kind == "highlight")
    assert markers == expected["marker"]
    assert highlights == expected["highlight"]


@pytest.mark.corpus
def test_every_row_has_a_bound_link(report_2002) -> None:
    for number, row in report_2002.rows.items():
        assert row.urls, f"джерело №{number} без посилання"
        assert len(row.link_rects) == len(row.urls)


@pytest.mark.corpus
def test_row_624_of_2002_report_has_two_links(report_2002) -> None:
    assert len(report_2002.rows[624].urls) == 2


@pytest.mark.corpus
@pytest.mark.parametrize("fixture_name", ["report_2020", "report_2002"])
def test_label_prefix_matches_host_of_first_uri(fixture_name, request) -> None:
    report = request.getfixturevalue(fixture_name)
    for number, row in report.rows.items():
        host = (urlparse(row.urls[0]).hostname or "").casefold()
        if host.startswith("www."):
            host = host[len("www."):]
        prefix = row.label.split(" / ")[0].casefold()
        assert prefix == host, f"джерело №{number}: '{prefix}' != '{host}'"


@pytest.mark.corpus
@pytest.mark.parametrize("fixture_name", ["report_2020", "report_2002"])
def test_highlight_width_is_positive_when_number_has_highlight(fixture_name, request) -> None:
    report = request.getfixturevalue(fixture_name)
    numbers_with_highlight = {
        event.number for event in report.events if event.kind == "highlight"
    }
    for number in numbers_with_highlight:
        assert report.highlight_width.get(number, 0.0) > 0


@pytest.mark.corpus
@pytest.mark.parametrize("fixture_name", ["report_2020", "report_2002"])
def test_pages_by_number_and_numbers_by_page_are_consistent(fixture_name, request) -> None:
    report = request.getfixturevalue(fixture_name)
    for page, numbers in report.numbers_by_page.items():
        for number in numbers:
            assert page in report.pages_by_number.get(number, ())
    for number, pages in report.pages_by_number.items():
        for page in pages:
            assert number in report.numbers_by_page.get(page, ())


def test_synthetic_pdf_without_plag_markup_is_unsupported() -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Синтетичний документ без розмітки Plag.")
    data = doc.tobytes()
    doc.close()

    with pytest.raises(UnsupportedReportError):
        parse_report(data)
