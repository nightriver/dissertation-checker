"""Дані для компонента перегляду аркуша й застосування його подій.

Контракт — `PLAN_PLAG_FILTER_V2.md`, §8.5, §9.2 етап 8, доповнено
`PLAN_PLAG_FILTER_V3.md`, §7 етап 2 — адреса документа, який приложение
реально отримало. Модуль не малює нічого сам: `viewer_payload` збирає все,
що потрібно компонентові на одному аркуші, а `apply_viewer_event` приймає
від нього рішення експерта та зміну аркуша.
"""

from __future__ import annotations

import base64

import streamlit as st

from plag_filter.fetch import wayback_calendar_url
from plag_filter.pdf import UnsupportedReportError, page_overlay, render_clean_page_png
from plag_filter.rules import date_evidence, recompute
from plag_filter.types import (
    REASON_LABELS,
    OverlayItem,
    PlagProject,
    PlagReport,
    SourceRow,
    SourceState,
)

# Ключ поточного аркуша в `session_state` — PLAN_PLAG_FILTER_V2.md, §8.6.
_PAGE_KEY = "plag_page"

_MANUAL_VALUES = ("keep", "exclude")

# Причини, за яких експертові пропонується календар Web Archive —
# PLAN_PLAG_FILTER_V2.md, §11 запис 14. `unavailable`: автоперевірка копії не
# дістала, але часто через відмову archive.org, а не через брак знімка.
# `date_unknown`: знімок — саме те свідчення дати, якого забракло.
_ARCHIVE_LINK_REASONS = ("unavailable", "date_unknown")


def _evidence_text(state: SourceState, project: PlagProject) -> str:
    """Свідчення для панелі джерела — PLAN_PLAG_FILTER_V2.md, §9.2 (етапи 2, 5, 6)."""
    check = state.check
    if check is None:
        return ""
    parts: list[str] = []
    if state.reason == "own_work" and check.author_hit is not None:
        parts.append(
            f"Підпис автора: «{check.author_hit.snippet}» (стор. документа {check.author_hit.page})"
        )
    elif state.reason == "cites_author" and check.author_hit is not None:
        parts.append(
            f"Цитування автора: «{check.author_hit.snippet}» (стор. документа {check.author_hit.page})"
        )
    elif state.reason == "unavailable":
        parts.append(f"Помилка: {check.error}")
    else:
        evidence = date_evidence(check, project.year)
        if evidence:
            parts.append(f"Дата документа: {evidence}")
        elif check.date_conflict:
            parts.append("Суперечливі дати в документі")
    if check.archive_used:
        parts.append("Архівна копія")
    if check.url_year_hint is not None:
        parts.append(f"рік в адресі: {check.url_year_hint}")
    return " · ".join(parts)


def _document_url(state: SourceState, url: str) -> str:
    """Адреса документа, який приложение реально отримало — PLAN_PLAG_FILTER_V3.md,
    §7 етап 2. Порожній рядок, якщо перевірки немає, вона з помилкою або
    отримана адреса збігається з вихідною (нема сенсу дублювати посилання)."""
    check = state.check
    if check is None or check.error is not None:
        return ""
    final_url = check.final_url or ""
    if not final_url or final_url == url:
        return ""
    return final_url


def archive_links(state: SourceState, row: SourceRow) -> tuple[str, str]:
    """Копія з архіву й календар знімків для таблиці всіх джерел.

    Повертає `(copy_url, calendar_url)`. Два випадки різні за вагою свідчення
    й тому не зливаються в одне посилання: `copy_url` — сам документ, який
    приложение дістало з архіву (`archive_used`), `calendar_url` — лише перелік
    знімків, коли документа немає або не встановлено дату. Обидва можуть бути
    непорожні водночас: копія є, а дати в ній забракло. Гейти ті самі, що й у
    панелі джерел, — `_document_url` і `_ARCHIVE_LINK_REASONS`.
    """
    url = row.urls[0] if row.urls else ""
    check = state.check
    copy_url = _document_url(state, url) if check is not None and check.archive_used else ""
    calendar_url = (
        wayback_calendar_url(url) if url and state.reason in _ARCHIVE_LINK_REASONS else ""
    )
    return copy_url, calendar_url


def _is_visible(row: SourceRow) -> bool:
    """Джерело ≥ 0,1 % або з нерозпізнаним відсотком — PLAN_PLAG_FILTER.md, §4."""
    return row.percent is None or row.percent >= 0.1


def _visible_numbers_on_page(report: PlagReport, page_index: int) -> list[int]:
    """Видимі номери джерел аркуша (індекс з нуля)."""
    return sorted(
        number
        for number in report.numbers_by_page.get(page_index, ())
        if _is_visible(report.rows[number])
    )


def _pages_with_kept_sources(report: PlagReport, project: PlagProject) -> list[int]:
    """Аркуші, де лишилося хоч одне невиключене видиме джерело (нумерація з 1)."""
    pages: set[int] = set()
    for number, state in project.states.items():
        row = report.rows.get(number)
        if row is None or not _is_visible(row) or state.decision == "exclude":
            continue
        for page_index in report.pages_by_number.get(number, ()):
            pages.add(page_index + 1)
    return sorted(pages)


def _overlay_items(data: bytes, report: PlagReport, page_index: int) -> list[OverlayItem]:
    """Фігури наведення аркуша; поза тілом дисертації їх немає.

    `page_overlay` зіставляє фігури з подіями звіту й тому працює лише на
    аркушах тіла. Титул, зміст і перелік джерел подій не мають: там наведення
    порожнє, а не помилка.
    """
    in_body = report.body_first <= page_index < report.list_first
    try:
        return page_overlay(data, report, page_index)
    except UnsupportedReportError:
        if in_body:
            raise
        return []


def viewer_payload(
    data: bytes,
    report: PlagReport,
    project: PlagProject,
    page: int,
    show_excluded: bool,
) -> dict:
    """Дані одного аркуша для компонента перегляду — §8.5, §9.2 етап 8.

    `page` — номер аркуша PDF з одиниці, як у `session_state["plag_page"]`.
    """
    page = max(1, min(int(page), report.page_count))
    page_index = page - 1

    png = render_clean_page_png(data, report, page_index)
    image = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    overlay = [
        {
            "number": item.number,
            "kind": item.kind,
            "color": item.color,
            "x0": item.x0,
            "y0": item.y0,
            "x1": item.x1,
            "y1": item.y1,
            "excluded": project.states[item.number].decision == "exclude"
            if item.number in project.states
            else False,
        }
        for item in _overlay_items(data, report, page_index)
    ]

    numbers_on_page = report.numbers_by_page.get(page_index, ())
    visible = _visible_numbers_on_page(report, page_index)
    sources = []
    for number in visible:
        row = report.rows[number]
        state = project.states[number]
        url = row.urls[0] if row.urls else ""
        archive_url = (
            wayback_calendar_url(url)
            if url and state.reason in _ARCHIVE_LINK_REASONS
            else ""
        )
        sources.append(
            {
                "number": number,
                "label": row.label,
                "percent_text": row.percent_text or "?",
                "reason_label": REASON_LABELS[state.reason],
                "evidence": _evidence_text(state, project),
                "decision": state.decision,
                "manual": state.manual,
                "url": url,
                "archive_url": archive_url,
                "document_url": _document_url(state, url),
            }
        )

    return {
        "page": page,
        "page_count": report.page_count,
        "image": image,
        "overlay": overlay,
        "sources": sources,
        "below_count": len(numbers_on_page) - len(visible),
        "show_excluded": bool(show_excluded),
        "keep_pages": _pages_with_kept_sources(report, project),
    }


def _apply_decision(project: PlagProject, report: PlagReport, event: dict) -> bool:
    number = event.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        return False
    state = project.states.get(number)
    if state is None:
        return False
    manual = event.get("manual")
    if manual is not None and manual not in _MANUAL_VALUES:
        return False
    state.manual = manual
    recompute(project, report)
    return True


def _apply_page(report: PlagReport, event: dict) -> bool:
    page = event.get("page")
    if not isinstance(page, int) or isinstance(page, bool):
        return False
    page = max(1, min(page, report.page_count))
    event["page"] = page
    if st.session_state.get(_PAGE_KEY) == page:
        return False
    st.session_state[_PAGE_KEY] = page
    return True


def apply_viewer_event(project: PlagProject, report: PlagReport, event: dict) -> bool:
    """Застосувати подію компонента — §8.5, §9.2 етап 8.

    Повертає `True`, якщо щось змінилося: рішення джерела або аркуш.
    Невідомий тип події, невідомий номер і неприпустиме значення нічого не
    змінюють і дають `False`.
    """
    if not isinstance(event, dict):
        return False
    kind = event.get("type")
    if kind == "decision":
        return _apply_decision(project, report, event)
    if kind == "page":
        return _apply_page(report, event)
    return False
