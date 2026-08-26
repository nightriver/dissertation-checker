"""
Модульні тести каналу A `search/markers.py` (§10.2, крок 3 §22): чотири
підправила маркера і межа балу +2/max 6. Канали N, B, K, T, L, D — крок 9.
"""

from __future__ import annotations

from search.markers import (
    CHANNEL_A_MAX_SCORE,
    CHANNEL_A_SIGNAL_SCORE,
    find_channel_a_signals,
    score_channel_a,
)


def test_finds_stem_marker_with_ukrainian_continuation():
    signals = find_channel_a_signals("Автори вважають це рішення доцільним.")
    rule_ids = {s.rule_id for s in signals}
    assert "A.stem.вважа" in rule_ids
    assert "A.stem.доцільн" in rule_ids


def test_finds_exact_phrase_markers():
    signals = find_channel_a_signals("Дійшли висновку, що у такій редакції норма діє.")
    rule_ids = {s.rule_id for s in signals}
    assert any(r.startswith("A.phrase.") for r in rule_ids)
    assert len(signals) == 2  # "дійшли висновку" і "у такій редакції"


def test_finds_under_understand_marker_within_eight_tokens():
    signals = find_channel_a_signals("Під ефективністю правового регулювання ми розуміємо досягнення мети.")
    assert any(s.rule_id == "A.under_understand" for s in signals)


def test_does_not_find_under_understand_marker_beyond_eight_tokens():
    filler = " ".join(f"слово{i}" for i in range(9))
    signals = find_channel_a_signals(f"Під {filler} розуміємо мету.")
    assert not any(s.rule_id == "A.under_understand" for s in signals)


def test_finds_we_surveyed_marker_with_optional_bulo():
    signals = find_channel_a_signals("Нами було опитано 120 респондентів.")
    assert any(s.rule_id == "A.we_surveyed" for s in signals)

    signals_without_bulo = find_channel_a_signals("Нами проаналізовано матеріали справ.")
    assert any(s.rule_id == "A.we_surveyed" for s in signals_without_bulo)


def test_bare_stem_without_ukrainian_letter_continuation_is_not_a_signal():
    """§10.2: продовження обов'язкове ("з українським буквеним продовженням");
    гола основа без жодної літери після неї — не слово, сигналом не є."""
    assert find_channel_a_signals("пропон.") == ()
    assert find_channel_a_signals("вважа!") == ()


def test_no_signals_in_neutral_text():
    assert find_channel_a_signals("Це нейтральне речення без маркерів.") == ()


def test_score_is_two_per_match_capped_at_six():
    assert score_channel_a(()) == 0.0

    one_signal = find_channel_a_signals("Ми пропонуємо це рішення.")
    assert len(one_signal) == 1
    assert score_channel_a(one_signal) == CHANNEL_A_SIGNAL_SCORE

    many_signals_text = (
        "Ми пропонуємо, вважаємо це доцільним, обґрунтованим і запропонованим, "
        "а також удосконаленим рішенням, на нашу думку."
    )
    many_signals = find_channel_a_signals(many_signals_text)
    assert len(many_signals) > 3
    assert score_channel_a(many_signals) == CHANNEL_A_MAX_SCORE


def test_signals_are_sorted_by_position():
    signals = find_channel_a_signals("Дійшли висновку, що ми пропонуємо це рішення.")
    starts = [s.start for s in signals]
    assert starts == sorted(starts)
