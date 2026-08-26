"""
Модульні тести переходів статусу `search/state.py` (§18.1). Крок 3 §22
реалізує лише сам об'єкт стану і чисті переходи — без JSON-серіалізації
(крок 13); ці тести фіксують інваріанти, які схема JSON-проєкту не повинна
буде порушити пізніше.
"""

from __future__ import annotations

import pytest

from search.state import (
    QueryState,
    add_failed_engine,
    initial_state,
    is_absolute_http_url,
    is_counted_as_checked,
    mark_found,
    mark_no_result,
    mark_unchecked,
)


def test_initial_state_is_unchecked_with_empty_fields():
    state = initial_state("q1")
    assert state.query_id == "q1"
    assert state.status == "unchecked"
    assert state.needs_review is False
    assert state.found_engine is None
    assert state.source_url is None
    assert state.failed_engines == ()
    assert state.comment == ""


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/x", True),
        ("http://example.com/x", True),
        ("ftp://example.com/x", False),
        ("not a url", False),
        ("example.com", False),
    ],
)
def test_is_absolute_http_url(url, expected):
    assert is_absolute_http_url(url) is expected


def test_mark_found_requires_non_empty_engine():
    state = initial_state("q1")
    with pytest.raises(ValueError):
        mark_found(state, found_engine="")


def test_mark_found_rejects_non_absolute_source_url():
    state = initial_state("q1")
    with pytest.raises(ValueError):
        mark_found(state, found_engine="Google", source_url="not-a-url")


def test_mark_found_sets_engine_and_url():
    state = initial_state("q1")
    updated = mark_found(state, found_engine="Google", source_url="https://example.com/doc")
    assert updated.status == "found"
    assert updated.found_engine == "Google"
    assert updated.source_url == "https://example.com/doc"


def test_mark_no_result_clears_engine_and_url_but_keeps_failed_engines_and_comment():
    state = QueryState(
        query_id="q1",
        status="found",
        found_engine="Google",
        source_url="https://example.com/doc",
        failed_engines=("yandex",),
        comment="старий коментар",
    )
    updated = mark_no_result(state)
    assert updated.status == "no_result"
    assert updated.found_engine is None
    assert updated.source_url is None
    assert updated.failed_engines == ("yandex",)
    assert updated.comment == "старий коментар"


def test_mark_no_result_overwrites_comment_when_a_new_one_is_given():
    state = mark_found(initial_state("q1"), found_engine="Google")
    updated = mark_no_result(state, comment="новий коментар")
    assert updated.comment == "новий коментар"


def test_mark_unchecked_clears_engine_and_url():
    state = mark_found(initial_state("q1"), found_engine="Google", source_url="https://example.com/doc")
    updated = mark_unchecked(state)
    assert updated.status == "unchecked"
    assert updated.found_engine is None
    assert updated.source_url is None


def test_add_failed_engine_is_idempotent_and_preserves_order():
    state = initial_state("q1")
    state = add_failed_engine(state, "yandex")
    state = add_failed_engine(state, "elibrary")
    state = add_failed_engine(state, "yandex")
    assert state.failed_engines == ("yandex", "elibrary")


def test_is_counted_as_checked_matches_18_1():
    assert is_counted_as_checked(initial_state("q1")) is False
    found = mark_found(initial_state("q1"), found_engine="Google")
    assert is_counted_as_checked(found) is True
    no_result = mark_no_result(initial_state("q1"))
    assert is_counted_as_checked(no_result) is True
    needs_review = QueryState(query_id="q1", status="found", found_engine="Google", needs_review=True)
    assert is_counted_as_checked(needs_review) is False
