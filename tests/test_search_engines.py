"""
Модульні тести реєстру рушіїв `search/engines.py` (§16, крок 3 §22).
Крок 3 реєструє лише Google; повна таблиця §16 і fallback-попередження —
крок 12. Тести передають фіксований `today`, щоб не залежати від календаря.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from search.engines import ENGINES, GOOGLE, STALE_AFTER_DAYS, build_engine_url, is_engine_verification_stale
from search.types import Channel


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
