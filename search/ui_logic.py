"""
search/ui_logic.py
Чиста логіка маршруту та екранного стану режиму пошуку джерел, що з'єднує
presentation, state та parser/search-модулі. Специфікація — PLAN_SEARCH.md,
§§17–19. `app.py` отримує готові секції, картки та зведення без прихованих
обчислень у Streamlit-шарі.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from parser.searchdoc import parse_search_document
from search.presentation import QueryCardView, SearchSummaryView, build_query_card, build_search_summary
from search.query_builder import build_search_result
from search.state import (
    QueryState,
    add_failed_engine,
    initial_state,
    mark_found,
    mark_no_result,
    mark_unchecked,
)
from search.types import CONTENT_SECTION_KINDS, EngineSpec, SearchResult, SectionShortfall


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


def run_search_pipeline(pdf_bytes: bytes) -> SearchResult:
    """Байти PDF → `SearchResult` (§22, крок 3): `searchdoc` → маркери → запити."""
    document = parse_search_document(pdf_bytes)
    return build_search_result(document)


def build_initial_query_states(result: SearchResult) -> dict[str, QueryState]:
    """Початковий стан триажу — `unchecked` для кожного запиту."""
    return {query.query_id: initial_state(query.query_id) for query in result.queries}


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
    else:
        raise ValueError(f"Невідома дія статусу: {action!r}")

    new_states = dict(states)
    new_states[query_id] = updated
    return new_states
