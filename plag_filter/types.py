"""Типи даних режиму очищення звіту Plag.

Контракт узятий з `PLAN_PLAG_FILTER.md`, §9, доповнений
`PLAN_PLAG_FILTER_V2.md`, §8.1. Поля та їхній сенс не змінюються без правки
плану.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Decision = Literal["keep", "exclude", "disputed"]
Reason = Literal[
    "manual_keep",
    "manual_exclude",
    "below_threshold",
    "unchecked",
    "unconfirmed",
    "own_work",
    "cites_author",
    "unavailable",
    "date_conflict",
    "date_unknown",
    "later",
    "earlier",
    "same_year",
]

# Підписи причин для інтерфейсу — PLAN_PLAG_FILTER.md, §4, доповнено
# PLAN_PLAG_FILTER_V2.md, §8.1.
REASON_LABELS: dict[Reason, str] = {
    "manual_keep": "Залишено вручну",
    "manual_exclude": "Виключено вручну",
    "below_threshold": "Нижче 0,1 %",
    "unchecked": "Ще не перевірено",
    "unconfirmed": "Автора і рік не підтверджено",
    "own_work": "Власна робота",
    "cites_author": "Цитує автора",
    "unavailable": "Документ недоступний — перевірити",
    "date_conflict": "Суперечливі дати — перевірити",
    "date_unknown": "Дату не встановлено — перевірити",
    "later": "Пізніша за дисертацію",
    "earlier": "Раніша за дисертацію",
    "same_year": "Той самий рік — перевірити",
}


@dataclass(frozen=True)
class SourceRow:
    """Рядок переліку джерел звіту Plag."""

    number: int
    percent: float | None
    percent_text: str
    label: str
    urls: tuple[str, ...]
    list_page: int
    band: tuple[float, float]
    row_cuts: tuple[tuple[int, int], ...]
    link_rects: tuple[tuple[float, float, float, float], ...]
    source_id: str


@dataclass(frozen=True)
class BodyEvent:
    """Подія в тілі дисертації: маркер номера або підсвічування збігу."""

    page: int
    number: int
    kind: Literal["marker", "highlight"]
    cuts: tuple[tuple[int, int], ...]
    width: float


@dataclass(frozen=True)
class PlagReport:
    """Розібраний звіт Plag: перелік джерел і події в тілі."""

    sha256: str
    page_count: int
    body_first: int
    list_first: int
    rows: dict[int, SourceRow]
    events: tuple[BodyEvent, ...]
    pages_by_number: dict[int, tuple[int, ...]]
    numbers_by_page: dict[int, tuple[int, ...]]
    highlight_width: dict[int, float]
    longest_run: dict[int, float]
    title_text: str


@dataclass(frozen=True)
class DateInterval:
    """Інтервал дати документа з точністю, з якою вона встановлена."""

    start: date
    end: date
    precision: Literal["year", "month", "day"]


@dataclass(frozen=True)
class AuthorHit:
    """Знайдений збіг прізвища та ініціалів автора в тексті документа.

    `kind` — PLAN_PLAG_FILTER_V2.md, §8.1, §9.2 етап 2: `byline` — підпис
    автора (документ належить автору), `mention` — автора лише цитують.
    """

    page: int
    snippet: str
    kind: Literal["byline", "mention"] = "byline"


@dataclass(frozen=True)
class AuthorGuess:
    """Автор, розпізнаний із тексту титулу — PLAN_PLAG_FILTER_V2.md, §8.1."""

    surname: str
    given_name: str
    patronymic: str
    initials: str
    confidence: Literal["confirmed", "single"]


@dataclass
class SourceCheck:
    """Результат перевірки одного джерела за адресою."""

    checked_for: str
    url: str
    final_url: str | None
    error: str | None
    author_hit: AuthorHit | None
    doc_date: DateInterval | None
    date_basis: str | None
    date_conflict: bool
    url_year_hint: int | None
    hints: dict[str, str]
    citation_years: list[int] = field(default_factory=list)


@dataclass
class SourceState:
    """Стан одного номера джерела в проєкті: перевірка, рішення, причина."""

    number: int
    source_id: str
    check: SourceCheck | None
    manual: Literal["keep", "exclude"] | None
    alt_url: str | None
    decision: Decision
    reason: Reason


@dataclass
class PlagProject:
    """Проєкт очищення одного звіту Plag."""

    schema_version: int
    report_sha256: str
    report_name: str
    surname: str
    initials: str
    year: int | None
    confirmed: bool
    given_name: str = ""
    patronymic: str = ""
    states: dict[int, SourceState] = field(default_factory=dict)
