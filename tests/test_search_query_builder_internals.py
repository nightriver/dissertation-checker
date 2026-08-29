"""
Модульні тести внутрішньої механіки вікна запиту `search/query_builder.py`
(§13, крок 3 §22): бали вікна, вибір найкращого вікна, обрізання до 220
символів і спрощений `pdf_anchor`. Наскрізний виклик через реальний PDF —
`test_search_thin_slice_integration.py`; тут — прицільні внутрішні кейси,
які важко детерміновано спровокувати через повний конвеєр (наприклад
`query_too_long`, коли єдиний токен із сигналом сам довший за ліміт).
"""

from __future__ import annotations

import hashlib

from search.normalization import normalize_text, tokenize
from search.query_builder import (
    MAX_QUERY_CHARS,
    _build_pdf_anchor,
    _guess_query_language,
    _score_window,
    _select_best_window,
    _trim_window_to_limit,
    build_search_result,
)
from search.sentences import split_sentences
from search.types import (
    Channel,
    Confidence,
    Language,
    RawSpan,
    SearchBlock,
    SearchDocument,
    SearchToken,
    SectionInfo,
    SectionKind,
    SentenceDonor,
    SourceSpan,
    TextZone,
    ZoneSpan,
)


def _word_token(raw: str, raw_start: int) -> SearchToken:
    return SearchToken(
        raw=raw,
        normalized=raw,
        raw_start=raw_start,
        raw_end=raw_start + len(raw),
        normalized_start=raw_start,
        normalized_end=raw_start + len(raw),
        is_word=True,
    )


def _tokens_from_words(words: list[str]) -> tuple[list[SearchToken], str]:
    """Будує словесні токени й raw_text, розділяючи слова одним пробілом."""
    tokens = []
    raw_text_parts = []
    pos = 0
    for i, word in enumerate(words):
        if i > 0:
            raw_text_parts.append(" ")
            pos += 1
        tokens.append(_word_token(word, pos))
        raw_text_parts.append(word)
        pos += len(word)
    return tokens, "".join(raw_text_parts)


def test_score_window_counts_proper_name_number_long_word_and_normative_penalty():
    words = ["Ми", "І.", "Керимов", "2020", "реформування", "стаття", "15"]
    tokens, raw_text = _tokens_from_words(words)
    freq = {w.casefold(): 5 for w in words}  # частий, щоб не рахувати "рідкісну форму"
    score = _score_window(tokens, 0, len(tokens), raw_text, freq)
    # +4 (покриття сигналу, завжди додається базово);
    # "І. Керимов" — конструкція ініціал + прізвище; прізвище отримує +3
    # і водночас є змістовним словом
    # ≥6 літер (+1, критерії не взаємовиключні — §13 не забороняє поєднання);
    # "2020" — число (+3);
    # "реформування" і "стаття" — по +1 як змістовні слова ≥6 літер;
    # "15" — номер статті, тому бонус числа не отримує;
    # "стаття 15" — нормативний маркер (−2).
    assert score == 4.0 + (3.0 + 1.0) + 3.0 + 1.0 + 1.0 - 2.0
    assert score == 11.0


def test_score_window_gives_rare_form_bonus_only_for_low_frequency_words():
    words = ["унікальність", "частий", "частий"]
    tokens, raw_text = _tokens_from_words(words)
    freq = {"унікальність": 1, "частий": 5}
    score = _score_window(tokens, 0, len(tokens), raw_text, freq)
    # "унікальність" рідкісна (+2) і водночас довга (+1); "частий" двічі —
    # довгий (+1 кожен), але не рідкісний.
    assert score == 4.0 + 2.0 + 1.0 + 1.0 + 1.0


def test_select_best_window_returns_none_for_short_sentence():
    words = ["Пропонуємо", "на", "нашу", "думку"]  # лише 4 слова, менше мінімуму 6
    tokens, raw_text = _tokens_from_words(words)
    result = _select_best_window(tokens, [(0, len(words[0]))], raw_text, {})
    assert result is None


def test_select_best_window_returns_none_when_signal_span_matches_no_window():
    words = ["Один", "два", "три", "чотири", "пять", "шість", "сім"]
    tokens, raw_text = _tokens_from_words(words)
    # Сигнал за межами будь-якого токена — жодне вікно не може його містити.
    result = _select_best_window(tokens, [(9_000, 9_001)], raw_text, {})
    assert result is None


def test_select_best_window_prefers_shorter_then_earlier_on_tie():
    # Однакові прості слова довжиною <6 і без сигнальних бонусів — усі
    # вікна набирають однаковий бал (лише базові +4), тож перемагає
    # найкоротше (6 слів), а серед рівних за довжиною — найраніше.
    words = ["на", "би", "чи", "та", "ще", "як", "би", "чи"]
    tokens, raw_text = _tokens_from_words(words)
    signal_span = (tokens[0].raw_start, tokens[0].raw_end)
    result = _select_best_window(tokens, [signal_span], raw_text, {})
    assert result is not None
    start_idx, end_idx, score = result
    assert end_idx - start_idx == 6
    assert start_idx == 0
    assert score == 4.0


def test_trim_window_to_limit_shrinks_from_the_side_without_signal():
    long_word = "довжелезне" * 30  # свідомо довге, але не несе сигналу
    words = [long_word, "на", "нашу", "думку", "коротке"]
    tokens, raw_text = _tokens_from_words(words)
    signal_span = (tokens[1].raw_start, tokens[3].raw_end)  # "на нашу думку"
    trimmed = _trim_window_to_limit(tokens, 0, len(tokens), [signal_span], raw_text)
    assert trimmed is not None
    lo, hi = trimmed
    assert lo == 1  # довге слово зліва прибране, бо не несе сигналу
    quoted = raw_text[tokens[lo].raw_start : tokens[hi - 1].raw_end]
    assert len(quoted) + 2 <= MAX_QUERY_CHARS


def test_trim_window_to_limit_gives_up_when_the_signal_token_alone_is_too_long():
    huge_signal_word = "пропон" + "у" * (MAX_QUERY_CHARS + 10)
    words = ["Автори", huge_signal_word, "важливе", "рішення"]
    tokens, raw_text = _tokens_from_words(words)
    signal_span = (tokens[1].raw_start, tokens[1].raw_end)
    trimmed = _trim_window_to_limit(tokens, 0, len(tokens), [signal_span], raw_text)
    assert trimmed is None


def test_build_pdf_anchor_falls_back_to_whole_donor_when_too_few_words():
    words = ["Коротке", "речення", "тут"]
    tokens, raw_text = _tokens_from_words(words)
    text, source, is_fallback = _build_pdf_anchor(tokens, raw_text, "blk-0", 1, 0)
    assert is_fallback is True
    assert text == raw_text


def test_build_pdf_anchor_falls_back_for_empty_donor():
    text, source, is_fallback = _build_pdf_anchor([], "", "blk-0", 1, 0)
    assert is_fallback is True
    assert text == ""


def test_build_pdf_anchor_takes_up_to_fifteen_leading_words():
    words = [f"слово{i}" for i in range(20)]
    tokens, raw_text = _tokens_from_words(words)
    text, source, is_fallback = _build_pdf_anchor(tokens, raw_text, "blk-0", 1, 0)
    assert is_fallback is False
    assert text == raw_text[tokens[0].raw_start : tokens[14].raw_end]
    assert text.count(" ") == 14


def test_guess_query_language_variants():
    assert _guess_query_language("існує") == Language.UK
    assert _guess_query_language("который") == Language.RU
    assert _guess_query_language("існуючий который") == Language.MIXED
    assert _guess_query_language("plain text 123") == Language.UNKNOWN


# ---------------------------------------------------------------------------
# `build_search_result` × тип розділу (§6.1, ревізія кроку 3): сигнали й
# кандидат каналу A мають лишатися видимими в діагностиці для БУДЬ-ЯКОГО
# розділу, а квоту (перетворення на `SearchQuery`) дають лише
# INTRO/CHAPTER/CONCLUSIONS. `parser.searchdoc` на кроці 3 вміє розпізнавати
# лише ці розділи й UNKNOWN (заголовки TITLE/TOC/ABSTRACT/BIBLIO/APPENDIX —
# крок 5), тому для TITLE/ABSTRACT/BIBLIO тут будується `SearchDocument`
# вручну, без реального PDF.
# ---------------------------------------------------------------------------

_TWO_SIGNAL_SENTENCE = (
    "Ми пропонуємо, на нашу думку, важливе рішення для реформування "
    "вітчизняного законодавства."
)
_DOCUMENT_SHA = hashlib.sha256(b"test-query-builder-section-kinds").hexdigest()


def _build_document_with_sections(specs: list[tuple[SectionKind, str]]) -> SearchDocument:
    """Мінімальний `SearchDocument` із заданими типами розділів (без PDF)."""
    blocks: list[SearchBlock] = []
    sections: list[SectionInfo] = []
    sentences: list[SentenceDonor] = []

    for i, (kind, raw_text) in enumerate(specs):
        section_id = f"sec-{i:03d}"
        block_id = f"blk-{i:05d}"
        normalized = normalize_text(raw_text)
        tokens = tokenize(raw_text, normalized)
        zone_spans = (ZoneSpan(0, len(raw_text), TextZone.AUTHOR_TEXT, Confidence.MEDIUM, "test"),)
        blocks.append(
            SearchBlock(
                block_id=block_id,
                raw_text=raw_text,
                normalized=normalized,
                tokens=tokens,
                section_id=section_id,
                heading_path=(),
                physical_page=1,
                block_index=i,
                zone_spans=zone_spans,
            )
        )
        author_words = sum(1 for t in tokens if t.is_word)
        sections.append(
            SectionInfo(
                section_id=section_id,
                kind=kind,
                ordinal=None,
                heading="",
                block_start=i,
                block_end=i + 1,
                physical_pages=(1,),
                author_words=author_words,
                expected_body_pages=1,
                extractable_body_pages=1,
                coverage_ratio=1.0,
                confidence=Confidence.MEDIUM,
            )
        )
        for ordinal, (start, end) in enumerate(split_sentences(raw_text)):
            s_raw = raw_text[start:end]
            s_normalized = normalize_text(s_raw).text
            author_word_count = sum(1 for t in tokenize(s_raw, normalize_text(s_raw)) if t.is_word)
            donor_id = hashlib.sha256(
                f"{_DOCUMENT_SHA}|{i}|{s_normalized}|0".encode("utf-8")
            ).hexdigest()
            sentences.append(
                SentenceDonor(
                    donor_id=donor_id,
                    block_id=block_id,
                    section_id=section_id,
                    sentence_ordinal=ordinal,
                    occurrence_index=0,
                    source=SourceSpan(parts=(RawSpan(block_id, 1, start, end),)),
                    raw_text=s_raw,
                    normalized_text=s_normalized,
                    author_word_count=author_word_count,
                )
            )

    return SearchDocument(
        document_sha256=_DOCUMENT_SHA,
        parser_version="test",
        n_pages=1,
        pages=(),
        expected_body_pages=0,
        extractable_body_pages=0,
        coverage_ratio=0.0,
        blocks=tuple(blocks),
        sections=tuple(sections),
        sentences=tuple(sentences),
        bibliography=(),
        citations=(),
        body_biblio_confidence=Confidence.LOW,
        applied_overrides=(),
    )


def test_unknown_and_non_content_sections_get_distinct_rejection_reasons():
    """
    §6.1: причина відсіву для UNKNOWN відрізняється від TITLE/TOC/ABSTRACT/
    BIBLIO/APPENDIX — обидва не мовчазні (CLAUDE.md, правило №3), обидва
    лишають сигнали в `signal_hits`, але жоден не дає `SearchQuery`.
    """
    document = _build_document_with_sections(
        [
            (SectionKind.UNKNOWN, _TWO_SIGNAL_SENTENCE),
            (SectionKind.ABSTRACT, _TWO_SIGNAL_SENTENCE),
            (SectionKind.CHAPTER, _TWO_SIGNAL_SENTENCE),
        ]
    )
    result = build_search_result(document)

    # Змістовний розділ (CHAPTER) — єдиний, що дав запит.
    assert len(result.queries) == 1
    assert result.queries[0].section_id == "sec-002"

    # Усі три речення мають по 2 сигнали каналу A — сигнали видимі завжди.
    assert len(result.signal_hits) == 6

    # Кандидат каналу A "згенеровано" для всіх трьох речень (є сигнали),
    # незалежно від того, чи дав розділ квоту.
    assert dict(result.candidate_metrics.generated_by_channel)[Channel.A] == 3
    assert dict(result.candidate_metrics.retained_primary_by_channel)[Channel.A] == 1

    rejected = dict(result.candidate_metrics.rejected_by_reason)
    assert rejected["section_unknown"] == 1
    assert rejected["section_not_content_kind"] == 1
    # UNKNOWN і "не змістовний тип розділу" — саме дві окремі причини
    # (не звалені в одну), як прямо вимагала ревізія кроку 3.
    assert set(rejected) == {"section_unknown", "section_not_content_kind"}
