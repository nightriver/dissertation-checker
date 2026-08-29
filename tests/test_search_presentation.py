"""Малі модульні тести безпечного форматування PLAN_SEARCH.md §17."""

from dataclasses import FrozenInstanceError

import pytest

from search.presentation import CopyFieldView, STATUS_LABELS, render_highlighted_text


def test_plain_text_is_escaped_even_without_highlights() -> None:
    assert render_highlighted_text("a & <b>", ()) == "a &amp; &lt;b&gt;"


def test_invalid_and_out_of_range_spans_are_clamped_or_ignored() -> None:
    rendered = render_highlighted_text("абвг", ((3, 2), (-10, 2), (20, 30)))
    assert rendered == "<mark>аб</mark>вг"


def test_adjacent_spans_form_one_trusted_mark_element() -> None:
    rendered = render_highlighted_text("абвг", ((0, 2), (2, 4)))
    assert rendered == "<mark>абвг</mark>"


def test_status_labels_keep_all_three_domain_states() -> None:
    assert tuple(STATUS_LABELS) == ("unchecked", "no_result", "found")


def test_view_models_are_immutable() -> None:
    item = CopyFieldView("Запит", "текст")
    with pytest.raises(FrozenInstanceError):
        item.text = "інший"
