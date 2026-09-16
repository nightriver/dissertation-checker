"""Тести розбору звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 1."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import fitz
import pytest

from plag_filter.pdf import (
    UnsupportedReportError,
    _truncated_row,
    filter_pdf,
    is_truncated_row,
    parse_report,
)
from plag_filter.types import SourceRow

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


REPORT_TRUNCATED = EXAMPLES / "!Plag_Originality_Report_2026-09-17_01-07-15.pdf"


def _listed_row(number: int, percent: float) -> SourceRow:
    return SourceRow(
        number=number,
        percent=percent,
        percent_text=f"{percent}%",
        label="doi.org",
        urls=("https://doi.org",),
        list_page=5,
        band=(100.0, 120.0),
        row_cuts=((0, 1),),
        link_rects=((0.0, 0.0, 1.0, 1.0),),
        source_id="abc",
    )


def test_marker_beyond_zero_percent_list_tail_becomes_truncated_row() -> None:
    """Plag обрізав перелік: номер понад останній рядок з 0.0 % — джерело 0.0 %."""
    rows = {1: _listed_row(1, 4.5), 2: _listed_row(2, 0.0)}
    row = _truncated_row(rows, 7)
    rows[7] = row

    assert row.percent == 0.0
    assert row.urls == ()
    assert row.row_cuts == ()
    assert row.list_page == 5
    assert is_truncated_row(row)
    assert not is_truncated_row(rows[2])
    assert "№ 2" in row.label
    # Порядок маркерів у тілі довільний: менший номер після більшого теж приймається.
    assert _truncated_row(rows, 3).number == 3


@pytest.mark.parametrize(
    ("rows", "number"),
    [
        ({1: _listed_row(1, 4.5), 3: _listed_row(3, 0.0)}, 2),  # пропуск усередині переліку
        ({1: _listed_row(1, 4.5), 2: _listed_row(2, 0.2)}, 3),  # ненульовий хвіст
        ({}, 1),  # перелік порожній
    ],
    ids=["gap", "nonzero_tail", "empty"],
)
def test_marker_not_explained_by_truncation_is_unsupported(rows, number) -> None:
    with pytest.raises(UnsupportedReportError) as info:
        _truncated_row(rows, number)
    assert info.value.code == "marker_not_in_list"


@pytest.mark.corpus
def test_truncated_report_keeps_markers_beyond_list() -> None:
    """Звіт 2026-09-17: перелік обрізано на № 1001, маркери в тілі до № 1457."""
    data = REPORT_TRUNCATED.read_bytes()
    report = parse_report(data)
    truncated = sorted(n for n, row in report.rows.items() if is_truncated_row(row))

    assert max(n for n, row in report.rows.items() if not is_truncated_row(row)) == 1001
    assert truncated[0] > 1001 and truncated[-1] == 1457
    assert all(report.rows[n].percent == 0.0 for n in truncated)
    assert all(report.pages_by_number.get(n) for n in truncated)
    assert filter_pdf(data, report, set(truncated))
