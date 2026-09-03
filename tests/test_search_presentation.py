"""Малі модульні тести безпечного форматування PLAN_SEARCH.md §17."""

from dataclasses import FrozenInstanceError
from urllib.parse import parse_qs, urlsplit

import pytest

from search.presentation import (
    CopyFieldView,
    STATUS_LABELS,
    _assistant_links,
    channel_label,
    rejection_reason_label,
    render_highlighted_text,
)
from search.types import Channel


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


def test_internal_channel_codes_are_rendered_as_plain_ukrainian() -> None:
    assert channel_label(Channel.A) == "Авторське положення"
    assert channel_label(Channel.K) == "Ознаки перекладу"
    assert rejection_reason_label("score_below_threshold_2:N") == (
        "недостатньо ознак: наукова новизна"
    )
    assert rejection_reason_label("diversity_limit") == (
        "обмеження різноманітності запитів"
    )


@pytest.mark.parametrize("has_calque", [False, True])
def test_assistant_prompt_preserves_long_paragraph_and_special_characters(has_calque):
    paragraph = 'Український текст: «цитата» & q=інше + 100% #фрагмент <тег>\n' * 60
    for link in _assistant_links(paragraph, has_calque=has_calque):
        url = urlsplit(link.url)
        assert not url.fragment
        params = parse_qs(url.query)
        assert set(params) == {"q"}
        assert params["q"][0].split("\n\n", 1)[1] == paragraph


@pytest.mark.parametrize("paragraph", [None, "", " \n "])
def test_missing_paragraph_does_not_create_a_short_fragment_fallback(paragraph):
    links = _assistant_links(paragraph, has_calque=False)
    assert len(links) == 2
    assert all(link.url is None for link in links)
