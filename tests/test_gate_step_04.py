"""
Шлюз кроку 4 — `search/normalization.py` (карта origins, апострофи, переноси,
гомогліфи) і `search/sentences.py` (захисти меж речень, сторінковий хвіст,
вікна слів) (`steps/step-04.md`).

Пишеться незалежно від реалізації, лише за контрактом і числами пакета.
Нумерація тестів `test_gate_NN_*` відповідає пунктам розділу «Шлюз» пакета
`steps/step-04.md`. Додаткові тести `test_reject_*` покривають розділ
«Відмови» пакета.
"""

from __future__ import annotations

import pytest

from search import ALGO_VERSION
from search.normalization import (
    map_normalized_offsets,
    map_normalized_span,
    normalize_text,
    tokenize,
)
from search.sentences import (
    WINDOW_MAX_WORDS,
    WINDOW_MIN_WORDS,
    SentenceSpan,
    iter_word_windows,
    split_sentences,
    split_sentences_detailed,
)
from search.types import CharOrigin, NormalizedText, RawSpan, SourceSpan

PREVIOUS_ALGO_VERSION = "search-algo-2026-08-25"  # значення кроку 3, §4.1


# ---------------------------------------------------------------------------
# 1. Мʼякий перенос (U+00AD) прибирається, обʼєднаний вихідний інтервал.
# ---------------------------------------------------------------------------


def test_gate_01_soft_hyphen_removed_and_offsets_merge_into_one_span() -> None:
    """
    "прик­лад" (мʼякий перенос усередині слова "приклад") мусить дати
    текст аналізу без U+00AD і рівно один обʼєднаний вихідний інтервал по
    всьому слову, попри те що сам символ мʼякого переносу не потрапляє
    в аналіз.
    """
    raw = "прик­лад"
    normalized = normalize_text(raw)

    assert "­" not in normalized.text
    assert normalized.text == "приклад"
    assert len(normalized.origins) == len(normalized.text) == 7

    merged = map_normalized_offsets(normalized, 0, len(normalized.text))
    assert merged == ((0, len(raw)),)


# ---------------------------------------------------------------------------
# 2. NFKC-розширення лігатури: два символи аналізу, один вихідний символ.
# ---------------------------------------------------------------------------


def test_gate_02_nfkc_ligature_expands_to_two_chars_with_shared_origin() -> None:
    """Лігатура U+FB01 ("ﬁ") після NFKC розкладається на "fi" — два символи
    аналізу, обидва з CharOrigin, що вказує на той самий вихідний символ."""
    raw = "ﬁle"
    normalized = normalize_text(raw)

    assert normalized.text == "file"
    assert len(normalized.origins) == 4
    assert normalized.origins[0] == CharOrigin(0, 1)
    assert normalized.origins[1] == CharOrigin(0, 1)
    assert normalized.origins[2] == CharOrigin(1, 2)
    assert normalized.origins[3] == CharOrigin(2, 3)


# ---------------------------------------------------------------------------
# 3. Апострофи приводяться до U+0027, покриваючи рівно один вихідний символ.
# ---------------------------------------------------------------------------


def test_gate_03_apostrophe_variants_normalize_to_the_same_text() -> None:
    """"з’являється" (U+2019) і "з'являється" (U+0027) мають дати однаковий
    NormalizedText.text, а origins апострофа покривати рівно один символ."""
    raw_curly = "з’являється"
    raw_straight = "з'являється"

    normalized_curly = normalize_text(raw_curly)
    normalized_straight = normalize_text(raw_straight)

    assert normalized_curly.text == normalized_straight.text
    assert normalized_curly.text[1] == "'"

    origin_curly = normalized_curly.origins[1]
    origin_straight = normalized_straight.origins[1]
    assert (origin_curly.raw_end - origin_curly.raw_start) == 1
    assert (origin_straight.raw_end - origin_straight.raw_start) == 1
    assert (origin_curly.raw_start, origin_curly.raw_end) == (1, 2)
    assert (origin_straight.raw_start, origin_straight.raw_end) == (1, 2)


# ---------------------------------------------------------------------------
# 4. Гомогліфи: латиниця → кирилиця лише всередині кириличного токена.
# ---------------------------------------------------------------------------


def test_gate_04_homoglyph_latin_o_in_cyrillic_token_normalizes_and_pure_latin_word_stays_ascii() -> None:
    """Латинська "o" всередині кириличного слова прирівнюється до кириличної
    "о"; чисто латинське слово "Post" гомогліфами не змінюється."""
    word_with_latin_o = "пoст"  # п, латинська 'o', с, т
    word_pure_cyrillic = "пост"  # усі символи кириличні

    normalized_mixed = normalize_text(word_with_latin_o)
    normalized_pure = normalize_text(word_pure_cyrillic)
    assert normalized_mixed.text == normalized_pure.text

    normalized_latin = normalize_text("Post")
    assert normalized_latin.text.isascii()


# ---------------------------------------------------------------------------
# 5. Перенос через дефіс: два вихідні інтервали, raw_text не змінюється.
# ---------------------------------------------------------------------------


def test_gate_05_hyphen_line_break_joins_word_and_keeps_two_source_intervals() -> None:
    """"загаль-\\nне" дає в аналізі "загальне" без дефіса й переведення
    рядка; мапування по слову повертає два вихідні інтервали — до дефіса
    і після переведення рядка."""
    raw = "загаль-\nне"
    normalized = normalize_text(raw)

    assert normalized.text == "загальне"
    assert "-" not in normalized.text
    assert "\n" not in normalized.text

    mapped = map_normalized_offsets(normalized, 0, len(normalized.text))
    assert mapped == ((0, 6), (8, 10))
    assert raw[0:6] == "загаль"
    assert raw[8:10] == "не"
    assert raw == "загаль-\nне"  # raw_text лишився недоторканим


# ---------------------------------------------------------------------------
# 6. Схлопування пробільного пробігу відображається на весь вихідний пробіг.
# ---------------------------------------------------------------------------


def test_gate_06_whitespace_run_maps_onto_full_source_run() -> None:
    """Пробіг із трьох пробілів між "a" і "b": якщо реалізація схлопує його в
    один символ аналізу — цей символ вказує на весь вихідний пробіг; якщо ні
    — довжина нормалізованого пробігу дорівнює вихідній (3 символи)."""
    raw = "a   b"
    normalized = normalize_text(raw)

    a_idx = normalized.text.index("a")
    b_idx = normalized.text.rindex("b")
    ws_segment = normalized.text[a_idx + 1 : b_idx]

    if len(ws_segment) == 1:
        origin = normalized.origins[a_idx + 1]
        assert (origin.raw_start, origin.raw_end) == (1, 4)
    else:
        assert len(ws_segment) == 3
        for offset in range(a_idx + 1, b_idx):
            origin = normalized.origins[offset]
            assert (origin.raw_end - origin.raw_start) == 1

    merged = map_normalized_offsets(normalized, a_idx + 1, b_idx)
    assert merged == ((1, 4),)


# ---------------------------------------------------------------------------
# 7. Пряме застосування нормалізованих зсувів до raw_text заборонене.
# ---------------------------------------------------------------------------


def test_gate_07_direct_offset_reuse_on_raw_text_is_wrong_and_source_span_has_two_parts() -> None:
    """На вході з переносом raw_text[start:end] за нормалізованими зсувами не
    дорівнює нормалізованому фрагменту; map_normalized_span дає SourceSpan з
    більш ніж одним RawSpan."""
    raw = "загаль-\nне"
    normalized = normalize_text(raw)
    n = len(normalized.text)

    assert raw[0:n] != normalized.text[0:n]

    span = map_normalized_span(normalized, 0, n, block_id="b1", physical_page=3)
    assert isinstance(span, SourceSpan)
    assert len(span.parts) == 2
    assert span.parts[0] == RawSpan(block_id="b1", physical_page=3, raw_start=0, raw_end=6)
    assert span.parts[1] == RawSpan(block_id="b1", physical_page=3, raw_start=8, raw_end=10)


# ---------------------------------------------------------------------------
# 8. tokenize на переносі дає один словесний токен з обома половинами.
# ---------------------------------------------------------------------------


def test_gate_08_tokenize_hyphenated_break_yields_single_word_token_spanning_both_halves() -> None:
    """Текст зі склеєним переносом дає рівно один словесний токен, чиї
    raw_start/raw_end покривають обидві вихідні половини слова."""
    raw = "загаль-\nне"
    normalized = normalize_text(raw)
    tokens = tokenize(raw, normalized)

    assert len(tokens) == 1
    token = tokens[0]
    assert token.is_word is True
    assert token.raw_start == 0
    assert token.raw_end == len(raw)
    assert token.normalized == "загальне"
    assert token.normalized_start == 0
    assert token.normalized_end == len(normalized.text)


# ---------------------------------------------------------------------------
# 9. Версіонований список скорочень захищає межу речення.
# ---------------------------------------------------------------------------


def test_gate_09_abbreviation_list_protects_sentence_boundary() -> None:
    """"див. рис. 2 у праці проф. Шевченка." — жодна крапка зі списку
    скорочень межею не стає, увесь текст — одне речення."""
    text = "див. рис. 2 у праці проф. Шевченка."
    spans = split_sentences(text)

    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end].strip() == text


# ---------------------------------------------------------------------------
# 10. Ініціали та пари ініціалів захищають межу; звичайне скорочення — ні.
# ---------------------------------------------------------------------------


def test_gate_10_double_initials_protect_boundary_single_initial_does_not() -> None:
    """"Праці І. І. Іванова відомі." — одне речення (пара ініціалів захищена).
    "Це праця В. Іванова. Наступне речення." — два речення (крапка після
    прізвища завершує речення)."""
    text_double_initials = "Праці І. І. Іванова відомі."
    spans_double = split_sentences(text_double_initials)
    assert len(spans_double) == 1
    start, end = spans_double[0]
    assert text_double_initials[start:end].strip() == text_double_initials

    text_two_sentences = "Це праця В. Іванова. Наступне речення."
    spans_two = split_sentences(text_two_sentences)
    assert len(spans_two) == 2
    s0, e0 = spans_two[0]
    s1, e1 = spans_two[1]
    assert text_two_sentences[s0:e0].strip() == "Це праця В. Іванова."
    assert text_two_sentences[s1:e1].strip() == "Наступне речення."
    assert e0 <= s1


# ---------------------------------------------------------------------------
# 11. Десяткові числа не розбивають речення жодною внутрішньою крапкою/комою.
# ---------------------------------------------------------------------------


def test_gate_11_decimal_numbers_do_not_split_sentence() -> None:
    """"Показник склав 3,14 та 2.72 відсотка. Далі." — рівно два речення;
    жодна з внутрішніх крапок/ком десяткових чисел межею не стала."""
    text = "Показник склав 3,14 та 2.72 відсотка. Далі."
    spans = split_sentences(text)

    assert len(spans) == 2
    s0, e0 = spans[0]
    s1, e1 = spans[1]
    assert text[s0:e0].strip() == "Показник склав 3,14 та 2.72 відсотка."
    assert text[s1:e1].strip() == "Далі."


# ---------------------------------------------------------------------------
# 12. Скорочення поза списком (14 елементів) межею стати може.
# ---------------------------------------------------------------------------


def test_gate_12_abbreviation_outside_frozen_list_may_end_sentence() -> None:
    """"Вага 5 кг. Далі йде текст." — два речення: "кг" немає у
    версіонованому списку скорочень, тож крапка після нього завершує
    речення."""
    text = "Вага 5 кг. Далі йде текст."
    spans = split_sentences(text)

    assert len(spans) == 2
    s0, e0 = spans[0]
    s1, e1 = spans[1]
    assert text[s0:e0].strip() == "Вага 5 кг."
    assert text[s1:e1].strip() == "Далі йде текст."


# ---------------------------------------------------------------------------
# 13. Незавершений хвіст НЕ останнього авторського блока сторінки —
#     звичайне речення.
# ---------------------------------------------------------------------------


def test_gate_13_unterminated_tail_of_non_last_block_is_ordinary_sentence() -> None:
    """При is_last_author_block_on_page=False незавершений хвіст блока —
    звичайне речення, обидва спани мають is_page_boundary_fragment=False."""
    text = "Перше речення. Незавершений хвіст"
    spans = split_sentences_detailed(text, is_last_author_block_on_page=False)

    assert len(spans) == 2
    assert all(isinstance(s, SentenceSpan) for s in spans)
    assert spans[0].is_page_boundary_fragment is False
    assert spans[1].is_page_boundary_fragment is False
    assert text[spans[0].start : spans[0].end].strip() == "Перше речення."
    assert text[spans[1].start : spans[1].end].strip() == "Незавершений хвіст"


# ---------------------------------------------------------------------------
# 14. Той самий вхід на межі фізичної сторінки — хвіст помічений.
# ---------------------------------------------------------------------------


def test_gate_14_unterminated_tail_of_last_block_on_page_is_marked_fragment() -> None:
    """При is_last_author_block_on_page=True незавершений хвіст того самого
    входу отримує is_page_boundary_fragment=True; завершене перше речення
    лишається False."""
    text = "Перше речення. Незавершений хвіст"
    spans = split_sentences_detailed(text, is_last_author_block_on_page=True)

    assert len(spans) == 2
    assert spans[0].is_page_boundary_fragment is False
    assert spans[1].is_page_boundary_fragment is True
    assert text[spans[0].start : spans[0].end].strip() == "Перше речення."
    assert text[spans[1].start : spans[1].end].strip() == "Незавершений хвіст"


# ---------------------------------------------------------------------------
# 15. Останній блок сторінки завершено пунктуацією — жоден спан не помічений.
# ---------------------------------------------------------------------------


def test_gate_15_terminated_last_block_on_page_has_no_marked_fragment() -> None:
    """Якщо останній блок сторінки завершується термінальною пунктуацією,
    жоден спан не має is_page_boundary_fragment=True навіть при
    is_last_author_block_on_page=True."""
    text = "Перше речення. Друге речення."
    spans = split_sentences_detailed(text, is_last_author_block_on_page=True)

    assert len(spans) == 2
    assert all(s.is_page_boundary_fragment is False for s in spans)


# ---------------------------------------------------------------------------
# 16. ';' і ':' самі по собі речення не ділять.
# ---------------------------------------------------------------------------


def test_gate_16_semicolon_and_colon_alone_do_not_split_sentence() -> None:
    """"Пункти: перший; другий та третій." — одне речення, попри двокрапку і
    крапку з комою всередині."""
    text = "Пункти: перший; другий та третій."
    spans = split_sentences(text)

    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end].strip() == text


# ---------------------------------------------------------------------------
# 17. split_sentences — точна проєкція split_sentences_detailed на (start, end).
# ---------------------------------------------------------------------------


def test_gate_17_split_sentences_matches_detailed_projection_on_three_inputs() -> None:
    """Для трьох різних входів split_sentences(text) збігається з проєкцією
    split_sentences_detailed(text) на (start, end)."""
    texts = (
        "див. рис. 2 у праці проф. Шевченка.",
        "Показник склав 3,14 та 2.72 відсотка. Далі.",
        "Перше речення. Незавершений хвіст",
    )
    for text in texts:
        plain = split_sentences(text)
        detailed = split_sentences_detailed(text)
        assert plain == tuple((s.start, s.end) for s in detailed)


# ---------------------------------------------------------------------------
# 18. iter_word_windows — чисте перечислення вікон 6-10 слів.
# ---------------------------------------------------------------------------


def test_gate_18_iter_word_windows_enumerates_pure_windows_by_start_then_length() -> None:
    """iter_word_windows(5) == () (менше за мінімум); iter_word_windows(6)
    дає рівно (0, 6); iter_word_windows(12) дає всі вікна довжини 6..10,
    відсортовані за start, потім за довжиною, без повторів."""
    assert WINDOW_MIN_WORDS == 6
    assert WINDOW_MAX_WORDS == 10

    assert iter_word_windows(5) == ()
    assert iter_word_windows(6) == ((0, 6),)

    expected_12 = (
        (0, 6), (0, 7), (0, 8), (0, 9), (0, 10),
        (1, 7), (1, 8), (1, 9), (1, 10), (1, 11),
        (2, 8), (2, 9), (2, 10), (2, 11), (2, 12),
        (3, 9), (3, 10), (3, 11), (3, 12),
        (4, 10), (4, 11), (4, 12),
        (5, 11), (5, 12),
        (6, 12),
    )
    result_12 = iter_word_windows(12)
    assert result_12 == expected_12
    assert len(set(result_12)) == len(result_12)
    for start, end in result_12:
        assert WINDOW_MIN_WORDS <= (end - start) <= WINDOW_MAX_WORDS


# ---------------------------------------------------------------------------
# 19. Детермінізм: два прогони кожної функції дають рівний результат.
# ---------------------------------------------------------------------------


def test_gate_19_all_four_functions_are_deterministic_across_two_runs() -> None:
    """Два прогони поспіль normalize_text, tokenize, split_sentences_detailed
    та iter_word_windows на одному вході дають рівний результат і той самий
    порядок."""
    raw = "Дослі-\nдження з’являється: див. рис. 2 у праці проф. Шевченка."

    normalized_1 = normalize_text(raw)
    normalized_2 = normalize_text(raw)
    assert normalized_1 == normalized_2

    tokens_1 = tokenize(raw, normalized_1)
    tokens_2 = tokenize(raw, normalized_2)
    assert tokens_1 == tokens_2

    sentences_1 = split_sentences_detailed(raw, is_last_author_block_on_page=True)
    sentences_2 = split_sentences_detailed(raw, is_last_author_block_on_page=True)
    assert sentences_1 == sentences_2

    windows_1 = iter_word_windows(9)
    windows_2 = iter_word_windows(9)
    assert windows_1 == windows_2


# ---------------------------------------------------------------------------
# 20. ALGO_VERSION підвищено відносно кроку 3.
# ---------------------------------------------------------------------------


def test_gate_20_algo_version_is_nonempty_and_bumped_from_step_3() -> None:
    """ALGO_VERSION у search/__init__.py — непорожній рядок, відмінний від
    значення кроку 3 "search-algo-2026-08-25" (§4.1: межі речень можуть
    змінити кандидатів)."""
    assert isinstance(ALGO_VERSION, str)
    assert ALGO_VERSION != ""
    assert ALGO_VERSION != PREVIOUS_ALGO_VERSION


# ---------------------------------------------------------------------------
# Відмови — граничні випадки з розділу «Відмови» пакета.
# ---------------------------------------------------------------------------


def test_reject_empty_input_yields_empty_result_without_exception() -> None:
    """Порожній вхід "" у normalize_text, split_sentences і
    split_sentences_detailed дає порожній результат без винятку."""
    normalized = normalize_text("")
    assert normalized == NormalizedText("", ())
    assert split_sentences("") == ()
    assert split_sentences_detailed("") == ()


def test_reject_invalid_offset_range_raises_value_error() -> None:
    """map_normalized_offsets піднімає ValueError при start < 0,
    end > len(origins) і start >= end."""
    normalized = normalize_text("слово")
    n = len(normalized.text)

    with pytest.raises(ValueError):
        map_normalized_offsets(normalized, -1, n)
    with pytest.raises(ValueError):
        map_normalized_offsets(normalized, 0, n + 1)
    with pytest.raises(ValueError):
        map_normalized_offsets(normalized, 2, 2)


def test_reject_text_without_terminal_punctuation_is_one_whole_sentence() -> None:
    """Текст без жодної термінальної пунктуації в звичайному блоці — одне
    речення, що охоплює весь текст цілком."""
    text = "Текст без крапки в кінці"
    spans = split_sentences(text)

    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end].strip() == text
