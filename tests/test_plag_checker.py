"""Тести перевірки партії джерел режиму очищення звіту Plag —
PLAN_PLAG_FILTER.md, §10.2, етап 6, доповнено `PLAN_PLAG_FILTER_V2.md`,
§9.2 (етап 3) — паралельна перевірка.

Замість мережі — підроблена функція `fetch`, що повертає заздалегідь
підготовлені `FetchResult` за адресою. Дані вигадані — PLAN_PLAG_FILTER.md,
§3, приклад автора «Петренко», «О. А.».
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from plag_filter.checker import check_batch, pending_count, recheck_source
from plag_filter.fetch import FetchResult
from plag_filter.rules import author_key
from plag_filter.types import (
    AuthorHit,
    PlagProject,
    PlagReport,
    SourceRow,
    SourceState,
)

SURNAME = "Петренко"
INITIALS = "О. А."


def make_row(
    number: int,
    percent: float | None = 5.0,
    urls: tuple[str, ...] = ("https://example.org/doc",),
) -> SourceRow:
    return SourceRow(
        number=number,
        percent=percent,
        percent_text="" if percent is None else f"{percent}%",
        label="example.org",
        urls=urls,
        list_page=10,
        band=(0.0, 10.0),
        row_cuts=(),
        link_rects=(),
        source_id=f"src{number}",
    )


def make_report(rows: dict[int, SourceRow], highlight_width: dict[int, float] | None = None) -> PlagReport:
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
        longest_run={},
        title_text="",
    )


def make_state(
    number: int,
    check=None,
    manual: str | None = None,
    alt_url: str | None = None,
    decision: str = "disputed",
    reason: str = "unchecked",
) -> SourceState:
    return SourceState(
        number=number,
        source_id=f"src{number}",
        check=check,
        manual=manual,
        alt_url=alt_url,
        decision=decision,
        reason=reason,
    )


def make_project(
    states: dict[int, SourceState],
    year: int | None = 2020,
    confirmed: bool = True,
    surname: str = SURNAME,
    initials: str = INITIALS,
) -> PlagProject:
    return PlagProject(
        schema_version=1,
        report_sha256="deadbeef",
        report_name="report.pdf",
        surname=surname,
        initials=initials,
        year=year,
        confirmed=confirmed,
        states=states,
    )


def ok_result(url: str, pages: list[str] | None = None) -> FetchResult:
    return FetchResult(
        ok=True,
        error=None,
        url=url,
        final_url=url,
        kind="pdf",
        pages=pages if pages is not None else ["Звичайний текст документа без прізвища автора."],
        meta={},
        jsonld=[],
        repository_meta={},
        hints={},
    )


def ok_html_result(url: str, pages: list[str] | None = None, meta: dict[str, str] | None = None) -> FetchResult:
    return FetchResult(
        ok=True,
        error=None,
        url=url,
        final_url=url,
        kind="html",
        pages=pages if pages is not None else ["Звичайний текст сторінки без прізвища автора."],
        meta=meta or {},
        jsonld=[],
        repository_meta={},
        hints={},
    )


def error_result(url: str, error: str) -> FetchResult:
    return FetchResult(
        ok=False,
        error=error,
        url=url,
        final_url=None,
        kind=None,
        pages=[],
        meta={},
        jsonld=[],
        repository_meta={},
        hints={},
    )


class FakeFetch:
    """Підроблена мережа: словник `url -> FetchResult` і журнал викликів."""

    def __init__(self, results: dict[str, FetchResult]) -> None:
        self.results = results
        self.calls: list[str] = []

    def __call__(self, url: str, *, tmp_dir: Path) -> FetchResult:
        self.calls.append(url)
        if url in self.results:
            return self.results[url]
        return error_result(url, "network_error")


# ---------------------------------------------------------------------------
# Непідтверджений проєкт — §9
# ---------------------------------------------------------------------------


def test_check_batch_raises_value_error_when_not_confirmed(tmp_path: Path) -> None:
    report = make_report({1: make_row(1)})
    project = make_project({1: make_state(1)}, confirmed=False)
    fetch = FakeFetch({})

    with pytest.raises(ValueError):
        check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)


# ---------------------------------------------------------------------------
# Порядок і відбір кандидатів — §9
# ---------------------------------------------------------------------------


def test_check_batch_orders_by_width_desc_then_number(tmp_path: Path) -> None:
    rows = {
        1: make_row(1, percent=5.0, urls=("https://a.example/1",)),
        2: make_row(2, percent=5.0, urls=("https://a.example/2",)),
        3: make_row(3, percent=5.0, urls=("https://a.example/3",)),
    }
    report = make_report(rows, highlight_width={1: 10.0, 2: 10.0, 3: 20.0})
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    fetch = FakeFetch({row.urls[0]: ok_result(row.urls[0]) for row in rows.values()})

    order = check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert order == [3, 1, 2]


def test_check_batch_skips_below_threshold_and_keeps_unrecognized_percent(tmp_path: Path) -> None:
    rows = {
        1: make_row(1, percent=0.05, urls=("https://a.example/low",)),
        2: make_row(2, percent=None, urls=("https://a.example/none",)),
    }
    report = make_report(rows, highlight_width={1: 100.0, 2: 1.0})
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    fetch = FakeFetch({row.urls[0]: ok_result(row.urls[0]) for row in rows.values()})

    order = check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert order == [2]
    assert "https://a.example/low" not in fetch.calls
    assert project.states[1].check is None


# ---------------------------------------------------------------------------
# Зміна автора повертає джерело в партію — §9
# ---------------------------------------------------------------------------


def test_check_batch_rechecks_source_checked_for_different_author(tmp_path: Path) -> None:
    row = make_row(1, urls=("https://a.example/1",))
    report = make_report({1: row}, highlight_width={1: 1.0})
    # Стан з перевіркою для іншого автора.
    from plag_filter.types import SourceCheck

    old_source_check = SourceCheck(
        checked_for=author_key("Іванов", "П. П."),
        url=row.urls[0],
        final_url=row.urls[0],
        error=None,
        author_hit=None,
        doc_date=None,
        date_basis=None,
        date_conflict=False,
        url_year_hint=None,
        hints={},
    )
    states = {1: make_state(1, check=old_source_check)}
    project = make_project(states)
    fetch = FakeFetch({row.urls[0]: ok_result(row.urls[0])})

    order = check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert order == [1]
    assert project.states[1].check.checked_for == author_key(SURNAME, INITIALS)


def test_check_batch_does_not_recheck_source_checked_for_current_author(tmp_path: Path) -> None:
    from plag_filter.types import SourceCheck

    row = make_row(1, urls=("https://a.example/1",))
    report = make_report({1: row}, highlight_width={1: 1.0})
    current_check = SourceCheck(
        checked_for=author_key(SURNAME, INITIALS),
        url=row.urls[0],
        final_url=row.urls[0],
        error=None,
        author_hit=None,
        doc_date=None,
        date_basis=None,
        date_conflict=False,
        url_year_hint=None,
        hints={},
    )
    states = {1: make_state(1, check=current_check)}
    project = make_project(states)
    fetch = FakeFetch({row.urls[0]: ok_result(row.urls[0])})

    order = check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert order == []
    assert fetch.calls == []


# ---------------------------------------------------------------------------
# Однаковий URL і rate_limited — §9
# ---------------------------------------------------------------------------


def test_check_batch_requests_same_url_once(tmp_path: Path) -> None:
    shared_url = "https://a.example/shared"
    rows = {
        1: make_row(1, urls=(shared_url,)),
        2: make_row(2, urls=(shared_url,)),
    }
    report = make_report(rows, highlight_width={1: 1.0, 2: 1.0})
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    fetch = FakeFetch({shared_url: ok_result(shared_url)})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert fetch.calls.count(shared_url) == 1


def test_check_batch_skips_other_sources_of_host_after_rate_limited(tmp_path: Path) -> None:
    url1 = "https://busy.example/one"
    url2 = "https://busy.example/two"
    rows = {
        1: make_row(1, urls=(url1,)),
        2: make_row(2, urls=(url2,)),
    }
    # Партія обробляється у порядку номерів (однакова вага) — url1 йде першим.
    report = make_report(rows, highlight_width={1: 2.0, 2: 1.0})
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    fetch = FakeFetch({url1: error_result(url1, "rate_limited")})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert url2 not in fetch.calls
    assert project.states[2].check.error == "rate_limited"


# ---------------------------------------------------------------------------
# Побудова SourceCheck: власна робота, пізніша дата, помилка, alt_url — §9
# ---------------------------------------------------------------------------


def test_check_batch_marks_own_work_when_author_found(tmp_path: Path) -> None:
    url = "https://a.example/own"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1)})
    fetch = FakeFetch({url: ok_result(url, pages=["УДК 004.9. Петренко О. А. розглянуто питання."])})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "own_work"
    assert project.states[1].check.author_hit is not None


def test_check_batch_marks_later_when_document_date_after_dissertation_year(tmp_path: Path) -> None:
    url = "https://a.example/later"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1)}, year=2018)
    fetch = FakeFetch({url: ok_result(url, pages=["Матеріали конференції. Вип. 5, 2020."])})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "later"
    assert project.states[1].check.doc_date.start.year == 2020


def test_check_batch_marks_unavailable_on_fetch_error(tmp_path: Path) -> None:
    url = "https://a.example/broken"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1)})
    fetch = FakeFetch({url: error_result(url, "http_404")})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert project.states[1].decision == "disputed"
    assert project.states[1].reason == "unavailable"
    assert project.states[1].check.error == "http_404"


def test_check_batch_html_without_meta_uses_html_head_anchor(tmp_path: Path) -> None:
    url = "https://a.example/reader"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1)}, year=2005)
    fetch = FakeFetch({url: ok_html_result(url, pages=["Харків, 2011. Далі текст сторінки."])})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    check = project.states[1].check
    assert check.doc_date is not None
    assert check.doc_date.start.year == 2011
    assert check.date_basis is not None
    assert check.date_basis.startswith("html:")
    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "later"


def test_check_batch_html_court_registry_uses_court_date(tmp_path: Path) -> None:
    url = "https://reyestr.court.gov.ua/Review/12345"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1)}, year=2020)
    fetch = FakeFetch(
        {url: ok_html_result(url, pages=["Справа № 1. Дата ухвалення рішення: 05.03.2015. Суддя."])}
    )

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    check = project.states[1].check
    assert check.doc_date is not None
    assert check.doc_date.start.year == 2015
    assert check.doc_date.precision == "day"
    assert project.states[1].decision == "keep"
    assert project.states[1].reason == "earlier"


def test_check_batch_uses_alt_url_instead_of_first_url(tmp_path: Path) -> None:
    original_url = "https://a.example/original"
    alt_url = "https://a.example/alt"
    row = make_row(1, urls=(original_url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1, alt_url=alt_url)})
    fetch = FakeFetch({alt_url: ok_result(alt_url)})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert original_url not in fetch.calls
    assert alt_url in fetch.calls
    assert project.states[1].check.url == alt_url


# ---------------------------------------------------------------------------
# Ручне рішення і progress — §9
# ---------------------------------------------------------------------------


def test_check_batch_preserves_manual_decision_after_recheck(tmp_path: Path) -> None:
    url = "https://a.example/manual"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1, manual="keep")})
    fetch = FakeFetch({url: ok_result(url, pages=["Петренко О. А. власна стаття."])})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path)

    assert project.states[1].manual == "keep"
    assert project.states[1].decision == "keep"
    assert project.states[1].reason == "manual_keep"


def test_check_batch_calls_progress_for_each_source(tmp_path: Path) -> None:
    rows = {
        1: make_row(1, urls=("https://a.example/1",)),
        2: make_row(2, urls=("https://a.example/2",)),
    }
    report = make_report(rows, highlight_width={1: 2.0, 2: 1.0})
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    fetch = FakeFetch({row.urls[0]: ok_result(row.urls[0]) for row in rows.values()})

    calls: list[tuple[int, int]] = []
    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path, progress=lambda done, total: calls.append((done, total)))

    assert calls == [(1, 2), (2, 2)]


# ---------------------------------------------------------------------------
# recheck_source — §9
# ---------------------------------------------------------------------------


def test_recheck_source_updates_check_and_recomputes_decision(tmp_path: Path) -> None:
    url = "https://a.example/orig"
    alt_url = "https://a.example/alt"
    row = make_row(1, urls=(url,))
    report = make_report({1: row}, highlight_width={1: 1.0})
    project = make_project({1: make_state(1)})
    fetch = FakeFetch(
        {alt_url: ok_result(alt_url, pages=["УДК 004.9. Петренко О. А. авторський текст."])}
    )

    recheck_source(report, project, 1, alt_url, fetch=fetch, tmp_dir=tmp_path)

    assert project.states[1].check.url == alt_url
    assert project.states[1].decision == "exclude"
    assert project.states[1].reason == "own_work"


# ---------------------------------------------------------------------------
# Паралельна перевірка — PLAN_PLAG_FILTER_V2.md, §9.2, етап 3
# ---------------------------------------------------------------------------


def _make_batch(count: int, hosts: int) -> tuple[PlagReport, dict[str, SourceRow]]:
    """30 джерел на кількох хостах — детермінований набір для порівняння workers."""
    rows: dict[int, SourceRow] = {}
    widths: dict[int, float] = {}
    for number in range(1, count + 1):
        host_index = number % hosts
        url = f"https://host{host_index}.example/doc{number}"
        rows[number] = make_row(number, percent=5.0, urls=(url,))
        widths[number] = float(count - number)
    report = make_report(rows, highlight_width=widths)
    return report, rows


def _fake_result_for(number: int, url: str) -> FetchResult:
    if number % 3 == 0:
        return ok_result(url, pages=["УДК 004.9. Петренко О. А. авторський текст."])
    if number % 3 == 1:
        return ok_result(url, pages=["Звичайний текст без прізвища автора."])
    return error_result(url, "http_404")


class _RecordingFetch:
    """Підроблена мережа з журналом викликів під `Lock` — потокобезпечна."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[str] = []

    def __call__(self, url: str, *, tmp_dir: Path) -> FetchResult:
        with self.lock:
            self.calls.append(url)
        number = int(url.rsplit("doc", 1)[1])
        return _fake_result_for(number, url)


def test_check_batch_workers_matches_sequential_result(tmp_path: Path) -> None:
    count, hosts = 30, 10
    report1, rows1 = _make_batch(count, hosts)
    report2, rows2 = _make_batch(count, hosts)
    states1 = {number: make_state(number) for number in rows1}
    states2 = {number: make_state(number) for number in rows2}
    project1 = make_project(states1)
    project2 = make_project(states2)

    check_batch(report1, project1, fetch=_RecordingFetch(), tmp_dir=tmp_path, limit=count, workers=1)
    check_batch(report2, project2, fetch=_RecordingFetch(), tmp_dir=tmp_path, limit=count, workers=6)

    for number in range(1, count + 1):
        state1, state2 = project1.states[number], project2.states[number]
        assert state1.decision == state2.decision
        assert state1.reason == state2.reason
        assert state1.check.error == state2.check.error
        hit1 = state1.check.author_hit
        hit2 = state2.check.author_hit
        assert (hit1 is None) == (hit2 is None)
        if hit1 is not None:
            assert hit1.kind == hit2.kind


def test_check_batch_limits_concurrent_requests_per_host(tmp_path: Path) -> None:
    count, hosts = 24, 8
    report, rows = _make_batch(count, hosts)
    states = {number: make_state(number) for number in rows}
    project = make_project(states)

    lock = threading.Lock()
    current_per_host: dict[str, int] = defaultdict(int)
    max_per_host: dict[str, int] = defaultdict(int)
    current_total = 0
    max_total = 0

    def slow_fetch(url: str, *, tmp_dir: Path) -> FetchResult:
        nonlocal current_total, max_total
        host = url.split("//", 1)[1].split("/", 1)[0]
        with lock:
            current_per_host[host] += 1
            max_per_host[host] = max(max_per_host[host], current_per_host[host])
            current_total += 1
            max_total = max(max_total, current_total)
        time.sleep(0.05)
        number = int(url.rsplit("doc", 1)[1])
        result = _fake_result_for(number, url)
        with lock:
            current_per_host[host] -= 1
            current_total -= 1
        return result

    check_batch(report, project, fetch=slow_fetch, tmp_dir=tmp_path, limit=count, workers=6)

    assert max(max_per_host.values()) == 1
    assert 2 <= max_total <= 6


def test_check_batch_rate_limited_host_skips_remaining_with_workers(tmp_path: Path) -> None:
    shared_url = "https://busy.example/shared"
    rows = {
        1: make_row(1, urls=("https://busy.example/one",)),
        2: make_row(2, urls=("https://busy.example/two",)),
        3: make_row(3, urls=(shared_url,)),
        4: make_row(4, urls=(shared_url,)),
    }
    report = make_report(rows, highlight_width={1: 4.0, 2: 3.0, 3: 2.0, 4: 1.0})
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    fetch = FakeFetch({"https://busy.example/one": error_result("https://busy.example/one", "rate_limited")})

    check_batch(report, project, fetch=fetch, tmp_dir=tmp_path, limit=4, workers=6)

    assert fetch.calls.count("https://busy.example/one") == 1
    assert "https://busy.example/two" not in fetch.calls
    assert shared_url not in fetch.calls
    assert project.states[2].check.error == "rate_limited"
    assert project.states[3].check.error == "rate_limited"
    assert project.states[4].check.error == "rate_limited"


def test_check_batch_progress_called_from_calling_thread(tmp_path: Path) -> None:
    count, hosts = 12, 6
    report, rows = _make_batch(count, hosts)
    states = {number: make_state(number) for number in rows}
    project = make_project(states)
    test_thread = threading.get_ident()

    calls: list[tuple[int, int]] = []
    threads_seen: set[int] = set()

    def progress(done: int, total: int) -> None:
        calls.append((done, total))
        threads_seen.add(threading.get_ident())

    check_batch(
        report, project, fetch=_RecordingFetch(), tmp_dir=tmp_path,
        limit=count, workers=6, progress=progress,
    )

    assert calls == [(i, count) for i in range(1, count + 1)]
    assert threads_seen == {test_thread}


def test_pending_count_before_and_after_batch(tmp_path: Path) -> None:
    count, hosts = 10, 5
    report, rows = _make_batch(count, hosts)
    states = {number: make_state(number) for number in rows}
    project = make_project(states)

    assert pending_count(report, project) == count

    check_batch(report, project, fetch=_RecordingFetch(), tmp_dir=tmp_path, limit=4, workers=1)

    assert pending_count(report, project) == count - 4
