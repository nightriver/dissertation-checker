"""
search/engines.py
Реєстр пошукових рушіїв, побудова URL, ліміти довжини запиту та
протухання посилань. Специфікація — PLAN_SEARCH.md, §16.

Крок 3 (§22) реєструє лише Google (перевірений `active_prefill`), щоб
тонкий зріз мав робочу кнопку рушія для одного запиту каналу A. Решта
таблиці §16 (Google Scholar, Яндекс, КиберЛенінка, eLibrary, disserCat,
НРАТ) з попередженнями про VPN і K-специфічними рушіями — крок 12.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from search.types import Channel, EngineSpec

# §16: перевірка старша за 180 днів вважається протухлою.
STALE_AFTER_DAYS = 180

GOOGLE = EngineSpec(
    code="google",
    label="Google",
    channels=frozenset({Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L}),
    home_url="https://www.google.com/",
    query_url_template="https://www.google.com/search?q={query}",
    max_query_chars=2048,
    warning=None,
    verified_on=date(2026, 8, 25),
    active_prefill=True,
)

# Реєстр рушіїв тонкого зрізу; повна таблиця §16 — крок 12.
ENGINES: tuple[EngineSpec, ...] = (GOOGLE,)


def is_engine_verification_stale(spec: EngineSpec, today: date) -> bool:
    """§16: перевірка, старша за 180 днів, вважається протухлою."""
    if spec.verified_on is None:
        return True
    return (today - spec.verified_on).days > STALE_AFTER_DAYS


def build_engine_url(spec: EngineSpec, query_text: str, today: date) -> str | None:
    """
    Повертає prefill-URL рушія або `None`, якщо запит не можна безпечно
    попередньо заповнити (рушій не перевірений, протух, шаблону немає, або
    довжина запиту в Unicode-символах перевищує `max_query_chars`).
    У всіх цих випадках екран показує лише `spec.home_url` і копіюваний
    `query_text` (§16).
    """
    if not spec.active_prefill or spec.query_url_template is None:
        return None
    if is_engine_verification_stale(spec, today):
        return None
    if len(query_text) > spec.max_query_chars:
        return None
    url = spec.query_url_template.format(query=quote_plus(query_text))
    if not url.startswith("https://"):
        return None
    return url
