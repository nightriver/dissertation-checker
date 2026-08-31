"""
Шлюз на дефект «ліва і права комірки рядка — різний текст».

Скарга користувача: у таблиці режиму порівняння лівий фрагмент часто не
містить того, що показано праворуч. Замір на парі `diss-doc.pdf` та її копії
без двох останніх аркушів (потоки токенів там збігаються поелементно) дав
360 рядків, з яких на діагоналі стояв 21, а 339 сполучали різні місця роботи.

Тести навмисно синтетичні: набір має лишатися швидким і детермінованим,
реальні пари живуть у `tools/benchmark_compare.py`.
"""

from __future__ import annotations

from compare import params
from compare.matcher import (
    Candidate,
    align_candidate_segments,
    compare_tokens,
    coverage_from_segments,
    count_off_alignment,
    deduplicate_segments,
)
from compare.normalize import tokenize_lines


def _tokens(text: str):
    return tokenize_lines([{"line": text, "page": None}])


def _words(prefix: str, count: int, start: int = 0) -> list[str]:
    return [f"{prefix}{index}" for index in range(start, start + count)]


def _longest_unmatched_run(spans) -> int:
    """Найдовша смуга поспіль без збігу з одного боку сегмента."""
    longest = current = 0
    for span in sorted(spans, key=lambda item: item.start_token):
        size = span.end_token - span.start_token
        if span.operation == "equal":
            current = 0
        else:
            current += size
            longest = max(longest, current)
    return longest


def test_segment_has_no_long_unmatched_gap():
    """Два збіги, розділені чужим текстом, — це два рядки, а не один."""
    common_first = " ".join(_words("норма", 20))
    common_second = " ".join(_words("вимога", 20))
    filler_a = " ".join(_words("лівий", 40))
    filler_b = " ".join(_words("правий", 40))
    result = compare_tokens(
        _tokens(f"{common_first} {filler_a} {common_second}"),
        _tokens(f"{common_first} {filler_b} {common_second}"),
    )
    assert len(result.segments) == 2
    for segment in result.segments:
        assert _longest_unmatched_run(segment.a_spans) <= params.MAX_MATCH_GAP
        assert _longest_unmatched_run(segment.b_spans) <= params.MAX_MATCH_GAP


def test_no_segment_anywhere_exceeds_the_gap_limit():
    """Інваріант для всієї таблиці, а не для окремо підібраного випадку."""
    base = _words("розділ", 60)
    changed = base[:20] + _words("вставка", 30) + base[20:]
    result = compare_tokens(_tokens(" ".join(base)), _tokens(" ".join(changed)))
    assert result.segments
    for segment in result.segments:
        assert _longest_unmatched_run(segment.a_spans) <= params.MAX_MATCH_GAP
        assert _longest_unmatched_run(segment.b_spans) <= params.MAX_MATCH_GAP


def test_identical_documents_give_single_diagonal():
    """
    Головний шлюз правки.

    Текст навмисно містить повторений абзац: без повтору тест проходить і на
    зламаному коді, бо зчіплювати немає чого.
    """
    repeated = " ".join(_words("положення", 30))
    text = " ".join([
        " ".join(_words("вступ", 40)),
        repeated,
        " ".join(_words("основна", 40)),
        repeated,
        " ".join(_words("висновки", 40)),
    ])
    tokens = _tokens(text)
    result = compare_tokens(tokens, tokens)
    assert result.segments
    for segment in result.segments:
        assert segment.a_start == segment.b_start
        assert segment.similarity == 1.0
        assert segment.kind == "verbatim"
    assert count_off_alignment(result.segments) == 0


def test_repeated_fragment_yields_one_row_with_counter():
    """П'ять копій одного абзацу — один рядок і лічильник, а не п'ять рядків."""
    fragment = " ".join(_words("формула", 30))
    separator = lambda index: " ".join(_words(f"проміжок{index}", 40))  # noqa: E731
    right = " ".join(
        part
        for index in range(5)
        for part in (fragment, separator(index))
    )
    result = compare_tokens(_tokens(fragment), _tokens(right))
    assert len(result.segments) == 1
    assert result.segments[0].suppressed_repeats == 4


def test_suppressed_repeats_default_to_zero_for_single_find():
    fragment = " ".join(_words("унікальне", 30))
    result = compare_tokens(_tokens(fragment), _tokens(fragment))
    assert len(result.segments) == 1
    assert result.segments[0].suppressed_repeats == 0


def test_split_pieces_keep_parent_coverage():
    """Різання по розриву не має втрачати жодного збіглого слова."""
    common_first = " ".join(_words("альфа", 25))
    common_second = " ".join(_words("бета", 25))
    tokens_a = _tokens(f"{common_first} {' '.join(_words('шум', 40))} {common_second}")
    tokens_b = _tokens(f"{common_first} {' '.join(_words('інше', 40))} {common_second}")
    candidate = Candidate(0, len(tokens_a), 0, len(tokens_b), 4)
    pieces = align_candidate_segments(candidate, tokens_a, tokens_b)
    assert len(pieces) == 2
    assert sum(piece.matched for piece in pieces) == 50
    assert coverage_from_segments(pieces, "a") == 50


def test_one_sided_overlap_is_enough_to_deduplicate():
    """Та сама область праворуч більше не дає окремих рядків."""
    tokens = _tokens(" ".join(_words("слово", 60)))
    wide = align_candidate_segments(Candidate(0, 60, 0, 60, 5), tokens, tokens)
    narrow = align_candidate_segments(Candidate(10, 50, 10, 50, 4), tokens, tokens)
    assert wide and narrow
    kept = deduplicate_segments(wide + narrow)
    assert len(kept) == 1
    assert kept[0].suppressed_repeats == 1


def test_unrelated_documents_keep_single_known_match():
    """Шлюз проти переужорсточення: справжня спільна цитата лишається."""
    quote = " ".join(_words("цитата", 20))
    left = " ".join(_words("ліва", 80)) + " " + quote + " " + " ".join(_words("хвіст", 40))
    right = " ".join(_words("права", 90)) + " " + quote + " " + " ".join(_words("кінець", 30))
    result = compare_tokens(_tokens(left), _tokens(right))
    assert len(result.segments) == 1
    assert result.segments[0].matched == 20


def test_off_alignment_counts_only_distant_drift():
    """Показник рахує саме віддалені знахідки, а не будь-яку перестановку."""
    assert count_off_alignment([]) == 0


def test_long_verbatim_match_stays_one_row():
    """
    Суцільний збіг довший за MAX_CANDIDATE_TOKENS — один рядок, не кілька.

    Область ріжеться на куски перед SequenceMatcher, але потоки опкодів
    зшиваються назад (розділ 6.3, п. 7). Довжина навмисно перевищує межу
    різання більш ніж удвічі, щоб швів було два, а не один.
    """
    long_text = " ".join(_words("речення", params.MAX_CANDIDATE_TOKENS * 2 + 500))
    tokens = _tokens(long_text)
    result = compare_tokens(tokens, tokens)
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.a_start == 0
    assert segment.a_end == len(tokens)
    assert segment.matched == len(tokens)
    assert segment.kind == "verbatim"


def test_reglue_preserves_coverage():
    """
    Шлюз проти помилки прототипу: зшивка не має губити збіги.

    Прототип склейки уронив покриття зі 100 % до 7,8 %, бо об'єднував блоки
    конкатенацією списків замість об'єднання інтервалів.
    """
    tokens = _tokens(" ".join(_words("абзац", params.MAX_CANDIDATE_TOKENS * 2 + 500)))
    result = compare_tokens(tokens, tokens)
    assert coverage_from_segments(result.segments, "a") == len(tokens)
    assert coverage_from_segments(result.segments, "b") == len(tokens)


def test_seam_words_are_not_shown_twice():
    """Перекриття кусків не повинно давати двох рядків з тим самим текстом."""
    tokens = _tokens(" ".join(_words("пункт", params.MAX_CANDIDATE_TOKENS * 2 + 500)))
    result = compare_tokens(tokens, tokens)
    total_a = sum(segment.len_a for segment in result.segments)
    assert total_a == len(tokens)
