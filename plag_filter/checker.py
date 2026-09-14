"""Перевірка партії джерел режиму очищення звіту Plag.

Контракт узятий з `PLAN_PLAG_FILTER.md`, §4, §5, §6, §9, доповнений
`PLAN_PLAG_FILTER_V2.md`, §8.3, §9.2 (етапи 3, 5, 6) — паралельна перевірка,
роки цитування для правила «цитує пізніші праці» та ланцюжок звернень до
Web Archive для недоступних і недатованих документів.
Мережа підставляється параметром `fetch`; у продукті це `fetch.fetch_document`.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urlunparse

from plag_filter.fetch import (
    FetchResult,
    fetch_document,
    parse_cdx_first_capture,
    wayback_copy_url,
    wayback_cdx_url,
)
from plag_filter.rules import (
    author_key,
    citation_years,
    date_from_court_text,
    date_from_html_head,
    date_from_meta,
    date_from_pdf_pages,
    find_author,
    recompute,
    url_year_hint,
)
from plag_filter.types import PlagProject, PlagReport, SourceCheck

FetchFn = Callable[..., FetchResult]

# Числа паралельної перевірки — PLAN_PLAG_FILTER_V2.md, §8.3, §9.2 (етап 3).
MAX_WORKERS = 6
CHUNK_SIZE = 12

# Помилки, після яких пробуємо іншу схему і Web Archive —
# PLAN_PLAG_FILTER_V2.md, §8.3, §9.2 (етап 6).
RETRY_ERRORS = frozenset({"http_404", "http_403", "network_error", "timeout"})


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
        else:
            page_text = result.pages[0] if result.pages else ""
            host = (urlparse(result.final_url or url).hostname or "").casefold()
            if host.endswith("reyestr.court.gov.ua"):
                court_date = date_from_court_text(page_text)
                if court_date is not None:
                    doc_date, date_basis = court_date, "court:дата ухвалення рішення"
            if doc_date is None:
                head_date, head_basis, head_conflict = date_from_html_head(page_text)
                if head_date is not None:
                    doc_date, date_basis = head_date, head_basis
                date_conflict = date_conflict or head_conflict

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
        citation_years=citation_years(result.pages),
    )


def _swap_scheme(url: str) -> str:
    """Замінити схему `http` на `https` і навпаки — PLAN_PLAG_FILTER_V2.md, §9.2 етап 6."""
    parsed = urlparse(url)
    scheme = "https" if parsed.scheme == "http" else "http"
    return urlunparse(parsed._replace(scheme=scheme))


def _rate_limited_result(url: str) -> FetchResult:
    return FetchResult(
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


def _cached_fetch(
    url: str,
    fetch: FetchFn,
    tmp_dir: Path,
    cache: dict[str, FetchResult],
    cache_lock: threading.Lock,
) -> FetchResult:
    with cache_lock:
        cached = cache.get(url)
    if cached is not None:
        return cached
    result = fetch(url, tmp_dir=tmp_dir)
    with cache_lock:
        cache[url] = result
    return result


def _fetch_with_fallback(
    url: str,
    year: int | None,
    fetch: FetchFn,
    tmp_dir: Path,
    cache: dict[str, FetchResult],
    cache_lock: threading.Lock,
    archive_lock: threading.Lock,
    archive_state: dict[str, bool],
) -> tuple[FetchResult, bool, str | None]:
    """Ланцюжок `fetch(url)` → заміна схеми → архівна копія —
    PLAN_PLAG_FILTER_V2.md, §8.4, §9.2 (етап 6).

    Повертає `(result, archive_used, original_error)`. Архівна копія
    використовується, лише якщо і пряма, і схемозамінна спроба з помилкою з
    `RETRY_ERRORS`; успіх архіву позначається `archive_used` і зберігає
    вихідну помилку `r.error` для підказки `hints["original_error"]`. Усі
    звернення до `WAYBACK_BASE` — під `archive_lock`; після `rate_limited`
    від архіву подальші архівні спроби в цій партії пропускаються.
    """
    result = _cached_fetch(url, fetch, tmp_dir, cache, cache_lock)
    if result.ok or result.error not in RETRY_ERRORS:
        return result, False, None

    original_error = result.error
    swap_result = _cached_fetch(_swap_scheme(url), fetch, tmp_dir, cache, cache_lock)
    if swap_result.ok:
        return swap_result, False, None

    if year is None:
        return result, False, None

    with archive_lock:
        if archive_state["blocked"]:
            return result, False, None
        archive_result = fetch(wayback_copy_url(url, year), tmp_dir=tmp_dir)
        if not archive_result.ok and archive_result.error == "rate_limited":
            archive_state["blocked"] = True

    if archive_result.ok:
        return archive_result, True, original_error
    return result, False, None


def _fetch_archive_first_capture(
    url: str,
    fetch: FetchFn,
    tmp_dir: Path,
    archive_lock: threading.Lock,
    archive_state: dict[str, bool],
) -> str | None:
    """Дата першого знімка адреси в архіві — PLAN_PLAG_FILTER_V2.md, §8.4,
    §9.2 (етап 6). Лише для успішно завантажених недатованих документів."""
    with archive_lock:
        if archive_state["blocked"]:
            return None
        result = fetch(wayback_cdx_url(url), tmp_dir=tmp_dir)
        if not result.ok:
            if result.error == "rate_limited":
                archive_state["blocked"] = True
            return None
    if not result.pages:
        return None
    return parse_cdx_first_capture(result.pages[0])


def check_batch(
    report: PlagReport,
    project: PlagProject,
    *,
    fetch: FetchFn = fetch_document,
    tmp_dir: Path,
    limit: int = 20,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 1,
) -> list[int]:
    """Перевірити чергову партію джерел — PLAN_PLAG_FILTER.md, §9, доповнено
    `PLAN_PLAG_FILTER_V2.md`, §9.2 (етап 3) — паралельна перевірка.

    Бере джерела з відсотком ≥ 0,1 або нерозпізнаним, які ще не перевірялися
    або перевірялися для іншого автора, за спаданням W, за рівності — за
    номером. Ручне рішення повторній перевірці не заважає й зберігається.
    Однакова адреса в партії запитується один раз; після `rate_limited`
    решта джерел того самого хоста в цій партії не запитуються.

    `workers` — скільки запитів виконується одночасно (`ThreadPoolExecutor`),
    але не більше одного одночасного запиту на хост. Запис у `state.check` і
    виклик `progress` завжди відбуваються у потоці, що викликав `check_batch`,
    у порядку кандидатів — незалежно від порядку завершення мережевих
    запитів. При `workers=1` поведінка ідентична послідовній.
    """
    if not project.confirmed:
        raise ValueError("Проєкт не підтверджено — партію перевіряти не можна")

    checked_for = author_key(project.surname, project.initials)
    candidates = _candidate_numbers(report, project, checked_for)[:limit]
    total = len(candidates)

    urls: dict[int, str] = {}
    hosts: dict[int, str] = {}
    for number in candidates:
        row = report.rows[number]
        state = project.states[number]
        url = state.alt_url if state.alt_url else row.urls[0]
        urls[number] = url
        hosts[number] = (urlparse(url).hostname or "").casefold()

    cache: dict[str, FetchResult] = {}
    cache_lock = threading.Lock()
    blocked_hosts: set[str] = set()
    blocked_lock = threading.Lock()
    host_locks: dict[str, threading.Lock] = {host: threading.Lock() for host in set(hosts.values())}
    archive_lock = threading.Lock()
    archive_state: dict[str, bool] = {"blocked": False}

    def fetch_for(number: int) -> SourceCheck:
        url = urls[number]
        host = hosts[number]
        with host_locks[host]:
            with blocked_lock:
                host_blocked = host in blocked_hosts
            if host_blocked:
                result, archive_used, original_error = _rate_limited_result(url), False, None
            else:
                result, archive_used, original_error = _fetch_with_fallback(
                    url, project.year, fetch, tmp_dir, cache, cache_lock, archive_lock, archive_state
                )
                if not result.ok and result.error == "rate_limited":
                    with blocked_lock:
                        blocked_hosts.add(host)

        check = _build_check(url, checked_for, result, project.surname, project.initials)
        if archive_used:
            check.archive_used = True
            check.hints["original_error"] = original_error
            if check.date_basis is not None:
                check.date_basis = f"archive:{check.date_basis}"
        if result.ok and check.doc_date is None and not check.date_conflict:
            check.archive_first_capture = _fetch_archive_first_capture(
                url, fetch, tmp_dir, archive_lock, archive_state
            )
        return check

    results: dict[int, SourceCheck]
    if workers <= 1:
        results = {number: fetch_for(number) for number in candidates}
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {number: pool.submit(fetch_for, number) for number in candidates}
            results = {number: futures[number].result() for number in candidates}

    for index, number in enumerate(candidates, start=1):
        project.states[number].check = results[number]
        if progress is not None:
            progress(index, total)

    recompute(project, report)
    return candidates


def pending_count(report: PlagReport, project: PlagProject) -> int:
    """Скільки джерел обрав би `check_batch` без обмеження `limit` —
    PLAN_PLAG_FILTER_V2.md, §8.3, §9.2 (етап 3)."""
    checked_for = author_key(project.surname, project.initials)
    return len(_candidate_numbers(report, project, checked_for))


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
