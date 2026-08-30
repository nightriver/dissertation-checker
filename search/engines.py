"""
search/engines.py
Реєстр пошукових рушіїв, побудова URL, ліміти довжини запиту та
протухання посилань. Специфікація — PLAN_SEARCH.md, §16.

`resolve_engine_link` — єдина точка рішення: чи можна показати
предзаповнену кнопку рушія, чи лише посилання на головну сторінку з
копіюваним запитом. Причина відмови (`PrefillBlockReason`) завжди явна —
мовчазного "просто немає кнопки" не буває (§16, §23.24).

Крок 12 (§22) заповнює повну таблицю з семи рушіїв. У шести з них
(усі, крім Google) `query_url_template=None`, `verified_on=None`,
`active_prefill=False` — це свідоме рішення, а не недоробка: §16 вимагає
ручної перевірки кожного prefill-URL перед випуском. На прийманні кроку 16
їхні головні сторінки перевірено, але стабільні prefill-шаблони не підтверджено,
тому fallback лишається свідомим випускним рішенням.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from urllib.parse import quote_plus

from search.types import Channel, EngineSpec

# §16: перевірка старша за 180 днів вважається протухлою.
STALE_AFTER_DAYS = 180

# §16 персональних лімітів рушіїв не задає; готовий query_text (§13) не
# довший за 220 символів. Ліміт єдиний для всіх семи рушіїв і завідомо не
# спрацьовує на реальних даних (рішення оркестратора, крок 12).
_MAX_QUERY_CHARS = 2048

GOOGLE = EngineSpec(
    code="google",
    label="Google",
    channels=frozenset({Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L}),
    home_url="https://www.google.com/",
    query_url_template="https://www.google.com/search?q={query}",
    max_query_chars=_MAX_QUERY_CHARS,
    warning=None,
    verified_on=date(2026, 8, 30),
    active_prefill=True,
)

GOOGLE_SCHOLAR = EngineSpec(
    code="google_scholar",
    label="Google Scholar",
    channels=frozenset({Channel.A, Channel.N, Channel.B, Channel.T, Channel.L}),
    home_url="https://scholar.google.com/",
    query_url_template=None,
    max_query_chars=_MAX_QUERY_CHARS,
    warning=None,
    verified_on=None,
    active_prefill=False,
)

YANDEX = EngineSpec(
    code="yandex",
    label="Яндекс",
    channels=frozenset({Channel.K}),
    home_url="https://yandex.ru/",
    query_url_template=None,
    max_query_chars=_MAX_QUERY_CHARS,
    warning="може бути недоступний з поточної мережі; за потреби VPN",
    verified_on=None,
    active_prefill=False,
)

CYBERLENINKA = EngineSpec(
    code="cyberleninka",
    label="КіберЛенінка",
    channels=frozenset({Channel.K}),
    home_url="https://cyberleninka.ru/",
    query_url_template=None,
    max_query_chars=_MAX_QUERY_CHARS,
    warning="може бути недоступна з поточної мережі; за потреби VPN",
    verified_on=None,
    active_prefill=False,
)

ELIBRARY = EngineSpec(
    code="elibrary",
    label="eLibrary",
    channels=frozenset({Channel.K}),
    home_url="https://elibrary.ru/",
    query_url_template=None,
    max_query_chars=_MAX_QUERY_CHARS,
    warning="може бути недоступний з поточної мережі; за потреби VPN",
    verified_on=None,
    active_prefill=False,
)

DISSERCAT = EngineSpec(
    code="dissercat",
    label="disserCat",
    channels=frozenset({Channel.K}),
    home_url="https://www.dissercat.com/",
    query_url_template=None,
    max_query_chars=_MAX_QUERY_CHARS,
    warning="може бути недоступний з поточної мережі; за потреби VPN",
    verified_on=None,
    active_prefill=False,
)

NRAT = EngineSpec(
    code="nrat",
    label="НРАТ",
    channels=frozenset({Channel.A, Channel.N, Channel.B, Channel.T, Channel.L}),
    home_url="https://nrat.ukrintei.ua/",
    query_url_template=None,
    max_query_chars=_MAX_QUERY_CHARS,
    warning=None,
    verified_on=None,
    active_prefill=False,
)

# Повний реєстр §16, у фіксованому порядку таблиці.
ENGINES: tuple[EngineSpec, ...] = (
    GOOGLE,
    GOOGLE_SCHOLAR,
    YANDEX,
    CYBERLENINKA,
    ELIBRARY,
    DISSERCAT,
    NRAT,
)


class PrefillBlockReason(Enum):
    NOT_VERIFIED = "not_verified"
    STALE_VERIFICATION = "stale_verification"
    NO_TEMPLATE = "no_template"
    QUERY_TOO_LONG = "query_too_long"
    PREFILL_DISABLED = "prefill_disabled"
    EMPTY_QUERY = "empty_query"


@dataclass(frozen=True)
class EngineLink:
    engine_code: str
    url: str  # prefill-URL або home_url
    is_prefilled: bool
    block_reason: PrefillBlockReason | None  # None рівно тоді, коли is_prefilled
    warning: str | None  # з EngineSpec.warning, без змін
    query_text: str  # завжди копіюваний вихідний текст


def is_engine_verification_stale(spec: EngineSpec, today: date) -> bool:
    """§16: перевірка, старша за 180 днів, вважається протухлою."""
    if spec.verified_on is None:
        return True
    return (today - spec.verified_on).days > STALE_AFTER_DAYS


def resolve_engine_link(spec: EngineSpec, query_text: str, *, today: date) -> EngineLink:
    """
    Єдина точка рішення: prefill-посилання чи fallback на `home_url`.

    Порядок перевірки причин фіксований (§16, крок 12):
    EMPTY_QUERY -> PREFILL_DISABLED -> NO_TEMPLATE -> NOT_VERIFIED ->
    STALE_VERIFICATION -> QUERY_TOO_LONG. Перша причина, що спрацювала,
    потрапляє в `block_reason`. `query_text` повертається без обрізки і
    без URL-кодування — карточка показує його `st.code`.
    """
    block_reason: PrefillBlockReason | None = None

    if query_text.strip() == "":
        block_reason = PrefillBlockReason.EMPTY_QUERY
    elif not spec.active_prefill:
        block_reason = PrefillBlockReason.PREFILL_DISABLED
    elif spec.query_url_template is None:
        block_reason = PrefillBlockReason.NO_TEMPLATE
    elif spec.verified_on is None:
        block_reason = PrefillBlockReason.NOT_VERIFIED
    elif is_engine_verification_stale(spec, today):
        block_reason = PrefillBlockReason.STALE_VERIFICATION
    elif len(query_text) > spec.max_query_chars:
        block_reason = PrefillBlockReason.QUERY_TOO_LONG

    if block_reason is None:
        url = spec.query_url_template.format(query=quote_plus(query_text))
        if not url.startswith("https://"):
            block_reason = PrefillBlockReason.NO_TEMPLATE

    if block_reason is None:
        return EngineLink(
            engine_code=spec.code,
            url=url,
            is_prefilled=True,
            block_reason=None,
            warning=spec.warning,
            query_text=query_text,
        )

    return EngineLink(
        engine_code=spec.code,
        url=spec.home_url,
        is_prefilled=False,
        block_reason=block_reason,
        warning=spec.warning,
        query_text=query_text,
    )


def build_engine_url(spec: EngineSpec, query_text: str, today: date) -> str | None:
    """
    Повертає prefill-URL рушія або `None`, якщо запит не можна безпечно
    попередньо заповнити. Тонка обгортка над `resolve_engine_link` —
    зберігається заради сумісності з тонким зрізом кроку 3.
    """
    link = resolve_engine_link(spec, query_text, today=today)
    return link.url if link.is_prefilled else None


def engines_for_channel(channel: Channel) -> tuple[EngineSpec, ...]:
    """Рушії, у чиїх `channels` є `channel`, у порядку `ENGINES`."""
    return tuple(spec for spec in ENGINES if channel in spec.channels)


def engine_by_code(code: str) -> EngineSpec:
    """Специфікація рушія за кодом; `KeyError` на невідомий код."""
    for spec in ENGINES:
        if spec.code == code:
            return spec
    raise KeyError(code)
