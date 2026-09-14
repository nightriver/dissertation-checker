"""Тести правил режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 3.

Дані вигадані — PLAN_PLAG_FILTER.md, §3, приклад автора «Петренко», «О. А.».
"""

from __future__ import annotations

from datetime import date

import pytest

from plag_filter.rules import (
    MAX_AUTHOR_HITS,
    author_key,
    classify_hit,
    date_from_meta,
    date_from_pdf_pages,
    decide,
    find_author,
    find_author_hits,
    order_numbers,
    parse_date_value,
    recompute,
    top20_share,
    url_year_hint,
)
from plag_filter.types import (
    AuthorHit,
    DateInterval,
    PlagProject,
    PlagReport,
    SourceCheck,
    SourceRow,
    SourceState,
)

SURNAME = "Петренко"
INITIALS = "О. А."


def make_row(number: int = 1, percent: float | None = 5.0) -> SourceRow:
    return SourceRow(
        number=number,
        percent=percent,
        percent_text="" if percent is None else f"{percent}%",
        label="example.org",
        urls=("https://example.org/doc",),
        list_page=10,
        band=(0.0, 10.0),
        row_cuts=(),
        link_rects=(),
        source_id=f"src{number}",
    )


def make_state(
    number: int = 1,
    check: SourceCheck | None = None,
    manual: str | None = None,
) -> SourceState:
    return SourceState(
        number=number,
        source_id=f"src{number}",
        check=check,
        manual=manual,
        alt_url=None,
        decision="disputed",
        reason="unchecked",
    )


def make_check(
    checked_for: str = "петренко|о.а.",
    error: str | None = None,
    author_hit: AuthorHit | None = None,
    doc_date: DateInterval | None = None,
    date_conflict: bool = False,
) -> SourceCheck:
    return SourceCheck(
        checked_for=checked_for,
        url="https://example.org/doc",
        final_url=None,
        error=error,
        author_hit=author_hit,
        doc_date=doc_date,
        date_basis=None,
        date_conflict=date_conflict,
        url_year_hint=None,
        hints={},
    )


def make_project(
    year: int | None = 2020,
    confirmed: bool = True,
    states: dict[int, SourceState] | None = None,
) -> PlagProject:
    return PlagProject(
        schema_version=1,
        report_sha256="deadbeef",
        report_name="report.pdf",
        surname=SURNAME,
        initials=INITIALS,
        year=year,
        confirmed=confirmed,
        states=states or {},
    )


def make_report(rows: dict[int, SourceRow], highlight_width: dict[int, float] | None = None,
                 longest_run: dict[int, float] | None = None) -> PlagReport:
    return PlagReport(
        sha256="deadbeef",
        page_count=100,
        body_first=3,
        list_first=50,
        rows=rows,
        events=(),
        pages_by_number={},
        numbers_by_page={},
        highlight_width=highlight_width or {},
        longest_run=longest_run or {},
        title_text="",
    )


# ---------------------------------------------------------------------------
# find_author — приклади §3
# ---------------------------------------------------------------------------

MATCHING_EXAMPLES = [
    "Петренко О. А.",
    "ПЕТРЕНКО О.А.",
    "Петренко, О. А.",
    "О. А. Петренко",
    "Петренко Олена Андріївна",
    "П Е Т Р Е Н К О Олена Андріївна",
    "Петрен-\nко О. А.",
    "Петренко О.",
]

NON_MATCHING_EXAMPLES = [
    "Петренко І. А.",
    "Петренко О. В.",
    "Петренка О. А.",
    "Petrenko O. A.",
    "Петренкович О. А.",
]


@pytest.mark.parametrize("text", MATCHING_EXAMPLES)
def test_find_author_matches_expected_examples(text: str) -> None:
    hit = find_author([text], SURNAME, INITIALS)
    assert hit is not None, text
    assert hit.page == 0


@pytest.mark.parametrize("text", NON_MATCHING_EXAMPLES)
def test_find_author_rejects_expected_examples(text: str) -> None:
    assert find_author([text], SURNAME, INITIALS) is None


def test_find_author_returns_page_and_snippet_of_first_match() -> None:
    pages = ["Нічого немає.", "Тут пише Петренко О. А. про щось важливе."]
    hit = find_author(pages, SURNAME, INITIALS)
    assert hit is not None
    assert hit.page == 1
    assert "петренко о. а." in hit.snippet


# ---------------------------------------------------------------------------
# classify_hit — PLAN_PLAG_FILTER_V2.md, §8.2 етап 2
# ---------------------------------------------------------------------------


def _single_kind(text: str) -> str:
    hits = find_author_hits([text], SURNAME, INITIALS)
    assert len(hits) == 1, text
    return hits[0].kind


def test_classify_hit_copyright_sign_before_is_byline() -> None:
    assert _single_kind("© Петренко О. А.") == "byline"


def test_classify_hit_degree_before_is_byline() -> None:
    assert _single_kind("кандидат педагогічних наук Петренко О. А.") == "byline"


def test_classify_hit_degree_after_is_byline() -> None:
    assert _single_kind("Петренко О. А., кандидат історичних наук") == "byline"


def test_classify_hit_udc_nearby_is_byline() -> None:
    assert _single_kind("УДК 371.132 Петренко О. А.") == "byline"


def test_classify_hit_doi_nearby_is_byline() -> None:
    assert _single_kind("doi:10.1000/xyz Петренко О. А.") == "byline"


def test_classify_hit_publication_list_before_is_byline() -> None:
    assert _single_kind("Список опублікованих праць Петренко О. А.") == "byline"


def test_classify_hit_author_colon_before_is_byline() -> None:
    assert _single_kind("Автор: Петренко О. А.") == "byline"


def test_classify_hit_plain_mention_is_mention() -> None:
    assert _single_kind("Як зазначає Петренко О. А. у своїй роботі.") == "mention"


# ---------------------------------------------------------------------------
# find_author_hits, find_author — PLAN_PLAG_FILTER_V2.md, §8.2 етап 2
# ---------------------------------------------------------------------------


def test_find_author_hits_finds_all_matches_on_two_pages() -> None:
    pages = [
        "Петренко О. А. пише. Далі згадується О. А. Петренко ще раз.",
        "На другій сторінці — Петренко О. А.",
    ]
    hits = find_author_hits(pages, SURNAME, INITIALS)
    assert len(hits) == 3
    assert [hit.page for hit in hits] == [0, 0, 1]


def test_find_author_hits_respects_max_limit() -> None:
    page = " Петренко О. А. " * 25
    hits = find_author_hits([page], SURNAME, INITIALS)
    assert len(hits) == MAX_AUTHOR_HITS


def test_find_author_prefers_byline_that_comes_after_mention() -> None:
    pages = [
        "Як зазначає Петренко О. А. у роботі. Пізніше © Петренко О. А., 2020."
    ]
    hit = find_author(pages, SURNAME, INITIALS)
    assert hit is not None
    assert hit.kind == "byline"


def test_find_author_falls_back_to_mention_when_no_byline() -> None:
    hit = find_author(["Як зазначає Петренко О. А. у роботі."], SURNAME, INITIALS)
    assert hit is not None
    assert hit.kind == "mention"


# ---------------------------------------------------------------------------
# parse_date_value — §5
# ---------------------------------------------------------------------------


def test_parse_date_value_year() -> None:
    assert parse_date_value("2020") == DateInterval(date(2020, 1, 1), date(2020, 12, 31), "year")


def test_parse_date_value_year_month() -> None:
    assert parse_date_value("2020-05") == DateInterval(
        date(2020, 5, 1), date(2020, 5, 31), "month"
    )


def test_parse_date_value_year_month_day_iso() -> None:
    assert parse_date_value("2020-05-03") == DateInterval(
        date(2020, 5, 3), date(2020, 5, 3), "day"
    )


def test_parse_date_value_slash() -> None:
    assert parse_date_value("2020/05/03") == DateInterval(
        date(2020, 5, 3), date(2020, 5, 3), "day"
    )


def test_parse_date_value_dot() -> None:
    assert parse_date_value("2020.05.03") == DateInterval(
        date(2020, 5, 3), date(2020, 5, 3), "day"
    )


def test_parse_date_value_day_month_year() -> None:
    assert parse_date_value("03.05.2020") == DateInterval(
        date(2020, 5, 3), date(2020, 5, 3), "day"
    )


def test_parse_date_value_iso_with_time() -> None:
    assert parse_date_value("2020-05-03T10:20:00Z") == DateInterval(
        date(2020, 5, 3), date(2020, 5, 3), "day"
    )


def test_parse_date_value_out_of_range_year_is_none() -> None:
    assert parse_date_value("1850") is None


def test_parse_date_value_unrecognized_is_none() -> None:
    assert parse_date_value("не дата") is None


# ---------------------------------------------------------------------------
# date_from_meta — §5
# ---------------------------------------------------------------------------


def test_date_from_meta_prefers_citation_publication_date() -> None:
    meta = {
        "citation_publication_date": "2020",
        "citation_date": "2019",
        "dc.date.issued": "2018",
    }
    interval, basis = date_from_meta(meta, [])
    assert interval == DateInterval(date(2020, 1, 1), date(2020, 12, 31), "year")
    assert basis == "meta:citation_publication_date"


def test_date_from_meta_falls_back_to_citation_date() -> None:
    meta = {"citation_date": "2019", "dc.date.issued": "2018"}
    interval, basis = date_from_meta(meta, [])
    assert interval == DateInterval(date(2019, 1, 1), date(2019, 12, 31), "year")
    assert basis == "meta:citation_date"


def test_date_from_meta_falls_back_to_dc_date_issued() -> None:
    meta = {"dc.date.issued": "2018"}
    interval, basis = date_from_meta(meta, [])
    assert interval == DateInterval(date(2018, 1, 1), date(2018, 12, 31), "year")
    assert basis == "meta:dc.date.issued"


def test_date_from_meta_ignores_dc_date_accessioned() -> None:
    meta = {"dc.date.accessioned": "2018"}
    assert date_from_meta(meta, []) is None


def test_date_from_meta_reads_jsonld_date_published() -> None:
    jsonld = ['{"datePublished": "2019"}']
    interval, basis = date_from_meta({}, jsonld)
    assert interval == DateInterval(date(2019, 1, 1), date(2019, 12, 31), "year")
    assert basis == "jsonld:datePublished"


def test_date_from_meta_reads_jsonld_graph_element() -> None:
    jsonld = ['{"@graph": [{"@type": "Organization"}, {"datePublished": "2018"}]}']
    interval, basis = date_from_meta({}, jsonld)
    assert interval == DateInterval(date(2018, 1, 1), date(2018, 12, 31), "year")
    assert basis == "jsonld:datePublished"


def test_date_from_meta_none_when_nothing_found() -> None:
    assert date_from_meta({}, []) is None


# ---------------------------------------------------------------------------
# date_from_pdf_pages — §5
# ---------------------------------------------------------------------------


def test_date_from_pdf_pages_anchor_number_issue() -> None:
    interval, basis, conflict = date_from_pdf_pages(["№ 3, 2020", ""])
    assert interval == DateInterval(date(2020, 1, 1), date(2020, 12, 31), "year")
    assert basis is not None
    assert conflict is False


def test_date_from_pdf_pages_anchor_vypusk() -> None:
    interval, basis, conflict = date_from_pdf_pages(["Вип. 5, 2018", ""])
    assert interval == DateInterval(date(2018, 1, 1), date(2018, 12, 31), "year")
    assert conflict is False


def test_date_from_pdf_pages_anchor_year_dash_number() -> None:
    interval, basis, conflict = date_from_pdf_pages(["2020. – № 3", ""])
    assert interval == DateInterval(date(2020, 1, 1), date(2020, 12, 31), "year")
    assert conflict is False


def test_date_from_pdf_pages_anchor_city() -> None:
    interval, basis, conflict = date_from_pdf_pages(["Київ – 2002", ""])
    assert interval == DateInterval(date(2002, 1, 1), date(2002, 12, 31), "year")
    assert conflict is False


def test_date_from_pdf_pages_two_different_years_conflict() -> None:
    interval, basis, conflict = date_from_pdf_pages(["№ 3, 2020", "№ 4, 2021"])
    assert interval is None
    assert basis is None
    assert conflict is True


def test_date_from_pdf_pages_ignores_third_page() -> None:
    interval, basis, conflict = date_from_pdf_pages(["plain text", "plain text", "№ 3, 2020"])
    assert interval is None
    assert basis is None
    assert conflict is False


# ---------------------------------------------------------------------------
# url_year_hint — §5
# ---------------------------------------------------------------------------


def test_url_year_hint_finds_year_in_path() -> None:
    assert url_year_hint("https://x.org/archive/2019/4/part_1/11.pdf") == 2019


def test_url_year_hint_finds_year_in_filename() -> None:
    assert url_year_hint("https://x.org/report_2022.pdf") == 2022


def test_url_year_hint_ignores_non_year_digit_runs() -> None:
    assert url_year_hint("https://x.org/12345/") is None


# ---------------------------------------------------------------------------
# decide — §4, одна перевірка на кожен рядок таблиці
# ---------------------------------------------------------------------------


def test_decide_rule0_manual_keep_wins_over_own_work() -> None:
    row = make_row(percent=5.0)
    check = make_check(author_hit=AuthorHit(page=0, snippet="петренко о. а."))
    state = make_state(check=check, manual="keep")
    project = make_project()
    assert decide(row, state, project) == ("keep", "manual_keep")


def test_decide_rule0_manual_exclude() -> None:
    row = make_row(percent=5.0)
    state = make_state(manual="exclude")
    project = make_project()
    assert decide(row, state, project) == ("exclude", "manual_exclude")


def test_decide_rule1_below_threshold() -> None:
    row = make_row(percent=0.05)
    state = make_state()
    project = make_project()
    assert decide(row, state, project) == ("exclude", "below_threshold")


def test_decide_rule2_unchecked_when_no_check() -> None:
    row = make_row(percent=5.0)
    state = make_state(check=None)
    project = make_project()
    assert decide(row, state, project) == ("disputed", "unchecked")


def test_decide_rule3_unchecked_when_checked_for_different_author() -> None:
    row = make_row(percent=5.0)
    check = make_check(checked_for="інший|і.і.")
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("disputed", "unchecked")


def test_decide_rule4_unconfirmed() -> None:
    row = make_row(percent=5.0)
    check = make_check()
    state = make_state(check=check)
    project = make_project(confirmed=False)
    assert decide(row, state, project) == ("disputed", "unconfirmed")


def test_decide_rule5_own_work() -> None:
    row = make_row(percent=5.0)
    check = make_check(author_hit=AuthorHit(page=2, snippet="петренко о. а."))
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("exclude", "own_work")


def test_decide_rule5_own_work_wins_over_unavailable() -> None:
    row = make_row(percent=5.0)
    check = make_check(
        author_hit=AuthorHit(page=2, snippet="петренко о. а."), error="http_404"
    )
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("exclude", "own_work")


def test_decide_rule5b_cites_author() -> None:
    row = make_row(percent=5.0)
    check = make_check(
        author_hit=AuthorHit(page=2, snippet="як зазначає петренко о. а.", kind="mention")
    )
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("exclude", "cites_author")


def test_decide_rule0_manual_wins_over_cites_author() -> None:
    row = make_row(percent=5.0)
    check = make_check(
        author_hit=AuthorHit(page=2, snippet="як зазначає петренко о. а.", kind="mention")
    )
    state = make_state(check=check, manual="keep")
    project = make_project()
    assert decide(row, state, project) == ("keep", "manual_keep")


def test_decide_rule6_unavailable() -> None:
    row = make_row(percent=5.0)
    check = make_check(error="http_404")
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("disputed", "unavailable")


def test_decide_rule7_date_conflict() -> None:
    row = make_row(percent=5.0)
    check = make_check(date_conflict=True)
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("disputed", "date_conflict")


def test_decide_rule8_date_unknown() -> None:
    row = make_row(percent=5.0)
    check = make_check(doc_date=None)
    state = make_state(check=check)
    project = make_project()
    assert decide(row, state, project) == ("disputed", "date_unknown")


def test_decide_rule9_later() -> None:
    row = make_row(percent=5.0)
    check = make_check(doc_date=DateInterval(date(2021, 1, 1), date(2021, 12, 31), "year"))
    state = make_state(check=check)
    project = make_project(year=2020)
    assert decide(row, state, project) == ("exclude", "later")


def test_decide_rule10_earlier() -> None:
    row = make_row(percent=5.0)
    check = make_check(doc_date=DateInterval(date(2018, 1, 1), date(2018, 12, 31), "year"))
    state = make_state(check=check)
    project = make_project(year=2020)
    assert decide(row, state, project) == ("keep", "earlier")


def test_decide_rule11_same_year_when_intervals_overlap() -> None:
    row = make_row(percent=5.0)
    check = make_check(doc_date=DateInterval(date(2020, 1, 1), date(2020, 12, 31), "year"))
    state = make_state(check=check)
    project = make_project(year=2020)
    assert decide(row, state, project) == ("disputed", "same_year")


# ---------------------------------------------------------------------------
# recompute — §4, §9
# ---------------------------------------------------------------------------


def test_recompute_marks_checked_source_unchecked_after_initials_change() -> None:
    row = make_row(number=1, percent=5.0)
    check = make_check(checked_for=author_key(SURNAME, INITIALS), doc_date=None)
    state = make_state(number=1, check=check)
    project = make_project(states={1: state})
    report = make_report({1: row})

    recompute(project, report)
    assert project.states[1].reason == "date_unknown"

    project.initials = "І. І."
    recompute(project, report)
    assert project.states[1].decision == "disputed"
    assert project.states[1].reason == "unchecked"


def test_recompute_year_change_moves_same_year_to_later() -> None:
    row = make_row(number=1, percent=5.0)
    check = make_check(
        checked_for=author_key(SURNAME, INITIALS),
        doc_date=DateInterval(date(2020, 1, 1), date(2020, 12, 31), "year"),
    )
    state = make_state(number=1, check=check)
    project = make_project(year=2020, states={1: state})
    report = make_report({1: row})

    recompute(project, report)
    assert project.states[1].reason == "same_year"

    project.year = 2019
    recompute(project, report)
    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "later"


# ---------------------------------------------------------------------------
# order_numbers, top20_share — §8, §9
# ---------------------------------------------------------------------------


def test_order_numbers_by_width_descending_then_number() -> None:
    states = {1: make_state(1), 2: make_state(2), 3: make_state(3)}
    project = make_project(states=states)
    report = make_report(
        {1: make_row(1), 2: make_row(2), 3: make_row(3)},
        highlight_width={1: 5.0, 2: 5.0, 3: 10.0},
    )
    assert order_numbers(project, report, "width") == [3, 1, 2]


def test_order_numbers_by_longest_descending() -> None:
    states = {1: make_state(1), 2: make_state(2)}
    project = make_project(states=states)
    report = make_report(
        {1: make_row(1), 2: make_row(2)}, longest_run={1: 2.0, 2: 8.0}
    )
    assert order_numbers(project, report, "longest") == [2, 1]


def test_order_numbers_by_number() -> None:
    states = {3: make_state(3), 1: make_state(1), 2: make_state(2)}
    project = make_project(states=states)
    report = make_report({1: make_row(1), 2: make_row(2), 3: make_row(3)})
    assert order_numbers(project, report, "number") == [1, 2, 3]


def test_top20_share_none_when_no_highlights() -> None:
    states = {1: make_state(1)}
    project = make_project(states=states)
    project.states[1].decision = "keep"
    report = make_report({1: make_row(1, percent=5.0)}, highlight_width={})
    assert top20_share(project, report) is None


def test_top20_share_uses_top_twenty_of_remaining_sources() -> None:
    states = {}
    rows = {}
    widths = {}
    for number in range(1, 23):
        state = make_state(number)
        state.decision = "keep"
        states[number] = state
        rows[number] = make_row(number, percent=5.0)
        widths[number] = float(number)
    # виключене джерело з найбільшою вагою (№22) не враховується взагалі
    states[22].decision = "exclude"

    project = make_project(states=states)
    report = make_report(rows, highlight_width=widths)

    share = top20_share(project, report)
    remaining = list(range(1, 22))  # № 1..21 лишаються, № 22 виключено
    top20 = sorted(remaining, reverse=True)[:20]  # без найменшого (№1)
    expected = sum(top20) / sum(remaining)
    assert share == pytest.approx(expected)
    assert share < 1.0


# ---------------------------------------------------------------------------
# Фікстура ручної розмітки §2.3 — PLAN_PLAG_FILTER_V2.md, §9.2 етап 2
# ---------------------------------------------------------------------------


def test_author_hits_fixture_has_expected_counts() -> None:
    import json
    from pathlib import Path

    fixture_path = Path(__file__).parent / "fixtures" / "plag_author_hits.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    reports = [key for key in data if key != "_comment"]
    assert len(reports) == 2
    total_byline = sum(len(data[report]["byline"]) for report in reports)
    total_mention = sum(len(data[report]["mention"]) for report in reports)
    assert total_byline == 27
    assert total_mention == 25
