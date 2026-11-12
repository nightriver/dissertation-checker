"""Тести тексту й аркуша дисертації у вкладках — PLAN_PLAG_FILTER_V4.md, §6.4, §7 етап 4."""

from __future__ import annotations

import time
from pathlib import Path

import fitz
import pytest

from plag_filter.project import new_project
from plag_filter.rules import recompute
from plag_filter.v4 import page as page_module
from plag_filter.v4.align import load_dissertation
from plag_filter.v4.build import build_from_parts, build_report
from plag_filter.v4.page import (
    PARAGRAPH_GAP_RATIO,
    PARAGRAPH_INDENT,
    page_overlay_v4,
    page_text_v4,
    render_page_png_v4,
)
from plag_filter.v4.report import PlagEntry, PlagFragment, V4Report
from plag_filter.view import report_payload, viewer_payload

ROOT = Path(__file__).resolve().parent.parent
NEW = ROOT / "examples" / "plag" / "new plag"
SOURCE = NEW / "source"
PAIRS = {
    "R1801": (NEW / "plag-report-dis-Чванкін-R001801.pdf", SOURCE / "dis Чванкін.pdf"),
    "R1904": (NEW / "plag-report-докт-R001904.pdf", SOURCE / "Чванкін докт.pdf"),
    "R1928": (NEW / "plag-report-канд-R001928.pdf", SOURCE / "Білоус канд.pdf"),
}

WORDS = [f"word{i:02d}x" for i in range(30)]


def synthetic_pdf() -> bytes:
    """Три рядки по 10 слів; третій — з відступом першого рядка (новий абзац)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), " ".join(WORDS[0:10]), fontsize=10)
    page.insert_text((72, 120), " ".join(WORDS[10:20]), fontsize=10)
    page.insert_text((110, 140), " ".join(WORDS[20:30]), fontsize=10)
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
        scores=(("Збіги з іншими джерелами", "97% Унікальність: 3%"),),
        entries=(main(1, "http://a.example/x", 0, 12), main(2, "http://b.example/y", 4, 12)),
        short_count=0,
    )
    page_module._text_cache.clear()
    report = build_from_parts(parsed, load_dissertation(data), sha256="1" * 64)
    return data, report


def segment_with(paragraphs, word: str):
    for paragraph in paragraphs:
        for segment in paragraph:
            text = segment["text"] if isinstance(segment, dict) else segment.text
            if word in text:
                return segment
    raise AssertionError(word)


def test_plan_numbers():
    assert PARAGRAPH_INDENT == 15.0
    assert PARAGRAPH_GAP_RATIO == 1.6


def test_page_text_numbers_and_paragraphs(synthetic):
    data, report = synthetic
    assert [row.urls[0] for _, row in sorted(report.rows.items())] == [
        "http://a.example/x",
        "http://b.example/y",
    ]
    paragraphs = page_text_v4(data, report, 0)
    assert len(paragraphs) == 2
    assert segment_with(paragraphs, "word01x").numbers == (1,)
    assert segment_with(paragraphs, "word05x").numbers == (1, 2)
    assert segment_with(paragraphs, "word13x").numbers == (2,)
    assert segment_with(paragraphs, "word18x").numbers == ()
    assert segment_with(paragraphs, "word25x").numbers == ()
    text = "".join(segment.text for segment in paragraphs[0])
    assert text == " ".join(WORDS[0:20])


def test_overlay_one_rect_per_line_and_source(synthetic):
    data, report = synthetic
    items = page_overlay_v4(data, report, 0)
    by_number: dict[int, int] = {}
    for item in items:
        by_number[item.number] = by_number.get(item.number, 0) + 1
        assert item.kind == "highlight"
        assert 0.0 <= item.x0 < item.x1 <= 1.0
        assert 0.0 <= item.y0 < item.y1 <= 1.0
    assert by_number == {1: 2, 2: 2}


def test_payload_shows_first_kept_number(synthetic):
    data, report = synthetic
    project = new_project(report, "звіт.pdf")
    payload = report_payload(data, report, project, 1, False)
    assert payload["scores"] == {}
    assert payload["score_rows"] == [["Збіги з іншими джерелами", "97% Унікальність: 3%"]]
    overlap = segment_with(payload["paragraphs"], "word05x")
    assert overlap["number"] == 1 and overlap["excluded"] is False

    project.states[1].manual = "exclude"
    recompute(project, report)
    overlap = segment_with(report_payload(data, report, project, 1, False)["paragraphs"], "word05x")
    assert overlap["number"] == 2 and overlap["excluded"] is False

    project.states[2].manual = "exclude"
    recompute(project, report)
    overlap = segment_with(report_payload(data, report, project, 1, True)["paragraphs"], "word05x")
    assert overlap["number"] == 1 and overlap["excluded"] is True


def test_viewer_payload_v4(synthetic):
    data, report = synthetic
    project = new_project(report, "звіт.pdf")
    payload = viewer_payload(data, report, project, 1, False)
    assert payload["image"].startswith("data:image/png;base64,")
    assert {item["number"] for item in payload["overlay"]} == {1, 2}
    assert payload["page_count"] == 1


def test_render_page_png_v4(synthetic):
    data, _report = synthetic
    assert render_page_png_v4(data, 0).startswith(b"\x89PNG")


def test_stream_mismatch_raises(synthetic):
    _data, report = synthetic
    other = fitz.open()
    other.new_page().insert_text((72, 100), "other text")
    with pytest.raises(Exception) as exc:
        page_text_v4(other.tobytes(), report, 0)
    assert getattr(exc.value, "code", None) == "v4_stream_mismatch"


# --- корпус -------------------------------------------------------------------


@pytest.fixture(scope="module", params=list(PAIRS), ids=list(PAIRS))
def built(request):
    report_path, diss_path = PAIRS[request.param]
    diss_data = diss_path.read_bytes()
    return diss_data, build_report(report_path.read_bytes(), diss_data)


@pytest.mark.corpus
def test_corpus_pages(built):
    data, report = built
    pages = sorted(report.numbers_by_page)
    chosen = [pages[int(i * (len(pages) - 1) / 4)] for i in range(5)]
    project = new_project(report, "звіт.pdf")
    for page_index in chosen:
        page_module._text_cache.clear()
        started = time.perf_counter()
        paragraphs = page_text_v4(data, report, page_index)
        assert time.perf_counter() - started <= 0.5
        assert paragraphs
        shown = {n for paragraph in paragraphs for segment in paragraph for n in segment.numbers}
        assert set(report.numbers_by_page[page_index]) <= shown
        assert page_overlay_v4(data, report, page_index)
        assert report_payload(data, report, project, page_index + 1, False)["paragraphs"]
        assert viewer_payload(data, report, project, page_index + 1, False)["overlay"]
