"""Еквівалентність нормативних маркерів для нормалізованих вікон пошуку."""

from __future__ import annotations

import pytest

from search.markers import _normative_marker_ids_from_normalized, normative_marker_ids
from search.normalization import normalize_text, tokenize


def _assert_window_equivalence(raw_text: str) -> None:
    """Порівнює обидва шляхи для усіх вікон 6–10 словесних токенів."""

    normalized = normalize_text(raw_text)
    tokens = [token for token in tokenize(raw_text, normalized) if token.is_word]
    for start in range(len(tokens)):
        for size in range(6, 11):
            end = start + size
            if end > len(tokens):
                break
            raw_window = raw_text[tokens[start].raw_start : tokens[end - 1].raw_end]
            normalized_window = normalized.text[
                tokens[start].normalized_start : tokens[end - 1].normalized_end
            ]
            assert normative_marker_ids(raw_window) == _normative_marker_ids_from_normalized(
                normalized_window
            )


@pytest.mark.parametrize(
    "raw_text",
    [
        "Положення стаття 15 визначає порядок наукової роботи для сучасного дослідження сьогодні",
        "Автор Aвтор аналізує рішeння для наукової роботи стаття 20 визначає порядок сьогодні",
        "Дослідникʼи розглядають нау-\nкове питання стаття 7 визначає сучасний порядок роботи",
        "Фінансове ﬁнансове дослідження містить сло­во стаття 8 визначає порядок наукової роботи",
        "Норма- \t\r\n права визначає стаття 9 сучасний порядок для наукового дослідження сьогодні",
    ],
)
def test_normalized_windows_match_raw_normative_marker_detection(raw_text: str):
    _assert_window_equivalence(raw_text)


@pytest.mark.corpus
def test_corpus_windows_match_raw_normative_marker_detection(canonical_corpus):
    for item in canonical_corpus:
        for donor in item.document.sentences:
            _assert_window_equivalence(donor.raw_text)
