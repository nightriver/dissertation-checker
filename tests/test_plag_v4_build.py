"""Тести збирання джерел за адресами й `PlagReport` — PLAN_PLAG_FILTER_V4.md, §6.3, §7 етап 3."""

from __future__ import annotations

from pathlib import Path

import pytest

from plag_filter.project import from_json, new_project, to_json
from plag_filter.rules import recompute
from plag_filter.v4.align import DissText, normalize
from plag_filter.v4.build import (
    ISLAND_GAP_CHARS,
    MIN_ISLAND_WORDS,
    MIN_TRANSLATED_SENTENCES,
    build_from_parts,
    build_report,
)
from plag_filter.v4.report import PlagEntry, PlagFragment, V4Report, parse_v4_report

ROOT = Path(__file__).resolve().parent.parent
NEW = ROOT / "examples" / "plag" / "new plag"
SOURCE = NEW / "source"
PAIRS = {
    "R1801": (NEW / "plag-report-dis-Чванкін-R001801.pdf", SOURCE / "dis Чванкін.pdf"),
    "R1904": (NEW / "plag-report-докт-R001904.pdf", SOURCE / "Чванкін докт.pdf"),
    "R1928": (NEW / "plag-report-канд-R001928.pdf", SOURCE / "Білоус канд.pdf"),
}

# Слова синтетичної дисертації: по 5 нормалізованих символів, усі різні.
WORDS = [f"q{i:04d}" for i in range(400)]


def diss_from_tokens(tokens: list[str], pages: list[int] | None = None) -> DissText:
    """`DissText` зі слів; `pages[i]` — аркуш слова `i` (типово 0)."""
    chars: list[str] = []
    char_page: list[int] = []
    word_start: list[bool] = []
    for index, token in enumerate(tokens):
        normalized = normalize(token)
        for position, char in enumerate(normalized):
            chars.append(char)
            char_page.append(pages[index] if pages else 0)
            word_start.append(position == 0)
    return DissText(
        stream="".join(chars),
        char_page=tuple(char_page),
        char_bbox=((0.0, 0.0, 0.0, 0.0),) * len(chars),
        word_start=tuple(word_start),
        page_count=max(pages) + 1 if pages else 1,
        title_text="",
    )


def run_of(first: int, count: int) -> str:
    return " ".join(WORDS[first : first + count])


def main(number: int, url: str, *runs: str, domain: str = "example.org") -> PlagEntry:
    return PlagEntry(
        section="main",
        number=number,
        domain=domain,
        url=url,
        words=10,
        percent=1.0,
        sentences=None,
        max_percent=None,
        fragments=tuple(PlagFragment(text=run, runs=(run,)) for run in runs),
        source_texts=("текст джерела",),
        report_page=number,
    )


def translated(number: int, url: str, sentences: int, *runs: str) -> PlagEntry:
    return PlagEntry(
        section="translated",
        number=number,
        domain="tr.example",
        url=url,
        words=None,
        percent=None,
        sentences=sentences,
        max_percent=99,
        fragments=tuple(PlagFragment(text=run, runs=(run,)) for run in runs),
        source_texts=("source",),
        report_page=100 + number,
    )


def v4(*entries: PlagEntry, short: int = 0) -> V4Report:
    return V4Report(
        report_id="R-000123",
        file_name="Вигаданко.pdf",
        total_words=1000,
        uploaded="2026-11-12 20:23",
        scores=(),
        entries=entries,
        short_count=short,
    )


def build(*entries: PlagEntry, tokens: list[str] | None = None, short: int = 0):
    diss = diss_from_tokens(tokens if tokens is not None else WORDS)
    return build_from_parts(v4(*entries, short=short), diss, sha256="0" * 64)


# --- числа плану ----------------------------------------------------------------


def test_plan_numbers():
    assert MIN_TRANSLATED_SENTENCES == 7
    assert ISLAND_GAP_CHARS == 40
    assert MIN_ISLAND_WORDS == 8


# --- адреси ---------------------------------------------------------------------


def test_same_address_merged_ignoring_scheme_and_trailing_slash():
    report = build(
        main(1, "http://Example.org/doc/", run_of(0, 10)),
        main(2, "https://example.org/doc", run_of(100, 10)),
        main(3, "https://example.org/doc#page=4", run_of(200, 10)),
    )
    assert len(report.rows) == 1
    row = report.rows[1]
    assert row.urls == ("http://Example.org/doc/",)
    assert [e.number for e in report.v4.entries_by_number[1]] == [1, 2, 3]
    assert report.v4.counters.sources == 1


def test_different_query_is_different_source():
    report = build(
        main(1, "http://example.org/doc?id=1", run_of(0, 10)),
        main(2, "http://example.org/doc?id=2", run_of(100, 10)),
    )
    assert len(report.rows) == 2


def test_translated_below_threshold_is_not_a_source():
    report = build(
        main(1, "http://a.example/x", run_of(0, 10)),
        translated(1, "http://tr.example/six", 6, run_of(100, 10)),
        translated(2, "http://tr.example/seven", 7, run_of(200, 10)),
    )
    counters = report.v4.counters
    assert counters.translated_entries == 2
    assert counters.translated_below == 1
    assert sorted(row.urls[0] for row in report.rows.values()) == [
        "http://a.example/x",
        "http://tr.example/seven",
    ]
    translated_row = next(r for r in report.rows.values() if r.urls[0].endswith("seven"))
    assert translated_row.label == "tr.example · переклад"


def test_translated_merged_with_main_keeps_main_label():
    report = build(
        main(1, "http://a.example/x", run_of(0, 10), domain="a.example"),
        translated(1, "https://a.example/x/", 9, run_of(100, 10)),
    )
    assert len(report.rows) == 1
    assert report.rows[1].label == "a.example"


# --- острови ----------------------------------------------------------------------


def test_islands_merge_at_gap_40():
    # 8 слів, 8 слів проміжку (40 символів), 8 слів.
    report = build(main(1, "http://a.example/x", run_of(0, 8), run_of(16, 8)))
    islands = report.v4.islands[1]
    assert len(islands) == 1
    assert islands[0].words == 24


def test_islands_do_not_merge_at_gap_41():
    tokens = WORDS[:8] + ["z"] + WORDS[8:]
    report = build(main(1, "http://a.example/x", run_of(0, 8), run_of(16, 8)), tokens=tokens)
    islands = report.v4.islands[1]
    assert [island.words for island in islands] == [8, 8]


def test_small_island_dropped_and_counted():
    report = build(
        main(1, "http://a.example/x", run_of(0, 7)),
        main(2, "http://b.example/y", run_of(100, 8)),
    )
    counters = report.v4.counters
    assert counters.small_islands == 1
    assert counters.small_island_words == 7
    by_url = {row.urls[0]: row for row in report.rows.values()}
    small = by_url["http://a.example/x"]
    kept = by_url["http://b.example/y"]
    assert report.v4.islands[small.number] == ()
    assert small.percent == 0.0
    assert kept.percent == pytest.approx(0.8)
    assert kept.percent_text == "0.8 %"


def test_island_pages():
    pages = [0] * 4 + [1] * 4 + [2] * (len(WORDS) - 8)
    diss = diss_from_tokens(WORDS, pages=pages)
    report = build_from_parts(v4(main(1, "http://a.example/x", run_of(2, 8))), diss, sha256="0" * 64)
    assert report.v4.islands[1][0].pages == (0, 1, 2)
    assert report.pages_by_number[1] == (0, 1, 2)
    assert report.numbers_by_page[1] == (1,)


# --- номери, лічильники, PlagReport -----------------------------------------------


def test_numbering_by_words_then_main_then_entry_number():
    report = build(
        main(1, "http://small.example/", run_of(0, 10)),
        main(2, "http://big.example/", run_of(20, 30)),
        main(3, "http://tie-main3.example/", run_of(60, 12)),
        main(4, "http://tie-main4.example/", run_of(80, 12)),
        translated(1, "http://tie-tr.example/", 9, run_of(100, 12)),
    )
    order = [report.rows[n].urls[0] for n in sorted(report.rows)]
    assert order == [
        "http://big.example/",
        "http://tie-main3.example/",
        "http://tie-main4.example/",
        "http://tie-tr.example/",
        "http://small.example/",
    ]


def test_source_id_does_not_depend_on_entry_order():
    entries = [
        main(1, "http://a.example/x", run_of(0, 10)),
        main(2, "http://b.example/y", run_of(100, 20)),
    ]
    first = build(*entries)
    second = build(*reversed(entries))
    ids_first = {row.urls[0]: row.source_id for row in first.rows.values()}
    ids_second = {row.urls[0]: row.source_id for row in second.rows.values()}
    assert ids_first == ids_second


def test_report_fields_and_counters():
    report = build(
        main(1, "http://a.example/x", run_of(0, 10), "відсутній у тексті шматок"),
        main(2, "http://b.example/y", run_of(100, 9)),
        short=5,
    )
    assert report.kind == "v4"
    assert report.sha256 == "0" * 64
    assert report.body_first == 0
    assert report.list_first == report.page_count == 1
    counters = report.v4.counters
    assert counters.main_entries == 2
    assert counters.short_matches == 5
    assert counters.runs_total == 3
    assert counters.runs_located == 2
    assert counters.runs_missing == 1
    assert sum(counters.runs_by_how.values()) == counters.runs_located
    assert set(counters.runs_by_how) == {"unique", "fragment", "nearest", "first"}
    row = report.rows[1]
    assert row.band == (0.0, 0.0) and row.row_cuts == () and row.link_rects == ()
    assert row.list_page == 1
    assert report.highlight_width[1] == 10.0
    assert report.longest_run[1] == 10.0
    assert [event.kind for event in report.events] == ["highlight", "highlight"]
    assert all(event.cuts == () for event in report.events)


def test_rules_and_project_work_on_v4_report():
    report = build(
        main(1, "http://a.example/x", run_of(0, 10)),
        main(2, "http://b.example/y", run_of(100, 7)),
    )
    project = new_project(report, "звіт.pdf")
    recompute(project, report)
    reasons = {number: state.reason for number, state in project.states.items()}
    small = next(n for n, row in report.rows.items() if row.urls[0] == "http://b.example/y")
    assert reasons[small] == "below_threshold"
    restored = from_json(to_json(project), report)
    assert set(restored.states) == set(project.states)


# --- корпус -------------------------------------------------------------------------


@pytest.fixture(scope="module", params=list(PAIRS), ids=list(PAIRS))
def built(request):
    report_path, diss_path = PAIRS[request.param]
    report_data = report_path.read_bytes()
    return parse_v4_report(report_data), build_report(report_data, diss_path.read_bytes())


@pytest.mark.corpus
def test_corpus_build(built):
    parsed, report = built
    main_urls = {e.url for e in parsed.entries if e.section == "main"}
    translated_kept = sum(
        1
        for e in parsed.entries
        if e.section == "translated" and (e.sentences or 0) >= MIN_TRANSLATED_SENTENCES
    )
    assert 0 < len(report.rows) <= len(main_urls) + translated_kept
    assert sorted(report.rows) == list(range(1, len(report.rows) + 1))
    counters = report.v4.counters
    assert sum(counters.runs_by_how.values()) == counters.runs_located
    assert counters.sources == len(report.rows)
    assert report.page_count == report.v4.diss.page_count
    assert len({row.source_id for row in report.rows.values()}) == len(report.rows)
