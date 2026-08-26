"""
search/state.py
JSON-схема проєкту режиму пошуку, статуси триажу та атомарне
відновлення стану. Специфікація — PLAN_SEARCH.md, §18.

Крок 3 (§22) реалізує лише сам об'єкт стану картки — `QueryState` та
перевірені переходи статусів §18.1 (без `JSON`-серіалізації, атомарного
імпорту й міграції §18.2/§18.3, які додаються повністю на кроці 13). Стан
триажу свідомо не живе як довільні ключі `st.session_state` (CLAUDE.md,
правило №7): екран тримає `dict[query_id, QueryState]`, той самий контейнер,
який крок 13 лише навчить серіалізувати в JSON-проєкт.
"""

from __future__ import annotations

import dataclasses
from typing import Literal
from urllib.parse import urlparse

QueryStatus = Literal["unchecked", "no_result", "found"]


@dataclasses.dataclass(frozen=True)
class QueryState:
    """Стан триажу однієї картки (§18.1). Мутабельний бізнес-стан UI-сесії."""

    query_id: str
    status: QueryStatus = "unchecked"
    needs_review: bool = False
    previous_status: QueryStatus | None = None
    prior_snapshot: str | None = None
    found_engine: str | None = None
    source_url: str | None = None
    failed_engines: tuple[str, ...] = ()
    comment: str = ""


def initial_state(query_id: str) -> QueryState:
    """Початковий стан щойно побудованого запиту: `unchecked`."""
    return QueryState(query_id=query_id)


def is_absolute_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def mark_unchecked(state: QueryState) -> QueryState:
    """§18.1: скидає статус на `unchecked`, не чіпаючи коментар/логи спроб."""
    return dataclasses.replace(
        state, status="unchecked", found_engine=None, source_url=None
    )


def mark_no_result(state: QueryState, *, comment: str | None = None) -> QueryState:
    """
    §18.1: `no_result` очищає `found_engine`/`source_url`, але не
    `failed_engines` і не коментар (якщо новий коментар не переданий).
    """
    return dataclasses.replace(
        state,
        status="no_result",
        found_engine=None,
        source_url=None,
        comment=state.comment if comment is None else comment,
    )


def mark_found(
    state: QueryState,
    *,
    found_engine: str,
    source_url: str | None = None,
    comment: str | None = None,
) -> QueryState:
    """§18.1: `found` вимагає `found_engine`; `source_url`, якщо є, — абсолютний http(s)."""
    if not found_engine:
        raise ValueError("Статус 'found' вимагає непорожній found_engine.")
    if source_url is not None and not is_absolute_http_url(source_url):
        raise ValueError("source_url має бути абсолютним http/https URL.")
    return dataclasses.replace(
        state,
        status="found",
        found_engine=found_engine,
        source_url=source_url,
        comment=state.comment if comment is None else comment,
    )


def add_failed_engine(state: QueryState, engine_code: str) -> QueryState:
    """Технічна недоступність рушія не є статусом усього запиту (§18.1)."""
    if engine_code in state.failed_engines:
        return state
    return dataclasses.replace(state, failed_engines=state.failed_engines + (engine_code,))


def is_counted_as_checked(state: QueryState) -> bool:
    """§18.1: у метриках перевіреними вважаються лише `no_result`/`found` без `needs_review`."""
    return state.status in ("no_result", "found") and not state.needs_review
