"""Тести підсумкового PDF нового формату — PLAN_PLAG_FILTER_V4.md, §6.5, §7 етап 6."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from plag_filter.project import REQUIRED_PROTOCOL_PHRASE, new_project, protocol_paragraphs
from plag_filter.rules import recompute
from plag_filter.v4.align import load_dissertation
from plag_filter.v4.build import build_from_parts, build_report
from plag_filter.v4.export import (
    PALETTE_BACKGROUND,
    highlighted_dissertation,
    highlighted_pdf_name,
)
from plag_filter.v4.page import island_page_spans
from plag_filter.v4.report import PlagEntry, PlagFragment, V4Report

ROOT = Path(__file__).resolve().parent.parent
NEW = ROOT / "examples" / "plag" / "new plag"
REPORT_V4 = NEW / "plag-report-докт-R001904.pdf"
DISS_V4 = NEW / "source" / "Чванкін докт.pdf"

WORDS = [f"word{i:02d}x" for i in range(40)]


def synthetic_pdf() -> bytes:
    """Два аркуші; острів джерела № 1 переходить з першого на другий."""
    doc = fitz.open()
    first = doc.new_page()
    first.insert_text((72, 100), " ".join(WORDS[0:10]), fontsize=10)
    first.insert_text((72, 120), " ".join(WORDS[10:20]), fontsize=10)
    second = doc.new_page()
    second.insert_text((72, 100), " ".join(WORDS[20:30]), fontsize=10)
    second.insert_text((72, 120), " ".join(WORDS[30:40]), fontsize=10)
    return doc.tobytes()


def main(number: int, url: str, first: int, count: int) -> PlagEntry:
    run = " ".join(WORDS[first : first + count])
    return PlagEntry(
        section="main",
        number=number,
        domain=url.split("/")[2],
        url=url,
        words=count,
        percent=1.0,
        sentences=None,
        max_percent=None,
        fragments=(PlagFragment(text=run, runs=(run,)),),
        source_texts=(),
        report_page=0,
    )


@pytest.fixture()
def synthetic():
    data = synthetic_pdf()
    parsed = V4Report(
        report_id="R-000123",
        file_name="Вигаданко.pdf",
        total_words=100,
        uploaded="",
        scores=(),
        entries=(main(1, "http://a.example/x", 14, 12), main(2, "http://b.example/y", 30, 9)),
        short_count=3,
    )
    report = build_from_parts(parsed, load_dissertation(data), sha256="2" * 64)
    project = new_project(report, "звіт.pdf")
    project.surname, project.given_name, project.year = "Вигаданко", "Олена", 2020
    return data, report, project


def annotations(pdf: bytes) -> list[tuple[int, str, tuple[float, ...]]]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        found = []
        for page in doc:
            for annot in page.annots() or []:
                assert annot.type[1] == "Highlight"
                found.append((page.number, annot.info["title"], tuple(annot.colors["stroke"])))
        return found
    finally:
        doc.close()


def expected_annotations(report, numbers) -> int:
    return sum(
        len(island_page_spans(report, island.start, island.end))
        for number in numbers
        for island in report.v4.islands[number]
    )


def protocol_text(pdf: bytes, first_protocol_page: int) -> str:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        return " ".join(doc[i].get_text() for i in range(first_protocol_page, doc.page_count))
    finally:
        doc.close()


def test_one_annotation_per_island_and_page(synthetic):
    data, report, project = synthetic
    pdf = highlighted_dissertation(report, project, data)
    found = annotations(pdf)
    assert len(found) == expected_annotations(report, [1, 2]) == 3
    assert {page for page, _title, _color in found} == {0, 1}
    titles = {title for _page, title, _color in found}
    assert titles == {"№1 · a.example", "№2 · b.example"}
    first_color = next(color for _p, title, color in found if title.startswith("№1"))
    assert first_color == pytest.approx(tuple(c / 255 for c in PALETTE_BACKGROUND[0]), abs=0.01)


def test_excluded_source_has_no_annotations(synthetic):
    data, report, project = synthetic
    project.states[1].manual = "exclude"
    recompute(project, report)
    found = annotations(highlighted_dissertation(report, project, data))
    assert found and all(title.startswith("№2") for _page, title, _color in found)
    assert len(found) == expected_annotations(report, [2])


def test_protocol_appended_with_v4_phrase_and_counters(synthetic):
    data, report, project = synthetic
    pdf = highlighted_dissertation(report, project, data)
    text = " ".join(protocol_text(pdf, report.page_count).split())
    assert "Показники Plag у звіті R-000123 належать до вихідного звіту" in text
    assert "коротких збігів 3" in text
    assert "Джерел за адресами: 2" in text
    paragraphs = protocol_paragraphs(project, report)
    assert REQUIRED_PROTOCOL_PHRASE not in paragraphs


def test_highlighted_pdf_name(synthetic):
    _data, report, _project = synthetic
    assert highlighted_pdf_name("Вигаданко канд.pdf", report) == "Вигаданко канд_plag_R-000123.pdf"


@pytest.mark.corpus
def test_corpus_r1904_export():
    diss_data = DISS_V4.read_bytes()
    report = build_report(REPORT_V4.read_bytes(), diss_data)
    project = new_project(report, "звіт.pdf")
    pdf = highlighted_dissertation(report, project, diss_data)
    found = annotations(pdf)
    # Джерела < 0,1 % правило одразу виключає (`below_threshold`) — без підсвітки.
    kept = [n for n, state in sorted(project.states.items()) if state.decision != "exclude"]
    assert len(kept) < len(report.rows)
    assert len(found) == expected_annotations(report, kept)
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        assert doc.page_count > report.page_count
    finally:
        doc.close()
