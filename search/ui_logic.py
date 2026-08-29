"""
search/ui_logic.py
Чиста логіка маршруту та екранного стану режиму пошуку джерел, що з'єднує
presentation, state та parser/search-модулі. Специфікація — PLAN_SEARCH.md,
§§17–19. `app.py` отримує готові секції, картки та зведення без прихованих
обчислень у Streamlit-шарі.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date

from parser.searchdoc import parse_search_document
from search.presentation import QueryCardView, SearchSummaryView, build_query_card, build_search_summary
from search.query_builder import build_search_result
from search.state import (
    ImportResult,
    QueryState,
    UnmatchedRecord,
    add_failed_engine,
    apply_project,
    export_project,
    initial_state,
    mark_found,
    mark_no_result,
    mark_unchecked,
)
from search.types import (
    CONTENT_SECTION_KINDS,
    EngineSpec,
    SearchResult,
    SectionOverride,
    SectionShortfall,
)


DEFAULT_VISIBLE_PER_SECTION = 5


@dataclass(frozen=True)
class SectionCardsView:
    section_id: str
    heading: str
    kind: str
    visible_cards: tuple[QueryCardView, ...]
    hidden_cards: tuple[QueryCardView, ...]
    hidden_count: int
    shortfall: SectionShortfall | None


@dataclass(frozen=True)
class SearchScreenView:
    summary: SearchSummaryView
    sections: tuple[SectionCardsView, ...]


def run_search_pipeline(
    pdf_bytes: bytes, overrides: tuple[SectionOverride, ...] = ()
) -> SearchResult:
    """Байти PDF та виправлення карти → повністю перерахований `SearchResult`."""
    document = parse_search_document(pdf_bytes, overrides=overrides)
    return build_search_result(document)


def build_initial_query_states(result: SearchResult) -> dict[str, QueryState]:
    """Початковий стан триажу — `unchecked` для кожного запиту."""
    return {query.query_id: initial_state(query.query_id) for query in result.queries}


def _complete_states(result: SearchResult, states: dict[str, QueryState]) -> dict[str, QueryState]:
    return {
        query.query_id: states.get(query.query_id, initial_state(query.query_id))
        for query in result.queries
    }


def _carry_unmatched(payload: dict, unmatched: tuple[UnmatchedRecord, ...]) -> dict:
    carried = dict(payload)
    carried["unmatched"] = [
        {
            "query_id": item.query_id,
            "donor_id": item.donor_id,
            "payload": item.payload,
        }
        for item in unmatched
    ]
    return carried


def serialize_search_project(
    result: SearchResult,
    states: dict[str, QueryState],
    *,
    app_version: str,
    file_name: str,
    unmatched: tuple[UnmatchedRecord, ...] = (),
) -> bytes:
    """Серіалізувати переносний JSON-проєкт UTF-8 без втрати unmatched (§18)."""

    payload = export_project(
        document=result.document,
        result=result,
        states=states,
        app_version=app_version,
        file_name=file_name,
    )
    payload = _carry_unmatched(payload, unmatched)
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def rebuild_search_pipeline(
    pdf_bytes: bytes,
    overrides: tuple[SectionOverride, ...],
    previous_result: SearchResult,
    previous_states: dict[str, QueryState],
    *,
    app_version: str,
    file_name: str,
    unmatched: tuple[UnmatchedRecord, ...] = (),
) -> tuple[SearchResult, dict[str, QueryState], ImportResult]:
    """Перерахувати карту та перенести статуси за стабільними ID (§18.2)."""

    payload = export_project(
        document=previous_result.document,
        result=previous_result,
        states=previous_states,
        app_version=app_version,
        file_name=file_name,
    )
    payload = _carry_unmatched(payload, unmatched)
    result = run_search_pipeline(pdf_bytes, overrides)
    imported = apply_project(payload, document=result.document, queries=result.queries)
    return result, _complete_states(result, imported.states), imported


def import_search_project(
    pdf_bytes: bytes,
    payload: dict,
    current_result: SearchResult,
) -> tuple[SearchResult, dict[str, QueryState], ImportResult]:
    """Атомарно: допуск JSON → overrides → перерахунок → зіставлення (§18.3)."""

    preliminary = apply_project(
        payload,
        document=current_result.document,
        queries=current_result.queries,
    )
    result = run_search_pipeline(pdf_bytes, preliminary.section_overrides)
    imported = apply_project(payload, document=result.document, queries=result.queries)
    return result, _complete_states(result, imported.states), imported


def build_search_screen(
    result: SearchResult,
    states: dict[str, QueryState],
    engines: tuple[EngineSpec, ...],
    today: date,
    *,
    visible_limit: int = DEFAULT_VISIBLE_PER_SECTION,
) -> SearchScreenView:
    """Побудувати секції «перші N + решта» та загальне зведення (§17)."""

    if visible_limit < 0:
        raise ValueError("visible_limit не може бути від'ємним.")
    shortfalls = {item.section_id: item for item in result.shortfalls}
    section_views: list[SectionCardsView] = []
    for section in result.document.sections:
        if section.kind not in CONTENT_SECTION_KINDS:
            continue
        queries = tuple(query for query in result.queries if query.section_id == section.section_id)
        cards = tuple(
            build_query_card(
                query,
                states.get(query.query_id, initial_state(query.query_id)),
                engines,
                today,
                document=result.document,
            )
            for query in queries
        )
        visible = cards[:visible_limit]
        hidden = cards[visible_limit:]
        section_views.append(SectionCardsView(
            section_id=section.section_id,
            heading=section.heading,
            kind=section.kind.value,
            visible_cards=visible,
            hidden_cards=hidden,
            hidden_count=len(hidden),
            shortfall=shortfalls.get(section.section_id),
        ))
    return SearchScreenView(
        summary=build_search_summary(result, states, engines),
        sections=tuple(section_views),
    )


def apply_status_action(
    states: dict[str, QueryState],
    query_id: str,
    action: str,
    *,
    found_engine: str | None = None,
    failed_engine: str | None = None,
    source_url: str | None = None,
    comment: str | None = None,
) -> dict[str, QueryState]:
    """
    Чистий перехід статусу однієї картки (§18.1). Повертає НОВИЙ словник —
    вхідний не мутується, узгоджено з тим, що триаж не живе довільними
    ключами `st.session_state` (CLAUDE.md, правило №7): екран тримає єдиний
    `dict[query_id, QueryState]`, який крок 13 навчить серіалізувати в JSON.
    """
    current = states.get(query_id)
    if current is None:
        raise KeyError(f"Немає стану для query_id={query_id!r}")

    if action == "unchecked":
        updated = mark_unchecked(current)
    elif action == "no_result":
        updated = mark_no_result(current, comment=comment)
    elif action == "found":
        if not found_engine:
            raise ValueError("Статус 'found' вимагає found_engine.")
        updated = mark_found(current, found_engine=found_engine, source_url=source_url, comment=comment)
    elif action == "failed_engine":
        if not failed_engine:
            raise ValueError("Дія 'failed_engine' вимагає код рушія.")
        updated = add_failed_engine(current, failed_engine)
    elif action == "comment":
        updated = replace(current, comment=comment or "")
    else:
        raise ValueError(f"Невідома дія статусу: {action!r}")

    new_states = dict(states)
    new_states[query_id] = updated
    return new_states
