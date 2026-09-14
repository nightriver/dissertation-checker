"""Тести проєкту й протоколу режиму очищення звіту Plag — PLAN_PLAG_FILTER.md, §10.2, етап 4.

Дані вигадані — PLAN_PLAG_FILTER.md, §3, приклад автора «Петренко», «О. А.».
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from plag_filter.project import (
    REQUIRED_PROTOCOL_PHRASE,
    from_json,
    new_project,
    protocol_paragraphs,
    to_json,
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


def make_state(
    number: int = 1,
    check: SourceCheck | None = None,
    manual: str | None = None,
    decision: str = "disputed",
    reason: str = "unchecked",
) -> SourceState:
    return SourceState(
        number=number,
        source_id=f"src{number}",
        check=check,
        manual=manual,
        alt_url=None,
        decision=decision,
        reason=reason,
    )


def make_project(
    year: int | None = 2020,
    confirmed: bool = True,
    states: dict[int, SourceState] | None = None,
) -> PlagProject:
    return PlagProject(
        schema_version=2,
        report_sha256="deadbeef",
        report_name="report.pdf",
        surname=SURNAME,
        initials=INITIALS,
        year=year,
        confirmed=confirmed,
        states=states or {},
    )


# ---------------------------------------------------------------------------
# to_json / from_json — §9
# ---------------------------------------------------------------------------


def test_to_json_from_json_roundtrip_preserves_project() -> None:
    report = make_report({1: make_row(1, 5.0), 2: make_row(2, 3.0)})
    check = SourceCheck(
        checked_for="петренко|о.а.",
        url="https://example.org/doc",
        final_url="https://example.org/doc2",
        error=None,
        author_hit=AuthorHit(page=1, snippet="петренко о. а."),
        doc_date=DateInterval(date(2019, 5, 3), date(2019, 5, 3), "day"),
        date_basis="pdf:2019 (стор. 1)",
        date_conflict=False,
        url_year_hint=2019,
        hints={"pdf_creation": "2019-05-03"},
    )
    states = {
        1: make_state(1, check=check, decision="exclude", reason="own_work"),
        2: make_state(2, manual="keep", decision="keep", reason="manual_keep"),
    }
    project = make_project(states=states)

    restored = from_json(to_json(project), report)

    assert restored == project


def test_to_json_from_json_roundtrip_without_check_or_manual() -> None:
    report = make_report({1: make_row(1, 5.0)})
    project = make_project(states={1: make_state(1)})

    restored = from_json(to_json(project), report)

    assert restored == project


# ---------------------------------------------------------------------------
# from_json — помилки узгодженості — §9
# ---------------------------------------------------------------------------


def test_from_json_rejects_different_sha256() -> None:
    report = make_report({1: make_row(1, 5.0)})
    project = make_project(states={1: make_state(1)})
    project.report_sha256 = "other-sha"

    with pytest.raises(ValueError):
        from_json(to_json(project), report)


def test_from_json_rejects_unsupported_schema_version() -> None:
    report = make_report({1: make_row(1, 5.0)})
    project = make_project(states={1: make_state(1)})
    text = to_json(project).replace('"schema_version": 2', '"schema_version": 3')

    with pytest.raises(ValueError):
        from_json(text, report)


def test_from_json_reads_schema_version_1_without_new_fields() -> None:
    report = make_report({1: make_row(1, 5.0)})
    payload = {
        "schema_version": 1,
        "report_sha256": report.sha256,
        "report_name": "report.pdf",
        "surname": SURNAME,
        "initials": INITIALS,
        "year": 2020,
        "confirmed": True,
        "states": {},
    }
    text_v1 = json.dumps(payload, ensure_ascii=False)

    restored = from_json(text_v1, report)

    assert restored.given_name == ""
    assert restored.patronymic == ""
    assert restored.schema_version == 2


def test_to_json_from_json_roundtrip_preserves_given_name_and_patronymic() -> None:
    report = make_report({1: make_row(1, 5.0)})
    project = make_project(states={1: make_state(1)})
    project.given_name = "Олена"
    project.patronymic = "Андріївна"

    restored = from_json(to_json(project), report)

    assert restored == project
    assert restored.given_name == "Олена"
    assert restored.patronymic == "Андріївна"


def test_from_json_rejects_mismatched_source_id() -> None:
    report = make_report({1: make_row(1, 5.0)})
    project = make_project(states={1: make_state(1)})
    text = to_json(project).replace('"src1"', '"src-other"')

    with pytest.raises(ValueError):
        from_json(text, report)


# ---------------------------------------------------------------------------
# new_project — §4, §9
# ---------------------------------------------------------------------------


def test_new_project_marks_low_percent_rows_below_threshold() -> None:
    report = make_report({1: make_row(1, 0.05), 2: make_row(2, 5.0)})

    project = new_project(report, "report.pdf")

    assert project.schema_version == 2
    assert project.report_sha256 == report.sha256
    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "below_threshold"
    assert project.states[2].decision == "disputed"
    assert project.states[2].reason == "unchecked"


# ---------------------------------------------------------------------------
# protocol_paragraphs — §8
# ---------------------------------------------------------------------------


def test_protocol_paragraphs_include_required_phrase() -> None:
    report = make_report({1: make_row(1, 5.0)})
    project = make_project(states={1: make_state(1)})

    paragraphs = protocol_paragraphs(project, report)

    assert REQUIRED_PROTOCOL_PHRASE in paragraphs
    assert paragraphs[-1] == REQUIRED_PROTOCOL_PHRASE


def test_protocol_paragraphs_format_excluded_numbers_as_ranges() -> None:
    rows = {number: make_row(number, 5.0) for number in (1, 2, 3, 5)}
    report = make_report(rows)
    states = {
        number: make_state(number, decision="exclude", reason="below_threshold")
        for number in (1, 2, 3, 5)
    }
    project = make_project(states=states)

    paragraphs = protocol_paragraphs(project, report)

    joined = "\n".join(paragraphs)
    assert "1–3, 5" in joined


def test_protocol_paragraphs_own_work_fragment_trimmed_to_120_chars() -> None:
    long_snippet = "а" * 200
    check = SourceCheck(
        checked_for="петренко|о.а.",
        url="https://example.org/doc",
        final_url=None,
        error=None,
        author_hit=AuthorHit(page=0, snippet=long_snippet),
        doc_date=None,
        date_basis=None,
        date_conflict=False,
        url_year_hint=None,
        hints={},
    )
    report = make_report({1: make_row(1, 5.0)})
    states = {1: make_state(1, check=check, decision="exclude", reason="own_work")}
    project = make_project(states=states)

    paragraphs = protocol_paragraphs(project, report)

    matches = [paragraph for paragraph in paragraphs if "№1" in paragraph and "власна робота" in paragraph]
    assert len(matches) == 1
    assert "а" * 120 in matches[0]
    assert "а" * 121 not in matches[0]


def test_protocol_paragraphs_list_disputed_with_reason_labels() -> None:
    rows = {1: make_row(1, 5.0), 2: make_row(2, 5.0)}
    report = make_report(rows)
    states = {
        1: make_state(1, decision="disputed", reason="unavailable"),
        2: make_state(2, decision="disputed", reason="same_year"),
    }
    project = make_project(states=states)

    paragraphs = protocol_paragraphs(project, report)

    joined = "\n".join(paragraphs)
    assert "№1" in joined and "Документ недоступний — перевірити" in joined
    assert "№2" in joined and "Той самий рік — перевірити" in joined
