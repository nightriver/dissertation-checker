"""
Модульні тести чистих переходів `search/ui_logic.py` (§22, крок 3): побудова
початкового триажу і `apply_status_action`. Наскрізний виклик
`run_search_pipeline` покритий інтеграційним тестом тонкого зрізу.
"""

from __future__ import annotations

import pytest

from search.state import initial_state
from search.ui_logic import apply_status_action, build_initial_query_states


class _FakeQuery:
    def __init__(self, query_id: str) -> None:
        self.query_id = query_id


def test_build_initial_query_states_is_unchecked_for_every_query():
    class _FakeResult:
        queries = (_FakeQuery("q1"), _FakeQuery("q2"))

    states = build_initial_query_states(_FakeResult())
    assert set(states) == {"q1", "q2"}
    assert all(state.status == "unchecked" for state in states.values())


def test_apply_status_action_no_result_does_not_mutate_input_dict():
    states = {"q1": initial_state("q1")}
    updated = apply_status_action(states, "q1", "no_result")
    assert states["q1"].status == "unchecked"
    assert updated["q1"].status == "no_result"
    assert updated is not states


def test_apply_status_action_found_requires_engine():
    states = {"q1": initial_state("q1")}
    with pytest.raises(ValueError):
        apply_status_action(states, "q1", "found")


def test_apply_status_action_found_sets_engine_and_url():
    states = {"q1": initial_state("q1")}
    updated = apply_status_action(
        states, "q1", "found", found_engine="Google", source_url="https://example.com/x"
    )
    assert updated["q1"].status == "found"
    assert updated["q1"].found_engine == "Google"
    assert updated["q1"].source_url == "https://example.com/x"


def test_apply_status_action_unchecked_resets_state():
    states = {"q1": initial_state("q1")}
    states = apply_status_action(states, "q1", "found", found_engine="Google")
    states = apply_status_action(states, "q1", "unchecked")
    assert states["q1"].status == "unchecked"
    assert states["q1"].found_engine is None


def test_apply_status_action_unknown_query_id_raises_key_error():
    with pytest.raises(KeyError):
        apply_status_action({}, "missing", "no_result")


def test_apply_status_action_unknown_action_raises_value_error():
    states = {"q1": initial_state("q1")}
    with pytest.raises(ValueError):
        apply_status_action(states, "q1", "bogus")
