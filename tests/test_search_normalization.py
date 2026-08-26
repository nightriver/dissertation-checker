"""
Модульні тести мінімальної нормалізації `search/normalization.py` (§7,
крок 3 §22): NFKC посимвольно, видалення soft hyphen, карта `origins` і
токенізація. Повна обробка гоміогліфів/переносу — крок 4.
"""

from __future__ import annotations

import pytest

from search.normalization import (
    WORD_TOKEN_RE,
    map_normalized_offsets,
    map_normalized_span,
    normalize_text,
    tokenize,
)


def test_normalize_text_removes_soft_hyphen_without_producing_a_char():
    raw = "приклад­слово"
    normalized = normalize_text(raw)
    assert "­" not in normalized.text
    assert normalized.text == "прикладслово"
    assert len(normalized.origins) == len(normalized.text)


def test_normalize_text_nfkc_expands_ligature_to_multiple_chars_with_same_origin():
    # U+FB01 "ﬁ" (лігатура) NFKC-розкладається на "fi" — обидва символи
    # мають вказувати на один вихідний кластер (§7).
    raw = "aﬁb"
    normalized = normalize_text(raw)
    assert normalized.text == "afib"
    # Індекси символів "f" та "i" відповідають одному вихідному символу "ﬁ" (позиція 1).
    assert normalized.origins[1] == normalized.origins[2]
    assert normalized.origins[1].raw_start == 1
    assert normalized.origins[1].raw_end == 2


def test_map_normalized_offsets_merges_adjacent_raw_intervals():
    normalized = normalize_text("абв")
    offsets = map_normalized_offsets(normalized, 0, 3)
    assert offsets == ((0, 3),)


def test_map_normalized_offsets_absorbs_soft_hyphen_into_neighbour_span():
    # Видалений soft hyphen не створює розриву в мапі: його вихідний символ
    # поглинається сусіднім випущеним символом, тож мапа лишається суцільною
    # і об'єднується в один інтервал по всьому вихідному тексту (§7).
    normalized = normalize_text("а­бв")
    offsets = map_normalized_offsets(normalized, 0, 3)
    assert offsets == ((0, 4),)


def test_map_normalized_offsets_keeps_non_adjacent_intervals_separate():
    # Дефіс і перевід рядка при склейці переносу (окремий прохід конвеєра,
    # §7 п.4) не поглинаються сусідом — обидві половини слова свідомо
    # лишаються окремими вихідними інтервалами.
    normalized = normalize_text("загаль-\nне")
    offsets = map_normalized_offsets(normalized, 0, len(normalized.text))
    assert offsets == ((0, 6), (8, 10))


def test_map_normalized_offsets_rejects_invalid_ranges():
    normalized = normalize_text("абв")
    with pytest.raises(ValueError):
        map_normalized_offsets(normalized, 2, 1)
    with pytest.raises(ValueError):
        map_normalized_offsets(normalized, 0, 100)
    with pytest.raises(ValueError):
        map_normalized_offsets(normalized, -1, 2)


def test_map_normalized_span_wraps_offsets_into_a_source_span():
    normalized = normalize_text("абв")
    span = map_normalized_span(normalized, 0, 3, block_id="blk-1", physical_page=5)
    assert len(span.parts) == 1
    part = span.parts[0]
    assert part.block_id == "blk-1"
    assert part.physical_page == 5
    assert (part.raw_start, part.raw_end) == (0, 3)


def test_tokenize_splits_words_and_punctuation_and_skips_whitespace():
    raw = "Привіт, світ! 123"
    normalized = normalize_text(raw)
    tokens = tokenize(raw, normalized)
    word_tokens = [t for t in tokens if t.is_word]
    punct_tokens = [t for t in tokens if not t.is_word]
    assert [t.raw for t in word_tokens] == ["Привіт", "світ", "123"]
    assert [t.raw for t in punct_tokens] == [",", "!"]
    for token in tokens:
        assert raw[token.raw_start : token.raw_end] == token.raw


def test_word_token_re_matches_apostrophe_and_hyphen_inside_words():
    assert WORD_TOKEN_RE.fullmatch("розум'я")
    assert WORD_TOKEN_RE.fullmatch("будь-який")
