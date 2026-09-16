"""Тести даних компонента перегляду — PLAN_PLAG_FILTER_V2.md, §8.5, §9.2 етап 8.

Дані вигадані — прізвище «Петренко», ініціали «О. А.».
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st

from plag_filter.fetch import wayback_calendar_url, wayback_copy_url
from plag_filter.project import new_project
from plag_filter.rules import recompute
from plag_filter.types import (
    PlagProject,
    PlagReport,
    SourceCheck,
    SourceRow,
    SourceState,
)
from plag_filter.view import apply_viewer_event, archive_links, viewer_payload

ROOT = Path(__file__).resolve().parent.parent
REPORT_2002 = ROOT / "examples" / "plag" / "Plag_Originality_Report_2026-09-09_16-36-02.pdf"


# ---------------------------------------------------------------------------
# Допоміжні вигадані дані
# ---------------------------------------------------------------------------


def make_row(number: int, percent: float | None = 5.0) -> SourceRow:
    return SourceRow(
        number=number,
        percent=percent,
        percent_text="" if percent is None else f"{percent}%",
        label="example.org",
        urls=(f"https://example.org/doc{number}",),
        list_page=10,
        band=(0.0, 10.0),
        row_cuts=(),
        link_rects=(),
        source_id=f"src{number}",
    )


def make_report(numbers: tuple[int, ...] = (1, 2)) -> PlagReport:
    return PlagReport(
        sha256="deadbeef",
        page_count=40,
        body_first=3,
        list_first=30,
        rows={number: make_row(number) for number in numbers},
        events=(),
        pages_by_number={number: (3,) for number in numbers},
        numbers_by_page={3: numbers},
        highlight_width={number: 10.0 for number in numbers},
        longest_run={number: 5.0 for number in numbers},
        title_text="",
    )


def make_project(report: PlagReport) -> PlagProject:
    project = PlagProject(
        schema_version=2,
        report_sha256=report.sha256,
        report_name="report.pdf",
        surname="Петренко",
        initials="О.А.",
        year=2002,
        confirmed=True,
        given_name="Олена",
        patronymic="Андріївна",
        states={
            number: SourceState(
                number=number,
                source_id=row.source_id,
                check=None,
                manual=None,
                alt_url=None,
                decision="disputed",
                reason="unchecked",
            )
            for number, row in report.rows.items()
        },
    )
    recompute(project, report)
    return project


# ---------------------------------------------------------------------------
# apply_viewer_event — PLAN_PLAG_FILTER_V2.md, §9.2 етап 8
# ---------------------------------------------------------------------------


def test_decision_event_excludes_source() -> None:
    report = make_report()
    project = make_project(report)

    changed = apply_viewer_event(
        project, report, {"type": "decision", "number": 1, "manual": "exclude"}
    )

    assert changed is True
    assert project.states[1].manual == "exclude"
    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "manual_exclude"


def test_decision_event_keep_then_none_returns_automatic_decision() -> None:
    report = make_report()
    project = make_project(report)
    automatic = (project.states[2].decision, project.states[2].reason)

    assert apply_viewer_event(
        project, report, {"type": "decision", "number": 2, "manual": "keep"}
    )
    assert project.states[2].decision == "keep"
    assert project.states[2].reason == "manual_keep"

    assert apply_viewer_event(
        project, report, {"type": "decision", "number": 2, "manual": None}
    )
    assert project.states[2].manual is None
    assert (project.states[2].decision, project.states[2].reason) == automatic


def test_decision_event_with_unknown_number_or_value_is_ignored() -> None:
    report = make_report()
    project = make_project(report)

    assert (
        apply_viewer_event(
            project, report, {"type": "decision", "number": 99, "manual": "exclude"}
        )
        is False
    )
    assert (
        apply_viewer_event(
            project, report, {"type": "decision", "number": 1, "manual": "видалити"}
        )
        is False
    )
    assert apply_viewer_event(project, report, {"type": "інше", "number": 1}) is False
    assert apply_viewer_event(project, report, {}) is False
    assert all(state.manual is None for state in project.states.values())


def test_page_event_is_clamped_to_document_and_written_to_session_state() -> None:
    report = make_report()
    project = make_project(report)
    st.session_state["plag_page"] = 5

    event = {"type": "page", "page": 900}
    assert apply_viewer_event(project, report, event) is True
    assert event["page"] == report.page_count
    assert st.session_state["plag_page"] == report.page_count

    event = {"type": "page", "page": -3}
    assert apply_viewer_event(project, report, event) is True
    assert event["page"] == 1
    assert st.session_state["plag_page"] == 1

    # Те саме значення не є зміною.
    assert apply_viewer_event(project, report, {"type": "page", "page": 1}) is False
    assert apply_viewer_event(project, report, {"type": "page", "page": "п'ять"}) is False
    assert st.session_state["plag_page"] == 1


# ---------------------------------------------------------------------------
# viewer_payload на звіті 2002 — corpus
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def data_2002() -> bytes:
    return REPORT_2002.read_bytes()


@pytest.fixture(scope="module")
def report_2002(data_2002: bytes) -> PlagReport:
    from plag_filter.pdf import parse_report

    return parse_report(data_2002)


@pytest.mark.corpus
def test_viewer_payload_matches_events_and_sources_of_page(
    data_2002: bytes, report_2002: PlagReport
) -> None:
    project = new_project(report_2002, "report.pdf")
    page = 4
    page_index = page - 1

    numbers_on_page = report_2002.numbers_by_page.get(page_index, ())
    visible = sorted(
        number
        for number in numbers_on_page
        if report_2002.rows[number].percent is None
        or report_2002.rows[number].percent >= 0.1
    )
    assert visible, "на аркуші 4 звіту 2002 повинні бути видимі джерела"

    project.states[visible[0]].manual = "exclude"
    recompute(project, report_2002)

    payload = viewer_payload(data_2002, report_2002, project, page, show_excluded=True)

    assert payload["page"] == page
    assert payload["page_count"] == report_2002.page_count
    assert payload["image"].startswith("data:image/png;base64,")
    assert payload["show_excluded"] is True

    events = [event for event in report_2002.events if event.page == page_index]
    assert len(payload["overlay"]) == len(events)
    for item, event in zip(payload["overlay"], events):
        assert item["number"] == event.number
        assert item["kind"] == event.kind
        assert item["excluded"] is (
            project.states[event.number].decision == "exclude"
        )
        for key in ("x0", "y0", "x1", "y1"):
            assert 0.0 <= item[key] <= 1.0

    assert [source["number"] for source in payload["sources"]] == visible
    assert payload["below_count"] == len(numbers_on_page) - len(visible)

    outside = viewer_payload(
        data_2002, report_2002, project, report_2002.list_first + 1, show_excluded=False
    )
    assert outside["overlay"] == []
    assert outside["sources"] == []

    first = payload["sources"][0]
    assert first["decision"] == "exclude"
    assert first["manual"] == "exclude"
    assert first["url"] == report_2002.rows[visible[0]].urls[0]
    assert first["label"] == report_2002.rows[visible[0]].label
    assert first["reason_label"] == "Виключено вручну"


@pytest.mark.corpus
@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("unavailable", True),
        ("date_unknown", True),
        ("own_work", False),
        ("later", False),
        ("earlier", False),
    ],
)
def test_viewer_payload_offers_archive_calendar_only_where_it_helps(
    data_2002: bytes, report_2002: PlagReport, reason: str, expected: bool
) -> None:
    """Календар архіву — лише для недоступних і недатованих джерел —
    PLAN_PLAG_FILTER_V2.md, §11 запис 14."""
    project = new_project(report_2002, "report.pdf")
    page = 4
    number = sorted(
        candidate
        for candidate in report_2002.numbers_by_page.get(page - 1, ())
        if report_2002.rows[candidate].percent is None
        or report_2002.rows[candidate].percent >= 0.1
    )[0]
    project.states[number].reason = reason

    payload = viewer_payload(data_2002, report_2002, project, page, show_excluded=True)
    source = next(item for item in payload["sources"] if item["number"] == number)

    if expected:
        assert source["archive_url"] == wayback_calendar_url(source["url"])
    else:
        assert source["archive_url"] == ""


# ---------------------------------------------------------------------------
# document_url — PLAN_PLAG_FILTER_V3.md, §7 етап 2
# ---------------------------------------------------------------------------


def _make_check(url: str, final_url: str | None, error: str | None = None) -> SourceCheck:
    return SourceCheck(
        checked_for="петренко|о.а.",
        url=url,
        final_url=final_url,
        error=error,
        author_hit=None,
        doc_date=None,
        date_basis=None,
        date_conflict=False,
        url_year_hint=None,
        hints={},
    )


def _first_visible_number(report: PlagReport, page: int) -> int:
    return sorted(
        candidate
        for candidate in report.numbers_by_page.get(page - 1, ())
        if report.rows[candidate].percent is None or report.rows[candidate].percent >= 0.1
    )[0]


@pytest.mark.corpus
def test_document_url_present_when_final_url_differs(data_2002: bytes, report_2002: PlagReport) -> None:
    project = new_project(report_2002, "report.pdf")
    page = 4
    number = _first_visible_number(report_2002, page)
    url = report_2002.rows[number].urls[0]
    project.states[number].check = _make_check(url, final_url="https://web.archive.org/web/20200101/doc")
    recompute(project, report_2002)

    payload = viewer_payload(data_2002, report_2002, project, page, show_excluded=True)
    source = next(item for item in payload["sources"] if item["number"] == number)

    assert source["document_url"] == "https://web.archive.org/web/20200101/doc"


@pytest.mark.corpus
def test_document_url_empty_when_check_missing(data_2002: bytes, report_2002: PlagReport) -> None:
    project = new_project(report_2002, "report.pdf")
    page = 4
    number = _first_visible_number(report_2002, page)

    payload = viewer_payload(data_2002, report_2002, project, page, show_excluded=True)
    source = next(item for item in payload["sources"] if item["number"] == number)

    assert source["document_url"] == ""


@pytest.mark.corpus
def test_document_url_empty_when_check_has_error(data_2002: bytes, report_2002: PlagReport) -> None:
    project = new_project(report_2002, "report.pdf")
    page = 4
    number = _first_visible_number(report_2002, page)
    url = report_2002.rows[number].urls[0]
    project.states[number].check = _make_check(url, final_url=None, error="http_404")
    recompute(project, report_2002)

    payload = viewer_payload(data_2002, report_2002, project, page, show_excluded=True)
    source = next(item for item in payload["sources"] if item["number"] == number)

    assert source["document_url"] == ""


@pytest.mark.corpus
def test_document_url_empty_when_final_url_equals_original(
    data_2002: bytes, report_2002: PlagReport
) -> None:
    project = new_project(report_2002, "report.pdf")
    page = 4
    number = _first_visible_number(report_2002, page)
    url = report_2002.rows[number].urls[0]
    project.states[number].check = _make_check(url, final_url=url)
    recompute(project, report_2002)

    payload = viewer_payload(data_2002, report_2002, project, page, show_excluded=True)
    source = next(item for item in payload["sources"] if item["number"] == number)

    assert source["document_url"] == ""


# ---------------------------------------------------------------------------
# archive_links — посилання на архів у таблиці всіх джерел
# ---------------------------------------------------------------------------


def _state_with_check(number: int, check: SourceCheck | None, reason: str) -> SourceState:
    return SourceState(
        number=number,
        source_id=f"src{number}",
        check=check,
        manual=None,
        alt_url=None,
        decision="disputed",
        reason=reason,
    )


def test_archive_links_gives_copy_when_document_came_from_archive() -> None:
    """Архів віддав документ — таблиця веде прямо на копію, календар зайвий."""
    row = make_row(1)
    url = row.urls[0]
    copy = wayback_copy_url(url, 2002)
    check = _make_check(url, final_url=copy)
    check.archive_used = True

    assert archive_links(_state_with_check(1, check, "same_year"), row) == (copy, "")


def test_archive_links_gives_calendar_when_document_is_unavailable() -> None:
    """Документа немає ніде — копії немає, лишається перелік знімків."""
    row = make_row(1)
    url = row.urls[0]
    check = _make_check(url, final_url=None, error="http_404")

    assert archive_links(_state_with_check(1, check, "unavailable"), row) == (
        "",
        wayback_calendar_url(url),
    )


def test_archive_links_gives_both_when_copy_found_but_date_unknown() -> None:
    """Копія є, а дати в ній забракло: обидва посилання потрібні водночас."""
    row = make_row(1)
    url = row.urls[0]
    copy = wayback_copy_url(url, 2002)
    check = _make_check(url, final_url=copy)
    check.archive_used = True

    assert archive_links(_state_with_check(1, check, "date_unknown"), row) == (
        copy,
        wayback_calendar_url(url),
    )


def test_archive_links_ignores_plain_redirect_that_is_not_archive() -> None:
    """Звичайне переадресування — не архівна копія, стовпець лишається порожнім."""
    row = make_row(1)
    check = _make_check(row.urls[0], final_url="https://example.org/inshyi-doc")

    assert archive_links(_state_with_check(1, check, "same_year"), row) == ("", "")


def test_archive_links_empty_without_check() -> None:
    """Неперевірене джерело не пропонує ні копії, ні календаря."""
    row = make_row(1)

    assert archive_links(_state_with_check(1, None, "unchecked"), row) == ("", "")
