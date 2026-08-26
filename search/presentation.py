"""
search/presentation.py
Чисте форматування карток кандидатів і зведень для екрана
?mode=search, без звернень до Streamlit. Специфікація — PLAN_SEARCH.md, §17.

Крок 3 (§22) реалізує лише мінімальний вміст картки, потрібний для
smoke-тесту UI: канал, текст запиту, сторінка, донор, якір, статус і
кнопки перевірених рушіїв. Повний макет картки §17 (причина RU-посилання,
ознаки перекладу, «ще N зачіпок», HTML-екранування за символьними
інтервалами) — крок 14.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from search.engines import build_engine_url
from search.state import QueryState
from search.types import EngineSpec, SearchQuery

STATUS_LABELS: dict[str, str] = {
    "unchecked": "не перевірено",
    "no_result": "нічого не знайдено",
    "found": "знайдено",
}


@dataclass(frozen=True)
class EngineLinkView:
    label: str
    url: str | None
    home_url: str


@dataclass(frozen=True)
class QueryCardView:
    query_id: str
    channel_label: str
    query_text: str
    page_label: str
    donor_text: str
    anchor_text: str
    status_label: str
    engine_links: tuple[EngineLinkView, ...]


def build_query_card(
    query: SearchQuery, state: QueryState, engines: tuple[EngineSpec, ...], today: date
) -> QueryCardView:
    """Чиста похідна від `SearchQuery` + `QueryState` — без звернень до Streamlit."""
    engine_links = tuple(
        EngineLinkView(
            label=engine.label,
            url=build_engine_url(engine, query.query_text, today),
            home_url=engine.home_url,
        )
        for engine in engines
        if query.primary_channel in engine.channels
    )
    return QueryCardView(
        query_id=query.query_id,
        channel_label=f"[{query.primary_channel.value}]",
        query_text=query.query_text,
        page_label=f"Аркуш PDF {query.physical_page}",
        donor_text=query.donor_text,
        anchor_text=query.pdf_anchor,
        status_label=STATUS_LABELS[state.status],
        engine_links=engine_links,
    )
