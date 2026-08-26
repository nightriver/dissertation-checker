"""
Модульні тести реєстру рушіїв `search/engines.py` (§16, крок 3 §22).
Крок 3 реєструє лише Google; повна таблиця §16 і fallback-попередження —
крок 12. Тести передають фіксований `today`, щоб не залежати від календаря.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from search.engines import (
    ENGINES,
    GOOGLE,
    NRAT,
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

FIXED_TODAY = date(2026, 8, 26)


def test_google_is_registered_with_active_prefill():
    assert GOOGLE in ENGINES
    assert GOOGLE.active_prefill is True
    assert Channel.A in GOOGLE.channels


def test_build_engine_url_encodes_query_and_uses_https():
    url = build_engine_url(GOOGLE, '«на нашу думку»', FIXED_TODAY)
    assert url is not None
    assert url.startswith("https://www.google.com/search?q=")
    assert "+" in url or "%C2%AB" in url  # quote_plus кодує пробіли/лапки


def test_build_engine_url_returns_none_when_verification_is_stale():
    stale = dataclasses.replace(GOOGLE, verified_on=FIXED_TODAY - timedelta(days=STALE_AFTER_DAYS + 1))
    assert is_engine_verification_stale(stale, FIXED_TODAY) is True
    assert build_engine_url(stale, "запит", FIXED_TODAY) is None


def test_build_engine_url_returns_none_when_verification_is_missing():
    unverified = dataclasses.replace(GOOGLE, verified_on=None)
    assert is_engine_verification_stale(unverified, FIXED_TODAY) is True
    assert build_engine_url(unverified, "запит", FIXED_TODAY) is None


def test_verification_exactly_at_the_boundary_is_not_stale():
    boundary = dataclasses.replace(GOOGLE, verified_on=FIXED_TODAY - timedelta(days=STALE_AFTER_DAYS))
    assert is_engine_verification_stale(boundary, FIXED_TODAY) is False


def test_build_engine_url_returns_none_when_query_exceeds_max_chars():
    tight = dataclasses.replace(GOOGLE, max_query_chars=5)
    assert build_engine_url(tight, "занадто довгий запит", FIXED_TODAY) is None


def test_build_engine_url_returns_none_without_prefill_or_template():
    no_prefill = dataclasses.replace(GOOGLE, active_prefill=False)
    assert build_engine_url(no_prefill, "запит", FIXED_TODAY) is None

    no_template = dataclasses.replace(GOOGLE, query_url_template=None)
    assert build_engine_url(no_template, "запит", FIXED_TODAY) is None


# ---------------------------------------------------------------------------
# Крок 12: повний реєстр і resolve_engine_link
# ---------------------------------------------------------------------------

def test_registry_has_seven_engines_in_order():
    codes = [spec.code for spec in ENGINES]
    assert codes == [
        "google",
        "google_scholar",
        "yandex",
        "cyberleninka",
        "elibrary",
        "dissercat",
        "nrat",
    ]
    assert len(set(codes)) == 7


def test_only_google_has_active_prefill():
    for spec in ENGINES:
        if spec.code == "google":
            assert spec.active_prefill is True
        else:
            assert spec.active_prefill is False
            assert spec.verified_on is None
            assert spec.query_url_template is None


def test_all_home_urls_use_https():
    for spec in ENGINES:
        assert spec.home_url.startswith("https://")


def test_warnings_match_table_exactly():
    warnings = {spec.code: spec.warning for spec in ENGINES}
    vpn_masc = "може бути недоступний з поточної мережі; за потреби VPN"
    vpn_fem = "може бути недоступна з поточної мережі; за потреби VPN"
    assert warnings["yandex"] == vpn_masc
    assert warnings["elibrary"] == vpn_masc
    assert warnings["dissercat"] == vpn_masc
    assert warnings["cyberleninka"] == vpn_fem
    assert warnings["google"] is None
    assert warnings["google_scholar"] is None
    assert warnings["nrat"] is None


def test_resolve_engine_link_prefills_google():
    link = resolve_engine_link(GOOGLE, '«тест»', today=FIXED_TODAY)
    assert link.is_prefilled is True
    assert link.block_reason is None
    assert link.url.startswith("https://www.google.com/search?q=")
    assert "«" not in link.url and "»" not in link.url and " " not in link.url


def test_resolve_engine_link_blocks_unverified_engine():
    link = resolve_engine_link(NRAT, "запит", today=FIXED_TODAY)
    assert link.is_prefilled is False
    assert link.url == NRAT.home_url
    assert link.block_reason == PrefillBlockReason.PREFILL_DISABLED


def test_resolve_engine_link_stale_verification():
    spec = dataclasses.replace(
        GOOGLE, verified_on=FIXED_TODAY - timedelta(days=STALE_AFTER_DAYS + 1)
    )
    link = resolve_engine_link(spec, "запит", today=FIXED_TODAY)
    assert link.block_reason == PrefillBlockReason.STALE_VERIFICATION
    assert link.url == spec.home_url

    boundary = dataclasses.replace(GOOGLE, verified_on=FIXED_TODAY - timedelta(days=STALE_AFTER_DAYS))
    boundary_link = resolve_engine_link(boundary, "запит", today=FIXED_TODAY)
    assert boundary_link.is_prefilled is True


def test_resolve_engine_link_query_too_long_not_truncated():
    long_query = "а" * 2049
    link = resolve_engine_link(GOOGLE, long_query, today=FIXED_TODAY)
    assert link.block_reason == PrefillBlockReason.QUERY_TOO_LONG
    assert link.url == GOOGLE.home_url
    assert link.query_text == long_query

    ok_query = "а" * 2048
    ok_link = resolve_engine_link(GOOGLE, ok_query, today=FIXED_TODAY)
    assert ok_link.is_prefilled is True


def test_resolve_engine_link_reason_order():
    disabled_stale = dataclasses.replace(
        GOOGLE, active_prefill=False, verified_on=FIXED_TODAY - timedelta(days=1000)
    )
    assert resolve_engine_link(disabled_stale, "запит", today=FIXED_TODAY).block_reason == (
        PrefillBlockReason.PREFILL_DISABLED
    )

    no_template = dataclasses.replace(GOOGLE, active_prefill=True, query_url_template=None)
    assert resolve_engine_link(no_template, "запит", today=FIXED_TODAY).block_reason == (
        PrefillBlockReason.NO_TEMPLATE
    )

    not_verified = dataclasses.replace(GOOGLE, active_prefill=True, verified_on=None)
    assert resolve_engine_link(not_verified, "запит", today=FIXED_TODAY).block_reason == (
        PrefillBlockReason.NOT_VERIFIED
    )


def test_resolve_engine_link_rejects_non_https_scheme():
    http_spec = dataclasses.replace(
        GOOGLE, query_url_template="http://www.google.com/search?q={query}"
    )
    link = resolve_engine_link(http_spec, "запит", today=FIXED_TODAY)
    assert link.is_prefilled is False
    assert link.block_reason == PrefillBlockReason.NO_TEMPLATE
    assert link.url == http_spec.home_url


def test_query_text_present_even_when_prefilled():
    link = resolve_engine_link(GOOGLE, "запит", today=FIXED_TODAY)
    assert link.query_text == "запит"


def test_engines_for_channel_returns_correct_order_and_empty_for_channel_d():
    k_engines = [spec.code for spec in engines_for_channel(Channel.K)]
    assert k_engines == ["google", "yandex", "cyberleninka", "elibrary", "dissercat"]
    assert engines_for_channel(Channel.D) == ()


def test_engine_by_code_lookup_and_missing():
    assert engine_by_code("google") is GOOGLE
    try:
        engine_by_code("no_such")
        assert False, "має бути KeyError"
    except KeyError:
        pass


def test_build_engine_url_matches_resolve_engine_link_for_all_engines():
    for spec in ENGINES:
        link = resolve_engine_link(spec, "запит", today=FIXED_TODAY)
        expected = link.url if link.is_prefilled else None
        assert build_engine_url(spec, "запит", FIXED_TODAY) == expected


def test_resolve_engine_link_is_deterministic():
    first = [resolve_engine_link(spec, "запит", today=FIXED_TODAY) for spec in ENGINES]
    second = [resolve_engine_link(spec, "запит", today=FIXED_TODAY) for spec in ENGINES]
    assert first == second


def test_resolve_engine_link_empty_query():
    link = resolve_engine_link(GOOGLE, "   ", today=FIXED_TODAY)
    assert link.is_prefilled is False
    assert link.url == GOOGLE.home_url
    assert link.block_reason == PrefillBlockReason.EMPTY_QUERY


def test_length_counted_before_url_encoding():
    query = "а" * 2048  # кирилиця, після quote_plus значно довша
    link = resolve_engine_link(GOOGLE, query, today=FIXED_TODAY)
    assert link.is_prefilled is True
