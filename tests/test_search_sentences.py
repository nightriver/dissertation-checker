"""
Модульні тести мінімального поділу речень `search/sentences.py` (§22, крок 3).
Захист скорочень і десяткових чисел (§10.1) — крок 4; тут перевіряється
лише сама межа "розділовий знак → пробіл/кінець → велика літера/цифра/
лапка/тире" і свідоме відкидання незавершеного хвоста.
"""

from __future__ import annotations

from search.sentences import split_sentences


def test_empty_text_gives_no_sentences():
    assert split_sentences("") == ()


def test_single_terminated_sentence():
    bounds = split_sentences("Це речення.")
    assert bounds == ((0, 11),)


def test_two_sentences_split_on_period_before_uppercase():
    text = "Перше речення. Друге речення."
    bounds = split_sentences(text)
    assert len(bounds) == 2
    assert text[bounds[0][0] : bounds[0][1]] == "Перше речення."
    assert text[bounds[1][0] : bounds[1][1]] == "Друге речення."


def test_boundary_before_digit_and_open_quote_and_dash():
    text = 'Перше. 2026 рік. «Цитата». — Тире.'
    bounds = split_sentences(text)
    fragments = [text[s:e] for s, e in bounds]
    assert fragments == ["Перше.", "2026 рік.", "«Цитата».", "— Тире."]


def test_exclamation_and_ellipsis_are_terminators():
    text = "Дійсно! А що далі… Кінець."
    bounds = split_sentences(text)
    fragments = [text[s:e] for s, e in bounds]
    assert fragments == ["Дійсно!", "А що далі…", "Кінець."]


def test_unterminated_tail_is_dropped_not_returned_as_a_sentence():
    text = "Завершене речення. Незавершений залишок без крапки"
    bounds = split_sentences(text)
    assert len(bounds) == 1
    assert text[bounds[0][0] : bounds[0][1]] == "Завершене речення."


def test_lowercase_after_period_does_not_split():
    text = "Наприклад щось. далі маленька буква не є межею."
    bounds = split_sentences(text)
    # Крапка після "щось" не межа, бо наступний символ малий — весь фрагмент
    # до першої дійсної межі лишається одним реченням.
    assert len(bounds) == 1


def test_terminal_punctuation_at_end_of_text_without_following_char():
    text = "Одне речення без хвоста."
    bounds = split_sentences(text)
    assert bounds == ((0, len(text)),)
