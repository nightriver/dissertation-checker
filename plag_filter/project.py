"""Проєкт очищення звіту Plag як дані: серіалізація та протокол.

Контракт узятий з `PLAN_PLAG_FILTER.md`, §4, §8, §9, доповнений
`PLAN_PLAG_FILTER_V2.md`, §8.1, §9.2 (етапи 1–2, 5–6).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from plag_filter.rules import date_evidence, recompute
from plag_filter.types import (
    AuthorHit,
    DateInterval,
    PlagProject,
    PlagReport,
    Reason,
    REASON_LABELS,
    SourceCheck,
    SourceState,
)

# Обов'язкова фраза протоколу — PLAN_PLAG_FILTER.md, §8.
REQUIRED_PROTOCOL_PHRASE = (
    "Відсотки й оцінки Plag на початкових сторінках належать до вихідного звіту "
    "та не перераховувалися."
)


def new_project(report: PlagReport, report_name: str) -> PlagProject:
    """Створити новий проєкт для звіту й одразу порахувати рішення — §9."""
    states = {
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
    }
    project = PlagProject(
        schema_version=2,
        report_sha256=report.sha256,
        report_name=report_name,
        surname="",
        initials="",
        year=None,
        confirmed=False,
        given_name="",
        patronymic="",
        states=states,
    )
    recompute(project, report)
    return project


def _author_hit_to_dict(hit: AuthorHit | None) -> dict | None:
    if hit is None:
        return None
    return {"page": hit.page, "snippet": hit.snippet, "kind": hit.kind}


def _author_hit_from_dict(data: dict | None) -> AuthorHit | None:
    if data is None:
        return None
    return AuthorHit(page=data["page"], snippet=data["snippet"], kind=data.get("kind", "byline"))


def _date_interval_to_dict(interval: DateInterval | None) -> dict | None:
    if interval is None:
        return None
    return {
        "start": interval.start.isoformat(),
        "end": interval.end.isoformat(),
        "precision": interval.precision,
    }


def _date_interval_from_dict(data: dict | None) -> DateInterval | None:
    if data is None:
        return None
    return DateInterval(
        start=date.fromisoformat(data["start"]),
        end=date.fromisoformat(data["end"]),
        precision=data["precision"],
    )


def _check_to_dict(check: SourceCheck | None) -> dict | None:
    if check is None:
        return None
    return {
        "checked_for": check.checked_for,
        "url": check.url,
        "final_url": check.final_url,
        "error": check.error,
        "author_hit": _author_hit_to_dict(check.author_hit),
        "doc_date": _date_interval_to_dict(check.doc_date),
        "date_basis": check.date_basis,
        "date_conflict": check.date_conflict,
        "url_year_hint": check.url_year_hint,
        "hints": dict(check.hints),
        "citation_years": list(check.citation_years),
        "archive_used": check.archive_used,
        "archive_first_capture": check.archive_first_capture,
    }


def _check_from_dict(data: dict | None) -> SourceCheck | None:
    if data is None:
        return None
    return SourceCheck(
        checked_for=data["checked_for"],
        url=data["url"],
        final_url=data["final_url"],
        error=data["error"],
        author_hit=_author_hit_from_dict(data["author_hit"]),
        doc_date=_date_interval_from_dict(data["doc_date"]),
        date_basis=data["date_basis"],
        date_conflict=data["date_conflict"],
        url_year_hint=data["url_year_hint"],
        hints=dict(data["hints"]),
        citation_years=list(data.get("citation_years", [])),
        archive_used=data.get("archive_used", False),
        archive_first_capture=data.get("archive_first_capture"),
    )


def _state_to_dict(state: SourceState) -> dict:
    return {
        "number": state.number,
        "source_id": state.source_id,
        "check": _check_to_dict(state.check),
        "manual": state.manual,
        "alt_url": state.alt_url,
        "decision": state.decision,
        "reason": state.reason,
    }


def _state_from_dict(data: dict) -> SourceState:
    return SourceState(
        number=data["number"],
        source_id=data["source_id"],
        check=_check_from_dict(data["check"]),
        manual=data["manual"],
        alt_url=data["alt_url"],
        decision=data["decision"],
        reason=data["reason"],
    )


def to_json(project: PlagProject) -> str:
    """Серіалізувати проєкт у JSON — PLAN_PLAG_FILTER.md, §9."""
    payload = {
        "schema_version": project.schema_version,
        "report_sha256": project.report_sha256,
        "report_name": project.report_name,
        "surname": project.surname,
        "initials": project.initials,
        "year": project.year,
        "confirmed": project.confirmed,
        "given_name": project.given_name,
        "patronymic": project.patronymic,
        "states": {
            str(number): _state_to_dict(state) for number, state in project.states.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def from_json(text: str, report: PlagReport) -> PlagProject:
    """Відновити проєкт із JSON, перевіривши узгодженість зі звітом — §9.

    `ValueError`: інша версія схеми, інший звіт (SHA-256), чужий `source_id`
    у стані джерела.
    """
    data = json.loads(text)

    schema_version = data.get("schema_version")
    if schema_version not in (1, 2):
        raise ValueError(f"Непідтримувана версія схеми проєкту: {schema_version!r}")

    report_sha256 = data.get("report_sha256")
    if report_sha256 != report.sha256:
        raise ValueError("Проєкт створено для іншого звіту (інший SHA-256)")

    states: dict[int, SourceState] = {}
    for key, raw_state in data.get("states", {}).items():
        number = int(key)
        state = _state_from_dict(raw_state)
        row = report.rows.get(number)
        if row is not None and row.source_id != state.source_id:
            raise ValueError(f"Джерело №{number}: source_id не збігається зі звітом")
        states[number] = state

    return PlagProject(
        schema_version=2,
        report_sha256=report_sha256,
        report_name=data["report_name"],
        surname=data["surname"],
        initials=data["initials"],
        year=data["year"],
        confirmed=data["confirmed"],
        given_name=data.get("given_name", ""),
        patronymic=data.get("patronymic", ""),
        states=states,
    )


def _format_ranges(numbers: list[int]) -> str:
    """Стиснути відсортовані номери в діапазони «1–3, 5» — PLAN_PLAG_FILTER.md, §8."""
    if not numbers:
        return ""
    ordered = sorted(numbers)
    ranges: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for number in ordered[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append((start, prev))
        start = prev = number
    ranges.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in ranges)


def protocol_paragraphs(project: PlagProject, report: PlagReport) -> list[str]:
    """Абзаци протоколу очищення для вставки в PDF — PLAN_PLAG_FILTER.md, §8."""
    paragraphs: list[str] = []

    confirmed_text = "підтверджено" if project.confirmed else "не підтверджено"
    year_text = str(project.year) if project.year is not None else "не вказано"
    if project.given_name:
        author_text = " ".join(
            part for part in (project.surname, project.given_name, project.patronymic) if part
        )
    else:
        author_text = f"{project.surname} {project.initials}"
    paragraphs.append(
        f"Автор дисертації: {author_text}. "
        f"Рік дисертації: {year_text}. Автора і рік {confirmed_text}."
    )

    numbers_by_reason: dict[Reason, list[int]] = defaultdict(list)
    for number, state in sorted(project.states.items()):
        numbers_by_reason[state.reason].append(number)

    counters = "; ".join(
        f"{REASON_LABELS[reason]}: {len(numbers)}"
        for reason, numbers in numbers_by_reason.items()
    )
    if counters:
        paragraphs.append(f"Кількість джерел за причинами: {counters}.")

    for reason, numbers in numbers_by_reason.items():
        excluded_numbers = [
            number for number in numbers if project.states[number].decision == "exclude"
        ]
        if not excluded_numbers:
            continue
        paragraphs.append(
            f"Виключено за причиною «{REASON_LABELS[reason]}»: "
            f"{_format_ranges(excluded_numbers)}."
        )

    for number in numbers_by_reason.get("own_work", []):
        state = project.states[number]
        snippet = ""
        if state.check is not None and state.check.author_hit is not None:
            snippet = state.check.author_hit.snippet
        fragment = snippet[:120]
        paragraphs.append(f"№{number}: власна робота, фрагмент: «{fragment}».")

    for number in numbers_by_reason.get("cites_author", []):
        state = project.states[number]
        snippet = ""
        if state.check is not None and state.check.author_hit is not None:
            snippet = state.check.author_hit.snippet
        fragment = snippet[:120]
        paragraphs.append(f"№{number}: цитує автора, фрагмент: «{fragment}».")

    for number in numbers_by_reason.get("later", []):
        state = project.states[number]
        evidence = date_evidence(state.check, project.year) if state.check is not None else ""
        evidence = evidence or "невідома"
        paragraphs.append(f"№{number}: пізніша за дисертацію, {evidence}.")

    disputed_numbers = [
        number for number, state in sorted(project.states.items()) if state.decision == "disputed"
    ]
    if disputed_numbers:
        items = ", ".join(
            f"№{number} ({REASON_LABELS[project.states[number].reason]})"
            for number in disputed_numbers
        )
        paragraphs.append(f"Спірні джерела: {items}.")

    paragraphs.append(REQUIRED_PROTOCOL_PHRASE)

    return paragraphs
