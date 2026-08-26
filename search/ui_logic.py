"""
search/ui_logic.py
Чиста логіка маршруту та екранного стану режиму пошуку джерел, що
з'єднує presentation, state та parser/search-модулі. Специфікація —
PLAN_SEARCH.md, §17.

Крок 3 (§22) реалізує лише наскрізний виклик конвеєра (байти PDF →
`SearchResult`) і чисті переходи статусу картки. Карта розділів з
виправленнями, лічильники K, аккордеони по розділах — крок 14/15.
"""

from __future__ import annotations

from parser.searchdoc import parse_search_document
from search.query_builder import build_search_result
from search.state import QueryState, initial_state, mark_found, mark_no_result, mark_unchecked
from search.types import SearchResult


def run_search_pipeline(pdf_bytes: bytes) -> SearchResult:
    """Байти PDF → `SearchResult` (§22, крок 3): `searchdoc` → маркери → запити."""
    document = parse_search_document(pdf_bytes)
    return build_search_result(document)


def build_initial_query_states(result: SearchResult) -> dict[str, QueryState]:
    """Початковий стан триажу — `unchecked` для кожного запиту."""
    return {query.query_id: initial_state(query.query_id) for query in result.queries}


def apply_status_action(
    states: dict[str, QueryState],
    query_id: str,
    action: str,
    *,
    found_engine: str | None = None,
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
    else:
        raise ValueError(f"Невідома дія статусу: {action!r}")

    new_states = dict(states)
    new_states[query_id] = updated
    return new_states
