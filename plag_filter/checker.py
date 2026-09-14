"""Перевірка партії джерел режиму очищення звіту Plag.

Контракт узятий з `PLAN_PLAG_FILTER.md`, §4, §5, §6, §9. Мережа підставляється
параметром `fetch`; у продукті це `fetch.fetch_document`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from plag_filter.fetch import FetchResult, fetch_document
from plag_filter.rules import (
    author_key,
    date_from_meta,
    date_from_pdf_pages,
    find_author,
    recompute,
    url_year_hint,
)
from plag_filter.types import PlagProject, PlagReport, SourceCheck

FetchFn = Callable[..., FetchResult]


def _candidate_numbers(report: PlagReport, project: PlagProject, checked_for: str) -> list[int]:
    """Джерела, які потрібно перевірити цього разу — PLAN_PLAG_FILTER.md, §9."""
    numbers: list[int] = []
    for number, state in project.states.items():
        row = report.rows.get(number)
        if row is None:
            continue
        if row.percent is not None and row.percent < 0.1:
            continue
        if state.check is not None and state.check.checked_for == checked_for:
            continue
        numbers.append(number)
    numbers.sort(key=lambda number: (-report.highlight_width.get(number, 0.0), number))
    return numbers


def _build_check(url: str, checked_for: str, result: FetchResult, surname: str, initials: str) -> SourceCheck:
    """Зібрати `SourceCheck` із результату завантаження — PLAN_PLAG_FILTER.md, §5, §9."""
    if not result.ok:
        return SourceCheck(
            checked_for=checked_for,
            url=url,
            final_url=None,
            error=result.error,
            author_hit=None,
            doc_date=None,
            date_basis=None,
            date_conflict=False,
            url_year_hint=url_year_hint(url),
            hints={},
        )

    author_hit = find_author(result.pages, surname, initials)

    doc_date = None
    date_basis = None
    date_conflict = False
    if result.kind == "pdf":
        doc_date, date_basis, date_conflict = date_from_pdf_pages(result.pages)
    else:
        meta_hit = date_from_meta(result.meta, result.jsonld)
        if meta_hit is not None:
            doc_date, date_basis = meta_hit

    if result.repository_meta:
        repo_hit = date_from_meta(result.repository_meta, [])
        if repo_hit is not None:
            repo_date, repo_basis = repo_hit
            if doc_date is None:
                doc_date, date_basis = repo_date, repo_basis
            elif repo_date.start.year != doc_date.start.year:
                date_conflict = True

    return SourceCheck(
        checked_for=checked_for,
        url=url,
        final_url=result.final_url,
        error=None,
        author_hit=author_hit,
        doc_date=doc_date,
        date_basis=date_basis,
        date_conflict=date_conflict,
        url_year_hint=url_year_hint(url),
        hints=dict(result.hints),
    )


def check_batch(
    report: PlagReport,
    project: PlagProject,
    *,
    fetch: FetchFn = fetch_document,
    tmp_dir: Path,
    limit: int = 20,
    progress: Callable[[int, int], None] | None = None,
) -> list[int]:
    """Перевірити чергову партію джерел — PLAN_PLAG_FILTER.md, §9.

    Бере джерела з відсотком ≥ 0,1 або нерозпізнаним, які ще не перевірялися
    або перевірялися для іншого автора, за спаданням W, за рівності — за
    номером. Ручне рішення повторній перевірці не заважає й зберігається.
    Однакова адреса в партії запитується один раз; після `rate_limited`
    решта джерел того самого хоста в цій партії не запитуються.
    """
    if not project.confirmed:
        raise ValueError("Проєкт не підтверджено — партію перевіряти не можна")

    checked_for = author_key(project.surname, project.initials)
    candidates = _candidate_numbers(report, project, checked_for)[:limit]

    cache: dict[str, FetchResult] = {}
    blocked_hosts: set[str] = set()

    total = len(candidates)
    for index, number in enumerate(candidates, start=1):
        row = report.rows[number]
        state = project.states[number]
        url = state.alt_url if state.alt_url else row.urls[0]
        host = urlparse(url).hostname or ""

        if host in blocked_hosts:
            result = FetchResult(
                ok=False,
                error="rate_limited",
                url=url,
                final_url=None,
                kind=None,
                pages=[],
                meta={},
                jsonld=[],
                repository_meta={},
                hints={},
            )
        elif url in cache:
            result = cache[url]
        else:
            result = fetch(url, tmp_dir=tmp_dir)
            cache[url] = result
            if not result.ok and result.error == "rate_limited":
                blocked_hosts.add(host)

        state.check = _build_check(url, checked_for, result, project.surname, project.initials)

        if progress is not None:
            progress(index, total)

    recompute(project, report)
    return candidates


def recheck_source(
    report: PlagReport,
    project: PlagProject,
    number: int,
    url: str,
    *,
    fetch: FetchFn = fetch_document,
    tmp_dir: Path,
) -> None:
    """Перевірити одне джерело за іншою адресою — PLAN_PLAG_FILTER.md, §9."""
    checked_for = author_key(project.surname, project.initials)
    result = fetch(url, tmp_dir=tmp_dir)
    project.states[number].check = _build_check(url, checked_for, result, project.surname, project.initials)
    recompute(project, report)
