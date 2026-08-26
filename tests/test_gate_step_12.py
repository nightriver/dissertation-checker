"""
Шлюз кроку 12 — `search/engines.py`: повна таблиця з 7 рушіїв, `EngineLink`,
`resolve_engine_link`, fallback і протухання (`steps/step-12.md`).

Пишеться незалежно від реалізації, лише за контрактом пакета. Скрізь, де
задіяна актуальність, `today` передається явно в тест — календар не
читається ніде.

Нумерація тестів відповідає пунктам розділу «Шлюз» пакета `steps/step-12.md`.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from search.engines import (
    ENGINES,
    STALE_AFTER_DAYS,
    EngineLink,
    PrefillBlockReason,
    build_engine_url,
    engine_by_code,
    engines_for_channel,
    is_engine_verification_stale,
    resolve_engine_link,
)
from search.types import Channel, EngineSpec

# Таблиця §16, дослівно з пакета кроку.
EXPECTED_TABLE: tuple[tuple[str, str, frozenset[Channel], str], ...] = (
    ("google", "Google", frozenset({Channel.A, Channel.N, Channel.B, Channel.K, Channel.T, Channel.L}),
     "https://www.google.com/"),
    ("google_scholar", "Google Scholar", frozenset({Channel.A, Channel.N, Channel.B, Channel.T, Channel.L}),
     "https://scholar.google.com/"),
    ("yandex", "Яндекс", frozenset({Channel.K}), "https://yandex.ru/"),
    ("cyberleninka", "КіберЛенінка", frozenset({Channel.K}), "https://cyberleninka.ru/"),
    ("elibrary", "eLibrary", frozenset({Channel.K}), "https://elibrary.ru/"),
    ("dissercat", "disserCat", frozenset({Channel.K}), "https://www.dissercat.com/"),
    ("nrat", "НРАТ", frozenset({Channel.A, Channel.N, Channel.B, Channel.T, Channel.L}),
     "https://nrat.ukrintei.ua/"),
)

EXPECTED_WARNINGS: dict[str, str | None] = {
    "google": None,
    "google_scholar": None,
    "yandex": "може бути недоступний з поточної мережі; за потреби VPN",
    "cyberleninka": "може бути недоступна з поточної мережі; за потреби VPN",
    "elibrary": "може бути недоступний з поточної мережі; за потреби VPN",
    "dissercat": "може бути недоступний з поточної мережі; за потреби VPN",
    "nrat": None,
}

GOOGLE_VERIFIED_ON = date(2026, 8, 25)
# Свіжий "сьогодні" відносно ручної перевірки Google — 7 днів, точно не протухло.
TODAY_FRESH = date(2026, 9, 1)


def _synthetic_spec(**overrides) -> EngineSpec:
    """Синтетичний EngineSpec з валідними значеннями за замовчуванням."""
    defaults = dict(
        code="synthetic",
        label="Synthetic",
        channels=frozenset({Channel.A}),
        home_url="https://example.com/",
        query_url_template="https://example.com/search?q={query}",
        max_query_chars=2048,
        warning=None,
        verified_on=TODAY_FRESH,
        active_prefill=True,
    )
    defaults.update(overrides)
    return EngineSpec(**defaults)


# ---------------------------------------------------------------------------
# 1. Реєстр — 7 записів, той самий порядок, унікальні коди.
# ---------------------------------------------------------------------------


def test_engines_registry_has_seven_entries_in_expected_order():
    """Псує: у пакеті коди йдуть у фіксованому порядку — перевіряємо його дослівно."""
    codes = tuple(spec.code for spec in ENGINES)
    expected_codes = tuple(row[0] for row in EXPECTED_TABLE)
    assert codes == expected_codes
    assert len(codes) == 7
    assert len(set(codes)) == 7


# ---------------------------------------------------------------------------
# 2. Канали кожного рушія — frozenset, точно за таблицею.
# ---------------------------------------------------------------------------


def test_engine_channels_match_table():
    by_code = {spec.code: spec for spec in ENGINES}
    for code, _label, channels, _home in EXPECTED_TABLE:
        assert by_code[code].channels == channels, code


# ---------------------------------------------------------------------------
# 3. active_prefill=True рівно у google; решта без дати/шаблону.
# ---------------------------------------------------------------------------


def test_active_prefill_only_google():
    by_code = {spec.code: spec for spec in ENGINES}
    assert by_code["google"].active_prefill is True
    for code, _label, _channels, _home in EXPECTED_TABLE:
        if code == "google":
            continue
        spec = by_code[code]
        assert spec.active_prefill is False, code
        assert spec.verified_on is None, code
        assert spec.query_url_template is None, code


# ---------------------------------------------------------------------------
# 4. Інваріант §16: active_prefill=True => verified_on і query_url_template непорожні.
# ---------------------------------------------------------------------------


def test_active_prefill_invariant_requires_verification_and_template():
    for spec in ENGINES:
        if spec.active_prefill:
            assert spec.verified_on is not None, spec.code
            assert spec.query_url_template is not None, spec.code


# ---------------------------------------------------------------------------
# 5. Усі home_url — https.
# ---------------------------------------------------------------------------


def test_all_home_urls_use_https_scheme():
    for spec in ENGINES:
        assert spec.home_url.startswith("https://"), spec.code


# ---------------------------------------------------------------------------
# 6. Попередження дослівно, включно з None у трьох рушіїв.
# ---------------------------------------------------------------------------


def test_warnings_match_table_verbatim():
    by_code = {spec.code: spec for spec in ENGINES}
    for code, expected_warning in EXPECTED_WARNINGS.items():
        assert by_code[code].warning == expected_warning, code


# ---------------------------------------------------------------------------
# 7. Google при свіжому today — prefilled, кодування кирилиці/лапок/пробілів.
# ---------------------------------------------------------------------------


def test_resolve_engine_link_google_prefilled_encodes_cyrillic_and_quotes():
    google = engine_by_code("google")
    query = 'Штучний інтелект "нейронна мережа"'
    link = resolve_engine_link(google, query, today=TODAY_FRESH)
    assert link.is_prefilled is True
    assert link.block_reason is None
    assert link.url.startswith("https://www.google.com/search?q=")
    # Псує: якщо кодування зламане (сирі символи потрапляють у URL) — впаде тут.
    assert '"' not in link.url
    assert " " not in link.url
    assert link.url.isascii()


# ---------------------------------------------------------------------------
# 8. Неперевірений рушій (nrat) — fallback з PREFILL_DISABLED.
# ---------------------------------------------------------------------------


def test_resolve_engine_link_unverified_engine_falls_back():
    nrat = engine_by_code("nrat")
    link = resolve_engine_link(nrat, "запит", today=TODAY_FRESH)
    assert link.is_prefilled is False
    assert link.url == nrat.home_url
    assert link.block_reason == PrefillBlockReason.PREFILL_DISABLED


# ---------------------------------------------------------------------------
# 9. Протухання на межі 180 днів.
# ---------------------------------------------------------------------------


def test_stale_verification_boundary_at_180_days():
    stale_spec = _synthetic_spec(verified_on=TODAY_FRESH - timedelta(days=181))
    stale_link = resolve_engine_link(stale_spec, "запит", today=TODAY_FRESH)
    assert stale_link.block_reason == PrefillBlockReason.STALE_VERIFICATION
    assert stale_link.url == stale_spec.home_url

    boundary_spec = _synthetic_spec(verified_on=TODAY_FRESH - timedelta(days=180))
    boundary_link = resolve_engine_link(boundary_spec, "запит", today=TODAY_FRESH)
    assert boundary_link.is_prefilled is True
    assert boundary_link.block_reason is None
    # Псує: перевіряємо і низькорівневу функцію протухання окремо.
    assert is_engine_verification_stale(stale_spec, TODAY_FRESH) is True
    assert is_engine_verification_stale(boundary_spec, TODAY_FRESH) is False


# ---------------------------------------------------------------------------
# 10. Ліміт довжини: 2049 символів падає, 2048 проходить, без обрізання.
# ---------------------------------------------------------------------------


def test_query_length_limit_boundary_at_2048_chars():
    google = engine_by_code("google")
    too_long = "a" * 2049
    link_too_long = resolve_engine_link(google, too_long, today=TODAY_FRESH)
    assert link_too_long.block_reason == PrefillBlockReason.QUERY_TOO_LONG
    assert link_too_long.url == google.home_url
    # Псує: якщо запит обрізається — довжина query_text зміниться.
    assert link_too_long.query_text == too_long
    assert len(link_too_long.query_text) == 2049

    exactly_limit = "a" * 2048
    link_ok = resolve_engine_link(google, exactly_limit, today=TODAY_FRESH)
    assert link_ok.is_prefilled is True
    assert link_ok.block_reason is None


# ---------------------------------------------------------------------------
# 11. Довжина рахується до URL-кодування.
# ---------------------------------------------------------------------------


def test_length_is_counted_before_url_encoding():
    google = engine_by_code("google")
    cyrillic_query = "а" * 2048  # кирилична 'а', після quote_plus займе набагато більше символів
    link = resolve_engine_link(google, cyrillic_query, today=TODAY_FRESH)
    assert link.is_prefilled is True
    assert link.block_reason is None
    # Псує: якщо рахувати довжину після кодування — цей запит мав би не пройти.
    from urllib.parse import quote_plus

    assert len(quote_plus(cyrillic_query)) > google.max_query_chars


# ---------------------------------------------------------------------------
# 12. Фіксований порядок причин відмови.
# ---------------------------------------------------------------------------


def test_block_reason_priority_order_on_synthetic_engines():
    # a) active_prefill=False разом із простроченою датою -> PREFILL_DISABLED
    #    (перевірка active_prefill йде раніше за перевірку дати).
    disabled_and_stale = _synthetic_spec(
        active_prefill=False,
        verified_on=TODAY_FRESH - timedelta(days=500),
    )
    link_a = resolve_engine_link(disabled_and_stale, "запит", today=TODAY_FRESH)
    assert link_a.block_reason == PrefillBlockReason.PREFILL_DISABLED

    # b) active_prefill=True, шаблону немає -> NO_TEMPLATE.
    no_template = _synthetic_spec(active_prefill=True, query_url_template=None)
    link_b = resolve_engine_link(no_template, "запит", today=TODAY_FRESH)
    assert link_b.block_reason == PrefillBlockReason.NO_TEMPLATE

    # c) active_prefill=True, шаблон є, verified_on=None -> NOT_VERIFIED.
    not_verified = _synthetic_spec(active_prefill=True, verified_on=None)
    link_c = resolve_engine_link(not_verified, "запит", today=TODAY_FRESH)
    assert link_c.block_reason == PrefillBlockReason.NOT_VERIFIED


# ---------------------------------------------------------------------------
# 13. Схема не-https -> fallback з NO_TEMPLATE.
# ---------------------------------------------------------------------------


def test_non_https_template_falls_back_to_no_template():
    http_spec = _synthetic_spec(query_url_template="http://example.com/search?q={query}")
    link = resolve_engine_link(http_spec, "запит", today=TODAY_FRESH)
    assert link.is_prefilled is False
    assert link.block_reason == PrefillBlockReason.NO_TEMPLATE
    assert link.url == http_spec.home_url


# ---------------------------------------------------------------------------
# 14. query_text завжди присутній, і у prefilled-рушія теж.
# ---------------------------------------------------------------------------


def test_query_text_is_always_present_in_engine_link():
    nrat = engine_by_code("nrat")
    google = engine_by_code("google")
    query = "стійкий запит для перевірки"
    fallback_link = resolve_engine_link(nrat, query, today=TODAY_FRESH)
    prefilled_link = resolve_engine_link(google, query, today=TODAY_FRESH)
    assert fallback_link.query_text == query
    assert prefilled_link.query_text == query


# ---------------------------------------------------------------------------
# 15. engines_for_channel — канал K і порожній канал D.
# ---------------------------------------------------------------------------


def test_engines_for_channel_matches_table():
    k_codes = tuple(spec.code for spec in engines_for_channel(Channel.K))
    assert k_codes == ("google", "yandex", "cyberleninka", "elibrary", "dissercat")
    assert engines_for_channel(Channel.D) == ()


# ---------------------------------------------------------------------------
# 16. engine_by_code — успіх і KeyError.
# ---------------------------------------------------------------------------


def test_engine_by_code_lookup_and_keyerror_on_unknown():
    spec = engine_by_code("google")
    assert spec.label == "Google"
    with pytest.raises(KeyError):
        engine_by_code("no_such")


# ---------------------------------------------------------------------------
# 17. Зворотна сумісність build_engine_url з resolve_engine_link.
# ---------------------------------------------------------------------------


def test_build_engine_url_matches_resolve_engine_link_for_every_engine():
    query = "тестовий запит"
    for spec in ENGINES:
        link = resolve_engine_link(spec, query, today=TODAY_FRESH)
        legacy_url = build_engine_url(spec, query, TODAY_FRESH)
        if link.is_prefilled:
            assert legacy_url == link.url, spec.code
        else:
            assert legacy_url is None, spec.code


# ---------------------------------------------------------------------------
# 18. Детермінізм: два прогони по всьому реєстру дають однаковий результат.
# ---------------------------------------------------------------------------


def test_resolve_engine_link_is_deterministic_across_runs():
    query = "детермінований запит"
    run_1 = [resolve_engine_link(spec, query, today=TODAY_FRESH) for spec in ENGINES]
    run_2 = [resolve_engine_link(spec, query, today=TODAY_FRESH) for spec in ENGINES]
    assert run_1 == run_2
    assert [link.engine_code for link in run_1] == [link.engine_code for link in run_2]


# ---------------------------------------------------------------------------
# 19. Порожній query_text блокує prefill навіть у Google.
# ---------------------------------------------------------------------------


def test_empty_or_blank_query_blocks_prefill():
    google = engine_by_code("google")
    link = resolve_engine_link(google, "   ", today=TODAY_FRESH)
    assert link.is_prefilled is False
    assert link.url == google.home_url
    assert link.block_reason == PrefillBlockReason.EMPTY_QUERY


# ---------------------------------------------------------------------------
# 20. Модуль не робить мережевих викликів.
# ---------------------------------------------------------------------------


def test_engines_module_has_no_network_imports():
    import search.engines as engines_module

    source = Path(engines_module.__file__).read_text(encoding="utf-8")
    forbidden = ("import requests", "urllib.request", "import httpx", "import socket", "from socket")
    offenders = [needle for needle in forbidden if needle in source]
    assert not offenders, offenders
